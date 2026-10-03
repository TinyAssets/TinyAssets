from __future__ import annotations

import gc
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest
import rfc8785

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import daemon_registry, daemon_server
from tinyassets.branch_tasks_v2 import Epoch2BranchTaskAdapter
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


def test_task_planner_selects_only_stale_valid_waiting_cloud_tasks(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    cloud = _activation(
        tmp_path,
        automation_id="cloud",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    tray = _activation(
        tmp_path,
        automation_id="tray",
        executor_class=AutomationActivationExecutor.TRAY,
    )
    stale = _commit_task(
        tmp_path,
        key="stale-cloud",
        created_at="2026-07-01T00:00:00+00:00",
        activation=cloud,
    )
    fresh = _commit_task(
        tmp_path,
        key="fresh-cloud",
        created_at="2026-08-20T00:00:00+00:00",
        activation=cloud,
    )
    foreign = _commit_task(
        tmp_path,
        key="stale-tray",
        created_at="2026-07-01T00:00:01+00:00",
        activation=tray,
    )
    runnable = _commit_task(
        tmp_path,
        key="stale-runnable",
        created_at="2026-07-01T00:00:02+00:00",
        activation=cloud,
    )
    invalid = _commit_task(
        tmp_path,
        key="stale-invalid",
        created_at="2026-07-01T00:00:03+00:00",
        activation=cloud,
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE request_admissions SET receipt_json = '{}' "
            "WHERE branch_task_id = ?",
            (invalid["branch_task_id"],),
        )
    before = db_path(tmp_path).read_bytes()

    planned = Epoch2BranchTaskAdapter(
        tmp_path,
    ).plan_stale_capacity_cancellation(
        cutoff=datetime(2026, 8, 1, tzinfo=timezone.utc),
        capacity_matcher=lambda task: (
            task.branch_task_id == runnable["branch_task_id"]
        ),
        policy_matcher=lambda _task: True,
    )

    assert [item.branch_task_id for item in planned] == [
        stale["branch_task_id"]
    ]
    assert db_path(tmp_path).read_bytes() == before
    with sqlite3.connect(db_path(tmp_path)) as conn:
        statuses = dict(conn.execute(
            "SELECT branch_task_id, status FROM branch_tasks_v2"
        ).fetchall())
    assert statuses[fresh["branch_task_id"]] == "pending"
    assert statuses[foreign["branch_task_id"]] == "pending"
    assert statuses[runnable["branch_task_id"]] == "pending"
    assert statuses[invalid["branch_task_id"]] == "pending"


def test_task_apply_cas_cancels_with_event_and_retains_payload(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    cloud = _activation(
        tmp_path,
        automation_id="cloud",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    committed = _commit_task(
        tmp_path,
        key="stale-cloud",
        created_at="2026-07-01T00:00:00+00:00",
        activation=cloud,
    )
    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    planned = Epoch2BranchTaskAdapter(
        tmp_path,
    ).plan_stale_capacity_cancellation(
        cutoff=cutoff,
        capacity_matcher=lambda _task: False,
        policy_matcher=lambda _task: True,
    )
    assert len(planned) == 1
    item = planned[0]
    store = RequestAdmissionStore(tmp_path)
    assert store.cancel_pending_v2_task_if_stale(
        item.branch_task_id,
        cutoff=cutoff,
        expected_queued_at=item.queued_at,
        expected_grant_generation=item.grant_generation,
        expected_body_digest=item.body_digest,
        expected_row_digest="0" * 64,
        reason="stale_awaiting_compatible_capacity_retired_fleet",
    ) is None

    cancelled = store.cancel_pending_v2_task_if_stale(
        item.branch_task_id,
        cutoff=cutoff,
        expected_queued_at=item.queued_at,
        expected_grant_generation=item.grant_generation,
        expected_body_digest=item.body_digest,
        expected_row_digest=item.row_digest,
        reason="stale_awaiting_compatible_capacity_retired_fleet",
    )

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        task = conn.execute(
            "SELECT status, inputs_json FROM branch_tasks_v2 "
            "WHERE branch_task_id = ?",
            (committed["branch_task_id"],),
        ).fetchone()
        request = conn.execute(
            "SELECT status, text FROM user_requests WHERE request_id = ?",
            (committed["request_id"],),
        ).fetchone()
        admission = conn.execute(
            "SELECT terminal_at, result_json FROM request_admissions "
            "WHERE admission_id = ?",
            (committed["admission_id"],),
        ).fetchone()
        event = conn.execute(
            "SELECT event_type, detail_json FROM request_admission_events "
            "WHERE branch_task_id = ? ORDER BY event_at DESC LIMIT 1",
            (committed["branch_task_id"],),
        ).fetchone()
    assert task[0] == "cancelled"
    assert json.loads(task[1])["request_type"] == "general"
    assert request == ("cancelled", "payload-stale-cloud")
    assert admission[0] is not None
    assert json.loads(admission[1])["branch_task_id"] == committed[
        "branch_task_id"
    ]
    assert event[0] == "cancelled"
    assert json.loads(event[1]) == {
        "reason": "stale_awaiting_compatible_capacity_retired_fleet"
    }


def test_task_apply_cas_rejects_linked_request_tampering(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    cloud = _activation(
        tmp_path,
        automation_id="tamper-cloud",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    committed = _commit_task(
        tmp_path,
        key="tamper-cloud",
        created_at="2026-07-01T00:00:00+00:00",
        activation=cloud,
    )
    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    item = Epoch2BranchTaskAdapter(
        tmp_path,
    ).plan_stale_capacity_cancellation(
        cutoff=cutoff,
        capacity_matcher=lambda _task: False,
        policy_matcher=lambda _task: True,
    )[0]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE user_requests SET text = 'tampered after plan' "
            "WHERE request_id = ?",
            (committed["request_id"],),
        )

    result = RequestAdmissionStore(
        tmp_path,
    ).cancel_pending_v2_task_if_stale(
        item.branch_task_id,
        cutoff=cutoff,
        expected_queued_at=item.queued_at,
        expected_grant_generation=item.grant_generation,
        expected_body_digest=item.body_digest,
        expected_row_digest=item.row_digest,
        reason="stale_awaiting_compatible_capacity_retired_fleet",
    )

    assert result is None
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT status FROM branch_tasks_v2 WHERE branch_task_id = ?",
            (committed["branch_task_id"],),
        ).fetchone()[0] == "pending"


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


def test_runtime_planner_selects_only_stale_unowned_cloud_workers(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Fleet reconciler test daemon",
        created_by="actor-a",
        soul_mode="soulless",
    )
    stale = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-stale",
        provider_name="codex",
    )
    fresh = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-fresh",
        provider_name="claude",
    )
    leased = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-leased",
        provider_name="gemini",
    )
    malformed_lease = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-malformed-lease",
        provider_name="grok",
    )
    foreign = daemon_registry.summon_daemon(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        universe_id="universe-a",
        provider_name="local",
        model_name="model-local",
        created_by="actor-a",
    )
    cloud = _activation(
        tmp_path,
        automation_id="leased-runtime",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    claimed = _commit_task(
        tmp_path,
        key="leased-runtime-task",
        created_at="2026-07-01T00:00:00+00:00",
        activation=cloud,
    )
    malformed_claim = _commit_task(
        tmp_path,
        key="malformed-runtime-task",
        created_at="2026-07-01T00:00:01+00:00",
        activation=cloud,
    )
    stale_at = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
    fresh_at = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.executemany(
            "UPDATE author_runtime_instances SET updated_at = ? "
            "WHERE instance_id = ?",
            [
                (stale_at, stale["runtime_instance_id"]),
                (fresh_at, fresh["runtime_instance_id"]),
                (stale_at, leased["runtime_instance_id"]),
                (stale_at, malformed_lease["runtime_instance_id"]),
                (stale_at, foreign["runtime_instance_id"]),
            ],
        )
        conn.execute(
            "UPDATE branch_tasks_v2 SET status = 'running', claimed_by = ?, "
            "claimed_at = ?, lease_expires_at = ? WHERE branch_task_id = ?",
            (
                "worker-leased",
                "2026-07-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
                claimed["branch_task_id"],
            ),
        )
        conn.execute(
            "UPDATE user_requests SET status = 'running' WHERE request_id = ?",
            (claimed["request_id"],),
        )
        conn.execute(
            "UPDATE branch_tasks_v2 SET status = 'running', claimed_by = ?, "
            "claimed_at = ?, lease_expires_at = 'not-a-timestamp' "
            "WHERE branch_task_id = ?",
            (
                "worker-malformed-lease",
                "2026-07-01T00:00:01+00:00",
                malformed_claim["branch_task_id"],
            ),
        )
        conn.execute(
            "UPDATE user_requests SET status = 'running' WHERE request_id = ?",
            (malformed_claim["request_id"],),
        )
    before = db_path(tmp_path).read_bytes()

    planned = daemon_registry.plan_stale_cloud_worker_runtime_retirement(
        tmp_path,
        cutoff=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert [item.instance_id for item in planned] == [
        stale["runtime_instance_id"]
    ]
    assert db_path(tmp_path).read_bytes() == before
    assert daemon_server.get_runtime_instance(
        tmp_path,
        instance_id=fresh["runtime_instance_id"],
    )["status"] == "provisioned"
    assert daemon_server.get_runtime_instance(
        tmp_path,
        instance_id=leased["runtime_instance_id"],
    )["status"] == "provisioned"
    assert daemon_server.get_runtime_instance(
        tmp_path,
        instance_id=malformed_lease["runtime_instance_id"],
    )["status"] == "provisioned"
    assert daemon_server.get_runtime_instance(
        tmp_path,
        instance_id=foreign["runtime_instance_id"],
    )["status"] == "provisioned"


def test_runtime_apply_cas_retires_stale_cloud_worker(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Retirement test daemon",
        created_by="actor-a",
        soul_mode="soulless",
    )
    runtime = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-stale",
        provider_name="codex",
    )
    stale_at = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE author_runtime_instances SET updated_at = ? "
            "WHERE instance_id = ?",
            (stale_at, runtime["runtime_instance_id"]),
        )
    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    planned = daemon_registry.plan_stale_cloud_worker_runtime_retirement(
        tmp_path,
        cutoff=cutoff,
    )
    assert len(planned) == 1
    item = planned[0]
    assert daemon_server.retire_runtime_instance_if_stale(
        tmp_path,
        instance_id=item.instance_id,
        cutoff=cutoff,
        expected_updated_at=item.updated_at,
        expected_row_digest="0" * 64,
    ) is None

    retired = daemon_server.retire_runtime_instance_if_stale(
        tmp_path,
        instance_id=item.instance_id,
        cutoff=cutoff,
        expected_updated_at=item.updated_at,
        expected_row_digest=item.row_digest,
    )

    assert retired is not None
    assert retired["status"] == "retired"
    assert daemon_registry.get_daemon(
        tmp_path,
        daemon_id=daemon["daemon_id"],
    )["daemon_id"] == daemon["daemon_id"]


