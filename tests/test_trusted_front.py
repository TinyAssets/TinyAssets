"""trusted-front (ADR-030): the router answers only what its one registry names.

``owner_router.TRUSTED`` keys MCP requests by JSON-RPC method, tool and target,
and app requests by path and method. A registered request is answered from the
router's verified identity; every other one reaches the owner's box unchanged.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import owner_router
from tinyassets.auth.provider import Identity

ROUTER = Path(__file__).resolve().parents[1] / "tinyassets" / "owner_router.py"
# What OwnerRouter.__call__ may await. Anything else there is a second way to answer.
_CALL_AWAITS = {"refuse_non_http", "JSONResponse", "match", "_owner", "answer", "app"}
_OWNER_AWAITS = {"inbound", "JSONResponse", "run_in_threadpool", "_unavailable", "forward"}


def _awaited(function):
    names = []
    for node in ast.walk(function):
        if isinstance(node, ast.Await):
            call = node.value
            while isinstance(call, ast.Call) and isinstance(call.func, ast.Call):
                call = call.func  # JSONResponse(...)(scope, receive, send)
            func = call.func if isinstance(call, ast.Call) else call
            names.append(func.attr if isinstance(func, ast.Attribute) else func.id)
    return names


def _method(tree, name):
    router = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == "OwnerRouter")
    return next(node for node in router.body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_the_router_answers_at_one_dispatch_point():
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    call = _awaited(_method(tree, "__call__"))
    assert set(call) <= _CALL_AWAITS, "a second interception path in OwnerRouter.__call__"
    assert call.count("answer") == 1 and call.count("match") == 1
    assert set(_awaited(_method(tree, "_owner"))) <= _OWNER_AWAITS
    # Only lifespan and the registered shared-app answer reach the router's own app.
    app_calls = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "app"
                 and getattr(node.func.value, "id", "") != "TRUSTED"]
    assert len(app_calls) == 2, f"the router's own app is reached at lines {app_calls}"
    # Elsewhere the router builds only refusals. The one exception is the inbound
    # door's setup handshake, which echoes the broker-verified challenge.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "JSONResponse":
            keys = {key.value for key in node.args[0].keys if isinstance(key, ast.Constant)}
            assert keys & {"error", "challenge"}, f"line {node.lineno} answers beside TRUSTED"
    # A registered answer the router composes is framed in one place, mcp_answer.
    builders = {function.name for function in ast.walk(tree)
                if isinstance(function, ast.AsyncFunctionDef | ast.FunctionDef)
                for node in ast.walk(function) if isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "Response"}
    assert builders == {"mcp_answer"}, f"answers composed in {builders}"


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_no_second_route_table_or_events_door():
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    registered = {id(arg) for node in ast.walk(tree) if isinstance(node, ast.For)
                  for arg in ast.walk(node.iter)}
    loose = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
             and isinstance(node.value, str) and node.value.startswith("/app/")
             and node.value != "/app/" and id(node) not in registered]
    assert loose == [], f"app paths outside TRUSTED registrations: {loose}"
    for path in ROUTER.parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (path != ROUTER and isinstance(node, ast.ImportFrom)
                    and node.module == "tinyassets.mcp_events_http"
                    and any(alias.name == "door" for alias in node.names)):
                pytest.fail(f"{path} answers events outside the registry")


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_the_tools_list_stays_the_boxs():
    front = owner_router.TrustedFront()
    with pytest.raises(ValueError):
        front.mcp("tools/list")
    assert not any(key[0] == "tools/list" for key in owner_router.TRUSTED.mcp_routes)
    front.mcp("tools/call", "read_graph", "board")(lambda front: None)
    with pytest.raises(ValueError):
        front.mcp("tools/call", "read_graph", "board")(lambda front: None)


# -- Behaviour through the real router --------------------------------------


@pytest.fixture
def door(monkeypatch):
    """The router with its box recorded and one registered tool target."""
    from tinyassets import router_traffic, routing_snapshot
    from tinyassets.auth import middleware

    class Snapshot:
        def require_current(self):
            pass

    monkeypatch.setattr(routing_snapshot, "snapshot", lambda root: Snapshot())
    monkeypatch.setattr(router_traffic, "admits", lambda *args: True)
    monkeypatch.setattr(middleware, "published_box_owner", lambda root, path, caller: caller)
    boxed, answered = [], []

    async def forward(scope, receive, send, identity, route):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        boxed.append(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"box"})

    monkeypatch.setattr(owner_router, "forward", forward)
    front = owner_router.TrustedFront()
    front.app_routes.update(owner_router.TRUSTED.app_routes)  # the real app registrations

    @front.mcp("tools/call", "read_graph", "board")
    async def board(request):
        answered.append((request.identity.user_id, request.route))
        await reply(request, {"board": []})

    monkeypatch.setattr(owner_router, "TRUSTED", front)
    route = owner_router.BoxRoute(Path("/unused"), "s" * 43)
    shared = []

    async def own_app(scope, receive, send):
        """The router's own route table: records what it was handed."""
        from starlette.requests import Request

        shared.append((scope["method"], scope["path"], await Request(scope, receive).body()))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"router"})

    router = owner_router.OwnerRouter(own_app, resolver=lambda owner: route)

    async def app(scope, receive, send):
        token = middleware._current_identity.set(Identity("alice", "alice"))
        try:
            await router(scope, receive, send)
        finally:
            middleware._current_identity.reset(token)

    def post(body, chunk=None, path="/mcp"):
        async def chunks():
            for start in range(0, len(body), chunk):
                yield body[start:start + chunk]

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="https://tinyassets.io") as client:
                return await client.post(path, content=chunks() if chunk else body)
        return asyncio.run(exercise())

    post.shared = shared
    return post, boxed, answered, route


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_owner_policy_is_served_by_the_routers_own_app(door):
    from tinyassets import onboarding

    post, boxed, _, _ = door
    # N10: the router's own app keeps /app/api/policy and reads its body (up to
    # 32 kB) itself; the registry hands that body over untouched, never to the box.
    proposal = json.dumps({"action": "apply_proposal", "shown": "x" * 20000}).encode()
    assert post(proposal, chunk=4096, path="/app/api/policy").text == "router"
    assert post.shared == [("POST", "/app/api/policy", proposal)] and boxed == []
    assert not owner_router.forwarded_path("/app/api/policy")
    routes = [route for route in onboarding.onboarding_routes()
              if route.path == "/app/api/policy"]
    assert len(routes) == 1 and {"GET", "POST"} <= routes[0].methods


