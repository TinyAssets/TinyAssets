"""Shared unpowered model catalogue: actual ownership/storage, synthetic HTTP."""

import asyncio
import json

import pytest

from tests import test_served_model_preferences as integration
from tests.test_discovery_snapshot import _model
from tests.test_provider_served_router import _RecordingProvider, _served_context
from tinyassets import daemon_server, universe_server
from tinyassets.api import permissions
from tinyassets.auth import middleware as auth
from tinyassets.providers import call as provider_calls
from tinyassets.providers import definition, discovery_snapshot
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.model_preferences import ModelPreferenceStore
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

rig = integration.rig
reader = integration.reader
configured = integration.configured


@pytest.fixture
def catalogue(configured):
    base = configured.rig.base
    (base / "u-models" / "soul.md").write_text("# Fixture home", encoding="utf-8")
    daemon_server.grant_universe_access(base, universe_id="u-models", actor_id="owner",
                                       permission="admin")
    return configured


def read(**kwargs):
    """The complete catalogue: the shared domain read the owner door serves.

    The connector (the model door) projects it; see
    `test_full_catalogue_survives_read_limit_and_adapter`."""
    from tinyassets.api.graph_reads import read_graph

    return json.loads(read_graph(target="model_options", **kwargs))


def test_configured_but_unpowered_agent_can_read_without_activation(catalogue):
    before = catalogue.binding
    result = read()
    assert result["kind"] == "advisory_model_options" and result["advisory"] is True
    assert result["binding_state"] == "no_serving_binding" and result["binding"] is None
    assert len(result["options"]) == 1 and result["order"] == []
    assert result["unavailable"] == []  # Source failure is not a phantom empty model.
    assert result["source_failures"]
    assert result["sources"][0]["bind_key"] == catalogue.rig.definition.id
    assert result["sources"][0]["accepted"] is True
    assert "no_serving_binding" in result["sources"][0]["reasons"]
    from tinyassets.custom_agents import get_binding

    assert get_binding(catalogue.rig.base, universe_id="u-models",
                       binding_id=before["agent_binding_id"]) == before
    assert ModelPreferenceStore(catalogue.rig.base).get("owner", "u-models").generation == 0


def test_live_binding_uses_shared_plan_and_preserves_accepted_constraints(catalogue):
    catalogue.binding = integration.enable(catalogue)
    result = read()
    provider = "api_key_http:" + catalogue.rig.definition.id
    assert result["binding"] == {"id": catalogue.binding["agent_binding_id"],
                                 "revision": catalogue.binding["revision"]}
    assert result["choice_authority"] == "accepted_manifest"
    assert result["order"] == [{"provider_ref": provider, "model_id": "new-company/new-model"}]
    assert result["accepted_model_access"] == {catalogue.rig.definition.id: {
        "model_scope": "discovered", "model_ids": [], "cost_caps": None,
    }}


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
@pytest.mark.parametrize("revoke_native", [False, True])
def test_mixed_native_catalogue_keeps_independent_http_choices(
    catalogue, monkeypatch, revoke_native,
):
    from tests.test_native_discovery_integration import catalogue as native_catalogue
    from tinyassets.api import model_options
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import bind_serving_provider

    connected = bind_serving_provider(
        base_path=catalogue.rig.base, universe_dir=catalogue.rig.base / "u-models",
        owner_user_id="owner", universe_id="u-models",
        agent_binding_id=catalogue.binding["agent_binding_id"],
        expected_revision=catalogue.binding["revision"], provider=catalogue.rig.definition.id,
        model_access={"codex": ModelAccess("discovered"),
                      catalogue.rig.definition.id: ModelAccess("discovered")},
    )
    catalogue.binding = connected["agent_binding"]
    catalogue.binding = integration.enable(catalogue)
    monkeypatch.setattr(catalogue.native, "native_credential_service", "codex")

    async def enumerate_models(**kwargs):
        return native_catalogue(["future-native-choice"])

    monkeypatch.setattr(catalogue.native, "enumerate_models", enumerate_models)
    prepare = model_options.prepare_owned_model_plan

    def prepare_then_revoke(**kwargs):
        prepared = prepare(**kwargs)
        if revoke_native:
            write_credential_vault(catalogue.rig.base / "u-models", [],
                                   owner_user_id="owner", universe_id="u-models")
        return prepared

    monkeypatch.setattr(model_options, "prepare_owned_model_plan", prepare_then_revoke)
    result = read()
    providers = {row["reference"]["provider_ref"] for row in result["options"]}
    assert "api_key_http:" + catalogue.rig.definition.id in providers
    assert ("codex" in providers) is not revoke_native
    assert catalogue.native.calls == 0


