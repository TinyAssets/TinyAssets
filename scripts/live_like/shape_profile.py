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
from scripts.live_like.reviewed_columns import REVIEWED_COLUMNS
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
ALTER = re.compile(
    r"\bALTER\s+TABLE\s+([A-Za-z_][A-Za-z_0-9]*)\s+ADD\s+COLUMN\s+"
    r'["`\[]?([A-Za-z_][A-Za-z_0-9]*)\s+(INTEGER|TEXT|REAL|BLOB|NUMERIC)\b',
    re.I,
)
IDENT = re.compile(
    r'^(?:"([A-Za-z_][A-Za-z_0-9]*)"|`([A-Za-z_][A-Za-z_0-9]*)`|'
    r'\[([A-Za-z_][A-Za-z_0-9]*)\]|([A-Za-z_][A-Za-z_0-9]*))'
)
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
            ".owner-cutover.json",
            ".ordinary-admission.json",
            ".router-credentials.json",
            ".disaster-restore.json",
            ".run_recovery.lock",
            ".run_recovery.lock.pid",
            ".ui-preview.lock",
            ".deploy-pending.json",
            ".runtime_status.json",
            ".worker_supervisor.json",
            ".engine_mcp_http_routes.json",
            ".scoped-reset.barrier",
        }
    )
)


def _extension_class(component: str) -> str:
    match = re.search(r"\.(db-(?:wal|shm|journal)|[A-Za-z0-9]{1,10})$", component)
    suffix = "." + match.group(1).lower() if match else ""
    return suffix if suffix in EXTENSIONS else ""


def _normalize_file_pattern(pattern: str) -> str:
    parts = pattern.split("/")
    workspace = "agent-home" in parts or "workspace" in parts
    for index, part in enumerate(parts):
        if part.startswith("<file>"):
            parts[index] = "<file>" + _extension_class(part)
        elif workspace and index == len(parts) - 1:
            parts[index] = "<file>" + _extension_class(part)
        elif part not in PLACEHOLDERS and part not in FIXED_PATH_PARTS:
            parts[index] = "<file>" + _extension_class(part) if index == len(parts) - 1 else "<id>"
    return "/".join(parts)


def _character_class(segment: str) -> str:
    chars = set(segment)
    if chars <= set("0123456789"):
        return "digits"
    if chars <= set("0123456789abcdefABCDEF"):
        return "hex"
    if chars <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        return "letters"
    if chars <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
        return "alphanumeric"
    if chars <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."):
        return "slug"
    if chars <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.<>"):
        return "placeholder-like"
    return "mixed"


