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
    class Admission:
        def __init__(self):
            self.secrets = {}
            self.revoked = set()
            self.suspended = set()
            self.volumes = {owner: {"uid": 300001 + ord(owner) - ord("a"),
                                   "volume_generation": 1, "snapshot_id": None,
                                   "local": True, "asleep_at": None,
                                   "snapshot_sha256": None, "work": {"kind": "create"}}
                            for owner in ("a", "b", "c")}
            self.snapshots = {owner: [] for owner in self.volumes}

        def volume(self, owner, action="binding", **arguments):
            if action != "binding" and owner in self.revoked:
                raise LookupError("unadmitted")
            record = self.volumes[owner]
            if action == "sleep":
                record["work"] = {"kind": "snapshot", "snapshot_id": secrets.token_hex(16)}
            elif action == "created":
                record["work"] = None
            elif action == "publish":
                record.update(snapshot_id=arguments["snapshot_id"],
                              snapshot_sha256=arguments["sha256"], work=None,
                              asleep_at=time.time())
                self.snapshots[owner].insert(0, arguments)
            elif action == "wake":
                record["asleep_at"] = None
            elif action == "evict":
                # These runtime-decision fixtures never age a snapshot seven days.
                # Cold eviction itself uses the real map in the volume proofs.
                assert record["asleep_at"] is None or time.time() - record["asleep_at"] < 7 * 86400
                return None
            elif action == "snapshots":
                return self.snapshots[owner]
            elif action == "forget":
                self.snapshots[owner] = [s for s in self.snapshots[owner]
                                         if s["snapshot_id"] != arguments["snapshot_id"]]
            return dict(record)

        def pending_volumes(self):
            return [owner for owner, record in self.volumes.items()
                    if record["work"] and owner not in self.revoked]

        def owner(self, owner, *, include_secret=False):
            from tinyassets.boxes.admission import BoxSuspended

            if owner in self.revoked or owner not in {"a", "b", "c"}:
                raise LookupError("unadmitted")
            if owner in self.suspended:
                raise BoxSuspended("suspended")
            self.secrets.setdefault(owner, secrets.token_urlsafe(32))
            return {"uid": 300001 + ord(owner) - ord("a"), "quota": 1024**3,
                    "centers": [owner], "secret": self.secrets.get(owner),
                    "volume_generation": self.volumes[owner]["volume_generation"]}

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
    admission, runtime = Admission(), Runtime()
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
    return SimpleNamespace(manager=manager, runtime=runtime, admission=admission, states=states)


def test_wake_reuses_one_box_and_preserves_secret_on_restart(fleet):
    manager = fleet.manager
    uid = manager.ensure_awake("a")
    first = fleet.admission.secrets["a"]
    assert manager.ensure_awake("a") == uid
    assert len(fleet.runtime.starts) == 1
    assert fleet.runtime.starts[0][1]["memory_bytes"] == 1024**3
    with manager.condition:
        assert manager._sleep(manager.boxes[uid], idle=0)
    manager.ensure_awake("a")
    assert fleet.admission.secrets["a"] == first
    assert len(fleet.runtime.starts) == 2


def test_snapshot_deadline_drains_current_turn_before_sleep_and_restart(fleet):
    manager = fleet.manager
    uid = manager.ensure_awake("a")
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
    assert len(fleet.runtime.starts) == 2
    assert fleet.admission.volume("a")["snapshot_id"]


def test_center_retirement_has_no_volume_operation(fleet):
    manager = fleet.manager
    uid = manager.ensure_awake("a")
    previous = list(manager.volumes.events)
    manager.retire("a", uid)
    assert manager.volumes.events == previous
    assert uid in manager.volumes.images
    manager.ensure_awake("a")
    assert uid in fleet.runtime.live


