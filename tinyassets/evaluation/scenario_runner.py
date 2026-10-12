"""AcceptanceScenario runtime — thin dispatcher to existing primitives.

Per `docs/ops/acceptance-probe-catalog.md` + spec
`docs/ops/acceptance-probe-catalog.md` (landed via
PR #936). Slice 2 of the AcceptanceScenario lane.

The runner is intentionally a thin dispatcher. It does NOT execute
user-sim sessions, branch runs, or MCP calls itself — it routes a typed
`AcceptanceScenario` contract to the appropriate existing runtime
(registered as a dispatcher per `target_surface`) and bundles the
result as a standard `EvalResult`.

Design constraints (per the audit verdict, REAFFIRMED):
- No new EvaluatorKind. Scenario results use the existing `custom` kind.
- No parallel runner. The dispatcher routes to existing primitives.
- No new sandbox. Universes register dispatchers that use their own
  sandbox model (typically the `external_tool_node` multi-layer auth
  surface for code, the ui-test single-tab discipline for browser
  scenarios).
- Cost budget enforcement is the dispatcher's responsibility. The
  runner verifies a budget was declared; the dispatcher honors it.
- Visibility is a tag carried on the result; universes compose
  enforcement via gates (per the PrivateTraceCommons pattern, #935).

Slice 3+ scope:
- Concrete dispatcher implementations per target_surface.
- Optimization-gate integration (route scenario PASS/FAIL into rung
  claim evidence per Goals & Gates).
- Community scenario library + remix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from tinyassets.evaluation import EvalResult, EvalVerdict

TargetSurface = Literal[
    "mcp_call",
    "ui_test_mission",
    "branch_run",
    "external_effect",
    "session_trace_summary",
]
PrivacyScope = Literal[
    "scenario_internal",
    "universe_only",
    "commons_publishable",
]
VALID_TARGET_SURFACES = frozenset(
    ("mcp_call", "ui_test_mission", "branch_run", "external_effect", "session_trace_summary")
)
VALID_PRIVACY_SCOPES = frozenset(
    ("scenario_internal", "universe_only", "commons_publishable")
)


@dataclass
class AcceptanceScenario:
    """Typed contract for a long-horizon acceptance check.

    11 fields per the Slice 1 spec; each justified by a concrete failure
    mode it prevents (see the design note).
    """

    scenario_id: str
    target_surface: TargetSurface
    user_story: str
    allowed_tools: list[str]
    evaluator_chain: list[str]
    artifact_requirements: list[dict]
    pass_threshold: dict
    cost_budget: dict
    privacy_scope: PrivacyScope
    idempotency_key_constructor: str
    setup: list[dict] = field(default_factory=list)
    supersedes_id: str = ""

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.scenario_id.startswith("scenario:"):
            raise ValueError(
                f"AcceptanceScenario.scenario_id must be non-empty and start "
                f"with 'scenario:' (got {self.scenario_id!r})"
            )
        if self.target_surface not in VALID_TARGET_SURFACES:
            raise ValueError(
                f"AcceptanceScenario.target_surface must be one of "
                f"{sorted(VALID_TARGET_SURFACES)} (got {self.target_surface!r})"
            )
        if self.privacy_scope not in VALID_PRIVACY_SCOPES:
            raise ValueError(
                f"AcceptanceScenario.privacy_scope must be one of "
                f"{sorted(VALID_PRIVACY_SCOPES)} (got {self.privacy_scope!r})"
            )
        if not 200 <= len(self.user_story) <= 2000:
            raise ValueError(
                f"AcceptanceScenario.user_story length must be in [200, 2000] "
                f"chars; got {len(self.user_story)}"
            )
        if not self.evaluator_chain:
            raise ValueError(
                "AcceptanceScenario.evaluator_chain must list at least one "
                "evaluator ID; scenarios without evaluators produce opinion, "
                "not evidence"
            )
        if not self.artifact_requirements:
            raise ValueError(
                "AcceptanceScenario.artifact_requirements must list at least "
                "one required artifact descriptor"
            )
        if "min_score" not in self.pass_threshold:
            raise ValueError(
                "AcceptanceScenario.pass_threshold must include 'min_score'"
            )
        for required_cost_field in ("max_tokens", "max_wall_time_seconds"):
            if required_cost_field not in self.cost_budget:
                raise ValueError(
                    f"AcceptanceScenario.cost_budget must include "
                    f"{required_cost_field!r} (bounded autonomous spend; "
                    f"see Brain Module principle)"
                )
        if not self.idempotency_key_constructor:
            raise ValueError(
                "AcceptanceScenario.idempotency_key_constructor must be "
                "declared at registration (per #914 external-write authority "
                "strict idempotency contract)"
            )


# Dispatcher = callable that executes a scenario against a candidate ref
# and returns a dict of {score, verdict, label, details}. The runner
# wraps that into a standard EvalResult.
#
# Signature:
#   dispatcher(scenario: AcceptanceScenario, candidate_ref: str, **kwargs)
#       -> dict[str, Any]
Dispatcher = Callable[..., dict[str, Any]]

_DISPATCHERS: dict[str, Dispatcher] = {}


def register_dispatcher(target_surface: str, dispatcher: Dispatcher) -> None:
    """Register a dispatcher for a target_surface.

    Universes register dispatchers at startup. The runner uses them; no
    dispatcher means scenarios for that surface return ``skip`` verdict
    with a clear note.
    """
    if target_surface not in VALID_TARGET_SURFACES:
        raise ValueError(
            f"unknown target_surface {target_surface!r}; valid: "
            f"{sorted(VALID_TARGET_SURFACES)}"
        )
    _DISPATCHERS[target_surface] = dispatcher


def unregister_dispatcher(target_surface: str) -> None:
    """Remove a registered dispatcher (primarily for tests)."""
    _DISPATCHERS.pop(target_surface, None)


def registered_dispatchers() -> dict[str, Dispatcher]:
    """Return a shallow copy of the dispatcher registry (read-only view)."""
    return dict(_DISPATCHERS)


def run_scenario(
    scenario: AcceptanceScenario,
    candidate_ref: str,
    **kwargs: Any,
) -> EvalResult:
    """Dispatch a scenario against a candidate; return a standard EvalResult.

    Args:
        scenario: validated AcceptanceScenario contract.
        candidate_ref: opaque reference to whatever is being tested (a
            branch_def_id, an MCP tool spec, a ui-test mission id, etc.).
        **kwargs: passed through to the dispatcher unchanged.

    Returns:
        A standard EvalResult with kind="custom". If no dispatcher is
        registered for the scenario's target_surface, returns a SKIP
        verdict with a clear note in details. If the dispatcher raises,
        returns an ERROR verdict with the exception text in details.
    """
    dispatcher = _DISPATCHERS.get(scenario.target_surface)
    if dispatcher is None:
        return EvalResult(
            score=-1.0,
            verdict="skip",
            kind="custom",
            label=f"scenario:{scenario.scenario_id}",
            details={
                "reason": "no_dispatcher_registered",
                "target_surface": scenario.target_surface,
                "scenario_id": scenario.scenario_id,
                "candidate_ref": candidate_ref,
                "registered_surfaces": sorted(_DISPATCHERS.keys()),
            },
        )

    try:
        raw = dispatcher(scenario, candidate_ref, **kwargs)
    except Exception as exc:  # noqa: BLE001 — surface any dispatcher error
        return EvalResult(
            score=-1.0,
            verdict="error",
            kind="custom",
            label=f"scenario:{scenario.scenario_id}",
            details={
                "reason": "dispatcher_raised",
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "target_surface": scenario.target_surface,
                "scenario_id": scenario.scenario_id,
                "candidate_ref": candidate_ref,
            },
        )

    # Normalize the dispatcher's return into an EvalResult.
    score = float(raw.get("score", 0.0))
    verdict_raw = raw.get("verdict")
    if verdict_raw in ("pass", "fail", "skip", "error"):
        verdict: EvalVerdict = verdict_raw
    else:
        # Derive PASS/FAIL from threshold if dispatcher didn't supply one.
        min_score = float(scenario.pass_threshold.get("min_score", 0.0))
        verdict = "pass" if score >= min_score else "fail"

    details = dict(raw.get("details", {}))
    details.setdefault("scenario_id", scenario.scenario_id)
    details.setdefault("target_surface", scenario.target_surface)
    details.setdefault("candidate_ref", candidate_ref)
    details.setdefault("privacy_scope", scenario.privacy_scope)
    details.setdefault("pass_threshold", scenario.pass_threshold)

    return EvalResult(
        score=max(-1.0, min(1.0, score)),
        verdict=verdict,
        kind="custom",
        label=raw.get("label", f"scenario:{scenario.scenario_id}"),
        details=details,
    )


__all__ = [
    "AcceptanceScenario",
    "Dispatcher",
    "PrivacyScope",
    "TargetSurface",
    "VALID_PRIVACY_SCOPES",
    "VALID_TARGET_SURFACES",
    "register_dispatcher",
    "registered_dispatchers",
    "run_scenario",
    "unregister_dispatcher",
]