def test_runtime_apply_cas_rejects_new_malformed_active_ownership(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Malformed ownership test daemon",
        created_by="actor-a",
        soul_mode="soulless",
    )
    runtime = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-malformed-owner",
        provider_name="codex",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE author_runtime_instances SET updated_at = ? "
            "WHERE instance_id = ?",
            (
                datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp(),
                runtime["runtime_instance_id"],
            ),
        )
    cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)
    item = daemon_registry.plan_stale_cloud_worker_runtime_retirement(
        tmp_path,
        cutoff=cutoff,
    )[0]
    cloud = _activation(
        tmp_path,
        automation_id="malformed-owner",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    claimed = _commit_task(
        tmp_path,
        key="malformed-owner-task",
        created_at="2026-07-01T00:00:00+00:00",
        activation=cloud,
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE branch_tasks_v2 SET status = 'running', claimed_by = ?, "
            "claimed_at = ?, lease_expires_at = 'not-a-timestamp' "
            "WHERE branch_task_id = ?",
            (
                "worker-malformed-owner",
                "2026-07-01T00:00:00+00:00",
                claimed["branch_task_id"],
            ),
        )
        conn.execute(
            "UPDATE user_requests SET status = 'running' WHERE request_id = ?",
            (claimed["request_id"],),
        )

    retired = daemon_server.retire_runtime_instance_if_stale(
        tmp_path,
        instance_id=item.instance_id,
        cutoff=cutoff,
        expected_updated_at=item.updated_at,
        expected_row_digest=item.row_digest,
    )

    assert retired is None
    assert daemon_server.get_runtime_instance(
        tmp_path,
        instance_id=runtime["runtime_instance_id"],
    )["status"] == "provisioned"


def test_runtime_planner_excludes_recently_dead_heartbeat(
    tmp_path: Path,
) -> None:
    initialize_author_server(tmp_path)
    daemon = daemon_registry.create_daemon(
        tmp_path,
        display_name="Recently dead runtime",
        created_by="actor-a",
        soul_mode="soulless",
    )
    runtime = _cloud_runtime(
        tmp_path,
        daemon_id=daemon["daemon_id"],
        worker_id="worker-recently-dead",
        provider_name="codex",
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute(
            "UPDATE author_runtime_instances SET updated_at = ? "
            "WHERE instance_id = ?",
            (
                datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp(),
                runtime["runtime_instance_id"],
            ),
        )
    universe_path = tmp_path / "universe-a"
    universe_path.mkdir()
    (universe_path / ".worker_supervisor.worker-recently-dead.json").write_text(
        json.dumps({
            "worker_id": "worker-recently-dead",
            "runtime_instance_id": runtime["runtime_instance_id"],
            "ts": "2026-08-10T00:00:00Z",
            "subprocess_alive": False,
        }),
        encoding="utf-8",
    )

    planned = daemon_registry.plan_stale_cloud_worker_runtime_retirement(
        tmp_path,
        cutoff=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert planned == []


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


def test_stale_fleet_fixture_is_settled_before_cli(tmp_path: Path) -> None:
    # Make the formerly incidental collection schedule deterministic. The
    # fixture must settle its own writes even when automatic GC has not run.
    enabled = gc.isenabled()
    gc.disable()
    try:
        _stale_fleet_fixture(tmp_path)
        before = db_path(tmp_path).read_bytes()
        gc.collect()
        assert db_path(tmp_path).read_bytes() == before
    finally:
        if enabled:
            gc.enable()


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


def test_cli_defaults_to_stable_read_only_dry_run(tmp_path: Path) -> None:
    task, runtime = _stale_fleet_fixture(tmp_path)
    before = db_path(tmp_path).read_bytes()

    default = _run_reconciler(tmp_path)
    explicit = _run_reconciler(tmp_path, "--dry-run")

    assert default.returncode == 0, default.stderr
    assert explicit.returncode == 0, explicit.stderr
    assert default.stdout == explicit.stdout
    plan = json.loads(default.stdout)
    assert plan["mode"] == "dry-run"
    assert plan["task_count"] == 1
    assert plan["runtime_count"] == 1
    assert [item["branch_task_id"] for item in plan["tasks"]] == [
        task["branch_task_id"]
    ]
    assert [item["instance_id"] for item in plan["runtimes"]] == [
        runtime["runtime_instance_id"]
    ]
    assert len(plan["plan_digest"]) == 64
    assert db_path(tmp_path).read_bytes() == before


def test_cli_apply_aborts_without_writes_on_digest_or_count_mismatch(
    tmp_path: Path,
) -> None:
    task, runtime = _stale_fleet_fixture(tmp_path)
    dry_run = _run_reconciler(tmp_path)
    assert dry_run.returncode == 0, dry_run.stderr
    plan = json.loads(dry_run.stdout)
    before = db_path(tmp_path).read_bytes()

    bad_digest = _run_reconciler(
        tmp_path,
        "--apply",
        "--expected-plan-digest",
        "0" * 64,
        "--expect-task-count",
        "1",
        "--expect-runtime-count",
        "1",
    )
    assert bad_digest.returncode != 0
    assert db_path(tmp_path).read_bytes() == before

    bad_count = _run_reconciler(
        tmp_path,
        "--apply",
        "--expected-plan-digest",
        plan["plan_digest"],
        "--expect-task-count",
        "2",
        "--expect-runtime-count",
        "1",
    )
    assert bad_count.returncode != 0
    assert db_path(tmp_path).read_bytes() == before

    bad_runtime_count = _run_reconciler(
        tmp_path,
        "--apply",
        "--expected-plan-digest",
        plan["plan_digest"],
        "--expect-task-count",
        "1",
        "--expect-runtime-count",
        "2",
    )
    assert bad_runtime_count.returncode != 0
    assert db_path(tmp_path).read_bytes() == before
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT status FROM branch_tasks_v2 WHERE branch_task_id = ?",
            (task["branch_task_id"],),
        ).fetchone()[0] == "pending"
        assert conn.execute(
            "SELECT status FROM author_runtime_instances WHERE instance_id = ?",
            (runtime["runtime_instance_id"],),
        ).fetchone()[0] == "provisioned"


def test_cli_apply_requires_digest_and_both_exact_counts(
    tmp_path: Path,
) -> None:
    _stale_fleet_fixture(tmp_path)
    before = db_path(tmp_path).read_bytes()

    result = _run_reconciler(tmp_path, "--apply")

    assert result.returncode != 0
    assert "--expected-plan-digest" in result.stderr
    assert "--expect-task-count" in result.stderr
    assert "--expect-runtime-count" in result.stderr
    assert db_path(tmp_path).read_bytes() == before


def test_cli_guarded_apply_cancels_task_and_retires_runtime(
    tmp_path: Path,
) -> None:
    task, runtime = _stale_fleet_fixture(tmp_path)
    fresh_cloud = _activation(
        tmp_path,
        automation_id="cli-fresh-cloud",
        executor_class=AutomationActivationExecutor.CLOUD,
    )
    fresh = _commit_task(
        tmp_path,
        key="cli-fresh-task",
        created_at=datetime.now(timezone.utc).isoformat(),
        activation=fresh_cloud,
    )
    stale_tray = _activation(
        tmp_path,
        automation_id="cli-stale-tray",
        executor_class=AutomationActivationExecutor.TRAY,
    )
    foreign = _commit_task(
        tmp_path,
        key="cli-stale-tray-task",
        created_at="2026-07-01T00:00:00+00:00",
        activation=stale_tray,
    )
    dry_run = _run_reconciler(tmp_path)
    assert dry_run.returncode == 0, dry_run.stderr
    plan = json.loads(dry_run.stdout)

    applied = _run_reconciler(
        tmp_path,
        "--apply",
        "--expected-plan-digest",
        plan["plan_digest"],
        "--expect-task-count",
        str(plan["task_count"]),
        "--expect-runtime-count",
        str(plan["runtime_count"]),
    )

    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["mode"] == "apply"
    assert result["applied_task_count"] == 1
    assert result["applied_runtime_count"] == 1
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT status FROM branch_tasks_v2 WHERE branch_task_id = ?",
            (task["branch_task_id"],),
        ).fetchone()[0] == "cancelled"
        assert conn.execute(
            "SELECT status FROM author_runtime_instances WHERE instance_id = ?",
            (runtime["runtime_instance_id"],),
        ).fetchone()[0] == "retired"
        untouched = dict(conn.execute(
            "SELECT branch_task_id, status FROM branch_tasks_v2 "
            "WHERE branch_task_id IN (?, ?)",
            (fresh["branch_task_id"], foreign["branch_task_id"]),
        ).fetchall())
    assert untouched == {
        fresh["branch_task_id"]: "pending",
        foreign["branch_task_id"]: "pending",
    }
