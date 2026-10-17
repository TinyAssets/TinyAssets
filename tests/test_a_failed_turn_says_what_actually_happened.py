"""A failed turn must not tell the owner a confident wrong story.

Founder, 2026-09-01. His universe stopped answering and the app said:

    Your universe couldn't be reached right now: Served provider 'codex'
    exhausted; universe authority forbids fallback widening.

So he went and checked his codex usage. It was fine. The real error, sitting in
the run record the whole time, was:

    CompilerError: Provider call failed in node 'heartbeat':
    provider invocation carrier is already consumed

One failure, three stories: the app said quota, the run record said
`provider_unavailable` and advised checking ANTHROPIC/GROQ/GEMINI keys on a
universe with `api_key_providers_enabled=False`, and the truth was a platform
bug in neither category.

Two causes, both fixed here:

* `_TURN_ENDED_FAILURE_CLASSES` mapped two classes and fell through to the raw
  router exception -- and that exception says "exhausted" for EVERY
  all-attempts-failed reason.
* the run classifier matched the bare substring "provider", which swallowed our
  own invariant failures into "your keys are wrong".

The governing rule these tests encode: **a vague answer is a poor outcome, a
confident wrong one is worse**, because the owner acts on it.
"""
from __future__ import annotations

import json

import pytest

from tinyassets.conversation_failure import _CLASS_WORDS as _TURN_ENDED_FAILURE_CLASSES
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.universe_server import (
    _served_failure_notice,
)


class _Failure(Exception):
    """A router exception, which carries its class as an attribute."""

    def __init__(self, message: str, failure_class: str | None = None):
        super().__init__(message)
        self.failure_class = failure_class


# The exact text the founder was shown.
_THE_MESSAGE = (
    "Served provider 'codex' exhausted; universe authority forbids fallback widening."
)


def test_the_exact_sentence_the_founder_saw_can_never_reach_a_user_again():
    """The regression, pinned verbatim."""
    notice = _served_failure_notice(_Failure(_THE_MESSAGE, "provider_unavailable"))
    assert "exhausted" not in notice.lower(), notice
    assert "codex" not in notice.lower(), "the raw exception is still leaking"


def test_a_platform_bug_is_named_as_ours_not_as_the_owners_problem():
    """`provider invocation carrier is already consumed` is our invariant, not
    anything the owner configured. It must never be described as quota, keys or
    billing."""
    notice = _served_failure_notice(
        _Failure(
            "Provider call failed in node 'heartbeat': provider invocation "
            "carrier is already consumed",
            "provider_unavailable",
        )
    )
    lowered = notice.lower()
    assert "our side" in lowered
    # It DENIES those causes rather than avoiding the words -- a naive
    # substring check flagged the denial itself, which is the copy doing its
    # job. Assert the meaning.
    assert "not a problem with your account" in lowered
    assert "usage limits" in lowered and "not a problem" in lowered
    assert "exhausted" not in lowered, "the raw quota wording still leaks"


@pytest.mark.parametrize(
    "failure_class",
    ["provider_rate_limited", "provider_overloaded", "auth_invalid",
     "endpoint_unreachable", "platform_fault"],
)
def test_every_class_the_platform_distinguishes_has_its_own_sentence(failure_class):
    """`diagnostics.py` separates auth_invalid from endpoint_unreachable and
    calls that split "the main signal operators need" -- and then the notice
    layer threw it away. A distinction the platform computes and does not show
    is a distinction it does not have."""
    assert failure_class in _TURN_ENDED_FAILURE_CLASSES
    words = _TURN_ENDED_FAILURE_CLASSES[failure_class]
    # A clause the notice is composed from, not a whole sentence of its own.
    assert words.strip() and words[-1] not in ".!"
    assert words in _served_failure_notice(_Failure("x", failure_class))


def test_no_two_classes_share_a_sentence():
    """If two causes read identically the distinction is decorative."""
    sentences = list(_TURN_ENDED_FAILURE_CLASSES.values())
    assert len(set(sentences)) == len(sentences)


