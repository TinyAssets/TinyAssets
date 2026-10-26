"""MCP 2026-07-28 at both doors: the box's adapter, and the router's subscription door."""

import asyncio
import json

import httpx
import pytest
from fastmcp import FastMCP

from tests.test_mcp_events import SECRET, params
from tinyassets.auth import middleware as auth
from tinyassets.auth.provider import Identity
from tinyassets.mcp_browser_cors import McpBrowserCors
from tinyassets.mcp_event_webhooks import EventError
from tinyassets.mcp_events_http import VERSION, MCPEventsMiddleware, door, routed


def request(method, **params):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": VERSION,
                "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            **params,
        },
    }


def headers(method, *, owner="alice", name=None):
    return {
        "authorization": f"Bearer {owner}",
        "mcp-protocol-version": VERSION,
        "mcp-method": method,
        "accept": "application/json,text/event-stream",
        **({"mcp-name": name} if name else {}),
    }


@pytest.mark.verifies("native-mcp-events:Modern envelopes keep legacy compatibility")
@pytest.mark.asyncio
async def test_real_fastmcp_stateless_and_legacy_transports(monkeypatch):
    mcp = FastMCP("test")

    @mcp.tool
    def who():
        identity = auth.current_mcp_message_identity()
        return {"owner": identity.user_id}

    class Provider:
        def resolve_token(self, token):
            return (
                Identity(token, token, capabilities=["read", "list"])
                if token in {"alice", "bob"}
                else None
            )

    monkeypatch.setattr(auth, "_get_provider", lambda: Provider())
    legacy = mcp.http_app(path="/mcp")
    modern = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    app = McpBrowserCors(auth.AuthContextMiddleware(MCPEventsMiddleware(legacy, modern)))
    async with (
        asyncio.timeout(15),
        legacy.router.lifespan_context(legacy),
        modern.router.lifespan_context(modern),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io"
        ) as c:
            discovery = await c.post(
                "/mcp", headers=headers("server/discover"), json=request("server/discover")
            )
            assert discovery.status_code == 200, discovery.text
            assert "events" in discovery.json()["result"]["capabilities"]
            result = discovery.json()["result"]
            # DiscoverResult's required fields in the official 2026-07-28 schema:
            # https://modelcontextprotocol.io/specification/2026-07-28/server/discover
            assert {
                "cacheScope", "capabilities", "resultType", "supportedVersions", "ttlMs",
            } <= result.keys()
            assert result["resultType"] == "complete"
            assert type(result["ttlMs"]) is int and result["ttlMs"] == 0
            assert result["cacheScope"] == "private"
            assert VERSION in result["supportedVersions"]
            assert result["_meta"]["io.modelcontextprotocol/serverInfo"] == {
                "name": "TinyAssets", "version": "0.1.0",
            }
            assert "serverInfo" not in result
            assert discovery.headers["cache-control"] == "no-store"
            catalog = await c.post(
                "/mcp", headers=headers("events/list"), json=request("events/list")
            )
            assert catalog.status_code == 200
            assert catalog.json()["result"]["events"][0]["name"] == "workflow.succeeded"
            listed = await c.post("/mcp", headers=headers("tools/list"), json=request("tools/list"))
            assert listed.status_code == 200, listed.text
            assert listed.json()["result"]["tools"][0]["name"] == "who"
            assert listed.json()["result"]["cacheScope"] == "private"
            for owner in ("alice", "bob", "alice"):
                answer = await c.post(
                    "/mcp/",
                    headers=headers("tools/call", owner=owner, name="who"),
                    json=request("tools/call", name="who", arguments={}),
                )
                assert answer.status_code == 200, answer.text
                result = answer.json()["result"]
                assert result["resultType"] == "complete"
                assert result["structuredContent"] == {"owner": owner}
                assert "mcp-session-id" not in answer.headers
            unauthorized = await c.post(
                "/mcp", headers=headers("events/list", owner="bad"), json=request("events/list")
            )
            assert unauthorized.status_code == 401
            origin = await c.post(
                "/mcp",
                headers=headers("events/list") | {"origin": "https://evil.example"},
                json=request("events/list"),
            )
            assert origin.status_code == 403
            # Real legacy initialize still succeeds and receives its session.
            old = await c.post(
                "/mcp",
                headers={
                    "authorization": "Bearer alice",
                    "accept": "application/json,text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "old", "version": "1"},
                    },
                },
            )
            assert old.status_code == 200, old.text
            assert old.headers["mcp-session-id"]


async def forbidden(*args):
    pytest.fail("request reached a dispatcher")


async def post_box(method, identity=Identity("alice", "alice"), change=None, **params):
    app = MCPEventsMiddleware(forbidden, forbidden)
    with auth.identity_context(identity):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io"
        ) as c:
            return await c.post("/mcp", headers=headers(method, name="who") | (change or {}),
                                json=request(method, **params))


