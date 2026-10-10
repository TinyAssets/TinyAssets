"""The run lifecycle owes the workspace outbox its work (workspace-node D0/D6).

A run that held a scratch lease releases it THROUGH an outbox entry.  A local
lease is atomic with terminal status; a universe-owned lease is enqueued just
after the root commit and repaired by its sweep across the two-WAL crash gap.
Every terminal recovery path follows that protocol; a once-per-process
reconciler finishes what an earlier process left; and every workspace refusal
is a first-class failure class with an action.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import runs, workspace_pool

GIB = 1024 ** 3


class _NoopFs:
    """A filesystem that has nothing to delete: every entry completes."""

    def exists(self, path: Path) -> bool:
        return False

    def rename(self, src: Path, dst: Path) -> None:
        raise AssertionError("nothing exists, nothing to rename")

    def owner_scoped(self, path: Path) -> bool:
        return False

    def remove_tree_no_follow(self, path: Path) -> None:
        raise AssertionError("nothing exists, nothing to remove")


def _seed_run(
    base: Path,
    run_id: str,
    status: str = "running",
    *,
    queue_universe_id: str = "",
    actor: str = "universe:u-test",
) -> None:
    runs.initialize_runs_db(base)
    with runs._connect(base) as conn:
        conn.execute(
            "INSERT INTO runs "
            "(run_id, branch_def_id, thread_id, status, started_at, actor, "
            "queue_universe_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, "b1", f"t-{run_id}", status, time.time() - 7200,
                # Every run records who asked for it; the column is NOT NULL.
                actor, queue_universe_id or None,
            ),
        )


def _admit(base: Path, tmp: Path, run_id: str, universe: str = "u-1") -> workspace_pool.Lease:
    db = runs.runs_db_path(base)
    with runs._connect(base) as conn:
        workspace_pool.ensure_schema(conn)
    return workspace_pool.admit(
        db,
        universe_id=universe,
        connection_id="c1",
        repo_key="github.com--o--r",
        storage_class="scratch",
        run_id=run_id,
        max_bytes=GIB,
        pool_root=tmp / "scratch",
        universe_root=tmp / "universes" / universe,
    )


def _pending(base: Path, run_id: str) -> list[tuple]:
    conn = sqlite3.connect(runs.runs_db_path(base))
    try:
        return conn.execute(
            "SELECT action, lease_id FROM workspace_outbox WHERE run_id = ? AND done_at IS NULL",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()


def test_a_terminal_status_enqueues_the_lease_in_the_same_write(tmp_path, monkeypatch):
    base = tmp_path / "data"
    _seed_run(base, "r1")
    lease = _admit(base, tmp_path, "r1")
    kicks: list[Path] = []
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: kicks.append(Path(p)))
    assert _pending(base, "r1") == []
    runs.update_run_status(base, "r1", status="running", last_node_id="n1")
    assert _pending(base, "r1") == [], "a non-terminal status owes nothing"
    assert kicks == [], "nothing owed, nothing kicked"
    runs.update_run_status(base, "r1", status="completed", finished_at=time.time())
    assert _pending(base, "r1") == [("wipe_scratch", lease.lease_id)]
    assert kicks == [base], "a finished run's release is kicked, not left to the tick"


@pytest.mark.parametrize("identity_source", ["queue", "legacy_actor"])
def test_a_root_terminal_enqueues_the_universe_that_owns_the_workspace(
    tmp_path, monkeypatch, identity_source,
):
    """Production topology: run at root, workspace rows in the universe.

    This is stronger than the periodic repair regression: the outbox row must
    exist on the normal terminal path, before any sweep gets a chance.
    """
    root = tmp_path / "data"
    universe_id = "u-00000000000000000000000000"
    universe = root / universe_id
    universe.mkdir(parents=True)
    _seed_run(
        root,
        "root-run",
        queue_universe_id=universe_id if identity_source == "queue" else "",
        actor=(
            "universe:u-test"
            if identity_source == "queue"
            else f"universe:{universe_id}"
        ),
    )
    lease = _admit(universe, tmp_path, "root-run", universe=universe_id)
    kicks: list[Path] = []
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: kicks.append(Path(p)))

    runs.update_run_status(root, "root-run", status="completed", finished_at=time.time())

    assert _pending(root, "root-run") == []
    assert _pending(universe, "root-run") == [("wipe_scratch", lease.lease_id)]
    assert kicks == [universe]
    with sqlite3.connect(runs.runs_db_path(root)) as conn:
        assert conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", ("root-run",)
        ).fetchone()[0] == "completed"


def test_a_universe_enqueue_failure_preserves_terminal_status_and_kicks_repair(
    tmp_path, monkeypatch,
):
    """A second-WAL failure cannot roll back or reclassify the root commit."""
    root = tmp_path / "data"
    universe_id = "u-00000000000000000000000001"
    universe = root / universe_id
    universe.mkdir(parents=True)
    _seed_run(root, "split-run", queue_universe_id=universe_id)
    runs.initialize_runs_db(universe)
    lease = _admit(universe, tmp_path, "split-run", universe=universe_id)
    real_enqueue = runs._enqueue_workspace_terminal
    kicks: list[Path] = []

    def _fail_universe(conn, base_path, run_id):
        if Path(base_path).resolve() == universe.resolve():
            raise sqlite3.OperationalError("universe WAL unavailable")
        return real_enqueue(conn, base_path, run_id)

    monkeypatch.setattr(runs, "_enqueue_workspace_terminal", _fail_universe)
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: kicks.append(Path(p)))

    runs.update_run_status(root, "split-run", status="completed")

    with sqlite3.connect(runs.runs_db_path(root)) as conn:
        assert conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", ("split-run",)
        ).fetchone()[0] == "completed"
    assert _pending(universe, "split-run") == []
    assert kicks == [universe]

    # The kicked pass is the durable recovery half of the two-WAL protocol.
    monkeypatch.setattr(runs, "_enqueue_workspace_terminal", real_enqueue)
    monkeypatch.setattr("tinyassets.workspace_fs.RealPoolFilesystem", _NoopFs)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    runs._workspace_sweep_once(universe, claimant="test")
    after = workspace_pool.get_lease(runs.runs_db_path(universe), lease.lease_id)
    assert after is not None and after.state == "AVAILABLE"


def test_a_noncanonical_queue_universe_never_becomes_a_terminal_path(
    tmp_path, monkeypatch,
):
    root = tmp_path / "data"
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    _seed_run(root, "hostile-run", queue_universe_id="../escaped")
    kicks: list[Path] = []
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: kicks.append(Path(p)))

    runs.update_run_status(root, "hostile-run", status="completed")

    assert kicks == []
    assert not runs.runs_db_path(escaped).exists()


def test_an_enqueue_failure_rolls_the_terminal_status_back(tmp_path, monkeypatch):
    """One transaction means one: if the outbox cannot take the entry, the
    status write must not land either (Codex, code round 1)."""
    base = tmp_path / "data"
    _seed_run(base, "r1c")
    _admit(base, tmp_path, "r1c")
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: None)

    def _boom(conn, base_path, run_id):
        raise sqlite3.OperationalError("outbox unavailable")

    monkeypatch.setattr(runs, "_enqueue_workspace_terminal", _boom)
    with pytest.raises(sqlite3.OperationalError):
        runs.update_run_status(base, "r1c", status="completed", finished_at=time.time())
    conn = sqlite3.connect(runs.runs_db_path(base))
    try:
        status = conn.execute("SELECT status FROM runs WHERE run_id = ?", ("r1c",)).fetchone()[0]
    finally:
        conn.close()
    assert status == "running", "the terminal status rolled back with the failed enqueue"
    assert _pending(base, "r1c") == []


def test_the_kick_releases_the_lock_within_the_same_second(tmp_path, monkeypatch):
    base = tmp_path / "data"
    _seed_run(base, "r1b")
    lease = _admit(base, tmp_path, "r1b")
    monkeypatch.setattr("tinyassets.workspace_fs.RealPoolFilesystem", _NoopFs)
    threads: list = []
    real_kick = runs._kick_workspace_sweep
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: threads.append(real_kick(p)))
    runs.update_run_status(base, "r1b", status="completed", finished_at=time.time())
    for t in threads:
        t.join(timeout=10)
    assert _pending(base, "r1b") == []
    after = workspace_pool.get_lease(runs.runs_db_path(base), lease.lease_id)
    assert after is not None and after.state == "AVAILABLE"
    conn = sqlite3.connect(runs.runs_db_path(base))
    try:
        held = conn.execute(
            "SELECT COUNT(*) FROM workspace_locks WHERE run_id = ?", ("r1b",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert held == 0, "both locks are released by the kicked sweep"


def test_a_run_holding_only_the_lock_releases_it(tmp_path, monkeypatch):
    base = tmp_path / "data"
    _seed_run(base, "r2")
    lease = _admit(base, tmp_path, "r2")
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: None)
    # Simulate a lease that already left ACTIVE (a discard) while the run's
    # universe lock is still held: the terminal write owes a lock release.
    conn = sqlite3.connect(runs.runs_db_path(base))
    conn.execute(
        "UPDATE workspace_leases SET state = 'AVAILABLE' WHERE lease_id = ?",
        (lease.lease_id,),
    )
    conn.commit()
    conn.close()
    runs.update_run_status(base, "r2", status="failed", error="boom")
    assert _pending(base, "r2") == [("release_lock_only", None)]


@pytest.mark.parametrize("entry_point", ["startup"])
def test_every_orphan_recovery_path_enqueues_the_owning_universe(
    tmp_path, monkeypatch, entry_point,
):
    root = tmp_path / "data"
    universe_id = "u-00000000000000000000000002"
    universe = root / universe_id
    universe.mkdir(parents=True)
    run_id = f"orphan-{entry_point}"
    _seed_run(root, run_id, status="running", queue_universe_id=universe_id)
    lease = _admit(universe, tmp_path, run_id, universe=universe_id)
    kicks: list[Path] = []
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: kicks.append(Path(p)))

    assert runs.recover_in_flight_runs(root) == 1

    assert _pending(root, run_id) == []
    assert _pending(universe, run_id) == [("wipe_scratch", lease.lease_id)]
    assert kicks == [universe]


def test_startup_recovery_enqueues_every_in_flight_run(tmp_path, monkeypatch):
    # The kick is a background sweep that consumes the outbox row this test
    # reads; left live, the assertion races it.
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda _p: None)
    base = tmp_path / "data"
    _seed_run(base, "r4", status="queued")
    _seed_run(base, "r5", status="completed")
    lease4 = _admit(base, tmp_path, "r4")
    assert runs.recover_in_flight_runs(base) == 1
    assert _pending(base, "r4") == [("wipe_scratch", lease4.lease_id)]
    assert _pending(base, "r5") == []


def test_the_reconciler_runs_once_and_finishes_old_entries(tmp_path, monkeypatch):
    base = tmp_path / "data"
    _seed_run(base, "r6")
    lease = _admit(base, tmp_path, "r6")
    monkeypatch.setattr(runs, "_kick_workspace_sweep", lambda p: None)
    runs.update_run_status(base, "r6", status="completed")
    assert _pending(base, "r6")
    monkeypatch.setattr("tinyassets.workspace_fs.RealPoolFilesystem", _NoopFs)
    import os

    runs._WORKSPACE_RECONCILED.discard((os.getpid(), str(base.resolve())))
    assert runs.ensure_workspace_reconciled(base, start_sweeper=False) is True
    assert runs.ensure_workspace_reconciled(base, start_sweeper=False) is False
    assert _pending(base, "r6") == []
    after = workspace_pool.get_lease(runs.runs_db_path(base), lease.lease_id)
    assert after is not None and after.state == "AVAILABLE"


def test_the_periodic_pass_leaves_a_permanent_generation_alone(tmp_path, monkeypatch):
    """An authoritative permanent generation stays ACTIVE after its run by
    design; the sweep must not re-enqueue it every tick."""
    base = tmp_path / "data"
    _seed_run(base, "r7b", status="completed")
    db = runs.runs_db_path(base)
    with runs._connect(base) as conn:
        workspace_pool.ensure_schema(conn)
    lease = workspace_pool.admit(
        db, universe_id="u-1", connection_id="c1", repo_key="github.com--o--r",
        storage_class="universe", run_id="r7b", max_bytes=GIB,
        pool_root=tmp_path / "scratch", universe_root=tmp_path / "universes" / "u-1",
        universe_quota_bytes=10 * GIB, universe_used_bytes_fn=lambda _u: 0,
    )
    monkeypatch.setattr("tinyassets.workspace_fs.RealPoolFilesystem", _NoopFs)
    runs._workspace_sweep_once(base, claimant="test")
    after = workspace_pool.get_lease(db, lease.lease_id)
    assert after is not None and after.state == "ACTIVE", "the generation is kept"
    conn = sqlite3.connect(db)
    try:
        held = conn.execute(
            "SELECT COUNT(*) FROM workspace_locks WHERE run_id = ?", ("r7b",)
        ).fetchone()[0]
        entries = conn.execute(
            "SELECT action FROM workspace_outbox WHERE run_id = ?", ("r7b",)
        ).fetchall()
    finally:
        conn.close()
    assert held == 0, "its locks were released through a lock-only entry"
    assert entries == [("release_lock_only",)]
    runs._workspace_sweep_once(base, claimant="test")
    conn = sqlite3.connect(db)
    try:
        again = conn.execute(
            "SELECT COUNT(*) FROM workspace_outbox WHERE run_id = ?", ("r7b",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert again == 1, "a second tick enqueues nothing more"


def test_the_reconciler_retries_after_a_failed_attempt(tmp_path, monkeypatch):
    base = tmp_path / "data"
    runs.initialize_runs_db(base)
    calls = {"n": 0}

    def _fail_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("sweep exploded")
        return 0

    monkeypatch.setattr(workspace_pool, "startup_sweep", _fail_once)
    with pytest.raises(RuntimeError):
        runs.ensure_workspace_reconciled(base, start_sweeper=False)
    assert runs.ensure_workspace_reconciled(base, start_sweeper=False) is True
    assert runs.ensure_workspace_reconciled(base, start_sweeper=False) is False
    assert calls["n"] == 2


def test_a_periodic_sweeper_owns_its_dependency_and_stops_promptly(
    tmp_path, monkeypatch
):
    """An old worker cannot call a later test's process-global monkeypatch."""
    base = tmp_path / "data"
    owned_calls: list[str] = []
    foreign_calls: list[str] = []
    entered_sweep = threading.Event()
    allow_sweep = threading.Event()
    swept = threading.Event()

    def owned_sweep(_base, *, claimant):
        entered_sweep.set()
        assert allow_sweep.wait(2)
        owned_calls.append(claimant)
        swept.set()
        return 0

    monkeypatch.setattr(workspace_pool, "startup_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(runs, "_reconcile_push_intents", lambda *a, **k: 0)
    assert runs.ensure_workspace_reconciled(
        base, interval_s=0.01, sweep_once=owned_sweep,
    ) is True
    handle = runs._WORKSPACE_SWEEPERS[(os.getpid(), str(base.resolve()))]
    assert handle.thread.ident is not None, "an unstarted worker was published"
    assert entered_sweep.wait(2), "the worker did not reach its owned callable"
    monkeypatch.setattr(
        runs,
        "_workspace_sweep_once",
        lambda _base, *, claimant: foreign_calls.append(claimant) or 0,
    )
    allow_sweep.set()
    assert swept.wait(2), "the periodic worker did not run"
    assert handle.thread.is_alive()
    assert foreign_calls == [], "the old worker followed a later global monkeypatch"

    assert runs._stop_workspace_sweeper(base, timeout_s=2) is True
    assert not handle.thread.is_alive()
    assert owned_calls, "the injected sweep dependency was not called"


def test_stopping_a_sweeper_requires_the_startup_barrier_again(
    tmp_path, monkeypatch
):
    base = tmp_path / "data"
    monkeypatch.setattr(workspace_pool, "startup_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(runs, "_reconcile_push_intents", lambda *a, **k: 0)

    assert runs.ensure_workspace_reconciled(base, interval_s=60) is True
    first = runs._WORKSPACE_SWEEPERS[(os.getpid(), str(base.resolve()))]
    assert runs._stop_workspace_sweeper(base, timeout_s=2) is True
    assert not first.thread.is_alive()

    assert runs.ensure_workspace_reconciled(base, start_sweeper=False) is True


def test_stop_during_reconciliation_cannot_publish_a_late_worker(
    tmp_path, monkeypatch
):
    base = tmp_path / "data"
    startup_entered = threading.Event()
    allow_startup = threading.Event()
    results: list[bool] = []

    def blocked_startup(*_args, **_kwargs):
        startup_entered.set()
        assert allow_startup.wait(2)
        return 0

    monkeypatch.setattr(workspace_pool, "startup_sweep", blocked_startup)
    monkeypatch.setattr(runs, "_reconcile_push_intents", lambda *a, **k: 0)
    reconciler = threading.Thread(
        target=lambda: results.append(
            runs.ensure_workspace_reconciled(base, interval_s=60)
        )
    )
    reconciler.start()
    assert startup_entered.wait(2)
    assert runs._stop_workspace_sweeper(base, timeout_s=2) is True
    allow_startup.set()
    reconciler.join(2)

    key = (os.getpid(), str(base.resolve()))
    assert not reconciler.is_alive()
    assert results == [True]
    assert key not in runs._WORKSPACE_SWEEPERS
    assert key not in runs._WORKSPACE_RECONCILED
    assert key not in runs._WORKSPACE_STOP_REQUESTED


def test_stop_all_during_reconciliation_cannot_publish_a_late_worker(
    tmp_path, monkeypatch
):
    base = tmp_path / "data"
    startup_entered = threading.Event()
    allow_startup = threading.Event()
    results: list[bool] = []

    def blocked_startup(*_args, **_kwargs):
        startup_entered.set()
        assert allow_startup.wait(2)
        return 0

    monkeypatch.setattr(workspace_pool, "startup_sweep", blocked_startup)
    monkeypatch.setattr(runs, "_reconcile_push_intents", lambda *a, **k: 0)
    reconciler = threading.Thread(
        target=lambda: results.append(
            runs.ensure_workspace_reconciled(base, interval_s=60)
        )
    )
    reconciler.start()
    assert startup_entered.wait(2)
    assert runs._stop_all_workspace_sweepers(timeout_s=2) is True
    key = (os.getpid(), str(base.resolve()))
    assert key in runs._WORKSPACE_STOP_REQUESTED
    allow_startup.set()
    reconciler.join(2)

    assert not reconciler.is_alive()
    assert results == [True]
    assert key not in runs._WORKSPACE_SWEEPERS
    assert key not in runs._WORKSPACE_RECONCILED
    assert key not in runs._WORKSPACE_STOP_REQUESTED


def test_a_sweeper_that_stops_itself_retires_its_handle(
    tmp_path, monkeypatch
):
    base = tmp_path / "data"
    allow_sweep = threading.Event()
    stop_called = threading.Event()
    stop_results: list[bool] = []

    def self_stopping_sweep(_base, *, claimant):
        assert claimant.startswith("sweeper:")
        assert allow_sweep.wait(2)
        stop_results.append(runs._stop_workspace_sweeper(base, timeout_s=2))
        stop_called.set()
        return 0

    monkeypatch.setattr(workspace_pool, "startup_sweep", lambda *a, **k: 0)
    monkeypatch.setattr(runs, "_reconcile_push_intents", lambda *a, **k: 0)
    assert runs.ensure_workspace_reconciled(
        base, interval_s=0.01, sweep_once=self_stopping_sweep,
    ) is True
    key = (os.getpid(), str(base.resolve()))
    handle = runs._WORKSPACE_SWEEPERS[key]
    allow_sweep.set()
    assert stop_called.wait(2)
    handle.thread.join(2)

    assert stop_results == [False], "a worker cannot join itself"
    assert not handle.thread.is_alive()
    assert key not in runs._WORKSPACE_SWEEPERS
    assert key not in runs._WORKSPACE_RECONCILED


def test_a_stale_stopper_cannot_clear_a_replacement_worker(tmp_path):
    base = tmp_path / "data"
    key = (os.getpid(), str(base.resolve()))
    replacement_stop = threading.Event()
    replacement = threading.Thread(
        target=lambda: replacement_stop.wait(2), daemon=True,
    )
    replacement.start()
    replacement_handle = runs._WorkspaceSweeperHandle(
        stop_event=replacement_stop,
        thread=replacement,
    )

    class _OldThread:
        def join(self, timeout=None):
            del timeout
            with runs._WORKSPACE_RECONCILE_LOCK:
                runs._WORKSPACE_SWEEPERS[key] = replacement_handle
                runs._WORKSPACE_RECONCILED.add(key)

        def is_alive(self):
            return False

    old_handle = runs._WorkspaceSweeperHandle(
        stop_event=threading.Event(),
        thread=_OldThread(),
    )
    with runs._WORKSPACE_RECONCILE_LOCK:
        runs._WORKSPACE_SWEEPERS[key] = old_handle
        runs._WORKSPACE_RECONCILED.add(key)

    try:
        assert runs._stop_workspace_sweeper(base, timeout_s=2) is True
        assert runs._WORKSPACE_SWEEPERS[key] is replacement_handle
        assert key in runs._WORKSPACE_RECONCILED
    finally:
        replacement_stop.set()
        replacement.join(2)
        with runs._WORKSPACE_RECONCILE_LOCK:
            runs._WORKSPACE_SWEEPERS.pop(key, None)
            runs._WORKSPACE_RECONCILED.discard(key)


def test_stopping_no_sweepers_does_not_read_the_lifecycle_clock(monkeypatch):
    assert not any(key[0] == os.getpid() for key in runs._WORKSPACE_SWEEPERS)

    def unexpected_clock_read():
        raise AssertionError("an empty stop must not read the lifecycle clock")

    with monkeypatch.context() as patch:
        patch.setattr(runs, "_WORKSPACE_SWEEPER_MONOTONIC", unexpected_clock_read)
        assert runs._stop_all_workspace_sweepers(timeout_s=2) is True


def test_stopping_live_sweepers_ignores_a_process_global_clock_patch(monkeypatch):
    base = Path("clock-isolation")
    key = (os.getpid(), str(base.resolve()))
    stop_event = threading.Event()
    thread = threading.Thread(target=stop_event.wait, daemon=True)
    thread.start()
    handle = runs._WorkspaceSweeperHandle(stop_event=stop_event, thread=thread)
    with runs._WORKSPACE_RECONCILE_LOCK:
        runs._WORKSPACE_SWEEPERS[key] = handle

    def unexpected_clock_read():
        raise AssertionError("sweeper stop followed a process-global clock patch")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runs.time, "monotonic", unexpected_clock_read)
            assert runs._stop_all_workspace_sweepers(timeout_s=2) is True
        assert not thread.is_alive()
        assert key not in runs._WORKSPACE_SWEEPERS
    finally:
        stop_event.set()
        thread.join(2)
        with runs._WORKSPACE_RECONCILE_LOCK:
            runs._WORKSPACE_SWEEPERS.pop(key, None)


def test_the_fork_reset_replaces_an_inherited_locked_mutex():
    assert runs._stop_all_workspace_sweepers(timeout_s=2)
    inherited_lock = runs._WORKSPACE_RECONCILE_LOCK
    inherited_lock.acquire()
    try:
        runs._reset_workspace_reconciliation_after_fork()
    finally:
        inherited_lock.release()

    child_lock = runs._WORKSPACE_RECONCILE_LOCK
    assert child_lock is not inherited_lock
    assert child_lock.acquire(blocking=False)
    child_lock.release()


@pytest.mark.usefixtures("cloud_runtime")
def test_http_application_lifespan_stops_workspace_sweepers(
    tmp_path, monkeypatch
):
    from starlette.testclient import TestClient

    from tinyassets import scoped_reset, universe_server
    from tinyassets.api import visibility

    lifecycle: list[str] = []

    class _Barrier:
        def release(self):
            lifecycle.append("writer-barrier")

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        scoped_reset, "prepare_service_writer_barrier", lambda _root: _Barrier()
    )
    monkeypatch.setattr(visibility, "run_visibility_startup_gate", lambda: None)
    monkeypatch.setattr(
        universe_server, "start_scheduler_for_serving", lambda: True
    )
    monkeypatch.setattr(
        universe_server,
        "stop_scheduler_for_serving",
        lambda: lifecycle.append("scheduler"),
    )
    # Record the stop AND perform it. Replacing it with a bare recorder left
    # the real staging sweeper this lifespan starts running for the rest of
    # the session, so every later start_sweeper() was a no-op and
    # test_workspace_staging's boot-sweeper test timed out whenever it ran
    # after this one (order-dependent; seen on two heads 2026-10-01).
    real_stop = universe_server.stop_workspace_sweepers_for_serving

    def _stop() -> None:
        lifecycle.append("workspace-sweepers")
        real_stop()

    monkeypatch.setattr(universe_server, "stop_workspace_sweepers_for_serving", _stop)

    with TestClient(universe_server.create_streamable_http_app()):
        lifecycle.append("serving")

    assert lifecycle == [
        "serving", "scheduler", "workspace-sweepers", "writer-barrier",
    ]


