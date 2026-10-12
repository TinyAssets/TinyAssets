"""Escrow MCP action business logic — lock / release / refund / inspect.

Pure business logic; callers pass an sqlite3.Connection and pre-validated args.
No writes outside the passed connection. PAID_MARKET gate enforced by callers.

Spec: openspec/specs/paid-market-economy/spec.md.
Topic: Gate bonuses — escrow stays on the node, not the attempt.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from tinyassets.payments.escrow import (
    DuplicateLockError,
    EscrowLock,
    LockAlreadyResolvedError,
    LockNotFoundError,
    get_lock,
    get_lock_for_claim,
    list_locks_for_claim,
    lock_bonus,
    migrate_escrow_schema,
    refund_bonus,
    release_bonus,
)
from tinyassets.payments.funding import (
    FundingError,
    InsufficientFundsError,
    canonical_currency,
    credit_balance,
    debit_reserved,
    get_balance,
    release_reservation,
    reserve,
    withdraw_balance,
)
from tinyassets.payments.identifiers import SettlementKey
from tinyassets.payments.settlement_backend import (
    BASE_SEPOLIA_CHAIN_ID,
    SettlementBackendError,
    get_settlement_backend,
    stable_idempotency_key,
)
from tinyassets.payments.settlement_ledger import record_settlement
from tinyassets.payments.wallets import (
    WalletError,
    get_payout_wallet,
    set_payout_wallet,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_lock_id() -> str:
    return f"lock-{uuid.uuid4().hex}"


def ensure_escrow_schema(conn: sqlite3.Connection) -> None:
    """Idempotent — creates escrow_locks table if absent."""
    migrate_escrow_schema(conn)


# ── escrow_lock ───────────────────────────────────────────────────────────────

def action_escrow_lock(
    conn: sqlite3.Connection,
    *,
    node_id: str,
    amount: int,
    claimer: str,
    currency: str = "MicroToken",
) -> dict[str, Any]:
    """Lock funds from claimer's budget for a node request.

    One lock per (node_id, claimer) at a time. Rejects duplicate locks.
    Returns lock record on success.
    """
    ensure_escrow_schema(conn)

    if not node_id:
        return {"status": "rejected", "error": "node_id is required."}
    if not claimer:
        return {"status": "rejected", "error": "claimer is required."}
    if amount <= 0:
        return {
            "status": "rejected",
            "error": f"amount must be > 0, got {amount!r}.",
        }
    if currency not in ("MicroToken", "token"):
        return {
            "status": "rejected",
            "error": f"currency must be 'MicroToken' or 'token', got {currency!r}.",
        }

    lock_id = _generate_lock_id()
    locked_at = _now_iso()
    cur = canonical_currency(currency)

    # Reserve funded budget before creating the lock — escrow cannot be minted
    # from nothing. Insufficient spendable funds reject without a lock.
    try:
        reserve(
            conn,
            staker_id=claimer,
            amount=amount,
            now_iso=locked_at,
            currency=cur,
        )
    except InsufficientFundsError as exc:
        return {"status": "rejected", "error": str(exc)}

    try:
        lock = lock_bonus(
            conn,
            lock_id=lock_id,
            gate_claim_id=node_id,
            staker_id=claimer,
            amount=amount,
            locked_at=locked_at,
            currency=cur,
        )
    except DuplicateLockError:
        # Undo the reservation we just made so it is not orphaned.
        release_reservation(
            conn,
            staker_id=claimer,
            amount=amount,
            now_iso=locked_at,
            currency=cur,
        )
        existing = get_lock_for_claim(conn, gate_claim_id=node_id, staker_id=claimer)
        return {
            "status": "rejected",
            "error": (
                f"An escrow lock already exists for node_id={node_id!r} "
                f"claimer={claimer!r}. Refund or release the existing lock first."
            ),
            "existing_lock_id": existing.lock_id if existing else None,
        }

    return {
        "status": "ok",
        "lock_id": lock.lock_id,
        "node_id": node_id,
        "amount": lock.amount,
        "currency": lock.currency,
        "claimer": claimer,
        "locked_at": lock.locked_at,
    }


# ── escrow_release ────────────────────────────────────────────────────────────

def action_escrow_release(
    conn: sqlite3.Connection,
    *,
    lock_id: str,
    recipient_id: str,
    evidence: str = "",
    caller_id: str | None = None,
    host_id: str | None = None,
) -> dict[str, Any]:
    """Release escrow to recipient_id on completion verdict.

    Only works on locks in 'locked' status. One-way transition.

    Financial-integrity rule (slice1a review CRITICAL 1): a release moves the
    staker's locked funds to a recipient. Only the staker who owns the lock
    (or the configured host acting on their behalf) may release it — an
    arbitrary caller cannot release another actor's lock or redirect the funds.
    Pass ``caller_id`` (the authenticated actor) to enforce this; when
    ``caller_id`` is None the check is skipped (pure-unit / internal callers).
    """
    ensure_escrow_schema(conn)

    if not lock_id:
        return {"status": "rejected", "error": "lock_id is required."}
    if not recipient_id:
        return {"status": "rejected", "error": "recipient_id is required."}

    # Authorization: resolve the lock first so we can check ownership before any
    # state transition. release_bonus would also raise on a missing lock, but we
    # need the staker_id here to authorize.
    if caller_id is not None:
        existing = get_lock(conn, lock_id)
        if existing is None:
            return {
                "status": "rejected",
                "error": f"No escrow lock with lock_id={lock_id!r}.",
            }
        owner = (existing.staker_id or "").strip()
        caller = (caller_id or "").strip()
        host = (host_id or "").strip()
        if caller != owner and (not host or caller != host):
            return {
                "status": "rejected",
                "error": (
                    f"Cross-actor escrow release is not permitted: caller "
                    f"{caller!r} does not own lock {lock_id!r} (staker "
                    f"{owner!r}). Only the staker (or host) may release it."
                ),
            }

    try:
        lock = release_bonus(
            conn,
            lock_id=lock_id,
            recipient_id=recipient_id,
            resolved_at=_now_iso(),
        )
    except LockNotFoundError:
        return {
            "status": "rejected",
            "error": f"No escrow lock with lock_id={lock_id!r}.",
        }
    except LockAlreadyResolvedError as exc:
        return {"status": "rejected", "error": str(exc)}

    # Money loop: the staker's reservation becomes a permanent debit, the gross
    # is settled (net to recipient, 1% to treasury via record_settlement), and
    # the recipient's spendable budget is credited the net so earnings can be
    # re-staked.
    cur = canonical_currency(lock.currency)
    resolved_at = lock.resolved_at or _now_iso()
    debit_reserved(
        conn,
        staker_id=lock.staker_id,
        amount=lock.amount,
        now_iso=resolved_at,
        currency=cur,
    )
    settlement = record_settlement(
        conn,
        settlement_key=str(
            SettlementKey.build(
                lock.lock_id, lock.gate_claim_id, recipient_id, "escrow_release"
            )
        ),
        recipient_id=recipient_id,
        gross_amount=lock.amount,
        event_type="escrow_release",
        now_iso=resolved_at,
        source_label=lock.lock_id,
    )
    credit_balance(
        conn,
        staker_id=recipient_id,
        amount=settlement["net_amount"],
        now_iso=resolved_at,
        currency=cur,
    )

    result: dict[str, Any] = {
        "status": "ok",
        "lock_id": lock.lock_id,
        "disposition": "released",
        "amount": lock.amount,
        "currency": cur,
        "recipient_id": lock.recipient_id,
        "resolved_at": lock.resolved_at,
        "net_amount": settlement["net_amount"],
        "treasury_fee": settlement["treasury_fee"],
        "bounty_share": settlement["bounty_share"],
        "treasury_retained": settlement["treasury_retained"],
        "settlement_id": settlement["settlement_id"],
    }
    if evidence:
        result["evidence"] = evidence
    return result


# ── escrow_refund ─────────────────────────────────────────────────────────────

def action_escrow_refund(
    conn: sqlite3.Connection,
    *,
    lock_id: str,
    reason: str = "",
    caller_id: str | None = None,
    host_id: str | None = None,
) -> dict[str, Any]:
    """Refund escrow back to staker on abandonment or rejection.

    Only works on locks in 'locked' status. One-way transition.

    Financial-integrity rule (slice1a review CRITICAL — round 2): a refund
    cancels the staker's escrow lock and returns the reserved funds. Only the
    staker who owns the lock (or the configured host acting on their behalf)
    may refund it — an arbitrary write-scoped caller who knows another actor's
    ``lock_id`` cannot cancel their escrow. Pass ``caller_id`` (the
    authenticated actor) to enforce this; when ``caller_id`` is None the check
    is skipped (pure-unit / internal callers), matching ``action_escrow_release``.
    """
    ensure_escrow_schema(conn)

    if not lock_id:
        return {"status": "rejected", "error": "lock_id is required."}

    # Authorization: resolve the lock first so we can check ownership before any
    # state transition. refund_bonus would also raise on a missing lock, but we
    # need the staker_id here to authorize.
    if caller_id is not None:
        existing = get_lock(conn, lock_id)
        if existing is None:
            return {
                "status": "rejected",
                "error": f"No escrow lock with lock_id={lock_id!r}.",
            }
        owner = (existing.staker_id or "").strip()
        caller = (caller_id or "").strip()
        host = (host_id or "").strip()
        if caller != owner and (not host or caller != host):
            return {
                "status": "rejected",
                "error": (
                    f"Cross-actor escrow refund is not permitted: caller "
                    f"{caller!r} does not own lock {lock_id!r} (staker "
                    f"{owner!r}). Only the staker (or host) may refund it."
                ),
            }

    try:
        lock = refund_bonus(conn, lock_id=lock_id, resolved_at=_now_iso())
    except LockNotFoundError:
        return {
            "status": "rejected",
            "error": f"No escrow lock with lock_id={lock_id!r}.",
        }
    except LockAlreadyResolvedError as exc:
        return {"status": "rejected", "error": str(exc)}

    # Money loop: refund releases the staker's reservation back to spendable;
    # no value moves to the platform, so no settlement and no treasury fee.
    cur = canonical_currency(lock.currency)
    release_reservation(
        conn,
        staker_id=lock.staker_id,
        amount=lock.amount,
        now_iso=lock.resolved_at or _now_iso(),
        currency=cur,
    )

    result: dict[str, Any] = {
        "status": "ok",
        "lock_id": lock.lock_id,
        "disposition": "refunded",
        "amount": lock.amount,
        "currency": cur,
        "refunded_to": lock.staker_id,
        "resolved_at": lock.resolved_at,
    }
    if reason:
        result["reason"] = reason
    return result


# ── escrow_inspect ────────────────────────────────────────────────────────────

def action_escrow_inspect(
    conn: sqlite3.Connection,
    *,
    node_id: str = "",
    lock_id: str = "",
) -> dict[str, Any]:
    """Read-only inspection. Provide either lock_id or node_id (or both).

    lock_id → returns the single lock record.
    node_id → returns all locks for that node_id (gate_claim_id).
    Both provided → returns the single lock, filtered by node_id match.
    """
    ensure_escrow_schema(conn)

    if not lock_id and not node_id:
        return {
            "status": "rejected",
            "error": "Provide at least one of: lock_id, node_id.",
        }

    def _lock_to_dict(lk: EscrowLock) -> dict[str, Any]:
        return {
            "lock_id": lk.lock_id,
            "node_id": lk.gate_claim_id,
            "claimer": lk.staker_id,
            "amount": lk.amount,
            "status": lk.status,
            "locked_at": lk.locked_at,
            "resolved_at": lk.resolved_at,
            "recipient_id": lk.recipient_id,
        }

    if lock_id:
        lk = get_lock(conn, lock_id)
        if lk is None:
            return {
                "status": "rejected",
                "error": f"No escrow lock with lock_id={lock_id!r}.",
            }
        if node_id and lk.gate_claim_id != node_id:
            return {
                "status": "rejected",
                "error": (
                    f"Lock {lock_id!r} belongs to node_id={lk.gate_claim_id!r}, "
                    f"not {node_id!r}."
                ),
            }
        return {"status": "ok", "lock": _lock_to_dict(lk)}

    locks = list_locks_for_claim(conn, gate_claim_id=node_id)
    return {
        "status": "ok",
        "node_id": node_id,
        "locks": [_lock_to_dict(lk) for lk in locks],
        "total": len(locks),
    }


# ── escrow_fund ───────────────────────────────────────────────────────────────

def action_escrow_fund(
    conn: sqlite3.Connection,
    *,
    staker_id: str,
    amount: int,
    currency: str = "MicroToken",
) -> dict[str, Any]:
    """Credit a staker's escrow budget — the "money in" side of the loop.

    Off-chain / testnet this is the faucet that funds budgets so escrow can be
    locked. On mainnet the credit source becomes an on-chain deposit (Slice 1).
    PAID_MARKET gate enforced by callers.
    """
    if not staker_id:
        return {"status": "rejected", "error": "staker_id is required."}
    if amount <= 0:
        return {
            "status": "rejected",
            "error": f"amount must be > 0, got {amount!r}.",
        }
    cur = canonical_currency(currency)
    try:
        bal = credit_balance(
            conn, staker_id=staker_id, amount=amount, now_iso=_now_iso(), currency=cur
        )
    except FundingError as exc:
        return {"status": "rejected", "error": str(exc)}
    return {
        "status": "ok",
        "staker_id": staker_id,
        "currency": cur,
        "credited": amount,
        "total": int(bal.total_amount),
        "reserved": int(bal.reserved_amount),
        "spendable": bal.spendable_amount,
    }


# ── escrow_balance ────────────────────────────────────────────────────────────

def action_escrow_balance(
    conn: sqlite3.Connection,
    *,
    staker_id: str,
    currency: str = "MicroToken",
) -> dict[str, Any]:
    """Read-only — a staker's escrow budget (total / reserved / spendable)."""
    if not staker_id:
        return {"status": "rejected", "error": "staker_id is required."}
    cur = canonical_currency(currency)
    bal = get_balance(conn, staker_id=staker_id, currency=cur)
    if bal is None:
        return {
            "status": "ok",
            "staker_id": staker_id,
            "currency": cur,
            "total": 0,
            "reserved": 0,
            "spendable": 0,
        }
    return {
        "status": "ok",
        "staker_id": staker_id,
        "currency": cur,
        "total": int(bal.total_amount),
        "reserved": int(bal.reserved_amount),
        "spendable": bal.spendable_amount,
    }


