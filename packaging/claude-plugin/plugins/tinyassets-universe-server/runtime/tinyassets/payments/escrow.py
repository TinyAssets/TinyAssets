"""Escrow primitives for gate bonus staking.

Spec: openspec/specs/paid-market-economy/spec.md.
Topic: Gate bonuses — staked payouts attached to gate milestones.

This module owns the budget-lock / release / refund lifecycle for bonus stakes.
It is intentionally decoupled from MCP action wiring (universe_server.py) and
from the gate claim record itself (tinyassets/gates/schema.py). The caller
decides when to call each primitive; this module just maintains the ledger.

SQLite table ``escrow_locks`` lives in the same ``.tinyassets.db`` as
``gate_claims`` — shared connection context from tinyassets.storage._connect.

Invariants enforced here:
  * Only one lock per (staker_id, gate_claim_id) at a time.
  * Locks are immutable once released/refunded (status transitions are one-way).
  * Release goes to recipient_id (node's last-claimer at gate-pass-time).
  * Refund goes to staker_id (original staker).
  * Amount precision: integer (smallest currency unit), no floats.

The PAID_MARKET gate is enforced by callers, not here — this module
does not read env vars. The contract is: don't call lock() when the
paid-market flag is off; the module will happily lock if called.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

# ── Schema ────────────────────────────────────────────────────────────────────

ESCROW_SCHEMA = """
CREATE TABLE IF NOT EXISTS escrow_locks (
    lock_id         TEXT PRIMARY KEY,
    gate_claim_id   TEXT NOT NULL,
    staker_id       TEXT NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'MicroToken',
    amount          INTEGER NOT NULL CHECK (amount >= 0),
    status          TEXT NOT NULL DEFAULT 'locked'
                        CHECK (status IN ('locked', 'released', 'refunded')),
    locked_at       TEXT NOT NULL,
    resolved_at     TEXT,
    recipient_id    TEXT,
    UNIQUE (gate_claim_id, staker_id)
);

CREATE INDEX IF NOT EXISTS idx_escrow_gate_claim
    ON escrow_locks(gate_claim_id);

CREATE INDEX IF NOT EXISTS idx_escrow_staker
    ON escrow_locks(staker_id);
