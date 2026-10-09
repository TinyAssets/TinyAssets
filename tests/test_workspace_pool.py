"""The scratch lease pool, its hourly ledger, and the terminal outbox.

Every scenario of ``openspec/changes/workspace-node/specs/scratch-storage``,
plus the durable job lock (``graph-execution-substrate``) and the pre-wire
reservation (``engine-run-admissions``). The filesystem is injected: a fake
drives every crash window deterministically, and ``RealPoolFilesystem`` proves
the no-follow deletion against real bytes under ``tmp_path``.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from tinyassets import workspace_pool as wp
from tinyassets.workspace_fs import RealPoolFilesystem

GIB = wp.GIB


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Roots:
    pool: Path
    universe: Path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "runs.db"


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(pool=tmp_path / "scratch", universe=tmp_path / "universe")


class FakeFs:
    """A ``PoolFilesystem`` over a set of paths, with an ordered op log."""

    def __init__(self, present: tuple[Path | str, ...] = (), fail_remove: Path | str | None = None):
        self.present: set[str] = {str(p) for p in present}
        self.ops: list[tuple] = []
        self.fail_remove = None if fail_remove is None else str(fail_remove)

    def exists(self, path: Path) -> bool:
        return str(path) in self.present

    def rename(self, src: Path, dst: Path) -> None:
        if str(src) not in self.present:
            raise FileNotFoundError(str(src))
        self.present.discard(str(src))
        self.present.add(str(dst))
        self.ops.append(("rename", str(src), str(dst)))

    def owner_scoped(self, path: Path) -> bool:
        return False

    def remove_tree_no_follow(self, path: Path) -> None:
        if self.fail_remove is not None and str(path) == self.fail_remove:
            self.ops.append(("remove_failed", str(path)))
            raise OSError(13, "permission denied")
        self.ops.append(("remove", str(path)))
        self.present.discard(str(path))


def _ids(*names: str):
    """A deterministic ``lease_id_factory`` handing out ``names`` in order."""
    remaining = list(names)

    def factory() -> str:
        return remaining.pop(0)

    return factory


def admit_scratch(db: Path, roots: Roots, **kw):
    params: dict = dict(
        universe_id="u1",
        connection_id="c1",
        repo_key="repo",
        storage_class=wp.STORAGE_SCRATCH,
        run_id="run-1",
        max_bytes=GIB,
        pool_root=roots.pool,
        universe_root=roots.universe,
    )
    params.update(kw)
    return wp.admit(db, **params)


def admit_universe(db: Path, roots: Roots, **kw):
    params: dict = dict(
        universe_id="u1",
        connection_id="c1",
        repo_key="repo",
        storage_class=wp.STORAGE_UNIVERSE,
        run_id="run-1",
        max_bytes=GIB,
        pool_root=roots.pool,
        universe_root=roots.universe,
        universe_quota_bytes=10 * GIB,
        universe_used_bytes_fn=lambda _u: 0,
    )
    params.update(kw)
    return wp.admit(db, **params)


@contextlib.contextmanager
def terminal_txn(db: Path):
    """What ``runs.py`` will do: the run's terminal row and the outbox entries
    in ONE transaction the pool functions join."""
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(db: Path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db), timeout=30)
    try:
        return list(conn.execute(sql, args))
    finally:
        conn.close()


def lock_rows(db: Path) -> list[tuple]:
    return rows(db, 'SELECT scope, "key", run_id FROM workspace_locks ORDER BY scope')


def lease_state(db: Path, lease_id: str) -> str:
    return rows(db, "SELECT state FROM workspace_leases WHERE lease_id = ?", (lease_id,))[0][0]


def make_dir_link(link: Path, target: Path) -> str | None:
    """A symlink, else a Windows junction (no privilege needed), else None."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and os.path.lexists(link):
            return "junction"
    return None


# --------------------------------------------------------------------------
# admission: shape, paths, and the two storage classes
# --------------------------------------------------------------------------


