"""Real preference -> enable -> converse -> writer composition; synthetic wires."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests import test_interactive_http_agent as integration
from tests import test_selected_model_authority as authority
from tinyassets import daemon_server, universe_intelligence
from tinyassets.api.commons_catalog import commons_catalog
from tinyassets.auth import middleware as auth
from tinyassets.commons_catalog import create_binding, get_binding, publish_definition
from tinyassets.config import load_universe_config
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import set_serving
from tinyassets.providers import discovery_snapshot
from tinyassets.providers.base import UniverseContext
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.model_preferences import ModelPreferences
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.model_preferences import ModelPreferenceStore
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

rig = authority.rig
reader = authority.reader
agent = integration.agent
REAL_GET_HOME = daemon_server.get_founder_home




@pytest.fixture
def configured(rig, reader, monkeypatch, request):
    monkeypatch.setattr(daemon_server, "get_founder_home", REAL_GET_HOME)
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    daemon_server.set_founder_home(
        rig.base, founder_sub="owner", universe_id="u-models", platform_generated=True,
    )
    definition = publish_definition(rig.base, author_id="owner", payload={
        "schema_version": 1, "name": "Policy", "description": "test", "tags": [],
        "components": {"identity": {"kind": "soul", "config": {}}},
    })
    binding = create_binding(
        rig.base, universe_id="u-models", definition_id=definition["agent_definition_id"],
        created_by="owner", payload={"schema_version": 1, "name": "Policy", "role": "writer"},
    )
    access = {rig.definition.id: ModelAccess("discovered")}
    connected = commons_catalog(
        action="bind_serving_provider", universe_id="u-models",
        binding_id=binding["agent_binding_id"], expected_revision=binding["revision"],
        payload={"provider": rig.definition.id,
                 "model_access": {name: scope.document() for name, scope in access.items()}},
    )
    assert connected["status"] == "ready"
    return SimpleNamespace(rig=rig, binding=connected["agent_binding"])


def enable(configured):
    return set_serving(
        base_path=configured.rig.base, universe_dir=configured.rig.base / "u-models",
        owner_user_id="owner", universe_id="u-models",
        agent_binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"], enabled=True,
    )["agent_binding"]


@pytest.fixture
def served(configured):
    binding = enable(configured)  # Real readiness: no direct serving-row mutation.
    reserve = auth.reserve_provider_request(
        principal_id="owner", session_id="prefs", request_id="prefs", tool_name="converse",
    )
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    carrier = auth.mint_provider_request_carrier(
        universe_id="u-models", agent_binding_id=binding["agent_binding_id"],
        binding_revision=binding["revision"], operation="converse",
    )
    state = SimpleNamespace(
        rig=configured.rig, agent=binding, capability=capability,
        wire=[], router=ProviderRouter({}),
        context=UniverseContext(
            universe_dir=configured.rig.base / "u-models",
            config=load_universe_config(configured.rig.base / "u-models"),
            provider_request=carrier,
        ),
    )
    yield state
    auth.revoke_provider_request(capability)


def _converse(agent, monkeypatch, choice=None, observer=None, message="hello"):
    monkeypatch.setattr(
        universe_intelligence.interlocutor, "resolve_interlocutor_tier",
        lambda *_: SimpleNamespace(tier=universe_intelligence.interlocutor.FOUNDER),
    )
    monkeypatch.setattr(
        universe_intelligence, "_build_persona_system_prompt",
        lambda *a, **k: getattr(agent, "before_system", lambda: "system")(),
    )
    monkeypatch.setattr(universe_intelligence, "extract_learning", lambda *a: None)
    monkeypatch.setattr(universe_intelligence, "commit_learning", lambda *a, **k: None)
    return universe_intelligence.converse(
        "u-models", message, model_choice=choice, response_observer=observer,
    )


def _save(agent, prefs):
    return ModelPreferenceStore(agent.served.rig.base).save(
        "owner", "u-models", expected_generation=0, policy=prefs, require_current_home=True,
    )


def test_new_opt_in_assignment_automatically_builds_real_plan(agent, monkeypatch):
    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert not agent.tools and len(agent.wires) == 1
    assert agent.wires[0][1]["body"]["model"] == authority.MODEL
    assert agent.latest().policy_source == "automatic"
    assert agent.latest().policy_generation == 0


def test_saved_choice_reaches_actual_writer_and_journal(agent, monkeypatch):
    selected = ModelRef(f"api_key_http:{agent.served.rig.definition.id}", authority.MODEL)
    _save(agent, ModelPreferences("explicit", selected, ()))
    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert agent.latest().policy_source == "saved" and agent.latest().policy_generation == 1


def test_one_turn_auto_replaces_unavailable_saved_choice_without_saving(agent, monkeypatch):
    store = ModelPreferenceStore(agent.served.rig.base)
    saved = _save(agent, ModelPreferences("explicit", ModelRef("missing", "unavailable"), ()))
    with pytest.raises(ProviderAuthorityHeldError):
        _converse(agent, monkeypatch)
    assert agent.wires == []
    assert _converse(agent, monkeypatch, ModelPreferences("automatic", None, ()).document())
    assert store.get("owner", "u-models", require_current_home=True) == saved
    assert agent.latest().policy_source == "current" and agent.latest().policy_generation == 1


def test_engine_disabled_allows_plain_chat_but_refuses_full_agent_task(agent, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert len(agent.wires) == 1 and not agent.tools
    with pytest.raises(ProviderAuthorityHeldError):
        _converse(agent, monkeypatch, message="Build an app in my command center")
    assert len(agent.wires) == 1 and not agent.tools












def test_unavailable_settings_pin_refuses_without_substituting(agent, monkeypatch):
    settings = agent.served.rig.base / "u-models" / "settings.yaml"
    settings.write_text("schema_version: 1\nmodel: {connection: missing, id: unavailable}\n")
    with pytest.raises(ProviderAuthorityHeldError, match="unavailable"):
        _converse(agent, monkeypatch)
    assert agent.wires == [] and agent.tools == []


def test_revocation_after_capture_still_prevents_inference(agent, monkeypatch):
    def revoke():
        agent.served.rig.ledger.revoke_grant("grant-models")
        return "system"

    agent.before_system = revoke
    with pytest.raises((ProviderAuthorityHeldError, PermissionError)):
        _converse(agent, monkeypatch)
    assert agent.wires == [] and agent.tools == []


def test_http_enable_allows_text_when_agent_executor_is_disabled(configured, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    assert enable(configured)["status"] == "serving"


def test_public_enable_accepts_the_owned_model_assignment(configured):
    response = commons_catalog(
        action="set_serving", universe_id="u-models",
        binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"], payload={"enabled": True},
    )
    assert response["status"] == "serving"
    assert response["provider"] == f"api_key_http:{configured.rig.definition.id}"


def test_canonical_mcp_choice_runs_real_selected_agent(agent, monkeypatch):
    from tests.test_converse_handle import _founder_auth
    from tinyassets import universe_server

    _founder_auth(monkeypatch, actor="owner", base=agent.served.rig.base, uid="u-models")
    monkeypatch.setattr(
        universe_intelligence, "_build_persona_system_prompt", lambda *a, **k: "system",
    )
    monkeypatch.setattr(universe_intelligence, "extract_learning", lambda *a: None)
    monkeypatch.setattr(universe_intelligence, "commit_learning", lambda *a, **k: None)
    choice = ModelPreferences("explicit", ModelRef(
        f"api_key_http:{agent.served.rig.definition.id}", authority.MODEL,
    ), ()).document()

    async def call():
        # Model the authenticated transport's inert reserve, not a capability
        # claimed in this test thread. The actual adapter claims in its worker.
        reserve = auth.reserve_provider_request(
            principal_id="owner", session_id="mcp-choice", request_id="mcp-choice",
            tool_name="converse",
        )
        token = auth.set_provider_request_reserve(reserve)
        try:
            return await universe_server.mcp.call_tool("converse", {
                "message": "hello", "graph_id": "u-models", "model_choice": choice,
            })
        finally:
            auth.cancel_provider_request_reserve(reserve)
            auth.reset_provider_request_reserve(token)

    result = asyncio.run(call())
    assert "reply" in result.structured_content, result.structured_content
    assert result.structured_content["reply"] == "finished exact answer"
    assert json.loads(result.content[0].text) == result.structured_content
    assert result.structured_content["execution"]["model"] == "actual-answer-model"
    assert not agent.tools and agent.latest().policy_source == "current"


@pytest.mark.parametrize("access", [None, {}, [], {"x": {}}, {"x": {
    "model_scope": "discovered", "model_ids": [], "cost_caps": {"request_usd": True},
}}, {"x": {"model_scope": "discovered", "model_ids": [], "cost_caps": None, "extra": 1}}])
def test_public_invalid_model_access_cannot_republish(configured, access):
    response = commons_catalog(
        action="bind_serving_provider", universe_id="u-models",
        binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"],
        payload={"provider": configured.rig.definition.id, "model_access": access},
    )
    assert response["error"] == "provider_authority_denied"
    assert get_binding(configured.rig.base, universe_id="u-models",
                       binding_id=configured.binding["agent_binding_id"]) == configured.binding


def test_public_missing_home_returns_structured_refusal(configured):
    daemon_server.set_founder_home(
        configured.rig.base, founder_sub="owner", universe_id="different", platform_generated=True,
    )
    response = commons_catalog(
        action="set_serving", universe_id="u-models",
        binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"], payload={"enabled": True},
    )
    assert response["error"] == "provider_authority_denied"


def test_refresh_selects_new_best_model_without_saved_policy_change(agent, monkeypatch):
    original = discovery_snapshot.read_http_discovery_document
    new_id = "brand-new-provider/opaque-release"

    def new_model(**kwargs):
        result = original(**kwargs)
        if "benchmarks" in kwargs["url"]:
            result["data"].append({
                "source": "artificial-analysis", "model_permaslug": new_id,
                "agentic_index": 99, "intelligence_index": 99,
            })
        else:
            result["data"].append(authority.snapshot_tests._model(new_id))
        return result

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", new_model)
    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert agent.wires[0][1]["body"]["model"] == new_id
    assert ModelPreferenceStore(agent.served.rig.base).get("owner", "u-models").generation == 0


def test_paid_new_best_model_is_not_a_free_fallback(agent, monkeypatch):
    original = discovery_snapshot.read_http_discovery_document
    paid_id = "new-vendor/paid-model"

    def paid_model(**kwargs):
        result = original(**kwargs)
        if "benchmarks" in kwargs["url"]:
            result["data"].append({
                "source": "artificial-analysis", "model_permaslug": paid_id,
                "agentic_index": 99, "intelligence_index": 99,
            })
        else:
            model = authority.snapshot_tests._model(paid_id)
            model["pricing"]["prompt"] = "0.000001"
            result["data"].insert(0, model)
        return result

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", paid_model)
    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert all(wire[1]["body"]["model"] == authority.MODEL for wire in agent.wires)


@pytest.mark.parametrize("choice", [{}, {"version": 2}, {"owner_user_id": "other"}])
def test_invalid_current_choice_refuses_before_provider_work(agent, monkeypatch, choice):
    with pytest.raises(ProviderAuthorityHeldError):
        _converse(agent, monkeypatch, choice)
    assert agent.wires == [] and agent.tools == []










def test_corrupt_saved_preferences_are_not_treated_as_absent(agent, monkeypatch):
    _save(agent, ModelPreferences("automatic", None, ()))
    with SQLiteProviderWorkAuthorityStore(agent.served.rig.base).connection() as conn:
        conn.execute("UPDATE universe_model_preferences SET policy_json = '{}' ")
    with pytest.raises(ProviderAuthorityHeldError, match="record unavailable"):
        _converse(agent, monkeypatch)
    assert agent.wires == [] and agent.tools == []




def test_discovery_expiry_at_final_capture_is_a_structured_hold(agent, monkeypatch):
    from tinyassets.exceptions import ProviderUnavailableError

    def expired(_snapshot):
        raise ProviderUnavailableError("model discovery is no longer fresh")

    monkeypatch.setattr(discovery_snapshot, "assert_discovery_snapshot_current", expired)
    with pytest.raises(ProviderAuthorityHeldError, match="no longer fresh"):
        _converse(agent, monkeypatch)
    assert agent.wires == [] and agent.tools == []


def test_public_enable_discovery_expiry_is_a_structured_refusal(configured, monkeypatch):
    from tinyassets.exceptions import ProviderUnavailableError

    def expired(_snapshot):
        raise ProviderUnavailableError("model discovery is no longer fresh")

    monkeypatch.setattr(discovery_snapshot, "assert_discovery_snapshot_current", expired)
    response = commons_catalog(
        action="set_serving", universe_id="u-models",
        binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"], payload={"enabled": True},
    )
    assert response["error"] == "provider_authority_denied"
    assert "no longer fresh" in response["detail"]
    assert get_binding(configured.rig.base, universe_id="u-models",
                       binding_id=configured.binding["agent_binding_id"]) == configured.binding


def test_preference_change_after_capture_applies_to_next_turn(agent, monkeypatch):
    _save(agent, ModelPreferences("automatic", None, ()))
    store = ModelPreferenceStore(agent.served.rig.base)

    def change():
        store.save("owner", "u-models", expected_generation=1, policy=ModelPreferences(
            "explicit", ModelRef("no-longer-selected", "missing"), (),
        ), require_current_home=True)
        return "system"

    agent.before_system = change
    assert _converse(agent, monkeypatch) == "finished exact answer"
    assert agent.latest().policy_generation == 1
    assert store.get("owner", "u-models").generation == 2
    with pytest.raises(ProviderAuthorityHeldError):
        _converse(agent, monkeypatch)
    assert len(agent.wires) == 1


def test_discovery_does_not_hold_database_write_transaction(configured, monkeypatch):
    original = discovery_snapshot.read_http_discovery_document
    probes = []
    store = SQLiteProviderWorkAuthorityStore(configured.rig.base)

    def probe(**kwargs):
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            probes.append(True)
            conn.rollback()
        return original(**kwargs)

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", probe)
    assert enable(configured)["status"] == "serving"
    assert len(probes) == 2


def test_changed_preferences_during_enable_refuse_without_enabling(configured, monkeypatch):
    original = discovery_snapshot.refresh_model_discovery

    def changed(**kwargs):
        result = original(**kwargs)
        ModelPreferenceStore(configured.rig.base).save(
            "owner", "u-models", expected_generation=0,
            policy=ModelPreferences("automatic", None, ()), require_current_home=True,
        )
        return result

    monkeypatch.setattr(discovery_snapshot, "refresh_model_discovery", changed)
    with pytest.raises(PermissionError, match="preferences changed"):
        enable(configured)
    assert get_binding(
        configured.rig.base, universe_id="u-models",
        binding_id=configured.binding["agent_binding_id"],
    )["status"] != "serving"
