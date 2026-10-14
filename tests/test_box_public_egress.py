"""Real async streams through the owner public proxy; no production network."""

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from tinyassets import rpc_frames as rf
from tinyassets.broker.public_egress import PublicEgress


@pytest.fixture
def proxy(tmp_path):
    path = tmp_path / "identities.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES ('alice',300001,300001,1048576)")
    identities = SimpleNamespace(path=path, owner_machine=lambda owner: 300001,
                                 admitted_owner=lambda center: "alice" if center == "a" else "bob")
    return PublicEgress(identities)


async def exchange(proxy, request, *, center="a", port=None):
    async def handler(reader, writer):
        try:
            await proxy.serve(reader, writer, {"op": "BOX_PUBLIC", "principal": "alice",
                                               "center": center})
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


def test_box_forwarder_child_does_not_hold_command_output_open():
    import os
    import signal
    import subprocess
    import sys

    from tinyassets.universe_egress import FORWARDER

    if os.name != "posix":
        pytest.skip("guest forwarder uses POSIX fork")
    proc = subprocess.Popen([
        sys.executable, "-c", FORWARDER, "0=/run/boxhostd/relay.sock", "--",
        sys.executable, "-c", "print('finished')"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True)
    try:
        output, error = proc.communicate(timeout=5)
        assert proc.returncode == 0 and output == b"finished\n" and error == b""
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
