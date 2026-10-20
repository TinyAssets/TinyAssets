"""Persistent lifecycle decisions with a runtime double; real process proof below."""
from __future__ import annotations

import asyncio
import os
import secrets
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

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
        identities.admission("admit", owner, owner)
        box_admission.provision(identities, tmp_path, owner, owner)
    admission, runtime = Admission(identities), Runtime()
    admission.secrets = {owner: identities.box_route(owner)[1] for owner in "abc"}
    volumes = Volumes()
    relays = SimpleNamespace(bind=lambda owner, uid: tmp_path / f"{uid}.sock")
    manager = OwnerDaemons(runtime, admission, volumes, relays, tmp_path,
                           idle_seconds=1, awake_cap=2, wake_timeout=2)

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
    from tinyassets.broker.routing import RoutingAuthority

    host = HostService(admission, volumes, relays, manager)
    def control(operation, owner=None, **fields):
        result = host.lifecycle({"lifecycle": operation, **fields,
                                 **({"owner": owner} if owner is not None else {})}, 1002)
        if set(result) == {"error"}:
            raise BoxError(result["error"])
        return result
    monkeypatch.setattr(daemon_control, "request", control)
    server = SimpleNamespace(_owner_identities=identities, _data_root=tmp_path)
    routing = RoutingAuthority(server)
    routing.publish = lambda **kwargs: None
    return SimpleNamespace(manager=manager, runtime=runtime, admission=admission, states=states,
                           identities=identities, routing=routing, wake=routing.wake,
                           control=control)


def test_wake_reuses_one_box_and_secret_across_restart(fleet):
    manager = fleet.manager
    uid = fleet.wake("a")
    first = fleet.admission.secrets["a"]
    assert fleet.wake("a") == uid
    assert len(fleet.runtime.starts) == 1
    assert fleet.runtime.starts[0][1]["memory_bytes"] == 1024**3
    with manager.condition:
        assert manager._sleep(manager.boxes[uid], idle=0)
    fleet.wake("a")
    assert fleet.admission.secrets["a"] == first
    assert len(fleet.runtime.starts) == 2


def test_snapshot_deadline_drains_current_turn_before_sleep_and_restart(fleet):
    manager = fleet.manager
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    manager.boxes[uid].started -= 86401
    with activity.enter():
        manager.maintain()
        assert activity.draining and activity.active == 1
        assert not fleet.runtime.stops
        with pytest.raises(RuntimeError, match="draining"):
            with activity.enter():
                pytest.fail("new turn admitted after snapshot deadline")
    manager.maintain()
    assert fleet.runtime.stops == [uid]
    fleet.routing.volumes.tick(fleet.wake)
    assert len(fleet.runtime.starts) == 2
    assert fleet.admission.volume("a")["snapshot_id"]


def test_center_retirement_has_no_volume_operation(fleet):
    manager = fleet.manager
    uid = fleet.wake("a")
    previous = list(manager.volumes.events)
    manager.retire("a", uid)
    assert manager.volumes.events == previous
    assert uid in manager.volumes.images
    fleet.wake("a")
    assert uid in fleet.runtime.live


def test_restore_executor_accepts_only_broker_bound_uid(fleet):
    uid = fleet.wake("a")
    snapshot = fleet.routing.volumes.sleep("a")
    request = {"lifecycle": "restore", "uid": uid,
               "snapshot_id": snapshot["snapshot_id"], "sha256": snapshot["snapshot_sha256"]}
    service = HostService(fleet.admission, fleet.manager.volumes, None, fleet.manager)
    for peer in (0, 1001):
        assert "error" in service.lifecycle(request, peer)
    assert "error" in service.lifecycle({**request, "owner": "b"}, 1002)
    assert service.lifecycle(request, 1002) == {"restored": True}


@pytest.mark.parametrize("failure", ["suspended", "auth", "refused", "transport"])
def test_broker_admission_failure_refuses_wake_without_host_callback(fleet, monkeypatch, failure):
    from tinyassets.broker import box_admission
    manager = fleet.manager
    manager.awake_cap = 3
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
    assert not manager.boxes[a].wanted