def test_only_OUR_misleading_wrapper_is_replaced_with_an_honest_unknown():
    """The rule is "honest vs misleading", not "classified vs unclassified".

    An earlier draft of mine suppressed EVERY unmapped exception. Two existing
    tests caught it, and they were right: `engine binding unreadable` is precise
    and the owner is better off reading it. Blanket suppression trades one wrong
    answer for another by destroying good information to hide bad.

    Only the router's synthetic wrapper actually lies -- it says "exhausted" for
    every all-attempts-failed reason there is.
    """
    lying = _served_failure_notice(_Failure(_THE_MESSAGE))
    assert "could not identify why" in lying.lower()
    assert "exhausted" not in lying.lower()
    assert "billing" in lying.lower(), "it should rule out the wrong guess"

    honest = _served_failure_notice(_Failure("engine binding unreadable"))
    assert "engine binding unreadable" in honest, (
        "a precise, non-misleading cause must still reach the owner"
    )


def test_the_auth_notice_does_not_promise_a_request_that_may_not_exist():
    """The rail synthesises the connect ask only when NO binding exists. A
    binding whose credential died leaves the owner bound, unserved and un-asked
    -- so promising "there is a request waiting" would be the same
    confident-wrong shape being fixed here."""
    notice = _TURN_ENDED_FAILURE_CLASSES["auth_invalid"]
    lowered = notice.lower()
    assert "reconnect" in lowered
    assert "request waiting" not in lowered
    assert "billing" in lowered or "usage" in lowered, (
        "it should say plainly that this is not a billing problem"
    )


# ------------------------------------------------------- the advice on runs


def test_a_subscription_only_universe_is_not_told_to_check_api_keys(monkeypatch):
    """The advice the founder actually received, on a deployment configured to
    ignore those variables entirely. Advice the reader cannot act on is worse
    than none: it sends them somewhere nothing they find can help."""
    from tinyassets.api.runs import _no_provider_advice

    monkeypatch.delenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", raising=False)
    advice = _no_provider_advice()
    for banned in ("ANTHROPIC", "GROQ", "GEMINI"):
        assert banned not in advice, f"still naming {banned} on a subscription universe"
    assert "connect" in advice.lower()


def test_the_retired_api_key_switch_does_not_change_the_advice(monkeypatch):
    """The platform has no LLM (Hard Rule 15): no deployment serves a universe
    from its own API keys, so the old opt-in switch no longer changes what the
    owner is told. The only fix worth naming is connecting their provider."""
    from tinyassets.api.runs import _no_provider_advice

    monkeypatch.delenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", raising=False)
    default = _no_provider_advice()
    monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "1")
    assert _no_provider_advice() == default
    assert "connect" in default.lower()


def test_the_run_classifier_calls_a_carrier_fault_ours(monkeypatch):
    """`"provider" in msg` swallowed our own invariants into "check your keys".
    Order matters: the platform-fault test has to run first."""
    from tinyassets.api.runs import _classify_run_outcome_error

    result = _classify_run_outcome_error(
        "CompilerError: Provider call failed in node 'heartbeat': provider "
        "invocation carrier is already consumed"
    )
    assert result is not None
    failure_class, advice = result
    assert failure_class == "platform_fault", failure_class
    assert "our side" in advice.lower()
    for banned in ("ANTHROPIC", "GROQ", "GEMINI"):
        assert banned not in advice


# ------------------------------ the promise the notice makes must be true


def test_the_details_the_notice_promises_are_actually_recorded(caplog):
    """"we have recorded the details" was a lie when I shipped it.

    `AllProvidersExhaustedError` has carried a structured `attempts` list since
    FEAT-006 -- provider, status, skip_class, failure_class, cooldown, detail --
    and a repo-wide grep found NO consumer. The router assembled the exact
    diagnosis on every failure and dropped it, which is how one outage could be
    reported as quota, then as missing API keys, and be neither.

    A promise in a user-facing sentence is a claim about behaviour, and it needs
    a test like any other claim.
    """
    import logging

    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.universe_server import _record_served_failure

    exc = AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        failure_class="provider_unavailable",
        attempts=[
            ProviderAttemptDiagnostic(
                provider="codex",
                status="failed",
                skip_class="auth_invalid",
                detail="launch credential rejected",
                failure_class="auth_invalid",
            )
        ],
    )

    with caplog.at_level(logging.WARNING):
        _record_served_failure("u-test", exc)

    written = caplog.text
    assert "codex" in written
    assert "auth_invalid" in written, "the per-provider class was not recorded"
    assert "launch credential rejected" in written
    assert "u-test" in written


def test_recording_never_breaks_the_failure_path(caplog):
    """It runs while a turn is already failing. A diagnostics bug must not
    replace the owner's error with a stack trace."""
    import logging

    from tinyassets.universe_server import _record_served_failure

    class _Hostile(Exception):
        @property
        def attempts(self):
            raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        _record_served_failure("u-test", _Hostile("x"))   # must not raise

    assert "u-test" in caplog.text


