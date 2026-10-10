"""Tests for ``tinyassets.git_bridge`` (Phase 7.2 G1).

Exercises the primitives end-to-end against real ``git init`` repositories
in ``tmp_path``. Skips cleanly when git isn't installed so CI environments
without git (none expected, but belt-and-braces) don't fail hard.

The module is a thin subprocess wrapper, so mocking would only re-verify
the wiring. These tests hit real git so they verify the ACTUAL contract
the backend will depend on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tinyassets import git_bridge

# Skip the whole module if git isn't installed — local-only branch of the
# spec where git features simply no-op.
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git binary not available",
)


AUTHOR = "Test Dev <test@example.invalid>"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path, *, bare: bool = False) -> None:
    path.mkdir(parents=True, exist_ok=True)
    args = ["git", "init", "--initial-branch=main"]
    if bare:
        args.append("--bare")
    _run(args, path)
    if not bare:
        # Deterministic identity so commits don't pick up host global config.
        _run(["git", "config", "user.email", "ci@example.invalid"], path)
        _run(["git", "config", "user.name", "CI Bot"], path)
        # Silence hint about commit.gpgsign.
        _run(["git", "config", "commit.gpgsign", "false"], path)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Ensure ``is_enabled`` re-probes between tests."""
    git_bridge.invalidate_cache()
    yield
    git_bridge.invalidate_cache()


# ─── is_enabled ──────────────────────────────────────────────────────────


def test_is_enabled_true_inside_repo(tmp_path: Path):
    _init_repo(tmp_path)
    assert git_bridge.is_enabled(repo_path=tmp_path) is True


def test_is_enabled_false_outside_repo(tmp_path: Path):
    # tmp_path has no .git; no parent should be a git repo either
    assert git_bridge.is_enabled(repo_path=tmp_path) is False


def test_is_enabled_is_not_cached_across_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The probe runs in the owner's git cell, so the answer is per-owner.

    Caching it process-wide would let one owner's refusal (or one repository's
    absence) disable git for the next owner the daemon serves.
    """
    _init_repo(tmp_path)
    assert git_bridge.is_enabled(repo_path=tmp_path) is True
    present = {"git": True}
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which",
        lambda name: real_which(name) if present.get(name, True) else None,
    )
    present["git"] = False
    assert git_bridge.is_enabled(repo_path=tmp_path) is False
    present["git"] = True
    assert git_bridge.is_enabled(repo_path=tmp_path) is True


def test_is_enabled_false_when_git_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "git" else "/bin/true")
    assert git_bridge.is_enabled(repo_path=tmp_path) is False


# ─── stage + commit ──────────────────────────────────────────────────────


def _seed_initial_commit(repo: Path) -> None:
    """Leave the repo with at least one commit so HEAD resolves."""
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-m", "seed", "--no-gpg-sign"], repo)


def test_stage_and_commit_returns_sha(tmp_path: Path):
    _init_repo(tmp_path)
    _seed_initial_commit(tmp_path)

    f = tmp_path / "branches" / "my-branch.yaml"
    f.parent.mkdir()
    f.write_text("name: my-branch\n", encoding="utf-8")

    staged = git_bridge.stage(f, repo_path=tmp_path)
    assert staged is True

    result = git_bridge.commit("add my-branch", AUTHOR, repo_path=tmp_path)
    assert result.ok, result.error
    assert len(result.sha) == 40  # full sha

    # Verify the commit really landed
    log = _run(["git", "log", "-1", "--format=%s"], tmp_path).stdout.strip()
    assert log == "add my-branch"


def test_commit_with_paths_arg_stages_and_commits(tmp_path: Path):
    _init_repo(tmp_path)
    _seed_initial_commit(tmp_path)

    f = tmp_path / "goals" / "research-paper.yaml"
    f.parent.mkdir()
    f.write_text("name: research-paper\n", encoding="utf-8")

    result = git_bridge.commit(
        "add goal", AUTHOR, paths=[f], repo_path=tmp_path,
    )
    assert result.ok, result.error
    assert result.sha


def test_commit_returns_nothing_to_commit_on_clean_tree(tmp_path: Path):
    _init_repo(tmp_path)
    _seed_initial_commit(tmp_path)

    result = git_bridge.commit("empty", AUTHOR, repo_path=tmp_path)
    assert result.ok is False
    assert "nothing to commit" in (result.error or result.message).lower()


def test_stage_no_op_when_git_disabled(tmp_path: Path):
    # Not a repo; stage must return False cleanly, not raise
    f = tmp_path / "x.yaml"
    f.write_text("x: 1\n", encoding="utf-8")
    assert git_bridge.stage(f, repo_path=tmp_path) is False


def test_commit_no_op_when_git_disabled(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("x: 1\n", encoding="utf-8")
    result = git_bridge.commit("x", AUTHOR, paths=[f], repo_path=tmp_path)
    assert result.ok is False
    assert "git not enabled" in result.error


# ─── has_uncommitted_changes ─────────────────────────────────────────────


def test_has_uncommitted_changes_detects_dirty_file(tmp_path: Path):
    _init_repo(tmp_path)
    _seed_initial_commit(tmp_path)

    target = tmp_path / "branches" / "b.yaml"
    target.parent.mkdir()
    target.write_text("v: 1\n", encoding="utf-8")
    _run(["git", "add", "b.yaml"], target.parent)
    _run(["git", "commit", "-m", "add b", "--no-gpg-sign"], tmp_path)

    # Clean
    assert git_bridge.has_uncommitted_changes(target, repo_path=tmp_path) is False
    # Dirty
    target.write_text("v: 2\n", encoding="utf-8")
    assert git_bridge.has_uncommitted_changes(target, repo_path=tmp_path) is True


def test_has_uncommitted_changes_false_when_git_disabled(tmp_path: Path):
    f = tmp_path / "x.yaml"
    f.write_text("x: 1\n", encoding="utf-8")
    assert git_bridge.has_uncommitted_changes(f, repo_path=tmp_path) is False


# ─── push / pull against a bare remote ───────────────────────────────────


def _make_clone_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare remote + clone that tracks it."""
    remote = tmp_path / "remote.git"
    _init_repo(remote, bare=True)
    local = tmp_path / "local"
    _run(["git", "clone", str(remote), str(local)], tmp_path)
    _run(["git", "config", "user.email", "ci@example.invalid"], local)
    _run(["git", "config", "user.name", "CI Bot"], local)
    _run(["git", "config", "commit.gpgsign", "false"], local)
    # Seed so HEAD exists
    (local / "README.md").write_text("seed\n", encoding="utf-8")
    _run(["git", "add", "README.md"], local)
    _run(["git", "commit", "-m", "seed", "--no-gpg-sign"], local)
    _run(["git", "push", "origin", "main"], local)
    return remote, local