def test_restore_control_is_root_only_and_accepts_no_uid():
    calls = []
    service = HostService(None, None, None, SimpleNamespace(
        restore=lambda owner, snapshot_id: calls.append((owner, snapshot_id)) or {
            "volume_generation": 2}))
    request = {"lifecycle": "restore", "owner": "alice", "snapshot_id": "a" * 32}
    for peer in (1001, 1002):
        assert "error" in service.lifecycle(request, peer)
    assert "error" in service.lifecycle({**request, "uid": 300002}, 0)
    assert not calls
    assert service.lifecycle(request, 0) == {"volume_generation": 2}
    assert calls == [("alice", "a" * 32)]


@pytest.mark.parametrize("failure", ["suspended", "auth", "refused", "transport"])
def test_admission_failure_stops_only_its_owner_while_two_turns_continue(
    fleet, monkeypatch, failure,
):
    from tinyassets.boxes.admission import BoxSuspended
    from tinyassets.boxes.provider import BoxAuthError

    manager = fleet.manager
    manager.awake_cap = 3
    a, b, c = [manager.ensure_awake(owner) for owner in "abc"]
    original = fleet.admission.owner

    def admission(owner, **kwargs):
        if owner == "a":
            raise {"suspended": BoxSuspended, "auth": BoxAuthError,
                   "refused": PermissionError, "transport": OSError}[failure](failure)
        return original(owner, **kwargs)

    monkeypatch.setattr(fleet.admission, "owner", admission)
    with fleet.states[b].enter(), fleet.states[c].enter():
        for _ in range(2):
            manager.maintain()
            assert fleet.runtime.live == {b, c}
            assert fleet.states[b].status()["active"] == fleet.states[c].status()["active"] == 1
            assert not fleet.states[b].draining and not fleet.states[c].draining
    expected = "suspended" if failure == "suspended" else "admission_refused"
    assert manager.boxes[a].error == expected
    assert manager.boxes[a].wanted is False


@pytest.mark.parametrize("awake", [False, True])
def test_suspended_cleanup_addresses_existing_box_without_new_admission(fleet, awake):
    from tinyassets.boxes.admission import BoxSuspended

    manager = fleet.manager
    if awake:
        manager.ensure_awake("a")
    fleet.admission.suspended.add("a")
    for scheduled in (False, True):
        with pytest.raises(BoxSuspended):
            manager.ensure_awake("a", scheduled=scheduled)
    if not awake:
        with pytest.raises(LookupError, match="no live cleanup box"):
            manager.resolve("a", cleanup=True)
        assert not fleet.runtime.alive(300001)
        return
    lease = manager.resolve("a", cleanup=True)
    uid = lease["uid"]
    assert fleet.runtime.alive(uid)
    assert lease["secret"] == manager.boxes[uid].secret
    manager.release("a", lease["lease"])


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
        fleet.manager.ensure_awake("a")
    uid = fleet.admission.owner("a")["uid"]
    assert fleet.manager.boxes[uid].awake  # uncertain slot remains reserved
    with pytest.raises(BoxError, match="unverified"):
        fleet.manager.ensure_awake("a")
    assert len(fleet.runtime.starts) == 1


def test_idle_never_stops_a_running_turn_and_drain_refuses_new_turns(fleet):
    uid = fleet.manager.ensure_awake("a")
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
    uid = manager.ensure_awake("a")
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
                manager.ensure_awake("a")
            assert manager.status("a")["error"] == "owner daemon failed repeatedly"
        box.retry_at = 0
        manager.maintain()
        assert fleet.runtime.live == {uid}  # Recovery needs no human after the loud threshold.
    assert manager.status("a")["error"] == ""


def test_interactive_evicts_lru_idle_and_scheduled_waits_behind_it(fleet):
    manager = fleet.manager
    a = manager.ensure_awake("a")
    b = manager.ensure_awake("b")
    manager.ensure_awake("a")  # b is now least recently used.
    manager.ensure_awake("c")
    assert b not in fleet.runtime.live and a in fleet.runtime.live
    c = fleet.admission.owner("c")["uid"]
    with manager.condition:
        manager._stop(manager.boxes[c])
    manager.awake_cap = 1
    manager.wake_timeout = 5
    result = []

    def scheduled():
        result.append(manager.ensure_awake("b", scheduled=True))

    thread = threading.Thread(target=scheduled)
    thread.start()
    deadline = time.monotonic() + 2
    while not manager.waiting and time.monotonic() < deadline:
        time.sleep(.01)
    assert manager.waiting and not result
    # Interactive callers bypass the older scheduled ticket and can evict.
    manager.ensure_awake("c")
    assert fleet.runtime.live == {c} and not result
    with manager.condition:
        manager._stop(manager.boxes[c])
    thread.join(5)
    assert result == [b] and not thread.is_alive()


