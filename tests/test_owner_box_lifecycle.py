"""Persistent lifecycle decisions with a runtime double; real process proof below."""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from tests.support.admission import drive, projection
from tests.test_owner_volume_lifecycle import Images as Volumes
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.host_service import HostService
from tinyassets.boxes.owner_daemons import OwnerDaemons
from tinyassets.boxes.provider import BoxError
from tinyassets.owner_activity import Activity


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    class Runtime:
        box_id = staticmethod(GvisorRuntime.box_id)

        def __init__(self):
            self.live = set()
            self.starts = []
            self.stops = []

        def start(self, uid, gid, **kwargs):
            self.starts.append((uid, kwargs))
            self.live.add(uid)

        def alive(self, uid):
            return uid in self.live

        def stop(self, uid):
            self.stops.append(uid)
            self.live.discard(uid)

        shutdown = stop

    states = {}
    import sqlite3

    from tests.test_owner_volume_lifecycle import Admission
    from tinyassets.broker import box_admission
    from tinyassets.broker.owner_identities import OwnerIdentities

    state = tmp_path / ".broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "identities.db", initialize=True)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    for owner in "abc":
        identities.resolve(owner, allocate=True)
        drive(identities.admit(owner, routing=projection(identities.path)))
        box_admission.provision(identities, tmp_path, owner)
    admission, runtime = Admission(identities), Runtime()
    admission.secrets = {owner: identities.box_route(owner)[1] for owner in "abc"}
    volumes = Volumes()
    relays = SimpleNamespace(bind=lambda owner, uid: tmp_path / f"{uid}.sock",
                             retire=lambda uid: None)
    manager = OwnerDaemons(runtime, admission, volumes, relays, tmp_path,
                           startup_timeout=2)

    def paths(uid, secret):
        states[uid] = Activity()
        directory = tmp_path / str(uid)
        directory.mkdir(exist_ok=True)
        path = directory / "secret"
        path.write_text(secret)
        return directory, path

    def rpc(box, operation="status", **body):
        if operation == "delete-admission":
            states[box.uid].admit_deletion_only()
        return states[box.uid].status(drain=operation == "drain",
                                     snapshot=operation == "snapshot-stop",
                                     revoke=operation == "revoke",
                                     idle=body.get("idle_seconds", 0))

    monkeypatch.setattr(manager, "_paths", paths)
    monkeypatch.setattr(manager, "_rpc", rpc)
    from tinyassets.boxes import daemon_control
    from tinyassets.broker.routing import BrokerLifecycle

    host = HostService(admission, volumes, relays, manager)
    def control(operation, owner=None, **fields):
        result = host.lifecycle({"lifecycle": operation, **fields,
                                 **({"owner": owner} if owner is not None else {})}, 1002)
        if set(result) == {"error"}:
            raise BoxError(result["error"])
        return result
    monkeypatch.setattr(daemon_control, "request", control)
    server = SimpleNamespace(_owner_identities=identities, _data_root=tmp_path)
    routing = BrokerLifecycle(server)
    routing.config.idle_limit = 1
    routing.config.awake_cap = 2
    routing.config.wake_timeout = 2
    routing.publish = lambda **kwargs: None
    server.routing = routing
    server.deletion_jobs = SimpleNamespace(tick=lambda: None)
    loop = asyncio.new_event_loop()
    server._loop = loop
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    def tick():
        drive(routing.tick())
        drive(routing.close())
    try:
        yield SimpleNamespace(manager=manager, runtime=runtime, admission=admission, states=states,
                              identities=identities, routing=routing,
                              wake=lambda *a, **kw: drive(routing.wake(*a, **kw)),
                              tick=tick, control=control)
    finally:
        drive(routing.close())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()




@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_snapshot_deadline_drains_current_turn_then_sleeps_until_a_new_wake(fleet):
    manager = fleet.manager
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    manager.boxes[uid].started -= 86401
    with activity.enter():
        manager.maintain()
        assert not activity.draining  # The executor cannot select sleep.
        fleet.tick()
        assert activity.draining and activity.active == 1
        assert not fleet.runtime.stops
        with pytest.raises(RuntimeError, match="draining"):
            with activity.enter():
                pytest.fail("new turn admitted after snapshot deadline")
    fleet.tick()
    assert fleet.runtime.stops == [uid]
    manager.maintain()
    assert len(fleet.runtime.starts) == 1
    assert fleet.admission.volume("a")["snapshot_id"]
    fleet.wake("a")
    assert len(fleet.runtime.starts) == 2