def test_the_periodic_pass_repairs_an_orphaned_active_lease(tmp_path, monkeypatch):
    base = tmp_path / "data"
    _seed_run(base, "r7", status="completed")
    lease = _admit(base, tmp_path, "r7")
    # The run is terminal but nothing enqueued (an older terminal writer): the
    # sweep notices the ACTIVE lease with a finished run and repairs it.
    monkeypatch.setattr("tinyassets.workspace_fs.RealPoolFilesystem", _NoopFs)
    assert _pending(base, "r7") == []
    runs._workspace_sweep_once(base, claimant="test")
    after = workspace_pool.get_lease(runs.runs_db_path(base), lease.lease_id)
    assert after is not None and after.state == "AVAILABLE"


def test_the_scratch_root_is_inside_the_command_center_and_startup_makes_none(
    tmp_path, monkeypatch
):
    """The adapter admits into ``<center>/workspaces/scratch``, and startup
    agrees on that one spelling without creating it (Codex code round 2, #1).

    A lease has to be a directory its OWNER owns -- a node sandbox mounts
    nothing else -- so it lives in the owner's command center and is created
    per operation, under the admitted owner identity, by the owner's own cell.
    Startup has nobody to name, so it creates nothing.
    """
    from tinyassets.effectors.workspace import scratch_pool_root

    universe = tmp_path / "data" / "universe-x"
    universe.mkdir(parents=True)
    monkeypatch.setattr(workspace_pool, "startup_sweep", lambda *a, **k: 0)
    assert runs.ensure_workspace_reconciled(universe, start_sweeper=False) is True
    assert runs._ensure_scratch_root(universe) == scratch_pool_root(universe)
    assert scratch_pool_root(universe) == universe / "workspaces" / "scratch"
    assert not (tmp_path / "data" / "scratch").exists()
    assert not (universe / "workspaces").exists()


