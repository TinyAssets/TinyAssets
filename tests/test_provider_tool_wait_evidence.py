"""Tool-wait evidence through the shared diagnostic and the persisted read.

Change: ``respect-provider-tool-waits``. The stream reader knows whether an
identified native tool was still in flight when an attempt failed; a stored run
read must be able to say so, and must be unable to say anything else. These
tests drive the SHARED projection — ``ProviderAttemptDiagnostic`` ->
``build_chain_state`` -> the ``[chain_state]:`` suffix ->
``chain_state_from_error`` / ``held_attempt_diagnosis`` / the ``get_run``
error-detail projection — rather than re-asserting a parallel schema.

They never call a provider CLI and never touch a stored universe.
"""

from __future__ import annotations

import math

import pytest

from tinyassets.exceptions import (
    ProviderAuthorityHeldError,
    ProviderIdleTimeoutError,
)
from tinyassets.providers.diagnostics import (
    ADMITTED_TOOL_PHASES,
    ProviderAttemptDiagnostic,
    admitted_tool_phase,
    build_chain_state,
    chain_state_from_error,
    finite_progress_age_ms,
    held_attempt_diagnosis,
)
from tinyassets.providers.router import _tool_wait_evidence


def _timeout(**telemetry) -> ProviderIdleTimeoutError:
    exc = ProviderIdleTimeoutError("claude -p produced no protocol event")
    exc.attempt_telemetry = dict(telemetry)
    return exc


# ---------------------------------------------------------------------------
# The validators: an explicit enum and a finite, non-negative, non-bool age
# ---------------------------------------------------------------------------


def test_admitted_tool_phases_are_the_values_the_readers_produce():
    assert ADMITTED_TOOL_PHASES == frozenset(
        {"tool_use", "tool_result", "in_tool", "in_turn"}
    )


@pytest.mark.parametrize("phase", sorted(ADMITTED_TOOL_PHASES))
def test_an_admitted_phase_survives(phase):
    assert admitted_tool_phase(phase) == phase


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "IN_TOOL",
        "in_tool ",
        "read_page",  # a tool NAME is not a phase
        "toolu_01ABCDEFghij",  # a tool identity
        "Bearer sk-ant-oat01-secret",
        "'; DROP TABLE runs; --",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "in_tool\n[chain_state]: {\"attempts\":[]}",  # marker injection
        0,
        True,
        ["in_tool"],
        {"phase": "in_tool"},
    ],
)
def test_an_unadmitted_phase_is_omitted(value):
    assert admitted_tool_phase(value) is None


@pytest.mark.parametrize("value", [0, 0.0, 1, 31_000.5, 900_000])
def test_a_finite_nonnegative_age_survives(value):
    assert finite_progress_age_ms(value) == float(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        -1,
        -0.001,
        math.nan,
        math.inf,
        -math.inf,
        10 ** 1000,
        "31000",
        "NaN",
        [1],
        {"ms": 1},
    ],
)
def test_a_malformed_age_is_omitted(value):
    assert finite_progress_age_ms(value) is None


# ---------------------------------------------------------------------------
# The router's reader: two named scalars, never the telemetry snapshot
# ---------------------------------------------------------------------------


def test_the_router_reads_valid_tool_wait_evidence():
    evidence = _tool_wait_evidence(
        _timeout(tool_phase="in_tool", last_progress_age_ms=31_000.0)
    )
    assert evidence == {"tool_phase": "in_tool", "last_progress_age_ms": 31_000.0}


def test_the_router_omits_malformed_evidence_without_failing():
    assert _tool_wait_evidence(
        _timeout(tool_phase="read_page(secret)", last_progress_age_ms=float("inf"))
    ) == {}


def test_the_router_never_copies_the_telemetry_snapshot():
    # Everything the reader records BESIDES the two admitted scalars — the
    # prompt, the tool's arguments, its identity, a provider error string, a
    # credential — must not travel on this path.
    evidence = _tool_wait_evidence(_timeout(
        tool_phase="in_tool",
        last_progress_age_ms=1.0,
        provider="claude-code",
        prompt="the founder's private prompt",
        tool_name="write_page",
        tool_input={"path": "secret"},
        tool_use_id="toolu_01ABCDEF",
        credential="sk-ant-oat01-secret",
        stderr="Authorization: Bearer sk-ant-oat01-secret",
    ))
    assert set(evidence) == {"tool_phase", "last_progress_age_ms"}