def test_registered_but_unaccepted_models_are_visible_and_not_authorized(catalogue):
    catalogue.binding = integration.enable(catalogue)
    extra = definition.register_definition(
        universe_id="u-models", owner_user_id="owner", access_method="api_key_http",
        protocol="openai_chat", model="other-fixed-model", ref="grant-models",
    )
    result = read()
    source = next(row for row in result["sources"] if row["bind_key"] == extra.id)
    assert source["accepted"] is False and "source_not_accepted" in source["reasons"]
    row = next(row for row in result["options"]
               if row["reference"]["provider_ref"] == "api_key_http:" + extra.id)
    assert not row["in_candidate_catalog"] and row["order_index"] is None
    assert extra.id not in result["accepted_model_access"]


def test_legacy_model_scope_explains_optin_without_hiding_discovery(catalogue):
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import bind_serving_provider

    connected = bind_serving_provider(
        base_path=catalogue.rig.base, universe_dir=catalogue.rig.base / "u-models",
        owner_user_id="owner", universe_id="u-models",
        agent_binding_id=catalogue.binding["agent_binding_id"],
        expected_revision=catalogue.binding["revision"], provider=catalogue.rig.definition.id,
        model_access={catalogue.rig.definition.id: ModelAccess("legacy")},
    )
    catalogue.binding = connected["agent_binding"]
    result = read()
    assert len(result["options"]) == 1 and result["order"] == result["unavailable"] == []
    assert "model_access_optin_required" in result["sources"][0]["reasons"]
    assert "discovery_unavailable" not in result["sources"][0]["reasons"]


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_http_configuration_is_identified_without_inventing_a_candidate(
    catalogue, reader, monkeypatch, revoked,
):
    from tinyassets.provider_serving_binding import bind_serving_provider

    connected = bind_serving_provider(
        base_path=catalogue.rig.base, universe_dir=catalogue.rig.base / "u-models",
        owner_user_id="owner", universe_id="u-models",
        agent_binding_id=catalogue.binding["agent_binding_id"],
        expected_revision=catalogue.binding["revision"], provider=catalogue.rig.definition.id,
    )
    catalogue.binding = connected["agent_binding"]
    catalogue.binding = integration.enable(catalogue)
    if revoked:
        def revoke_during_discovery(**kwargs):
            result = reader[1](**kwargs)
            catalogue.rig.ledger.revoke_grant("grant-models")
            return result
        monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document",
                            revoke_during_discovery)
    result = read()
    assert result["legacy_source"] == (None if revoked else {
        "provider_ref": "api_key_http:" + catalogue.rig.definition.id,
        "bind_key": catalogue.rig.definition.id,
        "access_method": "api_key_http",
        "model_id": catalogue.rig.definition.model,
    })
    assert result["accepted_model_access"] == {} and result["order"] == []
    assert not any(row["in_candidate_catalog"] for row in result["options"])


def test_foreign_registered_source_is_not_listed(catalogue):
    foreign = definition.register_definition(
        universe_id="u-models", owner_user_id="different-owner", access_method="api_key_http",
        protocol="openai_chat", model="private-model", ref="private-grant",
    )
    assert foreign.id not in json.dumps(read())


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_access_confirmation_composes_bind_then_enable(catalogue, legacy):
    from tinyassets.provider_serving_binding import bind_serving_provider

    if legacy:
        bound = bind_serving_provider(
            base_path=catalogue.rig.base, universe_dir=catalogue.rig.base / "u-models",
            owner_user_id="owner", universe_id="u-models",
            agent_binding_id=catalogue.binding["agent_binding_id"],
            expected_revision=catalogue.binding["revision"], provider=catalogue.rig.definition.id,
        )
        catalogue.binding = bound["agent_binding"]
        source_id = catalogue.rig.definition.id
    else:
        extra = definition.register_definition(
            universe_id="u-models", owner_user_id="owner", access_method="api_key_http",
            protocol="openai_chat", model="extra-fixed", ref="grant-models",
        )
        source_id = extra.id
    catalogue.binding = integration.enable(catalogue)
    before = read()
    source = next(s for s in before["sources"] if s["bind_key"] == source_id)
    assert source["access_method"] == "api_key_http"
    access = {**before["accepted_model_access"], source["bind_key"]: {
        "model_scope": "discovered", "model_ids": [], "cost_caps": None,
    }}
    bound = json.loads(universe_server.write_graph(
        target="agent_binding", operation="bind_serving_provider", graph_id="u-models",
        agent_binding_id=before["binding"]["id"], expected_revision=before["binding"]["revision"],
        payload_json=json.dumps({"provider": source["bind_key"], "model_access": access}),
    ))
    assert bound["status"] == "ready"
    assert bound["agent_binding"]["status"] == "configured"
    enabled = json.loads(universe_server.write_graph(
        target="agent_binding", operation="set_serving", graph_id="u-models",
        agent_binding_id=bound["agent_binding"]["agent_binding_id"],
        expected_revision=bound["agent_binding"]["revision"],
        payload_json=json.dumps({"enabled": True}),
    ))
    assert enabled["status"] == "serving"
    after = read()
    assert after["choice_authority"] == "accepted_manifest" and after["legacy_source"] is None
    assert after["accepted_model_access"] == access
    assert after["order"]
    assert before["preferences"] == after["preferences"]


@pytest.mark.parametrize("case", ["anonymous", "other_actor", "non_admin", "foreign_graph",
                                 "no_home", "incomplete_home"])
def test_scope_refuses_before_discovery(catalogue, reader, monkeypatch, case):
    if case == "anonymous":
        monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    elif case == "other_actor":
        monkeypatch.setattr(permissions, "current_actor_id", lambda: "other-owner")
    elif case == "non_admin":
        daemon_server.grant_universe_access(catalogue.rig.base, universe_id="u-models",
                                           actor_id="owner", permission="read")
    elif case == "no_home":
        with SQLiteProviderWorkAuthorityStore(catalogue.rig.base).connection() as conn:
            conn.execute("DELETE FROM founder_home")
    elif case == "incomplete_home":
        (catalogue.rig.base / "u-models" / "soul.md").unlink()
    result = read(graph_id="foreign-home" if case == "foreign_graph" else "")
    assert "error" in result and "options" not in result
    assert reader[0] == []


def test_full_catalogue_survives_read_limit_and_adapter(catalogue, reader, monkeypatch):
    def many(**kwargs):
        result = reader[1](**kwargs)
        if "models/user" in kwargs["url"]:
            result["data"] = [_model("future/model-" + str(i)) for i in range(71)]
        return result

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", many)
    # The owner door: every choice, and `limit` does not reach this target.
    assert len(read(limit=1)["options"]) == 71
    # The model door: a projection that still counts every one of them.
    result = asyncio.run(universe_server.mcp.call_tool("read_graph", {"target": "model_options"}))
    assert result.structured_content["total_models"] == 71
    assert result.content and result.content[0].type == "text"


def test_scope_change_during_refresh_refuses_entire_result(catalogue, reader, monkeypatch):
    def rebind(**kwargs):
        result = reader[1](**kwargs)
        daemon_server.set_founder_home(catalogue.rig.base, founder_sub="owner",
                                       universe_id="other-home", platform_generated=True)
        return result

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", rebind)
    assert read() == {"error": "not_found", "resource": "model_options"}


