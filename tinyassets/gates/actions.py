"""Gate bonus business logic — stake / unstake / release helpers.

Pure business logic; callers pass an sqlite3.Connection and a pre-validated
GateBonusClaim.  No writes outside the passed connection.

Spec: openspec/specs/paid-market-economy/spec.md.
Topic: Gate bonuses — staked payouts attached to gate milestones.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from tinyassets.gates.schema import GateBonusClaim, migrate_gate_bonus_columns

# Default refund window (days from stake time).
DEFAULT_REFUND_DAYS: int = 30


def ensure_bonus_columns(conn: sqlite3.Connection) -> None:
    """Idempotent — adds bonus columns if absent."""
    migrate_gate_bonus_columns(conn)


def validate_stake_amount(bonus_stake: Any) -> tuple[int, str | None]:
    """Parse and validate bonus_stake.

    Returns (parsed_int, error_message_or_None).
    """
    try:
        stake = int(bonus_stake)
    except (TypeError, ValueError):
        return 0, f"bonus_stake must be an integer, got {bonus_stake!r}"
    if stake < 0:
        return 0, f"bonus_stake must be >= 0, got {stake}"
    return stake, None


def compute_bonus_payout(stake: int, *, treasury_take_bp: int = 100) -> tuple[int, int]:
    """Return (net_to_recipient, treasury_take) from a bonus stake.

    Uses floor division to avoid fractional tokens.
    Invariant: net + treasury == stake.
    """
    treasury = stake * treasury_take_bp // 10_000
    net = stake - treasury
    return net, treasury


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refund_after_iso(days: int = DEFAULT_REFUND_DAYS) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def stake_bonus(
    conn: sqlite3.Connection,
    *,
    claim_id: str,
    bonus_stake: int,
    node_id: str,
    staker_id: str,
    attachment_scope: str = "node",
    refund_days: int = DEFAULT_REFUND_DAYS,
) -> dict[str, Any]:
    """Lock a bonus stake on an existing gate claim.

    ``staker_id`` is the immutable owner-of-record for the bonus (the gate
    claimer at stake time). It is recorded in ``bonus_staker_id`` and is the
    sole authority for later unstake/refund — never the mutable ``claimed_by``,
    which a gate re-claim can overwrite (basing ownership on it would let a
    re-claimer steal the staked bonus).

    Returns a result dict; caller serializes.
    Rejects if claim is already retracted or already has a bonus staked.
    """
    ensure_bonus_columns(conn)

    if attachment_scope == "branch":
        return {
            "status": "rejected",
            "error": (
                "attachment_scope='branch' gate bonuses not yet implemented. "
                "See deferred spec in openspec/specs/paid-market-economy/spec.md."
            ),
        }
    if attachment_scope != "node":
        return {
            "status": "rejected",
            "error": f"attachment_scope must be 'node', got {attachment_scope!r}",
        }

    row = conn.execute(
        "SELECT * FROM gate_claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        return {"status": "rejected", "error": f"Claim '{claim_id}' not found."}

    claim = GateBonusClaim.from_row(row)

    if claim.is_retracted:
        return {
            "status": "rejected",
            "error": "Cannot stake bonus on a retracted claim.",
        }
    if claim.bonus_stake > 0:
        return {
            "status": "rejected",
            "error": (
                f"Claim already has a bonus_stake of {claim.bonus_stake}. "
                "Unstake first to replace it."
            ),
        }

    refund_after = _refund_after_iso(refund_days)
    # Atomic, owner-predicated write (slice1a review CRITICAL — round 5/6): the
    # stake only lands while the claim is STILL owned by ``staker_id``,
    # unretracted, and unstaked. This closes the check→write TOCTOU and records
    # the IMMUTABLE bonus_staker_id, so a later gate re-claim that rewrites
    # claimed_by can neither transfer nor steal the staked bonus.
    cur = conn.execute(
        """
        UPDATE gate_claims
           SET bonus_stake = ?,
               bonus_refund_after = ?,
               attachment_scope = ?,
               node_id = ?,
               bonus_staker_id = ?
         WHERE claim_id = ?
           AND claimed_by = ?
           AND retracted_at IS NULL
           AND bonus_stake = 0
        """,
        (bonus_stake, refund_after, attachment_scope, node_id, staker_id,
         claim_id, staker_id),
    )
    if cur.rowcount != 1:
        return {
            "status": "rejected",
            "error": (
                f"Claim {claim_id!r} changed concurrently (owner, retraction, "
                "or an existing stake) before the bonus could be staked; no "
                "stake was recorded."
            ),
        }
    return {
        "status": "ok",
        "claim_id": claim_id,
        "bonus_stake": bonus_stake,
        "attachment_scope": attachment_scope,
        "node_id": node_id,
        "bonus_refund_after": refund_after,
        "bonus_staker_id": staker_id,
    }


def unstake_bonus(
    conn: sqlite3.Connection,
    *,
    claim_id: str,
    actor: str,
) -> dict[str, Any]:
    """Remove a bonus stake, refunding to the immutable original staker.

    Only the recorded ``bonus_staker_id`` (the actor who staked it) can
    unstake, and only while the claim is not retracted. Authority is the
    IMMUTABLE staker-of-record, NOT the mutable ``claimed_by`` — a gate
    re-claim can overwrite ``claimed_by``, so a re-claimer must not be able to
    unstake (and pocket) another actor's bonus.
    """
    ensure_bonus_columns(conn)

    row = conn.execute(
        "SELECT * FROM gate_claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        return {"status": "rejected", "error": f"Claim '{claim_id}' not found."}

    claim = GateBonusClaim.from_row(row)

    if claim.is_retracted:
        return {
            "status": "rejected",
            "error": "Cannot unstake bonus on a retracted claim.",
        }
    if claim.bonus_stake == 0:
        return {
            "status": "rejected",
            "error": "No bonus staked on this claim.",
        }
    # Pre-migration rows have no bonus_staker_id; fall back to claimed_by so
    # legacy stakes remain unstakeable by their original claimer.
    effective_staker = (claim.bonus_staker_id or claim.claimed_by or "").strip()
    if actor != effective_staker:
        return {
            "status": "rejected",
            "error": (
                f"Only the original staker ('{effective_staker}') can unstake. "
                f"Actor '{actor}' is not authorized."
            ),
        }

    refunded = claim.bonus_stake
    # Compare-and-swap on the stake value read: a concurrent unstake/release
    # that already zeroed the stake makes this a no-op, so the refund is
    # reported at most once.
    cur = conn.execute(
        """
        UPDATE gate_claims
           SET bonus_stake = 0,
               bonus_refund_after = NULL,
               node_id = NULL,
               bonus_staker_id = NULL
         WHERE claim_id = ?
           AND bonus_stake = ?
        """,
        (claim_id, refunded),
    )
    if cur.rowcount != 1:
        return {
            "status": "rejected",
            "error": (
                f"Bonus on claim {claim_id!r} was already resolved by a "
                "concurrent unstake/release; no double refund."
            ),
        }
    return {
        "status": "ok",
        "claim_id": claim_id,
        "refunded": refunded,
        "refunded_to": effective_staker,
    }


def release_bonus(
    conn: sqlite3.Connection,
    *,
    claim_id: str,
    eval_verdict: str,
    node_last_claimer: str,
    staker: str | None = None,
) -> dict[str, Any]:
    """Release or refund a bonus based on the evaluator verdict.

    eval_verdict: "pass" → release to node_last_claimer.
                  "fail" or "skip" → refund to the ORIGINAL staker.

    Financial-integrity rule (slice1a review CRITICAL — rounds 3 + 5/6): a
    refund must return the stake to the actor who actually staked it, recorded
    IMMUTABLY on the claim (``bonus_staker_id``), NOT a caller-supplied
    ``staker`` and NOT the mutable ``claimed_by`` (a gate re-claim can rewrite
    claimed_by, which would otherwise let a re-claimer steal the refund).
    Pre-migration rows fall back to claimed_by. ``staker`` is only an OPTIONAL
    assertion: when provided it must match the recorded staker, otherwise the
    release is rejected — it never overrides the recorded staker.

    Returns a result dict describing the disbursement.
    Retracted claims are rejected before any payout or refund path runs.

    Authorization (who may call release at all — i.e. who adjudicates the gate
    outcome) is enforced by the caller (``_action_gates_release_bonus``):
    only the Goal owner, the configured host, or an actor holding the
    gate-claim capability may release/refund a bonus.
    """
    ensure_bonus_columns(conn)

    row = conn.execute(
        "SELECT * FROM gate_claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        return {"status": "rejected", "error": f"Claim '{claim_id}' not found."}

    claim = GateBonusClaim.from_row(row)

    # A retracted claim invalidates the attached bonus stake entirely.
    if claim.is_retracted:
        return {
            "status": "rejected",
            "error": "Cannot release bonus on a retracted claim.",
        }
    if claim.bonus_stake == 0:
        return {
            "status": "rejected",
            "error": "No bonus staked on this claim.",
        }

    if eval_verdict not in ("pass", "fail", "skip"):
        return {
            "status": "rejected",
            "error": (
                f"eval_verdict must be 'pass', 'fail', or 'skip'; got {eval_verdict!r}. "
                "Use the Evaluator protocol to obtain a verdict before releasing."
            ),
        }

    # The refund recipient is the IMMUTABLE recorded staker (bonus_staker_id),
    # never caller-supplied and never the mutable ``claimed_by`` (a gate
    # re-claim can overwrite claimed_by — see claim_gate — which would let a
    # re-claimer redirect another actor's refund to themselves). Pre-migration
    # rows fall back to claimed_by. A supplied ``staker`` is honored only as an
    # assertion that must match.
    recorded_staker = (claim.bonus_staker_id or claim.claimed_by or "").strip()
    if staker is not None and (staker or "").strip() != recorded_staker:
        return {
            "status": "rejected",
            "error": (
                f"staker {staker!r} does not match the recorded staker "
                f"{recorded_staker!r} for claim {claim_id!r}; a bonus refund "
                "always returns to the original staker."
            ),
        }

    stake = claim.bonus_stake
    net, treasury = compute_bonus_payout(stake)
    resolved_at = _now_iso()

    if eval_verdict == "pass":
        recipient = node_last_claimer
        disposition = "released"
    else:
        recipient = recorded_staker
        disposition = "refunded"

    # Atomically clear the bonus, gated on the stake still being exactly what we
    # read (compare-and-swap). Two concurrent release_bonus calls both read the
    # same nonzero stake, but only the first UPDATE matches (bonus_stake = stake);
    # the loser sees 0 rows changed and bails BEFORE any settlement is recorded,
    # so a bonus can never be double-settled (slice1a review CRITICAL — round 4).
    cur = conn.execute(
        "UPDATE gate_claims SET bonus_stake = 0, bonus_refund_after = NULL "
        "WHERE claim_id = ? AND bonus_stake = ?",
        (claim_id, stake),
    )
    if cur.rowcount != 1:
        return {
            "status": "rejected",
            "error": (
                f"Bonus on claim {claim_id!r} was already resolved by a "
                "concurrent release; refusing to double-settle."
            ),
        }

    result: dict[str, Any] = {
        "status": "ok",
        "claim_id": claim_id,
        "disposition": disposition,
        "eval_verdict": eval_verdict,
        "gross_stake": stake,
        "net_disbursed": net,
        "treasury_take": treasury,
        "recipient": recipient,
        "resolved_at": resolved_at,
    }

    # Record the disbursement in the canonical settlement ledger so the 1% take
    # actually credits the treasury + bounty pool and the treasury_status read
    # surface reflects real money flow. A "pass" is a value-moving settlement
    # (net to recipient, 1% to treasury); a "fail"/"skip" is a no-fee refund.
    if stake > 0:
        node_ref = getattr(claim, "node_id", None) or claim_id
        if eval_verdict == "pass":
            from tinyassets.payments.identifiers import SettlementKey
            from tinyassets.payments.settlement_ledger import record_settlement

            settlement_key = str(
                SettlementKey.build(claim_id, node_ref, recipient, "gate_bonus_release")
            )
            settlement = record_settlement(
                conn,
                settlement_key=settlement_key,
                recipient_id=recipient,
                gross_amount=stake,
                event_type="gate_bonus_release",
                now_iso=resolved_at,
                source_label=claim_id,
            )
            result["settlement_id"] = settlement["settlement_id"]
            result["bounty_share"] = settlement["bounty_share"]
            result["treasury_retained"] = settlement["treasury_retained"]
            result["ledger_recorded"] = True
        else:
            from tinyassets.payments.settlement_ledger import record_refund

            record_refund(
                conn,
                staker_id=recipient,
                amount=stake,
                now_iso=resolved_at,
                source_label=claim_id,
                event_type="gate_bonus_refund",
            )
            result["ledger_recorded"] = True

    return result
