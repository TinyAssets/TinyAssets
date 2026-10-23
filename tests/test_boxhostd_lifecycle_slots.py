"""A busy host queues broker lifecycle work and gives requested wakes priority."""

import threading
import time
from types import SimpleNamespace

import pytest

from tinyassets.boxes.host_service import HostService


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
        assert service.lifecycle({"lifecycle": "readiness"}, 0) == {"ready": False}
        assert service.broker_lifecycle({"lifecycle": "status", "uid": 300001},
                                        1002, timeout=0.02) == {
            "error": "boxhostd lifecycle wait timed out"}
    assert service.lifecycle({"lifecycle": "readiness"}, 0) == {"ready": True}


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

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

    monkeypatch.setattr(daemon_control, "connect", lambda *_, **__: (Socket(), None))
    monkeypatch.setattr(daemon_control, "send", lambda _, document: sent.append(document))
    monkeypatch.setattr(daemon_control, "receive", lambda *_: {"awake": False})
    token = daemon_control.REQUEST_DRIVEN.set(True)
    try:
        assert daemon_control.request("status", uid=300001) == {"awake": False}
    finally:
        daemon_control.REQUEST_DRIVEN.reset(token)
    assert sent == [{"lifecycle": "status", "uid": 300001, "_request_driven": True}]
