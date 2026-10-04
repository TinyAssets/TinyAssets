"""A held captured-prompt attempt keeps the cause the provider actually gave.

Live 2026-09-21 (run 07c1611916cc4eb4): a bound, admitted, invoked work model
failed; the attempt evidence lived only on ``__cause__``; the compiler's readers
take ``chain_state`` off the OUTER exception, so the stored run said "held" and
nothing else, and the run-read classifier told the owner to connect a provider
they had already connected and used.

These tests drive the REAL captured-prompt loop and the REAL persisted string
(``_wrap_provider_failure`` / ``_emit_failed_event``), not the annotation helper
on its own: the regression was the persistence path losing the evidence.
"""

from __future__ import annotations

import pytest

from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    ProviderAuthorityHeldError,
)
from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
from tinyassets.providers.model_policy import ModelRef

CONNECTION = "conn_a"
MODEL = "model-one"


def _attempt(**over):
    base = dict(
        provider=CONNECTION,
        status="failed",
        skip_class="timed_out",
        detail="stream idle for 30s",
        failure_class="provider_idle_timeout",
        side_effect_state=None,
        capacity_scope=None,
    )
    base.update(over)
    return ProviderAttemptDiagnostic(**base)


class _Candidates:
    """The run's work-model order. Records the boundaries the loop validated."""

    def __init__(self, refs):
        self._refs = list(refs)
        self.advanced = []

    def next_candidate(self, policy, exhaustions=None, *, local_exclusions=()):
        if exhaustions is not None:
            self.advanced.append(exhaustions)
            return None
        return self._refs.pop(0) if self._refs else None

    def exhausted_error(self, boundaries=()):
        from tinyassets.exceptions import WorkModelExhaustedError

        return WorkModelExhaustedError(
            f"{WorkModelExhaustedError.MESSAGE}: {MODEL} on {CONNECTION}",
        )


class _Router:
    def selected_agent_execution_kind(self, selected):
        return "engine_inference"


def _session(monkeypatch, *, refs, calls):
    """A real ``_ForegroundRunProviderSession`` with only the two collaborators
    the captured-prompt loop touches replaced."""
    from tinyassets import foreground_run_provider as frp
    from tinyassets.providers import call as provider_call

    monkeypatch.setattr(provider_call, "get_provider_router", lambda: _Router())
    session = object.__new__(frp._ForegroundRunProviderSession)
    from pathlib import Path
    from types import SimpleNamespace

    from tinyassets.request_budget import TurnRequestBudget

    session._work_candidates = _Candidates(refs)
    session._work_candidates.catalog = SimpleNamespace(owner_id="owner", connections=())
    session._universe_dir = Path("/tmp/diagnostic-fixture-universe")
    session._principal_id = "owner"
    session._request_budget = TurnRequestBudget("owner", session._universe_dir.name)
    calls_left = list(calls)

    def _call_once(role, prompt, system, config, policy, kwargs):
        outcome = calls_left.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    session._call_once = _call_once
    return session


def _run(session):
    from tinyassets.providers.base import ModelConfig

    return session._call_captured_prompt(
        "writer", "p", "s", ModelConfig(), {}, {}, None,
    )


def _stored_error(exc):
    """The exact string the run row stores for a node-level provider failure."""
    from tinyassets.graph_compiler import _wrap_provider_failure

    return str(_wrap_provider_failure("n1", exc))


def _held_from(monkeypatch, attempts, *, model=MODEL, **exc_kwargs):
    exc = AllProvidersExhaustedError(
        f"Armed provider {CONNECTION!r} exhausted; provider "
        f"{AllProvidersExhaustedError.NO_WIDENING_MESSAGE}.",
        attempts=attempts,
        **exc_kwargs,
    )
    session = _session(
        monkeypatch, refs=[ModelRef(CONNECTION, model)], calls=[exc],
    )
    with pytest.raises(ProviderAuthorityHeldError) as caught:
        _run(session)
    return caught.value


# --------------------------------------------------------------------------
# 1. The evidence survives the held raise and the compiler's persistence.
# --------------------------------------------------------------------------

def test_a_held_attempt_carries_its_own_typed_cause(monkeypatch):
    held = _held_from(monkeypatch, [_attempt()])

    assert ProviderAuthorityHeldError.ATTEMPT_MESSAGE in str(held)
    assert "provider_idle_timeout" in str(held)
    assert held.chain_state["attempts"][0]["failure_class"] == "provider_idle_timeout"
    # The capacity cause is not swallowed: it stays reachable as __cause__.
    assert isinstance(held.__cause__, AllProvidersExhaustedError)


