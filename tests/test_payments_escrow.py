"""Tests for tinyassets.payments.escrow — gate bonus escrow primitives.

Spec: openspec/specs/community-patch-loop/spec.md §Gate bonuses invariants:
  * bonus_stake locked when claim made; release to node's last-claimer on pass
  * refund to staker on gate fail / timeout / retraction
  * unstake by non-staker rejected (tested at caller contract level here)
  * status transitions are one-way: locked → released | refunded
  * duplicate lock for same (gate_claim_id, staker_id) raises DuplicateLockError
"""

from __future__ import annotations

import sqlite3

import pytest

from tinyassets.payments import (
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

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate_escrow_schema(conn)
    return conn


def _lock(
    conn: sqlite3.Connection,
    *,
    lock_id: str = "lock-001",
    gate_claim_id: str = "claim-001",
    staker_id: str = "staker-1",
    amount: int = 100,
    locked_at: str = "2026-04-25T00:00:00Z",
) -> EscrowLock:
    return lock_bonus(
        conn,
        lock_id=lock_id,
        gate_claim_id=gate_claim_id,
        staker_id=staker_id,
        amount=amount,
        locked_at=locked_at,
    )


# ── migrate_escrow_schema ─────────────────────────────────────────────────────

class TestMigrateEscrowSchema:
    def test_table_created(self):
        conn = _fresh_conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "escrow_locks" in tables

    def test_idempotent_double_call(self):
        conn = _fresh_conn()
        migrate_escrow_schema(conn)  # second call must not raise
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "escrow_locks" in tables


# ── lock_bonus ────────────────────────────────────────────────────────────────

class TestLockBonus:
    def test_creates_lock(self):
        conn = _fresh_conn()
        lock = _lock(conn)
        assert lock.lock_id == "lock-001"
        assert lock.status == "locked"
        assert lock.amount == 100

    def test_lock_is_locked(self):
        conn = _fresh_conn()
        lock = _lock(conn)
        assert lock.is_locked is True
        assert lock.is_released is False
        assert lock.is_refunded is False

    def test_staker_id_stored(self):
        conn = _fresh_conn()
        lock = _lock(conn, staker_id="alice")
        assert lock.staker_id == "alice"

    def test_gate_claim_id_stored(self):
        conn = _fresh_conn()
        lock = _lock(conn, gate_claim_id="gate-xyz")
        assert lock.gate_claim_id == "gate-xyz"

    def test_zero_amount_ok(self):
        conn = _fresh_conn()
        lock = _lock(conn, amount=0)
        assert lock.amount == 0

    def test_large_amount_ok(self):
        conn = _fresh_conn()
        lock = _lock(conn, amount=10_000_000)
        assert lock.amount == 10_000_000

    def test_negative_amount_raises(self):
        conn = _fresh_conn()
        from tinyassets.payments.escrow import EscrowError
        with pytest.raises(EscrowError, match="amount"):
            lock_bonus(
                conn,
                lock_id="l1",
                gate_claim_id="c1",
                staker_id="s1",
                amount=-1,
                locked_at="2026-04-25T00:00:00Z",
            )

    def test_duplicate_claim_staker_raises(self):
        conn = _fresh_conn()
        _lock(conn, lock_id="l1")
        with pytest.raises(DuplicateLockError):
            _lock(conn, lock_id="l2")  # same gate_claim_id + staker_id

    def test_different_stakers_same_claim_ok(self):
        conn = _fresh_conn()
        _lock(conn, lock_id="l1", staker_id="alice")
        _lock(conn, lock_id="l2", staker_id="bob")  # different staker → ok
        lock_alice = get_lock(conn, "l1")
        lock_bob = get_lock(conn, "l2")
        assert lock_alice is not None
        assert lock_bob is not None

    def test_same_staker_different_claims_ok(self):
        conn = _fresh_conn()
        _lock(conn, lock_id="l1", gate_claim_id="claim-A")
        _lock(conn, lock_id="l2", gate_claim_id="claim-B")
        assert get_lock(conn, "l1") is not None
        assert get_lock(conn, "l2") is not None

    def test_recipient_id_none_on_lock(self):
        conn = _fresh_conn()
        lock = _lock(conn)
        assert lock.recipient_id is None

    def test_resolved_at_none_on_lock(self):
        conn = _fresh_conn()
        lock = _lock(conn)
        assert lock.resolved_at is None


# ── release_bonus ─────────────────────────────────────────────────────────────

class TestReleaseBonus:
    def test_releases_to_recipient(self):
        conn = _fresh_conn()
        _lock(conn)
        lock = release_bonus(
            conn,
            lock_id="lock-001",
            recipient_id="daemon-winner",
            resolved_at="2026-04-25T01:00:00Z",
        )
        assert lock.status == "released"
        assert lock.recipient_id == "daemon-winner"
        assert lock.resolved_at == "2026-04-25T01:00:00Z"

    def test_is_released_true(self):
        conn = _fresh_conn()
        _lock(conn)
        lock = release_bonus(
            conn, lock_id="lock-001",
            recipient_id="r", resolved_at="2026-04-25T01:00:00Z",
        )
        assert lock.is_released is True
        assert lock.is_locked is False

    def test_release_not_found_raises(self):
        conn = _fresh_conn()
        with pytest.raises(LockNotFoundError):
            release_bonus(
                conn, lock_id="nonexistent",
                recipient_id="r", resolved_at="2026-04-25T00:00:00Z",
            )

    def test_release_already_released_raises(self):
        conn = _fresh_conn()
        _lock(conn)
        release_bonus(
            conn, lock_id="lock-001",
            recipient_id="r", resolved_at="2026-04-25T01:00:00Z",
        )
        with pytest.raises(LockAlreadyResolvedError):
            release_bonus(
                conn, lock_id="lock-001",
                recipient_id="r2", resolved_at="2026-04-25T02:00:00Z",
            )

    def test_release_already_refunded_raises(self):
        conn = _fresh_conn()
        _lock(conn)
        refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T01:00:00Z")
        with pytest.raises(LockAlreadyResolvedError):
            release_bonus(
                conn, lock_id="lock-001",
                recipient_id="r", resolved_at="2026-04-25T02:00:00Z",
            )

    def test_recipient_can_differ_from_staker(self):
        conn = _fresh_conn()
        _lock(conn, staker_id="alice")
        lock = release_bonus(
            conn, lock_id="lock-001",
            recipient_id="daemon-bob",  # last-claimer, not the staker
            resolved_at="2026-04-25T01:00:00Z",
        )
        assert lock.staker_id == "alice"
        assert lock.recipient_id == "daemon-bob"


# ── refund_bonus ──────────────────────────────────────────────────────────────

class TestRefundBonus:
    def test_refund_sets_status_refunded(self):
        conn = _fresh_conn()
        _lock(conn)
        lock = refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T01:00:00Z")
        assert lock.status == "refunded"
        assert lock.is_refunded is True

    def test_refund_recipient_is_staker(self):
        conn = _fresh_conn()
        _lock(conn, staker_id="carol")
        lock = refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T01:00:00Z")
        assert lock.recipient_id == "carol"

    def test_refund_not_found_raises(self):
        conn = _fresh_conn()
        with pytest.raises(LockNotFoundError):
            refund_bonus(conn, lock_id="nonexistent", resolved_at="2026-04-25T00:00:00Z")

    def test_refund_already_released_raises(self):
        conn = _fresh_conn()
        _lock(conn)
        release_bonus(
            conn, lock_id="lock-001",
            recipient_id="r", resolved_at="2026-04-25T01:00:00Z",
        )
        with pytest.raises(LockAlreadyResolvedError):
            refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T02:00:00Z")

    def test_refund_already_refunded_raises(self):
        conn = _fresh_conn()
        _lock(conn)
        refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T01:00:00Z")
        with pytest.raises(LockAlreadyResolvedError):
            refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T02:00:00Z")

    def test_resolved_at_stored(self):
        conn = _fresh_conn()
        _lock(conn)
        lock = refund_bonus(conn, lock_id="lock-001", resolved_at="2026-05-01T12:00:00Z")
        assert lock.resolved_at == "2026-05-01T12:00:00Z"


# ── get_lock / get_lock_for_claim ─────────────────────────────────────────────

class TestGetLock:
    def test_get_existing(self):
        conn = _fresh_conn()
        _lock(conn)
        lock = get_lock(conn, "lock-001")
        assert lock is not None
        assert lock.lock_id == "lock-001"

    def test_get_missing_returns_none(self):
        conn = _fresh_conn()
        assert get_lock(conn, "no-such-lock") is None

    def test_get_lock_for_claim(self):
        conn = _fresh_conn()
        _lock(conn, gate_claim_id="claim-X", staker_id="dave")
        lock = get_lock_for_claim(conn, "claim-X", "dave")
        assert lock is not None
        assert lock.staker_id == "dave"

    def test_get_lock_for_claim_missing_returns_none(self):
        conn = _fresh_conn()
        assert get_lock_for_claim(conn, "claim-X", "nobody") is None


# ── list_locks_for_claim ──────────────────────────────────────────────────────

class TestListLocksForClaim:
    def test_empty_when_no_locks(self):
        conn = _fresh_conn()
        assert list_locks_for_claim(conn, "claim-Z") == []

    def test_single_lock(self):
        conn = _fresh_conn()
        _lock(conn, gate_claim_id="claim-A", staker_id="s1", lock_id="l1")
        locks = list_locks_for_claim(conn, "claim-A")
        assert len(locks) == 1
        assert locks[0].staker_id == "s1"

    def test_multiple_stakers(self):
        conn = _fresh_conn()
        _lock(conn, gate_claim_id="claim-B", staker_id="s1", lock_id="l1")
        _lock(conn, gate_claim_id="claim-B", staker_id="s2", lock_id="l2")
        locks = list_locks_for_claim(conn, "claim-B")
        assert len(locks) == 2
        stakers = {lk.staker_id for lk in locks}
        assert stakers == {"s1", "s2"}

    def test_only_returns_locks_for_matching_claim(self):
        conn = _fresh_conn()
        _lock(conn, gate_claim_id="claim-C", staker_id="s1", lock_id="l1")
        _lock(conn, gate_claim_id="claim-D", staker_id="s2", lock_id="l2")
        assert len(list_locks_for_claim(conn, "claim-C")) == 1
        assert len(list_locks_for_claim(conn, "claim-D")) == 1


# ── Integration: full lifecycle ───────────────────────────────────────────────

class TestLifecycle:
    def test_lock_release_full_path(self):
        conn = _fresh_conn()
        lock = _lock(conn, staker_id="sponsor", amount=500)
        assert lock.is_locked

        released = release_bonus(
            conn, lock_id="lock-001",
            recipient_id="daemon-winner",
            resolved_at="2026-04-25T10:00:00Z",
        )
        assert released.is_released
        assert released.recipient_id == "daemon-winner"
        assert released.amount == 500

        # Verify persisted
        fetched = get_lock(conn, "lock-001")
        assert fetched is not None
        assert fetched.status == "released"

    def test_lock_refund_full_path(self):
        conn = _fresh_conn()
        lock = _lock(conn, staker_id="sponsor", amount=200)
        assert lock.is_locked

        refunded = refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T11:00:00Z")
        assert refunded.is_refunded
        assert refunded.recipient_id == "sponsor"  # refund goes to staker

        fetched = get_lock(conn, "lock-001")
        assert fetched is not None
        assert fetched.status == "refunded"

    def test_zero_amount_lock_refund(self):
        conn = _fresh_conn()
        _lock(conn, amount=0)
        lock = refund_bonus(conn, lock_id="lock-001", resolved_at="2026-04-25T00:00:00Z")
        assert lock.amount == 0
        assert lock.is_refunded
