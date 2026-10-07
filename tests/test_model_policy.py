"""Policy decisions only: these tests do not prove inference or grant authority."""

import ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tinyassets.providers import model_policy as policy_module
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
    order_models,
)

COMPONENTS = frozenset({"input_million_tokens_usd", "output_million_tokens_usd"})
FREE = Pricing("fresh", tuple(Charge(name, 0, True) for name in sorted(COMPONENTS)))
NEEDS = Interaction(True, frozenset({"text"}), COMPONENTS, min_context=100)
AUTO = ModelPolicy(7, "automatic", (), ranking_source="comparable-v1")


def model(name="opaque-A", *, score=None):
    return Model(name, True, frozenset({"text"}), 1000, FREE, score)


def connection(name="gateway", *, models=None, **kwargs):
    return ConnectionModels(
        name,
        "adapter-scope",
        "http",
        "fresh",
        True,
        True,
        tuple(models or [model()]),
        **kwargs,
    )


def order(connections, policy=AUTO, needs=NEEDS, exhaustion=()):
    return order_models(
        Catalog("owner", "universe", tuple(connections)),
        policy,
        needs,
        owner_id="owner",
        universe_id="universe",
        exhaustion=exhaustion,
    )


def refs(result):
    return [c.ref for c in result.candidates]


def test_connected_local_and_subscription_defaults_precede_ranked_http():
    hosted = connection(models=[model(score=Scores("comparable-v1", "fresh", 999))])
    local = replace(connection("local"), source_kind="local", default_model_id="opaque-A")
    subscription = replace(
        connection("subscription", models=[model("native"), model("not-selected")]),
        source_kind="subscription",
        default_model_id="native",
    )
    assert refs(order([hosted, local, subscription])) == [
        ModelRef("gateway", "opaque-A"),
        ModelRef("local", "opaque-A"),
        ModelRef("subscription", "native"),
        ModelRef("subscription", "not-selected"),
    ]


def test_explicit_current_overrides_saved_default_not_fallback_consent():
    c = connection(models=[model("current"), model("saved"), model("fallback")])
    policy = replace(
        AUTO,
        current_selection=ModelRef("gateway", "current"),
        saved_default=ModelRef("gateway", "saved"),
        fallbacks=(ModelRef("gateway", "fallback"),),
    )
    assert refs(order([c], policy)) == [policy.current_selection, policy.fallbacks[0]]
    assert refs(order([c], replace(policy, current_selection=None))) == [
        policy.saved_default,
        policy.fallbacks[0],
    ]


def test_empty_fallbacks_do_not_substitute_saved_default_or_auto_candidates():
    c = connection(models=[model("current"), model("saved")])
    primary = ModelRef("gateway", "current")
    policy = replace(AUTO, current_selection=primary, saved_default=ModelRef("gateway", "saved"))
    result = order([c], policy, exhaustion=(Exhaustion("model", primary),))
    assert not result.candidates
    assert result.ineligible[0].reason == "model_exhausted"


def test_missing_explicit_choice_stays_visible_without_automatic_substitution():
    missing = ModelRef("not-present", "new-unknown-release")
    result = order([connection()], replace(AUTO, current_selection=missing))
    assert not result.candidates
    assert result.ineligible[0].ref == missing
    assert result.ineligible[0].reason == "absent_from_catalogue"


def test_explicit_empty_primary_does_not_mean_auto():
    assert not order([connection()], replace(AUTO, mode="explicit")).candidates


@pytest.mark.parametrize("source", ["local", "subscription", "http"])
def test_stale_auto_capabilities_are_excluded_for_every_source(source):
    c = replace(connection(), source_kind=source, freshness="stale", default_model_id="opaque-A")
    result = order([c])
    assert result.ineligible[0].reason == "stale_capability"
    explicit = order([c], replace(AUTO, current_selection=ModelRef("gateway", "opaque-A")))
    assert explicit.candidates[0].labels == ("refresh_capabilities",)
    assert explicit.kind == "advisory_order"


def test_stale_explicit_price_is_labelled_not_treated_as_verified():
    c = connection(models=[replace(model(), pricing=replace(FREE, freshness="stale"))])
    assert order([c]).ineligible[0].reason == "stale_price"
    result = order([c], replace(AUTO, saved_default=ModelRef("gateway", "opaque-A")))
    assert result.candidates[0].labels == ("refresh_price",)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"owner_filtered": False}, "privacy_unverified"),
        ({"executor_tools": False}, "executor_unsupported"),
    ],
)
def test_connection_privacy_and_real_executor_capabilities(change, reason):
    c = replace(connection(), **change)
    assert order([c]).ineligible[0].reason == reason
    assert (
        order([c], replace(AUTO, saved_default=ModelRef("gateway", "opaque-A")))
        .ineligible[0]
        .reason
        == reason
    )


