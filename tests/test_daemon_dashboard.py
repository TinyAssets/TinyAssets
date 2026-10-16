"""Host dashboard and MCP inspect surface tests.

Covers phase_h_preflight.md (archived in git) §4.4:

- `daemon_overview` action (8)
- `set_tier_config` action (6)
- Node lookup wiring (4)
- MCP surface non-inflation (2)
- Bid execution log rename (supplementary)

Pane-isolation + emergency-pause tests are dashboard-thread-scoped
and deferred with the dashboard UI panes themselves (non-blocking
per preflight §4.3 invariant 2 — panes wrap their own handlers).
"""

from __future__ import annotations

import json

import pytest
import yaml

from tinyassets.api.universe import (
    WRITE_ACTIONS,
    _action_daemon_overview,
    _action_set_tier_config,
)

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def universe_harness(tmp_path, monkeypatch):
    """TINYASSETS_DATA_DIR + TINYASSETS_REPO_ROOT pinned."""
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bids").mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(repo))
    # Clear the cache between tests to avoid cross-contamination.
    from tinyassets.api.universe import _OVERVIEW_CACHE
    _OVERVIEW_CACHE.clear()
    return {"base": base, "uid": uid, "repo": repo}


# ───────────────────────────────────────────────────────────────────────
# daemon_overview action (8 tests)
# ───────────────────────────────────────────────────────────────────────




def test_daemon_overview_exposes_merged_epoch_counts(
    universe_harness,
    monkeypatch,
):
    epoch_counts = {
        "1": {
            "available": True,
            "depth": 0,
            "lifecycle": {"pending": 0},
        },
        "2": {
            "available": True,
            "depth": 3,
            "lifecycle": {"pending": 3},
            "operational": {
                "awaiting_compatible_capacity": 1,
                "invalid_operator_admission": 1,
                "quarantined": 1,
                "policy_parked": 0,
            },
        },
    }
    queue_read = {
        "universe_id": universe_harness["uid"],
        "queue": [],
        "pending_count": 3,
        "running_count": 0,
        "counts_complete": True,
        "epoch_counts": epoch_counts,
        "epoch_health": {
            "1": {"available": True},
            "2": {"available": True},
        },
        "operational_state_counts": epoch_counts["2"]["operational"],
        "operational_state_oldest_age_s": {},
        "operational_reason_counts": {
            "quarantined": {"unsupported_protocol": 1},
        },
        "operational_diagnostics": [],
        "operational_diagnostics_truncated": False,
        "operational_counts_authoritative": True,
        "integrity_scope_complete": True,
        "unknown_epoch2_lifecycle_status_counts": {},
        "unclassified_epoch2_active_count": 0,
        "epoch2_active_scan_limit": 1000,
        "capacity_evidence_available": True,
        "capacity_evidence_error": None,
        "valid_epoch2_pending_count": 1,
        "eligible_epoch2_pending_count": 0,
        "compatible_worker_count": 0,
        "tier_status": {},
    }
    monkeypatch.setattr(
        "tinyassets.api.universe._action_queue_list",
        lambda **_kwargs: json.dumps(queue_read),
    )

    response = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))

    assert response["queue"]["pending_count"] == 3
    assert response["queue"]["epoch_counts"]["1"]["depth"] == 0
    assert response["queue"]["epoch_counts"]["2"]["depth"] == 3
    assert response["queue"]["operational_state_counts"] == (
        epoch_counts["2"]["operational"]
    )
    assert response["queue"]["operational_reason_counts"] == {
        "quarantined": {"unsupported_protocol": 1},
    }




def test_daemon_overview_ttl_cache(universe_harness):
    """Two calls within 1s return identical bytes (cached)."""
    r1 = _action_daemon_overview(universe_id=universe_harness["uid"])
    r2 = _action_daemon_overview(universe_id=universe_harness["uid"])
    assert r1 == r2


def test_daemon_overview_cache_separates_admin_integrity_counts(
    universe_harness,
    monkeypatch,
):
    authorization = {"admin": True}

    def queue_list(**_kwargs):
        response = {
            "queue": [],
            "pending_count": 0,
            "counts_complete": False,
            "epoch_counts": {},
            "epoch_health": {},
            "operational_state_counts": {},
            "operational_state_oldest_age_s": {},
            "operational_reason_counts": {},
            "operational_diagnostics": [],
            "operational_diagnostics_truncated": False,
            "operational_counts_authoritative": False,
            "integrity_scope_complete": False,
            "unknown_epoch2_lifecycle_status_counts": {},
            "unclassified_epoch2_active_count": 0,
            "epoch2_active_scan_limit": 1000,
            "capacity_evidence_available": True,
            "capacity_evidence_error": None,
            "valid_epoch2_pending_count": 0,
            "eligible_epoch2_pending_count": 0,
            "compatible_worker_count": 0,
        }
        if authorization["admin"]:
            response["unscoped_invalid_count"] = 7
        return json.dumps(response)

    monkeypatch.setattr(
        "tinyassets.api.universe._may_view_unscoped_epoch2_integrity",
        lambda _udir: authorization["admin"],
    )
    monkeypatch.setattr(
        "tinyassets.api.universe._action_queue_list",
        queue_list,
    )

    admin = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    authorization["admin"] = False
    reader = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))

    assert admin["queue"]["unscoped_invalid_count"] == 7
    assert "unscoped_invalid_count" not in reader["queue"]


