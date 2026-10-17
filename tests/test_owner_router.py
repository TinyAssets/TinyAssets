"""Real broker + two owner processes. Shared handlers must never open owner stores."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
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
def boxes(admitted, broker, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets import router_credentials
    from tinyassets.boxes import relay as relay_module
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.broker.server import BOX_HOST, BrokerServer

    # Same-uid fixture: production pins both directions to the kernel role uid.
    monkeypatch.setattr(router_credentials, "BROKER_UID", os.getuid())
    monkeypatch.setattr(router_credentials, "DAEMON_UID", os.getuid())
    monkeypatch.setattr(relay_module, "peer", lambda sock: (os.getpid(), 1002, 1002))
    monkeypatch.setattr(relay_module, "os", SimpleNamespace(
        chown=lambda *args: None, chmod=os.chmod))
    host_broker = BrokerServer(
        ledger_for=broker.server._ledger_for, dispatch_for=broker.server._dispatch_for,
        ops=broker.ops, fence=broker.fence, roles={os.getuid(): BOX_HOST},
        owner_identities=admitted, data_root=tmp_path,
    )
    host_path = tmp_path / "host.sock"
    listener = asyncio.run_coroutine_threadsafe(host_broker.serve(host_path),
                                               broker.loop).result(5)
    credential_service = router_credentials.serving(tmp_path)
    asyncio.run_coroutine_threadsafe(credential_service.__aenter__(), broker.loop).result(5)
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
                relay = OwnerRelays(directory / "relay", SimpleNamespace(broker_socket=host_path))
                relays.append(relay)
                log = tempfile.TemporaryFile()
                process = subprocess.Popen([
                    sys.executable, "-m", "tests.support.owner_router_process", str(root),
                    str(relay.bind(owner, uid)), str(secret_path),
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
            keys = {owner: json.loads((root / "key.json").read_text())["key"]
                    for owner, root in roots.items()}
            assert all(not (root / ".api-keys.sqlite3").exists() for root in roots.values())
            yield SimpleNamespace(run=run, roots=roots, secrets=secrets, keys=keys)
        finally:
            for process, log in processes:
                process.terminate()
                process.wait(timeout=30)
                log.close()
            for relay in relays:
                relay.close()
            asyncio.run_coroutine_threadsafe(
                credential_service.__aexit__(None, None, None), broker.loop).result(5)
            broker.loop.call_soon_threadsafe(listener.close)


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
            mixed = await client.post("/app/test-session", headers=headers | {
                "authorization": "Bearer bob"}, json={})
            assert mixed.status_code == 401
            matching = await client.post("/app/test-session", headers=headers | {
                "authorization": "Bearer alice"}, json={})
            assert matching.status_code == 200
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
    # Approval handoff is sign-in navigation at the door: before sign-in it
    # redirects with a return cookie; with the session it reaches the app.
    import json
    import time

    from tinyassets.onboarding import approval_handoff
    from tinyassets.onboarding.owner_sessions import COOKIE, hashed, store

    ref = "h" * 43
    with approval_handoff.flows() as conn:
        conn.execute("INSERT INTO approval_handoffs VALUES (?,?,?,?,?)",
                     (hashed(ref), "alice", "request-1", "web", time.time() + 600))
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(approval_handoff.PREFIX + ref, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/app/owner-sign-in")
        assert approval_handoff.RETURN_COOKIE in response.headers["set-cookie"]
        with store() as conn:
            conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)", (
                hashed("alice-session"),
                json.dumps(Identity(user_id="alice", username="alice").to_dict()),
                time.time() + 600))
        client.cookies.set(COOKIE, "alice-session")
        response = client.get(approval_handoff.PREFIX + ref, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/app?approval=" + ref


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


def test_owner_box_refuses_a_request_for_any_other_owner(boxes, admitted):
    """The silo authorizes its own caller: even with its valid secret, alice's
    box serves no identity but alice's."""
    other = Identity(user_id="bob", username="bob")
    path = boxes.run / str(admitted.owner_machine("alice")) / "app.sock"
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(path)),
                      base_url="http://owner") as client:
        response = client.get("/mcp", headers={
            "x-owner": "bob", "x-box-secret": boxes.secrets["alice"],
            "x-owner-identity": base64.urlsafe_b64encode(
                json.dumps(asdict(other)).encode()).decode(),
        })
        assert response.status_code == 401


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
            assert (await client.post("/hooks/inbound/hook-secret", headers={
                "authorization": "Bearer foreign"}, content=b"untouched")).status_code == 404
            assert selected == ["alice"]
            assert (await client.post("/hooks/inbound/unknown")).status_code == 404
            assert selected == ["alice"]

    asyncio.run(exercise())


