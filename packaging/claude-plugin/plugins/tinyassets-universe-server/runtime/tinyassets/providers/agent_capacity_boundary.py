"""Pure, closed evidence for a safe capacity transition; never launch authority.

The caller must additionally prove durable progress is quiescent and authorize
the next candidate. In particular, an empty native tool journal is NOT evidence
that a delegated agent did nothing. Missing execution telemetry stays unknown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
from tinyassets.providers.model_capacity import MAX_RETRY_SECONDS
from tinyassets.providers.model_policy import Exhaustion, ModelRef

_FAILURES = frozenset({
    "provider_credit_exhausted", "provider_rate_limited", "provider_overloaded",
    "provider_daily_quota",
})


@dataclass(frozen=True, slots=True)
class CapacityBoundary:
    exhaustion: Exhaustion
    attempted: bool
    failure_class: str | None
    retry_after_s: float | None
    #: What the SOURCE actually reported, before ``exhaustion`` collapses it to
    #: the conservative reading: ``model`` when every attempt said so,
    #: ``account`` when any attempt did, ``unknown`` when none of them could
    #: tell. Evidence, never a decision -- it exists so a caller can see WHY the
    #: exhaustion is account-wide instead of assuming a source proved it.
    observed_scope: str = "account"
    daily_detail: str = ""
    #: Seconds left on a cooldown the router skipped this source for, when
    #: that cooldown carried no reported cause of its own. The run's error says
    #: "cooling down" rather than an unexplained account-wide exhaustion, which
    #: read as a spent allowance on a source whose daily allowance was fresh.
    cooling_s: float | None = None
    #: The router's own words for that skip, naming the earlier failure class.
    cooling_detail: str = ""
    #: The source's own scrubbed words for a model it refused.
    refusal_detail: str = ""


@dataclass(frozen=True, slots=True)
class NativeCompletionEvidence:
    """Local adapter evidence for one reaped, completely observed native attempt.

    This value must be supplied by the installed executor, not remote metadata.
    A complete protocol can attest absence of effects only when every event is
    supported. Any unknown event, truncated stream or live child stays unproved.
    """

    provider: str
    protocol_complete: bool
    process_reaped: bool
    side_effect_state: str


def capacity_boundary(
    current: ModelRef, attempts: Any, *, execution_kind: str,
    native_evidence: Any = (),
) -> CapacityBoundary | None:
    """Return evidence only when every diagnostic permits this exact transition.

    A skipped quota gate is not relabelled as a remotely reported rate limit.
    Unknown scope excludes the account through the existing conservative policy;
    it never invents account independence. Callers cannot substitute exception
    summary fields for per-attempt evidence.
    """
    if execution_kind not in ("engine_inference", "native_agent"):
        return None
    if (type(current) is not ModelRef or type(current.connection_id) is not str
            or not current.connection_id or not current.connection_id.isprintable()
            or len(current.connection_id) > 4096 or type(current.model_id) is not str
            or len(current.model_id) > 4096
            or (current.model_id and not current.model_id.isprintable())):
        return None
    if type(attempts) not in (tuple, list) or not 1 <= len(attempts) <= 64:
        return None
    if type(native_evidence) is not tuple:
        return None
    if execution_kind == "native_agent":
        # Each proof belongs to that exact attempt slot; skipped slots need none.
        if len(native_evidence) != len(attempts):
            return None
    elif native_evidence:
        return None
    scopes = []
    delays = []
    failures = []
    attempted = False
    for index, item in enumerate(attempts):
        if (type(item) is not ProviderAttemptDiagnostic
                or item.provider != current.connection_id
                or item.status not in ("skipped", "failed")
                or item.capacity_scope not in (None, "model", "account", "unknown")
                or (item.failure_class is not None and type(item.failure_class) is not str)):
            return None
        if item.status == "skipped":
            if (item.skip_class != "quota_or_cooldown"
                    or item.side_effect_state not in (None, "none")
                    or item.failure_class not in (None, *_FAILURES)
                    or (execution_kind == "native_agent" and native_evidence[index] is not None)):
                return None
        else:
            if item.side_effect_state != "none" or item.failure_class not in _FAILURES:
                return None
            if execution_kind == "native_agent":
                proof = native_evidence[index]
                if (type(proof) is not NativeCompletionEvidence
                        or proof.provider != current.connection_id
                        or proof.protocol_complete is not True
                        or proof.process_reaped is not True
                        or proof.side_effect_state != "none"):
                    return None
            attempted = True
        delay = item.retry_after_s
        if delay is not None:
            if (type(delay) not in (int, float) or not 0 <= delay <= MAX_RETRY_SECONDS
                    or not math.isfinite(delay)):
                return None
            delays.append(delay)
        scopes.append(item.capacity_scope)
        if item.failure_class is not None:
            failures.append(item.failure_class)
    # Only unanimous model-local evidence allows a sibling on the same source.
    observed = (
        "model" if all(value == "model" for value in scopes)
        else "account" if any(value == "account" for value in scopes)
        else "unknown"
    )
    scope = "model" if observed == "model" else "account"
    from tinyassets.providers.diagnostics import redacted_failure_detail

    daily = next((redacted_failure_detail(a.detail) for a in reversed(attempts)
                  if a.failure_class == "provider_daily_quota"), "")
    cooling = [
        a for a in attempts
        if a.status == "skipped" and a.failure_class is None
        and type(a.cooldown_remaining_s) in (int, float)
    ]
    return CapacityBoundary(
        Exhaustion(scope, current), attempted, failures[-1] if failures else None,
        max(delays) if delays else None, observed, daily,
        max(a.cooldown_remaining_s for a in cooling) if cooling else None,
        redacted_failure_detail(cooling[-1].detail or "") if cooling else "",
    )


def refusal_boundary(current: ModelRef, attempts: Any) -> CapacityBoundary | None:
    """MODEL-scoped evidence that the source refused ``current`` before generating.

    ``None`` unless every attempt was that refusal. A refusal is a fact about
    one model on one source -- never the source's or the account's capacity --
    so its exhaustion is always ``model`` and siblings stay eligible.
    """
    if type(current) is not ModelRef or not uniform_pre_generation_failure(
        attempts, "provider_refused",
    ):
        return None
    from tinyassets.providers.diagnostics import redacted_failure_detail

    return CapacityBoundary(
        Exhaustion("model", current), True, "provider_refused", None, "model",
        refusal_detail=redacted_failure_detail(str(getattr(attempts[-1], "detail", "") or "")),
    )


def uniform_pre_generation_failure(attempts: Any, failure_class: str) -> bool:
    """Whether EVERY attempt failed with ``failure_class`` and none may have acted.

    The one test a conversation turn and a workflow run both apply before
    stepping past a refusal: ``provider_refused`` (the source will not serve
    THIS model, a model-scoped fact) or ``auth_invalid`` (the connection's
    sign-in, an account-scoped one). A round that also hit capacity is the
    capacity boundary's to read, and a round that may have committed an effect
    is never replayed on another model.
    """
    attempts = tuple(attempts or ())
    return bool(attempts) and all(
        getattr(item, "failure_class", None) == failure_class
        and getattr(item, "side_effect_state", "none") in ("", "none")
        for item in attempts
    )
