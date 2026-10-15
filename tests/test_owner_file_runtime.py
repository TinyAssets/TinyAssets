"""Host authority and crash invariants; sandbox doubles are NOT a G2/G5 proof."""

from __future__ import annotations

import array
import os
import socket
import struct
import subprocess
import threading
import uuid
from dataclasses import replace

import pytest

from tinyassets.boxes.boxd import OwnerBoxd
from tinyassets.boxes.file_channel import FileChannel, receive, send, serve_channel
from tinyassets.boxes.file_wire import MAX_DATA, decode, encode
from tinyassets.boxes.file_worker import operate
from tinyassets.boxes.handles import OwnerGrant
from tinyassets.boxes.owner_files import OwnerFileHost
from tinyassets.boxes.production_files import ProductionFileProvider
from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxBusy,
    BoxError,
    OpIdReuse,
    RecordConflict,
    StaleHandle,
    WriteConflict,
    WriteMode,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux owner file protocol")

A = OwnerGrant("alice", 300001, "session-a")
B = OwnerGrant("bob", 300002, "session-b")


class SandboxDouble:
    """Real descriptor operations in test roots, with explicit transport faults."""

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.fail_after_write = False
        self.fail_stop = False
        self.recoveries = 0

    def recover(self):
        self.recoveries += 1

    def call(self, uid, operation, args):
        self.calls.append((uid, operation, args))
        root = self.root / str(uid)
        root.mkdir(exist_ok=True)
        core = OwnerBoxd(owner_id="owner", owner_root=root,
                         state_dir=self.root / f"worker-{uid}-{uuid.uuid4().hex}")
        try:
            response = decode(encode({"result": operate(core, {
                "operation": operation, "args": args})}))
            if self.fail_after_write and operation == "write":
                raise OSError("response lost after fsync")
            return response
        finally:
            core.close()

    def stop(self, uid):
        if self.fail_stop:
            raise BoxError("cannot prove guest stopped")

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    sandbox = SandboxDouble(tmp_path)
    grants = {A, B}
    owners = {"a": "alice", "a2": "alice", "b": "bob"}
    def authorize(grant, center):
        return grant in grants and owners.get(center) == grant.owner_id
    host = OwnerFileHost(state_dir=tmp_path / "host", backend=sandbox, authorize=authorize)
    yield host, sandbox, grants, authorize
    host.close()


def write_args(body=b"hello", *, mode="replace", generation=None, etag=0, path="/cc/wiki/file.md"):
    return {"path": path, "data": body, "max_bytes": 100,
            "mode": mode, "expect_generation": generation, "expect_etag": etag}


def test_owner_session_center_and_epoch_are_checked_before_io(setup):
    host, sandbox, grants, _ = setup
    token = host.bind(A, "a")["token"]
    with pytest.raises(BoxAuthError):
        host.request(B, token, "stat", {"path": "/cc/file"})
    with pytest.raises(BoxAuthError):
        host.request(replace(A, session_id="forged"), token, "stat", {"path": "/cc/file"})
    with pytest.raises(BoxAuthError):
        host.bind(A, "b")
    grants.remove(A)
    with pytest.raises(BoxAuthError):
        host.request(A, token, "stat", {"path": "/cc/file"})
    grants.add(A)
    host.handles.destroy("alice")
    with pytest.raises(StaleHandle):
        host.request(A, token, "stat", {"path": "/cc/file"})
    assert sandbox.calls == []


def test_owner_wide_generations_cas_and_idempotency(setup):
    host, sandbox, _, _ = setup
    one = host.bind(A, "a")["token"]
    two = host.bind(A, "a2")["token"]
    before = host.request(A, two, "committed_generation", {})["result"]["generation"]
    answer = host.request(A, one, "write", write_args(), op_id="w")
    assert answer["result"]["generation"] > before
    assert answer["result"]["path"] == "/cc/wiki/file.md"
    assert host.request(A, one, "write", write_args(), op_id="w") == answer
    assert len(sandbox.calls) == 1
    with pytest.raises(OpIdReuse):
        host.request(A, two, "write", write_args(), op_id="w")
    with pytest.raises(WriteConflict):
        host.request(A, two, "write", write_args(mode="cas", generation=before), op_id="cas")
    assert len(sandbox.calls) == 1
    generation = answer["result"]["generation"]
    host.request(A, two, "write", write_args(mode="cas", generation=generation), op_id="cas")
    assert sandbox.calls[-1][2]["mode"] == "replace"
    assert sandbox.calls[-1][2]["path"] == "/cc/a2/wiki/file.md"
    assert "expect_etag" not in sandbox.calls[-1][2]
    assert "expect_generation" not in sandbox.calls[-1][2]


