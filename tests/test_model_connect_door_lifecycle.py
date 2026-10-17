"""Door, broker and box processes exercise real custody and relay without network."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.connection_oauth import pkce
from tinyassets.onboarding.model_connect import handle_client_metadata, handle_model_connect
from tinyassets.owner_router import OwnerRouter

PRESET = "openrouter_user_models_v1"
VERIFIER = "v" * 64
ORIGIN = "https://tinyassets.io"
DEPOSIT_SECRETS = {
    "bearer": "fixture-bearer-value",
    "basic": "fixture-user:fixture-password",
    "header": "fixture-header-value",
    "oauth1a": json.dumps(dict(api_key="fixture-api-key", api_secret="fixture-api-secret",
                             access_token="fixture-access", access_token_secret="fixture-secret")),
    "url_secret": "fixture-url-token",
}


def _broker_process(root, socket_path, relay_socket, pipe, kind):
    import secrets

    from tests.support.broker_double import seed_owner_admission
    from tinyassets.broker import acquisition
    from tinyassets.broker.fence import Fence
    from tinyassets.broker.ops import OpStore
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.broker.server import BOX_HOST, OWNER, BrokerServer
    from tinyassets.broker.tokens import ConnectionTokens
    from tinyassets.connection_oauth import directory, tokens, transport
    from tinyassets.storage import outbound_connections as outbound

    root = Path(root)
    seed_owner_admission(root, principal="owner", center="center")
    seed_owner_admission(root, principal="foreign", center="foreign-center")
    identities = OwnerIdentities(root / ".broker/owner-identities.db")
    machine = identities.owner_machine("owner")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INTEGER, "
                   "project_id INTEGER, bhard_bytes INTEGER)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)",
                   ("owner", machine, machine, 1024**3))
    client_secret = secrets.token_urlsafe(32)
    access, refreshed, refresh_token, key = [secrets.token_urlsafe(32) for _ in range(4)]
    os.environ["TINYASSETS_OAUTH_FIXTURE_CLIENT_ID"] = "registered-client"
    if kind == "registered":
        os.environ["TINYASSETS_OAUTH_FIXTURE_SECRET"] = client_secret
    directory.entries = lambda: [{
        "id": "fixture", "hosts": ["api.example.com"],
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "revocation_endpoint": "https://auth.example.com/revoke",
        "client_id_env": "TINYASSETS_OAUTH_FIXTURE_CLIENT_ID",
        "client_secret_env": "TINYASSETS_OAUTH_FIXTURE_SECRET",
        "token_endpoint_auth_method": "client_secret_post",
        "default_scopes": {"call": ["tasks.write"]},
    }]
    events = []

    def token_request(method, url, *, form, **kwargs):
        assert method == "POST"
        assert (form.get("client_secret") == client_secret) is (kind == "registered")
        if url.endswith("/revoke"):
            assert form["token"] == refresh_token
            events.append(("revoke", os.getpid()))
            return 200, {}
        assert url == "https://auth.example.com/token"
        if form["grant_type"] == "authorization_code":
            assert form["code"] == "code" and form["code_verifier"] == VERIFIER
            assert form["redirect_uri"] == ORIGIN + "/app/model-callback/connect"
            events.append(("exchange", os.getpid()))
            return 200, {"access_token": access, "refresh_token": refresh_token,
                         "expires_in": 3600}
        assert form["refresh_token"] == refresh_token
        events.append(("refresh", os.getpid()))
        return 200, {"access_token": refreshed, "refresh_token": refresh_token,
                     "expires_in": 3600}

    tokens.request_json = token_request
    transport.request_json = token_request

    def key_request(request):
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "code": "code", "code_verifier": VERIFIER, "code_challenge_method": "S256"}
        events.append(("exchange", os.getpid()))
        return httpx.Response(200, json={"key": key})

    original_exchange = acquisition.exchange_key

    async def exchange(**kwargs):
        return await original_exchange(**kwargs, client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(key_request)))
    acquisition.exchange_key = exchange

    def network(**kwargs):
        if kind in DEPOSIT_SECRETS:
            assert kwargs["credential"] == DEPOSIT_SECRETS[kind]
        elif kind == "none":
            assert not kwargs["credential"]
        else:
            assert kwargs["credential"] in (access, refreshed, key)
        events.append(("call", os.getpid()))
        return outbound.UpstreamStream.complete({
            "status": 200, "reason": "OK", "headers": {}, "body": b"broker response"}, ())

    outbound._TrustedNetworkDriver = lambda *_: network
    dispatchers = _Dispatchers(root, allow_test_fixtures=True)
    fence = Fence(root / ".broker/fence.json", verify_lease_proof=lambda *_: True)
    args = dict(ledger_for=dispatchers.ledger_for, dispatch_for=dispatchers.dispatch_for,
                ops=OpStore(root / ".broker/ops.db"), fence=fence,
                owner_identities=identities, data_root=root)
    door_server = BrokerServer(**args, roles={os.getuid(): OWNER})
    box_server = BrokerServer(**args, roles={os.getuid(): BOX_HOST})

    async def serve():
        listeners = [await door_server.serve(Path(socket_path)),
                     await box_server.serve(Path(relay_socket))]
        pipe.send(fence.barrier(1, "proof"))
        try:
            while True:
                if not pipe.poll():
                    await asyncio.sleep(0.01)
                    continue
                command = pipe.recv()
                if command == "stop":
                    return
                if command == "expire":
                    keeper = ConnectionTokens(universe_dir=root / "center", owner_user_id="owner")
                    bundle = tokens.decode(keeper._read("fixture"))
                    keeper._write("fixture", replace(bundle, expires_at=time.time() - 10))
                pipe.send(list(events))
        finally:
            for listener in listeners:
                listener.close()
                await listener.wait_closed()
    asyncio.run(serve())


@pytest.fixture
def lifecycle(tmp_path, monkeypatch, request):
    from tinyassets import onboarding
    from tinyassets.boxes import relay as relays
    from tinyassets.broker import supervisor
    from tinyassets.connection_oauth import directory, discovery, tokens
    from tinyassets.onboarding import owner_sessions
    from tinyassets.onboarding.connections import handle_connections

    kind = request.param
    router = tmp_path / "router"
    router.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(router))
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": ORIGIN + "/mcp"})
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    broker_socket, relay_socket = tmp_path / "broker.sock", tmp_path / "box.sock"
    process = ctx.Process(target=_broker_process, args=(
        str(tmp_path / "custody"), str(broker_socket), str(relay_socket), child, kind))
    process.start()
    child.close()
    assert parent.poll(25), "broker startup timed out"
    fence = parent.recv()
    live = SimpleNamespace(socket_path=broker_socket, fence=lambda: fence,
                           verify_broker=lambda _: None)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: live)

    class Admission:
        broker_socket = relay_socket

        def grant(self, owner, center, purpose):
            assert (owner, center) == ("owner", "center")
            return SimpleNamespace(uid=os.getuid())

        def authorize(self, grant, center):
            return center == "center"

    monkeypatch.setattr(relays, "peer", lambda _: (os.getpid(), 1002, 1002))
    relay = relays.OwnerRelays(tmp_path / "relay", Admission())
    relay_path = relay.bind("owner", os.getuid())

    def forbidden(*args, **kwargs):
        raise AssertionError("router reached owner data or credential material")

    monkeypatch.setattr(onboarding, "_read_home", forbidden)
    monkeypatch.setattr(onboarding, "_bootstrap_home", forbidden)
    from tinyassets import storage
    from tinyassets.storage import pending_requests

    monkeypatch.setattr(storage, "_connect", forbidden)
    monkeypatch.setattr(pending_requests, "get_request", forbidden)
    monkeypatch.setattr(directory, "secret", forbidden)
    monkeypatch.setattr(tokens, "exchange_code", forbidden)
    monkeypatch.setattr(discovery, "DISCOVERY_ENABLED", True)
    registrations = []

    def public_provider(method, url, **kwargs):
        if method == "POST":
            assert url == "https://auth.example.com/register"
            assert kwargs["json_body"]["token_endpoint_auth_method"] == "none"
            registrations.append(url)
            return 201, {"client_id": "public-client", "token_endpoint_auth_method": "none"}
        if url == "https://api.example.com/data":
            return 401, {}
        if "/.well-known/oauth-protected-resource" in url:
            return 200, {"resource": "https://api.example.com",
                         "authorization_servers": ["https://auth.example.com"]}
        if "/.well-known/" in url:
            return 200, {"issuer": "https://auth.example.com",
                         "authorization_endpoint": "https://auth.example.com/authorize",
                         "token_endpoint": "https://auth.example.com/token",
                         "registration_endpoint": "https://auth.example.com/register",
                         "code_challenge_methods_supported": ["S256"],
                         "response_types_supported": ["code"],
                         "grant_types_supported": ["authorization_code", "refresh_token"]}
        raise AssertionError((method, url))

    monkeypatch.setattr(discovery, "request_json", public_provider)
    with owner_sessions.store() as db:
        db.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                   (owner_sessions.hashed("owner-cookie"), json.dumps({"user_id": "owner"}),
                    time.time() + 600))
    app = OwnerRouter(Starlette(routes=[
        Route("/app/model-connect/{operation}", handle_model_connect, methods=["POST"]),
        Route("/app/oauth/client-metadata.json", handle_client_metadata),
        Route("/app/connections", handle_connections, methods=["GET", "POST"]),
    ]), resolver=forbidden)

    def post(operation, body):
        async def run():
            identity = Identity(user_id="owner", username="owner", capabilities=["write"])
            with identity_context(identity):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url=ORIGIN) as client:
                    return await client.post(("/app/connections" if operation == "disconnect" else
                        "/app/model-connect/" + operation), json=body,
                        headers={"Origin": ORIGIN, "Cookie": "__Host-ta-owner=owner-cookie"})
        return asyncio.run(run())

    def events(command="events"):
        parent.send(command)
        assert parent.poll(10)
        return parent.recv()

    try:
        yield SimpleNamespace(post=post, events=events, kind=kind, router=router,
                              relay=relay_path, broker_pid=process.pid, registrations=registrations)
    finally:
        relay.close()
        if process.is_alive():
            parent.send("stop")
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        parent.close()
    assert process.exitcode == 0


def _box_call(rig, grant, connection, *, succeeds):
    from tinyassets.platform_secrets import (
        CHILD_FORBIDDEN_ENV,
        ENGINE_REQUIRED_SECRET_ENV,
        child_env,
    )

    code = """