def _length_bucket(segment: str) -> str:
    length = len(segment)
    if length <= 8:
        return "1-8"
    if length <= 16:
        return "9-16"
    if length <= 32:
        return "17-32"
    if length <= 64:
        return "33-64"
    return ">64"


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
    """Find code-owned SQL columns without executing source or opening a live store."""
    found: dict[str, dict[str, str]] = {}
    sources = sorted((*source.rglob("*.py"), *source.rglob("*.sql")))
    if (REPO / "scripts/live_like/fixture.py").is_file():
        sources.append(REPO / "scripts/live_like/fixture.py")
    for path in sources:
        code = path.read_text(encoding="utf-8")
        tree = ast.parse(code) if path.suffix == ".py" else None
        # Keep the raw scan for split/dynamic table names in older code. AST
        # fragments supply the accurate column bodies for Python SQL strings.
        sql_fragments: list[str] = [code]
        migrations: list[tuple[str, str, str]] = []
        replacements: set[tuple[str, str]] = set()
        if tree is not None:
            constants: dict[str, str] = {}

            def literal_string(node: ast.AST) -> str | None:
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    return node.value
                if isinstance(node, ast.Name):
                    return constants.get(node.id)
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    left, right = literal_string(node.left), literal_string(node.right)
                    return left + right if left is not None and right is not None else None
                if isinstance(node, ast.JoinedStr):
                    return "".join(
                        part.value if isinstance(part, ast.Constant) else "{dynamic}"
                        for part in node.values
                    )
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    template = literal_string(node.func.value)
                    if (
                        template is not None
                        and "CREATE TABLE" in template
                        and node.func.attr == "format"
                    ):
                        kwargs = {kw.arg: literal_string(kw.value) for kw in node.keywords}
                        if all(value is not None for value in kwargs.values()):
                            try:
                                return template.format(**kwargs)
                            except (IndexError, KeyError, ValueError):
                                pass
                return None

            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = literal_string(node.value)
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if value is not None:
                        for target in targets:
                            if isinstance(target, ast.Name):
                                constants[target.id] = value

            def static_value(node: ast.AST, env: dict[str, object]) -> object | None:
                if isinstance(node, ast.Name):
                    return env.get(node.id)
                try:
                    return ast.literal_eval(node)
                except (ValueError, TypeError):
                    return None

            def sql_value(node: ast.AST, env: dict[str, object]) -> str | None:
                if isinstance(node, ast.JoinedStr):
                    parts = []
                    for part in node.values:
                        if isinstance(part, ast.Constant):
                            parts.append(str(part.value))
                        elif isinstance(part, ast.FormattedValue):
                            value = static_value(part.value, env)
                            parts.append(
                                str(value) if isinstance(value, (str, int)) else "{dynamic}"
                            )
                    return "".join(parts)
                return literal_string(node)

            def scan_statements(
                statements: list[ast.stmt], env: dict[str, object]
            ) -> dict[str, object]:
                local = dict(env)
                for statement in statements:
                    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                        value = static_value(statement.value, local)
                        targets = (
                            statement.targets
                            if isinstance(statement, ast.Assign)
                            else [statement.target]
                        )
                        for target in targets:
                            if isinstance(target, ast.Name) and value is not None:
                                local[target.id] = value
                    elif (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Attribute)
                        and statement.value.func.attr == "append"
                        and isinstance(statement.value.func.value, ast.Name)
                    ):
                        target = statement.value.func.value.id
                        item = static_value(statement.value.args[0], local)
                        if isinstance(local.get(target), list) and item is not None:
                            local[target] = [*local[target], item]
                    elif isinstance(statement, ast.For):
                        values = static_value(statement.iter, local)
                        if isinstance(values, (tuple, list)):
                            for value in values:
                                branch = dict(local)
                                if isinstance(statement.target, ast.Name):
                                    branch[statement.target.id] = value
                                elif (
                                    isinstance(statement.target, (ast.Tuple, ast.List))
                                    and isinstance(value, (tuple, list))
                                ):
                                    for target, item in zip(statement.target.elts, value):
                                        if isinstance(target, ast.Name):
                                            branch[target.id] = item
                                scan_statements(statement.body, branch)
                        continue
                    for node in ast.walk(statement):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                            if node.func.attr in {"execute", "executescript"} and node.args:
                                value = sql_value(node.args[0], local)
                                if value is not None:
                                    sql_fragments.append(value)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                            if (
                                node.func.id in {"_ensure_column", "_add_column"}
                                and len(node.args) >= 4
                            ):
                                args = [static_value(arg, local) for arg in node.args[-3:]]
                                if all(isinstance(arg, str) for arg in args):
                                    migrations.append((args[0], args[1], args[2]))
                            if node.func.id == "_alter" and len(node.args) == 2:
                                args = [static_value(arg, local) for arg in node.args]
                                if all(isinstance(arg, str) for arg in args):
                                    migrations.append(("runs", args[0], args[1]))
                    if isinstance(statement, ast.If):
                        test = statement.test
                        decision = None
                        if (
                            isinstance(test, ast.Compare)
                            and len(test.ops) == len(test.comparators) == 1
                        ):
                            left = static_value(test.left, local)
                            right = static_value(test.comparators[0], local)
                            if left is not None and right is not None:
                                if isinstance(test.ops[0], ast.Eq):
                                    decision = left == right
                                elif isinstance(test.ops[0], ast.NotEq):
                                    decision = left != right
                        if decision is not False:
                            local = scan_statements(statement.body, local)
                        if decision is not True:
                            local = scan_statements(statement.orelse, local)
                    elif isinstance(statement, (ast.Try, ast.With)):
                        local = scan_statements(statement.body, local)
                return local

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    scan_statements(node.body, {})
            for node in ast.walk(tree):
                value = literal_string(node)
                if value is not None:
                    sql_fragments.append(value)
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(
                        isinstance(target, ast.Name) and target.id == "_MIGRATIONS"
                        for target in targets
                    ):
                        try:
                            literal = ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            continue
                        migrations.extend(
                            item
                            for item in literal
                            if isinstance(item, tuple)
                            and len(item) == 3
                            and all(isinstance(value, str) for value in item)
                        )
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "replace"
                    and len(node.args) == 2
                    and all(
                        isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        for arg in node.args
                    )
                ):
                    replacements.add((node.args[0].value, node.args[1].value))
        # Python joins adjacent quoted strings into one AST constant. Scanning
        # source text instead silently loses every column after a quote boundary.
        for sql in sql_fragments:
            for match in CREATE.finditer(sql):
                table = match.group(1)
                start = sql.find("(", match.end())
                if start < 0 or start - match.end() > 80:
                    found.setdefault(table, {})
                    continue
                depth, end = 0, start
                for end in range(start, min(len(sql), start + 30000)):
                    if sql[end] == "(":
                        depth += 1
                    elif sql[end] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                body = re.sub(r"--[^\n]*", "", sql[start + 1 : end])
                columns: dict[str, str] = {}
                for fragment in re.split(r",\s*(?![^()]*\))", body):
                    token = IDENT.match(fragment.strip())
                    if not token or next(g for g in token.groups() if g is not None).upper() in {
                        "PRIMARY",
                        "FOREIGN",
                        "UNIQUE",
                        "CHECK",
                        "CONSTRAINT",
                    }:
                        continue
                    name = next(group for group in token.groups() if group is not None)
                    tail = fragment.strip()[token.end() :].strip()
                    kind = re.match(r"(INTEGER|TEXT|REAL|BLOB|NUMERIC)\b", tail, re.I)
                    if kind:
                        columns[name] = kind.group(1).upper()
                found.setdefault(table, {}).update(columns)
            for match in ALTER.finditer(sql):
                found.setdefault(match.group(1), {})[match.group(2)] = match.group(3).upper()
        for template, actual in replacements:
            if template in found:
                found.setdefault(actual, {}).update(found[template])
        for table, column, declaration in migrations:
            kind = re.match(r"(INTEGER|TEXT|REAL|BLOB|NUMERIC)\b", declaration, re.I)
            if table in found and kind:
                found[table][column] = kind.group(1).upper()
    return found