def test_catalogue_does_not_serialize_private_source_metadata(catalogue):
    result = read()
    encoded = json.dumps(result)
    for secret in ("grant-models", "conn-models", "vault://", "owned.example",
                   str(catalogue.rig.base), "source_digest", "authenticated_account_id"):
        assert secret not in encoded
    assert result["sources"][0]["observed_at"] and result["sources"][0]["expires_at"]


def test_empty_owned_home_is_readable_without_provider_or_agent(tmp_path, monkeypatch):
    from tinyassets.auth.provider import Identity

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    universe = tmp_path / "u-empty"
    universe.mkdir()
    (universe / "soul.md").write_text("# Empty home", encoding="utf-8")
    daemon_server.set_founder_home(tmp_path, founder_sub="empty-owner", universe_id=universe.name,
                                   platform_generated=True)
    daemon_server.grant_universe_access(tmp_path, universe_id=universe.name,
                                       actor_id="empty-owner", permission="admin")
    with auth.identity_context(Identity(user_id="empty-owner", username="empty-owner")):
        result = read()
    assert result["options"] == result["sources"] == result["order"] == []
    assert result["preferences"]["generation"] == 0
    assert result["binding_state"] == "no_serving_binding"
    assert result["choice_authority"] == "none"


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_source_revocation_does_not_hide_independent_source(catalogue, reader, monkeypatch):
    catalogue.binding = integration.enable(catalogue)

    def revoked(**kwargs):
        result = reader[1](**kwargs)
        catalogue.rig.ledger.revoke_grant("grant-models")
        return result

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", revoked)
    result = read()
    assert result["order"] == [{"provider_ref": "codex", "model_id": ""}]
    # The ADMITTED options are exactly the routing order. Not every option: a
    # subscription source also offers the reviewed public list and the owner's own
    # verified ids, which are candidates to GRANT and deliberately absent from the
    # order -- an unusable id in the fallback chain is a turn that fails for no reason
    # the user can see.
    assert [row["reference"] for row in result["options"]
            if row["in_candidate_catalog"]] == result["order"]
    assert result["unavailable"] == []
    assert any("source_revoked" in row["reasons"] for row in result["sources"])


@pytest.mark.parametrize("metadata", [False, True])
def test_legacy_native_default_stays_visible_without_model_access_optin(
    tmp_path, monkeypatch, metadata,
):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "owner-1")
    universe, binding, capability, context = _served_context(tmp_path)
    try:
        (universe / "soul.md").write_text("# Fixture home", encoding="utf-8")
        daemon_server.set_founder_home(tmp_path, founder_sub="owner-1", universe_id=universe.name,
                                       platform_generated=True)
        daemon_server.grant_universe_access(tmp_path, universe_id=universe.name,
                                           actor_id="owner-1", permission="admin")
        native = _RecordingProvider("codex")
        if metadata:
            monkeypatch.setattr(native, "native_discovery_protocol", object())
        monkeypatch.setattr(provider_calls, "_router", ProviderRouter({"codex": native}))
        result = read()
        assert result["choice_authority"] == "legacy_single_provider"
        assert result["options"][0]["reference"] == {"provider_ref": "codex", "model_id": ""}
        assert result["options"][0]["provider_default"] is True
        assert result["accepted_model_access"] == {}
        assert result["legacy_source"] == {
            "provider_ref": "codex", "bind_key": "codex", "model_id": "",
            "access_method": "subscription_cli",
        }
        assert native.calls == 0
        assert result["sources"][0]["enumeration"] == ("supported" if metadata else "unknown")
    finally:
        auth.revoke_provider_request(capability)


def test_enumeration_support_accepts_executor_override_without_invoking_it(monkeypatch):
    from tinyassets.api.model_options import _native_enumeration

    native = _RecordingProvider("future-source")

    async def metadata(**kwargs):
        raise AssertionError("Capability display must not invoke discovery")

    monkeypatch.setattr(native, "enumerate_models", metadata)
    monkeypatch.setattr(provider_calls, "_router", ProviderRouter({"future-source": native}))
    assert _native_enumeration("future-source") == "supported"
    assert _native_enumeration("absent") == "unavailable"