import json
import os
import sys
from pathlib import Path
from tinyassets import owner_runtime, policy_rules
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.effectors.authenticated_external_call import (
    EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL as SINK,
    run_authenticated_external_call_effector,
)
from tinyassets.storage.effector_consents import grant_consent
root, relay, grant, connection, url, succeeds, kind = sys.argv[1:]
root = Path(root)
home = root / "center"
home.mkdir(parents=True, exist_ok=True)
owner_runtime.OWNER_MOUNT = root
owner_runtime._is_owner_mount = lambda p: p == root
os.environ["TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED"] = "1"
# Reuse the effector rig's explicit owner-approved rule and real consent store.
policy_rules.configured_agents = lambda _: {"main"}
policy_rules.classify = lambda *_: ("app.write", "POST")
policy_rules.decide = lambda *_a, **_k: policy_rules.Decision(policy_rules.DO, None, "test_policy")
destination = "model:openrouter_user_models_v1" if "openrouter.ai" in url else "fixture"
grant_consent(home, sink=SINK, destination=destination, granted_by="owner")
runtime = owner_runtime.start(Path(relay))
try:
    packet = {"sink": SINK, "grant_id": grant, "connection_id": connection,
              "verb": "POST", "request": {"url": url, "body": {"input": "hello"}}}
    if kind == "header":
        packet["request"]["header_name"] = "X-Api-Key"
    with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
        result = run_authenticated_external_call_effector(
            node_id="call", output_keys=["out"], run_state={"out": json.dumps(packet)},
            base_path=home)
    if succeeds == "True":
        assert result.get("delivered") and result["response"]["status"] == 200, result
    else:
        assert result.get("error_kind") == "connection_authority_unavailable", result
