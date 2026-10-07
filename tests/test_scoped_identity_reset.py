"""Operator-only, exact-founder-home scoped reset safety contract."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_HOME_A = "u-01aaaaaaaaaaaaaaaaaaaaaaaa"
_HOME_B = "u-01bbbbbbbbbbbbbbbbbbbbbbbb"
_SUBJECT_A = "workos|test-a"
_SUBJECT_B = "workos|test-b"


def _seed(base: Path) -> None:
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
        initialize_author_server,
        set_founder_home,
    )

    initialize_author_server(base)
    for universe_id, subject in (
        (_HOME_A, _SUBJECT_A),
        (_HOME_B, _SUBJECT_B),
    ):
        home = base / universe_id
        home.mkdir()
        (home / "soul.md").write_text(f"# {universe_id}\n", encoding="utf-8")
        ensure_universe_registered(
            base,
            universe_id=universe_id,
            universe_path=home,
        )
        grant_universe_access(
            base,
            universe_id=universe_id,
            actor_id=subject,
            permission="admin",
            granted_by=subject,
        )
        set_founder_home(
            base,
            founder_sub=subject,
            universe_id=universe_id,
            platform_generated=True,
        )


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    base = tmp_path / "data"
    base.mkdir()
    _seed(base)
    return base


def _connect(base: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(base / ".tinyassets.db"))
    conn.row_factory = sqlite3.Row
    return conn


def test_inventory_classifies_current_schema_and_epoch2_stores(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        MAIN_DB_TABLE_CLASSIFICATIONS,
        inspect_reset_scope,
    )

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)

    assert scope.home_id == _HOME_A
    assert scope.blockers == ()
    assert scope.schema_tables <= MAIN_DB_TABLE_CLASSIFICATIONS.keys()
    assert MAIN_DB_TABLE_CLASSIFICATIONS["goal_canonicals"] == "preserve"
    assert MAIN_DB_TABLE_CLASSIFICATIONS["request_admissions"] == "preserve"
    assert MAIN_DB_TABLE_CLASSIFICATIONS["request_admission_events"] == "preserve"
    assert MAIN_DB_TABLE_CLASSIFICATIONS["branch_tasks_v2"] == "preserve_or_block"
    assert (
        MAIN_DB_TABLE_CLASSIFICATIONS["branch_tasks_v2_quarantine"]
        == "block_matching"
    )
    assert (
        MAIN_DB_TABLE_CLASSIFICATIONS["branch_tasks_v2_maintenance_state"]
        == "preserve"
    )
    assert (
        MAIN_DB_TABLE_CLASSIFICATIONS["request_admission_rollouts"]
        == "preserve_or_block"
    )


def test_unclassified_schema_growth_fails_loudly(seeded: Path) -> None:
    from tinyassets.scoped_reset import ScopedResetSchemaError, inspect_reset_scope

    with _connect(seeded) as conn:
        conn.execute(
            "CREATE TABLE future_store "
            "(row_id TEXT PRIMARY KEY, universe_id TEXT NOT NULL)"
        )

    with pytest.raises(ScopedResetSchemaError, match="future_store"):
        inspect_reset_scope(seeded, principal=_SUBJECT_A)


def test_the_retired_fleet_tables_do_not_block_a_scoped_reset(seeded: Path) -> None:
    """Production still holds the fleet-era cloud-automation and background-
    branch tables until a host-action drops them (plan C1, 2026-09-28). Created
    here by their real stores; an unclassified one would block every reset."""
    from tinyassets.scoped_reset import inspect_reset_scope
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
    )
    from tinyassets.storage.cloud_automation_continuation import (
        SQLiteCloudAutomationContinuationStore,
    )
    from tinyassets.storage.cloud_automation_control import (
        CloudAutomationControlStore,
    )

    for store in (
        CloudAutomationControlStore(seeded),
        SQLiteCloudAutomationContinuationStore(seeded),
    ):
        with store.connection():
            pass
    with SQLiteBackgroundBranchAuthorityStore(seeded)._connection():
        pass
    with _connect(seeded) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"cloud_automation_controls", "background_branch_bindings"} <= tables

    inspect_reset_scope(seeded, principal=_SUBJECT_A)


@pytest.mark.parametrize(
    "growth",
    ["column", "generated_column", "trigger", "incoming_foreign_key"],
)
def test_reviewed_schema_manifest_rejects_authority_growth(
    seeded: Path,
    growth: str,
) -> None:
    from tinyassets.scoped_reset import ScopedResetSchemaError, inspect_reset_scope

    with _connect(seeded) as conn:
        if growth == "column":
            conn.execute("ALTER TABLE universes ADD COLUMN future_owner TEXT")
        elif growth == "generated_column":
            conn.execute(
                "ALTER TABLE universes ADD COLUMN shadow TEXT "
                "GENERATED ALWAYS AS (display_name) VIRTUAL"
            )
        elif growth == "trigger":
            conn.execute(
                "CREATE TRIGGER future_delete_authority "
                "AFTER DELETE ON universes BEGIN "
                "DELETE FROM user_accounts; END"
            )
        else:
            conn.execute(
                "CREATE TABLE escrow_locks ("
                "lock_id TEXT PRIMARY KEY, gate_claim_id TEXT NOT NULL, "
                "staker_id TEXT NOT NULL, recipient_id TEXT, "
                "status TEXT NOT NULL, universe_id TEXT, "
                "FOREIGN KEY(universe_id) REFERENCES universes(universe_id) "
                "ON DELETE CASCADE)"
            )

    expected = (
        "foreign-key"
        if growth == "incoming_foreign_key"
        else "column"
        if growth == "generated_column"
        else growth
    )
    with pytest.raises(ScopedResetSchemaError, match=expected):
        inspect_reset_scope(seeded, principal=_SUBJECT_A)


@pytest.mark.parametrize("foreign_state", ["binding", "grant"])
def test_foreign_binding_or_grant_blocks_exact_home(
    seeded: Path,
    foreign_state: str,
) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    with _connect(seeded) as conn:
        if foreign_state == "binding":
            conn.execute(
                "UPDATE founder_home SET universe_id = ? WHERE founder_sub = ?",
                (_HOME_A, _SUBJECT_B),
            )
        else:
            conn.execute(
                """
                INSERT INTO universe_acl (
                    universe_id, actor_id, permission, granted_at, granted_by
                ) VALUES (?, ?, 'read', 1.0, ?)
                """,
                (_HOME_A, _SUBJECT_B, _SUBJECT_A),
            )

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert any(foreign_state in blocker for blocker in scope.blockers)


def test_foreign_terminal_actors_block_exact_home(seeded: Path) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    with _connect(seeded) as conn:
        conn.execute(
            """
            INSERT INTO user_requests (
                request_id, universe_id, user_id, request_type, text,
                status, created_at, updated_at
            ) VALUES ('foreign-request', ?, ?, 'test', 'done', 1.0, 1.0, 1.0)
            """,
            (_HOME_A, _SUBJECT_B),
        )
        conn.execute(
            """
            INSERT INTO branches (
                branch_id, universe_id, name, created_by, created_at, updated_at
            ) VALUES ('foreign-branch', ?, 'foreign', ?, 1.0, 1.0)
            """,
            (_HOME_A, _SUBJECT_B),
        )
        conn.execute(
            """
            INSERT INTO universe_snapshots (
                snapshot_id, universe_id, branch_id, label, created_by, created_at
            ) VALUES ('foreign-snapshot', ?, 'foreign-branch', 'foreign', ?, 1.0)
            """,
            (_HOME_A, _SUBJECT_B),
        )
        conn.execute(
            """
            INSERT INTO vote_windows (
                vote_id, universe_id, vote_type, subject_type, subject_id,
                created_by, opens_at, closes_at, status
            ) VALUES (
                'foreign-vote', ?, 'test', 'test', 'test', ?, 1.0, 2.0,
                'closed'
            )
            """,
            (_HOME_A, _SUBJECT_B),
        )
        conn.execute(
            """
            INSERT INTO vote_ballots (
                vote_id, user_id, choice, created_at
            ) VALUES ('foreign-vote', ?, 'yes', 1.0)
            """,
            (_SUBJECT_B,),
        )

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    for table in (
        "user_requests",
        "branches",
        "universe_snapshots",
        "vote_windows",
        "vote_ballots",
    ):
        assert any(table in blocker for blocker in scope.blockers)


@pytest.mark.parametrize("active_store", ["daemon", "request", "epoch2"])
def test_active_daemon_request_or_epoch2_task_blocks(
    seeded: Path,
    active_store: str,
) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    with _connect(seeded) as conn:
        if active_store == "daemon":
            conn.execute(
                """
                INSERT INTO author_runtime_instances (
                    instance_id, universe_id, author_id, provider_name,
                    model_name, status, created_by, created_at, updated_at
                ) VALUES (
                    'runtime-a', ?, 'author-a', 'test', 'test', 'running',
                    ?, 1.0, 1.0
                )
                """,
                (_HOME_A, _SUBJECT_A),
            )
        elif active_store == "request":
            conn.execute(
                """
                INSERT INTO user_requests (
                    request_id, universe_id, user_id, request_type, text,
                    status, created_at, updated_at
                ) VALUES ('request-a', ?, ?, 'test', 'test', 'open', 1.0, 1.0)
                """,
                (_HOME_A, _SUBJECT_A),
            )
        else:
            conn.execute(
                """
                INSERT INTO branch_tasks_v2 (
                    branch_task_id, admission_id, request_id, universe_id,
                    branch_def_id, trigger_source, priority_weight, status,
                    queued_at
                ) VALUES (
                    'task-a', 'admission-a', 'request-a', ?, 'branch-a',
                    'user_request', 1.0, 'pending', '2026-07-25T00:00:00Z'
                )
                """,
                (_HOME_A,),
            )

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert any(active_store in blocker for blocker in scope.blockers)


def test_preserved_request_audit_dependencies_block_terminal_request_delete(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    with _connect(seeded) as conn:
        conn.execute(
            """
            INSERT INTO user_requests (
                request_id, universe_id, user_id, request_type, text,
                status, created_at, updated_at
            ) VALUES (
                'request-audit', ?, ?, 'test', 'test', 'done', 1.0, 1.0
            )
            """,
            (_HOME_A, _SUBJECT_A),
        )
        conn.execute(
            """
            INSERT INTO request_admissions (
                admission_id, request_id, branch_task_id, tenant_id, actor_id,
                universe_id, idempotency_key_hash, body_digest,
                body_digest_version, trigger_source, accepted_priority_weight,
                priority_policy_version, result_json, created_at, updated_at
            ) VALUES (
                'admission-audit', 'request-audit', 'task-audit', 'tenant',
                ?, ?, 'idem', 'digest', 'v1', 'user_request', 1.0, 'v1',
                '{}', '2026-07-25T00:00:00Z', '2026-07-25T00:00:00Z'
            )
            """,
            (_SUBJECT_A, _HOME_A),
        )
        conn.execute(
            """
            INSERT INTO request_admission_events (
                event_id, admission_id, request_id, branch_task_id,
                event_type, event_at
            ) VALUES (
                'event-audit', 'admission-audit', 'request-audit',
                'task-audit', 'completed', '2026-07-25T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO branch_tasks_v2 (
                branch_task_id, admission_id, request_id, universe_id,
                branch_def_id, trigger_source, priority_weight, status,
                queued_at, terminal_at
            ) VALUES (
                'task-audit', 'admission-audit', 'request-audit', ?,
                'branch-a', 'user_request', 1.0, 'succeeded',
                '2026-07-25T00:00:00Z', '2026-07-25T00:01:00Z'
            )
            """,
            (_HOME_A,),
        )

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    for table in (
        "request_admissions",
        "request_admission_events",
        "branch_tasks_v2",
    ):
        assert any(f"preserved {table}" in blocker for blocker in scope.blockers)


def test_active_root_run_schedule_and_market_obligation_block(
    seeded: Path,
) -> None:
    from tinyassets.runs import initialize_runs_db
    from tinyassets.scoped_reset import inspect_reset_scope

    runs_path = initialize_runs_db(seeded)
    with sqlite3.connect(str(runs_path)) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, branch_def_id, thread_id, status, actor, started_at
            ) VALUES ('run-a', 'branch-a', 'thread-a', 'running', ?, 1.0)
            """,
            (_SUBJECT_A,),
        )
        conn.execute(
            """
            INSERT INTO branch_schedules (
                schedule_id, branch_def_id, owner_actor, active, paused,
                created_at
            ) VALUES ('schedule-a', 'branch-a', ?, 1, 0, 1.0)
            """,
            (_SUBJECT_A,),
        )
    with _connect(seeded) as conn:
        conn.execute(
            """
            CREATE TABLE escrow_locks (
                lock_id TEXT PRIMARY KEY,
                gate_claim_id TEXT NOT NULL,
                staker_id TEXT NOT NULL,
                status TEXT NOT NULL,
                recipient_id TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO escrow_locks (
                lock_id, gate_claim_id, staker_id, status
            ) VALUES ('lock-a', 'claim-a', ?, 'locked')
            """,
            (_SUBJECT_A,),
        )

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert any("active root run" in blocker for blocker in scope.blockers)
    assert any("active schedule" in blocker for blocker in scope.blockers)
    assert any("active market" in blocker for blocker in scope.blockers)


