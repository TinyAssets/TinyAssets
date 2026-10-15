"""Dashboard pane isolation and tray extension tests.

Covers phase_h_preflight.md (archived in git) §4.3 invariant 2 (pane isolation)
and §4.4 test strategy (pane-isolation, 5 tests) plus tray dashboard
additions (invariant 9, emergency pause).

Pane classes: DispatcherPane, QueuePane.
Handler fan-out: DashboardHandler.refresh_from_overview.
Tray: update_tier_states, _handle_tier_toggle, _handle_pause_all_tiers,
      _handle_show_dashboard.
"""

from __future__ import annotations

from tinyassets.desktop.dashboard import (
    DashboardHandler,
    DispatcherPane,
    QueuePane,
)
from tinyassets.desktop.tray import TrayApp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_OVERVIEW: dict = {
    "dispatcher": {
        "tier_status_map": {
            "external_requests": "enabled",
            "goal_pool": "disabled",
            "opportunistic": "enabled",
        },
        "config": {
            "accept_external_requests": True,
            "accept_goal_pool": False,
            "allow_opportunistic": True,
        },
    },
    "queue": {
        "pending_count": 3,
        "top": [
            {"branch_task_id": "bt1", "trigger_source": "user", "priority_weight": 1.0},
            {"branch_task_id": "bt2", "trigger_source": "pool", "priority_weight": 0.5},
        ],
        "archived_recent_count": 10,
    },
    "run_state": {
        "current_phase": "orient",
        "status": "running",
        "idle_reason": None,
        "last_verdict": "accept",
        "total_words": 5000,
        "total_chapters": 2,
    },
    "gates": {"ladder_count_on_bound_goal": 5, "claims_on_this_universe": 2},
    "activity_tail": ["2026-04-14T00:00:00 [orient] started", "scene complete"],
}


# ---------------------------------------------------------------------------
# DispatcherPane
# ---------------------------------------------------------------------------


class TestDispatcherPane:
    def test_refresh_happy_path(self):
        pane = DispatcherPane()
        pane.refresh(_VALID_OVERVIEW)
        assert pane.tier_status == _VALID_OVERVIEW["dispatcher"]["tier_status_map"]
        assert pane.config == _VALID_OVERVIEW["dispatcher"]["config"]
        assert pane.last_error is None

    def test_refresh_missing_dispatcher_key(self):
        """Gracefully handles absent 'dispatcher' key — returns empty dicts."""
        pane = DispatcherPane()
        pane.refresh({})
        assert pane.tier_status == {}
        assert pane.config == {}
        assert pane.last_error is None

    def test_refresh_malformed_payload_isolated(self):
        """R3 invariant 2: malformed payload does not propagate an exception."""
        pane = DispatcherPane()
        pane.refresh({"dispatcher": "NOT_A_DICT"})  # would raise without isolation
        assert pane.last_error is not None
        assert "str" in pane.last_error or "attribute" in pane.last_error

    def test_summary_shape(self):
        pane = DispatcherPane()
        pane.refresh(_VALID_OVERVIEW)
        s = pane.summary()
        assert "tier_status" in s
        assert "config" in s
        assert "last_error" in s

    def test_refresh_does_not_share_mutable_state(self):
        """summary() returns copies, not live references."""
        pane = DispatcherPane()
        pane.refresh(_VALID_OVERVIEW)
        s = pane.summary()
        s["tier_status"]["new_key"] = "injected"
        # pane's internal state must be unaffected
        assert "new_key" not in pane.tier_status


# ---------------------------------------------------------------------------
# QueuePane
# ---------------------------------------------------------------------------


class TestQueuePane:
    def test_refresh_happy_path(self):
        pane = QueuePane()
        pane.refresh(_VALID_OVERVIEW)
        assert pane.pending_count == 3
        assert len(pane.top_tasks) == 2
        assert pane.idle_reason is None
        assert pane.last_error is None

    def test_refresh_surfaces_idle_reason(self):
        """QueuePane exposes run_state.idle_reason for §4.9 Q9 item #2."""
        overview = dict(_VALID_OVERVIEW)
        overview["run_state"] = dict(overview["run_state"])
        overview["run_state"]["idle_reason"] = "universe_cycle_noop_streak"
        pane = QueuePane()
        pane.refresh(overview)
        assert pane.idle_reason == "universe_cycle_noop_streak"

    def test_refresh_malformed_payload_isolated(self):
        """R3 invariant 2: bad payload captured, not raised."""
        pane = QueuePane()
        pane.refresh({"queue": "NOT_A_DICT"})
        assert pane.last_error is not None

    def test_summary_shape(self):
        pane = QueuePane()
        pane.refresh(_VALID_OVERVIEW)
        s = pane.summary()
        assert "pending_count" in s
        assert "top_tasks" in s
        assert "idle_reason" in s


