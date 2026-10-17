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
    queued = provider.start_exec(handle, "another", ["unused"])
    provider.cancel(handle, queued)
    assert collect(provider, handle, queued)[1].killed == "cancelled"
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


def test_owner_fifo_holds_generation_and_cancels_queued_without_launch(runtime):
    host, provider, handle, state = runtime
    started, release = threading.Event(), threading.Event()
    launches = []

    def execute(uid, args, running):
        launches.append(args["argv"])
        started.set()
        assert release.wait(5)
        return {"exit_code": 0, "killed": None}

    host.backend.execute = execute
    first = provider.start_exec(handle, "first", ["first"])
    assert started.wait(5)
    second = provider.start_exec(handle, "second", ["second"])
    third = provider.start_exec(handle, "third", ["third"])
    assert host.state.pending("alice") == 3
    provider.cancel(handle, second)
    assert collect(provider, handle, second)[1].killed == "cancelled"
    assert host.state.pending("alice") == 2
    with pytest.raises(BoxBusy):
        provider.committed_generation(handle)
    release.set()
    assert collect(provider, handle, first)[1].exit_code == 0
    assert collect(provider, handle, third)[1].exit_code == 0
    assert launches == [["first"], ["third"]]
    assert host.state.pending("alice") == 0


def test_failed_quiescence_cancels_successor_and_keeps_owner_closed(runtime):
    host, provider, handle, state = runtime
    started, release = threading.Event(), threading.Event()
    launches = []

    def execute(uid, args, running):
        launches.append(args["argv"])
        started.set()
        assert release.wait(5)
        return {"exit_code": 0, "killed": None}

    host.backend.execute = execute
    first = provider.start_exec(handle, "first", ["first"])
    assert started.wait(5)
    second = provider.start_exec(handle, "second", ["second"])
    state["stop_fail"] = True
    release.set()
    with pytest.raises(BoxError, match="unknown"):
        collect(provider, handle, first)
    assert collect(provider, handle, second)[1].killed == "cancelled"
    assert launches == [["first"]]
    assert host.state.pending("alice") == 1
    with pytest.raises(BoxBusy):
        provider.start_exec(handle, "third", ["third"])


def test_session_close_cancels_its_queue_and_exec_ids_are_session_scoped(runtime):
    from tinyassets.boxes.provider import OpIdReuse

    host, provider, handle, state = runtime
    state["code"] = "import time; time.sleep(10)"
    first = provider.start_exec(handle, "first", ["first"])
    second = provider.start_exec(handle, "second", ["second"])
    other = OwnerGrant("alice", 300001, "other-session")
    host.authorize = lambda g, c: g.owner_id == "alice" and c == "a"
    bound = host.bind(other, "a")
    # A sibling session cannot recover the opaque exec id by guessing an op id.
    args = {"argv": ["first"], "stdin": b"", "env": {}, "cwd": "/cc",
            "wall_seconds": 300, "output_bytes": 1048576, "interactive_stdin": False}
    with pytest.raises(OpIdReuse):
        host.request(other, bound["token"], "start_exec", args, op_id="first")
    host.close_session(OwnerGrant("alice", 300001, "session"))
    deadline = time.monotonic() + 5
    while host.executions.running and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert not host.executions.running
    assert host.state.pending("alice") == 0
    assert host.state.find_exec("alice", first)["outcome"]["killed"] == "cancelled"
    assert host.state.find_exec("alice", second)["outcome"]["killed"] == "cancelled"


