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
  machinery changed (PR mode selects the machinery's own contract tests
  instead; the image gate and the conservative queue still run);
- a changed module is in a conftest's import closure (every test loads it;
  PR mode selects its importers, since any selected test loads it too);
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
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories whose .py files make up the import graph. Top-level modules in
# scripts/ and tests/ are imported by bare name (tests put scripts/ on
# sys.path), so those two are also searched for a bare top-level name.
GRAPH_ROOTS = ("tinyassets", "scripts", "tests", "domains", "packaging")
BARE_NAME_DIRS = ("", "scripts", "tests")

# Any change here can move the verdict of an arbitrary test. The CI machinery
# only decides WHICH tests run and how a verdict is read, so a PR selects the
# tests that read or import it; the queue still runs everything.
CI_MACHINERY = (
    ".github/known-failing-tests.txt",
    ".github/heavy-test-files.txt",
    ".github/workflows/tests.yml",
    "scripts/ci_required_tests.py",
    "scripts/affected_tests.py",
)
FULL_SUITE_TRIGGERS = ("pyproject.toml", "tests/__init__.py", *CI_MACHINERY)

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
        self._closures: dict[str, frozenset[str]] = {}

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

    def closure(self, rel: str) -> frozenset[str]:
        cached = self._closures.get(rel)
        if cached is not None:
            return cached
        seen: set[str] = set()
        stack = [rel]
        while stack:
            for dep in self.edges.get(stack.pop(), ()):
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        self._closures[rel] = frozenset(seen)
        return self._closures[rel]


def is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.startswith("tests/") and name.startswith("test_") and name.endswith(".py")


