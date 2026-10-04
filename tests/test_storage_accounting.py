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


def test_owner_refusal_distinguishes_measurements_and_pending_states(base):
    root = _universe(base, "u-private", A)
    _write(root, "private.bin", 10 * KIB)
    active = _admit(base, root.name, 60 * KIB)
    committed = _admit(base, root.name, 20 * KIB)
    sa.commit(committed)
    with pytest.raises(sa.StorageRefused) as caught:
        _admit(base, root.name, 11 * KIB)
    refused = caught.value
    record = sa.visible_record(refused, A)
    assert record["measured_bytes"] == 10 * KIB
    assert record["reserved_bytes"] == 60 * KIB
    assert record["committed_bytes"] == 20 * KIB
    assert record["used_bytes"] == 90 * KIB
    assert "measured" in record["error"]
    assert "reserved for in-flight writes" in record["error"]
    assert "committed pending remeasurement" in record["error"]
    assert "retry then" in record["error"]
    for viewer in (B, "", "unknown"):
        assert sa.visible_record(refused, viewer) == sa._OTHER_ACCOUNT_FULL
    sa.release(active)
    sa.measure(base, root.name, "universe_files")
    current = sa.usage(base, A)
    assert current.reserved_bytes == current.committed_bytes == 0
    assert "retry then" not in sa.refusal_record(current, 100 * KIB, universes=1)["error"]


class TestOnePoolPerAccount:
    def test_platform_consent_artifacts_do_not_exhaust_the_owners_pool(self, base):
        udir = _universe(base, "u-one", A)
        _write(udir, "mine.bin", 10 * KIB)
        sidecar = base / ".universe-sidecars" / udir.name
        sidecar.mkdir(parents=True)
        _write(sidecar, ".effector_consents.db", 200 * KIB)
        _write(_universe(base, "u-other", B), "other.bin", 500 * KIB)

        reservation = _admit(base, udir.name, 20 * KIB)
        assert sa.usage(base, A).measured_bytes == 10 * KIB
        sa.release(reservation)

    @pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal", ".premigration"])
    @pytest.mark.parametrize("nested", [False, True])
    def test_consent_lookalike_growth_remains_charged_between_runs(self, base, suffix, nested):
        udir = _universe(base, "u-one", A)
        target = udir / "nested" if nested else udir
        target.mkdir(exist_ok=True)
        path = target / (".effector_consents.db" + suffix)
        # Three individually admitted writes must consume the shared quota even
        # after their reservations have been reconciled with actual file bytes.
        for run in range(3):
            reservation = _admit(base, udir.name, 30 * KIB)
            with path.open("ab") as stream:
                stream.write(b"x" * (30 * KIB))
            sa.commit(reservation)
            sa.measure(base, udir.name, "universe_files")
            assert sa.usage(base, A).used_bytes == (run + 1) * 30 * KIB
        with pytest.raises(sa.StorageRefused):
            _admit(base, udir.name, 20 * KIB)

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


