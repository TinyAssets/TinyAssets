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