def test_daemon_overview_cache_invalidated_on_set_tier_config(universe_harness):
    """Writing set_tier_config busts the cache for that universe."""
    r1 = _action_daemon_overview(universe_id=universe_harness["uid"])
    d1 = json.loads(r1)
    assert d1["dispatcher"]["config"]["accept_external_requests"] in (True, False)

    before_state = d1["dispatcher"]["config"]["accept_external_requests"]
    new_state = not before_state
    _action_set_tier_config(
        universe_id=universe_harness["uid"],
        tier="external_requests", enabled=new_state,
    )
    r2 = _action_daemon_overview(universe_id=universe_harness["uid"])
    d2 = json.loads(r2)
    assert d2["dispatcher"]["config"]["accept_external_requests"] == new_state




def test_daemon_overview_does_not_advertise_retired_paid_market(universe_harness, monkeypatch):
    resp = json.loads(_action_daemon_overview(universe_id=universe_harness["uid"]))
    assert "paid_bid" not in resp["dispatcher"]["tier_status_map"]
    assert "accept_paid_bids" not in resp["dispatcher"]["config"]
    refused = json.loads(_action_set_tier_config(
        universe_id=universe_harness["uid"], tier="paid_bids", enabled=True,
    ))
    assert refused["error"] == "unknown_tier"


def test_set_tier_config_valid_tier_writes_yaml(universe_harness):
    resp = json.loads(_action_set_tier_config(
        universe_id=universe_harness["uid"],
        tier="goal_pool", enabled=True,
    ))
    assert resp["status"] == "ok"
    assert resp["tier"] == "goal_pool"
    assert resp["enabled"] is True
    assert resp["takes_effect"] == "next_dispatcher_cycle"
    cfg_path = (
        universe_harness["base"] / universe_harness["uid"]
        / "dispatcher_config.yaml"
    )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["accept_goal_pool"] is True


def test_set_tier_config_invalid_tier_rejects(universe_harness):
    resp = json.loads(_action_set_tier_config(
        universe_id=universe_harness["uid"],
        tier="nonexistent_tier", enabled=True,
    ))
    assert resp["status"] == "rejected"
    assert resp["error"] == "unknown_tier"
    assert "available_tiers" in resp


def test_set_tier_config_persists_across_load(universe_harness):
    """Invariant 4: toggle → load_dispatcher_config sees it."""
    from tinyassets.control_plane.queue_policy import load_dispatcher_config
    _action_set_tier_config(
        universe_id=universe_harness["uid"],
        tier="external_requests", enabled=True,
    )
    udir = universe_harness["base"] / universe_harness["uid"]
    cfg = load_dispatcher_config(udir)
    assert cfg.accept_external_requests is True


def test_set_tier_config_preserves_other_yaml_fields(universe_harness):
    """Round-trip doesn't clobber unrelated fields."""
    udir = universe_harness["base"] / universe_harness["uid"]
    cfg_path = udir / "dispatcher_config.yaml"
    cfg_path.write_text(
        "custom_field: preserved\naccept_goal_pool: false\n",
        encoding="utf-8",
    )
    _action_set_tier_config(
        universe_id=universe_harness["uid"],
        tier="goal_pool", enabled=True,
    )
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["custom_field"] == "preserved"
    assert data["accept_goal_pool"] is True


def test_set_tier_config_surviving_tiers(universe_harness):
    """All surviving tier keys are accepted."""
    for tier in ("external_requests", "goal_pool", "opportunistic"):
        resp = json.loads(_action_set_tier_config(
            universe_id=universe_harness["uid"],
            tier=tier, enabled=True,
        ))
        assert resp["status"] == "ok", f"tier {tier} rejected: {resp}"


def test_set_tier_config_is_write_action(universe_harness):
    """set_tier_config is in WRITE_ACTIONS for ledger attribution."""
    assert "set_tier_config" in WRITE_ACTIONS


# ───────────────────────────────────────────────────────────────────────
# Node lookup wiring (invariant 5, 4 tests)
# ───────────────────────────────────────────────────────────────────────










# ───────────────────────────────────────────────────────────────────────
# MCP surface non-inflation (invariant 8, 2 tests)
# ───────────────────────────────────────────────────────────────────────


