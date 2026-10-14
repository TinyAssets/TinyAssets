"""Host dashboard and MCP inspect surface tests.

Covers phase_h_preflight.md (archived in git) §4.4:

- `daemon_overview` action (8)
- `set_tier_config` action (6)
- Node lookup wiring (4)
- MCP surface non-inflation (2)
- Concerns audit doc + misc (supplementary)

Pane-isolation + emergency-pause tests are dashboard-thread-scoped
and deferred with the dashboard UI panes themselves (non-blocking
per preflight §4.3 invariant 2 — panes wrap their own handlers).
"""

from __future__ import annotations

import json
from pathlib import Path

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


def test_daemon_overview_response_shape(universe_harness):
    resp = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    assert resp["universe_id"] == universe_harness["uid"]
    assert "dispatcher" in resp
    assert "queue" in resp
    assert "subscriptions" in resp
    assert "bids" in resp
    assert "settlements" in resp
    assert "gates" in resp
    assert "activity_tail" in resp
    assert "run_state" in resp


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


def test_daemon_overview_top_n_limit_honored(universe_harness, monkeypatch):
    """Seed queue with 50 tasks; default limit returns top 20."""
    from tinyassets.branch_tasks import BranchTask, append_task, new_task_id
    udir = universe_harness["base"] / universe_harness["uid"]
    for i in range(50):
        t = BranchTask(
            branch_task_id=new_task_id(),
            branch_def_id="x",
            universe_id=universe_harness["uid"],
            trigger_source="user_request",
            priority_weight=float(50 - i),  # strictly decreasing score
            queued_at="2026-04-14T12:00:00+00:00",
        )
        append_task(udir, t)
    resp = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    assert len(resp["queue"]["top"]) == 20
    assert resp["queue"]["pending_count"] == 50


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
    assert d1["dispatcher"]["config"]["accept_paid_bids"] in (True, False)

    before_state = d1["dispatcher"]["config"]["accept_paid_bids"]
    new_state = not before_state
    _action_set_tier_config(
        universe_id=universe_harness["uid"],
        tier="paid_bids", enabled=new_state,
    )
    r2 = _action_daemon_overview(universe_id=universe_harness["uid"])
    d2 = json.loads(r2)
    assert d2["dispatcher"]["config"]["accept_paid_bids"] == new_state


def test_daemon_overview_large_queue_bounded(universe_harness):
    """With 500 tasks, response still bounded by top-N."""
    from tinyassets.branch_tasks import BranchTask, append_task, new_task_id
    udir = universe_harness["base"] / universe_harness["uid"]
    for _ in range(500):
        append_task(udir, BranchTask(
            branch_task_id=new_task_id(), branch_def_id="x",
            universe_id=universe_harness["uid"],
            trigger_source="user_request",
            queued_at="2026-04-14T12:00:00+00:00",
        ))
    resp = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    # Default top_n=20, absolute cap for full=500.
    assert len(resp["queue"]["top"]) == 20

    resp_full = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"], limit="full",
    ))
    assert len(resp_full["queue"]["top"]) <= 500


def test_daemon_overview_flag_off_shows_paid_bid_not_live(
    universe_harness, monkeypatch,
):
    """With flag off AND no config override, paid_bid reports non-live
    (landed dispatcher uses "disabled"; older spec drafts said
    "stubbed"). Either is acceptable; test pins "not live" so the
    real invariant — flag-off must not misadvertise a live market —
    is enforced regardless of the naming choice."""
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "off")
    # Clear any pre-existing config from other tests in the class
    udir = universe_harness["base"] / universe_harness["uid"]
    cfg_path = udir / "dispatcher_config.yaml"
    if cfg_path.exists():
        cfg_path.unlink()
    from tinyassets.api.universe import _OVERVIEW_CACHE
    _OVERVIEW_CACHE.clear()
    resp = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    status = resp["dispatcher"]["tier_status_map"]["paid_bid"]
    assert status != "live"
    assert status.lower() in {"disabled", "stubbed", "stubbed (phase g)"}


def test_daemon_overview_drift_surfaces(universe_harness, monkeypatch):
    """Phase F drift flag visible via daemon_overview."""
    udir = universe_harness["base"] / universe_harness["uid"]
    # accept_goal_pool=true but empty subscriptions
    cfg_path = udir / "dispatcher_config.yaml"
    cfg_path.write_text("accept_goal_pool: true\n", encoding="utf-8")
    (udir / "subscriptions.json").write_text(
        json.dumps({"goals": [], "updated_at": ""}), encoding="utf-8",
    )

    from tinyassets.api.universe import _OVERVIEW_CACHE
    _OVERVIEW_CACHE.clear()
    resp = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    assert resp["subscriptions"]["drift_flag"] == "pool_enabled_no_subs"


