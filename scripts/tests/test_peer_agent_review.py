"""A PR reviewer is never a full agent: `peer_agent.py claude --review DIR` denies
every call its rules do not allow, writes only in DIR, loads no MCP server, and
sees no credential of the dispatcher's. A live probe of the same flags
(ship-code-review.md, "Reviewer mode") refused env, printenv, curl, gh POST,
python3 -c and a write outside DIR."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import peer_agent  # noqa: E402

MAIN, DIR = "/w/main", "/w/review"


def _cmd(*extra: str) -> list[str]:
    args = peer_agent.build_arg_parser().parse_args(
        ["claude", "--cwd", MAIN, "--prompt", "x", *extra])
    return peer_agent.build_claude_cmd(args)


def _values(cmd: list[str], flag: str) -> list[str]:
    """The values after `flag`, up to the next flag."""
    start = cmd.index(flag) + 1
    end = next((i for i in range(start, len(cmd)) if cmd[i].startswith("--")), len(cmd))
    return cmd[start:end]


def test_a_reviewer_denies_what_no_rule_allows_and_skips_no_permission():
    cmd = _cmd("--review", DIR)
    assert "--dangerously-skip-permissions" not in cmd
    assert _values(cmd, "--permission-mode") == ["dontAsk"]
    assert _values(cmd, "--permission-prompts") == ["none"]
    assert "--strict-mcp-config" in cmd and _values(cmd, "--add-dir") == [DIR]
    settings = json.loads(_values(cmd, "--settings")[0])["permissions"]
    assert settings == {"blockReadsOutsideWorkingDirectories": True,
                        "blockWritesOutsideWorkingDirectories": True}
    assert "WebFetch" not in _values(cmd, "--tools")[0].split(",")


def test_a_reviewer_may_read_git_run_mains_poster_and_write_only_its_review():
    allow = _values(_cmd("--review", DIR), "--allowedTools")
    assert f"Edit(/{DIR}/**)" in allow
    assert f"Bash(python3 -I {MAIN}/scripts/review_post.py *)" in allow
    assert not any(rule.startswith(("Bash(gh", "Bash(python3 -c", "Bash(*"))
                   or rule in {"Bash", "Edit", "Write"} for rule in allow)


@pytest.mark.parametrize("denied", ["Bash(gh *)", "Bash(curl *)", "Bash(env*)",
                                    "Bash(printenv*)", "Bash(git push *)"])
def test_a_reviewer_is_refused_what_claude_code_could_run_unasked(denied):
    assert denied in _values(_cmd("--review", DIR), "--disallowedTools")


def test_a_reviewer_sees_no_credential_of_the_dispatchers():
    env = peer_agent.review_env({"PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "http://p",
                                 "GH_TOKEN": "real", "GITHUB_TOKEN": "real",
                                 "AWS_SECRET_ACCESS_KEY": "real", "E2B_API_KEY": "real"})
    assert env == {"PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "http://p",
                   "GH_TOKEN": peer_agent.REVIEW_GH_TOKEN}
    assert "real" not in env.values()


def test_review_and_write_are_never_one_dispatch():
    with pytest.raises(SystemExit):
        peer_agent.build_arg_parser().parse_args(
            ["claude", "--prompt", "x", "--write", "--review", DIR])
