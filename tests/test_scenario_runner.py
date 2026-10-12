"""Tests for AcceptanceScenario runtime (Slice 2 of the lane).

Per 2026-05-02-acceptance-scenario-packs.md (archived in git) (#936).

The runner is a thin dispatcher: validates the contract, routes to a
registered dispatcher per target_surface, and wraps the dispatcher's
return into a standard EvalResult. These tests exercise the contract +
dispatch + wrap behavior without invoking real user-sim, branch runs,
or live MCP calls (those would require heavy infrastructure).
"""

from __future__ import annotations

import pytest

from tinyassets.evaluation import EvalResult
from tinyassets.evaluation.scenario_runner import (
    AcceptanceScenario,
    register_dispatcher,
    registered_dispatchers,
    run_scenario,
    unregister_dispatcher,
)


def _valid_scenario(**overrides) -> AcceptanceScenario:
    """Build a valid AcceptanceScenario for tests, overridable per case."""
    base = {
        "scenario_id": "scenario:test-happy-path-v1",
        "target_surface": "mcp_call",
        "user_story": (
            "A new user opens their MCP-connected chatbot and asks "
            "for a Goal to be proposed for their new project. The "
            "chatbot should call goals action=propose, return a "
            "goal_id, and confirm the proposal in plain language. "
            "Acceptance requires both the goal_id and a confirmation "
            "narrative. This story is at least two hundred chars."
        ),
        "allowed_tools": ["goals", "universe"],
        "evaluator_chain": ["evaluator:goal-record-shape-check"],
        "artifact_requirements": [
            {"kind": "packet", "scope": "final", "redact_pattern": None}
        ],
        "pass_threshold": {"min_score": 0.5, "score_aggregation": "mean"},
        "cost_budget": {"max_tokens": 4000, "max_wall_time_seconds": 60},
        "privacy_scope": "commons_publishable",
        "idempotency_key_constructor": "sha256({scenario_id}|{candidate_ref}|{date_hour})",
    }
    base.update(overrides)
    return AcceptanceScenario(**base)


@pytest.fixture(autouse=True)
def _clean_dispatcher_registry():
    """Each test starts with an empty dispatcher registry."""
    yield
    for target_surface in list(registered_dispatchers().keys()):
        unregister_dispatcher(target_surface)


# ── Validation tests ──────────────────────────────────────────────────────────


def test_valid_scenario_constructs() -> None:
    scenario = _valid_scenario()
    assert scenario.scenario_id == "scenario:test-happy-path-v1"
    assert scenario.target_surface == "mcp_call"


def test_scenario_id_must_be_namespaced() -> None:
    with pytest.raises(ValueError, match="scenario_id"):
        _valid_scenario(scenario_id="no-namespace-prefix")


def test_target_surface_must_be_valid_enum() -> None:
    with pytest.raises(ValueError, match="target_surface"):
        _valid_scenario(target_surface="bogus_surface")


def test_privacy_scope_must_be_valid_enum() -> None:
    with pytest.raises(ValueError, match="privacy_scope"):
        _valid_scenario(privacy_scope="bogus_scope")


def test_user_story_length_bounds_enforced() -> None:
    with pytest.raises(ValueError, match="user_story length"):
        _valid_scenario(user_story="too short")
    with pytest.raises(ValueError, match="user_story length"):
        _valid_scenario(user_story="x" * 2001)


def test_evaluator_chain_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="evaluator_chain"):
        _valid_scenario(evaluator_chain=[])


def test_artifact_requirements_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="artifact_requirements"):
        _valid_scenario(artifact_requirements=[])


def test_pass_threshold_must_have_min_score() -> None:
    with pytest.raises(ValueError, match="min_score"):
        _valid_scenario(pass_threshold={})


def test_cost_budget_must_have_required_fields() -> None:
    with pytest.raises(ValueError, match="max_tokens"):
        _valid_scenario(cost_budget={"max_wall_time_seconds": 60})
    with pytest.raises(ValueError, match="max_wall_time_seconds"):
        _valid_scenario(cost_budget={"max_tokens": 4000})


def test_idempotency_key_constructor_must_be_declared() -> None:
    with pytest.raises(ValueError, match="idempotency_key_constructor"):
        _valid_scenario(idempotency_key_constructor="")


# ── Dispatch tests ───────────────────────────────────────────────────────────


def test_skip_verdict_when_no_dispatcher_registered() -> None:
    scenario = _valid_scenario()
    result = run_scenario(scenario, candidate_ref="branch:test")
    assert isinstance(result, EvalResult)
    assert result.verdict == "skip"
    assert result.kind == "custom"
    assert result.details["reason"] == "no_dispatcher_registered"
    assert result.details["scenario_id"] == scenario.scenario_id


