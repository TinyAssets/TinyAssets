"""In-box knowledge and memory file authority.

One counted conversion turns retired SQLite rows into the owner's Markdown
tree. Files win after that: a later edit is what a restart reads, and the
named canonical SQL writers no longer publish knowledge.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from tinyassets.universe_files import read_data_path, unlink_data_path, write_data_path

OKF_VERSION = "0.1"
_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,200}$")
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNIT = "\x1f"

# Fixed relative database names. Conversion never walks an owner tree looking
# for stores; callers may pass an explicit ``stores`` map in tests.
KNOWN_DATABASES: tuple[str, ...] = (
    "knowledge.db",
    "episodic.db",
    ".project_memory.db",
    "temporal.db",
    "versions.db",
    "draft_versions.db",
    "promises.db",
    "daemon_brain.db",
)

_FILE_FORBIDDEN_CONSUMERS = frozenset({
    "tinyassets/owner_router.py",
    "tinyassets/broker/server.py",
    "tinyassets/broker/egress.py",
    "tinyassets/broker/next_wake.py",
    "tinyassets/control_plane/next_wake.py",
})


class ConversionRefused(RuntimeError):
    """Cutover stopped. Nothing was published as the file authority."""

    def __init__(self, message: str, report: "ConversionReport") -> None:
        super().__init__(message)
        self.report = report


@dataclass
class SourceCount:
    """One retired source after classification."""

    source: str
    kind: str
    disposition: str
    input_count: int
    output_count: int
    ids: tuple[str, ...]
    hashes: dict[str, str]
    rebuild_from: str = ""
    unconvertible: tuple[str, ...] = ()


@dataclass
class ConversionReport:
    """Per-source counts, ids and content hashes for one owner."""

    sources: list[SourceCount]
    snapshot_id: str
    reasons: list[str] = field(default_factory=list)

    def source(self, name: str) -> SourceCount:
        for item in self.sources:
            if item.source == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True)
class _TableSpec:
    kind: str
    disposition: str
    rebuild_from: str = ""
    id_keys: tuple[str, ...] = ()
    body_key: str = ""
    allow_blank_tail: bool = False


def _file(directory: str, id_keys: tuple[str, ...], body_key: str, *,
          allow_blank_tail: bool = False) -> _TableSpec:
    return _TableSpec(
        "file", directory, "", id_keys, body_key, allow_blank_tail,
    )


def _operational(module: str, id_keys: tuple[str, ...], *,
                 allow_blank_tail: bool = False) -> _TableSpec:
    return _TableSpec("operational", module, "", id_keys, "", allow_blank_tail)


def _derived(rebuild_from: str, id_keys: tuple[str, ...] = ()) -> _TableSpec:
    return _TableSpec("derived", rebuild_from, rebuild_from, id_keys, "")


# Every CREATE TABLE in the retired stores has a row here. FTS shadow tables
# share the ``daemon_brain_entries_fts`` prefix and rebuild from entry files.
TABLE_SPECS: dict[str, _TableSpec] = {
    "entities": _file("knowledge/graph/entities", ("entity_id",), "public_description"),
    "edges": _file(
        "knowledge/graph/edges", ("source", "target", "relation_type"),
        "relation_type", allow_blank_tail=True,
    ),
    "facts": _file("knowledge/graph/facts", ("fact_id",), "text"),
    "communities": _derived("knowledge.db:entities", ("community_id", "entity_id")),
    "episodic_facts": _file(
        "knowledge/memory/facts", ("universe_id", "fact_id"), "content",
    ),
    "scene_summaries": _operational("tinyassets/memory/episodic.py", ("id",)),
    "style_observations": _operational("tinyassets/memory/episodic.py", ("id",)),
    "reflections": _operational("tinyassets/memory/episodic.py", ("id",)),
    "project_memory": _file(
        "knowledge/project/current", ("project_id", "key"), "value",
    ),
    "project_memory_history": _file(
        "knowledge/project/history", ("id",), "value",
    ),
    "temporal_facts": _file("knowledge/temporal", ("fact_id",), "value"),
    "draft_versions": _operational("tinyassets/memory/versioning.py", ("id",)),
    "series_promises": _operational(
        "tinyassets/memory/promises.py", ("universe_id", "promise_id"),
    ),
    "daemon_brain_entries": _file("knowledge/brain", ("entry_id",), "content"),
    "daemon_memory_events": _operational("tinyassets/daemon_brain.py", ("event_id",)),
    "daemon_memory_promotions": _file(
        "knowledge/brain/promotions", ("promotion_id",), "summary",
    ),
    "daemon_brain_entries_fts": _derived(
        "daemon_brain.db:daemon_brain_entries", ("entry_id",),
    ),
}

DATABASE_TABLES: dict[str, frozenset[str]] = {
    "knowledge.db": frozenset({"entities", "edges", "facts", "communities"}),
    "episodic.db": frozenset({
        "episodic_facts", "scene_summaries", "style_observations", "reflections",
    }),
    ".project_memory.db": frozenset({"project_memory", "project_memory_history"}),
    "temporal.db": frozenset({"temporal_facts"}),
    "versions.db": frozenset({"draft_versions"}),
    "draft_versions.db": frozenset({"draft_versions"}),
    "promises.db": frozenset({"series_promises"}),
    "daemon_brain.db": frozenset({
        "daemon_brain_entries",
        "daemon_brain_entries_fts",
        "daemon_memory_events",
        "daemon_memory_promotions",
    }),
}


FILE_SOURCES: dict[str, dict[str, str]] = {
    "file:notes.json": {
        "kind": "file",
        "disposition": "notes.json",
        "consumer": "tinyassets/notes.py",
    },
    "file:wiki": {
        "kind": "file",
        "disposition": "wiki/",
        "consumer": "tinyassets/api/wiki.py",
    },
    "file:daemon_wikis": {
        "kind": "file",
        "disposition": "daemon_wikis/",
        "consumer": "tinyassets/daemon_wiki.py",
    },
}


def retired_store_inventory() -> dict[str, dict[str, str]]:
    """Name every retired row's file, operational store, or rebuild source.

    Shared router, broker and waker modules are not consumers. Owner knowledge
    stays in the box.
    """
    inventory: dict[str, dict[str, str]] = {}
    for database, tables in DATABASE_TABLES.items():
        for table in tables:
            spec = TABLE_SPECS[table]
            key = f"{database}:{table}"
            inventory[key] = {
                "kind": spec.kind,
                "disposition": spec.disposition,
                "rebuild_from": spec.rebuild_from,
                "consumer": spec.disposition,
            }
    inventory["daemon_brain.db:daemon_brain_entries_fts*"] = {
        "kind": "derived",
        "disposition": "daemon_brain.db:daemon_brain_entries",
        "rebuild_from": "daemon_brain.db:daemon_brain_entries",
        "consumer": "daemon_brain.db:daemon_brain_entries",
    }
    inventory.update(FILE_SOURCES)
    overlap = set(inventory) & {f"module:{name}" for name in _FILE_FORBIDDEN_CONSUMERS}
    if overlap:
        raise RuntimeError(f"inventory names a shared service: {sorted(overlap)}")
    for row in inventory.values():
        if row.get("consumer") in _FILE_FORBIDDEN_CONSUMERS:
            raise RuntimeError("inventory consumer is a shared service")
    return inventory


def _spec_for_table(table: str) -> _TableSpec | None:
    if table.startswith("sqlite_"):
        return _TableSpec("ignore", "sqlite")
    spec = TABLE_SPECS.get(table)
    if spec is not None:
        return spec
    if table.startswith("daemon_brain_entries_fts"):
        return _derived("daemon_brain.db:daemon_brain_entries")
    return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return {"__b64__": base64.b64encode(value).decode("ascii")}
    return value


def content_hash(row: Mapping[str, Any]) -> str:
    """Stable hash of one source row. Owner edits do not rewrite it."""
    payload = json.dumps(
        row, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_jsonable,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_id(row: Mapping[str, Any], spec: _TableSpec) -> str:
    if not spec.id_keys:
        rowid = row.get("_rowid")
        if rowid is not None and str(rowid) != "":
            return f"rowid:{rowid}"
        if row.get("k") not in (None, ""):
            return f"k:{row['k']}"
        if row.get("id") not in (None, ""):
            return f"id:{row['id']}"
        hashed = {key: value for key, value in row.items() if key != "_rowid"}
        return "hash:" + content_hash(hashed)
    parts: list[str] = []
    for index, key in enumerate(spec.id_keys):
        value = row.get(key)
        blank = value is None or str(value) == ""
        tail = spec.allow_blank_tail and index == len(spec.id_keys) - 1
        if blank and not tail:
            return ""
        parts.append("" if value is None else str(value))
    return _UNIT.join(parts)


def _file_token(source_id: str) -> str:
    digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:20]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", source_id).strip("._")[:80]
    return f"{safe or 'row'}-{digest}"


def _record_rel(spec: _TableSpec, source_id: str) -> str:
    return f"{spec.disposition}/{_file_token(source_id)}.md"


def _status_of(table: str, row: Mapping[str, Any]) -> str:
    if table == "daemon_brain_entries":
        return str(row.get("promotion_state") or "candidate")
    if table == "episodic_facts":
        return "promoted" if row.get("promoted") else "active"
    if table == "temporal_facts" and row.get("valid_until"):
        return "superseded"
    return "active"


def _body_of(row: Mapping[str, Any], spec: _TableSpec) -> str:
    if not spec.body_key:
        return ""
    value = row.get(spec.body_key)
    return "" if value is None else str(value)


def render_record(
    *,
    source: str,
    source_id: str,
    row: Mapping[str, Any],
    body: str,
    status: str,
) -> str:
    """Markdown with frontmatter. The body is the field an owner edits."""
    hashed = content_hash(row)
    front = "\n".join([
        "---",
        f"okf_version: {json.dumps(OKF_VERSION)}",
        f"source: {json.dumps(source)}",
        f"source_id: {json.dumps(source_id)}",
        f"source_hash: {json.dumps(hashed)}",
        f"status: {json.dumps(status)}",
        "row: " + json.dumps(row, sort_keys=True, ensure_ascii=False, default=_jsonable),
        "---",
        "",
    ])
    return front + body + "\n"


def parse_record(text: str) -> tuple[dict[str, Any], str]:
    """Split frontmatter from the editable body."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            meta[key.strip()] = raw
    body = text[end + 5:]
    if body.startswith("\n"):
        body = body[1:]
    if body.endswith("\n"):
        body = body[:-1]
    return meta, body


