"""Real async streams through the owner public proxy; no production network."""

import asyncio
import sqlite3

import pytest

from tinyassets import rpc_frames as rf
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.public_egress import PublicEgress


@pytest.fixture
def proxy(tmp_path):
    from tinyassets.erasure_ledger import ErasureLedger

    ErasureLedger(tmp_path).initialize()
    tmp_path.chmod(0o700)
    path = tmp_path / "identities.db"
    identities = OwnerIdentities(path, initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    identities.plans.apply("alice", tier="paid", event_created=1)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1048576))
    return PublicEgress(identities, tmp_path)


async def exchange(proxy, request, *, center=None, principal="alice"):
    async def handler(reader, writer):
        try:
            document = {"op": "BOX_PUBLIC", "principal": principal}
            if center is not None:
                document["center"] = center
            await proxy.serve(reader, writer, document)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, limit=16384)
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1])
        try:
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


@pytest.mark.asyncio
async def test_proxy_pins_destination_and_preserves_body_after_half_close(proxy, monkeypatch):
    received = []

    async def upstream(reader, writer):
        received.append(await reader.readuntil(b"\r\n\r\n"))
        received.append(await reader.readexactly(3))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    def resolve(host, actual_port):
        assert (host, actual_port) == ("example.test", port)
        return ["127.0.0.1"]  # substituted DNS policy only, real sockets below

    monkeypatch.setattr("tinyassets.broker.public_egress._checked_addresses", resolve)
    try:
        request = (f"POST http://example.test:{port}/path HTTP/1.1\r\n"
                   "Content-Length: 3\r\n\r\nabc").encode()
        result = await exchange(proxy, request)
        assert result.endswith(b"\r\n\r\nok")
        assert received == [b"POST /path HTTP/1.1\r\nContent-Length: 3\r\n\r\n", b"abc"]
        assert not proxy.active
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["127.0.0.1:80", "169.254.169.254:80", "example.test:25"])
async def test_public_proxy_refuses_private_metadata_and_mail(proxy, target):
    result = await exchange(proxy, f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
    assert result.startswith(b"HTTP/1.1 403")
    assert not proxy.active


@pytest.mark.asyncio
async def test_public_proxy_refuses_foreign_center_capacity_and_oversized_headers(proxy):
    assert await exchange(proxy, b"", center="foreign") == b"REFUSED"
    proxy.active["alice"] = 32
    assert await exchange(proxy, b"") == b"REFUSED"
    proxy.active.clear()
    assert (await exchange(proxy, b"x" * 17000 + b"\r\n\r\n")).startswith(b"HTTP/1.1 403")
    assert not proxy.active


@pytest.mark.asyncio
@pytest.mark.parametrize("allocated", [False, True])
async def test_public_proxy_refuses_unknown_or_unadmitted_owner(proxy, monkeypatch, allocated):
    def no_upstream(*args):
        pytest.fail("unadmitted owner reached destination resolution")

    monkeypatch.setattr("tinyassets.broker.public_egress._checked_addresses", no_upstream)
    if allocated:
        proxy.identities.resolve("bob", allocate=True)
        proxy.identities.plans.apply("bob", tier="paid", event_created=1)
    assert await exchange(proxy, b"CONNECT example.test:443 HTTP/1.1\r\n\r\n",
                          principal="bob") == b"REFUSED"
    assert not proxy.active


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["fenced", "corrupt", "missing", "close"])
async def test_established_idle_tunnel_closes_within_erasure_bound(proxy, monkeypatch, damage):
    from tinyassets.broker.public_egress import ERASURE_CHECK_SECONDS
    from tinyassets.erasure_ledger import ErasureLedger

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
    monkeypatch.setattr("tinyassets.broker.public_egress._checked_addresses",
                        lambda *a: ["127.0.0.1"])

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            await proxy.serve(reader, writer, {"op": "BOX_PUBLIC", "principal": "alice"})
        finally:
            writer.close()
            handlers.discard(task)

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", listener.sockets[0].getsockname()[1])
    try:
        assert (await rf.read_frame(reader)).control()["op"] == "BOX_ADMITTED"
        writer.write(f"CONNECT example.test:{port} HTTP/1.1\r\n\r\n".encode())
        await writer.drain()
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        ledger = ErasureLedger(proxy.data_root)
        if damage == "fenced":
            ledger.add("alice")
        elif damage == "corrupt":
            ledger.path.write_bytes(b"corrupt ledger")
        elif damage == "missing":
            ledger.path.unlink()
        else:
            proxy.close_owner("alice")
        async with asyncio.timeout(2 * ERASURE_CHECK_SECONDS + .25):
            assert await reader.read() == b""
            await upstream_closed.wait()
            await asyncio.gather(*handlers, return_exceptions=True)
        assert not proxy.active
    finally:
        writer.close()
        listener.close()
        destination.close()
        await listener.wait_closed()
        await destination.wait_closed()
        await asyncio.gather(*handlers, return_exceptions=True)
