"""Provider sandbox seam (2026-07-03 live-test P0).

The founder-facing universe-intelligence turn must run the CLI subprocess
isolated to the universe's own dir with host tools denied. These guard the
``claude_provider`` flag/cwd builder that enforces it, and the ``ModelConfig``
fields that carry the policy.
"""
from __future__ import annotations

from pathlib import Path

from tinyassets.providers.base import ModelConfig
from tinyassets.providers.claude_provider import _sandbox_cli_args


def test_sandbox_emits_variadic_tool_flags(tmp_path):
    cfg = ModelConfig(
        sandbox_workspace=True,
        allowed_tools=("WebFetch",),
        disallowed_tools=("Bash", "Read", "Write"),
    )
    flags = _sandbox_cli_args(cfg, tmp_path)

    # cwd pinned to the universe's own dir (not the daemon checkout)
    # user-tier settings (MCP servers + bypassPermissions) are stripped so the
    # universe can't reach ambient MCP tools (e.g. mcp__codex → code exec)
    assert "--setting-sources" in flags
    assert flags[flags.index("--setting-sources") + 1] == ""
    # variadic flags: each tool is its OWN argv token (a joined string would be
    # read as one bogus tool name and silently match nothing)
    assert "--allowedTools" in flags
    assert "WebFetch" in flags
    assert flags[flags.index("--allowedTools") + 1] == "WebFetch"
    assert "--disallowedTools" in flags
    for denied in ("Bash", "Read", "Write"):
        assert denied in flags


def test_sandbox_without_universe_dir_fails_closed():
    # A sandboxed turn with no universe_dir would inherit the daemon's cwd —
    # the exact leak this fixes. It must FAIL CLOSED, not run un-isolated.
    import pytest

    from tinyassets.exceptions import ProviderError

    with pytest.raises(ProviderError):
        _sandbox_cli_args(ModelConfig(sandbox_workspace=True), None)


def test_codex_refuses_a_sandboxed_founder_turn():
    # Codex cannot enforce the universe sandbox, so a founder-facing (sandboxed)
    # turn routed to Codex must fail closed rather than run unconfined.
    import asyncio

    import pytest

    from tinyassets.exceptions import ProviderError
    from tinyassets.providers.codex_provider import CodexProvider

    cfg = ModelConfig(sandbox_workspace=True)
    with pytest.raises(ProviderError):
        asyncio.run(
            CodexProvider().complete("hi", "", cfg, universe_dir=Path("C:/u"))
        )


def test_disallow_only_still_emits_deny_floor(tmp_path):
    # The deny floor is emitted even without an allowlist.
    cfg = ModelConfig(sandbox_workspace=True, disallowed_tools=("Bash",))
    flags = _sandbox_cli_args(cfg, tmp_path)
    assert "--disallowedTools" in flags
    assert "Bash" in flags
    assert "--allowedTools" not in flags


def test_codex_fast_exit_excerpt_is_redacted_and_capped():
    from tinyassets.providers.codex_provider import _redacted_stderr_excerpt

    raw = "warning: x\nerror: auth failed token=sk-abcdefghijklmnop Bearer eyJhbGciOi.abcdefghijk\n"
    out = _redacted_stderr_excerpt(raw)
    assert "sk-abc" not in out and "eyJ" not in out and "[redacted]" in out
    # No generic long-token rule: hashes / paths / model ids stay readable.
    keep = "stream error for model gpt-5.5 at /opt/codex-install/node_modules/.bin/codex sha256:"
    keep += "a" * 64
    assert _redacted_stderr_excerpt(keep) == keep
    # Long lines keep head AND tail (codex appends its auth error code last).
    long = "x" * 400 + " auth error code: E_FOO"
    ex = _redacted_stderr_excerpt(long)
    assert len(ex) <= 240 and ex.endswith("E_FOO") and " ... " in ex
    assert _redacted_stderr_excerpt("") == "(no stderr)"