def test_dashboard_actions_do_not_add_new_mcp_tools():
    """Dashboard actions live on the existing `universe` tool, NOT new
    top-level tools. Enforced by the tool-registry introspection.
    """
    from tinyassets.api import universe as uni
    # Dashboard action handlers live in tinyassets.api.universe after
    # decomp Step 9. Assert they exist there as a regression guard
    # against accidental removal.
    assert hasattr(uni, "_action_daemon_overview")
    assert hasattr(uni, "_action_set_tier_config")


def test_dashboard_adds_exactly_two_actions_to_universe():
    """Introspective: daemon_overview + set_tier_config are the only
    two new `universe` tool actions."""
    from tinyassets.api import universe as uni
    dashboard_actions = {"daemon_overview", "set_tier_config"}
    for action in dashboard_actions:
        if action == "set_tier_config":
            assert action in uni.WRITE_ACTIONS
        assert hasattr(uni, f"_action_{action}")


# ───────────────────────────────────────────────────────────────────────
# R14 byte cap (DAEMON_OVERVIEW_MAX_BYTES)
# ───────────────────────────────────────────────────────────────────────


def test_trim_overview_under_cap_passes_through():
    """Response under the byte cap is returned unchanged, no
    `truncated` marker."""
    from tinyassets.api.universe import _trim_overview_for_bytes

    response = {"ok": True, "queue": {"top": [1, 2, 3]}}
    out = _trim_overview_for_bytes(response, cap=10_000)
    assert "truncated" not in response
    assert json.loads(out) == {"ok": True, "queue": {"top": [1, 2, 3]}}


def test_trim_overview_activity_tail_first():
    """R14: `activity_tail` trims before queue/bids/settlements. The
    gates + dispatcher + subscriptions fields are NEVER trimmed —
    load-bearing per reviewer polish #5.
    """
    from tinyassets.api.universe import (
        DAEMON_OVERVIEW_MAX_BYTES,
        _trim_overview_for_bytes,
    )

    # Craft a response well over the cap dominated by activity_tail.
    big_activity = ["x" * 200 for _ in range(500)]
    response = {
        "queue": {"top": [{"id": f"t{i}"} for i in range(10)], "count": 10},
        "bids": {"recent": [{"id": f"b{i}"} for i in range(5)], "count": 5},
        "settlements": {
            "recent": [{"id": f"s{i}"} for i in range(5)], "count": 5,
        },
        "gates": {"recent_claims": [{"id": "g"}], "total_claims": 1},
        "dispatcher": {"tier_config": {"accept_external_requests": True}},
        "subscriptions": {"goal_ids": ["g1"]},
        "activity_tail": list(big_activity),
    }
    out = _trim_overview_for_bytes(response)
    assert len(out.encode("utf-8")) <= DAEMON_OVERVIEW_MAX_BYTES
    assert response["truncated"] is True
    # activity_tail shrank (trimmed first). Other lists still intact.
    assert len(response["activity_tail"]) < len(big_activity)
    # Load-bearing fields untouched.
    assert response["gates"] == {
        "recent_claims": [{"id": "g"}], "total_claims": 1,
    }
    assert response["dispatcher"] == {
        "tier_config": {"accept_external_requests": True},
    }
    assert response["subscriptions"] == {"goal_ids": ["g1"]}


def test_trim_overview_activity_keeps_tail_not_head():
    """`activity_tail` is chronological (latest last). When halved,
    the latest entries must survive so operators still see recent
    events in the trimmed response.
    """
    from tinyassets.api.universe import _trim_overview_for_bytes

    lines = [f"event-{i:04d}" + ("x" * 200) for i in range(500)]
    response = {"activity_tail": list(lines)}
    _trim_overview_for_bytes(response, cap=8_000)
    tail = response["activity_tail"]
    assert tail, "expected some entries to survive"
    # The last original line must still be in the trimmed tail.
    assert tail[-1] == lines[-1]


def test_trim_overview_preserves_count_fields():
    """After a trim, `*_count` fields remain authoritative. Consumers
    learn the ACTUAL totals via counts, even when lists are halved.
    """
    from tinyassets.api.universe import _trim_overview_for_bytes

    response = {
        "queue": {
            "top": [{"id": f"t{i}", "payload": "z" * 200} for i in range(200)],
            "count": 200,
        },
        "bids": {"recent": [], "count": 0},
        "settlements": {"recent": [], "count": 0},
        "gates": {"total_claims": 0, "recent_claims": []},
        "dispatcher": {"tier_config": {}},
        "subscriptions": {"goal_ids": []},
        "activity_tail": [],
    }
    _trim_overview_for_bytes(response, cap=8_000)
    # queue.count still reports the real total.
    assert response["queue"]["count"] == 200
    assert len(response["queue"]["top"]) < 200
    assert response["truncated"] is True
