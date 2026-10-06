"""Outage regression: a saved exact model is a preference, not an outage lock."""

from dataclasses import replace

import pytest

from tests.test_model_policy import AUTO, NEEDS, connection, model, refs
from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.base import ProviderResponse
from tinyassets.providers.model_policy import Catalog, Charge, Exhaustion, ModelRef, Pricing, Scores


def plan():
    primary = replace(connection("primary", models=[model("chosen")]),
                      source_kind="subscription", provider_scope="primary-account",
                      default_model_id="chosen")
    secondary = replace(connection("secondary", models=[model("strongest")]),
                        source_kind="subscription", provider_scope="secondary-account",
                        default_model_id="strongest")
    free = connection("free", models=[
        replace(model("weaker", score=Scores("comparable-v1", "fresh", 1)),
                remaining_requests=50),
        replace(model("stronger", score=Scores("comparable-v1", "fresh", 99)),
                remaining_requests=50),
    ])
    return AgentModelPlan(
        Catalog("owner", "universe", (primary, free, secondary)),
        replace(AUTO, mode="explicit", saved_default=ModelRef("primary", "chosen")),
        NEEDS, "saved",
    )


def test_exact_saved_primary_recovers_on_other_subscription_before_ranked_free_models():
    selected = plan()
    failed = (Exhaustion("account", ModelRef("primary", "chosen")),)
    assert refs(selected.order("owner", "universe")) == [ModelRef("primary", "chosen")]
    assert not selected.order("owner", "universe", failed).candidates
    assert refs(selected.capacity_order("owner", "universe", failed)) == [
        ModelRef("secondary", "strongest"), ModelRef("free", "stronger"),
        ModelRef("free", "weaker"),
    ]
    assert selected.policy.fallbacks == ()
    assert selected.policy.saved_default == ModelRef("primary", "chosen")


def test_capacity_recovery_keeps_subscriptions_ahead_of_explicit_http_tail():
    selected = plan()
    selected = replace(selected, policy=replace(
        selected.policy, fallbacks=(ModelRef("free", "stronger"),),
    ))
    failed = (Exhaustion("account", ModelRef("primary", "chosen")),)
    assert refs(selected.capacity_order("owner", "universe", failed))[0] == ModelRef(
        "free", "stronger",
    )


@pytest.mark.parametrize("owner,universe", [("other-owner", "universe"), ("owner", "other-home")])
def test_capacity_order_cannot_read_another_owner_or_home(owner, universe):
    with pytest.raises(ValueError, match="catalogue scope mismatch"):
        plan().capacity_order(owner, universe)


def test_capacity_recovery_cannot_spend_on_unaccepted_paid_models():
    selected = plan()
    paid = replace(model("paid"), pricing=Pricing("fresh", (
        Charge("input_million_tokens_usd", 1, True),
        Charge("output_million_tokens_usd", 1, True),
    )))
    selected = replace(selected, catalog=replace(
        selected.catalog, connections=(*selected.catalog.connections,
                                       connection("paid-source", models=[paid])),
    ))
    assert ModelRef("paid-source", "paid") not in refs(
        selected.capacity_order("owner", "universe"),
    )


@pytest.mark.parametrize("reset,expected", [
    (1791244800, "The original source can be retried after 2026-10-06 00:00 UTC."),
    (None, "The original source did not report a reset time."),
])
def test_success_notice_names_answering_source_and_original_reset_once(reset, expected):
    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.capacity_switch = ("original", reset)
    response = ProviderResponse("answer", "secondary", "model", "family", 1,
                                provider_display="My subscription")
    result = turn._capacity_notice(response)
    assert result.text == (
        "answer\n\nAnswered by My subscription because original is cooling down or out of "
        f"capacity. {expected}"
    )
    assert response.text == "answer"
    assert result.text.count("Answered by") == 1
