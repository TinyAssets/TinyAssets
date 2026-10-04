"""The signed-in Hugging Face source is the next hop when OpenRouter's free
daily requests run out, in chat and in workflows.

Same rig as ``test_daily_source_pooling`` (real consent, resolver, router, chat
and runs), with the second source built exactly the way a Hugging Face sign-in
builds it: an ``oauth2`` token bundle deposited for the card's one inference
endpoint, the card's DECLARED model list (no catalogue read), and the owner's
explicit pool confirmation.
"""

from types import SimpleNamespace

import pytest

from tests import test_daily_source_pooling as pooling
from tests import test_free_account_run_provider_parity as parity
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver

wires = parity.wires
pytestmark = pytest.mark.usefixtures("cloud_runtime")
RESOURCE = "https://tinyassets.io/mcp"


@pytest.fixture
def hf_pool(tmp_path, monkeypatch, authenticate_request, wires):
    import json

    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.api.http_connection import connect_http
    from tinyassets.api.pending_requests import answer_request
    from tinyassets.connection_oauth.tokens import TokenBundle, encode
    from tinyassets.onboarding.source_connect import _offer_pool_access, sign_in_action
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.providers.free_sources import sign_in_preset
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    authenticate_request(parity.A_OWNER)
    first = parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.A_OWNER,
                                  universe=parity.A_HOME, suffix="a")
    authenticate_request(parity.B_OWNER)
    parity._seed_universe(tmp_path, monkeypatch, wires, owner=parity.B_OWNER,
                          universe=parity.B_HOME, suffix="b")
    wires[parity.B_OWNER].reads.clear()
    wires[parity.B_OWNER].requests.clear()
    authenticate_request(parity.A_OWNER)

    from tests.test_huggingface_sign_in_source import enable_fake_source

    enable_fake_source(monkeypatch)  # nonbillable fake; installed source is withheld
    preset = sign_in_preset("huggingface")
    action = sign_in_action(preset, RESOURCE)
    bundle = TokenBundle(access_token="hf_oauth_alice", token_url="https://huggingface.co/oauth/token",
                         client_id=action["oauth"]["client_id"], refresh_token="hf_rt_alice",
                         expires_at=None, scope="openid profile inference-api")
    deposit = connect_http(universe_id=parity.A_HOME, allow_oauth2=True, payload={
        "destination": action["destination"], "secret": encode(bundle), "auth_scheme": "oauth2",
        "allowed_endpoints": action["endpoints"], "access": "exact"})
    assert deposit.get("status") == "provisioned", deposit
    applied = apply_connection_uses(base=tmp_path, uid=parity.A_HOME, actor=parity.A_OWNER,
                                    grant_id=deposit["grant_id"], uses=action["uses"],
                                    constant_headers={}, owner_confirmed=True)
    assert applied.get("provider"), applied
    offered = _offer_pool_access(base=tmp_path, uid=parity.A_HOME, owner=parity.A_OWNER,
                                 preset=preset, definition_id=applied["definition_id"],
                                 models=list(preset["models"]))
    response = answer_request(universe_id=parity.A_HOME,
                              payload={"request_id": offered["request_id"], "values": {}})
    assert response.get("status") == "answered", response
    second = applied["provider"]
    assert second in offered["request"]["action"]["proposed_membership"]

    hf_wire = parity._Wire(models=tuple(preset["models"]))
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
            return hf_wire
        return wires[self._definition.owner_user_id]
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(resolve))
    ModelPreferenceStore(tmp_path).save(
        parity.A_OWNER, parity.A_HOME, expected_generation=0,
        policy=ModelPreferences("explicit", ModelRef(first, parity.LIVE_MODELS[0]), (
            ModelRef(first, parity.LIVE_MODELS[1]), ModelRef(second, hf_wire.models[0]),
        )), require_current_home=True,
    )
    return SimpleNamespace(base=tmp_path, first=first, second=second, first_wire=first_wire,
                           second_wire=hf_wire, foreign=wires[parity.B_OWNER])


def test_workflow_falls_back_to_hugging_face_after_the_openrouter_daily_cap(
        hf_pool, monkeypatch):
    pooling.test_workflow_daily_quota_skips_sibling_and_uses_next_owned_source(
        hf_pool, monkeypatch)
    # Two runs (the second honours the retained OpenRouter cooldown): both on HF.
    assert hf_pool.second_wire.sent_models == ["openai/gpt-oss-120b"] * 2
    assert hf_pool.second_wire.reads == []  # a declared list: no catalogue read


def test_chat_falls_back_to_hugging_face_after_the_openrouter_daily_cap(hf_pool, monkeypatch):
    pooling.test_chat_daily_quota_skips_sibling_and_uses_next_owned_source(hf_pool, monkeypatch)
    assert hf_pool.second_wire.sent_models == ["openai/gpt-oss-120b"]


def test_subscription_deposit_needs_consent_before_joining_existing_sources(
        hf_pool, monkeypatch, authenticate_request):
    from tinyassets.api.pending_requests import answer_request
    from tinyassets.credential_vault import load_credential_vault, write_credential_vault
    from tinyassets.onboarding.source_connect import offer_subscription_source
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.call import get_provider_router

    authenticate_request(parity.A_OWNER)
    universe = hf_pool.base / parity.A_HOME
    records = load_credential_vault(universe)
    records.append({"credential_type": "llm_subscription", "service": "codex",
                    "auth_json_b64": "e30="})
    write_credential_vault(universe, records, owner_user_id=parity.A_OWNER,
                           universe_id=parity.A_HOME)
    get_provider_router()._providers["codex"] = SimpleNamespace(is_available=lambda: True)
    before = load_provider_assignment(hf_pool.base, universe_id=parity.A_HOME)
    prior = {m.provider: m.access for m in before.candidates}
    offered = offer_subscription_source(base=hf_pool.base, uid=parity.A_HOME,
                                        owner=parity.A_OWNER, service="codex")
    assert offered["request"]["action"]["type"] == "bind_model_access"
    assert offered["request"]["status"] == "pending"
    assert load_provider_assignment(hf_pool.base, universe_id=parity.A_HOME) == before
    action = offered["request"]["action"]
    assert action["model_access"]["codex"]["cost_caps"] is None
    for provider, access in prior.items():
        assert action["proposed_membership"][provider] == access.document()
    result = answer_request(universe_id=parity.A_HOME,
                             payload={"request_id": offered["request_id"], "values": {}})
    assert result.get("status") == "answered", result
    after = load_provider_assignment(hf_pool.base, universe_id=parity.A_HOME)
    assert after.provider == before.provider
    assert {m.provider for m in after.candidates} == set(prior) | {"codex"}
    for member in after.candidates:
        if member.provider in prior:
            assert member.access == prior[member.provider]
    assert offer_subscription_source(base=hf_pool.base, uid=parity.A_HOME,
                                      owner=parity.A_OWNER, service="codex") is None
