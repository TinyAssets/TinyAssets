"""The clock is outside; rows and idempotent work remain inside the owner process."""
from __future__ import annotations

import asyncio
import os
import queue
import socket
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.support.admission import Plans, async_call, drive, projection, retire_local, suspend
from tinyassets import rpc_frames as rf
from tinyassets.broker.admission import local_mutate
from tinyassets.broker.next_wake import NextWakeIndex
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.owner_reads import PlanMissing
from tinyassets.broker.waker import Waker


@pytest.fixture
def index(tmp_path):
    state = tmp_path / ".broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    for owner in ("a", "b", "c", "retired", "reserved"):
        identities.resolve(owner, allocate=True)
        if owner != "reserved":
            drive(identities.admit(owner, routing=projection(identities.path)))
            from tinyassets.broker.box_admission import provision

            provision(identities, tmp_path, owner)
    drive(local_mutate(identities.path, "retired", "erase",
      routing=projection(identities.path)))
    return NextWakeIndex(identities)


def plan_store(index):
    return Plans(broker_dir=index.path.parent)


async def settled_tick(waker):
    await waker.tick()
    await asyncio.gather(*waker.pending.values(), return_exceptions=True)


@pytest.mark.verifies('owner-daemon-in-box:C9')
def test_missing_row_reconstructs_and_null_never_periodically_wakes(index):
    calls = []
    clock = [100.0]
    index.report("b", None)
    index.report("c", 1000000)

    def wake(owner, **kw):
        calls.append((owner, kw))
        index.report(owner, None)

    waker = Waker(index, plan_store(index), clock=lambda: clock[0],
                  wake=async_call(wake))

    async def exercise():
        await settled_tick(waker)
        await settled_tick(waker)
        assert calls == [("a", {"scheduled": True})]
        calls.clear()
        clock[0] += 2 * 24 * 60 * 60
        await settled_tick(waker)
        assert calls == []
        with closing(sqlite3.connect(index.path)) as db, db:
            db.execute("DELETE FROM next_wakes WHERE owner='a'")
        await settled_tick(waker)
        await settled_tick(waker)
        assert calls == [("a", {"scheduled": True})]

    asyncio.run(exercise())
    restored = NextWakeIndex(index.identities)
    with closing(sqlite3.connect(restored.path)) as db:
        assert [r[1] for r in db.execute("PRAGMA table_info(next_wakes)")] == [
            "owner", "next_wake_at"]
        assert db.execute("SELECT * FROM next_wakes ORDER BY owner").fetchall() == [
            ("a", None), ("b", None), ("c", 1000000)]


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("reason", ["billing", "abuse"])
def test_suspension_and_missing_launch_plan_refuse_but_retired_center_wakes(index, reason, caplog):
    plans = plan_store(index)
    index.report("a", 0)
    suspend(plans, "a", reason=reason, suspended=True)
    with pytest.raises(PermissionError, match="suspended"):
        index.report("a", 0)
    with closing(sqlite3.connect(plans.path)) as db, db:
        db.execute("DELETE FROM plans WHERE owner='b'")
    retire_local(index.identities.path.parent / "synthetic-box-c", "cc-c")
    calls = []
    from tinyassets.broker.box_admission import owner_record

    def wake(owner, **kw):
        owner_record(index.identities, owner)
        calls.append(owner)
        index.report(owner, None)

    waker = Waker(index, plans, wake=async_call(wake))

    async def exercise():
        await settled_tick(waker)
        await settled_tick(waker)
        assert calls == ["c"]
        assert "PlanMissing" in caplog.text
        suspend(plans, "a", reason=reason, suspended=False)
        await settled_tick(waker)
        assert calls == ["c", "a"]

    asyncio.run(exercise())
    with pytest.raises(PlanMissing):
        owner_record(index.identities, "b")


@pytest.mark.verifies('owner-daemon-in-box:C9')
def test_due_delivery_retries_without_consuming_report_and_failed_wake_has_no_ack(index):
    calls = []
    for owner in ("a", "b", "c"):
        index.report(owner, None)
    index.report("a", 90)

    def wake(owner, **kw):
        calls.append(owner)
        if len(calls) == 1:
            raise RuntimeError("capacity unavailable")
        # A tick reporting during startup must not be overwritten by its wake ACK.
        if len(calls) == 3:
            index.report(owner, None)

    waker = Waker(index, plan_store(index), wake=async_call(wake), clock=lambda: 101)

    async def exercise():
        await settled_tick(waker)
        with closing(sqlite3.connect(index.path)) as db:
            assert db.execute("SELECT next_wake_at FROM next_wakes "
                              "WHERE owner='a'").fetchone() == (90,)
        await settled_tick(waker)
        assert index.due(now=101) == ["a"]
        await settled_tick(waker)
        assert index.due(now=102) == []
        assert calls == ["a", "a", "a"]

    asyncio.run(exercise())