def test_recording_keeps_provider_terminal_cause_after_shortening(caplog):
    import logging

    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic, redacted_failure_detail
    from tinyassets.universe_server import (
        _record_served_failure,
    )

    detail = (
        "codex exec returned exit code 1 quickly -- likely unavailable: "
        + "model-list diagnostic " * 30
        + "https://x-access-token:ghp_SECRETVALUE@github.com/o/r "
        + "terminal cause: test-provider-refusal"
    )
    exc = AllProvidersExhaustedError(
        "exhausted",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="endpoint_unreachable",
            detail=detail,
        )],
    )
    with caplog.at_level(logging.WARNING):
        _record_served_failure("u-test", exc)
    assert "codex exec returned exit code 1" in caplog.text
    assert "terminal cause: test-provider-refusal" in caplog.text
    assert "ghp_SECRETVALUE" not in caplog.text
    assert len(redacted_failure_detail("x" * 500)) == 200
    assert redacted_failure_detail("short reason") == "short reason"


@pytest.mark.asyncio
async def test_router_failure_terminal_cause_reaches_served_log(caplog):
    import logging

    from tests.support.owner_bound import owner_bound_call
    from tinyassets.exceptions import ProviderUnavailableError
    from tinyassets.providers.base import BaseProvider
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.universe_server import _record_served_failure

    secret = "ghp_" + "S" * 40
    detail = (
        "codex launch: " + "x" * 50
        + " https://x-access-token:" + secret + "@github.com/o/r "
        + "model-list diagnostic " * 20
        + "terminal cause: test-provider-refusal"
    )

    class FailingProvider(BaseProvider):
        name = "codex"
        family = "openai"

        async def complete(self, prompt, system, config, *, universe_dir=None):
            raise ProviderUnavailableError(detail)

    router = ProviderRouter(providers={"codex": FailingProvider()})
    with pytest.raises(AllProvidersExhaustedError) as caught:
        await owner_bound_call(router, "codex", prompt="test prompt", system="test system")
    with caplog.at_level(logging.WARNING):
        _record_served_failure("u-test", caught.value)
    attempt = next(a for a in caught.value.attempts if a.status == "failed")
    assert "terminal cause: test-provider-refusal" in caplog.text
    assert "terminal cause: test-provider-refusal" in attempt.detail
    assert len(attempt.detail) <= 200
    assert secret not in caplog.text
    assert "ghp_" not in attempt.detail  # Also catches a partially cut secret.


@pytest.mark.asyncio
async def test_idle_timeout_evidence_survives_to_the_served_log_and_nothing_else_does(caplog):
    """The 2026-09-24 00:46Z line said `provider_idle_timeout ... attempts=1`
    and nothing about WHERE the turn was when the watchdog fired. The reader had
    attached `tool_phase` and `last_progress_age_ms` to the exception and the
    router had validated both onto the attempt; the recorder dropped them.

    Drive the REAL router so the evidence travels the production path, and
    poison the telemetry snapshot with every kind of field that must never
    reach a log: a prompt, a tool argument, a token, a user string, a
    credential-looking key. Only the allowlisted scalars may come out.
    """
    import logging

    from tests.support.owner_bound import owner_bound_call
    from tinyassets.exceptions import ProviderIdleTimeoutError
    from tinyassets.providers.base import BaseProvider
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.universe_server import _record_served_failure

    secret = "ghp_" + "S" * 40
    poison = {
        "tool_phase": "in_tool",
        "last_progress_age_ms": 31417.9,
        "side_effect_state": "possible",
        # None of the below is allowlisted; none may be rendered.
        "prompt": "USER_PROMPT_TEXT_MUST_NOT_LOG",
        "tool_arguments": {"path": "/home/owner/.ssh/id_rsa"},
        "tool_name": "Bash",
        "token": secret,
        "api_key": "sk-ant-SECRETKEY",
        "user_text": "the owner said something private",
        "last_assistant_error": "raw provider error string",
        "phase": "streaming",
    }

    class IdleProvider(BaseProvider):
        name = "claude-code"
        family = "anthropic"

        async def complete(self, prompt, system, config, *, universe_dir=None):
            exc = ProviderIdleTimeoutError(
                "claude -p produced no protocol event for 30s "
                "(idle watchdog fired; no provider cooldown)"
            )
            exc.attempt_telemetry = dict(poison)
            raise exc

    router = ProviderRouter(providers={"claude-code": IdleProvider()})
    with pytest.raises(AllProvidersExhaustedError) as caught:
        await owner_bound_call(
            router, "claude-code", prompt="test prompt", system="test system",
        )
    with caplog.at_level(logging.WARNING, logger="universe_server"):
        _record_served_failure("u-test", caught.value)

    written = caplog.text
    assert "provider_idle_timeout" in written
    assert "tool_phase=in_tool" in written, written
    assert "last_progress_age_ms=31418" in written, written
    assert "side_effect_state=possible" in written, written
    # The configured budget stays readable from the detail text.
    assert "no protocol event for 30s" in written
    # Probe by the poisoned VALUES: if any unlisted key had travelled, its
    # value would be here. (The chain-state dict legitimately carries the
    # boolean flag `api_key_providers_enabled`, so a bare key-name probe for
    # `api_key` would flag configuration, not a leak.)
    for leak in (
        "USER_PROMPT_TEXT_MUST_NOT_LOG", "id_rsa", "Bash", secret, "ghp_",
        "sk-ant", "owner said", "raw provider error string", "phase=streaming",
    ):
        assert leak not in written, f"{leak!r} reached the served log"