def test_scratch_admission_is_active_with_computed_paths(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    assert lease.lease_id == "lease1"
    assert lease.state == wp.STATE_ACTIVE
    assert lease.generation == 1
    assert lease.reserved_bytes == wp.DEFAULT_LEASE_BYTES_CAP
    assert lease.path == roots.pool / "lease1"
    assert lease.quarantine_path == roots.pool / ".quarantine" / "lease1.1"
    # The module computes the path; the caller creates it under a held handle.
    assert not lease.path.exists()
    assert not roots.pool.exists()


def test_universe_admission_paths_are_the_generation_and_its_quarantine(
    db: Path, roots: Roots
) -> None:
    lease = admit_universe(db, roots, lease_id_factory=_ids("lease1"))
    assert lease.storage_class == wp.STORAGE_UNIVERSE
    assert lease.path == roots.universe / "workspaces" / "repo" / "1"
    assert lease.quarantine_path == roots.universe / "workspaces" / ".quarantine" / "repo.1"
    assert lease.reserved_bytes == GIB


def test_a_large_checkout_does_not_enlarge_the_universe(db: Path, roots: Roots) -> None:
    """scratch-storage: a 3 GiB scratch checkout raises the POOL's reserved
    bytes by the lease bound and touches no universe quota."""
    calls: list[str] = []
    lease = admit_scratch(
        db,
        roots,
        max_bytes=3 * GIB,
        universe_quota_bytes=1,
        universe_used_bytes_fn=lambda u: calls.append(u) or 0,
    )
    assert calls == []  # the universe's quota is never consulted for scratch
    usage = wp.pool_usage(db)
    assert usage.reserved_bytes == wp.DEFAULT_LEASE_BYTES_CAP
    assert usage.active_leases == 1
    assert lease.storage_class == wp.STORAGE_SCRATCH


def test_a_permanent_generation_does_not_consume_the_scratch_pool(
    db: Path, roots: Roots
) -> None:
    admit_universe(db, roots, max_bytes=3 * GIB)
    assert wp.pool_usage(db).reserved_bytes == 0


def test_a_repo_key_that_could_escape_its_directory_is_refused(db: Path, roots: Roots) -> None:
    with pytest.raises(ValueError, match="repo_key"):
        admit_scratch(db, roots, repo_key="../../etc")


def test_a_lease_larger_than_the_lease_bound_is_refused(db: Path, roots: Roots) -> None:
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(db, roots, max_bytes=5 * GIB, lease_bytes_cap=4 * GIB)
    assert exc.value.code == wp.REFUSED_QUOTA
    assert "lease bound" in exc.value.detail


# --------------------------------------------------------------------------
# the pool bound
# --------------------------------------------------------------------------


def test_two_admissions_cannot_oversubscribe_the_pool(db: Path, roots: Roots) -> None:
    """scratch-storage: 5 GiB left, two 4 GiB leases, exactly one admitted."""
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    lock = threading.Lock()

    def attempt(name: str) -> None:
        barrier.wait(timeout=30)
        try:
            outcome: object = wp.admit(
                db,
                universe_id=f"u-{name}",
                connection_id="c1",
                repo_key="repo",
                storage_class=wp.STORAGE_SCRATCH,
                run_id=f"run-{name}",
                max_bytes=4 * GIB,
                pool_root=roots.pool,
                universe_root=roots.universe,
                pool_bytes_cap=5 * GIB,
                lease_bytes_cap=4 * GIB,
                lease_id_factory=_ids(f"lease{name}"),
            )
        except wp.WorkspacePoolRefused as exc:
            outcome = exc
        with lock:
            results[name] = outcome

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    admitted = [r for r in results.values() if isinstance(r, wp.Lease)]
    refused = [r for r in results.values() if isinstance(r, wp.WorkspacePoolRefused)]
    assert len(admitted) == 1, results
    assert len(refused) == 1, results
    assert refused[0].code == wp.REFUSED_POOL_BUSY
    assert wp.pool_usage(db).reserved_bytes == 4 * GIB


def test_lost_bytes_still_count_against_the_pool(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"), max_bytes=4 * GIB)
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    fs = FakeFs(present=(lease.path,), fail_remove=lease.quarantine_path)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert wp.process_entry(db, entry, fs=fs) == wp.OUTCOME_LOST

    usage = wp.pool_usage(db)
    assert usage.lost_leases == 1
    assert usage.lost_bytes == wp.DEFAULT_LEASE_BYTES_CAP
    assert usage.reserved_bytes == wp.DEFAULT_LEASE_BYTES_CAP  # charged forever

    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(
            db,
            roots,
            run_id="run-2",
            pool_bytes_cap=5 * GIB,
            lease_bytes_cap=4 * GIB,
            max_bytes=4 * GIB,
            lease_id_factory=_ids("lease2"),
        )
    assert exc.value.code == wp.REFUSED_POOL_BUSY


def test_a_released_lease_frees_its_pool_bytes(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert wp.process_entry(db, entry, fs=FakeFs(present=(lease.path,))) == wp.OUTCOME_WIPED
    assert lease_state(db, "lease1") == wp.STATE_AVAILABLE
    assert wp.pool_usage(db).reserved_bytes == 0


# --------------------------------------------------------------------------
# the universe quota
# --------------------------------------------------------------------------


def test_a_quota_refusal_destroys_nothing(db: Path, roots: Roots) -> None:
    """scratch-storage: the existing generation is untouched by a refusal."""
    first = admit_universe(db, roots, lease_id_factory=_ids("lease1"), max_bytes=GIB)
    with terminal_txn(db) as conn:
        previous = wp.publish_generation(
            conn,
            universe_id="u1",
            repo_key="repo",
            generation=first.generation,
            path=first.path,
        )
    assert previous is None
    published_before = rows(db, "SELECT generation, path FROM workspace_generations")

    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_universe(
            db,
            roots,
            run_id="run-2",
            max_bytes=2 * GIB,
            universe_quota_bytes=10 * GIB,
            universe_used_bytes_fn=lambda _u: 9 * GIB,
            lease_id_factory=_ids("lease2"),
        )
    assert exc.value.code == wp.REFUSED_QUOTA
    assert rows(db, "SELECT generation, path FROM workspace_generations") == published_before
    assert rows(db, "SELECT lease_id FROM workspace_leases") == [("lease1",)]
    assert lease_state(db, "lease1") == wp.STATE_ACTIVE


def test_publish_generation_returns_the_previous_one(db: Path, roots: Roots) -> None:
    first = admit_universe(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        assert wp.publish_generation(
            conn, universe_id="u1", repo_key="repo", generation=1, path=first.path
        ) is None
    second = admit_universe(db, roots, lease_id_factory=_ids("lease2"))
    assert second.generation == 2
    with terminal_txn(db) as conn:
        assert (
            wp.publish_generation(
                conn, universe_id="u1", repo_key="repo", generation=2, path=second.path
            )
            == 1
        )
    assert rows(db, "SELECT generation, path FROM workspace_generations") == [
        (2, str(second.path))
    ]


def test_publishing_a_generation_backwards_is_a_loud_error(db: Path, roots: Roots) -> None:
    with terminal_txn(db) as conn:
        wp.publish_generation(
            conn, universe_id="u1", repo_key="repo", generation=4, path=roots.universe / "g4"
        )
    with pytest.raises(ValueError, match="does not follow"):
        with terminal_txn(db) as conn:
            wp.publish_generation(
                conn, universe_id="u1", repo_key="repo", generation=3, path=roots.universe / "g3"
            )


# --------------------------------------------------------------------------
# the durable job locks
# --------------------------------------------------------------------------


def test_the_lock_is_reentrant_for_the_same_run(db: Path, roots: Roots) -> None:
    """graph-execution-substrate: checkout, then a later node, then the push."""
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    admit_scratch(db, roots, lease_id_factory=_ids("lease2"))
    assert lock_rows(db) == [("host", "slot-0", "run-1"), ("universe", "u1", "run-1")]


def test_another_run_in_the_same_universe_is_workspace_busy(db: Path, roots: Roots) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(db, roots, run_id="run-2", lease_id_factory=_ids("lease2"))
    assert exc.value.code == wp.REFUSED_BUSY
    assert "universe lock" in exc.value.detail
    assert rows(db, "SELECT lease_id FROM workspace_leases") == [("lease1",)]


def test_the_host_slot_holds_off_another_universe_and_rolls_its_lock_back(
    db: Path, roots: Roots
) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(
            db, roots, universe_id="u2", run_id="run-2", lease_id_factory=_ids("lease2")
        )
    assert exc.value.code == wp.REFUSED_BUSY
    assert "host lock" in exc.value.detail
    # The universe lock u2 acquired a moment earlier rolled back with everything else.
    assert lock_rows(db) == [("host", "slot-0", "run-1"), ("universe", "u1", "run-1")]


def test_processing_the_terminal_entry_releases_both_locks(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    assert len(lock_rows(db)) == 2
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    wp.process_entry(db, entry, fs=FakeFs(present=(lease.path,)))
    assert lock_rows(db) == []
    # And the next universe's run is admitted where it was refused before.
    admit_scratch(db, roots, universe_id="u2", run_id="run-2", lease_id_factory=_ids("lease2"))


def test_a_lock_held_by_another_run_is_not_released_by_this_entry(
    db: Path, roots: Roots
) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-other", universe_id="u1", lease=None)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert wp.process_entry(db, entry, fs=FakeFs()) == wp.OUTCOME_RELEASED
    assert lock_rows(db) == [("host", "slot-0", "run-1"), ("universe", "u1", "run-1")]


# --------------------------------------------------------------------------
# the rolling-hour ledger (engine-run-admissions)
# --------------------------------------------------------------------------


def test_more_than_ten_released_jobs_are_observations_not_a_second_starts_cap(
    db: Path, roots: Roots,
) -> None:
    clock = [1_000_000.0]
    for index in range(11):
        run_id = f"run-{index}"
        lease = admit_scratch(
            db,
            roots,
            run_id=run_id,
            lease_id_factory=_ids(f"lease{index}"),
            now=lambda: clock[0],
        )
        with terminal_txn(db) as conn:
            wp.enqueue_terminal(conn, run_id=run_id, universe_id="u1", lease=lease)
        entry = wp.claim_next(db, claimant="test")
        assert entry is not None
        wp.process_entry(db, entry, fs=FakeFs(present=(lease.path,)))
        assert lock_rows(db) == []

    usage = wp.ledger_usage(db, "u1", now=lambda: clock[0])
    assert usage.jobs == 11
    assert usage.bytes == 11 * GIB
    assert usage.clears_at == clock[0] + wp.WINDOW_S


def test_more_than_ten_zero_byte_operations_remain_releasable(db: Path) -> None:
    for index in range(11):
        assert wp.reserve_operation_bytes(
            db, universe_id="u1", run_id="run-1",
            operation_id=f"discard-{index}", max_bytes=0,
        ) == 0
    assert wp.ledger_usage(db, "u1").jobs == 11
    assert wp.ledger_usage(db, "u1").bytes == 0


def test_charges_outside_the_window_do_not_count(db: Path, roots: Roots) -> None:
    clock = [1_000_000.0]
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"), now=lambda: clock[0])
    later = clock[0] + wp.WINDOW_S + 1
    admit_scratch(db, roots, lease_id_factory=_ids("lease2"), now=lambda: later)
    assert wp.ledger_usage(db, "u1", now=lambda: later).jobs == 1


def test_the_reservation_reconciles_downward_only(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, max_bytes=4 * GIB, lease_id_factory=_ids("lease1"))
    assert wp.ledger_usage(db, "u1").bytes == 4 * GIB
    assert wp.reconcile_bytes(db, lease.lease_id, GIB) == GIB
    assert wp.ledger_usage(db, "u1").bytes == GIB
    stored = wp.get_lease(db, "lease1")
    assert stored is not None
    assert stored.measured_bytes == GIB
    # The POOL reservation stands until release, whatever the transfer measured.
    assert stored.reserved_bytes == wp.DEFAULT_LEASE_BYTES_CAP
    # A measurement above the reservation is clamped, never charged upward.
    assert wp.reconcile_bytes(db, lease.lease_id, 9 * GIB) == GIB
    assert wp.ledger_usage(db, "u1").bytes == GIB


def test_an_interrupted_transfer_keeps_the_maximum_charged(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, max_bytes=4 * GIB, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:  # the run dies; reconcile_bytes is never called
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    wp.process_entry(db, entry, fs=FakeFs(present=(lease.path,)))
    assert wp.ledger_usage(db, "u1").bytes == 4 * GIB
    assert rows(db, "SELECT reserved FROM workspace_ledger WHERE kind = 'bytes'") == [(1,)]


def test_reconciling_an_unknown_lease_is_a_loud_error(db: Path, roots: Roots) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with pytest.raises(ValueError, match="unknown lease"):
        wp.reconcile_bytes(db, "nope", 1)


# --------------------------------------------------------------------------
# the outbox processor
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src_present,quarantine_present",
    [(True, False), (False, True), (False, False), (True, True)],
)
def test_every_crash_window_reconciles_on_retry(
    db: Path, roots: Roots, src_present: bool, quarantine_present: bool
) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    present: list[Path] = []
    if src_present:
        present.append(lease.path)
    if quarantine_present:
        present.append(lease.quarantine_path)
    fs = FakeFs(present=tuple(present))
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None

    assert wp.process_entry(db, entry, fs=fs) == wp.OUTCOME_WIPED
    assert fs.present == set()
    assert lease_state(db, "lease1") == wp.STATE_AVAILABLE
    if src_present and quarantine_present:
        # The stale quarantine goes FIRST, or the rename would fail on it.
        assert fs.ops == [
            ("remove", str(lease.quarantine_path)),
            ("rename", str(lease.path), str(lease.quarantine_path)),
            ("remove", str(lease.quarantine_path)),
        ]
    elif src_present:
        assert fs.ops[0] == ("rename", str(lease.path), str(lease.quarantine_path))
    elif quarantine_present:
        assert fs.ops == [("remove", str(lease.quarantine_path))]
    else:
        assert fs.ops == []


def test_a_failed_wipe_is_lost_reported_and_still_releases_the_locks(
    db: Path, roots: Roots
) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    fs = FakeFs(present=(lease.path,), fail_remove=lease.quarantine_path)
    assert wp.process_entry(db, entry, fs=fs) == wp.OUTCOME_LOST
    assert lease_state(db, "lease1") == wp.STATE_LOST
    assert lock_rows(db) == []
    outcome = rows(db, "SELECT outcome, done_at FROM workspace_outbox")[0]
    assert outcome[0].startswith("lost: ")
    assert "permission denied" in outcome[0]
    assert outcome[1] is not None


def test_a_lost_claim_changes_nothing(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    stale = wp.claim_next(db, claimant="slow")
    assert stale is not None
    stolen = wp.claim_next(db, claimant="sweeper", claim_ttl_s=wp.STEAL_ALL_TTL_S)
    assert stolen is not None
    assert stolen.entry_id == stale.entry_id
    assert stolen.claim_token != stale.claim_token

    assert wp.process_entry(db, stale, fs=FakeFs(present=(lease.path,))) == wp.OUTCOME_LOST_CLAIM
    assert lease_state(db, "lease1") == wp.STATE_ACTIVE
    assert len(lock_rows(db)) == 2
    assert rows(db, "SELECT done_at, outcome FROM workspace_outbox") == [(None, None)]

    assert wp.process_entry(db, stolen, fs=FakeFs(present=(lease.path,))) == wp.OUTCOME_WIPED
    assert lease_state(db, "lease1") == wp.STATE_AVAILABLE


def test_processing_an_entry_twice_is_a_no_op(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert wp.process_entry(db, entry, fs=FakeFs(present=(lease.path,))) == wp.OUTCOME_WIPED
    assert wp.process_entry(db, entry, fs=FakeFs()) == wp.OUTCOME_ALREADY_DONE
    assert len(rows(db, "SELECT entry_id FROM workspace_outbox WHERE done_at IS NOT NULL")) == 1


def test_the_claim_token_names_its_claimant(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="processor-7")
    assert entry is not None
    assert entry.claim_token is not None
    assert entry.claim_token.startswith("processor-7:")
    assert wp.claim_next(db, claimant="other") is None  # a fresh claim is respected


def test_a_permanent_discard_quarantines_by_repo_key_and_generation(
    db: Path, roots: Roots
) -> None:
    first = admit_universe(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.publish_generation(
            conn, universe_id="u1", repo_key="repo", generation=1, path=first.path
        )
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=first)
    assert wp.startup_sweep(db, fs=FakeFs(), claimant="startup") == 1

    second = admit_universe(db, roots, run_id="run-2", lease_id_factory=_ids("lease2"))
    with terminal_txn(db) as conn:
        previous = wp.publish_generation(
            conn, universe_id="u1", repo_key="repo", generation=2, path=second.path
        )
        assert previous == 1
        wp.enqueue_discard(
            conn,
            run_id="run-2",
            universe_id="u1",
            storage_class=wp.STORAGE_UNIVERSE,
            repo_key="repo",
            generation=previous,
        )
    fs = FakeFs(present=(first.path,))
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert entry.action == wp.ACTION_DISCARD_PERMANENT
    assert wp.process_entry(db, entry, fs=fs) == wp.OUTCOME_WIPED
    assert fs.ops == [
        ("rename", str(first.path), str(roots.universe / "workspaces" / ".quarantine" / "repo.1")),
        ("remove", str(roots.universe / "workspaces" / ".quarantine" / "repo.1")),
    ]
    # The discarded generation's lease is finished; the published one is not.
    assert lease_state(db, "lease1") == wp.STATE_AVAILABLE
    assert lease_state(db, "lease2") == wp.STATE_ACTIVE
    # The discard released no lock: the run that owns it is still running.
    assert len(lock_rows(db)) == 2


def test_a_run_with_no_lease_only_releases_its_locks(db: Path, roots: Roots) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        entry_id = wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=None)
    assert entry_id > 0
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert entry.action == wp.ACTION_RELEASE_LOCK_ONLY
    assert wp.process_entry(db, entry, fs=FakeFs()) == wp.OUTCOME_RELEASED
    assert lock_rows(db) == []


def test_a_still_authoritative_permanent_lease_is_release_lock_only(
    db: Path, roots: Roots
) -> None:
    lease = admit_universe(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.publish_generation(
            conn, universe_id="u1", repo_key="repo", generation=1, path=lease.path
        )
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert entry.action == wp.ACTION_RELEASE_LOCK_ONLY
    fs = FakeFs(present=(lease.path,))
    assert wp.process_entry(db, entry, fs=fs) == wp.OUTCOME_RELEASED
    assert fs.present == {str(lease.path)}  # the universe keeps its workspace
    assert lease_state(db, "lease1") == wp.STATE_ACTIVE
    assert lock_rows(db) == []


# --------------------------------------------------------------------------
# the admission barrier and the sweepers
# --------------------------------------------------------------------------


CRASH_TIME = 1_000_000.0
RESTART_TIME = CRASH_TIME + 60


def test_entries_from_before_process_start_block_admission_until_the_sweep(
    db: Path, roots: Roots
) -> None:
    """A crash left a wipe owed; the startup sweeper runs to completion before
    any new workspace job is admitted."""
    lease = admit_scratch(
        db, roots, lease_id_factory=_ids("lease1"), process_started_at=CRASH_TIME
    )
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(
            conn, run_id="run-1", universe_id="u1", lease=lease, now=lambda: CRASH_TIME + 1
        )
    assert wp.admission_open(db, process_started_at=RESTART_TIME) is False
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(
            db,
            roots,
            universe_id="u2",
            run_id="run-2",
            lease_id_factory=_ids("x"),
            process_started_at=RESTART_TIME,
        )
    assert exc.value.code == wp.REFUSED_POOL_BUSY
    assert "startup reconciliation pending" in exc.value.detail

    assert wp.startup_sweep(db, fs=FakeFs(present=(lease.path,)), claimant="startup") == 1
    assert wp.admission_open(db, process_started_at=RESTART_TIME) is True
    admitted = admit_scratch(
        db,
        roots,
        universe_id="u2",
        run_id="run-2",
        lease_id_factory=_ids("lease2"),
        process_started_at=RESTART_TIME,
    )
    assert admitted.state == wp.STATE_ACTIVE


def test_an_entry_created_after_process_start_never_refuses_a_new_job(
    db: Path, roots: Roots
) -> None:
    """The other side of the boundary: a run that just ended is the periodic
    processor's backlog, and must not close the pool to every universe."""
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(
            conn,
            run_id="just-finished",
            universe_id="u9",
            lease=None,
            now=lambda: RESTART_TIME + 5,
        )
    assert wp.admission_open(db, process_started_at=RESTART_TIME) is True
    lease = admit_scratch(
        db, roots, lease_id_factory=_ids("lease1"), process_started_at=RESTART_TIME
    )
    assert lease.state == wp.STATE_ACTIVE
    # ... and it is still owed: the barrier moved, the work did not.
    assert rows(db, "SELECT done_at FROM workspace_outbox") == [(None,)]


def test_the_barrier_defaults_to_this_processs_import_time(db: Path, roots: Roots) -> None:
    """No caller has to remember the parameter for the barrier to exist: the
    default is import time, so a pre-restart entry still refuses."""
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(
            conn, run_id="crashed", universe_id="u9", lease=None, now=lambda: CRASH_TIME
        )
    assert wp.admission_open(db) is False
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(db, roots, lease_id_factory=_ids("x"))
    assert exc.value.code == wp.REFUSED_POOL_BUSY


def test_the_startup_sweep_takes_over_a_dead_processors_claim(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    wp.claim_next(db, claimant="died-mid-wipe")
    assert wp.startup_sweep(db, fs=FakeFs(present=(lease.path,)), claimant="startup") == 1
    assert wp.admission_open(db) is True
    assert rows(db, "SELECT claim_token FROM workspace_outbox")[0][0].startswith("startup:")


def test_the_periodic_sweep_respects_a_live_claim_and_reclaims_an_expired_one(
    db: Path, roots: Roots
) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    clock = [1_000_000.0]
    wp.claim_next(db, claimant="live", now=lambda: clock[0])
    fs = FakeFs(present=(lease.path,))
    assert wp.periodic_sweep(db, fs=fs, claimant="sweeper", now=lambda: clock[0]) == 0
    assert fs.ops == []
    clock[0] += wp.DEFAULT_CLAIM_TTL_S + 1
    assert wp.periodic_sweep(db, fs=fs, claimant="sweeper", now=lambda: clock[0]) == 1
    assert lease_state(db, "lease1") == wp.STATE_AVAILABLE


def test_the_sweeps_are_empty_when_nothing_is_owed(db: Path) -> None:
    assert wp.startup_sweep(db, fs=FakeFs(), claimant="startup") == 0
    assert wp.periodic_sweep(db, fs=FakeFs(), claimant="sweeper") == 0
    assert wp.admission_open(db) is True


def test_claims_are_taken_oldest_first(db: Path, roots: Roots) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        first = wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
        second = wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=None)
    claimed = wp.claim_next(db, claimant="test")
    assert claimed is not None and claimed.entry_id == first
    assert second > first


# --------------------------------------------------------------------------
# the real filesystem
# --------------------------------------------------------------------------


def test_the_real_processor_wipes_a_lease_and_the_next_one_is_a_new_directory(
    db: Path, roots: Roots
) -> None:
    """scratch-storage: the next run's lease is a NEW directory and nothing of
    the old lease survives under the pool."""
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    (lease.path / "repo" / ".git").mkdir(parents=True)
    (lease.path / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert wp.process_entry(db, entry, fs=RealPoolFilesystem()) == wp.OUTCOME_WIPED
    assert not lease.path.exists()
    assert not lease.quarantine_path.exists()

    nxt = admit_scratch(db, roots, universe_id="u2", run_id="run-2", lease_id_factory=_ids("l2"))
    assert nxt.path != lease.path
    survivors = [p for p in roots.pool.rglob("*") if p.name != ".quarantine"]
    assert survivors == []


def test_a_crash_after_the_rename_is_repaired_on_the_next_claim(
    db: Path, roots: Roots
) -> None:
    """scratch-storage: source absent, deterministic quarantine present."""
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    lease.quarantine_path.mkdir(parents=True)
    (lease.quarantine_path / "leftover.pack").write_bytes(b"0123456789")
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    assert wp.startup_sweep(db, fs=RealPoolFilesystem(), claimant="startup") == 1
    assert not lease.quarantine_path.exists()
    assert lease_state(db, "lease1") == wp.STATE_AVAILABLE
    assert wp.admission_open(db) is True


def test_a_link_inside_a_lease_never_leaks_the_wipe_outside_it(
    db: Path, roots: Roots, tmp_path: Path
) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    (lease.path / "repo").mkdir(parents=True)
    (lease.path / "repo" / "file.txt").write_text("scratch", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    precious = outside / "keep.txt"
    precious.write_text("the host's data", encoding="utf-8")
    kind = make_dir_link(lease.path / "repo" / "escape", outside)
    if kind is None:
        pytest.skip("this host allows neither a symlink nor a junction")

    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None
    assert wp.process_entry(db, entry, fs=RealPoolFilesystem()) == wp.OUTCOME_WIPED

    assert not lease.path.exists()
    assert outside.is_dir()
    assert precious.read_text(encoding="utf-8") == "the host's data"


def test_the_real_filesystem_sees_a_dangling_link_as_present(tmp_path: Path) -> None:
    fs = RealPoolFilesystem()
    target = tmp_path / "gone"
    link = tmp_path / "link"
    kind = make_dir_link(link, target)
    if kind is None:
        pytest.skip("this host allows neither a symlink nor a junction")
    assert fs.exists(link) is True
    fs.remove_tree_no_follow(link)
    assert fs.exists(link) is False


def test_removing_a_path_that_is_already_gone_is_a_no_op(tmp_path: Path) -> None:
    RealPoolFilesystem().remove_tree_no_follow(tmp_path / "never-existed")


# --------------------------------------------------------------------------
# the module's own discipline
# --------------------------------------------------------------------------


def test_the_module_creates_no_directories_and_reads_no_env_vars() -> None:
    """The pool computes paths only. Directory creation belongs to the caller,
    which holds a handle resolved without following links, and configuration is
    a parameter - never a switch read out of the process's env vars."""
    source = Path(wp.__file__).read_text(encoding="utf-8")
    for forbidden in ("environ", "mkdir", "makedirs", "getenv"):
        assert forbidden not in source, forbidden
    # This used to assert `import os` was absent, which was a PROXY for the
    # rule. The module now imports os for exactly one thing - re-anchoring the
    # startup barrier in a forked child - so the assertion is the rule itself:
    # every attribute it touches on os, by name.
    assert set(re.findall(r"os\.[a-z_]+", source)) == {"os.register_at_fork"}


# --------------------------------------------------------------------------
# the permanent quota is transactional (Codex P1 #1)
# --------------------------------------------------------------------------


def test_two_permanent_admissions_cannot_cross_the_quota_before_any_bytes_move(
    db: Path, roots: Roots
) -> None:
    """The filesystem still says 0 while a generation is being fetched, so a
    quota measured only from disk would let two 6 GiB checkouts reserve 12
    against a 10 GiB quota."""
    first = admit_universe(
        db,
        roots,
        max_bytes=6 * GIB,
        universe_quota_bytes=10 * GIB,
        universe_used_bytes_fn=lambda _u: 0,
        lease_id_factory=_ids("lease1"),
    )
    assert first.reserved_bytes == 6 * GIB
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_universe(
            db,
            roots,
            max_bytes=6 * GIB,
            universe_quota_bytes=10 * GIB,
            universe_used_bytes_fn=lambda _u: 0,
            lease_id_factory=_ids("lease2"),
        )
    assert exc.value.code == wp.REFUSED_QUOTA
    assert "reserved" in exc.value.detail
    assert rows(db, "SELECT lease_id FROM workspace_leases") == [("lease1",)]


def test_a_reconciled_generation_is_not_charged_twice(db: Path, roots: Roots) -> None:
    """Once the transfer is measured the bytes ARE on disk, so the filesystem
    measurement covers them: keeping the reservation on top would halve the
    quota for as long as the generation exists."""
    lease = admit_universe(
        db,
        roots,
        max_bytes=6 * GIB,
        universe_quota_bytes=10 * GIB,
        universe_used_bytes_fn=lambda _u: 0,
        lease_id_factory=_ids("lease1"),
    )
    wp.reconcile_bytes(db, lease.lease_id, 6 * GIB)
    second = admit_universe(
        db,
        roots,
        max_bytes=4 * GIB,
        universe_quota_bytes=10 * GIB,
        universe_used_bytes_fn=lambda _u: 6 * GIB,  # now visible on disk
        lease_id_factory=_ids("lease2"),
    )
    assert second.generation == 2


def test_a_scratch_lease_is_not_counted_against_a_universe_quota(
    db: Path, roots: Roots
) -> None:
    admit_scratch(db, roots, max_bytes=4 * GIB, lease_id_factory=_ids("scratch1"))
    permanent = admit_universe(
        db,
        roots,
        max_bytes=9 * GIB,
        universe_quota_bytes=10 * GIB,
        universe_used_bytes_fn=lambda _u: 0,
        lease_id_factory=_ids("lease1"),
    )
    assert permanent.reserved_bytes == 9 * GIB


# --------------------------------------------------------------------------
# the bounded wait on the job lock (Codex P1 #2)
# --------------------------------------------------------------------------


def test_admission_observation_is_a_snapshot_not_lease_state(db: Path, roots: Roots):
    observation = wp.AdmissionObservation()
    lease = admit_scratch(db, roots, observation=observation)
    snapshot = observation.snapshot()
    assert snapshot == {"attempts": 1, "lock_conflicts": 0, "retry_sleep_seconds": 0.0}
    observation.attempts += 1
    assert snapshot["attempts"] == 1
    assert wp.get_lease(db, lease.lease_id) == lease
    assert not hasattr(lease, "workspace_admission")


def test_validation_before_a_transaction_has_no_observation(db: Path, roots: Roots):
    observation = wp.AdmissionObservation()
    with pytest.raises(ValueError, match="run_id"):
        admit_scratch(db, roots, run_id="", observation=observation)
    assert observation.attempts == 0


@pytest.mark.parametrize("release", [False, True])
def test_observation_preserves_the_uninstrumented_wait_policy(db: Path, roots: Roots, release):
    outcomes = []
    for observed in (False, True):
        case_db = db.with_name(f"observed-{observed}.db")
        clock = [1_000_000.0]
        lease = admit_scratch(case_db, roots, lease_id_factory=_ids("lease1"))
        with terminal_txn(case_db) as conn:
            wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
        entry = wp.claim_next(case_db, claimant="test")
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
            if release and len(sleeps) == 2:
                wp.process_entry(case_db, entry, fs=FakeFs(present=(lease.path,)))

        observation = wp.AdmissionObservation() if observed else None
        try:
            admit_scratch(
                case_db, roots, run_id="run-2", lease_id_factory=_ids("lease2"),
                wait_s=1.2, now=lambda: clock[0], monotonic=lambda: clock[0],
                sleep=sleep, observation=observation, process_started_at=clock[0] - 1,
            )
            outcome = "admitted"
        except wp.WorkspacePoolRefused as exc:
            outcome = exc.code
        outcomes.append((outcome, sleeps))
        if observation is not None:
            assert observation.attempts == (3 if release else 4)
            assert observation.lock_conflicts == (2 if release else 4)
            assert observation.retry_sleep_seconds == pytest.approx(1.0 if release else 1.2)
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == ("admitted" if release else wp.REFUSED_BUSY)


def test_without_a_wait_a_held_lock_refuses_at_once(db: Path, roots: Roots) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    slept: list[float] = []
    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(
            db,
            roots,
            run_id="run-2",
            lease_id_factory=_ids("lease2"),
            sleep=slept.append,
        )
    assert exc.value.code == wp.REFUSED_BUSY
    assert slept == []


def test_a_bounded_wait_retries_until_the_lock_is_released(
    db: Path, roots: Roots
) -> None:
    lease = admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    with terminal_txn(db) as conn:
        wp.enqueue_terminal(conn, run_id="run-1", universe_id="u1", lease=lease)
    entry = wp.claim_next(db, claimant="test")
    assert entry is not None

    clock = [1_000_000.0]
    slept: list[float] = []

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds
        if len(slept) == 2:
            # The run that held the lock finishes while we are waiting.
            wp.process_entry(db, entry, fs=FakeFs(present=(lease.path,)))

    admitted = admit_scratch(
        db,
        roots,
        universe_id="u2",
        run_id="run-2",
        lease_id_factory=_ids("lease2"),
        wait_s=10.0,
        now=lambda: clock[0],
        sleep=_sleep,
        process_started_at=clock[0] - 1,
    )
    assert admitted.state == wp.STATE_ACTIVE
    assert slept == [wp.LOCK_POLL_S, wp.LOCK_POLL_S]


def test_a_bounded_wait_gives_up_at_its_deadline(db: Path, roots: Roots) -> None:
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    clock = [1_000_000.0]
    slept: list[float] = []

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    with pytest.raises(wp.WorkspacePoolRefused) as exc:
        admit_scratch(
            db,
            roots,
            universe_id="u2",
            run_id="run-2",
            lease_id_factory=_ids("lease2"),
            wait_s=1.2,
            now=lambda: clock[0],
            sleep=_sleep,
        )
    assert exc.value.code == wp.REFUSED_BUSY
    # 0.5 + 0.5 + the 0.2 that is left, then the deadline is past.
    assert slept == [0.5, 0.5, pytest.approx(0.2)]


# --------------------------------------------------------------------------
# operation-scoped reservations (Codex P1 #2)
# --------------------------------------------------------------------------


def test_an_operation_reserves_its_maximum_before_the_wire(db: Path, roots: Roots) -> None:
    """A push and a provisioning hold no lease, so nothing was charging the
    hourly ledger for the bytes they moved."""
    reserved = wp.reserve_operation_bytes(
        db,
        universe_id="u1",
        run_id="run-1",
        operation_id="push-1",
        max_bytes=2 * GIB,
    )
    assert reserved == 2 * GIB
    usage = wp.ledger_usage(db, "u1")
    assert usage.bytes == 2 * GIB
    assert usage.jobs == 1


def test_an_operation_is_charged_once_however_often_it_is_retried(
    db: Path, roots: Roots
) -> None:
    for _ in range(3):
        assert (
            wp.reserve_operation_bytes(
                db,
                universe_id="u1",
                run_id="run-1",
                operation_id="push-1",
                max_bytes=2 * GIB,
            )
            == 2 * GIB
        )
    usage = wp.ledger_usage(db, "u1")
    assert usage.bytes == 2 * GIB
    assert usage.jobs == 1


def test_an_operation_reconciles_downward_only(db: Path, roots: Roots) -> None:
    wp.reserve_operation_bytes(
        db,
        universe_id="u1",
        run_id="run-1",
        operation_id="push-1",
        max_bytes=2 * GIB,
    )
    assert wp.reconcile_operation_bytes(db, "push-1", 3 * GIB) == 2 * GIB
    assert wp.reconcile_operation_bytes(db, "push-1", GIB) == GIB
    assert wp.ledger_usage(db, "u1").bytes == GIB
    assert wp.reconcile_operation_bytes(db, "push-1", 2 * GIB) == GIB


def test_an_interrupted_operation_keeps_its_maximum_charged(db: Path, roots: Roots) -> None:
    wp.reserve_operation_bytes(
        db,
        universe_id="u1",
        run_id="run-1",
        operation_id="push-1",
        max_bytes=2 * GIB,
    )
    # The push dies; reconcile_operation_bytes is never called.
    assert wp.ledger_usage(db, "u1").bytes == 2 * GIB
    assert rows(
        db, "SELECT reserved FROM workspace_ledger WHERE kind = 'bytes'"
    ) == [(1,)]


def test_a_checkout_and_a_push_share_one_hourly_ledger(db: Path, roots: Roots) -> None:
    admit_scratch(db, roots, max_bytes=2 * GIB, lease_id_factory=_ids("lease1"))
    wp.reserve_operation_bytes(
        db,
        universe_id="u1",
        run_id="run-1",
        operation_id="push-1",
        max_bytes=GIB,
    )
    usage = wp.ledger_usage(db, "u1")
    assert usage.bytes == 3 * GIB
    assert usage.jobs == 2


def test_reconciling_an_unknown_operation_is_a_loud_error(db: Path) -> None:
    with pytest.raises(ValueError, match="no bytes reservation"):
        wp.reconcile_operation_bytes(db, "nope", 1)


# --------------------------------------------------------------------------
# indexes (Codex P1 #3)
# --------------------------------------------------------------------------


def test_the_hot_lookups_are_indexed(db: Path, roots: Roots) -> None:
    """Each of these is a scan of a table that only grows, on a path every run
    takes."""
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    names = {
        row[0]
        for row in rows(db, "SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {
        "workspace_leases_state_run",
        "workspace_leases_universe_class_state",
        "workspace_outbox_run",
        "workspace_outbox_age",
        "workspace_ledger_operation",
    } <= names


# --------------------------------------------------------------------------
# the startup barrier survives a fork (Codex P1 #4)
# --------------------------------------------------------------------------


def test_mark_process_started_re_anchors_the_barrier() -> None:
    original = wp.PROCESS_STARTED_AT
    try:
        assert wp.mark_process_started(1_234.5) == 1_234.5
        assert wp.PROCESS_STARTED_AT == 1_234.5
        moved = wp.mark_process_started()
        assert moved > 1_234.5
    finally:
        wp.mark_process_started(original)


def _report_process_started_at(queue) -> None:
    """Runs in the forked child."""
    from tinyassets import workspace_pool as child_wp

    queue.put(child_wp.PROCESS_STARTED_AT)


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="fork is POSIX only; there is no inherited-import problem elsewhere",
)
def test_a_forked_child_re_anchors_the_startup_barrier() -> None:
    """Import time is process start only for the process that imported. A worker
    forked from a parent that imported hours ago would otherwise treat the
    parent's own outbox entries as pre-restart and refuse every admission."""
    original = wp.PROCESS_STARTED_AT
    try:
        wp.mark_process_started(1_000_000.0)  # the parent's stale instant
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        child = ctx.Process(target=_report_process_started_at, args=(queue,))
        forked_at = time.time()
        child.start()
        child.join(timeout=60)
        assert child.exitcode == 0
        child_started_at = queue.get(timeout=10)
        assert child_started_at > 1_000_000.0
        assert child_started_at >= forked_at
    finally:
        wp.mark_process_started(original)


# --------------------------------------------------------------------------
# the pool bound holds across PROCESSES, not just threads (Codex P2)
# --------------------------------------------------------------------------


def _admit_in_child(
    db_path: str,
    pool_root: str,
    universe_root: str,
    name: str,
    barrier,
    queue,
) -> None:
    """Runs in a separate PROCESS: its own sqlite connection, its own GIL."""
    from pathlib import Path as _Path

    from tinyassets import workspace_pool as child_wp

    try:
        barrier.wait(timeout=60)
        lease = child_wp.admit(
            _Path(db_path),
            universe_id=f"u-{name}",
            connection_id="c1",
            repo_key="repo",
            storage_class=child_wp.STORAGE_SCRATCH,
            run_id=f"run-{name}",
            max_bytes=4 * child_wp.GIB,
            pool_root=_Path(pool_root),
            universe_root=_Path(universe_root),
            pool_bytes_cap=5 * child_wp.GIB,
            lease_bytes_cap=4 * child_wp.GIB,
            lease_id_factory=lambda: f"lease{name}",
        )
        queue.put(("admitted", lease.lease_id))
    except child_wp.WorkspacePoolRefused as refusal:
        queue.put(("refused", refusal.code))
    except BaseException as exc:  # pragma: no cover - a child that broke
        queue.put(("error", repr(exc)))


def _mp_context():
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


def test_two_processes_cannot_oversubscribe_the_pool(db: Path, roots: Roots) -> None:
    """Threads share a GIL and a process; the daemon does not. The bound is
    sqlite's BEGIN IMMEDIATE, and only two real processes prove that is what is
    holding it rather than the interpreter."""
    # Create the schema first so neither child pays for it in the race.
    admit_scratch(db, roots, lease_id_factory=_ids("seed"), max_bytes=0)
    with terminal_txn(db) as conn:
        conn.execute("DELETE FROM workspace_leases")
        conn.execute("DELETE FROM workspace_locks")
        conn.execute("DELETE FROM workspace_ledger")

    ctx = _mp_context()
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    children = [
        ctx.Process(
            target=_admit_in_child,
            args=(str(db), str(roots.pool), str(roots.universe), name, barrier, queue),
        )
        for name in ("a", "b")
    ]
    for child in children:
        child.start()
    try:
        results = [queue.get(timeout=120) for _ in range(2)]
    finally:
        for child in children:
            child.join(timeout=60)
            if child.is_alive():  # pragma: no cover - a hung child
                child.terminate()

    kinds = sorted(kind for kind, _ in results)
    assert kinds == ["admitted", "refused"], results
    refusal = next(detail for kind, detail in results if kind == "refused")
    assert refusal == wp.REFUSED_POOL_BUSY, results
    assert wp.pool_usage(db).reserved_bytes == 4 * GIB
    assert len(rows(db, "SELECT lease_id FROM workspace_leases")) == 1


# --------------------------------------------------------------------------
# cancelling a bounded wait
#
# The wait parks on ANOTHER run's lock. Whatever else waiting grows into, a
# run its owner has stopped must not stay parked there and must not come out
# of the park holding a lease: cancellation is the caller's, and the pool's
# job is only to consult it and leave nothing behind.
# --------------------------------------------------------------------------


def _cancelled_after(n: int) -> tuple[Callable[[], bool], list[int]]:
    """A predicate that reports "cancelled" from its ``n``-th consultation on,
    and the log of how many times it was asked."""
    asked: list[int] = []

    def should_cancel() -> bool:
        asked.append(len(asked) + 1)
        return len(asked) > n

    return should_cancel, asked


def test_a_cancel_during_the_wait_stops_it_and_admits_nothing(
    db: Path, roots: Roots
) -> None:
    """The contended case. A run cancelled while parked on another run's lock
    wakes, sees the cancel, and unwinds as a CANCELLATION -- not a refusal, and
    not a lease. Nothing it touched is left behind and the holder is untouched.
    """
    from tinyassets.runs import RunCancelledError

    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))
    before_leases = rows(db, "SELECT lease_id FROM workspace_leases")
    before_ledger = rows(db, "SELECT universe_id, kind, amount FROM workspace_ledger")

    clock = [1000.0]
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    # Not cancelled at the first check (so the wait really starts), cancelled
    # by the time the first sleep returns.
    should_cancel, asked = _cancelled_after(1)

    with pytest.raises(RunCancelledError):
        admit_scratch(
            db,
            roots,
            run_id="run-2",
            lease_id_factory=_ids("lease2"),
            wait_s=60.0,
            now=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=fake_sleep,
            should_cancel=should_cancel,
        )

    # It stopped at the first wake, not at the 60-second deadline.
    assert slept == [wp.LOCK_POLL_S], slept
    assert len(asked) == 2, asked
    # No lease, no reservation, no generation, no lock of its own.
    assert rows(db, "SELECT lease_id FROM workspace_leases") == before_leases
    assert rows(db, "SELECT universe_id, kind, amount FROM workspace_ledger") == before_ledger
    assert rows(db, "SELECT generation FROM workspace_generations") == []
    # The legitimate holder still holds exactly what it held.
    assert lock_rows(db) == [("host", "slot-0", "run-1"), ("universe", "u1", "run-1")]


def test_an_already_cancelled_admission_takes_nothing_even_with_a_free_lock(
    db: Path, roots: Roots
) -> None:
    """Nothing is contended here: the pool is empty and the admission would
    succeed. It still must not, because a lease a cancelled run will never
    populate or release is a leak only the outbox can reclaim."""
    from tinyassets.runs import RunCancelledError

    slept: list[float] = []

    with pytest.raises(RunCancelledError):
        admit_scratch(
            db,
            roots,
            lease_id_factory=_ids("lease1"),
            sleep=lambda s: slept.append(s),
            should_cancel=lambda: True,
        )

    assert slept == []
    # It stopped before the pool database was even opened: not one workspace
    # table exists, so there is no lease, no lock, no reservation to find.
    assert not db.exists() or rows(
        db,
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'workspace_%'",
    ) == []
    assert not (roots.pool / wp.WORKSPACES_DIR).exists()


def test_a_run_that_is_not_cancelled_still_admits_when_the_holder_releases(
    db: Path, roots: Roots
) -> None:
    """The predicate must not become a second refusal. A waiter that is NOT
    cancelled still gets the lock the moment the holder gives it up -- exactly
    once, and with the contention it actually saw recorded."""
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))

    clock = [1000.0]
    observation = wp.AdmissionObservation()
    asked: list[bool] = []

    def release_on_first_sleep(seconds: float) -> None:
        clock[0] += seconds
        conn = sqlite3.connect(str(db), timeout=30)
        try:
            conn.execute("DELETE FROM workspace_locks")
            conn.commit()
        finally:
            conn.close()

    def should_cancel() -> bool:
        asked.append(False)
        return False

    lease = admit_scratch(
        db,
        roots,
        run_id="run-2",
        lease_id_factory=_ids("lease2"),
        wait_s=60.0,
        observation=observation,
        now=lambda: clock[0],
        monotonic=lambda: clock[0],
        sleep=release_on_first_sleep,
        should_cancel=should_cancel,
    )

    assert lease.lease_id == "lease2"
    assert asked, "the predicate was never consulted"
    assert observation.attempts == 2 and observation.lock_conflicts == 1
    assert lock_rows(db) == [("host", "slot-0", "run-2"), ("universe", "u1", "run-2")]
    assert rows(db, "SELECT lease_id FROM workspace_leases ORDER BY lease_id") == [
        ("lease1",),
        ("lease2",),
    ]


# --------------------------------------------------------------------------
# characterization: what the bounded wait still does NOT do
#
# Bounded in-process waiting does not persist a resumable waiting intent.
# Cancellation support must not be mistaken for durable restart support.
# --------------------------------------------------------------------------


def test_today_a_busy_admission_persists_no_waiting_intent(db: Path, roots: Roots) -> None:
    """The bounded in-node wait is counted in memory and forgotten. Nothing in
    the pool DB records that THIS wait wanted the lock, so a park dies with the
    process. Durable waiting is a separate, earlier step: a root run takes a
    ``workspace_waiters`` ticket at admission, before it starts
    (change durable-workspace-wait), never from inside a node's wait.
    """
    admit_scratch(db, roots, lease_id_factory=_ids("lease1"))

    clock = [1000.0]
    observation = wp.AdmissionObservation()
    with pytest.raises(wp.WorkspacePoolRefused):
        admit_scratch(
            db,
            roots,
            run_id="run-2",
            lease_id_factory=_ids("lease2"),
            wait_s=1.0,
            observation=observation,
            now=lambda: clock[0],
            monotonic=lambda: clock[0],
            sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        )

    # The waiting really happened...
    assert observation.lock_conflicts >= 2
    assert observation.retry_sleep_seconds >= 0.0
    # ...and left no durable trace: the lock names only its HOLDER, and no table
    # in this database mentions run-2.
    assert lock_rows(db) == [("host", "slot-0", "run-1"), ("universe", "u1", "run-1")]
    tables = [
        name
        for (name,) in rows(db, "SELECT name FROM sqlite_master WHERE type = 'table'")
        if name.startswith("workspace_")
    ]
    for table in tables:
        columns = [c[1] for c in rows(db, f"PRAGMA table_info({table})")]
        if "run_id" not in columns:
            continue
        mentions = rows(db, f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", ("run-2",))
        assert mentions == [(0,)], (table, mentions)