@pytest.mark.verifies("native-mcp-events:Modern envelopes keep legacy compatibility")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,change,expected",
    [
        ("events/list", {"mcp-method": "tools/call"}, -32020),
        ("tools/call", {"mcp-name": "other"}, -32020),
        ("events/list", {"mcp-protocol-version": "2099-01-01"}, -32022),
        ("events/poll", {}, -32601),
    ],
)
async def test_protocol_validation_precedes_dispatch(method, change, expected):
    response = await post_box(method, change=change, name="who")
    assert response.json()["error"]["code"] == expected


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_box_subscribe_only_authorizes_the_verified_caller(monkeypatch):
    observed = []

    def authorize(base, owner, args, authority):
        observed.append((owner, args, authority))

    monkeypatch.setattr("tinyassets.mcp_events_http.authorize", authorize)
    # The router's verified identity, as the box's auth layer admitted it.
    identity = Identity("alice", "alice", metadata={"outside_origin": {"client": "c"}})
    monkeypatch.setattr(auth, "current_identity", lambda: identity)
    document = {k: v for k, v in params().items() if k != "delivery"}
    response = await post_box("events/subscribe", owner="bob", **document)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"authorized": True, "authority": {"client": "c"},
                                         "resultType": "complete"}
    assert observed == [("alice", params()["arguments"], {"client": "c"})]


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_box_refuses_a_delivery_block_and_unsubscribe(monkeypatch):
    monkeypatch.setattr("tinyassets.mcp_events_http.authorize",
                        lambda *a: pytest.fail("authorized a request carrying a callback"))
    response = await post_box("events/subscribe", **params())
    assert response.json()["error"]["code"] == -32602
    response = await post_box("events/unsubscribe", **params())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_box_authorization_refusal_is_bounded(monkeypatch):
    def refused(*args):
        raise PermissionError("private detail")

    monkeypatch.setattr("tinyassets.mcp_events_http.authorize", refused)
    document = {k: v for k, v in params().items() if k != "delivery"}
    response = await post_box("events/subscribe", **document)
    assert response.status_code == 403
    assert response.json()["error"] == {"code": -32024, "message": "Event access unavailable"}


@pytest.mark.verifies("native-mcp-events:Modern envelopes keep legacy compatibility")
@pytest.mark.asyncio
async def test_forwarded_non_json_transport_error_keeps_original_status():
    async def transport_error(scope, receive, send):
        from starlette.responses import PlainTextResponse

        await PlainTextResponse("Not Acceptable", status_code=406)(scope, receive, send)

    app = MCPEventsMiddleware(forbidden, transport_error)
    with auth.identity_context(Identity("alice", "alice")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io"
        ) as client:
            answer = await client.post(
                "/mcp", headers=headers("tools/list"), json=request("tools/list"))
    assert answer.status_code == 406 and answer.text == "Not Acceptable"
    assert answer.headers["cache-control"] == "no-store"


# -- The router's door: subscriptions live in the broker ------------------------


@pytest.fixture
def router_door(monkeypatch):
    """``door`` with the box and the broker recorded; the identity is the router's."""
    asked, brokered = [], []
    state = {"box": {"authorized": True, "authority": None, "resultType": "complete"},
             "broker": {"id": "sub_x", "cursor": None, "refreshBefore": "later",
                        "truncated": False}}

    async def ask_box(document):
        asked.append(document)
        if isinstance(state["box"], Exception):
            raise state["box"]
        return state["box"]

    def broker(root, document):
        brokered.append(document)
        if isinstance(state["broker"], Exception):
            raise state["broker"]
        return dict(state["broker"])

    monkeypatch.setattr("tinyassets.broker.mcp_events.request", broker)

    async def post(method, identity=Identity("alice", "alice"), change=None, **params):
        async def app(scope, receive, send):
            await door(scope, receive, send, identity, ask_box)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io"
        ) as c:
            return await c.post("/mcp", headers=headers(method) | (change or {}),
                                json=request(method, **params))

    return post, asked, brokered, state


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_router_subscribes_from_the_verified_identity_not_the_body(router_door):
    post, asked, brokered, state = router_door
    verified = {"client": "c", "family": "f", "authenticated_at": 1}
    identity = Identity("alice", "alice", metadata={"outside_origin": verified})
    # The box admits that same client and stamps its grant generation.
    state["box"] = {"authorized": True, "authority": verified | {"generation": 3},
                    "resultType": "complete"}
    response = await post("events/subscribe", identity, owner="bob", principal="bob",
                          **params())
    assert response.status_code == 200, response.text
    assert response.json()["result"]["id"] == "sub_x"
    assert response.json()["result"]["resultType"] == "complete"
    # The box saw neither the callback URL nor the signing key.
    assert len(asked) == 1 and "delivery" not in asked[0]["params"]
    assert "receiver.example" not in json.dumps(asked) and SECRET not in json.dumps(asked)
    assert asked[0]["method"] == "events/subscribe"
    assert brokered == [{"action": "subscribe", "principal": "alice",
                         "authority": verified | {"generation": 3},
                         "params": request("events/subscribe", owner="bob", principal="bob",
                                           **params())["params"]}]


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_router_unsubscribe_never_wakes_the_box(router_door):
    post, asked, brokered, state = router_door
    state["broker"] = {}
    response = await post("events/unsubscribe", **params())
    assert response.json()["result"] == {"resultType": "complete"}
    assert asked == [] and brokered[0]["action"] == "unsubscribe"


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
@pytest.mark.parametrize("refusal,code,status", [
    (EventError(-32024, "Event access unavailable"), -32024, 403),
    (EventError(-32602, "Exact command center and workflow IDs are required"), -32602, 400),
    (RuntimeError("owner box answered 503"), -32603, 503),
])
async def test_a_box_refusal_stops_before_the_broker(router_door, refusal, code, status):
    post, _, brokered, state = router_door
    state["box"] = refusal
    response = await post("events/subscribe", **params())
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert brokered == []


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    {"authorized": "yes", "authority": None, "resultType": "complete"},
    {"authorized": True, "resultType": "complete"},
    # The box may add the grant generation, never swap in another client.
    {"authorized": True, "authority": {"client": "other", "family": "f", "authenticated_at": 1},
     "resultType": "complete"},
    {"authorized": True, "authority": {"client": "c", "family": "f", "authenticated_at": 1},
     "resultType": "complete"},
])
async def test_a_malformed_box_answer_fails_loudly(router_door, answer):
    post, _, brokered, state = router_door
    state["box"] = answer
    response = await post("events/subscribe", **params())  # the caller is the owner
    assert response.json()["error"]["code"] == -32603
    assert brokered == []


