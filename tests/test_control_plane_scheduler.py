"""Control-plane scheduler (target design D7, change control-plane-scheduler).

The owner tick fires owed triggers under the owner lease, coalesces to one
pending fire, never fires a due instant twice (restart included), decays the
proactive cadence with engagement, and reads platform state only.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.control_plane import cadence
from tinyassets.control_plane.lease import (
    LeaseLost,
    SingleProcessLease,
    current_owner_lease,
    set_owner_lease,
)
from tinyassets.control_plane.scheduler import (
    ControlPlaneScheduler,
    ensure_proactive_trigger,
    note_owner_engagement,
    scheduler_metrics,
)
from tinyassets.control_plane.triggers import (
    DB_FILENAME,
    KIND_PROACTIVE,
    TriggerOwnershipError,
    TriggerStore,
)
from tinyassets.control_plane.wake import (
    WakeResult,
    register_wake_handler,
    unregister_wake_handler,
)

UTC = timezone.utc
CC = "cc-01JTESTCOMMANDCENTER0000000"
OWNER = "user:alice"
#: A Monday, 09:00 UTC: inside the default 08:00-22:00 active hours.
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def _utc(_base: Path, _owner: str) -> ZoneInfo:
    return ZoneInfo("UTC")


class _Handler:
    """A wake handler that records each fire and returns a run id."""

    def __init__(self, *, declined: str = "", raises: bool = False) -> None:
        self.calls: list = []
        self.declined = declined
        self.raises = raises

    def __call__(self, base: Path, request):
        self.calls.append(request)
        if self.raises:
            raise RuntimeError("handler broke")
        if self.declined:
            return WakeResult(declined=self.declined)
        return WakeResult(run_id=f"run-{len(self.calls)}")


@pytest.fixture
def handler():
    h = _Handler()
    register_wake_handler(KIND_PROACTIVE, h, replace=True)
    yield h
    unregister_wake_handler(KIND_PROACTIVE)


@pytest.fixture(autouse=True)
def _lease_and_policy(monkeypatch):
    monkeypatch.delenv(cadence.POLICY_ENV, raising=False)
    previous = set_owner_lease(SingleProcessLease())
    yield
    set_owner_lease(previous)


def _scheduler(base: Path, *, live=None, lease=None):
    return ControlPlaneScheduler(
        base,
        live=live or (lambda _b, _r: False),
        zone_for=_utc,
        lease=lease,
    )


def _enrol(base: Path, *, at: datetime = T0) -> str:
    return ensure_proactive_trigger(
        base, command_center_id=CC, owner_principal_id=OWNER, now=at,
    ).trigger_key


def _fires(base: Path) -> list[dict]:
    with sqlite3.connect(base / DB_FILENAME) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trigger_fires ORDER BY due_at")]


# -- cadence policy ---------------------------------------------------------


def test_engaged_cadence_is_four_a_day_inside_active_hours():
    policy = cadence.DEFAULT_POLICY
    zone = ZoneInfo("UTC")
    fires = []
    last = None
    created = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    moment = created
    while moment < created + timedelta(days=1):
        due, state, _ = cadence.due_instant(
            policy, zone=zone, created_at=created, engaged_at=created - timedelta(hours=1),
            last_due_at=last, now=moment,
        )
        if due <= moment:
            fires.append(due)
            last = due
        moment += timedelta(minutes=5)
    assert state == cadence.ENGAGED
    assert [f.hour for f in fires] == [8, 12, 16, 20]


def test_decay_states_follow_days_since_engagement_and_reset():
    policy = cadence.DEFAULT_POLICY
    engaged = T0
    assert cadence.decay_state(policy, engaged_at=engaged, now=T0 + timedelta(days=6)) == "engaged"
    assert cadence.decay_state(policy, engaged_at=engaged, now=T0 + timedelta(days=8)) == "cooling"
    assert cadence.decay_state(policy, engaged_at=engaged, now=T0 + timedelta(days=31)) == "dormant"
    # The next interaction resets to engaged.
    later = T0 + timedelta(days=40)
    back = cadence.decay_state(policy, engaged_at=later, now=later + timedelta(hours=1))
    assert back == "engaged"


def test_a_wake_waits_the_idle_period_after_the_owner_interacted():
    policy = cadence.DEFAULT_POLICY
    due, _, _ = cadence.due_instant(
        policy, zone=ZoneInfo("UTC"), created_at=T0 - timedelta(days=2),
        engaged_at=T0, last_due_at=T0 - timedelta(hours=5), now=T0,
    )
    assert due == T0 + timedelta(minutes=30)


def test_a_night_outside_active_hours_is_not_counted_as_coalesced():
    policy = cadence.DEFAULT_POLICY
    last = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
    morning = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    due, _, collapsed = cadence.due_instant(
        policy, zone=ZoneInfo("UTC"), created_at=last - timedelta(days=1),
        engaged_at=last - timedelta(hours=1), last_due_at=last, now=morning,
    )
    assert due == morning and collapsed == 0


def test_active_hours_are_the_owners_clock():
    policy = cadence.DEFAULT_POLICY
    pacific = ZoneInfo("America/Los_Angeles")
    # 09:00 UTC is 02:00 in Los Angeles: outside active hours, so the fire
    # waits for 08:00 local (15:00 UTC in October).
    due, _, _ = cadence.due_instant(
        policy, zone=pacific, created_at=T0 - timedelta(days=1),
        engaged_at=T0 - timedelta(days=1), last_due_at=T0 - timedelta(hours=5), now=T0,
    )
    assert due == datetime(2026, 10, 5, 15, 0, tzinfo=UTC)


def test_the_policy_is_one_default_overridable_by_env_and_refuses_garbage(monkeypatch):
    assert cadence.deploy_policy() == cadence.DEFAULT_POLICY
    monkeypatch.setenv(cadence.POLICY_ENV, json.dumps({"cooling_period_s": 4 * 3600,
                                                       "dormant_period_s": 4 * 3600}))
    flat = cadence.deploy_policy()
    # Equal periods turn decay off without a code change.
    assert {flat.period_for(s) for s in cadence.DECAY_STATES} == {4 * 3600}
    monkeypatch.setenv(cadence.POLICY_ENV, "{not json")
    with pytest.raises(ValueError):
        cadence.deploy_policy()
    monkeypatch.setenv(cadence.POLICY_ENV, json.dumps({"engaged_period_s": 10}))
    with pytest.raises(ValueError):
        cadence.deploy_policy()
    monkeypatch.setenv(cadence.POLICY_ENV, json.dumps({"no_such_field": 1}))
    with pytest.raises(ValueError):
        cadence.deploy_policy()


# -- the owner tick ----------------------------------------------------------


def test_a_due_trigger_fires_once_and_records_lag_and_generation(tmp_path, handler):
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    report = _scheduler(tmp_path).tick(T0)
    assert report.fired == [key]
    assert len(handler.calls) == 1
    request = handler.calls[0]
    assert request.command_center_id == CC and request.owner_principal_id == OWNER
    assert request.decay_state == "engaged"
    (fire,) = _fires(tmp_path)
    assert fire["outcome"] == "started" and fire["run_id"] == "run-1"
    assert fire["owner_generation"] == 1
    # Enrolled 07:00; the idle wait ends 07:30, outside active hours, so it was
    # owed from 08:00 and fired at 09:00.
    assert fire["due_at"] == "2026-10-05T08:00:00Z"
    assert fire["lag_s"] == 3600.0
    # Nothing more is owed until the next window.
    assert _scheduler(tmp_path).tick(T0 + timedelta(minutes=5)).fired == []


def test_no_double_fire_across_a_restart(tmp_path, handler):
    """Two owner processes either side of a restart, at the same instant, derive
    the same due_at; the (trigger_key, due_at) fence admits one fire."""
    _enrol(tmp_path, at=T0 - timedelta(hours=2))
    first = _scheduler(tmp_path)
    second = _scheduler(tmp_path)
    assert len(first.tick(T0).fired) == 1
    assert second.tick(T0).fired == []
    assert len(handler.calls) == 1
    assert len(_fires(tmp_path)) == 1


def test_a_claim_that_never_started_is_settled_lost_not_replayed(tmp_path, handler):
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    store = TriggerStore(tmp_path)
    trigger = store.get(key)
    due = T0 - timedelta(hours=1, minutes=30)
    # The previous process claimed and died before calling its handler -- in
    # the SAME second the successor starts, under the same generation.
    assert store.claim_fire(key, due_at=due, expected_revision=trigger.revision,
                            owner_generation=1, owner_incarnation="dead-process",
                            collapsed=0, now=T0)
    restarted = _scheduler(tmp_path)
    report = restarted.tick(T0)
    assert report.lost_settled == 1
    assert report.fired == []  # the due instant was spent; the next is at +4 h
    assert handler.calls == []
    assert [f["outcome"] for f in _fires(tmp_path)] == ["lost_on_restart"]


def test_single_flight_waits_on_a_live_run_then_collapses_missed_windows(tmp_path, handler):
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    live = {"run-1"}
    sched = _scheduler(tmp_path, live=lambda _b, run_id: run_id in live)
    assert sched.tick(T0).fired == [key]
    # The run outlives three windows: the trigger waits, one pending fire.
    later = T0 + timedelta(hours=12, minutes=10)
    assert sched.tick(later).waiting_on_run == [key]
    assert len(handler.calls) == 1
    live.clear()
    assert sched.tick(later).fired == [key]
    assert len(handler.calls) == 2
    trigger = TriggerStore(tmp_path).get(key)
    assert trigger.coalesced_total == 2  # windows folded into the one fire
    assert scheduler_metrics(tmp_path, now=later)["proactive"]["coalesced_total"] == 2


def test_no_fire_without_the_owner_lease(tmp_path, handler):
    class _NotHeld(SingleProcessLease):
        def held(self) -> bool:
            return False

    _enrol(tmp_path, at=T0 - timedelta(hours=2))
    report = _scheduler(tmp_path, lease=_NotHeld()).tick(T0)
    assert report.lease_held is False
    assert handler.calls == []
    assert _fires(tmp_path) == []


def test_a_lease_lost_before_the_claim_fires_nothing(tmp_path, handler):
    class _LostAtCheck(SingleProcessLease):
        def check(self) -> None:
            raise LeaseLost("successor took over")

    _enrol(tmp_path, at=T0 - timedelta(hours=2))
    report = _scheduler(tmp_path, lease=_LostAtCheck()).tick(T0)
    assert report.lease_held is False
    assert handler.calls == [] and _fires(tmp_path) == []


def test_without_a_registered_handler_nothing_is_claimed(tmp_path):
    unregister_wake_handler(KIND_PROACTIVE)
    _enrol(tmp_path, at=T0 - timedelta(hours=2))
    report = _scheduler(tmp_path).tick(T0)
    assert report.no_handler == 1 and _fires(tmp_path) == []


def test_a_declined_fire_spends_its_window(tmp_path):
    h = _Handler(declined="no_compute")
    register_wake_handler(KIND_PROACTIVE, h, replace=True)
    try:
        key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
        sched = _scheduler(tmp_path)
        assert sched.tick(T0).declined
        assert sched.tick(T0 + timedelta(hours=1)).declined == []
        assert [f["outcome"] for f in _fires(tmp_path)] == ["declined:no_compute"]
        assert sched.store.get(key).last_run_id == ""
        assert sched.tick(T0 + timedelta(hours=4)).declined == [key]
    finally:
        unregister_wake_handler(KIND_PROACTIVE)


def test_a_raising_handler_is_recorded_and_does_not_stop_the_tick(tmp_path):
    h = _Handler(raises=True)
    register_wake_handler(KIND_PROACTIVE, h, replace=True)
    try:
        key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
        sched = _scheduler(tmp_path)
        report = sched.tick(T0)
        assert report.failed and [f["outcome"] for f in _fires(tmp_path)] == [
            "failed:RuntimeError"]
        assert sched.store.get(key).last_run_id == ""
        assert sched.tick(T0 + timedelta(hours=4)).failed == [key]
    finally:
        unregister_wake_handler(KIND_PROACTIVE)


def test_a_trigger_first_served_late_fires_once_not_twice(tmp_path, handler):
    """Enrolled 09:00, first tick 21:00: one fire, with the missed windows
    counted -- not 09:30 now and 17:30 on the next tick (refute round 1)."""
    key = _enrol(tmp_path, at=T0)
    sched = _scheduler(tmp_path)
    evening = T0 + timedelta(hours=12)
    assert sched.tick(evening).fired == [key]
    assert sched.tick(evening + timedelta(minutes=30)).fired == []
    assert TriggerStore(tmp_path).get(key).coalesced_total == 3


def test_a_fire_owed_in_the_evening_waits_for_active_hours(tmp_path, handler):
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    sched = _scheduler(tmp_path)
    assert sched.tick(T0).fired == [key]  # due 08:00, fired 09:00
    sched.tick(T0 + timedelta(hours=3))  # 12:00 fire
    sched.tick(T0 + timedelta(hours=7))  # 16:00 fire
    late = datetime(2026, 10, 5, 23, 0, tzinfo=UTC)  # 20:00 owed, now outside hours
    assert sched.tick(late).fired == []
    morning = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    assert sched.tick(morning).fired == [key]
    assert len(handler.calls) == 4


def test_an_engagement_committed_after_the_decision_voids_the_claim(tmp_path, handler):
    """The owner's message lands between the tick's read and its claim: the
    claim's revision check refuses, and the next tick honours the idle wait."""
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))

    class _EngageAtCheck(SingleProcessLease):
        def check(self) -> None:
            note_owner_engagement(tmp_path, command_center_id=CC, principal_id=OWNER, at=T0)

    report = _scheduler(tmp_path, lease=_EngageAtCheck()).tick(T0)
    assert report.fired == [] and handler.calls == [] and _fires(tmp_path) == []
    sched = _scheduler(tmp_path)
    assert sched.tick(T0 + timedelta(minutes=10)).fired == []
    assert sched.tick(T0 + timedelta(minutes=30)).fired == [key]