def test_host_capacity_yields_to_another_owner_before_same_owner_successor(runtime):
    host, provider, handle, _ = runtime
    host.authorize = lambda grant, center: True
    launched = []
    changes = threading.Condition()
    releases = {name: threading.Event() for name in ("a1", "a2", "b1", "c1")}

    def execute(uid, args, running):
        name = args["argv"][0]
        with changes:
            launched.append(name)
            changes.notify_all()
        assert releases[name].wait(5)
        return {"exit_code": 0, "killed": None}

    def start(owner, uid, name):
        grant = OwnerGrant(owner, uid, "session")
        token = host.bind(grant, "a")["token"]
        args = {"argv": [name], "stdin": b"", "env": {}, "cwd": "/cc",
                "wall_seconds": 5, "output_bytes": 100, "interactive_stdin": False}
        return host.request(grant, token, "start_exec", args, op_id=name)

    host.backend.execute = execute
    try:
        start("alice", 300001, "a1")
        start("bob", 300002, "b1")
        with changes:
            assert changes.wait_for(lambda: len(launched) == 2, timeout=5)
        start("alice", 300001, "a2")
        start("charlie", 300003, "c1")
        releases["a1"].set()
        with changes:
            assert changes.wait_for(lambda: len(launched) == 3, timeout=5)
        assert launched[2] == "c1"
        assert host.state.pending("alice") == 1
    finally:
        for release in releases.values():
            release.set()


def test_cancelling_not_yet_selected_owner_job_does_not_duplicate_its_successor(runtime):
    host, provider, handle, state = runtime
    # Prevent the pool from selecting its first job until a cancelled empty
    # owner queue has been replaced. Exactly one work item may own that queue.
    with host.lock("alice"):
        first = provider.start_exec(handle, "first", ["first"])
        provider.cancel(handle, first)
        second = provider.start_exec(handle, "second", ["second"])
    assert collect(provider, handle, second)[1].exit_code == 0
    assert collect(provider, handle, first)[1].killed == "cancelled"
    assert len(state["launches"]) == 1


def test_channel_detach_keeps_work_and_reconnected_handle_alive(runtime):
    host, provider, handle, _ = runtime
    started, release = threading.Event(), threading.Event()

    def execute(uid, args, running):
        started.set()
        assert release.wait(5)
        assert not running.cancel.is_set()
        return {"exit_code": 0, "killed": None}

    host.backend.execute = execute
    exec_id = provider.start_exec(handle, "survive", ["work"])
    assert started.wait(5)
    grant = OwnerGrant("alice", 300001, "session")
    new_handle = host.bind(grant, "a")
    old_token = provider._bindings[id(handle)][2]
    try:
        host.detach(grant, old_token)
        assert not host.executions.running[exec_id].cancel.is_set()
        status = host.request(grant, new_handle["token"], "exec_status", {"op_id": "survive"})
        assert status["result"]["state"] == "running"
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while host.executions.running and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert host.state.find_exec("alice", exec_id)["state"] == "done"
    assert host.state.pending("alice") == 0


def cli_config():
    return {"home_dir": "test-cli", "workdir": "/agent-home/jobs/" + "a" * 32,
            "auth_files": {"auth.json": b"old-private-material"}, "nonce": "b" * 32,
            "catalogue_rewrite": None, "requested_model": None, "custody": "d" * 64,
            "env_from_file": {}}


def cli_frames(*, nonce="b" * 32, code=0):
    import base64
    import struct

    from tinyassets.boxes.file_wire import encode

    output = b"public CLI response\n"
    private = encode({"external_agent_writeback": {
        "auth.json": base64.b64encode(b"new-private-material").decode()},
        "nonce": nonce, "exit": code})
    return (struct.pack("!BI", 1, len(output)) + output
            + struct.pack("!BI", 2, len(private)) + private)


def test_private_cli_writeback_stays_out_of_output_and_journal(runtime):
    host, provider, handle, state = runtime
    state["code"] = "sys.stdout.buffer.write(" + repr(cli_frames()) + ")"
    ident = provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config())
    output, end = collect(provider, handle, ident)
    assert output == b"public CLI response\n" and end.exit_code == 0
    assert provider.cli_result(handle, ident) == {
        "files": {"auth.json": b"new-private-material"}, "exit_code": 0}
    assert provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config()) == ident
    assert len(state["launches"]) == 1
    record = repr(host.state.lookup("alice", "refresh"))
    assert "private-material" not in record
    assert "cHJpdmF0ZS1tYXRlcmlhbA" not in record
    assert "auth.json" not in record
    provider.ack_cli(handle, ident)
    with pytest.raises(BoxError, match="unavailable"):
        provider.cli_result(handle, ident)
    assert provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config()) == ident
    assert len(state["launches"]) == 1


