"""Schedules belong to a person — user-owned-automations tasks 2.1 + 2.2.

Every schedule that ever came due was refused. Three separate holes produced
that, and this module pins each one shut:

  * the tick loop fired as ``scheduler:<schedule_id>``, an actor the run function
    rejects, so no scheduled run ever started;
  * registration read ``owner_actor`` out of the caller's own kwargs and defaulted
    it to ``"anonymous"`` — unauthenticated, un-ACL'd, self-issued authority;
  * a background run reached the provider session with no principal, because the
    tick thread has no request identity, so the founder-home check refused it.

These use the REAL surfaces: real authenticated principals via
``authenticate_request``, a real universe created through the real op (which is
what grants the admin ACL and binds the founder home), the real ``extensions()``
MCP dispatch, and a REAL running scheduler singleton where liveness matters —
not a monkeypatched ``is_running``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

# ── Real fixtures ────────────────────────────────────────────────────────────

#: OAuth scopes a founder needs to create a universe and drive the schedule ops.
#: ``schedule_branch``, ``pause_schedule``, ``unpause_schedule``, and
#: ``unschedule_branch`` all derive EXTENSIONS_COSTLY (tinyassets/auth/provider.py);
#: ``.admin`` is included here too so this fixture keeps covering the
#: fine-grained-admin-scoped path as well, not just costly.
_FOUNDER_CAPS = [
    "tinyassets.universe.costly",
    "tinyassets.extensions.read",
    "tinyassets.extensions.write",
    "tinyassets.extensions.admin",
    "tinyassets.extensions.costly",
]

#: The exact production shape from the 2026-08-30 concern
#: (docs/concerns/2026-08-30-owner-cannot-pause-or-delete-own-schedule-from-app.md):
#: a founder session with the coarse ``costly`` grant ``schedule_branch`` needs,
#: and deliberately no fine-grained ``tinyassets.extensions.admin`` scope --
#: proves pause/unpause/unschedule no longer require the admin tier they used to.


@pytest.fixture
def env(tmp_path: Path, monkeypatch, authenticate_request):
    """A real data dir with the author server + runs DB up, and the inbound flag OFF.

    The flag stays unset deliberately: after 2.2 the scheduler lifecycle is
    independent of it, so every test here runs in the configuration that used to
    mean "schedules silently never tick".
    """
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.delenv("TINYASSETS_INBOUND_ENABLED", raising=False)
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.runs import initialize_runs_db
    from tinyassets.scheduler import shutdown_scheduler

    initialize_author_server(base)
    initialize_runs_db(base)  # scheduler tables, as the daemon does at boot
    shutdown_scheduler()  # no singleton leaked in from another module
    try:
        yield base, authenticate_request
    finally:
        shutdown_scheduler()


def _create_universe(sub: str, authenticate) -> str:
    """Authenticate as ``sub`` and create a REAL universe they own.

    The real op is what grants the admin ACL and binds the founder home — the two
    facts registration checks — so the test never asserts against a hand-built
    permission row that production would not produce.
    """
    from tinyassets.api import universe as universe_api
    from tinyassets.api.helpers import _base_path

    authenticate(sub, _FOUNDER_CAPS)
    out = json.loads(universe_api._universe_impl(action="create_universe"))
    assert out.get("error") is None, out
    uid = out["universe_id"]
    # The real op grants the ACL and binds the home; the serving assignment is
    # separate state, and the tick refuses without it.
    set_provider_assignment(_base_path(), universe_id=uid, owner=sub)
    return uid


def set_provider_assignment(
    base: Path, *, universe_id: str, owner: str, state: str = "ready"
) -> None:
    """Write a REAL `provider_assignments` row through the real writer.

    The tick refuses a universe with no serving assignment (D1/D3: authority is
    resolved from what the universe currently has), so a firing test has to give
    it one. Written through `store_provider_assignment_in_transaction` — which
    validates the digest — so a test cannot pass against a row shape production
    would reject.
    """
    from tinyassets.provider_assignment import (
        ProviderAssignment,
        ensure_provider_assignment_schema,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )
    from tinyassets.storage import db_path

    fields = {
        "owner_user_id": owner,
        "universe_id": universe_id,
        "provider": "codex",
        "generation": 1,
        "binding_id": "bnd-sched",
        "credential_reference_id": "cred-sched",
        "credential_reference_generation": 1,
        "credential_reference_digest": "sha256:" + "1" * 64,
    }
    assignment = ProviderAssignment(
        state=state,
        binding_generation=1,
        binding_digest="sha256:" + "2" * 64,
        assignment_digest=provider_assignment_digest(**fields),
        updated_at="2026-08-29T00:00:00+00:00",
        **fields,
    )
    conn = sqlite3.connect(db_path(base))
    try:
        ensure_provider_assignment_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        store_provider_assignment_in_transaction(conn, assignment)
        conn.commit()
    finally:
        conn.close()


def _seed_branch(base: Path, *, bid: str, author: str) -> None:
    """Persist a REAL, structurally-valid, runnable branch authored by ``author``."""
    from tinyassets.daemon_server import save_branch_definition

    src = "def run(state):\n    return {'out': 'ok'}\n"
    save_branch_definition(base, branch_def={
        "branch_def_id": bid,
        "name": bid,
        "author": author,
        "domain_id": "workflow",
        "visibility": "public",
        "node_defs": [{
            "node_id": "only",
            "display_name": "Only",
            "phase": "custom",
            "input_keys": [],
            "output_keys": ["out"],
            "source_code": src,
            "approved": True,
            "approved_source_hash": hashlib.sha256(src.encode()).hexdigest(),
            "tools_allowed": [],
        }],
        "graph_nodes": [{"id": "only", "node_def_id": "only", "position": 0}],
        "edges": [
            {"from_node": "START", "to_node": "only"},
            {"from_node": "only", "to_node": "END"},
        ],
        "conditional_edges": [],
        "state_schema": [{"name": "out", "type": "str"}],
        "entry_point": "only",
    })


# ── Registration derives the owner; it never accepts one ─────────────────────


# ── Firing carries the universe actor and the owner principal ────────────────


# ── The principal reaches the provider session ───────────────────────────────


def test_enqueue_binds_the_session_with_the_given_principal(env):
    """A background run has no request identity; the principal must be passed.

    The request identity is cleared before the call, exactly as it is on the
    scheduler's tick thread. If the binding still read
    ``current_request_actor_id()`` the session would be built for ``anonymous``
    and ``_validate_founder_home`` would refuse — which is why every scheduled
    run failed even once its actor was right.
    """
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")

    import tinyassets.foreground_run_provider as fgp
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.api.runs import enqueue_universe_branch_run

    captured: list[dict] = []
    original = fgp.new_foreground_run_provider_session

    def _spy(base_path, **kwargs):
        captured.append(dict(kwargs))
        return original(base_path, **kwargs)

    authenticate(None)  # no request context — a background thread has none
    assert current_request_actor_id() == ""  # nobody, not "anonymous"

    fgp.new_foreground_run_provider_session = _spy
    try:
        run_id = enqueue_universe_branch_run(
            base,
            universe_id=uid,
            branch_def_id="b",
            inputs={},
            run_name="sched",
            principal_id="founder-a",
        )
    finally:
        fgp.new_foreground_run_provider_session = original

    assert captured, "the run never reached the provider session"
    assert captured[0]["principal_id"] == "founder-a"
    assert captured[0]["universe_id"] == uid
    _drain_run(base, run_id)


def test_enqueue_without_a_principal_keeps_the_request_identity(env):
    """The webhook path is unchanged: no principal means the request's own."""
    base, authenticate = env
    uid = _create_universe("founder-a", authenticate)
    _seed_branch(base, bid="b", author="founder-a")

    import tinyassets.foreground_run_provider as fgp
    from tinyassets.api.runs import enqueue_universe_branch_run

    captured: list[dict] = []
    original = fgp.new_foreground_run_provider_session

    def _spy(base_path, **kwargs):
        captured.append(dict(kwargs))
        return original(base_path, **kwargs)

    fgp.new_foreground_run_provider_session = _spy
    try:
        run_id = enqueue_universe_branch_run(
            base, universe_id=uid, branch_def_id="b", inputs={}, run_name="webhook"
        )
    finally:
        fgp.new_foreground_run_provider_session = original

    assert captured[0]["principal_id"] == "founder-a"  # the live request subject
    _drain_run(base, run_id)


