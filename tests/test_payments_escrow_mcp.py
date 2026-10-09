"""Tests for payments escrow MCP actions: escrow_lock/release/refund/inspect.

Spec: Task #41 — Payments escrow MCP wiring.
Business logic lives in tinyassets/payments/actions.py; MCP wiring in universe_server.py.
"""

from __future__ import annotations

# ── helpers ───────────────────────────────────────────────────────────────────


# ── escrow_lock ───────────────────────────────────────────────────────────────


# ── escrow_release ────────────────────────────────────────────────────────────


# ── escrow_refund ─────────────────────────────────────────────────────────────


# ── escrow_inspect ────────────────────────────────────────────────────────────


# ── Round-trip integration ────────────────────────────────────────────────────


# ── Cross-actor authorization (slice1a review CRITICAL 1) ──────────────────────


# ── Pure business logic unit tests ────────────────────────────────────────────

class TestPaymentsActionsUnit:
    def _conn(self, tmp_path):
        # storage._connect is a context manager (closes on exit); these unit
        # tests need a connection that stays open across multiple action calls,
        # so open a raw connection to the same DB path with matching pragmas.
        import sqlite3

        from tinyassets.daemon_server import initialize_author_server
        from tinyassets.payments.escrow import migrate_escrow_schema
        from tinyassets.storage import db_path
        initialize_author_server(tmp_path)
        conn = sqlite3.connect(str(db_path(tmp_path)), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        migrate_escrow_schema(conn)
        from tinyassets.payments.funding import credit_balance
        credit_balance(
            conn, staker_id="staker-1", amount=1_000_000,
            now_iso="2026-06-08T00:00:00+00:00",
        )
        return conn

    def test_action_lock_success(self, tmp_path):
        from tinyassets.payments.actions import action_escrow_lock
        conn = self._conn(tmp_path)
        result = action_escrow_lock(
            conn, node_id="n1", amount=100, claimer="staker-1"
        )
        assert result["status"] == "ok"
        assert result["amount"] == 100

    def test_action_lock_zero_rejected(self, tmp_path):
        from tinyassets.payments.actions import action_escrow_lock
        conn = self._conn(tmp_path)
        result = action_escrow_lock(
            conn, node_id="n1", amount=0, claimer="staker-1"
        )
        assert result["status"] == "rejected"

    def test_action_release_unknown_lock(self, tmp_path):
        from tinyassets.payments.actions import action_escrow_release
        conn = self._conn(tmp_path)
        result = action_escrow_release(
            conn, lock_id="no-such", recipient_id="r"
        )
        assert result["status"] == "rejected"

    def test_action_refund_unknown_lock(self, tmp_path):
        from tinyassets.payments.actions import action_escrow_refund
        conn = self._conn(tmp_path)
        result = action_escrow_refund(conn, lock_id="no-such")
        assert result["status"] == "rejected"

    def test_action_refund_rejects_non_owner(self, tmp_path):
        # CRITICAL round 2: caller_id that doesn't own the lock is rejected.
        from tinyassets.payments.actions import (
            action_escrow_lock,
            action_escrow_refund,
        )
        conn = self._conn(tmp_path)
        lock = action_escrow_lock(conn, node_id="n1", amount=100, claimer="staker-1")
        result = action_escrow_refund(
            conn, lock_id=lock["lock_id"], caller_id="attacker", host_id="host"
        )
        assert result["status"] == "rejected"
        assert "refund" in result["error"].lower()
        # Lock untouched.
        from tinyassets.payments.escrow import get_lock
        assert get_lock(conn, lock["lock_id"]).status == "locked"

    def test_action_refund_owner_allowed_with_caller_id(self, tmp_path):
        from tinyassets.payments.actions import (
            action_escrow_lock,
            action_escrow_refund,
        )
        conn = self._conn(tmp_path)
        lock = action_escrow_lock(conn, node_id="n2", amount=100, claimer="staker-1")
        result = action_escrow_refund(
            conn, lock_id=lock["lock_id"], caller_id="staker-1", host_id="host"
        )
        assert result["status"] == "ok"
        assert result["disposition"] == "refunded"

    def test_action_refund_no_caller_id_skips_auth(self, tmp_path):
        # Internal callers (caller_id=None) keep the pre-fix behavior.
        from tinyassets.payments.actions import (
            action_escrow_lock,
            action_escrow_refund,
        )
        conn = self._conn(tmp_path)
        lock = action_escrow_lock(conn, node_id="n3", amount=100, claimer="staker-1")
        result = action_escrow_refund(conn, lock_id=lock["lock_id"])
        assert result["status"] == "ok"

    def test_action_inspect_no_params(self, tmp_path):
        from tinyassets.payments.actions import action_escrow_inspect
        conn = self._conn(tmp_path)
        result = action_escrow_inspect(conn)
        assert result["status"] == "rejected"