@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_restore_executor_accepts_only_broker_bound_uid(fleet):
    uid = fleet.wake("a")
    snapshot = drive(fleet.routing.volumes.sleep("a"))
    request = {"lifecycle": "restore", "uid": uid,
               "snapshot_id": snapshot["snapshot_id"], "sha256": snapshot["snapshot_sha256"]}
    service = HostService(fleet.admission, fleet.manager.volumes, None, fleet.manager)
    for peer in (0, 1001):
        assert "error" in service.lifecycle(request, peer)
    assert "error" in service.lifecycle({**request, "owner": "b"}, 1002)
    assert service.lifecycle(request, 1002) == {"restored": True}


@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("failure", ["suspended", "auth", "refused", "transport"])
def test_broker_admission_failure_refuses_wake_without_host_callback(fleet, monkeypatch, failure):
    from tinyassets.broker import box_admission
    manager = fleet.manager
    fleet.routing.config.awake_cap = 3
    a, b, c = [fleet.wake(owner) for owner in "abc"]
    original = box_admission.owner_record
    def admission(identities, owner, **kwargs):
        if owner == "a":
            raise {"suspended": PermissionError, "auth": PermissionError,
                   "refused": PermissionError, "transport": OSError}[failure](failure)
        return original(identities, owner, **kwargs)
    monkeypatch.setattr(box_admission, "owner_record", admission)
    with fleet.states[b].enter(), fleet.states[c].enter():
        with pytest.raises((PermissionError, OSError), match=failure):
            fleet.wake("a")
        fleet.control("stop", uid=a)
        manager.maintain()
        assert fleet.runtime.live == {b, c}
        assert fleet.states[b].active == fleet.states[c].active == 1
        assert not fleet.states[b].draining and not fleet.states[c].draining
    assert "wanted" not in vars(manager.boxes[a])


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("awake", [False, True])
def test_suspended_owner_cannot_wake_but_broker_can_stop_bound_box(fleet, awake):
    from tinyassets.broker.admission import local_mutate
    uid = fleet.identities.owner_machine("a")
    if awake:
        fleet.wake("a")
    drive(local_mutate(fleet.identities.path, "a", "suspend", reason="abuse", suspended=True,
        routing=projection(fleet.identities.path)))
    for scheduled in (False, True):
        with pytest.raises(PermissionError):
            fleet.wake("a", scheduled=scheduled)
    assert fleet.control("stop", uid=uid) == {"stopped": True}
    assert not fleet.runtime.alive(uid)


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_failed_launch_cleanup_keeps_capacity_and_never_advertises_readiness(fleet, monkeypatch):
    original = fleet.runtime.start

    def launch(*args, **kwargs):
        original(*args, **kwargs)
        raise BoxError("resource verification failed")

    def failed_stop(uid):
        raise BoxError("cannot prove sandbox stopped")

    monkeypatch.setattr(fleet.runtime, "start", launch)
    monkeypatch.setattr(fleet.runtime, "stop", failed_stop)
    with pytest.raises(BoxError, match="startup failed"):
        fleet.wake("a")
    uid = fleet.admission.owner("a")["uid"]
    assert fleet.manager.boxes[uid].awake  # uncertain slot remains reserved
    with pytest.raises(BoxError, match="unverified"):
        fleet.wake("a")
    assert len(fleet.runtime.starts) == 1


@pytest.mark.verifies('owner-daemon-in-box:C12')
def test_idle_never_stops_a_running_turn_and_drain_refuses_new_turns(fleet):
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    with activity.enter():
        fleet.manager.boxes[uid].last -= 10
        activity.last -= 10
        fleet.manager.maintain()
        assert uid in fleet.runtime.live and not activity.draining
        fleet.tick()
        assert uid in fleet.runtime.live
    activity.last -= 10
    fleet.manager.boxes[uid].last -= 10
    fleet.tick()
    assert uid not in fleet.runtime.live
    with pytest.raises(RuntimeError, match="draining"):
        with activity.enter():
            pytest.fail("drained box admitted a turn")


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_crashed_process_never_restarts_without_a_broker_wake(fleet):
    uid = fleet.wake("a")
    for _ in range(6):
        starts = len(fleet.runtime.starts)
        fleet.runtime.live.clear()
        fleet.manager.maintain()
        assert "crashed" in fleet.manager.status(uid)["error"]
        fleet.manager.maintain()
        fleet.tick()
        assert not fleet.runtime.live
        assert len(fleet.runtime.starts) == starts
        assert fleet.wake("a") == uid
        assert fleet.runtime.live == {uid}
    assert fleet.manager.status(uid)["error"] == ""


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_interactive_evicts_lru_idle_and_scheduled_waits_behind_it(fleet):
    a = fleet.wake("a")
    b = fleet.wake("b")
    fleet.wake("a")  # b is now least recently used.
    fleet.tick()  # recover supplies victim facts; wake makes no candidate RPCs.
    fleet.wake("c")
    assert b not in fleet.runtime.live and a in fleet.runtime.live
    c = fleet.admission.owner("c")["uid"]
    drive(fleet.routing.sleep("c"))
    fleet.routing.config.awake_cap = 1
    fleet.routing.config.wake_timeout = 5
    result = []

    def scheduled():
        result.append(fleet.wake("b", scheduled=True))

    thread = threading.Thread(target=scheduled)
    thread.start()
    deadline = time.monotonic() + 2
    while not fleet.routing._capacity._waiters and time.monotonic() < deadline:
        time.sleep(.01)
    assert fleet.routing._capacity._waiters and not result
    # Interactive callers bypass the older scheduled ticket and can evict.
    fleet.wake("c")
    assert fleet.runtime.live == {c} and not result
    drive(fleet.routing.sleep("c"))
    thread.join(5)
    assert result == [b] and not thread.is_alive()


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_busy_boxes_are_never_evicted_and_wake_is_bounded(fleet):
    a, b = fleet.wake("a"), fleet.wake("b")
    fleet.routing.config.wake_timeout = .05
    with fleet.states[a].enter(), fleet.states[b].enter():
        with pytest.raises(BoxError, match="deadline"):
            fleet.wake("c")
    assert fleet.runtime.live == {a, b}


