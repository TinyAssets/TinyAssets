from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
import rfc8785

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import daemon_registry
from tinyassets.daemon_server import initialize_author_server
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.storage import db_path
from tinyassets.storage.automation_activations import (
    AutomationActivation,
    AutomationActivationExecutor,
    AutomationActivationStore,
)
from tinyassets.storage.request_admissions import RequestAdmissionStore

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def _activation_subject(ref: str) -> ExecutionSubject:
    return ExecutionSubject(
        kind=ExecutionSubjectKind.BRANCH_VERSION,
        ref=ref,
        digest=f"sha256:{hashlib.sha256(ref.encode()).hexdigest()}",
    )


def _activation(
    base_path: Path,
    *,
    automation_id: str,
    executor_class: AutomationActivationExecutor,
) -> AutomationActivation:
    store = AutomationActivationStore(base_path)
    stopped = store.create_stopped(
        universe_id="universe-a",
        automation_id=automation_id,
    )
    active = store.activate(
        expected=stopped,
        executor_class=executor_class,
        subject=_activation_subject(f"branch-version-{automation_id}"),
        lease_id=f"lease-{automation_id}",
    )
    assert active is not None
    return active


def _commit_task(
    base_path: Path,
    *,
    key: str,
    created_at: str,
    activation: AutomationActivation,
) -> dict:
    key_hash = "sha256:" + hashlib.sha256(key.encode()).hexdigest()
    body = rfc8785.dumps({
        "branch_id": "",
        "directed_daemon_id": "",
        "directed_daemon_instruction": "",
        "pickup_incentive": "",
        "priority_weight": 50.0,
        "request_type": "general",
        "schema_version": "request-admission-v2",
        "text": f"payload-{key}",
        "universe_id": "universe-a",
    })
    return RequestAdmissionStore(base_path).commit_admission(
        tenant_id="tenant-a",
        actor_id="actor-a",
        universe_id="universe-a",
        idempotency_key_hash=key_hash,
        body_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        body_digest_version="rfc8785-v1",
        request_type="general",
        text=f"payload-{key}",
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
        created_at=created_at,
        automation_activation=activation,
    )








def _cloud_runtime(
    base_path: Path,
    *,
    daemon_id: str,
    worker_id: str,
    provider_name: str,
) -> dict:
    return daemon_registry.ensure_daemon_runtime(
        base_path,
        daemon_id=daemon_id,
        universe_id="universe-a",
        provider_name=provider_name,
        model_name=f"model-{worker_id}",
        created_by="fleet-test",
        worker_id=worker_id,
    )










def _stale_fleet_fixture(base_path: Path) -> tuple[dict, dict]:
    initialize_author_server(base_path)
    cloud = _activation(
        base_path,
        automation_id="cli-cloud",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    task = _commit_task(
        base_path,
        key="cli-stale-cloud",
        created_at="2026-07-01T00:00:00+00:00",
        activation=cloud,
    )
    daemon = daemon_registry.create_daemon(
        base_path,
        display_name="CLI stale fleet daemon",
        created_by="actor-a",
        soul_mode="soulless",
    )
    runtime = _cloud_runtime(
        base_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-cli-stale",
        provider_name="codex",
    )
    # SQLite's connection context commits but does not close. Close the setup
    # writer before callers snapshot bytes, so later GC cannot checkpoint its
    # WAL during a read-only CLI call and look like a reconciler write.
    with closing(sqlite3.connect(db_path(base_path))) as conn, conn:
        conn.execute(
            "UPDATE author_runtime_instances SET updated_at = ? "
            "WHERE instance_id = ?",
            (
                datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp(),
                runtime["runtime_instance_id"],
            ),
        )
    return task, runtime




_REPO_ROOT = Path(__file__).resolve().parents[1]

# A spawned child inherits no monkeypatch and, by design, no cached verdict. The
# CLI coverage here stays a real subprocess -- the seam is one explicit test-only
# line the child runs before `main`, installing the SAME injected fake resolver
# `cloud_runtime` uses in-process. No environment variable, no production bypass
# and no network: an env switch would be reachable from a real deployment's
# env_file, whereas this is only reachable by a caller that imports `tests`.
# Kept on ONE line: cmd.exe truncates argv at a newline on this host.
_ADMITTED_CLI_BOOTSTRAP = (
    "import sys; "
    "from tests.cloud_runtime_fixture import install_admitted_observation; "
    "install_admitted_observation(); "
    "from tinyassets.runtime_reconcile import main; "
    "raise SystemExit(main(sys.argv[1:]))"
)


def _run_reconciler(base_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _ADMITTED_CLI_BOOTSTRAP,
            "stale-fleet",
            "--data-dir",
            str(base_path),
            "--older-than-hours",
            "24",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )








