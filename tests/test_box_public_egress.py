"""Real async streams through the owner public proxy; no production network."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from tests.support.admission import drive, projection
from tinyassets import rpc_frames as rf
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.server import BOX_HOST, BrokerServer


def broker_proxy(identities, root):
    return BrokerServer(ledger_for=None, dispatch_for=None,
                        ops=SimpleNamespace(recover=lambda: None),
                        fence=None, roles={0: BOX_HOST}, uid_of=lambda _: 0,
                        owner_identities=identities, data_root=root)


@pytest.fixture
def proxy(tmp_path):

    tmp_path.chmod(0o700)
    from tests.support.admission import Plans

    path = tmp_path / ".broker/state/owner-identities.db"
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.parent.chmod(0o700)
    identities = OwnerIdentities(path, initialize=True, plans=Plans(tmp_path))
    uid = identities.resolve("alice", allocate=True).uid
    from tinyassets.broker.admission import local_mutate

    drive(local_mutate(identities.path, "alice", "admit", routing=projection(identities.path)))
    identities.plans.apply("alice", tier="paid", event_created=1)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1048576))
    return broker_proxy(identities, tmp_path)


async def exchange(proxy, request, *, center=None, principal="alice"):
    async def handler(reader, writer):
        try:
            await proxy._connection(reader, writer)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, limit=16384)
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1])
        try:
            document = {"op": "BOX_PUBLIC", "principal": principal}
            if center is not None:
                document["center"] = center
            writer.write(rf.control(rf.CONNECTION, document))
            await writer.drain()
            frame = await rf.read_frame(reader)
            if frame.control()["op"] == "BOX_REFUSED":
                return b"REFUSED"
            assert frame.control() == {"op": "BOX_ADMITTED"}
            writer.write(request)
            await writer.drain()
            writer.write_eof()
            return await asyncio.wait_for(reader.read(), 5)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["CONNECT", "GET"])
@pytest.mark.parametrize("target", ["127.0.0.1:80", "169.254.169.254:80", "example.test:25",
                                    "engine.tinyassets.box:80"])
async def test_public_proxy_refuses_private_and_unresolvable_destinations(proxy, target, method):
    url = target if method == "CONNECT" else f"http://{target}/"
    result = await exchange(proxy, f"{method} {url} HTTP/1.1\r\n\r\n".encode())
    assert result.startswith(b"HTTP/1.1 403")
    assert not proxy._public_egress.active


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.asyncio
@pytest.mark.parametrize("allocated", [False, True])
async def test_public_proxy_refuses_unknown_or_unadmitted_owner(proxy, monkeypatch, allocated):
    def no_upstream(*args):
        pytest.fail("unadmitted owner reached destination resolution")

    monkeypatch.setattr("tinyassets.broker.public_egress.destination", no_upstream)
    if allocated:
        proxy._owner_identities.resolve("bob", allocate=True)
        proxy._owner_identities.plans.apply("bob", tier="paid", event_created=1)
    assert await exchange(proxy, b"CONNECT example.test:443 HTTP/1.1\r\n\r\n",
                          principal="bob") == b"REFUSED"
    assert not proxy._public_egress.active


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["erased", "close"])
async def test_record_withdrawal_closes_established_idle_tunnel(proxy, monkeypatch, damage):
    from tinyassets.broker.admission import read

    upstream_closed = asyncio.Event()
    handlers = set()

    async def upstream(reader, writer):
        try:
            assert await reader.read() == b""
            upstream_closed.set()
        finally:
            writer.close()

    destination = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = destination.sockets[0].getsockname()[1]
    monkeypatch.setattr("tinyassets.broker.public_egress.destination",
                        lambda *a: ["127.0.0.1"])

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            await proxy._connection(reader, writer)
        finally:
            writer.close()
            handlers.discard(task)

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", listener.sockets[0].getsockname()[1])
    try:
        writer.write(rf.control(rf.CONNECTION, {"op": "BOX_PUBLIC", "principal": "alice"}))
        await writer.drain()
        assert (await rf.read_frame(reader)).control()["op"] == "BOX_ADMITTED"
        writer.write(f"CONNECT example.test:{port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        if damage == "erased":
            from tinyassets.broker.admission import local_mutate, owner_call
            path = proxy._owner_identities.path
            await owner_call(path, "alice", local_mutate, path, "alice", "erase",
                             routing=projection(path))
            proxy.close_owner("alice")
            assert read(proxy._owner_identities, "alice").state == "erased"
        else:
            proxy.close_owner("alice")
        async with asyncio.timeout(1.25):
            assert await reader.read() == b""
            await upstream_closed.wait()
            await asyncio.gather(*handlers, return_exceptions=True)
        assert not proxy._public_egress.active
    finally:
        writer.close()
        listener.close()
        destination.close()
        await listener.wait_closed()
        await destination.wait_closed()
        await asyncio.gather(*handlers, return_exceptions=True)
