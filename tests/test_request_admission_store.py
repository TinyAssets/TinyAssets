from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from tinyassets.daemon_server import initialize_author_server
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.storage import db_path
from tinyassets.storage.automation_activations import (
    AutomationActivationExecutor,
    AutomationActivationStore,
)
from tinyassets.storage.request_admissions import (
    COMMIT_STEPS,
    IdempotencyKeyBodyConflict,
    RequestAdmissionStore,
    migrate_request_admission_schema,
)

_USER_KEY_HASH_A = "hmac-sha256:" + "a" * 64
_USER_KEY_HASH_B = "hmac-sha256:" + "b" * 64
_SERVER_KEY_HASH_B = "sha256:" + "b" * 64


def _activation_subject(
    ref: str,
    digest: str = f"sha256:{'a' * 64}",
) -> ExecutionSubject:
    return ExecutionSubject(
        kind=ExecutionSubjectKind.BRANCH_VERSION,
        ref=ref,
        digest=digest,
    )


def _commit_kwargs(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "actor_id": "alice",
        "universe_id": "universe-a",
        "idempotency_key_hash": _USER_KEY_HASH_A,
        "body_digest": "sha256:body-a",
        "body_digest_version": "rfc8785-v1",
        "request_type": "general",
        "text": "repair the queue",
        "branch_id": "",
        "branch_def_id": "loop-branch",
        "trigger_source": "operator_request",
        "accepted_priority_weight": 50.0,
        "policy_version": "operator-priority-v1",
        "grant_generation": 3,
        "receipt": {"authority": "exact-universe-grant"},
        "directed_daemon_id": "",
        "created_at": "2026-07-24T08:00:00Z",
    }
    values.update(overrides)
    if (
        values.get("automation_activation") is not None
        and "idempotency_key_hash" not in overrides
    ):
        # A server-derived automation identity is unkeyed on purpose; the
        # cloud worker that mints it is denied the admission HMAC secret.
        values["idempotency_key_hash"] = "sha256:" + "a" * 64
    return values


def _connect(base: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(base), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_count(base: Path, table: str) -> int:
    with _connect(base) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_author_server_pretraffic_init_migrates_epoch2_schema(tmp_path):
    initialize_author_server(tmp_path)
    with _connect(tmp_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "request_admissions",
            "request_admission_events",
            "branch_tasks_v2",
            "branch_tasks_v2_quarantine",
            "request_admission_rollouts",
        } <= tables
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_branch_subject_check_migrates_copy_of_populated_current_schema(
    tmp_path, monkeypatch,
):
    """A production-shaped DB copy keeps queued work and its child rows."""
    from tinyassets.storage import request_admissions

    legacy_root = tmp_path / "legacy"
    copy_root = tmp_path / "copy"
    old_check = "automation_subject_kind IN ('branch_version', 'agent_runtime_manifest')"
    new_check = "automation_subject_kind = 'branch_version'"
    assert new_check in request_admissions._SCHEMA
    with monkeypatch.context() as patch:
        patch.setattr(request_admissions, "_SCHEMA", request_admissions._SCHEMA.replace(
            new_check, old_check,
        ))
        patch.setattr(request_admissions, "_migrate_branch_task_subject_constraint",
                      lambda conn: None)
        initialize_author_server(legacy_root)
    committed = RequestAdmissionStore(legacy_root).commit_admission(**_commit_kwargs())

    db_path(copy_root).parent.mkdir(parents=True, exist_ok=True)
    with _connect(legacy_root) as source, sqlite3.connect(db_path(copy_root)) as target:
        old_rowid = source.execute(
            "SELECT rowid FROM branch_tasks_v2 WHERE branch_task_id = ?",
            (committed["branch_task_id"],),
        ).fetchone()[0]
        source.backup(target)
    with sqlite3.connect(db_path(copy_root)) as conn:
        conn.executescript(
            "CREATE TABLE unrelated_parent (id TEXT PRIMARY KEY);"
            "CREATE TABLE unrelated_child (parent_id TEXT REFERENCES unrelated_parent(id));"
            "INSERT INTO unrelated_child (parent_id) VALUES ('preexisting-orphan');"
        )

    with _connect(copy_root) as conn:
        migrate_request_admission_schema(conn)
        migrate_request_admission_schema(conn)
        assert conn.execute("PRAGMA foreign_key_check(unrelated_child)").fetchall()
        for table in ("branch_tasks_v2", "request_admissions",
                      "request_admission_events", "branch_tasks_v2_quarantine"):
            assert conn.execute(f"PRAGMA foreign_key_check({table})").fetchall() == []
        task = conn.execute(
            "SELECT branch_task_id, rowid FROM branch_tasks_v2"
        ).fetchone()
        assert task["branch_task_id"] == committed["branch_task_id"]
        assert task["rowid"] == old_rowid
        assert conn.execute(
            "SELECT branch_task_id FROM request_admission_events"
        ).fetchone()[0] == committed["branch_task_id"]
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='branch_tasks_v2'"
        ).fetchone()[0]
        assert "agent_runtime_manifest" not in table_sql
        assert "IN ('branch_version')" in table_sql
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE branch_tasks_v2 SET automation_subject_kind = ?",
                ("agent_runtime_manifest",),
            )


