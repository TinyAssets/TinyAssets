#!/usr/bin/env python3
"""Read-only, redacted census of a backup for the live-like fixture."""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import sqlite3
import stat
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tinyassets.box_layout_migration import CENSUS_RULES, _census_files
from tinyassets.storage_accounting import ROOT_ENTRIES

REPO = Path(__file__).resolve().parents[2]
DEFAULT = REPO / "deploy/live_like/shape-profile.json"
BUCKETS = ("0", "1-10", "11-1k", "1k-100k", ">100k")
EXTENSIONS = frozenset(
    {
        "",
        ".txt",
        ".json",
        ".jsonl",
        ".db",
        ".sqlite3",
        ".lock",
        ".log",
        ".body",
        ".sock",
        ".gz",
        ".toml",
        ".md",
        ".key",
        ".bak",
        ".wal",
        ".shm",
        ".journal",
        ".fifo",
    }
)
PLACEHOLDERS = frozenset(
    {"<owner>", "<center>", "<universe>", "<id>", "<file>", "<router-private>", "<unknown>"}
)
CREATE = re.compile(
    r"\bCREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"[\"`\[]?([A-Za-z_][A-Za-z_0-9]*)",
    re.I,
)
IDENT = re.compile(r"^[\"`\[]?([A-Za-z_][A-Za-z_0-9]*)")
STORE_LITERAL = re.compile(r"^[A-Za-z0-9_./*-]+\.(?:db|sqlite3)$")
FIXED_PATH_PARTS = (
    frozenset(part for path in CENSUS_RULES for part in path.split("/") if part != "*")
    | frozenset(ROOT_ENTRIES)
    | frozenset(
        {
            ".layout.json",
            ".layout.lock",
            "platform-expected-instance.json",
            ".platform",
            "owners",
            "agent-home",
            "workspace",
            ".universe-sidecars",
            ".agent-sessions",
            "state.json",
            "engine.sock",
            "synthetic-target",
            "runs",
            "archived",
            "lancedb",
            "runs",
            "audit.jsonl",
            ".scheduler_fires.jsonl",
            "ledger.json",
            ".credential-vault.json",
            ".credentials",
            ".credentials.json",
            ".oauth-refresh",
            ".deleted",
            "client_id",
            "client_secret",
            "session_secret",
            "release-state.json",
            ".billing_webhook_verified.json",
            "fence.json",
            "architecture-signing.json",
            ".consumer_liveness",
            ".delivery-locks",
            ".workspace-family-locks",
            "activity.log",
            "activity.log.1",
        }
    )
)


def bucket(value: int) -> str:
    if value == 0:
        return BUCKETS[0]
    if value <= 10:
        return BUCKETS[1]
    if value <= 1000:
        return BUCKETS[2]
    if value <= 100000:
        return BUCKETS[3]
    return BUCKETS[4]


def source_schema(source: Path = REPO / "tinyassets") -> dict[str, dict[str, str]]:
    """Find literal CREATE statements; keep vocabulary, never evaluate source."""
    found: dict[str, dict[str, str]] = {}
    sources = sorted((*source.rglob("*.py"), *source.rglob("*.sql")))
    if (REPO / "scripts/live_like/fixture.py").is_file():
        sources.append(REPO / "scripts/live_like/fixture.py")
    for path in sources:
        code = path.read_text(encoding="utf-8")
        for match in CREATE.finditer(code):
            table = match.group(1)
            # SQL is frequently assembled from adjacent Python string literals.
            # The name is enough to detect drift even if columns cannot be parsed.
            start = code.find("(", match.end())
            if start < 0 or start - match.end() > 80:
                found.setdefault(table, {})
                continue
            depth, end = 0, start
            for end in range(start, min(len(code), start + 30000)):
                if code[end] == "(":
                    depth += 1
                elif code[end] == ")":
                    depth -= 1
                    if depth == 0:
                        break
            body = code[start + 1 : end]
            columns: dict[str, str] = {}
            for fragment in re.split(r",\s*(?![^()]*\))", body):
                token = IDENT.match(fragment.strip())
                if not token or token.group(1).upper() in {
                    "PRIMARY",
                    "FOREIGN",
                    "UNIQUE",
                    "CHECK",
                    "CONSTRAINT",
                }:
                    continue
                name = token.group(1)
                tail = fragment.strip()[token.end() :].strip()
                kind = re.match(r"(INTEGER|TEXT|REAL|BLOB|NUMERIC)\b", tail, re.I)
                if kind:
                    columns[name] = kind.group(1).upper()
            if len(columns) > len(found.get(table, {})):
                found[table] = columns
            else:
                found.setdefault(table, {})
    return found


def source_stores(source: Path = REPO / "tinyassets") -> list[str]:
    """Static SQLite filenames used by store classes and their callers."""
    names = set()
    for path in source.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if STORE_LITERAL.fullmatch(value) and ".." not in value.split("/"):
                    names.add(value)
    return sorted(names)


def known_stores(schema: dict[str, dict[str, str]]) -> dict[str, dict]:
    stores = {}
    for path, rules in sorted(CENSUS_RULES.items()):
        pattern = path.replace("*", "<owner>")
        stores[pattern] = {
            "class": sorted({r.placement for r in rules.values()}),
            "tables": {
                name: {
                    "columns": schema.get(name)
                    or {col: "TEXT" for col in sorted(set(rule.principals + rule.centers))}
                    or {"fixture_key": "TEXT"},
                    "rows": "0",
                }
                for name, rule in sorted(rules.items())
            },
        }
    return stores


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    escaped = re.escape(pattern)
    for marker in PLACEHOLDERS:
        escaped = escaped.replace(re.escape(marker), "[^/]+")
    return re.compile("^" + escaped + "$")


def _file_shapes(
    root: Path, centers: dict[str, set[str]], owners: dict[str, int], database_files: set[Path]
) -> list[dict]:
    patterns: dict = {}
    targets: dict = {}
    labels = {owner: "account" for owner in owners}
    _census_files(
        root,
        centers,
        labels,
        database_files,
        Counter(),
        file_targets=targets,
        patterns=patterns,
        uid_owners={str(uid): owner for owner, uid in owners.items()},
    )
    paths = {
        p.relative_to(root).as_posix(): p
        for base, dirs, files in os.walk(root, followlinks=False)
        for p in (
            Path(base) / name
            for name in files + [name for name in dirs if (Path(base) / name).is_symlink()]
        )
    }
    by_inode: Counter = Counter(
        (p.lstat().st_dev, p.lstat().st_ino)
        for p in paths.values()
        if stat.S_ISREG(p.lstat().st_mode)
    )
    ranked = []
    for placement, entries in patterns.items():
        for raw_pattern in entries:
            literal_size = len(re.sub(r"<[^>]+>", "", raw_pattern))
            ranked.append((literal_size, placement, raw_pattern, _pattern_regex(raw_pattern)))
    assigned: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for name, path in paths.items():
        target = targets.get(name)
        placement = "owner" if isinstance(target, tuple) or target == "split" else target
        candidates = [
            (size, key, pattern)
            for size, key, pattern, regex in ranked
            if key == placement and regex.fullmatch(name)
        ]
        if candidates:
            _, key, pattern = max(candidates)
            assigned[key, pattern].append(path)
    output = []
    for placement, entries in sorted(patterns.items()):
        for raw_pattern in sorted(entries):
            # Census owns the masking and placement. Normalize unapproved suffixes.
            pattern = re.sub(
                r"<file>\.[A-Za-z0-9]{1,10}",
                lambda m: m.group() if m.group()[6:] in EXTENSIONS else "<file>",
                raw_pattern,
            )
            matches = assigned.get((placement, raw_pattern), [])
            if not matches:
                continue
            kinds = Counter()
            sizes = Counter()
            links = Counter()
            for path in matches:
                mode = path.lstat().st_mode
                kind = (
                    "socket"
                    if stat.S_ISSOCK(mode)
                    else "fifo"
                    if stat.S_ISFIFO(mode)
                    else "symlink"
                    if stat.S_ISLNK(mode)
                    else "file"
                )
                kinds[kind] += 1
                if kind == "file":
                    sizes[bucket(path.lstat().st_size)] += 1
                    if by_inode[path.lstat().st_dev, path.lstat().st_ino] > 1:
                        links["hardlink"] += 1
            output.append(
                {
                    "class": placement,
                    "pattern": pattern,
                    "count": bucket(len(matches)),
                    "kinds": {k: bucket(v) for k, v in sorted(kinds.items())},
                    "sizes": {k: bucket(v) for k, v in sorted(sizes.items())},
                    "links": {k: bucket(v) for k, v in sorted(links.items())},
                }
            )
    return output


def profile(root: Path, source: Path = REPO / "tinyassets") -> dict:
    root = root.resolve(strict=True)
    schema = source_schema(source)
    stores = known_stores(schema)
    owners: dict[str, int] = {}
    centers: dict[str, set[str]] = {}
    identities = root / ".broker/state/owner-identities.db"
    if identities.is_file():
        with sqlite3.connect(f"file:{identities.as_posix()}?mode=ro", uri=True) as db:
            owners = dict(db.execute("SELECT principal,machine_id FROM owner_identities"))
            for principal, center in db.execute(
                "SELECT principal,center FROM center_admissions WHERE event='admit'"
            ):
                centers.setdefault(center, set()).add(principal)
    databases = set()
    for name, store in stores.items():
        candidates = (
            list(root.glob(name.replace("<owner>", "*"))) if "<owner>" in name else [root / name]
        )
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            databases.add(path)
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    if table.startswith("sqlite_"):
                        continue
                    if table not in store["tables"]:
                        raise ValueError(f"unreviewed table in {name}")
                    columns = {
                        row[1]: row[2].upper()
                        for row in db.execute(f'PRAGMA table_info("{table}")')
                    }
                    allowed = set(schema.get(table, {})) | set(store["tables"][table]["columns"])
                    if not set(columns) <= allowed:
                        raise ValueError(f"unreviewed columns in {name}/{table}")
                    count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    store["tables"][table] = {"columns": columns, "rows": bucket(count)}
    per_owner = Counter(next(iter(v)) for v in centers.values() if len(v) == 1)
    universes = 0
    main = root / ".tinyassets.db"
    if main.is_file():
        with sqlite3.connect(f"file:{main.as_posix()}?mode=ro", uri=True) as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='universes'"
            ).fetchone():
                universes = db.execute("SELECT count(*) FROM universes").fetchone()[0]
    return {
        "version": 1,
        "counts": {
            "owners": bucket(len(owners)),
            "centers_per_owner": bucket(max(per_owner.values(), default=0)),
            "universes": bucket(universes),
        },
        "stores": stores,
        "files": _file_shapes(root, centers, owners, databases),
        "code_tables": sorted(schema),
        "code_stores": source_stores(source),
    }