def test_box_minted_key_authenticates_router_until_box_revokes_it(router, boxes):
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            key = json.loads((boxes.roots["alice"] / "key.json").read_text())
            api = "/api/command-centers/cc-alice/status"
            bearer = {"authorization": "Bearer " + key["key"]}
            assert (await client.get(api, headers=bearer)).status_code == 200
            headers = {"authorization": "Bearer alice"}
            listing = await client.post("/app/test-credentials", headers=headers,
                                        json={"operation": "key_list"})
            assert listing.status_code == 200, listing.text
            assert listing.json()["keys"][0]["key_id"] == key["key_id"]
            assert "secret" not in listing.text and key["key"] not in listing.text
            revoked = await client.post("/app/test-credentials", headers=headers,
                                        json={"operation": "key_revoke", "key_id": key["key_id"]})
            assert revoked.status_code == 200, revoked.text
            assert (await client.get(api, headers=bearer)).status_code == 401
            assert not (boxes.roots["alice"] / ".api-keys.sqlite3").exists()

    asyncio.run(exercise())


def test_box_created_hooks_route_then_all_revocation_paths_refuse(router, monkeypatch):
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            headers = {"authorization": "Bearer alice"}
            for operation in ("hook_revoke", "hook_revoke_prefix", "hook_revoke_source"):
                source = {"source_id": "source-a"} if operation == "hook_revoke_source" else {}
                minted = await client.post("/app/test-credentials", headers=headers,
                                          json={"operation": "hook_mint", **source})
                assert minted.status_code == 200, minted.text
                token = minted.json()["token"]
                response = await client.post("/hooks/inbound/" + token, content=b"delivery")
                assert response.status_code == 202, response.text
                assert response.json() == {"owner": "alice", "payload": "delivery"}
                from tinyassets.storage.webhook_hooks import _PREFIX_LEN

                revoked = await client.post("/app/test-credentials", headers=headers, json={
                    "operation": operation, "token": token, "prefix": token[:_PREFIX_LEN],
                    **source,
                })
                assert revoked.status_code == 200 and revoked.json()["revoked"], revoked.text
                refused = await client.post("/hooks/inbound/" + token, content=b"delivery")
                assert refused.status_code == 404

    asyncio.run(exercise())