# ---------------------------------------------------------------------------
# DashboardHandler.refresh_from_overview — fan-out isolation
# ---------------------------------------------------------------------------


class TestDashboardHandlerOverviewFanOut:
    def test_all_panes_updated_on_valid_overview(self):
        handler = DashboardHandler()
        handler.refresh_from_overview(_VALID_OVERVIEW)
        assert handler.dispatcher_pane.tier_status != {}
        assert handler.queue_pane.pending_count == 3

    def test_malformed_one_pane_does_not_crash_others(self):
        """R3 invariant 2: if one pane's refresh raises, others still run."""

        class _BrokenPane:
            last_error = None

            def refresh(self, _data):
                raise RuntimeError("pane on fire")

        handler = DashboardHandler()
        broken = _BrokenPane()
        # Patch one pane with a broken one.
        handler.queue_pane = broken  # type: ignore[assignment]
        handler.refresh_from_overview(_VALID_OVERVIEW)
        # dispatcher_pane must still have been refreshed.
        assert handler.dispatcher_pane.tier_status != {}

    def test_summary_includes_all_pane_keys(self):
        handler = DashboardHandler()
        handler.refresh_from_overview(_VALID_OVERVIEW)
        s = handler.summary()
        assert "dispatcher" in s
        assert "queue" in s
        assert "earnings" not in s

    def test_empty_overview_does_not_raise(self):
        """Defensive: totally empty dict must not crash refresh_from_overview."""
        handler = DashboardHandler()
        handler.refresh_from_overview({})
        # No assertion needed — absence of exception is the pass condition.


# ---------------------------------------------------------------------------
# TrayApp dashboard additions
# ---------------------------------------------------------------------------


class TestTrayAppPhaseH:
    def _make_tray(self):
        toggle_log: list[tuple[str, bool]] = []
        pause_log: list[bool] = []
        dashboard_log: list[bool] = []

        tray = TrayApp(
            on_tier_toggle=lambda t, e: toggle_log.append((t, e)),
            on_pause_all_tiers=lambda: pause_log.append(True),
            on_show_dashboard=lambda: dashboard_log.append(True),
        )
        return tray, toggle_log, pause_log, dashboard_log

    def test_tier_names_constant(self):
        assert set(TrayApp.TIER_NAMES) == {
            "external_requests", "goal_pool", "opportunistic"
        }

    def test_update_tier_states_updates_internal_state(self):
        tray, *_ = self._make_tray()
        tray.update_tier_states({"external_requests": False, "goal_pool": True})
        with tray._lock:
            assert tray._tier_states["external_requests"] is False
            assert tray._tier_states["goal_pool"] is True

    def test_handle_tier_toggle_calls_callback(self):
        tray, toggle_log, *_ = self._make_tray()
        tray._handle_tier_toggle("goal_pool", False)
        assert ("goal_pool", False) in toggle_log

    def test_handle_tier_toggle_updates_local_state(self):
        tray, *_ = self._make_tray()
        tray._handle_tier_toggle("opportunistic", False)
        with tray._lock:
            assert tray._tier_states["opportunistic"] is False

    def test_handle_pause_all_tiers_calls_callback(self):
        tray, _, pause_log, _ = self._make_tray()
        tray._handle_pause_all_tiers()
        assert pause_log == [True]
        assert tray._emergency_off is True

    def test_handle_pause_all_tiers_toggles_emergency_off(self):
        """Second click restores emergency_off to False."""
        tray, _, pause_log, _ = self._make_tray()
        tray._handle_pause_all_tiers()
        tray._handle_pause_all_tiers()
        assert len(pause_log) == 2
        assert tray._emergency_off is False

    def test_handle_show_dashboard_calls_callback(self):
        tray, _, _, dashboard_log = self._make_tray()
        tray._handle_show_dashboard()
        assert dashboard_log == [True]

    def test_no_crash_when_callbacks_absent(self):
        """Tray without dashboard callbacks handles calls gracefully."""
        tray = TrayApp()
        tray._handle_tier_toggle("goal_pool", False)
        tray._handle_pause_all_tiers()
        tray._handle_show_dashboard()
        # No assertion needed — absence of exception is the pass condition.

    def test_update_tier_states_ignores_unknown_tiers(self):
        tray, *_ = self._make_tray()
        tray.update_tier_states({"unknown_tier_xyz": True})
        with tray._lock:
            assert "unknown_tier_xyz" not in tray._tier_states

    def test_emergency_off_reflected_in_tier_states_after_pause(
        self, monkeypatch
    ):
        """After pause_all_tiers fires, tier callback is called once per tier
        when the full 4-tier disable flow is implemented by the caller.
        The tray itself just fires on_pause_all_tiers — the caller is
        responsible for iterating tiers.  Assert callback was invoked."""
        tray, _, pause_log, _ = self._make_tray()
        tray._handle_pause_all_tiers()
        assert len(pause_log) == 1  # called once; caller loops over tiers
