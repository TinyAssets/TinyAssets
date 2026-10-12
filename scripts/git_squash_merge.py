"""Squash-merge-aware "is this branch already merged?" detection.

Part of the branch lifecycle automation; see
``docs/reference/worktree-discipline.md``.

GitHub squash-merges (this repo's default — ``delete_branch_on_merge=true``)
rewrite history: the merged change lands as a brand-new commit on main, so
``git merge-base --is-ancestor <branch> main`` returns false even though every
change on the branch is already in main. The original Layer-1 (branch_janitor)
and Layer-2 (wt.py) checks used ``--is-ancestor`` alone, so they were blind to
the *default* merge style — the structural cause of merged branches/worktrees
piling up (the safe teardown path refused the common case, so lanes were either
``--force``-discarded or never torn down).

This module adds the standard squash-detection fallback: synthesize a commit
holding the branch's cumulative tree on top of the merge-base, then ask
``git cherry`` whether that patch is already present on the base ref.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol


class _Completed(Protocol):
    returncode: int
    stdout: str


Runner = Callable[[Sequence[str]], _Completed]


def _first_line(text: str) -> str:
    text = (text or "").strip()
    return text.splitlines()[0].strip() if text else ""


def is_merged_into(run: Runner, ref: str, base_ref: str) -> bool:
    """Return True if every change in ``ref`` is already present on ``base_ref``.

    Catches fast-forward / merge-commit merges (``ref`` is an ancestor of
    ``base_ref``) *and* squash merges (``ref``'s cumulative diff is already
    applied on ``base_ref``). ``run`` is a subprocess runner returning an object
    with ``returncode`` and ``stdout`` (e.g. ``subprocess.run(..., text=True,
    capture_output=True)``). Conservative on any git error: returns False so
    callers refuse teardown rather than discard unmerged work.
    """
    # Cheap path: ancestor check catches fast-forward + merge-commit merges.
    if run(["git", "merge-base", "--is-ancestor", ref, base_ref]).returncode == 0:
        return True

    # Squash path: is base_ref already carrying ref's cumulative patch?
    mb = run(["git", "merge-base", ref, base_ref])
    merge_base = _first_line(mb.stdout) if mb.returncode == 0 else ""
    if not merge_base:
        return False

    tree = run(["git", "rev-parse", f"{ref}^{{tree}}"])
    tree_id = _first_line(tree.stdout) if tree.returncode == 0 else ""
    if not tree_id:
        return False

    dangling = run(["git", "commit-tree", tree_id, "-p", merge_base, "-m", "_"])
    dangling_sha = _first_line(dangling.stdout) if dangling.returncode == 0 else ""
    if not dangling_sha:
        return False

    cherry = run(["git", "cherry", base_ref, dangling_sha])
    if cherry.returncode != 0:
        return False
    lines = [ln.strip() for ln in (cherry.stdout or "").splitlines() if ln.strip()]
    # ``git cherry`` prefixes "- <sha>" when the patch is already upstream
    # (merged) and "+ <sha>" when it is still missing. No output => the
    # synthesized patch is empty (nothing to merge) => treat as merged.
    return all(ln.startswith("-") for ln in lines)