def test_dispatcher_pass_result_wrapped_into_evalresult() -> None:
    def fake_dispatcher(scenario, candidate_ref, **kwargs):
        return {
            "score": 0.85,
            "verdict": "pass",
            "label": "fake-evaluator",
            "details": {"observation": "all assertions passed"},
        }

    register_dispatcher("mcp_call", fake_dispatcher)
    scenario = _valid_scenario()
    result = run_scenario(scenario, candidate_ref="branch:test")

    assert result.verdict == "pass"
    assert result.score == 0.85
    assert result.kind == "custom"
    assert result.label == "fake-evaluator"
    assert result.details["observation"] == "all assertions passed"
    # Runner-injected defaults
    assert result.details["scenario_id"] == scenario.scenario_id
    assert result.details["target_surface"] == "mcp_call"
    assert result.details["privacy_scope"] == "commons_publishable"


def test_dispatcher_score_below_threshold_yields_fail_when_verdict_omitted() -> None:
    def low_score_dispatcher(scenario, candidate_ref, **kwargs):
        # Returns no verdict; runner derives from pass_threshold.min_score
        return {"score": 0.3}

    register_dispatcher("mcp_call", low_score_dispatcher)
    scenario = _valid_scenario()  # min_score=0.5
    result = run_scenario(scenario, candidate_ref="branch:test")

    assert result.verdict == "fail"
    assert result.score == 0.3


def test_dispatcher_score_above_threshold_yields_pass_when_verdict_omitted() -> None:
    def high_score_dispatcher(scenario, candidate_ref, **kwargs):
        return {"score": 0.8}

    register_dispatcher("mcp_call", high_score_dispatcher)
    scenario = _valid_scenario()  # min_score=0.5
    result = run_scenario(scenario, candidate_ref="branch:test")

    assert result.verdict == "pass"
    assert result.score == 0.8


def test_dispatcher_exception_yields_error_verdict() -> None:
    def crashing_dispatcher(scenario, candidate_ref, **kwargs):
        raise RuntimeError("simulated dispatcher failure")

    register_dispatcher("mcp_call", crashing_dispatcher)
    scenario = _valid_scenario()
    result = run_scenario(scenario, candidate_ref="branch:test")

    assert result.verdict == "error"
    assert result.score == -1.0
    assert result.details["reason"] == "dispatcher_raised"
    assert result.details["exception_type"] == "RuntimeError"
    assert "simulated dispatcher failure" in result.details["exception_message"]


def test_dispatcher_invalid_verdict_falls_back_to_threshold_derivation() -> None:
    def weird_verdict_dispatcher(scenario, candidate_ref, **kwargs):
        return {"score": 0.7, "verdict": "definitely_pass"}  # not in enum

    register_dispatcher("mcp_call", weird_verdict_dispatcher)
    scenario = _valid_scenario()  # min_score=0.5
    result = run_scenario(scenario, candidate_ref="branch:test")

    # Falls back to threshold-derived verdict
    assert result.verdict == "pass"  # 0.7 > 0.5
    assert result.score == 0.7


def test_registry_isolates_per_target_surface() -> None:
    def mcp_dispatcher(scenario, candidate_ref, **kwargs):
        return {"score": 0.9, "verdict": "pass", "label": "mcp"}

    def ui_dispatcher(scenario, candidate_ref, **kwargs):
        return {"score": 0.6, "verdict": "pass", "label": "ui"}

    register_dispatcher("mcp_call", mcp_dispatcher)
    register_dispatcher("ui_test_mission", ui_dispatcher)

    mcp_scenario = _valid_scenario(scenario_id="scenario:mcp-v1", target_surface="mcp_call")
    ui_scenario = _valid_scenario(scenario_id="scenario:ui-v1", target_surface="ui_test_mission")

    mcp_result = run_scenario(mcp_scenario, candidate_ref="branch:x")
    ui_result = run_scenario(ui_scenario, candidate_ref="branch:x")

    assert mcp_result.label == "mcp"
    assert ui_result.label == "ui"


def test_register_dispatcher_rejects_unknown_target_surface() -> None:
    def some_dispatcher(scenario, candidate_ref, **kwargs):
        return {"score": 1.0, "verdict": "pass"}

    with pytest.raises(ValueError, match="unknown target_surface"):
        register_dispatcher("bogus_surface", some_dispatcher)


def test_dispatcher_score_clamped_to_valid_range() -> None:
    def out_of_range_dispatcher(scenario, candidate_ref, **kwargs):
        return {"score": 5.0, "verdict": "pass"}  # > 1.0

    register_dispatcher("mcp_call", out_of_range_dispatcher)
    scenario = _valid_scenario()
    result = run_scenario(scenario, candidate_ref="branch:test")

    assert result.score == 1.0  # clamped to upper bound
    assert result.verdict == "pass"
