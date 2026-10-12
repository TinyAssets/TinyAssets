"""Tests for ``tinyassets.api.status._compute_supervisor_liveness``.

Pairs with PR #212 (BUG-011 Phase A lease metadata fields). This test
file uses ``getattr`` defaults to verify the supervisor_liveness helper
works both pre- and post-PR-#212.

Spec source: PR #206 (daemon-liveness-watchdog.md (archived in git)).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tinyassets.api.status import (
    _HEARTBEAT_STALE_THRESHOLD_S,
    _STUCK_PENDING_THRESHOLD_S,
    _compute_supervisor_liveness,
    _parse_iso_to_epoch,
)
from tinyassets.branch_tasks import BranchTask, append_task, claim_task

# ── _parse_iso_to_epoch unit ───────────────────────────────────────────────


def test_parse_iso_to_epoch_handles_empty_string():
    assert _parse_iso_to_epoch("") is None


def test_parse_iso_to_epoch_handles_none_safely():
    # Defensive: getattr default is "" but a None could leak through.
    assert _parse_iso_to_epoch(None or "") is None


def test_parse_iso_to_epoch_handles_z_suffix():
    iso = "2026-05-02T22:30:00Z"
    epoch = _parse_iso_to_epoch(iso)
    assert epoch is not None
    assert epoch > 0


def test_parse_iso_to_epoch_handles_offset_suffix():
    iso = "2026-05-02T22:30:00+00:00"
    epoch = _parse_iso_to_epoch(iso)
    assert epoch is not None


def test_parse_iso_to_epoch_returns_none_on_garbage():
    assert _parse_iso_to_epoch("not-a-timestamp") is None


# ── _compute_supervisor_liveness — empty queue ─────────────────────────────


def test_empty_queue_returns_zero_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    # Pass a NESTED universe dir: the epoch-2 read resolves its store from
    # `udir.parent`, so handing it `tmp_path` directly reads the SHARED pytest
    # temp root (`pytest-N/`) — which any sibling test can pollute with a stray
    # runs store, flipping epoch-2 to "available" and failing this test only in a
    # full-suite run. Nesting one level makes `udir.parent == tmp_path` (isolated).
    udir = tmp_path / "universe"
    udir.mkdir()
    out = _compute_supervisor_liveness(udir)
    assert out["queue_state"]["depth"] == 0
    assert out["queue_state"]["pending"] == 0
    assert out["queue_state"]["running"] == 0
    assert out["running_tasks_lease"] == []
    assert out["stale_running_tasks"] == []
    assert out["epoch_health"]["1"]["available"] is True
    assert out["epoch_health"]["2"]["available"] is False
    assert any(
        "epoch2_operational_read_failed" in warning
        for warning in out["warnings"]
    )
    assert out["lease_data_available"] is True


def test_missing_queue_file_does_not_raise(tmp_path):
    # tmp_path with no branch_tasks.json — read_queue should return [].
    out = _compute_supervisor_liveness(tmp_path)
    assert "queue_state" in out


# ── queue counts ───────────────────────────────────────────────────────────


def test_queue_counts_by_status(tmp_path):
    for status, count in [("pending", 2), ("running", 1), ("succeeded", 3), ("failed", 1)]:
        for i in range(count):
            t = BranchTask(
                branch_task_id=f"bt-{status}-{i}",
                branch_def_id="branch-1",
                universe_id="u",
                status=status,
            )
            append_task(tmp_path, t)
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["pending"] == 2
    assert out["queue_state"]["running"] == 1
    assert out["queue_state"]["succeeded"] == 3
    assert out["queue_state"]["failed"] == 1
    assert out["queue_state"]["depth"] == 7


def test_queue_counts_merge_epoch2_operational_states(
    tmp_path,
    monkeypatch,
):
    append_task(
        tmp_path,
        BranchTask(
            branch_task_id="bt-v1",
            branch_def_id="branch-1",
            universe_id=tmp_path.name,
            status="pending",
        ),
    )
    epoch2 = {
        "available": True,
        "queue_epoch": 2,
        "depth": 4,
        "compatible_worker_count": 0,
        "capacity_evidence_available": True,
        "lifecycle_counts": {
            "pending": 4,
            "running": 0,
            "cancel_requested": 0,
            "cancelled": 0,
            "succeeded": 0,
            "failed": 0,
        },
        "lifecycle_oldest_age_s": {"pending": 600},
        "operational_state_counts": {
            "awaiting_compatible_capacity": 1,
            "invalid_operator_admission": 1,
            "quarantined": 1,
            "policy_parked": 1,
        },
        "operational_oldest_age_s": {
            "awaiting_compatible_capacity": 600,
            "invalid_operator_admission": 500,
            "quarantined": 400,
            "policy_parked": 300,
        },
        "operational_reason_counts": {
            "awaiting_compatible_capacity": {
                "no_live_compatible_worker": 1,
            },
            "invalid_operator_admission": {
                "invalid_operator_admission": 1,
            },
            "quarantined": {"unsupported_protocol": 1},
            "policy_parked": {"disabled": 1},
        },
        "diagnostics": [
            {
                "branch_task_id": "bt2_" + "1" * 32,
                "row_digest": "2" * 64,
                "operational_state": "invalid_operator_admission",
                "reason": "invalid_operator_admission",
            },
        ],
        "diagnostics_truncated": False,
        "valid_pending_count": 1,
        "eligible_pending_count": 0,
        "operational_counts_authoritative": True,
        "unclassified_active_count": 0,
        "active_scan_limit": 1000,
    }
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: epoch2,
        raising=False,
    )

    out = _compute_supervisor_liveness(tmp_path)

    queue_state = out["queue_state"]
    assert queue_state["depth"] == 5
    assert queue_state["pending"] == 5
    assert queue_state["epoch_counts"]["1"]["lifecycle"]["pending"] == 1
    assert queue_state["epoch_counts"]["2"]["depth"] == 4
    assert sum(
        queue_state["epoch_counts"]["2"]["lifecycle"].values()
    ) == 4
    assert queue_state["awaiting_compatible_capacity"] == 1
    assert queue_state["invalid_operator_admission"] == 1
    assert queue_state["quarantined"] == 1
    assert queue_state["policy_parked"] == 1
    assert queue_state["awaiting_compatible_capacity_max_age_s"] == 600
    assert out["epoch2_operational"] == epoch2
    assert any("invalid_operator_admission" in item for item in out["warnings"])


def test_corrupt_v1_does_not_hide_healthy_epoch2(tmp_path, monkeypatch):
    (tmp_path / "branch_tasks.json").write_text(
        "{corrupt-v1",
        encoding="utf-8",
    )
    epoch2 = {
        "available": True,
        "queue_epoch": 2,
        "depth": 1,
        "lifecycle_counts": {
            "pending": 1,
            "running": 0,
            "cancel_requested": 0,
            "cancelled": 0,
            "succeeded": 0,
            "failed": 0,
        },
        "lifecycle_oldest_age_s": {"pending": 10},
        "operational_state_counts": {
            "awaiting_compatible_capacity": 1,
            "invalid_operator_admission": 0,
            "quarantined": 0,
            "policy_parked": 0,
        },
        "operational_oldest_age_s": {
            "awaiting_compatible_capacity": 10,
        },
        "operational_reason_counts": {},
        "valid_pending_count": 1,
        "eligible_pending_count": 0,
        "operational_counts_authoritative": True,
        "unclassified_active_count": 0,
        "active_scan_limit": 1000,
        "diagnostics": [],
        "diagnostics_truncated": False,
        "compatible_worker_count": 0,
        "capacity_evidence_available": True,
    }
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: epoch2,
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["epoch_health"]["1"]["available"] is False
    assert out["epoch_health"]["2"]["available"] is True
    assert out["counts_complete"] is False
    assert out["queue_state"]["depth"] == 1
    assert out["queue_state"]["pending"] == 1


def test_failed_epoch2_does_not_zero_healthy_v1(tmp_path, monkeypatch):
    append_task(
        tmp_path,
        BranchTask(
            branch_task_id="bt-v1-survives",
            branch_def_id="branch-1",
            universe_id=tmp_path.name,
            status="pending",
        ),
    )
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": False,
            "error": "injected_epoch2_failure",
            "diagnostics": [],
            "diagnostics_truncated": False,
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["epoch_health"]["1"]["available"] is True
    assert out["epoch_health"]["2"] == {
        "available": False,
        "error": "injected_epoch2_failure",
    }
    assert out["counts_complete"] is False
    assert out["queue_state"]["depth"] == 1
    assert out["queue_state"]["pending"] == 1


def test_missing_capacity_evidence_marks_operational_counts_incomplete(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": True,
            "queue_epoch": 2,
            "depth": 1,
            "lifecycle_counts": {
                "pending": 1,
                "running": 0,
                "cancel_requested": 0,
                "cancelled": 0,
                "succeeded": 0,
                "failed": 0,
            },
            "lifecycle_oldest_age_s": {"pending": 10},
            "operational_state_counts": {
                "awaiting_compatible_capacity": 1,
                "invalid_operator_admission": 0,
                "quarantined": 0,
                "policy_parked": 0,
            },
            "operational_oldest_age_s": {
                "awaiting_compatible_capacity": 10,
            },
            "operational_reason_counts": {},
            "valid_pending_count": 1,
            "eligible_pending_count": 0,
            "operational_counts_authoritative": False,
            "unclassified_active_count": 0,
            "active_scan_limit": 1000,
            "diagnostics": [],
            "diagnostics_truncated": False,
            "compatible_worker_count": 0,
            "capacity_evidence_available": False,
            "capacity_evidence_error": "runtime registry unavailable",
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["counts_complete"] is False
    assert any(
        "epoch2_capacity_evidence_unavailable" in warning
        for warning in out["warnings"]
    )
    assert not any(
        "epoch2_operational_scan_overflow" in warning
        for warning in out["warnings"]
    )


def test_disabled_epoch2_consumer_is_visible_in_status(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": True,
            "queue_epoch": 2,
            "depth": 1,
            "lifecycle_counts": {
                "pending": 1,
                "running": 0,
                "cancel_requested": 0,
                "cancelled": 0,
                "succeeded": 0,
                "failed": 0,
            },
            "lifecycle_oldest_age_s": {"pending": 10},
            "operational_state_counts": {
                "awaiting_compatible_capacity": 1,
                "invalid_operator_admission": 0,
                "quarantined": 0,
                "policy_parked": 0,
            },
            "operational_oldest_age_s": {
                "awaiting_compatible_capacity": 10,
            },
            "operational_reason_counts": {},
            "valid_pending_count": 1,
            "eligible_pending_count": 0,
            "operational_counts_authoritative": False,
            "unclassified_active_count": 0,
            "active_scan_limit": 1000,
            "diagnostics": [],
            "diagnostics_truncated": False,
            "compatible_worker_count": 0,
            "capacity_evidence_available": False,
            "capacity_evidence_error": "epoch2_consumer_not_ready",
            "consumer_ready": False,
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["counts_complete"] is False
    assert any(
        "epoch2_consumer_not_ready" in warning
        for warning in out["warnings"]
    )


def test_unknown_epoch2_lifecycle_status_is_visible_and_incomplete(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "tinyassets.api.universe._epoch2_operational_snapshot",
        lambda _udir: {
            "available": True,
            "queue_epoch": 2,
            "depth": 1,
            "lifecycle_counts": {
                "pending": 0,
                "running": 0,
                "cancel_requested": 0,
                "cancelled": 0,
                "succeeded": 0,
                "failed": 0,
                "unknown": 1,
            },
            "lifecycle_oldest_age_s": {"unknown": 10},
            "unknown_lifecycle_status_counts": {"unknown": 1},
            "operational_state_counts": {
                "awaiting_compatible_capacity": 0,
                "invalid_operator_admission": 1,
                "quarantined": 0,
                "policy_parked": 0,
            },
            "operational_oldest_age_s": {
                "invalid_operator_admission": 10,
            },
            "operational_reason_counts": {
                "invalid_operator_admission": {"incomplete": 1},
            },
            "valid_pending_count": 0,
            "eligible_pending_count": 0,
            "operational_counts_authoritative": False,
            "integrity_scope_complete": True,
            "unclassified_active_count": 0,
            "active_scan_limit": 1000,
            "diagnostics": [],
            "diagnostics_truncated": False,
            "compatible_worker_count": 0,
            "capacity_evidence_available": True,
        },
    )

    out = _compute_supervisor_liveness(tmp_path)

    assert out["queue_state"]["depth"] == 1
    assert out["queue_state"]["unknown"] == 1
    assert out["counts_complete"] is False
    assert any(
        "epoch2_unknown_lifecycle_status" in warning
        for warning in out["warnings"]
    )


# ── pending-age detection (BUG-009 incident pattern) ───────────────────────


def test_stuck_pending_above_threshold_emits_warning(tmp_path):
    # Manually craft a pending task with queued_at well in the past.
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    t = BranchTask(
        branch_task_id="bt-stuck",
        branch_def_id="branch-1",
        universe_id="u",
        status="pending",
        queued_at=old,
    )
    append_task(tmp_path, t)
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["stuck_pending_max_age_s"] >= 600
    assert any("stuck_pending" in w for w in out["warnings"])


def test_policy_parked_pending_does_not_emit_stuck_pending_warning(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    t = BranchTask(
        branch_task_id="bt-policy-parked",
        branch_def_id="branch-1",
        universe_id="u",
        trigger_source="goal_pool",
        status="pending",
        queued_at=old,
    )
    append_task(tmp_path, t)

    out = _compute_supervisor_liveness(tmp_path)

    assert out["queue_state"]["pending"] == 1
    assert out["queue_state"]["policy_parked_pending"] == 1
    assert out["queue_state"]["policy_parked_pending_max_age_s"] >= 600
    assert out["queue_state"]["stuck_pending_max_age_s"] == 0
    assert not any("stuck_pending" in w for w in out["warnings"])


def test_enabled_pending_still_warns_when_policy_parked_pending_exists(tmp_path):
    now = datetime.now(timezone.utc)
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-policy-parked",
        branch_def_id="branch-1",
        universe_id="u",
        trigger_source="goal_pool",
        status="pending",
        queued_at=(now - timedelta(minutes=10)).isoformat(),
    ))
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-stuck-enabled",
        branch_def_id="branch-1",
        universe_id="u",
        trigger_source="user_request",
        status="pending",
        queued_at=(now - timedelta(minutes=5)).isoformat(),
    ))

    out = _compute_supervisor_liveness(tmp_path)

    assert out["queue_state"]["policy_parked_pending"] == 1
    assert 300 <= out["queue_state"]["stuck_pending_max_age_s"] < 600
    assert any("stuck_pending" in w for w in out["warnings"])


def test_recent_pending_no_warning(tmp_path):
    t = BranchTask(
        branch_task_id="bt-fresh",
        branch_def_id="branch-1",
        universe_id="u",
        status="pending",
        queued_at=datetime.now(timezone.utc).isoformat(),
    )
    append_task(tmp_path, t)
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["stuck_pending_max_age_s"] < _STUCK_PENDING_THRESHOLD_S
    assert not any("stuck_pending" in w for w in out["warnings"])


# ── loop-stall signal (2026-06-25 wedge detector) ──────────────────────────


def test_loop_stalled_warns_on_fail_only_backlog(tmp_path):
    """The real wedge: failures stamp terminal_at but zero successes."""
    from tinyassets.api.status import _LOOP_STALL_WINDOW_S

    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=_LOOP_STALL_WINDOW_S + 600)).isoformat()
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-stalled",
        branch_def_id="branch-1",
        universe_id="u",
        status="pending",
        queued_at=old,
    ))
    # A recently FAILED task: opens the any_terminal_at gate but is not a success.
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-failed",
        branch_def_id="branch-1",
        universe_id="u",
        status="failed",
        queued_at=old,
        terminal_at=now.isoformat(),
    ))
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["recent_succeeded_count"] == 0
    assert out["queue_state"]["stuck_pending_max_age_s"] > _LOOP_STALL_WINDOW_S
    assert any("loop_stalled" in w for w in out["warnings"])


def test_no_loop_stalled_when_a_recent_success_exists(tmp_path):
    from tinyassets.api.status import _LOOP_STALL_WINDOW_S

    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=_LOOP_STALL_WINDOW_S + 600)).isoformat()
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-pending",
        branch_def_id="branch-1",
        universe_id="u",
        status="pending",
        queued_at=old,
    ))
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-done",
        branch_def_id="branch-1",
        universe_id="u",
        status="succeeded",
        queued_at=old,
        terminal_at=now.isoformat(),
    ))
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["recent_succeeded_count"] == 1
    assert not any("loop_stalled" in w for w in out["warnings"])


def test_no_loop_stalled_on_fresh_queue_without_terminal_at(tmp_path):
    """Old rows predating terminal_at must not false-fire the stall warning."""
    from tinyassets.api.status import _LOOP_STALL_WINDOW_S

    old = (
        datetime.now(timezone.utc)
        - timedelta(seconds=_LOOP_STALL_WINDOW_S + 600)
    ).isoformat()
    # Backlog + a succeeded row that predates the field (no terminal_at).
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-pending",
        branch_def_id="branch-1",
        universe_id="u",
        status="pending",
        queued_at=old,
    ))
    append_task(tmp_path, BranchTask(
        branch_task_id="bt-legacy-done",
        branch_def_id="branch-1",
        universe_id="u",
        status="succeeded",
        queued_at=old,
        terminal_at="",  # pre-field row
    ))
    out = _compute_supervisor_liveness(tmp_path)
    assert not any("loop_stalled" in w for w in out["warnings"])


# ── lease metadata path (post-#212) ────────────────────────────────────────


def _running_task_with_lease(
    *,
    task_id: str,
    heartbeat_age_s: int,
    lease_remaining_s: int,
    progress_age_s: int = 0,
) -> dict:
    """Build a serialized BranchTask dict with PR #212 lease fields."""
    now = datetime.now(timezone.utc)
    return {
        "branch_task_id": task_id,
        "branch_def_id": "branch-1",
        "universe_id": "u",
        "inputs": {},
        "trigger_source": "owner_queued",
        "priority_weight": 0.0,
        "queued_at": (now - timedelta(seconds=heartbeat_age_s + 30)).isoformat(),
        "claimed_by": "daemon::owner",
        "status": "running",
        "bid": 0.0,
        "goal_id": "",
        "required_llm_type": "",
        "evidence_url": "",
        "error": "",
        "cancel_requested": False,
        "request_type": "branch_run",
        "deadline": "",
        "worker_owner_id": "daemon::owner",
        "lease_expires_at": (now + timedelta(seconds=lease_remaining_s)).isoformat(),
        "heartbeat_at": (now - timedelta(seconds=heartbeat_age_s)).isoformat(),
        "last_progress_at": (now - timedelta(seconds=progress_age_s)).isoformat(),
    }


