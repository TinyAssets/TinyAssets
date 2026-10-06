# ruff: noqa: F811
"""Activity-style pre-round failure through the actual coordinator."""
import asyncio

import pytest

from tests.test_interactive_http_agent import agent, reader, rig, served  # noqa: F401
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.interactive_http_agent import ServedChatAgentAdapter
from tinyassets.providers.call import make_interactive_agent_turn


def test_pre_round_activity_refusal_settles_without_writer_cleanup(agent, monkeypatch):
    async def refused(*args, **kwargs):
        raise ProviderAuthorityHeldError(
            "activity runs need an engine-inference executor until native yield is fenced")

    monkeypatch.setattr(ServedChatAgentAdapter, "infer", refused)
    turn = make_interactive_agent_turn(
        prompt="background job", system="system",
        config=agent.config, universe_context=agent.served.context)
    with pytest.raises(ProviderAuthorityHeldError, match="native yield"):
        asyncio.run(turn.run())
    row = agent.latest()
    assert row.state == "abandoned" and not row.rounds


def test_all_skipped_retry_owns_a_fresh_root_and_can_dispatch(agent, monkeypatch):
    from tests.test_interactive_http_agent import run
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    original = ServedChatAgentAdapter.infer
    calls = []

    async def skip_once(self, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise AllProvidersExhaustedError("cooled", attempts=[ProviderAttemptDiagnostic(
                "source", "skipped", "quota_or_cooldown")])
        return await original(self, **kwargs)

    monkeypatch.setattr(ServedChatAgentAdapter, "infer", skip_once)
    assert run(agent)
    assert agent.latest().state == "completed"
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute(
            "SELECT state, round_ordinal FROM agent_turns ORDER BY rowid").fetchall()
    assert [tuple(row) for row in rows] == [("abandoned", 0), ("completed", 2)]


def test_cleanup_error_preserves_refusal_and_releases_runner_for_recovery(agent, monkeypatch):
    from datetime import datetime, timezone

    from tinyassets.agent_turn_reconcile import reconcile_orphaned_turns
    from tinyassets.storage.agent_turn_boot import BootTurns
    from tinyassets.storage.agent_turn_journal import AgentTurnJournal, JournalUnavailable

    refusal = ProviderAuthorityHeldError("native activity cannot yield safely")

    async def refused(*args, **kwargs):
        raise refusal

    def unavailable(*args, **kwargs):
        raise JournalUnavailable("abandon storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ServedChatAgentAdapter, "infer", refused)
        patch.setattr(AgentTurnJournal, "abandon", unavailable)
        turn = make_interactive_agent_turn(
            prompt="background job", system="system", config=agent.config,
            universe_context=agent.served.context)
        with pytest.raises(ProviderAuthorityHeldError) as raised:
            asyncio.run(turn.run())
        assert raised.value is refusal
    row = agent.latest()
    assert row.state == "ready" and not row.rounds
    root = agent.served.context.universe_dir
    assert agent.journal.universe_working_turn(
        root.name, now=datetime.now(timezone.utc), max_age_s=3600, boot=BootTurns()) is None
    result = reconcile_orphaned_turns(root.parent)
    assert [(r["turn_id"], r["settled"]) for r in result] == [(row.turn_id, "abandoned")]
