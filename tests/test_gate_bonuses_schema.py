"""Tests for tinyassets.gates schema layer.

Spec: openspec/changes/package-runtime-convergence/ (historical schema preserved).
Topic: Gate bonuses — staked payouts attached to gate milestones.

Covers:
  * GateBonusClaim: field defaults, validation invariants, from_row deserialization.
  * migrate_gate_bonus_columns: idempotency, column presence after migration.
  * BONUS_COLUMNS: completeness vs spec.
  * Integration: migrate on existing gate_claims table shape.
"""

from __future__ import annotations

import sqlite3

import pytest

from tinyassets.gates import (
    BONUS_COLUMNS,
    GateBonusClaim,
    migrate_gate_bonus_columns,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_BASE_GATE_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS gate_claims (
    claim_id          TEXT PRIMARY KEY,
    branch_def_id     TEXT NOT NULL,
    goal_id           TEXT NOT NULL,
    rung_key          TEXT NOT NULL,
    evidence_url      TEXT NOT NULL,
    evidence_note     TEXT NOT NULL DEFAULT '',
    claimed_by        TEXT NOT NULL,
    claimed_at        TEXT NOT NULL,
    retracted_at      TEXT,
    retracted_reason  TEXT NOT NULL DEFAULT ''
);
"""


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_BASE_GATE_CLAIMS_DDL)
    return conn


def _base_row(**overrides) -> dict:
    base = {
        "claim_id": "claim-001",
        "branch_def_id": "branch-abc",
        "goal_id": "goal-xyz",
        "rung_key": "milestone-1",
        "evidence_url": "https://example.com/proof",
        "evidence_note": "looks good",
        "claimed_by": "daemon-1",
        "claimed_at": "2026-04-25T00:00:00Z",
        "retracted_at": None,
        "retracted_reason": "",
    }
    base.update(overrides)
    return base


# ── GateBonusClaim dataclass ──────────────────────────────────────────────────

class TestGateBonusClaim:
    def test_defaults(self):
        c = GateBonusClaim(**_base_row())
        assert c.bonus_stake == 0
        assert c.bonus_refund_after is None
        assert c.attachment_scope == "node"
        assert c.node_id is None

    def test_has_bonus_false_when_zero(self):
        c = GateBonusClaim(**_base_row(), bonus_stake=0)
        assert c.has_bonus is False

    def test_has_bonus_true_when_positive(self):
        c = GateBonusClaim(**_base_row(), bonus_stake=100)
        assert c.has_bonus is True

    def test_is_retracted_false(self):
        c = GateBonusClaim(**_base_row())
        assert c.is_retracted is False

    def test_is_retracted_true(self):
        row = _base_row()
        row["retracted_at"] = "2026-04-25T01:00:00Z"
        c = GateBonusClaim(**row)
        assert c.is_retracted is True

    def test_negative_bonus_stake_raises(self):
        with pytest.raises(ValueError, match="bonus_stake"):
            GateBonusClaim(**_base_row(), bonus_stake=-1)

    def test_invalid_attachment_scope_raises(self):
        with pytest.raises(ValueError, match="attachment_scope"):
            GateBonusClaim(**_base_row(), attachment_scope="universe")  # type: ignore[arg-type]

    def test_node_attachment_scope_accepted(self):
        c = GateBonusClaim(**_base_row(), attachment_scope="node")
        assert c.attachment_scope == "node"

    def test_branch_attachment_scope_accepted(self):
        c = GateBonusClaim(**_base_row(), attachment_scope="branch")
        assert c.attachment_scope == "branch"

    def test_bonus_stake_zero_ok(self):
        c = GateBonusClaim(**_base_row(), bonus_stake=0)
        assert c.bonus_stake == 0

    def test_large_bonus_stake_ok(self):
        c = GateBonusClaim(**_base_row(), bonus_stake=10_000_000)
        assert c.bonus_stake == 10_000_000

    def test_node_id_stored(self):
        c = GateBonusClaim(**_base_row(), node_id="n1")
        assert c.node_id == "n1"

    def test_bonus_refund_after_stored(self):
        c = GateBonusClaim(**_base_row(), bonus_refund_after="2026-05-25T00:00:00Z")
        assert c.bonus_refund_after == "2026-05-25T00:00:00Z"


# ── from_row deserialization ──────────────────────────────────────────────────

class TestFromRow:
    def test_from_dict_base_row(self):
        c = GateBonusClaim.from_row(_base_row())
        assert c.claim_id == "claim-001"
        assert c.bonus_stake == 0
        assert c.attachment_scope == "node"

    def test_from_dict_with_bonus_fields(self):
        row = _base_row(
            bonus_stake=500,
            bonus_refund_after="2026-05-25T00:00:00Z",
            attachment_scope="branch",
            node_id="n2",
        )
        c = GateBonusClaim.from_row(row)
        assert c.bonus_stake == 500
        assert c.bonus_refund_after == "2026-05-25T00:00:00Z"
        assert c.attachment_scope == "branch"
        assert c.node_id == "n2"

    def test_from_row_missing_bonus_columns_uses_defaults(self):
        row = _base_row()  # no bonus_* keys
        c = GateBonusClaim.from_row(row)
        assert c.bonus_stake == 0
        assert c.attachment_scope == "node"
        assert c.bonus_refund_after is None
        assert c.node_id is None

    def test_from_row_none_bonus_stake_treated_as_zero(self):
        row = _base_row(bonus_stake=None)
        c = GateBonusClaim.from_row(row)
        assert c.bonus_stake == 0

    def test_from_sqlite_row(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        conn.execute(
            "INSERT INTO gate_claims (claim_id, branch_def_id, goal_id, rung_key, "
            "evidence_url, claimed_by, claimed_at, bonus_stake, attachment_scope) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("c1", "b1", "g1", "r1", "http://x", "d1", "2026-04-25T00:00:00Z", 250, "node"),
        )
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id = 'c1'"
        ).fetchone()
        c = GateBonusClaim.from_row(row)
        assert c.bonus_stake == 250
        assert c.attachment_scope == "node"


# ── BONUS_COLUMNS completeness ────────────────────────────────────────────────

class TestBonusColumns:
    def test_all_spec_columns_present(self):
        col_names = {col for col, _ in BONUS_COLUMNS}
        assert "bonus_stake" in col_names
        assert "bonus_refund_after" in col_names
        assert "attachment_scope" in col_names
        assert "node_id" in col_names
        assert "bonus_staker_id" in col_names

    def test_bonus_stake_has_default_zero(self):
        ddl = dict(BONUS_COLUMNS)["bonus_stake"]
        assert "DEFAULT 0" in ddl

    def test_attachment_scope_has_default_node(self):
        ddl = dict(BONUS_COLUMNS)["attachment_scope"]
        assert "DEFAULT 'node'" in ddl

    def test_bonus_refund_after_is_nullable(self):
        ddl = dict(BONUS_COLUMNS)["bonus_refund_after"]
        assert "NOT NULL" not in ddl


# ── migrate_gate_bonus_columns ────────────────────────────────────────────────

class TestMigrateGateBonusColumns:
    def _column_names(self, conn: sqlite3.Connection) -> set[str]:
        return {
            row[1]
            for row in conn.execute("PRAGMA table_info(gate_claims)")
        }

    def test_columns_absent_before_migration(self):
        conn = _fresh_db()
        cols = self._column_names(conn)
        assert "bonus_stake" not in cols
        assert "attachment_scope" not in cols

    def test_columns_present_after_migration(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        cols = self._column_names(conn)
        assert "bonus_stake" in cols
        assert "bonus_refund_after" in cols
        assert "attachment_scope" in cols
        assert "node_id" in cols

    def test_migration_idempotent_double_call(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        migrate_gate_bonus_columns(conn)  # must not raise
        cols = self._column_names(conn)
        assert "bonus_stake" in cols

    def test_migration_idempotent_ten_calls(self):
        conn = _fresh_db()
        for _ in range(10):
            migrate_gate_bonus_columns(conn)
        cols = self._column_names(conn)
        # bonus_stake, bonus_refund_after, bonus_staker_id
        assert len([c for c in cols if c.startswith("bonus")]) == 3

    def test_existing_rows_get_default_values(self):
        conn = _fresh_db()
        conn.execute(
            "INSERT INTO gate_claims (claim_id, branch_def_id, goal_id, rung_key, "
            "evidence_url, claimed_by, claimed_at) VALUES (?,?,?,?,?,?,?)",
            ("c-old", "b1", "g1", "r1", "http://x", "d1", "2026-04-01T00:00:00Z"),
        )
        migrate_gate_bonus_columns(conn)
        row = conn.execute(
            "SELECT bonus_stake, attachment_scope FROM gate_claims WHERE claim_id='c-old'"
        ).fetchone()
        assert row["bonus_stake"] == 0
        assert row["attachment_scope"] == "node"

    def test_new_rows_after_migration_accept_bonus_fields(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        conn.execute(
            "INSERT INTO gate_claims (claim_id, branch_def_id, goal_id, rung_key, "
            "evidence_url, claimed_by, claimed_at, bonus_stake, attachment_scope, node_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("c-new", "b2", "g2", "r2", "http://y", "d2", "2026-04-25T00:00:00Z",
             1000, "node", "n3"),
        )
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id='c-new'"
        ).fetchone()
        assert row["bonus_stake"] == 1000
        assert row["node_id"] == "n3"

    def test_migration_on_already_migrated_db(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        cols_before = self._column_names(conn)
        migrate_gate_bonus_columns(conn)
        cols_after = self._column_names(conn)
        assert cols_before == cols_after


# ── Integration: from_row round-trip via migrated DB ─────────────────────────

class TestIntegrationRoundtrip:
    def test_full_roundtrip_with_bonus(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        conn.execute(
            "INSERT INTO gate_claims (claim_id, branch_def_id, goal_id, rung_key, "
            "evidence_url, evidence_note, claimed_by, claimed_at, "
            "bonus_stake, bonus_refund_after, attachment_scope, node_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "rt-1", "b-rt", "g-rt", "milestone-rt",
                "https://proof.example", "verified by human",
                "daemon-rt", "2026-04-25T10:00:00Z",
                750, "2026-05-25T10:00:00Z", "node", "n-rt",
            ),
        )
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id='rt-1'"
        ).fetchone()
        claim = GateBonusClaim.from_row(row)

        assert claim.claim_id == "rt-1"
        assert claim.bonus_stake == 750
        assert claim.bonus_refund_after == "2026-05-25T10:00:00Z"
        assert claim.attachment_scope == "node"
        assert claim.node_id == "n-rt"
        assert claim.has_bonus is True
        assert claim.is_retracted is False

    def test_zero_bonus_roundtrip(self):
        conn = _fresh_db()
        migrate_gate_bonus_columns(conn)
        conn.execute(
            "INSERT INTO gate_claims (claim_id, branch_def_id, goal_id, rung_key, "
            "evidence_url, claimed_by, claimed_at) VALUES (?,?,?,?,?,?,?)",
            ("rt-2", "b2", "g2", "r2", "https://x", "d2", "2026-04-25T00:00:00Z"),
        )
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id='rt-2'"
        ).fetchone()
        claim = GateBonusClaim.from_row(row)
        assert claim.bonus_stake == 0
        assert claim.has_bonus is False