def test_record_etag_conflicts_only_that_path_and_is_retryable(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    sibling = host.bind(A, "a2")["token"]
    assert host.request(A, token, "record_etag", {"path": "/cc/wiki/a.md"})["result"] == {
        "path": "/cc/wiki/a.md", "etag": 0}
    first = host.request(A, token, "write", write_args(path="/cc/wiki/a.md", etag=0), op_id="a")
    assert first["result"]["etag"] == 1
    assert len(sandbox.calls) == 1
    with pytest.raises(RecordConflict, match="record changed") as conflict:
        host.request(A, token, "write", write_args(path="/cc/wiki/a.md", body=b"stale", etag=0),
                     op_id="stale")
    assert conflict.value.retryable is True
    assert len(sandbox.calls) == 1
    other = host.request(
        A, token, "write", write_args(path="/cc/wiki/b.md", body=b"other", etag=0), op_id="b")
    assert other["result"]["etag"] == 1
    assert len(sandbox.calls) == 2
    # A sibling center is a different record even at the same relative path.
    host.request(A, sibling, "write", write_args(path="/cc/wiki/a.md", etag=0), op_id="a2")
    assert sandbox.calls[-1][2]["path"] == "/cc/a2/wiki/a.md"
    fresh = host.request(A, token, "record_etag", {"path": "/cc/wiki/a.md"})["result"]["etag"]
    again = host.request(
        A, token, "write", write_args(path="/cc/wiki/a.md", body=b"new", etag=fresh), op_id="a3")
    assert again["result"]["etag"] == fresh + 1
    assert sandbox.calls[-1][2]["mode"] == "replace"


def test_concurrent_writers_of_one_record_publish_once(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    barrier = threading.Barrier(2)
    published, conflicts = [], []

    def write(name):
        barrier.wait(2)
        try:
            published.append(host.request(
                A, token, "write", write_args(body=name.encode(), etag=0), op_id=name))
        except RecordConflict as exc:
            conflicts.append(exc)

    threads = [threading.Thread(target=write, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert len(published) == 1 and len(conflicts) == 1
    assert conflicts[0].retryable is True
    assert len(sandbox.calls) == 1
    written = (sandbox.root / "300001/a/wiki/file.md").read_bytes()
    assert written == sandbox.calls[0][2]["data"]
    assert written in {b"one", b"two"}


def test_lost_write_invalidates_the_record_etag(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    sandbox.fail_after_write = True
    with pytest.raises(OSError):
        host.request(A, token, "write", write_args(etag=0), op_id="lost")
    sandbox.fail_after_write = False
    with pytest.raises(RecordConflict, match="record changed"):
        host.request(A, token, "write", write_args(etag=0), op_id="retry")
    assert len(sandbox.calls) == 1
    fresh = host.request(A, token, "record_etag", {"path": "/cc/wiki/file.md"})["result"]["etag"]
    host.request(A, token, "write", write_args(body=b"after", etag=fresh), op_id="after")
    assert len(sandbox.calls) == 2


def test_arbitrary_execution_fences_every_record_etag(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    written = host.request(A, token, "write", write_args(etag=0), op_id="w")
    with host.lock(A.owner_id):
        host.begin_work(A, "a")
    host.end_work(A)
    with pytest.raises(RecordConflict, match="record changed"):
        host.request(A, token, "write", write_args(etag=written["result"]["etag"]), op_id="stale")
    fresh = host.request(A, token, "record_etag", {"path": "/cc/wiki/file.md"})["result"]["etag"]
    unseen = host.request(A, token, "record_etag", {"path": "/cc/wiki/new.md"})["result"]["etag"]
    assert unseen > 0
    host.request(A, token, "write", write_args(etag=fresh), op_id="fresh")
    host.request(A, token, "remove", {"path": "/cc/wiki/file.md"}, op_id="rm")
    with pytest.raises(RecordConflict, match="record changed"):
        host.request(A, token, "write", write_args(etag=fresh), op_id="after-remove")


def test_lost_write_response_is_not_replayed_even_after_host_restart(setup, tmp_path):
    host, sandbox, _, authorize = setup
    token = host.bind(A, "a")["token"]
    sandbox.fail_after_write = True
    with pytest.raises(OSError):
        host.request(A, token, "write", write_args(), op_id="lost")
    assert (tmp_path / "300001/a/wiki/file.md").read_bytes() == b"hello"
    sandbox.fail_after_write = False
    with pytest.raises(BoxError, match="unknown"):
        host.request(A, token, "write", write_args(), op_id="lost")
    host.close()
    recovered = OwnerFileHost(state_dir=tmp_path / "host", backend=sandbox, authorize=authorize)
    try:
        fresh = recovered.bind(A, "a")["token"]
        with pytest.raises(BoxError, match="unknown"):
            recovered.request(A, fresh, "write", write_args(), op_id="lost")
        assert recovered.state.generation("alice") > 1
        assert len(sandbox.calls) == 1
        assert sandbox.recoveries == 2
    finally:
        recovered.close()


def test_successful_mutation_receipt_survives_restart(setup, tmp_path):
    host, sandbox, _, authorize = setup
    token = host.bind(A, "a")["token"]
    answer = host.request(A, token, "write", write_args(), op_id="saved")
    host.close()
    restarted = OwnerFileHost(state_dir=tmp_path / "host", backend=sandbox, authorize=authorize)
    try:
        token = restarted.bind(A, "a")["token"]
        assert restarted.request(A, token, "write", write_args(), op_id="saved") == answer
        assert len(sandbox.calls) == 1
    finally:
        restarted.close()


def test_running_or_unquiesced_writers_refuse_snapshots_and_cas(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    with host.lock(A.owner_id):
        host.begin_work(A, "a")
    with pytest.raises(BoxBusy):
        host.request(A, token, "read_many", {"paths": ["/cc/file"], "max_total": 10})
    sandbox.fail_stop = True
    with pytest.raises(BoxError):
        host.end_work(A)
    with pytest.raises(BoxBusy):
        host.request(A, token, "write", write_args(mode="cas", generation=1), op_id="w")
    sandbox.fail_stop = False
    host.end_work(A)
    assert host.request(A, token, "committed_generation", {})["result"]["generation"] == 2


def test_failed_mutation_stop_never_publishes_success_or_coherent_snapshot(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    sandbox.fail_stop = True
    with pytest.raises(BoxError):
        host.request(A, token, "write", write_args(), op_id="w")
    assert host.state.lookup(A.owner_id, "w")["state"] == "running"
    with pytest.raises(BoxBusy):
        host.request(A, token, "committed_generation", {})
    sandbox.fail_stop = False


def test_guest_cannot_claim_dispatched_mutation_had_no_effect(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    calls = []

    def dishonest(uid, operation, args):
        calls.append(operation)
        return {"error": "BoxOperationRefused"}

    sandbox.call = dishonest
    assert host.request(A, token, "write", write_args(), op_id="w") == {"error": "BoxError"}
    assert host.request(A, token, "write", write_args(), op_id="w") == {"error": "BoxError"}
    assert calls == ["write"]


def test_usage_generation_and_quota_come_from_host_authority(setup):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    host.bound_for = lambda grant, center: 1024**3
    sandbox.call = lambda *args: {"result": {
        "logical_bytes": 17, "bound_bytes": 1, "generation": -1}}
    usage = host.request(A, token, "usage", {})["result"]
    assert usage == {"logical_bytes": 17, "bound_bytes": 1024**3, "generation": 0}


def test_second_host_cannot_stop_first_hosts_sandboxes(setup, tmp_path):
    host, sandbox, _, authorize = setup
    with pytest.raises(BoxError, match="another box host"):
        OwnerFileHost(state_dir=tmp_path / "host", backend=sandbox, authorize=authorize)
    assert sandbox.recoveries == 1


def test_live_peer_credentials_are_required_and_passed_descriptors_rejected():
    peer = (os.getpid(), os.getuid(), os.getgid())
    for forged, with_fd in ((True, False), (False, True)):
        client, server = socket.socketpair()
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            server.settimeout(1)
            if with_fd:
                body = encode({"bind": "a"})
                client.sendmsg([struct.pack("!I", len(body)) + body], [
                    (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [client.fileno()]))])
            else:
                send(client, {"bind": "a"})
            expected = (peer[0] + 1, peer[1], peer[2]) if forged else peer
            before = len(os.listdir("/proc/self/fd"))
            with pytest.raises(BoxAuthError):
                receive(server, expected)
            assert len(os.listdir("/proc/self/fd")) == before
        finally:
            client.close()
            server.close()


def test_dead_pidfd_refuses_recycled_peer_identity():
    process = subprocess.Popen(["/bin/true"])
    pidfd = os.pidfd_open(process.pid)
    client, server = socket.socketpair()
    try:
        process.wait(timeout=2)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        peer = (os.getpid(), os.getuid(), os.getgid())
        send(client, {"bind": "a"})
        with pytest.raises(BoxAuthError, match="exited"):
            receive(server, peer, pidfd)
    finally:
        os.close(pidfd)
        client.close()
        server.close()


def test_sibling_owner_io_does_not_wait_for_slow_owner(setup):
    host, sandbox, _, _ = setup
    one = host.bind(A, "a")["token"]
    two = host.bind(B, "b")["token"]
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    original = sandbox.call

    def paused(uid, operation, args):
        if uid == A.uid:
            started.set()
            assert release.wait(2)
        return original(uid, operation, args)

    sandbox.call = paused
    first = threading.Thread(target=lambda: host.request(A, one, "stat", {"path": "/cc/x"}))
    second = threading.Thread(target=lambda: (
        host.request(B, two, "stat", {"path": "/cc/x"}), completed.set()))
    first.start()
    try:
        assert started.wait(1)
        second.start()
        assert completed.wait(1)
    finally:
        release.set()
        first.join(2)
        second.join(2)


def test_planted_link_is_not_followed_by_file_worker(setup, tmp_path):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    target = tmp_path / "secret"
    target.write_bytes(b"foreign")
    center = tmp_path / "300001/a"
    center.mkdir(parents=True)
    (center / "stolen").symlink_to(target)
    with pytest.raises(BoxError):
        host.request(A, token, "read", {"path": "/cc/stolen", "offset": 0, "max_bytes": 10})
    assert target.read_bytes() == b"foreign"


@pytest.mark.parametrize("operation,args", [
    ("write", {**write_args(), "uid": 300002}),
    ("read", {"path": "/cc/x", "offset": 0, "max_bytes": MAX_DATA + 1}),
    ("read_many", {"paths": ["/cc/../b"], "max_total": 10}),
    ("read_many", {"paths": ["/cc/x"] * 1001, "max_total": 10}),
    ("list", {"path": "/cc", "cursor": None, "limit": 0}),
    ("destroy", {}), ("start_exec", {}), ("close", {}),
])
def test_unsupported_payloads_never_reach_sandbox(setup, operation, args):
    host, sandbox, _, _ = setup
    token = host.bind(A, "a")["token"]
    with pytest.raises(BoxError):
        host.request(A, token, operation, args)
    assert sandbox.calls == []


def test_end_to_end_authenticated_socket_provider_and_snapshot(setup):
    host, sandbox, _, _ = setup
    threads = []
    peer = (os.getpid(), os.getuid(), os.getgid())

    def factory(account, center, session):
        assert (account, center, session) == ("alice", "a", "session-a")
        client, server = socket.socketpair()
        # Set before either side writes: SO_PASSCRED cannot authenticate bytes
        # queued before it was enabled on the receiving socket.
        for sock in (client, server):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        thread = threading.Thread(target=serve_channel,
                                  kwargs={"sock": server, "peer": peer, "grant": A,
                                          "host": host})
        thread.start()
        threads.append(thread)
        return FileChannel(client, host_peer=peer)

    provider = ProductionFileProvider(channel_factory=factory)
    try:
        handle = provider.bind("a", account_id="alice", turn_id="session-a")
        with pytest.raises(BoxAuthError):
            provider.stat(replace(handle), "/cc/wiki")
        first = provider.write(handle, "w", "/cc/wiki/f", b"test", max_bytes=10,
                               mode=WriteMode.CREATE, expect_etag=0)
        assert first.etag == 1
        assert provider.record_etag(handle, "/cc/wiki/f") == 1
        snapshot = provider.read_many(handle, ["/cc/wiki/f", "/cc/no"], max_total=10)
        assert snapshot.files == {"/cc/wiki/f": b"test"}
        assert snapshot.missing == ("/cc/no",)
        assert snapshot.generation == first.generation
        assert b"".join(provider.download(handle, "/cc/wiki/f", chunk_bytes=2)) == b"test"
        assert provider.list(handle, "/cc/wiki").entries[0].name == "f"
        assert provider.stat(handle, "/cc/wiki/f").path == "/cc/wiki/f"
        provider.remove(handle, "rm", "/cc/wiki/f")
        assert provider.stat(handle, "/cc/wiki/f") is None
    finally:
        provider.close()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
