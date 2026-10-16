"""Tests for get_status auto_ship_health observability.

Pairs with PR #198 auto-ship canary v0 Slice B: surface a compact
status summary over the Slice A auto_ship_attempts ledger without
adding shipper behavior.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tinyassets.api.status import (
    _compute_auto_ship_health,
    _parse_iso_to_epoch,
    get_status,
)
from tinyassets.auto_ship_ledger import ShipAttempt, ledger_path, record_attempt


@pytest.fixture(autouse=True)
def _ledger_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path.parent))


def _ts(minutes: int) -> str:
    base = datetime(2026, 5, 3, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=minutes)).isoformat()


def _attempt(
    idx: int,
    *,
    ship_status: str = "skipped",
    pr_url: str = "",
    observation_status: str = "",
    rollback_handle: str = "",
) -> ShipAttempt:
    return ShipAttempt(
        ship_attempt_id=f"ship_{idx:02d}",
        request_id=f"REQ-{idx:02d}",
        parent_run_id=f"parent-{idx:02d}",
        child_run_id=f"child-{idx:02d}",
        branch_def_id=f"branch-{idx:02d}",
        release_gate_result="APPROVE_AUTO_SHIP",
        ship_class="docs_canary",
        ship_status=ship_status,
        pr_url=pr_url,
        commit_sha=f"commit-{idx:02d}" if ship_status in {"merged", "opened"} else "",
        changed_paths_json='["docs/autoship-canaries/example.md"]',
        ci_status="passed" if ship_status in {"merged", "opened"} else "",
        rollback_handle=rollback_handle,
        stable_evidence_handle=f"evidence:{idx:02d}",
        created_at=_ts(idx),
        updated_at=_ts(idx),
        error_class="",
        error_message="",
        would_open_pr=True,
        observation_status=observation_status,
        observation_status_at=_ts(idx) if observation_status else "",
    )


def test_auto_ship_health_empty_ledger_shape(tmp_path):
    health = _compute_auto_ship_health(tmp_path)
    assert health == {
        "recent_attempts": [],
        "opened_prs": [],
        "rollback_recommendations": [],
        "window_seconds": 86400,
        "ledger_available": True,
        "warnings": [],
    }


def test_auto_ship_health_surfaces_corrupt_ledger(tmp_path):
    ledger_path(tmp_path).write_text("not-json\n", encoding="utf-8")

    health = _compute_auto_ship_health(tmp_path)

    assert health["ledger_available"] is False
    assert health["recent_attempts"] == []
    assert any("ledger_read_failed" in w for w in health["warnings"])


def test_auto_ship_health_summarizes_recent_open_and_regressed_attempts(tmp_path):
    for idx in range(12):
        kwargs = {}
        if idx == 8:
            kwargs = {
                "ship_status": "opened",
                "pr_url": "https://github.com/TinyAssets/TinyAssets/pull/308",
            }
        elif idx == 9:
            kwargs = {
                "ship_status": "opened",
                "pr_url": "https://github.com/TinyAssets/TinyAssets/pull/309",
                "observation_status": "regressed",
                "rollback_handle": "revert:commit-09",
            }
        elif idx == 10:
            kwargs = {
                "ship_status": "merged",
                "pr_url": "https://github.com/TinyAssets/TinyAssets/pull/310",
                "observation_status": "regressed",
                "rollback_handle": "revert:commit-10",
            }
        record_attempt(tmp_path, _attempt(idx, **kwargs))

    now_ts = _parse_iso_to_epoch("2026-05-03T12:00:00+00:00")
    assert now_ts is not None
    health = _compute_auto_ship_health(tmp_path, now_ts=now_ts)

    assert [a["ship_attempt_id"] for a in health["recent_attempts"]] == [
        "ship_11",
        "ship_10",
        "ship_09",
        "ship_08",
        "ship_07",
        "ship_06",
        "ship_05",
        "ship_04",
        "ship_03",
        "ship_02",
    ]
    # Compact summaries omit changed_paths_json to keep get_status small.
    assert "changed_paths_json" not in health["recent_attempts"][0]
    assert health["opened_prs"][0]["ship_attempt_id"] == "ship_09"
    assert health["opened_prs"][0]["observation_status"] == "regressed"
    assert health["opened_prs"][0]["observation_window_remaining_s"] > 0
    assert health["opened_prs"][1]["ship_attempt_id"] == "ship_08"
    assert health["opened_prs"][1]["observation_status"] == "observing"
    assert [r["ship_attempt_id"] for r in health["rollback_recommendations"]] == [
        "ship_10",
        "ship_09",
    ]
    assert health["rollback_recommendations"][0]["rollback_handle"] == "revert:commit-10"


def test_get_status_response_includes_auto_ship_health(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "test-universe")
    universe = tmp_path / "test-universe"
    universe.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(universe.parent, universe.name)
    record_attempt(universe, _attempt(1, ship_status="opened"))

    response = json.loads(get_status("test-universe"))
    assert "auto_ship_health" in response
    assert (
        response["auto_ship_health"]["recent_attempts"][0]["ship_attempt_id"]
        == "ship_01"
    )
    assert (
        response["auto_ship_health"]["opened_prs"][0]["ship_attempt_id"]
        == "ship_01"
    )