def test_malformed_evidence_is_omitted_not_rendered(caplog):
    """An unknown tool phase, a NaN age and an unknown side-effect state are
    not evidence. The recorder must leave them out rather than invite a
    reader to interpret them -- and must still write the line."""
    import logging
    import math

    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.universe_server import _record_served_failure

    exc = AllProvidersExhaustedError(
        "Served provider 'claude-code' exhausted; universe authority forbids fallback widening.",
        failure_class="provider_idle_timeout",
        attempts=[
            ProviderAttemptDiagnostic(
                provider="claude-code",
                status="failed",
                skip_class="timed_out",
                detail="claude -p produced no protocol event for 30s",
                failure_class="provider_idle_timeout",
                tool_phase="Bash(rm -rf /)",
                last_progress_age_ms=math.nan,
                side_effect_state="ghp_notastate",
            )
        ],
    )
    with caplog.at_level(logging.WARNING, logger="universe_server"):
        _record_served_failure("u-test", exc)
    written = caplog.text
    assert "provider_idle_timeout" in written
    assert "tool_phase=" not in written
    assert "last_progress_age_ms=" not in written
    assert "side_effect_state=" not in written
    assert "rm -rf" not in written and "ghp_" not in written


def test_a_secret_in_provider_detail_does_not_reach_the_log(caplog):
    """`detail` is provider text and may carry a token or a host path. It is
    scrubbed, and it never enters the owner's notice at all."""
    import logging

    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.universe_server import _record_served_failure

    exc = AllProvidersExhaustedError(
        "exhausted",
        attempts=[
            ProviderAttemptDiagnostic(
                provider="codex", status="failed", skip_class="auth_invalid",
                detail="clone failed https://x-access-token:ghp_SECRETVALUE@github.com/o/r",
            )
        ],
    )
    with caplog.at_level(logging.WARNING):
        _record_served_failure("u-test", exc)

    assert "ghp_SECRETVALUE" not in caplog.text, "a credential reached the log"


def test_the_recorder_is_actually_WIRED_into_the_failure_path():
    """Testing the helper is not testing that anything calls it.

    The mutation check caught this: deleting the call from the served-failure
    handler left every test above green, because they all invoke
    `_record_served_failure` directly. That is the same shape as the defect
    this PR started from -- a capability that exists and is not reachable on the
    surface that needs it.

    Source-level on purpose: the handler needs a full served turn to drive, and
    a guard that only fires when someone deletes the wiring is worth more than
    no guard at all.
    """
    import inspect
    import re

    import tinyassets.universe_server as srv

    source = inspect.getsource(srv)
    # The notice and the recorder must appear in the same handler, with the
    # recording BEFORE the reply is built.
    pattern = (
        r"_record_served_failure\(uid, exc, ref=record\.ref\)\s*\n\s*"
        # Telemetry may be guarded so its outage cannot hide the notice.
        r"(?:except Exception:\s*\n\s*logger\.warning\([^\n]*\n\s*)?"
        r"return json\.dumps\(\s*\{\s*\"error\":\s*"
        r"_served_failure_notice\(exc, record\)"
    )
    m = re.search(pattern, source)
    assert m, (
        "the served-failure handler no longer records the details before "
        "telling the owner that it has"
    )