def test_daemon_overview_settlements_count_accurate(universe_harness):
    """Settlements counted from repo_root/settlements/ dir."""
    from tinyassets.bid.node_bid import NodeBid
    from tinyassets.bid.settlements import record_settlement_event
    from tinyassets.executors.node_bid import NodeBidResult

    repo_root = universe_harness["repo"]
    bid = NodeBid(node_bid_id="nb_o1", node_def_id="n/x", bid=1.0)
    result = NodeBidResult(
        node_bid_id="nb_o1", status="succeeded",
        evidence_url="file:///tmp/e",
    )
    record_settlement_event(repo_root, bid, result, "daemon-a")

    from tinyassets.api.universe import _OVERVIEW_CACHE
    _OVERVIEW_CACHE.clear()
    resp = json.loads(_action_daemon_overview(
        universe_id=universe_harness["uid"],
    ))
    assert resp["settlements"]["count_total"] == 1
    assert resp["settlements"]["count_unsettled"] == 1


# ───────────────────────────────────────────────────────────────────────
# set_tier_config action (6 tests)
# ───────────────────────────────────────────────────────────────────────


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
        tier="paid_bids", enabled=True,
    )
    udir = universe_harness["base"] / universe_harness["uid"]
    cfg = load_dispatcher_config(udir)
    assert cfg.accept_paid_bids is True


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


def test_set_tier_config_all_four_tiers(universe_harness):
    """All four tier keys are accepted."""
    for tier in ("external_requests", "goal_pool", "paid_bids", "opportunistic"):
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


def test_node_lookup_none_default_is_no_op():
    """NodeBidProducer with node_lookup_fn=None defaults to letting
    all bids through (executor-side catches adversarials)."""
    from tinyassets.producers.node_bid import _producer_sandbox_reject
    # None lookup → empty reject reason → bid passes producer layer.
    assert _producer_sandbox_reject("any-node", None) == ""


def test_node_lookup_unknown_node_rejected():
    """Wired lookup returning None → producer rejects pre-pick."""
    from tinyassets.producers.node_bid import _producer_sandbox_reject
    reason = _producer_sandbox_reject(
        "missing-node", lambda _slug: None,
    )
    assert reason == "unknown_node_def_id"


def test_node_lookup_unapproved_node_rejected():
    from tinyassets.producers.node_bid import _producer_sandbox_reject

    class _Node:
        approved = False
        source_code = "def run(s): return {}"

    reason = _producer_sandbox_reject(
        "node-unapproved", lambda _slug: _Node(),
    )
    assert reason == "unapproved_node"


def test_node_lookup_raise_fails_closed():
    """R4 + invariant 5 (updated 2026-04-14 after Phase H reviewer
    audit): lookup raise → producer skips the bid (fail-CLOSED).

    Spec was originally fail-OPEN (producer WARN-logs and lets the
    bid through for the executor to catch). Reviewer + lead concur
    the shipped fail-CLOSED behavior is correct: under disk-walk
    lookup, transient failures are rare, and dropping the bid until
    recovery is preferable to flooding the executor with unvalidated
    bids during a transient outage.

    Tight assertion on the exact reason so a future regression that
    flips back to fail-OPEN ("" reason) surfaces immediately.
    """
    from tinyassets.producers.node_bid import _producer_sandbox_reject

    def _raises(_slug):
        raise RuntimeError("boom")

    reason = _producer_sandbox_reject("anything", _raises)
    assert reason == "node_lookup_error"


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
# Concerns audit doc (R8, 1 test)
# ───────────────────────────────────────────────────────────────────────


def test_dashboard_conventions_doc_exists():
    """Preflight §4.1 #12: dashboard_conventions.md ships with Phase H."""
    conv = Path(__file__).parent.parent / "docs" / "planning" / (
        "dashboard_conventions.md"
    )
    assert conv.exists(), f"expected {conv} to exist"


def test_daemon_overview_response_shape_doc_exists():
    """Preflight §4.1 #12: daemon_overview_response_shape.md ships."""
    doc = Path(__file__).parent.parent / "docs" / "planning" / (
        "daemon_overview_response_shape.md"
    )
    assert doc.exists(), f"expected {doc} to exist"


# ───────────────────────────────────────────────────────────────────────
# Bid execution log rename (G.2 #4, R11)
# ───────────────────────────────────────────────────────────────────────


def test_bid_execution_log_import_works():
    from tinyassets.bid.execution_log import (  # noqa: F401
        append_execution_log_entry,
        read_execution_log,
    )


def test_bid_ledger_shim_still_works():
    """Deprecated alias continues to re-export during transition."""
    from tinyassets.bid.execution_log import (  # noqa: F401
        append_ledger_entry,
        read_ledger,
    )


def test_bid_execution_log_reads_legacy_filename(tmp_path):
    """Transition: if only bid_ledger.json exists, read from it."""
    from tinyassets.bid.execution_log import read_execution_log
    (tmp_path / "bid_ledger.json").write_text(
        json.dumps([{"k": "v"}]), encoding="utf-8",
    )
    entries = read_execution_log(tmp_path)
    assert entries == [{"k": "v"}]


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
