"""Two owned API sources through real consent, resolver, router, chat and runs."""

import json
from types import SimpleNamespace

import pytest
from mcp.types import ListToolsResult, Tool

from tests import test_free_account_run_provider_parity as parity
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver

wires = parity.wires
pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def pool(tmp_path, monkeypatch, authenticate_request, wires):
    from tinyassets.api.pending_requests import answer_request
    from tinyassets.onboarding.source_connect import connect_source
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.providers.free_sources import source_preset

    authenticate_request(parity.A_OWNER)
    first = parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.A_OWNER,
                                  universe=parity.A_HOME, suffix="a")
    from tinyassets.api.pending_requests import list_requests

    setup = next(r for r in list_requests(universe_id=parity.A_HOME)["pending"]
                 if r["request_id"] == "sys_connect_llm")
    assert setup["status"] == "optional" and setup["suggestion"]
    # An unrelated healthy owner's credential must never be reached.
    authenticate_request(parity.B_OWNER)
    parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.B_OWNER,
                          universe=parity.B_HOME, suffix="b")
    wires[parity.B_OWNER].reads.clear()
    wires[parity.B_OWNER].requests.clear()
    authenticate_request(parity.A_OWNER)
    second_wire = parity._Wire(models=("openai/gpt-oss-120b",))
    monkeypatch.setattr("tinyassets.providers.discovery_http.read_granted_discovery_document",
                        second_wire.read)
    result = connect_source(base=tmp_path, uid=parity.A_HOME, owner=parity.A_OWNER,
                            preset=source_preset("groq"), key="alice-own-second-key")
    response = answer_request(universe_id=parity.A_HOME,
                              payload={"request_id": result["request_id"], "values": {}})
    assert response.get("status") == "answered", response
    second = next(p for p in result["request"]["action"]["proposed_membership"] if p != first)
    first_wire = wires[parity.A_OWNER]
    original = first_wire.request
    def daily(verb, doc):
        original(verb, doc)
        return {"status": 429, "body": json.dumps({"error": {
            "code": 429, "message": "Rate limit exceeded: free-models-per-day"}})}
    first_wire.request = daily
    def resolve(self, **kwargs):
        assert kwargs["owner_user_id"] == self._definition.owner_user_id
        if self.name == second:
            assert kwargs["universe_id"] == parity.A_HOME
            return second_wire
        return wires[self._definition.owner_user_id]
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(resolve))
    # Explicit source order makes the exhaustion test independent of ranking.
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    ModelPreferenceStore(tmp_path).save(
        parity.A_OWNER, parity.A_HOME, expected_generation=0,
        policy=ModelPreferences("explicit", ModelRef(first, parity.LIVE_MODELS[0]), (
            ModelRef(first, parity.LIVE_MODELS[1]), ModelRef(second, second_wire.models[0]),
        )), require_current_home=True,
    )
    return SimpleNamespace(base=tmp_path, first=first, second=second, first_wire=first_wire,
                           second_wire=second_wire, foreign=wires[parity.B_OWNER])


def _assert_pool(pool):
    from tinyassets.providers.call import _real_router

    assert pool.first_wire.sent_models == [parity.LIVE_MODELS[0]]
    assert pool.second_wire.sent_models == [pool.second_wire.models[0]]
    assert _real_router._quota.cooldown_remaining(pool.first) > 86000
    assert _real_router._quota.daily_detail(pool.first)
    assert _real_router._quota.available(pool.second)
    assert pool.foreign.requests == [] and pool.foreign.reads == []


def test_workflow_daily_quota_skips_sibling_and_uses_next_owned_source(pool, monkeypatch):
    record = parity._run(pool.base, monkeypatch, parity._branch(owner=parity.A_OWNER),
                         parity.A_HOME)
    assert record["status"] == "completed", record["error"]
    assert record["output"]["note"] == "morning focus note"
    _assert_pool(pool)
    # Another workflow honors the retained SOURCE cooldown, not merely a model mark.
    record = parity._run(pool.base, monkeypatch, parity._branch(owner=parity.A_OWNER),
                         parity.A_HOME)
    assert record["status"] == "completed", record["error"]
    assert pool.first_wire.sent_models == [parity.LIVE_MODELS[0]]


def test_chat_daily_quota_skips_sibling_and_uses_next_owned_source(pool, monkeypatch):
    from tinyassets import engine_mcp_http, engine_tool_client, universe_intelligence
    from tinyassets.auth import middleware as auth
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

    engine_mcp_http._write_routes(pool.base, [SimpleNamespace(
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
    reserve = auth.reserve_provider_request(principal_id=parity.A_OWNER, session_id="pool",
                                             request_id="pool", tool_name="converse")
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    try:
        result = universe_intelligence.converse(parity.A_HOME, "Write a morning focus note.")
        assert result == "morning focus note"
    finally:
        auth.revoke_provider_request(capability)
    _assert_pool(pool)


def test_single_source_suggestion_is_optional_and_disappears_for_a_pool(pool):
    from tinyassets.api.pending_requests import list_requests

    rows = list_requests(universe_id=parity.A_HOME)["pending"]
    setup = next(r for r in rows if r["request_id"] == "sys_connect_llm")
    assert setup["status"] == "optional" and "suggestion" not in setup
    assert len(setup["action"]["setup"]["sources"]) == 4
