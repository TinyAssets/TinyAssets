"""The clock is outside; rows and idempotent work remain inside the owner process."""
from __future__ import annotations

import asyncio
import os
import queue
import socket
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import rpc_frames as rf
from tinyassets.broker.next_wake import FLOOR_SECONDS, NextWakeIndex
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.owner_reads import PlanMissing, Plans
from tinyassets.broker.waker import MAX_PENDING, Waker


@pytest.fixture
def index(tmp_path):
    state = tmp_path / ".broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True)
    for owner in ("a", "b", "c", "retired", "reserved"):
        identities.resolve(owner, allocate=True)
        if owner != "reserved":
            identities.admission("admit", owner, "cc-" + owner)
    identities.retire_owner("retired")
    return NextWakeIndex(identities)


def plan_store(index):
    return Plans(broker_dir=index.path.parent)


async def settled_tick(waker):
    await waker.tick()
    await asyncio.gather(*waker.pending.values(), return_exceptions=True)


def test_floor_includes_never_reported_and_preserves_reports_across_restart(index):
    calls = []
    clock = [100.0]
    index.report("b", None, now=90)
    index.report("c", 1000000, now=90)
    waker = Waker(index, plan_store(index), clock=lambda: clock[0],
                  wake=lambda owner, **kw: calls.append((owner, kw)))

    async def exercise():
        await settled_tick(waker)
        await settled_tick(waker)
        assert sorted(calls) == [(o, {"scheduled": True}) for o in ("a", "b", "c")]
        calls.clear()
        clock[0] += FLOOR_SECONDS - 1
        await settled_tick(waker)
        assert calls == []
        clock[0] += 1
        await settled_tick(waker)
        await settled_tick(waker)
        assert {o for o, _ in calls} == {"a", "b", "c"}

    asyncio.run(exercise())
    restored = NextWakeIndex(index.identities)
    with closing(sqlite3.connect(restored.path)) as db:
        assert db.execute("SELECT next_wake_at, reported_at FROM next_wakes "
                          "WHERE owner='c'").fetchone() == (1000000, 90)
        assert db.execute("SELECT reported_at FROM next_wakes "
                          "WHERE owner='a'").fetchone() == (None,)


@pytest.mark.parametrize("reason", ["billing", "abuse"])
def test_suspension_missing_plan_and_retirement_never_wake(index, reason, caplog):
    plans = plan_store(index)
    plans.set_suspension("a", reason=reason, suspended=True)
    index.report("a", 0, now=0)
    with closing(sqlite3.connect(plans.path)) as db, db:
        db.execute("DELETE FROM plans WHERE owner='b'")
    index.identities.admission("retire", "c", "cc-c")
    calls = []
    waker = Waker(index, plans, wake=lambda owner, **kw: calls.append(owner))

    async def exercise():
        await settled_tick(waker)
        await settled_tick(waker)
        assert calls == []
        assert "PlanMissing" in caplog.text
        plans.set_suspension("a", reason=reason, suspended=False)
        await settled_tick(waker)
        assert calls == ["a"]

    asyncio.run(exercise())
    with pytest.raises(PlanMissing):
        plans.suspended("b")


def test_due_delivery_retries_without_consuming_report_and_failed_wake_has_no_ack(index):
    calls = []
    for owner in ("a", "b", "c"):
        index.woken(owner, now=100)
    index.report("a", 90, now=99)

    def wake(owner, **kw):
        calls.append(owner)
        if len(calls) == 1:
            raise RuntimeError("capacity unavailable")
        # A tick reporting during startup must not be overwritten by its wake ACK.
        if len(calls) == 3:
            index.report(owner, None, now=102)

    waker = Waker(index, plan_store(index), wake=wake, clock=lambda: 101)

    async def exercise():
        await settled_tick(waker)
        with closing(sqlite3.connect(index.path)) as db:
            assert db.execute("SELECT last_woken_at FROM next_wakes "
                              "WHERE owner='a'").fetchone() == (100,)
        await settled_tick(waker)
        assert index.due(now=101) == ["a"]
        await settled_tick(waker)
        assert index.due(now=102) == []
        assert calls == ["a", "a", "a"]

    asyncio.run(exercise())


def test_slow_wakes_do_not_block_the_timer_and_are_bounded(index):
    release = threading.Event()
    entered = queue.Queue()

    def wake(owner, **kw):
        entered.put(owner)
        assert release.wait(10)

    waker = Waker(index, plan_store(index), wake=wake)

    async def exercise():
        try:
            await waker.tick()
            for _ in range(MAX_PENDING):
                await asyncio.to_thread(entered.get, timeout=5)
            await waker.tick()
            assert len(waker.pending) == MAX_PENDING
            assert entered.empty()
        finally:
            release.set()
            await asyncio.gather(*waker.pending.values())

    asyncio.run(exercise())


def test_report_rejects_invalid_times_and_retired_admission(index):
    for at in (True, "123", float("nan"), float("inf"), {}):
        with pytest.raises(ValueError):
            index.report("a", at, now=0)
    for owner in ("retired", "reserved", "unknown"):
        with pytest.raises(LookupError):
            index.report(owner, 0, now=0)


def test_lost_identity_database_is_never_recreated_by_the_wake_scan(index):
    index.identities.path.unlink()
    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        index.due(now=0)
    assert not index.identities.path.exists()


