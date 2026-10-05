"""A source's DAILY cap ends that source for the turn, not the turn itself.

LIVE EVIDENCE (prod, 2026-09-30 06:41Z and 16:36Z, free account
``u-01ky3zh1arr8qth8jee7zx63pq``): OpenRouter's account-wide free allowance
(about fifty requests a day, shared by every ``:free`` model) ran out, and the
router then tried four or five OTHER ``:free`` models on the SAME OpenRouter
source, one after another. Every one was bound to fail, and each counted as a
request against tomorrow's allowance too.

#4137 (``providers/daily_quota.py``) landed at 17:54Z, after both turns: it
classifies an explicit daily refusal as ``provider_daily_quota`` with
``account`` scope, which the router cools for the whole source and the turn
coordinator excludes by capacity identity. ``test_daily_source_pooling.py``
drives it with a message-only 429. These tests drive the body OpenRouter
actually sends -- message, ``metadata.headers`` with the epoch-ms reset, and
the HTTP rate-limit headers -- through the real router, and pin both halves of
the contract: no sibling on the exhausted source, and a source from a DIFFERENT
provider still answers.

What "different" means is the capacity identity in ``model_policy.order``,
unchanged here: a second connection to the SAME provider with no verified
account id is also excluded (``capacity_identity_unverified``), because two
keys do not prove two accounts and OpenRouter's cap is per account. That is
conservative on purpose; these tests do not claim otherwise.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests import test_daily_source_pooling as pooling
from tests import test_free_account_run_provider_parity as parity
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

wires = parity.wires
pool = pooling.pool
pytestmark = pooling.pytestmark

#: Tomorrow 00:00 UTC in epoch ms, as OpenRouter reports ``X-RateLimit-Reset``.
_RESET_MS = str(int(
    (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp() * 1000
))


def _openrouter_daily_429() -> dict:
    """OpenRouter's free-tier daily refusal, as documented and as seen live."""
    headers = {
        "X-RateLimit-Limit": "50",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": _RESET_MS,
    }
    return {
        "status": 429,
        "headers": dict(headers),
        "body": json.dumps({
            "error": {
                "message": (
                    "Rate limit exceeded: free-models-per-day. Add 10 credits to "
                    "unlock 1000 free model requests per day"
                ),
                "code": 429,
                "metadata": {"headers": headers, "provider_name": None},
            },
            "user_id": "user_synthetic",
        }),
    }


def _always_daily(wire):
    def request(verb, document):
        wire.requests.append((verb, document))
        return _openrouter_daily_429()

    wire.request = request


