"""Initialize serving storage before starting scheduler/assigned workers.

CI 35650830517 failed during HTTP startup at the first WAL switch. These tests
pin storage-before-workers ordering with controlled, real SQLite contention.
The stand-in holds a rollback-journal write transaction; the actual scheduler
switches to WAL before migrating, so this is not a reconstruction of its exact
historical lock state. Unordered independent initializers remain a separate risk.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest
from starlette.testclient import TestClient

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

# Every test here enters the real serving lifespan or hosted main, both of
# which now require an admitted cloud runtime. Module-local and explicit --
# never autouse -- and no admission guard is stubbed: the storage-before-
# workers ordering and the initialization-failure negatives are unchanged.
pytestmark = pytest.mark.usefixtures("cloud_runtime")


class SchedulerLikeWriter:
    """A controlled competing writer installed at the scheduler start boundary."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.attached = threading.Event()
        self.journal_mode_when_attached: str | None = None
        self._conn: sqlite3.Connection | None = None

    def attach(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=5.0)
        self.journal_mode_when_attached = str(
            self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        # Deliberately retain the current journal mode while holding a write lock.
        self._conn.execute("BEGIN IMMEDIATE")
        self._conn.execute("CREATE TABLE IF NOT EXISTS _scheduler_probe(x)")
        self.attached.set()

    def release(self) -> None:
        if self._conn is not None:
            try:
                self._conn.rollback()
            finally:
                self._conn.close()
                self._conn = None


@pytest.fixture
def fresh_data_dir(tmp_path, monkeypatch):
    """A data dir with no `.runs.db` yet -- the only state the race needs."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.runs import runs_db_path

    db = runs_db_path(tmp_path)
    assert not db.exists(), "this regression requires a fresh database"
    return tmp_path


def _journal_mode(db_path) -> str:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()


def test_http_lifespan_initializes_runs_db_before_the_scheduler_attaches(
    fresh_data_dir, monkeypatch
):
    """Entering the production HTTP lifespan on a fresh data dir must not race.

    RED before the fix: `start_scheduler_for_serving` runs first, the stand-in
    holds the write lock, and `initialize_consumer`'s `PRAGMA journal_mode=WAL`
    raises `database is locked` out of `TestClient.__enter__`.
    """
    from tinyassets import universe_server as us
    from tinyassets.runs import runs_db_path

    db = runs_db_path(fresh_data_dir)
    writer = SchedulerLikeWriter(db)

    def _start_scheduler_stand_in() -> bool:
        writer.attach()
        return True

    monkeypatch.setattr(us, "start_scheduler_for_serving", _start_scheduler_stand_in)
    monkeypatch.setattr(us, "stop_scheduler_for_serving", writer.release)

    app = us.create_streamable_http_app()
    try:
        with TestClient(app):
            pass
    finally:
        writer.release()

    assert writer.attached.is_set(), "the stand-in scheduler never started"
    assert writer.journal_mode_when_attached == "wal", (
        "the scheduler attached to a database that had not been initialized yet; "
        f"journal_mode was {writer.journal_mode_when_attached!r}"
    )
    assert _journal_mode(db) == "wal"


def test_http_lifespan_orders_initialization_before_background_work(fresh_data_dir, monkeypatch):
    """The ordering itself, independent of whether SQLite happens to lock."""
    from tinyassets import consumer_runtime
    from tinyassets import universe_server as us

    order: list[str] = []
    real_initialize = consumer_runtime.initialize

    def _record_initialize(base):
        order.append("initialize")
        return real_initialize(base)

    monkeypatch.setattr(consumer_runtime, "initialize", _record_initialize)
    monkeypatch.setattr(
        us, "start_scheduler_for_serving", lambda: (order.append("scheduler"), True)[1]
    )
    monkeypatch.setattr(us, "stop_scheduler_for_serving", lambda: None)

    with TestClient(us.create_streamable_http_app()):
        pass

    assert order == ["initialize", "scheduler"], order


@pytest.fixture
def isolate_boot_maintenance_thread(monkeypatch):
    """Do not leak main's perpetual background threads into later tests.

    `main` also takes the process-wide run-recovery lock and starts the
    run-owner watcher, which every 15 s interrupts "dead-owner" runs and
    redelivers terminal events in whatever data dir is CURRENT -- so a watcher
    leaked from here acted on later tests' runs (found by the leak probe,
    2026-10-01). Suppress the thread and restore the recovery globals.
    """
    from tinyassets.api import runs as api_runs

    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_DONE", False)
    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_LOCK", None)
    real_start = threading.Thread.start
    suppressed = []

    def _start(thread):
        if thread.name in ("served-budget-lease-reconciler", "run-owner-watcher"):
            suppressed.append(thread)
            return None
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", _start)
    yield suppressed
    lock = api_runs._RUNS_RECOVERY_LOCK
    if lock is not None:
        from tinyassets.singleton_lock import release_singleton_lock

        release_singleton_lock(lock)
    assert any(t.name == "served-budget-lease-reconciler" for t in suppressed), (
        "main did not reach maintenance thread startup"
    )
    assert all(not thread.is_alive() for thread in suppressed)


@pytest.mark.parametrize("transport", ["sse", "stdio", "streamable-http"])
def test_startup_initializes_before_the_assigned_consumer_starts(
    fresh_data_dir, monkeypatch, isolate_boot_maintenance_thread, transport
):
    """The second serving entrypoint has the same ordering obligation.

    `main`'s sse/stdio path starts `AssignedQueueConsumer` -- a polling
    thread -- before `initialize_consumer`. Fixing only the HTTP lifespan would
    leave this race in place.

    The earlier boot-maintenance block (`universe_server.py:4126-4131`) usually
    initializes first and hides this, but it swallows its own failure
    (`except Exception: logger.exception(...)`). So the first initialize here
    raises, exactly as that swallowed path leaves things, and the serving block
    must still initialize before it starts a background consumer.
    """
    from tinyassets import consumer_runtime
    from tinyassets import universe_server as us
    from tinyassets.runtime import assigned_queue_consumer as aqc

    order: list[str] = []
    real_initialize = consumer_runtime.initialize
    calls = {"n": 0}

    def _record_initialize(base):
        calls["n"] += 1
        if calls["n"] == 1:
            order.append("initialize_swallowed")
            raise sqlite3.OperationalError("database is locked")
        order.append("initialize")
        return real_initialize(base)

    class _RecordingConsumer:
        def __init__(self, base):
            self.base = base

        def start(self):
            order.append("assigned_consumer")

        def stop(self):
            order.append("assigned_consumer_stop")

    monkeypatch.setattr(consumer_runtime, "initialize", _record_initialize)
    monkeypatch.setattr(aqc, "assigned_queue_consumer_enabled", lambda: True)
    monkeypatch.setattr(aqc, "AssignedQueueConsumer", _RecordingConsumer)
    monkeypatch.setattr(us.mcp, "run", lambda *a, **k: order.append("serve"))
    monkeypatch.setattr(us.uvicorn, "run", lambda *a, **k: order.append("serve"))
    from tinyassets import engine_mcp_http

    monkeypatch.setattr(engine_mcp_http, "start_engine_mcp_http_servers", lambda: [])

    us.main(transport=transport)

    assert order[:3] == [
        "initialize_swallowed",
        "initialize",
        "assigned_consumer",
    ], order
    assert "serve" in order, order
    assert order[-1] == "assigned_consumer_stop", (
        f"teardown must still stop the consumer it started: {order}"
    )


def test_initializing_a_fresh_runs_db_under_a_held_write_lock_fails(tmp_path):
    """Control: this competing writer actually blocks fresh initialization."""
    from tinyassets import runs

    base = tmp_path
    writer = SchedulerLikeWriter(runs.runs_db_path(base))
    writer.attach()
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            runs.initialize_runs_db(base)
    finally:
        writer.release()


def test_http_initialization_failure_stops_before_scheduler_and_releases_barrier(
    fresh_data_dir, monkeypatch
):
    from tinyassets import consumer_runtime, scoped_reset
    from tinyassets import universe_server as us

    events = []

    class _Barrier:
        def release(self):
            events.append("release")

    def _fail_initialize(base):
        raise sqlite3.OperationalError("initialization refused")

    monkeypatch.setattr(scoped_reset, "prepare_service_writer_barrier", lambda base: _Barrier())
    monkeypatch.setattr(consumer_runtime, "initialize", _fail_initialize)
    monkeypatch.setattr(us, "start_scheduler_for_serving", lambda: events.append("start"))
    monkeypatch.setattr(us, "stop_scheduler_for_serving", lambda: events.append("stop"))
    monkeypatch.setattr(
        us, "stop_workspace_sweepers_for_serving", lambda: events.append("sweepers_stop")
    )
    with pytest.raises(sqlite3.OperationalError, match="initialization refused"):
        with TestClient(us.create_streamable_http_app()):
            pytest.fail("must not serve after initialization failure")
    assert events == ["stop", "sweepers_stop", "release"]


@pytest.mark.parametrize("transport", ["sse", "stdio", "streamable-http"])
def test_main_initialization_failure_does_not_start_worker_or_serve(
    fresh_data_dir, monkeypatch, isolate_boot_maintenance_thread, transport
):
    from tinyassets import consumer_runtime, engine_mcp_http, scoped_reset
    from tinyassets import universe_server as us
    from tinyassets.runtime import assigned_queue_consumer as aqc

    events = []

    class _Barrier:
        def release(self):
            events.append("release")

    def _fail_initialize(base):
        events.append("initialize")
        raise sqlite3.OperationalError("initialization refused")

    monkeypatch.setattr(scoped_reset, "prepare_service_writer_barrier", lambda base: _Barrier())
    monkeypatch.setattr(consumer_runtime, "initialize", _fail_initialize)
    monkeypatch.setattr(aqc, "assigned_queue_consumer_enabled", lambda: True)
    monkeypatch.setattr(
        aqc,
        "AssignedQueueConsumer",
        lambda base: pytest.fail("worker constructed before storage ready"),
    )
    monkeypatch.setattr(us.mcp, "run", lambda *a, **k: pytest.fail("must not serve"))
    monkeypatch.setattr(us.uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))
    monkeypatch.setattr(engine_mcp_http, "start_engine_mcp_http_servers", lambda: [])
    monkeypatch.setattr(
        us, "stop_workspace_sweepers_for_serving", lambda: events.append("sweepers_stop")
    )
    with pytest.raises(sqlite3.OperationalError, match="initialization refused"):
        us.main(transport=transport)
    expected = ["initialize", "initialize"]
    if transport != "streamable-http":
        expected += ["sweepers_stop", "release"]
    assert events == expected
