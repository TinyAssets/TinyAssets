#!/usr/bin/env python3
"""Select the test files a change can affect, for the PR-time test run.

The authoritative integration gate is the conservative selection on the merge-group commit
(`required-tests` in .github/workflows/tests.yml). Until this existed, a PR
met the Linux suite for the first time AFTER it was stamped and queued, so
every failure surfaced as a queue drop. This picks the subset worth running on
the pull request itself; required-tests now requires these PR shards to pass.
The queue selects conservatively, and main/scheduled runs cover the full surface.

A test file is selected when ANY of these holds:

1. it changed itself;
2. it imports, directly or transitively, a changed repo module (static import
   graph over every .py under the selection roots; string constants such as
   ``"tinyassets.x.y"`` in ``importlib.import_module`` / ``mock.patch``
   count as imports);
3. its source names a changed file by repo-relative path, by basename, or
   names one of the changed file's ancestor directories (two components or
   deeper). Tests here read workflows, app.html and docs as text, so the
   import graph alone would miss them.

When unsure it selects EVERYTHING (output ``ALL``):

- a conftest, pytest config, the dependency manifest or the gate's own
  machinery changed;
- a changed module is in a conftest's import closure (every test loads it);
- a changed .py file outside ``tests/`` is selected by nothing, since an
  unmapped module means the static graph cannot see who loads it;
- a non-test .py file was deleted or moved: the graph is built from the new
  tree, where nothing imports it any more.

A test that walks a tree (``rglob``, ``os.walk``...) is also selected for any
change under a top-level root it names as a string, e.g. ``"tinyassets"``.

Over-selection is the safe direction throughout: it costs runner minutes,
while under-selection only defers a failure to the queue, which runs anyway.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories whose .py files make up the import graph. Top-level modules in
# scripts/ and tests/ are imported by bare name (tests put scripts/ on
# sys.path), so those two are also searched for a bare top-level name.
GRAPH_ROOTS = ("tinyassets", "scripts", "tests", "fantasy_daemon", "domains", "packaging")
BARE_NAME_DIRS = ("", "scripts", "tests")

# Any change here can move the verdict of an arbitrary test.
FULL_SUITE_TRIGGERS = (
    "pyproject.toml",
    "tests/__init__.py",
    ".github/known-failing-tests.txt",
    ".github/heavy-test-files.txt",
    ".github/workflows/tests.yml",
    "scripts/ci_required_tests.py",
    "scripts/affected_tests.py",
)

# Basenames too common to mean "this test reads that file".
GENERIC_BASENAMES = {
    "__init__.py", "__main__.py", "conftest.py", "README.md", "SKILL.md",
    "spec.md", "proposal.md", "design.md", "tasks.md", "index.html",
    "index.ts", "index.js", "package.json", "config.py", "utils.py",
}

# A test that walks a tree. Such a test reads every file under the roots it
# names, including a bare top-level root like "tinyassets" or "mobile", which
# is too common a string to match on for any other test.
_WALKS = re.compile(r"\.rglob\(|\.glob\(|\.iterdir\(|os\.walk\(|os\.listdir\(|glob\.glob\(")

# `"docs" / "concerns"` (pathlib) reads as `"docs/concerns"` after this.
_PATHLIB_JOIN = re.compile(r"""["']\s*/\s*["']""")
_DOTTED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")


def _module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _import_time_nodes(body: list[ast.stmt]):
    """Yield the statements executed when a module is imported."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(stmt, ast.If):
            if not _is_type_checking(stmt.test):
                yield from _import_time_nodes(stmt.body)
            yield from _import_time_nodes(stmt.orelse)
        elif isinstance(stmt, ast.ClassDef):
            yield from _import_time_nodes(stmt.body)
        elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            yield from _import_time_nodes(stmt.body)
            for handler in stmt.handlers:
                yield from _import_time_nodes(handler.body)
            yield from _import_time_nodes(stmt.orelse)
            yield from _import_time_nodes(stmt.finalbody)
        elif isinstance(stmt, (ast.With, ast.For, ast.While)):
            yield from _import_time_nodes(stmt.body)


class Graph:
    """Static import graph of the repo's Python files, keyed by relpath."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, str] = {}  # relpath -> source
        for top in GRAPH_ROOTS:
            base = root / top
            if not base.is_dir():
                continue
            for path in base.rglob("*.py"):
                rel = path.relative_to(root).as_posix()
                if "/node_modules/" in rel or "/.venv/" in rel:
                    continue
                try:
                    self.files[rel] = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
        for path in root.glob("*.py"):
            self.files[path.name] = path.read_text(encoding="utf-8", errors="replace")
        self.by_module = {_module_name(rel): rel for rel in self.files}
        self.edges = {rel: self._imports(rel, src) for rel, src in self.files.items()}

    def _resolve(self, dotted: str) -> list[str]:
        """Files executed by importing `dotted`: each package __init__ plus the leaf."""
        hits: list[str] = []
        parts = dotted.split(".")
        for prefix in BARE_NAME_DIRS:
            pre = prefix.split("/") if prefix else []
            for i in range(1, len(parts) + 1):
                rel = self.by_module.get(".".join(pre + parts[:i]))
                if rel:
                    hits.append(rel)
        return hits

    def _imports(self, rel: str, src: str) -> set[str]:
        """Modules `rel` imports.

        A test file or conftest counts every import anywhere in it, plus dotted
        string constants (``mock.patch("tinyassets.x.y")``): that is what the test
        exercises. Any other file counts only what runs when it is IMPORTED --
        module-level statements, including class bodies and ``try``/``if``
        blocks, but not function bodies or ``if TYPE_CHECKING:``. Following
        every lazy import made each test reach ~470 of the 525 modules in
        ``tinyassets/``, where importing conftest's dependencies at runtime
        loads 38; a graph that selects everything selects nothing.
        """
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            return set()
        pkg = _module_name(rel).split(".")
        if not rel.endswith("__init__.py"):
            pkg = pkg[:-1]
        # A conftest's fixtures (autouse ones included) run for every test,
        # so its function bodies count as much as a test file's do.
        test = is_test_file(rel) or rel.endswith("conftest.py")
        nodes = ast.walk(tree) if test else _import_time_nodes(tree.body)
        names: set[str] = set()
        for node in nodes:
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = pkg[: len(pkg) - node.level + 1] if node.level else []
                mod = ".".join(base + ([node.module] if node.module else []))
                if mod:
                    names.add(mod)
                    names.update(f"{mod}.{alias.name}" for alias in node.names)
            elif test and isinstance(node, ast.Constant) and isinstance(node.value, str):
                if _DOTTED.match(node.value):
                    names.add(node.value)
        out: set[str] = set()
        for name in names:
            out.update(self._resolve(name))
        out.discard(rel)
        return out

    def closure(self, rel: str) -> set[str]:
        seen: set[str] = set()
        stack = [rel]
        while stack:
            for dep in self.edges.get(stack.pop(), ()):
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        return seen


def is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def _mention_keys(rel: str) -> list[str]:
    keys = [rel]
    name = rel.rsplit("/", 1)[-1]
    if name not in GENERIC_BASENAMES:
        keys.append(name)
    parts = rel.split("/")[:-1]
    keys.extend("/".join(parts[:i]) for i in range(2, len(parts) + 1))
    return keys


def loaded_by_conftests(graph: Graph, root: Path) -> set[str]:
    """Repo files every test loads: each conftest's closure, static AND runtime.

    Static alone undercounts: tests/conftest.py calls into the provider layer
    at import time, which then lazily imports 15 more modules (measured
    2026-10-01: 23 static, 38 at runtime). So the conftests are also imported
    for real and whatever repo modules land in ``sys.modules`` are added. If
    that import fails the caller cannot know what is shared, so it raises and
    the selection falls back to the whole suite.
    """
    conftests = sorted(rel for rel in graph.files if rel.endswith("conftest.py"))
    shared: set[str] = set()
    for rel in conftests:
        shared |= graph.closure(rel) | {rel}
    probe = (
        "import importlib.util, sys\n"
        "for i, path in enumerate(sys.argv[1:]):\n"
        "    spec = importlib.util.spec_from_file_location(f'_conftest_{i}', path)\n"
        "    spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
        "for mod in list(sys.modules.values()):\n"
        "    print(getattr(mod, '__file__', None) or '')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, *(str(root / c) for c in conftests)],
        cwd=root, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"importing the conftests failed:\n{proc.stderr[-2000:]}")
    resolved_root = root.resolve()
    for line in proc.stdout.splitlines():
        try:
            rel = Path(line).resolve().relative_to(resolved_root).as_posix()
        except (ValueError, OSError):
            continue
        if rel in graph.files:
            shared.add(rel)
    return shared


def select(
    changed: list[str],
    root: Path = REPO_ROOT,
    graph: Graph | None = None,
    shared: set[str] | None = None,
) -> tuple[list[str] | None, list[str]]:
    """Return (test files, reasons). ``None`` means run the whole suite.

    `graph` and `shared` may be passed in to reuse them across many calls
    (measurement scripts); left out, they are built for `root`.
    """
    reasons: list[str] = []
    changed = sorted({c.replace("\\", "/") for c in changed if c.strip()})
    for rel in changed:
        if rel in FULL_SUITE_TRIGGERS or rel.rsplit("/", 1)[-1] == "conftest.py":
            return None, [f"{rel}: shared test infrastructure"]

    graph = graph or Graph(root)
    tests = sorted(rel for rel in graph.files if is_test_file(rel))
    if shared is None:
        shared = loaded_by_conftests(graph, root)
    for rel in changed:
        if rel in shared:
            return None, [f"{rel}: in a conftest's import closure, so every test loads it"]

    closures = {t: graph.closure(t) for t in tests}
    texts = {t: _PATHLIB_JOIN.sub("/", graph.files[t]) for t in tests}
    walkers = [t for t in tests if _WALKS.search(texts[t])]
    selected: set[str] = set()
    for rel in changed:
        if rel.endswith(".py") and not is_test_file(rel) and not (root / rel).exists():
            # Its importers still import it (or were edited away in the same
            # diff), but the graph is built from the tree that no longer has
            # it, so nothing would select them.
            return None, [f"{rel}: deleted or moved Python module"]
        hit: set[str] = set()
        if is_test_file(rel) and (root / rel).exists():
            hit.add(rel)
        hit.update(t for t in tests if rel in closures[t])
        keys = _mention_keys(rel)
        hit.update(t for t in tests if any(k in texts[t] for k in keys))
        top = rel.split("/", 1)[0]
        if "/" in rel:
            roots = (f'"{top}"', f"'{top}'", f'"{top}/', f"'{top}/")
            hit.update(t for t in walkers if any(r in texts[t] for r in roots))
        source = rel.endswith(".py") and not rel.startswith("tests/")
        if not hit and source and (root / rel).exists():
            return None, [f"{rel}: Python file no test reaches statically"]
        reasons.append(f"{rel}: {len(hit)} test file(s)")
        selected |= hit
    return sorted(selected), reasons


#: Trees that hold prose and specs: never imported, never executed.
_PROSE_DIRS = ("docs/", "openspec/", "ideas/")


def provable_shape(changed: list[str]) -> str | None:
    """``prose``, or ``None`` when no argument covers the diff. ONE shape.

    This is the whole safety story of the merge gate, so it is a WHITELIST, and
    it is as short as it can be.

    The static import graph is not sound and cannot cheaply be made so: it
    counts only import-time statements outside ``tests/``, because following
    lazy imports made each test reach ~470 of the 525 modules in
    ``tinyassets/`` (see ``_imports``) -- a graph that selects everything
    selects nothing. Measured holes, all real and all found by cross-family
    review: changing ``tinyassets/run_file_erasure.py`` does not select
    ``tests/test_account_deletion.py`` (the import is inside a function);
    changing ``tinyassets/providers/daily_quota_shapes.json`` selects none of
    the six quota tests (a data file is not an edge); DELETING
    ``tests/test_agent_turn_journal.py`` omits all four test modules that
    import it. No digest or coverage check can see an OMISSION.

    ``prose`` is the one shape with an argument: every path is under docs/,
    openspec/, ideas/, or is a top-level ``.md``. Nothing imports or executes
    it, so it can only affect a test that READS it -- by name
    (``_mention_keys``) or by walking its directory (``_WALKS``).

    A ``tests``-only shape was tried and REMOVED in round 3 (2026-10-03). Its
    argument was that a changed ``tests/test_*.py`` can only affect the tests
    importing it, which the graph captures in full for test files -- but that
    leans on the import graph being complete, which is the exact class of claim
    the deletion hole above breaks. Not worth 8% of merges.

    Residual, stated rather than hidden: a test that reaches a prose file
    through a path it builds without naming the file or its directory. The
    tree-walking superset in ``gate_selection`` is what shrinks that. It is not
    a proof, and it is why this list has one entry.
    """
    if not changed:
        return None
    for raw in changed:
        rel = raw.replace("\\", "/")
        if not (rel.startswith(_PROSE_DIRS) or (rel.endswith(".md") and "/" not in rel)):
            return None
    return "prose"


def walking_tests(graph: Graph) -> set[str]:
    """Every test that enumerates a tree, so a file it never names still reaches it."""
    return {
        rel for rel in graph.files
        if is_test_file(rel) and _WALKS.search(_PATHLIB_JOIN.sub("/", graph.files[rel]))
    }


def gate_selection(
    changed: list[str],
    root: Path = REPO_ROOT,
    graph: Graph | None = None,
    shared: set[str] | None = None,
) -> tuple[list[str] | None, list[str]]:
    """A selection a MERGE GATE may rely on. ``None`` means run the whole suite.

    Conservative where ``select`` is advisory: ``select`` may under-select
    because a miss there only defers a failure to the queue, and the queue runs
    everything. Here there is no later run to catch it, so anything without a
    completeness argument is ALL.
    """
    shape = provable_shape(changed)
    if shape is None:
        return None, [
            "no completeness argument covers this diff shape: running the whole suite"
        ]
    graph = graph or Graph(root)
    selected, reasons = select(changed, root, graph, shared)
    if selected is None:
        return None, reasons
    walkers = walking_tests(graph)
    added = walkers - set(selected)
    return sorted(set(selected) | walkers), [
        f"shape: {shape}",
        *reasons,
        f"+{len(added)} tree-walking test file(s), added unconditionally",
    ]


def changed_files(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return out.splitlines()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="git ref to diff against (merge-base ...HEAD)")
    ap.add_argument("--changed", nargs="*", help="explicit changed paths instead of --base")
    ap.add_argument("--out", help="write the selection here; ALL means the whole suite")
    ap.add_argument(
        "--gate",
        action="store_true",
        help=(
            "Conservative mode for the MERGE GATE: ALL unless the diff shape "
            "carries a completeness argument (see provable_shape). Without it "
            "this is the advisory PR-time selection, where under-selecting only "
            "defers a failure to the queue."
        ),
    )
    args = ap.parse_args()
    if (args.base is None) == (args.changed is None):
        raise SystemExit("pass exactly one of --base or --changed")
    changed = args.changed if args.changed is not None else changed_files(args.base)
    try:
        selected, reasons = (gate_selection if args.gate else select)(changed)
    except RuntimeError as exc:
        # Unsure what is shared -> run everything, and say why. On the gate path
        # this is the ONLY safe direction, and it is loud: the reason is printed
        # and the workflow puts it in the step summary. A clean runner with no
        # dependencies installed lands here -- the conftest import probe needs
        # pytest -- which is why the select job installs before asking.
        selected, reasons = None, [f"selection failed, running the whole suite: {exc}"]
    body = "ALL\n" if selected is None else "".join(f"{t}\n" for t in selected)
    for line in reasons:
        print(line, file=sys.stderr)
    summary = "ALL (full suite)" if selected is None else f"{len(selected)} test file(s)"
    print(f"selection: {summary}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
