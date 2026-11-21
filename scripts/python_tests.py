"""What a Python test file holds, read as text: its tests, their bodies, their skips.

The counterpart of ``platform_tests.py``. The tamper check (``test_hygiene_gate.py``) reads
a pull request's test files with ``ast.parse`` inside ``pull_request_target`` and imports
nothing from them. Every reader of a test's shape takes a test, a marker and an environment
guard from here, so a report and the gate agree.

* A test file is one pytest collects by its default rule: ``test_*.py`` or ``*_test.py``
  under a ``tests/`` folder, at any depth.
* A test is a ``test*`` function at module level or in a ``Test*`` class.
* A marker is a skip, skipif, xfail or importorskip, as a decorator, a ``pytestmark``, a
  call, or a ``pytest.param(marks=...)`` entry, through any alias of pytest or unittest.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Iterator


def in_tests(path: str | None) -> bool:
    """A Python file under a ``tests/`` folder: a test file, a conftest or a helper."""
    return bool(path) and path.endswith(".py") and "tests" in path.split("/")[:-1]


def is_test_file(path: str | None) -> bool:
    """Would pytest's default discovery collect tests from this path?"""
    name = (path or "").rsplit("/", 1)[-1]
    return in_tests(path) and (name.startswith("test_") or name.endswith("_test.py"))


_MARKER_KINDS = ("skip", "skipif", "xfail", "importorskip")
#: A reason that names a date the marker is allowed to live until. The CI
#: expiry gate reads the same token, so the report and the gate agree.
EXPIRY_RE = re.compile(r"\bexpires?[=:]\s*(\d{4}-\d{2}-\d{2})\b")

# ---- is a skip an environment guard? -------------------------------------------
#
# Lexical on purpose: the reason string is the only thing a skip must carry.
# An environment guard (a platform fact, a missing installable tool) runs
# wherever that platform or tool exists; anything else suppresses a case
# nobody may ever run.
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


@dataclass
class Marker:
    file: str
    line: int
    kind: str
    reason: str
    scope: str  # "module" | "class" | "function" | "call"
    date: str | None = None
    expires: str | None = None
    #: Last source line of the marker expression (None when unknown).
    end_line: int | None = None


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


def dotted(node: ast.AST) -> str:
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
    name = dotted(func)
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
                markers.append(
                    Marker(rel, deco.lineno, kind, reason, scope, end_line=deco.end_lineno)
                )

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
                    markers.append(
                        Marker(rel, val.lineno, kind, reason, "module", end_line=val.end_lineno)
                    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and id(node) not in decorator_ids:
            kind = _marker_kind(node.func, aliases)
            # A bare call of pytest.mark.skipif(...) not used as a decorator is
            # usually a pytest.param(marks=...) entry; count it as a marker too.
            if kind:
                markers.append(
                    Marker(
                        rel,
                        node.lineno,
                        kind,
                        _reason(node, kind),
                        "call",
                        end_line=node.end_lineno,
                    )
                )
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


def norm_body(func: ast.FunctionDef) -> str:
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