def test_each_agent_has_its_own_trigger(tmp_path, handler):
    main = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    other = ensure_proactive_trigger(tmp_path, command_center_id=CC, agent_id="scout",
                                     owner_principal_id=OWNER,
                                     now=T0 - timedelta(hours=2)).trigger_key
    assert main != other
    assert sorted(_scheduler(tmp_path).tick(T0).fired) == sorted([main, other])
    assert {c.agent_id for c in handler.calls} == {"main", "scout"}


def test_the_lease_proof_is_per_acquisition_and_verified():
    from tinyassets.control_plane.lease import current_owner_lease, verify_lease_proof

    lease = current_owner_lease()
    assert verify_lease_proof(lease.generation, lease.proof)
    assert not verify_lease_proof(lease.generation, SingleProcessLease().proof)
    assert not verify_lease_proof(lease.generation + 1, lease.proof)
    assert not verify_lease_proof(lease.generation, "")


# -- owner controls and engagement ------------------------------------------


def test_only_the_owner_counts_as_engagement(tmp_path):
    key = _enrol(tmp_path, at=T0 - timedelta(days=40))
    at = T0
    assert note_owner_engagement(tmp_path, command_center_id=CC, principal_id="user:mallory",
                                 at=at) == 0
    assert TriggerStore(tmp_path).get(key).engaged_at is None
    assert note_owner_engagement(tmp_path, command_center_id=CC, principal_id=OWNER, at=at) == 1
    assert TriggerStore(tmp_path).get(key).engaged_at == at
    # An older stamp never replaces a newer one.
    note_owner_engagement(tmp_path, command_center_id=CC, principal_id=OWNER,
                          at=at - timedelta(days=1))
    assert TriggerStore(tmp_path).get(key).engaged_at == at


