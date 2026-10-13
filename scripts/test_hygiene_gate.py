#!/usr/bin/env python3
"""Hold the test suite to what a PR CHANGES, never to the calendar of main.

A suite rots without any test going red in three ways, and agent-written
suites do all three measurably more (tampering rates 0.7-96% by task,
2026-09-17 census; agent tests flakier from file I/O and nondeterminism,
arXiv 2607.12068):

* **Tampering.** A failing test gets deleted, skipped or its assertions
  loosened inside the feature PR that broke it. Deleting, skipping or
  weakening a test is legitimate -- but as its OWN change, with a stated
  reason, reviewed by the other model family. Not riding along in a feature.
* **Suppression that never ends.** ``tests/test_wiki_file_bug.py`` skipped
  itself for 144 days behind a guard that looked in the wrong module.
* **Non-hermetic new tests.** Real sleeps, the real calendar, the network,
  real paths outside ``tmp_path``.

This reads the merge base and head as TEXT (``git show``, ``ast.parse``;
nothing from the PR executes -- it runs inside the ``pull_request_target``
scope guard) and fails when:

1. **tampering** -- a test that exists at the merge base is removed, has
   fewer assertions, or gains a skip/xfail; or a known-failing entry is
   added -- and the body has no ``Test-Removal: retired|consolidated|wrong --
   <why>`` line. With that line it still fails if the PR is also a feature
   (adds lines outside tests/), unless the reason is ``retired``: retiring a
   behaviour legitimately deletes its code and its tests together.
2. a **new** skip/xfail that is not an environment guard lacks ``owner=`` and
   one of ``expires=YYYY-MM-DD`` (at most ``MAX_EXPIRY_DAYS`` out) or
   ``runs-in=<where it does run>``;
3. the PR touches a test file holding an **expired** ``expires=`` marker, or
   a ``flaky`` ledger entry for that file is expired. Expiry is enforced only
   on PRs that touch the file -- never on main, never in the merge queue, so
   a calendar day cannot block an unrelated PR. The weekly inventory reports
   the rest;
4. a **new** test does what actually flakes or pollutes: reaches the
   network, writes under a real home / cwd / absolute path instead of
   ``tmp_path``, or sleeps ``SLEEP_BLOCK_S`` seconds or more. Real-calendar
   reads and shorter sleeps are printed as notes and counted weekly, never
   blocked. A line ending ``# hermetic-ok: <reason>`` is exempt.

Size is deliberately NOT gated here: deleting tests to fit a count deletes
cheap, valuable ones. Wall-clock, CI skips and the quarantine count are
budgeted in ``scripts/ci_required_tests.py`` instead.

Usage::

    python scripts/test_hygiene_gate.py --base BASE_SHA --head HEAD_SHA \\
        [--body PR_BODY_FILE] [--today YYYY-MM-DD]

Exit codes: ``0`` clean, ``1`` a rule failed, ``2`` git or input error.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_inventory as inv  # noqa: E402  (one definition of a marker)

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_EXPIRY_DAYS = 180
LEDGER = ".github/known-failing-tests.txt"
OWNER_RE = re.compile(r"\bowner[=:]\s*[\w@./-]+")
RUNS_IN_RE = re.compile(r"\bruns-in[=:]\s*[\w./:-]+")
REMOVAL_RE = re.compile(
    r"^[ \t]*Test-Removal:[ \t]*(retired|consolidated|wrong)\b[^\n]*\S[^\n]{8,}$",
    re.MULTILINE | re.IGNORECASE,
)
HERMETIC_OK = "hermetic-ok"
#: The escape hatch is a COMMENT that gives a reason, not a substring.
HERMETIC_OK_RE = re.compile(r"#\s*hermetic-ok:\s*\S.{3,}")
#: Paths whose added lines do not make a PR a "feature".
NON_FEATURE = ("tests/", "docs/", LEDGER, ".github/heavy-test-files.txt", "openspec/")


@dataclass
class Finding:
    where: str
    what: str


@dataclass
class Delta:
    tampering: list[Finding] = field(default_factory=list)
    unexplained_markers: list[Finding] = field(default_factory=list)
    expired: list[Finding] = field(default_factory=list)
    unhermetic: list[Finding] = field(default_factory=list)
    notes: list[Finding] = field(default_factory=list)
    feature_lines: int = 0
    product_deleted: int = 0
    #: Tampering findings that are whole test removals (the only kind
    #: `Test-Removal: retired` may carry alongside product code).
    removals_only: bool = True
    new_tests: int = 0
    removed_tests: int = 0


# ---- git --------------------------------------------------------------------


def _git(*args: str, root: Path = REPO_ROOT) -> str:
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


def _show(rev: str, path: str, root: Path) -> str | None:
    """A file's text at `rev`, None if the path is absent there.

    Any OTHER git failure (a lazy blob fetch that failed, a bad rev) raises:
    reading it as "absent" would silently undercount what the PR changes.
    """
    probe = ["git", "-C", str(root), "cat-file", "-e", f"{rev}:{path}"]
    if subprocess.run(probe, capture_output=True).returncode != 0:
        return None
    return _git("show", f"{rev}:{path}", root=root)


def merge_base(base: str, head: str, root: Path) -> str:
    """The ONE baseline everything is compared against.

    Comparing the PR's file list from the merge base with blobs from current
    main credits the PR with whatever main changed in the same file.
    """
    return _git("merge-base", base, head, root=root).strip()


def changed_files(base: str, head: str, root: Path) -> list[tuple[str, str | None, str | None]]:
    """(status, base_path, head_path) for every changed path; renames paired.

    NUL-delimited, so an unusual file name cannot be dropped by git quoting.
    """
    raw = _git("diff", "--name-status", "-z", "-M", base, head, root=root)
    fields = raw.split("\0")
    out: list[tuple[str, str | None, str | None]] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status.startswith(("R", "C")):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
        else:
            path = fields[i + 1]
            i += 2
            old = None if status == "A" else path
            new = None if status == "D" else path
        out.append((status[0], old, new))
    return out


def line_counts(base: str, head: str, root: Path, path: str) -> tuple[int, int]:
    """(added, deleted) lines for one path; a binary file counts 1 each way."""
    raw = _git("diff", "--numstat", base, head, "--", path, root=root)
    added = deleted = 0
    for line in raw.splitlines():
        a, d = (line.split("\t") + ["", ""])[:2]
        added += int(a) if a.isdigit() else 1
        deleted += int(d) if d.isdigit() else 1
    return added, deleted


# ---- what a test file holds ---------------------------------------------------


@dataclass
class FileModel:
    tests: dict[str, ast.FunctionDef]
    asserts: dict[str, int]
    bodies: dict[str, str]
    markers: list[inv.Marker]
    lines: list[str]


def _assertions(func: ast.AST) -> int:
    n = 0
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            n += 1
        elif isinstance(node, ast.Call):
            name = inv._dotted(node.func)
            last = name.rsplit(".", 1)[-1]
            if last.startswith("assert") or last in ("raises", "warns", "fail"):
                n += 1
    return n


def model(text: str | None, rel: str) -> FileModel:
    empty = FileModel({}, {}, {}, [], [])
    if not text:
        return empty
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return empty
    tests: dict[str, ast.FunctionDef] = {}
    for cls, func in inv.iter_tests(tree):
        tests[f"{cls}::{func.name}" if cls else func.name] = func
    return FileModel(
        tests=tests,
        asserts={k: _assertions(f) for k, f in tests.items()},
        bodies={k: inv._norm_body(f) for k, f in tests.items()},
        markers=inv.scan_markers(tree, rel),
        lines=text.splitlines(),
    )


def _enclosing(m: inv.Marker, fm: FileModel) -> str | None:
    """The test a marker suppresses: its decorated function, or the function
    whose body holds the call. None for module/class-level markers."""
    for name, func in fm.tests.items():
        start = min([func.lineno] + [d.lineno for d in func.decorator_list])
        if start <= m.line <= (func.end_lineno or func.lineno):
            return name
    return None


# ---- the rules ----------------------------------------------------------------


def marker_problem(m: inv.Marker, today: dt.date) -> str | None:
    """Why an ADDED marker is not acceptable, or None."""
    if m.kind != "xfail" and inv.is_environment_guard(m.reason):
        return None
    missing = []
    if not OWNER_RE.search(m.reason):
        missing.append("`owner=<who>`")
    hit = inv.EXPIRY_RE.search(m.reason)
    if hit:
        try:
            when = dt.date.fromisoformat(hit.group(1))
        except ValueError:
            return f"expires={hit.group(1)} is not a date"
        if when < today:
            return f"expires={when} is already past"
        if (when - today).days > MAX_EXPIRY_DAYS:
            return f"expires={when} is more than {MAX_EXPIRY_DAYS} days out"
    elif not RUNS_IN_RE.search(m.reason):
        missing.append("`expires=YYYY-MM-DD` or `runs-in=<where this case does run>`")
    if missing:
        return "not an environment guard; the reason needs " + " and ".join(missing)
    return None


_SLEEPS = ("time.sleep", "asyncio.sleep")
_CLOCKS = (
    "datetime.now",
    "datetime.utcnow",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "date.today",
    "datetime.date.today",
)
_NETWORK = (
    "socket.create_connection",
    "urllib.request.urlopen",
    "urlopen",
    "requests.get",
    "requests.post",
    "httpx.get",
    "httpx.post",
)
_REAL_PATHS = (
    "Path.home",
    "pathlib.Path.home",
    "os.path.expanduser",
    "Path.cwd",
    "pathlib.Path.cwd",
    "os.getcwd",
)


#: Blocked sleeps start here. Shorter ones are mostly lock-yield idioms in
#: concurrency tests; they are reported weekly, not blocked (lead, 2026-10-01:
#: 19 of the last 60 merges tripped the first, broader rule).
SLEEP_BLOCK_S = 0.5
_WRITES = {
    "write_text",
    "write_bytes",
    "mkdir",
    "touch",
    "unlink",
    "rmdir",
    "rename",
    "replace",
    "symlink_to",
    "hardlink_to",
    "chmod",
}
#: Functions whose DESTINATION is the second argument.
_TWO_PATH_WRITES = {
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "shutil.move",
    "os.rename",
    "os.replace",
}
_WRITE_FUNCS = {
    "shutil.rmtree",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "shutil.move",
    "os.remove",
    "os.unlink",
    "os.makedirs",
    "os.mkdir",
    "os.rename",
    "os.replace",
    "os.rmdir",
}


def _is_abs_literal(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value.startswith(("/", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", node.value)))
    )


def _real_root(node: ast.AST) -> str | None:
    """The real location an expression is rooted at (home, cwd, an absolute
    literal), following `/` joins, attribute access and method chains."""
    while True:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            node = node.left
        elif isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Call):
            name = inv._dotted(node.func)
            if name in _REAL_PATHS:
                return f"`{name}()`"
            if name in ("Path", "pathlib.Path", "os.path.join") and node.args:
                if _is_abs_literal(node.args[0]):
                    return f"absolute path {node.args[0].value!r}"
                node = node.args[0]
            elif isinstance(node.func, ast.Attribute):
                node = node.func.value
            else:
                return None
        else:
            return f"absolute path {node.value!r}" if _is_abs_literal(node) else None


def _write_mode(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(c in mode.value for c in "wax+")
    )


def hermeticity(func: ast.FunctionDef, lines: list[str]) -> tuple[list, list]:
    """(blocking, report_only) lists of (line, why) for a NEW test.

    Blocking is what actually flakes or pollutes: the network, writes to a real
    home / cwd / absolute path outside tmp_path, and sleeps >= SLEEP_BLOCK_S.
    Real-calendar reads and short sleeps are report-only.
    """
    block: list[tuple[int, str]] = []
    report: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        name = inv._dotted(node.func)
        target = None
        why = None
        if name in _SLEEPS and node.args:
            arg = node.args[0]
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, (int, float))
                and arg.value > 0
            ):
                why = f"real sleep `{name}({arg.value})`: wait on an event or patch the clock"
                target = block if arg.value >= SLEEP_BLOCK_S else report
        elif name in _CLOCKS:
            why, target = f"`{name}()` reads the real calendar", report
        elif name in _NETWORK:
            why, target = f"`{name}` reaches the network", block
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _WRITES:
            root = _real_root(node.func.value)
            if root:
                why, target = f"writes under {root}, outside tmp_path", block
        elif name in _WRITE_FUNCS and node.args:
            dest = node.args[1] if name in _TWO_PATH_WRITES and len(node.args) > 1 else node.args[0]
            root = _real_root(dest)
            if root:
                why, target = f"`{name}` writes under {root}, outside tmp_path", block
        elif name == "open" and node.args and _write_mode(node):
            root = _real_root(node.args[0])
            if root:
                why, target = f"opens {root} for writing, outside tmp_path", block
        if why and target is not None:
            line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            if not HERMETIC_OK_RE.search(line):
                target.append((node.lineno, why))
    return block, report


def unhermetic(func: ast.FunctionDef, lines: list[str]) -> list[tuple[int, str]]:
    """The BLOCKING hermeticity findings for a new test."""
    return hermeticity(func, lines)[0]


def _ledger(text: str | None) -> dict[str, tuple]:
    """node id -> (is_flaky, node, fields), through the gate's own parser."""
    from ci_required_tests import split_ledger_line

    out = {}
    for raw in (text or "").splitlines():
        parsed = split_ledger_line(raw)
        if parsed is not None:
            out[parsed[1]] = (parsed[0], parsed[1], tuple(sorted(parsed[2].items())))
    return out