finally:
    runtime.tools.close()
"""
    url = ("https://openrouter.ai/api/v1/chat/completions" if rig.kind == "preset"
           else "https://api.example.com/data/{secret}" if rig.kind == "url_secret"
           else "https://api.example.com/data")
    environment = child_env(os.environ)
    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        environment.pop(name, None)
    result = subprocess.run([sys.executable, "-c", code, str(rig.router.parent / "box"),
        str(rig.relay), grant, connection, url, str(succeeds), rig.kind],
        env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("lifecycle", ["dcr", "registered", "preset"], indirect=True)
def test_door_exchange_box_call_refresh_disconnect(lifecycle):
    from tinyassets.broker.disconnect import disconnect
    from tinyassets.broker.ledger_queries import granted_resource_row

    rig = lifecycle
    challenge = pkce.challenge_for(VERIFIER)
    body = {"request_ref": "opaque-request", "code_challenge": challenge}
    if rig.kind == "preset":
        body["preset_id"] = PRESET
    else:
        body["definition"] = {"destination": "fixture", "endpoints": [
            {"host": "api.example.com", "path_template": "/data", "methods": ["POST"]}],
            "oauth": {"scopes": ["tasks.write"]}}
    begin_op = "begin" if rig.kind == "preset" else "oauth_begin"
    foreign = rig.post(begin_op, {**body, "center": "foreign-center"})
    assert foreign.status_code == 404
    begun = rig.post(begin_op, body)
    assert begun.status_code == 200, begun.text
    if rig.kind != "preset":
        query = parse_qs(urlsplit(begun.json()["authorize_url"]).query)
        assert query["client_id"] == [
            "registered-client" if rig.kind == "registered" else "public-client"]
        assert len(rig.registrations) == (rig.kind == "dcr")
    assert begun.json()["center"] == "center"
    flow = begun.json()["flow"]
    with pkce.flows_db(rig.router) as (db, _):
        table = "hosted_model_flows" if rig.kind == "preset" else "connection_oauth_flows"
        row = dict(db.execute("SELECT * FROM " + table).fetchone())
        assert row["challenge"] == challenge
        assert VERIFIER not in json.dumps(row)
        assert not {"client_secret", "access_token", "refresh_token"} & row.keys()
    operation = "exchange" if rig.kind == "preset" else "oauth_exchange"
    payload = {"flow": flow, "code": "code", "code_verifier": VERIFIER}
    done = rig.post(operation, payload)
    assert done.status_code == 200, done.text
    receipt = done.json()
    assert receipt["status"] == "provisioned"
    assert receipt["request_ref"] == "opaque-request"
    with pkce.flows_db(rig.router) as (db, _):
        assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert rig.post(operation, payload).status_code == 404
    grant, connection = receipt["grant_id"], receipt["connection_id"]
    resource = granted_resource_row(rig.router, principal="owner", command_center="center",
                                    grant_id=grant)
    assert resource["request_ref"] == "opaque-request"
    _box_call(rig, grant, connection, succeeds=True)
    if rig.kind != "preset":
        rig.events("expire")
        _box_call(rig, grant, connection, succeeds=True)
    destination = receipt["destination"]
    args = dict(principal="owner", command_center="center", destination=destination)
    snap = disconnect(rig.router, **args)
    disconnect(rig.router, **args, action="fence", incarnation=snap["incarnation"])
    erased = disconnect(rig.router, **args, action="erase", incarnation=snap["incarnation"])
    assert erased["removed"] and erased["secrets_removed"]
    _box_call(rig, grant, connection, succeeds=False)
    events = rig.events()
    assert [e[0] for e in events] == (
        ["exchange", "call"] if rig.kind == "preset"
        else ["exchange", "call", "refresh", "call", "revoke"])
    assert all(pid == rig.broker_pid and pid != os.getpid() for _, pid in events)
    assert not (rig.router / ".tinyassets.db").exists()
    assert not (rig.router / "center").exists()
    assert not (rig.router / ".platform").exists()
    assert {p.name for p in rig.router.iterdir()} <= {
        ".runtime", ".hosted-model-auth.db", ".hosted-model-auth.db-wal",
        ".hosted-model-auth.db-shm"}


@pytest.mark.parametrize("lifecycle", ["dcr"], indirect=True)
def test_source_sign_in_uses_client_metadata_without_owner_state(lifecycle, monkeypatch):
    from tinyassets.providers import free_sources

    monkeypatch.setattr(free_sources, "sign_in_preset", lambda _: {
        "id": "fixture", "base_url": "https://api.example.com",
        "wire": "openai_chat", "models": ["fixture-model"],
        "sign_in": {"issuer_host": "api.example.com", "scopes": ["tasks.write"]}})
    response = lifecycle.post("source_sign_in", {
        "preset_id": "fixture", "code_challenge": pkce.challenge_for(VERIFIER),
        "request_ref": "opaque-source-request"})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorize_url"]).query)
    assert query["client_id"] == [ORIGIN + "/app/oauth/client-metadata.json"]
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/app/oauth/client-metadata.json",
             "headers": [], "query_string": b"", "server": ("tinyassets.io", 443),
             "scheme": "https"}
    metadata = asyncio.run(handle_client_metadata(Request(scope)))
    document = json.loads(metadata.body)
    assert document["client_id"] == query["client_id"][0]
    assert document["token_endpoint_auth_method"] == "none"
    assert document["redirect_uris"] == [ORIGIN + "/app/model-callback/connect"]
    assert not lifecycle.registrations


@pytest.mark.parametrize("lifecycle", ["preset"], indirect=True)
def test_inline_popup_flow_at_empty_door_polls_and_cancels(lifecycle):
    begun = lifecycle.post("inline_begin", {"preset_id": PRESET, "request_ref": "popup-request"})
    assert begun.status_code == 200, begun.text
    payload = {"flow": begun.json()["flow"]}
    assert lifecycle.post("inline_poll", payload).json() == {"status": "waiting"}
    assert lifecycle.post("inline_cancel", payload).json() == {"status": "cancelled"}
    with pkce.flows_db(lifecycle.router) as (db, _):
        assert db.execute("SELECT COUNT(*) FROM hosted_model_flows").fetchone()[0] == 0
    assert lifecycle.events() == []


def _box_operation(rig, operation, payload):
    """Run a credential-free operation in an admitted box process."""
    from tinyassets.platform_secrets import (
        CHILD_FORBIDDEN_ENV,
        ENGINE_REQUIRED_SECRET_ENV,
        child_env,
    )

    code = """
