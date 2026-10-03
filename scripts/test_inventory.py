#!/usr/bin/env python3
"""Inventory the test suite so its size and relevance are measured, not guessed.

The suite is ~1.5x the product by line count. Most of that is fine; some of it
asserts features that are gone, pins hand-copied copies of another fact, or is
skipped everywhere CI runs. None of that is visible from a green check, because
a test that never runs and a test that passes print the same word.

This walks ``tests/`` statically (AST, no imports, no collection) and, when
given CI junit, joins in what CI actually did. It answers:

* **dead targets** -- imports of ``tinyassets``/``scripts`` modules or names
  that no longer exist, and literal repo paths a test reads that are missing;
* **test-only modules** -- ``tinyassets`` modules no production code imports,
  so their tests guard nothing a user can reach;
* **markers** -- every skip/skipif/xfail/importorskip with its reason and the
  git-blame date of the line;
* **never run in CI** -- tests CI junit records as skipped (CI is Linux only,
  apart from one Windows job running one file);
* **known-failing** -- each ``.github/known-failing-tests.txt`` entry with its
  age and the failure CI recorded for it;
* **duplicates** -- test bodies identical across files (exact), and literal
  lists of 6+ strings asserted for equality (pinned copies of another fact);
* **slowest** -- the slowest tests by CI junit time;
* **importers** -- ``tinyassets`` modules reached (directly / transitively at
  module level) by the most test files, which is what makes selections large;
* **low-signal files** -- a HEURISTIC, stated as one: unchanged for 90 days, no
  failure in the supplied junit window, and every ``tinyassets`` module it
  imports is also imported by at least five other test files. This is not
  coverage: it nominates files for a human to read, never for deletion.

Usage::

    python scripts/test_inventory.py --json inv.json --markdown inv.md \\
        [--junit-dir DIR ...] [--no-blame]

``--junit-dir`` may repeat; every ``*.xml`` under each is read. Durations and
skips come from the first window, failures from all of them.

Exit codes: ``0`` report written, ``2`` the repo or an input cannot be read.
The report never fails on its findings -- gates are separate, so a report run
can never be the thing that blocks a merge.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "test-inventory/1"

#: Packages whose imports are checked for existence. Tests import scripts as
#: ``scripts.x`` (package) or, after a sys.path insert, as bare ``x``; only the
#: package form is resolvable statically.
CHECKED_ROOTS = ("tinyassets", "scripts")

#: A literal that looks like a repo-relative file path. Only these prefixes:
#: tests also build paths inside tmp dirs, and a bare ``foo/bar.py`` is as
#: likely a fixture name as a repo file.
_REPO_PATH_RE = re.compile(
    r"^(?:scripts|tinyassets|docs|\.github|deploy|packaging|openspec|WebSite|\.agents|\.claude)"
    r"/[\w.@/-]+\.[A-Za-z0-9]+$"
)

_MARKER_KINDS = ("skip", "skipif", "xfail", "importorskip")
#: A reason that names a date the marker is allowed to live until. The CI
#: expiry gate reads the same token, so the report and the gate agree.
EXPIRY_RE = re.compile(r"\bexpires?[=:]\s*(\d{4}-\d{2}-\d{2})\b")

# ---- is a skip an environment guard? -------------------------------------------
#
# Lexical on purpose: the reason string is the only thing a skip must carry.
# An environment guard (a platform fact, a missing installable tool) runs
# wherever that platform or tool exists; anything else suppresses a case
# nobody may ever run. Moved here from the retired scripts/skip_census.py.
# Pending wording wins over every class: "Linux implementation pending" is not
# a platform guard. Known limit: a homonym ("the windows overlap") misreads.

_PLATFORM_WORDS = (
    "posix windows win32 win64 linux darwin macos unix nt wsl bwrap bubblewrap symlink "
    "symlinks symlinked junction junctions fifo mkfifo dir_fd o_nofollow max_path cygwin procfs"
).split()
_PLATFORM_FRAGMENTS = ("os.name", "sys.platform", "/proc", "max path", "/bin/", "/usr/", "/dev/")
_TOOL_WORDS = (
    "git node npm yarn docker gh curl bash shellcheck actionlint ffmpeg pandoc java sqlite3 "
    "graphviz pyyaml flock"
).split()
_ABSENCE_FRAGMENTS = (
    "no ",
    "not installed",
    "not available",
    "unavailable",
    "missing",
    "absent",
    "not found",
    "there is none",
    "none here",
    "requires ",
    "needs ",
    "could not find",
    "cannot find",
    "not on path",
    "is not on this",
)
_INSTALLABLE_FRAGMENTS = (
    "not installed",
    "not on path",
    "could not find",
    "cannot find",
    "not importable",
    "no such binary",
    "is missing",
)
_PLATFORM_RE = re.compile(r"\b(" + "|".join(map(re.escape, _PLATFORM_WORDS)) + r")\b")
_TOOL_RE = re.compile(r"\b(" + "|".join(map(re.escape, _TOOL_WORDS)) + r")\b")
PENDING_RE = re.compile(
    r"\b(pending|todo|awaits?|awaiting|until|not\s+(?:yet\s+)?"
    r"(?:landed|implemented|created|wired|built|done|ready))\b",
    re.IGNORECASE,
)


def is_environment_guard(reason: str) -> bool:
    """True when a skip reason names a platform fact or a missing installable tool."""
    text = (reason or "").lower()
    if PENDING_RE.search(text):
        return False
    if any(f in text for f in _INSTALLABLE_FRAGMENTS):
        return True
    if _TOOL_RE.search(text) and any(f in text for f in _ABSENCE_FRAGMENTS):
        return True
    return bool(_PLATFORM_RE.search(text) or any(f in text for f in _PLATFORM_FRAGMENTS))


PINNED_MIN_STRINGS = 6
TEXT_SUFFIXES = (
    ".py",
    ".yml",
    ".yaml",
    ".toml",
    ".sh",
    ".json",
    ".cfg",
    ".ps1",
    ".ts",
    ".js",
    ".spec",
    "",
)
LOW_SIGNAL_DAYS = 90
LOW_SIGNAL_MIN_OTHER_IMPORTERS = 5


# ---- module index -----------------------------------------------------------


@dataclass
class ModuleInfo:
    path: Path
    is_package: bool
    names: set[str] | None = None  # top-level bound names; None = dynamic


def build_module_index(root: Path) -> dict[str, ModuleInfo]:
    index: dict[str, ModuleInfo] = {}
    for top in CHECKED_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).with_suffix("")
            parts = list(rel.parts)
            is_pkg = parts[-1] == "__init__"
            if is_pkg:
                parts = parts[:-1]
            index[".".join(parts)] = ModuleInfo(path=path, is_package=is_pkg)
            # Namespace packages (a directory with no __init__.py) import fine.
            for i in range(1, len(parts)):
                index.setdefault(".".join(parts[:i]), ModuleInfo(path=path.parent, is_package=True))
    return index


def module_names(info: ModuleInfo) -> set[str] | None:
    """Top-level names a module binds, or None when it can bind names lazily."""
    if info.names is not None:
        return info.names
    if info.path.is_dir():
        return None
    try:
        tree = ast.parse(info.path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    names: set[str] = set()
    for node in _top_level(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "__getattr__":
                return None
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                names.update(_target_names(tgt))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names.update(_target_names(node.target))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    return None
                names.add((alias.asname or alias.name).split(".")[0])
    # `global x` inside a function binds a module name when it runs.
    names.update(n for node in ast.walk(tree) if isinstance(node, ast.Global) for n in node.names)
    info.names = names
    return names


def _top_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements at module level, descending into if/try/with blocks."""
    for node in body:
        yield node
        if isinstance(node, (ast.If, ast.Try, ast.With)):
            yield from _top_level(node.body)
            yield from _top_level(getattr(node, "orelse", []))
            yield from _top_level(getattr(node, "finalbody", []))
            for handler in getattr(node, "handlers", []):
                yield from _top_level(handler.body)