def test_the_diagnosis_is_readable_from_OUTSIDE_the_container():
    """A diagnosis only readable from inside the box is, from outside, none.

    The recorder logs to the container. Nobody debugging remotely can read that
    -- so "we recorded the details" would still leave the person trying to fix
    the outage with the same generic sentence. The structured classes come back
    beside the notice instead.
    """
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.universe_server import _served_failure_diagnosis

    out = _served_failure_diagnosis(AllProvidersExhaustedError(
        "exhausted",
        failure_class="provider_unavailable",
        retry_after=12.5,
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid",
            detail="launch credential rejected for /data/u-x/creds.json",
            failure_class="auth_invalid",
        )],
    ))

    assert out["failure_class"] == "provider_unavailable"
    assert out["retry_after_s"] == 12.5
    [row] = out["provider_diagnosis"]
    assert row["provider"] == "codex"
    assert row["failure_class"] == "auth_invalid"


def test_the_free_text_detail_never_leaves_the_server():
    """`detail` is provider output and can carry a host, a path or a token. It
    stays in the scrubbed log; only the classes are returned."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.universe_server import _served_failure_diagnosis

    out = _served_failure_diagnosis(AllProvidersExhaustedError(
        "exhausted",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid",
            detail="token ghp_SECRETVALUE at /data/u-x/creds.json",
        )],
    ))

    blob = json.dumps(out)
    assert "ghp_SECRETVALUE" not in blob
    assert "/data/" not in blob
    assert "detail" not in blob


def test_a_successful_turn_carries_no_diagnosis_keys():
    """These appear only on failure; a healthy reply is unchanged."""
    from tinyassets.universe_server import _served_failure_diagnosis

    assert _served_failure_diagnosis(RuntimeError("plain")) == {}


def test_the_EXACT_payload_the_founder_got_now_names_the_cause():
    """Captured live 2026-09-01 from the founder's universe. Verbatim.

    The response carried BOTH halves at once: a notice saying nothing could be
    identified, and the identification sitting beside it.

        "error": "...we could not identify why..."
        "provider_diagnosis": [{"provider": "codex", "status": "failed",
                                "skip_class": "auth_invalid"}]

    `dominant_failure_class` only reads the STREAMED failure_class, and a
    `ProviderUnavailableError` -- which is every auth failure on a subprocess
    provider -- is classified into `skip_class` instead. So the top-level class
    was None while the attempt knew the answer.
    """
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid",
            detail=(
                "codex exec returned exit code 1 quickly -- likely unavailable; "
                "stderr: Error: Not logged in. Run `codex login`."
            ),
        )],
    )

    notice = _served_failure_notice(exc)
    lowered = notice.lower()
    assert "could not identify" not in lowered, (
        "the cause was in the attempt and the notice still shrugged"
    )
    assert "reconnect" in lowered, "the owner is not told what would fix it"
    assert "billing" in lowered or "usage" in lowered


def test_a_provider_that_was_never_TRIED_explains_nothing():
    """A `skipped` attempt is not a cause when something actually failed.

    My first capture was a skip during a cooldown window, which says only that an
    earlier failure happened -- reading it as the reason would report a cooldown
    as the diagnosis forever. NARROWED 2026-09-25: that holds whenever ANY
    provider was tried -- even one whose own class came out unknown -- and for a
    skip whose class was guessed rather than measured. It does not hold for a
    chain where the gate is the only thing that happened, because then the gate is
    the whole story: see the next test.
    """
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        attempts=[
            ProviderAttemptDiagnostic(
                provider="claude-code", status="failed", skip_class="provider_error",
                detail="the source's own words",
            ),
            ProviderAttemptDiagnostic(
                provider="codex", status="skipped", skip_class="quota_or_cooldown",
            ),
        ],
    )
    notice = _served_failure_notice(exc).lower()
    assert "cooldown window" not in notice, (
        "a provider that was never tried was reported as the cause"
    )

    # And with a class that IS mapped, so the assertion can actually
    # discriminate: quota_or_cooldown falls through either way, which made an
    # earlier version of this test survive its own mutation.
    skipped_but_mapped = AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="skipped", skip_class="endpoint_unreachable",
        )],
    )
    assert "could not identify" in _served_failure_notice(skipped_but_mapped).lower(), (
        "a GUESSED skip class was reported as the cause"
    )


def test_a_chain_that_was_only_ever_GATED_says_so():
    """When the gate is the whole story, the gate IS the answer.

    Live 2026-09-25: a free-model universe's next message produced exactly one
    attempt -- ``skipped quota_or_cooldown``, from the router's own cooldown map,
    with the seconds remaining attached -- and the founder was told "we could not
    identify why". Nothing was unknown: we had refused our own call and knew for
    how long. Only MEASURED skip classes qualify (the assertion above keeps a
    guessed one unknown), and only when no attempt actually failed.
    """
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="skipped", skip_class="quota_or_cooldown",
            detail="quota or cooldown gate", cooldown_remaining_s=92,
        )],
    )
    notice = _served_failure_notice(exc).lower()
    assert "could not identify" not in notice
    assert "cooldown window" in notice
    assert "2 minutes" in notice, "the owner is not told how long to wait"


def test_the_streamed_class_still_wins_when_both_are_present():
    """`skip_class` is the coarse bucket; the streamed class is more precise."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "exhausted",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid",
            failure_class="provider_rate_limited",
        )],
    )
    assert "rate-limiting" in _served_failure_notice(exc)