@pytest.mark.verifies('owner-daemon-in-box:C9')
def test_report_rejects_invalid_times_and_retired_admission(index):
    for at in (True, "123", float("nan"), float("inf"), {}):
        with pytest.raises(ValueError):
            index.report("a", at)
    for owner in ("retired", "reserved", "unknown"):
        with pytest.raises(LookupError):
            index.report(owner, 0)


@pytest.mark.verifies('owner-daemon-in-box:T5')
def test_lost_identity_database_is_never_recreated_by_the_wake_scan(index):
    index.identities.path.unlink()
    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        index.due(now=0)
    assert not index.identities.path.exists()




@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_only_broker_can_request_bound_interactive_or_scheduled_wakes():
    from tinyassets.boxes.host_service import HostService

    calls = []

    def wake(owner, **kw):
        calls.append((owner, kw))
        return 300001

    service = HostService(None, None, None, SimpleNamespace(ensure_awake=wake))
    record = {"uid": 300001, "secret": "s" * 43}
    request = {"lifecycle": "wake", "owner": "a", "scheduled": True, "record": record}
    assert service.lifecycle(request, 1002) == {"uid": 300001}
    assert service.lifecycle(request | {"scheduled": False}, 1002) == {"uid": 300001}
    assert "error" in service.lifecycle(request, 1001)
    for changes in ({"scheduled": 1}, {"uid": 300002}):
        assert "error" in service.lifecycle(request | changes, 1002)
    assert calls == [("a", {"scheduled": True, "launch": record}),
                     ("a", {"scheduled": False, "launch": record})]


@pytest.mark.verifies('owner-daemon-in-box:T7')
def test_waker_starts_without_request_and_shutdown_joins_control_calls(index):
    calls = []

    async def exercise():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def wake(owner, **kw):
            calls.append(owner)
            loop.call_soon_threadsafe(stop.set)

        waker = Waker(index, plan_store(index), wake=async_call(wake))
        assert waker.poll_seconds == 30
        await asyncio.wait_for(waker.run(stop), timeout=5)
        assert calls
        assert all(task.done() for task in waker.pending.values())

    asyncio.run(exercise())