def _write_raw_queue(tmp_path, tasks: list[dict]) -> None:
    import json
    (tmp_path / "branch_tasks.json").write_text(json.dumps(tasks), encoding="utf-8")


def test_running_task_with_fresh_lease_not_stale(tmp_path):
    _write_raw_queue(
        tmp_path,
        [_running_task_with_lease(
            task_id="bt-fresh",
            heartbeat_age_s=10,
            lease_remaining_s=290,
        )],
    )
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["running"] == 1
    assert len(out["running_tasks_lease"]) == 1
    record = out["running_tasks_lease"][0]
    assert record["worker_owner_id"] == "daemon::owner"
    assert record["heartbeat_age_s"] is not None
    assert record["heartbeat_age_s"] < _HEARTBEAT_STALE_THRESHOLD_S
    assert record["lease_remaining_s"] is not None
    assert record["lease_remaining_s"] > 0
    assert out["stale_running_tasks"] == []
    assert out["lease_data_available"] is True


def test_running_task_lease_surfaces_executor_identity(tmp_path):
    task = BranchTask(
        branch_task_id="bt-executor-id",
        branch_def_id="branch-1",
        universe_id="u",
        status="pending",
    )
    append_task(tmp_path, task)

    claimed = claim_task(
        tmp_path,
        task.branch_task_id,
        "daemon::owner",
        executor_worker_id="codex-1",
        executor_runtime_id="runtime-123",
    )

    assert claimed is not None
    assert claimed.claimed_by == "daemon::owner"
    assert claimed.worker_owner_id == "daemon::owner"
    assert claimed.executor_worker_id == "codex-1"
    assert claimed.executor_runtime_id == "runtime-123"

    out = _compute_supervisor_liveness(tmp_path)
    record = out["running_tasks_lease"][0]
    assert record["daemon_id"] == "daemon::owner"
    assert record["executor_worker_id"] == "codex-1"
    assert record["executor_runtime_id"] == "runtime-123"


