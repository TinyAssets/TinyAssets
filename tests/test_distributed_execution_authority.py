from __future__ import annotations

import hashlib
from pathlib import Path

import rfc8785

from tinyassets.branch_tasks_v2 import (
    WorkerClaimDescriptor,
)
from tinyassets.storage.request_admissions import RequestAdmissionStore


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