def check_redaction(document: dict, source: Path = REPO / "tinyassets") -> None:
    schema = source_schema(source)
    known_columns = set().union(*(set(cols) for cols in schema.values()))
    for rules in CENSUS_RULES.values():
        for rule in rules.values():
            known_columns.update(rule.principals + rule.centers)
    known_columns.add("fixture_key")
    if set(document) != {"version", "counts", "stores", "files", "code_tables", "code_stores"}:
        raise ValueError("unexpected profile fields")
    if document["version"] != 1 or set(document["counts"]) != {
        "owners",
        "centers_per_owner",
        "universes",
    }:
        raise ValueError("unexpected profile structure")
    if any(v not in BUCKETS for v in document["counts"].values()):
        raise ValueError("unapproved count bucket")
    if set(document["stores"]) - {name.replace("*", "<owner>") for name in CENSUS_RULES}:
        raise ValueError("unapproved store path")
    for name, store in document["stores"].items():
        census_name = name.replace("<owner>", "*")
        if set(store) != {"class", "tables"} or set(store["class"]) - {
            r.placement for r in CENSUS_RULES[census_name].values()
        }:
            raise ValueError(f"unapproved store metadata: {name}")
        if set(store["tables"]) - set(CENSUS_RULES[census_name]):
            raise ValueError(f"unapproved table: {name}")
        for table, details in store["tables"].items():
            if set(details) != {"columns", "rows"} or details["rows"] not in BUCKETS:
                raise ValueError(f"unapproved table metadata: {table}")
            if set(details["columns"]) - known_columns or set(details["columns"].values()) - {
                "",
                "TEXT",
                "INTEGER",
                "REAL",
                "BLOB",
                "NUMERIC",
            }:
                raise ValueError(f"unapproved schema vocabulary: {table}")
    if set(document["code_tables"]) - set(schema):
        raise ValueError("unapproved CREATE TABLE vocabulary")
    if set(document["code_stores"]) - set(source_stores(source)):
        raise ValueError("unapproved store class vocabulary")
    for item in document["files"]:
        if set(item) != {"class", "pattern", "count", "kinds", "sizes", "links"}:
            raise ValueError("unapproved file metadata")
        if item["class"] not in {
            "owner",
            "router",
            "broker",
            "published",
            "dropped",
            "regenerated",
            "database_files",
            "split",
            "unattributed",
        }:
            raise ValueError("unapproved file class")
        for component in item["pattern"].split("/"):
            if component in PLACEHOLDERS or component in EXTENSIONS:
                continue
            if component.startswith("<file>") and component[6:] in EXTENSIONS:
                continue
            if component not in FIXED_PATH_PARTS:
                raise ValueError("unapproved path vocabulary")
        if item["count"] not in BUCKETS or any(
            v not in BUCKETS
            for section in ("kinds", "sizes", "links")
            for v in item[section].values()
        ):
            raise ValueError("unapproved file bucket")
        if (
            set(item["kinds"]) - {"file", "socket", "fifo", "symlink"}
            or set(item["sizes"]) - set(BUCKETS)
            or set(item["links"]) - {"hardlink"}
        ):
            raise ValueError("unapproved file kind")