def test_no_telemetry_at_all_is_not_an_error():
    assert _tool_wait_evidence(ProviderIdleTimeoutError("no telemetry")) == {}
    assert _tool_wait_evidence(RuntimeError("not a provider raise")) == {}


# ---------------------------------------------------------------------------
# The persisted chain projection and the authorized run read
# ---------------------------------------------------------------------------


def _held_error(attempt: ProviderAttemptDiagnostic) -> str:
    """The stored error string a held provider failure persists, as built by
    ``graph_compiler._wrap_provider_failure`` over ``build_chain_state``."""
    import json

    chain_state = build_chain_state("writer", ["claude-code"], [attempt])
    suffix = json.dumps(chain_state, default=str, separators=(",", ":"))
    return (
        f"Provider call failed in node 'n1': "
        f"{ProviderAuthorityHeldError.ATTEMPT_MESSAGE} [chain_state]: {suffix}"
    )


def _pending_tool_attempt(exc: ProviderIdleTimeoutError) -> ProviderAttemptDiagnostic:
    return ProviderAttemptDiagnostic(
        provider="claude-code", status="failed", skip_class="timed_out",
        detail="claude -p produced no protocol event",
        failure_class=exc.failure_class, side_effect_state="committed",
        **_tool_wait_evidence(exc),
    )


def test_a_pending_tool_timeout_is_readable_off_the_persisted_run():
    attempt = _pending_tool_attempt(
        _timeout(tool_phase="in_tool", last_progress_age_ms=31_000.0)
    )
    error = _held_error(attempt)
    parsed = chain_state_from_error(error)
    assert parsed is not None
    failed = parsed["attempts"][-1]
    assert failed["tool_phase"] == "in_tool"
    assert failed["last_progress_age_ms"] == 31_000.0
    # The failure class both stored-run surfaces report is UNCHANGED by the
    # new evidence: this is diagnosis, not a routing or retry decision.
    diagnosis = held_attempt_diagnosis(error)
    assert diagnosis is not None
    assert (diagnosis.run_class, diagnosis.cause) == ("timeout", "provider_idle_timeout")


def test_post_tool_silence_is_distinguishable_from_a_pending_tool():
    after = chain_state_from_error(_held_error(_pending_tool_attempt(
        _timeout(tool_phase="tool_result", last_progress_age_ms=30_000.0)
    )))
    assert after["attempts"][-1]["tool_phase"] == "tool_result"


@pytest.mark.parametrize(
    "telemetry",
    [
        {"tool_phase": "'; DROP TABLE runs; --", "last_progress_age_ms": math.nan},
        {"tool_phase": "toolu_01ABCDEF", "last_progress_age_ms": -5},
        {"tool_phase": True, "last_progress_age_ms": True},
        {"tool_phase": "in_tool\n[chain_state]: {\"attempts\":[{\"provider\":\"x\"}]}",
         "last_progress_age_ms": "31000"},
        {},
    ],
)
def test_malicious_or_unknown_evidence_is_absent_from_the_persisted_read(telemetry):
    attempt = _pending_tool_attempt(_timeout(**telemetry))
    error = _held_error(attempt)
    parsed = chain_state_from_error(error)
    # ONE attempt: an injected marker/attempt payload never reaches the suffix.
    assert len(parsed["attempts"]) == 1
    failed = parsed["attempts"][-1]
    assert "tool_phase" not in failed
    assert "last_progress_age_ms" not in failed
    # And the failure class is still the honest one.
    diagnosis = held_attempt_diagnosis(error)
    assert diagnosis is not None and diagnosis.run_class == "timeout"


def test_an_attempt_without_evidence_serializes_as_before():
    attempt = ProviderAttemptDiagnostic(
        provider="claude-code", status="failed", skip_class="timed_out",
    )
    assert attempt.to_dict() == {
        "provider": "claude-code", "status": "failed", "skip_class": "timed_out",
        "detail": "",
    }