@pytest.mark.parametrize(
    "tools,reason", [(False, "capability_unsupported"), (None, "capability_unknown")]
)
def test_tool_support_must_be_known(tools, reason):
    assert (
        order([connection(models=[replace(model(), tools=tools)])]).ineligible[0].reason == reason
    )


@pytest.mark.parametrize(
    "pricing,reason",
    [
        (Pricing("fresh", ()), "missing_price_component"),
        (
            Pricing("fresh", (Charge("input_million_tokens_usd", 0, False),)),
            "missing_price_component",
        ),
        (Pricing("fresh", (Charge("input_million_tokens_usd", 1, True),)), "not_confirmed_free"),
    ],
)
def test_free_suffix_does_not_override_missing_unconfirmed_or_nonzero_price(pricing, reason):
    c = connection(models=[replace(model("brand/new:free"), pricing=pricing)])
    result = order([c])
    assert not result.candidates
    assert result.ineligible[0].reason == reason
    assert result.ineligible[0].component == "input_million_tokens_usd"


def test_explicit_selection_cannot_override_free_only_or_incomplete_caps():
    paid = Pricing("fresh", tuple(Charge(c, 3, True) for c in sorted(COMPONENTS)))
    c = connection(models=[replace(model(), pricing=paid)])
    policy = replace(AUTO, current_selection=ModelRef("gateway", "opaque-A"))
    assert order([c], policy).ineligible[0].reason == "not_confirmed_free"
    caps = tuple(Charge(c, 4, True) for c in sorted(COMPONENTS))
    assert order([c], replace(policy, cost_caps=caps)).candidates
    assert (
        order([c], replace(policy, cost_caps=caps[:1])).ineligible[0].reason == "exceeds_cost_cap"
    )
    assert (
        order([c], replace(policy, cost_caps=tuple(Charge(c, 2, True) for c in COMPONENTS)))
        .ineligible[0]
        .reason
        == "exceeds_cost_cap"
    )


def test_missing_interaction_pricing_requirements_cannot_vacuously_authorize_free():
    result = order([connection()], needs=replace(NEEDS, charge_components=frozenset()))
    assert not result.candidates
    assert result.ineligible[0].component == "required_charge_components"


def test_confirmed_unmetered_connection_does_not_require_metered_charge_components():
    c = connection(models=[replace(model(), pricing=Pricing("fresh", unmetered=True))])
    assert order([c]).candidates


def test_ranking_uses_only_comparable_fresh_evidence_and_stable_ties():
    models = [
        model("unranked"),
        model("other-source", score=Scores("other", "fresh", 10000)),
        model("low", score=Scores("comparable-v1", "fresh", 1, 99)),
        model("tie-a", score=Scores("comparable-v1", "fresh", 2, 1)),
        model("tie-b", score=Scores("comparable-v1", "fresh", 2, 1)),
        model("stale", score=Scores("comparable-v1", "stale", 90000)),
    ]
    result = order([connection(models=models)])
    assert [r.model_id for r in refs(result)] == [
        "tie-a",
        "tie-b",
        "low",
        "unranked",
        "other-source",
        "stale",
    ]
    result = order(
        [connection(models=models)], replace(AUTO, stable_preference=ModelRef("gateway", "tie-b"))
    )
    assert [r.model_id for r in refs(result)][:3] == ["tie-b", "tie-a", "low"]
    # A stable preference must not move an unranked model above scored ones.
    result = order(
        [connection(models=models)], replace(AUTO, stable_preference=ModelRef("gateway", "stale"))
    )
    assert refs(result)[-1].model_id == "stale"


def test_identifiers_and_excess_context_size_do_not_become_quality_scores():
    original = [model("old"), model("new-largest-latest"), model("small")]
    renamed = [
        replace(m, model_id=f"unseen/{i}:revision-999", context_tokens=10000 - i)
        for i, m in enumerate(original)
    ]
    assert [r.model_id for r in refs(order([connection(models=original)]))] == [
        m.model_id for m in original
    ]
    assert [r.model_id for r in refs(order([connection(models=renamed)]))] == [
        m.model_id for m in renamed
    ]


def test_new_catalogue_model_is_available_without_code_change():
    old = connection()
    refreshed = replace(old, models=old.models + (model("never-seen-provider/new-model"),))
    assert len(order([old]).candidates) == 1
    assert refs(order([refreshed]))[-1].model_id == "never-seen-provider/new-model"


def test_authenticated_shared_account_limit_skips_siblings_but_not_independent_account():
    a = connection("a", models=[model("x"), model("y")], authenticated_account_id="same")
    b = connection("b", authenticated_account_id="same")
    independent = connection("independent", authenticated_account_id="different")
    result = order([a, b, independent], exhaustion=(Exhaustion("account", ModelRef("a", "x")),))
    assert refs(result) == [ModelRef("independent", "opaque-A")]
    assert {i.reason for i in result.ineligible} == {"account_exhausted"}


