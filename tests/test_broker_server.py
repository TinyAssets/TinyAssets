"""The broker process over a real Unix socket (S6, I14).

POSIX only: the server classifies peers by ``SO_PEERCRED``. The ledger, op
store and fence are real; the upstream is an injected dispatch returning a
scripted stream, so these tests pin the protocol: authorization per stream,
the fence, op_id records, credit, cancel and the request/close client.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time

import pytest

from tinyassets import rpc_frames as rf
from tinyassets.broker.client import BrokerClient, BrokerRefused
from tinyassets.broker.fence import Fence
from tinyassets.broker.ops import OpStore
from tinyassets.broker.server import OWNER, BrokerServer

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="the broker serves a Unix socket and reads peer credentials",
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_op_id(tail: str = "0" * 16) -> str:
    ms, head = int(time.time() * 1000), ""
    for _ in range(10):
        head = _CROCKFORD[ms % 32] + head
        ms //= 32
    return head + tail


class Script:
    """A scripted upstream stream with the BrokerStream surface."""

    def __init__(self, pieces, *, status=200, gate=None):
        self.pieces = list(pieces)
        self.status, self.reason, self.headers, self.redirect_count = status, "OK", {}, 0
        self.gate = gate
        self.closed = threading.Event()

    def read(self, max_bytes):
        if self.gate is not None and not self.gate.wait(0.2):
            return b""  # an idle tick: a real read returns within its idle bound
        if self.closed.is_set():
            raise RuntimeError("closed")
        if not self.pieces:
            return None
        piece = self.pieces.pop(0)
        return piece[:max_bytes] if piece is not None else b""

    def close(self):
        self.closed.set()
        if self.gate is not None:
            self.gate.set()


class Lease:
    def __init__(self):
        self.generation, self.proof = 1, "proof-1"

    def verify(self, generation, proof):
        return (generation, proof) == (self.generation, self.proof)


REVOKED: set[str] = set()


class Ledger:
    """authorize_exact as the real ledger would answer for one owner's grant."""

    def __init__(self, principal):
        self.principal = principal

    def authorize_exact(self, *, universe_id, grant_id, connection_id):
        from tinyassets.storage.outbound_connections import GrantResolutionError

        if grant_id in REVOKED or (self.principal, universe_id, grant_id, connection_id) != (
            "alice", "cc-alice", "grant-a", "conn-a",
        ):
            raise GrantResolutionError("outbound connection grant identity mismatch")
        return object(), object()


@pytest.fixture(autouse=True)
def _no_revocations():
    REVOKED.clear()
    yield
    REVOKED.clear()


@pytest.fixture
def broker(tmp_path):
    lease = Lease()
    fence = Fence(tmp_path / "fence.json", verify_lease_proof=lease.verify)
    ops = OpStore(tmp_path / "ops.db")
    sent = []
    upstreams = {"next": lambda: Script([b"data: hello\n\n", b"data: [DONE]\n\n"])}

    def dispatch_for(principal, command_center, grant_id, resource):
        if upstreams.get("dispatch_for_fails"):
            raise RuntimeError("thread exhaustion")

        def dispatch(grant, verb, request, *, stream, idle_s=None, guard=None,
                     on_connect=None, checkpoint=None, deadline_at=None):
            upstreams["idle_s"] = idle_s
            maker = upstreams["next"]
            if getattr(maker, "raw_dispatch", False):
                return maker(guard=guard, on_connect=on_connect, sent=sent)
            with guard():
                sent.append((principal, grant, verb, request))
                return maker(on_connect) if getattr(maker, "wants_socket", False) else maker()

        return dispatch

    server = BrokerServer(ledger_for=Ledger, dispatch_for=dispatch_for, ops=ops, fence=fence,
                          roles={os.getuid(): OWNER})
    path = tmp_path / "b.sock"
    loop = asyncio.new_event_loop()
    started = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve(path))
        started.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    assert started.wait(5)
    generation, token = fence.barrier(1, "proof-1")
    state = {"generation": generation, "token": token}
    client = BrokerClient(path, principal="alice", command_center="cc-alice",
                          fence=lambda: (state["generation"], state["token"]), timeout=10)
    yield type("B", (), dict(server=server, path=path, client=client, sent=sent, lease=lease,
                             fence=fence, ops=ops, state=state, upstreams=upstreams,
                             loop=loop))
    loop.call_soon_threadsafe(loop.stop)


