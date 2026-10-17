"""Finite advisory orders never enlarge accepted invocation authority."""

import pytest

from tinyassets.providers.model_policy import ModelRef


def refs(n):
    return tuple(ModelRef("owned", str(i)) for i in range(n))


def test_automatic_prefixes_fit_weighted_ceiling_without_increasing_it():
    from tinyassets.providers.work_candidate_data import fit_orders

    groups = {"a": (refs(100), 2, True), "b": (refs(100), 1, True)}
    fitted, minimum = fit_orders(groups, ceiling=7)
    assert fitted == {"a": refs(2), "b": refs(3)}
    assert minimum == 7


def test_explicit_order_does_not_silently_truncate():
    from tinyassets.providers.work_candidate_data import fit_orders

    with pytest.raises(PermissionError, match="invocation allowance"):
        fit_orders({"explicit": (refs(3), 2, False)}, ceiling=5)
    assert fit_orders({"empty_tail": (refs(1), 2, False)}, ceiling=100) == (
        {"empty_tail": refs(1)}, 2,
    )


@pytest.mark.parametrize("ceiling", [0, True, -1])
def test_invalid_ceiling_refuses(ceiling):
    from tinyassets.providers.work_candidate_data import fit_orders

    with pytest.raises((ValueError, PermissionError)):
        fit_orders({"a": (refs(1), 1, True)}, ceiling=ceiling)


def data(*, automatic=False, reconnect=()):
    from tests.test_model_policy import NEEDS, connection, model
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import Catalog, ModelPolicy
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    catalog = Catalog("owner", "universe", (
        connection("a", models=[model("A")]), connection("b", models=[model("B")]),
    ))
    policy = (ModelPolicy(0, "automatic", ()) if automatic else
              ModelPolicy(0, "explicit", (ModelRef("b", "B"),),
                          saved_default=ModelRef("a", "A")))
    plan = AgentModelPlan(catalog, policy, NEEDS, reconnect_sources=reconnect)
    return WorkCandidateData(plan)


def test_exhaustion_is_retained_across_reentry_without_fresh_catalogue_lookup():
    from tinyassets.providers.model_policy import Exhaustion

    choices = data()
    assert choices.fit({"node_defs": [{"prompt_template": "hello"}]},
                       ceiling=10, retry_multiplier=3) == 2
    assert choices.next_candidate(None) == ModelRef("a", "A")
    assert choices.next_candidate(None, (Exhaustion("model", ModelRef("a", "A")),)) == (
        ModelRef("b", "B")
    )
    # A newly refreshed catalogue could lack A. No refreshed object is consulted;
    # the session retains original capacity facts instead of restarting its plan.
    assert choices.next_candidate(None) == ModelRef("b", "B")
    assert choices.next_candidate(None, (Exhaustion("account", ModelRef("b", "B")),)) is None
    assert choices.next_candidate(None) is None


def test_automatic_health_demotion_is_preserved_but_explicit_primary_is_not_replaced():
    for automatic, expected in ((True, ModelRef("b", "B")), (False, ModelRef("a", "A"))):
        choices = data(automatic=automatic, reconnect=("a",))
        choices.fit({"node_defs": [{"prompt_template": "hello"}]},
                    ceiling=10, retry_multiplier=3)
        assert choices.next_candidate(None) == expected


def test_graph_pin_cannot_replace_explicit_user_primary_or_discard_tail():
    from tinyassets.providers.model_policy import Exhaustion

    choices = data()
    pinned = {"preferred": {"provider": "b", "model_id": "B"}}
    choices.fit({"node_defs": [{"prompt_template": "hello", "llm_policy": pinned}]},
                ceiling=10, retry_multiplier=3)
    assert choices.next_candidate(pinned) == ModelRef("b", "B")
    assert choices.order[0] == ModelRef("a", "A")
    assert choices.next_candidate(pinned, (Exhaustion("model", ModelRef("b", "B")),)) is None
    with pytest.raises(PermissionError, match="conflicts"):
        data().fit({"node_defs": [{"prompt_template": "hello",
                                   "llm_policy": {"fallback_chain": []}}]},
                   ceiling=10, retry_multiplier=3)