def test_bwrap_proc_mount_and_lock_signatures_are_sandbox_failures(monkeypatch):
    import sys

    import pytest

    from tinyassets.providers.base import SandboxUnavailableError, check_bwrap_failure

    monkeypatch.setattr(sys, "platform", "linux")
    for text in (
        "bwrap: Can't mount proc on /newroot/proc: Operation not permitted",
        "flock: cannot open lock file /codex-home/.lock: Read-only file system",
    ):
        with pytest.raises(SandboxUnavailableError):
            check_bwrap_failure(text)
    # an unrelated lock error is NOT a sandbox failure
    check_bwrap_failure("flock: cannot open lock file /data/.codex/.lock: Permission denied")


def test_workflow_node_call_is_pinned_to_its_universe_with_host_tools_denied(tmp_path):
    # A workflow node call (2026-09-24 latency root cause): cwd pinned to the
    # universe, project-only settings, shell/filesystem builtins denied, and
    # the node's own denies kept. Web tools are not the host's and stay.
    from tinyassets.providers.base import ACCOUNT_REACH_TOOLS, HOST_REACH_TOOLS

    # ReportFindings is deliberately in NEITHER constant (it reports into the
    # turn, not out of it), so it pins "the node's own denies are kept, first"
    # without colliding with the dedupe that the next test covers.
    cfg = ModelConfig(workflow_node=True, disallowed_tools=("ReportFindings",))
    flags = _sandbox_cli_args(cfg, tmp_path)
    assert flags[flags.index("--setting-sources") + 1] == ""
    denied = flags[flags.index("--disallowedTools") + 1:]
    assert denied == ["ReportFindings", *HOST_REACH_TOOLS, *ACCOUNT_REACH_TOOLS]
    assert "--allowedTools" not in flags
    assert "WebSearch" not in denied
    assert "WebFetch" not in denied


def test_workflow_node_call_without_a_universe_fails_closed():
    import pytest

    from tinyassets.exceptions import ProviderError

    with pytest.raises(ProviderError):
        _sandbox_cli_args(ModelConfig(workflow_node=True), None)


def test_confined_turns_state_the_permission_mode_explicitly(tmp_path):
    """An unspecified mode is upstream's to change (CLI 2.1.285, Codex ADAPT).

    2.1.285 starts ``claude -p`` in AUTO mode when no mode is configured on
    third-party providers or with telemetry off. A confined turn must therefore
    SAY which mode it runs in rather than inherit one that could begin
    auto-approving tools it never pre-approved.
    """
    configs = (
        ModelConfig(sandbox_workspace=True, allowed_tools=("WebFetch",)),
        ModelConfig(sandbox_workspace=True, disallowed_tools=("Bash",)),
        ModelConfig(workflow_node=True),
    )
    for cfg in configs:
        flags = _sandbox_cli_args(cfg, tmp_path)
        assert "--permission-mode" in flags, flags
        assert flags[flags.index("--permission-mode") + 1] == "default"
        # Before the variadic tool flags: --allowedTools/--disallowedTools take
        # every following token, so a flag after them would be read as a tool
        # name instead of a flag.
        for variadic in ("--allowedTools", "--disallowedTools"):
            if variadic in flags:
                assert flags.index("--permission-mode") < flags.index(variadic)


def test_host_trusted_roles_keep_their_permission_mode(tmp_path):
    # The explicit mode is scoped to confined turns; a plain config stays a no-op.
    flags = _sandbox_cli_args(ModelConfig(), tmp_path)
    assert flags == []


def test_claude_ai_account_tools_are_denied_to_the_engine():
    """Artifact and friends reach the DAEMON HOST's claude.ai account.

    The OS jail bounds the filesystem and ``--strict-mcp-config`` bounds MCP
    servers; neither contains a tool that publishes an artifact, enumerates
    other live sessions or sends feedback off-box. Re-checked against the CLI
    changelog for 2.1.184-2.1.288 (Codex ADAPT 2026-10-03).
    """
    from tinyassets.universe_intelligence import (
        _ENGINE_DISALLOWED_TOOLS,
        _ENGINE_DISALLOWED_TOOLS_WITH_MCP,
    )

    account_reach = ("Artifact", "ListAgents", "SendFeedback", "ListPlugins",
                     "EndConversation")
    for tool in account_reach:
        assert tool in _ENGINE_DISALLOWED_TOOLS, tool
        # Denied on the engine-MCP turn too: that turn only drops the ``mcp__*``
        # wildcard and ``ToolSearch``, never a builtin.
        assert tool in _ENGINE_DISALLOWED_TOOLS_WITH_MCP, tool