def _call(broker, op_id=None, **overrides):
    kwargs = dict(grant_id="grant-a", connection_id="conn-a", verb="POST",
                  request={"url": "https://models.example.com/v1/chat", "body": {}},
                  op_id=op_id or new_op_id())
    kwargs.update(overrides)
    return broker.client.request(**kwargs)


def test_a_request_streams_through_the_broker_and_collects_like_request_close(broker):
    result = _call(broker)
    assert result["status"] == 200 and result["body"] == "data: hello\n\ndata: [DONE]\n\n"
    assert len(broker.sent) == 1


def test_another_principals_grant_is_refused_before_anything_is_sent(broker):
    from tinyassets.storage.outbound_connections import GrantResolutionError

    with pytest.raises(GrantResolutionError):
        _call(broker, grant_id="grant-b")
    assert broker.sent == []


def test_a_reused_op_id_is_never_sent_twice(broker):
    op = new_op_id()
    _call(broker, op_id=op)
    with pytest.raises(BrokerRefused) as refused:
        _call(broker, op_id=op)
    assert refused.value.side_effect_state == "unknown"
    assert len(broker.sent) == 1


def test_a_stream_below_the_fence_is_refused_with_no_network(broker):
    stale = dict(broker.state)
    broker.lease.generation, broker.lease.proof = 2, "proof-2"
    generation, token = broker.fence.barrier(2, "proof-2")
    broker.state.update(stale)  # the old owner keeps its old pair
    with pytest.raises(BrokerRefused) as refused:
        _call(broker)
    assert refused.value.side_effect_state == "none"
    assert broker.sent == []
    broker.state.update(generation=generation, token=token)
    assert _call(broker)["status"] == 200


def test_the_barrier_cancels_a_stream_of_the_old_generation(broker):
    gate = threading.Event()
    script = Script([b"never"], gate=gate)
    broker.upstreams["next"] = lambda: script
    errors = []

    def call():
        try:
            _call(broker)
        except Exception as exc:  # noqa: BLE001 - inspected below
            errors.append(exc)

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.monotonic() + 5
    while not broker.sent and time.monotonic() < deadline:
        time.sleep(0.05)
    broker.lease.generation, broker.lease.proof = 2, "proof-2"
    broker.fence.barrier(2, "proof-2", cancel_older=broker.server._cancel_older,
                         close_older=broker.server._close_older)
    thread.join(5)
    assert script.closed.is_set() and errors


def test_status_reports_an_operation_that_may_have_sent(broker):
    op = new_op_id()
    _call(broker, op_id=op)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(broker.path))
        sock.sendall(rf.control(rf.CONNECTION, {"op": "STATUS", "op_id": op,
                                                "principal": "alice",
                                                "command_center": "cc-alice"}))
        answer = rf.read_frame_blocking(sock).control()
    assert answer["state"] == "completed" and answer["side_effect_state"] == "unknown"


def test_a_cancel_closes_the_upstream_and_ends_the_stream(broker):
    gate = threading.Event()
    script = Script([b"x"], gate=gate)
    broker.upstreams["next"] = lambda: script
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(broker.path))
        sock.sendall(rf.control(1, {
            "op": "OPEN", "op_id": new_op_id(), "principal": "alice",
            "command_center": "cc-alice", "grant_id": "grant-a", "connection_id": "conn-a",
            "verb": "POST", "request": {"url": "u", "body": {}}, "credit": 10,
            **broker.state,
        }))
        frames = [rf.read_frame_blocking(sock).control()["op"]]  # ADMITTED
        frames.append(rf.read_frame_blocking(sock).control()["op"])  # HEAD
        sock.sendall(rf.control(1, {"op": "CANCEL"}))
        end = rf.read_frame_blocking(sock).control()
    assert frames == ["ADMITTED", "HEAD"]
    assert end["op"] == "END" and end["outcome"] == "cancelled"
    assert end["side_effect_state"] == "unknown" and script.closed.is_set()