def test_engagement_on_a_root_without_triggers_creates_nothing(tmp_path):
    assert note_owner_engagement(tmp_path, command_center_id=CC, principal_id=OWNER) == 0
    assert not (tmp_path / DB_FILENAME).exists()


def test_the_owner_message_event_resets_decay(tmp_path):
    from tinyassets.automation_events import emit_owner_message

    key = _enrol(tmp_path, at=T0 - timedelta(days=40))
    emit_owner_message(tmp_path / CC, principal_id=OWNER)
    assert TriggerStore(tmp_path).get(key).engaged_at is not None


def test_a_trigger_cannot_be_re_owned_and_controls_are_owner_only(tmp_path):
    key = _enrol(tmp_path)
    with pytest.raises(TriggerOwnershipError):
        ensure_proactive_trigger(tmp_path, command_center_id=CC,
                                 owner_principal_id="user:mallory", now=T0)
    store = TriggerStore(tmp_path)
    with pytest.raises(TriggerOwnershipError):
        store.set_enabled(key, principal_id="user:mallory", enabled=False, now=T0)
    with pytest.raises(TriggerOwnershipError):
        store.set_cadence_override(key, principal_id="user:mallory",
                                   overrides={"engaged_period_s": 3600},
                                   base=cadence.DEFAULT_POLICY, now=T0)
    with pytest.raises(ValueError):
        store.set_cadence_override(key, principal_id=OWNER, overrides={"idle_s": "soon"},
                                   base=cadence.DEFAULT_POLICY, now=T0)
    updated = store.set_cadence_override(key, principal_id=OWNER,
                                         overrides={"engaged_period_s": 3600},
                                         base=cadence.DEFAULT_POLICY, now=T0)
    assert updated.policy(cadence.DEFAULT_POLICY).engaged_period_s == 3600