def test_the_run_recovery_lock_does_not_block_a_reset(seeded: Path) -> None:
    """The live server holds it at the data root for its whole life."""
    from tinyassets.scoped_reset import inspect_reset_scope

    (seeded / ".run_recovery.lock").write_bytes(b"")
    (seeded / ".run_recovery.lock.pid").write_text("7", encoding="utf-8")

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert not [b for b in scope.blockers if ".run_recovery" in b], scope.blockers


def test_unclassified_root_operational_store_blocks(seeded: Path) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    (seeded / "future_execution_evidence.db").write_bytes(b"not inspected")

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert any(
        "unclassified root operational store" in blocker
        for blocker in scope.blockers
    )


@pytest.mark.parametrize(
    ("location", "name", "directory"),
    [
        ("root", ".langgraph_runs.db", False),
        ("root", "lance", True),
        ("home", "checkpoints.db", False),
        ("home", "future_home_store.db", False),
        ("home", "lance", True),
    ],
)
def test_known_but_unadapted_operational_stores_block(
    seeded: Path,
    location: str,
    name: str,
    directory: bool,
) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    parent = seeded if location == "root" else seeded / _HOME_A
    path = parent / name
    if directory:
        path.mkdir()
    else:
        path.write_bytes(b"unadapted operational state")

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert any(name in blocker for blocker in scope.blockers)


