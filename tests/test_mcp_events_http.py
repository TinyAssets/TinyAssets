import asyncio

import httpx
import pytest
from fastmcp import FastMCP

from tinyassets.auth import middleware as auth
from tinyassets.auth.provider import Identity
from tinyassets.mcp_browser_cors import McpBrowserCors
from tinyassets.mcp_events_http import VERSION, MCPEventsMiddleware


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
    async def forbidden(*args):
        pytest.fail("request reached legacy dispatcher")

    app = MCPEventsMiddleware(forbidden, forbidden)
    with auth.identity_context(Identity("alice", "alice")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io"
        ) as c:
            response = await c.post(
                "/mcp",
                headers=headers(method, name="who") | change,
                json=request(method, name="who"),
            )
    assert response.json()["error"]["code"] == expected


@pytest.mark.asyncio
async def test_subscribe_uses_request_owner_not_body():
    observed = []

    class Store:
        def subscribe(self, owner, params, *, authority):
            observed.append((owner, authority))
            return {"id": "sub", "cursor": None, "refreshBefore": "later", "truncated": False}

    async def forbidden(*args):
        pytest.fail("event reached legacy dispatcher")

    app = MCPEventsMiddleware(forbidden, forbidden, store_factory=Store)
    with auth.identity_context(Identity("alice", "alice")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io"
        ) as c:
            answer = await c.post(
                "/mcp",
                headers=headers("events/subscribe"),
                json=request("events/subscribe", owner="bob"),
            )
    assert answer.status_code == 200
    assert observed == [("alice", None)]


@pytest.mark.asyncio
async def test_forwarded_non_json_transport_error_keeps_original_status():
    async def forbidden(*args):
        pytest.fail("request reached legacy dispatcher")

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