def test_data_never_exceeds_the_credit_granted(broker):
    broker.upstreams["next"] = lambda: Script([b"a" * 100, b"b" * 100])
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(broker.path))
        sock.sendall(rf.control(1, {
            "op": "OPEN", "op_id": new_op_id(), "principal": "alice",
            "command_center": "cc-alice", "grant_id": "grant-a", "connection_id": "conn-a",
            "verb": "POST", "request": {"url": "u", "body": {}}, "credit": 30,
            **broker.state,
        }))
        received = 0
        while True:
            frame = rf.read_frame_blocking(sock)
            if frame.kind == rf.DATA:
                received += len(frame.payload)
                continue
            if frame.control()["op"] == "HEAD":
                break
        sock.settimeout(0.5)
        try:
            while True:
                frame = rf.read_frame_blocking(sock)
                assert frame.kind == rf.DATA
                received += len(frame.payload)
        except TimeoutError:
            pass
        assert received == 30
        sock.settimeout(5)
        sock.sendall(rf.control(1, {"op": "CREDIT", "n": 1000}))
        while True:
            frame = rf.read_frame_blocking(sock)
            if frame.kind == rf.DATA:
                received += len(frame.payload)
            elif frame.control()["op"] == "END":
                break
    assert received == 200


def test_an_unmapped_uid_is_dropped_before_any_frame(broker):
    broker.server._roles = {}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(broker.path))
        sock.sendall(rf.control(rf.CONNECTION, {"op": "STATUS", "op_id": new_op_id()}))
        try:
            answer = rf.read_frame_blocking(sock)
        except ConnectionResetError:  # closed with our frame unread: also a drop
            answer = None
        assert answer is None


def _open_raw(broker, sock, *, credit=0, op_id=None, stream_id=1):
    sock.sendall(rf.control(stream_id, {
        "op": "OPEN", "op_id": op_id or new_op_id(), "principal": "alice",
        "command_center": "cc-alice", "grant_id": "grant-a", "connection_id": "conn-a",
        "verb": "POST", "request": {"url": "u", "body": {}}, "credit": credit,
        **broker.state,
    }))


def _connect(broker):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(str(broker.path))
    return sock


def test_a_refusal_after_the_operation_sent_reports_unknown_not_none(broker):
    op = new_op_id()
    _call(broker, op_id=op)
    REVOKED.add("grant-a")
    with _connect(broker) as sock:
        _open_raw(broker, sock, op_id=op)
        end = rf.read_frame_blocking(sock).control()
    assert end["outcome"] == "refused" and end["side_effect_state"] == "unknown"
    assert end["stream_sent"] is False


def test_a_finished_response_reaches_end_with_exactly_its_own_credit(broker):
    broker.upstreams["next"] = lambda: Script([b"abc"])
    with _connect(broker) as sock:
        _open_raw(broker, sock, credit=0)
        ops = []
        while len(ops) < 2:
            ops.append(rf.read_frame_blocking(sock).control()["op"])
        assert ops == ["ADMITTED", "HEAD"]
        time.sleep(0.3)  # let the response reach its end with nothing granted
        sock.sendall(rf.control(1, {"op": "CREDIT", "n": 3}))
        data = rf.read_frame_blocking(sock)
        end = rf.read_frame_blocking(sock).control()
    assert data.payload == b"abc" and end["outcome"] == "completed"