@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("operation", ["snapshot", "restore"])
def test_copy_operations_refuse_a_running_box_without_stopping_it(fleet, operation):
    uid = fleet.wake("a")
    before = list(fleet.runtime.stops)
    arguments = {"snapshot_id": "a" * 32}
    if operation == "restore":
        arguments["sha256"] = "b" * 64
    with pytest.raises(BoxError, match="stopped box"):
        fleet.control(operation, uid=uid, **arguments)
    assert fleet.runtime.alive(uid)
    assert fleet.runtime.stops == before


@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_stop_executes_only_broker_bound_uid_and_retires_old_ingress(fleet):
    uid = fleet.wake("a")
    service = HostService(fleet.admission, fleet.manager.volumes, None, fleet.manager)
    request = {"lifecycle": "stop", "uid": uid}
    for peer in (0, 1001):
        assert "error" in service.lifecycle(request, peer)
    assert "error" in service.lifecycle({**request, "owner": "b"}, 1002)
    assert service.lifecycle(request, 1002) == {"stopped": True}
    assert not fleet.runtime.live and fleet.states[uid].revoked








@pytest.mark.verifies('owner-daemon-in-box:C12')
def test_quiesce_refuses_background_submission_and_maintenance(tmp_path, monkeypatch):
    from tinyassets import owner_activity, owner_runtime, run_input_runtime
    from tinyassets.api import runs
    from tinyassets.control_plane import activities, tick

    gate = Activity()
    assert gate.quiesce(0)
    monkeypatch.setattr(owner_activity, "activity", gate)
    for dispatch in (
        lambda: owner_runtime._start_maintenance(None),
        lambda: tick.AssignedQueueConsumer.poll_once(None),
        lambda: activities.dispatch_all(tmp_path),
        lambda: activities.tick_in_background(tmp_path),
        lambda: run_input_runtime.dispatch_admitted_run(tmp_path, run_id="pending", prepare=None),
    ):
        with pytest.raises(RuntimeError, match="draining"):
            dispatch()
    monkeypatch.setattr(runs, "_RUNS_RECOVERY_LOCK", object())
    monkeypatch.setattr("tinyassets.runs.recover_in_flight_runs",
                        lambda *a, **kw: pytest.fail("drained recovery executed"))
    assert runs.recover_dead_owner_runs_now() == 0
    assert gate.status()["active"] == 0


@pytest.mark.verifies('owner-daemon-in-box:C12')
def test_background_work_blocks_drain_without_renewing_user_idle(monkeypatch):
    from tinyassets import owner_activity

    gate = Activity()
    gate.last = 0
    monkeypatch.setattr(owner_activity, "activity", gate)
    monkeypatch.setattr(owner_activity, "time", SimpleNamespace(monotonic=lambda: 1000))

    @owner_activity.owner_work
    def background():
        status = gate.status(drain=True, idle=900)
        assert status["active"] == 1 and not status["draining"]

    background()
    assert gate.status()["idle_seconds"] == 1000
    assert gate.status(drain=True, idle=900)["draining"]




@pytest.mark.verifies('owner-daemon-in-box:T5')
def test_erasure_refuses_broker_restart_and_host_has_no_restart_intent(fleet):
    from tinyassets.broker.admission import local_mutate
    uid = fleet.wake("a")
    drive(local_mutate(fleet.identities.path, "a", "erase",
      routing=projection(fleet.identities.path)))
    fleet.control("stop", uid=uid)
    for scheduled in (False, True):
        with pytest.raises(PermissionError):
            fleet.wake("a", scheduled=scheduled)
    fleet.manager.maintain()
    fleet.tick()
    assert not fleet.runtime.live
    assert len(fleet.runtime.starts) == 1
