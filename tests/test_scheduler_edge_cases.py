"""Edge-case tests for tinyassets/scheduler.py.

Spec: docs/vetted-specs.md §Scheduled + event-triggered branch invocation.

These extend the 39 baseline tests in tests/test_scheduler.py with:
- Cron parser: invalid format strings, boundary values
- Event registry: duplicate subscriptions, unsubscribe-nonexistent
- Persistence: schedule survives restart (hydrate from DB)
- Concurrency: two scheduler instances compete for same trigger

If a test surfaces a real bug, it is marked pytest.mark.xfail(strict=False)
with a comment explaining the bug. Do NOT edit tinyassets/scheduler.py here.
"""

from __future__ import annotations

import time

import pytest

from tinyassets.runs import initialize_runs_db
from tinyassets.scheduler import (
    CronParseError,
    CronSchedule,
    Scheduler,
    register_subscription,
    unregister_subscription,
)

# ─── Cron parser edge cases ───────────────────────────────────────────────────

class TestCronParserEdgeCases:
    def test_wrong_field_count_too_few(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("* * * *")

    def test_wrong_field_count_too_many(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("* * * * * *")

    def test_empty_string(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("")

    def test_non_numeric_field(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("abc * * * *")

    def test_minute_boundary_zero(self):
        s = CronSchedule.parse("0 * * * *")
        t = time.strptime("2026-04-24 12:00:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)

    def test_minute_boundary_59(self):
        s = CronSchedule.parse("59 * * * *")
        t = time.strptime("2026-04-24 12:59:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)

    def test_minute_out_of_range_60(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("60 * * * *")

    def test_hour_boundary_zero(self):
        s = CronSchedule.parse("0 0 * * *")
        t = time.strptime("2026-04-24 00:00:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)

    def test_hour_boundary_23(self):
        s = CronSchedule.parse("0 23 * * *")
        t = time.strptime("2026-04-24 23:00:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)

    def test_hour_out_of_range_24(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("0 24 * * *")

    def test_step_negative_raises(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("*/-1 * * * *")

    def test_range_inverted_raises(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("30-10 * * * *")

    def test_named_month_jan(self):
        s = CronSchedule.parse("0 0 1 jan *")
        t = time.strptime("2026-01-01 00:00:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)
        t2 = time.strptime("2026-02-01 00:00:00", "%Y-%m-%d %H:%M:%S")
        assert not s.matches(t2)

    def test_named_dow_sun(self):
        s = CronSchedule.parse("0 0 * * sun")
        # 2026-04-26 is a Sunday (Python tm_wday=6; cron DOW=0)
        t = time.strptime("2026-04-26 00:00:00", "%Y-%m-%d %H:%M:%S")
        assert s.matches(t)

    def test_wildcard_step_on_range(self):
        s = CronSchedule.parse("0-30/10 * * * *")
        assert 0 in s.minutes
        assert 10 in s.minutes
        assert 20 in s.minutes
        assert 30 in s.minutes
        assert 31 not in s.minutes

    def test_dom_boundary_31(self):
        s = CronSchedule.parse("0 0 31 * *")
        assert 31 in s.days_of_month

    def test_dom_out_of_range_0(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("0 0 0 * *")

    def test_dow_boundary_6(self):
        s = CronSchedule.parse("0 0 * * 6")
        assert 6 in s.days_of_week

    def test_dow_out_of_range_7(self):
        with pytest.raises(CronParseError):
            CronSchedule.parse("0 0 * * 7")


# ─── Persistence edge cases ───────────────────────────────────────────────────


# ─── Event registry edge cases ───────────────────────────────────────────────

class TestEventRegistryEdgeCases:
    def test_subscribe_same_event_type_twice_creates_two_rows(self, tmp_path):
        initialize_runs_db(tmp_path)
        event_type = "source:s1"
        sid1 = register_subscription(
            tmp_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type=event_type,
        )
        sid2 = register_subscription(
            tmp_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type=event_type,
        )
        assert sid1 != sid2

    def test_unsubscribe_nonexistent_returns_false(self, tmp_path):
        initialize_runs_db(tmp_path)
        result = unregister_subscription(
            tmp_path,
            "nonexistent-sub-id",
            requesting_actor="alice",
        )
        assert result is False

    def test_unsubscribe_other_owners_sub_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        event_type = "source:s1"
        sid = register_subscription(
            tmp_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type=event_type,
        )
        with pytest.raises(PermissionError):
            unregister_subscription(
                tmp_path, sid, requesting_actor="bob"
            )

    def test_admin_can_unsubscribe_any_owner(self, tmp_path):
        initialize_runs_db(tmp_path)
        event_type = "source:s1"
        sid = register_subscription(
            tmp_path,
            branch_def_id="b1",
            owner_actor="alice",
            event_type=event_type,
        )
        result = unregister_subscription(
            tmp_path, sid, requesting_actor="admin-bot", admin=True
        )
        assert result is True

    def test_invalid_event_type_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="unknown event_type"):
            register_subscription(
                tmp_path,
                branch_def_id="b1",
                owner_actor="alice",
                event_type="not_a_real_event",
            )


# ─── Concurrency edge cases ───────────────────────────────────────────────────


# ─── Schedule/subscription rate-limit edge cases ─────────────────────────────


class TestSchedulerConcurrency:
    """The event loop's lifecycle (the tick loop is retired with schedules)."""

    def test_scheduler_start_is_idempotent(self, tmp_path):
        initialize_runs_db(tmp_path)

        def _fake_run(bid, actor, inputs, name):
            pass

        sched = Scheduler(tmp_path, _fake_run)
        sched.start()
        try:
            first = sched._event_thread
            # Calling start() again must not raise or spawn another thread.
            sched.start()
            assert sched._event_thread is first
            assert first is not None and first.is_alive()
        finally:
            sched.stop()

    def test_two_schedulers_can_coexist_without_deadlock(self, tmp_path):
        initialize_runs_db(tmp_path)

        def _fake_run(bid, actor, inputs, name):
            pass

        sched1 = Scheduler(tmp_path, _fake_run)
        sched2 = Scheduler(tmp_path, _fake_run)
        sched1.start()
        sched2.start()
        try:
            assert sched1.is_alive()
            assert sched2.is_alive()
        finally:
            sched1.stop()
            sched2.stop()

    def test_stop_terminates_threads(self, tmp_path):
        initialize_runs_db(tmp_path)

        def _noop(bid, actor, inputs, name):
            pass

        sched = Scheduler(tmp_path, _noop)
        sched.start()
        assert sched._event_thread is not None
        sched.stop(timeout=2.0)
        assert not sched._event_thread.is_alive()
        assert not sched.is_alive()
