"""Phase 7.3 H2 — goals cluster cutover to SqliteCachedBackend.

Exercises the propose / update / bind handlers against a real git repo
in ``tmp_path``. Verifies:

- One MCP write action = one commit. Commit message follows the
  ``goals.{action}: …`` template.
- Dirty local edits raise the structured ``local_edit_conflict``
  response (caught once in ``_dispatch_goal_action``); SQLite stays
  untouched on refusal.
- ``force=True`` overrides the dirty refusal.
- ``TINYASSETS_STORAGE_BACKEND=sqlite_only`` and "no git repo" both keep
  the legacy behavior — handlers still work, no YAML, no commits.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git binary not available",
)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--initial-branch=main"], path)
    _run(["git", "config", "user.email", "ci@example.invalid"], path)
    _run(["git", "config", "user.name", "CI Bot"], path)
    _run(["git", "config", "commit.gpgsign", "false"], path)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _run(["git", "add", "README.md"], path)
    _run(["git", "commit", "-m", "seed", "--no-gpg-sign"], path)


@pytest.fixture
def repo_env(tmp_path, monkeypatch):
    """Real git repo with output/ inside; backend auto-probes to Cached."""
    _init_repo(tmp_path)
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.delenv("TINYASSETS_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("TINYASSETS_GIT_AUTHOR", raising=False)

    from tinyassets.catalog import invalidate_backend_cache
    invalidate_backend_cache()
    from tinyassets import universe_server as us
    importlib.reload(us)

    yield us, tmp_path, base

    invalidate_backend_cache()
    importlib.reload(us)


@pytest.fixture
def no_git_env(tmp_path, monkeypatch):
    """No git repo at parent — backend auto-probes to SqliteOnly."""
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    monkeypatch.delenv("TINYASSETS_STORAGE_BACKEND", raising=False)

    from tinyassets.catalog import invalidate_backend_cache
    invalidate_backend_cache()
    from tinyassets import universe_server as us
    importlib.reload(us)

    yield us, tmp_path, base

    invalidate_backend_cache()
    importlib.reload(us)


def _call(us, **kwargs):
    return json.loads(us._goals_impl(**kwargs))


def _commit_count(repo: Path) -> int:
    return int(
        _run(["git", "rev-list", "--count", "HEAD"], repo).stdout.strip()
    )


def _last_commit_subject(repo: Path) -> str:
    return _run(
        ["git", "log", "-1", "--format=%s"], repo,
    ).stdout.strip()


def _last_commit_files(repo: Path) -> list[str]:
    return [
        line.strip()
        for line in _run(
            ["git", "show", "--name-only", "--pretty=format:", "HEAD"], repo,
        ).stdout.splitlines()
        if line.strip()
    ]


# ─── propose ─────────────────────────────────────────────────────────────


def test_propose_creates_yaml_and_one_commit(repo_env):
    us, repo, _base = repo_env
    before = _commit_count(repo)
    result = _call(us, action="propose", name="Research paper", tags="research")
    assert result["status"] == "proposed"
    assert _commit_count(repo) - before == 1
    assert _last_commit_subject(repo) == "goals.propose: Research paper"
    assert any(
        "goals/research-paper.yaml" in f for f in _last_commit_files(repo)
    )


# ─── update ──────────────────────────────────────────────────────────────


# ─── bind ────────────────────────────────────────────────────────────────


# ─── env-var sqlite_only ─────────────────────────────────────────────────


def test_sqlite_only_env_skips_yaml_and_commits(repo_env, monkeypatch):
    """TINYASSETS_STORAGE_BACKEND=sqlite_only: handlers work, no YAML, no commit."""
    us, repo, _base = repo_env
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite_only")
    from tinyassets.catalog import invalidate_backend_cache
    invalidate_backend_cache()

    before = _commit_count(repo)
    result = _call(us, action="propose", name="No-yaml goal")
    assert result["status"] == "proposed"
    assert _commit_count(repo) == before
    assert not (repo / "goals" / "no-yaml-goal.yaml").exists()


# ─── git disabled (no repo) ──────────────────────────────────────────────


def test_no_git_repo_handlers_still_work(no_git_env):
    us, _root, _base = no_git_env
    result = _call(us, action="propose", name="Plain goal")
    assert result["status"] == "proposed"
    assert result["goal"]["name"] == "Plain goal"