def test_stale_heartbeat_flagged_as_stale(tmp_path):
    _write_raw_queue(
        tmp_path,
        [_running_task_with_lease(
            task_id="bt-zombie",
            heartbeat_age_s=_HEARTBEAT_STALE_THRESHOLD_S + 60,
            lease_remaining_s=120,  # lease still ok
        )],
    )
    out = _compute_supervisor_liveness(tmp_path)
    assert len(out["stale_running_tasks"]) == 1
    stale = out["stale_running_tasks"][0]
    assert stale["branch_task_id"] == "bt-zombie"
    assert any("heartbeat_age_s" in r for r in stale["stale_reasons"])
    assert any("stale running task" in w for w in out["warnings"])


def test_expired_lease_flagged_as_stale(tmp_path):
    _write_raw_queue(
        tmp_path,
        [_running_task_with_lease(
            task_id="bt-expired",
            heartbeat_age_s=30,  # heartbeat fresh
            lease_remaining_s=-60,  # lease expired 60s ago
        )],
    )
    out = _compute_supervisor_liveness(tmp_path)
    assert len(out["stale_running_tasks"]) == 1
    stale = out["stale_running_tasks"][0]
    assert any("lease_expired" in r for r in stale["stale_reasons"])


def test_both_stale_signals_combined(tmp_path):
    _write_raw_queue(
        tmp_path,
        [_running_task_with_lease(
            task_id="bt-doubly-dead",
            heartbeat_age_s=_HEARTBEAT_STALE_THRESHOLD_S + 60,
            lease_remaining_s=-30,
        )],
    )
    out = _compute_supervisor_liveness(tmp_path)
    assert len(out["stale_running_tasks"]) == 1
    stale = out["stale_running_tasks"][0]
    # Both reasons should be recorded.
    assert len(stale["stale_reasons"]) == 2