@pytest.mark.parametrize("awake", [False, True])
def test_suspended_owner_cannot_wake_but_broker_can_stop_bound_box(fleet, awake):
    from tinyassets.broker.admission import local_mutate
    uid = fleet.identities.owner_machine("a")
    if awake:
        fleet.wake("a")
    local_mutate(fleet.identities.path, "a", "suspend", reason="abuse", suspended=True)
    for scheduled in (False, True):
        with pytest.raises(PermissionError):
            fleet.wake("a", scheduled=scheduled)
    assert fleet.control("stop", uid=uid) == {"stopped": True}
    assert not fleet.runtime.alive(uid)


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


def test_idle_never_stops_a_running_turn_and_drain_refuses_new_turns(fleet):
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    with activity.enter():
        fleet.manager.boxes[uid].last -= 10
        activity.last -= 10
        fleet.manager.maintain()
        assert uid in fleet.runtime.live
    activity.last -= 10
    fleet.manager.maintain()
    assert uid not in fleet.runtime.live
    with pytest.raises(RuntimeError, match="draining"):
        with activity.enter():
            pytest.fail("drained box admitted a turn")


def test_crash_restart_backoff_caps_and_repeated_failures_are_loud(fleet):
    manager = fleet.manager
    manager.backoff_cap = 2
    uid = fleet.wake("a")
    for failure in range(1, manager.failure_limit + 1):
        fleet.runtime.live.clear()
        before = time.monotonic()
        manager.maintain()
        box = manager.boxes[uid]
        assert box.failures == failure
        assert box.retry_at >= before + min(manager.backoff_cap, 2 ** (failure - 1))
        manager.maintain()
        assert not fleet.runtime.live
        if failure == manager.failure_limit:
            with pytest.raises(BoxError, match="repeatedly"):
                fleet.wake("a")
            assert manager.status(uid)["error"] == "owner daemon failed repeatedly"
        box.retry_at = 0
        manager.maintain()
        assert fleet.runtime.live == {uid}  # Recovery needs no human after the loud threshold.
    assert manager.status(fleet.identities.owner_machine("a"))["error"] == ""


def test_interactive_evicts_lru_idle_and_scheduled_waits_behind_it(fleet):
    manager = fleet.manager
    a = fleet.wake("a")
    b = fleet.wake("b")
    fleet.wake("a")  # b is now least recently used.
    fleet.wake("c")
    assert b not in fleet.runtime.live and a in fleet.runtime.live
    c = fleet.admission.owner("c")["uid"]
    with manager.condition:
        manager._stop(manager.boxes[c])
    manager.awake_cap = 1
    manager.wake_timeout = 5
    result = []

    def scheduled():
        result.append(fleet.wake("b", scheduled=True))

    thread = threading.Thread(target=scheduled)
    thread.start()
    deadline = time.monotonic() + 2
    while not manager.waiting and time.monotonic() < deadline:
        time.sleep(.01)
    assert manager.waiting and not result
    # Interactive callers bypass the older scheduled ticket and can evict.
    fleet.wake("c")
    assert fleet.runtime.live == {c} and not result
    with manager.condition:
        manager._stop(manager.boxes[c])
    thread.join(5)
    assert result == [b] and not thread.is_alive()


def test_busy_boxes_are_never_evicted_and_wake_is_bounded(fleet):
    manager = fleet.manager
    a, b = fleet.wake("a"), fleet.wake("b")
    manager.wake_timeout = .05
    with fleet.states[a].enter(), fleet.states[b].enter():
        with pytest.raises(BoxError, match="deadline"):
            fleet.wake("c")
    assert fleet.runtime.live == {a, b}


def test_stop_executes_only_broker_bound_uid_and_retires_old_ingress(fleet):
    uid = fleet.wake("a")
    service = HostService(fleet.admission, fleet.manager.volumes, None, fleet.manager)
    request = {"lifecycle": "stop", "uid": uid}
    for peer in (0, 1001):
        assert "error" in service.lifecycle(request, peer)
    assert "error" in service.lifecycle({**request, "owner": "b"}, 1002)
    assert service.lifecycle(request, 1002) == {"stopped": True}
    assert not fleet.runtime.live and fleet.states[uid].revoked


