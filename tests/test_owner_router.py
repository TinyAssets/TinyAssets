"""Real broker + two owner processes. Shared handlers must never open owner stores."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.support.admission import drive, projection, retire_local, suspend
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.provider import Identity
from tinyassets.broker.admission import local_mutate
from tinyassets.broker.owner_identities import OwnerIdentities


@pytest.fixture(autouse=True)
def initialized_ledger(tmp_path):

    (tmp_path / ".ordinary-admission.json").write_text(json.dumps({"ordinary": True}))


@pytest.fixture
def admitted(in_process_broker, broker, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets.broker import supervisor

    tmp_path.chmod(0o700)
    state = tmp_path / ".broker"
    state.mkdir(mode=0o700, exist_ok=True)
    from tests.support.admission import Plans

    identities = OwnerIdentities(state / "state/owner-identities.db", plans=Plans(tmp_path))
    broker.server._owner_identities = identities
    broker.server._data_root = tmp_path
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(state / "outbound.db", data_root=tmp_path)
    broker.server._ledger_for = lambda owner: ledger
    monkeypatch.setenv("WORKOS_API_KEY", "test-only-unused-for-non-workos-principals")
    for name in ("alice", "bob", "reserved"):
        identities.resolve(name, allocate=True)
    for name in ("alice", "bob"):
        drive(identities.admit(name, routing=projection(identities.path)))
    live = SimpleNamespace(socket_path=broker.path,
                           fence=lambda: (broker.state["generation"], broker.state["token"]),
                           verify_broker=lambda channel: None)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: live)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return identities




@pytest.mark.verifies('owner-daemon-in-box:T1')
def test_route_operation_rejects_forged_authority_or_socket_overrides(
    admitted, broker, boxes, tmp_path,  # noqa: F811
):
    from tinyassets import rpc_frames as rf

    before = admitted.box_route("alice")
    request = {"op": "OWNER_BOX_WAKE", "principal": "alice",
               "generation": broker.state["generation"], "token": broker.state["token"]}
    for changes in ({"uid": 300002}, {"socket": "/foreign/app.sock"}, {"token": "old"},
                    {"generation": True}, {"rotate": 1}, {"principal": ""}):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(5)
            channel.connect(str(broker.path))
            channel.sendall(rf.control(rf.CONNECTION, request | changes))
            assert rf.read_frame_blocking(channel).control() == {"op": "OWNER_LIFECYCLE_REFUSED"}
    assert before == admitted.box_route("alice")


@pytest.fixture
def boxes(admitted, broker, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets import router_credentials
    from tinyassets.boxes import relay as relay_module
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.broker.server import BOX_HOST, BrokerServer

    # Same-uid fixture: production pins both directions to the kernel role uid.
    monkeypatch.setattr(router_credentials, "BROKER_UID", os.getuid())
    monkeypatch.setattr(router_credentials, "RELAY_UID", os.getuid())
    monkeypatch.setattr(router_credentials, "DAEMON_UID", os.getuid())
    monkeypatch.setattr(relay_module, "peer", lambda sock: (os.getpid(), 1002, 1002))
    monkeypatch.setattr(relay_module, "os", SimpleNamespace(
        chown=lambda *args: None, chmod=os.chmod))
    host_broker = BrokerServer(
        ledger_for=broker.server._ledger_for, dispatch_for=broker.server._dispatch_for,
        ops=broker.ops, fence=broker.fence, roles={os.getuid(): BOX_HOST},
        owner_identities=admitted, data_root=tmp_path,
    )
    # Two same-UID listeners model the production server's two kernel roles;
    # their channels belong to the same admission authority.
    host_broker._connections = broker.server._connections
    host_broker._public_egress = broker.server._public_egress
    host_path = tmp_path / "host.sock"
    listener = asyncio.run_coroutine_threadsafe(host_broker.serve(host_path),
                                               broker.loop).result(5)
    credential_service = router_credentials.serving(tmp_path)
    asyncio.run_coroutine_threadsafe(credential_service.__aenter__(), broker.loop).result(5)
    import sqlite3

    from tests.support.owner_daemon_launcher import ProcessRuntime
    from tests.test_owner_volume_lifecycle import Admission
    from tests.test_owner_volume_lifecycle import Images as Volumes
    from tinyassets.boxes import daemon_control
    from tinyassets.boxes.owner_daemons import OwnerDaemons
    from tinyassets.broker.owner_volumes import provision as bind_volume

    with sqlite3.connect(admitted.path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS box_records (principal TEXT PRIMARY KEY, "
                   "machine INTEGER, project_id INTEGER, bhard_bytes INTEGER)")
        for owner in ("alice", "bob"):
            uid = admitted.owner_machine(owner)
            db.execute("INSERT OR REPLACE INTO box_records VALUES (?,?,?,?)",
                       (owner, uid, uid, 1024**3))
            bind_volume(db, owner, uid)
    with tempfile.TemporaryDirectory(prefix="ta-router-") as short:
        run = Path(short)
        monkeypatch.setenv("TINYASSETS_BOXES_RUN", str(run))
        roots = {owner: tmp_path / owner for owner in ("alice", "bob")}
        for root in roots.values():
            root.mkdir()
        runtime = ProcessRuntime({admitted.owner_machine(owner): root
                                  for owner, root in roots.items()})
        relays = OwnerRelays(run / "relay", SimpleNamespace(
            broker_socket=host_path, data_root=tmp_path))
        admission = Admission(admitted)
        volumes = Volumes()
        def destroy_volume(uid):
            import shutil

            assert not runtime.alive(uid)
            Volumes.delete(volumes, uid)
            if runtime.roots[uid].exists():
                shutil.rmtree(runtime.roots[uid])

        volumes.delete = destroy_volume
        manager = OwnerDaemons(runtime, admission, volumes, relays, run)

        def paths(uid, secret):
            directory = run / str(uid)
            directory.mkdir(exist_ok=True)
            path = directory / "secret"
            path.unlink(missing_ok=True)
            path.write_text(secret)
            path.chmod(0o400)
            return directory, path

        monkeypatch.setattr(manager, "_paths", paths)

        from tinyassets.boxes.host_service import HostService

        host = HostService(admission, volumes, relays, manager)
        def control(operation, owner=None, **fields):
            result = host.lifecycle({"lifecycle": operation, **fields,
                                     **({"owner": owner} if owner is not None else {})}, 1002)
            if result.get("error") == "unadmitted":
                raise LookupError("owner has no live admission")
            if set(result) == {"error"}:
                raise RuntimeError(result["error"])
            return result

        monkeypatch.setattr(daemon_control, "request", control)
        try:
            broker.server.routing.publish()
            from tests.support.admission import bind

            bind(admitted.path, broker.server.routing)
            for owner in roots:
                drive(broker.server.routing.wake(owner))
            secrets = {owner: manager.boxes[admitted.owner_machine(owner)].secret
                       for owner in roots}
            keys = {owner: json.loads((root / "key.json").read_text())["key"]
                    for owner, root in roots.items()}
            assert all(not (root / ".api-keys.sqlite3").exists() for root in roots.values())
            yield SimpleNamespace(run=run, roots=roots, secrets=secrets, keys=keys,
                                  manager=manager, runtime=runtime, relays=relays,
                                  host_path=host_path)
        finally:
            # A fenced test box must be stopped, never snapshotted at teardown.
            for owner in roots:
                manager.retire(owner, admitted.owner_machine(owner))
            manager.close()
            runtime.close()
            relays.close()
            asyncio.run_coroutine_threadsafe(
                credential_service.__aexit__(None, None, None), broker.loop).result(5)
            broker.loop.call_soon_threadsafe(listener.close)


@pytest.fixture
def router(boxes, cloud_runtime, monkeypatch):  # noqa: F811
    from tinyassets import universe_server
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.auth import middleware
    from tinyassets.storage import data_dir

    keys = ApiKeyStore(data_dir())
    tokens = {owner: keys.mint(owner, name="authentication fixture", scopes=[],
                              authority=None)["key"]
              for owner in ("alice", "bob", "reserved", "unknown")}
    class Provider:
        def resolve_token(self, token):
            if token in tokens and keys.resolve(tokens[token]) is not None:
                return Identity(user_id=token, username=token, capabilities=["read", "list"])

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
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


@pytest.mark.verifies('owner-daemon-in-box:T1')
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




@pytest.mark.verifies('owner-daemon-in-box:T1')
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


@pytest.mark.verifies('owner-daemon-in-box:T1')
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


@pytest.mark.verifies('owner-daemon-in-box:T2')
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

    monkeypatch.setattr(owner_identities, "wake_owner", no_box)
    listing = publish_definition(tmp_path / ".published", author_id="alice",
                                 payload=_definition("Public item"))
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


@pytest.mark.verifies('owner-daemon-in-box:T1')
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
            assert client.post("/_box/account/delete", headers=selected).status_code == 401
        assert client.get("/mcp", headers=headers | {
            "x-box-secret": boxes.secrets["alice"]}).status_code == 200


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_awake_forwarding_survives_every_control_plane_rpc_outage(router, boxes, monkeypatch):
    from tinyassets.boxes import daemon_control
    from tinyassets.broker import owner_identities

    def unavailable(*args, **kwargs):
        raise AssertionError("awake forwarding called the control plane")

    monkeypatch.setattr(owner_identities, "_owner_request", unavailable)
    monkeypatch.setattr(daemon_control, "request", unavailable)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            response = await client.post("/app/api/file", headers={
                "authorization": "Bearer alice"},
                json={"graph_id": "cc-alice", "path": "hello.txt"})
            assert response.status_code == 200 and response.text == "alice private data"

    asyncio.run(exercise())


@pytest.mark.verifies('owner-daemon-in-box:C5')
@pytest.mark.parametrize("pending_restore", [False, True])
def test_cold_request_uses_broker_and_keeps_credential(
    router, boxes, admitted, broker, monkeypatch, pending_restore,  # noqa: F811
):
    uid, secret = admitted.box_route("alice")
    with boxes.manager.condition:
        assert boxes.manager._sleep(boxes.manager.boxes[uid], idle=0)
    assert not boxes.runtime.alive(uid)
    drive(broker.server.routing.volumes.recover("alice"))
    binding = boxes.manager.admission.volume("alice")
    if pending_restore:
        boxes.manager.admission.volume("alice", "restore", snapshot_id=binding["snapshot_id"])
        with pytest.raises(PermissionError, match="restore"):
            boxes.manager.admission.owner("alice")
    launches = []
    start = boxes.runtime.start

    def launched(*args, **fields):
        launches.append(fields["volume_generation"])
        return start(*args, **fields)

    monkeypatch.setattr(boxes.runtime, "start", launched)
    wake = broker.server.routing.wake
    wakes = []

    def observed(owner, **fields):
        wakes.append(owner)
        return wake(owner, **fields)

    monkeypatch.setattr(broker.server.routing, "wake", observed)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            response = await client.post("/app/api/file", headers={
                "authorization": "Bearer alice"},
                json={"graph_id": "cc-alice", "path": "hello.txt"})
            assert response.status_code == 200 and response.text == "alice private data"

    asyncio.run(exercise())
    assert wakes == ["alice"]
    assert admitted.box_route("alice") == (uid, secret)
    assert boxes.manager.boxes[uid].secret == secret
    assert launches == [binding["volume_generation"] + int(pending_restore)]
    assert boxes.manager.admission.volume("alice")["work"] == {"kind": "running"}


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_withdrawn_route_rejects_delayed_snapshot_and_wake(
    router, boxes, admitted, broker, tmp_path, monkeypatch,  # noqa: F811
):
    from tinyassets.owner_router import resolve_box
    from tinyassets.routing_snapshot import snapshot

    publication = []
    send = broker.server.routing._router

    def capture(request):
        if request["operation"] == "routing_install":
            publication.append(request)
        return send(request)

    monkeypatch.setattr(broker.server.routing, "_router", capture)
    broker.server.routing.publish()
    old = publication[-1]
    suspend(admitted.plans, "alice", reason="abuse", suspended=True)
    assert not boxes.runtime.alive(admitted.owner_machine("alice"))
    current = snapshot(tmp_path)
    assert current.install(old["instance"], old["revision"], old["records"]) > old["revision"]
    with pytest.raises(PermissionError):
        resolve_box("alice")
    with pytest.raises(PermissionError):
        drive(broker.server.routing.wake("alice"))
    assert not boxes.runtime.alive(admitted.owner_machine("alice"))


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_router_restart_needs_snapshot_from_its_own_incarnation(
    router, boxes, broker, tmp_path, monkeypatch,  # noqa: F811
):
    from tinyassets.routing_snapshot import RoutingSnapshot, _snapshots, snapshot

    old = snapshot(tmp_path)
    restarted = RoutingSnapshot()
    monkeypatch.setitem(_snapshots, tmp_path, restarted)
    with pytest.raises(ValueError):
        restarted.install(old.instance, 100000, {})

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            if restarted._installed is None:
                assert (await client.get("/app")).status_code == 503
            return await client.post("/app/api/file", headers={"authorization": "Bearer alice"},
                                     json={"graph_id": "cc-alice", "path": "hello.txt"})

    assert asyncio.run(request()).status_code != 200
    with pytest.raises(RuntimeError, match="snapshot"):
        restarted.route("alice")
    broker.server.routing.publish()
    assert asyncio.run(request()).status_code == 200


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_withdrawal_without_router_delivery_stops_old_ingress(
    router, boxes, admitted, broker, tmp_path, monkeypatch,  # noqa: F811
):
    from tinyassets.routing_snapshot import snapshot

    before = snapshot(tmp_path).route("alice")

    def unavailable(request):
        raise OSError("synthetic router publication outage")

    monkeypatch.setattr(broker.server.routing, "_router", unavailable)
    suspend(admitted.plans, "alice", reason="abuse", suspended=True)
    assert snapshot(tmp_path).route("alice") == before
    assert not boxes.runtime.alive(admitted.owner_machine("alice"))

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            response = await client.post("/app/api/file", headers={"authorization": "Bearer alice"},
                                         json={"graph_id": "cc-alice", "path": "hello.txt"})
            assert response.status_code == 503

    asyncio.run(request())
    assert not boxes.runtime.alive(admitted.owner_machine("alice"))


@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_rotation_stops_old_ingress_before_publishing_replacement(
    boxes, admitted, broker, tmp_path, monkeypatch,  # noqa: F811
):
    from tinyassets.routing_snapshot import snapshot

    uid, old = admitted.box_route("alice")
    send = broker.server.routing._router

    def publication(request):
        if request["operation"] == "routing_install":
            assert not boxes.runtime.alive(uid)
            from tinyassets.broker.admission import digest

            assert request["records"][digest("alice")]["secret"] != old
        return send(request)

    with monkeypatch.context() as patch:
        patch.setattr(broker.server.routing, "_router", publication)
        from tinyassets.restore_recovery import rotate_box_secrets

        boxes.manager.retire("alice", uid)
        boxes.manager.retire("bob", admitted.owner_machine("bob"))
        rotate_box_secrets(tmp_path)
        broker.server.routing.publish()
    fresh = snapshot(tmp_path).route("alice").secret
    assert fresh != old
    drive(broker.server.routing.wake("alice"))
    identity = Identity(user_id="alice", username="alice")
    headers = {"x-owner": "alice", "x-owner-identity": base64.urlsafe_b64encode(
        json.dumps(asdict(identity)).encode()).decode()}
    path = boxes.run / str(uid) / "app.sock"
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(path)),
                      base_url="http://owner") as client:
        assert client.get("/mcp", headers=headers | {"x-box-secret": old}).status_code == 401
        assert client.get("/mcp", headers=headers | {"x-box-secret": fresh}).status_code == 200


@pytest.mark.verifies('owner-daemon-in-box:T1')
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


@pytest.mark.verifies('owner-daemon-in-box:T1')
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


@pytest.mark.verifies('owner-daemon-in-box:C13')
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


@pytest.mark.verifies('owner-daemon-in-box:C13')
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


@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_box_channel_cannot_supply_owner_or_revoke_another_owners_credentials(
    boxes, tmp_path, admitted,
):
    from tinyassets import rpc_frames as rf
    from tinyassets.router_credentials import HookOwners

    def request(owner, fields, **extra):
        relay = boxes.relays.directory / f"{admitted.owner_machine(owner)}.sock"
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
    assert request("alice", {"operation": "account_revoke"}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    assert request("alice", {"operation": "revoke_account"}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    assert request("alice", {"operation": "account_revoke", "owner": "bob"}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    assert request("alice", {"operation": "routing_instance"}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    assert request("alice", {"operation": "routing_install", "instance": "forged",
                              "revision": 999999, "records": {}}) == {
        "op": "ROUTER_CREDENTIAL_REFUSED"}
    token = request("alice", {"operation": "hook_mint"})["result"]["token"]
    request("bob", {"operation": "hook_revoke", "token_hash": hashlib.sha256(token.encode())
            .hexdigest()})
    assert HookOwners(tmp_path).resolve(token) == "alice"
    retire_local(boxes.roots["bob"], "cc-bob")
    assert request("bob", {"operation": "key_list"})["op"] == "ROUTER_CREDENTIAL_IS"
    drive(local_mutate(admitted.path, "bob", "erase", routing=projection(admitted.path)))
    with pytest.raises(FileNotFoundError):
        request("bob", {"operation": "key_list"})


@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_relay_mint_uses_installed_snapshot_with_broker_lookups_unavailable(
    boxes, admitted, tmp_path, monkeypatch,
):
    from tinyassets import rpc_frames as rf
    from tinyassets.broker import owner_identities

    path = boxes.relays.directory / f"{admitted.owner_machine('alice')}.sock"
    def unavailable(*args, **kwargs):
        raise AssertionError("credential mint consulted the broker")

    with monkeypatch.context() as patch:
        patch.setattr(owner_identities, "_owner_request", unavailable)
        patch.setattr(OwnerIdentities, "_connect", unavailable)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(5)
            channel.connect(str(path))
            channel.sendall(rf.control(0, {"op": "ROUTER_CREDENTIAL",
                                          "request": {"operation": "hook_mint"}}))
            reply = rf.read_frame_blocking(channel).control()
    assert reply["op"] == "ROUTER_CREDENTIAL_IS"
    from tinyassets.router_credentials import HookOwners

    assert HookOwners(tmp_path).resolve(reply["result"]["token"]) == "alice"


@pytest.mark.verifies('owner-daemon-in-box:C13')
@pytest.mark.parametrize("publication_available", [True, False])
def test_snapshot_withdrawal_does_not_wait_for_mint_and_retires_relay(
    boxes, admitted, broker, monkeypatch, publication_available,  # noqa: F811
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets import router_credentials
    from tinyassets import rpc_frames as rf
    from tinyassets.broker.admission import owner_call

    entered, release, installing = threading.Event(), threading.Event(), threading.Event()
    original, publish = router_credentials._dispatch, broker.server.routing._router

    def mint(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    def route(document):
        if document["operation"] == "routing_install":
            installing.set()
            if not publication_available:
                raise OSError("synthetic snapshot publication outage")
        return publish(document)

    monkeypatch.setattr(router_credentials, "_dispatch", mint)
    monkeypatch.setattr(broker.server.routing, "_router", route)
    path = boxes.relays.directory / f"{admitted.owner_machine('alice')}.sock"

    def request():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(15)
            channel.connect(str(path))
            channel.sendall(rf.control(0, {"op": "ROUTER_CREDENTIAL",
                                          "request": {"operation": "hook_mint"}}))
            return rf.read_frame_blocking(channel).control()

    async def withdraw():
        await owner_call(admitted.path, "alice", local_mutate, admitted.path, "alice",
                         "suspend", reason="billing", suspended=True,
                         routing=broker.server.routing)

    with ThreadPoolExecutor(max_workers=2) as pool:
        minted = pool.submit(request)
        assert entered.wait(5)
        withdrawn = pool.submit(lambda: asyncio.run_coroutine_threadsafe(
            withdraw(), broker.loop).result(20))
        try:
            assert installing.wait(5)
            withdrawn.result(10)
            assert not minted.done()
        finally:
            release.set()
        assert minted.result(10)["op"] == ("ROUTER_CREDENTIAL_REFUSED" if publication_available
                                          else "ROUTER_CREDENTIAL_IS")
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        request()
    if publication_available:
        with pytest.raises(PermissionError, match="suspended"):
            router_credentials.dispatch(broker.server._data_root, "alice",
                                        {"operation": "hook_mint"})
    else:
        from tinyassets.routing_snapshot import snapshot

        # Even with a stale installed route, the authenticated binding is gone.
        assert snapshot(broker.server._data_root).route("alice")
        assert not boxes.runtime.alive(admitted.owner_machine("alice"))


@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_broker_credential_frame_only_revokes_and_cannot_mint(boxes, tmp_path):
    from tinyassets.router_credentials import _request, revoke_request

    for request in ({"operation": "key_mint", "name": "key", "request_ref": "request"},
                    {"operation": "key_list"}, {"operation": "hook_mint"}):
        answer = _request(tmp_path, {"op": "ROUTER_CREDENTIAL", "owner": "alice",
                                    "request": request})
        assert answer == {"op": "ROUTER_CREDENTIAL_REFUSED"}
    assert revoke_request(tmp_path, "alice") == {
        "op": "ROUTER_CREDENTIAL_IS", "result": {"revoked": True}}


@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_router_credential_socket_refuses_a_non_broker_peer(boxes, tmp_path, monkeypatch):
    from tinyassets import router_credentials

    monkeypatch.setattr(router_credentials, "BROKER_UID", os.getuid() + 1)
    monkeypatch.setattr(router_credentials, "RELAY_UID", os.getuid() + 2)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(router_credentials.credential_socket(tmp_path)))
        # Kernel peer rejection precedes parsing any caller-controlled frame.
        assert channel.recv(1) == b""












@pytest.mark.verifies('owner-daemon-in-box:C13')
def test_a_deleted_owner_can_never_mint_again(tmp_path, monkeypatch, admitted, boxes):
    from tinyassets import router_credentials
    from tinyassets.broker.owner_identities import owner_admission
    from tinyassets.router_credentials import dispatch

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    mint = {"operation": "key_mint", "name": "k", "request_ref": "ref-1"}
    token = dispatch(tmp_path, "alice", {"operation": "hook_mint"})["token"]
    assert token
    drive(local_mutate(tmp_path / ".broker/state/owner-identities.db", "alice", "erase",
        routing=projection(tmp_path / ".broker/state/owner-identities.db")))
    router_credentials.revoke_account(tmp_path, "alice")
    assert owner_admission(tmp_path, "alice").state == "erased"
    # The door's revocation removed the hook the owner minted before deletion.
    assert router_credentials.HookOwners(tmp_path).resolve(token) is None
    # The withdrawn snapshot is checked under the dispatch lock, so a box
    # request racing the revocation lands either before it (and is revoked)
    # or after it (and is refused).
    for request in (mint, {"operation": "hook_mint"}, {"operation": "key_list"}):
        with pytest.raises(LookupError, match="admitted route"):
            dispatch(tmp_path, "alice", request)
    assert dispatch(tmp_path, "bob", {"operation": "hook_mint"})["token"]
    assert not hasattr(router_credentials, "_DISPATCH")




@pytest.mark.verifies('owner-daemon-in-box:C5')
def test_boxhostd_real_process_restart_keeps_secret_and_serves_again(
    boxes, admitted, broker,  # noqa: F811
):
    from tinyassets.owner_router import resolve_box

    first = resolve_box("alice")
    uid = admitted.owner_machine("alice")
    process = boxes.runtime.processes[uid]
    with boxes.manager.condition:
        assert boxes.manager._sleep(boxes.manager.boxes[uid], idle=0)
    assert process.poll() == 0  # SIGTERM completed the actual app lifespan.
    drive(broker.server.routing.wake("alice"))
    second = resolve_box("alice")
    assert first.socket == second.socket and first.secret == second.secret
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(first.socket)),
                      base_url="http://owner") as client:
        assert client.get("/_box/status", headers={"x-box-secret": first.secret}).status_code == 200
        ready = client.get("/_box/status", headers={"x-box-secret": second.secret})
        assert ready.status_code == 200


@pytest.mark.verifies('owner-daemon-in-box:C11')
def test_local_center_retirement_preserves_owner_daemon_and_route(
    boxes, admitted, tmp_path,
):
    from tinyassets.owner_router import BoxRoute, forward, resolve_box

    stale = resolve_box("alice")
    uid = admitted.owner_machine("alice")
    retire_local(boxes.roots["alice"], "cc-alice")
    assert boxes.runtime.alive(uid)
    assert admitted.box_route("alice") == (uid, stale.secret)

    async def proxy(scope, receive, send):
        await forward(scope, receive, send, Identity(user_id="alice", username="alice"),
                      BoxRoute(stale.socket, stale.secret))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(proxy),
                                     base_url="http://router") as client:
            response = await client.post("/app/api/file",
                                         json={"graph_id": "cc-alice", "path": "hello.txt"})
            assert response.status_code == 200
    asyncio.run(exercise())
    # C6: lifecycle stop updates the center list; the owner remains wakeable.
    assert resolve_box("alice").secret == stale.secret
    assert boxes.runtime.alive(uid)
    assert "centers" not in boxes.manager.admission.owner("alice")




@pytest.mark.verifies('owner-daemon-in-box:C11')
def test_shared_channel_cannot_create_or_retire_local_centers(boxes, admitted):
    from tinyassets import rpc_frames as rf
    from tinyassets.owner_centers import Centers

    uid = admitted.owner_machine("alice")
    before = Centers(boxes.roots["alice"]).active()
    for operation in ("ADMIT_CENTER", "RETIRE_CENTER"):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(10)
            channel.connect(str(boxes.relays.directory / f"{uid}.sock"))
            channel.sendall(rf.control(0, {"op": "BOX_CONNECT"}))
            assert rf.read_frame_blocking(channel).control() == {"op": "BOX_CONNECTED"}
            channel.sendall(rf.control(0, {"op": operation, "center": "cc-alice"}))
            assert rf.read_frame_blocking(channel) is None
        assert boxes.runtime.alive(uid)
        assert boxes.runtime.alive(admitted.owner_machine("bob"))
        assert Centers(boxes.roots["alice"]).active() == before
    assert admitted.box_route("alice") == (uid, boxes.secrets["alice"])
    assert "centers" not in boxes.manager.admission.owner("alice")


@pytest.mark.verifies('owner-daemon-in-box:C15')
def test_box_admitted_request_blocks_eviction_until_disconnect(
    boxes, admitted, broker, monkeypatch,  # noqa: F811
):
    import threading

    from tinyassets.owner_router import forward, resolve_box

    monkeypatch.setenv("BACKUP_DEST", ":local:" + str(boxes.run / "snapshots"))
    manager = boxes.manager
    a, b = (admitted.owner_machine(owner) for owner in ("alice", "bob"))
    with manager.condition:
        manager._stop(manager.boxes[b])
    broker.server.routing.config.awake_cap = 1
    broker.server.routing.config.wake_timeout = 10
    route = resolve_box("alice")
    result = []
    waiter = threading.Thread(
        target=lambda: result.append(drive(broker.server.routing.wake("bob"))))

    async def exercise():
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})
        messages = []

        async def send(message):
            messages.append(message)
            if message.get("body"):
                assert message["body"] == b"data: first\n\n"
                waiter.start()
                deadline = time.monotonic() + 5
                while not broker.server.routing._capacity._waiters and time.monotonic() < deadline:
                    await asyncio.sleep(.01)
                assert broker.server.routing._capacity._waiters and not result
                assert boxes.runtime.alive(a)
                await incoming.put({"type": "http.disconnect"})

        scope = {"type": "http", "method": "GET", "path": "/app/api/test-stream",
                 "headers": [], "query_string": b""}
        await forward(scope, incoming.get, send,
                      Identity(user_id="alice", username="alice", capabilities=["read"]), route)
        assert messages[0]["status"] == 200
        # The existing tick observes the completed turn and selects idle sleep.
        broker.server.routing.config.idle_limit = 0
        await asyncio.to_thread(drive, broker.server.routing.tick())
        await asyncio.to_thread(drive, broker.server.routing.close())

    try:
        asyncio.run(exercise())
    finally:
        if waiter.ident is not None:
            waiter.join(15)
    assert not waiter.is_alive() and result == [b]


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("op", ["BOX_ADMISSION", "BOX_CHANNEL", "BOX_SHARED", "BOX_PUBLIC",
                                "BOX_LIFECYCLE", "BOX_CREDENTIAL", "BOX_IDENTITY",
                                "BOX_PING", "BOX_PUBLISH", "BOX_READ"])
def test_running_fenced_box_is_refused_on_every_broker_operation(boxes, admitted, tmp_path, op):
    from tinyassets import rpc_frames as rf

    uid = admitted.owner_machine("alice")
    assert boxes.runtime.alive(uid)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(boxes.host_path))
        if op in {"BOX_IDENTITY", "BOX_PING", "BOX_PUBLISH", "BOX_READ"}:
            channel.sendall(rf.control(rf.CONNECTION, {
                "op": "BOX_CHANNEL", "principal": "alice"}))
            assert rf.read_frame_blocking(channel).control()["op"] == "BOX_ADMITTED"
        drive(local_mutate(tmp_path / ".broker/state/owner-identities.db", "alice", "erase",
            routing=projection(tmp_path / ".broker/state/owner-identities.db")))
        document = {"op": op, "principal": "alice"}
        if op in {"BOX_ADMISSION", "BOX_CHANNEL", "BOX_SHARED"}:
            document["center"] = "cc-alice"
        if op in {"BOX_IDENTITY", "BOX_PING", "BOX_PUBLISH", "BOX_READ"}:
            assert channel.recv(1) == b""  # Withdrawal closed the previously bound channel.
        else:
            channel.sendall(rf.control(rf.CONNECTION, document))
            reply = rf.read_frame_blocking(channel)
            assert reply.control() == {"op": "BOX_REFUSED"}
    assert not boxes.runtime.alive(uid)
    from tinyassets.broker.admission import read

    assert read(admitted, "alice").state == "erased"
