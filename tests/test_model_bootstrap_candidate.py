"""Real candidate/profile stores; synthetic catalogue, never live credentials."""

import pytest

from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding.hosted_model_auth import HostedAuthError, load_preset
from tinyassets.onboarding.model_bootstrap_candidate import prepare_candidate
from tinyassets.providers.definition import get_definition, list_definitions


def row(name="new-provider/new-free-model", price="0", tools=True):
    return {"id": name, "canonical_slug": name,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"] if tools else [], "context_length": 32000,
            "pricing": {"prompt": price, "completion": price}}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.providers import discovery_http
    from tinyassets.storage.outbound_connections import ConnectionLedger

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "u-owner").mkdir()
    set_founder_home(tmp_path, founder_sub="owner", universe_id="u-owner", platform_generated=True)
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner", permission="admin")
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="bootstrap-connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"), provider="http",
        destination="model:openrouter_user_models_v1", credential_ref="vault://http/synthetic",
        allowed_endpoints=[
            {"host": "openrouter.ai", "path_template": "/api/v1/chat/completions",
             "methods": ["POST"]},
            {"host": "openrouter.ai", "path_template": "/api/v1/models/user", "methods": ["GET"],
             "allowed_query": ["output_modalities"]},
            {"host": "openrouter.ai", "path_template": "/api/v1/benchmarks", "methods": ["GET"]},
        ],
    )
    ledger.grant_connection(grant_id="bootstrap-grant", connection_id="bootstrap-connection",
                            owner_user_id="owner", universe_id="u-owner")
    payload = {"data": [row()]}
    calls = []
    def catalogue(**kwargs):
        assert kwargs["grant_id"] == "bootstrap-grant"
        assert kwargs["owner_user_id"] == "owner"
        assert kwargs["universe_id"] == "u-owner"
        assert kwargs["url"] == load_preset("openrouter_user_models_v1").catalogue_url
        calls.append(kwargs)
        return payload
    monkeypatch.setattr(discovery_http, "read_granted_discovery_document", catalogue)
    with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
        yield tmp_path, payload, calls


def prepare(rig):
    return prepare_candidate(base=rig[0], uid="u-owner", owner="owner", grant_id="bootstrap-grant",
                             preset=load_preset("openrouter_user_models_v1"))


def test_seed_is_real_free_catalogue_model_and_no_serving_is_created(rig):
    from tinyassets.custom_agents import list_bindings
    from tinyassets.provider_assignment import load_provider_assignment

    did = prepare(rig)
    assert get_definition("u-owner", did).model == "new-provider/new-free-model"
    assert list_bindings(rig[0], universe_id="u-owner") == []
    assert load_provider_assignment(rig[0], universe_id="u-owner") is None
    assert prepare(rig) == did  # Partial resume cannot create a new descriptor on catalogue drift.
    assert len(rig[2]) == 1
    assert len(list_definitions("u-owner")) == 1


@pytest.mark.parametrize("models", [[], [row(price="0.01")], [row(tools=False)],
    [row(price="unknown")], [row(price="0.000000000000001")]])
def test_no_eligible_free_tool_model_leaves_registration_empty(rig, models):
    rig[1]["data"] = models
    with pytest.raises(HostedAuthError, match="no_eligible_free_agent_model"):
        prepare(rig)
    assert not list_definitions("u-owner")


def test_paid_first_row_does_not_become_initial_model(rig):
    rig[1]["data"] = [row("first-paid", "1"), row("brand-new-free", "0")]
    assert get_definition("u-owner", prepare(rig)).model == "brand-new-free"


def test_changed_home_refuses_before_catalogue_network(rig):
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(rig[0], founder_sub="owner", universe_id="another-home")
    with pytest.raises(PermissionError):
        prepare(rig)
    assert not rig[2]
    assert not list_definitions("u-owner")
