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
              b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}']
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
