"""Repair catalogue stays informative without relaxing execution's guards."""

from dataclasses import replace
from datetime import timedelta

import pytest

from tests import test_served_model_preferences as integration
from tests.test_discovery_snapshot import NOW
from tinyassets import daemon_server
from tinyassets.commons_catalog import get_binding
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import bind_serving_provider
from tinyassets.providers import definition, discovery_snapshot, served_model_plan
from tinyassets.providers.model_options import model_options_document
from tinyassets.storage.current_home import CurrentHomeChanged

rig = integration.rig
reader = integration.reader
configured = integration.configured


def collect(configured, **kwargs):
    return served_model_plan.prepare_owned_model_plan(
        base=configured.rig.base, universe=configured.rig.base / "u-models", owner="owner",
        agent=configured.binding, allow_empty=True, **kwargs,
    )


def document(prepared):
    return model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)


def test_empty_execution_catalogue_keeps_discovered_choices_for_repair(configured, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    result = document(collect(configured))
    assert result["order"] == []
    assert len(result["options"]) == 1
    assert not result["options"][0]["in_candidate_catalog"]
    assert result["options"][0]["reasons"] == [
        {"reason": "engine_tools_unavailable", "component": ""},
    ]
    with pytest.raises(PermissionError, match="no eligible model"):
        served_model_plan.prepare_owned_model_plan(
            base=configured.rig.base, universe=configured.rig.base / "u-models",
            owner="owner", agent=configured.binding,
        )
    assert get_binding(configured.rig.base, universe_id="u-models",
                       binding_id=configured.binding["agent_binding_id"]) == configured.binding


def test_display_result_cannot_be_reused_for_serving_activation(configured, monkeypatch):
    prepared = collect(configured)
    assert prepared.display_only is True and prepared.plan.next_candidate("owner", "u-models")
    monkeypatch.setattr(served_model_plan, "prepare_owned_model_plan", lambda **kwargs: prepared)
    with pytest.raises(ValueError, match="display catalogue cannot authorize activation"):
        integration.enable(configured)
    assert get_binding(configured.rig.base, universe_id="u-models",
                       binding_id=configured.binding["agent_binding_id"]) == configured.binding


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
@pytest.mark.parametrize("failure", ["expiry", "revocation"])
def test_final_source_failure_keeps_independent_native_choice(configured, monkeypatch, failure):
    actual = served_model_plan._http_models

    def change_after_filter(*args, **kwargs):
        value = actual(*args, **kwargs)
        if failure == "expiry":
            monkeypatch.setattr(discovery_snapshot, "_now", lambda: NOW + timedelta(minutes=6))
        else:
            configured.rig.ledger.revoke_grant("grant-models")
        return value

    monkeypatch.setattr(served_model_plan, "_http_models", change_after_filter)
    result = document(collect(configured))
    assert result["order"] == [{"provider_ref": "codex", "model_id": ""}]
    # The ADMITTED options are exactly the routing order. Not every option: a
    # subscription source also offers the reviewed public list and the owner's own
    # verified ids, which are candidates to GRANT and deliberately absent from the
    # order -- an unusable id in the fallback chain is a turn that fails for no reason
    # the user can see.
    assert [row["reference"] for row in result["options"]
            if row["in_candidate_catalog"]] == result["order"]
    reason = "discovery_expired" if failure == "expiry" else "source_revoked"
    assert any({"reason": reason, "component": ""} in row["reasons"]
               for row in result["source_failures"])
    assert configured.native.calls == 0


def test_changed_home_refuses_complete_display(configured, monkeypatch):
    actual = served_model_plan._http_models

    def rebind(*args, **kwargs):
        value = actual(*args, **kwargs)
        daemon_server.set_founder_home(configured.rig.base, founder_sub="owner",
                                       universe_id="other-home", platform_generated=True)
        return value

    monkeypatch.setattr(served_model_plan, "_http_models", rebind)
    with pytest.raises(CurrentHomeChanged):
        collect(configured)


def test_changed_assignment_does_not_remove_connected_source(configured, monkeypatch):
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    actual = served_model_plan._http_models

    def change_assignment(*args, **kwargs):
        value = actual(*args, **kwargs)
        with SQLiteProviderWorkAuthorityStore(configured.rig.base).connection() as conn:
            conn.execute("UPDATE provider_assignments SET state = 'failed' WHERE universe_id = ?",
                         ("u-models",))
        return value

    monkeypatch.setattr(served_model_plan, "_http_models", change_assignment)
    assert document(collect(configured))["order"]


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_native_executor_absence_is_visible_without_blocking_http(configured, monkeypatch):
    monkeypatch.setattr(configured.native, "is_available", lambda: False)
    result = document(collect(configured))
    assert len(result["order"]) == 1
    assert result["order"][0]["provider_ref"].startswith("api_key_http:")
    assert any(
        row["provider_ref"] == "codex" and
        {"reason": "executor_unavailable", "component": ""} in row["reasons"]
        for row in result["source_failures"]
    )


def test_different_source_contracts_keep_both_eligible_catalogues(configured, monkeypatch):
    second = definition.register_definition(
        universe_id="u-models", owner_user_id="owner", access_method="api_key_http",
        protocol="openai_chat", model="another-legacy-pin", ref="grant-models",
    )
    connected = bind_serving_provider(
        base_path=configured.rig.base, universe_dir=configured.rig.base / "u-models",
        owner_user_id="owner", universe_id="u-models",
        agent_binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"], provider=configured.rig.definition.id,
        model_access={source: ModelAccess("discovered")
                      for source in (configured.rig.definition.id, second.id)},
    )
    configured.binding = connected["agent_binding"]
    actual = served_model_plan._http_models
    seen = []

    def differing_contract(*args, **kwargs):
        snapshot, models, required, caps, rejected = actual(*args, **kwargs)
        seen.append(snapshot.provider)
        if len(seen) == 2:
            # A second valid collector with a different encoder price contract.
            # This fixture does not claim a second real network protocol exists.
            required = replace(required, excluded_components=frozenset({"different_unit"}))
        return snapshot, models, required, caps, rejected

    monkeypatch.setattr(served_model_plan, "_http_models", differing_contract)
    result = document(collect(configured))
    assert len(result["options"]) == 2 and len(result["order"]) == 2
    assert {row["provider_ref"] for row in result["order"]} == set(seen)
    assert all(row["in_candidate_catalog"] and not row["reasons"] for row in result["options"])
