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
    tools = _values(cmd, "--tools")[0].split(",")
    # No Agent: a subagent can run in a git worktree Claude Code makes in the main
    # checkout, and it is one more session to keep narrow.
    assert "WebFetch" not in tools and "Agent" not in tools and "Task" not in tools
    assert "Agent" not in _values(cmd, "--allowedTools")


def test_a_reviewer_loads_no_other_source_of_rules_tools_hooks_or_memory():
    """No user, project or local settings file, whatever trust a host accepted
    (`--restricted`); no skill, whose `allowed-tools` would widen it; no MCP server;
    no hook; and no auto memory, one directory every worktree's sessions load."""
    cmd = _cmd("--review", DIR)
    assert {"--safe-mode", "--restricted", "--disable-slash-commands",
            "--strict-mcp-config"} <= set(cmd)
    assert "--setting-sources" not in cmd and "--plugin-dir" not in cmd
    settings = json.loads(_values(cmd, "--settings")[0])
    assert {key: settings[key] for key in (
        "autoMemoryEnabled", "disableAllHooks", "disableClaudeAiConnectors", "enableArtifact",
        "syncClaudeAiPlugins")} == {
        "autoMemoryEnabled": False, "disableAllHooks": True, "disableClaudeAiConnectors": True,
        "enableArtifact": False, "syncClaudeAiPlugins": False}
    assert f"Edit(/{DIR}/.claude/**)" in _values(cmd, "--disallowedTools")
    assert peer_agent.review_env({})["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


@pytest.fixture
def managed(tmp_path, monkeypatch):
    """An /etc/claude-code and a server-managed cache of the test's own."""
    etc, home = tmp_path / "etc", tmp_path / "home"
    etc.mkdir()
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(peer_agent, "_MANAGED_DIR", etc)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(peer_agent.sys, "platform", "linux")
    return etc, home / ".claude" / "remote-settings.json"


@pytest.mark.parametrize(("content", "refused"), [
    ("{}", None),
    ('{"permissions": {"deny": ["Bash"], "ask": [], "disableBypassPermissionsMode": "disable"},'
     ' "disableAllHooks": true, "allowManagedPermissionRulesOnly": true,'
     ' "disableAutoMode": "disable", "enableArtifact": false}', None),
    # A narrowing key passes only with its narrowing value.
    ('{"disableAllHooks": false}', "disableAllHooks to False"),
    ('{"disableClaudeAiConnectors": false}', "disableClaudeAiConnectors to False"),
    ('{"allowManagedHooksOnly": false}', "allowManagedHooksOnly to False"),
    ('{"permissions": {"disableBypassPermissionsMode": "no"}}',
     "permissions.disableBypassPermissionsMode to 'no'"),
    # Sandbox sub-keys are no top-level keys, and permissions holds no disableAutoMode.
    ('{"allowManagedDomainsOnly": true}', "allowManagedDomainsOnly"),
    ('{"permissions": {"disableAutoMode": "disable"}}', "permissions.disableAutoMode"),
    ('{"permissions": {"allow": ["Bash(*)"]}}', "permissions.allow"),
    ('{"permissions": {"defaultMode": "acceptEdits"}}', "permissions.defaultMode"),
    ('{"policyHelper": {"path": "/usr/local/bin/policy"}}', "policyHelper"),
    ('{"sandbox": {"enabled": true, "autoAllowBashIfSandboxed": true}}', "sandbox"),
    ('{"env": {"CLAUDE_CODE_SYNC_PLUGINS": "1"}}', "env"),
    ('{"otelHeadersHelper": "/bin/x"}', "otelHeadersHelper"),
    ('{"aKeyNobodyListedYet": 1}', "aKeyNobodyListedYet"),
    ('{"autoMemoryEnabled": true}', "autoMemoryEnabled to True"),
    ('{"syncClaudeAiPlugins": true}', "syncClaudeAiPlugins to True"),
    ("not json", "cannot be read"),
])
@pytest.mark.parametrize("where", ["managed-settings.json", "managed-settings.d/10.json",
                                   "cache"])
def test_managed_settings_pass_only_what_narrows(managed, content, refused, where):
    """An allowlist, so a widening key nobody has listed refuses too."""
    etc, cache = managed
    path = cache if where == "cache" else etc / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    problem = peer_agent.managed_settings_problem()
    assert problem is None if refused is None else refused in problem


@pytest.mark.parametrize("extra", ["CLAUDE.md", "managed-mcp.json", ".claude/agents/fixer.md",
                                   "managed-settings.d/nested/x.json"])
def test_any_other_managed_source_refuses(managed, extra):
    etc, _ = managed
    (etc / extra).parent.mkdir(parents=True, exist_ok=True)
    (etc / extra).write_text("{}")
    assert "managed source a reviewer could load" in peer_agent.managed_settings_problem()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_a_platform_whose_managed_sources_are_not_read_refuses(managed, monkeypatch, platform):
    monkeypatch.setattr(peer_agent.sys, "platform", platform)
    assert "Linux only" in peer_agent.managed_settings_problem()


def test_the_managed_snapshot_changes_with_any_source(managed):
    """The dispatch compares it before and after the self-check: the server's copy is
    fetched again in the background, so the review could apply one nobody read."""
    etc, cache = managed
    empty = peer_agent.managed_snapshot()
    cache.write_text("{}")
    first = peer_agent.managed_snapshot()
    assert first != empty
    cache.write_text('{"permissions": {"allow": ["Bash(*)"]}}')
    assert peer_agent.managed_snapshot() != first
    (etc / "managed-settings.json").write_text("{}")
    assert len(peer_agent.managed_snapshot()) == 2


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
