"""E2E withdrawal-bridge tests through the extensions() MCP surface (Slice 1a).

set_wallet -> fund -> withdraw: spendable balance settles OUT via the configured
backend (internal marker by default; mock base_sepolia USDC under env), debiting
the off-chain balance and recording a settlement_batch + transaction_log entry.
No network — the base_sepolia backend uses the mock on-chain client.
"""

from __future__ import annotations

ADDR = "0x" + "1" * 40


class TestWithdrawBackendFailureContract:
    """slice1a review HIGH — round 2: a withdrawal's response to a backend
    failure must depend on whether the payout was DEFINITIVELY-not-submitted
    (auto-refund + retryable) or UNKNOWN (in_doubt, never auto-refunded, retry
    never re-pays). These drive the action directly with an injected backend so
    we can simulate each failure mode without a network."""

    ADDR = "0x" + "c" * 40

    def _conn(self, tmp_path):
        import sqlite3

        from tinyassets.daemon_server import initialize_author_server
        from tinyassets.payments.escrow import migrate_escrow_schema
        from tinyassets.payments.funding import credit_balance
        from tinyassets.payments.wallets import set_payout_wallet
        from tinyassets.storage import db_path
        initialize_author_server(tmp_path)
        conn = sqlite3.connect(str(db_path(tmp_path)), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        migrate_escrow_schema(conn)
        credit_balance(
            conn, staker_id="alice", amount=1_000_000,
            now_iso="2026-06-08T00:00:00+00:00",
        )
        set_payout_wallet(
            conn, actor_id="alice", address=self.ADDR,
            now_iso="2026-06-08T00:00:00+00:00",
        )
        conn.commit()
        return conn

    def _inject_backend(self, monkeypatch, *, fail_mode):
        from tinyassets.payments import actions as actions_mod
        from tinyassets.payments.settlement_backend import (
            BaseSepoliaBackend,
            MockOnChainClient,
        )
        client = MockOnChainClient(fail_mode=fail_mode)
        backend = BaseSepoliaBackend(client=client)
        monkeypatch.setattr(actions_mod, "get_settlement_backend", lambda: backend)
        return client

    def _spendable(self, conn):
        from tinyassets.payments.funding import get_balance
        bal = get_balance(conn, staker_id="alice", currency="MicroToken")
        return bal.spendable_amount if bal else 0

    def test_definitive_failure_refunds_and_is_retryable(self, tmp_path, monkeypatch):
        from tinyassets.payments.actions import action_escrow_withdraw
        conn = self._conn(tmp_path)
        self._inject_backend(monkeypatch, fail_mode="not_submitted")

        out = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000,
            idempotency_key="wd-def",
        )
        assert out["status"] == "rejected"
        assert out["settlement_status"] == "not_submitted"
        assert out["refunded"] is True
        assert out["retryable"] is True
        # Balance fully restored — definitively no money moved.
        assert self._spendable(conn) == 1_000_000
        # No batch row left behind — a genuine retry can re-pay.
        assert conn.execute(
            "SELECT COUNT(*) FROM settlement_batch"
        ).fetchone()[0] == 0

        # Retry now succeeds (different backend that completes).
        from tinyassets.payments import actions as actions_mod
        from tinyassets.payments.settlement_backend import InternalBackend
        monkeypatch.setattr(
            actions_mod, "get_settlement_backend", lambda: InternalBackend()
        )
        retry = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000, idempotency_key="wd-def",
        )
        assert retry["status"] == "ok"
        assert self._spendable(conn) == 600_000

    def test_unknown_failure_goes_in_doubt_not_refunded(self, tmp_path, monkeypatch):
        from tinyassets.payments.actions import action_escrow_withdraw
        conn = self._conn(tmp_path)
        self._inject_backend(monkeypatch, fail_mode="unknown")

        out = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000, idempotency_key="wd-unk",
        )
        assert out["status"] == "in_doubt"
        assert out["settlement_status"] == "in_doubt"
        # NOT auto-refunded — balance stays debited pending reconciliation.
        assert self._spendable(conn) == 600_000
        # Batch row persists in in_doubt for reconciliation.
        row = conn.execute(
            "SELECT status FROM settlement_batch WHERE batch_id = ?",
            (out["batch_id"],),
        ).fetchone()
        assert row["status"] == "in_doubt"

    def test_unknown_failure_retry_does_not_double_pay(self, tmp_path, monkeypatch):
        from tinyassets.payments.actions import action_escrow_withdraw
        conn = self._conn(tmp_path)
        client = self._inject_backend(monkeypatch, fail_mode="unknown")

        first = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000, idempotency_key="wd-unk2",
        )
        assert first["status"] == "in_doubt"
        assert self._spendable(conn) == 600_000
        calls_after_first = len(client.calls)

        # A blind retry (even if the backend would now succeed) must NOT re-pay
        # or re-debit — it returns the in-doubt record unchanged.
        from tinyassets.payments import actions as actions_mod
        from tinyassets.payments.settlement_backend import (
            BaseSepoliaBackend,
            MockOnChainClient,
        )
        ok_client = MockOnChainClient()  # would succeed if called
        monkeypatch.setattr(
            actions_mod, "get_settlement_backend",
            lambda: BaseSepoliaBackend(client=ok_client),
        )
        retry = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000, idempotency_key="wd-unk2",
        )
        assert retry["status"] == "in_doubt"
        assert retry["idempotent_replay"] is True
        assert retry["batch_id"] == first["batch_id"]
        # No second debit.
        assert self._spendable(conn) == 600_000
        # The retry never reached the (would-succeed) backend.
        assert len(ok_client.calls) == 0
        # The original failing client saw exactly one submit attempt.
        assert len(client.calls) == calls_after_first
        # Still exactly one batch row.
        assert conn.execute(
            "SELECT COUNT(*) FROM settlement_batch WHERE batch_id = ?",
            (first["batch_id"],),
        ).fetchone()[0] == 1

    def test_committed_success_replay_dedups(self, tmp_path, monkeypatch):
        from tinyassets.payments import actions as actions_mod
        from tinyassets.payments.actions import action_escrow_withdraw
        from tinyassets.payments.settlement_backend import InternalBackend
        conn = self._conn(tmp_path)
        monkeypatch.setattr(
            actions_mod, "get_settlement_backend", lambda: InternalBackend()
        )

        first = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000, idempotency_key="wd-ok",
        )
        assert first["status"] == "ok"
        assert first["idempotent_replay"] is False
        assert self._spendable(conn) == 600_000

        replay = action_escrow_withdraw(
            conn, actor_id="alice", amount=400_000, idempotency_key="wd-ok",
        )
        assert replay["status"] == "ok"
        assert replay["idempotent_replay"] is True
        assert replay["batch_id"] == first["batch_id"]
        assert replay["tx_ref"] == first["tx_ref"]
        # Debited exactly once.
        assert self._spendable(conn) == 600_000