def collectable(path: str | None) -> bool:
    """Would pytest's default discovery collect tests from this path?"""
    if not path or not path.startswith("tests/") or not path.endswith(".py"):
        return False
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def compute_delta(base: str, head: str, root: Path, today: dt.date) -> Delta:
    delta = Delta()
    base = merge_base(base, head, root)
    changes = changed_files(base, head, root)

    for status, old, new in changes:
        path = new or old or ""
        if not path.startswith(NON_FEATURE):
            added, deleted = line_counts(base, head, root, path)
            delta.feature_lines += added
            delta.product_deleted += deleted

    test_changes = [
        (old, new)
        for _s, old, new in changes
        if ((old or "").startswith("tests/") and (old or "").endswith(".py"))
        or ((new or "").startswith("tests/") and (new or "").endswith(".py"))
    ]
    befores = {old: model(_show(base, old, root), old) for old, _ in test_changes if old}
    afters = {new: model(_show(head, new, root), new) for _, new in test_changes if new}
    # A test counts as MOVED only if an identical body was ADDED to a file
    # pytest still collects; matched one-to-one, so a surviving copy can never
    # vouch for a deleted one.
    added_bodies: Counter = Counter()
    for old, new in test_changes:
        if not collectable(new):
            continue
        b = befores.get(old) if old else model(None, "")
        a = afters[new]
        for name, body in a.bodies.items():
            if name not in b.tests and body:
                added_bodies[body] += 1
    moved_bodies = added_bodies

    for old, new in test_changes:
        b = befores.get(old) if old else model(None, "")
        a = afters.get(new) if new else model(None, "")
        rel = new or old or ""
        lost_collection = old is not None and collectable(old) and not collectable(new)
        for name, body in b.bodies.items():
            if name not in a.tests or lost_collection:
                if moved_bodies[body] and body:
                    moved_bodies[body] -= 1  # moved, not removed
                    continue
                delta.removed_tests += 1
                delta.tampering.append(Finding(f"{old}::{name}", "test removed"))
            elif a.asserts[name] < b.asserts[name]:
                delta.removals_only = False
                delta.tampering.append(
                    Finding(
                        f"{rel}::{name}",
                        f"assertions loosened ({b.asserts[name]} -> {a.asserts[name]})",
                    )
                )
        for name in a.tests:
            if name not in b.tests:
                delta.new_tests += 1
                block, report = hermeticity(a.tests[name], a.lines)
                for line, why in block:
                    delta.unhermetic.append(Finding(f"{rel}:{line} ({name})", why))
                for line, why in report:
                    delta.notes.append(Finding(f"{rel}:{line} ({name})", why))

        def key(m: inv.Marker, fm: FileModel) -> tuple:
            return (m.kind, " ".join(m.reason.split()), _enclosing(m, fm), m.scope)

        had = Counter(key(m, b) for m in b.markers)
        for m in a.markers:
            if had[key(m, a)]:
                had[key(m, a)] -= 1
                continue
            target = _enclosing(m, a)
            existing = target in b.tests if target else bool(b.tests)
            if existing:
                delta.removals_only = False
                delta.tampering.append(
                    Finding(
                        f"{rel}:{m.line}",
                        f"new `{m.kind}` on a test that already existed: {m.reason!r}",
                    )
                )
            why = marker_problem(m, today)
            if why:
                delta.unexplained_markers.append(
                    Finding(f"{rel}:{m.line}", f"new `{m.kind}` -- {why}")
                )
        for m in a.markers:
            if m.expires and m.expires < today.isoformat():
                delta.expired.append(
                    Finding(
                        f"{rel}:{m.line}",
                        f"`{m.kind}` expired {m.expires} -- this PR touches the file, so revive "
                        "the test, delete it (its own PR), or re-justify with a new date",
                    )
                )

    before_ledger, after_ledger = (
        _ledger(_show(base, LEDGER, root)),
        _ledger(_show(head, LEDGER, root)),
    )
    for node, line in after_ledger.items():
        if before_ledger.get(node) != line:
            delta.removals_only = False
            delta.tampering.append(
                Finding(node, f"known-failing entry added or changed: {' '.join(map(str, line))}")
            )
    touched = {new or old for old, new in test_changes}
    for node, (_flaky, _node, fields) in after_ledger.items():
        when = dict(fields).get("expires", "")
        if when and node.split("::", 1)[0] in touched and when < today.isoformat():
            delta.expired.append(Finding(node, f"quarantine entry expired {when}"))
    return delta


