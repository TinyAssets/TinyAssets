"""Two owned API sources through real consent, resolver, router, chat and runs."""

import json
from types import SimpleNamespace

import pytest

from tests import test_free_account_run_provider_parity as parity
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver

wires = parity.wires
pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def pool(tmp_path, monkeypatch, authenticate_request, wires):
    from tests.owner_answer import answer_request
    from tests.support.connection_door import post
    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.onboarding.source_connect import _offer_pool_access
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.providers.free_sources import source_preset

    authenticate_request(parity.A_OWNER)
    first = parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.A_OWNER,
                                  universe=parity.A_HOME, suffix="a")
    from tinyassets.api.pending_requests import list_requests

    setup = next(r for r in list_requests(universe_id=parity.A_HOME)["pending"]
                 if r["request_id"] == "sys_connect_model")
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
    preset = source_preset("groq")
    deposit = post("deposit_key", {"center": parity.A_HOME, "preset_id": preset["id"],
                                   "key": "alice-own-second-key"})
    assert deposit["status"] == "provisioned", deposit
    applied = apply_connection_uses(
        base=tmp_path, uid=parity.A_HOME, actor=parity.A_OWNER, grant_id=deposit["grant_id"],
        uses={"model": {"wire": preset["wire"], "billing": "free", "models": [
            {"id": model, "tools": True, "context": 32768} for model in second_wire.models]}},
        constant_headers={}, owner_confirmed=True)
    assert applied.get("provider"), applied
    result = _offer_pool_access(base=tmp_path, uid=parity.A_HOME, owner=parity.A_OWNER,
                                preset=preset, definition_id=applied["definition_id"],
                                models=list(second_wire.models))
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
    from tinyassets.providers.call import _router

    assert pool.first_wire.sent_models == [parity.LIVE_MODELS[0]]
    assert pool.second_wire.sent_models == [pool.second_wire.models[0]]
    assert _router._quota.cooldown_remaining(pool.first, owner=parity.A_OWNER) > 86000
    assert _router._quota.daily_detail(pool.first, owner=parity.A_OWNER)
    assert _router._quota.available(pool.second, owner=parity.A_OWNER)
    assert pool.foreign.requests == [] and pool.foreign.reads == []