def test_push_round_trip(tmp_path: Path):
    _, local = _make_clone_with_remote(tmp_path)
    (local / "branches.yaml").write_text("a: 1\n", encoding="utf-8")
    result = git_bridge.commit(
        "add branches", AUTHOR, paths=[local / "branches.yaml"], repo_path=local,
    )
    assert result.ok

    pushed = git_bridge.push(repo_path=local, branch="main")
    assert pushed.ok, pushed.error


def test_pull_advances_head(tmp_path: Path):
    remote, local_a = _make_clone_with_remote(tmp_path)
    # Second clone to push an update from
    local_b = tmp_path / "local_b"
    _run(["git", "clone", str(remote), str(local_b)], tmp_path)
    _run(["git", "config", "user.email", "ci@example.invalid"], local_b)
    _run(["git", "config", "user.name", "CI Bot"], local_b)
    _run(["git", "config", "commit.gpgsign", "false"], local_b)
    (local_b / "new.yaml").write_text("x: 1\n", encoding="utf-8")
    _run(["git", "add", "new.yaml"], local_b)
    _run(["git", "commit", "-m", "from b", "--no-gpg-sign"], local_b)
    _run(["git", "push", "origin", "main"], local_b)

    pulled = git_bridge.pull(repo_path=local_a, branch="main")
    assert pulled.ok, pulled.error
    assert pulled.pulled_commits == 1
    assert (local_a / "new.yaml").exists()


def test_pull_conflict_aborts_and_returns_file_list(tmp_path: Path):
    remote, local_a = _make_clone_with_remote(tmp_path)
    local_b = tmp_path / "local_b"
    _run(["git", "clone", str(remote), str(local_b)], tmp_path)
    _run(["git", "config", "user.email", "ci@example.invalid"], local_b)
    _run(["git", "config", "user.name", "CI Bot"], local_b)
    _run(["git", "config", "commit.gpgsign", "false"], local_b)

    # Both commit conflicting content to the same file
    shared = "conflict.yaml"
    (local_a / shared).write_text("val: a\n", encoding="utf-8")
    _run(["git", "add", shared], local_a)
    _run(["git", "commit", "-m", "a side", "--no-gpg-sign"], local_a)

    (local_b / shared).write_text("val: b\n", encoding="utf-8")
    _run(["git", "add", shared], local_b)
    _run(["git", "commit", "-m", "b side", "--no-gpg-sign"], local_b)
    _run(["git", "push", "origin", "main"], local_b)

    result = git_bridge.pull(repo_path=local_a, branch="main")
    assert result.ok is False
    assert shared in result.conflicts, (
        f"expected {shared} in conflicts, got {result.conflicts}"
    )
    # Working tree should be clean again after the abort
    status = _run(["git", "status", "--porcelain"], local_a).stdout.strip()
    assert "UU" not in status, (
        f"merge should have been aborted; got status: {status!r}"
    )


def test_push_no_op_when_git_disabled(tmp_path: Path):
    assert git_bridge.push(repo_path=tmp_path).ok is False


def test_pull_no_op_when_git_disabled(tmp_path: Path):
    assert git_bridge.pull(repo_path=tmp_path).ok is False


# ─── open_pr ─────────────────────────────────────────────────────────────


def test_open_pr_returns_manual_when_gh_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    result = git_bridge.open_pr(
        "test PR", "body here", repo_path=tmp_path,
    )
    assert result.ok is False
    assert result.mode == "manual"
    assert "gh CLI not installed" in result.error


# ─── CommitResult / PullResult defaults ──────────────────────────────────


def test_pull_result_default_conflicts_is_empty_list():
    """Mutable default guard — each instance gets its own list."""
    r1 = git_bridge.PullResult(ok=True)
    r2 = git_bridge.PullResult(ok=True)
    assert r1.conflicts == []
    assert r2.conflicts == []
    r1.conflicts.append("x")
    assert r2.conflicts == [], "conflicts list leaked between instances"
