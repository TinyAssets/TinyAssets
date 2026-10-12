"""Deterministic coding_packet/release_packet rubric validator.

This implements ``openspec/specs/evaluation-outcomes-and-attribution/spec.md`` Phase 1 for the
KEEP criteria and anti-pattern checks. It is intentionally pure: no IO, no
network, no repo writes, and no LLM judgment.
"""

from __future__ import annotations

from typing import Any

KEEP_SCORE_MIN = 9.0

_KEEP_STATUSES = {"KEEP_READY", "AUTO_SHIP_READY"}
_KEEP_DECISIONS = {"KEEP"}
_APPROVED_RELEASE_GATES = {"APPROVE", "APPROVE_AUTO_SHIP"}
_ATTACHED_CLAIM_STATUSES = {
    "attached_completed",
    "child_attached_existing_receipt",
    "child_attached_with_handle",
    "child_invoked_with_handle",
}
_SHIP_CLAIM_LABELS = {"shipped", "ship", "merged", "auto_shipped"}


def _value(packet: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = packet.get(name)
        if value not in (None, ""):
            return value
    return None


def _coding_packet_status(packet: dict[str, Any]) -> Any:
    coding_packet = packet.get("coding_packet")
    if isinstance(coding_packet, dict):
        status = coding_packet.get("status")
        if status not in (None, ""):
            return status

    source_packet = packet.get("source_packet")
    if isinstance(source_packet, dict):
        source_coding_packet = source_packet.get("coding_packet")
        if isinstance(source_coding_packet, dict):
            status = source_coding_packet.get("status")
            if status not in (None, ""):
                return status

    return packet.get("coding_packet_status")


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _is_present(value: Any) -> bool:
    return value not in (None, "", {}, [])


def _claims_keep(packet: dict[str, Any]) -> bool:
    if _coding_packet_status(packet) in _KEEP_STATUSES:
        return True
    if packet.get("child_keep_reject_decision") in _KEEP_DECISIONS:
        return True
    if str(packet.get("outcome_label") or "").strip().lower() == "keep":
        return True
    return False


def _claims_shipped(packet: dict[str, Any]) -> bool:
    values = (
        packet.get("outcome_label"),
        packet.get("ship_status"),
        packet.get("release_outcome"),
    )
    return any(str(value or "").strip().lower() in _SHIP_CLAIM_LABELS for value in values)


def _recursion_limit_applied(packet: dict[str, Any]) -> bool:
    system = packet.get("__system__")
    if isinstance(system, dict) and _is_truthy(system.get("recursion_limit_applied")):
        return True
    return _is_truthy(packet.get("recursion_limit_applied"))


def _child_output_empty(packet: dict[str, Any]) -> bool:
    if "child_output" in packet:
        return not _is_present(packet.get("child_output"))
    output = packet.get("output")
    if isinstance(output, dict) and output.get("__system__"):
        non_system_keys = [key for key in output if key != "__system__"]
        return not non_system_keys
    return False


def _add_violation(
    violations: list[dict[str, Any]],
    *,
    rule_id: str,
    field: str,
    message: str,
) -> None:
    violations.append({
        "event": "rubric_violation",
        "rule_id": rule_id,
        "field": field,
        "severity": "block",
        "message": message,
    })


def validate_coding_packet_rubric(
    packet: dict[str, Any],
    *,
    keep_score_min: float = KEEP_SCORE_MIN,
) -> dict[str, Any]:
    """Validate a coding_packet/release_packet against rubric v0.

    Returns a deterministic decision dict with ``rubric_violation`` records.
    The validator does not perform side effects; callers decide whether to log,
    hold, send back, or merely annotate the packet.
    """
    if not isinstance(packet, dict):
        return {
            "validation_result": "blocked",
            "rubric_violation_count": 1,
            "violations": [{
                "event": "rubric_violation",
                "rule_id": "packet_not_dict",
                "field": None,
                "severity": "block",
                "message": f"packet must be a dict; got {type(packet).__name__}",
            }],
        }

    violations: list[dict[str, Any]] = []
    claims_keep = _claims_keep(packet)

    if claims_keep and _recursion_limit_applied(packet) and _child_output_empty(packet):
        _add_violation(
            violations,
            rule_id="recursion_limit_keep_claim",
            field="__system__.recursion_limit_applied",
            message=(
                "packet claims KEEP even though recursion_limit_applied produced "
                "empty child output"
            ),
        )

    if claims_keep:
        child_status = _value(packet, "child_run_status", "child-run.status", "run_status")
        if child_status != "completed":
            _add_violation(
                violations,
                rule_id="child_run_not_completed_for_keep",
                field="child_run_status",
                message=(
                    "KEEP requires child-run.status=completed; got "
                    f"{child_status!r}"
                ),
            )

        evidence_handle = _value(
            packet,
            "attached_child_evidence_handle",
            "stable_evidence_handle",
            "evidence_handle",
        )
        patch_packet = _value(
            packet,
            "child_candidate_patch_packet",
            "candidate_patch_packet",
        )
        if not _is_present(evidence_handle) or not _is_present(patch_packet):
            _add_violation(
                violations,
                rule_id="child_output_evidence_missing",
                field="attached_child_evidence_handle",
                message=(
                    "KEEP requires child-output evidence: a non-empty evidence "
                    "handle and child_candidate_patch_packet"
                ),
            )

        score = _value(packet, "release_score", "child_score", "score")
        if not isinstance(score, (int, float)) or score < keep_score_min:
            _add_violation(
                violations,
                rule_id="release_score_below_keep_threshold",
                field="release_score",
                message=(
                    f"KEEP requires release score >= {keep_score_min}; got "
                    f"{score!r}"
                ),
            )

        evidence_complete = _value(
            packet,
            "release_evidence_bundle_complete",
            "evidence_bundle_complete",
        )
        if evidence_complete is not True:
            _add_violation(
                violations,
                rule_id="release_evidence_bundle_incomplete",
                field="release_evidence_bundle_complete",
                message="KEEP requires release.evidence_bundle_complete=true",
            )

        gate = packet.get("release_gate_result")
        if gate not in _APPROVED_RELEASE_GATES:
            _add_violation(
                violations,
                rule_id="release_gate_not_approved_for_keep",
                field="release_gate_result",
                message=(
                    "KEEP requires release_gate_result in "
                    f"{sorted(_APPROVED_RELEASE_GATES)}; got {gate!r}"
                ),
            )

    automation_claim_status = str(packet.get("automation_claim_status") or "").strip()
    reason_for_downgrade = str(packet.get("reason_for_downgrade") or "").strip().lower()
    if (
        automation_claim_status in _ATTACHED_CLAIM_STATUSES
        and any(phrase in reason_for_downgrade for phrase in (
            "cannot attach",
            "cannot invoke",
            "could not attach",
            "could not invoke",
        ))
    ):
        _add_violation(
            violations,
            rule_id="contradictory_child_claim",
            field="automation_claim_status",
            message=(
                "automation_claim_status claims child attachment/invocation while "
                "reason_for_downgrade says the child packet could not be attached "
                "or invoked"
            ),
        )

    dispatcher_request_id = packet.get("dispatcher_request_id")
    run_id = packet.get("run_id")
    if dispatcher_request_id and run_id and dispatcher_request_id == run_id:
        _add_violation(
            violations,
            rule_id="dispatcher_request_id_used_as_run_id",
            field="run_id",
            message=(
                "dispatcher_request_id and run_id must be distinct evidence "
                "handles"
            ),
        )

    if _claims_shipped(packet) and not (
        _is_present(packet.get("commit_sha")) or _is_present(packet.get("pr_url"))
    ):
        _add_violation(
            violations,
            rule_id="shipped_without_repo_handle",
            field="commit_sha",
            message="shipped claims require a resolvable commit_sha or pr_url",
        )

    return {
        "validation_result": "blocked" if violations else "passed",
        "rubric_violation_count": len(violations),
        "violations": violations,
    }