"""

# ── Status type ───────────────────────────────────────────────────────────────

EscrowStatus = Literal["locked", "released", "refunded"]


# ── Errors ────────────────────────────────────────────────────────────────────

class EscrowError(Exception):
    """Base for escrow operation errors."""


class DuplicateLockError(EscrowError):
    """Raised when a lock already exists for (gate_claim_id, staker_id)."""


class LockNotFoundError(EscrowError):
    """Raised when the requested lock_id does not exist."""


class LockAlreadyResolvedError(EscrowError):
    """Raised when attempting to release/refund an already-resolved lock."""


class UnauthorizedUnstakeError(EscrowError):
    """Raised when a non-staker attempts to unstake."""


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class EscrowLock:
    """Represents one escrow lock record."""

    lock_id: str
    gate_claim_id: str
    staker_id: str
    currency: str
    amount: int
    status: EscrowStatus
    locked_at: str
    resolved_at: str | None = None
    recipient_id: str | None = None

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"

    @property
    def is_released(self) -> bool:
        return self.status == "released"

    @property
    def is_refunded(self) -> bool:
        return self.status == "refunded"

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> EscrowLock:
        d = dict(row)
        return cls(
            lock_id=d["lock_id"],
            gate_claim_id=d["gate_claim_id"],
            staker_id=d["staker_id"],
            currency=d.get("currency") or "MicroToken",
            amount=int(d["amount"]),
            status=d["status"],
            locked_at=d["locked_at"],
            resolved_at=d.get("resolved_at"),
            recipient_id=d.get("recipient_id"),
        )


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_escrow_schema(conn: sqlite3.Connection) -> None:
    """Create escrow_locks table if absent and backfill the currency column.

    Idempotent — safe to call on legacy DBs created before the currency column.
    """
    conn.executescript(ESCROW_SCHEMA)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(escrow_locks)").fetchall()
    }
    if "currency" not in columns:
        conn.execute(
            "ALTER TABLE escrow_locks "
            "ADD COLUMN currency TEXT NOT NULL DEFAULT 'MicroToken'"
        )


# ── Primitives ────────────────────────────────────────────────────────────────

def lock_bonus(
    conn: sqlite3.Connection,
    *,
    lock_id: str,
    gate_claim_id: str,
    staker_id: str,
    amount: int,
    locked_at: str,
    currency: str = "MicroToken",
) -> EscrowLock:
    """Lock ``amount`` from staker's budget for a gate bonus claim.

    Raises DuplicateLockError if a lock already exists for
    (gate_claim_id, staker_id).
    """
    if amount < 0:
        raise EscrowError(f"amount must be >= 0, got {amount!r}")
    try:
        conn.execute(
            """
            INSERT INTO escrow_locks
                (lock_id, gate_claim_id, staker_id, currency, amount, status, locked_at)
            VALUES (?, ?, ?, ?, ?, 'locked', ?)
            """,
            (lock_id, gate_claim_id, staker_id, currency, amount, locked_at),
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateLockError(
            f"Escrow lock already exists for claim={gate_claim_id!r} "
            f"staker={staker_id!r}"
        ) from exc
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    return EscrowLock.from_row(row)


def release_bonus(
    conn: sqlite3.Connection,
    *,
    lock_id: str,
    recipient_id: str,
    resolved_at: str,
) -> EscrowLock:
    """Release locked bonus to ``recipient_id`` (gate-pass path).

    Raises LockNotFoundError if lock_id does not exist.
    Raises LockAlreadyResolvedError if already released or refunded.
    """
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    if row is None:
        raise LockNotFoundError(f"No escrow lock with lock_id={lock_id!r}")
    lock = EscrowLock.from_row(row)
    if not lock.is_locked:
        raise LockAlreadyResolvedError(
            f"Lock {lock_id!r} is already {lock.status!r}"
        )
    conn.execute(
        """
        UPDATE escrow_locks
        SET status = 'released', recipient_id = ?, resolved_at = ?
        WHERE lock_id = ?
        """,
        (recipient_id, resolved_at, lock_id),
    )
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    return EscrowLock.from_row(row)


def refund_bonus(
    conn: sqlite3.Connection,
    *,
    lock_id: str,
    resolved_at: str,
) -> EscrowLock:
    """Refund locked bonus back to the staker (gate-fail / timeout / retraction).

    Raises LockNotFoundError if lock_id does not exist.
    Raises LockAlreadyResolvedError if already released or refunded.
    """
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    if row is None:
        raise LockNotFoundError(f"No escrow lock with lock_id={lock_id!r}")
    lock = EscrowLock.from_row(row)
    if not lock.is_locked:
        raise LockAlreadyResolvedError(
            f"Lock {lock_id!r} is already {lock.status!r}"
        )
    conn.execute(
        """
        UPDATE escrow_locks
        SET status = 'refunded', recipient_id = staker_id, resolved_at = ?
        WHERE lock_id = ?
        """,
        (resolved_at, lock_id),
    )
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    return EscrowLock.from_row(row)


def get_lock(conn: sqlite3.Connection, lock_id: str) -> EscrowLock | None:
    """Return EscrowLock for lock_id, or None if not found."""
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE lock_id = ?", (lock_id,)
    ).fetchone()
    return EscrowLock.from_row(row) if row else None


def get_lock_for_claim(
    conn: sqlite3.Connection,
    gate_claim_id: str,
    staker_id: str,
) -> EscrowLock | None:
    """Return the lock for a (gate_claim_id, staker_id) pair, or None."""
    row = conn.execute(
        "SELECT * FROM escrow_locks WHERE gate_claim_id = ? AND staker_id = ?",
        (gate_claim_id, staker_id),
    ).fetchone()
    return EscrowLock.from_row(row) if row else None


def list_locks_for_claim(
    conn: sqlite3.Connection,
    gate_claim_id: str,
) -> list[EscrowLock]:
    """Return all escrow locks for a gate claim (may be multiple stakers)."""
    rows = conn.execute(
        "SELECT * FROM escrow_locks WHERE gate_claim_id = ? ORDER BY locked_at",
        (gate_claim_id,),
    ).fetchall()
    return [EscrowLock.from_row(r) for r in rows]