def test_box_channel_cannot_supply_owner_or_revoke_another_owners_credentials(
    boxes, tmp_path, admitted,
):
    from tinyassets import rpc_frames as rf
    from tinyassets.router_credentials import HookOwners

    def request(owner, fields, **extra):
        directory = next(path for path in boxes.run.iterdir()
                         if (path / "secret").read_text() == boxes.secrets[owner])
        relay = next((directory / "relay").glob("*.sock"))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(5)
            channel.connect(str(relay))
            channel.sendall(rf.control(0, {"op": "ROUTER_CREDENTIAL", "request": fields, **extra}))
            answer = rf.read_frame_blocking(channel)
            return answer.control() if answer else None

    key = json.loads((boxes.roots["alice"] / "key.json").read_text())
    assert request("bob", {"operation": "key_revoke", "key_id": key["key_id"]}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    assert request("bob", {"operation": "key_list", "owner": "alice"}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    assert request("bob", {"operation": "key_list"}, principal="alice") is None
    assert request("alice", {"operation": "account_revoke", "owner": "bob"}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    token = request("alice", {"operation": "hook_mint"})["result"]["token"]
    request("bob", {"operation": "hook_revoke", "token_hash": hashlib.sha256(token.encode())
            .hexdigest()})
    assert HookOwners(tmp_path).resolve(token) == "alice"
    admitted.admission("retire", "bob", "cc-bob")
    assert request("bob", {"operation": "key_list"}) == {"op": "ROUTER_CREDENTIAL_REFUSED"}


def test_router_credential_socket_refuses_a_non_broker_peer(boxes, tmp_path, monkeypatch):
    from tinyassets import router_credentials
    from tinyassets import rpc_frames as rf

    monkeypatch.setattr(router_credentials, "BROKER_UID", os.getuid() + 1)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(router_credentials.credential_socket(tmp_path)))
        channel.sendall(rf.control(0, {"op": "ROUTER_CREDENTIAL", "owner": "alice",
                                       "request": {"operation": "key_list"}}))
        try:
            assert rf.read_frame_blocking(channel) is None
        except ConnectionResetError:
            pass


def test_default_socket_is_under_the_existing_compose_mount(admitted, monkeypatch):
    from tinyassets.owner_router import resolve_box

    monkeypatch.delenv("TINYASSETS_BOXES_RUN", raising=False)
    route = resolve_box("alice")
    assert route.socket == Path("/run/tinyassets-boxhostd") / str(
        admitted.owner_machine("alice")) / "app.sock"
    compose = (Path(__file__).parents[1] / "deploy/compose.yml").read_text()
    assert "/run/tinyassets-boxhostd:/run/tinyassets-boxhostd:ro" in compose


def test_router_hook_rows_are_capped_per_owner(tmp_path, monkeypatch):
    from tinyassets import router_credentials
    from tinyassets.router_credentials import HookOwners

    monkeypatch.setattr(router_credentials, "MAX_HOOKS_PER_OWNER", 3)
    hooks = HookOwners(tmp_path)
    digests = [hashlib.sha256(f"alice-{n}".encode()).hexdigest() for n in range(4)]
    for digest in digests[:3]:
        hooks.publish(digest, "alice")
    with pytest.raises(ValueError, match="too many inbound hooks"):
        hooks.publish(digests[3], "alice")
    # Another owner's room is its own; a revocation frees a slot.
    hooks.publish(hashlib.sha256(b"bob-0").hexdigest(), "bob")
    hooks.revoke(digests[0], "alice")
    hooks.publish(digests[3], "alice")


def test_router_key_rows_stay_bounded_across_mint_revoke_cycles(tmp_path, monkeypatch):
    import sqlite3
    from contextlib import closing

    from tinyassets import api_keys
    from tinyassets.api_keys import ApiKeyStore

    monkeypatch.setattr(api_keys, "MAX_REVOKED_KEYS_PER_OWNER", 2)
    keys = ApiKeyStore(tmp_path)
    for n in range(6):
        minted = keys.mint("alice", name=f"k{n}", request_ref=f"ref-{n}", scopes=[],
                           authority=None)
        keys.revoke("alice", minted["key_id"], authority=None)
    bob = keys.mint("bob", name="b", request_ref="ref-b", scopes=[], authority=None)
    keys.revoke("bob", bob["key_id"], authority=None)
    with closing(sqlite3.connect(keys.path)) as db:
        rows = dict(db.execute("SELECT owner, COUNT(*) FROM api_keys GROUP BY owner"))
    assert rows == {"alice": 2, "bob": 1}


def test_account_revoke_from_box_removes_only_bound_owners_router_state(
    router, boxes, tmp_path, monkeypatch,
):
    from tinyassets import router_credentials
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.connection_oauth.pkce import flows_db
    from tinyassets.onboarding import (
        app_config,
        approval_handoff,
        canary_sign_in,
        inline_model_connect,
        owner_sessions,
        session_store,
    )

    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    monkeypatch.setenv(canary_sign_in.USER_ENV, "alice")
    with canary_sign_in._limits() as db:
        db.execute("INSERT INTO canary_door_limits VALUES ('ip',1,?)", (time.time() + 600,))
    refresh_handles = {}
    for owner in ("alice", "bob"):
        first = session_store.mint(owner + "-refresh", owner=owner)
        second = session_store.rotate(first, owner + "-rotated")
        third = session_store.mint(owner + "-other-device", owner=owner)
        refresh_handles[owner] = (first, second, third)
    requests = []
    dispatch = router_credentials.dispatch

    def recorded(root, owner, request):
        requests.append((root, owner, request))
        return dispatch(root, owner, request)

    monkeypatch.setattr(router_credentials, "dispatch", recorded)
    for owner in ("alice", "bob"):
        with owner_sessions.store() as db:
            db.execute("INSERT INTO owner_sessions VALUES (?,?,?)", (
                owner_sessions.hashed(owner + "-cookie"),
                json.dumps(Identity(user_id=owner, username=owner).to_dict()), time.time() + 600))
        with approval_handoff.flows() as db:
            db.execute("INSERT INTO approval_handoffs VALUES (?,?,?,?,?)", (
                owner_sessions.hashed(owner[0] * 43), owner, "request", "web", time.time() + 600))
        with inline_model_connect.flows() as db:
            db.execute("INSERT INTO inline_model_flows VALUES (?,?,?,?,?,?,?)", (
                owner, owner, "home", b"secret", "browser", "ready", time.time() + 600))
        with flows_db(tmp_path) as (db, now):
            db.execute("INSERT INTO hosted_model_flows VALUES (?,?,?,?,?,?,?,?,?)", (
                owner, owner, "home", "preset", "digest", "challenge", "origin", now, now + 500))
            db.execute("INSERT INTO connection_oauth_flows VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                owner, owner, "home", "request", "digest", "challenge", "client", "redirect",
                now, now + 500, owner))
    # Multiple live keys must all be revoked, including keys beyond the fixture key.
    extra = ApiKeyStore(tmp_path).mint("alice", name="extra", scopes=[], authority=None)
    resource = urlsplit(app_config()["resource"])
    origin = resource.scheme + "://" + resource.netloc

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            tokens = {}
            for owner in ("alice", "bob"):
                minted = await client.post("/app/test-credentials", headers={
                    "authorization": "Bearer " + owner}, json={"operation": "hook_mint"})
                assert minted.status_code == 200, minted.text
                tokens[owner] = minted.json()["token"]
                assert (await client.post("/hooks/inbound/" + tokens[owner])).status_code == 202
                assert (await client.post("/app/test-session", headers={
                    "cookie": owner_sessions.COOKIE + "=" + owner + "-cookie",
                    "origin": origin}, json={})).status_code == 200
            requests.clear()
            revoked = await client.post("/app/test-credentials", headers={
                "authorization": "Bearer alice"}, json={"operation": "account_revoke"})
            assert revoked.status_code == 200, revoked.text
            assert revoked.json() == {"revoked": True}
            assert requests == [(tmp_path, "alice", {"operation": "account_revoke"})]
            for owner in ("alice", "bob"):
                session = await client.post("/app/test-session", headers={
                    "cookie": owner_sessions.COOKIE + "=" + owner + "-cookie",
                    "origin": origin}, json={})
                assert session.status_code == (401 if owner == "alice" else 200)
                api = await client.get("/api/command-centers/cc-" + owner + "/status", headers={
                    "authorization": "Bearer " + boxes.keys[owner]})
                assert api.status_code == (401 if owner == "alice" else 200)
                hook = await client.post("/hooks/inbound/" + tokens[owner])
                assert hook.status_code == (404 if owner == "alice" else 202)
            assert (await client.get("/api/command-centers/cc-alice/status", headers={
                "authorization": "Bearer " + extra["key"]})).status_code == 401

    asyncio.run(exercise())
    for owner, handles in refresh_handles.items():
        for handle in handles:
            assert bool(session_store.read(handle)[0]) == (owner == "bob")
    from tests.test_onboarding_session_refresh import _boom, _drive

    for handle in refresh_handles["alice"]:
        status, body, _, calls = _drive(
            {"grant_type": "refresh_token", "session_ref": handle},
            monkeypatch=monkeypatch, upstream=_boom, data_dir=str(tmp_path))
        assert status == 401 and body["error"] == "no_refresh_token"
        assert not calls
    with canary_sign_in._limits() as db:
        assert db.execute("SELECT COUNT(*) FROM canary_door_limits").fetchone()[0] == 0
    assert owner_sessions.lookup("alice-cookie") is None
    assert owner_sessions.lookup("bob-cookie") is not None
    assert approval_handoff.load("a" * 43) is None
    assert approval_handoff.load("b" * 43)["owner"] == "bob"
    with inline_model_connect.flows() as db:
        rows = {row["owner"]: (row["status"], row["sealed"]) for row in
                db.execute("SELECT * FROM inline_model_flows")}
    assert rows == {"alice": ("cancelled", b""), "bob": ("ready", b"secret")}
    with flows_db(tmp_path) as (db, _):
        for table in ("hosted_model_flows", "connection_oauth_flows"):
            assert [r[0] for r in db.execute(f"SELECT owner_user_id FROM {table}")] == ["bob"]
    for root in boxes.roots.values():
        assert not (root / ".runtime" / "owner-sessions.db").exists()


def test_retired_hook_door_never_delivers_a_valid_token(router, monkeypatch):
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            minted = await client.post("/app/test-credentials", headers={
                "authorization": "Bearer alice"}, json={"operation": "hook_mint"})
            assert minted.status_code == 200, minted.text
            token = minted.json()["token"]
            for headers in ({}, {"authorization": "Bearer alice"}):
                retired = await client.post("/mcp/hooks/" + token, headers=headers)
                assert retired.status_code in {401, 404}
            delivered = await client.post("/hooks/inbound/" + token, content=b"still delivered")
            assert delivered.status_code == 202
            assert delivered.json() == {"owner": "alice", "payload": "still delivered"}

    asyncio.run(exercise())