def column_vocabulary(source: Path = REPO / "tinyassets") -> dict[str, dict[str, str]]:
    """Current code plus reviewed retained or dependency-owned columns."""
    schema = source_schema(source)
    if source.resolve() == (REPO / "tinyassets").resolve():
        for table, columns in REVIEWED_COLUMNS.items():
            allowed = schema.setdefault(table, {})
            for column in columns:
                allowed.setdefault(column, "TEXT")
    return schema


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
            pattern = _normalize_file_pattern(raw_pattern)
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


def profile(
    root: Path, source: Path = REPO / "tinyassets", *, findings: list[str] | None = None
) -> dict:
    root = root.resolve(strict=True)
    schema = column_vocabulary(source)
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
    drift: list[str] = []
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
                        drift.append(f"{name}/{table}: unreviewed table")
                        quoted_table = '"' + table.replace('"', '""') + '"'
                        column_count = len(
                            db.execute(f"PRAGMA table_info({quoted_table})").fetchall()
                        )
                        if column_count:
                            drift.append(f"{name}/{table}: {column_count} unreviewed columns")
                        continue
                    columns = {
                        row[1]: row[2].upper()
                        for row in db.execute(f'PRAGMA table_info("{table}")')
                    }
                    allowed = set(schema.get(table, {})) | set(store["tables"][table]["columns"])
                    unreviewed = set(columns) - allowed
                    if unreviewed:
                        drift.append(f"{name}/{table}: {len(unreviewed)} unreviewed columns")
                    count = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    store["tables"][table] = {
                        "columns": {
                            column: kind for column, kind in columns.items() if column in allowed
                        },
                        "rows": bucket(count),
                    }
    per_owner = Counter(next(iter(v)) for v in centers.values() if len(v) == 1)
    universes = 0
    main = root / ".tinyassets.db"
    if main.is_file():
        with sqlite3.connect(f"file:{main.as_posix()}?mode=ro", uri=True) as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='universes'"
            ).fetchone():
                universes = db.execute("SELECT count(*) FROM universes").fetchone()[0]
    result = {
        "version": 1,
        "counts": {
            "owners": bucket(len(owners)),
            "centers_per_owner": bucket(max(per_owner.values(), default=0)),
            "universes": bucket(universes),
        },
        "stores": stores,
        "files": _file_shapes(root, centers, owners, databases),
        "code_tables": sorted(source_schema(source)),
        "code_stores": source_stores(source),
    }
    if drift:
        if findings is None:
            raise ValueError("\n".join(sorted(set(drift))))
        findings.extend(sorted(set(drift)))
    return result


def check_redaction(document: dict, source: Path = REPO / "tinyassets") -> None:
    schema = column_vocabulary(source)
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
    if set(document["code_tables"]) - set(source_schema(source)):
        raise ValueError("unapproved CREATE TABLE vocabulary")
    if set(document["code_stores"]) - set(source_stores(source)):
        raise ValueError("unapproved store class vocabulary")
    path_failures = []
    store_patterns = [
        (name, _pattern_regex(name)) for name in sorted(document["stores"])
    ]
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
        store_name = next(
            (name for name, matcher in store_patterns if matcher.fullmatch(item["pattern"])),
            None,
        )
        for index, component in enumerate(item["pattern"].split("/")):
            if component in PLACEHOLDERS or component in EXTENSIONS:
                continue
            if component.startswith("<file>") and component[6:] in EXTENSIONS:
                continue
            if component not in FIXED_PATH_PARTS:
                path_failures.append(
                    f"path class={item['class']} segment={index} "
                    f"chars={_character_class(component)} length={_length_bucket(component)} "
                    f"store={store_name or 'none'} table=none"
                )
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
    if path_failures:
        for failure in path_failures:
            print(failure, file=sys.stderr)
        raise ValueError("unapproved path vocabulary")


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
        findings: list[str] = []
        result = profile(args.data, findings=findings)
        check_redaction(result)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if findings:
            parser.exit(1, "\n".join(findings) + "\n")
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
