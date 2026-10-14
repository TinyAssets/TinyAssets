"""Bounded process transport tests; no claim of a real gVisor gate pass."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.boxes.file_wire import MAX_MESSAGE, encode
from tinyassets.boxes.gvisor_files import GvisorFileBackend
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError


def backend():
    runtime = SimpleNamespace(runsc=Path("/pinned/runsc"), state_root=Path("/private/state"),
                              box_id=GvisorRuntime.box_id)
    return GvisorFileBackend(runtime, relay_for=lambda uid: Path("/trusted/relay"),
                             memory_for=lambda uid: 1024**3, request_timeout=2)


def launch_fixture(monkeypatch, code):
    popen = subprocess.Popen
    calls = []

    def launch(argv, **kwargs):
        calls.append(argv)
        return popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr("tinyassets.boxes.gvisor_files.subprocess.Popen", launch)
    return calls


def test_fixed_worker_and_large_stdin_do_not_deadlock(monkeypatch):
    calls = launch_fixture(monkeypatch,
                           "import sys; data=sys.stdin.buffer.read(); "
                           "sys.stdout.buffer.write(data)")
    instance = backend()
    body = encode({"operation": "write", "args": {"data": b"x" * (1024 * 1024)}})
    assert instance._invoke(300001, body) == body
    assert calls[0][:9] == [
        "/pinned/runsc", "--root", "/private/state", "--systemd-cgroup=false",
        "--network=none", "--platform=systrap", "--overlay2=root:memory,size=256m",
        "exec", "owner-300001",
    ]
    assert calls[0][9:13] == ["/opt/venv/bin/python", "-I", "-B", "-c"]
    assert "file_worker" in calls[0][13]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_combined_response_bound_stops_untrusted_worker(monkeypatch, stream):
    launch_fixture(monkeypatch,
                   f"import sys; sys.{stream}.buffer.write(b'x' * {MAX_MESSAGE + 65536})")
    with pytest.raises(BoxError, match="byte bound"):
        backend()._invoke(300001, b"{}")


def test_timeout_and_nonzero_exit_are_unknown_transport_errors(monkeypatch):
    launch_fixture(monkeypatch, "import time; time.sleep(5)")
    instance = backend()
    instance.request_timeout = 0.05
    with pytest.raises(BoxError, match="timed out"):
        instance._invoke(300001, b"{}")


def test_recovery_stops_runtime_survivor_without_bundle(tmp_path):
    instance = backend()
    instance.runtime.bundle_root = tmp_path
    calls = []
    instance.runtime._command = lambda *args: SimpleNamespace(
        stdout='[{"id":"owner-300001"}]')
    instance.runtime.stop = calls.append
    instance.recover()
    assert calls == [300001]


def test_stop_failure_keeps_owner_marked_awake():
    instance = backend()
    instance._awake[300001] = 0

    def fail(uid):
        raise BoxError("still running")

    instance.runtime.stop = fail
    with pytest.raises(BoxError):
        instance.stop(300001)
    assert 300001 in instance._awake


def test_file_wakes_share_capacity_and_evict_only_idle_boxes():
    instance = backend()
    started, stopped = [], []
    instance.runtime.start = lambda uid, gid, **kwargs: started.append(uid)
    instance.runtime.stop = stopped.append
    instance.wake(300001)
    instance.wake(300002)
    instance._busy.add(300001)
    instance.wake(300003)
    assert started == [300001, 300002, 300003]
    assert stopped == [300002]
    assert set(instance._awake) == {300001, 300003}


def test_failed_launch_cleanup_keeps_its_capacity_reservation():
    instance = backend()

    def fail(*args, **kwargs):
        raise BoxError("uncertain sandbox")

    instance.runtime.start = fail
    instance.runtime.stop = fail
    with pytest.raises(BoxError):
        instance.wake(300001)
    assert 300001 in instance._awake


def test_busy_capacity_waits_until_an_owner_is_proven_stopped():
    import threading

    instance = backend()
    instance.runtime.start = lambda *args, **kwargs: None
    instance.runtime.stop = lambda *args: None
    instance.wake(300001)
    instance.wake(300002)
    instance._busy.update({300001, 300002})
    completed = threading.Event()
    thread = threading.Thread(target=lambda: (instance.wake(300003), completed.set()))
    thread.start()
    try:
        with instance._capacity:
            assert instance._capacity.wait_for(lambda: bool(instance._waiting), timeout=1)
        assert not completed.is_set() and len(instance._awake) == 2
        instance.stop(300001)
        assert completed.wait(2)
        assert set(instance._awake) == {300002, 300003}
    finally:
        thread.join(3)
        assert not thread.is_alive()
