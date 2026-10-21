"""The owner stops their own live conversation turn (founder, 2026-09-30).

"there is no way to interrupt a run and push your messages. it should work just
like it works in claude code where i can press escape to interrupt and send all
pending messages".

The served-chat cases drive the REAL writer, router, HTTP adapter, journal and
engine client (``tests/test_interactive_http_agent.py``'s rig: only the remote
wires are synthetic). A native CLI turn runs in its owner's cell, whose
revocation the cell tests prove (``tests/test_role_provider_execution.py``).
"""
# ruff: noqa: F811 -- imported pytest fixtures

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


from tests.test_interactive_http_agent import agent, reader, rig, run, served  # noqa: F401
from tinyassets.providers.base import ModelConfig
from tinyassets.storage.agent_turn_boot import BOOT


def _uid(agent):
    return agent.served.context.universe_dir.name


def _reservations(agent):
    with agent.journal._ledger.connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'served_provider_budget_reservations'"
        ).fetchone():
            return []  # nothing was ever reserved
        return [row[0] for row in conn.execute(
            "SELECT state FROM served_provider_budget_reservations ORDER BY rowid"
        )]


def _not_thinking(agent, turn):
    """No surface may paint this turn as work any more, and no seat is held."""
    observed = agent.journal.universe_working_turn(
        _uid(agent), now=datetime.now(timezone.utc), max_age_s=3600,
    )
    assert observed is None, f"a stopped turn still reads as thinking: {observed}"
    assert (_uid(agent), turn.turn_id) not in BOOT._claimed, "the boot still runs it"
    assert "reserved" not in _reservations(agent), "a reservation was left held"


# ---------------------------------------------------------------------------
# Served chat on an HTTP model: the real rig.
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# A native CLI turn: the real Claude adapter against a real process tree.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The unplanned served call (no journal) and everything that is not a chat.
# ---------------------------------------------------------------------------


def _slow_provider(kind: str, seconds: float):
    """A provider of one execution kind, dispatched by the REAL router."""
    from tinyassets.providers.base import BaseProvider, ProviderResponse

    state = SimpleNamespace(cancelled=False, finished=False)

    class Slow(BaseProvider):
        name = "http-source"
        family = "anthropic"
        agent_execution_kind = kind

        async def complete(self, prompt, system, config, *, universe_dir=None):
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                state.cancelled = True
                raise
            state.finished = True
            return ProviderResponse(text="done", provider=self.name, model="m",
                                    family="anthropic", latency_ms=1.0,
                                    input_tokens=1, output_tokens=1, cost_microunits=5)

    return Slow(), state


def _call_sync(provider, operation):
    """``router.call_sync`` bound to one owner, as the unplanned served path is."""
    from unittest.mock import patch

    from tests.support.model_call import call_sync as model_call_sync
    from tests.support.owner_bound import owner_carrier
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.router import ProviderRouter

    router = ProviderRouter(providers={provider.name: provider})
    carrier = owner_carrier(provider.name, operation=operation)
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as root:
        universe = Path(root) / "u-owner-bound"
        universe.mkdir()
        context = UniverseContext(universe_dir=universe, provider_invocation=carrier)
        with patch("tinyassets.providers.router._provider_invocation_carrier",
                   return_value=carrier), patch(
            "tinyassets.providers.served_model_plan.connected_sources",
            return_value=frozenset({provider.name}),
        ):
            return model_call_sync(router, "writer", "p", "s",
                                   ModelConfig(absolute_cap_s=60.0),
                                   operation=operation, universe_context=context)










# ---------------------------------------------------------------------------
# What the owner sees, and the route that asks.
# ---------------------------------------------------------------------------






class _Request:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = {"content-type": "application/json", "origin": "https://tinyassets.io",
                        "host": "tinyassets.io", "content-length": str(len(self._body))}

    async def stream(self):
        yield self._body