def test_an_owner_override_and_disable_change_firing(tmp_path, handler):
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    store = TriggerStore(tmp_path)
    store.set_cadence_override(key, principal_id=OWNER, overrides={"engaged_period_s": 3600},
                               base=cadence.DEFAULT_POLICY, now=T0)
    sched = _scheduler(tmp_path)
    sched.tick(T0)
    assert sched.tick(T0 + timedelta(hours=1)).fired == [key]
    store.set_enabled(key, principal_id=OWNER, enabled=False, now=T0)
    assert sched.tick(T0 + timedelta(hours=2)).fired == []


def test_account_deletion_takes_the_owners_triggers_and_fires(tmp_path, handler):
    """``.control_plane.db`` is a root store, so deletion's schema-derived rule
    covers it: both tables carry ``owner_principal_id``."""
    from tinyassets.account_deletion import deletion_plan

    _enrol(tmp_path, at=T0 - timedelta(hours=2))
    _scheduler(tmp_path).tick(T0)
    conn = sqlite3.connect(tmp_path / DB_FILENAME)
    try:
        plan = deletion_plan(conn, principal=OWNER, home=CC)
    finally:
        conn.close()
    assert ("owner_principal_id", "principal") in plan["triggers"]
    assert ("owner_principal_id", "principal") in plan["trigger_fires"]


