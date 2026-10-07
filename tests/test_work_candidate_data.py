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
    for policy in ({"preferred": {"provider": "b"}}, {"fallback_chain": []}):
        choices = data()
        with pytest.raises(PermissionError, match="conflicts"):
            choices.fit({"node_defs": [{"prompt_template": "hello", "llm_policy": policy}]},
                        ceiling=10, retry_multiplier=3)


def subscription_data():
    """One subscription source whose catalogue holds more than its own default.

    `order_models` deliberately gives a subscription/local connection ONE
    advisory candidate -- its advertised default -- and keeps its other accepted
    ids "visible in the catalogue for explicit selection". So the advisory order
    and the admitted catalogue genuinely differ here, which is the case below.
    """
    from tests.test_model_policy import NEEDS, model
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.model_policy import Catalog, ConnectionModels, ModelPolicy
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    connection = ConnectionModels(
        "codex", "codex-scope", "subscription", "fresh", True, True,
        (model(""), model("future-native-model")), default_model_id="",
    )
    plan = AgentModelPlan(
        Catalog("owner", "universe", (connection,)),
        ModelPolicy(0, "automatic", ()),
        NEEDS,
    )
    return WorkCandidateData(plan)


def test_a_graph_pin_the_advisory_order_omits_is_honoured_when_nothing_was_saved():
    """A node naming an accepted native id must still run.

    The advisory order for a subscription source is only its advertised default,
    so once EVERY run began capturing an order (the fix for the free-account
    run/chat divergence) a node pinning an accepted native model id was refused
    for not appearing in a ranking that never contained it. CI shards 5/6 and
    6/6 caught it: `test_native_model_execution` and
    `test_native_discovery_integration` both went red with "graph model
    constraint conflicts with captured primary".

    The owner chose nothing, so the order ranks rather than decides, and the
    graph pin IS the explicit choice. The pin still comes from the plan's
    ADMITTED catalogue, so it cannot reach a model the owner's own ModelAccess
    excludes.
    """
    choices = subscription_data()
    assert [ref.model_id for ref in choices.order] == ["", "future-native-model"]

    pinned = {"preferred": {"provider": "codex", "model": "future-native-model"}}
    choices.fit(
        {"node_defs": [{"prompt_template": "hello", "llm_policy": pinned}]},
        ceiling=10, retry_multiplier=1,
    )

    assert choices.next_candidate(pinned) == ModelRef("codex", "future-native-model")


def test_a_graph_pin_outside_the_admitted_catalogue_is_still_refused():
    """Honouring a pin the ranking omits must not honour one nobody admitted."""
    choices = subscription_data()
    absent = {"preferred": {"provider": "codex", "model": "never-granted"}}

    with pytest.raises(PermissionError, match="conflicts"):
        choices.fit(
            {"node_defs": [{"prompt_template": "hello", "llm_policy": absent}]},
            ceiling=10, retry_multiplier=1,
        )


def test_a_graph_pin_cannot_name_a_connection_outside_the_owners_catalogue():
    """The catalogue read is this owner's admitted sources, and only those."""
    choices = subscription_data()
    foreign = {"preferred": {"provider": "api_key_http:provdef_someone_else"}}

    with pytest.raises(PermissionError, match="conflicts"):
        choices.fit(
            {"node_defs": [{"prompt_template": "hello", "llm_policy": foreign}]},
            ceiling=10, retry_multiplier=1,
        )


def test_code_only_review_fits_the_captured_order_without_a_prompt_node():
    choices = data()
    snapshot = {"node_defs": [{"code": "pass", "effects": ["authenticated_external_call"]}]}
    assert choices.fit(snapshot, ceiling=4, retry_multiplier=3, review_attempts=2) == 4
    assert choices.next_candidate(None) == ModelRef("a", "A")
    with pytest.raises(PermissionError, match="invocation allowance"):
        data().fit(snapshot, ceiling=3, retry_multiplier=3, review_attempts=2)