def test_a_stream_starved_of_credit_ends_at_its_deadline(broker, monkeypatch):
    from tinyassets.broker import server as server_module

    monkeypatch.setattr(server_module, "ORDINARY_BUDGET_S", 0.3)
    monkeypatch.setattr(server_module, "RESEND_GRACE_S", 0.2)
    broker.upstreams["next"] = lambda: Script([b"x" * 100])
    with _connect(broker) as sock:
        _open_raw(broker, sock, credit=0)
        while True:
            doc = rf.read_frame_blocking(sock).control()
            if doc["op"] == "END":
                break
    assert doc["outcome"] == "failed" and doc["error_class"] == "OutboundDeadlineExceeded"


class _FakeSocket:
    def __init__(self):
        self.down = threading.Event()

    def fileno(self):
        return -1

    def shutdown(self, _how):
        self.down.set()

    def close(self):
        self.down.set()


def test_a_stream_that_cannot_start_is_refused_and_never_sent(broker):
    broker.upstreams["dispatch_for_fails"] = True
    op = new_op_id()
    with _connect(broker) as sock:
        _open_raw(broker, sock, op_id=op)
        end = rf.read_frame_blocking(sock).control()
    assert end["outcome"] == "refused" and end["side_effect_state"] == "none"
    assert broker.ops.status("alice|cc-alice", op).state == "refused"
    assert broker.server._streams == {}


def test_a_cancel_during_name_resolution_never_reaches_the_write(broker, monkeypatch):
    fake = _FakeSocket()
    resolving = threading.Event()
    cancelled = threading.Event()
    cancel = broker.server.cancel

    def observe_cancel(stream):
        cancel(stream)
        cancelled.set()

    monkeypatch.setattr(broker.server, "cancel", observe_cancel)

    def dispatch(*, guard, on_connect, sent):
        with guard():
            resolving.set()
            assert cancelled.wait(5), "broker never processed cancellation during DNS"
            on_connect(fake)         # must refuse: the stream was cancelled meanwhile
            sent.append("WROTE")     # the request write
        return Script([b"x"])

    dispatch.raw_dispatch = True
    broker.upstreams["next"] = dispatch
    with _connect(broker) as sock:
        _open_raw(broker, sock)
        assert rf.read_frame_blocking(sock).control()["op"] == "ADMITTED"
        assert resolving.wait(5)
        sock.sendall(rf.control(1, {"op": "CANCEL"}))
        end = rf.read_frame_blocking(sock).control()
    assert "WROTE" not in broker.sent
    assert end["outcome"] == "cancelled" and end["stream_sent"] is False


def test_a_failure_before_any_write_reports_stream_sent_false(broker):
    def dispatch(*, guard, on_connect, sent):
        from tinyassets.storage.outbound_connections import SsrfValidationError

        raise SsrfValidationError("resolved to a private address")

    dispatch.raw_dispatch = True
    broker.upstreams["next"] = dispatch
    with _connect(broker) as sock:
        _open_raw(broker, sock)
        rf.read_frame_blocking(sock)  # ADMITTED
        end = rf.read_frame_blocking(sock).control()
    assert end["outcome"] == "failed" and end["stream_sent"] is False
    # The operation recorded may_have_sent first, so its state stays unknown.
    assert end["side_effect_state"] == "unknown"


def test_an_enormous_budget_is_clamped_not_a_crash(broker):
    result = broker.client.request(
        grant_id="grant-a", connection_id="conn-a", verb="POST",
        request={"url": "u", "body": {}, "reply_budget_s": 10**400}, op_id=new_op_id())
    assert result["status"] == 200


# ── the async client ────────────────────────────────────────────────────────


def _async_client(broker):
    from tinyassets.broker.aclient import AsyncBrokerClient

    return AsyncBrokerClient(broker.path, principal="alice", command_center="cc-alice",
                             fence=lambda: (broker.state["generation"], broker.state["token"]))


