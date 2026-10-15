"""Tests for the 2026-06-10 loop-telemetry slice.

Covers:
  - branch_tasks.reclaim_expired_leases (lease-aware reaper, BUG-011 Phase C)
  - api.universe._worker_liveness beat interpretation

The supervisor-heartbeat WRITER and healthcheck cases were deleted on
2026-08-29 with the host-run `tinyassets.cloud_worker` fleet -- nothing runs
outside a user's universe (ADR-009). The beat READER cases stay: the served
`AssignedQueueConsumer` writes the same files.
  - last_activity_canary worker_liveness preference
  - ProviderRouter._call_meta shape and one-call response
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tinyassets.control_plane.tick import (
    SUPERVISOR_HEARTBEAT_FILENAME,
    supervisor_heartbeat_filename,
)


def _utc(offset_s: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_s)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")




# ---------------------------------------------------------------------------
# reclaim_expired_leases
# ---------------------------------------------------------------------------










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
    (universe / SUPERVISOR_HEARTBEAT_FILENAME).write_text(
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
    assert meta["model"] == "gpt-5.1-codex"
    assert meta["execution"]["model_status"] == "unknown"
    assert meta["execution"]["resolved_model"] == "gpt-5.1-codex"
    assert meta["family"] == "openai" and meta["attempts"] == 2


def test_call_meta_preserves_reported_answer_identity_separately():
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.router import ProviderRouter

    resp = ProviderResponse(
        text="hi", provider="claude", model="configured-alias",
        reported_model="provider-reported-model", family="anthropic", latency_ms=812,
    )
    meta = ProviderRouter._call_meta(resp, attempts=2)
    assert meta["model"] == "configured-alias"
    assert meta["execution"]["provider"] == "claude"
    assert meta["execution"]["reported_model"] == "provider-reported-model"
    assert meta["attempts"] == 2


@pytest.mark.asyncio
async def test_bound_call_returns_response_with_receipt():
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
    response = await owner_bound_call(
        router, "fake", prompt="p", system="s", policy={"preferred": {"provider": "fake"}},
    )
    assert response.text == "out"
    assert response.provider == "fake"
    assert response.execution_receipt.resolved_model == "fake-1"
