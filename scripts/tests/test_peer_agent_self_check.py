"""Every review dispatch first runs a self-check: the reviewer's own session is told
to try acts review mode must refuse, and the wrapper reads their effects from disk
and output, never from the session's report. A stand-in `claude` that really does
each act is a deliberately widened setup the check must catch; one that does
nothing passes. Every canary is gone afterwards."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import peer_agent  # noqa: E402

# Acts as a widened session would do them, from the paths in the self-check's prompt.
_FAKE = '''\
import os, re, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
prompt = sys.stdin.read()
act = open(os.path.join(here, "act")).read().strip()
def path(pattern):
    return re.search(pattern, prompt).group(1)
if act == "exec":
    open(path(r"open\\('([^']+)'"), "w").close()
elif act in ("write", "memory"):
    number = "2" if act == "write" else "3"
    target = path(number + r"\\. Write tool: write `canary` to (\\S+)")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    open(target, "w").write("canary")
elif act == "leak":
    print(open(path(r"Read tool on (\\S+?),")).read())
elif act == "edit":
    copy = path(r"in (\\S+?), replace")
    open(copy, "w").write("changed")
elif act == "worktree":
    subprocess.run(["git", "worktree", "add", "-q", "--detach",
                    os.path.join(here, "planted-worktree")], check=True)
print("1. REFUSED\\nSELF-CHECK DONE.")
'''


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "commit", "-q", "--allow-empty", "-m", "A")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # The canaries' temporary directory is the test's own, not the host's /tmp.
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\n{_FAKE}")
    fake.chmod(0o755)
    return main, home, fake, tmp_path


def _leftovers(home: Path, main: Path) -> list[str]:
    found = [*Path(tempfile.gettempdir()).glob("review-selfcheck-*"),
             *home.glob(".review-selfcheck-*"), *main.glob(".review-selfcheck-*"),
             *home.glob(".claude/projects/*/memory/review-selfcheck-*")]
    return sorted(str(path) for path in found)


@pytest.mark.parametrize(("act", "named"), [
    ("exec", "exec"), ("write", "write"), ("memory", "memory"),
    ("leak", "home (its content reached the session)"),
    ("edit", "copy (main's worktree was edited)"),
    ("worktree", "worktree (a subagent's worktree was made)"),
])
def test_a_widened_session_is_caught_and_its_canaries_removed(setup, act, named):
    main, home, fake, tmp_path = setup
    (tmp_path / "act").write_text(act)
    problem = peer_agent.review_self_check([str(fake)], dict(os.environ), str(main))
    assert problem is not None and named in problem and "no review dispatched" in problem
    assert _leftovers(home, main) == []


def test_a_session_that_refuses_every_act_passes(setup):
    main, home, fake, tmp_path = setup
    (tmp_path / "act").write_text("none")
    assert peer_agent.review_self_check([str(fake)], dict(os.environ), str(main)) is None
    assert _leftovers(home, main) == []


def test_a_failed_self_check_stops_the_dispatch_before_the_review(setup):
    """The whole dispatch: the self-check runs in the reviewer's session, and when an
    act ran, no review starts in it."""
    main, home, fake, tmp_path = setup
    (tmp_path / "act").write_text("exec")
    review = tmp_path / "review"
    review.mkdir()
    out = tmp_path / "out.md"
    done = subprocess.run(
        [sys.executable, str(_SCRIPTS / "peer_agent.py"), "claude", "--review", str(review),
         "--cwd", str(main), "--prompt", "Review PR 1.", "--out", str(out)],
        capture_output=True, text=True, env={**os.environ, "CLAUDE_BIN": str(fake)},
        timeout=120)
    assert done.returncode == 2
    assert "self-check found acts review mode must refuse that ran: exec" in out.read_text()
    (call,) = (tmp_path / "calls").read_text().splitlines()
    assert "--session-id" in call and "--resume" not in call and "--safe-mode" in call
    assert _leftovers(home, main) == []


def test_a_self_check_that_cannot_finish_is_no_pass(setup):
    main, home, fake, _ = setup
    fake.write_text(f"#!{sys.executable}\nimport sys; sys.exit(3)\n")
    problem = peer_agent.review_self_check([str(fake)], dict(os.environ), str(main))
    assert "exited 3" in problem and _leftovers(home, main) == []