def test_the_async_client_streams_many_turns_over_one_connection(broker):
    broker.upstreams["next"] = lambda: Script([b"data: a\n\n", b"data: b\n\n"])

    async def turn(client, n):
        async with client.stream(grant_id="grant-a", connection_id="conn-a", verb="POST",
                                 request={"url": "u", "body": {"n": n}},
                                 op_id=new_op_id(f"{n:016d}")) as stream:
            head = await stream.head()
            body = b"".join([chunk async for chunk in stream.body()])
            return head["status"], body

    async def scenario():
        client = _async_client(broker)
        try:
            return await asyncio.gather(*(turn(client, n) for n in range(20)))
        finally:
            await client.close()

    results = asyncio.run(scenario())
    assert results == [(200, b"data: a\n\ndata: b\n\n")] * 20
    assert len(broker.sent) == 20


def test_leaving_a_stream_early_cancels_it_in_the_broker(broker):
    gate = threading.Event()
    script = Script([b"x"], gate=gate)
    broker.upstreams["next"] = lambda: script

    async def scenario():
        client = _async_client(broker)
        try:
            async with client.stream(grant_id="grant-a", connection_id="conn-a", verb="POST",
                                     request={"url": "u", "body": {}},
                                     op_id=new_op_id()) as stream:
                await stream.head()
            assert await asyncio.to_thread(script.closed.wait, 5)
        finally:
            await client.close()

    asyncio.run(scenario())
    assert script.closed.is_set()


def test_the_async_client_raises_the_typed_refusal(broker):
    from tinyassets.storage.outbound_connections import GrantResolutionError

    async def scenario():
        client = _async_client(broker)
        try:
            async with client.stream(grant_id="grant-b", connection_id="conn-a", verb="POST",
                                     request={"url": "u", "body": {}},
                                     op_id=new_op_id()) as stream:
                await stream.head()
        finally:
            await client.close()

    with pytest.raises(GrantResolutionError):
        asyncio.run(scenario())


def test_idle_bound_cannot_be_widened_by_the_caller(broker):
    async def scenario():
        client = _async_client(broker)
        try:
            async with client.stream(grant_id="grant-a", connection_id="conn-a", verb="POST",
                                     request={}, op_id=new_op_id(), idle_s=600) as stream:
                await stream.head()
                _ = [chunk async for chunk in stream.body()]
            assert broker.upstreams["idle_s"] == 30
        finally:
            await client.close()

    asyncio.run(scenario())


def test_async_client_reconnects_after_broker_eof(tmp_path):
    from tinyassets.broker.aclient import AsyncBrokerClient
    from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome, ProxyRequestError

    async def scenario():
        connections = []

        async def serve(reader, writer):
            connections.append(writer)
            frame = await rf.read_frame(reader)
            if len(connections) > 1:
                writer.write(rf.control(frame.stream, {"op": "HEAD", "status": 200}))
                writer.write(rf.control(frame.stream, {"op": "END", "outcome": "completed"}))
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        path = tmp_path / "reconnect.sock"
        listener = await asyncio.start_unix_server(serve, path=str(path))
        client = AsyncBrokerClient(path, principal="alice", command_center="cc-alice",
                                   fence=lambda: (1, "token"))

        async def turn():
            async with client.stream(grant_id="grant-a", connection_id="conn-a", verb="POST",
                                     request={}, op_id=new_op_id()) as stream:
                await stream.head()
                return [chunk async for chunk in stream.body()]

        try:
            with pytest.raises(AmbiguousProxyOutcome):
                await asyncio.wait_for(turn(), 3)
            assert client._writer is client._reader is client._demux is None
            assert await asyncio.wait_for(turn(), 3) == []
            assert len(connections) == 2
            demux = client._demux
            if demux is not None:
                await asyncio.wait_for(demux, 3)
            with pytest.raises(AmbiguousProxyOutcome):
                await client._send(b"dead connection")
            listener.close()
            await listener.wait_closed()
            path.unlink(missing_ok=True)
            with pytest.raises(ProxyRequestError):
                await asyncio.wait_for(turn(), 3)
        finally:
            await client.close()
            listener.close()
            await listener.wait_closed()

    asyncio.run(scenario())
