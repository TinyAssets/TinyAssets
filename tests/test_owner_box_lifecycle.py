"""Persistent lifecycle decisions with a runtime double; real process proof below."""
from __future__ import annotations

import asyncio
import os
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
from tinyassets.broker.lifecycle_config import THIN_BYTES
from tinyassets.owner_activity import Activity


def _other_turn(activity, errors):
    """Probe a turn that does not already hold this admission.

    Nested ``enter`` on the admitted turn is that turn finishing. The next
    turn is a different task, which an owner server may schedule on the same
    OS thread.
    """
    try:
        with activity.enter():
            errors.append("admitted")
    except RuntimeError as exc:
        errors.append(exc)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    class Runtime:
        box_id = staticmethod(GvisorRuntime.box_id)

        def __init__(self):
            self.live = set()
            self.measured = {}
            self.starts = []
            self.stops = []

        def start(self, uid, gid, *, rewind: bool = False, **kwargs):
            if rewind:
                kwargs["rewind"] = True
            self.starts.append((uid, kwargs))
            self.live.add(uid)

        def alive(self, uid):
            return uid in self.live

        def memory(self, uid):
            return self.measured.get(uid)  # Unmeasured: the broker charges thin.

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
    monkeypatch.setattr("tinyassets.broker.volume_lifecycle.WAKE_LEASE", 1)
    # These tests cover publication itself; deferral has its own tests.
    monkeypatch.setattr("tinyassets.broker.owner_volumes.SNAPSHOT_QUIET", 0)
    routing.config.memory_budget = 2 * THIN_BYTES
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
        refused = []
        thread = threading.Thread(target=_other_turn, args=(activity, refused))
        thread.start()
        thread.join()
        assert isinstance(refused[0], RuntimeError)
        assert "draining" in str(refused[0])
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
    drive(fleet.routing.sleep("a"))
    fleet.tick()
    snapshot = fleet.admission.volume("a")
    request = {"lifecycle": "restore", "uid": uid,
               "snapshot_id": snapshot["snapshot_id"], "sha256": snapshot["snapshot_sha256"]}
    service = HostService(fleet.admission, fleet.manager.volumes, None, fleet.manager)
    for peer in (0, 1001):
        assert "error" in service.lifecycle(request, peer)
    assert "error" in service.lifecycle({**request, "owner": "b"}, 1002)
    assert service.lifecycle(request, 1002) == {"restored": True}