def test_the_classifiers_DEFAULT_bucket_is_not_promoted_to_a_diagnosis():
    """Codex, review round 1, P6. `classify_unavailable` returns
    `endpoint_unreachable` whenever the provider text carries no auth tell --
    it is "no evidence found", not "the network is down". A claude quick-exit
    on an expired credential reads "claude -p returned exit code 1 quickly --
    API likely unavailable": no tell, so `endpoint_unreachable`, and promoting
    it would tell the owner it is "nothing you set up wrong" and send them away
    from reconnecting. The honest unknown wins."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "Served provider 'claude-code' exhausted; universe authority forbids fallback widening.",
        attempts=[ProviderAttemptDiagnostic(
            provider="claude-code", status="failed", skip_class="endpoint_unreachable",
            detail="claude -p returned exit code 1 quickly -- API likely unavailable",
        )],
    )
    lowered = _served_failure_notice(exc).lower()
    assert "could not identify" in lowered, lowered
    assert "nothing you set up wrong" not in lowered
    assert "exhausted" not in lowered


def test_an_EVIDENCED_skip_class_is_still_promoted():
    """`auth_invalid` with narrow credential evidence in the attempt's text
    keeps reaching the owner."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "exhausted",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid",
            detail="codex: 401 unauthorized",
        )],
    )
    assert "reconnect" in _served_failure_notice(exc).lower()


def test_a_token_LIMIT_error_is_not_reported_as_an_expired_credential():
    """Codex, review round 2, R3. `classify_unavailable` matches the bare
    substring "token", so a context-length failure is classified
    `auth_invalid`. Promoting that would tell the owner their credential
    expired and that reconnecting will fix it -- which it cannot. The honest
    unknown wins."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "Served provider 'codex' exhausted; universe authority forbids fallback widening.",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid",
            detail=(
                "codex exec returned exit code 1 quickly; stderr: Error: input "
                "exceeds the model's maximum token limit"
            ),
        )],
    )
    lowered = _served_failure_notice(exc).lower()
    assert "reconnect" not in lowered, lowered
    assert "could not identify" in lowered


@pytest.mark.parametrize("detail", [
    "Error: Not logged in. Run `codex login`.",
    "HTTP 401 Unauthorized",
    "status 401",
    "oauth error: invalid_grant",
    "token expired; please reauthenticate",
    # Codex 0.146 and Claude 2.1, verbatim (Codex, review round 3, V2).
    "Your access token could not be refreshed. Please log out and sign in again.",
    "Not logged in \u00b7 Please run /login",
])
def test_narrow_credential_evidence_reaches_the_owner(detail):
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "exhausted",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid", detail=detail,
        )],
    )
    assert "reconnect" in _served_failure_notice(exc).lower(), detail


@pytest.mark.parametrize("detail", [
    "input exceeds the model's maximum token limit",
    # "401" inside a number is not an HTTP status (Codex, review round 3, V1).
    "maximum token limit: 140123 tokens",
    "request id 8401b2 timed out",
    "403 Forbidden: rate limit exceeded for this endpoint",
    "authorization server unreachable: connection refused",
    "",
])
def test_bare_substring_tells_do_not(detail):
    """"token", "403" and "auth" alone are what the classifier matched; none of
    these is a credential problem, and none may promise that reconnecting
    helps."""
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

    exc = AllProvidersExhaustedError(
        "exhausted",
        attempts=[ProviderAttemptDiagnostic(
            provider="codex", status="failed", skip_class="auth_invalid", detail=detail,
        )],
    )
    assert "reconnect" not in _served_failure_notice(exc).lower(), detail