# ── pre-#212 fallback (no lease fields populated) ──────────────────────────


def test_running_task_without_lease_fields_emits_lease_unavailable_warning(tmp_path):
    # Pre-PR-#212 BranchTasks have no lease metadata.
    t = BranchTask(
        branch_task_id="bt-pre212",
        branch_def_id="branch-1",
        universe_id="u",
        status="running",
        claimed_by="daemon::owner",
        queued_at=datetime.now(timezone.utc).isoformat(),
    )
    append_task(tmp_path, t)
    out = _compute_supervisor_liveness(tmp_path)
    assert out["queue_state"]["running"] == 1
    assert out["lease_data_available"] is False
    assert any("lease_data_unavailable" in w for w in out["warnings"])


# ── integration with get_status ────────────────────────────────────────────


def test_get_status_response_includes_supervisor_liveness(tmp_path, monkeypatch):
    import json

    from tinyassets.api.status import get_status

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "test-universe")
    universe = tmp_path / "test-universe"
    universe.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "test-universe")

    response = json.loads(get_status("test-universe"))
    assert "supervisor_liveness" in response
    assert "queue_state" in response["supervisor_liveness"]
    assert "running_tasks_lease" in response["supervisor_liveness"]
    assert "stale_running_tasks" in response["supervisor_liveness"]


