"""A busy host queues broker lifecycle work and gives requested wakes priority."""

import asyncio
import os
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from tinyassets.boxes.host_service import HostService


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_broker_queue_waits_without_consuming_the_shared_worker_pool():
    from tinyassets.boxes.daemon_control import REQUEST_DRIVEN
    from tinyassets.broker.routing import BrokerLifecycle

    async def scenario():
        broker = BrokerLifecycle.__new__(BrokerLifecycle)
        broker.config = SimpleNamespace(volume_concurrency=3)
        entered = threading.Event()
        release = threading.Event()
        started = [0]
        guard = threading.Lock()

        def background():
            with guard:
                started[0] += 1
                if started[0] == 3:
                    entered.set()
            release.wait(3)

        tasks = [asyncio.create_task(broker.host_call(background)) for _ in range(40)]
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert started[0] == 3
            token = REQUEST_DRIVEN.set(True)
            try:
                assert await asyncio.wait_for(broker.host_call(lambda: "awake"), 1) == "awake"
            finally:
                REQUEST_DRIVEN.reset(token)
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_broker_readiness_counts_durable_pending_and_running_work(monkeypatch):
    from tinyassets.broker import owner_volumes
    from tinyassets.broker.routing import BrokerLifecycle

    async def scenario():
        broker = BrokerLifecycle.__new__(BrokerLifecycle)
        broker.identities = SimpleNamespace(work_ready=True)
        broker.tasks = {}
        monkeypatch.setattr(owner_volumes, "pending", lambda _, **__: ["owner"])
        monkeypatch.setattr(owner_volumes, "pending_counts", lambda _, **__: {"snapshot": 1})
        blocked = await broker.readiness()
        assert blocked["ready"] is False and blocked["pending"] == {"snapshot": 1}
        monkeypatch.setattr(owner_volumes, "pending", lambda _, **__: [])
        broker.identities.work_ready = False
        closed = await broker.readiness()
        assert closed == {"ready": False, "blocked": "admission"}
        broker.identities.work_ready = True
        assert await broker.readiness() == {"ready": True}
        release = asyncio.Event()
        broker.tasks["owner"] = asyncio.create_task(release.wait())
        try:
            busy = await broker.readiness()
            assert busy["ready"] is False and busy["tasks"] == 1
        finally:
            release.set()
            await broker.tasks["owner"]

    asyncio.run(scenario())


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_readiness_asks_for_pins_only_when_excess_rows_remain(monkeypatch):
    from tinyassets.broker import owner_volumes
    from tinyassets.broker.routing import BrokerLifecycle

    async def scenario():
        broker = BrokerLifecycle.__new__(BrokerLifecycle)
        broker.identities = SimpleNamespace(work_ready=True)
        broker.tasks = {}
        broker.config = SimpleNamespace(volume_concurrency=1)
        calls = []
        forwarded = {}

        def execute(operation, owner=None, **fields):
            calls.append(operation)
            forwarded["pinned"] = [[300001, "a" * 32]]
            return {"pinned": forwarded["pinned"]}

        broker.volumes = SimpleNamespace(execute=execute)
        monkeypatch.setattr(owner_volumes, "pending", lambda _, **__: [])
        monkeypatch.setattr(owner_volumes, "pending_counts", lambda _, **__: {})
        assert await broker.readiness() == {"ready": True}
        assert calls == []

        monkeypatch.setattr(owner_volumes, "pending", lambda _, **__: ["alice"])
        monkeypatch.setattr(owner_volumes, "pending_counts",
                            lambda _, **__: {"excess_snapshots": 2})

        def capture(identities, pinned):
            forwarded["seen"] = pinned
            return 0

        monkeypatch.setattr(owner_volumes, "actionable_excess", capture)
        assert await broker.readiness() == {"ready": True}
        assert calls == ["pins"] and forwarded["seen"] == [[300001, "a" * 32]]

        monkeypatch.setattr(owner_volumes, "actionable_excess", lambda identities, pinned: 1)
        assert await broker.readiness() == {
            "ready": False, "tasks": 0, "pending": {"excess_snapshots": 1}}

        def broken(operation, owner=None, **fields):
            raise RuntimeError("catalog unreadable")

        broker.volumes.execute = broken
        held = await broker.readiness()
        assert held["pending"] == {"excess_snapshots": 2}

        monkeypatch.setattr(owner_volumes, "actionable_excess", lambda identities, pinned: 0)
        monkeypatch.setattr(owner_volumes, "pending_counts",
                            lambda _, **__: {"snapshot": 1, "excess_snapshots": 2})
        broker.volumes.execute = execute
        assert await broker.readiness() == {
            "ready": False, "tasks": 0, "pending": {"snapshot": 1}}

    asyncio.run(scenario())


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_broker_connection_reaches_serve_when_shared_nonbroker_slots_are_full(
    tmp_path, monkeypatch,
):
    from tinyassets.boxes import host_service

    service = HostService(None, None, None, SimpleNamespace())
    service._slots = threading.BoundedSemaphore(1)
    service._slots.acquire()
    monkeypatch.setattr(host_service, "peer", lambda _: (os.getpid(), 1002, 1002))
    monkeypatch.setattr(host_service.os, "chown", lambda *_: None)
    accepted = threading.Event()
    stop = threading.Event()
    path = tmp_path / "daemon.sock"

    def handle(connection):
        accepted.set()
        connection.close()

    server = threading.Thread(target=service.serve, args=(path, os.getgid(), handle, stop))
    server.start()
    try:
        for _ in range(100):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                try:
                    client.connect(str(path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    time.sleep(0.01)
        else:
            pytest.fail("boxhostd socket never began listening")
        assert accepted.wait(2)
    finally:
        stop.set()
        service._slots.release()
        server.join(3)
        assert not server.is_alive()


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_held_host_slot_queues_a_requested_wake_ahead_of_a_scheduled_wake():
    service = HostService(None, None, None, SimpleNamespace())
    service._broker_slots = service._new_broker_slots(1)
    entered = threading.Event()
    release = threading.Event()
    completed = []

    def lifecycle(document, uid):
        assert uid == 1002
        if document["lifecycle"] == "snapshot":
            entered.set()
            assert release.wait(3)
        completed.append(document.get("owner", "snapshot"))
        return {"ok": True}

    service.lifecycle = lifecycle
    snapshot = {"lifecycle": "snapshot", "uid": 300001, "snapshot_id": "s"}
    scheduled = {"lifecycle": "wake", "owner": "scheduled", "scheduled": True}
    requested = {"lifecycle": "wake", "owner": "requested", "scheduled": False}
    results = {}

    def call(name, document):
        results[name] = service.broker_lifecycle(document, 1002, timeout=2)

    threads = [threading.Thread(target=call, args=("snapshot", snapshot))]
    threads[0].start()
    assert entered.wait(2)
    for name, document in (("scheduled", scheduled), ("requested", requested)):
        thread = threading.Thread(target=call, args=(name, document))
        threads.append(thread)
        thread.start()
        time.sleep(0.03)
    assert completed == []
    release.set()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert list(completed) == ["snapshot", "requested", "scheduled"]
    assert all(result == {"ok": True} for result in results.values())


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_host_slot_wait_is_bounded_and_readiness_tracks_post_drain_work():
    service = HostService(None, None, None, SimpleNamespace())
    service._broker_slots = service._new_broker_slots(1)
    service.broker_ready.set()
    assert service.lifecycle({"lifecycle": "readiness"}, 0) == {"ready": True}
    with service._broker_slots.claim(request_driven=False, timeout=0.1):
        busy = service.lifecycle({"lifecycle": "readiness"}, 0)
        assert busy["ready"] is False and busy["background_active"] == 1
        assert service.broker_lifecycle({"lifecycle": "status", "uid": 300001},
                                        1002, timeout=0.02) == {
            "error": "boxhostd lifecycle wait timed out"}
    assert service.lifecycle({"lifecycle": "readiness"}, 0) == {"ready": True}
    with service._broker_slots.claim(request_driven=True, timeout=0.1):
        assert service.lifecycle({"lifecycle": "readiness"}, 0) == {"ready": True}


@pytest.mark.verifies("owner-daemon-in-box:C2")
def test_pin_report_does_not_occupy_a_readiness_slot(monkeypatch):
    service = HostService(None, SimpleNamespace(remote="remote:box"), None, SimpleNamespace())
    service._broker_slots = service._new_broker_slots(1)
    monkeypatch.setattr(
        "tinyassets.platform_backup.pinned_snapshots",
        lambda destination: {(300001, "a" * 32), (300002, "b" * 32)}
        if destination == "remote:box" else set())
    with service._broker_slots.claim(request_driven=False, timeout=0.1):
        assert service.broker_lifecycle({"lifecycle": "pins"}, 1002, timeout=0.02) == {
            "pinned": [[300001, "a" * 32], [300002, "b" * 32]]}
        assert service._broker_slots.background_active == 1
        assert service.broker_lifecycle(
            {"lifecycle": "pins", "released": []}, 1002, timeout=0.02) == {
            "error": "boxhostd lifecycle wait timed out"}
    assert "error" in service.lifecycle({"lifecycle": "pins"}, 0)


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_requested_wake_has_a_reserved_slot_while_background_copies_run():
    service = HostService(None, None, None, SimpleNamespace())
    service._broker_slots = service._new_broker_slots(4)
    entered = threading.Event()
    release = threading.Event()
    count = [0]
    lock = threading.Lock()

    def lifecycle(document, uid):
        if document["lifecycle"] == "snapshot":
            with lock:
                count[0] += 1
                if count[0] == 3:
                    entered.set()
            assert release.wait(3)
        return {"ok": True}

    service.lifecycle = lifecycle
    threads = [threading.Thread(target=service.broker_lifecycle,
                                args=({"lifecycle": "snapshot"}, 1002),
                                kwargs={"timeout": 2}) for _ in range(3)]
    for thread in threads:
        thread.start()
    try:
        assert entered.wait(2)
        assert service.broker_lifecycle({"lifecycle": "status", "uid": 300002,
                                         "_request_driven": True},
                                        1002, timeout=0.05) == {"ok": True}
    finally:
        release.set()
        for thread in threads:
            thread.join(3)


@pytest.mark.verifies("owner-daemon-in-box:T1")
def test_request_preflight_priority_reaches_host_control_message(monkeypatch):
    from tinyassets.boxes import daemon_control

    sent = []
    deadlines = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

    def connect(*_, **kwargs):
        deadlines.append(kwargs["timeout"])
        return Socket(), None

    monkeypatch.setattr(daemon_control, "connect", connect)
    monkeypatch.setattr(daemon_control, "send", lambda _, document: sent.append(document))
    monkeypatch.setattr(daemon_control, "receive", lambda *_: {"awake": False})
    token = daemon_control.REQUEST_DRIVEN.set(True)
    try:
        assert daemon_control.request("status", uid=300001) == {"awake": False}
    finally:
        daemon_control.REQUEST_DRIVEN.reset(token)
    assert sent == [{"lifecycle": "status", "uid": 300001, "_request_driven": True}]
    assert deadlines == [120]