def test_the_persisted_run_error_and_failed_event_name_the_cause(monkeypatch):
    """The OUTER exception is what the compiler reads. Both readers, one call."""
    from tinyassets.api.runs import _run_error_detail
    from tinyassets.graph_compiler import _emit_failed_event

    held = _held_from(monkeypatch, [_attempt()])
    events = []
    _emit_failed_event(lambda **kw: events.append(kw), "n1", held)

    assert events[0]["provider_chain"]["attempts"][0][
        "failure_class"] == "provider_idle_timeout"
    stored = _stored_error(held)
    assert "[chain_state]:" in stored
    # Read back the way get_run does: from the stored row, and from the events.
    from_error = _run_error_detail({"error": stored}, [])
    from_events = _run_error_detail(
        {"error": "x"}, [{"detail": {"provider_chain": events[0]["provider_chain"]}}],
    )
    for detail in (from_error, from_events):
        assert detail["provider_chain"]["attempts"][0][
            "failure_class"] == "provider_idle_timeout"


@pytest.mark.parametrize(("failure_class", "expected"), [
    ("provider_idle_timeout", "timeout"),
    ("interactive_deadline", "timeout"),
    ("provider_protocol_error", "provider_error"),
    ("auth_invalid", "auth_invalid"),
])
def test_the_run_read_advice_names_the_evidenced_cause(
    monkeypatch, failure_class, expected,
):
    """Not "connect your provider": the source was bound, admitted and invoked."""
    from tinyassets.api.runs import _classify_run_outcome_error

    held = _held_from(monkeypatch, [_attempt(failure_class=failure_class)])
    cls, action = _classify_run_outcome_error(_stored_error(held))

    assert cls == expected
    # The owner connected this source already; the advice must not send them
    # back to connect one, and must say so explicitly.
    assert "connect your provider" not in action.lower()
    assert "not a missing provider connection" in action
    # The safety advice is not traded away for the diagnosis.
    assert "may" in action and "already have happened" in action
    assert "nothing is retried automatically" in action


# --------------------------------------------------------------------------
# 2. Unknown stays unknown.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cause", ["unknown", "provider_future_class_2027"])
def test_an_unrecognised_cause_stays_unknown_and_keeps_its_evidence(
    monkeypatch, cause,
):
    from tinyassets.api.runs import _classify_run_outcome_error, _run_error_detail

    held = _held_from(
        monkeypatch, [_attempt(failure_class=cause, skip_class="unknown")],
    )
    stored = _stored_error(held)
    cls, action = _classify_run_outcome_error(stored)

    assert cls == "unknown"
    assert "does not recognise" in action
    # Unknown never means erased: the attempt is still in the run record.
    assert _run_error_detail({"error": stored}, [])["provider_chain"]["attempts"]


def test_a_held_attempt_with_no_diagnostics_is_unknown_not_unbound(monkeypatch):
    from tinyassets.api.runs import _classify_run_outcome_error

    held = _held_from(monkeypatch, [])
    cls, action = _classify_run_outcome_error(_stored_error(held))

    assert cls == "unknown"
    assert "not a missing provider connection" in action
    assert held.chain_state is None


# --------------------------------------------------------------------------
# 3. No leak: the persisted evidence goes through the router's own boundary.
# --------------------------------------------------------------------------

def test_a_secret_bearing_attempt_detail_is_scrubbed_before_it_is_persisted(
    monkeypatch,
):
    """The held boundary re-runs the router's own scrub, so a raise site that
    forgot to redact cannot publish a credential into the stored run."""
    token = "ghp_heldDiagnosticsFakeToken0123456789"
    held = _held_from(monkeypatch, [_attempt(
        failure_class="auth_invalid",
        skip_class="auth_invalid",
        detail=f"401 from provider, Authorization: Bearer {token}",
    )])
    stored = _stored_error(held)

    assert token not in stored
    assert token not in str(held)
    assert token not in str(held.chain_state)
    assert "[redacted]" in str(held.chain_state)
    assert "auth_invalid" in stored


# 4. Nothing else moved: capacity fallback, real no-authority, both readers.
# --------------------------------------------------------------------------

def test_a_clean_capacity_failure_still_falls_back_to_the_sibling(monkeypatch):
    """Proved side-effect-free capacity is NOT held: the loop advances."""
    clean = _attempt(
        skip_class="quota_or_cooldown",
        failure_class="provider_rate_limited",
        side_effect_state="none",
        capacity_scope="model",
        retry_after_s=30.0,
        detail="429",
    )
    exc = AllProvidersExhaustedError("exhausted", attempts=[clean])
    session = _session(
        monkeypatch,
        refs=[ModelRef(CONNECTION, MODEL), ModelRef(CONNECTION, "model-two")],
        calls=[exc, "second model answered"],
    )

    assert _run(session) == "second model answered"
    assert session._work_candidates.advanced, "the boundary must be recorded"


