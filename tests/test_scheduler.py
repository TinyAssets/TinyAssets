"""Tests for tinyassets/scheduler.py — scheduled + event-triggered branch invocation.

Spec: openspec/specs/user-owned-automations/spec.md §Scheduled + event-triggered branch invocation.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tinyassets.runs import initialize_runs_db
from tinyassets.control_plane.events import (
    VALID_EVENT_TYPES,
    CronParseError,
    CronSchedule,
    Scheduler,
    SchedulerEvent,
    register_subscription,
    unregister_subscription,
)

# ─── Cron parser ──────────────────────────────────────────────────────────────

class TestCronParse:
    def test_wildcard(self):
        s = CronSchedule.parse("* * * * *")
        t = time.strptime("2026-04-24 12:30:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)

    def test_exact_minute_hour(self):
        s = CronSchedule.parse("30 12 * * *")
        assert s.matches(time.strptime("2026-04-24 12:30:00", "%Y-%m-%d %H:%M:%S"))
        assert not s.matches(time.strptime("2026-04-24 12:31:00", "%Y-%m-%d %H:%M:%S"))
        assert not s.matches(time.strptime("2026-04-24 13:30:00", "%Y-%m-%d %H:%M:%S"))

    def test_range(self):
        s = CronSchedule.parse("0 9-17 * * *")
        assert s.matches(time.strptime("2026-04-24 09:00:00", "%Y-%m-%d %H:%M:%S"))
        assert s.matches(time.strptime("2026-04-24 17:00:00", "%Y-%m-%d %H:%M:%S"))
        assert not s.matches(time.strptime("2026-04-24 08:00:00", "%Y-%m-%d %H:%M:%S"))

    def test_step(self):
        s = CronSchedule.parse("*/15 * * * *")
        assert s.matches(time.strptime("2026-04-24 12:00:00", "%Y-%m-%d %H:%M:%S"))
        assert s.matches(time.strptime("2026-04-24 12:15:00", "%Y-%m-%d %H:%M:%S"))
        assert s.matches(time.strptime("2026-04-24 12:30:00", "%Y-%m-%d %H:%M:%S"))
        assert not s.matches(time.strptime("2026-04-24 12:01:00", "%Y-%m-%d %H:%M:%S"))

    def test_comma_list(self):
        s = CronSchedule.parse("0,30 * * * *")
        assert s.matches(time.strptime("2026-04-24 12:00:00", "%Y-%m-%d %H:%M:%S"))
        assert s.matches(time.strptime("2026-04-24 12:30:00", "%Y-%m-%d %H:%M:%S"))
        assert not s.matches(time.strptime("2026-04-24 12:15:00", "%Y-%m-%d %H:%M:%S"))

    def test_bad_field_count(self):
        with pytest.raises(CronParseError, match="5 fields"):
            CronSchedule.parse("* * * *")

    def test_out_of_range(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("61 * * * *")

    def test_bad_step(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("*/0 * * * *")

    def test_month_name(self):
        s = CronSchedule.parse("0 0 1 jan *")
        assert s.matches(time.strptime("2026-01-01 00:00:00", "%Y-%m-%d %H:%M:%S"))
        assert not s.matches(time.strptime("2026-02-01 00:00:00", "%Y-%m-%d %H:%M:%S"))


# ─── DB helpers (fixture) ─────────────────────────────────────────────────────

@pytest.fixture()
def base_path(tmp_path: Path) -> Path:
    initialize_runs_db(tmp_path)
    return tmp_path


# ─── register_schedule ────────────────────────────────────────────────────────


# ─── unregister_schedule ─────────────────────────────────────────────────────


# ─── register_subscription ───────────────────────────────────────────────────

class TestRegisterSubscription:
    def test_valid_event_type(self, base_path):
        sub_id = register_subscription(
            base_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type="source:s1",
        )
        assert sub_id

    def test_invalid_event_type(self, base_path):
        with pytest.raises(ValueError, match="unknown event_type"):
            register_subscription(
                base_path,
                branch_def_id="b1",
                owner_actor="alice",
                event_type="not_a_real_event",
            )

    def test_no_count_of_subscriptions_per_owner(self, base_path):
        """Plan item 6: each fire is charged as a run instead of a ceiling of 20."""
        for i in range(25):
            register_subscription(
                base_path,
                branch_def_id=f"b{i}",
                owner_actor="alice",
                event_type="source:s1",
            )

    @pytest.mark.parametrize(
        "etype", ["canon_change", "branch_run_completed", "canon_upload", "pr_open"],
    )
    def test_types_nothing_emits_are_refused(self, base_path, etype):
        """A subscription to an event with no emitter would be stored and never
        fire. Engine events are automation triggers (``automation_events``)."""
        assert not VALID_EVENT_TYPES
        with pytest.raises(ValueError, match="unknown event_type"):
            register_subscription(
                base_path, branch_def_id="b1", owner_actor="alice", event_type=etype,
            )


# ─── unregister_subscription ─────────────────────────────────────────────────

class TestUnregisterSubscription:
    def test_owner_can_unregister(self, base_path):
        sub_id = register_subscription(
            base_path, branch_def_id="b1", owner_actor="alice", event_type="source:s1"
        )
        result = unregister_subscription(base_path, sub_id, requesting_actor="alice")
        assert result is True

    def test_non_owner_rejected(self, base_path):
        sub_id = register_subscription(
            base_path, branch_def_id="b1", owner_actor="alice", event_type="source:s1"
        )
        with pytest.raises(PermissionError):
            unregister_subscription(base_path, sub_id, requesting_actor="bob")

    def test_admin_can_unregister(self, base_path):
        sub_id = register_subscription(
            base_path, branch_def_id="b1", owner_actor="alice", event_type="source:s1"
        )
        result = unregister_subscription(base_path, sub_id, requesting_actor="admin", admin=True)
        assert result is True


# ─── Scheduler tick loop (fake clock) ────────────────────────────────────────

#: The universe a tick-loop test's schedules belong to. A schedule that names no
#: universe and no principal is LEGACY and deliberately never fires
#: (user-owned-automations 2.1), so a test about WHEN a schedule fires has to
#: register an owned one.
_UID = "u-tick-owner"


def _owned(**overrides):
    """Registration kwargs for a schedule owned by ``_UID``'s founder."""
    owned = {
        "owner_actor": f"universe:{_UID}",
        "universe_id": _UID,
        "owner_principal_id": "founder-tick",
    }
    owned.update(overrides)
    return owned


