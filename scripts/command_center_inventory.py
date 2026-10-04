"""Read-only inventory for the command-center cutover (design E1/E6).

Copies regular files into a private artifact outside the source, with bounded
iterative traversal and explicit verbatim/prune exemptions. SQLite and LanceDB
NEVER open source databases. SQLite recovery and online backup run on the copy;
only the resulting standalone database is scanned with mode=ro. A size/mtime
recheck fails the run when the source CHANGED while being copied; it does not
refuse a live root, because a writer that happens to hold still looks idle.

Operational-name counts (``operational_word`` includes ``universes``) are meaningful
outside verbatim stores only; verbatim text matches are not migration work. Raw
TEXT/BLOB matching cannot decode every serialization or discover derived identities
(hashed lease keys, custody/grant digests). Those remain explicitly deferred, so
``migration_ready`` is false by construction, even for a complete zero-count scan.

    python scripts/command_center_inventory.py DATA_ROOT [--json]
        [--keep-artifact DIR] [--no-values] [--max-entries N]
        [--max-total-bytes N] [--max-file-bytes N] [--max-value-bytes N]
        [--max-rows N] [--deadline SECONDS]

Artifacts are removed on exit unless retained explicitly. Unclassified entries
and incomplete scans exit 2; usage errors exit 1. --no-values skips SQLite/JSON
values and LanceDB rows and always produces a partial, incomplete report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tinyassets.command_center_layout import (  # noqa: E402
    CONFIG_AUTHORITY_FIELDS,
    PLATFORM,
    PRUNE_DIR_NAMES,
    USER,
    VERBATIM_EXEMPT_NAMES,
    classify,
    sqlite_family,
)

ULID = r"[0-9a-hjkmnp-tv-z]{26}"
HOME_RE = re.compile(rf"^(?:u|cc)-{ULID}$")
U_ID = re.compile(rf"(?<![0-9A-Za-z])u-{ULID}(?![0-9A-Za-z])".encode())
ACTOR = re.compile(rb"universe:")
WORD = re.compile(rb"universe", re.IGNORECASE)
MATCHERS = {"u_ids": U_ID, "actor_prefix": ACTOR, "operational_word": WORD}
MAGIC = b"SQLite format 3\x00"
DEFERRED = [
    "derived identities (hashed lease keys, custody and grant digests) -- discovery "
    "belongs to cutover tasks 3/4 (design E4b); this scan cannot see them",
    "checkpoint serialization is matched as raw bytes, not decoded through its "
    "serde, so an id this scan does not find there is unproven, not absent",
    "acquisition is not producer-attested: a writer that holds the source still "
    "while it is copied is indistinguishable from an idle one, so a complete "
    "report is consistent per database, never a proven cross-store snapshot",
]


@dataclass(frozen=True)
class Limits:
    max_entries: int = 200_000
    max_total_bytes: int = 20 << 30
    max_file_bytes: int = 64 << 20
    max_value_bytes: int = 4096
    max_rows: int = 1_000_000
    deadline_s: float = 900.0

    def __post_init__(self):
        if any(value < 0 for value in vars(self).values()):
            raise ValueError("limits must be nonnegative")


def _unsafe(st: os.stat_result) -> bool:
    # `or 0`: on Windows the attribute EXISTS and can be None (a stat_result not
    # built by a real stat call), and `None & flag` would abort the whole walk
    # with a TypeError the acquisition loop does not catch.
    attributes = getattr(st, "st_file_attributes", 0) or 0
    return stat.S_ISLNK(st.st_mode) or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _artifact_path(source: Path, artifact: Path) -> Path:
    if artifact.is_symlink():
        raise ValueError("artifact must not be a symlink")
    artifact = artifact.resolve()
    if artifact.is_relative_to(source.resolve()):
        raise ValueError("artifact must not be inside source")
    if artifact.exists():
        if not artifact.is_dir() or _unsafe(os.lstat(artifact)):
            raise ValueError("artifact must be a real directory")
        with os.scandir(artifact) as entries:
            if next(entries, None) is not None:
                raise ValueError("artifact must be empty/new")
    return artifact


def acquire(source: Path, artifact: Path, limits: Limits) -> dict:
    """Copy bounded regular files; source handles are exclusively read-only.

    This detects observed mutation, not a trusted offline acquisition fence.
    Directory replacement races require that deferred host-side fence.
    """
    source = Path(source).absolute()
    if not source.is_dir() or _unsafe(os.lstat(source)):
        raise ValueError(f"not a real directory: {source}")
    artifact = _artifact_path(source, Path(artifact))
    artifact.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    record: dict[str, Any] = {
        "artifact": str(artifact), "deadline": started + limits.deadline_s,
        "files": [], "directories": [], "home_entries": {}, "source_mutated": [],
        "hot_journals": [], "limit_hit": None, "exemptions": [], "unscanned": [],
        "files_total": 0, "entries_seen": 0, "bytes_copied": 0,
        "consistency": "size/mtime recheck only; no offline layout fence",
    }

    def stopped() -> bool:
        if time.monotonic() >= record["deadline"]:
            record["limit_hit"] = "deadline"
        return record["limit_hit"] is not None

    # Keep iterators, not a materialized tree or sorted directory listing.
    stack = [(source, os.scandir(source))]
    try:
        while stack and not stopped():
            directory, entries = stack[-1]
            entry = next(entries, None)
            if entry is None:
                entries.close()
                stack.pop()
                continue
            if record["entries_seen"] >= limits.max_entries:
                record["limit_hit"] = "max_entries"
                break
            record["entries_seen"] += 1
            path = directory / entry.name
            rel = path.relative_to(source)
            name = rel.as_posix()
            if len(rel.parts) == 2 and HOME_RE.fullmatch(rel.parts[0]):
                record["home_entries"].setdefault(rel.parts[0], []).append(entry.name)
            try:
                before = os.lstat(path)
                if _unsafe(before) or not (
                    stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)
                ):
                    record["unscanned"].append({"path": name, "category": "link-or-special"})
                    continue
                if stat.S_ISDIR(before.st_mode):
                    if len(rel.parts) == 1 and HOME_RE.fullmatch(entry.name):
                        record["home_entries"].setdefault(entry.name, [])
                    reason = None
                    if entry.name in PRUNE_DIR_NAMES:
                        reason = f"prune:{entry.name}"
                    elif (len(rel.parts) == 2 and HOME_RE.fullmatch(rel.parts[0])
                          and entry.name in VERBATIM_EXEMPT_NAMES):
                        reason = f"verbatim:{entry.name}"
                    if reason:
                        record["exemptions"].append({"path": name, "reason": reason})
                        continue
                    (artifact / rel).mkdir(exist_ok=True)
                    record["directories"].append(name)
                    stack.append((path, os.scandir(path)))
                    continue
                record["files_total"] += 1
                if entry.name.endswith("-shm"):
                    # Transient shared memory, never copied: SQLite rebuilds it from
                    # the -wal, a writer mid-transaction holds it unreadable (Windows
                    # raises PermissionError), and treating it as a required family
                    # member blanks every finding in the database it belongs to.
                    record["exemptions"].append({"path": name, "reason": "sqlite-shm"})
                    continue
                flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
                fd = os.open(path, flags)
                with os.fdopen(fd, "rb") as src:
                    before = os.fstat(src.fileno())
                    if _unsafe(before) or not stat.S_ISREG(before.st_mode):
                        record["unscanned"].append({"path": name, "category": "replaced-file"})
                        continue
                    if before.st_size > limits.max_file_bytes:
                        record["unscanned"].append({
                            "path": name, "category": "oversize", "size": before.st_size,
                        })
                    digest = hashlib.sha256()
                    copied = 0
                    with (artifact / rel).open("xb") as dst:
                        while copied < min(before.st_size, limits.max_file_bytes):
                            if stopped():
                                break
                            remaining = limits.max_total_bytes - record["bytes_copied"]
                            if remaining <= 0:
                                record["limit_hit"] = "max_total_bytes"
                                break
                            chunk = src.read(min(
                                1 << 20, limits.max_file_bytes - copied, remaining,
                                before.st_size - copied,
                            ))
                            if not chunk:
                                break
                            dst.write(chunk)
                            digest.update(chunk)
                            copied += len(chunk)
                            record["bytes_copied"] += len(chunk)
                    record["files"].append({
                        "path": name, "bytes": copied, "sha256": digest.hexdigest(),
                        "st_size": before.st_size, "st_mtime_ns": before.st_mtime_ns,
                    })
                    if copied < min(before.st_size, limits.max_file_bytes) and not stopped():
                        record["source_mutated"].append(name)
                    # Hot journals are not fatal: the family is copied together, and
                    # SQLite recovery + backup on that private copy includes WAL commits.
                    # This is per-DB consistency, not proof of a cross-store snapshot.
                    if (entry.name.endswith("-wal") and copied) or entry.name.endswith("-journal"):
                        record["hot_journals"].append(name)
            except OSError as exc:
                record["unscanned"].append({
                    "path": name, "category": "io-error", "reason": type(exc).__name__,
                })
    finally:
        for _, entries in stack:
            entries.close()
    for file in record["files"]:
        try:
            after = os.lstat(source / file["path"])
            changed = (_unsafe(after) or not stat.S_ISREG(after.st_mode)
                       or (after.st_size, after.st_mtime_ns)
                       != (file["st_size"], file["st_mtime_ns"]))
        except OSError:
            changed = True
        if changed and file["path"] not in record["source_mutated"]:
            record["source_mutated"].append(file["path"])
    return record


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", "surrogatepass")
    return b""


def _counts() -> dict[str, int]:
    return dict.fromkeys(MATCHERS, 0)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _check_universe(sql: str) -> bool:
    # Track balanced CHECK parentheses, including nested calls and quoted SQL.
    for match in re.finditer(r"\bCHECK\s*\(", sql, re.IGNORECASE):
        depth, quote = 1, None
        start = match.end()
        for offset in range(start, len(sql)):
            char = sql[offset]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"`":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    if WORD.search(sql[start:offset].encode()):
                        return True
                    break
    return False


def _text_affinity(declared: str) -> bool:
    kind = declared.upper()
    # SQLite tests INT first (e.g. FLOATING POINT has INTEGER affinity).
    return "INT" not in kind and (
        not kind or any(word in kind for word in ("CHAR", "CLOB", "TEXT", "BLOB"))
    )


def scan_sqlite(path: Path, standalone: Path, *, values: bool,
                limits: Limits, deadline: float) -> dict[str, Any]:
    """Recover/backup an artifact DB, then scan the standalone read-only copy."""
    report: dict[str, Any] = {
        "tables": [], "columns": [], "schema_sql": [], "values": [],
        "table_values": {}, "error": None,
    }

    def check_deadline(*_args):
        if time.monotonic() >= deadline:
            raise TimeoutError("deadline")
        return 0

    connections = []
    try:
        check_deadline()
        standalone.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(path, timeout=0)
        connections.append(src)
        dst = sqlite3.connect(standalone, timeout=0)
        connections.append(dst)
        for conn in connections:
            conn.set_progress_handler(check_deadline, 10_000)
        src.backup(dst, pages=128, progress=check_deadline, sleep=0.01)
        src.close()
        dst.close()
        connections.clear()
        conn = sqlite3.connect(standalone.resolve().as_uri() + "?mode=ro", uri=True)
        connections.append(conn)
        conn.set_progress_handler(check_deadline, 10_000)
        for kind, name, sql in conn.execute("SELECT type, name, sql FROM sqlite_master"):
            check_deadline()
            if kind == "table" and "universe" in name.lower():
                report["tables"].append(name)
            if sql and "universe" in sql.lower():
                report["schema_sql"].append({
                    "type": "table_sql" if kind == "table" else kind, "name": name,
                })
            if kind != "table" or name.startswith("sqlite_"):
                continue
            if sql and _check_universe(sql):
                report["schema_sql"].append({"type": "check", "name": name})
            columns = list(conn.execute(f"PRAGMA table_xinfo({_quote(name)})"))
            selected = []
            skipped = []
            for _, column, declared, _, _, _, hidden in columns:
                if "universe" in column.lower():
                    report["columns"].append({
                        "table": name, "column": column, "generated": hidden in (2, 3),
                    })
                (selected if _text_affinity(declared) else skipped).append(column)
            table = {
                "rows_scanned": 0, "rows_total": conn.execute(
                    f"SELECT count(*) FROM {_quote(name)}"
                ).fetchone()[0], "truncated": False,
                "columns_not_value_scanned": skipped, **_counts(),
            }
            report["table_values"][name] = table
            if not values or not selected:
                continue
            cells = {col: _counts() for col in selected}
            # One bounded pass: substr prevents loading an entire large BLOB.
            expressions = ", ".join(f"substr({_quote(col)}, 1, ?)" for col in selected)
            lengths = ", ".join(f"length({_quote(col)})" for col in selected)
            query = f"SELECT {expressions}, {lengths} FROM {_quote(name)} LIMIT ?"
            for row in conn.execute(query, [limits.max_value_bytes] * len(selected)
                                    + [limits.max_rows]):
                check_deadline()
                table["rows_scanned"] += 1
                row_hits = _counts()
                for col, value, length in zip(selected, row[:len(selected)], row[len(selected):]):
                    if length is not None and length > limits.max_value_bytes:
                        table["values_truncated"] = True
                    raw = _as_bytes(value)
                    for key, matcher in MATCHERS.items():
                        if matcher.search(raw):
                            cells[col][key] += 1
                            row_hits[key] = 1
                for key, count in row_hits.items():
                    table[key] += count
            table["truncated"] = table["rows_scanned"] < table["rows_total"]
            report["values"].extend(
                {"table": name, "column": col, **counts} for col, counts in cells.items()
            )
    except (sqlite3.Error, TimeoutError) as exc:
        report["error"] = type(exc).__name__
        if isinstance(exc, TimeoutError) or time.monotonic() >= deadline:
            report["deadline_hit"] = True
    finally:
        for conn in connections:
            conn.close()
    return report


def _bounded(text: str, counts: dict[str, int], cap: int) -> bytes:
    """The first ``cap`` bytes, recording that the rest was never matched."""
    raw = text.encode("utf-8", "surrogatepass")
    if len(raw) > cap:
        counts["values_truncated"] = 1
        return raw[:cap]
    return raw


def _json_counts(node: Any, counts: dict[str, int], *, values: bool = True,
                 cap: int) -> None:
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                bounded = _bounded(key, counts, cap)
                counts["keys"] += int(bool(WORD.search(bounded)))
                counts["u_id_keys"] += int(bool(U_ID.search(bounded)))
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
        elif values and isinstance(current, str):
            bounded = _bounded(current, counts, cap)
            counts["u_id_values"] += int(bool(U_ID.search(bounded)))
            counts["word_values"] += int(bounded.lower() == b"universe")
            counts["actor_values"] += int(bounded.startswith(b"universe:"))


def scan_json(path: Path, *, values: bool, cap: int) -> dict[str, int]:
    counts = dict.fromkeys(
        ("keys", "u_id_keys", "u_id_values", "word_values", "actor_values",
         "values_truncated"), 0)
    _json_counts(json.loads(path.read_text(encoding="utf-8")), counts,
                 values=values, cap=cap)
    return counts


def config_authority(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [name for name in CONFIG_AUTHORITY_FIELDS
            if re.search(rf"^\s*{name}\s*:", text, re.MULTILINE)]


def scan_lancedb(path: Path, *, values: bool, limits: Limits, deadline: float) -> dict:
    try:
        import lancedb
    except ImportError:
        return {"scanned": False, "reason": "lancedb not importable"}
    try:
        db = lancedb.connect(str(path))
        tables = {}
        for name in db.table_names():
            if time.monotonic() >= deadline:
                return {"scanned": False, "reason": "deadline", "deadline_hit": True}
            table = db.open_table(name)
            fields = [field.name for field in table.schema
                      if "universe" in field.name.lower() or (
                          (field.name == "id" or field.name.endswith("_id"))
                          and str(field.type) in ("string", "large_string"))]
            result = {
                "fields": fields, "rows_total": table.count_rows(), "rows_scanned": 0,
                "values_truncated": 0,
                "truncated": False, **_counts(),
            }
            tables[name] = result
            if values and fields and limits.max_rows:
                batches = table.search().select(fields).limit(limits.max_rows).to_batches()
                for batch in batches:
                    for row in batch.to_pylist():
                        if time.monotonic() >= deadline:
                            return {"scanned": False, "reason": "deadline", "tables": tables,
                                    "deadline_hit": True}
                        result["rows_scanned"] += 1
                        bounded = []
                        for value in row.values():
                            raw = _as_bytes(value)
                            if len(raw) > limits.max_value_bytes:
                                result["values_truncated"] = 1
                                raw = raw[:limits.max_value_bytes]
                            bounded.append(raw)
                        for key, matcher in MATCHERS.items():
                            result[key] += int(any(matcher.search(raw) for raw in bounded))
            result["truncated"] = bool(fields) and result["rows_scanned"] < result["rows_total"]
        return {"scanned": values, "tables": tables,
                "reason": None if values else "--no-values skips LanceDB rows"}
    except Exception as exc:  # noqa: BLE001 - explicit incomplete store, no data in errors
        return {"scanned": False, "reason": type(exc).__name__}


def inventory(root: Path, *, values: bool = True, limits: Limits | None = None,
              keep_artifact: Path | None = None) -> dict[str, Any]:
    limits = limits or Limits()
    root = Path(root).absolute()
    if not root.is_dir() or _unsafe(os.lstat(root)):
        raise ValueError(f"not a real directory: {root}")
    if keep_artifact is not None:
        artifact = _artifact_path(root, Path(keep_artifact))
    else:
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root.is_relative_to(root.resolve()):
            raise ValueError("temporary directory is inside source; use --keep-artifact outside it")
        artifact = Path(tempfile.mkdtemp(prefix="command-center-inventory-", dir=temp_root))
    try:
        acquisition = acquire(root, artifact, limits)
        return _inventory_artifact(root, artifact, acquisition, values, limits)
    finally:
        if keep_artifact is None:
            shutil.rmtree(artifact)


def _inventory_artifact(root: Path, artifact: Path, acquisition: dict,
                        values: bool, limits: Limits) -> dict:
    report: dict[str, Any] = {
        "data_root": str(root), "acquisition": acquisition, "homes": {}, "sqlite": {},
        "json": {}, "lancedb": {}, "text": {}, "partial": not values,
        "partial_reason": (None if values else
                           "--no-values skips SQLite/JSON values and LanceDB rows"),
        # Copies: what the scan adds below must not rewrite the acquisition record.
        "exemptions": list(acquisition["exemptions"]),
        "unscanned": list(acquisition["unscanned"]),
        "deferred": list(DEFERRED), "incomplete_reasons": [],
        "operational_word": "case-insensitive universe substring; operational names only outside "
                            "verbatim stores; verbatim text matches are not migration work",
    }
    reasons = report["incomplete_reasons"]
    if not values:
        reasons.append(report["partial_reason"])
    if acquisition["limit_hit"]:
        reasons.append(f"acquisition limit_hit: {acquisition['limit_hit']}")
    for path in acquisition["source_mutated"]:
        reasons.append(f"source_mutated: {path}")
    for name, entries in acquisition["home_entries"].items():
        home = {USER: [], PLATFORM: [], "unclassified": [], "config_authority": []}
        report["homes"][name] = home
        for child in entries:
            home[classify(child) or "unclassified"].append(child)
            if classify(child) is None:
                reasons.append(f"unclassified home entry: {name}/{child}")
    scanned: set[str] = set()
    blocked = {entry["path"] for entry in report["unscanned"]}
    files = {entry["path"]: entry for entry in acquisition["files"]}
    lance_dirs = [p for p in acquisition["directories"] if Path(p).name == "lancedb"]
    sidecars: set[str] = set()
    databases = []
    for rel in files:
        path = artifact / rel
        with path.open("rb") as stream:
            if stream.read(16) == MAGIC:
                databases.append(rel)
                sidecars.update((path.parent / name).relative_to(artifact).as_posix()
                                for name in sqlite_family(path.name)[1:])
    # A source .scan is legitimate input. Reserve a unique private backup directory.
    scan_root = artifact / ".scan"
    if scan_root.exists():
        scan_root = Path(tempfile.mkdtemp(prefix=".scan-", dir=artifact))
    else:
        scan_root.mkdir()
    for rel in databases:
        # -shm is deliberately absent (see acquire): requiring it would discard the
        # findings of every database a live writer happened to be holding.
        if rel in blocked or any(p in blocked for p in (
            (Path(rel).parent / n).as_posix() for n in sqlite_family(Path(rel).name)
            if not n.endswith("-shm")
        )):
            report["unscanned"].append({"path": rel, "category": "incomplete-sqlite-family"})
            continue
        found = scan_sqlite(artifact / rel, scan_root / (rel + ".scan.db"),
                            values=values, limits=limits, deadline=acquisition["deadline"])
        report["sqlite"][rel] = found
        if found["error"]:
            reasons.append(f"SQLite error: {rel}: {found['error']}")
        else:
            scanned.add(rel)
        if found.get("deadline_hit"):
            reasons.append(f"deadline_hit: {rel}")
        for table, counts in found["table_values"].items():
            if counts["truncated"] or counts.get("values_truncated"):
                reasons.append(f"SQLite value/row limit: {rel}:{table}")
    for rel in lance_dirs:
        found = scan_lancedb(artifact / rel, values=values, limits=limits,
                             deadline=acquisition["deadline"])
        report["lancedb"][rel] = found
        if not found["scanned"]:
            reasons.append(f"unscanned LanceDB: {rel}: {found['reason']}")
        for table, counts in found.get("tables", {}).items():
            if counts["truncated"] or counts.get("values_truncated"):
                reasons.append(f"LanceDB row/value limit: {rel}:{table}")
    for rel in files:
        if rel in blocked or rel in databases:
            continue
        path = artifact / rel
        lance = next((p for p in lance_dirs if Path(rel).is_relative_to(p)), None)
        if lance:
            if report["lancedb"][lance]["scanned"]:
                scanned.add(rel)
            else:
                report["unscanned"].append({"path": rel, "category": "unscanned-lancedb"})
            continue
        if rel in sidecars:
            report["exemptions"].append({"path": rel, "reason": "sqlite-family recovery input"})
            continue
        if time.monotonic() >= acquisition["deadline"]:
            report["unscanned"].append({"path": rel, "category": "deadline_hit"})
            continue
        try:
            if path.suffix == ".json":
                counts = scan_json(path, values=values, cap=limits.max_value_bytes)
                report["json"][rel] = counts
                if counts["values_truncated"]:
                    reasons.append(f"JSON value limit: {rel}")
            elif path.name == "config.yaml":
                fields = config_authority(path)
                if path.parent.name in report["homes"]:
                    report["homes"][path.parent.name]["config_authority"] = fields
            elif path.suffix in (".yaml", ".yml", ".md", ".txt", ".jsonl", ".log"):
                text = path.read_text(encoding="utf-8")
                report["text"][rel] = {
                    key: len(matcher.findall(text.encode())) if values else 0
                    for key, matcher in MATCHERS.items()
                }
            else:
                report["unscanned"].append({"path": rel, "category": "unknown-encoding"})
                continue
            scanned.add(rel)
        except (OSError, ValueError, RecursionError) as exc:
            report["unscanned"].append({"path": rel, "category": "unknown-encoding",
                                        "reason": type(exc).__name__})
    for entry in report["unscanned"]:
        reasons.append(f"unscanned {entry['category']}: {entry['path']}")
    homes = report["homes"].values()
    sql = report["sqlite"].values()
    totals = {
        "homes": len(report["homes"]),
        "unclassified_entries": sum(len(h["unclassified"]) for h in homes),
        "configs_with_authority": sum(bool(h["config_authority"]) for h in homes),
        "sqlite_files_with_findings": sum(bool(s["tables"] or s["columns"] or s["schema_sql"]
                                                or any(any(v[k] for k in MATCHERS)
                                                       for v in s["values"])) for s in sql),
        "universe_tables": sum(len(s["tables"]) for s in sql),
        "universe_columns": sum(len(s["columns"]) for s in sql),
        "schema_objects": sum(len(s["schema_sql"]) for s in sql),
        # values_truncated is a coverage flag, not a finding
        "json_files_with_findings": sum(
            any(count for key, count in j.items() if key != "values_truncated")
            for j in report["json"].values()),
        "lancedb_unscanned": sum(not v["scanned"] for v in report["lancedb"].values()),
    }
    for key in MATCHERS:
        totals[f"value_rows_with_{key}"] = sum(
            t[key] for s in sql for t in s["table_values"].values())
        totals[f"value_cells_with_{key}"] = sum(v[key] for s in sql for v in s["values"])
        totals[f"lancedb_rows_with_{key}"] = sum(
            t[key] for store in report["lancedb"].values()
            for t in store.get("tables", {}).values())
        totals[f"text_occurrences_with_{key}"] = sum(t[key] for t in report["text"].values())
    report["totals"] = totals
    # A pruned or verbatim subtree is one exemption for many files it never
    # enumerated; every other exemption is one observed regular file.
    exempt_files = {e["path"] for e in report["exemptions"]
                    if not e["reason"].startswith(("prune:", "verbatim:"))}
    report["coverage"] = {
        # Pruned subtrees are never enumerated; totals count observed regular files only.
        "files_total": acquisition["files_total"], "files_copied": len(files),
        "files_scanned": len(scanned), "files_exempt": len(exempt_files),
        "files_unscanned": acquisition["files_total"] - len(scanned | exempt_files),
    }
    report["coverage_note"] = "Observed regular files only; exempt subtrees are not enumerated."
    report["complete"] = not reasons
    operational = [v for k, v in totals.items() if k != "homes"]
    report["migration_ready"] = (report["complete"] and not any(operational)
                                  and not report["deferred"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-values", action="store_true")
    parser.add_argument("--keep-artifact", type=Path)
    for field, default in vars(Limits()).items():
        flag = "--deadline" if field == "deadline_s" else "--" + field.replace("_", "-")
        parser.add_argument(flag, dest=field, type=type(default), default=default)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_request:
        if exit_request.code:  # argparse's own exit 2 is a usage error like any other
            return 1
        raise  # --help / --version asked to stop, and succeeded
    try:
        limits = Limits(**{key: getattr(args, key) for key in vars(Limits())})
        report = inventory(args.data_root, values=not args.no_values, limits=limits,
                           keep_artifact=args.keep_artifact)
    except (ValueError, OSError) as exc:
        print(f"inventory usage/acquisition error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for key, value in report["totals"].items():
            print(f"{key}: {value}")
        for key, value in report["coverage"].items():
            print(f"{key}: {value}")
        print(f"complete: {report['complete']}")
        print(f"migration_ready: {report['migration_ready']}")
        for item in report["deferred"]:
            print(f"deferred: {item}")
        for reason in report["incomplete_reasons"]:
            print(f"incomplete: {reason}")
        for home, entries in report["homes"].items():
            if entries["unclassified"]:
                print(f"unclassified in {home}: {', '.join(entries['unclassified'])}")
    return 0 if report["complete"] and not report["totals"]["unclassified_entries"] else 2


if __name__ == "__main__":
    sys.exit(main())