class TestFittedAdmissionConcurrency:
    @pytest.mark.parametrize("separate_owners", [False, True])
    def test_concurrent_requests_fit_the_capacity_at_admission(
        self, base, monkeypatch, separate_owners,
    ):
        from concurrent.futures import ThreadPoolExecutor

        owners = (A, B if separate_owners else A)
        for uid, owner in zip(("u-one", "u-two"), owners):
            _universe(base, uid, owner)
            sa.release(_admit(base, uid, 0, owner))
        real_usage = sa._usage_in
        stale_reads = threading.Barrier(2)
        seen = threading.local()

        def synchronize_unlocked_reads(conn, *args, **kwargs):
            result = real_usage(conn, *args, **kwargs)
            # Force the old read-then-reserve race. Atomic decisions do not
            # enter this barrier while holding the serialized transaction.
            if not conn.in_transaction and not getattr(seen, "read", False):
                seen.read = True
                stale_reads.wait(timeout=10)
            return result

        monkeypatch.setattr(sa, "_usage_in", synchronize_unlocked_reads)

        def fit(item):
            uid, owner = item
            return sa.reserve_fitted(
                base, account_id=owner, scope_id=uid, store="universe_files",
                cap=60 * KIB, minimum=1,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            admitted = list(pool.map(fit, zip(("u-one", "u-two"), owners)))
        monkeypatch.setattr(sa, "_usage_in", real_usage)
        assert sorted(bound for _, bound in admitted) == (
            [60 * KIB, 60 * KIB] if separate_owners else [40 * KIB, 60 * KIB]
        )
        for owner in set(owners):
            assert sa.usage(base, owner).used_bytes <= 100 * KIB
        for reservation, bound in admitted:
            assert reservation.bytes == bound
            sa.release(reservation)

    @pytest.mark.parametrize(
        "retained,credit,minimum,expected",
        [(30, 0, 1, 70), (90, 50, 1, 60), (100, 30, 1, 30),
         (100, 0, 0, 0), (101, 50, 0, None), (90, 0, 11, None)],
    )
    def test_fitted_bounds_preserve_quota_and_replacement_credit(
        self, base, retained, credit, minimum, expected,
    ):
        udir = _universe(base, "u-one", A)
        _write(udir, "retained.bin", retained * KIB)
        kwargs = dict(
            account_id=A, scope_id=udir.name, store="universe_files",
            cap=100 * KIB, credit=credit * KIB, minimum=minimum * KIB,
        )
        if expected is None:
            with pytest.raises(sa.StorageRefused) as refused:
                sa.reserve_fitted(base, **kwargs)
            assert refused.value.record["failure_class"] == sa.FAILURE_QUOTA
        else:
            reservation, bound = sa.reserve_fitted(base, **kwargs)
            assert bound == expected * KIB
            assert reservation.bytes == max(0, expected - credit) * KIB
            assert sa.usage(base, A).used_bytes <= 100 * KIB
            sa.release(reservation)

    def test_fitted_admission_preserves_account_membership_and_unattributed_policy(self, base):
        _universe(base, "u-bob", B)
        with pytest.raises(ValueError, match="not part of this account"):
            sa.reserve_fitted(
                base, account_id=A, scope_id="u-bob", store="universe_files", cap=KIB,
            )
        with pytest.raises(KeyError, match="unregistered store"):
            sa.reserve_fitted(base, account_id=A, scope_id="u-one", store="missing", cap=KIB)
        reservation, bound = sa.reserve_fitted(
            base, account_id=None, scope_id="u-unowned", store="universe_files", cap=KIB,
        )
        assert reservation.id is None and bound == KIB

    def test_a_fitted_admission_ledger_error_is_not_reported_as_quota(self, base, monkeypatch):
        import sqlite3

        _universe(base, "u-one", A)

        def unavailable(_base):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(sa, "_connect", unavailable)
        with pytest.raises(sa.StorageRefused) as refused:
            sa.reserve_fitted(
                base, account_id=A, scope_id="u-one", store="universe_files", cap=KIB,
            )
        assert refused.value.record["failure_class"] == sa.FAILURE_UNAVAILABLE


class TestRawWriteMeasurementOrdering:
    @pytest.mark.parametrize("newer_fails", [False, True])
    def test_an_older_scan_cannot_replace_a_newer_successful_raw_write_scan(
        self, base, monkeypatch, newer_fails,
    ):
        udir = _universe(base, "u-one", A)
        sa.release(_admit(base, "u-one", 0))
        _write(udir, "before.bin", 10 * KIB)
        real = sa.STORES["universe_files"].measure
        nested = False

        def overlapping_scan(root, scope):
            nonlocal nested
            if nested and newer_fails:
                raise OSError("incomplete newer scan")
            size = real(root, scope)
            if not nested:
                nested = True
                # Raw jailed writes do not commit an accounting reservation.
                _write(udir, "raw.bin", 40 * KIB)
                if newer_fails:
                    with pytest.raises(OSError, match="incomplete newer scan"):
                        sa.measure(base, scope, "universe_files")
                else:
                    sa.measure(base, scope, "universe_files")
            return size

        monkeypatch.setitem(
            sa.STORES, "universe_files",
            sa.Store("universe_files", sa.SCOPE_UNIVERSE, overlapping_scan),
        )
        sa.measure(base, "u-one", "universe_files")
        assert sa.usage(base, A).used_bytes == (10 if newer_fails else 50) * KIB
        monkeypatch.setitem(
            sa.STORES, "universe_files", sa.Store("universe_files", sa.SCOPE_UNIVERSE, real),
        )
        sa.measure(base, "u-one", "universe_files")
        assert sa.usage(base, A).used_bytes == 50 * KIB
