"""Per-source advisory rules never lend price or capability evidence to peers."""

from dataclasses import replace

import pytest

from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.model_policy import (
    Catalog,
    Charge,
    ConnectionModels,
    Exhaustion,
    Interaction,
    Model,
    ModelPolicy,
    ModelRef,
    Pricing,
    Scores,
    SourceModelPolicy,
)


def mixed_plan():
    connections, requirements = [], []
    for name, unit, amount, score in (("first", "request_usd", 2, 10),
                                      ("second", "input_million_tokens_usd", 5, 20)):
        charge = Charge(unit, amount, True)
        connections.append(ConnectionModels(
            name, "shared-unknown-capacity", "http", "fresh", True, True,
            (Model(name, True, frozenset({"text"}), 1000,
                   Pricing("fresh", (charge,)), Scores("same-scale", "fresh", score)),),
        ))
        requirements.append(SourceModelPolicy(
            name, Interaction(True, frozenset({"text"}), frozenset({unit}), min_context=100),
            (charge,),
        ))
    return AgentModelPlan(
        Catalog("owner", "u", tuple(connections)),
        ModelPolicy(1, "automatic", (), ranking_source="same-scale"),
        Interaction(True, frozenset({"text"}), frozenset()),
        "automatic", tuple(requirements),
    )


def refs(plan, exhaustion=()):
    return [item.ref for item in plan.order("owner", "u", exhaustion).candidates]


def test_distinct_prices_rank_together_and_picker_uses_the_same_rules():
    plan = mixed_plan()
    expected = [ModelRef("second", "second"), ModelRef("first", "first")]
    assert refs(plan) == expected and plan.next_candidate("owner", "u") == expected[0]
    result = model_options_document(plan.catalog, plan)
    assert result["order"] == [{"provider_ref": item.connection_id, "model_id": item.model_id}
                                for item in expected]
    assert "source_policies" not in result
    assert plan.policy.cost_caps is None  # No invented union or maximum of accepted caps.


@pytest.mark.parametrize("failure", ["free_only", "low_cap", "missing_cap", "unknown_price",
                                     "unconfirmed", "tools", "context", "privacy", "stale"])
def test_one_source_cannot_borrow_another_sources_permissions_or_evidence(failure):
    plan = mixed_plan()
    first, second = plan.catalog.connections
    rule, peer_rule = plan.source_policies
    model = first.models[0]
    if failure == "free_only":
        rule = replace(rule, cost_caps=None)
    elif failure == "low_cap":
        rule = replace(rule, cost_caps=(Charge("request_usd", 1, True),))
    elif failure == "missing_cap":
        rule = replace(rule, cost_caps=())
    elif failure == "unknown_price":
        model = replace(model, pricing=replace(
            model.pricing, unknown_components=frozenset({"fee"}),
        ))
    elif failure == "unconfirmed":
        rule = replace(rule, cost_caps=(Charge("request_usd", 2, False),))
    elif failure == "tools":
        model = replace(model, tools=False)
    elif failure == "context":
        model = replace(model, context_tokens=1)
    elif failure == "privacy":
        first = replace(first, owner_filtered=False)
    else:
        first = replace(first, freshness="stale")
    first = replace(first, models=(model,))
    plan = replace(plan, catalog=replace(plan.catalog, connections=(first, second)),
                   source_policies=(rule, peer_rule))
    assert refs(plan) == [ModelRef("second", "second")]
    assert len(plan.order("owner", "u").ineligible) == 1


def test_explicit_order_and_model_capacity_still_control_fallbacks():
    plan = mixed_plan()
    first, second = ModelRef("first", "first"), ModelRef("second", "second")
    plan = replace(plan, policy=replace(plan.policy, mode="explicit",
                                        current_selection=first, fallbacks=(second,)))
    assert refs(plan) == [first, second]
    assert refs(plan, (Exhaustion("model", first),)) == [second]
    assert not refs(replace(plan, policy=replace(plan.policy, fallbacks=())),
                    (Exhaustion("model", first),))


def test_different_price_rules_do_not_prove_independent_account_capacity():
    plan = mixed_plan()
    assert not refs(plan, (Exhaustion("account", ModelRef("second", "second")),))
    assert {item.reason for item in plan.order("owner", "u", (
        Exhaustion("account", ModelRef("second", "second")),
    )).ineligible} == {"account_exhausted", "capacity_identity_unverified"}


@pytest.mark.parametrize("kind", ["subscription", "local"])
def test_native_defaults_compete_with_ranked_http_without_overriding_owner_choice(kind):
    plan = mixed_plan()
    native = ConnectionModels("native", "owned-local", kind, "fresh", True, True, (
        Model("", True, frozenset({"text"}), pricing=Pricing("fresh", unmetered=True)),
    ), default_model_id="")
    plan = replace(
        plan, catalog=replace(plan.catalog, connections=(*plan.catalog.connections, native)),
        source_policies=(*plan.source_policies, SourceModelPolicy("native", plan.interaction)),
    )
    assert refs(plan) == [ModelRef("second", "second"), ModelRef("first", "first"),
                          ModelRef("native", "")]
    plan = replace(plan, policy=replace(plan.policy, mode="explicit",
                                        current_selection=ModelRef("native", "")))
    assert plan.next_candidate("owner", "u") == ModelRef("native", "")


@pytest.mark.parametrize("mode", ["missing", "extra", "duplicate"])
def test_partial_or_ambiguous_per_source_rules_refuse_instead_of_global_fallback(mode):
    plan = mixed_plan()
    policies = plan.source_policies
    if mode == "missing":
        policies = policies[:1]
    elif mode == "extra":
        policies += (replace(policies[0], connection_id="foreign"),)
    else:
        policies += policies[:1]
    with pytest.raises(ValueError, match="complete catalogue"):
        refs(replace(plan, source_policies=policies))


def test_old_single_policy_plans_keep_their_existing_interpretation():
    plan = mixed_plan()
    source = plan.source_policies[0]
    old = replace(plan, catalog=replace(plan.catalog, connections=plan.catalog.connections[:1]),
                  policy=replace(plan.policy, cost_caps=source.cost_caps),
                  interaction=source.interaction, source_policies=())
    assert refs(old) == [ModelRef("first", "first")]