def test_unknown_account_identity_does_not_enable_key_cycling():
    a, b = connection("a"), connection("b")
    independent = replace(connection("independent"), provider_scope="other-adapter")
    result = order(
        [a, b, independent], exhaustion=(Exhaustion("account", ModelRef("a", "opaque-A")),)
    )
    assert refs(result) == [ModelRef("independent", "opaque-A")]
    assert [i.reason for i in result.ineligible] == [
        "account_exhausted",
        "capacity_identity_unverified",
    ]


def test_model_local_exhaustion_allows_another_model_but_not_duplicate_account_keys():
    a = connection("a", models=[model("x"), model("y")], authenticated_account_id="same")
    b = connection("b", models=[model("x"), model("y")], authenticated_account_id="same")
    result = order([a, b], exhaustion=(Exhaustion("model", ModelRef("a", "x")),))
    assert refs(result) == [ModelRef("a", "y")]
    assert [i.reason for i in result.ineligible] == [
        "model_exhausted",
        "model_exhausted",
        "duplicate",
    ]


def test_ineligible_duplicate_does_not_suppress_usable_accepted_credential():
    bad = replace(connection("a", authenticated_account_id="same"), executor_tools=False)
    good = connection("b", authenticated_account_id="same")
    assert refs(order([bad, good])) == [ModelRef("b", "opaque-A")]


def test_scope_mismatch_and_conflicting_normalized_input_fail_closed():
    with pytest.raises(ValueError, match="scope"):
        order_models(
            Catalog("other", "universe", (connection(),)),
            AUTO,
            NEEDS,
            owner_id="owner",
            universe_id="universe",
        )
    with pytest.raises(ValueError, match="duplicate connection"):
        order([connection(), connection()])
    with pytest.raises(ValueError, match="duplicate model"):
        order([connection(models=[model(), model()])])
    with pytest.raises(ValueError, match="exhaustion connection"):
        order([connection()], exhaustion=(Exhaustion("account", ModelRef("absent", "x")),))


@pytest.mark.parametrize("invalid", [True, 0.1, -1, float("nan"), "0"])
def test_price_is_exact_and_never_coerced_to_free(invalid):
    with pytest.raises(ValueError):
        Charge("input", invalid)


def test_pure_output_is_repeatable_and_does_not_modify_inputs():
    catalog = [connection()]
    before = deepcopy((catalog, AUTO, NEEDS))
    assert order(catalog) == order(catalog)
    assert (catalog, AUTO, NEEDS) == before
    assert order(catalog).generation == 7
    tree = ast.parse(Path(policy_module.__file__).read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {"__future__", "dataclasses", "typing"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))


def test_unconfirmed_is_the_price_default():
    assert Charge("input", 0).confirmed is False


def test_invalid_price_combinations_fail_even_without_candidates():
    with pytest.raises(ValueError, match="duplicate"):
        Pricing("fresh", (Charge("input", 0), Charge("input", 0)))
    with pytest.raises(ValueError, match="unmetered"):
        Pricing("fresh", (Charge("input", 1),), unmetered=True)
    with pytest.raises(ValueError, match="duplicate"):
        replace(AUTO, cost_caps=(Charge("input", 0), Charge("input", 0)))


def test_automatic_without_primary_cannot_silently_discard_accepted_fallbacks():
    with pytest.raises(ValueError, match="primary"):
        replace(AUTO, fallbacks=(ModelRef("gateway", "opaque-A"),))


@pytest.mark.parametrize(
    "default,reason",
    [
        (None, "default_unavailable"),
        ("withdrawn", "absent_from_catalogue"),
    ],
)
def test_automatic_missing_native_default_has_a_visible_reason(default, reason):
    c = replace(connection(), source_kind="subscription", default_model_id=default)
    result = order([c])
    assert refs(result) == [ModelRef("gateway", "opaque-A")]
    assert result.candidates[0].basis == "automatic"
    assert not result.ineligible


def test_unverified_model_limit_does_not_cycle_same_model_through_sibling_keys():
    a = connection("a", models=[model("x"), model("y")])
    b = connection("b", models=[model("x"), model("y")])
    result = order([a, b], exhaustion=(Exhaustion("model", ModelRef("a", "x")),))
    assert ModelRef("b", "x") not in refs(result)
    assert ModelRef("b", "y") in refs(result)
    assert [i.reason for i in result.ineligible] == [
        "model_exhausted",
        "capacity_identity_unverified",
    ]


def test_ranked_order_is_invariant_under_model_id_and_excess_context_rewrites():
    models = [model(str(i), score=Scores("comparable-v1", "fresh", i)) for i in range(3)]
    rewritten = [
        replace(m, model_id=f"unexpected/{i}-latest", context_tokens=1000 - i)
        for i, m in enumerate(models)
    ]
    old = order([connection(models=models)])
    new = order([connection(models=rewritten)])
    assert [r.model_id for r in refs(old)] == ["2", "1", "0"]
    assert [r.model_id for r in refs(new)] == [m.model_id for m in reversed(rewritten)]
