"""The primary checkout heals to current main at session start (founder 2026-10-06:
"the main checkout being behind should not be possible to happen")."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session_sync_gate.py"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _identity(repo: Path) -> None:
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")


def _commit(repo: Path, message: str) -> None:
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", message)
    git(repo, "push", "-q", "origin", "main")


def _repos(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    _identity(seed)
    (seed / "AGENTS.md").write_text("old rules\n")
    _commit(seed, "one")
    primary = tmp_path / "primary"
    git(tmp_path, "clone", "-q", str(origin), str(primary))
    _identity(primary)
    (seed / "AGENTS.md").write_text("current rules\n")
    _commit(seed, "two")
    return origin, primary


def run(cwd: Path) -> str:
    return subprocess.run([sys.executable, str(SCRIPT), "--heal"], cwd=cwd, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def test_behind_main_fast_forwards(tmp_path):
    _, primary = _repos(tmp_path)
    run(primary)
    assert (primary / "AGENTS.md").read_text() == "current rules\n"


def test_feature_branch_and_edits_are_saved_then_main_is_current(tmp_path):
    _, primary = _repos(tmp_path)
    git(primary, "switch", "-q", "-c", "old-feature")
    (primary / "AGENTS.md").write_text("a local edit\n")
    (primary / "notes.txt").write_text("untracked\n")
    out = run(primary)
    assert git(primary, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (primary / "AGENTS.md").read_text() == "current rules\n"
    assert (primary / "notes.txt").read_text() == "untracked\n"  # untracked rides along
    backups = git(primary, "branch", "--list", "backup/primary-autosave-*").split()
    assert backups, out
    saved = git(primary, "show", f"{backups[-1]}:AGENTS.md")
    assert saved == "a local edit"  # nothing discarded


def test_a_linked_worktree_is_never_touched(tmp_path):
    _, primary = _repos(tmp_path)
    linked = tmp_path / "linked"
    git(primary, "worktree", "add", "-q", "-b", "lane", str(linked))
    (linked / "AGENTS.md").write_text("lane edit\n")
    run(linked)
    assert git(linked, "rev-parse", "--abbrev-ref", "HEAD") == "lane"
    assert (linked / "AGENTS.md").read_text() == "lane edit\n"