def _read_text(path: Path) -> str | None:
    raw = read_data_path(path)
    if raw is None:
        return None
    return raw.decode("utf-8")


def _write_text(path: Path, text: str, *, mode: str = "replace") -> None:
    write_data_path(path, text, mode=mode)


def files_are_authority(owner_root: Path | str) -> bool:
    """Whether this owner has published the file-authority marker."""
    text = _read_text(Path(owner_root) / "knowledge" / "index.md")
    if text is None:
        return False
    meta, _body = parse_record(text)
    return meta.get("authority") == "files"


def read_record(owner_root: Path | str, source: str, source_id: str) -> str | None:
    """Return the file body for one converted row, or None when no file exists."""
    table = source.split(":", 1)[-1]
    spec = TABLE_SPECS.get(table)
    if spec is None or spec.kind != "file":
        return None
    text = _read_text(Path(owner_root) / _record_rel(spec, source_id))
    if text is None:
        return None
    _meta, body = parse_record(text)
    return body


def _load_saved_row(owner: Path, spec: _TableSpec, source_id: str) -> dict[str, Any] | None:
    text = _read_text(owner / _record_rel(spec, source_id))
    if text is None:
        return None
    meta, body = parse_record(text)
    row = meta.get("row")
    if not isinstance(row, dict):
        return None
    if spec.body_key:
        row = dict(row)
        row[spec.body_key] = body
    return row