# ─── Scheduler event loop ─────────────────────────────────────────────────────

class TestSchedulerEventDispatch:
    def _make_scheduler(self, base_path, run_calls):
        def run_fn(branch_def_id, actor, inputs, run_name):
            run_calls.append((branch_def_id, actor, inputs, run_name))

        return Scheduler(base_path, run_fn)

    def test_event_fires_matching_subscription(self, base_path):
        run_calls: list = []
        register_subscription(
            base_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type="source:s1",
        )
        s = self._make_scheduler(base_path, run_calls)
        event = SchedulerEvent(event_type="source:s1", payload={"file": "world.md"})
        s._dispatch_event(event)
        assert len(run_calls) == 1
        assert run_calls[0][0] == "b1"
        assert "alice" in run_calls[0][1]  # actor includes owner

    def test_event_does_not_fire_wrong_type(self, base_path):
        run_calls: list = []
        register_subscription(
            base_path, branch_def_id="b1", owner_actor="alice", event_type="source:s1"
        )
        s = self._make_scheduler(base_path, run_calls)
        event = SchedulerEvent(event_type="source:s2", payload={})
        s._dispatch_event(event)
        assert len(run_calls) == 0

    def test_idempotency_no_double_fire(self, base_path):
        run_calls: list = []
        register_subscription(
            base_path, branch_def_id="b1", owner_actor="alice", event_type="source:s1"
        )
        s = self._make_scheduler(base_path, run_calls)
        event = SchedulerEvent(event_type="source:s1", event_id="fixed-id")
        s._dispatch_event(event)
        s._dispatch_event(event)  # same event_id — should not double-fire
        assert len(run_calls) == 1

    def test_event_filter_match(self, base_path):
        run_calls: list = []
        register_subscription(
            base_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type="source:s3",
            filter_json={"branch_def_id": "target-branch"},
        )
        s = self._make_scheduler(base_path, run_calls)
        # Matching event
        s._dispatch_event(SchedulerEvent(
            event_type="source:s3",
            event_id="e1",
            payload={"branch_def_id": "target-branch"},
        ))
        # Non-matching event
        s._dispatch_event(SchedulerEvent(
            event_type="source:s3",
            event_id="e2",
            payload={"branch_def_id": "other-branch"},
        ))
        assert len(run_calls) == 1

    def test_inputs_mapping_applied(self, base_path):
        run_calls: list = []
        register_subscription(
            base_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type="source:s1",
            inputs_mapping={"target_file": "file"},
        )
        s = self._make_scheduler(base_path, run_calls)
        s._dispatch_event(SchedulerEvent(
            event_type="source:s1",
            payload={"file": "world.md"},
        ))
        assert run_calls[0][2] == {"target_file": "world.md"}

    def test_unregistered_subscription_not_fired(self, base_path):
        run_calls: list = []
        sub_id = register_subscription(
            base_path, branch_def_id="b1", owner_actor="alice", event_type="source:s1"
        )
        unregister_subscription(base_path, sub_id, requesting_actor="alice")
        s = self._make_scheduler(base_path, run_calls)
        s._dispatch_event(SchedulerEvent(event_type="source:s1"))
        assert len(run_calls) == 0


# ─── DB schema (initialize_runs_db includes scheduler tables) ─────────────────

def test_initialize_runs_db_creates_scheduler_tables(tmp_path):
    """initialize_runs_db must create branch_schedules + branch_subscriptions."""
    import sqlite3
    initialize_runs_db(tmp_path)
    db = tmp_path / ".runs.db"
    conn = sqlite3.connect(str(db))
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "branch_schedules" in tables
    assert "branch_subscriptions" in tables
    assert "scheduler_delivered_events" in tables
