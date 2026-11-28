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
    settings = json.loads(_values(cmd, "--settings")[0])
    assert settings["permissions"] == {"blockReadsOutsideWorkingDirectories": True,
                                       "blockWritesOutsideWorkingDirectories": True}
    assert "WebFetch" not in _values(cmd, "--tools")[0].split(",")


def test_a_reviewer_loads_no_other_source_of_rules_tools_hooks_or_memory():
    """No user, project or local settings file, whatever trust a host accepted
    (`--restricted`); no skill, whose `allowed-tools` would widen it; no MCP server;
    no hook; and no auto memory, one directory every worktree's sessions load."""
    cmd = _cmd("--review", DIR)
    assert {"--restricted", "--disable-slash-commands", "--strict-mcp-config"} <= set(cmd)
    assert "--setting-sources" not in cmd and "--plugin-dir" not in cmd
    settings = json.loads(_values(cmd, "--settings")[0])
    assert (settings["autoMemoryEnabled"], settings["disableAllHooks"],
            settings["disableClaudeAiConnectors"]) == (False, True, True)
    assert f"Edit(/{DIR}/.claude/**)" in _values(cmd, "--disallowedTools")
    assert peer_agent.review_env({})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


@pytest.mark.parametrize(("content", "widens"), [
    ("{}", None), ('{"permissions": {"deny": ["Bash"]}}', None),
    ('{"permissions": {"allow": ["Bash(*)"]}}', "permissions.allow"),
    ('{"permissions": {"defaultMode": "bypassPermissions"}}', "permissions.defaultMode"),
    ('{"hooks": {"PreToolUse": []}, "enabledPlugins": {"x": true}}', "hooks, enabledPlugins"),
    ("not json", "cannot be read"),
])
def test_managed_settings_that_would_widen_a_reviewer_refuse_the_dispatch(
        tmp_path, monkeypatch, content, widens):
    """Managed settings apply even under --restricted, so they are read, not trusted."""
    (tmp_path / "managed.d").mkdir()
    (tmp_path / "managed.d" / "10.json").write_text(content)
    monkeypatch.setattr(peer_agent, "_MANAGED_SETTINGS",
                        (str(tmp_path / "absent.json"), str(tmp_path / "managed.d")))
    problem = peer_agent.managed_settings_problem()
    assert problem is None if widens is None else widens in problem


def test_a_reviewers_directory_is_new_and_empty(tmp_path):
    assert peer_agent.review_dir_problem(str(tmp_path)) is None
    (tmp_path / ".claude").mkdir()
    assert "empty" in peer_agent.review_dir_problem(str(tmp_path))
    assert "not a directory" in peer_agent.review_dir_problem(str(tmp_path / "absent"))
    link = tmp_path.parent / f"{tmp_path.name}-link"
    empty = tmp_path.parent / f"{tmp_path.name}-empty"
    empty.mkdir()
    link.symlink_to(empty)
    assert "not a directory" in peer_agent.review_dir_problem(str(link))


def test_a_reviewers_one_command_is_mains_poster_and_it_writes_only_its_review():
    """git's own flags (--no-index, --output, -c) reach past Claude Code's checks of
    its file tools, so git runs only inside the poster, with its arguments fixed."""
    allow = _values(_cmd("--review", DIR), "--allowedTools")
    assert f"Edit(/{DIR}/**)" in allow
    assert [rule for rule in allow if rule.startswith("Bash")] == [
        f"Bash(python3 -I {MAIN}/scripts/review_post.py *)"]
    assert not any(rule in {"Edit", "Write"} for rule in allow)


@pytest.mark.parametrize("denied", ["Bash(git *)", "Bash(gh *)", "Bash(curl *)",
                                    "Bash(env*)", "Bash(printenv*)"])
def test_a_reviewer_is_refused_what_claude_code_could_run_unasked(denied):
    assert denied in _values(_cmd("--review", DIR), "--disallowedTools")


def test_a_reviewer_sees_no_credential_of_the_dispatchers():
    env = peer_agent.review_env({"PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "http://p",
                                 "GH_TOKEN": "real", "GITHUB_TOKEN": "real",
                                 "AWS_SECRET_ACCESS_KEY": "real", "E2B_API_KEY": "real"})
    assert env == {"PATH": "/bin", "HOME": "/h", "HTTPS_PROXY": "http://p",
                   "GH_TOKEN": peer_agent.REVIEW_GH_TOKEN,
                   "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    assert "real" not in env.values()


def test_review_and_write_are_never_one_dispatch():
    with pytest.raises(SystemExit):
        peer_agent.build_arg_parser().parse_args(
            ["claude", "--prompt", "x", "--write", "--review", DIR])