async def reply(request, result):
    from starlette.responses import JSONResponse

    await JSONResponse({"jsonrpc": "2.0", "id": 1, "result": result})(
        request.scope, request.receive, request.send)


def call(target, name="read_graph", **extra):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", **extra,
                       "params": {"name": name, "arguments": {"target": target}}}).encode()


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_a_registered_target_is_answered_from_the_verified_identity(door):
    post, boxed, answered, route = door
    for target in ("board", " Board "):
        response = post(call(target))
        assert response.json()["result"] == {"board": []}
    assert answered == [("alice", route)] * 2 and boxed == []


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_everything_unregistered_reaches_the_box_byte_for_byte(door):
    post, boxed, answered, _ = door
    bodies = [call("status"), call("board", name="write_graph"),
              b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
              b'{ "jsonrpc" : "2.0", "method": "notifications/initialized" }\n',
              '{"jsonrpc":"2.0","id":3,"result":{"text":"caf\\u00e9 é"}}'.encode()]
    for body in bodies:
        assert post(body).text == "box"
    assert boxed == bodies and answered == []


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_an_oversized_or_unreadable_body_reaches_the_box_byte_for_byte(door):
    post, boxed, answered, _ = door
    # Over the bound, even a registered call is no registered request: no client
    # sends one that large. The box gets the bytes read so far, then the rest.
    oversized = call("board")[:-1] + b" " * (1_048_576 + 1) + b"}"
    bodies = [oversized, b"not json",
              b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}',
              b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}',
              b"[" * 200_000 + b"]" * 200_000]  # too deep to parse: the box refuses it
    for body in bodies:
        assert post(body, chunk=65536).text == "box"
    assert boxed == bodies and answered == []


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_duplicate_keys_resolve_last_wins_as_the_box_reads_them(door):
    post, boxed, answered, _ = door
    body = call("board").replace(b'"target": "board"', b'"target": "status", "target": "board"')
    assert post(body).json()["result"] == {"board": []}
    assert boxed == [] and len(answered) == 1


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_only_a_batch_holding_a_registered_request_is_refused(door, monkeypatch):
    from tinyassets import router_traffic

    post, boxed, answered, _ = door
    response = post(b"[" + call("board") + b"]")
    assert (response.status_code, response.json()["error"]["code"]) == (400, -32600)
    unregistered = b"[" + call("status") + b"]"
    assert post(unregistered).text == "box"
    assert boxed == [unregistered] and answered == []
    # The refusal is an owner answer: maintenance still comes first, as for a forward.
    monkeypatch.setattr(router_traffic, "admits", lambda *args: False)
    assert post(b"[" + call("board") + b"]").json() == {"error": "maintenance"}