def test_get_status_supervisor_liveness_reflects_stuck_pending(tmp_path, monkeypatch):
    import json

    from tinyassets.api.status import get_status

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "test-universe")
    universe = tmp_path / "test-universe"
    universe.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "test-universe")

    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    append_task(universe, BranchTask(
        branch_task_id="bt-stuck",
        branch_def_id="branch-1",
        universe_id="test-universe",
        status="pending",
        queued_at=old,
    ))

    response = json.loads(get_status("test-universe"))
    sl = response["supervisor_liveness"]
    assert sl["queue_state"]["pending"] == 1
    assert sl["queue_state"]["stuck_pending_max_age_s"] >= 300
    assert any("stuck_pending" in w for w in sl["warnings"])


def test_operational_capacity_requires_live_complete_descriptor(tmp_path):
    from tinyassets.api.universe import _compatible_epoch2_worker_ids

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    beat = {
        "ts": "2026-07-24T08:00:00Z",
        "phase": "polling",
        "subprocess_alive": True,
        "worker_id": "worker-a",
        "runtime_instance_id": "runtime-a",
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "boot_id": "boot-a",
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + "b" * 64,
        "universe_id": "universe-a",
        "expires_at": "2026-07-24T08:01:30Z",
    }
    heartbeat = universe / ".worker_supervisor.worker-a.json"
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    descriptor_fields = (
        "queue_protocol_version",
        "capabilities",
        "worker_id",
        "runtime_instance_id",
        "boot_id",
        "build_sha",
        "config_hash",
        "universe_id",
        "expires_at",
    )
    trusted = {
        "runtime-a": {field: beat[field] for field in descriptor_fields},
    }

    assert _compatible_epoch2_worker_ids(universe, now=now) == []
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == ["worker-a"]

    beat["subprocess_alive"] = False
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == []
    beat["subprocess_alive"] = True

    beat["expires_at"] = "2026-07-24T08:00:00Z"
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    trusted["runtime-a"] = {
        field: beat[field] for field in descriptor_fields
    }
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == []

    beat["expires_at"] = "2026-07-24T08:01:30Z"
    beat.pop("config_hash")
    heartbeat.write_text(json.dumps(beat), encoding="utf-8")
    trusted["runtime-a"] = {
        field: beat[field]
        for field in descriptor_fields
        if field in beat
    }
    assert _compatible_epoch2_worker_ids(
        universe,
        now=now,
        trusted_descriptors=trusted,
    ) == []