import json, os, sys
from pathlib import Path
from tinyassets import owner_runtime
root, relay, operation, payload = sys.argv[1:]
root = Path(root)
root.mkdir(parents=True, exist_ok=True)
owner_runtime.OWNER_MOUNT = root
owner_runtime._is_owner_mount = lambda p: p == root
runtime = owner_runtime.start(Path(relay))
try:
    data = json.loads(payload)
    args = dict(principal="owner", command_center="center")
    if operation == "connect":
        from tinyassets.broker.http_connect import connect_operation
        prepared = connect_operation(root, **args, **data, action="prepare")
        result = connect_operation(root, **args, **data, action="commit",
                                   expected=prepared["revision"])
    elif operation in {"ask", "reconcile", "answer"}:
        from tinyassets.auth.middleware import identity_context
        from tinyassets.auth.provider import Identity
        from tinyassets.api.pending_requests import (
            request_from_user, reconcile_connections, _answer_request,
        )
        from tinyassets.daemon_server import grant_universe_access
        from tinyassets.storage.pending_requests import get_request
        from tinyassets.onboarding import owner_sessions
        owner_sessions.store = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("box read router state"))
        home = root / "center"
        home.mkdir(exist_ok=True)
        with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
            grant_universe_access(root, universe_id="center", actor_id="owner",
                                  permission="admin", granted_by="owner")
            if operation == "ask":
                if data.pop("direct", False):
                    from tinyassets.api.http_connection import connect_http
                    result = connect_http(universe_id="center", payload=data)
                else:
                    result = request_from_user(universe_id="center", payload=data)
            elif operation == "answer":
                result = _answer_request(universe_id="center", payload={
                    "request_id":data["request_id"], "values":data.get("values", {})},
                    owner_session={"owner":"owner"})
            else:
                reconcile_connections("center", home)
                result = get_request(home, data["request_id"])
    else:
        from tinyassets.broker.disconnect import disconnect
        snap = disconnect(root, **args, **data)
        disconnect(root, **args, **data, action="fence", incarnation=snap["incarnation"])
        result = disconnect(root, **args, **data, action="erase", incarnation=snap["incarnation"])
    print(json.dumps(result))
