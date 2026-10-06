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