def _capacity_beat() -> dict:
    """The known-good beat shape used by the admission-gate tests."""
    return {
        "ts": "2026-07-24T08:00:00Z",
        "phase": "polling",
        "subprocess_alive": True,
        "worker_id": "worker-a",
        "runtime_instance_id": "runtime-a",
        "queue_protocol_version": 2,
        "capabilities": ["operator_request_v1"],
        "boot_id": "boot-a",
        "build_sha": "a" * 40,
        "config_hash": "sha256:" + "b" * 64,
        "universe_id": "universe-a",
        "expires_at": "2026-07-24T08:01:30Z",
    }


_CAPACITY_DESCRIPTOR_FIELDS = (
    "queue_protocol_version",
    "capabilities",
    "worker_id",
    "runtime_instance_id",
    "boot_id",
    "build_sha",
    "config_hash",
    "universe_id",
    "expires_at",
)


def test_capacity_rejections_name_the_gate_that_turned_each_worker_away(
    tmp_path,
):
    """A bare ``compatible_worker_count: 0`` is unactionable.

    Production sat blocked for 22.5h with ten live workers and zero
    compatible ones because no read-only surface named which admission
    gate rejected them. Every gate must now be reported by name.
    """
    from tinyassets.api.universe import (
        _classify_epoch2_workers,
        _compatible_epoch2_workers,
    )

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    heartbeat = universe / ".worker_supervisor.worker-a.json"

    def classify(beat, trusted):
        heartbeat.write_text(json.dumps(beat), encoding="utf-8")
        return _classify_epoch2_workers(
            universe,
            now=now,
            trusted_descriptors=trusted,
        )

    def trusted_from(beat):
        return {
            "runtime-a": {
                field: beat[field]
                for field in _CAPACITY_DESCRIPTOR_FIELDS
                if field in beat
            }
        }

    # ACCEPT DIRECTION — this must stay green, or every assertion below
    # passes vacuously against a gate that rejects everything.
    beat = _capacity_beat()
    workers, evidence = classify(beat, trusted_from(beat))
    assert [worker["worker_id"] for worker in workers] == ["worker-a"]
    assert evidence["rejected"] == {}
    assert evidence["observed_worker_beats"] == 1
    assert "descriptor_mismatch_fields" not in evidence

    # The refactor must not change the accept decision itself.
    assert _compatible_epoch2_workers(
        universe,
        now=now,
        trusted_descriptors=trusted_from(beat),
    ) == workers

    # Each gate, named.
    beat = _capacity_beat()
    beat["subprocess_alive"] = False
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"subprocess_not_alive": 1}

    beat = _capacity_beat()
    beat["capabilities"] = "operator_request_v1"
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"capabilities_not_a_collection": 1}

    beat = _capacity_beat()
    beat["universe_id"] = "universe-b"
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"universe_id_mismatch": 1}

    beat = _capacity_beat()
    workers, evidence = classify(beat, {"runtime-a": None})
    assert workers == []
    assert evidence["rejected"] == {"descriptor_never_published": 1}

    # A lease that has already expired is distinct from an untrusted one.
    beat = _capacity_beat()
    beat["expires_at"] = "2026-07-24T08:00:00Z"
    workers, evidence = classify(beat, trusted_from(beat))
    assert workers == []
    assert evidence["rejected"] == {"descriptor_lease_not_live": 1}