def _open_ro(path: Path) -> sqlite3.Connection:
    """Read a stopped snapshot. Immutable open avoids creating a new journal."""
    uri = path.resolve().as_posix()
    try:
        conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(f"file:{uri}?immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _quote_table(table: str) -> str:
    if not _TABLE_NAME.match(table):
        raise ConversionRefused(
            f"unclassified table name {table!r}",
            ConversionReport([], ""),
        )
    return '"' + table.replace('"', '""') + '"'


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _read_table(conn: sqlite3.Connection, table: str) -> tuple[list[dict[str, Any]], str | None]:
    quoted = _quote_table(table)
    try:
        cursor = conn.execute(f"SELECT rowid AS _k1_rowid, * FROM {quoted}")
    except sqlite3.Error:
        try:
            cursor = conn.execute(f"SELECT * FROM {quoted}")
        except sqlite3.Error as exc:
            return [], f"{table} could not be read: {exc}"
    loaded: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        keys = raw.keys() if hasattr(raw, "keys") else []
        row = {key: _jsonable(raw[key]) for key in keys if key != "_k1_rowid"}
        if "_k1_rowid" in keys:
            row["_rowid"] = _jsonable(raw["_k1_rowid"])
        loaded.append(row)
    return loaded, None


@dataclass
class _Pending:
    source: str
    source_id: str
    row: dict[str, Any]
    body: str
    status: str
    rel: str
    hashed: str


def _database_paths(owner: Path, stores: Mapping[str, Path] | None) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for name in KNOWN_DATABASES:
        if stores is not None and name in stores:
            path = Path(stores[name])
        else:
            path = owner / name
        if path.is_file():
            found.append((name, path))
    return found


def _classify_database(
    database: str,
    path: Path,
    problems: list[str],
) -> tuple[list[SourceCount], list[_Pending]]:
    counts: list[SourceCount] = []
    pending: list[_Pending] = []
    conn = _open_ro(path)
    try:
        tables = _list_tables(conn)
        for table in tables:
            spec = _spec_for_table(table)
            source = f"{database}:{table}"
            if spec is None:
                problems.append(f"unclassified table {source}")
                counts.append(SourceCount(
                    source, "unclassified", "", 0, 0, (), {},
                ))
                continue
            if spec.kind == "ignore":
                continue
            rows, error = _read_table(conn, table)
            if error:
                problems.append(error)
                counts.append(SourceCount(source, spec.kind, spec.disposition, 0, 0, (), {}))
                continue
            ids: list[str] = []
            hashes: dict[str, str] = {}
            lost: list[str] = []
            for row in rows:
                identity = _source_id(row, spec)
                if not identity:
                    lost.append(f"{source} row has an empty id")
                    continue
                hashed_row = {key: value for key, value in row.items() if key != "_rowid"}
                digest = content_hash(hashed_row)
                ids.append(identity)
                hashes[identity] = digest
                if spec.kind == "file":
                    pending.append(_Pending(
                        source=source,
                        source_id=identity,
                        row=hashed_row,
                        body=_body_of(hashed_row, spec),
                        status=_status_of(table, hashed_row),
                        rel=_record_rel(spec, identity),
                        hashed=digest,
                    ))
            if len(ids) != len(rows):
                problems.extend(lost)
            counts.append(SourceCount(
                source=source,
                kind=spec.kind,
                disposition=spec.disposition,
                input_count=len(rows),
                output_count=len(ids),
                ids=tuple(ids),
                hashes=hashes,
                rebuild_from=spec.rebuild_from,
                unconvertible=tuple(lost),
            ))
            if len(ids) != len(set(ids)):
                problems.append(f"duplicate ids in {source}")
    finally:
        conn.close()
    return counts, pending


def _walk_markdown(root: Path) -> tuple[list[tuple[str, bytes]], list[str]]:
    """List markdown files under one owner directory without following links."""
    if not root.exists():
        return [], []
    if root.is_symlink():
        return [], [f"{root.name} is a symlink"]
    found: list[tuple[str, bytes]] = []
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                problems.append(f"symlink directory {child}")
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            if path.is_symlink():
                problems.append(f"symlink file {path}")
                continue
            if path.suffix != ".md":
                continue
            raw = read_data_path(path)
            if raw is None:
                problems.append(f"missing file {path}")
                continue
            found.append((path.relative_to(root).as_posix(), raw))
    return found, problems


def _classify_files(owner: Path, problems: list[str]) -> list[SourceCount]:
    counts: list[SourceCount] = []
    notes = owner / "notes.json"
    if notes.is_symlink():
        problems.append("notes.json is a symlink")
        counts.append(SourceCount(
            "file:notes.json", "file", "notes.json", 1, 0, (), {},
            unconvertible=("notes.json is a symlink",),
        ))
    elif notes.is_file():
        raw = read_data_path(notes) or b""
        digest = hashlib.sha256(raw).hexdigest()
        counts.append(SourceCount(
            "file:notes.json", "file", "notes.json", 1, 1, ("notes.json",),
            {"notes.json": digest},
        ))
    else:
        counts.append(SourceCount("file:notes.json", "file", "notes.json", 0, 0, (), {}))

    for key, folder in (("file:wiki", "wiki"), ("file:daemon_wikis", "daemon_wikis")):
        consumer = FILE_SOURCES[key]["consumer"]
        pages, local = _walk_markdown(owner / folder)
        problems.extend(local)
        ids = tuple(rel for rel, _raw in pages)
        hashes = {rel: hashlib.sha256(raw).hexdigest() for rel, raw in pages}
        counts.append(SourceCount(
            key, "file", consumer, len(pages), len(ids) if not local else len(ids),
            ids, hashes, unconvertible=tuple(local),
        ))
    return counts


def _existing_conflict(owner: Path, item: _Pending) -> str | None:
    path = owner / item.rel
    if not path.exists():
        return None
    if path.is_symlink():
        return f"{item.source} {item.source_id} is a symlink"
    text = _read_text(path)
    if text is None:
        return f"{item.source} {item.source_id} file could not be read"
    meta, _body = parse_record(text)
    if meta.get("source_id") != item.source_id:
        return (
            f"{item.source} {item.source_id} already has a file without that identity"
        )
    return None


def convert_owner_knowledge(
    owner_root: Path | str,
    *,
    snapshot_id: str,
    writes_stopped: bool,
    stores: Mapping[str, Path] | None = None,
) -> ConversionReport:
    """Convert one owner's knowledge and memory after a verified snapshot.

    Refuses before publishing ``knowledge/index.md`` when any row is
    unclassified, unconvertible, or missing from the output.
    """
    owner = Path(owner_root)
    reasons: list[str] = []
    if writes_stopped is not True:
        reasons.append("writes are not stopped")
    if not isinstance(snapshot_id, str) or _SNAPSHOT_ID.fullmatch(snapshot_id) is None:
        reasons.append("owner snapshot is not verified")
    if reasons:
        report = ConversionReport([], snapshot_id or "", reasons)
        raise ConversionRefused("; ".join(reasons), report)

    problems: list[str] = []
    sources: list[SourceCount] = []
    pending: list[_Pending] = []
    for database, path in _database_paths(owner, stores):
        counted, staged = _classify_database(database, path, problems)
        sources.extend(counted)
        pending.extend(staged)
    sources.extend(_classify_files(owner, problems))
    for item in pending:
        conflict = _existing_conflict(owner, item)
        if conflict:
            problems.append(conflict)
    for source in sources:
        if source.kind == "unclassified" or source.unconvertible:
            continue
        if source.input_count != source.output_count:
            problems.append(
                f"{source.source} lost rows: {source.input_count} in, {source.output_count} out"
            )
        if len(source.hashes) != source.input_count:
            problems.append(f"{source.source} hash count does not match rows")

    if problems:
        report = ConversionReport(sources, snapshot_id, problems)
        raise ConversionRefused("; ".join(problems), report)

    written: list[Path] = []
    links: list[str] = []
    try:
        for item in pending:
            path = owner / item.rel
            links.append(item.rel.removeprefix("knowledge/"))
            if path.exists():
                continue
            _write_text(path, render_record(
                source=item.source,
                source_id=item.source_id,
                row=item.row,
                body=item.body,
                status=item.status,
            ))
            written.append(path)
            if item.source.endswith(":daemon_brain_entries"):
                _write_fingerprint(owner, item.row)
        _verify_written(owner, pending)
        _publish_authority(owner, snapshot_id, links)
        _ensure_root_index(owner)
    except Exception:
        for path in written:
            try:
                unlink_data_path(path)
            except FileNotFoundError:
                pass
        raise
    return ConversionReport(sources, snapshot_id, [])


def _verify_written(owner: Path, pending: Iterable[_Pending]) -> None:
    for item in pending:
        text = _read_text(owner / item.rel)
        if text is None:
            raise ConversionRefused(
                f"missing output file for {item.source} {item.source_id}",
                ConversionReport([], ""),
            )
        meta, body = parse_record(text)
        if meta.get("source_id") != item.source_id or meta.get("source_hash") != item.hashed:
            raise ConversionRefused(
                f"output mismatch for {item.source} {item.source_id}",
                ConversionReport([], ""),
            )
        # A pre-existing file with this identity keeps an owner edit of the body.
        if not (owner / item.rel).exists():
            raise ConversionRefused(
                f"missing output file for {item.source} {item.source_id}",
                ConversionReport([], ""),
            )
        if body != item.body and meta.get("source_hash") != item.hashed:
            raise ConversionRefused(
                f"output body mismatch for {item.source} {item.source_id}",
                ConversionReport([], ""),
            )


def _publish_authority(owner: Path, snapshot_id: str, links: list[str]) -> None:
    path = owner / "knowledge" / "index.md"
    existing = _read_text(path)
    if existing is not None:
        meta, _body = parse_record(existing)
        if meta.get("authority") == "files" and "okf_version" in existing:
            return
        raise ConversionRefused(
            "knowledge/index.md exists and is not the file-authority marker",
            ConversionReport([], snapshot_id),
        )
    lines = ["# Knowledge", ""]
    for link in sorted(set(links)):
        lines.append(f"- [{link}]({link})")
    body = "\n".join(lines)
    front = "\n".join([
        "---",
        f"okf_version: {json.dumps(OKF_VERSION)}",
        'authority: "files"',
        f"snapshot_id: {json.dumps(snapshot_id)}",
        "---",
        "",
    ])
    _write_text(path, front + body + "\n", mode="exclusive")


def _ensure_root_index(owner: Path) -> None:
    path = owner / "index.md"
    if path.exists():
        return
    text = (
        f"---\nokf_version: {json.dumps(OKF_VERSION)}\n---\n\n"
        "# Bundle Index\n\n- [knowledge](knowledge/index.md)\n"
    )
    _write_text(path, text, mode="exclusive")


def _write_fingerprint(owner: Path, row: Mapping[str, Any]) -> None:
    daemon_id = str(row.get("daemon_id") or "")
    fingerprint = str(row.get("content_fingerprint") or "")
    entry_id = str(row.get("entry_id") or "")
    if not daemon_id or not fingerprint or not entry_id:
        return
    rel = f"knowledge/brain/_fingerprints/{_file_token(daemon_id)}/{fingerprint}"
    _write_text(owner / rel, entry_id + "\n")


def _write_pending_row(
    owner: Path,
    table: str,
    row: dict[str, Any],
    *,
    source_database: str,
) -> None:
    spec = TABLE_SPECS[table]
    identity = _source_id(row, spec)
    if not identity:
        raise ValueError(f"{table} row has an empty id")
    source = f"{source_database}:{table}"
    _write_text(owner / _record_rel(spec, identity), render_record(
        source=source,
        source_id=identity,
        row=row,
        body=_body_of(row, spec),
        status=_status_of(table, row),
    ))
    if table == "daemon_brain_entries":
        _write_fingerprint(owner, row)


def _extra_value(value: Any) -> Any:
    if hasattr(value, "as_row_metadata"):
        return value.as_row_metadata()
    if any(hasattr(value, name) for name in ("universe_id", "goal_id", "branch_id", "user_id")):
        return {
            "universe_id": getattr(value, "universe_id", None),
            "goal_id": getattr(value, "goal_id", None),
            "branch_id": getattr(value, "branch_id", None),
            "user_id": getattr(value, "user_id", None),
        }
    return _jsonable(value)


def _with_extra(row: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if value is not None:
            row[key] = _extra_value(value)
    return row


def write_graph_entity(owner_root: Path | str, entity: Mapping[str, Any], **extra: Any) -> None:
    """Persist one graph entity as a file. The SQL writers are retired."""
    row = _with_extra(
        {key: _jsonable(value) for key, value in dict(entity).items()}, extra,
    )
    _write_pending_row(Path(owner_root), "entities", row, source_database="knowledge.db")


def write_graph_edge(owner_root: Path | str, edge: Mapping[str, Any], **extra: Any) -> None:
    """Persist one graph edge as a file."""
    row = _with_extra(
        {key: _jsonable(value) for key, value in dict(edge).items()}, extra,
    )
    _write_pending_row(Path(owner_root), "edges", row, source_database="knowledge.db")


def write_graph_facts(owner_root: Path | str, facts: Iterable[Any], **extra: Any) -> None:
    """Persist graph facts as files."""
    owner = Path(owner_root)
    for fact in facts:
        if hasattr(fact, "fact_id"):
            row = _fact_row(fact)
        else:
            row = {key: _jsonable(value) for key, value in dict(fact).items()}
        _write_pending_row(
            owner, "facts", _with_extra(row, extra), source_database="knowledge.db",
        )


def _fact_row(fact: Any) -> dict[str, Any]:
    def value(name: str, default: Any = None) -> Any:
        raw = getattr(fact, name, default)
        return raw.value if hasattr(raw, "value") else raw

    return {
        "fact_id": fact.fact_id,
        "text": fact.text,
        "source_type": value("source_type", "narrator_claim"),
        "narrator": fact.narrator,
        "narrator_reliability": fact.narrator_reliability,
        "valid_from_chapter": fact.valid_from_chapter,
        "valid_to_chapter": fact.valid_to_chapter,
        "truth_value_initial": fact.truth_value_initial,
        "truth_value_final": fact.truth_value_final,
        "truth_value_revealed": fact.truth_value_revealed,
        "language_type": value("language_type", "literal"),
        "narrative_function": value("narrative_function", "world_fact"),
        "importance": fact.importance,
        "weight": fact.weight,
        "hardness": fact.hardness,
        "horizon": fact.horizon,
        "provenance": fact.provenance,
        "confidence": fact.confidence,
        "seeded_scene": fact.seeded_scene,
        "access_tier": fact.access_tier,
        "pov_characters": list(fact.pov_characters),
    }


def read_graph_entity(owner_root: Path | str, entity_id: str) -> dict[str, Any] | None:
    """Return the file entity when one exists. Callers prefer it over SQL."""
    spec = TABLE_SPECS["entities"]
    row = _load_saved_row(Path(owner_root), spec, entity_id)
    if row is None:
        return None
    row["entity_id"] = entity_id
    return row


def write_memory_fact(
    owner_root: Path | str,
    *,
    universe_id: str,
    fact_id: str,
    entity: str,
    content: str,
    source_scene: str = "",
    scope: Any = None,
) -> None:
    """Insert or increment one episodic fact file."""
    if not fact_id or not universe_id:
        raise ValueError("fact_id and universe_id are required")
    owner = Path(owner_root)
    spec = TABLE_SPECS["episodic_facts"]
    identity = _UNIT.join((universe_id, fact_id))
    existing = _load_saved_row(owner, spec, identity) or {}
    scenes = list(existing.get("source_scenes") or [])
    if isinstance(scenes, str):
        scenes = json.loads(scenes or "[]")
    if source_scene and source_scene not in scenes:
        scenes.append(source_scene)
    row = {
        "universe_id": universe_id,
        "fact_id": fact_id,
        "entity": entity if not existing else existing.get("entity", entity),
        "content": content if not existing else existing.get("content", content),
        "evidence_count": int(existing.get("evidence_count") or 0) + 1,
        "source_scenes": scenes,
        "promoted": int(existing.get("promoted") or 0),
    }
    if scope is not None:
        row["goal_id"] = getattr(scope, "goal_id", None)
        row["branch_id"] = getattr(scope, "branch_id", None)
        row["user_id"] = getattr(scope, "user_id", None)
    _write_pending_row(owner, "episodic_facts", row, source_database="episodic.db")


def promote_memory_fact(
    owner_root: Path | str,
    universe_id: str,
    fact_id: str,
    *,
    seed: Mapping[str, Any] | None = None,
) -> None:
    """Mark one episodic fact file promoted. The SQL flag writer is retired."""
    owner = Path(owner_root)
    spec = TABLE_SPECS["episodic_facts"]
    identity = _UNIT.join((universe_id, fact_id))
    row = _load_saved_row(owner, spec, identity)
    if row is None:
        if seed is None:
            raise ValueError(f"fact file not found: {fact_id}")
        row = dict(seed)
        row["universe_id"] = universe_id
        row["fact_id"] = fact_id
    row["promoted"] = 1
    _write_pending_row(owner, "episodic_facts", row, source_database="episodic.db")


def list_promotable_memory_facts(
    owner_root: Path | str,
    universe_id: str,
    *,
    threshold: int,
    sql_facts: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """File facts at the threshold, plus SQL facts that have no file yet."""
    owner = Path(owner_root)
    spec = TABLE_SPECS["episodic_facts"]
    folder = owner / spec.disposition
    found: dict[str, dict[str, Any]] = {}
    if folder.is_dir() and not folder.is_symlink():
        for entry in os.scandir(folder):
            if not entry.is_file() or entry.is_symlink():
                continue
            text = _read_text(Path(entry.path))
            if text is None:
                continue
            meta, body = parse_record(text)
            row = meta.get("row")
            if not isinstance(row, dict):
                continue
            if str(row.get("universe_id") or "") != universe_id:
                continue
            row = dict(row)
            row["content"] = body
            fact_id = str(row.get("fact_id") or "")
            if fact_id:
                found[fact_id] = row
    selected: list[dict[str, Any]] = []
    for fact_id, row in found.items():
        if int(row.get("promoted") or 0):
            continue
        if int(row.get("evidence_count") or 0) >= threshold:
            selected.append({
                "fact_id": fact_id,
                "entity": row.get("entity", ""),
                "content": row.get("content", ""),
                "evidence_count": int(row.get("evidence_count") or 0),
                "source_scenes": row.get("source_scenes") or [],
            })
    for fact in sql_facts or ():
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id or fact_id in found:
            continue
        if int(fact.get("evidence_count") or 0) >= threshold:
            selected.append(dict(fact))
    return selected


def read_project_memory(
    owner_root: Path | str, *, project_id: str, key: str,
) -> dict[str, Any] | None:
    """Return one project-memory file, or None when the owner has no file."""
    spec = TABLE_SPECS["project_memory"]
    identity = _UNIT.join((project_id, key))
    row = _load_saved_row(Path(owner_root), spec, identity)
    if row is None:
        return None
    value = row.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return {
        "project_id": project_id,
        "key": key,
        "value": value,
        "updated_at": row.get("updated_at", ""),
        "updated_by": row.get("updated_by", ""),
        "version": int(row.get("version") or 0),
    }


def list_project_memory_files(
    owner_root: Path | str, *, project_id: str,
) -> list[dict[str, Any]]:
    """Project-memory files for one project."""
    owner = Path(owner_root)
    folder = owner / TABLE_SPECS["project_memory"].disposition
    rows: list[dict[str, Any]] = []
    if not folder.is_dir() or folder.is_symlink():
        return rows
    for entry in os.scandir(folder):
        if not entry.is_file() or entry.is_symlink():
            continue
        text = _read_text(Path(entry.path))
        if text is None:
            continue
        meta, body = parse_record(text)
        row = meta.get("row")
        if not isinstance(row, dict) or row.get("project_id") != project_id:
            continue
        value: Any = body
        try:
            value = json.loads(body)
        except json.JSONDecodeError:
            pass
        rows.append({
            "project_id": project_id,
            "key": row.get("key", ""),
            "value": value,
            "updated_at": row.get("updated_at", ""),
            "updated_by": row.get("updated_by", ""),
            "version": int(row.get("version") or 0),
        })
    rows.sort(key=lambda item: str(item["key"]))
    return rows


def write_project_memory(
    owner_root: Path | str,
    *,
    project_id: str,
    key: str,
    value: Any,
    actor: str = "",
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Write one project-memory value and its history as files.

    Optimistic version and account storage charging match the retired SQL
    writer. The account quota still refuses the write before a file is published.
    """
    from datetime import datetime, timezone

    from tinyassets import storage_accounting

    owner = Path(owner_root)
    serialised = json.dumps(value, default=str)
    current = read_project_memory(owner, project_id=project_id, key=key)
    if current is None and not files_are_authority(owner):
        current = _legacy_project_row(owner, project_id, key)
    current_version = int(current["version"]) if current else 0
    if expected_version is not None and current is not None and expected_version != current_version:
        return {
            "conflict": True,
            "current_version": current_version,
            "message": (
                f"Version mismatch for {project_id}/{key}: "
                f"expected {expected_version}, got {current_version}."
            ),
        }
    new_version = current_version + 1
    now = datetime.now(timezone.utc).isoformat()
    row_bytes = sum(len(part.encode("utf-8")) for part in (serialised, project_id, key))
    account = storage_accounting.account_for_actor(owner, actor)
    reservation = storage_accounting.reserve(
        owner,
        account_id=account,
        scope_id=account or "",
        store="project_memory",
        nbytes=2 * row_bytes,
    )
    try:
        row = {
            "project_id": project_id,
            "key": key,
            "value": serialised,
            "updated_at": now,
            "updated_by": actor,
            "version": new_version,
        }
        _write_pending_row(owner, "project_memory", row, source_database=".project_memory.db")
        history = dict(row)
        history["id"] = f"{project_id}{_UNIT}{key}{_UNIT}{new_version}"
        _write_pending_row(
            owner, "project_memory_history", history,
            source_database=".project_memory.db",
        )
    except BaseException:
        storage_accounting.release(reservation)
        raise
    storage_accounting.commit(reservation)
    return {"status": "ok", "version": new_version}


def _legacy_project_row(owner: Path, project_id: str, key: str) -> dict[str, Any] | None:
    database = owner / ".project_memory.db"
    if not database.is_file():
        return None
    conn = _open_ro(database)
    try:
        row = conn.execute(
            "SELECT version, value, updated_at, updated_by FROM project_memory "
            "WHERE project_id = ? AND key = ?",
            (project_id, key),
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "version": int(row["version"]),
        "value": row["value"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def write_brain_entry(owner_root: Path | str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Write one mini-brain entry file, including rejected and superseded rows."""
    row = {key: _jsonable(value) for key, value in dict(entry).items()}
    if not str(row.get("entry_id") or ""):
        raise ValueError("entry_id is required")
    _write_pending_row(
        Path(owner_root), "daemon_brain_entries", row,
        source_database="daemon_brain.db",
    )
    return row


def read_brain_entry(owner_root: Path | str, entry_id: str) -> dict[str, Any] | None:
    """Return one mini-brain file, or None."""
    spec = TABLE_SPECS["daemon_brain_entries"]
    row = _load_saved_row(Path(owner_root), spec, entry_id)
    if row is None:
        return None
    row["entry_id"] = entry_id
    return row


def brain_entry_by_fingerprint(
    owner_root: Path | str, daemon_id: str, fingerprint: str,
) -> dict[str, Any] | None:
    """Resolve a content fingerprint pointer to its entry file."""
    if not daemon_id or not fingerprint:
        return None
    owner = Path(owner_root)
    rel = f"knowledge/brain/_fingerprints/{_file_token(daemon_id)}/{fingerprint}"
    text = _read_text(owner / rel)
    if text is None:
        return None
    entry_id = text.strip()
    entry = read_brain_entry(owner, entry_id)
    if entry is None:
        return None
    if str(entry.get("content_fingerprint") or "") != fingerprint:
        return None
    if str(entry.get("daemon_id") or "") != daemon_id:
        return None
    return entry


def write_brain_promotion(owner_root: Path | str, promotion: Mapping[str, Any]) -> None:
    """Record one promotion as a file. Review history stays with the entry."""
    row = {key: _jsonable(value) for key, value in dict(promotion).items()}
    _write_pending_row(
        Path(owner_root), "daemon_memory_promotions", row,
        source_database="daemon_brain.db",
    )


def write_temporal_fact(owner_root: Path | str, fact: Mapping[str, Any]) -> None:
    """Persist one temporal fact as a file."""
    row = {key: _jsonable(value) for key, value in dict(fact).items()}
    _write_pending_row(Path(owner_root), "temporal_facts", row, source_database="temporal.db")
