"""Provider sandbox seam (2026-07-03 live-test P0).

The founder-facing universe-intelligence turn must run the CLI subprocess
isolated to the universe's own dir with host tools denied. These guard the
``claude_provider`` flag/cwd builder that enforces it, and the ``ModelConfig``
fields that carry the policy.
"""
from __future__ import annotations


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


