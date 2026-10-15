from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import rfc8785

import tinyassets.storage.request_admissions as request_admission_storage
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.branch_tasks_v2 import (
    AssignedConsumerLease,
    Epoch2BranchTaskAdapter,
    WorkerClaimDescriptor,
)
from tinyassets.daemon_registry import create_daemon
from tinyassets.daemon_server import initialize_author_server
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.storage import db_path
from tinyassets.storage.automation_activations import (
    AutomationActivation,
    AutomationActivationExecutor,
    AutomationActivationStore,
)
from tinyassets.storage.request_admissions import (
    RequestAdmissionStore,
    migrate_request_admission_schema,
)

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _activation_subject(ref: str) -> ExecutionSubject:
    return ExecutionSubject(
        kind=ExecutionSubjectKind.BRANCH_VERSION,
        ref=ref,
        digest=f"sha256:{hashlib.sha256(ref.encode()).hexdigest()}",
    )


class _MutableClock:
    def __init__(self, value: str) -> None:
        self.set(value)

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: str) -> None:
        self.value = datetime.fromisoformat(value.replace("Z", "+00:00"))


def _commit(
    base_path: Path,
    *,
    key: str = "hmac:epoch2-key-a",
    body: str = "sha256:epoch2-body-a",
    trigger_source: str = "operator_request",
    weight: float = 50.0,
    directed_daemon_id: str = "",
    directed_soul_hash: str = "c" * 64,
    directed_authority_scope: str = "owner",
    universe_id: str = "universe-a",
    created_at: str = "2026-07-24T08:00:00+00:00",
    automation_activation: AutomationActivation | None = None,
) -> dict:
    # Provenance decides the algorithm: a caller-supplied key is keyed, a
    # server-derived automation identity is not (the cloud worker that mints
    # it is denied the admission HMAC secret).
    prefix = "sha256:" if automation_activation is not None else "hmac-sha256:"
    if not key.startswith(prefix):
        key = prefix + hashlib.sha256(key.encode()).hexdigest()
    del body
    canonical_body = rfc8785.dumps({
        "branch_id": "",
        "directed_daemon_id": directed_daemon_id,
        "directed_daemon_instruction": "",
        "pickup_incentive": "",
        "priority_weight": weight,
        "request_type": "general",
        "schema_version": "request-admission-v2",
        "text": "repair the queue",
        "universe_id": universe_id,
    })
    body = "sha256:" + hashlib.sha256(canonical_body).hexdigest()
    return RequestAdmissionStore(base_path).commit_admission(
        tenant_id="tenant-a",
        actor_id="actor-a",
        universe_id=universe_id,
        idempotency_key_hash=key,
        body_digest=body,
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="repair the queue",
        branch_id="",
        branch_def_id="loop-branch",
        trigger_source=trigger_source,
        accepted_priority_weight=weight,
        policy_version="operator-priority-v1",
        grant_generation=3,
        receipt={
            "authority": "request-local",
            "grant_generation": 3,
            "priority_policy_version": "operator-priority-v1",
            "directed_assignment": (
                {
                    "daemon_id": directed_daemon_id,
                    "daemon_soul_hash": directed_soul_hash,
                    "authority_scope": directed_authority_scope,
                }
                if directed_daemon_id
                else {}
            ),
        },
        directed_daemon_id=directed_daemon_id,
        created_at=created_at,
        automation_activation=automation_activation,
    )


def _descriptor(
    *,
    worker_id: str = "worker-a",
    universe_id: str = "universe-a",
    expires_at: str = "2026-07-24T08:02:15+00:00",
    executor_class: AutomationActivationExecutor | None = None,
) -> WorkerClaimDescriptor:
    return WorkerClaimDescriptor(
        queue_protocol_version=2,
        capabilities=frozenset({"operator_request_v1"}),
        worker_id=worker_id,
        runtime_instance_id="runtime-a",
        boot_id="boot-a",
        build_sha="a" * 40,
        config_hash="b" * 64,
        universe_id=universe_id,
        expires_at=expires_at,
        executor_class=executor_class,
    )


def _request_status(base_path: Path, request_id: str) -> str:
    with sqlite3.connect(db_path(base_path)) as conn:
        row = conn.execute(
            "SELECT status FROM user_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def epoch2(
    tmp_path: Path,
) -> tuple[Epoch2BranchTaskAdapter, dict, _MutableClock]:
    initialize_author_server(tmp_path)
    committed = _commit(tmp_path)
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    return Epoch2BranchTaskAdapter(tmp_path, clock=clock), committed, clock


def test_assigned_consumer_claim_is_single_winner_and_exact_lease(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    activations = AutomationActivationStore(tmp_path)
    active = activations.activate(
        expected=activations.create_stopped(
            universe_id="universe-a", automation_id="automation-a"
        ),
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-a"),
        lease_id="activation-lease-a",
    )
    assert active is not None
    committed = _commit(tmp_path, automation_activation=active)
    adapter = Epoch2BranchTaskAdapter(
        tmp_path, clock=_MutableClock("2026-07-24T08:01:00+00:00")
    )
    candidate = adapter.get(committed["branch_task_id"])
    assert candidate is not None
    lease_a = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-a",
        lease_id="lease-a",
        expires_at="2026-07-24T08:02:00+00:00",
    )
    lease_b = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-b",
        lease_id="lease-b",
        expires_at="2026-07-24T08:02:00+00:00",
    )

    won = adapter.claim_assigned(candidate, consumer_lease=lease_a)
    lost = adapter.claim_assigned(candidate, consumer_lease=lease_b)

    assert won is not None
    assert won.claimed_by == lease_a.consumer_id
    assert lost is None


def test_assigned_consumer_release_requires_exact_claim_fence(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    activations = AutomationActivationStore(tmp_path)
    active = activations.activate(
        expected=activations.create_stopped(
            universe_id="universe-a", automation_id="automation-a"
        ),
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-a"),
        lease_id="activation-lease-a",
    )
    assert active is not None
    committed = _commit(tmp_path, automation_activation=active)
    adapter = Epoch2BranchTaskAdapter(
        tmp_path, clock=_MutableClock("2026-07-24T08:01:00+00:00")
    )
    candidate = adapter.get(committed["branch_task_id"])
    assert candidate is not None
    lease = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-a",
        lease_id="lease-a",
        expires_at="2026-07-24T08:02:00+00:00",
    )
    claimed = adapter.claim_assigned(candidate, consumer_lease=lease)
    assert claimed is not None

    assert not adapter.release_assigned(
        claimed,
        consumer_lease=replace(lease, consumer_id="assigned-consumer:other"),
        reason="authority_held",
    )
    assert adapter.release_assigned(
        claimed,
        consumer_lease=lease,
        reason="authority_held",
    )
    assert adapter.get(claimed.branch_task_id).status == "pending"


def test_assigned_consumer_claim_rejects_activation_rotation(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    activations = AutomationActivationStore(tmp_path)
    active = activations.activate(
        expected=activations.create_stopped(
            universe_id="universe-a", automation_id="automation-a"
        ),
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-a"),
        lease_id="activation-lease-a",
    )
    assert active is not None
    committed = _commit(tmp_path, automation_activation=active)
    adapter = Epoch2BranchTaskAdapter(
        tmp_path, clock=_MutableClock("2026-07-24T08:01:00+00:00")
    )
    candidate = adapter.get(committed["branch_task_id"])
    assert candidate is not None
    rebound = activations.rebind(
        expected=active,
        subject=_activation_subject("branch-version-b"),
        lease_id="activation-lease-b",
    )
    assert rebound is not None
    lease = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-a",
        lease_id="lease-a",
        expires_at="2026-07-24T08:02:00+00:00",
    )

    assert adapter.claim_assigned(candidate, consumer_lease=lease) is None
    assert adapter.get(candidate.branch_task_id).status == "pending"


def test_assigned_consumer_restart_recovers_expired_claim_for_one_new_winner(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    activations = AutomationActivationStore(tmp_path)
    active = activations.activate(
        expected=activations.create_stopped(
            universe_id="universe-a", automation_id="automation-a"
        ),
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-a"),
        lease_id="activation-lease-a",
    )
    assert active is not None
    committed = _commit(tmp_path, automation_activation=active)
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    adapter = Epoch2BranchTaskAdapter(tmp_path, clock=clock)
    candidate = adapter.get(committed["branch_task_id"])
    assert candidate is not None
    first_lease = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-a",
        lease_id="lease-a",
        expires_at="2026-07-24T08:02:00+00:00",
    )
    assert adapter.claim_assigned(
        candidate, consumer_lease=first_lease, lease_seconds=30
    ) is not None

    clock.set("2026-07-24T08:01:31+00:00")
    recovered = adapter.recover_expired(
        target_recovery_guard=lambda task: task.claimed_by.startswith(
            "assigned-consumer:"
        )
    )
    assert [task.status for task in recovered] == ["pending"]
    second_lease = AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-b",
        lease_id="lease-b",
        expires_at="2026-07-24T08:03:00+00:00",
    )
    reclaimed = adapter.claim_assigned(
        recovered[0], consumer_lease=second_lease, lease_seconds=30
    )
    assert reclaimed is not None
    assert reclaimed.claimed_by == second_lease.consumer_id