def test_migrates_prechange_database_without_losing_requests(tmp_path):
    path = db_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE user_requests (
                request_id TEXT PRIMARY KEY,
                universe_id TEXT NOT NULL,
                branch_id TEXT,
                user_id TEXT NOT NULL,
                request_type TEXT NOT NULL,
                text TEXT NOT NULL,
                preferred_author_id TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO user_requests (
                request_id, universe_id, branch_id, user_id, request_type,
                text, preferred_author_id, status, created_at, updated_at,
                metadata_json
            ) VALUES (
                'legacy-request', 'legacy-universe', NULL, 'alice', 'general',
                'keep me', NULL, 'open', 1.0, 1.0, '{}'
            );
            """
        )

    initialize_author_server(tmp_path)

    with _connect(tmp_path) as conn:
        assert conn.execute(
            "SELECT text FROM user_requests WHERE request_id='legacy-request'"
        ).fetchone()[0] == "keep me"
        assert conn.execute(
            "SELECT COUNT(*) FROM request_admissions"
        ).fetchone()[0] == 0


def test_migrates_populated_pre_activation_task_without_data_loss(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    committed = RequestAdmissionStore(tmp_path).commit_admission(
        **_commit_kwargs()
    )
    activation_columns = {
        "automation_id",
        "automation_activation_epoch",
        "automation_executor_class",
        "automation_branch_version",
        "automation_lease_id",
    }

    with _connect(tmp_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        old_columns = [
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(branch_tasks_v2)")
            if str(row["name"]) not in activation_columns
        ]
        projection = ", ".join(f'"{name}"' for name in old_columns)
        conn.execute(
            "CREATE TABLE branch_tasks_v2_pre_activation AS "
            f"SELECT {projection} FROM branch_tasks_v2"
        )
        conn.execute("DROP TABLE branch_tasks_v2")
        conn.execute(
            "ALTER TABLE branch_tasks_v2_pre_activation "
            "RENAME TO branch_tasks_v2"
        )

        migrate_request_admission_schema(conn)

        migrated_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(branch_tasks_v2)")
        }
        row = conn.execute(
            """
            SELECT branch_task_id, automation_id,
                   automation_activation_epoch,
                   automation_executor_class,
                   automation_branch_version, automation_lease_id
            FROM branch_tasks_v2
            """
        ).fetchone()
    assert activation_columns <= migrated_columns
    assert row is not None
    assert row["branch_task_id"] == committed["branch_task_id"]
    assert all(row[column] is None for column in activation_columns)


def test_commit_links_request_admission_task_and_event(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)

    result = store.commit_admission(**_commit_kwargs())

    assert result == {
        "universe_id": "universe-a",
        "admission_id": result["admission_id"],
        "admission_state": "committed",
        "request_id": result["request_id"],
        "branch_task_id": result["branch_task_id"],
        "request_status": "pending",
        "trigger_source": "operator_request",
        "accepted_priority_weight": 50.0,
        "priority_weight_cap": 100,
        "priority_policy_version": "operator-priority-v1",
        "idempotent_replay": False,
        "directed_daemon_id": "",
    }
    assert result["request_id"]
    assert result["admission_id"]
    assert result["branch_task_id"]

    with _connect(tmp_path) as conn:
        admission = conn.execute(
            "SELECT * FROM request_admissions"
        ).fetchone()
        task = conn.execute("SELECT * FROM branch_tasks_v2").fetchone()
        event = conn.execute(
            "SELECT * FROM request_admission_events"
        ).fetchone()
        assert admission["request_id"] == result["request_id"]
        assert admission["branch_task_id"] == result["branch_task_id"]
        assert task["admission_id"] == result["admission_id"]
        assert task["request_id"] == result["request_id"]
        assert task["queue_epoch"] == 2
        assert event["admission_id"] == result["admission_id"]
        assert event["request_id"] == result["request_id"]
        assert event["branch_task_id"] == result["branch_task_id"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_commit_persists_only_the_exact_current_automation_activation(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    activations = AutomationActivationStore(tmp_path)
    stopped = activations.create_stopped(
        universe_id="universe-a",
        automation_id="automation-a",
    )
    active = activations.activate(
        expected=stopped,
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-a"),
        lease_id="activation-lease-a",
    )
    assert active is not None
    store = RequestAdmissionStore(tmp_path)

    committed = store.commit_admission(
        **_commit_kwargs(automation_activation=active),
    )

    with _connect(tmp_path) as conn:
        row = conn.execute(
            """
            SELECT automation_id, automation_activation_epoch,
                   automation_executor_class, automation_subject_kind,
                   automation_subject_ref, automation_subject_digest,
                   automation_branch_version,
                   automation_lease_id
            FROM branch_tasks_v2
            WHERE branch_task_id = ?
            """,
            (committed["branch_task_id"],),
        ).fetchone()
    assert row is not None
    assert dict(row) == {
        "automation_id": "automation-a",
        "automation_activation_epoch": 1,
        "automation_executor_class": "cloud",
        "automation_subject_kind": "branch_version",
        "automation_subject_ref": "branch-version-a",
        "automation_subject_digest": f"sha256:{'a' * 64}",
        "automation_branch_version": "branch-version-a",
        "automation_lease_id": "activation-lease-a",
    }

    rebound = activations.rebind(
        expected=active,
        subject=_activation_subject(
            "branch-version-b",
            f"sha256:{'b' * 64}",
        ),
        lease_id="activation-lease-b",
    )
    assert rebound is not None
    with pytest.raises(
        PermissionError,
        match="automation_activation_not_current",
    ):
        store.commit_admission(
            **_commit_kwargs(
                idempotency_key_hash=_SERVER_KEY_HASH_B,
                body_digest="sha256:body-b",
                automation_activation=active,
            ),
        )
    assert _table_count(tmp_path, "branch_tasks_v2") == 1


def test_commit_preserves_incentive_and_directed_instruction_privately(
    tmp_path,
):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)

    result = store.commit_admission(
        **_commit_kwargs(directed_daemon_id="daemon-a"),
        pickup_incentive="10 credits after verified delivery",
        directed_daemon_instruction="Investigate only; do not merge.",
    )

    assert result["directed_daemon_id"] == "daemon-a"
    with _connect(tmp_path) as conn:
        request_metadata = json.loads(
            conn.execute(
                "SELECT metadata_json FROM user_requests"
            ).fetchone()[0]
        )
        task_inputs = json.loads(
            conn.execute(
                "SELECT inputs_json FROM branch_tasks_v2"
            ).fetchone()[0]
        )
    assert request_metadata["pickup_incentive"] == (
        "10 credits after verified delivery"
    )
    assert request_metadata["directed_daemon_instruction"] == (
        "Investigate only; do not merge."
    )
    assert task_inputs["pickup_incentive"] == (
        "10 credits after verified delivery"
    )
    assert task_inputs["directed_daemon_instruction"] == (
        "Investigate only; do not merge."
    )


def test_schema_rejects_out_of_range_weight_and_duplicate_links(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    committed = store.commit_admission(**_commit_kwargs())

    with _connect(tmp_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE request_admissions
                SET accepted_priority_weight = 100.000001
                WHERE admission_id = ?
                """,
                (committed["admission_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO request_admissions (
                    admission_id, request_id, branch_task_id, tenant_id,
                    actor_id, universe_id, idempotency_key_hash, body_digest,
                    body_digest_version, trigger_source,
                    accepted_priority_weight, priority_policy_version,
                    grant_generation, receipt_json, result_json, state,
                    created_at, updated_at
                )
                SELECT
                    'duplicate-admission', request_id, branch_task_id,
                    tenant_id, actor_id, universe_id, idempotency_key_hash,
                    body_digest, body_digest_version, trigger_source,
                    accepted_priority_weight, priority_policy_version,
                    grant_generation, receipt_json, result_json, state,
                    created_at, updated_at
                FROM request_admissions
                WHERE admission_id = ?
                """,
                (committed["admission_id"],),
            )


@pytest.mark.parametrize(
    "weight",
    [-1.0, 100.000001, float("inf"), float("-inf"), float("nan")],
)
def test_commit_rejects_nonfinite_or_out_of_range_weight(tmp_path, weight):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)

    with pytest.raises(
        ValueError,
        match=r"accepted_priority_weight must be finite and within \[0, 100\]",
    ):
        store.commit_admission(
            **_commit_kwargs(accepted_priority_weight=weight),
        )

    assert _table_count(tmp_path, "user_requests") == 0
    assert _table_count(tmp_path, "request_admissions") == 0


@pytest.mark.parametrize("fault_step", COMMIT_STEPS)
def test_precommit_fault_rolls_back_entire_aggregate(tmp_path, fault_step):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)

    def access_read(conn):
        conn.execute("SELECT COUNT(*) FROM universe_acl").fetchone()

    def authority_read(conn):
        conn.execute("SELECT COUNT(*) FROM capability_grants").fetchone()

    def inject(step, _conn):
        if step == fault_step:
            raise RuntimeError(f"fault:{step}")

    with pytest.raises(RuntimeError, match=f"fault:{fault_step}"):
        store.commit_admission(
            **_commit_kwargs(),
            access_check=access_read,
            authority_check=authority_read,
            fault_injector=inject,
        )

    for table in (
        "user_requests",
        "request_admissions",
        "branch_tasks_v2",
        "request_admission_events",
    ):
        assert _table_count(tmp_path, table) == 0


def test_replay_returns_original_ids_without_new_rows_or_event(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    first = store.commit_admission(**_commit_kwargs())

    replay = store.commit_admission(**_commit_kwargs(text="ignored on replay"))

    assert replay == {**first, "idempotent_replay": True}
    assert _table_count(tmp_path, "user_requests") == 1
    assert _table_count(tmp_path, "request_admissions") == 1
    assert _table_count(tmp_path, "branch_tasks_v2") == 1
    assert _table_count(tmp_path, "request_admission_events") == 1


def test_access_precedes_lookup_and_priority_runs_only_after_replay_miss(
    tmp_path,
):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    order = []

    def access(_conn):
        order.append("access")

    def priority(_conn):
        order.append("priority")

    first = store.commit_admission(
        **_commit_kwargs(),
        access_check=access,
        authority_check=priority,
    )
    assert order == ["access", "priority"]

    order.clear()

    def revoked_priority(_conn):
        pytest.fail("historical replay rechecked prospective priority")

    replay = store.commit_admission(
        **_commit_kwargs(),
        access_check=access,
        authority_check=revoked_priority,
    )
    assert replay == {**first, "idempotent_replay": True}
    assert order == ["access"]

    order.clear()
    lookup = store.lookup_replay(
        tenant_id="tenant-a",
        actor_id="alice",
        universe_id="universe-a",
        idempotency_key_hash=_USER_KEY_HASH_A,
        body_digest="sha256:body-a",
        body_digest_version="rfc8785-v1",
        access_check=access,
    )
    assert lookup == {**first, "idempotent_replay": True}
    assert order == ["access"]

    def lost_access(_conn):
        raise PermissionError("universe_access_denied")

    with pytest.raises(PermissionError, match="universe_access_denied"):
        store.lookup_replay(
            tenant_id="tenant-a",
            actor_id="alice",
            universe_id="universe-a",
            idempotency_key_hash=_USER_KEY_HASH_A,
            body_digest="sha256:body-a",
            body_digest_version="rfc8785-v1",
            access_check=lost_access,
        )

    with pytest.raises(PermissionError, match="universe_access_denied"):
        store.commit_admission(
            **_commit_kwargs(),
            access_check=lost_access,
            authority_check=revoked_priority,
        )


def test_random_id_collision_retries_in_a_fresh_transaction(tmp_path):
    initialize_author_server(tmp_path)
    first = RequestAdmissionStore(tmp_path).commit_admission(
        **_commit_kwargs()
    )
    generated = iter([
        first["request_id"],
        "unused-admission",
        "unused-task",
        "unused-event",
        "req_unique",
        "adm_unique",
        "bt2_unique",
        "evt_unique",
    ])
    store = RequestAdmissionStore(
        tmp_path,
        id_factory=lambda _prefix: next(generated),
    )

    second = store.commit_admission(
        **_commit_kwargs(
            idempotency_key_hash=_USER_KEY_HASH_B,
            body_digest="sha256:body-b",
        )
    )

    assert second["request_id"] == "req_unique"
    assert second["admission_id"] == "adm_unique"
    assert second["branch_task_id"] == "bt2_unique"
    assert _table_count(tmp_path, "request_admissions") == 2


def test_concurrent_same_key_commits_one_aggregate(tmp_path):
    initialize_author_server(tmp_path)

    def commit(_index):
        return RequestAdmissionStore(tmp_path).commit_admission(
            **_commit_kwargs()
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(commit, range(32)))

    assert len({row["request_id"] for row in results}) == 1
    assert len({row["admission_id"] for row in results}) == 1
    assert len({row["branch_task_id"] for row in results}) == 1
    assert sum(not row["idempotent_replay"] for row in results) == 1
    assert _table_count(tmp_path, "user_requests") == 1
    assert _table_count(tmp_path, "request_admissions") == 1
    assert _table_count(tmp_path, "branch_tasks_v2") == 1
    assert _table_count(tmp_path, "request_admission_events") == 1


def test_changed_body_replay_conflicts_without_mutation(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    store.commit_admission(**_commit_kwargs())

    with pytest.raises(IdempotencyKeyBodyConflict):
        store.commit_admission(
            **_commit_kwargs(body_digest="sha256:different-body"),
        )

    assert _table_count(tmp_path, "request_admissions") == 1
    assert _table_count(tmp_path, "request_admission_events") == 1


def test_authority_check_runs_inside_transaction_and_can_abort(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    observed = []

    def deny(conn):
        observed.append(conn.in_transaction)
        raise PermissionError("revoked")

    with pytest.raises(PermissionError, match="revoked"):
        store.commit_admission(
            **_commit_kwargs(),
            authority_check=deny,
        )

    assert observed == [True]
    assert _table_count(tmp_path, "user_requests") == 0


def test_claim_transition_quarantine_and_universe_delete(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(
        tmp_path,
        clock=lambda: datetime.fromisoformat(
            "2026-07-24T08:01:00+00:00"
        ),
    )
    committed = store.commit_admission(**_commit_kwargs())

    candidates = store.list_v2_candidates(
        universe_id="universe-a",
        integrity_check=lambda _row: True,
    )
    assert [row["branch_task_id"] for row in candidates] == [
        committed["branch_task_id"]
    ]

    claimed = store.claim_v2_task(
        committed["branch_task_id"],
        worker_id="worker-1",
        queue_protocol_version=2,
        capabilities={"operator_request_v1"},
        claim_check=lambda _conn, _row, _at: True,
    )
    assert claimed["status"] == "running"
    assert claimed["claimed_by"] == "worker-1"
    assert store.claim_v2_task(
        committed["branch_task_id"],
        worker_id="worker-2",
        queue_protocol_version=2,
        capabilities={"operator_request_v1"},
        claim_check=lambda _conn, _row, _at: True,
    ) is None

    store.transition_task(
        committed["branch_task_id"],
        expected_statuses={"running"},
        new_status="failed",
        at="2026-07-24T08:02:00Z",
        detail={"error": "fixture"},
    )
    receipt = store.quarantine_task(
        committed["branch_task_id"],
        reason="invalid_operator_admission",
        observed_at="2026-07-24T08:03:00Z",
    )
    assert receipt["reason"] == "invalid_operator_admission"
    replayed_receipt = store.quarantine_task(
        committed["branch_task_id"],
        reason="invalid_operator_admission",
        observed_at="2026-07-24T08:04:00Z",
    )
    assert replayed_receipt["row_digest"] == receipt["row_digest"]
    assert _table_count(tmp_path, "branch_tasks_v2_quarantine") == 1
    with _connect(tmp_path) as conn:
        quarantine = conn.execute(
            """
            SELECT first_seen_at, last_seen_at, seen_count
            FROM branch_tasks_v2_quarantine
            WHERE row_digest = ?
            """,
            (receipt["row_digest"],),
        ).fetchone()
        assert dict(quarantine) == {
            "first_seen_at": "2026-07-24T08:03:00Z",
            "last_seen_at": "2026-07-24T08:04:00Z",
            "seen_count": 2,
        }
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM request_admission_events
            WHERE branch_task_id = ? AND event_type = 'quarantined'
            """,
            (committed["branch_task_id"],),
        ).fetchone()[0] == 1

    assert store.delete_universe("universe-a") == 1
    assert _table_count(tmp_path, "user_requests") == 0
    assert _table_count(tmp_path, "request_admissions") == 0
    assert _table_count(tmp_path, "branch_tasks_v2") == 0


def test_worker_transition_ignores_backdated_at_and_uses_transaction_clock(
    tmp_path,
):
    initialize_author_server(tmp_path)
    now = [datetime.fromisoformat("2026-07-24T08:01:00+00:00")]
    store = RequestAdmissionStore(tmp_path, clock=lambda: now[0])
    committed = store.commit_admission(**_commit_kwargs())
    claimed = store.claim_v2_task(
        committed["branch_task_id"],
        worker_id="worker-1",
        queue_protocol_version=2,
        capabilities={"operator_request_v1"},
        lease_seconds=30,
        claim_check=lambda _conn, _row, _at: True,
    )
    assert claimed is not None

    now[0] = datetime.fromisoformat("2026-07-24T08:01:31+00:00")
    with pytest.raises(PermissionError, match="branch_task_lease_expired"):
        store.transition_task(
            committed["branch_task_id"],
            expected_statuses={"running"},
            new_status="succeeded",
            at="2026-07-24T08:01:01+00:00",
            worker_id="worker-1",
        )

    task = store.get_v2_task(committed["branch_task_id"])
    assert task is not None
    assert task["status"] == "running"


def test_terminal_compaction_retains_tombstone_but_not_private_detail(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    terminal = store.commit_admission(**_commit_kwargs())
    pending = store.commit_admission(
        **_commit_kwargs(
            idempotency_key_hash=_USER_KEY_HASH_B,
            body_digest="sha256:body-b",
            text="keep pending detail",
        )
    )
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-06-01T00:00:00Z",
        detail={"private": "remove me"},
    )
    store.quarantine_task(
        terminal["branch_task_id"],
        reason="invalid_operator_admission",
        observed_at="2026-06-01T00:01:00Z",
    )

    with _connect(tmp_path) as conn:
        quarantine_snapshot = json.loads(
            conn.execute(
                """
                SELECT row_json
                FROM branch_tasks_v2_quarantine
                WHERE branch_task_id = ?
                """,
                (terminal["branch_task_id"],),
            ).fetchone()[0]
        )
        assert quarantine_snapshot == {
            "branch_task_id": terminal["branch_task_id"],
            "queue_epoch": 2,
            "status": "succeeded",
            "trigger_source": "operator_request",
            "universe_id": "universe-a",
            "protocol_version": 2,
        }

    assert store.compact_terminal_details(
        terminal_before="2026-06-24T00:00:00Z",
        compacted_at="2026-07-24T08:05:00Z",
        classifier=lambda _row: None,
    ) == 1

    with _connect(tmp_path) as conn:
        terminal_request = conn.execute(
            """
            SELECT text, preferred_author_id
            FROM user_requests
            WHERE request_id = ?
            """,
            (terminal["request_id"],),
        ).fetchone()
        pending_request = conn.execute(
            "SELECT text FROM user_requests WHERE request_id = ?",
            (pending["request_id"],),
        ).fetchone()
        admission = conn.execute(
            "SELECT body_digest, idempotency_key_hash, result_json, compacted_at "
            "FROM request_admissions WHERE admission_id = ?",
            (terminal["admission_id"],),
        ).fetchone()
        task = conn.execute(
            """
            SELECT
                inputs_json, detail_json, directed_daemon_id, claimed_by
            FROM branch_tasks_v2
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
        quarantine = conn.execute(
            """
            SELECT row_json
            FROM branch_tasks_v2_quarantine
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
        assert terminal_request["text"] == ""
        assert terminal_request["preferred_author_id"] is None
        assert pending_request["text"] == "keep pending detail"
        assert admission["body_digest"] == "sha256:body-a"
        assert admission["idempotency_key_hash"] == _USER_KEY_HASH_A
        assert json.loads(admission["result_json"]) == {
            "admission_id": terminal["admission_id"],
            "branch_task_id": terminal["branch_task_id"],
            "request_id": terminal["request_id"],
            "request_status": "succeeded",
            "universe_id": "universe-a",
        }
        assert admission["compacted_at"] == "2026-07-24T08:05:00Z"
        assert dict(task) == {
            "inputs_json": "{}",
            "detail_json": "{}",
            "directed_daemon_id": "",
            "claimed_by": "",
        }
        assert quarantine["row_json"] == "{}"


def test_sqlite_pragmas_and_concurrent_initialization(tmp_path):
    def initialize(_index):
        initialize_author_server(tmp_path)
        store = RequestAdmissionStore(tmp_path)
        with store.connection() as conn:
            return (
                conn.execute("PRAGMA journal_mode").fetchone()[0],
                conn.execute("PRAGMA foreign_keys").fetchone()[0],
                conn.execute("PRAGMA busy_timeout").fetchone()[0],
            )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(initialize, range(64)))

    assert all(str(mode).lower() == "wal" for mode, _, _ in results)
    assert all(foreign_keys == 1 for _, foreign_keys, _ in results)
    assert all(busy_timeout == 30000 for _, _, busy_timeout in results)
    with _connect(tmp_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_lock_error_propagates_without_partial_admission(tmp_path):
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path, busy_timeout_ms=1)

    with _connect(tmp_path) as locker:
        locker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            store.commit_admission(**_commit_kwargs())
        locker.rollback()

    assert _table_count(tmp_path, "user_requests") == 0
    assert _table_count(tmp_path, "request_admissions") == 0
    assert _table_count(tmp_path, "branch_tasks_v2") == 0
    assert _table_count(tmp_path, "request_admission_events") == 0