@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("recover_by", ["wake", "tick"])
def test_failed_drain_retains_intent_until_stop_and_recovers(fleet, recover_by):
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    with activity.enter():
        with pytest.raises(BoxError, match="must drain"):
            drive(fleet.routing.sleep("a"))
        pending = fleet.admission.volume("a")["work"]
        assert pending["kind"] == "snapshot" and activity.draining
        with pytest.raises(BoxError, match="must drain"):
            fleet.wake("a", scheduled=True)
        assert fleet.admission.volume("a")["work"] == pending
    if recover_by == "tick":
        fleet.tick()
        assert fleet.admission.volume("a")["work"] is None
        assert fleet.admission.volume("a")["snapshot_id"] == pending["snapshot_id"]
    assert fleet.wake("a", scheduled=True) == uid
    assert not fleet.states[uid].draining
    assert fleet.admission.volume("a")["work"] == {"kind": "running"}
    assert fleet.runtime.stops == [uid]


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_status_does_not_queue_behind_real_volume_lock(fleet, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets.boxes.volumes import OwnerVolumes

    # Exercise the production flock, without requiring an XFS image for a
    # stopped box. A held copy lock must not consume a status socket deadline.
    volumes = OwnerVolumes.__new__(OwnerVolumes)
    volumes.store = tmp_path
    volumes.locks = tmp_path / "locks"
    volumes.locks.mkdir()
    volumes._held = threading.local()
    fleet.manager.volumes = volumes
    uid = fleet.identities.owner_machine("a")
    with ThreadPoolExecutor(1) as pool:
        with volumes.operation(uid):
            result = pool.submit(fleet.manager.status, uid)
            with pytest.raises(BlockingIOError):
                result.result(timeout=2)
        assert pool.submit(fleet.manager.status, uid).result(timeout=2)["awake"] is False


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_shared_volume_lock_lets_status_maintain_and_awake_wake_proceed(fleet, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets.boxes.volumes import OwnerVolumes

    uid = fleet.wake("a")
    record = fleet.manager.boxes[uid].launch
    volumes = OwnerVolumes.__new__(OwnerVolumes)
    volumes.store = tmp_path / "store"
    volumes.store.mkdir()
    (volumes.store / f"{uid}.img").write_bytes(b"img")
    volumes.locks = tmp_path / "locks"
    volumes.locks.mkdir()
    volumes._held = threading.local()
    fleet.manager.volumes = volumes
    with volumes.operation(uid, shared=True):
        with ThreadPoolExecutor(3) as pool:
            status = pool.submit(fleet.manager.status, uid)
            maintained = pool.submit(fleet.manager.maintain)
            woken = pool.submit(
                fleet.manager.ensure_awake, "a", scheduled=True, launch=record,
            )
            assert status.result(timeout=2)["awake"] is True
            assert maintained.result(timeout=2) is None
            assert woken.result(timeout=2) == uid
    with ThreadPoolExecutor(1) as pool:
        with volumes.operation(uid):
            with pytest.raises(BlockingIOError):
                pool.submit(fleet.manager.status, uid).result(timeout=2)


@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("recover_by", ["wake", "tick"])
def test_preexisting_running_record_with_draining_box_recovers(fleet, recover_by):
    uid = fleet.wake("a")
    with fleet.states[uid].enter():
        with pytest.raises(BoxError, match="must drain"):
            drive(fleet.routing.sleep("a"))
        # Reproduce the old wake path losing the pending stop before launch
        # refused a still-draining box. Host facts must repair that old state.
        fleet.routing.volumes.volume("a", "wake")
    assert fleet.manager.status(uid)["draining"]
    assert fleet.admission.volume("a")["work"] == {"kind": "running"}
    if recover_by == "tick":
        fleet.tick()
        assert fleet.admission.volume("a")["work"] is None
    fleet.wake("a", scheduled=True)
    assert not fleet.states[uid].draining
    assert fleet.runtime.stops == [uid]


@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("failure", ["suspended", "auth", "refused", "transport"])
def test_broker_admission_failure_refuses_wake_without_host_callback(fleet, monkeypatch, failure):
    from tinyassets.broker import box_admission
    manager = fleet.manager
    fleet.routing.config.memory_budget = 3 * THIN_BYTES
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
    fleet.routing.config.memory_budget = THIN_BYTES
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

@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("slow", ["launch", "readiness", "probe", "drain"])
def test_slow_owner_execution_does_not_hold_fleet_registry(fleet, monkeypatch, slow):
    from concurrent.futures import ThreadPoolExecutor

    b = fleet.wake("b")
    a = fleet.admission.owner("a")["uid"]
    if slow in {"probe", "drain"}:
        fleet.wake("a")
    entered, release = threading.Event(), threading.Event()
    start, rpc = fleet.runtime.start, fleet.manager._rpc

    def slow_start(uid, *args, **kwargs):
        if uid == a and slow == "launch":
            entered.set()
            assert release.wait(10)
        return start(uid, *args, **kwargs)

    def slow_rpc(box, operation="status", **body):
        if box.uid == a and ((slow in {"readiness", "probe"} and operation == "status")
                              or (slow == "drain" and operation == "delete-admission")):
            entered.set()
            assert release.wait(10)
        return rpc(box, operation, **body)

    monkeypatch.setattr(fleet.runtime, "start", slow_start)
    monkeypatch.setattr(fleet.manager, "_rpc", slow_rpc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(fleet.manager.withdraw, a) if slow == "drain" else (
            pool.submit(fleet.manager.status, a) if slow == "probe" else
            pool.submit(fleet.wake, "a"))
        try:
            assert entered.wait(2)
            assert pool.submit(fleet.control, "sleep", uid=b).result(timeout=2) == {"stopped": True}
            assert pool.submit(fleet.wake, "b").result(timeout=2) == b
            assert not first.done()
        finally:
            release.set()
            first.result(timeout=3)


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_fifth_broker_connection_gets_explicit_refusal(tmp_path, monkeypatch):
    """A fifth broker connection is admitted. Lifecycle work is queued with a
    deadline, so a held registration cannot exhaust the transport pool. The
    shared pool for every other peer still refuses the next connection."""
    import queue
    import socket

    from tinyassets.boxes.control_channel import receive

    peers = {"uid": 1002}
    monkeypatch.setattr("tinyassets.boxes.host_service.peer",
                        lambda sock: (os.getpid(), peers["uid"], peers["uid"]))
    monkeypatch.setattr("tinyassets.boxes.host_service.os.chown", lambda *_: None)
    state = {"listening": threading.Event()}
    listen = socket.socket.listen

    def ready(sock, backlog):
        listen(sock, backlog)
        state["listening"].set()
    monkeypatch.setattr(socket.socket, "listen", ready)

    def open_clients(service, path, count):
        stop, release = threading.Event(), threading.Event()
        entered = queue.Queue()

        def handler(sock):
            entered.put(True)
            try:
                assert release.wait(10)
            finally:
                sock.close()

        thread = threading.Thread(target=service.serve, args=(path, 1002, handler, stop))
        thread.start()
        clients = []
        try:
            assert state["listening"].wait(2)
            for _ in range(count):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                clients.append(client)
                client.connect(str(path))
                assert entered.get(timeout=2) is True
            return stop, release, entered, thread, clients
        except Exception:
            release.set()
            stop.set()
            for client in clients:
                client.close()
            thread.join(3)
            raise

    def finish(stop, release, thread, clients):
        release.set()
        stop.set()
        for client in clients:
            client.close()
        thread.join(3)
        assert not thread.is_alive()

    service = HostService(None, None, None, None)
    broker = open_clients(service, tmp_path / "broker.sock", 5)
    try:
        assert broker[2].empty()
    finally:
        finish(*broker[:2], *broker[3:])
    peers["uid"] = 1001
    state["listening"] = threading.Event()
    limited = HostService(None, None, None, None)
    limited._slots = threading.BoundedSemaphore(4)
    stop, release, entered, thread, clients = open_clients(
        limited, tmp_path / "root.sock", 4)
    try:
        fifth = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        clients.append(fifth)
        fifth.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        fifth.settimeout(2)
        fifth.connect(str(tmp_path / "root.sock"))
        assert receive(fifth, (os.getpid(), os.geteuid(), os.getegid())) == {
            "error": "boxhostd connection limit exceeded"}
        assert entered.empty()
    finally:
        finish(stop, release, thread, clients)



@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_a_running_box_that_stops_answering_is_replaced_on_the_next_wake(fleet, monkeypatch):
    manager = fleet.manager
    uid = fleet.wake("a")
    answering = manager._rpc
    silent = {uid}

    def rpc(box, operation="status", **body):
        if box.uid in silent:
            raise OSError("owner app does not answer")
        return answering(box, operation, **body)

    monkeypatch.setattr(manager, "_rpc", rpc)
    manager.answer_probe = 0.2
    silent.add(uid)
    assert fleet.runtime.alive(uid)
    # Silent, but it answered moments ago: a stalled turn is never killed for it.
    with pytest.raises(BoxError, match="not answering"):
        manager.ensure_awake("a", launch=manager.boxes[uid].launch)
    assert fleet.runtime.stops == [] and manager.boxes[uid].awake
    # Nothing has answered for the whole silence: replaced rather than failing forever.
    manager.boxes[uid].answered -= manager.answer_silence

    def restart(*args, **kwargs):
        silent.discard(uid)  # The fresh box answers.
        return started(*args, **kwargs)

    started = fleet.runtime.start
    monkeypatch.setattr(fleet.runtime, "start", restart)
    assert fleet.wake("a") == uid
    assert fleet.runtime.stops == [uid] and len(fleet.runtime.starts) == 2
    assert manager.boxes[uid].error == "" and manager.boxes[uid].awake


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_one_slow_answer_never_replaces_a_running_box(fleet, monkeypatch):
    manager = fleet.manager
    uid = fleet.wake("a")
    answering = manager._rpc
    misses = [1, 1]

    def rpc(box, operation="status", **body):
        if misses:
            misses.pop()
            raise OSError("slow reply")
        return answering(box, operation, **body)

    monkeypatch.setattr(manager, "_rpc", rpc)
    manager.answer_probe = 5
    manager.boxes[uid].answered -= manager.answer_silence  # Even long after an answer.
    assert fleet.wake("a") == uid
    assert fleet.runtime.stops == [] and len(fleet.runtime.starts) == 1


@pytest.mark.verifies("owner-daemon-in-box:Boxes sleep and wake without a person")
def test_the_default_budget_admits_far_more_thin_boxes_than_the_old_cap(fleet, tmp_path):
    from tinyassets.broker import box_admission
    from tinyassets.broker.lifecycle_config import LifecycleConfig

    assert LifecycleConfig().memory_budget // THIN_BYTES >= 4 * 6
    identities = fleet.identities
    for owner in "defgh":
        identities.resolve(owner, allocate=True)
        drive(identities.admit(owner, routing=projection(identities.path)))
        box_admission.provision(identities, tmp_path, owner)
        fleet.admission.secrets[owner] = identities.box_route(owner)[1]
    fleet.routing.config.memory_budget = LifecycleConfig().memory_budget
    fleet.routing.config.wake_timeout = .5  # Over budget, a wake would fail here.
    uids = [fleet.wake(owner) for owner in "abcdefgh"]  # Eight, past the old six.
    assert set(uids) <= fleet.runtime.live and len(set(uids)) == 8


@pytest.mark.verifies("owner-daemon-in-box:Boxes sleep and wake without a person")
def test_a_box_is_charged_its_measured_memory_once_a_pass_reports_it(fleet):
    from tinyassets.boxes.provider import BoxBusy

    fleet.routing.config.memory_budget = 3 * THIN_BYTES
    fleet.routing.config.wake_timeout = .5
    a = fleet.wake("a")
    fleet.runtime.measured[a] = 2 * THIN_BYTES  # A CLI started inside the box.
    assert fleet.routing._charged() == THIN_BYTES  # Not yet reported.
    with fleet.states[a].enter():
        fleet.tick()  # The pass (and its owner task) records the box's memory.
        assert fleet.routing._charged() == 2 * THIN_BYTES
        b = fleet.wake("b")  # Exactly fills the budget.
        with fleet.states[b].enter(), pytest.raises(BoxBusy, match="capacity"):
            fleet.wake("c", scheduled=True)
        assert fleet.runtime.live == {a, b}


@pytest.mark.verifies("owner-daemon-in-box:Boxes sleep and wake without a person")
def test_a_request_after_the_idle_read_keeps_the_box_serving(fleet):
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    activity.last -= 10
    fleet.manager.boxes[uid].last -= 10
    host = fleet.routing.volumes.host

    async def request_arrives_first(owner, operation, **arguments):
        if operation == "sleep":
            with activity.lock:  # The next message lands after the status read.
                activity.active += 1
                activity.last = time.monotonic()
        return await host(owner, operation, **arguments)

    fleet.routing.volumes.host = request_arrives_first
    try:
        fleet.tick()
        assert uid in fleet.runtime.live and not activity.draining
        assert fleet.admission.volume("a")["work"] == {"kind": "running"}
    finally:
        activity.active -= 1
        fleet.routing.volumes.host = host


@pytest.mark.verifies(
    "owner-daemon-in-box:Owner durability is off-host snapshots referenced in the map")
def test_a_restarted_broker_counts_no_deferred_stop_as_awake(fleet, monkeypatch):
    from tinyassets.broker import owner_volumes
    from tinyassets.broker.routing import BrokerLifecycle

    monkeypatch.setattr(owner_volumes, "SNAPSHOT_QUIET", 900)
    for _ in range(2):  # The first stop is copied at once; the second waits.
        uid = fleet.wake("a")
        fleet.states[uid].last -= 10
        fleet.manager.boxes[uid].last -= 10
        fleet.tick()
    assert "stopped_at" in fleet.admission.volume("a")["work"]
    assert uid not in fleet.runtime.live and "a" not in fleet.routing.awake
    restarted = BrokerLifecycle(fleet.routing.server)
    assert "a" not in restarted.awake


@pytest.mark.verifies("owner-daemon-in-box:Boxes sleep and wake without a person")
def test_an_idle_stop_past_its_shutdown_deadline_ends_the_box_and_the_next_wake_serves(
        fleet, monkeypatch):
    # SIGTERM closed the drained box's listener, but its process never exits
    # (lifespan shutdown waits on an uncancelled tool thread). Left alive, every
    # later wake was refused and the router answered 503 until boxhostd restarted.
    uid = fleet.wake("a")
    fleet.states[uid].last -= 10
    fleet.manager.boxes[uid].last -= 10
    rpc, start, closed = fleet.manager._rpc, fleet.runtime.start, set()

    def hung(uid):
        closed.add(uid)
        raise BoxError("owner daemon did not flush before shutdown deadline")

    def answer(box, operation="status", **body):
        if box.uid in closed:
            raise OSError("connection refused")
        return rpc(box, operation, **body)

    def started(uid, gid, **kwargs):
        closed.discard(uid)
        start(uid, gid, **kwargs)

    monkeypatch.setattr(fleet.runtime, "shutdown", hung)
    monkeypatch.setattr(fleet.runtime, "start", started)
    monkeypatch.setattr(fleet.manager, "_rpc", answer)
    fleet.tick()
    assert fleet.runtime.stops == [uid] and uid not in fleet.runtime.live
    box = fleet.manager.boxes[uid]
    assert not box.awake and box.failures == 0 and not box.error
    assert "a" not in fleet.routing.awake
    assert fleet.wake("a") == uid
    assert len(fleet.runtime.starts) == 2 and not fleet.states[uid].draining
    assert fleet.admission.volume("a")["work"] == {"kind": "running"}


@pytest.mark.verifies("owner-daemon-in-box:Boxes sleep and wake without a person")
def test_an_idle_box_that_drains_but_does_not_stop_is_not_read_as_busy(fleet, monkeypatch):
    uid = fleet.wake("a")
    fleet.states[uid].last -= 10
    fleet.manager.boxes[uid].last -= 10

    def stuck(uid):
        raise OSError("cannot prove the owner sandbox stopped")

    monkeypatch.setattr(fleet.runtime, "shutdown", stuck)
    monkeypatch.setattr(fleet.runtime, "stop", stuck)
    fleet.tick()
    assert fleet.manager.boxes[uid].failures == 1
    work = fleet.admission.volume("a")["work"]
    assert work["kind"] == "snapshot" and "stopped_at" not in work  # Owed, not withdrawn.


@pytest.mark.verifies("owner-daemon-in-box:Boxes sleep and wake without a person")
def test_a_pass_that_frees_memory_admits_a_waiting_wake(fleet, monkeypatch):
    fleet.routing.config.memory_budget = 2 * THIN_BYTES
    fleet.routing.config.wake_timeout = 5
    a = fleet.wake("a")
    fleet.runtime.measured[a] = 2 * THIN_BYTES  # A CLI holds the whole budget.
    waited, wait = threading.Event(), fleet.routing._capacity.wait

    async def signalled():
        waited.set()
        return await wait()

    monkeypatch.setattr(fleet.routing._capacity, "wait", signalled)
    result = []
    with fleet.states[a].enter():
        fleet.tick()
        waiting = threading.Thread(target=lambda: result.append(fleet.wake("b", scheduled=True)))
        waiting.start()
        assert waited.wait(2) and not result
        fleet.runtime.measured[a] = THIN_BYTES  # The CLI exits; the turn goes on.
        drive(fleet.routing.tick())  # The pass's facts wake the waiting wake.
        waiting.join(4)
    assert result and not waiting.is_alive()
