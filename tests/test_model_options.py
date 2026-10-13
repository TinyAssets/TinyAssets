"""Model-picker projection keeps choices, execution order and identity separate."""

import json
from dataclasses import replace

import pytest

from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.model_policy import (
    Catalog,
    Charge,
    ConnectionModels,
    Ineligible,
    Interaction,
    Model,
    ModelPolicy,
    ModelRef,
    Pricing,
    Scores,
)


def connection(*ids):
    return ConnectionModels(
        "owned-source", "trusted-protocol", "http", "fresh", True, True,
        tuple(Model(model_id, True, frozenset({"text"}), pricing=Pricing("fresh", (
            Charge("input", 0, True), Charge("output", 0, True),
        ))) for model_id in ids),
        authenticated_account_id="PRIVATE-ACCOUNT-MUST-NOT-LEAK",
    )


def plan(catalog, *, primary=None, fallbacks=()):
    return AgentModelPlan(
        catalog, ModelPolicy(7, "automatic" if primary is None else "explicit", fallbacks,
                             saved_default=primary),
        Interaction(True, frozenset({"text"}), frozenset({"input", "output"})), "saved",
    )


def test_all_choices_visible_without_adding_implicit_fallbacks():
    catalog = Catalog("private-owner", "private-home", (connection("first", "other"),))
    result = model_options_document(
        catalog, plan(catalog, primary=ModelRef("owned-source", "first")),
    )
    assert [row["reference"]["model_id"] for row in result["options"]] == ["first", "other"]
    assert result["order"] == [{"provider_ref": "owned-source", "model_id": "first"}]
    assert result["options"][1]["order_index"] is None
    assert result["options"][1]["in_candidate_catalog"] is True
    encoded = json.dumps(result)
    assert "PRIVATE-ACCOUNT" not in encoded
    assert "private-owner" not in encoded
    assert "private-home" not in encoded


def test_health_hint_visible_on_models_outside_explicit_order():
    catalog = Catalog("private-owner", "private-home", (connection("first", "other"),))
    selected = replace(plan(catalog, primary=ModelRef("owned-source", "first")),
                       reconnect_sources=("owned-source",))
    result = model_options_document(catalog, selected)
    assert all("recent_sign_in_failure" in row["labels"] for row in result["options"])
    assert all(row["in_candidate_catalog"] for row in result["options"])
    assert result["order"] == [{"provider_ref": "owned-source", "model_id": "first"}]


def test_source_and_rows_have_one_freshness_state():
    catalog = Catalog("owner", "home", (connection("first"),))
    ref = ModelRef("owned-source", "")
    result = model_options_document(catalog, plan(catalog), (
        Ineligible(ref, "fresh", scope="source"),
        Ineligible(ref, "stale", scope="source"),
    ))
    assert result["source_failures"] == [{
        "provider_ref": "owned-source",
        "reasons": [{"reason": "stale", "component": ""}],
    }]
    assert result["options"][0]["freshness"] == "stale"


def test_zero_eligible_models_still_shows_reason_and_original_price():
    source = connection("costly")
    source = replace(source, models=(replace(source.models[0], pricing=Pricing("fresh", (
        Charge("input", 123, True), Charge("output", 456, True),
    ))),))
    available = Catalog("owner", "home", (source,))
    eligible = replace(available, connections=())
    result = model_options_document(available, plan(eligible), (
        Ineligible(ModelRef("owned-source", "costly"), "exceeds_cost_cap", "input"),
    ))
    assert result["order"] == []
    row = result["options"][0]
    assert row["in_candidate_catalog"] is False
    assert row["reasons"] == [{"reason": "exceeds_cost_cap", "component": "input"}]
    assert row["pricing"]["charges"][0]["amount_micros"] == 123


def test_unpowered_missing_saved_primary_and_fallback_remain_visible():
    catalog = Catalog("owner", "home", ())
    first, second = ModelRef("lost-source", "first"), ModelRef("lost-source", "second")
    result = model_options_document(catalog, plan(catalog, primary=first, fallbacks=(second,)))
    assert result["options"] == []
    assert [row["reference"]["model_id"] for row in result["unavailable"]] == ["first", "second"]
    assert all("pricing" not in row for row in result["unavailable"])
    assert result["policy_source"] == "saved"
    assert result["generation"] == 7


def test_refused_connection_without_models_is_visible_once():
    catalog = Catalog("owner", "home", ())
    rejection = Ineligible(ModelRef("unavailable", ""), "discovery_unavailable")
    result = model_options_document(catalog, plan(catalog), (rejection, rejection))
    assert result["unavailable"] == [{
        "reference": {"provider_ref": "unavailable", "model_id": ""},
        "reasons": [{"reason": "discovery_unavailable", "component": ""}],
    }]


def test_native_default_keeps_unknown_context_and_no_actual_receipt():
    source = replace(connection(""), source_kind="subscription", default_model_id="",
                     models=(Model("", True, frozenset({"text"}),
                                   pricing=Pricing("fresh", unmetered=True)),))
    catalog = Catalog("owner", "home", (source,))
    row = model_options_document(catalog, plan(catalog))["options"][0]
    assert row["reference"]["model_id"] == ""
    assert row["provider_default"] is True
    assert row["context_tokens"] is None
    assert row["scores"] is None
    assert "execution" not in row


def test_opaque_new_model_and_stale_evidence_not_rewritten():
    opaque = "NewProvider/next-模型"
    source = connection(opaque)
    source = replace(source, freshness="stale", models=(replace(source.models[0], tools=None,
        scores=Scores("independent-source", "stale", agentic=812)),))
    catalog = Catalog("owner", "home", (source,))
    result = model_options_document(catalog, plan(catalog))
    row = result["options"][0]
    assert row["reference"]["model_id"] == opaque
    assert row["freshness"] == "stale"
    assert row["tools"] is None
    assert row["scores"] == {"source": "independent-source", "freshness": "stale",
                             "agentic": 812, "general": None}
    assert result["order"] == []


@pytest.mark.parametrize("scope", ["owner_id", "universe_id"])
def test_scopes_cannot_be_mixed(scope):
    catalog = Catalog("owner", "home", (connection("model"),))
    with pytest.raises(ValueError, match="scopes"):
        model_options_document(replace(catalog, **{scope: "foreign"}), plan(catalog))


def test_duplicate_option_is_refused_instead_of_ambiguous_picker_row():
    catalog = Catalog("owner", "home", (connection("same", "same"),))
    with pytest.raises(ValueError, match="duplicate"):
        model_options_document(catalog, plan(catalog))
