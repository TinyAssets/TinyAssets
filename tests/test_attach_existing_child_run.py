from __future__ import annotations

import json
import time

import pytest

from tinyassets.api.runs import _action_attach_existing_child_run
from tinyassets.runs import create_run, get_run, update_run_status


def _make_run(
    base,
    *,
    branch_def_id: str,
    status: str,
    output: dict,
    actor: str = "tester",
) -> str:
    run_id = create_run(
        base,
        branch_def_id=branch_def_id,
        thread_id=f"thread-{branch_def_id}",
        inputs={},
        actor=actor,
    )
    update_run_status(
        base,
        run_id,
        status=status,
        output=output,
        finished_at=time.time() if status in {"completed", "failed", "interrupted"} else None,
        error="boom" if status == "failed" else "",
    )
    return run_id


@pytest.fixture
def attach_env(tmp_path, monkeypatch):
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    return base


def _waiting_parent(base, *, selected_branch: str = "child-branch") -> str:
    return _make_run(
        base,
        branch_def_id="parent-branch",
        status="completed",
        output={
            "parent_loop_status": "blocked_before_child_attach",
            "selected_child_status": "selected_attach_required",
            "selected_loop_branch": selected_branch,
            "automation_claim_status": "no_execution_claim",
        },
    )


def _completed_child(
    base,
    *,
    branch_def_id: str = "child-branch",
    output: dict | None = None,
) -> str:
    return _make_run(
        base,
        branch_def_id=branch_def_id,
        status="completed",
        output=output if output is not None else {
            "keep_reject_decision": "REVIEW_READY",
            "candidate_score": "7.8/10",
            "candidate_patch_packet": {"summary": "patch me"},
        },
    )


def _attach(parent_run_id: str, child_run_id: str, **extra) -> dict:
    payload = {
        "run_id": parent_run_id,
        "child_run_id": child_run_id,
        "child_branch_def_id": extra.pop("child_branch_def_id", "child-branch"),
        **extra,
    }
    return json.loads(_action_attach_existing_child_run(payload))


def test_attach_existing_child_run_copies_completed_child_output(attach_env):
    parent_id = _waiting_parent(attach_env)
    child_id = _completed_child(attach_env)

    result = _attach(parent_id, child_id)

    assert result["status"] == "attached"
    assert result["automation_claim_status"] == "child_attached_with_handle"
    assert result["selected_child_status"] == "attached_completed"
    assert result["stable_evidence_handle"].startswith("run-attachment:")
    assert "spawned" not in json.dumps(result).lower()
    assert "dispatched" not in json.dumps(result).lower()
    assert "invoked" not in json.dumps(result).lower()

    parent = get_run(attach_env, parent_id)
    assert parent is not None
    output = parent["output"]
    assert output["automation_claim_status"] == "child_attached_with_handle"
    assert output["selected_child_status"] == "attached_completed"
    assert output["stable_evidence_handle"] == result["stable_evidence_handle"]
    assert output["attached_child_output"] == get_run(attach_env, child_id)["output"]
    assert output["attached_child_output"]["keep_reject_decision"] == "REVIEW_READY"


def test_attach_existing_child_run_does_not_leave_parent_waiting(attach_env):
    parent_id = _waiting_parent(attach_env)
    first_child_id = _completed_child(attach_env)
    second_child_id = _completed_child(
        attach_env,
        output={"keep_reject_decision": "KEEP", "candidate_score": "8/10"},
    )
    first = _attach(parent_id, first_child_id)
    assert first["status"] == "attached"

    result = _attach(parent_id, second_child_id)

    assert result["error_code"] == "parent_not_receipt_waiting"


def test_attach_existing_child_run_rejects_missing_child_run(attach_env):
    parent_id = _waiting_parent(attach_env)

    result = _attach(parent_id, "missing-child-run")

    assert result["error_code"] == "child_not_found"
    assert result["child_run_id"] == "missing-child-run"


@pytest.mark.parametrize("child_status", ["running", "interrupted", "failed"])
def test_attach_existing_child_run_rejects_non_completed_child(attach_env, child_status):
    parent_id = _waiting_parent(attach_env)
    child_id = _make_run(
        attach_env,
        branch_def_id="child-branch",
        status=child_status,
        output={"keep_reject_decision": "KEEP"},
    )

    result = _attach(parent_id, child_id)

    assert result["error_code"] == "child_not_completed"
    assert result["child_status"] == child_status


def test_attach_existing_child_run_rejects_wrong_branch(attach_env):
    parent_id = _waiting_parent(attach_env, selected_branch="expected-child")
    child_id = _completed_child(attach_env, branch_def_id="other-child")

    result = _attach(parent_id, child_id, child_branch_def_id="expected-child")

    assert result["error_code"] == "child_branch_mismatch"
    assert result["expected_child_branch_def_id"] == "expected-child"
    assert result["actual_child_branch_def_id"] == "other-child"


def test_attach_existing_child_run_rejects_supplied_branch_override(attach_env):
    parent_id = _waiting_parent(attach_env, selected_branch="expected-child")
    child_id = _completed_child(attach_env, branch_def_id="other-child")

    result = _attach(parent_id, child_id, child_branch_def_id="other-child")

    assert result["error_code"] == "child_branch_mismatch"
    assert result["expected_child_branch_def_id"] == "expected-child"
    assert result["supplied_child_branch_def_id"] == "other-child"
    assert result["actual_child_branch_def_id"] == "other-child"


def test_attach_existing_child_run_rejects_missing_output(attach_env):
    parent_id = _waiting_parent(attach_env)
    child_id = _completed_child(attach_env, output={})

    result = _attach(parent_id, child_id)

    assert result["error_code"] == "child_output_missing"


def test_attach_existing_child_run_rejects_same_child_conflicting_digest(attach_env):
    first_parent_id = _waiting_parent(attach_env)
    child_id = _completed_child(attach_env)
    first = _attach(first_parent_id, child_id)
    assert first["status"] == "attached"

    update_run_status(
        attach_env,
        child_id,
        output={"keep_reject_decision": "KEEP", "candidate_score": "9/10"},
    )
    second_parent_id = _waiting_parent(attach_env)

    result = _attach(second_parent_id, child_id)

    assert result["error_code"] == "conflicting_child_digest"
    assert result["child_run_id"] == child_id


def test_attach_existing_child_run_rejects_parent_not_receipt_waiting(attach_env):
    parent_id = _make_run(
        attach_env,
        branch_def_id="parent-branch",
        status="completed",
        output={
            "parent_loop_status": "completed_internal_handoff",
            "selected_child_status": "attached_completed",
        },
    )
    child_id = _completed_child(attach_env)

    result = _attach(parent_id, child_id)

    assert result["error_code"] == "parent_not_receipt_waiting"


def test_attach_existing_child_run_reclaims_child_invocation_receipt_waiting_parent(attach_env):
    parent_id = _make_run(
        attach_env,
        branch_def_id="parent-branch",
        status="interrupted",
        output={
            "parent_loop_status": "receipt_waiting",
            "selected_child_status": "child_invocation_receipt_waiting",
            "selected_branch_state": "child_invocation_receipt_waiting",
            "selected_child_branch_def_id": "child-branch",
            "child_invocation_receipt_gate": {
                "status": "receipt_waiting",
                "reclaim_action": "attach_existing_child_run",
            },
        },
    )
    child_id = _completed_child(attach_env)

    result = _attach(parent_id, child_id)

    assert result["status"] == "attached"
    assert result["automation_claim_status"] == "child_attached_with_handle"
    assert result["stable_evidence_handle"].startswith("run-attachment:")
