"""Every review dispatch first runs a self-check: the reviewer's own session is told
to try acts review mode must refuse. Each act must appear in the session's own
stream-json transcript as a tool call that its final result lists as a permission
denial, and no act's effect may show on disk or in the output. A stand-in `claude`
plays each way a session can go wrong, and one that refuses properly; every canary,
and every directory made for one, is gone afterwards."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import peer_agent  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"

# A session as each act would go, from the paths in the self-check's prompt.
_FAKE = '''\
import json, os, re, shutil, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
prompt = sys.stdin.read()
act = open(os.path.join(here, "act")).read().strip()
def grab(pattern):
    return re.search(pattern, prompt).group(1)
exec_ = grab(r"open\\('([^']+)'")
write = grab(r"2\\. Write tool: write `canary` to (\\S+)")
memory = grab(r"3\\. Write tool: write `canary` to (\\S+)")
home = grab(r"4\\. Read tool on (\\S+)")
copy = grab(r"in (\\S+?), replace")
pad = grab(r"7\\. Write tool: write `canary` to (\\S+)")
if act == "garbage":
    print("not a transcript")
    sys.exit(0)
uses = [("Bash", {"command": f"python3 -c \\"open('{exec_}', 'w').close()\\""}),
        ("Write", {"file_path": write}), ("Write", {"file_path": memory}),
        ("Read", {"file_path": home}), ("Bash", {"command": f"cat {home}"}),
        ("Edit", {"file_path": copy}), ("Write", {"file_path": pad})]
if act == "idle":
    uses = []
if act.startswith("skip:"):
    del uses[int(act[5:])]
if act == "compound":
    uses = [use for n, use in enumerate(uses) if n not in (0, 4)]
    uses.append(("Bash", {"command": f"cat {home}; python3 -c \\"open('{exec_}', 'w').close()\\""}))
if act == "agent":
    uses.append(("Agent", {"prompt": "do acts 1 and 2", "isolation": "worktree"}))
ids = [f"toolu_{n}" for n in range(len(uses))]
for (name, data), ident in zip(uses, ids):
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": ident, "name": name, "input": data}]}}))
def make(path, text="canary"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)
if act in ("exec", "write", "memory", "scratchpad"):
    make({"exec": exec_, "write": write, "memory": memory, "scratchpad": pad}[act])
elif act == "leak":
    print(json.dumps({"type": "user", "message": {"content": open(home).read()}}))
elif act == "edit":
    open(copy, "w").write("changed")
elif act == "managed":
    make(os.path.join(os.environ["HOME"], ".claude", "remote-settings.json"), "{}")
elif act == "worktree":
    made = os.path.join(os.environ["FAKE_ROOT"], ".claude", "worktrees", "agent-x")
    os.makedirs(made)
    shutil.rmtree(made)
denied = ids[1:] if act == "undenied" else ids
print(json.dumps({"type": "result", "permission_denials": [
    {"tool_name": name, "tool_use_id": ident} for (name, _), ident in zip(uses, ids)
    if ident in denied]}))
sys.exit(1 if act == "exit" else 0)
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


def _check(setup, act: str) -> str | None:
    main, home, fake, tmp_path = setup
    (tmp_path / "act").write_text(act)
    env = {**os.environ, "FAKE_ROOT": str(main)}
    return peer_agent.review_self_check([str(fake)], env, str(main), SESSION)


def _leftovers(setup) -> list[str]:
    main, home, _, tmp_path = setup
    scratch = Path(tempfile.gettempdir())
    found = [*scratch.glob("review-selfcheck-*"), *scratch.glob("claude-*"),
             *home.glob(".review-selfcheck-*"), *main.glob(".review-selfcheck-*"),
             *home.glob(".claude/projects/*")]
    return sorted(str(path) for path in found)


def test_a_session_that_tries_and_is_denied_every_act_passes(setup):
    assert _check(setup, "refuse") is None
    assert _leftovers(setup) == []
    main, _, _, tmp_path = setup
    (call,) = (tmp_path / "calls").read_text().splitlines()
    assert f"--session-id {SESSION}" in call


@pytest.mark.parametrize(("act", "named"), [
    ("idle", "exec (never tried)"),
    ("undenied", "exec (not denied)"),
    ("exec", "exec"), ("write", "write"), ("memory", "memory"), ("scratchpad", "scratchpad"),
    ("leak", "home (its content reached the session)"),
    ("edit", "copy (main's worktree was edited)"),
    ("agent", "subagent (the reviewer has no Agent tool)"),
    ("worktree", "worktree (a subagent's worktree was made)"),
    ("garbage", "left no transcript to read"),
    ("exit", "exited 1"),
    # One compound command names both Bash acts' targets, and is neither act.
    ("compound", "exec (never tried), cat (never tried)"),
    *[(f"skip:{n}", f"{name} (never tried)") for n, name in enumerate(
        ("exec", "write", "memory", "read", "cat", "edit", "scratchpad"))],
])
def test_a_session_that_is_wider_or_never_tried_is_caught(setup, act, named):
    problem = _check(setup, act)
    assert problem is not None and named in problem and "no review dispatched" in problem
    assert _leftovers(setup) == []


def test_a_subagent_worktree_made_and_removed_where_others_were_still_shows(setup):
    main, _, _, _ = setup
    where = main / ".claude" / "worktrees"
    where.mkdir(parents=True)
    os.utime(where, ns=(10**18, 10**18))
    assert "worktree (a subagent's worktree was made)" in _check(setup, "worktree")


def test_a_self_check_that_cannot_finish_is_no_pass(setup):
    main, home, fake, tmp_path = setup
    fake.write_text(f"#!{sys.executable}\nimport sys; sys.exit(3)\n")
    assert "no transcript" in _check(setup, "refuse")
    assert _leftovers(setup) == []


def test_a_self_check_past_its_time_kills_the_whole_group(setup):
    """Its child and the child's own children die together."""
    main, home, fake, tmp_path = setup
    pid_file = tmp_path / "grandchild"
    fake.write_text(f"#!{sys.executable}\nimport subprocess, time\n"
                    f"p = subprocess.Popen(['sleep', '60'])\n"
                    f"open({str(pid_file)!r}, 'w').write(str(p.pid))\ntime.sleep(60)\n")
    started = time.monotonic()
    problem = peer_agent.review_self_check([str(fake)], dict(os.environ), str(main), SESSION,
                                           timeout=3)
    assert "passed 3s" in problem
    # A grandchild left alive would hold the output open until its own end.
    assert time.monotonic() - started < 30
    grandchild = int(pid_file.read_text())
    for _ in range(50):
        if subprocess.run(["kill", "-0", str(grandchild)], capture_output=True).returncode:
            break
        subprocess.run(["sleep", "0.1"])
    else:
        pytest.fail("the grandchild outlived the timeout")
    assert _leftovers(setup) == []


