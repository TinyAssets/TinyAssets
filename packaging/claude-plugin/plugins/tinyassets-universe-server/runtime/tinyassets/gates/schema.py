"""Gate bonus schema — DDL and dataclasses for the node-scoped bonus extension.

Spec: openspec/specs/paid-market-economy/spec.md.
Topic: Gate bonuses — staked payouts attached to gate milestones.

This module owns:
  * BONUS_COLUMNS — new columns added to the existing gate_claims table.
  * GateBonusClaim — dataclass representing a gate claim row with bonus fields.
  * migrate_gate_bonus_columns(conn) — idempotent migration (probe + ALTER TABLE).

MCP action wiring (claim/unstake/release helpers) lives in a follow-up module
once universe_server.py exits the dirty-tree sweep.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

# ── Column definitions ────────────────────────────────────────────────────────

AttachmentScope = Literal["node", "branch"]

# (column_name, DDL_type_and_default) pairs for ALTER TABLE migration.
# Keep in sync with GateBonusClaim field order.
BONUS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("bonus_stake",       "INTEGER NOT NULL DEFAULT 0"),
    ("bonus_refund_after", "TEXT"),
    ("attachment_scope",  "TEXT NOT NULL DEFAULT 'node'"),
    ("node_id",           "TEXT"),
    # Immutable owner-of-record for the staked bonus. Set once at stake time
    # and NEVER derived from the mutable ``claimed_by`` (a gate re-claim can
    # overwrite ``claimed_by`` on an active claim — see claim_gate — so basing
    # unstake/refund authority on it would let a re-claimer steal the stake).
    ("bonus_staker_id",   "TEXT"),
)


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class GateBonusClaim:
    """A gate claim row with bonus fields populated.

    Fields mirror the gate_claims table including the 4 new bonus columns.
    Pre-bonus rows (missing columns) deserialize with defaults via from_row().
    """

    claim_id: str
    branch_def_id: str
    goal_id: str
    rung_key: str
    evidence_url: str
    evidence_note: str
    claimed_by: str
    claimed_at: str
    retracted_at: str | None = None
    retracted_reason: str = ""
    # ── bonus extension ───────────────────────────────────────────────────────
    bonus_stake: int = 0
    bonus_refund_after: str | None = None
    attachment_scope: AttachmentScope = "node"
    node_id: str | None = None
    # Immutable staker-of-record; see BONUS_COLUMNS. None on pre-migration rows.
    bonus_staker_id: str | None = None

    def __post_init__(self) -> None:
        if self.bonus_stake < 0:
            raise ValueError(
                f"bonus_stake must be >= 0, got {self.bonus_stake!r}"
            )
        if self.attachment_scope not in ("node", "branch"):
            raise ValueError(
                f"attachment_scope must be 'node' or 'branch', "
                f"got {self.attachment_scope!r}"
            )

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict) -> GateBonusClaim:
        """Construct from a DB row, tolerating missing bonus columns."""
        if isinstance(row, sqlite3.Row):
            d: dict = dict(row)
        else:
            d = dict(row)
        return cls(
            claim_id=d["claim_id"],
            branch_def_id=d["branch_def_id"],
            goal_id=d["goal_id"],
            rung_key=d["rung_key"],
            evidence_url=d["evidence_url"],
            evidence_note=d.get("evidence_note", ""),
            claimed_by=d["claimed_by"],
            claimed_at=d["claimed_at"],
            retracted_at=d.get("retracted_at"),
            retracted_reason=d.get("retracted_reason", ""),
            bonus_stake=int(d.get("bonus_stake") or 0),
            bonus_refund_after=d.get("bonus_refund_after"),
            attachment_scope=d.get("attachment_scope") or "node",  # type: ignore[arg-type]
            node_id=d.get("node_id"),
            bonus_staker_id=d.get("bonus_staker_id"),
        )

    @property
    def has_bonus(self) -> bool:
        return self.bonus_stake > 0

    @property
    def is_retracted(self) -> bool:
        return self.retracted_at is not None


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate_gate_bonus_columns(conn: sqlite3.Connection) -> None:
    """Add bonus columns to gate_claims if they are absent.

    Idempotent — safe to call on every startup. Uses PRAGMA table_info
    because SQLite does not support ADD COLUMN IF NOT EXISTS.
    """
    existing = {
        row[1]  # column name is index 1 in PRAGMA table_info output
        for row in conn.execute("PRAGMA table_info(gate_claims)")
    }
    for col_name, col_ddl in BONUS_COLUMNS:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE gate_claims ADD COLUMN {col_name} {col_ddl}"
            )