def http_data():
    """One HTTP source with two admitted models."""
    from tests.test_model_policy import NEEDS, model
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import Catalog, ConnectionModels, ModelPolicy
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    connection = ConnectionModels(
        "gateway", "gateway-scope", "http", "fresh", True, True,
        (model("a-default"), model("z-alternate")), default_model_id="a-default",
    )
    plan = AgentModelPlan(
        Catalog("owner", "universe", (connection,)),
        ModelPolicy(0, "automatic", ()),
        NEEDS,
    )
    return WorkCandidateData(plan)


def test_a_graph_pin_selects_an_admitted_alternative_without_saved_preference():
    """An explicit graph pin may select any model in its admitted catalogue.

    The owner chose nothing, so the order ranks rather than decides, and the
    graph pin IS the explicit choice. The pin still comes from the plan's
    ADMITTED catalogue, so it cannot reach a model the owner's own ModelAccess
    excludes.
    """
    choices = http_data()
    assert [ref.model_id for ref in choices.order] == ["a-default", "z-alternate"]

    pinned = {"preferred": {"provider": "gateway", "model": "z-alternate"}}
    choices.fit(
        {"node_defs": [{"prompt_template": "hello", "llm_policy": pinned}]},
        ceiling=10, retry_multiplier=1,
    )

    assert choices.next_candidate(pinned) == ModelRef("gateway", "z-alternate")


def test_a_graph_pin_outside_the_admitted_catalogue_is_still_refused():
    """Honouring a pin the ranking omits must not honour one nobody admitted."""
    choices = http_data()
    absent = {"preferred": {"provider": "gateway", "model": "never-granted"}}

    with pytest.raises(PermissionError, match="conflicts"):
        choices.fit(
            {"node_defs": [{"prompt_template": "hello", "llm_policy": absent}]},
            ceiling=10, retry_multiplier=1,
        )


def test_explicit_pin_to_unaccepted_or_over_cap_model_is_refused():
    from dataclasses import replace

    from tests.test_model_policy import NEEDS, connection, model
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import (
        Catalog,
        Charge,
        ModelPolicy,
        Pricing,
        SourceModelPolicy,
    )
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    priced = replace(model("priced"), pricing=Pricing("fresh", (
        Charge("input_million_tokens_usd", 1_000_000, True),
        Charge("output_million_tokens_usd", 1_000_000, True),
    )))
    catalog = Catalog("owner", "universe", (
        connection("claude-code", models=[model("opus")]),
        connection("gateway", models=[model("gpt-5.6-sol"), priced]),
    ))
    free_caps = tuple(Charge(name, 0, True) for name in sorted(
        ("input_million_tokens_usd", "output_million_tokens_usd")))
    choices = WorkCandidateData(AgentModelPlan(
        catalog, ModelPolicy(0, "explicit", (),
                             saved_default=ModelRef("claude-code", "opus")), NEEDS,
        source_policies=(SourceModelPolicy("claude-code", NEEDS, free_caps),
                         SourceModelPolicy("gateway", NEEDS, free_caps)),
    ))
    for model_id in ("unaccepted", "priced"):
        pinned = {"preferred": {"provider": "gateway", "model_id": model_id}}
        with pytest.raises(PermissionError, match="conflicts"):
            choices.fit({"node_defs": [{"prompt_template": "hello",
                                       "llm_policy": pinned}]},
                        ceiling=10, retry_multiplier=1)
    allowed = {"preferred": {"provider": "gateway", "model_id": "gpt-5.6-sol"}}
    choices.fit({"node_defs": [{"prompt_template": "hello", "llm_policy": allowed}]},
                ceiling=10, retry_multiplier=1)
    assert choices.next_candidate(allowed) == ModelRef("gateway", "gpt-5.6-sol")


def test_a_graph_pin_cannot_name_a_connection_outside_the_owners_catalogue():
    """The catalogue read is this owner's admitted sources, and only those."""
    choices = http_data()
    foreign = {"preferred": {"provider": "api_key_http:provdef_someone_else"}}

    with pytest.raises(PermissionError, match="conflicts"):
        choices.fit(
            {"node_defs": [{"prompt_template": "hello", "llm_policy": foreign}]},
            ceiling=10, retry_multiplier=1,
        )