def test_a_real_no_authority_refusal_is_still_provider_not_bound():
    """No attempt was made, so no evidence and no reclassification."""
    from tinyassets.api.runs import _classify_run_error
    from tinyassets.foreground_run_provider import _HELD

    payload = _classify_run_error(ProviderAuthorityHeldError(_HELD), "branch_x")

    assert payload["failure_class"] == "permission_denied:provider_not_bound"


@pytest.mark.parametrize(("cause", "expected"), [
    ("provider_idle_timeout", "timeout"),
    ("interactive_deadline", "timeout"),
    ("auth_invalid", "auth_invalid"),
    ("provider_protocol_error", "provider_error"),
    ("provider_rate_limited", "quota_exhausted"),
    # Not in the table: an unhandled router exception, and a class a later
    # slice adds. Unknown stays unknown on BOTH surfaces.
    ("unknown", "unknown"),
    ("provider_future_class_2027", "unknown"),
])
@pytest.mark.parametrize("model", [MODEL, "timeout-model", "exhausted-model"])
@pytest.mark.parametrize("detail", [
    "stream closed",
    # The words the substring nets hunt for, in text the PROVIDER wrote.
    "401 unauthorized after the request timed out upstream; quota exhausted",
])
def test_both_stored_run_classifiers_agree_on_a_held_attempt(
    monkeypatch, cause, expected, model, detail,
):
    """`api.runs` and `runs._classify_failure` read the same stored row, so they
    must return the SAME class for it -- whatever the owner named their model or
    connection, and whatever words the provider's own detail happens to carry.

    Drives the real persisted string (``_wrap_provider_failure``), because the
    two surfaces only ever see that string, never the exception.
    """
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.runs import _classify_failure

    held = _held_from(
        monkeypatch,
        [_attempt(failure_class=cause, skip_class="unknown", detail=detail)],
        model=model,
    )
    stored = _stored_error(held)

    from_get_run = _classify_run_outcome_error(stored)[0]
    from_routing = _classify_failure({"status": "failed", "error": stored})
    assert from_get_run == expected
    assert from_routing == from_get_run, (
        f"one row, two causes: get_run={from_get_run} routing={from_routing}"
    )


@pytest.mark.parametrize(("cause", "expected"), [
    ("provider_idle_timeout", "timeout"),
    ("auth_invalid", "auth_invalid"),
])
@pytest.mark.parametrize("connection", [CONNECTION, "timeout-conn", "exhausted-conn"])
def test_both_classifiers_agree_on_a_single_source_exhaustion(
    cause, expected, connection,
):
    """The armed single-source raise ("authority forbids fallback widening")
    carries the same typed evidence, and its own message says "exhausted".
    A recognised cause wins over that word on both surfaces."""
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.providers.diagnostics import build_chain_state
    from tinyassets.runs import _classify_failure

    attempts = [_attempt(
        provider=connection, failure_class=cause, skip_class="unknown",
        detail="503 after the stream timed out; quota exhausted",
    )]
    exc = AllProvidersExhaustedError(
        f"Armed provider {connection!r} exhausted; provider "
        f"{AllProvidersExhaustedError.NO_WIDENING_MESSAGE}.",
        attempts=attempts,
        chain_state=build_chain_state(
            role="writer", chain=[connection], attempts=attempts,
        ),
    )
    stored = _stored_error(exc)

    from_get_run = _classify_run_outcome_error(stored)[0]
    from_routing = _classify_failure({"status": "failed", "error": stored})
    assert from_get_run == expected
    assert from_routing == from_get_run, (
        f"one row, two causes: get_run={from_get_run} routing={from_routing}"
    )


def test_an_unevidenced_single_source_exhaustion_keeps_its_existing_classes():
    """Unchanged behaviour control. Without a recognised cause the single-source
    raise is NOT reclassified: an exhausted chain is a true thing to say, and
    each surface keeps the class it returned before this change (they differ,
    and always have -- `provider_unavailable` vs `provider_exhausted`). Only a
    HELD attempt is answered from evidence alone."""
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.runs import _classify_failure

    text = (
        f"Armed provider {CONNECTION!r} exhausted; provider "
        f"{AllProvidersExhaustedError.NO_WIDENING_MESSAGE}."
    )
    assert _classify_run_outcome_error(text)[0] == "provider_unavailable"
    assert _classify_failure({"status": "failed", "error": text}) == "provider_exhausted"


def test_a_held_attempt_that_invoked_nothing_does_not_claim_it_did(monkeypatch):
    """A SKIPPED attempt was never tried. The row still classifies (held,
    unknown cause), but the advice must not assert an invocation."""
    from tinyassets.api.runs import _classify_run_outcome_error

    held = _held_from(monkeypatch, [_attempt(
        status="skipped", skip_class="not_in_registry",
        failure_class=None, detail="no such connection",
    )])
    cls, action = _classify_run_outcome_error(_stored_error(held))

    assert cls == "unknown"
    assert "invoked" not in action
    assert "cannot be said" in action