def test_capacity_rejection_reports_the_mismatched_field_not_its_value(
    tmp_path,
):
    """The registry copy drifting from the beat is the hardest gate to see.

    Report which field diverged so the cause is one read away, but never
    the values — they carry build and config identity.
    """
    from tinyassets.api.universe import _classify_epoch2_workers

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    beat = _capacity_beat()
    (universe / ".worker_supervisor.worker-a.json").write_text(
        json.dumps(beat), encoding="utf-8"
    )

    stale = {field: beat[field] for field in _CAPACITY_DESCRIPTOR_FIELDS}
    stale["expires_at"] = "2026-07-24T07:59:00Z"
    stale["build_sha"] = "c" * 40

    workers, evidence = _classify_epoch2_workers(
        universe,
        now=now,
        trusted_descriptors={"runtime-a": stale},
    )
    assert workers == []
    assert evidence["rejected"] == {"descriptor_not_trusted": 1}
    assert evidence["descriptor_mismatch_fields"] == [
        "build_sha",
        "expires_at",
    ]

    serialized = json.dumps(evidence)
    assert "c" * 40 not in serialized
    assert "2026-07-24T07:59:00Z" not in serialized


def test_capacity_rejection_flags_a_beat_with_no_provisioned_runtime_row(
    tmp_path,
):
    """A live beat with no registry row is invisible without this gate."""
    from tinyassets.api.universe import _classify_epoch2_workers

    universe = tmp_path / "universe-a"
    universe.mkdir()
    now = datetime.fromisoformat("2026-07-24T08:00:00+00:00")
    (universe / ".worker_supervisor.worker-a.json").write_text(
        json.dumps(_capacity_beat()), encoding="utf-8"
    )

    workers, evidence = _classify_epoch2_workers(universe, now=now)
    assert workers == []
    assert evidence["rejected"] == {"no_provisioned_runtime_row": 1}
    assert evidence["provisioned_runtime_count"] == 0
    assert evidence["observed_worker_beats"] == 1