def _drain_run(base: Path, run_id: str, timeout: float = 5.0) -> None:
    """Let a real background run reach a terminal state so it cannot leak."""
    from tinyassets.runs import get_run

    terminal = {"completed", "failed", "cancelled", "interrupted"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (get_run(base, run_id) or {}).get("status") in terminal:
            return
        time.sleep(0.02)


# ── Owner controls ───────────────────────────────────────────────────────────


# ── Tick-time authority: D3 refusals and auto-pause ──────────────────────────


# ── The owner can read why their schedule stopped ────────────────────────────


# ── The due-row claim ────────────────────────────────────────────────────────


# ── Tier fix: pause/unpause/unschedule ride costly, not admin ────────────────
#
# 2026-08-30 concern: these three sat in `_EXTENSIONS_ADMIN_ACTIONS`, so a
# founder session with the ordinary coarse `costly` grant `schedule_branch`
# already needs was refused at the OAuth scope gate before the handler's real
# owner-or-admin check (`_schedule_control_context`) ever ran. They now derive
# `tinyassets.extensions.costly`, the same tier as `schedule_branch`. These
# mutation-check tests drive the real scope resolver (`require_action_scope`
# via the real `extensions()` dispatch, through `_ext`) and the real handler —
# no monkeypatched capability set.


# ── Lifecycle (2.2) ──────────────────────────────────────────────────────────


def test_the_scheduler_starts_with_the_inbound_flag_off(env):
    """2.2 — schedule ticks are not part of the inbound channel surface."""
    from tinyassets.scheduler import is_running, shutdown_scheduler
    from tinyassets.universe_server import (
        start_scheduler_for_serving,
        stop_scheduler_for_serving,
    )
    from tinyassets.webhook_inbound import inbound_enabled

    assert inbound_enabled() is False  # the configuration under test
    shutdown_scheduler()
    assert is_running() is False

    assert start_scheduler_for_serving() is True
    try:
        assert is_running() is True
    finally:
        stop_scheduler_for_serving()
    assert is_running() is False


def test_is_running_is_false_for_a_scheduler_that_was_never_started(env):
    """Liveness, not the mere presence of a singleton object."""
    import tinyassets.scheduler as sched

    sched.shutdown_scheduler()
    base, _authenticate = env
    never_started = sched.Scheduler(base, lambda *a, **k: None)
    original = sched._SINGLETON
    sched._SINGLETON = never_started
    try:
        assert sched.is_running() is False
    finally:
        sched._SINGLETON = original


# ── Schema migration ─────────────────────────────────────────────────────────


def _old_schema_db(tmp_path: Path) -> Path:
    """A ``.runs.db`` with the PRE-2.1 ``branch_schedules`` table and one row."""
    db = tmp_path / ".runs.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE branch_schedules (
            schedule_id          TEXT PRIMARY KEY,
            branch_def_id        TEXT NOT NULL,
            owner_actor          TEXT NOT NULL,
            cron_expr            TEXT NOT NULL DEFAULT '',
            interval_seconds     REAL NOT NULL DEFAULT 0,
            inputs_template_json TEXT NOT NULL DEFAULT '{}',
            skip_if_running      INTEGER NOT NULL DEFAULT 0,
            active               INTEGER NOT NULL DEFAULT 1,
            created_at           REAL NOT NULL,
            last_fired_at        REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO branch_schedules "
        "(schedule_id, branch_def_id, owner_actor, interval_seconds, created_at) "
        "VALUES ('old','b1','alice',600.0,0)"
    )
    conn.commit()
    conn.close()
    return db


