"""Recovery must not publish capacity or consume work from an unapproved process."""

import json
import sqlite3

import pytest

import tinyassets.platform_runtime_provenance as provenance
from tests.test_cloud_only_admission_regressions import (
    ADMITTED,
    UNADMITTED,
    _claimed_row,
    _ready_cloud_assignment,
    _register_cloud_worker,
)
from tests.test_runtime_reconcile import _stale_fleet_fixture
from tinyassets import runtime_reconcile
from tinyassets.control_plane.tick import AssignedQueueConsumer
from tinyassets.runtime_reconcile import build_stale_fleet_plan
from tinyassets.storage import db_path


def _bind(monkeypatch, verdict):
    observation = provenance.ProcessProvenanceObservation(resolver=lambda: verdict)
    observation.observe()
    monkeypatch.setattr(provenance, "_PROCESS_OBSERVATION", observation)


def test_unadmitted_recovery_keeps_pending_work_and_existing_registration_inert(
    tmp_path, monkeypatch
):
    _bind(monkeypatch, ADMITTED)
    _register_cloud_worker(tmp_path)
    _adapter, candidate, _lease = _ready_cloud_assignment(tmp_path)
    _bind(monkeypatch, UNADMITTED)
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    touched = []
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda base: touched.append("enumerate") or [],
    )
    consumer = AssignedQueueConsumer(tmp_path)
    try:
        with pytest.raises(PermissionError, match="platform_not_cloud"):
            consumer.poll_once()
        assert touched == []
        assert _claimed_row(tmp_path, candidate.branch_task_id) == ("pending", "")
        assert list(tmp_path.rglob(".worker_supervisor*.json")) == []
        assert consumer._active == {}
    finally:
        consumer.stop()


def test_unadmitted_consumer_cannot_start_before_control_maintenance(tmp_path, monkeypatch):
    _bind(monkeypatch, UNADMITTED)
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    consumer = AssignedQueueConsumer(tmp_path)

    def forbidden_maintenance():
        raise AssertionError("unapproved consumer reached control maintenance")

    monkeypatch.setattr(consumer, "_retire_fleet_controls", forbidden_maintenance)
    try:
        with pytest.raises(PermissionError, match="platform_not_cloud"):
            consumer.start()
        assert consumer._thread is None
    finally:
        consumer.stop()


def test_admitted_recovery_can_still_poll(tmp_path, monkeypatch):
    _bind(monkeypatch, ADMITTED)
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    touched = []
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda base: touched.append("enumerate") or [],
    )
    consumer = AssignedQueueConsumer(tmp_path)
    try:
        assert consumer.poll_once() == 0
        assert touched == ["enumerate"]
    finally:
        consumer.stop()


# --- explicit stale-fleet retirement (tinyassets/runtime_reconcile.py) -------
#
# Automatic recovery (above) must leave work pending with no admitted
# successor. Explicit operator retirement is a different act: a human confirms
# a reviewed dry-run plan by digest and both exact counts, and the tool then
# cancels exactly those stale tasks. It assigns nothing, so it is never a
# successor -- but it writes, so it is admitted like any other platform write.


def _pending_task_and_runtime_state(base_path, branch_task_id, instance_id):
    """Read the two real rows back. Never re-uses the planner's own view."""
    with sqlite3.connect(db_path(base_path)) as conn:
        task_status = conn.execute(
            "SELECT status FROM branch_tasks_v2 WHERE branch_task_id = ?",
            (branch_task_id,),
        ).fetchone()[0]
        runtime_status = conn.execute(
            "SELECT status FROM author_runtime_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()[0]
    return task_status, runtime_status


def _admitted_stale_fleet(tmp_path, monkeypatch):
    """Build a real stale task + provisioned runtime, then drop admission."""
    _bind(monkeypatch, ADMITTED)
    task, runtime = _stale_fleet_fixture(tmp_path)
    plan = build_stale_fleet_plan(tmp_path, older_than_hours=24)
    assert plan.task_count == 1 and plan.runtime_count == 1
    return task, runtime, plan


def test_unadmitted_apply_plan_refuses_before_any_store_or_write(
    tmp_path, monkeypatch
):
    task, runtime, plan = _admitted_stale_fleet(tmp_path, monkeypatch)
    _bind(monkeypatch, UNADMITTED)

    def forbidden_store(*args, **kwargs):
        raise AssertionError("unadmitted retirement constructed the write store")

    monkeypatch.setattr(runtime_reconcile, "RequestAdmissionStore", forbidden_store)
    monkeypatch.setattr(
        runtime_reconcile.daemon_server,
        "retire_runtime_instance_if_stale",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unadmitted retirement retired a runtime")
        ),
    )

    with pytest.raises(PermissionError, match="platform_not_cloud"):
        runtime_reconcile._apply_plan(tmp_path, plan)

    assert _pending_task_and_runtime_state(
        tmp_path, task["branch_task_id"], runtime["runtime_instance_id"]
    ) == ("pending", "provisioned")


def test_unadmitted_cli_apply_exits_two_with_sanitized_json_and_no_mutation(
    tmp_path, monkeypatch, capsys
):
    task, runtime, plan = _admitted_stale_fleet(tmp_path, monkeypatch)
    _bind(monkeypatch, UNADMITTED)
    before = db_path(tmp_path).read_bytes()

    code = runtime_reconcile.main([
        "stale-fleet",
        "--data-dir",
        str(tmp_path),
        "--older-than-hours",
        "24",
        "--apply",
        "--expected-plan-digest",
        plan.plan_digest,
        "--expect-task-count",
        str(plan.task_count),
        "--expect-runtime-count",
        str(plan.runtime_count),
    ])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "Traceback" not in captured.err
    error = json.loads(captured.err)["error"]
    assert error.startswith("platform_not_cloud: ")
    assert str(tmp_path) not in error
    assert task["branch_task_id"] not in error
    assert runtime["runtime_instance_id"] not in error
    assert db_path(tmp_path).read_bytes() == before
    assert _pending_task_and_runtime_state(
        tmp_path, task["branch_task_id"], runtime["runtime_instance_id"]
    ) == ("pending", "provisioned")


def test_unadmitted_cli_dry_run_still_reports_the_plan_and_mutates_nothing(
    tmp_path, monkeypatch, capsys
):
    task, runtime, plan = _admitted_stale_fleet(tmp_path, monkeypatch)
    _bind(monkeypatch, UNADMITTED)
    before = db_path(tmp_path).read_bytes()

    code = runtime_reconcile.main([
        "stale-fleet",
        "--data-dir",
        str(tmp_path),
        "--older-than-hours",
        "24",
        "--dry-run",
    ])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    reported = json.loads(captured.out)
    assert reported["mode"] == "dry-run"
    assert reported["plan_digest"] == plan.plan_digest
    assert db_path(tmp_path).read_bytes() == before
    assert _pending_task_and_runtime_state(
        tmp_path, task["branch_task_id"], runtime["runtime_instance_id"]
    ) == ("pending", "provisioned")
