"""Shared outbound admission boundary for non-value-moving effects."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tinyassets.broker.connection_authority import ConnectionAuthority, read_authority
from tinyassets.broker.supervisor import broker_selected
from tinyassets.storage.external_write_receipts import (
    STATUS_FAILED,
    STATUS_HELD,
    STATUS_SUCCEEDED,
    confirm_held_receipt,
    finalize_receipt,
    finalize_reconciliation,
    lookup_receipt,
    try_activate_confirmed_hold,
    try_record_held_receipt,
    try_reserve_receipt,
)
from tinyassets.storage.outbound_connections import (
    AmbiguousProxyOutcome,
    ScopedConnectionProxy,
    evaluate_action_cap,
)

# Who is paying is decided by the billing module's own state, not by a second
# copy in the usage ledger. Two authorities for one fact is how the stale one
# ends up being the one that is read.


AmbiguousEffectOutcome = AmbiguousProxyOutcome


@dataclass(frozen=True)
class BatchEffectItem:
    item_id: str
    admit: Callable[[], tuple[bool, str]]
    execute: Callable[[], dict[str, Any]]


def confirm_held_effect(
    *,
    universe_dir: str | Path,
    ledger: ConnectionAuthority,
    grant_id: str,
    effect_key: str,
    sink: str,
    confirmed_at: float | None = None,
) -> dict[str, Any]:
    """Record explicit authorization on an existing held effect."""
    if broker_selected():
        try:
            authorized_by, grant, _ = read_authority(ledger, grant_id, universe_dir=universe_dir)
        except RuntimeError as exc:
            raise PermissionError("confirmation requires a current grant") from exc
    else:
        authorized_by = ledger.require_authenticated_principal_id()
        try:
            grant = ledger.require_active_grant(grant_id)
        except RuntimeError as exc:
            raise PermissionError("confirmation requires a current grant") from exc
    if grant.owner_user_id != authorized_by:
        raise PermissionError(
            "confirmation must come from the authenticated grant owner"
        )
    held = lookup_receipt(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
    )
    if held is None or held["status"] != STATUS_HELD:
        raise LookupError("held effect does not exist")
    held_decision = held["evidence"].get("held_decision")
    if not isinstance(held_decision, dict):
        raise PermissionError("held effect has no reviewable decision")
    decision_sha256 = _decision_sha256(held_decision)
    confirmation = {
        "authorized_by": authorized_by,
        "confirmed_at": time.time() if confirmed_at is None else confirmed_at,
        "decision_sha256": decision_sha256,
    }
    confirmed = confirm_held_receipt(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
        confirmation=confirmation,
        expected_grant_id=grant_id,
    )
    return {
        **confirmation,
        "held_decision": confirmed["held_decision"],
    }


def execute_capped_action(
    *,
    universe_dir: str | Path,
    ledger: ConnectionAuthority,
    grant_id: str,
    proxy: ScopedConnectionProxy,
    tool_authorized: bool,
    action_value: float,
    action_unit: str,
    effect_key: str,
    sink: str,
    run_id: str,
    verb: str,
    request: object,
) -> dict[str, Any]:
    """Execute an authorized below-cap action or persist an actionable hold."""
    if not tool_authorized:
        raise PermissionError("tool authorization is required")
    selected = broker_selected()
    if selected:
        try:
            principal_id, grant, _ = read_authority(ledger, grant_id, universe_dir=universe_dir)
        except RuntimeError as exc:
            raise PermissionError("effect requires a current grant") from exc
    else:
        principal_id = ledger.require_authenticated_principal_id()
        try:
            grant = ledger.require_active_grant(grant_id)
        except RuntimeError as exc:
            raise PermissionError("effect requires a current grant") from exc
    if grant.owner_user_id != principal_id:
        raise PermissionError(
            "effect requires the authenticated grant owner"
        )
    if proxy.grant_id != grant_id:
        raise PermissionError("proxy grant does not match the evaluated grant")
    decision = evaluate_action_cap(
        grant.unprompted_action_cap,
        action_value=action_value,
        action_unit=action_unit,
    ) if selected else ledger.evaluate_unprompted_action_cap(
        grant_id=grant_id,
        action_value=action_value,
        action_unit=action_unit,
    )
    existing = lookup_receipt(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
    )
    cap_payload = (
        decision.cap.as_dict() if decision.cap is not None else None
    )
    held_decision = _held_decision(
        verb=verb,
        request=request,
        action_value=action_value,
        action_unit=action_unit,
        cap=cap_payload,
    )
    if existing is not None and existing["status"] in (
        STATUS_HELD,
        STATUS_FAILED,
    ):
        _require_matching_held_decision(existing["evidence"], held_decision)
    elif (
        existing is not None
        and existing["status"] == STATUS_SUCCEEDED
        and "held_decision" in existing["evidence"]
    ):
        _require_matching_held_decision(existing["evidence"], held_decision)
    if existing is not None and existing["status"] == STATUS_SUCCEEDED:
        return _format_capped_success(
            existing["evidence"],
            cap_payload,
            replay=True,
        )
    confirmed = bool(
        existing is not None
        and existing["status"] in (STATUS_HELD, STATUS_FAILED)
        and existing["evidence"].get("grant_id") == grant_id
        and existing["evidence"].get("confirmation")
    )
    if decision.status == "held" and not confirmed:
        assert decision.cap is not None
        remediation = {
            "action": "confirm_held_effect",
            "effect_key": effect_key,
            "message": (
                f"Confirm this effect to exceed cap {decision.cap.name!r}."
            ),
        }
        evidence = {
            "status": STATUS_HELD,
            "grant_id": grant_id,
            "cap": decision.cap.as_dict(),
            "action_value": action_value,
            "action_unit": action_unit,
            "held_decision": held_decision,
            "consumption": {"funds": 0, "quota": 0},
            "remediation": remediation,
        }
        held = try_record_held_receipt(
            universe_dir,
            idempotency_hint=effect_key,
            sink=sink,
            evidence=evidence,
            run_id=run_id,
        )
        current = held["row"]
        if held["status"] == "held_created":
            return evidence
        if current["status"] == STATUS_SUCCEEDED:
            return _format_capped_success(
                current["evidence"],
                cap_payload,
                replay=True,
            )
        if current["status"] == STATUS_HELD:
            if current["evidence"].get("grant_id") != grant_id:
                raise PermissionError("held effect belongs to a different grant")
            return {**current["evidence"], "status": STATUS_HELD, "replay": True}
        raise RuntimeError(
            f"capped effect journal is not safely holdable: {current['status']}"
        )

    if confirmed:
        activation = try_activate_confirmed_hold(
            universe_dir,
            idempotency_hint=effect_key,
            sink=sink,
            run_id=run_id,
            expected_grant_id=grant_id,
        )
        if activation["status"] == "duplicate":
            return _format_capped_success(
                activation["row"]["evidence"],
                cap_payload,
                replay=True,
            )
        if activation["status"] != "reserved":
            raise RuntimeError(
                "confirmed effect could not acquire its execution journal: "
                f"{activation['status']}"
            )
        result = _invoke_reserved_effect(
            universe_dir=universe_dir,
            effect_key=effect_key,
            sink=sink,
            run_id=run_id,
            invoke=lambda: proxy.request(verb, request),
            reconcile=None,
            base_evidence={
                "confirmation": activation["row"]["evidence"]["confirmation"],
                "grant_id": grant_id,
                "held_decision": activation["row"]["evidence"]["held_decision"],
            },
        )
    else:
        result = execute_replay_safe_effect(
            universe_dir=universe_dir,
            effect_key=effect_key,
            sink=sink,
            run_id=run_id,
            invoke=lambda: proxy.request(verb, request),
        )
    if result["status"] != STATUS_SUCCEEDED:
        return {**result, "cap": cap_payload}
    return _format_capped_success(result, cap_payload, replay=result["replay"])


def _held_decision(
    *,
    verb: str,
    request: object,
    action_value: float,
    action_unit: str,
    cap: dict[str, object] | None,
) -> dict[str, Any]:
    """Copy the complete executable decision into the redacted JSON contract."""
    decision = {
        "verb": verb,
        "request": request,
        "action_value": action_value,
        "action_unit": action_unit,
        "cap": cap,
    }
    try:
        return json.loads(_canonical_json(decision))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "held effect decision must use the redacted JSON contract"
        ) from exc


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decision_sha256(decision: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(decision).encode("utf-8")).hexdigest()


def _require_matching_held_decision(
    evidence: dict[str, Any],
    current: dict[str, Any],
) -> None:
    stored = evidence.get("held_decision")
    if not isinstance(stored, dict):
        raise PermissionError("held decision is missing; refusing replay")
    if _canonical_json(stored) != _canonical_json(current):
        raise PermissionError(
            "confirmed replay does not match the held decision"
        )
    confirmation = evidence.get("confirmation")
    if confirmation:
        if not isinstance(confirmation, dict):
            raise PermissionError("held decision confirmation is invalid")
        if confirmation.get("decision_sha256") != _decision_sha256(stored):
            raise PermissionError(
                "held decision confirmation is not bound to the decision"
            )


def _format_capped_success(
    evidence: dict[str, Any],
    cap: dict[str, object] | None,
    *,
    replay: bool,
) -> dict[str, Any]:
    return {
        **evidence,
        "status": "executed",
        "cap": cap,
        "replay": replay,
    }


def execute_replay_safe_effect(
    *,
    universe_dir: str | Path,
    effect_key: str,
    sink: str,
    run_id: str,
    invoke: Any,
    reconcile: Any | None = None,
    max_failed_retries: int | None = None,
    reservation_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Journal before fire and reconcile every ambiguous/pending replay."""
    if not effect_key.strip():
        raise ValueError("effect_key must be non-empty")
    reservation = try_reserve_receipt(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
        run_id=run_id,
        max_failed_retries=max_failed_retries,
        reservation_evidence=reservation_evidence,
    )
    status = reservation["status"]
    if status == "duplicate":
        evidence = dict(reservation["row"]["evidence"])
        return {**evidence, "status": STATUS_SUCCEEDED, "replay": True}
    if status == "held":
        evidence = dict(reservation["row"]["evidence"])
        return {**evidence, "status": STATUS_HELD, "replay": True}
    if status == "reconciliation_required":
        return _reconcile_effect(
            universe_dir=universe_dir,
            effect_key=effect_key,
            sink=sink,
            run_id=run_id,
            reconcile=reconcile,
            base_evidence=dict(reservation["row"]["evidence"]),
            track_failed_attempts=max_failed_retries is not None,
        )
    if status == "retry_exhausted":
        evidence = dict(reservation["row"]["evidence"])
        return {
            **evidence,
            "status": STATUS_FAILED,
            "terminal": True,
            "reason": "retry_limit_exhausted",
            "replay": True,
        }
    if status not in ("reserved", "reserved_after_failed"):
        raise RuntimeError(f"effect reservation failed closed: {status}")

    return _invoke_reserved_effect(
        universe_dir=universe_dir,
        effect_key=effect_key,
        sink=sink,
        run_id=run_id,
        invoke=invoke,
        reconcile=reconcile,
        base_evidence=(
            dict(reservation["row"]["evidence"])
            if status == "reserved_after_failed"
            else None
        ),
        track_failed_attempts=max_failed_retries is not None,
    )


