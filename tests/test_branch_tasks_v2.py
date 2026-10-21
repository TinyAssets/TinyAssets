from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest
import rfc8785

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.branch_tasks_v2 import (
    Epoch2BranchTaskAdapter,
    WorkerClaimDescriptor,
)
from tinyassets.daemon_server import initialize_author_server
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.storage.automation_activations import (
    AutomationActivation,
    AutomationActivationExecutor,
)
from tinyassets.storage.request_admissions import (
    RequestAdmissionStore,
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




@pytest.fixture
def epoch2(
    tmp_path: Path,
) -> tuple[Epoch2BranchTaskAdapter, dict, _MutableClock]:
    initialize_author_server(tmp_path)
    committed = _commit(tmp_path)
    clock = _MutableClock("2026-07-24T08:01:00+00:00")
    return Epoch2BranchTaskAdapter(tmp_path, clock=clock), committed, clock
































































































