# -- platform state only -----------------------------------------------------


_AUDIT: dict = {"on": False, "paths": []}


def _audit_hook(event: str, args: tuple) -> None:
    if not _AUDIT["on"]:
        return
    if event in {"open", "os.listdir", "os.scandir", "sqlite3.connect"} and args:
        _AUDIT["paths"].append(str(args[0]))


sys.addaudithook(_audit_hook)


def test_the_tick_never_opens_a_command_center_directory(tmp_path, handler):
    """Scheduling reads platform state only (D7/D8a): a fire must not touch the
    command center's own files -- under the sealed box that would wake it.

    A canonical id with its own ``.runs.db``, and a queued run queued on it, so
    the liveness read on the second tick takes the path where ``runs.get_run``
    opens the command center's database (refute round 1, P1)."""
    import sqlite3 as _sqlite3

    from tinyassets.ids import new_universe_id
    from tinyassets.runs import create_run

    cc = new_universe_id()
    cc_dir = tmp_path / cc
    (cc_dir / "notes").mkdir(parents=True)
    (cc_dir / "settings.yaml").write_text("research: every 5 minutes", encoding="utf-8")
    (cc_dir / ".pause").write_text("", encoding="utf-8")
    _sqlite3.connect(cc_dir / ".runs.db").close()
    ensure_proactive_trigger(tmp_path, command_center_id=cc, owner_principal_id=OWNER,
                             now=T0 - timedelta(hours=2))
    run_id = create_run(tmp_path, branch_def_id="b", thread_id="t", inputs={},
                        actor=OWNER, queue_universe_id=cc)
    register_wake_handler(KIND_PROACTIVE, lambda _b, _r: WakeResult(run_id=run_id),
                          replace=True)
    sched = ControlPlaneScheduler(tmp_path, zone_for=_utc)
    _AUDIT["paths"].clear()
    _AUDIT["on"] = True
    try:
        report = sched.tick(T0)
        waiting = sched.tick(T0 + timedelta(hours=5))
        scheduler_metrics(tmp_path, now=T0)
    finally:
        _AUDIT["on"] = False
    assert report.fired
    assert waiting.waiting_on_run  # the second tick really read the live run
    touched = [p for p in _AUDIT["paths"] if cc in p]
    assert touched == []
    assert any(DB_FILENAME in p for p in _AUDIT["paths"])


