"""Forbid `patch()` / `setattr()` entered from inside a thread target.

`unittest.mock.patch` swaps a module attribute. That attribute is
process-global, not thread-local. When two threads enter the *same* patch, they
can interleave so that the second thread saves the first thread's mock as the
"original" value and restores that mock on exit — leaving it installed for the
remainder of the pytest process.

This is not a theoretical concern in this repo. One occurrence
(`tests/test_external_write_phase_2_atomicity.py`, fixed alongside this file)
patched `tinyassets.effectors.github_pr.subprocess.run` inside a thread worker.
Because that module does a plain `import subprocess`, the patch target *is* the
global `subprocess` module, so the leak replaced `subprocess.run` for every
later test in the process. Its canned stdout —
`https://github.com/x/x/pull/99` — was observed in CI as the output of
`subprocess.run` in roughly 70 unrelated tests across a dozen files (git
integration, packaging, invariants, release-reconcile, worktree status).

The visible cost: 111 quarantine-ledger entries flipped between two CI runs
whose trees differed by two test functions (#2197), because collection order
determined which tests ran while the mock was installed. That is what made the
suite look "order-dependent" and forced `scripts/ci_required_tests.py` to run
serially.

The fix is always the same shape: hoist the patch out of the worker and wrap
the whole concurrent section on the calling thread. That keeps the mock in
force for the threads while making install/restore single-threaded.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent

# Callables that mutate PROCESS-GLOBAL state on enter and restore it on exit.
# Entering one from more than one thread is the unsafe pattern.
#
# Two separate shapes, deliberately not merged into one name set:
#
#   * bare `patch(...)` / `mock.patch(...)` / `mock.patch.object(...)`
#   * `monkeypatch.<verb>(...)` — pytest's fixture, equally process-global
#
# `setattr` etc. are matched ONLY on a `monkeypatch` receiver. Matching them by
# bare name would flag builtin `setattr(obj, "x", 1)` on a thread-local object,
# which is perfectly safe — a false-positive gate is worse than no gate, since
# it blocks correct code and trains people to delete the check.
_PATCH_NAMES = {"patch"}
_MONKEYPATCH_VERBS = {"setattr", "setenv", "delenv", "delattr", "chdir", "syspath_prepend"}
_MONKEYPATCH_RECEIVERS = {"monkeypatch", "mp"}


def _thread_target_names(tree: ast.Module) -> set[str]:
    """Function names handed to a thread/executor as the callable to run."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        targets = [kw.value for kw in node.keywords if kw.arg == "target"
                   and isinstance(kw.value, (ast.Name, ast.Attribute))]
        # Most calls cannot nominate a thread target. Preserve the existing
        # matching rule without formatting their function ASTs needlessly.
        if targets and "Thread" in ast.dump(node.func):
            names.update(target.id if isinstance(target, ast.Name) else target.attr
                         for target in targets)
        # executor.submit(fn, ...) / executor.map(fn, ...)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "submit",
            "map",
        }:
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    names.add(arg.id)
    return names