# ── escrow_set_wallet ─────────────────────────────────────────────────────────

def action_escrow_set_wallet(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    address: str,
    chain_id: int = BASE_SEPOLIA_CHAIN_ID,
) -> dict[str, Any]:
    """Register the actor's on-chain payout address (where withdrawals land)."""
    if not actor_id:
        return {"status": "rejected", "error": "actor_id is required."}
    try:
        wallet = set_payout_wallet(
            conn, actor_id=actor_id, address=address, now_iso=_now_iso(),
            chain_id=chain_id,
        )
    except WalletError as exc:
        return {"status": "rejected", "error": str(exc)}
    return {
        "status": "ok",
        "actor_id": wallet.actor_id,
        "chain_id": wallet.chain_id,
        "address": wallet.address,
    }


# ── escrow_withdraw ───────────────────────────────────────────────────────────

def _withdraw_result_from_batch(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    actor_id: str,
    currency: str,
    chain_id: int,
    wallet_address: str,
) -> dict[str, Any] | None:
    """Reconstruct a prior withdrawal result from a recorded batch + tx log.

    Returns None if no flushed batch with this key exists yet.
    """
    row = conn.execute(
        "SELECT total_amount, status FROM settlement_batch WHERE batch_id = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None or (dict(row).get("status") != "flushed"):
        return None
    rec = dict(row)
    log = conn.execute(
        "SELECT note FROM transaction_log "
        "WHERE batch_id = ? AND kind = 'batch_flush' ORDER BY tx_id LIMIT 1",
        (idempotency_key,),
    ).fetchone()
    note = dict(log).get("note", "") if log else ""
    backend_name, _, tx_ref = note.partition(":")
    return {
        "status": "ok",
        "actor_id": actor_id,
        "currency": currency,
        "amount": int(rec["total_amount"]),
        "chain_id": chain_id,
        "recipient_wallet": wallet_address,
        "backend": backend_name,
        "settlement_status": "settled",
        "tx_ref": tx_ref,
        "batch_id": idempotency_key,
        "idempotent_replay": True,
    }


def _indoubt_result_from_batch(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    actor_id: str,
    currency: str,
    chain_id: int,
    wallet_address: str,
) -> dict[str, Any] | None:
    """Reconstruct the response for an in-doubt / submitted batch.

    A batch left in ``'in_doubt'`` (backend returned an UNKNOWN result) or
    ``'submitted'`` (handed to the backend, outcome not yet confirmed) must NOT
    be auto-refunded or blind-retried — the payout may have landed. A retry that
    hits this row gets a deterministic in-doubt response and the balance is left
    debited pending reconciliation (slice1a review HIGH — round 2).
    Returns None if no such batch with this key exists.
    """
    row = conn.execute(
        "SELECT total_amount, status FROM settlement_batch WHERE batch_id = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    rec = dict(row)
    status = rec.get("status")
    if status not in ("in_doubt", "submitted"):
        return None
    return {
        "status": "in_doubt",
        "actor_id": actor_id,
        "currency": currency,
        "amount": int(rec["total_amount"]),
        "chain_id": chain_id,
        "recipient_wallet": wallet_address,
        "settlement_status": status,
        "batch_id": idempotency_key,
        "idempotent_replay": True,
        "error": (
            "A withdrawal with this idempotency key is awaiting settlement "
            "reconciliation: the backend returned an ambiguous result and the "
            "payout may already have landed. The balance stays debited and is "
            "NOT auto-refunded. A retry will not re-pay; reconcile the batch "
            f"(batch_id={idempotency_key!r}) before any further action."
        ),
    }


def action_escrow_withdraw(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    amount: int,
    currency: str = "MicroToken",
    chain_id: int = BASE_SEPOLIA_CHAIN_ID,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Withdraw spendable off-chain balance to the actor's payout wallet.

    The off-chain ledger is the source of truth; this settles balance OUT via
    the configured settlement backend (internal marker, or base_sepolia USDC).

    Retry-idempotent under an unknown backend result (slice1a review HIGH 4 +
    HIGH round 2): the idempotency key is derived deterministically from the
    request (or supplied by the client). The durable batch row is reserved in
    'open' BEFORE the debit and is transitioned to 'submitted' AND COMMITTED
    BEFORE ``backend.settle()`` is called, so a crash/retry mid-settle sees a
    durable record and never re-debits.

    Backend failure handling distinguishes the outcome:
      * DEFINITIVELY-not-submitted (``SettlementBackendError.submitted is
        False`` — off-chain / mock / pre-submit validation): the debit is
        refunded and the batch row deleted, so a genuine retry can re-pay.
      * UNKNOWN / ambiguous (``submitted is None`` — e.g. a real backend's
        post-broadcast timeout): the batch is left in 'in_doubt', the balance
        stays debited, and NO auto-refund happens. A retry detects the in-doubt
        row and returns it without re-paying; reconciliation resolves it.
    """
    if not actor_id:
        return {"status": "rejected", "error": "actor_id is required."}
    if amount <= 0:
        return {"status": "rejected", "error": f"amount must be > 0, got {amount!r}."}
    cur = canonical_currency(currency)

    wallet = get_payout_wallet(conn, actor_id=actor_id, chain_id=chain_id)
    if wallet is None:
        return {
            "status": "rejected",
            "error": (
                f"No payout wallet registered for actor={actor_id!r} "
                f"chain_id={chain_id}. Use escrow_set_wallet first."
            ),
        }

    key = stable_idempotency_key(
        actor_id=actor_id,
        amount=amount,
        currency=cur,
        chain_id=chain_id,
        recipient_wallet=wallet.address,
        client_key=idempotency_key,
    )

    # Replay detection BEFORE any debit: a flushed batch with this key means the
    # withdrawal already completed — return the prior result, do not double-pay.
    prior = _withdraw_result_from_batch(
        conn,
        idempotency_key=key,
        actor_id=actor_id,
        currency=cur,
        chain_id=chain_id,
        wallet_address=wallet.address,
    )
    if prior is not None:
        bal = get_balance(conn, staker_id=actor_id, currency=cur)
        prior["remaining_spendable"] = bal.spendable_amount if bal else 0
        return prior

    # An in-doubt / submitted batch with this key MUST NOT be re-paid: the prior
    # payout may have landed. Return the in-doubt response unchanged.
    indoubt = _indoubt_result_from_batch(
        conn,
        idempotency_key=key,
        actor_id=actor_id,
        currency=cur,
        chain_id=chain_id,
        wallet_address=wallet.address,
    )
    if indoubt is not None:
        bal = get_balance(conn, staker_id=actor_id, currency=cur)
        indoubt["remaining_spendable"] = bal.spendable_amount if bal else 0
        return indoubt

    # Reserve the idempotency key by claiming the batch row in 'open' state
    # BEFORE debiting. A concurrent/duplicate in-flight call hits the PK
    # conflict here and is treated as a replay — no second debit.
    now = _now_iso()
    try:
        conn.execute(
            """
            INSERT INTO settlement_batch
                (batch_id, recipient_id, total_amount, total_fee, item_count,
                 status, opened_at, flushed_at)
            VALUES (?, ?, ?, 0, 1, 'open', ?, NULL)
            """,
            (key, actor_id, amount, now),
        )
    except sqlite3.IntegrityError:
        # An in-flight / completed / in-doubt withdrawal already reserved this
        # key. Re-check every terminal-or-pending state — never re-debit.
        replay = _withdraw_result_from_batch(
            conn,
            idempotency_key=key,
            actor_id=actor_id,
            currency=cur,
            chain_id=chain_id,
            wallet_address=wallet.address,
        )
        if replay is not None:
            bal = get_balance(conn, staker_id=actor_id, currency=cur)
            replay["remaining_spendable"] = bal.spendable_amount if bal else 0
            return replay
        replay_indoubt = _indoubt_result_from_batch(
            conn,
            idempotency_key=key,
            actor_id=actor_id,
            currency=cur,
            chain_id=chain_id,
            wallet_address=wallet.address,
        )
        if replay_indoubt is not None:
            bal = get_balance(conn, staker_id=actor_id, currency=cur)
            replay_indoubt["remaining_spendable"] = bal.spendable_amount if bal else 0
            return replay_indoubt
        return {
            "status": "rejected",
            "error": (
                "A withdrawal with this idempotency key is already in flight; "
                "retry once it completes."
            ),
        }

    # Debit spendable balance; on a debit failure the reservation is removed so
    # a genuine retry can proceed (no settlement was attempted).
    try:
        new_bal = withdraw_balance(
            conn, staker_id=actor_id, amount=amount, now_iso=now, currency=cur
        )
    except InsufficientFundsError as exc:
        conn.execute("DELETE FROM settlement_batch WHERE batch_id = ?", (key,))
        return {"status": "rejected", "error": str(exc)}

    # Transition to a durable PRE-SETTLE state and COMMIT before handing the
    # payout to the backend. If the process crashes during backend.settle(), a
    # retry sees this 'submitted' row and routes to the in-doubt path instead of
    # re-debiting/re-paying (slice1a review HIGH — round 2).
    conn.execute(
        "UPDATE settlement_batch SET status = 'submitted' WHERE batch_id = ?",
        (key,),
    )
    conn.commit()

    backend = get_settlement_backend()
    try:
        settlement = backend.settle(
            recipient_wallet=wallet.address,
            amount_base_units=amount,
            currency=cur,
            idempotency_key=key,
        )
    except SettlementBackendError as exc:
        if exc.submitted is False:
            # DEFINITIVELY not submitted — no money moved. Refund the debit and
            # delete the reservation so a genuine retry can re-pay.
            credit_balance(
                conn, staker_id=actor_id, amount=amount,
                now_iso=_now_iso(), currency=cur,
            )
            conn.execute(
                "DELETE FROM settlement_batch WHERE batch_id = ?", (key,)
            )
            conn.commit()
            return {
                "status": "rejected",
                "error": str(exc),
                "settlement_status": "not_submitted",
                "refunded": True,
                "retryable": True,
            }
        # UNKNOWN / ambiguous result — the payout MAY have landed. Do NOT
        # auto-refund and do NOT allow a blind retry to re-pay. Leave the batch
        # in 'in_doubt' for reconciliation; the balance stays debited.
        conn.execute(
            "UPDATE settlement_batch SET status = 'in_doubt' WHERE batch_id = ?",
            (key,),
        )
        conn.execute(
            """
            INSERT INTO transaction_log
                (kind, escrow_id, settlement_id, batch_id, actor_id, amount,
                 recorded_at, note)
            VALUES ('batch_flush', NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (key, actor_id, amount, _now_iso(), f"in_doubt:{exc}"),
        )
        conn.commit()
        bal = get_balance(conn, staker_id=actor_id, currency=cur)
        return {
            "status": "in_doubt",
            "actor_id": actor_id,
            "currency": cur,
            "amount": amount,
            "chain_id": chain_id,
            "recipient_wallet": wallet.address,
            "settlement_status": "in_doubt",
            "batch_id": key,
            "idempotent_replay": False,
            "remaining_spendable": bal.spendable_amount if bal else 0,
            "error": (
                "Settlement returned an ambiguous result; the payout may have "
                "landed. The balance is NOT auto-refunded and a retry will not "
                f"re-pay. Reconcile batch_id={key!r}. Backend error: {exc}"
            ),
        }

    now = _now_iso()
    tx_ref = settlement["tx_ref"]
    # Finalize the reserved batch row to 'flushed'.
    conn.execute(
        """
        UPDATE settlement_batch
        SET status = 'flushed', flushed_at = ?
        WHERE batch_id = ?
        """,
        (now, key),
    )
    conn.execute(
        """
        INSERT INTO transaction_log
            (kind, escrow_id, settlement_id, batch_id, actor_id, amount,
             recorded_at, note)
        VALUES ('batch_flush', NULL, NULL, ?, ?, ?, ?, ?)
        """,
        (key, actor_id, amount, now, f"{settlement['backend']}:{tx_ref}"),
    )

    return {
        "status": "ok",
        "actor_id": actor_id,
        "currency": cur,
        "amount": amount,
        "chain_id": chain_id,
        "recipient_wallet": wallet.address,
        "backend": settlement["backend"],
        "settlement_status": settlement["status"],
        "tx_ref": tx_ref,
        "batch_id": key,
        "idempotent_replay": False,
        "remaining_spendable": new_bal.spendable_amount,
    }