finally:
    runtime.tools.close()
"""
    env = child_env(os.environ)
    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        env.pop(name, None)
    result = subprocess.run([sys.executable, "-c", code, str(rig.router.parent / "box"),
        str(rig.relay), operation, json.dumps(payload)], env=env,
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("lifecycle",
    ["none", "bearer", "basic", "header", "oauth1a", "url_secret"], indirect=True)
def test_other_auth_connect_box_call_disconnect(lifecycle):
    from tinyassets.broker.catalog import connections
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.onboarding.connection_definition import policy

    rig = lifecycle
    endpoints = [{"host": "api.example.com",
                  "path_template": "/data/{secret}" if rig.kind == "url_secret" else "/data",
                  "methods": ["POST"]}]
    if rig.kind == "none":
        receipt = _box_operation(rig, "connect", {
            "destination": "fixture", "policy": policy(endpoints, "none"),
            "request_ref": "box-request"})["projection"]
    else:
        secret = DEPOSIT_SECRETS[rig.kind]
        if rig.kind == "url_secret":
            secret = "https://api.example.com/data/" + secret
        body = {"center": "center", "request_ref": "box-request", "secret": secret,
                "definition": {"destination": "fixture", "auth_scheme": rig.kind,
                               "endpoints": endpoints}}
        assert rig.post("deposit", {**body, "center": "foreign-center"}).status_code == 404
        response = rig.post("deposit", body)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert DEPOSIT_SECRETS[rig.kind] not in response.text
    assert receipt["request_ref"] == "box-request"
    grant, connection = receipt["grant_id"], receipt["connection_id"]
    _box_call(rig, grant, connection, succeeds=True)
    if rig.kind in {"none", "basic", "oauth1a"}:
        erased = _box_operation(rig, "disconnect", {"destination": "fixture"})
        assert erased["removed"]
    else:
        rows = connections(rig.router, principal="owner", command_center="center")
        incarnation = next(i for _, r, i in rows if r.connection_id == connection)
        response = rig.post("disconnect", {"universe_id": "center",
            "destination": "fixture", "incarnation": incarnation})
        assert response.status_code == 200, response.text
        assert response.json()["connection_removed"]
    _box_call(rig, grant, connection, succeeds=False)
    from tinyassets.storage.outbound_connections import GrantResolutionError

    with pytest.raises(GrantResolutionError):
        granted_resource_row(rig.router, principal="owner", command_center="center",
                             grant_id=grant)
    assert rig.events() == [("call", rig.broker_pid)]
    assert not (rig.router / "center").exists()
    assert not (rig.router / ".platform").exists()


@pytest.mark.parametrize("lifecycle", ["bearer"], indirect=True)
def test_box_action_and_approval_link_then_ledger_completion(lifecycle):
    from tinyassets.api.http_connection import _ids

    rig = lifecycle
    endpoints = [{"host": "api.example.com", "path_template": "/data", "methods": ["POST"]}]
    direct = _box_operation(rig, "ask", {"direct": True, "destination": "direct",
        "auth_scheme": "bearer", "allowed_endpoints": endpoints})
    assert direct["status"] == "connect_required"
    refused = _box_operation(rig, "ask", {"direct": True, "destination": "leak",
        "auth_scheme": "bearer", "allowed_endpoints": endpoints, "secret": "never-in-box"})
    assert refused["error"] == "connection_setup_invalid"
    query = parse_qs(urlsplit(direct["connect_url"]).query)
    assert query == {"request": [direct["request_ref"]], "center": ["center"],
                     "connection": [_ids(universe_id="center", destination="direct")[0]]}
    pending = _box_operation(rig, "ask", {
        "kind": "connection", "title": "Connect fixture", "body": "Allow this endpoint.",
        "fields": [], "action": {"type": "connect", "destination": "fixture",
            "auth_scheme": "bearer", "endpoints": endpoints, "uses": {"call": {}}}})
    ref = pending["request_id"]
    answer = _box_operation(rig, "answer", {"request_id": ref})
    assert answer["status"] == "connect_required"
    refused = _box_operation(rig, "answer", {
        "request_id": ref, "values": {"secret": "never-in-box"}})
    assert refused["error"] == "request_invalid"
    assert parse_qs(urlsplit(answer["connect_url"]).query) == {
        "request": [ref], "center": ["center"],
        "connection": [_ids(universe_id="center", destination="fixture")[0]]}
    assert _box_operation(rig, "reconcile", {"request_id": ref})["status"] == "pending"
    body = {"center": "center", "request_ref": "unrelated-request",
            "secret": DEPOSIT_SECRETS["bearer"], "definition": {
                "destination": "fixture", "auth_scheme": "bearer", "endpoints": endpoints}}
    assert rig.post("deposit", body).status_code == 200
    assert _box_operation(rig, "reconcile", {"request_id": ref})["status"] == "pending"
    response = rig.post("deposit", {**body, "request_ref": ref})
    assert response.status_code == 200, response.text
    done = _box_operation(rig, "reconcile", {"request_id": ref})
    assert done["status"] == "answered"
    assert not done.get("answer")
    assert _box_operation(rig, "reconcile", {"request_id": ref})["status"] == "answered"
    assert not (rig.router / "center").exists()


def test_box_connect_schema_has_no_secret_parameters():
    import inspect

    from tinyassets.api.http_connection import CONNECT_FIELDS, connect_http

    assert not CONNECT_FIELDS & {"secret", "token", "key", "password", "capability_url"}
    assert set(inspect.signature(connect_http).parameters) == {"universe_id", "payload"}