def defines_tests(source: str) -> bool:
    """Keep files that can expose pytest cases; helper modules are dependencies."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return True  # Let pytest report the collection error.
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test")):
            return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            return True
        if isinstance(node, ast.alias) and (node.asname or node.name).startswith("test_"):
            return True
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                and node.id.startswith("test_")):
            return True
    return False


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
    pr: bool = False,
) -> tuple[list[str] | None, list[str]]:
    """Return (test files, reasons). ``None`` means run the whole suite.

    `graph` and `shared` may be passed in to reuse them across many calls
    (measurement scripts); left out, they are built for `root`. `pr` maps a
    CI_MACHINERY change, or a module in a conftest's closure, to the tests
    that import or name it instead of ALL: a selected test still loads every
    conftest dependency, so a startup break fails it. Conftest edits, the
    manifest, unreached and deleted source still select ALL.
    """
    reasons: list[str] = []
    changed = sorted({c.replace("\\", "/") for c in changed if c.strip()})
    for rel in changed:
        if pr and rel in CI_MACHINERY:
            continue
        if rel in FULL_SUITE_TRIGGERS or rel.rsplit("/", 1)[-1] == "conftest.py":
            return None, [f"{rel}: shared test infrastructure"]

    graph = graph or Graph(root)
    tests = sorted(rel for rel, source in graph.files.items()
                   if is_test_file(rel) and defines_tests(source))
    if pr:
        shared = set()
    elif shared is None:
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
        if rel in tests:
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


#: Trees no Python imports: the TypeScript platform workspace (ADR-032) and the
#: prose. A Python test can observe a file in them only by reading it.
TREES = ("platform", "docs", "openspec", "ideas")
#: The top-level markdown files, as one more such tree.
TOP_MD = "*.md"


def tree_of(rel: str) -> str | None:
    """The tree a repo-relative path lies in (a tree's own directory included), or None."""
    rel = rel.replace("\\", "/")
    top = rel.split("/", 1)[0]
    if top in TREES:
        return top
    return TOP_MD if "/" not in rel and rel.endswith(".md") else None


def provable_shape(changed: list[str]) -> str | None:
    """The trees a diff touches (``docs+platform``), or ``None``. ONE shape.

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

    The one shape with an argument: every path lies in a tree of ``TREES`` or
    is a top-level ``.md``, and none is a ``.py`` file. Nothing imports those
    trees, so a Python test can observe them only by READING them: opening or
    listing a path in them, or handing one to a child process. Which tests can
    do that is ``tree_readers``; the runner's audit (``ci_required_tests.py``,
    ``--ci-tree-audit``, on in every gate run) fails any test that reads a tree
    the map does not give it. Every diff outside this shape runs the whole
    surface under that audit, so no test gains an unmapped read without its own
    change failing there first.

    A ``tests``-only shape was tried and REMOVED in round 3 (2026-10-03). Its
    argument was that a changed ``tests/test_*.py`` can only affect the tests
    importing it, which the graph captures in full for test files -- but that
    leans on the import graph being complete, which is the exact class of claim
    the deletion hole above breaks. Not worth 8% of merges.

    The diff lists a move's old path too (``changed_files`` passes
    ``--no-renames``), so moving a file into a tree from anywhere else is not this
    shape. The audit sees opens, listings, existence checks (``os.stat``,
    ``lstat``, ``access``), ``sqlite3.connect`` and child processes; git run inside
    the checkout reads every tree unless its subcommand reads nothing.

    Residuals, stated rather than hidden: a native extension that opens a file
    itself (lxml, pyarrow, lancedb) raises no event; a child process that finds a
    tree by itself (a script that walks the checkout) when no argument word of it
    lies in the tree; and a helper's import-time read is judged by the tests that
    import it statically, not by one that reaches it only through a function-level
    import in source. The post-merge full run remains for those.
    """
    if not changed:
        return None
    trees: set[str] = set()
    for raw in changed:
        rel = raw.replace("\\", "/")
        tree = tree_of(rel)
        if tree is None or rel.endswith(".py"):
            return None
        trees.add(tree)
    return "+".join(sorted(trees))


_PATH_CALLS = {"Path", "PurePath", "PurePosixPath", "join", "chdir"}
_WALK_METHODS = {"rglob", "glob", "iterdir", "walk"}
_WALK_FUNCS = {"walk", "listdir", "scandir"}
_GIT = re.compile(r"""["']git["']""")


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _is_repo_root(node: ast.AST) -> bool:
    """``Path(".")``, ``Path.cwd()``, ``os.getcwd()`` or an upper-case ROOT/REPO constant."""
    if isinstance(node, ast.Constant):
        return node.value in (".", "", "./")
    if isinstance(node, ast.Call):
        name = _name(node.func)
        if name in ("cwd", "getcwd"):
            return True
        if name in ("Path", "PurePath"):
            return not node.args or _is_repo_root(node.args[0])
        if name in ("resolve", "absolute") and isinstance(node.func, ast.Attribute):
            return _is_repo_root(node.func.value)
        return False
    name = _name(node)
    return name.isupper() and ("ROOT" in name or "REPO" in name)


def tree_reads(source: str, top_md: frozenset[str] = frozenset()) -> set[str]:
    """The trees a source can read: named in path form, or walked from the checkout's root.

    A tree is named by a string starting ``<tree>/`` (``"platform/owner"``), or by
    ``"<tree>"`` itself where a path is built (``ROOT / "docs" / "x"``,
    ``Path("docs")``, ``os.path.join(.., "docs")``, ``cwd="docs"``). The top-level
    markdown tree is named by one of its file names (`top_md`). A walk
    (``rglob/glob/iterdir/walk``, ``os.walk/listdir/scandir``) of the root
    reaches every tree. Unparsable source reads everything.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {*TREES, TOP_MD}
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value[2:] if node.value.startswith("./") else node.value
            if text in top_md:
                out.add(TOP_MD)
            head = text.split("/", 1)[0]
            if head not in TREES:
                continue
            up = parent.get(node)
            if ("/" in text
                    or (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Div)
                        and up.right is node)
                    or (isinstance(up, ast.Call) and _name(up.func) in _PATH_CALLS)
                    or (isinstance(up, ast.keyword) and up.arg == "cwd")):
                out.add(head)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if ((func.attr in _WALK_METHODS and _is_repo_root(func.value))
                    or (func.attr in _WALK_FUNCS and _name(func.value) == "os"
                        and (not node.args or _is_repo_root(node.args[0])))):
                out.update((*TREES, TOP_MD))
    return out


def tree_readers(graph: Graph, extra_md: tuple[str, ...] = ()
                 ) -> tuple[dict[str, set[str]], set[str]]:
    """(tree -> test files that may read it, trees a conftest's closure reads).

    A test reads what its own text or any module in its import closure reads,
    and every tree when its own text runs ``git`` (git sees the whole checkout).
    A tree a conftest reads is read by every test, so a diff touching it is ALL.
    The top-level .md names are the checkout's plus `extra_md`: a diff's deleted
    ones, so both sides of the diff count.
    """
    top_md = frozenset(path.name for path in graph.root.glob("*.md")) | frozenset(extra_md)
    own = {rel: tree_reads(src, top_md) for rel, src in graph.files.items()}

    def reach(rel: str) -> set[str]:
        hit = set(own[rel])
        for dep in graph.closure(rel):
            hit |= own.get(dep, set())
        return hit

    readers: dict[str, set[str]] = {tree: set() for tree in (*TREES, TOP_MD)}
    for rel, src in graph.files.items():
        if is_test_file(rel) and defines_tests(src):
            for tree in reach(rel) | ({*TREES, TOP_MD} if _GIT.search(src) else set()):
                readers[tree].add(rel)
    shared: set[str] = set()
    for rel in graph.files:
        if rel.endswith("conftest.py"):
            shared |= reach(rel)
    return readers, shared


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
    completeness argument is ALL. With one, it is ``select`` plus every test
    ``tree_readers`` maps to a touched tree.
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
    readers, conftest_reads = tree_readers(
        graph, tuple(rel for rel in changed if tree_of(rel) == TOP_MD))
    touched = set(shape.split("+"))
    gone = sorted(tree for tree in touched if tree != TOP_MD and not (root / tree).is_dir())
    if gone:
        # A root listing would see the tree vanish, which the audit does not map.
        return None, [f"{', '.join(gone)}: the whole tree is deleted: running the whole suite"]
    if touched & conftest_reads:
        return None, [f"{', '.join(sorted(touched & conftest_reads))}: a conftest reads it, "
                      "so every test may: running the whole suite"]
    added = set().union(*(readers[tree] for tree in touched)) - set(selected)
    return sorted(set(selected) | added), [
        f"shape: {shape}",
        *reasons,
        f"+{len(added)} test file(s) that may read {', '.join(sorted(touched))}",
    ]


#: The old stack's code, deleted at the cutover (openspec/changes/clean-sheet-platform/
#: tasks.md section 9). A test that reaches only this retires with it.
RETIRING = ("tinyassets/",)


def retiring_tests(graph: Graph) -> list[str]:
    """Test files whose reached repo modules all lie in RETIRING, or load it at import.

    A package ``__init__.py`` holding at most a docstring has no code, so it decides nothing.
    """
    def no_code(rel: str) -> bool:
        try:
            body = ast.parse(graph.files[rel]).body
        except (SyntaxError, ValueError):
            return False
        return all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) for n in body)

    gone = {rel for rel in graph.files if rel.startswith(RETIRING)}
    gone |= {rel for rel in graph.files
             if not rel.startswith("tests/") and (
                 graph.closure(rel) & gone or (rel.endswith("__init__.py") and no_code(rel)))}
    out = []
    for rel, src in graph.files.items():
        if is_test_file(rel) and defines_tests(src):
            reached = {dep for dep in graph.closure(rel) if not dep.startswith("tests/")}
            if reached and reached <= gone:
                out.append(rel)
    return sorted(out)


def changed_files(base: str, root: Path = REPO_ROOT) -> list[str]:
    # --no-renames: a move lists its old path too, so a file moved into a tree
    # from anywhere else (or a .py moved to platform/*.ts) is not a tree-only diff.
    out = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout
    return out.splitlines()


def pr_tier(selected: list[str] | None, root: Path = REPO_ROOT) -> list[str]:
    """Deduplicate full structural files; retain affected execution proofs.

    Required CI's existing heavy-file exclusion still applies. Neither duration
    nor a jail/browser marker can remove an affected file from the PR run.
    """
    heavy = {line.split('#', 1)[0].strip() for line in
             (root / '.github/heavy-test-files.txt').read_text().splitlines()}
    # The required structural job already executes these exact full files.
    # Partial node-ID targets must leave their other cases in the affected tier.
    structural_files = set()
    guard_script = root / 'scripts/ci_structural_guards.py'
    if guard_script.is_file():
        spec = importlib.util.spec_from_file_location('pr_structural_targets', guard_script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        structural_files = {target for target in module.targets() if '::' not in target}
    candidates = selected if selected is not None else sorted(
        p.relative_to(root).as_posix() for p in (root / 'tests').rglob('test_*.py'))
    return [name for name in candidates if name not in structural_files and name not in heavy
            and defines_tests((root / name).read_text(encoding="utf-8", errors="replace"))]


def needs_image(changed: list[str], root: Path = REPO_ROOT) -> bool:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.runtime_paths import runtime_inputs

    inputs = runtime_inputs(root, 'HEAD')
    return any(inputs.covers(name)
               or name in ('scripts/affected_tests.py', '.github/workflows/tests.yml')
               for name in changed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="git ref to diff against (merge-base ...HEAD)")
    ap.add_argument("--changed", nargs="*", help="explicit changed paths instead of --base")
    ap.add_argument("--out", help="write the selection here; ALL means the whole suite")
    ap.add_argument('--pr', action='store_true', help='select affected tests for a PR')
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
    ap.add_argument("--retiring", action="store_true",
                    help="list the test files that retire with RETIRING at the cutover")
    args = ap.parse_args()
    if args.retiring:
        sys.stdout.write("".join(f"{rel}\n" for rel in retiring_tests(Graph(REPO_ROOT))))
        return 0
    if (args.base is None) == (args.changed is None):
        raise SystemExit("pass exactly one of --base or --changed")
    if args.pr and args.gate:
        raise SystemExit("--pr and --gate are separate tiers; pass one")
    changed = args.changed if args.changed is not None else changed_files(args.base)
    try:
        selected, reasons = (
            gate_selection(changed) if args.gate else select(changed, pr=args.pr))
    except RuntimeError as exc:
        # Unsure what is shared -> run everything, and say why. On the gate path
        # this is the ONLY safe direction, and it is loud: the reason is printed
        # and the workflow puts it in the step summary. A clean runner with no
        # dependencies installed lands here -- the conftest import probe needs
        # pytest -- which is why the select job installs before asking.
        selected, reasons = None, [f"selection failed, running the whole suite: {exc}"]
    if args.pr:
        selected = pr_tier(selected)
        reasons.append('PR tier: affected tests in the jail; full structural files deduplicated')
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