def test_pretraffic_migration_adds_lease_column_to_existing_v2_store(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "ALTER TABLE branch_tasks_v2 DROP COLUMN lease_expires_at"
        )
        conn.execute(
            "ALTER TABLE branch_tasks_v2_maintenance_state "
            "DROP COLUMN cycle_high_rowid"
        )
        migrate_request_admission_schema(conn)
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(branch_tasks_v2)")
        }
        maintenance_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(branch_tasks_v2_maintenance_state)"
            )
        }

    assert "lease_expires_at" in columns
    assert "cycle_high_rowid" in maintenance_columns


def test_adapter_reads_canonical_epoch2_task_and_ids_are_unique(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    first = _commit(tmp_path)
    second = _commit(
        tmp_path,
        key="hmac:epoch2-key-b",
        body="sha256:epoch2-body-b",
        created_at="2026-07-24T08:00:01+00:00",
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    candidates = adapter.list_candidates(universe_id="universe-a")

    assert [task.branch_task_id for task in candidates] == [
        first["branch_task_id"],
        second["branch_task_id"],
    ]
    assert len({
        first["request_id"],
        first["admission_id"],
        first["branch_task_id"],
        second["request_id"],
        second["admission_id"],
        second["branch_task_id"],
    }) == 6
    task = adapter.get(first["branch_task_id"])
    assert task is not None
    assert task.queue_epoch == 2
    assert task.protocol_version == 2
    assert task.request_id == first["request_id"]
    assert task.admission_id == first["admission_id"]
    assert task.inputs["request_type"] == "general"


def test_live_claimed_request_read_model_is_worker_bound_and_lease_bound(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, clock = epoch2

    assert adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-a",
    ) == []

    claimed = adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(),
        lease_seconds=90,
    )
    assert claimed is not None

    records = adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-a",
    )

    assert len(records) == 1
    record = records[0]
    assert record.request_id == committed["request_id"]
    assert record.admission_id == committed["admission_id"]
    assert record.branch_task_id == committed["branch_task_id"]
    assert record.universe_id == "universe-a"
    assert record.request_type == "general"
    assert record.text == "repair the queue"
    assert record.branch_def_id == "loop-branch"
    assert record.trigger_source == "operator_request"
    assert record.accepted_priority_weight == 50.0
    assert record.claimed_by == "worker-a"
    assert record.claimed_at == "2026-07-24T08:01:00+00:00"
    assert record.lease_expires_at == "2026-07-24T08:02:30+00:00"
    assert not {
        "receipt",
        "tenant_id",
        "idempotency_key_hash",
        "body_digest",
        "grant_generation",
    } & record.__dict__.keys()
    assert adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-b",
    ) == []

    clock.set("2026-07-24T08:02:30+00:00")
    assert adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-a",
    ) == []


def test_invalid_live_claim_does_not_block_later_valid_claim_at_limit_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import tinyassets.storage.request_admissions as request_admissions

    initialize_author_server(tmp_path)
    first = _commit(
        tmp_path,
        key="first-live-claim",
        created_at="2026-07-24T08:00:00+00:00",
    )
    second = _commit(
        tmp_path,
        key="second-live-claim",
        created_at="2026-07-24T08:00:01+00:00",
    )
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    adapter = Epoch2BranchTaskAdapter(tmp_path, clock=clock)
    descriptor = _descriptor()
    for committed in (first, second):
        assert adapter.claim(
            committed["branch_task_id"],
            descriptor=descriptor,
            descriptor_reader=lambda _conn, _worker_id: descriptor,
        ) is not None
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE user_requests SET text = ? WHERE request_id = ?",
            ("tampered after claim", first["request_id"]),
        )

    records = adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-a",
        limit=1,
    )

    assert [record.branch_task_id for record in records] == [
        second["branch_task_id"]
    ]

    monkeypatch.setattr(
        request_admissions,
        "MAX_OPERATIONAL_SCAN_ROWS",
        1,
    )
    caplog.clear()

    assert adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-a",
        limit=1,
    ) == []
    assert "integrity scan reached the 1-row operational bound" in caplog.text


def test_cancel_requested_claim_is_not_materializable(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, _clock = epoch2
    assert adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(),
    ) is not None

    cancelled = adapter.request_cancel(committed["branch_task_id"])

    assert cancelled.status == "cancel_requested"
    assert adapter.list_live_claimed_requests(
        universe_id="universe-a",
        worker_id="worker-a",
    ) == []


@pytest.mark.parametrize(
    "universe_id",
    ["fantasy", "default-universe", "patch-loop-live"],
)
def test_existing_path_safe_universe_ids_remain_eligible(
    tmp_path: Path,
    universe_id: str,
) -> None:
    initialize_author_server(tmp_path)
    committed = _commit(tmp_path, universe_id=universe_id)

    candidates = Epoch2BranchTaskAdapter(tmp_path).list_candidates(
        universe_id=universe_id,
    )

    assert [task.branch_task_id for task in candidates] == [
        committed["branch_task_id"]
    ]


def test_claim_rechecks_exact_live_descriptor_inside_transaction(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, clock = epoch2
    observed: list[bool] = []

    assert adapter.has_active_claim(
        universe_id="universe-a",
        worker_id="worker-a",
    ) is False
    trusted_descriptor = _descriptor()

    def trusted(conn, _worker_id):
        observed.append(conn.in_transaction)
        return trusted_descriptor

    false_descriptor = _descriptor(universe_id="other-universe")
    assert adapter.claim(
        committed["branch_task_id"],
        descriptor=false_descriptor,
        descriptor_reader=trusted,
    ) is None
    assert observed == []

    expired = _descriptor(expires_at="2026-07-24T08:00:59+00:00")
    assert adapter.claim(
        committed["branch_task_id"],
        descriptor=expired,
        descriptor_reader=trusted,
    ) is None
    assert observed == [True]

    claimed = adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=trusted,
        lease_seconds=90,
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.claimed_by == "worker-a"
    assert claimed.lease_expires_at == "2026-07-24T08:02:30+00:00"
    assert observed == [True, True]
    assert adapter.has_active_claim(
        universe_id="universe-a",
        worker_id="worker-a",
    ) is True
    assert adapter.has_active_claim(
        universe_id="universe-a",
        worker_id="worker-b",
    ) is False
    assert _request_status(
        adapter.base_path,
        committed["request_id"],
    ) == "running"
    clock.set("2026-07-24T08:01:01+00:00")
    assert adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(worker_id="worker-b"),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(
            worker_id="worker-b"
        ),
    ) is None
    clock.set("2026-07-24T08:02:31+00:00")
    assert adapter.has_active_claim(
        universe_id="universe-a",
        worker_id="worker-a",
    ) is False


