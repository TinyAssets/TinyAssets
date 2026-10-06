"""Automatic selection follows evidence, never source names or access methods."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_served_model_preferences as integration
from tests.test_model_policy import AUTO, NEEDS, connection, model, order, refs
from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.model_policy import Catalog, ModelRef, Scores


def sources():
    return (
        connection("small", models=[replace(model("free"), remaining_requests=50)]),
        replace(connection("large", models=[replace(model("paid"), remaining_requests=1000)]),
                source_kind="subscription", default_model_id="paid"),
    )


def test_subscription_beats_small_free_allowance_and_swapping_capacity_reverses_choice():
    small, large = sources()
    assert refs(order((small, large)))[0] == ModelRef("large", "paid")
    small = replace(small, models=(replace(small.models[0], remaining_requests=2000),))
    assert refs(order((small, large)))[0] == ModelRef("small", "free")
    # Changing access methods cannot change the answer.
    assert refs(order((replace(small, source_kind="subscription"),
                       replace(large, source_kind="http"))))[0] == ModelRef("small", "free")


def test_all_authorized_models_compete_and_swapping_catalog_scores_reverses_choice():
    a = connection("a", models=[model("a", score=Scores("comparable-v1", "fresh", 40))])
    b = replace(connection("b", models=[model("default"),
                model("best", score=Scores("comparable-v1", "fresh", 90))]),
                source_kind="subscription", default_model_id="default")
    assert refs(order((a, b)))[0] == ModelRef("b", "best")
    a = replace(a, models=(replace(a.models[0], scores=Scores("comparable-v1", "fresh", 99)),))
    assert refs(order((a, b)))[0] == ModelRef("a", "a")


@pytest.mark.parametrize("field", ["current_selection", "saved_default"])
def test_explicit_choice_survives_capacity_health_and_refusal(field):
    selected = ModelRef("small", "free")
    plan = AgentModelPlan(Catalog("owner", "universe", sources()),
                          replace(AUTO, **{field: selected}), NEEDS,
                          reconnect_sources=("small",), refused_models=(selected,),
                          cooling_sources=("small",))
    assert plan.next_candidate("owner", "universe") == selected
    assert plan.capacity_order("owner", "universe").candidates[0].ref == selected


@pytest.mark.parametrize("field", ["reconnect_sources", "cooling_sources", "refused_models"])
def test_health_demotes_only_automatic(field):
    failure = (ModelRef("large", "paid"),) if field == "refused_models" else ("large",)
    plan = AgentModelPlan(Catalog("owner", "universe", sources()), AUTO, NEEDS,
                          **{field: failure})
    assert plan.next_candidate("owner", "universe") == ModelRef("small", "free")


def test_declared_and_observed_capacity_reach_the_real_prepared_plan(configured, monkeypatch):
    from tinyassets import request_budget
    from tinyassets.providers import free_sources
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan

    monkeypatch.setattr(free_sources, "daily_cap_for_host", lambda host: {
        "requests_per_day": 50, "reset_timezone": "UTC", "name": "Small allowance",
    })
    def prepared():
        return prepare_owned_model_plan(
            base=configured.rig.base, universe=configured.rig.base / "u-models",
            owner="owner", agent=configured.binding,
        ).plan

    monkeypatch.setattr(request_budget, "requests_today", lambda *a, **kw: (0, 0))
    assert prepared().catalog.connections[0].models[0].remaining_requests == 50
    monkeypatch.setattr(free_sources, "daily_cap_for_host", lambda host: {
        "requests_per_day": 50, "credit_requests_per_day": 1000,
        "reset_timezone": "UTC", "name": "Observed upgrade",
    })
    monkeypatch.setattr(request_budget, "requests_today", lambda *a, **kw: (100, 100))
    assert prepared().catalog.connections[0].models[0].remaining_requests == 900


@pytest.mark.parametrize("counts", [(60, 60), None])
def test_disproven_or_unobserved_capacity_stays_unknown(configured, monkeypatch, counts):
    from tinyassets import request_budget
    from tinyassets.providers import free_sources
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan

    monkeypatch.setattr(free_sources, "daily_cap_for_host", lambda host: {
        "requests_per_day": 50, "reset_timezone": "UTC", "name": "Small allowance",
    })
    monkeypatch.setattr(request_budget, "requests_today", lambda *a, **kw: counts)
    plan = prepare_owned_model_plan(
        base=configured.rig.base, universe=configured.rig.base / "u-models",
        owner="owner", agent=configured.binding,
    ).plan
    assert plan.catalog.connections[0].models[0].remaining_requests is None


def test_ranking_has_no_vendor_or_source_kind_branches():
    from tinyassets.providers import default_model_evidence, model_policy

    banned = {"openrouter", "anthropic", "claude", "openai", "chatgpt", "codex", "gemini"}
    for module in (default_model_evidence, model_policy):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        literals = [n.value.lower() for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not any(vendor in literal for vendor in banned for literal in literals)
    ranking = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "ranking")
    assert not any(isinstance(n, ast.Attribute) and n.attr in
                   {"source_kind", "connection_id", "provider_scope", "model_id"}
                   for n in ast.walk(ranking))


# Reuse the real accepted-source fixture; no synthetic authority grants.
configured = integration.configured
reader = integration.reader
rig = integration.rig