def test_a_workspace_command_timeout_keeps_its_class():
    def classify(text):
        return runs._classify_failure({"status": "failed", "error": text})

    kind = classify("Workspace command timeout: node 'n1' ws.run exceeded 30s")
    assert kind == "workspace_command_timeout"
    assert classify("Node timeout: node 'n1' exceeded 300s") == "timeout"


def test_the_chain_closes_workspace_descriptors_on_revoke_and_settle():
    from tinyassets.effectors import EffectChain

    class _Mount:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    chain = EffectChain(run_id="r-close", base_path=None)
    a, b = _Mount(), _Mount()
    chain.register_workspace("checkout_a", a)
    chain.register_workspace("checkout_b", b)
    assert chain.revoke_workspace("checkout_a") is a
    assert a.closed == 1
    chain.revoke_workspace("checkout_a")
    assert a.closed == 1, "idempotent: a second revoke closes nothing twice"
    chain.settle()
    assert b.closed == 1, "settle closes what the run still held"
    assert chain.workspace_mount("checkout_b") is None


def test_startup_reconciliation_settles_lost_push_intents(tmp_path, monkeypatch):
    """A push whose receipt was lost is settled by ls-remote on the next
    startup (workspace-node D1; Codex code round 2 #4) - best-effort, never
    blocking the startup on the network."""
    from tinyassets import workspace_worker

    universe = tmp_path / "data" / "universe-y"
    universe.mkdir(parents=True)
    monkeypatch.setattr(workspace_pool, "startup_sweep", lambda *a, **k: 0)
    calls: list = []
    monkeypatch.setattr(
        workspace_worker, "reconcile_push_intents",
        lambda base, **k: calls.append(Path(base)) or [("i-1", "done")],
    )
    assert runs.ensure_workspace_reconciled(universe, start_sweeper=False) is True
    assert calls == [universe]

    def _boom(base, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(workspace_worker, "reconcile_push_intents", _boom)
    settled = runs._reconcile_push_intents(universe, when="test")
    assert settled == 0, "a failure is logged, not raised"


@pytest.mark.parametrize("kind", runs.WORKSPACE_FAILURE_KINDS)
def test_every_workspace_refusal_is_its_own_class_with_an_action(kind):
    summary = f"external write failed - checkout/workspace: refused [{kind}]"
    assert runs._classify_external_write(summary) == kind
    assert runs.external_write_suggested_action(kind)
    assert runs.ACTIONABLE_BY[kind] == "chatbot"


def test_non_workspace_summaries_classify_as_before():
    assert (
        runs._classify_external_write("external write failed - x [effect_budget_exhausted]")
        == "effect_budget_exhausted"
    )
    assert (
        runs._classify_external_write("external write failed - x [missing_consent]")
        == "external_write_refused"
    )
    assert runs._classify_external_write("external write failed - 422") == "external_write_failed"


def test_served_docs_name_the_workspace_sink():
    from tinyassets import engine_mcp_server as srv

    doc = srv.__doc__ or ""
    import inspect

    text = inspect.getsource(srv)
    for needle in (
        "WORKSPACES.",
        '"op": "checkout"',
        '"workspace": "<that node id>"',
        "ws.run(",
        "ws.bundle(",
        "workspace_command_timeout",
        "tiny/<command-center-id>/<slug>",
    ):
        assert needle in text or needle in doc, needle
