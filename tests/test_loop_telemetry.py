"""Tests for the 2026-06-10 loop-telemetry slice.

Covers:
  - branch_tasks.reclaim_expired_leases (lease-aware reaper, BUG-011 Phase C)
  - api.universe._worker_liveness beat interpretation

The supervisor-heartbeat WRITER and healthcheck cases were deleted on
2026-08-29 with the host-run `tinyassets.cloud_worker` fleet -- nothing runs
outside a user's universe (PLAN.md). The beat READER cases stay: the served
`AssignedQueueConsumer` writes the same files.
  - last_activity_canary worker_liveness preference
  - ProviderRouter._call_meta shape + call_with_policy 3-tuple
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tinyassets.branch_tasks import (
    BranchTask,
    append_task,
    claim_task,
    new_task_id,
    read_queue,
    reclaim_expired_leases,
)
from tinyassets.runtime.assigned_queue_consumer import (
    SUPERVISOR_HEARTBEAT_FILENAME,
    supervisor_heartbeat_filename,
)
from tinyassets.universe_paths import platform_path


def _utc(offset_s: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_s)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_task(universe: Path) -> BranchTask:
    task = BranchTask(
        branch_task_id=new_task_id(),
        branch_def_id="def-telemetry-test",
        universe_id="u-telemetry-test",
        trigger_source="owner_queued",
    )
    append_task(universe, task)
    return task


# ---------------------------------------------------------------------------
# reclaim_expired_leases
# ---------------------------------------------------------------------------


def test_reclaim_resets_expired_lease(tmp_path):
    task = _make_task(tmp_path)
    claimed = claim_task(tmp_path, task.branch_task_id, "daemon::test::1")
    assert claimed is not None

    future = _utc(offset_s=10_000)
    count = reclaim_expired_leases(tmp_path, now=future)
    assert count == 1
    rows = {t.branch_task_id: t for t in read_queue(tmp_path)}
    row = rows[task.branch_task_id]
    assert row.status == "pending"
    assert row.claimed_by == ""
    assert row.lease_expires_at == ""


def test_reclaim_leaves_fresh_lease_alone(tmp_path):
    task = _make_task(tmp_path)
    claimed = claim_task(tmp_path, task.branch_task_id, "daemon::test::1")
    assert claimed is not None

    count = reclaim_expired_leases(tmp_path)
    assert count == 0
    rows = {t.branch_task_id: t for t in read_queue(tmp_path)}
    assert rows[task.branch_task_id].status == "running"


def test_reclaim_skips_leaseless_running_rows(tmp_path):
    # Pre-lease-era claim: running but no lease stamp. The reaper must
    # not guess — startup recovery owns that case.
    task = _make_task(tmp_path)
    claimed = claim_task(tmp_path, task.branch_task_id, "daemon::test::1")
    assert claimed is not None
    from tinyassets.branch_tasks import _read_raw, _write_raw, queue_path

    qp = queue_path(tmp_path)
    raw = _read_raw(qp)
    for row in raw:
        row["lease_expires_at"] = ""
    _write_raw(qp, raw)

    count = reclaim_expired_leases(tmp_path, now=_utc(offset_s=10_000))
    assert count == 0


def test_reclaim_ignores_pending_rows(tmp_path):
    _make_task(tmp_path)
    assert reclaim_expired_leases(tmp_path, now=_utc(offset_s=10_000)) == 0


# ---------------------------------------------------------------------------
# supervisor heartbeat
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# beat fixtures
# ---------------------------------------------------------------------------


def _write_beat(universe: Path, *, age_s: float, phase: str = "polling",
                planned_sleep_s: float = 0.0) -> None:
    beat = {
        "ts": _iso(_utc(-age_s)),
        "phase": phase,
        "planned_sleep_s": planned_sleep_s,
    }
    platform_path(universe, SUPERVISOR_HEARTBEAT_FILENAME).write_text(
        json.dumps(beat), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# universe inspect worker_liveness
# ---------------------------------------------------------------------------


def test_worker_liveness_absent(tmp_path):
    from tinyassets.api.universe import _worker_liveness

    assert _worker_liveness(tmp_path) == {"present": False}


def test_worker_liveness_alive_and_dead(tmp_path):
    from tinyassets.api.universe import _worker_liveness

    _write_beat(tmp_path, age_s=10)
    live = _worker_liveness(tmp_path)
    assert live["present"] and live["alive"]

    _write_beat(tmp_path, age_s=3600)
    dead = _worker_liveness(tmp_path)
    assert dead["present"] and not dead["alive"]
    assert dead["beat_age_s"] > 3000


def test_worker_liveness_enumerates_worker_specific_beats(tmp_path):
    from tinyassets.api.universe import _worker_liveness

    for worker_id, runtime_id in (
        ("codex-1", "runtime-codex"),
        ("claude-1", "runtime-claude"),
    ):
        beat = {
            "ts": _iso(_utc(-10)),
            "phase": "polling",
            "planned_sleep_s": 0.0,
            "worker_id": worker_id,
            "runtime_instance_id": runtime_id,
        }
        (tmp_path / supervisor_heartbeat_filename(worker_id)).write_text(
            json.dumps(beat), encoding="utf-8",
        )

    liveness = _worker_liveness(tmp_path)

    assert liveness["present"] is True
    assert liveness["worker_count"] == 2
    assert liveness["runtime_instance_count"] == 2
    workers = {
        worker["worker_id"]: worker
        for worker in liveness["workers"]
    }
    assert workers["codex-1"]["runtime_instance_id"] == "runtime-codex"
    assert workers["claude-1"]["runtime_instance_id"] == "runtime-claude"


# ---------------------------------------------------------------------------
# canary worker_liveness preference
# ---------------------------------------------------------------------------


def _canary(daemon: dict) -> tuple[int, str]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import last_activity_canary as canary

    # The canary negotiates a bearer once and passes it to every post.
    def fake_post(url, sid, payload, timeout, step_code=0, bearer_token=None):
        if payload and payload.get("method") == "tools/call":
            return {
                "result": {
                    "structuredContent": {
                        "universe_id": "u-test",
                        "daemon": daemon,
                    },
                },
            }, "session-1"
        return {"result": {}}, "session-1"

    return canary.run_canary(
        "http://test/mcp", 5.0, 30, post_fn=fake_post,
    )


def test_canary_pages_on_dead_worker():
    code, msg = _canary({
        "staleness": "fresh",
        "is_paused": False,
        "has_work": True,
        "last_activity_at": _iso(_utc(-60)),
        "worker_liveness": {
            "present": True, "alive": False, "beat_age_s": 9999.0,
            "phase": "polling", "consec_crashes": 0,
        },
    })
    assert code == 2
    assert "worker_wedged" in msg
    assert "restart the worker container" not in msg


def test_canary_does_not_hide_unknown_coordinator_behind_fresh_activity():
    code, message = _canary({
        "has_work": False,
        "last_activity_at": _iso(_utc(-1)),
        "worker_liveness": {"present": True, "alive": None, "phase": "unavailable"},
    })
    assert code == 3
    assert "unavailable" in message


def test_canary_quiet_on_alive_worker_with_no_work():
    code, msg = _canary({
        "staleness": "dormant",
        "is_paused": False,
        "has_work": False,
        "last_activity_at": _iso(_utc(-90_000)),
        "worker_liveness": {
            "present": True, "alive": True, "beat_age_s": 12.0,
            "phase": "backoff", "consec_crashes": 0,
        },
    })
    assert code == 0
    assert "worker alive" in msg


def test_canary_falls_through_when_alive_with_work():
    code, _msg = _canary({
        "staleness": "dormant",
        "is_paused": False,
        "has_work": True,
        "last_activity_at": _iso(_utc(-90_000)),
        "worker_liveness": {
            "present": True, "alive": True, "beat_age_s": 12.0,
            "phase": "polling", "consec_crashes": 0,
        },
    })
    assert code == 2  # stale activity with live worker + work = real problem


# ---------------------------------------------------------------------------
# router call meta
# ---------------------------------------------------------------------------


def test_call_meta_shape():
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.router import ProviderRouter

    resp = ProviderResponse(
        text="hi", provider="codex", model="gpt-5.1-codex",
        family="openai", latency_ms=812,
    )
    meta = ProviderRouter._call_meta(resp, attempts=2)
    assert meta == {
        "model": "gpt-5.1-codex",
        # The configured label is not evidence of the model that answered.
        "execution": {"provider": "codex", "model": "", "model_status": "unknown"},
        "family": "openai",
        "latency_ms": 812,
        "degraded": False,
        "attempts": 2,
    }


def test_call_meta_preserves_reported_answer_identity_separately():
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.router import ProviderRouter

    resp = ProviderResponse(
        text="hi", provider="claude", model="configured-alias",
        reported_model="provider-reported-model", family="anthropic", latency_ms=812,
    )
    meta = ProviderRouter._call_meta(resp, attempts=2)
    assert meta["model"] == "configured-alias"
    assert meta["execution"] == {
        "provider": "claude", "model": "provider-reported-model", "model_status": "reported",
    }
    assert meta["attempts"] == 2


@pytest.mark.asyncio
async def test_call_with_policy_returns_meta_triple():
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.router import ProviderRouter

    class _Prov:
        name = "fake"

        async def complete(self, prompt, system, cfg, *, universe_dir=None):
            return ProviderResponse(
                text="out", provider="fake", model="fake-1",
                family="test", latency_ms=5,
            )

    router = ProviderRouter()
    router._providers = {"fake": _Prov()}
    router._role_chains = {"writer": ["fake"]}

    from tests.support.owner_bound import owner_bound_call

    # Hard Rule 15: served only under the owner's authority for "fake".
    text, name, meta = await owner_bound_call(
        router, "fake", prompt="p", system="s", policy={"preferred": {"provider": "fake"}},
    )
    assert text == "out"
    assert name == "fake"
    assert meta["model"] == "fake-1"
    assert meta["attempts"] == 1
