"""The SessionStart sync gate never moves a Linux session off its own branch."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "session_sync_gate_hook.py"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


@pytest.mark.skipif(
    os.name == "nt",
    reason="the dev box's primary checkout is healed by design; owner=sync-gate runs-in=linux-ci",
)
@pytest.mark.verifies(
    "development-coordination-runtime:Session Orientation Reports Repository Freshness Without "
    "Mutating Work"
)
def test_a_dirty_session_branch_survives_a_compact_session_start(tmp_path: Path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.check_call(["git", "init", "-q", "--bare", "-b", "main", str(origin)])
    subprocess.check_call(["git", "init", "-q", "-b", "main", str(seed)])
    _git(seed, "config", "user.email", "gate@localhost")
    _git(seed, "config", "user.name", "gate")
    (seed / "notes.md").write_text("one\n", encoding="utf-8")
    _git(seed, "add", "notes.md")
    _git(seed, "commit", "-q", "-m", "one")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")

    session = tmp_path / "session"
    subprocess.check_call(["git", "clone", "-q", str(origin), str(session)])
    _git(session, "config", "user.email", "gate@localhost")
    _git(session, "config", "user.name", "gate")
    _git(session, "switch", "-q", "-c", "claude/session-branch")
    (session / "notes.md").write_text("one\nin-flight edit\n", encoding="utf-8")
    # origin/main moves on, so a heal would have something to fast-forward to.
    (seed / "notes.md").write_text("two\n", encoding="utf-8")
    _git(seed, "commit", "-q", "-am", "two")
    _git(seed, "push", "-q", "origin", "main")
    (session / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "session_sync_gate.py", session / "scripts")

    payload = {"hook_event_name": "SessionStart", "source": "compact", "cwd": str(session)}
    proc = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
        capture_output=True, cwd=session, timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert _git(session, "rev-parse", "--abbrev-ref", "HEAD") == "claude/session-branch"
    assert (session / "notes.md").read_text(encoding="utf-8") == "one\nin-flight edit\n"
    assert _git(session, "branch", "--list", "backup/*") == ""
    assert "git switch" not in proc.stdout
    assert "Merge origin/main into this branch" in proc.stdout
