"""One storage pool per account: measured + pending, never refused on a stale number.

account-storage-quota D4/D5/D8. These drive the real ledger and the real walk
against a temp data root; the tier quota is shrunk through the real env override
so a test writes kilobytes, not gibibytes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tinyassets import storage_accounting as sa
from tinyassets import universe_owner as uo
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

A = "workos|alice"
B = "workos|bob"
KIB = 1024


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    # 100 KiB free quota: the real override, in its real unit.
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(100 * KIB / 1024**3))
    initialize_author_server(root)
    return root


def _universe(base: Path, uid: str, owner: str) -> Path:
    grant_universe_ownership(base, universe_id=uid, owner_id=owner)
    udir = base / uid
    udir.mkdir(exist_ok=True)
    return udir


def _write(udir: Path, name: str, size: int) -> None:
    (udir / name).write_bytes(b"x" * size)


def _admit(base, uid, n, account=A):
    return sa.reserve(base, account_id=account, scope_id=uid, store="universe_files", nbytes=n)


class TestOnePoolPerAccount:
    def test_bytes_in_two_universes_share_one_quota(self, base):
        _write(_universe(base, "u-one", A), "a.bin", 60 * KIB)
        _write(_universe(base, "u-two", A), "b.bin", 30 * KIB)

        assert sa.usage(base, A).used_bytes == 0  # nothing measured yet
        with pytest.raises(sa.StorageRefused) as refused:
            _admit(base, "u-two", 20 * KIB)

        record = refused.value.record
        assert record["failure_class"] == sa.FAILURE_QUOTA
        assert record["used_bytes"] == 90 * KIB
        assert "across 2 command centers" in record["error"]

    def test_another_account_is_unaffected(self, base):
        _write(_universe(base, "u-a", A), "a.bin", 95 * KIB)
        _universe(base, "u-b", B)

        res = _admit(base, "u-b", 50 * KIB, account=B)
        sa.commit(res)

    def test_platform_bytes_are_not_charged(self, base):
        udir = _universe(base, "u-one", A)
        for platform_dir in (".runtime", ".workspace-staging"):
            (udir / platform_dir).mkdir()
            _write(udir / platform_dir, "big.bin", 500 * KIB)
        _write(udir, "mine.bin", 10 * KIB)

        sa.commit(_admit(base, "u-one", 1 * KIB))

        assert sa.usage(base, A).used_bytes == 10 * KIB + 1 * KIB

    def test_permanent_workspaces_are_charged_through_their_own_store(self, base):
        udir = _universe(base, "u-one", A)
        (udir / "workspaces").mkdir()
        _write(udir / "workspaces", "gen.bin", 30 * KIB)

        sa.measure(base, "u-one", "workspaces")
        sa.measure(base, "u-one", "universe_files")

        measured = dict(((s, st), b) for s, st, b in sa.usage(base, A).breakdown)
        assert measured[("u-one", "workspaces")] == 30 * KIB
        assert ("u-one", "universe_files") not in measured  # not counted twice

    def test_a_hard_link_is_counted_once(self, base):
        udir = _universe(base, "u-one", A)
        _write(udir, "a.bin", 40 * KIB)
        (udir / "b.bin").hardlink_to(udir / "a.bin")

        sa.measure(base, "u-one", "universe_files")

        assert sa.usage(base, A).used_bytes == 40 * KIB


class TestPendingIsNeverLost:
    def test_many_small_writes_cannot_slip_between_measurements(self, base):
        _universe(base, "u-one", A)
        admitted = 0
        with pytest.raises(sa.StorageRefused):
            for _ in range(200):
                sa.commit(_admit(base, "u-one", 10 * KIB))  # never written to disk
                admitted += 1

        # 100 KiB quota, 10 KiB each: exactly 10 fit, whatever the measurements say.
        assert admitted == 10

    def test_a_write_committed_after_the_scan_started_stays_pending(self, base, monkeypatch):
        udir = _universe(base, "u-one", A)
        sa.commit(_admit(base, "u-one", 0))  # every store measured once, up front
        real = sa.STORES["universe_files"].measure
        landed = {}

        def _scan_then_race(b, scope):
            size = real(b, scope)  # the scan does not see the write below
            if landed:
                return size
            res = _admit(base, "u-one", 30 * KIB)
            _write(udir, "late.bin", 30 * KIB)
            sa.commit(res)
            landed["yes"] = True
            return size

        monkeypatch.setitem(
            sa.STORES, "universe_files",
            sa.Store("universe_files", sa.SCOPE_UNIVERSE, _scan_then_race),
        )
        sa.measure(base, "u-one", "universe_files")
        monkeypatch.undo()

        assert landed
        assert sa.usage(base, A).used_bytes == 30 * KIB  # not 0: the write is not lost

    def test_concurrent_writes_cannot_share_the_last_headroom(self, base, monkeypatch):
        _universe(base, "u-one", A)
        sa.commit(_admit(base, "u-one", 0))  # every store measured up front
        real_usage = sa._usage_in

        def _slow_decision(*args, **kwargs):
            # Hold the read long enough that every thread's decision overlaps:
            # only a serialized admission keeps them from all seeing headroom.
            result = real_usage(*args, **kwargs)
            time.sleep(0.2)
            return result

        monkeypatch.setattr(sa, "_usage_in", _slow_decision)
        wins, refusals = [], []

        barrier = threading.Barrier(6)

        def _try():
            barrier.wait()
            try:
                wins.append(_admit(base, "u-one", 60 * KIB))
            except sa.StorageRefused as exc:
                refusals.append(exc.record["failure_class"])

        threads = [threading.Thread(target=_try) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(wins) == 1
        # Every loser was decided on the winner's bytes -- not bounced off a
        # lock as "unavailable", which is what an unserialized admission does.
        assert refusals == [sa.FAILURE_QUOTA] * 5

    def test_a_superseded_measurement_is_discarded(self, base, monkeypatch):
        """gpt-6-astra PR #4158: scan S1 starts, a write commits, scan S2 measures
        it and retires its pending row; S1 then lands its smaller number. It
        must be discarded, or the write is counted nowhere."""
        udir = _universe(base, "u-one", A)
        sa.commit(_admit(base, "u-one", 0))
        real = sa.STORES["universe_files"].measure
        state = {"nested": False}

        def _slow_first_scan(b, scope):
            size = real(b, scope)  # S1 sees nothing yet
            if not state["nested"]:
                state["nested"] = True
                res = _admit(base, "u-one", 40 * KIB)
                _write(udir, "late.bin", 40 * KIB)
                sa.commit(res)
                sa.measure(base, "u-one", "universe_files")  # S2: sees it, retires pending
            return size

        monkeypatch.setitem(
            sa.STORES, "universe_files",
            sa.Store("universe_files", sa.SCOPE_UNIVERSE, _slow_first_scan),
        )
        sa.measure(base, "u-one", "universe_files")  # S1 lands last
        monkeypatch.undo()

        assert sa.usage(base, A).used_bytes >= 40 * KIB

    def test_a_write_larger_than_its_reservation_fails_loudly(self, base):
        _universe(base, "u-one", A)
        res = _admit(base, "u-one", 1 * KIB)
        with pytest.raises(ValueError):
            sa.commit(res, 2 * KIB)

    def test_a_released_reservation_frees_its_bytes(self, base):
        _universe(base, "u-one", A)
        sa.release(_admit(base, "u-one", 90 * KIB))

        sa.commit(_admit(base, "u-one", 90 * KIB))


class TestDeleteThenRetry:
    def test_a_delete_frees_space_on_the_retry_without_touch(self, base):
        udir = _universe(base, "u-one", A)
        _write(udir, "big.bin", 90 * KIB)
        with pytest.raises(sa.StorageRefused):
            _admit(base, "u-one", 20 * KIB)

        (udir / "big.bin").unlink()  # no touch() -- correctness must not need it
        # Age the measurement past the freshness bound the refusal path uses.
        conn = sa._connect(base)
        try:
            conn.execute("UPDATE measurements SET measured_at = measured_at - 3600")
        finally:
            conn.close()

        sa.commit(_admit(base, "u-one", 20 * KIB))

    def test_a_fresh_refusal_does_not_rescan(self, base, monkeypatch):
        udir = _universe(base, "u-one", A)
        _write(udir, "big.bin", 90 * KIB)
        with pytest.raises(sa.StorageRefused):
            _admit(base, "u-one", 20 * KIB)
        calls = []
        real = sa.measure
        monkeypatch.setattr(sa, "measure", lambda *a, **k: calls.append(a) or real(*a, **k))

        with pytest.raises(sa.StorageRefused):
            _admit(base, "u-one", 20 * KIB)

        assert calls == []  # measured seconds ago: fresh, no rescan

    def test_touch_marks_dirty_and_the_refusal_path_rescans_it(self, base):
        udir = _universe(base, "u-one", A)
        _write(udir, "big.bin", 90 * KIB)
        with pytest.raises(sa.StorageRefused):
            _admit(base, "u-one", 20 * KIB)
        (udir / "big.bin").unlink()
        sa.touch(base, "u-one", "universe_files")

        sa.commit(_admit(base, "u-one", 20 * KIB))


class TestUnattributedAndUnmeasurable:
    def test_an_unattributed_universe_is_never_refused(self, base):
        udir = base / "u-legacy"
        udir.mkdir()
        _write(udir, "big.bin", 500 * KIB)

        res = sa.reserve(
            base, account_id=None, scope_id="u-legacy", store="universe_files", nbytes=500 * KIB,
        )
        sa.commit(res)
        assert res.id is None

    def test_an_unmeasurable_store_is_still_bounded_by_known_bytes(self, base, monkeypatch):
        """A store that cannot be measured counts as 0, but every admitted write
        is pending, so the allowance is bounded -- never unlimited."""
        _universe(base, "u-one", A)

        def _broken(_b, _s):
            raise OSError("disk unreadable")

        monkeypatch.setitem(
            sa.STORES, "universe_files", sa.Store("universe_files", sa.SCOPE_UNIVERSE, _broken),
        )
        sa.commit(_admit(base, "u-one", 60 * KIB))
        with pytest.raises(sa.StorageRefused) as refused:
            _admit(base, "u-one", 60 * KIB)

        assert refused.value.record["failure_class"] == sa.FAILURE_QUOTA

    def test_known_measured_bytes_count_even_when_another_store_fails(self, base, monkeypatch):
        """The bug this guards: one broken store must not make the others' bytes
        vanish from the decision."""
        _write(_universe(base, "u-one", A), "big.bin", 90 * KIB)

        def _broken(_b, _s):
            raise OSError("unreadable")

        monkeypatch.setitem(
            sa.STORES, "ui_library", sa.Store("ui_library", sa.SCOPE_ACCOUNT, _broken),
        )
        with pytest.raises(sa.StorageRefused):
            _admit(base, "u-one", 20 * KIB)

    def test_an_unopenable_ledger_is_the_hosts_problem_and_says_so(self, base, monkeypatch):
        import sqlite3

        _universe(base, "u-one", A)

        def _locked(_b):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(sa, "_connect", _locked)
        with pytest.raises(sa.StorageRefused) as refused:
            _admit(base, "u-one", 1 * KIB)

        assert refused.value.record["failure_class"] == sa.FAILURE_UNAVAILABLE
        assert refused.value.record["actionable_by"] == "host"

    def test_a_crashed_reservation_expires(self, base):
        _universe(base, "u-one", A)
        _admit(base, "u-one", 90 * KIB)  # never committed nor released
        conn = sa._connect(base)
        try:
            conn.execute("UPDATE pending SET created_at = created_at - ?", (sa.RESERVED_TTL_S + 1,))
        finally:
            conn.close()

        sa.measure(base, "u-one", "universe_files")

        sa.commit(_admit(base, "u-one", 90 * KIB))


class TestTheRefusal:
    @pytest.mark.parametrize("viewer", [A, B, ""])
    def test_accounting_components_are_truthful_and_visible_only_to_owner(
        self, base, signed_in, viewer,
    ):
        root = _universe(base, "u-one", A)
        _write(root, "retained.bin", 10 * KIB)
        _universe(base, "u-other-private", B)
        reserved = _admit(base, "u-one", 20 * KIB)
        landed = _admit(base, "u-one", 30 * KIB)
        _write(root, "landed.bin", 30 * KIB)
        sa.commit(landed)
        # Another owner's pending bytes must never enter this number.
        other = _admit(base, "u-other-private", 90 * KIB, account=B)
        try:
            with pytest.raises(sa.StorageRefused) as refused:
                _admit(base, "u-one", 50 * KIB)
            record = refused.value.record
            assert record["measured_bytes"] == 10 * KIB
            assert record["reserved_bytes"] == 20 * KIB
            assert record["committed_bytes"] == 30 * KIB
            assert record["used_bytes"] == 60 * KIB
            assert "Reservations may clear" in record["error"]
            assert "u-other-private" not in str(record)
            assert str(base) not in str(record)
            visible = sa.visible_record(refused.value, viewer=viewer)
            if viewer == A:
                assert visible == record
            else:
                assert visible == sa._OTHER_ACCOUNT_FULL
                assert set(visible) == {"error", "failure_class", "actionable_by"}
            if viewer:
                signed_in(viewer)
                assert sa.visible_record(refused.value) == visible
            # Measurement absorbs only the landed committed part. Outstanding
            # speculative bytes remain reserved, with the same total charge.
            sa.measure(base, "u-one", "universe_files")
            usage = sa.usage(base, A)
            assert usage.measured_bytes == 40 * KIB
            assert usage.committed_bytes == 0
            assert usage.reserved_bytes == 20 * KIB
            assert usage.used_bytes == 60 * KIB
        finally:
            sa.release(reserved)
            sa.release(other)

    def test_free_refusal_carries_the_inline_upgrade_link(self, base):
        _write(_universe(base, "u-one", A), "big.bin", 95 * KIB)
        with pytest.raises(sa.StorageRefused) as refused:
            _admit(base, "u-one", 20 * KIB)

        message = refused.value.record["error"]
        assert "[Upgrade](https://tinyassets.io/app?upgrade=1)" in message
        assert refused.value.record["actionable_by"] == "user"
        assert refused.value.record["largest"][0]["scope_id"] == "u-one"

    def test_top_tier_refusal_has_no_link(self, base, monkeypatch):
        _write(_universe(base, "u-one", A), "big.bin", 95 * KIB)
        from tinyassets.usage_policy import AccountType

        monkeypatch.setattr(
            uo, "account_type_of", lambda *_a, **_k: AccountType.SUBSCRIPTION,
        )
        monkeypatch.setenv("TINYASSETS_PAID_STORAGE_GIB", str(100 * KIB / 1024**3))

        with pytest.raises(sa.StorageRefused) as refused:
            _admit(base, "u-one", 20 * KIB)

        assert "Upgrade" not in refused.value.record["error"]

    def test_a_scope_outside_the_account_is_a_bug_not_a_charge(self, base):
        _universe(base, "u-a", A)
        _universe(base, "u-b", B)

        with pytest.raises(ValueError):
            _admit(base, "u-b", 1 * KIB, account=A)


def test_actor_resolution(base):
    _universe(base, "u-one", A)

    assert sa.account_for_actor(base, A) == A
    assert sa.account_for_actor(base, "universe:u-one") == A
    assert sa.account_for_actor(base, "universe:u-nobody") is None
    assert sa.account_for_actor(base, "") is None


def test_measurement_is_off_the_clock_of_the_admission(base):
    """The admission transaction never walks the filesystem."""
    _universe(base, "u-one", A)
    sa.measure(base, "u-one", "universe_files")
    started = time.monotonic()
    sa.commit(_admit(base, "u-one", 1 * KIB))
    assert time.monotonic() - started < 5


def _fresh_ledger_held(tmp_path):
    """A fresh rollback-journal ledger with another connection's write lock held."""
    import sqlite3

    tmp_path.mkdir(exist_ok=True)
    holder = sqlite3.connect(
        sa.ledger_path(tmp_path), isolation_level=None, check_same_thread=False,
    )
    holder.execute("CREATE TABLE IF NOT EXISTS other (x)")  # a fresh, non-WAL file
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_first_contact_waits_out_a_concurrent_wal_switch(tmp_path):
    """A fresh ledger whose first switch to WAL meets another writer waits for it.

    SQLite skips the busy handler for the read-to-write upgrade inside that
    switch, so this used to raise "database is locked" at once, and an
    admission refused the write as unmeasurable: the concurrent branch-create
    test failed about 1 run in 10. A held write lock reproduces it every time.
    """
    holder = _fresh_ledger_held(tmp_path)
    releaser = threading.Timer(0.3, lambda: holder.execute("COMMIT"))
    started = time.monotonic()
    releaser.start()
    try:
        conn = sa._connect(tmp_path)
        waited = time.monotonic() - started
    finally:
        releaser.join()
        holder.close()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert waited >= 0.25  # it waited, it did not race past
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
    finally:
        conn.close()


def test_a_held_wal_switch_gives_up_at_one_deadline(tmp_path, monkeypatch):
    """The busy handler is off inside the loop, so one deadline bounds the wait:
    a writer that never lets go is refused after it, not after it twice."""
    import sqlite3

    monkeypatch.setattr(sa, "_BUSY_TIMEOUT_S", 0.4)
    holder = _fresh_ledger_held(tmp_path)
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            sa._connect(tmp_path)
        waited = time.monotonic() - started
    finally:
        holder.close()
    assert 0.35 <= waited < 0.75


def test_a_wal_switch_that_is_not_busy_fails_on_the_first_attempt():
    import sqlite3

    attempts = []

    class Broken:
        def execute(self, sql):
            if sql.startswith("PRAGMA journal_mode"):
                attempts.append(sql)
                exc = sqlite3.OperationalError("disk I/O error")
                exc.sqlite_errorcode = sqlite3.SQLITE_IOERR
                raise exc

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        sa._enable_wal(Broken())
    assert len(attempts) == 1