# -- consumer wiring ---------------------------------------------------------


@pytest.mark.usefixtures("cloud_runtime")
def test_the_consumer_tick_runs_triggers_only_while_holding_the_lease(tmp_path, monkeypatch):
    from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
    from tinyassets.runtime.assigned_queue_consumer import AssignedQueueConsumer
    from tinyassets.storage import db_path
    from tinyassets.storage.request_admissions import migrate_request_admission_schema

    _seed_serving_assignment(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        migrate_request_admission_schema(conn)
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    consumer = AssignedQueueConsumer(tmp_path, max_concurrency=1)
    calls = {"automations": 0, "triggers": 0}

    def _submit(*_a, **_k):
        calls["automations"] += 1
        return 0, set()

    monkeypatch.setattr(consumer, "_submit_due_automations", _submit)
    monkeypatch.setattr(consumer._control_plane, "tick",
                        lambda: calls.__setitem__("triggers", calls["triggers"] + 1))

    class _NotHeld(SingleProcessLease):
        def held(self) -> bool:
            return False

    try:
        consumer.poll_once()
        assert calls == {"automations": 1, "triggers": 1}
        set_owner_lease(_NotHeld())
        consumer.poll_once()
        assert calls == {"automations": 1, "triggers": 1}
    finally:
        set_owner_lease(SingleProcessLease())
        consumer.stop()
    assert isinstance(current_owner_lease(), SingleProcessLease)


@pytest.mark.parametrize("minute", [55, 60, 90])
def test_fall_back_cadence_moves_forward_without_stalling(monkeypatch, minute):
    """Bound the walk so the old fall-back cycle fails instead of hanging pytest."""
    policy = cadence.CadencePolicy(
        active_start="01:30", active_end="02:00", engaged_period_s=300, idle_s=0,
    )
    zone = ZoneInfo("America/New_York")
    created = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    previous = created + timedelta(minutes=20)
    original = cadence._into_active_hours
    calls = 0

    def bounded(moment, policy, zone):
        nonlocal calls
        calls += 1
        assert calls < 100, "cadence walk stalled across the repeated hour"
        return original(moment, policy, zone)

    monkeypatch.setattr(cadence, "_into_active_hours", bounded)
    due, _, _ = cadence.due_instant(
        policy, zone=zone, created_at=created, engaged_at=created,
        last_due_at=previous, now=created.replace(minute=0) + timedelta(minutes=minute),
    )
    assert due >= previous
    moment = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
    assert original(moment, policy, zone) == moment + timedelta(minutes=30)


def test_cadence_walk_stops_if_a_window_does_not_advance(monkeypatch):
    calls = 0

    def stuck(moment, policy, zone):
        nonlocal calls
        calls += 1
        assert calls < 10, "cadence walk must require strict progress"
        return T0

    monkeypatch.setattr(cadence, "_into_active_hours", stuck)
    due, _, collapsed = cadence.due_instant(
        cadence.DEFAULT_POLICY, zone=ZoneInfo("UTC"), created_at=T0,
        engaged_at=None, last_due_at=None, now=T0,
    )
    assert due == T0 and collapsed == 0


@pytest.mark.parametrize("failure", ["locked", "corrupt", "connect"])
def test_unknown_run_liveness_defers_a_second_fire(tmp_path, handler, monkeypatch, caplog,
                                                  failure):
    from tinyassets.control_plane.scheduler import run_is_live
    from tinyassets.runs import runs_db_path

    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    sched = ControlPlaneScheduler(tmp_path, zone_for=_utc)
    assert sched.tick(T0).fired == [key]
    path = runs_db_path(tmp_path)
    path.write_bytes(b"not a database")
    if failure != "corrupt":
        original = sqlite3.connect

        class BrokenConnection:
            def execute(self, *_args):
                raise sqlite3.OperationalError("database is locked")

            def close(self):
                pass

        def connect(database, *args, **kwargs):
            if str(path.as_posix()) in str(database):
                if failure == "connect":
                    raise sqlite3.OperationalError("unable to open database file")
                return BrokenConnection()
            return original(database, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", connect)
    assert run_is_live(tmp_path, "run-1") is True
    assert sched.tick(T0 + timedelta(hours=5)).waiting_on_run == [key]
    assert len(handler.calls) == 1
    assert "liveness" in caplog.text and "WARNING" in caplog.text


def test_absent_runs_store_or_table_has_no_live_run(tmp_path):
    from tinyassets.control_plane.scheduler import run_is_live
    from tinyassets.runs import runs_db_path

    assert run_is_live(tmp_path, "missing") is False
    sqlite3.connect(runs_db_path(tmp_path)).close()
    assert run_is_live(tmp_path, "missing") is False


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("before_reads", [1, 2])
def test_dispatch_rechecks_active_hours_with_a_fresh_clock(tmp_path, handler, explicit,
                                                          before_reads):
    for agent in ("main", "scout"):
        ensure_proactive_trigger(tmp_path, command_center_id=CC, agent_id=agent,
                                 owner_principal_id=OWNER, now=T0)
    before = T0.replace(hour=21, minute=59, second=59)
    after = T0.replace(hour=22, minute=0, second=1)
    reads = []

    def clock():
        reads.append(True)
        return before if len(reads) <= before_reads else after

    sched = ControlPlaneScheduler(tmp_path, zone_for=_utc, clock=clock)
    report = sched.tick(before if explicit else None)
    expected = 2 if explicit else before_reads - 1
    assert len(report.fired) == expected
    assert len(handler.calls) == expected
    assert not explicit or reads == []
    for fire in _fires(tmp_path):
        assert fire["claimed_at"] == "2026-10-05T21:59:59Z"
        request = next(r for r in handler.calls if r.trigger_key == fire["trigger_key"])
        assert request.due_at == fire["due_at"]
        due = datetime.fromisoformat(request.due_at.replace("Z", "+00:00"))
        assert fire["lag_s"] == (before - due).total_seconds()


def test_generation_is_read_after_each_triggers_lease_check(tmp_path, handler, monkeypatch):
    for agent in ("main", "scout"):
        ensure_proactive_trigger(tmp_path, command_center_id=CC, agent_id=agent,
                                 owner_principal_id=OWNER, now=T0 - timedelta(hours=2))

    class ChangingLease(SingleProcessLease):
        generation = 0

        def check(self):
            self.generation += 1

    sched = _scheduler(tmp_path, lease=ChangingLease())
    settled = []
    original = sched.store.settle_lost_claims

    def settle(**kwargs):
        settled.append(kwargs["owner_generation"])
        return original(**kwargs)

    monkeypatch.setattr(sched.store, "settle_lost_claims", settle)
    assert len(sched.tick(T0).fired) == 2
    assert [r.owner_generation for r in handler.calls] == [1, 2]
    assert {f["trigger_key"]: f["owner_generation"] for f in _fires(tmp_path)} == {
        r.trigger_key: r.owner_generation for r in handler.calls
    }
    assert settled == [1, 2]


def test_failed_finish_preserves_single_flight_until_restart(tmp_path, handler, monkeypatch):
    """A started run with an unwritten outcome must block this owner's next wake."""
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    live_reads = []
    sched = _scheduler(tmp_path, live=lambda _b, run: live_reads.append(run) or False)
    finish = sched.store.finish_fire
    attempts = 0

    def fail_first_finish(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("injected finish failure")
        return finish(*args, **kwargs)

    monkeypatch.setattr(sched.store, "finish_fire", fail_first_finish)
    assert sched.tick(T0).failed == [key]
    assert len(handler.calls) == 1
    later = T0 + timedelta(hours=4)
    assert sched.tick(later).waiting_on_run == [key]
    assert len(handler.calls) == 1
    assert live_reads == []
    assert sched.store.get(key).last_run_id == "claim:2026-10-05T08:00:00Z"
    restarted = _scheduler(tmp_path)
    report = restarted.tick(later)
    assert report.lost_settled == 1
    assert report.fired == [key]
    assert len(handler.calls) == 2
    assert [f["outcome"] for f in _fires(tmp_path)] == ["lost_on_restart", "started"]
    assert restarted.store.get(key).last_run_id == "run-2"


@pytest.mark.parametrize("explicit", [False, True])
def test_claim_rechecks_active_hours_after_the_write_lock(tmp_path, handler, monkeypatch,
                                                         explicit):
    """Waiting for SQLite's writer lock can carry an eligible claim past closing."""
    key = _enrol(tmp_path)
    before = T0.replace(hour=21, minute=59, second=59)
    after = T0.replace(hour=22, minute=0, second=1)
    locked = False
    sched = ControlPlaneScheduler(
        tmp_path, zone_for=_utc, clock=lambda: after if locked else before,
    )
    # Isolate the claim transaction from the once-per-generation settlement.
    sched.tick(T0)
    original = sched.store._write
    snapshot = sched.store.get(key)

    @contextmanager
    def write():
        nonlocal locked
        with original() as conn:
            assert conn.in_transaction
            locked = True
            yield conn

    monkeypatch.setattr(sched.store, "_write", write)
    report = sched.tick(before if explicit else None)
    assert locked
    assert report.failed == []
    if explicit:
        assert report.fired == [key]
        assert len(handler.calls) == len(_fires(tmp_path)) == 1
    else:
        assert report.fired == []
        assert handler.calls == [] and _fires(tmp_path) == []
        assert sched.store.get(key) == snapshot


def test_claim_admission_time_is_used_for_lag(tmp_path, handler, monkeypatch):
    """Time spent waiting for the claim lock belongs in the recorded dispatch lag."""
    key = _enrol(tmp_path, at=T0 - timedelta(hours=2))
    moment = T0
    sched = ControlPlaneScheduler(tmp_path, zone_for=_utc, clock=lambda: moment)
    original = sched.store.claim_fire

    def claim(*args, **kwargs):
        nonlocal moment
        moment = T0 + timedelta(minutes=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(sched.store, "claim_fire", claim)
    report = sched.tick()
    assert report.fired == [key]
    assert report.lags_s == [3900.0]
    (fire,) = _fires(tmp_path)
    assert fire["lag_s"] == 3900.0
    assert fire["started_at"] == "2026-10-05T09:05:00Z"
    assert handler.calls[0].due_at == fire["due_at"]


def test_fall_back_cadence_uses_the_second_short_window():
    """Closing the first repeated window must not spend the second one."""
    policy = cadence.CadencePolicy(
        active_start="01:30", active_end="01:45", engaged_period_s=300, idle_s=0,
    )
    zone = ZoneInfo("America/New_York")
    created = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    due, _, collapsed = cadence.due_instant(
        policy, zone=zone, created_at=created, engaged_at=created,
        last_due_at=datetime(2026, 11, 1, 5, 40, tzinfo=UTC),
        now=datetime(2026, 11, 1, 6, 35, tzinfo=UTC),
    )
    assert due == datetime(2026, 11, 1, 6, 35, tzinfo=UTC)
    assert due.astimezone(zone).fold == 1
    assert collapsed == 1


def test_spring_forward_opening_waits_for_the_gap_end():
    """An imaginary opening must neither admit an early wake nor skip the gap end."""
    policy = cadence.CadencePolicy(active_start="02:30", active_end="04:00")
    zone = ZoneInfo("America/New_York")
    moment = datetime(2026, 3, 8, 1, 30, tzinfo=zone)
    assert not cadence.in_active_hours(moment, policy, zone)
    opening = cadence._into_active_hours(moment, policy, zone)
    assert opening == datetime(2026, 3, 8, 7, tzinfo=UTC)
    assert opening.astimezone(zone) == datetime(2026, 3, 8, 3, tzinfo=zone)