def test_credentials_and_reparse_points_block_without_following(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import inspect_reset_scope

    credentials = seeded / _HOME_A / ".credential-vault.json"
    credentials.write_text('{"sentinel":"do-not-read"}', encoding="utf-8")
    link = seeded / _HOME_A / "outside-link"
    if sys.platform == "win32":
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "New-Item",
                "-ItemType",
                "Junction",
                "-Path",
                str(link),
                "-Target",
                str(seeded / _HOME_B),
            ],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(seeded / _HOME_B, target_is_directory=True)

    scope = inspect_reset_scope(seeded, principal=_SUBJECT_A)
    assert any("credential" in blocker for blocker in scope.blockers)
    assert any("link or reparse point" in blocker for blocker in scope.blockers)
    assert credentials.read_text(encoding="utf-8") == '{"sentinel":"do-not-read"}'


def test_hardlinked_main_database_blocks_before_inspection(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import ScopedResetBlocked, inspect_reset_scope

    database = seeded / ".tinyassets.db"
    outside = seeded.parent / "outside-main.db"
    database.replace(outside)
    os.link(outside, database)

    with pytest.raises(ScopedResetBlocked, match="multiple filesystem links"):
        inspect_reset_scope(seeded, principal=_SUBJECT_A)


def test_hardlinked_main_database_sidecar_blocks_before_inspection(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import ScopedResetBlocked, inspect_reset_scope

    outside = seeded.parent / "outside-wal"
    outside.write_bytes(b"outside sqlite state")
    os.link(outside, Path(f"{seeded / '.tinyassets.db'}-wal"))

    with pytest.raises(ScopedResetBlocked, match="sidecar"):
        inspect_reset_scope(seeded, principal=_SUBJECT_A)


def test_process_shared_barrier_excludes_reset_while_writer_is_active(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetLeaseBusy,
        acquire_maintenance_barrier,
    )

    writer = acquire_maintenance_barrier(seeded, exclusive=False, timeout=0.2)
    try:
        with pytest.raises(ScopedResetLeaseBusy):
            acquire_maintenance_barrier(seeded, exclusive=True, timeout=0.05)
    finally:
        writer.release()

    resetter = acquire_maintenance_barrier(seeded, exclusive=True, timeout=0.2)
    resetter.release()


def test_barrier_file_link_is_rejected_without_mutating_its_target(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import ScopedResetBlocked, acquire_maintenance_barrier

    victim = seeded.parent / "barrier-victim"
    victim.write_bytes(b"victim!")
    os.link(victim, seeded / ".scoped-reset.barrier")

    with pytest.raises(ScopedResetBlocked, match="barrier file"):
        acquire_maintenance_barrier(seeded, exclusive=False)

    assert victim.read_bytes() == b"victim!"


def test_service_writer_barrier_recovers_then_holds_shared_lease(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetLeaseBusy,
        acquire_maintenance_barrier,
        prepare_service_writer_barrier,
    )

    writer = prepare_service_writer_barrier(seeded)
    second_writer = prepare_service_writer_barrier(seeded)
    try:
        with pytest.raises(ScopedResetLeaseBusy):
            acquire_maintenance_barrier(seeded, exclusive=True, timeout=0.05)
    finally:
        second_writer.release()
        writer.release()


def test_two_writer_processes_join_one_clean_shared_barrier(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetLeaseBusy,
        acquire_maintenance_barrier,
        prepare_service_writer_barrier,
    )

    child_code = (
        "import sys,time;"
        "from pathlib import Path;"
        "from tinyassets.scoped_reset import prepare_service_writer_barrier;"
        "barrier=prepare_service_writer_barrier(Path(sys.argv[1]));"
        "print('ready',flush=True);"
        "time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(seeded)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        writer = prepare_service_writer_barrier(seeded, timeout=2.0)
        try:
            with pytest.raises(ScopedResetLeaseBusy):
                acquire_maintenance_barrier(
                    seeded,
                    exclusive=True,
                    timeout=0.05,
                )
        finally:
            writer.release()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_every_writer_process_entrypoint_installs_maintenance_barrier() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "tinyassets/__main__.py",
        # `tinyassets/cloud_worker.py` was a writer entrypoint until 2026-08-29,
        # when the host-run fleet was deleted: nothing runs outside a user's
        # universe (ADR-009). No module replaces it -- the served consumer runs
        # inside the daemon, which is covered by tinyassets/__main__.py.
        "tinyassets/mcp_server.py",
        "tinyassets/universe_server.py",
        "tinyassets/desktop/launcher.py",
        "fantasy_daemon/__main__.py",
        "fantasy_daemon/api.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "prepare_service_writer_barrier" in source, relative
    tray = (root / "tinyassets_tray.py").read_text(encoding="utf-8")
    assert "tinyassets.universe_server import main" in tray


def test_fault_contract_names_every_recovery_boundary() -> None:
    from tinyassets.scoped_reset import FAULT_POINTS

    assert FAULT_POINTS == (
        "after_journal",
        "after_prepare",
        "before_rename",
        "after_rename",
        "before_commit",
        "after_commit",
        "before_cleanup",
        "after_cleanup",
        "after_complete",
    )


def test_scoped_reset_is_not_registered_as_public_surface() -> None:
    package_root = Path(__file__).resolve().parents[1] / "tinyassets"
    public_sources = (
        package_root / "universe_server.py",
        package_root / "mcp_server.py",
        package_root / "api" / "universe.py",
    )
    for source in public_sources:
        text = source.read_text(encoding="utf-8")
        assert "plan_test_identity_reset" not in text
        assert "apply_test_identity_reset" not in text
        assert "recover_scoped_resets" not in text

    assert "TINYASSETS_TEST_IDENTITIES" not in os.environ


def test_packaged_runtime_matches_scoped_reset_writer_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    mirror = (
        root
        / "packaging"
        / "claude-plugin"
        / "plugins"
        / "tinyassets-universe-server"
        / "runtime"
    )
    for relative in (
        Path("tinyassets/scoped_reset.py"),
        Path("tinyassets/mcp_server.py"),
        Path("tinyassets/desktop/launcher.py"),
    ):
        assert (root / relative).read_bytes() == (mirror / relative).read_bytes()


def _roster(*, include_subject: bool = True):
    from tinyassets.scoped_reset import TestIdentityRoster

    aliases = {"alice": _SUBJECT_A}
    allowlisted = frozenset({_SUBJECT_A}) if include_subject else frozenset()
    return TestIdentityRoster(
        revision="roster-rev-1",
        aliases=aliases,
        allowlisted_subjects=allowlisted,
    )


def _state_snapshot(base: Path) -> tuple[bytes, tuple[tuple[object, ...], ...]]:
    db_bytes = (base / ".tinyassets.db").read_bytes()
    with _connect(base) as conn:
        rows = tuple(
            tuple(row)
            for row in conn.execute(
                "SELECT founder_sub, universe_id, created_at, platform_generated "
                "FROM founder_home ORDER BY founder_sub"
            )
        )
    return db_bytes, rows


def test_plan_is_read_only_stable_and_never_emits_raw_subject(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import plan_test_identity_reset

    before = _state_snapshot(seeded)
    first = plan_test_identity_reset(seeded, alias="alice", roster=_roster())
    second = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert first == second
    assert first["plan_id"].startswith("sha256:")
    assert first["inventory_revision"]
    assert first["roster_revision"] == "roster-rev-1"
    assert first["identity_alias"] == "alice"
    assert first["home_id"] == _HOME_A
    assert first["noop"] is False
    assert first["blockers"] == []
    assert any(
        action["table"] == "founder_home"
        for action in first["database_actions"]
    )
    assert _SUBJECT_A not in json.dumps(first, sort_keys=True)
    assert _state_snapshot(seeded) == before


def test_unknown_alias_and_nonallowlisted_subject_fail_closed(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import plan_test_identity_reset

    with pytest.raises(PermissionError, match="unknown test identity alias"):
        plan_test_identity_reset(seeded, alias="missing", roster=_roster())
    with pytest.raises(PermissionError, match="not allowlisted"):
        plan_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(include_subject=False),
        )


def test_allowlisted_subject_without_state_has_stable_noop_plan(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import TestIdentityRoster, plan_test_identity_reset

    roster = TestIdentityRoster(
        revision="roster-rev-1",
        aliases={"empty": "workos|empty"},
        allowlisted_subjects=frozenset({"workos|empty"}),
    )
    (seeded / ".langgraph_runs.db").write_bytes(b"unrelated operator state")
    plan = plan_test_identity_reset(seeded, alias="empty", roster=roster)

    assert plan["noop"] is True
    assert plan["blockers"] == []
    assert plan["home_id"] is None
    assert plan["database_actions"] == []
    assert plan["filesystem_actions"] == []


def test_no_database_noop_apply_is_repeatable_without_creating_control_state(
    tmp_path: Path,
) -> None:
    from tinyassets.scoped_reset import (
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    empty_root = tmp_path / "empty-data"
    empty_root.mkdir()
    plan = plan_test_identity_reset(
        empty_root,
        alias="alice",
        roster=_roster(),
    )
    assert plan["noop"] is True

    first = apply_test_identity_reset(
        empty_root,
        alias="alice",
        roster=_roster(),
        plan_id=plan["plan_id"],
    )
    replacement = empty_root / _HOME_A
    replacement.mkdir()
    (replacement / "sentinel").write_text("new", encoding="utf-8")
    replay = apply_test_identity_reset(
        empty_root,
        alias="alice",
        roster=_roster(),
        plan_id=plan["plan_id"],
    )

    assert first == replay
    assert first["status"] == "noop"
    assert not (empty_root / ".tinyassets.db").exists()
    assert (replacement / "sentinel").read_text(encoding="utf-8") == "new"


def test_plan_digest_changes_when_exact_binding_version_changes(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import plan_test_identity_reset

    before = plan_test_identity_reset(seeded, alias="alice", roster=_roster())
    with _connect(seeded) as conn:
        conn.execute(
            "UPDATE founder_home SET created_at = created_at + 1 "
            "WHERE founder_sub = ?",
            (_SUBJECT_A,),
        )
    after = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert before["plan_id"] != after["plan_id"]
    assert before["state_digest"] != after["state_digest"]


def test_operator_cli_loads_private_roster_and_emits_redacted_plan(
    seeded: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tinyassets.scoped_reset as scoped_reset

    roster_path = tmp_path / "test-identities.json"
    roster_path.write_text(
        json.dumps({
            "revision": "roster-rev-1",
            "aliases": {"alice": _SUBJECT_A},
            "allowlisted_subjects": [_SUBJECT_A],
        }),
        encoding="utf-8",
    )
    if sys.platform == "win32":
        monkeypatch.setattr(
            scoped_reset,
            "_windows_roster_acl_is_private",
            lambda _path: True,
        )
    else:
        roster_path.chmod(0o600)

    assert scoped_reset.main([
        "plan",
        "--data-dir",
        str(seeded),
        "--roster",
        str(roster_path),
        "--identity",
        "alice",
    ]) == 0
    output = capsys.readouterr().out
    plan = json.loads(output)
    assert plan["identity_alias"] == "alice"
    assert plan["home_id"] == _HOME_A
    assert _SUBJECT_A not in output


def test_roster_rejects_credentials_and_unexpected_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tinyassets.scoped_reset as scoped_reset

    roster_path = tmp_path / "bad-roster.json"
    roster_path.write_text(
        json.dumps({
            "revision": "roster-rev-1",
            "aliases": {"alice": _SUBJECT_A},
            "allowlisted_subjects": [_SUBJECT_A],
            "cookies": "must-not-be-accepted",
        }),
        encoding="utf-8",
    )
    if sys.platform == "win32":
        monkeypatch.setattr(
            scoped_reset,
            "_windows_roster_acl_is_private",
            lambda _path: True,
        )
    else:
        roster_path.chmod(0o600)

    with pytest.raises(ValueError, match="unexpected roster fields"):
        scoped_reset.load_test_identity_roster(roster_path)


def test_roster_refuses_nonprivate_permissions(tmp_path: Path, monkeypatch) -> None:
    import tinyassets.scoped_reset as scoped_reset

    roster_path = tmp_path / "unprotected-roster.json"
    roster_path.write_text("{}", encoding="utf-8")
    if sys.platform == "win32":
        monkeypatch.setattr(scoped_reset, "_windows_roster_acl_is_private", lambda _path: False)
    else:
        roster_path.chmod(0o644)
    with pytest.raises(PermissionError, match="another principal|group/world"):
        scoped_reset.load_test_identity_roster(roster_path)


def test_windows_roster_acl_failure_is_loud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tinyassets.scoped_reset as scoped_reset

    roster_path = tmp_path / "private.json"
    roster_path.write_text(
        json.dumps({
            "revision": "roster-rev-1",
            "aliases": {"alice": _SUBJECT_A},
            "allowlisted_subjects": [_SUBJECT_A],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(scoped_reset.sys, "platform", "win32")
    monkeypatch.setattr(
        scoped_reset,
        "_windows_roster_acl_is_private",
        lambda _path: False,
        raising=False,
    )

    with pytest.raises(PermissionError, match="Windows ACL"):
        scoped_reset.load_test_identity_roster(roster_path)


def test_directory_fsync_uses_windows_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tinyassets.scoped_reset as scoped_reset

    seen: list[Path] = []
    monkeypatch.setattr(scoped_reset.sys, "platform", "win32")
    monkeypatch.setattr(
        scoped_reset,
        "_flush_windows_directory",
        lambda path: seen.append(path),
        raising=False,
    )

    scoped_reset._fsync_directory(tmp_path)

    assert seen == [tmp_path]


def test_completed_plan_replay_is_read_only_and_returns_receipt(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import read_completed_plan_receipt

    receipt = {
        "plan_id": "sha256:completed",
        "status": "completed",
        "home_id": _HOME_A,
        "fence": 7,
    }
    with _connect(seeded) as conn:
        conn.execute(
            """
            CREATE TABLE scoped_reset_operations (
                plan_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                commit_witness INTEGER NOT NULL,
                receipt_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO scoped_reset_operations (
                plan_id, state, commit_witness, receipt_json
            ) VALUES (?, 'completed', 1, ?)
            """,
            ("sha256:completed", json.dumps(receipt)),
        )
    replacement = seeded / _HOME_A / "new-home-sentinel"
    replacement.write_text("new state\n", encoding="utf-8")
    before = _state_snapshot(seeded)

    assert read_completed_plan_receipt(
        seeded,
        plan_id="sha256:completed",
    ) == receipt
    assert read_completed_plan_receipt(
        seeded,
        plan_id="sha256:unknown",
    ) is None
    assert replacement.read_text(encoding="utf-8") == "new state\n"
    assert _state_snapshot(seeded) == before


def _table_rows(base: Path, table: str) -> tuple[tuple[object, ...], ...]:
    with _connect(base) as conn:
        return tuple(
            tuple(row)
            for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY 1')
        )


def _preserved_snapshot(base: Path) -> dict[str, object]:
    return {
        "home_b": (base / _HOME_B / "soul.md").read_bytes(),
        "home_b_binding": tuple(
            row
            for row in _table_rows(base, "founder_home")
            if row[0] == _SUBJECT_B
        ),
        "home_b_grants": tuple(
            row
            for row in _table_rows(base, "universe_acl")
            if row[1] == _SUBJECT_B
        ),
        "commons": {
            table: _table_rows(base, table)
            for table in (
                "action_records",
                "author_definitions",
                "branch_definitions",
                "canonical_bindings",
                "goals",
            )
        },
        "runs": (base / ".runs.db").read_bytes(),
        "wiki": (base / "wiki" / "commons.md").read_bytes(),
    }


def _seed_preserved_state(base: Path) -> None:
    from tinyassets.runs import initialize_runs_db

    runs_path = initialize_runs_db(base)
    with sqlite3.connect(str(runs_path)) as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, branch_def_id, thread_id, status, actor, started_at,
                finished_at
            ) VALUES (
                'historical-run', 'branch-b', 'thread-b', 'completed', ?, 1.0,
                2.0
            )
            """,
            (_SUBJECT_B,),
        )
    # Freeze the fixture's committed WAL before the byte-preservation snapshot;
    # sqlite context-manager exit commits but does not close the connection.
    conn.close()
    with sqlite3.connect(str(runs_path)) as checkpoint:
        assert checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    checkpoint.close()
    (base / "wiki").mkdir()
    (base / "wiki" / "commons.md").write_bytes(b"commons sentinel")
    with _connect(base) as conn:
        conn.execute(
            """
            INSERT INTO universe_acl (
                universe_id, actor_id, permission, granted_at, granted_by
            ) VALUES (?, ?, 'read', 2.0, ?)
            """,
            (_HOME_B, _SUBJECT_A, _SUBJECT_B),
        )


def test_apply_resets_exact_founder_home_and_subject_grants_only(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    _seed_preserved_state(seeded)
    before = _preserved_snapshot(seeded)
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    receipt = apply_test_identity_reset(
        seeded,
        alias="alice",
        roster=_roster(),
        plan_id=plan["plan_id"],
    )

    assert receipt["status"] == "completed"
    assert receipt["home_id"] == _HOME_A
    assert not (seeded / _HOME_A).exists()
    assert all(row[0] != _SUBJECT_A for row in _table_rows(seeded, "founder_home"))
    assert all(row[1] != _SUBJECT_A for row in _table_rows(seeded, "universe_acl"))
    assert all(row[0] != _HOME_A for row in _table_rows(seeded, "universes"))
    assert _preserved_snapshot(seeded) == before


def test_completed_apply_replay_preserves_new_home(
    seeded: Path,
) -> None:
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
        set_founder_home,
    )
    from tinyassets.scoped_reset import (
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())
    first = apply_test_identity_reset(
        seeded,
        alias="alice",
        roster=_roster(),
        plan_id=plan["plan_id"],
    )
    replacement_id = "u-01cccccccccccccccccccccccc"
    replacement = seeded / replacement_id
    replacement.mkdir()
    (replacement / "new.md").write_text("replacement\n", encoding="utf-8")
    ensure_universe_registered(
        seeded,
        universe_id=replacement_id,
        universe_path=replacement,
    )
    grant_universe_access(
        seeded,
        universe_id=replacement_id,
        actor_id=_SUBJECT_A,
        permission="admin",
        granted_by=_SUBJECT_A,
    )
    set_founder_home(
        seeded,
        founder_sub=_SUBJECT_A,
        universe_id=replacement_id,
        platform_generated=True,
    )
    with _connect(seeded) as conn:
        conn.execute(
            """
            INSERT INTO user_requests (
                request_id, universe_id, user_id, request_type, text,
                status, created_at, updated_at
            ) VALUES (
                'replacement-active', ?, ?, 'test', 'new state', 'open',
                1.0, 1.0
            )
            """,
            (replacement_id, _SUBJECT_A),
        )

    replay = apply_test_identity_reset(
        seeded,
        alias="alice",
        roster=_roster(),
        plan_id=plan["plan_id"],
    )

    assert replay == first
    assert (replacement / "new.md").read_text(encoding="utf-8") == "replacement\n"


def test_replay_recovers_postcommit_fault_then_returns_receipt(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    class InjectedFault(RuntimeError):
        pass

    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    def inject(point: str) -> None:
        if point == "after_commit":
            raise InjectedFault(point)

    with pytest.raises(InjectedFault, match="after_commit"):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
            fault_injector=inject,
        )

    receipt = apply_test_identity_reset(
        seeded,
        alias="alice",
        roster=_roster(),
        plan_id=plan["plan_id"],
    )
    assert receipt["plan_id"] == plan["plan_id"]
    assert receipt["status"] == "completed"
    assert not (seeded / _HOME_A).exists()


def test_apply_rejects_plan_change_and_active_blocker_without_mutation(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetBlocked,
        ScopedResetPlanChanged,
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())
    before = _state_snapshot(seeded)
    with _connect(seeded) as conn:
        conn.execute(
            """
            INSERT INTO user_requests (
                request_id, universe_id, user_id, request_type, text,
                status, created_at, updated_at
            ) VALUES ('request-block', ?, ?, 'test', 'test', 'open', 1.0, 1.0)
            """,
            (_HOME_A, _SUBJECT_A),
        )

    with pytest.raises(ScopedResetBlocked, match="active request"):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )
    with _connect(seeded) as conn:
        conn.execute("DELETE FROM user_requests WHERE request_id = 'request-block'")
        conn.execute(
            "UPDATE founder_home SET created_at = created_at + 1 "
            "WHERE founder_sub = ?",
            (_SUBJECT_A,),
        )
    with pytest.raises(ScopedResetPlanChanged):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )
    assert (seeded / _HOME_A / "soul.md").is_file()
    assert before[1][0][1] == _HOME_A


@pytest.mark.parametrize(
    ("fault_point", "committed"),
    [
        ("before_rename", False),
        ("after_rename", False),
        ("before_commit", False),
        ("after_commit", True),
        ("before_cleanup", True),
        ("after_cleanup", True),
    ],
)
def test_fault_recovery_converges_at_every_boundary(
    seeded: Path,
    fault_point: str,
    committed: bool,
) -> None:
    from tinyassets.scoped_reset import (
        apply_test_identity_reset,
        plan_test_identity_reset,
        recover_scoped_resets,
    )

    class InjectedFault(RuntimeError):
        pass

    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    def inject(point: str) -> None:
        if point == fault_point:
            raise InjectedFault(point)

    with pytest.raises(InjectedFault, match=fault_point):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
            fault_injector=inject,
        )
    recover_scoped_resets(seeded)

    if committed:
        assert not (seeded / _HOME_A).exists()
        assert all(
            row[0] != _SUBJECT_A
            for row in _table_rows(seeded, "founder_home")
        )
    else:
        assert (seeded / _HOME_A / "soul.md").is_file()
        assert any(
            row[0] == _SUBJECT_A
            for row in _table_rows(seeded, "founder_home")
        )


def test_apply_refuses_credential_and_home_local_audit_stores(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import ScopedResetBlocked, plan_test_identity_reset

    (seeded / _HOME_A / ".runs.db").write_bytes(b"home audit")
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert any("home-local audit" in blocker for blocker in plan["blockers"])
    with pytest.raises(ScopedResetBlocked):
        from tinyassets.scoped_reset import apply_test_identity_reset

        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )


def test_apply_refuses_home_directory_identity_swap(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetBlocked,
        ScopedResetPlanChanged,
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    bob_file = seeded / _HOME_B / "bobs_novel.md"
    bob_file.write_text("belongs to bob\n", encoding="utf-8")
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    parked_alice = seeded / "parked-alice-home"
    (seeded / _HOME_A).replace(parked_alice)
    (seeded / _HOME_B).replace(seeded / _HOME_A)

    with pytest.raises((ScopedResetBlocked, ScopedResetPlanChanged)):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )

    current = plan_test_identity_reset(
        seeded,
        alias="alice",
        roster=_roster(),
    )
    assert current["plan_id"] != plan["plan_id"]
    assert (seeded / _HOME_A / "bobs_novel.md").read_text(
        encoding="utf-8"
    ) == "belongs to bob\n"
    assert (parked_alice / "soul.md").is_file()


def test_recovery_refuses_preexisting_directory_at_staging_path(
    seeded: Path,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetRecoveryError,
        apply_test_identity_reset,
        plan_test_identity_reset,
        recover_scoped_resets,
    )

    bob_file = seeded / _HOME_B / "bobs_novel.md"
    bob_file.write_text("belongs to bob\n", encoding="utf-8")
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    def fail_after_commit(point: str) -> None:
        if point == "after_commit":
            raise RuntimeError("injected:after_commit")

    with pytest.raises(RuntimeError, match="injected:after_commit"):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
            fault_injector=fail_after_commit,
        )

    operation_id = str(plan["plan_id"]).removeprefix("sha256:")
    staging = seeded / ".scoped-reset-staging" / operation_id / "home"
    parked_alice = seeded.parent / "parked-alice-staging"
    staging.replace(parked_alice)
    (seeded / _HOME_B).replace(staging)

    with pytest.raises(ScopedResetRecoveryError, match="filesystem identity"):
        recover_scoped_resets(seeded)

    assert (staging / "bobs_novel.md").read_text(
        encoding="utf-8"
    ) == "belongs to bob\n"
    assert (parked_alice / "soul.md").is_file()


@pytest.mark.parametrize(
    ("store_name", "blocker_text", "is_directory"),
    [
        ("AUTH.JSON", "home contains credential artifact", False),
        ("Auth.Json", "home contains credential artifact", False),
        (".CREDENTIAL-VAULT.JSON", "home contains credential artifact", False),
        ("BID_EXECUTION_LOG.JSON", "home-local audit or receipt store", False),
        (
            "CHECKPOINTS.DB",
            "home operational store has no scoped-reset adapter",
            False,
        ),
        (
            "CHECKPOINTS",
            "home operational directory has no scoped-reset adapter",
            True,
        ),
    ],
)
def test_casefolded_protected_store_aborts_without_deletion(
    seeded: Path,
    store_name: str,
    blocker_text: str,
    is_directory: bool,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetBlocked,
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    if sys.platform == "win32":
        case_probe = seeded.parent / "CaseFoldProbe"
        case_probe.mkdir()
        assert (seeded.parent / "casefoldprobe").exists()

    store = seeded / _HOME_A / store_name
    if is_directory:
        store.mkdir()
        protected = store / "protected.bin"
    else:
        protected = store
    protected.write_bytes(b"protected operational state")
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert any(blocker.startswith(blocker_text) for blocker in plan["blockers"])
    with pytest.raises(ScopedResetBlocked):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )
    assert protected.read_bytes() == b"protected operational state"
    assert (seeded / _HOME_A / "soul.md").is_file()


@pytest.mark.parametrize(
    "store_name",
    [
        "future_queue.sqlite3",
        "future_events.jsonl",
        "future_vectors.parquet",
        "future_queue",
    ],
)
def test_unclassified_home_store_aborts_without_deletion(
    seeded: Path,
    store_name: str,
) -> None:
    from tinyassets.scoped_reset import (
        ScopedResetBlocked,
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    store = seeded / _HOME_A / store_name
    store.write_bytes(b"future operational state")
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert any(
        "unclassified home operational store" in blocker
        for blocker in plan["blockers"]
    )
    with pytest.raises(ScopedResetBlocked):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )
    assert store.read_bytes() == b"future operational state"
    assert (seeded / _HOME_A / "soul.md").is_file()


def test_branch_version_tables_are_all_classified(seeded: Path) -> None:
    """Initializing branch versions -- including its one-time migration marker
    table -- must not leave a table reset refuses as unclassified."""
    from tinyassets.branch_versions import initialize_branch_versions_db
    from tinyassets.scoped_reset import plan_test_identity_reset

    initialize_branch_versions_db(seeded)
    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())
    assert not any(
        "unclassified root run-history table" in blocker
        for blocker in plan["blockers"]
    ), plan["blockers"]


def test_unclassified_root_store_and_runs_table_abort_reset(
    seeded: Path,
) -> None:
    from tinyassets.runs import initialize_runs_db
    from tinyassets.scoped_reset import (
        ScopedResetBlocked,
        apply_test_identity_reset,
        plan_test_identity_reset,
    )

    root_store = seeded / "future_queue.sqlite3"
    root_store.write_bytes(b"future root state")
    runs_path = initialize_runs_db(seeded)
    with sqlite3.connect(str(runs_path)) as conn:
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute(
            "CREATE TABLE pending_jobs ("
            "job_id TEXT PRIMARY KEY, owner TEXT, status TEXT)"
        )
        conn.execute(
            "INSERT INTO pending_jobs VALUES ('job-a', ?, 'running')",
            (_SUBJECT_A,),
        )

    plan = plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert any(
        "unclassified root operational store" in blocker
        for blocker in plan["blockers"]
    )
    assert any(
        "unclassified root run-history table" in blocker
        for blocker in plan["blockers"]
    )
    with pytest.raises(ScopedResetBlocked):
        apply_test_identity_reset(
            seeded,
            alias="alice",
            roster=_roster(),
            plan_id=plan["plan_id"],
        )
    assert root_store.read_bytes() == b"future root state"
    assert (seeded / _HOME_A / "soul.md").is_file()


def _recursive_file_snapshot(base: Path) -> dict[str, tuple[int, int, str]]:
    import hashlib

    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        file_stat = path.stat()
        snapshot[path.relative_to(base).as_posix()] = (
            file_stat.st_size,
            file_stat.st_mtime_ns,
            hashlib.sha256(payload).hexdigest(),
        )
    return snapshot


def test_plan_does_not_create_sqlite_sidecars(seeded: Path) -> None:
    from tinyassets.scoped_reset import plan_test_identity_reset

    before = _recursive_file_snapshot(seeded)
    plan_test_identity_reset(seeded, alias="alice", roster=_roster())

    assert _recursive_file_snapshot(seeded) == before


def test_legacy_api_reconfigure_keeps_previous_writer_fence_on_failure(
    tmp_path: Path,
) -> None:
    import fantasy_daemon.api as legacy_api
    from tinyassets.scoped_reset import (
        ScopedResetLeaseBusy,
        acquire_maintenance_barrier,
    )

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    blocker = None
    probe = None
    try:
        legacy_api.configure(str(root_a))
        blocker = acquire_maintenance_barrier(
            root_b,
            exclusive=True,
            timeout=0.2,
        )

        with pytest.raises(ScopedResetLeaseBusy):
            legacy_api.configure(str(root_b))

        assert legacy_api._base_path == str(root_a)
        try:
            probe = acquire_maintenance_barrier(
                root_a,
                exclusive=True,
                timeout=0.05,
            )
        except ScopedResetLeaseBusy:
            pass
        else:
            pytest.fail("legacy API still serves root A without its writer fence")
    finally:
        if probe is not None:
            probe.release()
        if blocker is not None:
            blocker.release()
        if legacy_api._writer_barrier is not None:
            legacy_api._writer_barrier.release()
            legacy_api._writer_barrier = None
        legacy_api._base_path = ""


def test_legacy_api_swaps_root_and_fence_before_releasing_previous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fantasy_daemon.api as legacy_api
    import tinyassets.scoped_reset as scoped_reset

    root_b = tmp_path / "b"
    root_b.mkdir()
    replacement_released = False

    class ReplacementBarrier:
        def release(self) -> None:
            nonlocal replacement_released
            replacement_released = True

    replacement = ReplacementBarrier()

    class PreviousBarrier:
        released = False

        def release(self) -> None:
            assert legacy_api._base_path == str(root_b)
            assert legacy_api._writer_barrier is replacement
            self.released = True

    previous = PreviousBarrier()
    monkeypatch.setattr(
        scoped_reset,
        "prepare_service_writer_barrier",
        lambda _path: replacement,
    )
    legacy_api._base_path = str(tmp_path / "a")
    legacy_api._writer_barrier = previous
    try:
        legacy_api.configure(str(root_b))
        assert previous.released
    finally:
        if legacy_api._writer_barrier is replacement:
            replacement.release()
        legacy_api._writer_barrier = None
        legacy_api._base_path = ""
    assert replacement_released
