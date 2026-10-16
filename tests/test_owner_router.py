"""Real broker + two owner processes. Shared handlers must never open owner stores."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.provider import Identity
from tinyassets.broker.owner_identities import OwnerIdentities, owner_box_route


@pytest.fixture
def admitted(in_process_broker, broker, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets.broker import supervisor

    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    broker.server._owner_identities = identities
    for name in ("alice", "bob", "reserved"):
        identities.resolve(name, allocate=True)
    for name in ("alice", "bob"):
        identities.admission("admit", name, "cc-" + name)
    live = SimpleNamespace(socket_path=broker.path,
                           fence=lambda: (broker.state["generation"], broker.state["token"]),
                           verify_broker=lambda channel: None)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: live)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return identities


def test_secret_mint_rotation_and_admission_through_real_broker(admitted, tmp_path):
    first = owner_box_route(tmp_path, principal="alice")
    assert first == owner_box_route(tmp_path, principal="alice")
    assert first == OwnerIdentities(admitted.path).box_route("alice")
    bob = owner_box_route(tmp_path, principal="bob")
    assert first[0] != bob[0] and first[1] != bob[1]
    rotated = owner_box_route(tmp_path, principal="alice", rotate=True)
    assert rotated[0] == first[0] and rotated[1] != first[1]
    for owner in ("unknown", "reserved"):
        with pytest.raises(LookupError):
            owner_box_route(tmp_path, principal=owner)
    admitted.admission("retire", "alice", "cc-alice")
    with pytest.raises(LookupError):
        owner_box_route(tmp_path, principal="alice")


def test_route_operation_rejects_forged_authority_or_socket_overrides(
    admitted, broker, tmp_path,  # noqa: F811
):
    from tinyassets import rpc_frames as rf

    before = owner_box_route(tmp_path, principal="alice")
    request = {"op": "OWNER_BOX_ROUTE", "principal": "alice", "rotate": True,
               "generation": broker.state["generation"], "token": broker.state["token"]}
    for changes in ({"uid": 300002}, {"socket": "/foreign/app.sock"}, {"token": "old"},
                    {"generation": True}, {"rotate": 1}, {"principal": ""}):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(5)
            channel.connect(str(broker.path))
            channel.sendall(rf.control(rf.CONNECTION, request | changes))
            assert rf.read_frame_blocking(channel).control() == {"op": "OWNER_BOX_ROUTE_REFUSED"}
    assert before == owner_box_route(tmp_path, principal="alice")


@pytest.fixture
def boxes(admitted, tmp_path, monkeypatch):
    from tinyassets.boxes.relay import OwnerRelays

    processes = []
    with tempfile.TemporaryDirectory(prefix="ta-router-") as short:
        run = Path(short)
        monkeypatch.setenv("TINYASSETS_BOXES_RUN", str(run))
        relays = []
        roots, secrets = {}, {}
        try:
            for owner in ("alice", "bob"):
                uid, secret = owner_box_route(tmp_path, principal=owner)
                directory = run / str(uid)
                directory.mkdir()
                secret_path = directory / "secret"
                secret_path.write_text(secret)
                secret_path.chmod(0o400)
                root = tmp_path / owner
                root.mkdir()
                roots[owner], secrets[owner] = root, secret
                relay = OwnerRelays(directory / "relay", None)
                relays.append(relay)
                log = tempfile.TemporaryFile()
                process = subprocess.Popen([
                    sys.executable, "-m", "tests.support.owner_router_process", str(root),
                    str(relay.bind(owner, os.getuid())), str(secret_path),
                    str(directory / "app.sock"),
                ], stdout=log, stderr=log)
                processes.append((process, log))
            for (process, log), owner in zip(processes, ("alice", "bob"), strict=True):
                uid = admitted.owner_machine(owner)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and process.poll() is None:
                    if (run / str(uid) / "app.sock").exists():
                        break
                    time.sleep(.05)
                else:
                    log.seek(0)
                    pytest.fail(log.read().decode())
            # Only shared credential rows are copied to the router. Grants stay
            # inside each owner root, as do all center/file/database records.
            from tinyassets.api_keys import ApiKeyStore

            keys = {}
            with ApiKeyStore(tmp_path).db() as destination:
                for owner, root in roots.items():
                    keys[owner] = json.loads((root / "key.json").read_text())["key"]
                    with sqlite3.connect(root / ".api-keys.sqlite3") as source:
                        destination.executemany("INSERT INTO api_keys VALUES (?,?,?,?,?,?,?)",
                                                source.execute("SELECT * FROM api_keys"))
            yield SimpleNamespace(run=run, roots=roots, secrets=secrets, keys=keys)
        finally:
            for process, log in processes:
                process.terminate()
                process.wait(timeout=30)
                log.close()
            for relay in relays:
                relay.close()


@pytest.fixture
def router(boxes, cloud_runtime, monkeypatch):  # noqa: F811
    from tinyassets import universe_server
    from tinyassets.auth import middleware

    class Provider:
        def resolve_token(self, token):
            if token in {"alice", "bob", "reserved", "unknown"}:
                return Identity(user_id=token, username=token, capabilities=["read", "list"])

    monkeypatch.setattr(middleware, "_provider", Provider())
    app = universe_server.create_streamable_http_app()
    # Guard the actual router request, including auth/middleware, not a mock
    # endpoint. Box processes have independent modules and their own data.
    owner_calls = []

    def forbidden(*args, **kwargs):
        owner_calls.append(True)
        raise AssertionError("router attempted an owner-store operation")

    import tinyassets.conversation_store as conversations
    import tinyassets.daemon_server as owners
    import tinyassets.universe_files as files
    from tinyassets import outside_authority
    from tinyassets.control_plane import binding

    monkeypatch.setattr(binding, "file_binding", forbidden)
    for module in (conversations, files):
        for name, value in vars(module).copy().items():
            if callable(value) and getattr(value, "__module__", "") == module.__name__:
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(owners, "_connect", forbidden)
    monkeypatch.setattr(outside_authority, "current_store", forbidden)
    yield app
    assert owner_calls == [], "even a swallowed owner-store read violates router mode"


def test_owner_doors_use_only_authenticated_box_and_stream_mcp(router, boxes):
    async def exercise():
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        transport = httpx.ASGITransport(app=router)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for owner in ("alice", "bob"):
                response = await client.post("/app/api/file", headers={
                    "authorization": "Bearer " + owner,
                    "x-owner": "alice", "x-box-secret": boxes.secrets["alice"],
                    "x-owner-identity": "forged",
                }, json={"graph_id": "cc-" + owner, "path": "hello.txt"})
                assert response.status_code == 200, response.text
                assert response.text == owner + " private data"
            response = await client.post("/app/api/file", headers={
                "authorization": "Bearer bob"}, json={"graph_id": "cc-alice", "path": "hello.txt"})
            assert response.status_code == 404, response.text
            for owner in ("reserved", "unknown"):
                response = await client.post("/app/api/file", headers={
                    "authorization": "Bearer " + owner}, json={"graph_id": "cc-alice"})
                assert response.status_code == 404, response.text
            response = await client.get("/api/command-centers/cc-alice/status", headers={
                "authorization": "Bearer " + boxes.keys["alice"]})
            assert response.status_code == 200, response.text
            assert "cc-alice" in response.text

        def http_client(**kwargs):
            return httpx.AsyncClient(transport=transport,
                                     headers={"authorization": "Bearer alice"}, timeout=30)

        async with Client(StreamableHttpTransport("http://test/mcp",
                                                  httpx_client_factory=http_client)) as client:
            result = await client.call_tool(
                "read_graph", {"target": "status", "graph_id": "cc-alice"})
            assert not result.is_error, result
            assert "cc-alice" in str(result)

    asyncio.run(exercise())


def test_sse_first_chunk_and_disconnect_are_streamed(router, boxes):
    """A body that cannot finish still delivers its first chunk, then cancels."""
    async def exercise():
        messages = []
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                assert message["body"] == b"data: first\n\n"
                await incoming.put({"type": "http.disconnect"})

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": "/app/api/test-stream",
                 "raw_path": b"/app/api/test-stream", "query_string": b"",
                 "root_path": "", "headers": [(b"authorization", b"Bearer alice")],
                 "server": ("test", 80), "client": ("127.0.0.1", 1)}
        await asyncio.wait_for(router(scope, incoming.get, send), timeout=5)
        assert messages[0]["status"] == 200
        assert (b"mcp-session-id", b"box-stream") in messages[0]["headers"]
        assert any(m.get("body") == b"data: first\n\n" for m in messages)

    asyncio.run(exercise())
    deadline = time.monotonic() + 5
    while not (boxes.roots["alice"] / "stream-closed").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert (boxes.roots["alice"] / "stream-closed").read_text() == "closed"


def test_shared_owner_session_authenticates_and_carries_protected_view_proof(router):
    from tinyassets.onboarding import app_config, owner_sessions

    cookie = "private-session-cookie"
    identity = Identity(user_id="alice", username="alice", capabilities=["read", "list"])
    with owner_sessions.store() as db:
        db.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                   (owner_sessions.hashed(cookie), json.dumps(asdict(identity)), time.time() + 60))
    resource = urlsplit(app_config()["resource"])
    headers = {"cookie": owner_sessions.COOKIE + "=" + cookie,
               "origin": resource.scheme + "://" + resource.netloc}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            response = await client.post("/app/api/file", headers=headers,
                                         json={"graph_id": "cc-alice", "path": "hello.txt"})
            assert response.status_code == 200, response.text
            assert response.text == "alice private data"
            response = await client.post("/app/test-session", headers=headers, json={})
            assert response.status_code == 200, response.text
            assert response.json() == {"owner": "alice"}
            refused = await client.post("/app/api/file", headers=headers | {
                "origin": "https://foreign.example"}, json={})
            assert refused.status_code == 401

    asyncio.run(exercise())


def test_hook_delivery_reaches_token_owners_real_socket(router, tmp_path, monkeypatch):
    from tinyassets.router_credentials import HookOwners

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    HookOwners(tmp_path).publish(hashlib.sha256(b"alice-hook").hexdigest(), "alice")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            response = await client.post("/hooks/inbound/alice-hook", content='{"owner":"bob"}',
                                         headers={"authorization": "Bearer foreign",
                                                  "x-owner": "bob"})
            assert response.status_code == 202, response.text
            assert response.json() == {"owner": "alice", "payload": '{"owner":"bob"}'}

    asyncio.run(exercise())


def test_router_only_routes_need_no_box(tmp_path, monkeypatch, cloud_runtime):  # noqa: F811
    from starlette.testclient import TestClient

    from tests.test_public_run_link import _definition
    from tinyassets import universe_server
    from tinyassets.broker import owner_identities
    from tinyassets.commons_catalog import publish_definition
    from tinyassets.owner_router import forwarded_path

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    def no_box(*args, **kwargs):
        raise AssertionError("router-only route resolved a box")

    monkeypatch.setattr(owner_identities, "owner_box_route", no_box)
    listing = publish_definition(tmp_path, author_id="alice", payload=_definition("Public item"))
    app = universe_server.create_streamable_http_app()
    assert all(not forwarded_path(route.path) for route in app.routes)
    with TestClient(app) as client:
        assert client.get("/app").status_code == 200
        assert client.get("/mcp/pulse", headers={"authorization": "Bearer any"}).status_code == 200
        assert client.get("/.well-known/oauth-protected-resource").status_code == 200
        response = client.get("/app/run/" + listing["agent_definition_id"])
        assert response.status_code == 200 and "Public item" in response.text


def test_owner_socket_requires_current_secret(boxes, admitted):
    owner = Identity(user_id="alice", username="alice")
    headers = {"x-owner": "alice", "x-owner-identity": base64.urlsafe_b64encode(
        json.dumps(asdict(owner)).encode()).decode()}
    path = boxes.run / str(admitted.owner_machine("alice")) / "app.sock"
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(path)),
                      base_url="http://owner") as client:
        for value in (None, "wrong", boxes.secrets["bob"]):
            selected = headers | ({"x-box-secret": value} if value else {})
            assert client.get("/mcp", headers=selected).status_code == 401
        assert client.get("/mcp", headers=headers | {
            "x-box-secret": boxes.secrets["alice"]}).status_code == 200


def test_hook_map_is_router_state_and_unknown_owners_open_no_socket(admitted, tmp_path,
                                                                  monkeypatch):
    from starlette.responses import Response

    from tinyassets.auth.middleware import AuthContextMiddleware
    from tinyassets.owner_router import OwnerRouter
    from tinyassets.router_credentials import HookOwners

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    HookOwners(tmp_path).publish(hashlib.sha256(b"hook-secret").hexdigest(), "alice")
    selected = []

    def resolve(owner):
        selected.append(owner)
        raise LookupError("test stopped before socket")

    app = AuthContextMiddleware(OwnerRouter(Response(), resolver=resolve), router=True)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            for path in ("/hooks/inbound/hook-secret", "/mcp/hooks/hook-secret"):
                assert (await client.post(path, headers={"authorization": "Bearer foreign"},
                                          content=b"untouched")).status_code == 404
            assert selected == ["alice", "alice"]
            assert (await client.post("/hooks/inbound/unknown")).status_code == 404
            assert selected == ["alice", "alice"]

    asyncio.run(exercise())
