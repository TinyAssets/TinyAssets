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

        def owner(self, owner, *, rotate=False, deletion_only=False):
            from tinyassets.boxes.admission import BoxSuspended

            if owner in self.revoked or owner not in {"a", "b", "c"}:
                raise LookupError("unadmitted")
            if owner in self.suspended and not deletion_only:
                raise BoxSuspended("suspended")
            if rotate:
                self.secrets[owner] = secrets.token_urlsafe(32)
            return {"uid": 300001 + ord(owner) - ord("a"), "quota": 1024**3,
                    "centers": [owner], "secret": self.secrets.get(owner)}

    class Runtime:
        box_id = staticmethod(GvisorRuntime.box_id)

        def __init__(self):
            self.live = set()
            self.starts = []
            self.stops = []

        def start(self, uid, gid, **kwargs):
            self.starts.append((uid, kwargs))
            self.live.add(uid)
            if kwargs.get("deletion_only"):
                states[uid].admit_deletion_only()

        def alive(self, uid):
            return uid in self.live

        def stop(self, uid):
            self.stops.append(uid)
            self.live.discard(uid)

        shutdown = stop

    states = {}
    admission, runtime = Admission(), Runtime()
    quota = SimpleNamespace(provision=lambda *args: None)
    relays = SimpleNamespace(bind=lambda owner, uid: tmp_path / f"{uid}.sock")
    manager = OwnerDaemons(runtime, admission, quota, relays, tmp_path,
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
                                     revoke=operation == "revoke",
                                     idle=body.get("idle_seconds", 0))

    monkeypatch.setattr(manager, "_paths", paths)
    monkeypatch.setattr(manager, "_rpc", rpc)
    return SimpleNamespace(manager=manager, runtime=runtime, admission=admission, states=states)


def test_wake_reuses_one_box_and_rotates_on_restart(fleet):
    manager = fleet.manager
    uid = manager.ensure_awake("a")
    first = fleet.admission.secrets["a"]
    assert manager.ensure_awake("a") == uid
    assert len(fleet.runtime.starts) == 1
    assert fleet.runtime.starts[0][1]["memory_bytes"] == 1024**3
    with manager.condition:
        assert manager._sleep(manager.boxes[uid], idle=0)
    manager.ensure_awake("a")
    assert fleet.admission.secrets["a"] != first
    assert len(fleet.runtime.starts) == 2


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
def test_suspended_cleanup_wake_admits_only_deletion(fleet, awake):
    from tinyassets.boxes.admission import BoxSuspended

    manager = fleet.manager
    if awake:
        manager.ensure_awake("a")
    fleet.admission.suspended.add("a")
    for scheduled in (False, True):
        with pytest.raises(BoxSuspended):
            manager.ensure_awake("a", scheduled=scheduled)
    lease = manager.resolve("a", cleanup=True)
    uid = lease["uid"]
    assert fleet.runtime.alive(uid)
    assert manager.boxes[uid].deletion_only
    # Pending delivery dispatch is a boot side effect. Even constructing a
    # maintenance worker is forbidden in this authenticated cleanup startup.
    from unittest.mock import patch

    from tinyassets import owner_activity, owner_runtime

    with (patch.object(owner_activity, "activity", fleet.states[uid]),
          patch.object(owner_runtime, "current", return_value=object()),
          patch.object(owner_runtime, "_start_maintenance") as maintenance):
        with owner_runtime.serving():
            assert fleet.states[uid].status()["active"] == 0
        maintenance.assert_not_called()
    manager.maintain()
    assert fleet.runtime.alive(uid)
    state = fleet.states[uid]
    assert state.quiesce(0)
    state.deletion_refused()
    with pytest.raises(RuntimeError, match="draining"):
        with state.enter():
            pytest.fail("cleanup wake admitted ordinary work")
    with pytest.raises(BoxSuspended):
        manager.ensure_awake("a", scheduled=True)
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
    retirement = {"lifecycle": "retire", "owner": "a", "uid": uid}
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
    with activity.enter():
        assert activity.active == 1


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