def _invoke_reserved_effect(
    *,
    universe_dir: str | Path,
    effect_key: str,
    sink: str,
    run_id: str,
    invoke: Any,
    reconcile: Any | None,
    base_evidence: dict[str, Any] | None = None,
    track_failed_attempts: bool = False,
) -> dict[str, Any]:
    """Invoke only after the caller atomically acquired the pending journal."""
    preserved = dict(base_evidence or {})

    try:
        destination_result = invoke()
    except AmbiguousEffectOutcome:
        return _reconcile_effect(
            universe_dir=universe_dir,
            effect_key=effect_key,
            sink=sink,
            run_id=run_id,
            reconcile=reconcile,
            base_evidence=preserved,
            track_failed_attempts=track_failed_attempts,
        )
    except Exception as exc:
        evidence = {
            **preserved,
            "status": STATUS_FAILED,
            "terminal": True,
            "reason": "destination_rejected",
            "error_type": type(exc).__name__,
            "replay": False,
        }
        if track_failed_attempts:
            evidence["failed_attempts"] = int(
                preserved.get("failed_attempts", 0)
            ) + 1
        finalized = finalize_receipt(
            universe_dir,
            idempotency_hint=effect_key,
            sink=sink,
            evidence=evidence,
            run_id=run_id,
            status=STATUS_FAILED,
        )
        if not finalized:
            raise RuntimeError("failed effect could not finalize its journal")
        # The destination rejected it, so nothing reached the world: refund.
        return evidence

    evidence = {
        **preserved,
        "status": STATUS_SUCCEEDED,
        "terminal": True,
        "result": destination_result,
        "replay": False,
    }
    finalized = finalize_receipt(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
        evidence=evidence,
        run_id=run_id,
        status=STATUS_SUCCEEDED,
    )
    if not finalized:
        raise RuntimeError("successful effect could not finalize its journal")
    # Reached the world: spend the reserved slot. settle is transition-sensitive,
    # so a later reconciliation or replay of this same effect settles nothing.
    return evidence