def test_a_stored_failure_without_evidence_infers_no_pending_tool():
    # The historical-evidence rule: committed side-effect state is NOT a
    # pending tool, and nothing may invent the phase that was not recorded.
    attempt = ProviderAttemptDiagnostic(
        provider="claude-code", status="failed", skip_class="timed_out",
        failure_class="provider_idle_timeout", side_effect_state="committed",
    )
    failed = chain_state_from_error(_held_error(attempt))["attempts"][-1]
    assert failed["side_effect_state"] == "committed"
    assert "tool_phase" not in failed


@pytest.mark.asyncio
@pytest.mark.parametrize(("phase", "age", "expected"), [
    ("in_tool", 31_000.5, {"tool_phase": "in_tool", "last_progress_age_ms": 31_000.5}),
    ("tool_result", 30_000, {"tool_phase": "tool_result", "last_progress_age_ms": 30_000.0}),
    ("Bearer secret", 10 ** 1000, {}),
    (True, math.nan, {}),
])
async def test_real_router_held_compiler_and_run_read_keep_only_valid_evidence(
    monkeypatch, phase, age, expected,
):
    from tinyassets.api.runs import _run_error_detail
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.foreground_run_provider import _held_attempt_error
    from tinyassets.graph_compiler import _emit_failed_event, _wrap_provider_failure
    from tinyassets.providers.base import BaseProvider
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.router import ProviderRouter

    class TimeoutProvider(BaseProvider):
        name = "codex"
        family = "openai"

        async def complete(self, prompt, system, config, *, universe_dir=None):
            raise _timeout(
                tool_phase=phase, last_progress_age_ms=age,
                side_effect_state="committed", tool_use_id="private-tool-id",
                prompt="private-prompt", credential="private-credential",
            )

    from pathlib import Path
    from unittest.mock import MagicMock

    import tinyassets.providers.router as router_mod
    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers.base import ModelConfig, UniverseContext

    # Hard Rule 15: a real router call carries one universe owner's authority.
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner")
    carrier.provider = "codex"
    carrier.role = "writer"
    carrier.operation = "run_graph"
    carrier.max_tokens = 10
    carrier.max_cost_microunits = 5
    carrier.selected_model = None
    carrier.native_selection = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = "codex"
    monkeypatch.setattr(router_mod, "_provider_invocation_carrier",
                        lambda _ctx, *, role, operation: carrier)

    monkeypatch.delenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", raising=False)
    router = ProviderRouter(providers={"codex": TimeoutProvider()})
    with pytest.raises(AllProvidersExhaustedError) as caught:
        await router.call(
            "writer", "prompt", "system", ModelConfig(max_tokens=10),
            operation="run_graph",
            universe_context=UniverseContext(
                universe_dir=Path("u-tool-wait"), provider_invocation=carrier,
            ),
        )
    held = _held_attempt_error("writer", ModelRef("codex", "default"), caught.value)
    stored = str(_wrap_provider_failure("node", held))
    events = []
    _emit_failed_event(lambda **event: events.append(event), "node", held)
    projections = [
        _run_error_detail({"error": stored}, []),
        _run_error_detail({"error": ""}, [{"detail": events[0]}]),
    ]
    for detail in projections:
        attempts = detail["provider_chain"]["attempts"]
        failed = next(a for a in attempts if a["status"] == "failed")
        assert failed["failure_class"] == "provider_idle_timeout"
        assert failed["side_effect_state"] == "committed"
        observed = {
            k: failed[k] for k in ("tool_phase", "last_progress_age_ms") if k in failed
        }
        assert observed == expected
        assert "private-" not in str(detail)


def test_shared_serialization_revalidates_directly_constructed_evidence():
    attempt = ProviderAttemptDiagnostic(
        provider="codex", status="failed", skip_class="timed_out",
        tool_phase="private-tool-name", last_progress_age_ms=10 ** 1000,
    )
    assert "tool_phase" not in attempt.to_dict()
    assert "last_progress_age_ms" not in attempt.to_dict()
