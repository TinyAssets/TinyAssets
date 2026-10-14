from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tinyassets.branch_tasks_v2 import Epoch2BranchTask
from tinyassets.runtime.claimed_branch_execution import (
    ClaimedBranchExecutorIdentity,
    execute_claimed_branch_task,
)


def _task() -> Epoch2BranchTask:
    return Epoch2BranchTask(
        branch_task_id="bt2_" + "a" * 32,
        branch_def_id="branch-a",
        universe_id="universe-a",
        admission_id="adm_" + "c" * 32,
        request_id="req_" + "d" * 32,
        actor_id="owner-a",
        automation_id="automation-a",
        automation_branch_version="branch-version-a",
        automation_subject_ref="branch-version-a",
        automation_subject_digest="sha256:" + "b" * 64,
        inputs={"secret": "kept-verbatim"},
        depth=2,
        origin_branch_task_id="bt2_origin",
    )


def test_shared_executor_uses_immutable_version_and_authz_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "universe-a").mkdir()
    captured = {}
    provider_call = object()

    def execute(_base, *, branch_version_id, **kwargs):
        captured.update(branch_version_id=branch_version_id, **kwargs)
        return SimpleNamespace(run_id="run-a", status="completed", output={}, error="")

    monkeypatch.setattr("tinyassets.runs.get_run_by_branch_task_id", lambda *_a, **_k: None)
    monkeypatch.setattr("tinyassets.runs.execute_branch_version", execute)

    result = execute_claimed_branch_task(
        tmp_path,
        _task(),
        ClaimedBranchExecutorIdentity(
            daemon_id="daemon-a",
            worker_id="consumer-a",
            runtime_instance_id="lease-a",
        ),
        provider_call,
    )

    assert result[0] is True
    assert captured["branch_version_id"] == "branch-version-a"
    assert captured["provider_call"] is provider_call
    assert captured["inputs"] == {"secret": "kept-verbatim"}
    assert captured["_queue_branch_task_id"] == _task().branch_task_id
    assert captured["_enqueue_universe_id"] == "universe-a"
    assert captured["actor"] == "owner-a"
    assert captured["runtime_instance_id"] == "lease-a"
    assert captured["worker_id"] == "consumer-a"


def test_shared_executor_preserves_missing_input_diagnostic(
    tmp_path: Path, monkeypatch
) -> None:
    from tinyassets.runs import MissingRequiredInputs

    (tmp_path / "universe-a").mkdir()
    monkeypatch.setattr("tinyassets.runs.get_run_by_branch_task_id", lambda *_a, **_k: None)

    def refuse(*_args, **_kwargs):
        raise MissingRequiredInputs(
            ["context"],
            {"context": {"type": "dict", "example": {}}},
        )

    monkeypatch.setattr("tinyassets.runs.execute_branch_version", refuse)
    success, error, detail = execute_claimed_branch_task(
        tmp_path,
        _task(),
        ClaimedBranchExecutorIdentity(daemon_id="daemon-a"),
        object(),
    )

    assert success is False
    assert error == "missing_required_inputs"
    assert detail["missing_input_keys"] == ["context"]
    assert detail["input_guidance"] == {
        "context": {"type": "dict", "example": {}},
    }
    assert "run_id" not in detail


def test_shared_executor_reconciles_exact_terminal_run(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "universe-a").mkdir()
    task = _task()
    monkeypatch.setattr(
        "tinyassets.runs.get_run_by_branch_task_id",
        lambda *_a, **_k: {
            "run_name": f"branch-task-{task.branch_task_id}",
            "branch_task_id": task.branch_task_id,
            "branch_def_id": task.branch_def_id,
            "branch_version_id": task.automation_branch_version,
            "queue_universe_id": task.universe_id,
            "actor": task.actor_id,
            "daemon_id": "daemon-a",
            "runtime_instance_id": "lease-a",
            "worker_id": "consumer-a",
            "run_id": "run-a",
            "status": "completed",
            "output": {},
        },
    )

    result = execute_claimed_branch_task(
        tmp_path,
        task,
        ClaimedBranchExecutorIdentity("daemon-a", "consumer-a", "lease-a"),
        object(),
    )

    assert result == (
        True,
        "",
        {
            "branch_def_id": "branch-a",
            "branch_version_id": "branch-version-a",
            "run_id": "run-a",
            "run_status": "completed",
            "actor": "owner-a",
            "reused_existing_run": True,
        },
    )