def test_activation_bound_claim_rechecks_current_epoch_and_executor(
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
    stale_task = _commit(
        tmp_path,
        key="activation-stale",
        automation_activation=active,
    )
    rebound = activations.rebind(
        expected=active,
        subject=_activation_subject("branch-version-b"),
        lease_id="activation-lease-b",
    )
    assert rebound is not None
    current_task = _commit(
        tmp_path,
        key="activation-current",
        created_at="2026-07-24T08:00:01+00:00",
        automation_activation=rebound,
    )
    adapter = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=_MutableClock("2026-07-24T08:01:00+00:00"),
    )
    cloud = _descriptor(
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    tray = _descriptor(
        worker_id="worker-tray",
        executor_class=AutomationActivationExecutor.TRAY,
    )

    assert adapter.claim(
        stale_task["branch_task_id"],
        descriptor=cloud,
        descriptor_reader=lambda _conn, _worker_id: cloud,
    ) is None
    assert adapter.claim(
        current_task["branch_task_id"],
        descriptor=tray,
        descriptor_reader=lambda _conn, _worker_id: tray,
    ) is None
    claimed = adapter.claim(
        current_task["branch_task_id"],
        descriptor=cloud,
        descriptor_reader=lambda _conn, _worker_id: cloud,
    )
    assert claimed is not None
    assert claimed.claimed_by == "worker-a"


def test_stopped_or_tampered_activation_cannot_claim(
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
    stopped_task = _commit(
        tmp_path,
        key="activation-stopped",
        automation_activation=active,
    )
    stopped_again = activations.stop(expected=active)
    assert stopped_again is not None
    cloud = _descriptor(
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    adapter = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=_MutableClock("2026-07-24T08:01:00+00:00"),
    )

    assert adapter.claim(
        stopped_task["branch_task_id"],
        descriptor=cloud,
        descriptor_reader=lambda _conn, _worker_id: cloud,
    ) is None

    reactivated = activations.activate(
        expected=stopped_again,
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=_activation_subject("branch-version-b"),
        lease_id="activation-lease-b",
    )
    assert reactivated is not None
    tampered_task = _commit(
        tmp_path,
        key="activation-tampered",
        created_at="2026-07-24T08:00:01+00:00",
        automation_activation=reactivated,
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET automation_id = 'alternate-automation'
            WHERE branch_task_id = ?
            """,
            (tampered_task["branch_task_id"],),
        )

    assert adapter.claim(
        tampered_task["branch_task_id"],
        descriptor=cloud,
        descriptor_reader=lambda _conn, _worker_id: cloud,
    ) is None

    subject_tampered_task = _commit(
        tmp_path,
        key="activation-subject-tampered",
        created_at="2026-07-24T08:00:01.500000+00:00",
        automation_activation=reactivated,
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET automation_subject_digest = ?
            WHERE branch_task_id = ?
            """,
            (
                f"sha256:{'f' * 64}",
                subject_tampered_task["branch_task_id"],
            ),
        )

    assert adapter.claim(
        subject_tampered_task["branch_task_id"],
        descriptor=cloud,
        descriptor_reader=lambda _conn, _worker_id: cloud,
    ) is None

    erased_task = _commit(
        tmp_path,
        key="activation-erased",
        created_at="2026-07-24T08:00:02+00:00",
        automation_activation=reactivated,
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET automation_id = NULL,
                automation_activation_epoch = NULL,
                automation_executor_class = NULL,
                automation_subject_kind = NULL,
                automation_subject_ref = NULL,
                automation_subject_digest = NULL,
                automation_branch_version = NULL,
                automation_lease_id = NULL
            WHERE branch_task_id = ?
            """,
            (erased_task["branch_task_id"],),
        )

    assert adapter.claim(
        erased_task["branch_task_id"],
        descriptor=cloud,
        descriptor_reader=lambda _conn, _worker_id: cloud,
    ) is None


def test_claim_uses_transaction_time_for_descriptor_freshness(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    committed = _commit(tmp_path)
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    adapter = Epoch2BranchTaskAdapter(tmp_path, clock=clock)
    expired = _descriptor(expires_at="2026-07-24T08:00:59+00:00")

    claimed = adapter.claim(
        committed["branch_task_id"],
        descriptor=expired,
        descriptor_reader=lambda _conn, _worker_id: expired,
    )

    assert claimed is None
    overlong = _descriptor(expires_at="2026-07-24T08:02:31+00:00")
    assert adapter.claim(
        committed["branch_task_id"],
        descriptor=overlong,
        descriptor_reader=lambda _conn, _worker_id: overlong,
    ) is None
    live = _descriptor()
    with pytest.raises(ValueError, match="lease_seconds must be between"):
        adapter.claim(
            committed["branch_task_id"],
            descriptor=live,
            descriptor_reader=lambda _conn, _worker_id: live,
            lease_seconds=1801,
        )
    task = adapter.get(committed["branch_task_id"])
    assert task is not None
    assert task.status == "pending"


def test_concurrent_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    initialize_author_server(tmp_path)
    committed = _commit(tmp_path)
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    barrier = threading.Barrier(3)
    results: dict[str, object] = {}
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def race(worker_id: str) -> None:
        try:
            descriptor = _descriptor(worker_id=worker_id)
            adapter = Epoch2BranchTaskAdapter(tmp_path, clock=clock)
            barrier.wait()
            claimed = adapter.claim(
                committed["branch_task_id"],
                descriptor=descriptor,
                descriptor_reader=lambda _conn, _worker_id: descriptor,
            )
            with result_lock:
                results[worker_id] = claimed
        except BaseException as exc:
            with result_lock:
                errors.append(exc)
            try:
                barrier.abort()
            except threading.BrokenBarrierError:
                pass

    threads = [
        threading.Thread(target=race, args=("worker-a",)),
        threading.Thread(target=race, args=("worker-b",)),
    ]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=10)
    except threading.BrokenBarrierError:
        pass
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    winners = [
        worker_id for worker_id, task in results.items() if task is not None
    ]
    assert len(winners) == 1
    task = Epoch2BranchTaskAdapter(tmp_path).get(
        committed["branch_task_id"]
    )
    assert task is not None
    assert task.status == "running"
    assert task.claimed_by == winners[0]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        claim_events = conn.execute(
            """
            SELECT COUNT(*)
            FROM request_admission_events
            WHERE branch_task_id = ? AND event_type = 'claimed'
            """,
            (committed["branch_task_id"],),
        ).fetchone()
    assert claim_events is not None
    assert claim_events[0] == 1


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("queue_protocol_version", 1),
        ("capabilities", frozenset()),
        ("worker_id", "worker-other"),
        ("runtime_instance_id", "runtime-other"),
        ("boot_id", "boot-other"),
        ("build_sha", "c" * 40),
        ("config_hash", "d" * 64),
        ("universe_id", "universe-other"),
    ],
)
def test_claim_rejects_every_descriptor_identity_mismatch(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
    field_name: str,
    invalid_value,
) -> None:
    adapter, committed, _clock = epoch2
    trusted = _descriptor()
    offered = replace(trusted, **{field_name: invalid_value})

    claimed = adapter.claim(
        committed["branch_task_id"],
        descriptor=offered,
        descriptor_reader=lambda _conn, _worker_id: trusted,
    )

    assert claimed is None
    task = adapter.get(committed["branch_task_id"])
    assert task is not None
    assert task.status == "pending"


def test_heartbeat_cancel_terminal_and_expired_recovery(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    first = _commit(tmp_path)
    second = _commit(
        tmp_path,
        key="hmac:epoch2-key-b",
        body="sha256:epoch2-body-b",
    )
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    adapter = Epoch2BranchTaskAdapter(tmp_path, clock=clock)

    def trusted_a(_conn, _worker_id):
        return _descriptor()

    def trusted_b(_conn, _worker_id):
        return _descriptor(worker_id="worker-b")

    adapter.claim(
        first["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=trusted_a,
        lease_seconds=30,
    )
    adapter.claim(
        second["branch_task_id"],
        descriptor=_descriptor(worker_id="worker-b"),
        descriptor_reader=trusted_b,
        lease_seconds=30,
    )

    clock.set("2026-07-24T08:01:10+00:00")
    assert adapter.heartbeat(
        first["branch_task_id"],
        worker_id="wrong-worker",
    ) is None
    heartbeat = adapter.heartbeat(
        first["branch_task_id"],
        worker_id="worker-a",
        lease_seconds=90,
    )
    assert heartbeat is not None
    assert heartbeat.heartbeat_at == "2026-07-24T08:01:10+00:00"
    assert heartbeat.lease_expires_at == "2026-07-24T08:02:40+00:00"

    clock.set("2026-07-24T08:01:20+00:00")
    cancel_requested = adapter.request_cancel(first["branch_task_id"])
    assert cancel_requested.status == "cancel_requested"
    clock.set("2026-07-24T08:01:30+00:00")
    cancelled = adapter.finish(
        first["branch_task_id"],
        worker_id="worker-a",
        status="cancelled",
    )
    assert cancelled.status == "cancelled"
    assert cancelled.terminal_at == "2026-07-24T08:01:30+00:00"

    clock.set("2026-07-24T08:01:31+00:00")
    recovered = adapter.recover_expired()
    assert [task.branch_task_id for task in recovered] == [
        second["branch_task_id"]
    ]
    assert recovered[0].status == "pending"
    assert recovered[0].claimed_by == ""
    assert recovered[0].lease_expires_at == ""
    assert _request_status(
        tmp_path,
        second["request_id"],
    ) == "pending"


def test_recovery_poll_without_expired_claim_stays_read_only(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
    monkeypatch,
) -> None:
    adapter, _committed, _clock = epoch2
    statements: list[str] = []
    real_connection = adapter._store.connection

    @contextmanager
    def traced_connection():
        with real_connection() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(
        adapter._store,
        "connection",
        traced_connection,
    )

    assert adapter.recover_expired() == []
    assert not any(
        "BEGIN IMMEDIATE" in statement.upper()
        for statement in statements
    )


def test_recovery_fails_closed_for_partial_epoch2_schema(tmp_path) -> None:
    adapter = Epoch2BranchTaskAdapter(tmp_path)
    with adapter._store.connection() as conn:
        conn.execute(
            "CREATE TABLE branch_tasks_v2 (branch_task_id TEXT PRIMARY KEY)"
        )
        conn.commit()

    with pytest.raises(
        sqlite3.OperationalError,
        match="branch_tasks_v2 recovery schema incomplete",
    ):
        adapter.recover_expired()


def test_pending_cancel_is_terminal_without_claim(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, _clock = epoch2

    cancelled = adapter.request_cancel(committed["branch_task_id"])

    assert cancelled.status == "cancelled"
    assert cancelled.terminal_at == "2026-07-24T08:01:00+00:00"
    assert _request_status(
        adapter.base_path,
        committed["request_id"],
    ) == "cancelled"
    assert adapter.list_candidates(universe_id="universe-a") == []


def test_expired_worker_cannot_heartbeat_or_finish_before_recovery(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, clock = epoch2
    adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(),
        lease_seconds=30,
    )

    clock.set("2026-07-24T08:01:31+00:00")
    assert adapter.heartbeat(
        committed["branch_task_id"],
        worker_id="worker-a",
    ) is None
    with pytest.raises(PermissionError, match="branch_task_lease_expired"):
        adapter.finish(
            committed["branch_task_id"],
            worker_id="worker-a",
            status="succeeded",
        )
    recovered = adapter.recover_expired()
    assert [task.status for task in recovered] == ["pending"]


def test_epoch2_recovery_requires_target_guard_when_supplied(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, clock = epoch2
    adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(),
        lease_seconds=30,
    )
    clock.set("2026-07-24T08:01:31+00:00")

    assert adapter.recover_expired(
        target_recovery_guard=lambda _task: False,
    ) == []
    assert adapter.get(committed["branch_task_id"]).status == "running"


def test_epoch2_recovery_revalidates_row_after_target_proof(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, clock = epoch2
    adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(),
        lease_seconds=30,
    )
    clock.set("2026-07-24T08:01:31+00:00")

    def renew_during_proof(_task) -> bool:
        with adapter._store.connection() as conn:
            conn.execute(
                """
                UPDATE branch_tasks_v2
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE branch_task_id = ?
                """,
                (
                    "2026-07-24T08:01:31+00:00",
                    "2026-07-24T08:03:00+00:00",
                    committed["branch_task_id"],
                ),
            )
            conn.commit()
        return True

    assert adapter.recover_expired(
        target_recovery_guard=renew_during_proof,
    ) == []
    task = adapter.get(committed["branch_task_id"])
    assert task.status == "running"
    assert task.lease_expires_at == "2026-07-24T08:03:00+00:00"


def test_cancel_requested_task_recovers_to_cancelled(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, clock = epoch2
    adapter.claim(
        committed["branch_task_id"],
        descriptor=_descriptor(),
        descriptor_reader=lambda _conn, _worker_id: _descriptor(),
        lease_seconds=30,
    )
    clock.set("2026-07-24T08:01:10+00:00")
    requested = adapter.request_cancel(committed["branch_task_id"])
    assert requested.status == "cancel_requested"
    assert _request_status(
        adapter.base_path,
        committed["request_id"],
    ) == "cancel_requested"

    clock.set("2026-07-24T08:01:31+00:00")
    recovered = adapter.recover_expired()

    assert [task.status for task in recovered] == ["cancelled"]
    assert recovered[0].terminal_at == "2026-07-24T08:01:31+00:00"
    assert _request_status(
        adapter.base_path,
        committed["request_id"],
    ) == "cancelled"
    assert adapter.list_candidates(universe_id="universe-a") == []


def test_claim_event_failure_rolls_back_running_transition(
    epoch2: tuple[Epoch2BranchTaskAdapter, dict, _MutableClock],
) -> None:
    adapter, committed, _clock = epoch2
    with sqlite3.connect(db_path(adapter.base_path)) as conn:
        conn.executescript(
            """
            CREATE TRIGGER fail_claim_event
            BEFORE INSERT ON request_admission_events
            WHEN NEW.event_type = 'claimed'
            BEGIN
                SELECT RAISE(ABORT, 'claim event failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="claim event failure"):
        adapter.claim(
            committed["branch_task_id"],
            descriptor=_descriptor(),
            descriptor_reader=lambda _conn, _worker_id: _descriptor(),
        )

    task = adapter.get(committed["branch_task_id"])
    assert task is not None
    assert task.status == "pending"
    assert task.claimed_by == ""
    assert task.heartbeat_at == ""
    assert task.lease_expires_at == ""
    assert _request_status(
        adapter.base_path,
        committed["request_id"],
    ) == "pending"






def test_directed_epoch2_task_preserves_owner_tier_and_assignment(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    committed = _commit(
        tmp_path,
        trigger_source="owner_queued",
        weight=25,
        directed_daemon_id="daemon-a",
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    task = adapter.get(committed["branch_task_id"])

    assert task is not None
    assert task.trigger_source == "owner_queued"
    assert task.priority_weight == 25
    assert task.directed_daemon_id == "daemon-a"


def test_quarantine_maintenance_is_separate_atomic_and_sanitized(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    valid = _commit(
        tmp_path,
        key="hmac:epoch2-key-b",
        body="sha256:epoch2-body-b",
        created_at="2026-07-24T08:00:01+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET protocol_version = 99
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )
    adapter = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=_MutableClock("2026-07-24T08:01:00+00:00"),
    )

    # Pure selection rejects the bad row without writing quarantine state.
    assert [
        task.branch_task_id for task in adapter.list_candidates()
    ] == [valid["branch_task_id"]]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT disabled FROM branch_tasks_v2 "
            "WHERE branch_task_id = ?",
            (invalid["branch_task_id"],),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine"
        ).fetchone()[0] == 0

    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.branch_task_id == invalid["branch_task_id"]
    assert receipt.reason == "unsupported_protocol"
    assert len(receipt.row_digest) == 64
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.row_factory = sqlite3.Row
        source = conn.execute(
            """
            SELECT disabled, quarantine_reason
            FROM branch_tasks_v2
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        ).fetchone()
        stored = conn.execute(
            """
            SELECT row_digest, reason, row_json, seen_count
            FROM branch_tasks_v2_quarantine
            """
        ).fetchone()
    assert dict(source) == {
        "disabled": 1,
        "quarantine_reason": "unsupported_protocol",
    }
    assert stored["row_digest"] == receipt.row_digest
    assert stored["reason"] == "unsupported_protocol"
    assert stored["seen_count"] == 1
    snapshot = json.loads(stored["row_json"])
    assert set(snapshot) == {
        "branch_task_id",
        "protocol_version",
        "queue_epoch",
        "status",
        "trigger_source",
        "universe_id",
    }
    assert "repair the queue" not in stored["row_json"]
    assert "request-local" not in stored["row_json"]

    replay = adapter.maintain_quarantine()
    assert replay.health == "green"
    assert replay.quarantined == 0
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "fail_stage",
    ["quarantine_receipt_written", "quarantine_source_disabled"],
)
def test_quarantine_failure_is_red_rollback_and_row_remains_inert(
    tmp_path: Path,
    fail_stage: str,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET admission_id = 'missing-admission'
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )
    adapter = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=_MutableClock("2026-07-24T08:01:00+00:00"),
    )

    def fail_at_stage(stage, _conn):
        if stage == fail_stage:
            raise RuntimeError("injected quarantine failure")

    result = adapter.maintain_quarantine(
        fault_injector=fail_at_stage,
    )

    assert result.health == "red"
    assert result.error_code == "quarantine_persistence_failed"
    assert result.quarantined == 0
    assert result.receipts == ()
    assert adapter.list_candidates() == []
    descriptor = _descriptor()
    assert adapter.claim(
        invalid["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    ) is None
    with sqlite3.connect(db_path(tmp_path)) as conn:
        source = conn.execute(
            """
            SELECT disabled, quarantine_reason
            FROM branch_tasks_v2
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        ).fetchone()
        quarantine_count = conn.execute(
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine"
        ).fetchone()[0]
        event_count = conn.execute(
            """
            SELECT COUNT(*) FROM request_admission_events
            WHERE branch_task_id = ? AND event_type = 'quarantined'
            """,
            (invalid["branch_task_id"],),
        ).fetchone()[0]
    assert source == (0, "")
    assert quarantine_count == 0
    assert event_count == 0

    recovered = adapter.maintain_quarantine()
    assert recovered.health == "green"
    assert recovered.quarantined == 1
    assert recovered.receipts[0].reason == "incomplete"


def test_malformed_json_is_incomplete_and_never_reaches_task_decoding(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET inputs_json = '{not-json'
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    assert adapter.list_candidates() == []
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "incomplete"


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("request_type", "revision"),
        ("branch_id", "forged-branch"),
        ("pickup_incentive", "forged-pickup"),
        ("directed_daemon_instruction", "forged instruction"),
    ],
)
def test_parseable_task_input_mutation_breaks_canonical_body_evidence(
    tmp_path: Path,
    field: str,
    forged_value: str,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        raw = conn.execute(
            """
            SELECT inputs_json FROM branch_tasks_v2
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        ).fetchone()[0]
        inputs = json.loads(raw)
        inputs[field] = forged_value
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET inputs_json = ?
            WHERE branch_task_id = ?
            """,
            (json.dumps(inputs), invalid["branch_task_id"]),
        )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    assert adapter.list_candidates() == []
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "invalid_operator_admission"


@pytest.mark.parametrize(
    "mutation",
    ["branch_def_id", "unexpected_input"],
)
def test_unauthenticated_executable_task_inputs_are_inert_and_quarantined(
    tmp_path: Path,
    mutation: str,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        if mutation == "branch_def_id":
            conn.execute(
                """
                UPDATE branch_tasks_v2 SET branch_def_id = 'forged-branch-def'
                WHERE branch_task_id = ?
                """,
                (invalid["branch_task_id"],),
            )
        else:
            raw = conn.execute(
                """
                SELECT inputs_json FROM branch_tasks_v2
                WHERE branch_task_id = ?
                """,
                (invalid["branch_task_id"],),
            ).fetchone()[0]
            inputs = json.loads(raw)
            inputs["unexpected_executable_input"] = "forged"
            conn.execute(
                """
                UPDATE branch_tasks_v2 SET inputs_json = ?
                WHERE branch_task_id = ?
                """,
                (json.dumps(inputs), invalid["branch_task_id"]),
            )
    adapter = Epoch2BranchTaskAdapter(tmp_path)
    descriptor = _descriptor()

    assert adapter.list_candidates() == []
    assert adapter.claim(
        invalid["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    ) is None
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "invalid_operator_admission"


def test_corrupt_admission_evidence_cannot_claim_before_maintenance(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE request_admissions
            SET receipt_json = '{not-json'
            WHERE admission_id = (
                SELECT admission_id FROM branch_tasks_v2
                WHERE branch_task_id = ?
            )
            """,
            (invalid["branch_task_id"],),
        )
    adapter = Epoch2BranchTaskAdapter(tmp_path)
    descriptor = _descriptor()

    assert adapter.list_candidates() == []
    assert adapter.claim(
        invalid["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    ) is None
    result = adapter.maintain_quarantine()
    assert result.health == "green"
    assert result.receipts[0].reason == "invalid_operator_admission"


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        (
            "UPDATE request_admissions "
            "SET idempotency_key_hash = 'forged-key' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions "
            "SET body_digest = 'forged-body', "
            "body_digest_version = 'rfc8785-v0' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions "
            "SET priority_policy_version = 'forged-policy' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions SET receipt_json = "
            "'{\"authority\":\"request-local\","
            "\"grant_generation\":999,"
            "\"priority_policy_version\":\"operator-priority-v1\","
            "\"directed_assignment\":{}}' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions SET actor_id = 'forged-actor' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions SET tenant_id = 'forged-tenant' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions SET receipt_json = '{}' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE request_admissions SET result_json = '{}' "
            "WHERE branch_task_id = ?",
            "invalid_operator_admission",
        ),
        (
            "UPDATE user_requests SET status = 'succeeded' "
            "WHERE request_id = ("
            "SELECT request_id FROM branch_tasks_v2 "
            "WHERE branch_task_id = ?)",
            "invalid_operator_admission",
        ),
    ],
)
def test_parseable_forged_evidence_is_inert_and_quarantined(
    tmp_path: Path,
    mutation: str,
    expected_reason: str,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(mutation, (invalid["branch_task_id"],))
    adapter = Epoch2BranchTaskAdapter(tmp_path)
    descriptor = _descriptor()

    assert adapter.list_candidates() == []
    assert adapter.claim(
        invalid["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    ) is None
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == expected_reason


@pytest.mark.parametrize(
    ("authority_scope", "soul_hash"),
    [
        ("none", "c" * 64),
        ("local_host", "c" * 64),
        ("delegated_host", "c" * 64),
        ("owner", "d" * 64),
        ("owner", "not-a-canonical-soul-hash"),
    ],
)
def test_unauthorized_directed_receipt_is_inert(
    tmp_path: Path,
    authority_scope: str,
    soul_hash: str,
) -> None:
    initialize_author_server(tmp_path)
    daemon = create_daemon(
        tmp_path,
        display_name="Directed Test Daemon",
        created_by="actor-a",
        soul_mode="soul",
        soul_text="A stable directed test soul.",
    )
    effective_soul_hash = (
        daemon["soul_hash"] if soul_hash == "c" * 64 else soul_hash
    )
    invalid = _commit(
        tmp_path,
        directed_daemon_id=daemon["daemon_id"],
        directed_soul_hash=daemon["soul_hash"],
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)
    assert [task.branch_task_id for task in adapter.list_candidates()] == [
        invalid["branch_task_id"]
    ]
    forged_receipt = {
        "authority": "request-local",
        "grant_generation": 3,
        "priority_policy_version": "operator-priority-v1",
        "directed_assignment": {
            "daemon_id": daemon["daemon_id"],
            "daemon_soul_hash": effective_soul_hash,
            "authority_scope": authority_scope,
        },
    }
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE request_admissions SET receipt_json = ?
            WHERE branch_task_id = ?
            """,
            (json.dumps(forged_receipt), invalid["branch_task_id"]),
        )

    assert adapter.list_candidates() == []
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "invalid_operator_admission"


def test_null_task_id_uses_stable_sanitized_quarantine_identifier(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE branch_tasks_v2 SET branch_task_id = NULL "
            "WHERE branch_task_id = ?",
            (invalid["branch_task_id"],),
        )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    assert adapter.list_candidates() == []
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    receipt = result.receipts[0]
    assert receipt.reason == "incomplete"
    assert receipt.branch_task_id.startswith("quarantined-task-")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        source = conn.execute(
            """
            SELECT branch_task_id, disabled, quarantine_reason
            FROM branch_tasks_v2
            """
        ).fetchone()
        stored = conn.execute(
            """
            SELECT branch_task_id, row_json
            FROM branch_tasks_v2_quarantine
            """
        ).fetchone()
    assert source == (receipt.branch_task_id, 1, "incomplete")
    assert stored[0] == receipt.branch_task_id
    assert invalid["branch_task_id"] not in stored[1]
    replayed = RequestAdmissionStore(tmp_path).quarantine_task(
        receipt.branch_task_id,
        reason="incomplete",
        observed_at="2026-07-24T08:02:00+00:00",
    )
    assert replayed["row_digest"] == receipt.row_digest
    with sqlite3.connect(db_path(tmp_path)) as conn:
        count, seen_count = conn.execute(
            """
            SELECT COUNT(*), MAX(seen_count)
            FROM branch_tasks_v2_quarantine
            """
        ).fetchone()
    assert (count, seen_count) == (1, 2)


def test_corrupt_string_identifiers_are_never_exposed_in_receipt(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    private_task_value = "repair the queue bearer-secret-value"
    private_universe_value = "raw request text authorization evidence"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET branch_task_id = ?, universe_id = ?
            WHERE branch_task_id = ?
            """,
            (
                private_task_value,
                private_universe_value,
                invalid["branch_task_id"],
            ),
        )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    receipt = result.receipts[0]
    assert receipt.branch_task_id.startswith("quarantined-task-")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        stored = conn.execute(
            """
            SELECT universe_id, row_json
            FROM branch_tasks_v2_quarantine
            """
        ).fetchone()
    assert stored[0].startswith("unknown-universe-")
    assert private_task_value not in stored[1]
    assert private_universe_value not in stored[1]


def test_quarantine_digest_is_independent_of_physical_rowid(
    tmp_path: Path,
) -> None:
    first_base = tmp_path / "first"
    second_base = tmp_path / "second"
    initialize_author_server(first_base)
    invalid = _commit(first_base)
    with sqlite3.connect(db_path(first_base)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET protocol_version = 99
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )
    with sqlite3.connect(db_path(first_base)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    second_base.mkdir()
    shutil.copy2(db_path(first_base), db_path(second_base))
    with sqlite3.connect(db_path(second_base)) as conn:
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET rowid = 100
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )

    first = Epoch2BranchTaskAdapter(first_base).maintain_quarantine()
    second = Epoch2BranchTaskAdapter(second_base).maintain_quarantine()

    assert first.health == second.health == "green"
    assert first.quarantined == second.quarantined == 1
    assert first.receipts[0].row_digest == second.receipts[0].row_digest


@pytest.mark.parametrize("corruption", ["non_finite", "blob"])
def test_raw_sqlite_corruption_has_totalized_digest_and_receipt(
    tmp_path: Path,
    corruption: str,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        if corruption == "non_finite":
            conn.execute(
                """
                UPDATE branch_tasks_v2 SET priority_weight = ?
                WHERE branch_task_id = ?
                """,
                (float("inf"), invalid["branch_task_id"]),
            )
        else:
            conn.execute(
                """
                UPDATE branch_tasks_v2 SET branch_def_id = ?
                WHERE branch_task_id = ?
                """,
                (sqlite3.Binary(b"private-corrupt-bytes"), invalid["branch_task_id"]),
            )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    assert adapter.list_candidates() == []
    result = adapter.maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "incomplete"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        row_json = conn.execute(
            "SELECT row_json FROM branch_tasks_v2_quarantine"
        ).fetchone()[0]
    assert "private-corrupt-bytes" not in row_json


def test_maintenance_scan_limit_rotates_and_bounds_writer_lock(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    for index in range(8):
        _commit(
            tmp_path,
            key=f"hmac:bounded-{index}",
            body=f"sha256:bounded-{index}",
            created_at=f"2026-07-24T08:00:{index:02d}+00:00",
        )
    entered = threading.Event()
    release = threading.Event()
    writer_done = threading.Event()
    classifier_calls: list[str] = []
    failures: list[BaseException] = []
    store = RequestAdmissionStore(tmp_path)

    def bounded_classifier(row) -> None:
        classifier_calls.append(str(row["branch_task_id"]))
        if len(classifier_calls) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return None

    def maintain() -> None:
        try:
            result = store.maintain_v2_quarantine(
                classifier=bounded_classifier,
                limit=2,
            )
            assert result["scanned"] == 2
        except BaseException as exc:
            failures.append(exc)

    def write() -> None:
        try:
            _commit(
                tmp_path,
                key="hmac:concurrent-writer",
                body="sha256:concurrent-writer",
                created_at="2026-07-24T08:01:00+00:00",
            )
            writer_done.set()
        except BaseException as exc:
            failures.append(exc)

    maintenance_thread = threading.Thread(target=maintain)
    writer_thread = threading.Thread(target=write)
    maintenance_thread.start()
    assert entered.wait(timeout=5)
    writer_thread.start()
    release.set()
    maintenance_thread.join(timeout=10)
    writer_thread.join(timeout=10)

    assert failures == []
    assert classifier_calls and len(classifier_calls) == 2
    assert writer_done.is_set()


def test_maintenance_bounds_terminal_history_to_scan_limit(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    for index in range(4):
        terminal = _commit(
            tmp_path,
            key=f"hmac:terminal-{index}",
            body=f"sha256:terminal-{index}",
            created_at=f"2026-07-24T08:00:{index:02d}+00:00",
        )
        store.transition_task(
            terminal["branch_task_id"],
            expected_statuses={"pending"},
            new_status="succeeded",
            at=f"2026-07-24T08:01:{index:02d}+00:00",
        )
    _commit(
        tmp_path,
        key="hmac:active-after-terminal",
        body="sha256:active-after-terminal",
        created_at="2026-07-24T08:01:01+00:00",
    )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine(limit=2)

    assert result.health == "green"
    assert result.scanned == 2
    assert result.quarantined == 0


def test_compacted_terminal_row_is_not_reclassified_as_invalid(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    terminal = _commit(tmp_path)
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-07-24T08:01:00+00:00",
    )
    assert Epoch2BranchTaskAdapter(tmp_path).compact_terminal_details(
        terminal_before="2026-07-25T00:00:00+00:00",
        compacted_at="2026-07-25T00:01:00+00:00",
    ) == 1

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 0
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine"
        ).fetchone()[0] == 0


def test_compaction_hard_caps_physical_rows_per_writer_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_author_server(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    for index in range(3):
        terminal = _commit(
            tmp_path,
            key=f"hmac:compaction-cap-{index}",
            created_at=f"2026-07-24T08:00:0{index}+00:00",
        )
        store.transition_task(
            terminal["branch_task_id"],
            expected_statuses={"pending"},
            new_status="succeeded",
            at=f"2026-07-24T08:01:0{index}+00:00",
        )
    monkeypatch.setattr(
        request_admission_storage,
        "MAX_QUARANTINE_SCAN_ROWS",
        2,
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    first = adapter.compact_terminal_details(
        terminal_before="2026-07-25T00:00:00+00:00",
        compacted_at="2026-07-25T00:01:00+00:00",
        limit=10_000,
    )
    second = adapter.compact_terminal_details(
        terminal_before="2026-07-25T00:00:00+00:00",
        compacted_at="2026-07-25T00:02:00+00:00",
        limit=10_000,
    )

    assert (first, second) == (2, 1)


def test_compaction_rejects_operation_time_before_terminal_without_mutation(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    terminal = _commit(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-07-24T08:01:00+00:00",
    )

    with pytest.raises(
        ValueError,
        match="compacted_at must not precede terminal_at",
    ):
        Epoch2BranchTaskAdapter(tmp_path).compact_terminal_details(
            terminal_before="2026-07-25T00:00:00+00:00",
            compacted_at="2026-07-24T08:00:00+00:00",
        )

    with sqlite3.connect(db_path(tmp_path)) as conn:
        admission = conn.execute(
            """
            SELECT compacted_at FROM request_admissions
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
        request_text = conn.execute(
            """
            SELECT text FROM user_requests
            WHERE request_id = ?
            """,
            (terminal["request_id"],),
        ).fetchone()
    assert admission == (None,)
    assert request_text == ("repair the queue",)


@pytest.mark.parametrize(
    ("table", "value"),
    [
        ("request_admissions", "2026-07-24T08:02:00+00:00"),
        ("branch_tasks_v2", ""),
    ],
)
def test_compaction_quarantines_inconsistent_persisted_terminal_time(
    tmp_path: Path,
    table: str,
    value: str,
) -> None:
    initialize_author_server(tmp_path)
    terminal = _commit(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-07-24T08:01:00+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            f"""
            UPDATE {table} SET terminal_at = ?
            WHERE branch_task_id = ?
            """,
            (value, terminal["branch_task_id"]),
        )

    compacted = Epoch2BranchTaskAdapter(tmp_path).compact_terminal_details(
        terminal_before="2026-07-25T00:00:00+00:00",
        compacted_at="2026-07-25T00:01:00+00:00",
    )

    assert compacted == 0
    with sqlite3.connect(db_path(tmp_path)) as conn:
        admission = conn.execute(
            """
            SELECT compacted_at FROM request_admissions
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
        quarantine = conn.execute(
            """
            SELECT reason FROM branch_tasks_v2_quarantine
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
    assert admission == (None,)
    assert quarantine == ("invalid_operator_admission",)


def test_compaction_quarantines_invalid_terminal_before_erasing_evidence(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    terminal = _commit(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-07-24T08:01:00+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            """
            UPDATE request_admissions SET receipt_json = '{}'
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        )

    compacted = Epoch2BranchTaskAdapter(tmp_path).compact_terminal_details(
        terminal_before="2026-07-25T00:00:00+00:00",
        compacted_at="2026-07-25T00:01:00+00:00",
    )

    assert compacted == 0
    with sqlite3.connect(db_path(tmp_path)) as conn:
        admission = conn.execute(
            """
            SELECT compacted_at FROM request_admissions
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
        quarantine = conn.execute(
            """
            SELECT reason FROM branch_tasks_v2_quarantine
            WHERE branch_task_id = ?
            """,
            (terminal["branch_task_id"],),
        ).fetchone()
    assert admission == (None,)
    assert quarantine == ("invalid_operator_admission",)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("terminal_at", "2026-07-24T08:02:00+00:00"),
        ("compacted_at", "2026-07-24T08:00:00+00:00"),
    ],
)
def test_compacted_terminal_timestamps_must_match_and_be_ordered(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    initialize_author_server(tmp_path)
    terminal = _commit(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-07-24T08:01:00+00:00",
    )
    assert Epoch2BranchTaskAdapter(tmp_path).compact_terminal_details(
        terminal_before="2026-07-25T00:00:00+00:00",
        compacted_at="2026-07-25T00:01:00+00:00",
    ) == 1
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            f"""
            UPDATE request_admissions
            SET {column} = ?
            WHERE branch_task_id = ?
            """,
            (value, terminal["branch_task_id"]),
        )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "invalid_operator_admission"


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("protocol_version", 99, "unsupported_protocol"),
        ("receipt_json", "{}", "invalid_operator_admission"),
    ],
)
def test_noncompacted_corrupt_terminal_row_is_quarantined(
    tmp_path: Path,
    column: str,
    value: object,
    reason: str,
) -> None:
    initialize_author_server(tmp_path)
    terminal = _commit(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    store.transition_task(
        terminal["branch_task_id"],
        expected_statuses={"pending"},
        new_status="succeeded",
        at="2026-07-24T08:01:00+00:00",
    )
    table = (
        "branch_tasks_v2"
        if column == "protocol_version"
        else "request_admissions"
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE branch_task_id = ?",
            (value, terminal["branch_task_id"]),
        )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == reason


def test_disabled_invalid_row_gets_receipt_but_valid_policy_row_does_not(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    valid = _commit(tmp_path)
    invalid = _commit(
        tmp_path,
        key="hmac:disabled-invalid",
        body="sha256:disabled-invalid",
        created_at="2026-07-24T08:00:01+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE branch_tasks_v2 SET disabled = 1 "
            "WHERE branch_task_id IN (?, ?)",
            (valid["branch_task_id"], invalid["branch_task_id"]),
        )
        conn.execute(
            "UPDATE request_admissions SET receipt_json = '{}' "
            "WHERE branch_task_id = ?",
            (invalid["branch_task_id"],),
        )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine()

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].branch_task_id == invalid["branch_task_id"]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        valid_source = conn.execute(
            """
            SELECT disabled, quarantine_reason
            FROM branch_tasks_v2 WHERE branch_task_id = ?
            """,
            (valid["branch_task_id"],),
        ).fetchone()
        valid_receipts = conn.execute(
            """
            SELECT COUNT(*) FROM branch_tasks_v2_quarantine
            WHERE branch_task_id = ?
            """,
            (valid["branch_task_id"],),
        ).fetchone()[0]
    assert valid_source == (1, "")
    assert valid_receipts == 0


def test_maintenance_hard_caps_caller_requested_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_author_server(tmp_path)
    for index in range(3):
        _commit(
            tmp_path,
            key=f"hmac:hard-cap-{index}",
            body=f"sha256:hard-cap-{index}",
            created_at=f"2026-07-24T08:00:{index:02d}+00:00",
        )
    monkeypatch.setattr(
        request_admission_storage,
        "MAX_QUARANTINE_SCAN_ROWS",
        2,
    )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine(
        limit=1_000_000,
    )

    assert result.health == "green"
    assert result.scanned == 2


def test_storage_rejects_unbounded_private_quarantine_reason(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    task = _commit(tmp_path)
    store = RequestAdmissionStore(tmp_path)
    private_reason = "repair the queue bearer-secret authorization evidence"

    with pytest.raises(ValueError, match="unsupported quarantine reason"):
        store.quarantine_task(
            task["branch_task_id"],
            reason=private_reason,
            observed_at="2026-07-24T08:01:00+00:00",
        )
    with pytest.raises(ValueError, match="unsupported quarantine reason"):
        store.maintain_v2_quarantine(
            classifier=lambda _row: private_reason,
        )

    with sqlite3.connect(db_path(tmp_path)) as conn:
        source = conn.execute(
            """
            SELECT disabled, quarantine_reason
            FROM branch_tasks_v2 WHERE branch_task_id = ?
            """,
            (task["branch_task_id"],),
        ).fetchone()
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM branch_tasks_v2_quarantine"
        ).fetchone()[0]
    assert source == (0, "")
    assert receipt_count == 0


def test_corrupt_status_is_scanned_and_sanitized(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    private_status = "raw request text with bearer-secret"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET status = ?
            WHERE branch_task_id = ?
            """,
            (private_status, invalid["branch_task_id"]),
        )

    result = Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine(limit=1)

    assert result.health == "green"
    assert result.quarantined == 1
    assert result.receipts[0].reason == "incomplete"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        row_json = conn.execute(
            "SELECT row_json FROM branch_tasks_v2_quarantine"
        ).fetchone()[0]
    assert private_status not in row_json


def test_cycle_high_water_prevents_starvation_under_sustained_inserts(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    rows = [
        _commit(
            tmp_path,
            key=f"hmac:high-water-{index}",
            body=f"sha256:high-water-{index}",
            created_at=f"2026-07-24T08:00:{index:02d}+00:00",
        )
        for index in range(3)
    ]
    adapter = Epoch2BranchTaskAdapter(tmp_path)
    assert adapter.maintain_quarantine(limit=1).scanned == 1
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE request_admissions SET receipt_json = '{}' "
            "WHERE branch_task_id = ?",
            (rows[0]["branch_task_id"],),
        )

    results = []
    for index in range(3, 6):
        _commit(
            tmp_path,
            key=f"hmac:high-water-{index}",
            body=f"sha256:high-water-{index}",
            created_at=f"2026-07-24T08:00:{index:02d}+00:00",
        )
        results.append(adapter.maintain_quarantine(limit=1))

    receipts = [
        receipt
        for result in results
        for receipt in result.receipts
    ]
    assert [receipt.branch_task_id for receipt in receipts] == [
        rows[0]["branch_task_id"]
    ]


def test_bounded_maintenance_cursor_reaches_invalid_row_after_valid_rows(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    for index in range(4):
        _commit(
            tmp_path,
            key=f"hmac:cursor-{index}",
            body=f"sha256:cursor-{index}",
            created_at=f"2026-07-24T08:00:{index:02d}+00:00",
        )
    invalid = _commit(
        tmp_path,
        key="hmac:cursor-invalid",
        body="sha256:cursor-invalid",
        created_at="2026-07-24T08:00:05+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE branch_tasks_v2 SET protocol_version = 99
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    first = adapter.maintain_quarantine(limit=2)
    second = adapter.maintain_quarantine(limit=2)
    third = adapter.maintain_quarantine(limit=2)

    assert (first.scanned, first.quarantined) == (2, 0)
    assert (second.scanned, second.quarantined) == (2, 0)
    assert (third.scanned, third.quarantined) == (1, 1)
    assert third.receipts[0].branch_task_id == invalid["branch_task_id"]


def test_concurrent_maintenance_writes_one_receipt_per_digest(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    invalid = _commit(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            UPDATE branch_tasks_v2
            SET queue_epoch = 3
            WHERE branch_task_id = ?
            """,
            (invalid["branch_task_id"],),
        )
    barrier = threading.Barrier(3)
    results = []
    failures = []

    def maintain() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                Epoch2BranchTaskAdapter(tmp_path).maintain_quarantine()
            )
        except Exception as exc:  # pragma: no cover - diagnostic capture
            failures.append(exc)

    threads = [
        threading.Thread(target=maintain),
        threading.Thread(target=maintain),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    assert failures == []
    assert len(results) == 2
    assert all(result.health == "green" for result in results)
    assert sum(result.quarantined for result in results) == 1
    with sqlite3.connect(db_path(tmp_path)) as conn:
        receipt = conn.execute(
            """
            SELECT seen_count
            FROM branch_tasks_v2_quarantine
            """
        ).fetchone()
    assert receipt == (1,)


def test_operational_snapshot_separates_inert_and_waiting_rows(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    waiting = _commit(
        tmp_path,
        key="waiting",
        created_at="2026-07-24T08:00:00+00:00",
    )
    parked = _commit(
        tmp_path,
        key="parked",
        created_at="2026-07-24T08:01:00+00:00",
    )
    quarantined = _commit(
        tmp_path,
        key="quarantined",
        created_at="2026-07-24T08:02:00+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE branch_tasks_v2 SET disabled = 1 "
            "WHERE branch_task_id = ?",
            (parked["branch_task_id"],),
        )
        conn.execute(
            "UPDATE request_admissions SET receipt_json = '{}' "
            "WHERE branch_task_id = ?",
            (quarantined["branch_task_id"],),
        )

    clock = _MutableClock("2026-07-24T08:10:00+00:00")
    adapter = Epoch2BranchTaskAdapter(tmp_path, clock=clock)
    maintenance = adapter.maintain_quarantine()
    assert maintenance.quarantined == 1
    forged = _commit(
        tmp_path,
        key="forged",
        created_at="2026-07-24T08:03:00+00:00",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE request_admissions SET receipt_json = '{}' "
            "WHERE branch_task_id = ?",
            (forged["branch_task_id"],),
        )
        conn.execute(
            "UPDATE branch_tasks_v2 SET universe_id = 'universe-b' "
            "WHERE branch_task_id = ?",
            (forged["branch_task_id"],),
        )

    snapshot = adapter.operational_snapshot(
        universe_id="universe-a",
        compatible_capacity=False,
    )

    assert snapshot["queue_epoch"] == 2
    assert snapshot["depth"] == 4
    assert snapshot["lifecycle_counts"]["pending"] == 4
    assert sum(snapshot["lifecycle_counts"].values()) == snapshot["depth"]
    assert snapshot["valid_pending_count"] == 1
    assert snapshot["eligible_pending_count"] == 0
    assert snapshot["operational_state_counts"] == {
        "awaiting_compatible_capacity": 1,
        "invalid_operator_admission": 1,
        "quarantined": 1,
        "policy_parked": 1,
    }
    assert snapshot["operational_reason_counts"] == {
        "awaiting_compatible_capacity": {
            "no_live_compatible_worker": 1,
        },
        "invalid_operator_admission": {
            "invalid_operator_admission": 1,
        },
        "quarantined": {
            "invalid_operator_admission": 1,
        },
        "policy_parked": {"disabled": 1},
    }
    assert snapshot["operational_oldest_age_s"] == {
        "awaiting_compatible_capacity": 600,
        "invalid_operator_admission": 420,
        "quarantined": 480,
        "policy_parked": 540,
    }
    diagnostics = {
        item["operational_state"]: item
        for item in snapshot["diagnostics"]
    }
    assert diagnostics["awaiting_compatible_capacity"][
        "branch_task_id"
    ] == waiting["branch_task_id"]
    assert diagnostics["policy_parked"]["branch_task_id"] == (
        parked["branch_task_id"]
    )
    assert diagnostics["quarantined"]["row_digest"] == (
        maintenance.receipts[0].row_digest
    )
    assert len(diagnostics["invalid_operator_admission"]["row_digest"]) == 64
    serialized = json.dumps(snapshot, sort_keys=True)
    assert "repair the queue" not in serialized
    assert "request-local" not in serialized
    assert "tenant-a" not in serialized
    other_universe = adapter.operational_snapshot(
        universe_id="universe-b",
        compatible_capacity=False,
    )
    assert other_universe["depth"] == 0
    assert sum(other_universe["lifecycle_counts"].values()) == 0

    with_capacity = adapter.operational_snapshot(
        universe_id="universe-a",
        compatible_capacity=True,
    )
    assert (
        with_capacity["operational_state_counts"][
            "awaiting_compatible_capacity"
        ]
        == 0
    )
    assert with_capacity["lifecycle_counts"]["pending"] == 4
    assert with_capacity["valid_pending_count"] == 1
    assert with_capacity["eligible_pending_count"] == 1
    invalid_digest = diagnostics["invalid_operator_admission"]["row_digest"]
    followup = adapter.maintain_quarantine()
    assert followup.quarantined == 1
    assert followup.receipts[0].branch_task_id == forged["branch_task_id"]
    assert followup.receipts[0].row_digest == invalid_digest

    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE branch_tasks_v2 SET disabled = 0 "
            "WHERE branch_task_id = ?",
            (forged["branch_task_id"],),
        )
    inconsistent = adapter.operational_snapshot(
        universe_id="universe-a",
        compatible_capacity=True,
    )
    assert inconsistent["operational_state_counts"][
        "invalid_operator_admission"
    ] == 1
    assert forged["branch_task_id"] not in {
        task.branch_task_id
        for task in adapter.list_candidates(universe_id="universe-a")
    }


def test_operational_capacity_is_evaluated_per_pending_task(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    ordinary = _commit(tmp_path, key="ordinary-capacity")
    daemon = create_daemon(
        tmp_path,
        display_name="Directed capacity target",
        created_by="actor-a",
        soul_mode="soul",
        soul_text="A stable directed capacity target.",
    )
    directed = _commit(
        tmp_path,
        key="directed-capacity",
        directed_daemon_id=daemon["daemon_id"],
        directed_soul_hash=daemon["soul_hash"],
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    read = adapter.operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda task: not task.directed_daemon_id,
    )

    assert read.summary["lifecycle_counts"]["pending"] == 2
    assert read.summary["valid_pending_count"] == 2
    assert read.summary["eligible_pending_count"] == 1
    assert read.summary["operational_state_counts"][
        "awaiting_compatible_capacity"
    ] == 1
    assert {
        task.branch_task_id for task in read.candidates
    } == {
        ordinary["branch_task_id"],
        directed["branch_task_id"],
    }


def test_operational_scope_uses_admission_then_request_fallback(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    mismatched = _commit(tmp_path, key="mismatched-linked-universes")
    request_fallback = _commit(tmp_path, key="request-universe-fallback")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE user_requests SET universe_id = 'universe-b' "
            "WHERE request_id = ?",
            (mismatched["request_id"],),
        )
        conn.execute(
            "DELETE FROM request_admissions WHERE admission_id = ?",
            (request_fallback["admission_id"],),
        )

    adapter = Epoch2BranchTaskAdapter(tmp_path)
    universe_a = adapter.operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: True,
    )
    universe_b = adapter.operational_read(
        universe_id="universe-b",
        capacity_matcher=lambda _task: True,
    )

    assert universe_a.summary["depth"] == 2
    assert universe_a.summary["operational_state_counts"][
        "invalid_operator_admission"
    ] == 2
    assert universe_b.summary["depth"] == 0


def test_operational_read_surfaces_unknown_and_unscoped_rows_as_incomplete(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    unknown_status = _commit(tmp_path, key="unknown-lifecycle-status")
    unscoped = _commit(tmp_path, key="unscoped-missing-links")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "UPDATE branch_tasks_v2 SET status = 'forged' "
            "WHERE branch_task_id = ?",
            (unknown_status["branch_task_id"],),
        )
        conn.execute(
            "DELETE FROM request_admissions WHERE admission_id = ?",
            (unscoped["admission_id"],),
        )
        conn.execute(
            "DELETE FROM user_requests WHERE request_id = ?",
            (unscoped["request_id"],),
        )

    adapter = Epoch2BranchTaskAdapter(tmp_path)
    owner_read = adapter.operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: True,
        include_unscoped_invalid=True,
    )
    public_read = adapter.operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: True,
        include_unscoped_invalid=False,
    )

    assert owner_read.summary["depth"] == 1
    assert owner_read.summary["lifecycle_counts"]["unknown"] == 1
    assert sum(owner_read.summary["lifecycle_counts"].values()) == 1
    assert owner_read.summary["unknown_lifecycle_status_counts"] == {
        "unknown": 1,
    }
    assert owner_read.summary["operational_state_counts"][
        "invalid_operator_admission"
    ] == 1
    assert owner_read.summary["unscoped_invalid_count"] == 1
    assert owner_read.summary["integrity_scope_complete"] is False
    assert owner_read.summary["operational_counts_authoritative"] is False
    assert owner_read.summary["diagnostics"][0]["branch_task_id"] == (
        unknown_status["branch_task_id"]
    )
    assert "unscoped_invalid_count" not in public_read.summary
    assert public_read.summary["integrity_scope_complete"] is False
    assert public_read.summary["operational_counts_authoritative"] is False

    maintenance = adapter.maintain_quarantine()
    after_quarantine = adapter.operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: True,
        include_unscoped_invalid=True,
    )

    assert maintenance.quarantined == 2
    assert after_quarantine.summary["depth"] == 1
    assert after_quarantine.summary["lifecycle_counts"]["unknown"] == 1
    assert after_quarantine.summary["operational_state_counts"][
        "quarantined"
    ] == 1
    assert after_quarantine.summary["unscoped_invalid_count"] == 1
    assert (
        after_quarantine.summary["operational_counts_authoritative"]
        is False
    )


def test_unknown_lifecycle_status_output_is_fixed_and_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    initialize_author_server(tmp_path)
    committed = [
        _commit(tmp_path, key=f"forged-status-{index}")
        for index in range(25)
    ]
    private_status = "PRIVATE-STATUS-SENTINEL-" + ("x" * 4096)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        for index, row in enumerate(committed):
            conn.execute(
                "UPDATE branch_tasks_v2 SET status = ? "
                "WHERE branch_task_id = ?",
                (f"{private_status}-{index}", row["branch_task_id"]),
            )
    monkeypatch.setattr(
        request_admission_storage,
        "MAX_OPERATIONAL_SCAN_ROWS",
        5,
    )

    read = Epoch2BranchTaskAdapter(tmp_path).operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: True,
    )
    serialized = json.dumps(read.summary, sort_keys=True)

    assert read.summary["depth"] == 25
    assert read.summary["lifecycle_counts"]["unknown"] == 25
    assert read.summary["unknown_lifecycle_status_counts"] == {
        "unknown": 25,
    }
    assert read.summary["operational_state_counts"][
        "invalid_operator_admission"
    ] == 5
    assert read.summary["unclassified_active_count"] == 20
    assert len(read.summary["diagnostics"]) == 5
    assert read.summary["diagnostics_truncated"] is True
    assert read.summary["operational_counts_authoritative"] is False
    assert "PRIVATE-STATUS-SENTINEL" not in serialized
    assert len(serialized) < 20_000


def test_public_operational_read_uses_existence_not_exact_unscoped_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    initialize_author_server(tmp_path)
    _commit(tmp_path, key="unscoped-query-shape")
    store = RequestAdmissionStore(tmp_path)
    original_connection = store.connection
    statements: list[str] = []

    @contextmanager
    def traced_connection():
        with original_connection() as conn:
            conn.set_trace_callback(statements.append)
            yield conn
            conn.set_trace_callback(None)

    monkeypatch.setattr(store, "connection", traced_connection)

    store.read_v2_operational_data(
        universe_id="universe-a",
        include_unscoped_invalid=False,
    )
    public_sql = "\n".join(
        " ".join(statement.split()) for statement in statements
    )
    assert "SELECT 1 FROM branch_tasks_v2 AS t" in public_sql
    assert "SELECT COUNT(*) FROM branch_tasks_v2 AS t" not in public_sql

    statements.clear()
    store.read_v2_operational_data(
        universe_id="universe-a",
        include_unscoped_invalid=True,
    )
    admin_sql = "\n".join(
        " ".join(statement.split()) for statement in statements
    )
    assert "SELECT COUNT(*) FROM branch_tasks_v2 AS t" in admin_sql


def test_operational_counts_and_candidates_share_one_sqlite_snapshot(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    committed = _commit(tmp_path, key="snapshot-consistency")
    start_write = threading.Event()
    write_done = threading.Event()
    failures: list[Exception] = []

    def transition() -> None:
        try:
            assert start_write.wait(timeout=5)
            with sqlite3.connect(db_path(tmp_path)) as conn:
                conn.execute(
                    "UPDATE branch_tasks_v2 SET status = 'running' "
                    "WHERE branch_task_id = ?",
                    (committed["branch_task_id"],),
                )
                conn.execute(
                    "UPDATE user_requests SET status = 'running' "
                    "WHERE request_id = ?",
                    (committed["request_id"],),
                )
            write_done.set()
        except Exception as exc:  # pragma: no cover - diagnostic capture
            failures.append(exc)
            write_done.set()

    writer = threading.Thread(target=transition)
    writer.start()

    def after_lifecycle_counts() -> None:
        start_write.set()
        assert write_done.wait(timeout=5)

    read = Epoch2BranchTaskAdapter(tmp_path).operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: True,
        snapshot_hook=after_lifecycle_counts,
    )
    writer.join(timeout=5)

    assert failures == []
    assert read.summary["lifecycle_counts"]["pending"] == 1
    assert read.summary["lifecycle_counts"]["running"] == 0
    assert [task.branch_task_id for task in read.candidates] == [
        committed["branch_task_id"]
    ]


def test_operational_diagnostics_are_output_and_scan_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    initialize_author_server(tmp_path)
    for index in range(105):
        _commit(
            tmp_path,
            key=f"bounded-{index}",
            created_at=(
                datetime.fromisoformat("2026-07-24T08:00:00+00:00")
                + timedelta(seconds=index)
            ).isoformat(),
        )
    monkeypatch.setattr(
        request_admission_storage,
        "MAX_OPERATIONAL_SCAN_ROWS",
        5,
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    read = adapter.operational_read(
        universe_id="universe-a",
        capacity_matcher=lambda _task: False,
    )

    assert read.summary["lifecycle_counts"]["pending"] == 105
    assert read.summary["active_scan_limit"] == 5
    assert read.summary["operational_counts_authoritative"] is False
    assert read.summary["unclassified_active_count"] == 100
    assert len(read.summary["diagnostics"]) == 5
    assert read.summary["diagnostics_truncated"] is True
