"""Read historical escrow records without creating or mutating tables."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal


EscrowStatus = Literal["locked", "released", "refunded"]


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