def test_single_source_daily_cap_sends_one_request_and_names_the_daily_limit(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """Two accepted :free models on one source; the first answers the daily cap.

    Before #4137 this was an unknown-scope 429, narrowed to the model, and the
    sibling was tried: the live 4-5 wasted requests. Now: one request, the
    source cooled until the reported reset, and the failure says DAILY.
    """
    from tinyassets.providers import call as call_module

    authenticate_request(parity.A_OWNER)
    provider = parity._seed_universe(
        tmp_path, monkeypatch, wires, owner=parity.A_OWNER, universe=parity.A_HOME,
        suffix="a",
    )
    _always_daily(wires[parity.A_OWNER])

    record = parity._run(tmp_path, monkeypatch, parity._branch(owner=parity.A_OWNER),
                         parity.A_HOME)

    assert record["status"] == "failed"
    assert wires[parity.A_OWNER].sent_models == [parity.LIVE_MODELS[0]], (
        "a sibling model was tried on a source whose whole account is spent"
    )
    assert "provider_daily_quota" in record["error"], record["error"]
    router = call_module.get_provider_router()
    assert router._quota.daily_detail(provider)
    # The cooldown runs to the reported reset (next 00:00 UTC), not a short
    # retry. Measured against the reset itself, not a fixed hour: `> 3600`
    # failed every run in the last hour of the UTC day (merge queue, 23:41Z).
    until_reset = int(_RESET_MS) / 1000 - datetime.now(UTC).timestamp()
    assert router._quota.cooldown_remaining(provider) >= until_reset - 120

    # The next run does not pay for the answer again: the source is skipped.
    again = parity._run(tmp_path, monkeypatch,
                        parity._branch(owner=parity.A_OWNER, branch_def_id="again"),
                        parity.A_HOME)
    assert again["status"] == "failed"
    assert wires[parity.A_OWNER].sent_models == [parity.LIVE_MODELS[0]]


def test_a_per_minute_429_on_the_same_source_still_tries_the_sibling(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The stop is for DAILY evidence only: a transient 429 keeps its sibling.

    Mutation guard for "too broad": if every 429 were read as daily, this
    account would lose the sibling model that answers in the live 09-30 case.
    """
    authenticate_request(parity.A_OWNER)
    parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.A_OWNER,
                          universe=parity.A_HOME, suffix="a")
    wires[parity.A_OWNER].rate_limited = frozenset({parity.LIVE_MODELS[0]})

    record = parity._run(tmp_path, monkeypatch, parity._branch(owner=parity.A_OWNER),
                         parity.A_HOME)

    assert record["status"] == "completed", record["error"]
    assert wires[parity.A_OWNER].sent_models == list(parity.LIVE_MODELS)


def test_a_different_source_still_answers_after_the_openrouter_daily_cap(
    pool, monkeypatch,
):
    """The stop must not be too broad: another provider's source still answers."""
    _always_daily(pool.first_wire)

    record = parity._run(pool.base, monkeypatch, parity._branch(owner=parity.A_OWNER),
                         parity.A_HOME)

    assert record["status"] == "completed", record["error"]
    assert record["output"]["note"] == "morning focus note"
    # Exactly one request to the exhausted source; its sibling never called.
    assert pool.first_wire.sent_models == [parity.LIVE_MODELS[0]]
    assert pool.second_wire.sent_models == [pool.second_wire.models[0]]
    from tinyassets.providers.call import _real_router

    assert _real_router._quota.available(pool.second)
    assert not _real_router._quota.available(pool.first)


def _converse(base, monkeypatch, message):
    """One chat turn through the real coordinator, router and HTTP provider."""
    from types import SimpleNamespace

    from mcp.types import ListToolsResult, Tool

    from tinyassets import engine_mcp_http, engine_tool_client, universe_intelligence
    from tinyassets.auth import middleware as auth
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

    engine_mcp_http._write_routes(base, [SimpleNamespace(
        universe_id=parity.A_HOME, owner=parity.A_OWNER, port=8790, secret="s" * 43,
    )])

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def is_connected(self):
            return True

        async def list_tools_mcp(self, *, cursor=None):
            return ListToolsResult(tools=[Tool(name=n, inputSchema={"type": "object"})
                                         for n in SERVED_ENGINE_MCP_TOOLS])

    monkeypatch.setattr(engine_tool_client, "_make_client", lambda *_: Client())
    reserve = auth.reserve_provider_request(principal_id=parity.A_OWNER, session_id="dc",
                                             request_id="dc", tool_name="converse")
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    try:
        return universe_intelligence.converse(parity.A_HOME, message)
    finally:
        auth.revoke_provider_request(capability)


def test_chat_daily_cap_on_the_only_source_is_one_request_and_a_daily_notice(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The live turns were CHAT turns: the coordinator must not walk siblings."""
    authenticate_request(parity.A_OWNER)
    parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.A_OWNER,
                          universe=parity.A_HOME, suffix="a")
    _always_daily(wires[parity.A_OWNER])

    from tinyassets.exceptions import AllProvidersExhaustedError

    with pytest.raises(AllProvidersExhaustedError) as raised:
        _converse(tmp_path, monkeypatch, "make me a morning note")

    assert wires[parity.A_OWNER].sent_models == [parity.LIVE_MODELS[0]], (
        wires[parity.A_OWNER].sent_models
    )
    # Typed daily, which is what the owner's notice is rendered from.
    classes = [a.failure_class for a in raised.value.attempts]
    assert "provider_daily_quota" in classes, classes


def test_chat_moves_to_a_different_source_after_the_openrouter_daily_cap(pool, monkeypatch):
    _always_daily(pool.first_wire)

    reply = _converse(pool.base, monkeypatch, "Write a morning focus note.")

    reset = datetime.fromtimestamp(int(_RESET_MS) / 1000, UTC).strftime("%Y-%m-%d %H:%M UTC")
    assert reply == (
        f"morning focus note\n\nAnswered by model-groq because {pool.first} "
        "is cooling down or out of capacity. "
        f"The original source can be retried after {reset}."
    )
    assert pool.first_wire.sent_models == [parity.LIVE_MODELS[0]]
    assert pool.second_wire.sent_models == [pool.second_wire.models[0]]