def test_private_cli_ack_requires_received_writeback(runtime):
    host, provider, handle, state = runtime
    state["code"] = "sys.stdout.buffer.write(" + repr(cli_frames()) + ")"
    ident = provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config())
    collect(provider, handle, ident)
    host.executions.private[ident] = None

    with pytest.raises(BoxError, match="writeback was not received"):
        provider.ack_cli(handle, ident)
    assert host.state.lookup("alice", "refresh")["outcome"]["cli_ack"] is False
    assert ident in host.executions.private
    with pytest.raises(BoxError, match="unsettled"):
        provider.start_cli(handle, "fresh-op", ["unused"], env={}, cli=cli_config())


@pytest.mark.parametrize("failure", ["truncated", "foreign_nonce", "stop_failed"])
def test_private_cli_unknown_writeback_never_recovers_or_replays(runtime, failure):
    host, provider, handle, state = runtime
    wire = cli_frames(nonce="c" * 32) if failure == "foreign_nonce" else cli_frames()
    if failure == "truncated":
        wire = wire[:-1]
    state["code"] = "sys.stdout.buffer.write(" + repr(wire) + ")"
    state["stop_fail"] = failure == "stop_failed"
    ident = provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config())
    with pytest.raises(BoxError, match="unknown"):
        collect(provider, handle, ident)
    with pytest.raises(BoxError, match="unavailable"):
        provider.cli_result(handle, ident)
    with pytest.raises(BoxError, match="unknown"):
        provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config())
    with pytest.raises(BoxError, match="unquiesced" if failure == "stop_failed" else "unsettled"):
        provider.start_cli(handle, "new-id-same-custody", ["unused"], env={}, cli=cli_config())
    assert ident not in host.executions.private
    assert len(state["launches"]) == 1


def test_private_cli_requires_same_session_and_acknowledges_before_capacity_reuse(runtime):
    host, provider, handle, state = runtime
    state["code"] = "sys.stdout.buffer.write(" + repr(cli_frames()) + ")"
    ident = provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config())
    collect(provider, handle, ident)
    foreign = OwnerGrant("alice", 300001, "other-session")
    host.authorize = lambda g, c: c == "a"
    token = host.bind(foreign, "a")["token"]
    with pytest.raises(BoxAuthError, match="session"):
        host.request(foreign, token, "cli_result", {"exec_id": ident})
    # A restarted host has durable execution identity but no private material.
    host.executions.private.clear()
    with pytest.raises(BoxError, match="unavailable"):
        provider.cli_result(handle, ident)
    assert provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config()) == ident
    assert len(state["launches"]) == 1


def test_close_stdin_delivers_eof_after_pending_input(runtime):
    _, provider, handle, state = runtime
    state["code"] = "sys.stdout.write(sys.stdin.read())"
    ident = provider.start_exec(handle, "eof", ["unused"], interactive_stdin=True)
    provider.send_stdin(handle, ident, "input", b"until EOF")
    provider.close_stdin(handle, ident)
    output, end = collect(provider, handle, ident)
    assert output == b"until EOF" and end.exit_code == 0












def test_terminal_session_discards_private_bytes_without_forgiving_custody(runtime):
    host, provider, handle, state = runtime
    state["code"] = "sys.stdout.buffer.write(" + repr(cli_frames()) + ")"
    ident = provider.start_cli(handle, "refresh", ["unused"], env={}, cli=cli_config())
    collect(provider, handle, ident)
    provider.end_session(handle)
    assert ident not in host.executions.private
    assert not host.state.lookup("alice", "refresh")["outcome"]["cli_ack"]
    next_handle = provider.bind("a", account_id="alice", turn_id="session")
    with pytest.raises(BoxError, match="unsettled"):
        provider.start_cli(next_handle, "next", ["unused"], env={}, cli=cli_config())