# -- The box builds only what the router forwards -----------------------------


def _mounted(app):
    while not hasattr(getattr(app, "router", None), "routes"):
        app = app.app  # through the middleware stack to the Starlette app
    return [(route.path, verb) for route in app.router.routes
            for verb in sorted(getattr(route, "methods", None) or {None})]


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_the_box_mounts_no_trusted_app_registration():
    from tests.owner_app_fixture import create_owner_app
    from tinyassets.onboarding import onboarding_routes

    mounted = _mounted(create_owner_app())
    overlap = [(path, verb) for path, verb in mounted
               if owner_router.TRUSTED._app(path, verb) is not None]
    assert overlap == [], f"the box mounts what the router answers: {overlap}"
    # Not vacuous: registered routes exist to drop, and forwarded ones stay.
    built = {route.path for route in onboarding_routes()}
    assert {"/app/trace", "/app/api/policy", "/app/{slug}/state"} <= built
    paths = {path for path, _ in mounted}
    assert {"/app/me", "/app/{slug}", "/app/api/read", "/mcp"} <= paths
    assert not {"/app/trace", "/app/client-error", "/app/run/{listing}"} & paths


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_a_box_route_keeps_only_its_forwarded_methods():
    from starlette.routing import Route

    async def endpoint(request):
        pass

    front = owner_router.TrustedFront()
    front.app("/app/x", "POST")
    front.app("/app/y/")
    front.app("/app/*/z")
    kept = front.box_routes([
        Route("/app/x", endpoint, methods=["GET", "POST"]),
        Route("/app/y/{a}", endpoint), Route("/app/{s}/z", endpoint, methods=["POST"]),
        Route("/app/{s}", endpoint, methods=["GET", "POST"]),
    ])
    assert [(route.path, route.methods) for route in kept] == [
        ("/app/x", {"GET", "HEAD"}), ("/app/{s}", {"GET", "HEAD", "POST"})]


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_a_route_that_cannot_narrow_faithfully_refuses_the_build():
    from starlette.middleware import Middleware
    from starlette.routing import Mount, Route

    async def endpoint(request):
        pass

    class App:  # an ASGI class: its route takes any method
        async def __call__(self, scope, receive, send):
            pass

    asgi = App()

    class Special(Route):
        pass

    class Wrap:
        def __init__(self, app):
            self.app = app

    front = owner_router.TrustedFront()
    front.app("/app/x", "POST")
    front.app("/app/any")
    for route in (Special("/app/x", endpoint, methods=["GET", "POST"]),
                  Route("/app/x", endpoint, methods=["GET", "POST"],
                        middleware=[Middleware(Wrap)])):
        with pytest.raises(ValueError, match="cannot be narrowed"):
            front.box_routes([route])
    # A method-less route or mount goes whole or not at all, never on one method.
    with pytest.raises(ValueError, match="any method"):
        front.box_routes([Route("/app/x", asgi)])
    with pytest.raises(ValueError, match="any method"):
        front.box_routes([Mount("/app/x", app=asgi)])
    kept = front.box_routes([Route("/app/any", asgi), Route("/app/free", asgi),
                             Special("/app/any", endpoint, methods=["GET"])])
    assert [route.path for route in kept] == ["/app/free"]