def test_production_layout_uses_one_plan_store_for_admission_billing_and_launch(tmp_path):
    from tinyassets.broker.box_admission import owner_record

    state = tmp_path / ".broker" / "state"
    state.mkdir(parents=True, mode=0o700)
    plans = Plans(tmp_path)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True, plans=plans)
    identities.resolve("a", allocate=True)
    identities.admission("admit", "a", "cc-a")
    assert plans.read("a")["tier"] == "free"
    assert not (state / "plans.db").exists()
    for reason in ("billing", "abuse"):
        plans.set_suspension("a", reason=reason, suspended=True)
    plans.set_plan("a", "paid", event_created=10)
    plans.set_suspension("a", reason="billing", suspended=False)
    with pytest.raises(PermissionError, match="suspended"):
        owner_record(identities, "a", rotate=True)
    with closing(sqlite3.connect(plans.path)) as db, db:
        db.execute("DELETE FROM plans WHERE owner='a'")
    with pytest.raises(PlanMissing):
        owner_record(identities, "a", rotate=True)


def test_broker_can_only_request_scheduled_wakes():
    from tinyassets.boxes.host_service import HostService

    calls = []

    def wake(owner, **kw):
        calls.append((owner, kw))
        return 300001

    service = HostService(None, None, None, None, SimpleNamespace(ensure_awake=wake))
    request = {"lifecycle": "ensure_awake", "owner": "a", "scheduled": True}
    assert service.lifecycle(request, 1002) == {"uid": 300001}
    for changes in ({"scheduled": False}, {"scheduled": 1}, {"uid": 300002}):
        assert "error" in service.lifecycle(request | changes, 1002)
    assert calls == [("a", {"scheduled": True})]


def test_waker_starts_without_request_and_shutdown_joins_control_calls(index):
    calls = []

    async def exercise():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def wake(owner, **kw):
            calls.append(owner)
            loop.call_soon_threadsafe(stop.set)

        waker = Waker(index, plan_store(index), wake=wake)
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
    from tinyassets import automations
    from tinyassets.boxes import relay as relay_module
    from tinyassets.boxes.owner_daemons import OwnerDaemons
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.broker.box_admission import owner_record
    from tinyassets.broker.server import BOX_HOST
    from tinyassets.storage import db_path

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

        def grant(self, principal, selected, purpose):
            assert live.identities.admitted_owner(selected) == principal
            return SimpleNamespace(uid=uid)

        def authorize(self, grant, selected):
            return live.identities.admitted_owner(selected) == owner

    with tempfile.TemporaryDirectory(prefix="ta-wake-") as short:
        directory = Path(short)
        relays = OwnerRelays(directory / "relays", Admission())
        runtime = ProcessRuntime({uid: tmp_path}, module="tests.support.owner_waker_process")
        admission = SimpleNamespace(owner=lambda owner, rotate=False:
                                    owner_record(live.identities, owner, rotate=rotate))
        manager = OwnerDaemons(runtime, admission, SimpleNamespace(provision=lambda *a: None),
                               relays, directory)

        def paths(uid, secret):
            path = directory / str(uid)
            path.mkdir(exist_ok=True)
            secret_file = path / "secret"
            secret_file.write_text(secret)
            return path, secret_file

        monkeypatch.setattr(manager, "_paths", paths)
        try:
            manager.ensure_awake(owner)
            yield SimpleNamespace(root=tmp_path, owner=owner, uid=uid, manager=manager,
                                  runtime=runtime, index=index, reports=reports,
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


def test_sleeping_box_runs_without_request_and_duplicate_wake_is_idempotent(sleeping_box):
    box = sleeping_box
    await_report(box, box.at)
    with box.manager.condition:
        assert box.manager._sleep(box.manager.boxes[box.uid], idle=0)
    assert not box.runtime.alive(box.uid)
    assert not (box.root / "executed").exists()
    (box.root / "clock").write_text(str(box.at))
    waker = Waker(box.index, plan_store(box.index), wake=box.manager.ensure_awake,
                  clock=lambda: box.at)
    asyncio.run(settled_tick(waker))
    await_report(box, box.at + 600)
    assert box.runtime.alive(box.uid)
    assert (box.root / "executed").read_text().splitlines() == [box.automation.automation_id]
    # Lost ACK/stale delivery is allowed. Restart the real tick at the same instant.
    with box.manager.condition:
        assert box.manager._sleep(box.manager.boxes[box.uid], idle=0)
    while not box.reports.empty():
        box.reports.get_nowait()
    box.manager.ensure_awake(box.owner, scheduled=True)
    await_report(box, box.at + 600)
    assert (box.root / "executed").read_text().splitlines() == [box.automation.automation_id]
    with closing(sqlite3.connect(box.root / ".automations.db")) as db:
        assert db.execute("SELECT COUNT(*) FROM automation_attempts").fetchone()[0] == 1


def test_bound_report_cannot_name_another_owner(sleeping_box):
    box = sleeping_box
    await_report(box, box.at)
    victim = box.index.identities.resolve("victim", allocate=True)
    box.index.identities.admission("admit", "victim", "cc-victim")
    box.index.report("victim", box.at + 99, now=0)
    path = box.relays.bind(box.owner, box.uid)
    for field in ("owner", "principal", "uid"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(path))
            sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_SHARED"}))
            assert rf.read_frame_blocking(sock).control() == {"op": "BOX_CONNECTED"}
            sock.sendall(rf.control(rf.CONNECTION, {
                "op": "NEXT_WAKE", "at": 1, field: victim.uid if field == "uid" else "victim"}))
            assert rf.read_frame_blocking(sock).control() == {"op": "BOX_REFUSED"}
    with closing(sqlite3.connect(box.index.path)) as db:
        assert db.execute("SELECT owner, next_wake_at FROM next_wakes "
                          "ORDER BY owner").fetchall() == [
            (box.owner, box.at), ("victim", box.at + 99)]