def test_engine_mcp_turn_drops_only_the_wildcard_and_toolsearch():
    """The relaxation stays exactly two names wide.

    ``mcp__*`` would deny the tinyassets handles and ``ToolSearch`` is how the
    CLI loads their schemas, so both must go -- and nothing else may, or a
    builtin silently becomes callable on the founder's turn.
    """
    from tinyassets.universe_intelligence import (
        _ENGINE_DISALLOWED_TOOLS,
        _ENGINE_DISALLOWED_TOOLS_WITH_MCP,
    )

    dropped = set(_ENGINE_DISALLOWED_TOOLS) - set(_ENGINE_DISALLOWED_TOOLS_WITH_MCP)
    assert dropped == {"mcp__*", "ToolSearch"}


def test_account_reach_tools_are_denied_on_both_confined_paths(tmp_path):
    """One constant, both call paths -- the gap found reviewing CLI 2.1.288.

    These act on the DAEMON HOST'S claude.ai account, so neither the OS jail
    (nothing touches disk, no shell starts) nor ``--strict-mcp-config`` (they
    are builtins, not MCP servers) bounds them. The engine turn denied them; a
    WORKFLOW NODE denied only ``HOST_REACH_TOOLS``, so a node could publish an
    artifact or message another session on the host's account.
    """
    from tinyassets.providers.base import ACCOUNT_REACH_TOOLS
    from tinyassets.universe_intelligence import (
        _ENGINE_DISALLOWED_TOOLS,
        _ENGINE_DISALLOWED_TOOLS_WITH_MCP,
    )

    assert ACCOUNT_REACH_TOOLS, "the constant must not be empty"

    node_flags = _sandbox_cli_args(ModelConfig(workflow_node=True), tmp_path)
    node_denied = node_flags[node_flags.index("--disallowedTools") + 1:]
    for tool in ACCOUNT_REACH_TOOLS:
        assert tool in node_denied, f"{tool} callable on a workflow node"
        assert tool in _ENGINE_DISALLOWED_TOOLS, f"{tool} callable on an engine turn"
        # Also denied when engine MCP is on: that turn drops only the ``mcp__*``
        # wildcard and ``ToolSearch``, never a builtin.
        assert tool in _ENGINE_DISALLOWED_TOOLS_WITH_MCP, f"{tool} callable with MCP on"


def test_the_two_reach_constants_stay_separate_and_disjoint():
    """Host reach and account reach are different boundaries, not one list.

    ``HOST_REACH_TOOLS`` is bounded by the OS jail and is also a latency
    control on nodes; ``ACCOUNT_REACH_TOOLS`` is bounded by neither the jail
    nor strict MCP. Merging them would lose the reason either exists.
    """
    from tinyassets.providers.base import ACCOUNT_REACH_TOOLS, HOST_REACH_TOOLS

    assert not set(HOST_REACH_TOOLS) & set(ACCOUNT_REACH_TOOLS)
    assert len(set(ACCOUNT_REACH_TOOLS)) == len(ACCOUNT_REACH_TOOLS), "no duplicates"
    # SendMessage lives in the account constant, not as a second literal in the
    # engine list: one definition is the point.
    assert "SendMessage" in ACCOUNT_REACH_TOOLS


def test_the_engine_denylist_has_no_duplicate_names(tmp_path):
    """Splatting a shared constant must not leave a name listed twice."""
    from tinyassets.universe_intelligence import _ENGINE_DISALLOWED_TOOLS

    duplicated = sorted({
        t for t in _ENGINE_DISALLOWED_TOOLS
        if _ENGINE_DISALLOWED_TOOLS.count(t) > 1
    })
    assert duplicated == [], duplicated

    node_flags = _sandbox_cli_args(
        ModelConfig(workflow_node=True, disallowed_tools=("Artifact",)), tmp_path,
    )
    node_denied = node_flags[node_flags.index("--disallowedTools") + 1:]
    # A node that already denied one of them by name keeps exactly one copy.
    assert node_denied.count("Artifact") == 1


