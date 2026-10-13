"""The sentence a failed served turn shows the user names the TRUE failure class.

2026-08-29, live: a healthy multi-step GitHub job ended on the idle watchdog and
the app said "Served provider 'codex' exhausted; universe authority forbids
fallback widening." -- which reads as quota. The provider-routing spec
("the user notice reflects the true failure class, never mislabeling a timeout
as capacity") forbids exactly that.
"""

from __future__ import annotations

import pytest

from tinyassets.conversation_failure import (
    failure_notice,
    normalize_turn_failure,
    read_turn_failure,
)
from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    PlatformLLMCallRefusedError,
    ProviderAuthorityHeldError,
    WorkModelExhaustedError,
)
from tinyassets.providers.model_pins import ModelPinError
from tinyassets.providers.provider_jail import ProviderConfinementError
from tinyassets.providers.served_model_plan import NO_ELIGIBLE_MODEL
from tinyassets.universe_server import _served_failure_notice, _served_failure_record


def _exhausted(failure_class: str | None) -> AllProvidersExhaustedError:
    return AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        failure_class=failure_class,
    )


def test_an_idle_timeout_is_described_as_the_turn_ending_not_capacity():
    notice = _served_failure_notice(_exhausted("provider_idle_timeout"))
    assert "went quiet" in notice
    # A resend repeats the whole instruction, and the turn may already have
    # acted (Codex round 1, P1 concern): the sentence must say so.
    assert "repeats" in notice
    assert "continue" in notice
    for mislabel in ("exhausted", "fallback", "capacity", "quota"):
        assert mislabel not in notice.lower()


def test_an_interactive_deadline_is_described_honestly():
    notice = _served_failure_notice(_exhausted("interactive_deadline"))
    assert "time limit" in notice
    assert "repeats" in notice
    assert "exhausted" not in notice


def test_a_real_outage_is_named_more_precisely_than_the_raw_text():
    """CHANGED 2026-09-01, with the reasoning, not quietly.

    This asserted the VERBATIM exception for `provider_rate_limited`, on the
    grounds that a soft sentence would hide a real outage (Hard Rule 8). The
    concern is right and the conclusion no longer follows:

    * the raw text is "Served provider 'codex' exhausted; universe authority
      forbids fallback widening" -- the replacement names rate-limiting
      explicitly and says when to retry, so it is MORE specific, not softer;
    * this file's own docstring cites the spec, "the user notice reflects the
      true failure class". "Exhausted" for a rate limit is the mislabelling
      that spec forbids;
    * Hard Rule 8 targets failure dressed as success. This still reports
      failure, in words the owner can act on.

    The verbatim property survives for anything that is not our synthetic
    wrapper -- see `test_an_exception_without_a_failure_class_is_unchanged`,
    unchanged and passing.

    Live cost of the old behaviour: on 2026-09-01 the founder read "exhausted"
    and went to check his provider usage. It was fine; the real cause was a
    platform bug.
    """
    notice = _served_failure_notice(_exhausted("provider_rate_limited"))
    assert "rate-limiting" in notice
    assert "send again" in notice
    for mislabel in ("exhausted", "fallback widening"):
        assert mislabel not in notice.lower()


def test_an_exception_without_a_failure_class_keeps_its_own_words():
    notice = _served_failure_notice(RuntimeError("engine binding unreadable"))
    assert 'Detail: "engine binding unreadable"' in notice
    assert "Ref: " in notice


def test_unknown_failure_does_not_rule_out_billing_without_evidence():
    notice = _served_failure_notice(_exhausted(None))
    assert "could not identify why" in notice
    assert "not a usage or billing limit" not in notice


def test_auth_notice_does_not_invent_expiry_or_guarantee_reconnect():
    notice = _served_failure_notice(_exhausted("auth_invalid"))
    assert "sign-in" in notice and "reconnect" in notice.lower()
    for unsupported in ("expired", "revoked", "will fix"):
        assert unsupported not in notice


@pytest.mark.parametrize("skip_class", ["unknown", "timed_out"])
def test_native_auth_clue_requires_generic_provider_error(skip_class):
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = _exhausted(None)
    exc.attempts = [ProviderAttemptDiagnostic(
        "fixture", "failed", skip_class,
        "fixture-native terminal result was not success "
        "(subtype=success, is_error=true, last_assistant_error=authentication_failed)",
    )]
    assert "sign-in" not in _served_failure_notice(exc)


def test_native_auth_clue_never_uses_an_earlier_failed_attempt():
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = _exhausted(None)
    exc.attempts = [
        ProviderAttemptDiagnostic(
            "first", "failed", "provider_error",
            "fixture-native terminal result was not success "
            "(subtype=success, is_error=true, last_assistant_error=authentication_failed)",
        ),
        ProviderAttemptDiagnostic("second", "failed", "provider_error", "different failure"),
    ]
    assert "sign-in" not in _served_failure_notice(exc)


def test_malformed_attempts_do_not_break_the_unknown_failure_notice():
    exc = _exhausted(None)
    exc.attempts = 123
    assert "could not identify why" in _served_failure_notice(exc)


@pytest.mark.parametrize("error,code,advice", [
    (PermissionError(NO_ELIGIBLE_MODEL + "; not usable now: source (source not accepted); "
                     "model chosen/missing unavailable: absent from catalogue"),
     "model_unavailable", "pick another model"),
    (ModelPinError("model pin provider 'missing' is unavailable"),
     "model_unavailable", "pick another model"),
    (ProviderAuthorityHeldError("selected source is not accepted"),
     "authority_held", "check its connection"),
    (PlatformLLMCallRefusedError("no owner-connected authority"),
     "authority_held", "check its connection"),
    (WorkModelExhaustedError(WorkModelExhaustedError.MESSAGE),
     "work_model_exhausted", "restore capacity"),
    (ProviderConfinementError(ProviderConfinementError.MESSAGE),
     "provider_confinement_unavailable", "report this platform problem"),
])
def test_known_pre_request_refusal_has_specific_durable_notice(error, code, advice):
    import json

    record = _served_failure_record(error)
    assert (record.code, record.stage, record.effects) == (code, "before_send", "none")
    stored = json.dumps(normalize_turn_failure(record))
    assert read_turn_failure("platform", stored) == record
    notice = failure_notice(record)
    assert advice in notice
    assert "Nothing ran." in notice
    assert "could not identify why" not in notice


def test_unrelated_permission_error_still_has_unknown_cause():
    record = _served_failure_record(PermissionError("unclassified permission failure"))
    assert record.code == "unknown"
    assert "could not identify why" in failure_notice(record)


def test_a_previous_model_request_does_not_claim_nothing_ran():
    error = WorkModelExhaustedError(WorkModelExhaustedError.MESSAGE)
    error.turn_requests = 1
    record = _served_failure_record(error)
    assert record.code == "work_model_exhausted"
    assert record.effects == "unknown"
    assert "Nothing ran." not in failure_notice(record)
