"""Negatives for the three authority sites task 8/9 left ungated.

Each test drives the real caller under an **injected** process observation —
never an environment variable, never a network read — and asserts the guarded
path refuses. The provider negative spies the provider invocation itself rather
than the returned text, because a refusal that still called the model is not a
refusal.

Admission is never inherited here. Every test installs its own observation
through the module-local `admitted_runtime` fixture or `_install_observation`,
so no suite-wide admitted default can make a negative vacuous. Nothing in
this module is autouse.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tinyassets import daemon_registry
from tinyassets import platform_runtime_provenance as provenance
from tinyassets.branch_tasks_v2 import Epoch2BranchTaskAdapter, WorkerClaimDescriptor
from tinyassets.daemon_registry import (
    create_daemon,
    ensure_daemon_runtime,
    set_worker_queue_descriptor,
)
from tinyassets.daemon_server import initialize_author_server
from tinyassets.storage.automation_activations import AutomationActivationExecutor


def _install_observation(monkeypatch, verdict: str | None) -> None:
    """Inject the process-owned observation. `None` = never observed."""
    observation = provenance.ProcessProvenanceObservation(
        resolver=lambda: provenance.RuntimeProvenance(
            verdict or provenance.NOT_CLOUD, "instance_mismatch", True, True
        )
    )
    if verdict is not None:
        observation.observe()
    monkeypatch.setattr(provenance, "_PROCESS_OBSERVATION", observation)


@pytest.fixture
def admitted_runtime(monkeypatch):
    """An admitted cloud process, injected — the baseline every control needs."""
    _install_observation(monkeypatch, provenance.CLOUD)


@pytest.fixture
def refused_runtime(monkeypatch):
    """A process that looked and is NOT cloud."""
    _install_observation(monkeypatch, provenance.NOT_CLOUD)


@pytest.fixture
def unobserved_runtime(monkeypatch):
    """A process that never looked. `peek()` is None; that is not cloud."""
    _install_observation(monkeypatch, None)


# --- Site 1 (the agent runtime's provider execution) was retired with that
# runtime; its admission gate had no other caller.


# --- Site 2: worker queue descriptor publication/refresh -------------------


def _seed_admitted_worker(base_path: Path) -> dict:
    daemon = create_daemon(
        base_path,
        display_name="Descriptor Runner",
        created_by="owner-a",
        soul_text="Run the owner's versioned Branch composition.",
    )
    return ensure_daemon_runtime(
        base_path,
        daemon_id=daemon["daemon_id"],
        universe_id="universe-a",
        provider_name="codex",
        model_name="gpt-5",
        created_by="cloud-worker",
        worker_id="worker-a",
        metadata={"automation_executor_class": "cloud"},
    )


def _descriptor(runtime: dict, *, seconds: int = 75, boot_id: str = "boot-a") -> dict:
    return {
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "worker_id": "worker-a",
        "runtime_instance_id": runtime["runtime_instance_id"],
        "boot_id": boot_id,
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + ("b" * 64),
        "universe_id": "universe-a",
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=seconds)
        ).isoformat(),
    }


@pytest.mark.parametrize("observation", ["refused", "unobserved"])
def test_unadmitted_process_cannot_renew_a_cloud_descriptor(
    tmp_path, monkeypatch, admitted_runtime, observation
) -> None:
    """Row-mint is admitted; row-REFRESH must be too, or the window is renewable."""
    initialize_author_server(tmp_path)
    runtime = _seed_admitted_worker(tmp_path)
    runtime_id = runtime["runtime_instance_id"]
    set_worker_queue_descriptor(
        tmp_path,
        runtime_instance_id=runtime_id,
        descriptor=_descriptor(runtime),
        expected_worker_id="worker-a",
    )
    before = daemon_registry.daemon_server.get_runtime_instance(
        tmp_path, instance_id=runtime_id
    )
    before_descriptor = dict(
        before["metadata"]["queue_protocol_descriptor"]
    )

    _install_observation(
        monkeypatch, provenance.NOT_CLOUD if observation == "refused" else None
    )

    with pytest.raises(PermissionError) as refusal:
        set_worker_queue_descriptor(
            tmp_path,
            runtime_instance_id=runtime_id,
            descriptor=_descriptor(runtime, seconds=900, boot_id="boot-hijack"),
            expected_worker_id="worker-a",
        )
    assert provenance.PLATFORM_NOT_CLOUD_REASON in str(refusal.value)

    after = daemon_registry.daemon_server.get_runtime_instance(
        tmp_path, instance_id=runtime_id
    )
    # The expiry window is exactly what an unadmitted renewal would extend.
    assert after["metadata"]["queue_protocol_descriptor"] == before_descriptor


@pytest.mark.parametrize("observation", ["refused", "unobserved"])
def test_unchanged_descriptor_fast_return_is_also_gated(
    tmp_path, monkeypatch, admitted_runtime, observation
) -> None:
    """The no-op equality return is still an authority-bearing refresh path.

    Left ungated it would report success to an unadmitted caller, which is a
    liveness assertion about a runtime that process has no authority over.
    """
    initialize_author_server(tmp_path)
    runtime = _seed_admitted_worker(tmp_path)
    runtime_id = runtime["runtime_instance_id"]
    descriptor = _descriptor(runtime)
    set_worker_queue_descriptor(
        tmp_path,
        runtime_instance_id=runtime_id,
        descriptor=descriptor,
        expected_worker_id="worker-a",
    )

    _install_observation(
        monkeypatch, provenance.NOT_CLOUD if observation == "refused" else None
    )

    with pytest.raises(PermissionError):
        set_worker_queue_descriptor(
            tmp_path,
            runtime_instance_id=runtime_id,
            descriptor=dict(descriptor),
            expected_worker_id="worker-a",
        )


def test_clearing_a_descriptor_stays_allowed_for_shutdown_cleanup(
    tmp_path, monkeypatch, admitted_runtime
) -> None:
    """Revocation direction-check: clearing REMOVES authority, so it is not gated.

    A process that has lost admission must still be able to withdraw its own
    claim; refusing the clear would strand a live-looking descriptor for the
    remainder of its validity window — the opposite of the invariant.
    """
    initialize_author_server(tmp_path)
    runtime = _seed_admitted_worker(tmp_path)
    runtime_id = runtime["runtime_instance_id"]
    set_worker_queue_descriptor(
        tmp_path,
        runtime_instance_id=runtime_id,
        descriptor=_descriptor(runtime),
        expected_worker_id="worker-a",
    )

    _install_observation(monkeypatch, provenance.NOT_CLOUD)

    cleared = set_worker_queue_descriptor(
        tmp_path,
        runtime_instance_id=runtime_id,
        descriptor=None,
        expected_worker_id="worker-a",
    )
    assert cleared["metadata"]["queue_protocol_descriptor"] is None


# --- Site 3: Epoch2 cloud-activation claim ---------------------------------


def _activation_descriptor(
    executor_class: AutomationActivationExecutor,
    *,
    worker_id: str = "worker-a",
) -> WorkerClaimDescriptor:
    return WorkerClaimDescriptor(
        queue_protocol_version=2,
        capabilities=frozenset({"operator_request_v1"}),
        worker_id=worker_id,
        runtime_instance_id=f"runtime::{worker_id}",
        boot_id=f"boot::{worker_id}",
        build_sha="a" * 40,
        config_hash="sha256:" + ("b" * 64),
        universe_id="universe-a",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=75)
        ).isoformat(),
        executor_class=executor_class,
    )








def test_non_cloud_generic_claim_is_unaffected_by_admission(
    tmp_path, monkeypatch, admitted_runtime
) -> None:
    """Preserved semantics: a task with no activation fields is not cloud-class.

    The guard belongs to the cloud-activation arm only; widening it to every
    claim would change generic queue behaviour, which this change does not own.
    """
    import rfc8785

    from tinyassets.storage.request_admissions import (
        IDEMPOTENCY_HMAC_ENV,
        RequestAdmissionStore,
        mint_idempotency_key_hash,
    )

    initialize_author_server(tmp_path)
    monkeypatch.setenv(IDEMPOTENCY_HMAC_ENV, "k" * 48)
    body = rfc8785.dumps(
        {
            "branch_id": "",
            "directed_daemon_id": "",
            "directed_daemon_instruction": "",
            "pickup_incentive": "",
            "priority_weight": 50.0,
            "request_type": "general",
            "schema_version": "request-admission-v2",
            "text": "execute one bounded branch slice",
            "universe_id": "universe-a",
        }
    )
    committed = RequestAdmissionStore(tmp_path).commit_admission(
        tenant_id="tenant-a",
        actor_id="actor-a",
        universe_id="universe-a",
        idempotency_key_hash=mint_idempotency_key_hash("generic-claim"),
        body_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="execute one bounded branch slice",
        branch_id="",
        branch_def_id="ordinary-user-branch",
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
        created_at=datetime.now(timezone.utc).isoformat(),
        automation_activation=None,
    )
    live = WorkerClaimDescriptor(
        queue_protocol_version=2,
        capabilities=frozenset({"operator_request_v1"}),
        worker_id="worker-a",
        runtime_instance_id="runtime::worker-a",
        boot_id="boot::worker-a",
        build_sha="a" * 40,
        config_hash="sha256:" + ("b" * 64),
        universe_id="universe-a",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=75)
        ).isoformat(),
        executor_class=None,
    )
    adapter = Epoch2BranchTaskAdapter(tmp_path)

    _install_observation(monkeypatch, provenance.NOT_CLOUD)

    claimed = adapter.claim(
        committed["branch_task_id"],
        descriptor=live,
        descriptor_reader=lambda _conn, _worker: live,
    )

    assert claimed is not None