def test_scheduling_push_and_remote_leave_the_platform_so_they_are_denied():
    """Host decision 2026-10-03, recorded so the reason outlives the list.

    Scheduling, push and remote runs belong to the user's own platform-side
    automations -- the channels they build -- never to the CLI's account-side
    features. A turn that scheduled its own wakeup or fired its own push would
    run work the owner never authored and cannot see in their automations.
    """
    from tinyassets.providers.base import ACCOUNT_REACH_TOOLS

    for tool in ("ScheduleWakeup", "PushNotification", "RemoteTrigger",
                 "CronCreate", "CronDelete", "CronList",
                 "DesignSync", "DesignSyncTool"):
        assert tool in ACCOUNT_REACH_TOOLS, tool


def test_session_local_and_strict_mcp_bounded_tools_stay_allowed_on_a_node(tmp_path):
    """The same decision's other half: do NOT sweep in what is already bounded.

    ``Task*`` is the turn's own bookkeeping and ``ReportFindings`` reports into
    the turn; the MCP resource readers are already bounded by
    ``--strict-mcp-config``. A node keeps them, as it keeps web tools, subagents
    and plans -- narrowing a node is about effects that ESCAPE it.
    """
    from tinyassets.providers.base import ACCOUNT_REACH_TOOLS

    node_flags = _sandbox_cli_args(ModelConfig(workflow_node=True), tmp_path)
    node_denied = node_flags[node_flags.index("--disallowedTools") + 1:]
    still_allowed = (
        "TaskCreate", "TaskUpdate", "TaskGet", "TaskList", "TaskStop", "TaskOutput",
        "ReportFindings",
        "ReadMcpResourceTool", "ReadMcpResourceDirTool", "ListMcpResourcesTool",
        # Owner-level capability a node has always kept.
        "WebFetch", "WebSearch", "Task", "Agent", "Skill",
    )
    for tool in still_allowed:
        assert tool not in ACCOUNT_REACH_TOOLS, f"{tool} should not be account-reach"
        assert tool not in node_denied, f"{tool} should stay callable on a node"


def test_the_engine_turn_denies_everything_it_denied_before_the_refactor():
    """Moving names from literals into the shared constant must LOSE nothing.

    The engine denylist is the stricter of the two paths; this refactor pulled
    SendMessage, ScheduleWakeup, PushNotification, RemoteTrigger, Cron* and
    DesignSync* out of its literals and into ACCOUNT_REACH_TOOLS. Each must
    still be denied, or the refactor quietly widened the founder's turn.
    """
    from tinyassets.universe_intelligence import _ENGINE_DISALLOWED_TOOLS

    moved_out_of_literals = (
        "SendMessage", "ScheduleWakeup", "PushNotification", "RemoteTrigger",
        "CronCreate", "CronDelete", "CronList", "DesignSync", "DesignSyncTool",
    )
    for tool in moved_out_of_literals:
        assert tool in _ENGINE_DISALLOWED_TOOLS, tool


def test_the_universe_the_agent_writes_is_no_setting_source(tmp_path):
    """The served turn runs in the universe dir, which the agent writes. With
    ``--setting-sources project`` Claude CLI 2.1.291 sent a ``CLAUDE.md``,
    ``.claude/CLAUDE.md`` and ``.claude/rules/*.md`` from it to the model and
    ran a ``.claude/settings.json`` hook (credential-free capture 2026-10-06);
    no setting source at all loads none of them."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / "CLAUDE.md").write_text("planted", encoding="utf-8")
    (tmp_path / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    for config in (ModelConfig(sandbox_workspace=True), ModelConfig(workflow_node=True)):
        flags = _sandbox_cli_args(config, tmp_path)
        sources = [value for flag, value in zip(flags, flags[1:])
                   if flag == "--setting-sources"]
        assert sources == [""]
        assert str(tmp_path) not in flags
