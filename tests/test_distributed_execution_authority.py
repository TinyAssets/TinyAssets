from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import rfc8785

from tinyassets.branch_tasks_v2 import (
    Epoch2BranchTaskAdapter,
    WorkerClaimDescriptor,
)
from tinyassets.daemon_server import initialize_author_server
from tinyassets.storage.request_admissions import RequestAdmissionStore
from tinyassets.work_targets import (
    list_selectable_targets,
    materialize_pending_requests,
)


def _commit_admission(base_path: Path) -> dict:
    body = rfc8785.dumps(
        {
            "branch_id": "",
            "directed_daemon_id": "",
            "directed_daemon_instruction": "",
            "pickup_incentive": "",
            "priority_weight": 50.0,
            "request_type": "general",
            "schema_version": "request-admission-v2",
            "text": "repair the queue",
            "universe_id": "universe-a",
        }
    )
    return RequestAdmissionStore(base_path).commit_admission(
        tenant_id="tenant-a",
        actor_id="actor-a",
        universe_id="universe-a",
        idempotency_key_hash=("hmac-sha256:" + hashlib.sha256(b"authority-test").hexdigest()),
        body_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="repair the queue",
        branch_id="",
        branch_def_id="loop-branch",
        trigger_source="operator_request",
        accepted_priority_weight=50.0,
        policy_version="operator-priority-v1",
        grant_generation=3,
        receipt={
            "authority": "request-local",
            "grant_generation": 3,
            "priority_policy_version": "operator-priority-v1",
            "directed_assignment": {},
        },
        directed_daemon_id="",
        created_at="2026-07-24T08:00:00Z",
    )


def _descriptor() -> WorkerClaimDescriptor:
    return WorkerClaimDescriptor(
        queue_protocol_version=2,
        capabilities=frozenset({"operator_request_v1"}),
        worker_id="worker-a",
        runtime_instance_id="runtime-a",
        boot_id="boot-a",
        build_sha="a" * 40,
        config_hash="b" * 64,
        universe_id="universe-a",
        expires_at="2026-07-24T08:02:15Z",
    )


def test_epoch2_claim_enters_execution_for_its_claiming_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fantasy_daemon.branch_registrations import _restartable_work_exists

    universe_path = tmp_path / "universe-a"
    universe_path.mkdir()
    initialize_author_server(tmp_path)
    admission = _commit_admission(tmp_path)
    claim_time = datetime.now(timezone.utc)
    descriptor = replace(
        _descriptor(),
        expires_at=(claim_time + timedelta(seconds=75)).isoformat(),
    )
    adapter = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=lambda: claim_time,
    )

    claimed = adapter.claim(
        admission["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    )

    assert claimed is not None
    assert claimed.status == "running"
    heartbeat = adapter.heartbeat(
        admission["branch_task_id"],
        worker_id=descriptor.worker_id,
    )
    assert heartbeat is not None
    assert heartbeat.heartbeat_at

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_WORKER_ID", descriptor.worker_id)

    # `64f27fe7` (#2182) wired the epoch-2 Branch consumer, so the gate this
    # test was built around is open: a claim held by THIS worker now enters
    # execution instead of being withheld. Previously all three were empty or
    # False. Assert the replacement contract rather than dropping the coverage.
    materialized = materialize_pending_requests(universe_path)
    assert len(materialized) == 1
    target = materialized[0]
    # It must be exactly this admission, claimed by exactly this worker.
    assert target.metadata["branch_task_id"] == admission["branch_task_id"]
    assert target.metadata["admission_id"] == admission["admission_id"]
    assert target.metadata["claimed_by"] == descriptor.worker_id
    assert target.metadata["queue_epoch"] == 2

    selectable = list_selectable_targets(universe_path)
    assert [t.target_id for t in selectable] == [target.target_id]

    assert _restartable_work_exists(universe_path) is True