def evaluate(delta: Delta, body: str) -> list[str]:
    failures = []
    if delta.tampering:
        listed = "; ".join(f"{f.where} ({f.what})" for f in delta.tampering[:20])
        reason = REMOVAL_RE.search(body or "")
        if not reason:
            failures.append(
                f"this PR removes, skips or loosens existing tests: {listed}. That is allowed "
                "only as its own reviewed change: add `Test-Removal: retired|consolidated|wrong "
                "-- <why>` to the PR body and get the other model family's review."
            )
        elif delta.feature_lines and not (
            reason.group(1).lower() == "retired" and delta.removals_only and delta.product_deleted
        ):
            failures.append(
                f"this PR changes product code (+{delta.feature_lines} lines outside tests/) AND "
                f"removes, skips or loosens existing tests: {listed}. Split the test change into "
                "its own PR. Only whole-test removals declared `Test-Removal: retired` may ride "
                "with product code, and only when the PR also deletes the code they covered."
            )
    for f in delta.unexplained_markers:
        failures.append(f"{f.where} {f.what}")
    for f in delta.expired:
        failures.append(f"{f.where} {f.what}")
    for f in delta.unhermetic:
        failures.append(
            f"{f.where} new test is not hermetic: {f.what}. "
            f"If it truly must, end the line with `# {HERMETIC_OK}: <reason>`."
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--body", type=Path)
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument(
        "--today", type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone.utc).date()
    )
    args = ap.parse_args(argv)
    try:
        delta = compute_delta(args.base, args.head, args.root, args.today)
    except RuntimeError as exc:
        print(f"test-hygiene: cannot read the diff: {exc}", file=sys.stderr)
        return 2
    body = ""
    if args.body and args.body.exists():
        body = args.body.read_text(encoding="utf-8", errors="replace")
    print(
        f"tests added {delta.new_tests}, removed {delta.removed_tests}, "
        f"tampering findings {len(delta.tampering)}, product lines added {delta.feature_lines}"
    )
    for n in delta.notes:
        print(f"note (not blocking) {n.where}: {n.what}")
    failures = evaluate(delta, body)
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