@pytest.mark.verifies("native-mcp-events:Callbacks leave only from the broker via owner egress")
@pytest.mark.asyncio
async def test_broker_refusal_is_relayed_bounded(router_door):
    post, _, _, state = router_door
    state["broker"] = EventError(-32015, "Callback verification failed",
                                 reason="challenge_failed")
    response = await post("events/subscribe", **params())
    assert response.json()["error"] == {"code": -32015, "message": "Callback verification failed",
                                         "data": {"reason": "challenge_failed"}}


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_router_validates_the_envelope_before_box_or_broker(router_door):
    post, asked, brokered, _ = router_door
    # The header routes it here; the body asks for something else.
    response = await post("tools/call", change={"mcp-method": "events/subscribe"}, **params())
    assert response.json()["error"]["code"] == -32020
    assert asked == [] and brokered == []


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
def test_only_modern_subscription_posts_are_routed():
    def scope(method="POST", path="/mcp", **head):
        return {"type": "http", "method": method, "path": path,
                "headers": [(k.encode(), v.encode()) for k, v in head.items()]}

    modern = {"mcp-protocol-version": VERSION}
    assert routed(scope(**modern, **{"mcp-method": "events/subscribe"}))
    assert routed(scope(path="/mcp/", **modern, **{"mcp-method": "events/unsubscribe"}))
    assert not routed(scope(**modern, **{"mcp-method": "events/list"}))
    assert not routed(scope(**{"mcp-protocol-version": "2025-11-25",
                               "mcp-method": "events/subscribe"}))
    assert not routed(scope(method="GET", **modern, **{"mcp-method": "events/subscribe"}))
    assert not routed(scope(path="/app/x", **modern, **{"mcp-method": "events/subscribe"}))


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
@pytest.mark.asyncio
async def test_box_result_forwards_one_composed_request(monkeypatch):
    from tinyassets import owner_router

    seen = {}

    async def forward(scope, receive, send, identity, route):
        message = await receive()
        seen.update(body=message["body"], headers=dict(scope["headers"]), identity=identity,
                    scope=scope)
        reply = state["reply"]
        await send({"type": "http.response.start", "status": reply[0], "headers": []})
        await send({"type": "http.response.body", "body": json.dumps(reply[1]).encode()})

    state = {"reply": (200, {"jsonrpc": "2.0", "id": 1, "result": {"authorized": True}})}
    monkeypatch.setattr(owner_router, "forward", forward)
    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"content-length", b"9999"), (b"mcp-method", b"events/subscribe")]}
    identity = Identity("alice", "alice")
    document = request("events/subscribe", name="workflow.succeeded")
    assert await owner_router.box_result(scope, identity, None, document) == {"authorized": True}
    assert json.loads(seen["body"]) == document
    assert seen["headers"][b"content-length"] == str(len(seen["body"])).encode()
    assert seen["identity"] is identity
    state["reply"] = (403, {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32024, "message": "Event access unavailable"}})
    with pytest.raises(EventError) as error:
        await owner_router.box_result(scope, identity, None, document)
    assert error.value.code == -32024
    state["reply"] = (503, {"error": "owner_box_unavailable"})
    with pytest.raises(RuntimeError):
        await owner_router.box_result(scope, identity, None, document)
    # The router reads a bounded answer under a read timeout.
    assert owner_router._forward_timeout(seen["scope"]).read == 30
    state["reply"] = (200, {"jsonrpc": "2.0", "id": 1, "result": {
        "authorized": True, "pad": "x" * owner_router.BOX_RESULT_BYTES}})
    with pytest.raises(RuntimeError):
        await owner_router.box_result(scope, identity, None, document)
    # The box's own auth refusing the caller is access, not an outage.
    state["reply"] = (403, {"error": "forbidden"})
    with pytest.raises(PermissionError):
        await owner_router.box_result(scope, identity, None, document)