def test_ingress_old_secret_and_revoked_forward_fail(monkeypatch, tmp_path):
    from starlette.responses import JSONResponse

    from tinyassets import owner_activity, owner_runtime
    from tinyassets.owner_ingress import OwnerIngress

    activity = Activity()
    monkeypatch.setattr(owner_activity, "activity", activity)
    monkeypatch.setattr(owner_runtime, "_runtime", SimpleNamespace(principal="a", pid=os.getpid()))
    secret = tmp_path / "secret"
    old, fresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    secret.write_text(fresh)
    app = OwnerIngress(JSONResponse({"ok": True}), secret)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://owner") as client:
            denied = await client.get("/_box/status", headers={"x-box-secret": old})
            assert denied.status_code == 401
            headers = {"x-box-secret": fresh}
            assert (await client.get("/_box/status", headers=headers)).status_code == 200
            assert (await client.post("/_box/revoke", headers=headers, json={})).status_code == 200
            assert (await client.get("/_box/status", headers=headers)).status_code == 401

    asyncio.run(exercise())


def test_wake_needs_no_lease_and_active_owner_work_blocks_sleep(fleet):
    manager = fleet.manager
    uid = fleet.wake("a")
    box = manager.boxes[uid]
    assert "leases" not in vars(box)
    with fleet.states[uid].enter(), manager.condition:
        assert not manager._sleep(box, idle=0)
    with manager.condition:
        assert manager._sleep(box, idle=0)
    assert not fleet.runtime.live


def test_deleting_box_is_not_idle_even_after_last_turn_and_lease_finish(fleet):
    manager = fleet.manager
    uid = fleet.wake("a")
    activity = fleet.states[uid]
    with activity.enter():
        activity.status(deleting=True)
        with manager.condition:
            assert not manager._sleep(manager.boxes[uid], idle=0)
    with manager.condition:
        assert not manager._sleep(manager.boxes[uid], idle=0)
    assert fleet.runtime.live == {uid}
    activity.deletion_refused()
    with pytest.raises(RuntimeError, match="draining"), activity.enter():
        pytest.fail("refused deletion reopened activity")


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


def test_erased_record_cleanup_409_keeps_existing_box_drained(fleet, tmp_path, monkeypatch):
    import base64
    import json

    from starlette.responses import JSONResponse

    from tinyassets import owner_activity, owner_ingress, owner_runtime

    manager = fleet.manager
    uid = fleet.wake("a")
    gate = fleet.states[uid]
    gate.admit_deletion_only()
    route = {"secret": manager.boxes[uid].secret}
    assert gate.deletion_only
    monkeypatch.setattr(owner_activity, "activity", gate)
    monkeypatch.setattr(owner_runtime, "current", lambda: SimpleNamespace(principal="a", uid=uid))

    async def refused(identity):
        return JSONResponse({"error": "deletion_refused"}, status_code=409)

    monkeypatch.setattr(owner_ingress, "delete_local_account", refused)
    app = owner_ingress.OwnerIngress(None, tmp_path / str(uid) / "secret")
    headers = {"x-box-secret": route["secret"], "x-owner": "a",
               "x-owner-identity": base64.b64encode(json.dumps(
                   {"user_id": "a", "username": "a"}).encode()).decode()}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://owner") as client:
            assert (await client.post("/_box/account/delete", headers=headers)).status_code == 409

    asyncio.run(exercise())
    for keep_awake in (True, False):
        with pytest.raises(RuntimeError, match="draining"), gate.enter(keep_awake=keep_awake):
            pytest.fail("fenced owner executed")
    with manager.condition:
        assert not manager._sleep(manager.boxes[uid], idle=0)


def test_erasure_refuses_broker_restart_and_host_has_no_restart_intent(fleet):
    from tinyassets.broker.admission import local_mutate
    uid = fleet.wake("a")
    local_mutate(fleet.identities.path, "a", "erase")
    fleet.control("stop", uid=uid)
    for scheduled in (False, True):
        with pytest.raises(PermissionError):
            fleet.wake("a", scheduled=scheduled)
    fleet.manager.maintain()
    fleet.routing.volumes.tick(fleet.wake)
    assert not fleet.runtime.live
    assert len(fleet.runtime.starts) == 1

