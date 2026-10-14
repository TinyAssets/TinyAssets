"""Real pipe supervision with a substituted runsc; not gVisor isolation evidence."""

import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.boxes.gvisor_files import GvisorFileBackend
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.handles import OwnerGrant
from tinyassets.boxes.owner_files import OwnerFileHost
from tinyassets.boxes.production_files import ProductionFileProvider
from tinyassets.boxes.provider import BoxAuthError, BoxBusy, BoxError, ExecLimits, ExecState

PRELUDE = "import sys,struct; n=struct.unpack('!I',sys.stdin.buffer.read(4))[0]; " \
          "sys.stdin.buffer.read(n); "


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    state = {"code": "print('hello')", "stop_fail": False, "launches": []}
    popen = subprocess.Popen

    def launch(argv, **kwargs):
        state["launches"].append(argv)
        return popen([sys.executable, "-c", PRELUDE + state["code"]], **kwargs)

    monkeypatch.setattr("tinyassets.boxes.gvisor_files.subprocess.Popen", launch)
    driver = SimpleNamespace(runsc=Path("/pinned/runsc"), state_root=tmp_path / "runsc",
                             box_id=GvisorRuntime.box_id)
    backend = GvisorFileBackend(driver, relay_for=None, memory_for=None)
    backend.recover = lambda: None
    backend._wake = lambda uid: None

    def stop(uid):
        if state["stop_fail"]:
            raise BoxError("sandbox is not quiescent")

    backend.stop = stop
    grant = OwnerGrant("alice", 300001, "session")
    host = OwnerFileHost(state_dir=tmp_path / "host", backend=backend,
                         authorize=lambda g, c: g == grant and c == "a")

    class Channel:
        def request(self, request):
            if "bind" in request:
                return host.bind(grant, request["bind"])
            return host.request(grant, **request)["result"]

        def close(self):
            pass

    provider = ProductionFileProvider(channel_factory=lambda *args: Channel())
    handle = provider.bind("a", account_id="alice", turn_id="session")
    yield host, provider, handle, state
    state["stop_fail"] = False
    provider.close()
    host.close()


def collect(provider, handle, exec_id):
    events = list(provider.stream(handle, exec_id, timeout=5))
    return b"".join(event.data for event in events), events[-1]


def test_host_records_output_after_quiescence_and_never_replays(runtime):
    host, provider, handle, state = runtime
    before = provider.committed_generation(handle)
    exec_id = provider.start_exec(handle, "once", ["echo", "hello"], cwd=handle.root)
    assert provider.start_exec(handle, "once", ["echo", "hello"], cwd=handle.root) == exec_id
    output, end = collect(provider, handle, exec_id)
    assert output == b"hello\n" and end.exit_code == 0
    assert provider.exec_status(handle, "once").state is ExecState.EXITED
    assert provider.committed_generation(handle) > before
    assert len(state["launches"]) == 1
    assert "exec_worker" in state["launches"][0][-1]
    assert host.state.pending("alice") == 0
    assert collect(provider, handle, exec_id)[0] == output


@pytest.mark.parametrize(("code", "limits", "expected"), [
    ("import time; time.sleep(10)", ExecLimits(.1, 20), "timeout"),
    ("sys.stdout.buffer.write(b'x'*10000)", ExecLimits(2, 20), "output_limit"),
])
def test_host_enforces_time_and_output_bounds(runtime, code, limits, expected):
    _, provider, handle, state = runtime
    state["code"] = code
    exec_id = provider.start_exec(handle, "bounded", ["unused"], limits=limits)
    output, end = collect(provider, handle, exec_id)
    assert end.killed == expected and len(output) <= limits.output_bytes


def test_pending_work_blocks_snapshots_and_cancel_is_reachable(runtime):
    _, provider, handle, state = runtime
    state["code"] = "import time; time.sleep(10)"
    exec_id = provider.start_exec(handle, "long", ["unused"])
    with pytest.raises(BoxBusy):
        provider.read_many(handle, ["/cc/file"], max_total=20)
    with pytest.raises(BoxBusy):
        provider.start_exec(handle, "another", ["unused"])
    provider.cancel(handle, exec_id)
    assert collect(provider, handle, exec_id)[1].killed == "cancelled"


def test_interactive_stdin_is_bounded_idempotent_and_not_written_to_owner_files(runtime):
    _, provider, handle, state = runtime
    state["code"] = "sys.stdout.buffer.write(sys.stdin.buffer.read(6)); sys.stdout.flush()"
    exec_id = provider.start_exec(handle, "interactive", ["unused"], interactive_stdin=True)
    provider.send_stdin(handle, exec_id, "reply-1", b"abc")
    provider.send_stdin(handle, exec_id, "reply-1", b"abc")
    provider.send_stdin(handle, exec_id, "reply-2", b"def")
    assert collect(provider, handle, exec_id)[0] == b"abcdef"


def test_failed_stop_is_unknown_and_cannot_publish_a_generation(runtime):
    host, provider, handle, state = runtime
    state["stop_fail"] = True
    exec_id = provider.start_exec(handle, "unknown", ["unused"])
    with pytest.raises(BoxError, match="unknown"):
        collect(provider, handle, exec_id)
    assert provider.exec_status(handle, "unknown").state is ExecState.UNKNOWN_AFTER_RESTORE
    with pytest.raises(BoxBusy):
        provider.committed_generation(handle)
    with pytest.raises(BoxError, match="unknown"):
        provider.start_exec(handle, "unknown", ["unused"])
    assert len(state["launches"]) == 1
    assert host.state.pending("alice") == 1


def test_launch_rechecks_admission_under_deletion_lock(runtime):
    host, provider, handle, state = runtime
    # Hold launch behind the same owner lock used to fence deletion.
    with host.lock("alice"):
        exec_id = provider.start_exec(handle, "queued", ["unused"])
        host.authorize = lambda *args: False
    deadline = time.monotonic() + 5
    while host.executions.running and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert not state["launches"]
    with pytest.raises(BoxAuthError):
        provider.cancel(handle, exec_id)


def test_receipt_store_refuses_growth_instead_of_filling_host_disk(tmp_path):
    import sqlite3

    from tinyassets.boxes.state import BoxHostState

    maximum = 1024 * 1024
    state = BoxHostState(tmp_path / "receipts.db", max_bytes=maximum)
    try:
        state.begin("alice", "effect", "exec", "digest")
        with pytest.raises(sqlite3.Error):
            state.finish("alice", "effect", {"output": "x" * (maximum * 2)})
        assert state.lookup("alice", "effect")["state"] == "running"
        assert state.begin("alice", "effect", "exec", "digest") is not None
        assert sum(path.stat().st_size for path in tmp_path.iterdir()) < maximum * 4
    finally:
        state.close()