def test_initialize_runs_db_migrates_an_existing_schedules_table(tmp_path, monkeypatch):
    """The PRODUCTION path, which is where this broke.

    `SCHEDULER_SCHEMA` creates an index on ``branch_schedules(universe_id)``.
    Run against an install that predates the column, that index raised
    ``OperationalError: no such column: universe_id`` and took the whole of
    `initialize_runs_db` down — so the daemon could not open an existing data
    root at all. The earlier test called `scheduler._connect` directly and
    never touched the failing order.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    db = _old_schema_db(tmp_path)

    from tinyassets.runs import initialize_runs_db

    initialize_runs_db(tmp_path)  # must not raise
    initialize_runs_db(tmp_path)  # idempotent

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(branch_schedules)")}
        assert {"paused", "universe_id", "owner_principal_id", "pause_reason"} <= cols
        indexes = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_schedules_universe" in indexes
        row = dict(
            conn.execute(
                "SELECT * FROM branch_schedules WHERE schedule_id='old'"
            ).fetchone()
        )
    finally:
        conn.close()
    assert row["universe_id"] == "" and row["owner_principal_id"] == ""


def test_four_concurrent_initialize_runs_db_calls_migrate_correctly(tmp_path, monkeypatch):
    """The PRODUCTION entry point raced, not just the migration primitive.

    Codex noted the four-thread test drove `migrate_scheduler_schema` directly.
    What a restarting fleet actually does is call `initialize_runs_db` from
    several processes at once, which runs the migration AND the schema script.

    SCOPE. ``initialize_runs_db`` is not concurrency-safe today and that is
    PRE-EXISTING, not something this change introduced: four concurrent callers
    raise ``database is locked`` on a FRESH database too, where the scheduler
    migration is a no-op (measured 2026-08-29 — 3 of 4 threads fresh, 2 of 4 with
    the migration, so the added `BEGIN IMMEDIATE` if anything helps). Fixing the
    schema script's lock behaviour touches every daemon boot and every test and
    belongs in its own lane. So a lock collision is retried here, exactly as a
    real caller would, and the assertions are about the MIGRATION: no thread may
    see `no such column` or `duplicate column`, and the final schema must be
    right — neither of which a retry could paper over.
    """
    import threading

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _old_schema_db(tmp_path)
    from tinyassets.runs import initialize_runs_db

    schema_errors: list[str] = []
    barrier = threading.Barrier(4)

    def initialize() -> None:
        barrier.wait(timeout=10)
        for _attempt in range(20):
            try:
                initialize_runs_db(tmp_path)
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc).lower():
                    time.sleep(0.05)
                    continue
                schema_errors.append(repr(exc))
                return
            except BaseException as exc:  # noqa: BLE001
                schema_errors.append(repr(exc))
                return
        schema_errors.append("gave up retrying a locked database")

    threads = [threading.Thread(target=initialize) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), (
        "a thread is still running after the 60s join timeout -- it hung "
        "instead of finishing or raising"
    )
    assert schema_errors == [], schema_errors

    conn = sqlite3.connect(tmp_path / ".runs.db")
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(branch_schedules)")]
        runs_cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
        runs_indexes = {r[1] for r in conn.execute("PRAGMA index_list(runs)")}
    finally:
        conn.close()
    assert cols.count("universe_id") == 1  # added exactly once, not four times
    assert {"pause_reason", "revision"} <= set(cols)

    # The same race this test proves fixed for branch_schedules also applies to
    # every runs-table column _migrate_runs_table_columns adds via ALTER, plus
    # the indexes that depend on them (Codex ADAPT on a41e5c6c) -- assert the
    # full migrated shape, not just branch_schedules.
    assert runs_cols.count("branch_version_id") == 1  # added exactly once, not four times
    assert {
        "queue_universe_id",
        "branch_version_id",
        "daemon_id",
        "runtime_instance_id",
        "worker_id",
        "branch_task_id",
        "provider_used",
        "model",
        "token_count",
        "owner_user_id",
    } <= set(runs_cols)
    assert {
        "idx_runs_branch_version",
        "idx_runs_branch_task",
        "idx_runs_scope_status_finished",
    } <= runs_indexes


def test_the_migration_is_safe_when_two_connections_race(tmp_path):
    """Check-then-ALTER is atomic under one BEGIN IMMEDIATE; a loser is idempotent."""
    import threading

    from tinyassets import scheduler as sched

    db = _old_schema_db(tmp_path)
    errors: list[BaseException] = []
    barrier = threading.Barrier(4)

    def migrate() -> None:
        conn = sqlite3.connect(str(db), timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            barrier.wait(timeout=10)
            sched.migrate_scheduler_schema(conn)
        except BaseException as exc:  # noqa: BLE001 - the assertion is "none of these"
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=migrate) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == [], errors

    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(branch_schedules)")]
    finally:
        conn.close()
    assert cols.count("universe_id") == 1  # added exactly once, not four times


# ── Cadence floor ────────────────────────────────────────────────────────────


#: The expression Codex used to break the cadence floor, and the instant that
#: breaks it. US DST begins Sunday 2026-03-08 at 02:00 local, so on a
#: DST-observing host 01:59 EST → 03:00 EDT is sixty elapsed seconds while the
#: minute/hour algebra reads it as 3540.


# ── Legacy rows stay discoverable and deletable ──────────────────────────────


def test_the_migration_keeps_every_schedule_row_with_its_retirement(tmp_path):
    """Schedules are retired. An existing install gains the columns, and every
    row is kept with a recorded disposition -- inactive, paused, and the reason
    on the row -- rather than dropped. Idempotent."""
    from tinyassets import scheduler as sched

    db = tmp_path / ".runs.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE branch_schedules (
            schedule_id          TEXT PRIMARY KEY,
            branch_def_id        TEXT NOT NULL,
            owner_actor          TEXT NOT NULL,
            cron_expr            TEXT NOT NULL DEFAULT '',
            interval_seconds     REAL NOT NULL DEFAULT 0,
            inputs_template_json TEXT NOT NULL DEFAULT '{}',
            skip_if_running      INTEGER NOT NULL DEFAULT 0,
            active               INTEGER NOT NULL DEFAULT 1,
            created_at           REAL NOT NULL,
            last_fired_at        REAL
        );
        """
    )
    conn.executemany(
        "INSERT INTO branch_schedules "
        "(schedule_id, branch_def_id, owner_actor, interval_seconds, active, created_at) "
        "VALUES (?, 'b1', 'alice', 60.0, ?, 0)",
        [("was-active", 1), ("was-inactive", 0)],
    )
    conn.commit()
    conn.close()

    for _ in range(2):  # idempotent: a second connect must not raise
        migrated = sched._connect(db)
        try:
            cols = {r[1] for r in migrated.execute("PRAGMA table_info(branch_schedules)")}
            assert {"paused", "universe_id", "owner_principal_id", "pause_reason"} <= cols
            rows = {
                row["schedule_id"]: dict(row)
                for row in migrated.execute("SELECT * FROM branch_schedules")
            }
        finally:
            migrated.close()
    assert set(rows) == {"was-active", "was-inactive"}
    for row in rows.values():
        assert row["active"] == 0
        assert row["paused"] == 1
        assert row["pause_reason"] == sched.RETIRED_SCHEDULE_REASON