@pytest.mark.verifies("owner-daemon-in-box:The router only authenticates, maps and forwards")
def test_the_router_answers_app_trace_and_slashes_while_the_box_sleeps(
        tmp_path, monkeypatch, caplog, cloud_runtime):  # noqa: F811
    import logging

    from starlette.testclient import TestClient

    from tinyassets import onboarding, router_traffic, universe_server
    from tinyassets.auth import middleware
    from tinyassets.broker import owner_identities

    def asleep(*args, **kwargs):
        raise AssertionError("the router reached a box for /app/trace")

    for name in ("forward", "box_result"):
        monkeypatch.setattr(owner_router, name, asleep)
    for name in ("wake_owner", "admit_owner"):
        monkeypatch.setattr(owner_identities, name, asleep)

    class Provider:
        def resolve_token(self, token):
            return Identity(user_id="alice", username="alice") if token == "alice" else None

    monkeypatch.setattr(middleware, "_provider", Provider())
    monkeypatch.setattr(onboarding, "_trace_buckets", {})
    monkeypatch.setattr(onboarding, "_TRACE_BUCKET_MAX", 1)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    (tmp_path / router_traffic.NAME).write_text(json.dumps({"ordinary": True}))
    bearer = {"authorization": "Bearer alice"}
    step = {"step": "voice_output_mismatch", "detail": "expected_chars=3\nactual_chars=2"}
    with TestClient(universe_server.create_streamable_http_app()) as client:
        with caplog.at_level(logging.WARNING, logger="tinyassets.onboarding"):
            ok = client.post("/app/trace", headers=bearer, json=step)
        assert (ok.status_code, ok.json()) == (200, {"ok": True})
        assert ok.headers["cache-control"] == "no-store"
        assert ("app-trace user=alice step=voice_output_mismatch "
                "detail=expected_chars=3 actual_chars=2") in caplog.text
        limited = client.post("/app/trace", headers=bearer, json=step)
        assert (limited.status_code, limited.json()) == (429, {"error": "rate_limited"})
        unknown = client.post("/app/trace", headers=bearer, json={"step": "other"})
        assert (unknown.status_code, unknown.json()) == (400, {"error": "unknown_step"})
        garbled = client.post("/app/trace", headers=bearer, content=b"not json")
        assert (garbled.status_code, garbled.json()) == (400, {"error": "invalid_json"})
        anonymous = client.post("/app/trace", json=step)
        assert anonymous.status_code == 401 and "www-authenticate" in anonymous.headers
        assert anonymous.json()["error"] == "authentication_required"
        # The box redirected an exact app path spelled with a trailing slash
        # (Starlette's redirect_slashes); the router's own table does the same.
        for method, path, exact in (("GET", "/app/?x=1", "/app?x=1"),
                                    ("POST", "/app/owner/restore/?a=b", "/app/owner/restore?a=b"),
                                    ("GET", "/app/api/policy/", "/app/api/policy")):
            moved = client.request(method, path, headers=bearer, follow_redirects=False)
            assert moved.status_code == 307, path
            assert moved.headers["location"] == "http://testserver" + exact
    # Only an exact /app registration: a subtree already covers its slash, and
    # the /api registrations never had a box route to redirect.
    assert not owner_router.TRUSTED._slashed("/api/mailbox/", "GET")
    assert not owner_router.TRUSTED._slashed("/app/trace/", "GET")
    assert owner_router.TRUSTED._slashed("/app/trace/", "POST")