def _target_names(tgt: ast.AST) -> Iterator[str]:
    if isinstance(tgt, ast.Name):
        yield tgt.id
    elif isinstance(tgt, (ast.Tuple, ast.List)):
        for elt in tgt.elts:
            yield from _target_names(elt)


def module_level_imports(tree: ast.Module, own: str | None = None) -> set[str]:
    """Checked-root modules a file imports at module level (collection time)."""
    found: set[str] = set()
    for node in _top_level(tree.body):
        found.update(_imports_of(node, own))
    return found


def _imports_of(node: ast.AST, own: str | None) -> Iterator[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in CHECKED_ROOTS:
                yield alias.name
    elif isinstance(node, ast.ImportFrom):
        mod = _resolve_from(node, own)
        if mod and mod.split(".")[0] in CHECKED_ROOTS:
            yield mod
            for alias in node.names:
                yield f"{mod}.{alias.name}"  # may be a submodule; filtered later


def _resolve_from(node: ast.ImportFrom, own: str | None) -> str | None:
    if not node.level:
        return node.module
    if own is None:
        return None
    base = own.split(".")
    base = base[: len(base) - node.level]
    return ".".join(base + ([node.module] if node.module else [])) or None


# ---- test file scan ---------------------------------------------------------


@dataclass
class Marker:
    file: str
    line: int
    kind: str
    reason: str
    scope: str  # "module" | "class" | "function" | "call"
    date: str | None = None
    expires: str | None = None


@dataclass
class InventoryFile:
    path: str
    lines: int
    tests: int
    area: str
    imports: list[str] = field(default_factory=list)
    module_imports: list[str] = field(default_factory=list)
    last_change: str | None = None


def iter_test_files(root: Path) -> Iterator[Path]:
    for path in sorted((root / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            yield path


def is_test_func(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
        "test"
    )


def iter_tests(tree: ast.Module) -> Iterator[tuple[str | None, ast.FunctionDef]]:
    for node in tree.body:
        if is_test_func(node):
            yield None, node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if is_test_func(sub):
                    yield node.name, sub


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local names bound to pytest / unittest or their members.

    `import pytest as p` and `from pytest import skip` are ordinary spellings;
    without this a marker written through them is invisible.
    """
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("pytest", "unittest") and a.asname:
                    names[a.asname] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module in ("pytest", "unittest", "_pytest"):
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _marker_kind(func: ast.AST, aliases: dict[str, str] | None = None) -> str | None:
    name = _dotted(func)
    head, _, tail = name.partition(".")
    if aliases and head in aliases:
        name = aliases[head] + ("." + tail if tail else "")
    if name.startswith(("pytest.mark.", "pytest.", "mark.", "unittest.")):
        last = name.rsplit(".", 1)[-1]
        if last in _MARKER_KINDS:
            return last
        if last in ("skipIf", "skipUnless"):
            return "skipif"
        if last == "SkipTest":
            return "skip"
    return None


def _str_of(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{...}" for v in node.values)
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on parsed trees
        return "<expr>"


def _reason(call: ast.Call, kind: str) -> str:
    for kw in call.keywords:
        if kw.arg in ("reason", "msg"):
            return _str_of(kw.value)
    if kind in ("skip", "importorskip") and call.args:
        if kind == "importorskip":
            return f"importorskip({_str_of(call.args[0])})"
        return _str_of(call.args[0])
    if kind in ("skipif", "xfail") and len(call.args) >= 2:
        return _str_of(call.args[1])
    if kind in ("skipif", "xfail") and call.args:
        return f"<no reason; condition {_str_of(call.args[0])}>"
    return ""


def scan_markers(tree: ast.Module, rel: str) -> list[Marker]:
    markers: list[Marker] = []
    decorator_ids: set[int] = set()
    aliases = _aliases(tree)

    def from_decorators(decos: list[ast.expr], scope: str) -> None:
        for deco in decos:
            target = deco.func if isinstance(deco, ast.Call) else deco
            kind = _marker_kind(target, aliases)
            if kind:
                decorator_ids.add(id(deco))
                reason = _reason(deco, kind) if isinstance(deco, ast.Call) else ""
                markers.append(Marker(rel, deco.lineno, kind, reason, scope))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            from_decorators(node.decorator_list, "class")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            from_decorators(node.decorator_list, "function")
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            values = (
                node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            )
            for val in values:
                target = val.func if isinstance(val, ast.Call) else val
                kind = _marker_kind(target, aliases)
                if kind:
                    decorator_ids.add(id(val))
                    reason = _reason(val, kind) if isinstance(val, ast.Call) else ""
                    markers.append(Marker(rel, val.lineno, kind, reason, "module"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in decorator_ids:
            kind = _marker_kind(node.func, aliases)
            # A bare call of pytest.mark.skipif(...) not used as a decorator is
            # usually a pytest.param(marks=...) entry; count it as a marker too.
            if kind:
                markers.append(Marker(rel, node.lineno, kind, _reason(node, kind), "call"))
            # pytest.param(..., marks=pytest.mark.skip) -- a mark that is
            # neither a decorator nor a call, alone or in a list.
            for kw in node.keywords:
                if kw.arg != "marks":
                    continue
                vals = kw.value.elts if isinstance(kw.value, (ast.List, ast.Tuple)) else [kw.value]
                for val in vals:
                    if not isinstance(val, ast.Call):
                        kind = _marker_kind(val, aliases)
                        if kind:
                            markers.append(Marker(rel, val.lineno, kind, "", "param"))
    for m in markers:
        hit = EXPIRY_RE.search(m.reason)
        m.expires = hit.group(1) if hit else None
    return markers


def _norm_body(func: ast.FunctionDef) -> str:
    body = list(func.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
    ):
        body = body[1:]  # docstring
    if not body:
        return ""
    # Decorators and arguments are part of identity: one body parametrized over
    # two different tables is two tests, not a duplicate.
    mod = ast.Module(body=[*func.decorator_list, func.args, *body], type_ignores=[])
    return ast.dump(mod, annotate_fields=False, include_attributes=False)


_CALENDAR_CALLS = {
    "datetime.now",
    "datetime.utcnow",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "date.today",
    "datetime.date.today",
}


def soft_hermetic_calls(tree: ast.Module) -> int:
    """Real-calendar reads plus sleeps under 0.5 s: counted weekly, never gated."""
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name in _CALENDAR_CALLS:
            n += 1
        elif name in ("time.sleep", "asyncio.sleep") and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                n += 0 < arg.value < 0.5
    return n


def pinned_lists(tree: ast.Module) -> Iterator[tuple[int, int, str]]:
    """(line, n_strings, digest) for `assert x == <literal of N+ strings>`.

    The digest is over the SET of strings, so the same fact pinned as a list
    in one file and a set in another still groups as one duplicate pin.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not any(isinstance(op, ast.Eq) for op in node.ops):
            continue
        for side in [node.left, *node.comparators]:
            lit = side
            if (
                isinstance(lit, ast.Call)
                and _dotted(lit.func) in ("set", "frozenset", "tuple", "list", "sorted")
                and lit.args
            ):
                lit = lit.args[0]
            if isinstance(lit, (ast.List, ast.Set, ast.Tuple)):
                n = sum(
                    1 for e in lit.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                )
                if n >= PINNED_MIN_STRINGS:
                    strings = sorted(
                        {
                            e.value
                            for e in lit.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        }
                    )
                    yield node.lineno, n, hashlib.sha1("\0".join(strings).encode()).hexdigest()[:12]
                    break


def repo_path_literals(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Repo-relative file paths a test names: string literals and `/` joins."""
    seen: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and id(node) not in seen:
            parts: list[str] = []
            cur: ast.AST = node
            ok = True
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                seen.add(id(cur))
                if isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                    parts.append(cur.right.value)
                else:
                    ok = False
                    break
                cur = cur.left
            if ok and len(parts) >= 2:
                cand = "/".join(reversed(parts))
                if _REPO_PATH_RE.match(cand):
                    yield node.lineno, cand
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _REPO_PATH_RE.match(node.value):
                yield node.lineno, node.value


def _group_by(rows: list[dict], key: str) -> list[list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return list(groups.values())


def _named_elsewhere(mod: str, rel: str, text: str) -> bool:
    """Is a module named by dotted path or file path outside its own file?"""
    return text.count(mod) > 0 or text.count(rel) > 0


def area_of(rel: str, imports: Iterable[str]) -> str:
    """Stable bucket: a tests/ subdirectory, else the most-imported tinyassets unit."""
    parts = rel.split("/")
    if len(parts) > 2:
        return parts[1]
    tops = Counter(
        i.split(".")[1] for i in imports if i.startswith("tinyassets.") and i.count(".") >= 1
    )
    if not tops:
        return "scripts" if any(i.startswith("scripts") for i in imports) else "unscoped"
    best = max(tops.values())
    return sorted(k for k, v in tops.items() if v == best)[0]


# ---- git --------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def last_change_dates(root: Path) -> dict[str, str]:
    """Most recent commit date per path under tests/ -- one git log, not N."""
    dates: dict[str, str] = {}
    current = None
    for line in _git(root, "log", "--format=@%cs", "--name-only", "--", "tests").splitlines():
        if line.startswith("@"):
            current = line[1:]
        elif line and current:
            dates.setdefault(line, current)
    return dates


def blame_dates(root: Path, rel: str, lines: Iterable[int]) -> dict[int, str]:
    wanted = sorted(set(lines))
    if not wanted:
        return {}
    args = ["blame", "--line-porcelain"]
    for n in wanted:
        args += ["-L", f"{n},{n}"]
    try:
        out = _git(root, *args, "--", rel)
    except RuntimeError:
        return {}
    result: dict[int, str] = {}
    current_line = None
    for line in out.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            current_line = int(line.split()[2])
        elif line.startswith("committer-time ") and current_line is not None:
            ts = int(line.split()[1])
            result[current_line] = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
    return result


# ---- junit ------------------------------------------------------------------


@dataclass
class JunitWindow:
    durations: dict[str, float] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)
    failure_text: dict[str, str] = field(default_factory=dict)
    ran: set[str] = field(default_factory=set)


def read_junit(dirs: list[Path]) -> JunitWindow:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from ci_required_tests import node_id  # one definition of a node id

    win = JunitWindow()
    for i, d in enumerate(dirs):
        for xml in sorted(d.rglob("*.xml")):
            root = ET.parse(xml).getroot()
            for tc in root.iter("testcase"):
                nid = node_id(tc)
                skipped = tc.find("skipped")
                if i == 0:
                    if skipped is not None:
                        win.skipped[nid] = skipped.get("message") or ""
                    else:
                        win.durations[nid] = float(tc.get("time") or 0)
                        win.ran.add(nid)
                bad = tc.find("failure")
                if bad is None:
                    bad = tc.find("error")
                if bad is not None:
                    win.failing.add(nid)
                    win.failure_text.setdefault(nid, (bad.get("message") or "")[:300])
    return win


# ---- known-failing ----------------------------------------------------------


def known_failing(root: Path, blame: bool) -> list[dict]:
    path = root / ".github" / "known-failing-tests.txt"
    if not path.exists():
        return []
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from ci_required_tests import split_ledger_line  # the gate's own parser

    entries = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        parsed = split_ledger_line(raw)
        if parsed is None:
            continue
        flaky, nid, fields = parsed
        entries.append({"line": n, "node": nid, "flaky": flaky, "expires": fields.get("expires")})
    if blame:
        dates = blame_dates(root, ".github/known-failing-tests.txt", [e["line"] for e in entries])
        for e in entries:
            e["date"] = dates.get(e["line"])
    for e in entries:
        file_part, _, rest = e["node"].partition("::")
        fpath = root / file_part
        e["file_exists"] = fpath.exists()
        e["test_exists"] = fpath.exists() and node_resolves(fpath, rest)
    return entries


def node_resolves(path: Path, rest: str) -> bool:
    """Could `rest` (the node id after `file::`) still name a test in `path`?

    Conservative: only a PROVEN absence returns False. The parameter suffix is
    cut first, because a parameter id may itself contain `::`; a collection-
    error id (`tests.x`) names the module; a class with bases may inherit the
    method; a module-level name may be imported rather than defined.
    """
    bracket = rest.find("[")
    parts = (rest[:bracket] if bracket >= 0 else rest).split("::")
    if not parts or not parts[0] or parts[0].startswith("tests."):
        return True
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return True
    scope: list[ast.stmt] = tree.body
    for i, name in enumerate(parts):
        last = i == len(parts) - 1
        bound = [
            n
            for n in _top_level(scope)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.name == name
        ]
        if not bound:
            # An import or assignment may bind it; that is not a proven absence.
            other = any(
                (
                    isinstance(n, (ast.Import, ast.ImportFrom))
                    and any((a.asname or a.name).split(".")[0] == name for a in n.names)
                )
                or (
                    isinstance(n, ast.Assign)
                    and name in {t for tgt in n.targets for t in _target_names(tgt)}
                )
                for n in _top_level(scope)
            )
            return other
        node = bound[-1]
        if last:
            return True
        if not isinstance(node, ast.ClassDef):
            return False
        if node.bases:
            # Inherited methods are not visible statically.
            next_name = parts[i + 1]
            if not any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.name == next_name
                for n in node.body
            ):
                return True
        scope = node.body
    return True


# ---- main -------------------------------------------------------------------


def build(
    root: Path, junit_dirs: list[Path], blame: bool, today: dt.date, history_files: list[Path] = ()
) -> dict:
    index = build_module_index(root)
    changes = last_change_dates(root)
    files: list[InventoryFile] = []
    markers: list[Marker] = []
    dead_imports: list[dict] = []
    missing_paths: list[dict] = []
    pinned: list[dict] = []
    soft_hermetic: Counter = Counter()
    bodies: dict[str, list[str]] = defaultdict(list)
    importers: dict[str, set[str]] = defaultdict(set)
    parse_errors: list[str] = []

    for path in iter_test_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            parse_errors.append(f"{rel}: {exc}")
            continue
        all_imports: set[str] = set()
        for node in ast.walk(tree):
            for mod in _imports_of(node, None):
                all_imports.add(mod)
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in CHECKED_ROOTS
                and not node.level
            ):
                info = index.get(node.module)
                if info is None:
                    dead_imports.append(
                        {
                            "file": rel,
                            "line": node.lineno,
                            "target": node.module,
                            "why": "module missing",
                        }
                    )
                    continue
                names = module_names(info)
                for alias in node.names:
                    sub = f"{node.module}.{alias.name}"
                    if alias.name == "*" or sub in index or names is None or alias.name in names:
                        continue
                    dead_imports.append(
                        {"file": rel, "line": node.lineno, "target": sub, "why": "name missing"}
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in CHECKED_ROOTS and alias.name not in index:
                        dead_imports.append(
                            {
                                "file": rel,
                                "line": node.lineno,
                                "target": alias.name,
                                "why": "module missing",
                            }
                        )
        mods = sorted(m for m in all_imports if m in index)
        for m in mods:
            importers[m].add(rel)
        top_mods = sorted(m for m in module_level_imports(tree) if m in index)
        tests = list(iter_tests(tree))
        files.append(
            InventoryFile(
                rel,
                text.count("\n") + 1,
                len(tests),
                area_of(rel, mods),
                mods,
                top_mods,
                changes.get(rel),
            )
        )
        markers.extend(scan_markers(tree, rel))
        for cls, func in tests:
            norm = _norm_body(func)
            if len(norm) > 200:  # trivially short bodies collide by accident
                key = hashlib.sha1(norm.encode()).hexdigest()
                bodies[key].append(f"{rel}::{cls + '::' if cls else ''}{func.name}")
        soft = soft_hermetic_calls(tree)
        if soft:
            soft_hermetic[rel] = soft
        for line, n, digest in pinned_lists(tree):
            pinned.append({"file": rel, "line": line, "strings": n, "digest": digest})
        for line, cand in repo_path_literals(tree):
            if not (root / cand).exists():
                missing_paths.append({"file": rel, "line": line, "path": cand})

    if blame:
        by_file: dict[str, list[Marker]] = defaultdict(list)
        for m in markers:
            by_file[m.file].append(m)
        for rel, ms in by_file.items():
            dates = blame_dates(root, rel, [m.line for m in ms])
            for m in ms:
                m.date = dates.get(m.line)

    # Production importers of each tinyassets module (non-test code), to find
    # modules only tests reach.
    prod_imported: set[str] = set()
    graph: dict[str, set[str]] = {}
    for mod, info in index.items():
        if info.path.is_dir():
            continue
        try:
            tree = ast.parse(info.path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        own = mod if info.is_package else mod.rsplit(".", 1)[0] + ".x"
        every = {m for node in ast.walk(tree) for m in _imports_of(node, own) if m in index}
        prod_imported.update(every - {mod})
        graph[mod] = {m for m in module_level_imports(tree, own) if m in index} - {mod}
    # Everything tracked outside tests/ counts as a consumer: root-level
    # modules (the tray), other packages, the plugin mirror, deploy scripts.
    # String-based loading (entry points, importlib, compose commands, `-m`)
    # never shows up as an import, so a module NAMED in any tracked non-test
    # text file is treated as reachable too.
    module_paths = {info.path.resolve() for info in index.values()}
    named_parts = []
    for rel in _git(root, "ls-files").splitlines():
        if rel.startswith("tests/"):
            continue
        f = root / rel
        if f.suffix not in TEXT_SUFFIXES or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        named_parts.append(text)
        if f.suffix == ".py" and f.resolve() not in module_paths:
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            prod_imported.update(
                m for node in ast.walk(tree) for m in _imports_of(node, None) if m in index
            )
    entry_text = "\n".join(named_parts)
    test_only = sorted(
        mod
        for mod in index
        if mod.startswith("tinyassets.")
        and not index[mod].is_package
        and mod not in prod_imported
        and not mod.endswith("__main__")
        and importers.get(mod)
        and not _named_elsewhere(mod, index[mod].path.relative_to(root).as_posix(), entry_text)
    )

    # Transitive module-level reach, for selection size.
    reach_cache: dict[str, set[str]] = {}

    def reach(mod: str) -> set[str]:
        if mod in reach_cache:
            return reach_cache[mod]
        reach_cache[mod] = {mod}
        seen = {mod}
        stack = [mod]
        while stack:
            cur = stack.pop()
            # importing a.b.c runs a/__init__ and a/b/__init__ too
            parents = [".".join(cur.split(".")[:i]) for i in range(1, cur.count(".") + 1)]
            for nxt in list(graph.get(cur, ())) + [p for p in parents if p in index]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        reach_cache[mod] = seen
        return seen

    transitive: dict[str, int] = Counter()
    for f in files:
        reached: set[str] = set()
        for m in f.module_imports:
            reached |= reach(m)
        for m in reached:
            transitive[m] += 1

    win = read_junit(junit_dirs) if junit_dirs else None
    history: set[str] = set()
    for hist in history_files:
        for line in hist.read_text(encoding="utf-8").splitlines():
            if line.strip():
                history.add(line.rsplit("\t", 1)[-1])
    if win:
        history |= win.failing

    exact_dups = [sorted(v) for v in bodies.values() if len({x.split("::")[0] for x in v}) > 1]
    exact_dups.sort(key=len, reverse=True)

    cutoff = (today - dt.timedelta(days=LOW_SIGNAL_DAYS)).isoformat()
    failing_files = {n.split("::")[0] for n in history}
    low_signal = []
    for f in files:
        targets = [m for m in f.imports if m.startswith("tinyassets.")]
        if not targets or not f.last_change or f.last_change > cutoff or f.path in failing_files:
            continue
        if all(len(importers[m] - {f.path}) >= LOW_SIGNAL_MIN_OTHER_IMPORTERS for m in targets):
            low_signal.append(
                {"file": f.path, "last_change": f.last_change, "tests": f.tests, "lines": f.lines}
            )

    kf = known_failing(root, blame)
    if win:
        for e in kf:
            e["ci_failure"] = win.failure_text.get(e["node"])
            e["ci_skipped"] = win.skipped.get(e["node"])
            # The ratchet only deletes an entry that RAN and passed in the
            # required gate; an entry in a heavy-listed file, or one whose test
            # was deleted, is never re-judged there and sits forever.
            if e["node"] in win.failing:
                e["ci_status"] = "failing"
            elif e["node"] in win.skipped:
                e["ci_status"] = "skipped"
            elif e["node"] in win.ran:
                e["ci_status"] = "passing"
            else:
                e["ci_status"] = "absent"

    areas: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "tests": 0, "lines": 0})
    for f in files:
        a = areas[f.area]
        a["files"] += 1
        a["tests"] += f.tests
        a["lines"] += f.lines

    source_lines = sum(
        p.read_text(encoding="utf-8", errors="replace").count("\n") + 1
        for p in (root / "tinyassets").rglob("*.py")
        if "__pycache__" not in p.parts
    )

    def age(d: str | None) -> int | None:
        return (today - dt.date.fromisoformat(d)).days if d else None

    marker_rows = []
    for m in markers:
        row = asdict(m)
        row["age_days"] = age(m.date)
        marker_rows.append(row)

    report = {
        "schema": SCHEMA,
        "generated_for": today.isoformat(),
        "totals": {
            "files": len(files),
            "tests": sum(f.tests for f in files),
            "lines": sum(f.lines for f in files),
            "source_lines": source_lines,
            "markers": len(markers),
            "known_failing": len(kf),
        },
        "areas": dict(sorted(areas.items(), key=lambda kv: -kv[1]["lines"])),
        "dead_imports": dead_imports,
        "missing_repo_paths": missing_paths,
        "test_only_modules": [{"module": m, "test_files": len(importers[m])} for m in test_only],
        "markers": marker_rows,
        "known_failing": [dict(e, age_days=age(e.get("date"))) for e in kf],
        "exact_duplicate_bodies": exact_dups,
        "pinned_literal_lists": pinned,
        "duplicate_pins": [
            sorted(f"{x['file']}:{x['line']}" for x in group)
            for group in _group_by(pinned, "digest")
            if len({x["file"] for x in group}) > 1
        ],
        "top_importers_direct": Counter({m: len(v) for m, v in importers.items()}).most_common(25),
        "top_importers_transitive": transitive.most_common(25),
        "low_signal_files": sorted(low_signal, key=lambda r: r["last_change"]),
        "failure_history_size": len(history),
        "parse_errors": parse_errors,
        "soft_hermetic": dict(soft_hermetic.most_common()),
    }
    if win:
        report["ci"] = {
            "ran": len(win.ran),
            "skipped_in_ci": [{"node": n, "reason": r} for n, r in sorted(win.skipped.items())],
            "slowest": [
                {"node": n, "seconds": round(s, 2)}
                for n, s in sorted(win.durations.items(), key=lambda kv: -kv[1])[:100]
            ],
            "failing_in_window": sorted(win.failing),
        }
    return report


EXPIRY_WARNING_DAYS = 7


def needs_action(r: dict) -> list[str]:
    """What a maintainer should do this week. Dates are reported here, never
    enforced at merge time, so a calendar day cannot block an unrelated PR."""
    today = dt.date.fromisoformat(r["generated_for"])
    items: list[str] = []
    for m in r["markers"]:
        if not m.get("expires"):
            continue
        try:
            left = (dt.date.fromisoformat(m["expires"]) - today).days
        except ValueError:
            items.append(
                f"`{m['file']}:{m['line']}` {m['kind']} has an unreadable expiry `{m['expires']}`"
            )
            continue
        if left < 0:
            items.append(
                f"`{m['file']}:{m['line']}` {m['kind']} EXPIRED {-left}d ago: "
                f"revive or delete -- {m['reason'][:120]}"
            )
        elif left <= EXPIRY_WARNING_DAYS:
            items.append(
                f"`{m['file']}:{m['line']}` {m['kind']} expires in {left}d -- {m['reason'][:120]}"
            )
    for e in r["known_failing"]:
        when = e.get("expires")
        if when and when < today.isoformat():
            items.append(f"quarantine `{e['node'][:140]}` EXPIRED {when}: fix it or delete it")
        elif when and (dt.date.fromisoformat(when) - today).days <= EXPIRY_WARNING_DAYS:
            items.append(f"quarantine `{e['node'][:140]}` expires {when}")
        if not e["test_exists"]:
            items.append(
                f"known-failing `{e['node'][:140]}` names a test that no longer exists: "
                "delete the line"
            )
        elif e.get("ci_status") == "passing" and not e.get("flaky"):
            items.append(f"known-failing `{e['node'][:140]}` PASSES in CI: delete the line")
    for d in r["dead_imports"]:
        items.append(f"`{d['file']}:{d['line']}` imports `{d['target']}` ({d['why']})")
    ci = r.get("ci") or {}
    by_reason = Counter(s["reason"][:100] for s in ci.get("skipped_in_ci", []))
    for reason, n in by_reason.most_common():
        items.append(
            f"{n} test(s) skipped in CI, so run nowhere in CI unless another job runs "
            f"them: {reason}"
        )
    return items


def to_markdown(r: dict) -> str:
    t = r["totals"]
    out = [
        f"# Test inventory ({r['generated_for']})",
        "",
        f"- files **{t['files']}**, tests **{t['tests']}**, lines **{t['lines']}** "
        f"(source {t['source_lines']}; ratio {t['lines'] / max(t['source_lines'], 1):.2f})",
        f"- skip/xfail markers **{t['markers']}**, known-failing entries **{t['known_failing']}**",
        f"- dead imports **{len(r['dead_imports'])}**, "
        f"missing repo paths named **{len(r['missing_repo_paths'])}**",
        f"- tinyassets modules reached only by tests **{len(r['test_only_modules'])}**",
        f"- exact duplicate test bodies across files **{len(r['exact_duplicate_bodies'])}** groups",
        f"- pinned literal lists (>= {PINNED_MIN_STRINGS} strings) "
        f"**{len(r['pinned_literal_lists'])}**, "
        f"the same literal pinned in 2+ files **{len(r['duplicate_pins'])}** groups",
        f"- low-signal files (heuristic) **{len(r['low_signal_files'])}**",
    ]
    ci = r.get("ci")
    if ci:
        out.append(
            f"- CI window: ran {ci['ran']}, skipped {len(ci['skipped_in_ci'])}, "
            f"failing {len(ci['failing_in_window'])}"
        )
    actions = needs_action(r)
    out += ["", f"## Needs action ({len(actions)})", ""]
    out += [f"- {a}" for a in actions] or ["- nothing"]
    soft = r.get("soft_hermetic", {})
    out += ["", f"## Hermeticity, reported not blocked ({sum(soft.values())} calls)", ""]
    out.append(
        "Real-calendar reads and sleeps under 0.5 s across the suite; the PR gate "
        "blocks only the network, writes outside tmp_path and longer sleeps."
    )
    out += [f"- {f}: {n}" for f, n in list(soft.items())[:10]]
    out += ["", "## Markers by kind and age", ""]
    buckets = Counter()
    for m in r["markers"]:
        a = m["age_days"]
        b = "unknown" if a is None else ("<=30d" if a <= 30 else ("31-90d" if a <= 90 else ">90d"))
        buckets[(m["kind"], b)] += 1
    for (kind, b), n in sorted(buckets.items()):
        out.append(f"- {kind} {b}: {n}")
    no_reason = [m for m in r["markers"] if not m["reason"] or m["reason"].startswith("<no reason")]
    with_expiry = sum(1 for m in r["markers"] if m["expires"])
    out.append(f"- without a reason: {len(no_reason)}; with an expiry: {with_expiry}")
    out += ["", "## Known-failing", ""]
    for e in r["known_failing"]:
        flags = [e.get("ci_status", "")]
        if not e["file_exists"]:
            flags.append("FILE GONE")
        elif not e["test_exists"]:
            flags.append("TEST GONE")
        out.append(f"- `{e['node'][:140]}` age {e.get('age_days')}d {' '.join(flags)}")
    out += ["", "## Top importers (transitive, module level)", ""]
    out += [f"- {m}: {n}" for m, n in r["top_importers_transitive"][:15]]
    if ci:
        out += ["", "## Slowest 20", ""]
        out += [f"- {s['seconds']}s `{s['node'][:140]}`" for s in ci["slowest"][:20]]
    out += ["", "## Largest areas", ""]
    out += [
        f"- {a}: {v['files']} files, {v['tests']} tests, {v['lines']} lines"
        for a, v in list(r["areas"].items())[:20]
    ]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--markdown", type=Path)
    ap.add_argument("--junit-dir", type=Path, action="append", default=[])
    ap.add_argument(
        "--failure-history",
        type=Path,
        action="append",
        default=[],
        help="file of failing node ids (one per line, optionally `run<TAB>node`) from past CI runs",
    )
    ap.add_argument("--no-blame", action="store_true")
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = ap.parse_args(argv)
    if not (args.root / "tests").is_dir():
        print(f"no tests/ under {args.root}", file=sys.stderr)
        return 2
    for d in args.junit_dir:
        if not d.is_dir():
            print(f"--junit-dir {d} is not a directory", file=sys.stderr)
            return 2
    report = build(args.root, args.junit_dir, not args.no_blame, args.today, args.failure_history)
    if args.json:
        args.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    md = to_markdown(report)
    if args.markdown:
        args.markdown.write_text(md, encoding="utf-8")
    if not args.json and not args.markdown:
        sys.stdout.buffer.write(md.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