def test_busy_boxes_are_never_evicted_and_wake_is_bounded(fleet):
    from tinyassets.boxes.provider import BoxBusy

    manager = fleet.manager
    a, b = manager.ensure_awake("a"), manager.ensure_awake("b")
    manager.wake_timeout = .05
    with fleet.states[a].enter(), fleet.states[b].enter():
        with pytest.raises(BoxBusy, match="deadline"):
            manager.ensure_awake("c")
    assert fleet.runtime.live == {a, b}


def test_retirement_stops_box_before_ack_and_control_roles_are_positive(fleet):
    manager = fleet.manager
    service = HostService(fleet.admission, None, None, manager)
    request = {"lifecycle": "ensure_awake", "owner": "a", "scheduled": False}
    assert "error" in service.lifecycle(request, 1002)
    assert "error" in service.lifecycle({**request, "uid": 300002}, 1001)
    uid = service.lifecycle(request, 1001)["uid"]
    retirement = {"lifecycle": "retire", "owner": "a"}
    assert "error" in service.lifecycle({**retirement, "uid": 300002}, 1002)
    assert "error" in service.lifecycle(retirement, 1001)
    fleet.admission.revoked.add("a")
    assert service.lifecycle(retirement, 1002) == {"stopped": True}
    assert not fleet.runtime.live and fleet.states[uid].revoked
    assert service.lifecycle(request, 1001) == {"error": "unadmitted"}


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


def test_abandoned_lease_expires_but_active_lease_blocks_sleep(fleet):
    manager = fleet.manager
    resolved = manager.resolve("a")
    box = manager.boxes[resolved["uid"]]
    with manager.condition:
        assert not manager._sleep(box, idle=0)
        box.leases[resolved["lease"]] = time.monotonic() - 1
        assert manager._sleep(box, idle=0)
    assert not fleet.runtime.live


def test_deleting_box_is_not_idle_even_after_last_turn_and_lease_finish(fleet):
    manager = fleet.manager
    uid = manager.ensure_awake("a")
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


def test_ledger_fenced_cleanup_409_keeps_existing_box_drained(fleet, tmp_path, monkeypatch):
    import base64
    import json

    from starlette.responses import JSONResponse

    from tinyassets import owner_activity, owner_ingress, owner_runtime
    from tinyassets.erasure_ledger import ErasureLedger

    manager = fleet.manager
    uid = manager.ensure_awake("a")
    ledger = ErasureLedger(tmp_path)
    ledger.initialize()
    ledger.add("a")
    route = manager.resolve("a", cleanup=True)
    gate = fleet.states[uid]
    assert gate.deletion_only and manager.boxes[uid].deletion_only
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
    manager.release("a", route["lease"])
    for keep_awake in (True, False):
        with pytest.raises(RuntimeError, match="draining"), gate.enter(keep_awake=keep_awake):
            pytest.fail("fenced owner executed")
    with manager.condition:
        assert not manager._sleep(manager.boxes[uid], idle=0)


def test_deletion_latch_refuses_every_restart_path(fleet):
    manager = fleet.manager
    uid = manager.ensure_awake("a")
    manager.resolve("a", cleanup=True)
    fleet.runtime.live.clear()
    for scheduled in (False, True):
        with pytest.raises(BoxError, match="cannot restart"):
            manager.ensure_awake("a", scheduled=scheduled)
    manager.maintain()
    with pytest.raises(BoxError, match="cannot restart"):
        manager._start(manager.boxes[uid], time.monotonic() + 1)
    assert len(fleet.runtime.starts) == 1
