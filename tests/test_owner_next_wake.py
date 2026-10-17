"""Local schedule projection: empty, paused, pending, retry and proactive rows."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tinyassets.automations import ONCE_RETRY_SECONDS, Automation, AutomationStore
from tinyassets.control_plane.next_wake import next_wake_at

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def row(**changes):
    base = Automation(
        automation_id="interval", universe_id="home", owner_principal_id="a", name="test",
        branch_def_id="branch", trigger_kind="interval", interval_seconds=600, cron_expr="",
        inputs={}, desired_state="active", pause_reason="", revision=1,
        created_at=NOW.isoformat(), updated_at=NOW.isoformat(), retired_at="",
        last_due_at="", last_run_id="", last_reason="", last_finished_at="")
    return replace(base, **changes)


def test_local_automations_use_the_existing_due_rules_and_one_shot_retry(tmp_path):
    assert next_wake_at(tmp_path, now=NOW) is None
    store = AutomationStore(tmp_path)
    store.insert(row())
    assert next_wake_at(tmp_path, now=NOW) == NOW.timestamp() + 600
    store.insert(row(automation_id="once", trigger_kind="once", interval_seconds=0,
                     not_before=NOW.isoformat()))
    assert next_wake_at(tmp_path, now=NOW) == NOW.timestamp()
    assert store.claim_attempt("once", NOW.isoformat(), now=NOW)
    assert next_wake_at(tmp_path, now=NOW) == NOW.timestamp() + ONCE_RETRY_SECONDS
    home = tmp_path / "home"
    home.mkdir()
    (home / ".pause").touch()
    assert next_wake_at(tmp_path, now=NOW) is None
    (home / ".pause").unlink()
    store.pause_for_reason("once", reason="owner paused", now=NOW)
    assert next_wake_at(tmp_path, now=NOW) == NOW.timestamp() + 600


def test_pending_wakes_and_delivery_retries_are_part_of_the_minimum(tmp_path):
    from tinyassets import agent_activities, bound_requests
    from tinyassets.control_plane.agent_wakes import _db

    home = tmp_path / "home"
    home.mkdir()
    with closing(agent_activities._connect(home, create=True)):
        pass
    # History alone, before any continuation was registered, is not scheduled work.
    assert next_wake_at(tmp_path, now=NOW) is None
    with closing(bound_requests.connect(home)) as db, db:
        db.execute("INSERT INTO activity_events "
                   "(activity_id,seq,ts,kind,line,wake_required,next_attempt_at) "
                   "VALUES ('task',1,0,'result','retry',1,300)")
        db.execute("INSERT INTO request_answer_deliveries "
                   "(request_id,event_key,origin_json,outcome_json,next_attempt_at) "
                   "VALUES ('request','answer','{}','{}',200)")
    with closing(_db(home)) as db, db:
        db.execute("INSERT INTO agent_wakes (wake_id,owner,agent,note,condition_json,"
                   "due_at,expires_at,interval_seconds,max_fires,max_checks,backoff_seconds,"
                   "max_backoff_seconds,next_check,created_at) "
                   "VALUES ('wake','a','main','follow up','{}',100,1000,0,1,5,10,60,100,0)")
        db.commit()
        assert next_wake_at(tmp_path, now=NOW) == 100
        db.execute("UPDATE agent_wakes SET status='done'")
    assert next_wake_at(tmp_path, now=NOW) == 200
    with closing(bound_requests.connect(home)) as db, db:
        db.execute("UPDATE request_answer_deliveries SET delivered_at=201")
    assert next_wake_at(tmp_path, now=NOW) == 300
    with closing(bound_requests.connect(home)) as db, db:
        db.execute("UPDATE activity_events SET processed_at=301")
    assert next_wake_at(tmp_path, now=NOW) is None


def test_proactive_trigger_respects_active_hours_and_disabled_handler(tmp_path, monkeypatch):
    from tinyassets.control_plane import wake
    from tinyassets.control_plane.triggers import KIND_PROACTIVE, TriggerStore

    monkeypatch.setattr(wake, "_handlers", {})
    monkeypatch.delenv("TINYASSETS_PROACTIVE_CADENCE", raising=False)
    store = TriggerStore(tmp_path)
    trigger = store.ensure(KIND_PROACTIVE, command_center_id="home",
                           owner_principal_id="a", now=NOW)
    assert next_wake_at(tmp_path, now=NOW) is None
    wake.register_wake_handler(KIND_PROACTIVE, lambda *a: None)
    night = NOW.replace(hour=23)
    expected = (night + timedelta(days=1)).replace(hour=8)
    assert next_wake_at(tmp_path, now=night) == expected.timestamp()
    store.set_enabled(trigger.trigger_key, principal_id="a", enabled=False, now=NOW)
    assert next_wake_at(tmp_path, now=NOW) is None


def test_every_owner_poll_reports_even_on_failure_and_report_failure_is_loud(tmp_path, monkeypatch):
    from tinyassets import owner_runtime
    from tinyassets.control_plane import next_wake
    from tinyassets.control_plane.tick import AssignedQueueConsumer

    consumer = AssignedQueueConsumer(tmp_path)
    monkeypatch.setattr(owner_runtime, "current", lambda: SimpleNamespace())
    calls = []
    monkeypatch.setattr(next_wake, "report", calls.append)
    monkeypatch.setattr(consumer, "_poll_once", lambda: 3)
    try:
        assert consumer.poll_once() == 3

        def fail():
            raise RuntimeError("tick failed")

        monkeypatch.setattr(consumer, "_poll_once", fail)
        with pytest.raises(RuntimeError, match="tick failed"):
            consumer.poll_once()
        assert calls == [tmp_path, tmp_path]
        monkeypatch.setattr(consumer, "_poll_once", lambda: 0)
        monkeypatch.setattr(next_wake, "report", lambda base: fail())
        with pytest.raises(RuntimeError, match="tick failed"):
            consumer.poll_once()
    finally:
        consumer.stop()