def _offenders(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    targets = _thread_target_names(tree)
    if not targets:
        return []
    found: list[str] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if func.name not in targets:
            continue
        for sub in ast.walk(func):
            if not isinstance(sub, ast.Call):
                continue
            name = _unsafe_call_name(sub.func)
            if name:
                found.append(
                    f"{path.name}:{sub.lineno} — {name} inside thread target "
                    f"{func.name!r}"
                )
    return found


def _unsafe_call_name(func: ast.expr) -> str | None:
    """Return a label if this call mutates process-global state, else None."""
    # patch(...)
    if isinstance(func, ast.Name) and func.id in _PATCH_NAMES:
        return f"{func.id}()"
    if isinstance(func, ast.Attribute):
        # mock.patch(...) / mock.patch.object(...) / monkeypatch.setattr(...)
        if func.attr in _PATCH_NAMES:
            return f"{func.attr}()"
        if isinstance(func.value, ast.Attribute) and func.value.attr in _PATCH_NAMES:
            return f"{func.value.attr}.{func.attr}()"
        receiver = getattr(func.value, "id", None)
        if func.attr in _MONKEYPATCH_VERBS and receiver in _MONKEYPATCH_RECEIVERS:
            return f"{receiver}.{func.attr}()"
    return None


def test_no_patching_from_inside_a_thread_target() -> None:
    offenders: list[str] = []
    for path in sorted(_TESTS.rglob("test_*.py")):
        offenders.extend(_offenders(path))
    assert not offenders, (
        "patch()/setattr() must not be entered from inside a thread target — "
        "the swap is process-global, so two threads can interleave and leave "
        "the mock permanently installed, poisoning every later test in the "
        "process. Hoist it around the whole concurrent section instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_detector_can_actually_fire(tmp_path: Path) -> None:
    """Guard the guard: a scanner that never matches proves nothing."""
    sample = tmp_path / "test_sample.py"
    sample.write_text(
        "import threading\n"
        "from unittest.mock import patch\n"
        "def worker():\n"
        "    with patch('subprocess.run'):\n"
        "        pass\n"
        "def test_x():\n"
        "    t = threading.Thread(target=worker)\n"
        "    t.start(); t.join()\n",
        encoding="utf-8",
    )
    assert _offenders(sample), "detector failed to flag the known-bad pattern"

    safe = tmp_path / "test_safe.py"
    safe.write_text(
        "import threading\n"
        "from unittest.mock import patch\n"
        "def worker():\n"
        "    pass\n"
        "def test_x():\n"
        "    with patch('subprocess.run'):\n"
        "        t = threading.Thread(target=worker)\n"
        "        t.start(); t.join()\n",
        encoding="utf-8",
    )
    assert not _offenders(safe), "detector flagged the correct hoisted pattern"


def test_builtin_setattr_in_a_thread_is_not_flagged(tmp_path: Path) -> None:
    """False positives block correct code, which is worse than no check.

    Builtin `setattr` on a thread-local object is safe and common; only
    `monkeypatch.setattr`, which mutates process-global state, is unsafe.
    """
    sample = tmp_path / "test_builtin.py"
    sample.write_text(
        "import threading\n"
        "class Box: pass\n"
        "def worker(box):\n"
        "    setattr(box, 'done', True)\n"
        "def test_x():\n"
        "    b = Box()\n"
        "    t = threading.Thread(target=worker, args=(b,))\n"
        "    t.start(); t.join()\n",
        encoding="utf-8",
    )
    assert not _offenders(sample), (
        "builtin setattr on a local object must not be flagged"
    )


def test_monkeypatch_setattr_in_a_thread_is_flagged(tmp_path: Path) -> None:
    """The receiver is what makes it unsafe, not the verb."""
    sample = tmp_path / "test_mp.py"
    sample.write_text(
        "import threading\n"
        "def worker(monkeypatch):\n"
        "    monkeypatch.setattr('subprocess.run', None)\n"
        "def test_x(monkeypatch):\n"
        "    t = threading.Thread(target=worker, args=(monkeypatch,))\n"
        "    t.start(); t.join()\n",
        encoding="utf-8",
    )
    assert _offenders(sample), "monkeypatch.setattr in a thread must be flagged"


@pytest.mark.parametrize("dispatch", [
    "threading.Thread(target=worker)",
    "threading.Thread(target=holder.worker)",
    "ThreadFactory()(target=worker)",
    "executor.submit(worker)",
    "executor.map(worker, ())",
])
def test_dispatch_shapes_retain_patch_detection(tmp_path: Path, dispatch: str) -> None:
    sample = tmp_path / "test_dispatch.py"
    sample.write_text(
        "def worker():\n"
        "    with mock.patch.object(module, 'run'):\n"
        "        pass\n"
        "def test_dispatch():\n"
        f"    {dispatch}\n",
        encoding="utf-8",
    )
    assert _offenders(sample) == [
        "test_dispatch.py:2 — patch.object() inside thread target 'worker'",
    ]