@pytest.fixture
def sleeping_box(tmp_path, monkeypatch, in_process_broker, cloud_runtime):  # noqa: F811
    from tests.support.http_model import seed_http_assignment
    from tests.support.owner_daemon_launcher import ProcessRuntime
    from tests.test_automations import _seed_branch, _seed_owner
    from tests.test_owner_volume_lifecycle import Admission as VolumeAdmission
    from tests.test_owner_volume_lifecycle import Images as Volumes
    from tinyassets import automations
    from tinyassets.boxes import relay as relay_module
    from tinyassets.boxes.owner_daemons import OwnerDaemons
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.broker.owner_volumes import provision as bind_volume
    from tinyassets.broker.server import BOX_HOST
    from tinyassets.storage import db_path

    # This process fixture shares the router and owner roots. Keep the owner's
    # admission-lock base private after root-ledger setup protects the router root.
    tmp_path.chmod(0o700)
    owner, center = "acct_alice", "universe_alice"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_http_assignment(tmp_path)
    _seed_owner(tmp_path)
    branch = _seed_branch(tmp_path)
    moment = datetime(2026, 10, 17, tzinfo=timezone.utc)
    (tmp_path / "clock").write_text(str(moment.timestamp()))
    automation = automations.register_automation(
        tmp_path, universe_id=center, owner_principal_id=owner, name="wake proof",
        branch_def_id=branch, interval_seconds=600, now=moment)
    live = in_process_broker.supervisor_for(tmp_path)
    live.server._roles = {os.getuid(): BOX_HOST}
    live.server._data_root = tmp_path
    uid = live.identities.owner_machine(owner)
    with closing(sqlite3.connect(live.identities.path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS box_records (principal TEXT PRIMARY KEY, "
                   "machine INTEGER, project_id INTEGER, bhard_bytes INTEGER)")
        db.execute("INSERT OR REPLACE INTO box_records VALUES (?,?,?,?)",
                   (owner, uid, uid, 1024**3))
        bind_volume(db, owner, uid)
    published = tmp_path / ".published"
    published.mkdir(exist_ok=True)
    with closing(sqlite3.connect(db_path(tmp_path))) as source:
        with closing(sqlite3.connect(db_path(published))) as destination:
            source.backup(destination)
    monkeypatch.setattr(relay_module, "peer", lambda sock: (os.getpid(), 1002, 1002))
    monkeypatch.setattr(relay_module, "os", SimpleNamespace(chown=lambda *a: None, chmod=os.chmod))
    reports = queue.Queue()
    index = live.server.next_wakes
    original_report = index.report

    def report(owner, at, **kw):
        original_report(owner, at, **kw)
        reports.put(at)

    monkeypatch.setattr(index, "report", report)

    class Admission:
        broker_socket = live.socket_path



    with tempfile.TemporaryDirectory(prefix="ta-wake-") as short:
        directory = Path(short)
        relays = OwnerRelays(directory / "relays", Admission())
        runtime = ProcessRuntime({uid: tmp_path}, module="tests.support.owner_waker_process")
        admission = VolumeAdmission(live.identities)
        manager = OwnerDaemons(runtime, admission, Volumes(),
                               relays, directory)

        def paths(uid, secret):
            path = directory / str(uid)
            path.mkdir(exist_ok=True)
            secret_file = path / "secret"
            secret_file.write_text(secret)
            return path, secret_file

        monkeypatch.setattr(manager, "_paths", paths)
        from tinyassets.boxes import daemon_control
        from tinyassets.boxes.host_service import HostService
        service = HostService(admission, manager.volumes, relays, manager)
        def control(operation, owner=None, **fields):
            result = service.lifecycle({"lifecycle": operation, **fields,
                **({"owner": owner} if owner is not None else {})}, 1002)
            if set(result) == {"error"}:
                raise RuntimeError(result["error"])
            return result
        monkeypatch.setattr(daemon_control, "request", control)
        routing = live.server.routing
        routing.volumes.execute = control
        monkeypatch.setattr(routing, "publish", lambda **kwargs: None)
        try:
            drive(routing.wake(owner))
            yield SimpleNamespace(root=tmp_path, owner=owner, uid=uid, manager=manager,
                                  runtime=runtime, index=index, reports=reports, wake=routing.wake,
                                  relays=relays, at=moment.timestamp() + 600,
                                  automation=automation)
        finally:
            manager.close()
            for log in runtime.logs:
                log.seek(0)
                print(log.read().decode(errors="replace"))
            runtime.close()
            relays.close()


def await_report(box, expected):
    deadline = time.monotonic() + 20
    while True:
        at = box.reports.get(timeout=max(.001, deadline - time.monotonic()))
        if at == expected:
            return
        assert time.monotonic() < deadline, (at, expected)


def sleep_when_quiet(box):
    # A broker report arrives before the background tick unwinds its gate.
    # Exercise real drain until that work finishes; never force-stop an active box.
    deadline = time.monotonic() + 20
    while True:
        with box.manager.condition:
            if box.manager._sleep(box.manager.boxes[box.uid], idle=0):
                return
        assert box.runtime.alive(box.uid)
        assert time.monotonic() < deadline, "background work did not drain"


@pytest.mark.verifies('owner-daemon-in-box:C9')
def test_sleeping_box_runs_without_request_and_duplicate_wake_is_idempotent(sleeping_box):
    box = sleeping_box
    await_report(box, box.at)
    sleep_when_quiet(box)
    assert not box.runtime.alive(box.uid)
    assert not (box.root / "executed").exists()
    (box.root / "clock").write_text(str(box.at))
    waker = Waker(box.index, plan_store(box.index), wake=async_call(box.wake),
                  clock=lambda: box.at)
    asyncio.run(settled_tick(waker))
    await_report(box, box.at + 600)
    assert box.runtime.alive(box.uid)
    assert (box.root / "executed").read_text().splitlines() == [box.automation.automation_id]
    # Lost ACK/stale delivery is allowed. Restart the real tick at the same instant.
    sleep_when_quiet(box)
    while not box.reports.empty():
        box.reports.get_nowait()
    drive(box.wake(box.owner, scheduled=True))
    await_report(box, box.at + 600)
    assert (box.root / "executed").read_text().splitlines() == [box.automation.automation_id]
    with closing(sqlite3.connect(box.root / ".automations.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM automation_attempts").fetchone()[0] == 1


@pytest.mark.verifies('owner-daemon-in-box:T5')
def test_bound_report_cannot_name_another_owner(sleeping_box):
    box = sleeping_box
    await_report(box, box.at)
    victim = box.index.identities.resolve("victim", allocate=True)
    drive(box.index.identities.admit("victim", routing=projection(box.index.identities.path)))
    box.index.report("victim", box.at + 99)
    path = box.relays.bind(box.owner, box.uid)
    for field in ("owner", "principal", "uid"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(path))
            sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECT"}))
            assert rf.read_frame_blocking(sock).control() == {"op": "BOX_CONNECTED"}
            sock.sendall(rf.control(rf.CONNECTION, {
                "op": "NEXT_WAKE", "at": 1, field: victim.uid if field == "uid" else "victim"}))
            assert rf.read_frame_blocking(sock).control() == {"op": "BOX_REFUSED"}
    with closing(sqlite3.connect(box.index.path)) as db:
        assert db.execute("SELECT owner, next_wake_at FROM next_wakes "
                          "ORDER BY owner").fetchall() == [
            (box.owner, box.at), ("victim", box.at + 99)]