def _reconcile_effect(
    *,
    universe_dir: str | Path,
    effect_key: str,
    sink: str,
    run_id: str,
    reconcile: Any | None,
    base_evidence: dict[str, Any] | None = None,
    track_failed_attempts: bool = False,
) -> dict[str, Any]:
    preserved = dict(base_evidence or {})
    if reconcile is None:
        evidence = {
            **preserved,
            "status": STATUS_HELD,
            "terminal": True,
            "reason": "reconciliation_unavailable",
            "remediation": {
                "action": "inspect_destination_then_resolve",
                "effect_key": effect_key,
                "message": (
                    "Inspect the destination, then explicitly resolve this "
                    "effect before retrying."
                ),
            },
            "reconciled": False,
        }
        _persist_reconciliation(
            universe_dir,
            effect_key,
            sink,
            run_id,
            evidence,
            STATUS_HELD,
        )
        return evidence

    try:
        result = reconcile(effect_key)
    except Exception:
        result = {"status": "unknown"}
    result_status = result.get("status") if isinstance(result, dict) else None
    if result_status not in (STATUS_SUCCEEDED, STATUS_FAILED):
        evidence = {
            **preserved,
            "status": STATUS_HELD,
            "terminal": True,
            "reason": "reconciliation_inconclusive",
            "remediation": {
                "action": "inspect_destination_then_resolve",
                "effect_key": effect_key,
                "message": (
                    "Destination reconciliation was inconclusive; inspect "
                    "the destination before retrying."
                ),
            },
            "reconciled": False,
        }
        _persist_reconciliation(
            universe_dir,
            effect_key,
            sink,
            run_id,
            evidence,
            STATUS_HELD,
        )
        return evidence

    destination_evidence = result.get("evidence")
    if not isinstance(destination_evidence, dict):
        destination_evidence = {}
    evidence = {
        **preserved,
        **destination_evidence,
        "status": result_status,
        "terminal": True,
        "reconciled": True,
        "replay": False,
    }
    if track_failed_attempts and result_status == STATUS_FAILED:
        evidence["failed_attempts"] = int(
            preserved.get("failed_attempts", 0)
        ) + 1
    _persist_reconciliation(
        universe_dir,
        effect_key,
        sink,
        run_id,
        evidence,
        result_status,
    )
    return evidence


