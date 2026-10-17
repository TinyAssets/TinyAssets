"""Closed retry evidence, not a source-selection or execution-authority fixture."""

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from tinyassets.providers.agent_capacity_boundary import (
    capacity_boundary,
)
from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
from tinyassets.providers.model_policy import ModelRef

CURRENT = ModelRef("opaque-source", "")


def engine_boundary(current, attempts):
    return capacity_boundary(current, attempts, execution_kind="engine_inference")


def diagnostic(**kwargs):
    return ProviderAttemptDiagnostic(**{
        "provider": CURRENT.connection_id, "status": "failed", "skip_class": "provider_error",
        "failure_class": "provider_rate_limited", "side_effect_state": "none", **kwargs,
    })


@pytest.mark.parametrize("status", ["skipped", "failed"])
@pytest.mark.parametrize("scope,expected", [(None, "account"), ("unknown", "account"),
                                            ("account", "account"), ("model", "model")])
@pytest.mark.parametrize("failure", ["provider_rate_limited", "provider_overloaded",
                                      "provider_credit_exhausted"])
def test_only_explicit_no_effect_capacity_is_a_boundary(status, scope, expected, failure):
    boundary = engine_boundary(CURRENT, [diagnostic(
        status=status, skip_class="quota_or_cooldown", capacity_scope=scope,
        failure_class=failure, retry_after_s=9.5,
    )])
    assert boundary.exhaustion.ref == CURRENT and boundary.exhaustion.scope == expected
    assert boundary.failure_class == failure and boundary.retry_after_s == 9.5
    assert boundary.attempted is (status == "failed")


def test_all_skipped_gate_does_not_fabricate_a_remote_failure():
    boundary = engine_boundary(CURRENT, [diagnostic(
        status="skipped", skip_class="quota_or_cooldown",
        failure_class=None, side_effect_state=None,
    )])
    assert not boundary.attempted and boundary.failure_class is None
    assert boundary.exhaustion.scope == "account" and boundary.retry_after_s is None


@pytest.mark.parametrize("effect", [None, "possible", "committed", "unknown", "", 0, False])
def test_attempted_unknown_or_possible_work_is_not_retryable(effect):
    assert engine_boundary(CURRENT, [diagnostic(side_effect_state=effect)]) is None


@pytest.mark.parametrize("failure", [None, "unknown", "provider_idle_timeout",
                                      "interactive_deadline", "provider_error", "auth_invalid",
                                      [], {}, False])
def test_noncapacity_failures_are_not_retryable_even_with_no_effects(failure):
    assert engine_boundary(CURRENT, [diagnostic(failure_class=failure)]) is None


@pytest.mark.parametrize("skip", ["auth_invalid", "not_in_registry", "endpoint_unreachable",
                                  "timed_out", "provider_error", "unknown"])
def test_other_skipped_reasons_are_not_relabelled_capacity(skip):
    assert engine_boundary(CURRENT, [diagnostic(status="skipped", skip_class=skip)]) is None


@pytest.mark.parametrize("delay", [True, "9", -1, float("inf"), float("nan"), 2**31, 10**400])
def test_malformed_hint_invalidates_evidence(delay):
    assert engine_boundary(CURRENT, [diagnostic(retry_after_s=delay)]) is None


@pytest.mark.parametrize("bad", [
    {"provider": "other-source"}, {"status": "succeeded"}, {"capacity_scope": "global"},
    {"status": "skipped", "side_effect_state": "possible"},
    {"status": "skipped", "side_effect_state": "committed"},
])
def test_mismatched_and_contradictory_diagnostics_refuse(bad):
    assert engine_boundary(CURRENT, [diagnostic(**bad)]) is None


@pytest.mark.parametrize("attempts", [None, [], (), "skipped", {},
                                     [SimpleNamespace(status="skipped")],
                                     [diagnostic()] * 65])
def test_empty_unknown_or_unbounded_diagnostics_refuse(attempts):
    assert engine_boundary(CURRENT, attempts) is None


def test_every_attempt_must_be_safe_and_scope_is_conservative():
    local = diagnostic(capacity_scope="model", retry_after_s=8)
    unknown = diagnostic(capacity_scope=None, retry_after_s=15)
    boundary = engine_boundary(CURRENT, [local, unknown])
    assert boundary.exhaustion.scope == "account" and boundary.retry_after_s == 15
    assert engine_boundary(CURRENT, [local, replace(unknown, side_effect_state=None)]) is None
    assert engine_boundary(CURRENT, [replace(unknown, side_effect_state=None), local]) is None


def test_result_is_detached_from_mutable_router_diagnostics():
    item = diagnostic()
    boundary = engine_boundary(CURRENT, [item])
    item.side_effect_state = "possible"
    assert boundary.attempted and boundary.exhaustion.ref == CURRENT
    with pytest.raises(FrozenInstanceError):
        boundary.attempted = False


@pytest.mark.parametrize("ref", [None, SimpleNamespace(connection_id="opaque-source"),
                                 ModelRef("", ""), ModelRef("source\n", ""),
                                 ModelRef("opaque-source", None)])
def test_invalid_current_reference_refuses(ref):
    assert engine_boundary(ref, [diagnostic()]) is None