def diff(committed: dict, fresh: dict, *, require_fixture: bool = True) -> list[str]:
    errors = []
    for name, store in fresh["stores"].items():
        if name not in committed["stores"]:
            errors.append(f"store missing from profile: {name}")
            continue
        for table, info in store["tables"].items():
            old = committed["stores"][name]["tables"].get(table)
            if old is None:
                errors.append(f"table missing from profile: {name}/{table}")
            elif set(info["columns"]) - set(old["columns"]):
                missing = sorted(set(info["columns"]) - set(old["columns"]))
                errors.append(f"columns missing from profile: {name}/{table}: {missing}")
    for table in set(fresh["code_tables"]) - set(committed["code_tables"]):
        errors.append(f"new CREATE TABLE missing from profile: {table}")
    for store in set(fresh["code_stores"]) - set(committed["code_stores"]):
        errors.append(f"new SQLite store missing from profile: {store}")
    old_files = {
        (i["class"], i["pattern"], tuple(sorted(i["kinds"])), tuple(sorted(i["links"])))
        for i in committed["files"]
    }
    for item in fresh["files"]:
        shape = (
            item["class"],
            item["pattern"],
            tuple(sorted(item["kinds"])),
            tuple(sorted(item["links"])),
        )
        if shape not in old_files:
            errors.append(
                f"file shape missing from profile: {item['class']} {item['pattern']} "
                f"{shape[2]} {shape[3]}"
            )
    if require_fixture:
        from scripts.live_like.fixture import fixture_coverage

        errors.extend(fixture_coverage(committed))
    return sorted(errors)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("profile")
    make.add_argument("--data", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--check-redaction", action="store_true")
    check = sub.add_parser("check-redaction")
    check.add_argument("--profile", type=Path, required=True)
    compare = sub.add_parser("diff")
    compare.add_argument("--profile", type=Path, default=DEFAULT)
    compare.add_argument("--fresh", type=Path)
    compare.add_argument("--source", type=Path, default=REPO / "tinyassets")
    args = parser.parse_args()
    if args.command == "profile":
        result = profile(args.data)
        check_redaction(result)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif args.command == "check-redaction":
        check_redaction(json.loads(args.profile.read_text(encoding="utf-8")))
    else:
        committed = json.loads(args.profile.read_text(encoding="utf-8"))
        check_redaction(committed)
        fresh = (
            json.loads(args.fresh.read_text(encoding="utf-8"))
            if args.fresh
            else {
                **committed,
                "stores": known_stores(source_schema(args.source)),
                "code_tables": sorted(source_schema(args.source)),
                "code_stores": source_stores(args.source),
            }
        )
        if args.fresh:
            check_redaction(fresh)
            before = json.dumps(committed, indent=2, sort_keys=True).splitlines()
            after = json.dumps(fresh, indent=2, sort_keys=True).splitlines()
            print(
                "\n".join(
                    difflib.unified_diff(
                        before, after, fromfile="committed profile", tofile="fresh backup profile"
                    )
                )
            )
        failures = diff(committed, fresh)
        if failures:
            parser.exit(1, "\n".join(failures) + "\n")
        print("shape profile and fixture cover code shapes")


if __name__ == "__main__":
    main()
