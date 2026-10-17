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
from tests.owner_app_fixture import create_owner_app

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
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
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

    app = create_owner_app()
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

    with TestClient(create_owner_app()):
        pass

    assert order == ["initialize", "scheduler"], order


@pytest.mark.parametrize("transport", ["sse", "stdio", "streamable-http"])
def test_router_startup_never_starts_an_assigned_consumer(
    fresh_data_dir, monkeypatch, transport
):
    from tinyassets import consumer_runtime
    from tinyassets import universe_server as us
    from tinyassets.control_plane import tick

    def forbidden(*args, **kwargs):
        pytest.fail("router opened owner storage or started an owner consumer")

    monkeypatch.setattr(consumer_runtime, "initialize", forbidden)
    monkeypatch.setattr(tick, "AssignedQueueConsumer", forbidden)
    reached = []
    monkeypatch.setattr(us.uvicorn, "run", lambda *a, **k: reached.append("router"))
    if transport == "streamable-http":
        us.main(transport=transport)
        assert reached == ["router"]
    else:
        with pytest.raises(ValueError, match="only authenticated streamable-http"):
            us.main(transport=transport)
        assert reached == []


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
        with TestClient(create_owner_app()):
            pytest.fail("must not serve after initialization failure")
    assert events == ["stop", "sweepers_stop", "release"]


@pytest.mark.parametrize("transport", ["sse", "stdio", "streamable-http"])
def test_router_does_not_depend_on_owner_storage_initialization(
    fresh_data_dir, monkeypatch, transport
):
    from tinyassets import consumer_runtime
    from tinyassets import universe_server as us

    def unavailable(base):
        raise sqlite3.OperationalError("owner store unavailable")

    monkeypatch.setattr(consumer_runtime, "initialize", unavailable)
    reached = []
    monkeypatch.setattr(us.uvicorn, "run", lambda *a, **k: reached.append("router"))
    if transport == "streamable-http":
        us.main(transport=transport)
        assert reached == ["router"]
    else:
        with pytest.raises(ValueError, match="only authenticated streamable-http"):
            us.main(transport=transport)
        assert reached == []
