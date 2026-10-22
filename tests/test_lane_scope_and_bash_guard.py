"""A PR stays inside its declared lane; shell commands that lost time are refused."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import drain_review_gate as review

_ROOT = Path(__file__).resolve().parents[1]
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _guard():
    spec = importlib.util.spec_from_file_location(
        "bash_guard", _ROOT / ".claude" / "hooks" / "bash_guard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lane_cli(tmp_path, body, paths, *flags):
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "drain_review_gate.py"), "--lane-scope",
         "--body-file", str(body_file), *flags],
        input="\n".join(paths) + "\n", capture_output=True, text=True,
    )


@pytest.mark.verifies(
    "development-coordination-runtime:A pull request stays inside its declared lane"
)
def test_a_smuggled_lineage_is_refused(tmp_path):
    body = "Drain-Review-Verdict: APPROVE\n\nLane: `scripts/peer_agent.py`, tests/\n"
    assert review.declared_lane(body) == ["scripts/peer_agent.py", "tests/"]
    ok = _lane_cli(tmp_path, body, ["scripts/peer_agent.py", "tests/test_peer_agent.py"])
    assert ok.returncode == 0, ok.stderr
    out = _lane_cli(tmp_path, body, ["scripts/peer_agent.py", "deploy/deploy_fail_safe.sh"])
    assert out.returncode == 2
    assert "deploy/deploy_fail_safe.sh" in out.stderr and "scripts/peer_agent.py" not in out.stderr


@pytest.mark.verifies(
    "development-coordination-runtime:A pull request stays inside its declared lane"
)
def test_a_release_critical_change_must_declare_a_lane(tmp_path):
    refused = _lane_cli(tmp_path, "no lane here", ["deploy/deploy_fail_safe.sh"], "--require")
    assert refused.returncode == 2
    assert _lane_cli(tmp_path, "no lane here", ["scripts/peer_agent.py"]).returncode == 0


@pytest.mark.verifies(
    "development-coordination-runtime:Agent shell commands that lost time are refused"
)
def test_heredoc_and_pkill_hazards_are_refused():
    guard = _guard()
    assert guard.heredoc_backslash("cat > f <<'EOF'\na\\nb\nEOF\n")
    assert guard.heredoc_backslash("cat > f <<'EOF'\nplain\nEOF\n") is None
    assert guard.self_matching_pkill("ssh host 'pkill -f rehearse'")
    assert guard.self_matching_pkill("ssh host \"pkill -f '[r]ehearse'\"") is None
    assert guard.self_matching_pkill("pkill -9 node") is None


@pytest.mark.verifies(
    "development-coordination-runtime:Agent shell commands that lost time are refused"
)
def test_a_push_that_did_not_land_is_reported(tmp_path):
    def git(cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                       env=_ENV)

    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", "-b", "lane", str(work))
    (work / "a.txt").write_text("a", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "a")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "lane")
    guard = _guard()
    assert guard.push_did_not_land("git push", work) is None
    (work / "a.txt").write_text("b", encoding="utf-8")
    git(work, "commit", "-q", "-am", "b")
    command = f"cd '{work.as_posix()}' && git push"
    assert "did not land" in guard.push_did_not_land(command, tmp_path)
    assert guard.push_did_not_land("git push origin HEAD:other", work) is None