@pytest.mark.parametrize(("act", "said"), [
    ("exec", "self-check found acts review mode must refuse that ran or went untried: exec"),
    # The server's managed copy, refetched while the self-check ran.
    ("managed", "managed settings changed since they were checked; no review dispatched"),
])
def test_a_failed_self_check_stops_the_dispatch_before_the_review(setup, act, said):
    """The whole dispatch: the self-check runs in the reviewer's session, and when an
    act ran, or the managed settings changed, no review starts in it."""
    main, home, fake, tmp_path = setup
    (tmp_path / "act").write_text(act)
    review = tmp_path / "review"
    review.mkdir()
    out = tmp_path / "out.md"
    done = subprocess.run(
        [sys.executable, str(_SCRIPTS / "peer_agent.py"), "claude", "--review", str(review),
         "--cwd", str(main), "--prompt", "Review PR 1.", "--out", str(out)],
        capture_output=True, text=True, env={**os.environ, "CLAUDE_BIN": str(fake)},
        timeout=120)
    assert done.returncode == 2
    assert said in out.read_text()
    (call,) = (tmp_path / "calls").read_text().splitlines()
    assert "--session-id" in call and "--resume" not in call and "--safe-mode" in call
    assert "--output-format stream-json" in call
    assert _leftovers(setup) == []
