"""Real preference -> enable -> converse -> writer composition; synthetic wires."""

from types import SimpleNamespace

import pytest

from tests import test_interactive_http_agent as integration
from tests import test_selected_model_authority as authority
from tests.support.http_model import provider_name
from tinyassets import daemon_server, universe_intelligence
from tinyassets.api.commons_catalog import commons_catalog
from tinyassets.auth import middleware as auth
from tinyassets.commons_catalog import create_binding, publish_definition
from tinyassets.config import load_universe_config
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import set_serving
from tinyassets.providers.base import UniverseContext
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.model_preferences import ModelPreferenceStore

OWNED_HTTP = provider_name("owner-1", "u-owner")

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