def _persist_reconciliation(
    universe_dir: str | Path,
    effect_key: str,
    sink: str,
    run_id: str,
    evidence: dict[str, Any],
    status: str,
) -> None:
    if not finalize_reconciliation(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
        evidence=evidence,
        run_id=run_id,
        status=status,
    ):
        current = lookup_receipt(
            universe_dir,
            idempotency_hint=effect_key,
            sink=sink,
        )
        if current is None or current["status"] != status:
            raise RuntimeError("reconciliation could not finalize its journal")


def hold_unreconciled_pending(
    *,
    universe_dir: str | Path,
    effect_key: str,
    sink: str,
    run_id: str,
) -> dict[str, Any]:
    """Persist the safe hold for an adapter with no reconciliation interface."""
    return _reconcile_effect(
        universe_dir=universe_dir,
        effect_key=effect_key,
        sink=sink,
        run_id=run_id,
        reconcile=None,
        base_evidence=None,
    )


def hold_receipt_finalization_failure(
    *,
    universe_dir: str | Path,
    effect_key: str,
    sink: str,
    run_id: str,
    destination_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Persist a visible terminal hold when success cannot finalize its journal."""
    evidence = {
        **destination_evidence,
        "status": STATUS_HELD,
        "terminal": True,
        "reason": "receipt_finalize_failed",
        "remediation": {
            "action": "inspect_destination_then_resolve",
            "effect_key": effect_key,
            "message": (
                "The destination reported success but receipt finalization "
                "lost its journal race; inspect the destination and resolve "
                "the held effect before retrying."
            ),
        },
    }
    if not finalize_reconciliation(
        universe_dir,
        idempotency_hint=effect_key,
        sink=sink,
        evidence=evidence,
        run_id=run_id,
        status=STATUS_HELD,
    ):
        raise RuntimeError(
            "receipt finalization failed and its remediation hold "
            "could not be persisted"
        )
    return evidence


def execute_effect_batch(items: list[BatchEffectItem]) -> dict[str, Any]:
    """Admit as a whole, stop after failure, and report every item explicitly."""
    admissions: list[tuple[bool, str]] = []
    for item in items:
        try:
            admissions.append(item.admit())
        except Exception:
            admissions.append((False, "admission check failed"))
    if any(not admitted for admitted, _reason in admissions):
        return {
            "status": STATUS_HELD,
            "rollback_claimed": False,
            "items": [
                {
                    "item_id": item.item_id,
                    "status": STATUS_HELD if not admitted else "not_fired",
                    "reason": reason if not admitted else "batch admission failed",
                }
                for item, (admitted, reason) in zip(
                    items,
                    admissions,
                    strict=True,
                )
            ],
        }

    outcomes: list[dict[str, Any]] = []
    successful_statuses = {STATUS_SUCCEEDED, "executed", "replayed"}
    for index, item in enumerate(items):
        try:
            effect_result = item.execute()
        except Exception:
            effect_result = {
                "status": STATUS_FAILED,
                "reason": "effect execution failed",
            }
        if not isinstance(effect_result, dict):
            effect_result = {
                "status": STATUS_FAILED,
                "reason": "effect returned an invalid result",
            }
        outcome = {"item_id": item.item_id, **effect_result}
        outcomes.append(outcome)
        if outcome.get("status") in successful_statuses:
            continue

        for prior in outcomes[:-1]:
            prior["reason"] = "terminal effect not reversed"
            prior["disposition"] = "terminal_not_reversed"
        outcomes.extend(
            {
                "item_id": remaining.item_id,
                "status": "not_fired",
                "reason": f"nothing further fired after {item.item_id}",
            }
            for remaining in items[index + 1 :]
        )
        return {
            "status": STATUS_HELD,
            "rollback_claimed": False,
            "items": outcomes,
        }
    return {
        "status": STATUS_SUCCEEDED,
        "rollback_claimed": False,
        "items": outcomes,
    }


__all__ = [
    "AmbiguousEffectOutcome",
    "BatchEffectItem",
    "confirm_held_effect",
    "execute_capped_action",
    "execute_effect_batch",
    "execute_replay_safe_effect",
    "hold_unreconciled_pending",
    "hold_receipt_finalization_failure",
]
