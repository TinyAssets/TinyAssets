"""Synthetic private MCP transport; never contacts the owner's engine or providers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from tests.engine_authority_helpers import seed_engine_authority
from tinyassets import engine_mcp_http as routes
from tinyassets import engine_tool_client as subject
from tinyassets.storage import DB_FILENAME


def _tool(name="read_graph", **changes):
    return Tool(name=name, inputSchema={"type": "object"}, **changes)


@pytest.fixture
def route(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    seed_engine_authority(tmp_path)
    server = SimpleNamespace(universe_id="u-a", owner="actor-a", port=8790, secret="s" * 43)
    routes._write_routes(tmp_path, [server])
    return tmp_path, server


def _open(**kwargs):
    return subject.open_engine_tools(
        actor_id="actor-a",
        graph_id="u-a",
        enabled_tools=["read_graph"],
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["read_graph", [], ["bash"], ["read_graph"] * 2,
                                  ["read_graph", "unknown"], [{}], 42])
async def test_invalid_backend_grant_never_connects(fake, grant):
    with pytest.raises(subject.EngineToolError, match="invalid_grant"):
        async with _open(capability_grant=grant):
            pytest.fail("invalid backend grant entered")
    assert fake.lists == fake.calls == []


@pytest.mark.asyncio
async def test_model_inventory_can_be_narrower_than_signed_backend_grant(route, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES, verified_launch_grant

    root, server = route
    server.grant_key = "k" * 43
    routes._write_routes(root, [server])
    client = _FakeClient()
    client.pages = [ListToolsResult(tools=[_tool(name) for name in BACKEND_ENGINE_CAPABILITIES])]
    dialled = []

    def make(route, timeout):
        dialled.append(route)
        return client

    monkeypatch.setattr(subject, "_make_client", make)
    async with subject.open_engine_tools(
        actor_id="actor-a", graph_id="u-a", enabled_tools=["bash"],
        capability_grant=BACKEND_ENGINE_CAPABILITIES, session_key="thread:owner", turn="turn",
    ) as session:
        assert [tool.name for tool in session.tools] == ["bash"]
        with pytest.raises(subject.EngineToolError, match="not_allowed"):
            await session.call("write_graph", {})
        params = parse_qs(urlsplit(dialled[0].url).query)
        assert verified_launch_grant(server.grant_key, "thread:owner", "turn",
                                     params["grant"][0]) == BACKEND_ENGINE_CAPABILITIES
    assert client.closed


class _FakeClient:
    def __init__(self):
        self.pages = [ListToolsResult(tools=[_tool()])]
        self.calls = []
        self.lists = []
        self.closed = False
        self.close_error = False
        self.failure = None
        self.result = CallToolResult(
            content=[TextContent(type="text", text="exact text")],
            structuredContent={"x": 1},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True
        if self.close_error:
            raise RuntimeError("fixture secret must not escape cleanup")

    def is_connected(self):
        return not self.closed

    async def list_tools_mcp(self, *, cursor=None):
        self.lists.append(cursor)
        return self.pages[min(len(self.lists) - 1, len(self.pages) - 1)]

    async def call_tool_mcp(self, name, arguments):
        self.calls.append((name, arguments))
        if self.failure:
            raise self.failure
        return self.result


@pytest.fixture
def fake(monkeypatch, route):
    client = _FakeClient()
    monkeypatch.setattr(subject, "_make_client", lambda *_: client)
    return client


@pytest.mark.asyncio
async def test_exact_result_and_schema_copy(fake):
    async with _open() as session:
        displayed = session.tools
        displayed[0].name = "source_channel"
        assert session.tools[0].name == "read_graph"
        assert await session.call("read_graph", {"opaque": "雪\nverbatim"}) is fake.result
    assert session.cleanup_status == "closed"
    assert fake.calls == [("read_graph", {"opaque": "雪\nverbatim"})]
    with pytest.raises(subject.EngineToolError, match="closed"):
        await session.call("read_graph", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "secret", "port", "disabled", "revoked", "missing"])
async def test_changed_route_refuses_without_dispatch(fake, route, monkeypatch, change):
    root, server = route
    async with _open() as session:
        if change in ("owner", "secret", "port"):
            setattr(server, change, {"owner": "actor-b", "secret": "t" * 43, "port": 8791}[change])
            routes._write_routes(root, [server])
        elif change == "disabled":
            monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
        elif change == "revoked":
            with sqlite3.connect(root / DB_FILENAME) as conn:
                conn.execute("DELETE FROM universe_acl")
        else:
            (root / routes.ROUTES_FILENAME).unlink()
        with pytest.raises(subject.EngineToolError) as caught:
            await session.call("read_graph", {})
        assert caught.value.outcome == "not_sent"
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection", [None, "read_graph", [], ["no_such_tool"], ["read_graph"] * 2, [{}]]
)
async def test_invalid_selection_never_connects(fake, selection):
    with pytest.raises(subject.EngineToolError, match="invalid_selection"):
        async with subject.open_engine_tools(
            actor_id="actor-a",
            graph_id="u-a",
            enabled_tools=selection,
        ):
            pytest.fail("invalid selection entered")
    assert fake.lists == fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, 0, -1, float("inf"), float("nan"), "10"])
async def test_invalid_timeout_refuses(fake, timeout):
    with pytest.raises(subject.EngineToolError, match="invalid_timeout"):
        async with _open(timeout=timeout):
            pytest.fail("invalid timeout entered")
    assert fake.lists == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["duplicate", "missing", "loop", "incomplete", "bad_schema", "remote_ref"]
)
async def test_incomplete_or_invalid_inventory_is_not_readiness(fake, kind):
    if kind == "duplicate":
        fake.pages = [ListToolsResult(tools=[_tool(), _tool()])]
    elif kind == "missing":
        fake.pages = [ListToolsResult(tools=[_tool("get_status")])]
    elif kind == "loop":
        fake.pages = [ListToolsResult(tools=[], nextCursor="again")]
    elif kind == "incomplete":
        fake.pages = [ListToolsResult(tools=[], nextCursor=str(i)) for i in range(32)]
    elif kind == "bad_schema":
        fake.pages[0].tools[0].inputSchema = {"type": "object", "required": "invalid"}
    else:
        fake.pages[0].tools[0].outputSchema = {"$ref": "https://untrusted.example/schema"}
    with pytest.raises(subject.EngineToolError):
        async with _open():
            pytest.fail("incomplete inventory entered")
    assert fake.closed and not fake.calls


@pytest.mark.asyncio
async def test_same_session_pagination_and_enabled_subset(fake):
    fake.pages = [
        ListToolsResult(tools=[_tool("source_channel")], nextCursor="next"),
        ListToolsResult(tools=[_tool()]),
    ]
    async with _open() as session:
        assert [tool.name for tool in session.tools] == ["read_graph"]
        with pytest.raises(subject.EngineToolError, match="not_allowed"):
            await session.call("source_channel", {})
        await session.call("read_graph", {})
    assert fake.lists == [None, "next"]
    assert len(fake.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("received but invalid"), ValueError("secret")])
async def test_dispatch_exception_is_unknown_and_never_retried(fake, failure):
    fake.failure = failure
    async with _open() as session:
        with pytest.raises(subject.EngineToolError) as caught:
            await session.call("read_graph", {})
        with pytest.raises(subject.EngineToolError, match="closed"):
            await session.call("read_graph", {})
    assert caught.value.outcome == "unknown"
    assert str(caught.value) == "engine_tool_outcome_unknown"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_tool_error_is_a_received_result_and_cleanup_cannot_replace_it(fake):
    fake.result.isError = True
    fake.close_error = True
    async with _open() as session:
        result = await session.call("read_graph", {})
    assert result is fake.result and result.isError
    assert session.cleanup_status == "failed"


@pytest.mark.asyncio
async def test_foreign_owner_cannot_create_a_client(monkeypatch, route):
    made = []
    monkeypatch.setattr(subject, "_make_client", lambda *_: made.append(True))
    with pytest.raises(subject.EngineToolError, match="unavailable"):
        async with subject.open_engine_tools(
            actor_id="actor-b",
            graph_id="u-a",
            enabled_tools=["read_graph"],
        ):
            pytest.fail("foreign owner entered")
    assert made == []


@pytest.mark.asyncio
async def test_route_change_during_discovery_refuses_readiness(fake, route, monkeypatch):
    original = fake.list_tools_mcp

    async def change(*, cursor=None):
        route[1].secret = "t" * 43
        routes._write_routes(route[0], [route[1]])
        return await original(cursor=cursor)

    monkeypatch.setattr(fake, "list_tools_mcp", change)
    with pytest.raises(subject.EngineToolError, match="route_changed"):
        async with _open():
            pytest.fail("changed route entered")
    assert fake.closed and not fake.calls


@pytest.mark.asyncio
async def test_connect_failure_is_not_sent_and_cleanup_is_attempted(fake, monkeypatch):
    async def fail(_self):
        raise RuntimeError("private fixture failure")

    monkeypatch.setattr(_FakeClient, "__aenter__", fail)
    with pytest.raises(subject.EngineToolError) as caught:
        async with _open():
            pytest.fail("failed connection entered")
    assert caught.value.outcome == "not_sent"
    assert str(caught.value) == "engine_tools_unavailable"
    assert fake.closed and not fake.calls


@pytest.mark.asyncio
async def test_pending_cleanup_is_explicit_and_does_not_mask_body_error(fake, monkeypatch):
    monkeypatch.setattr(fake, "is_connected", lambda: True)
    with pytest.raises(ValueError, match="caller error"):
        async with _open() as session:
            raise ValueError("caller error")
    assert session.cleanup_status == "pending"


@pytest.mark.asyncio
async def test_server_log_content_is_not_logged(caplog):
    await subject._ignore_server_log({"data": "private notification text"})
    assert "private notification text" not in caplog.text


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
def test_schema_references_cannot_leave_the_document(keyword):
    assert not subject._local_schema_references(
        {"properties": {"x": {keyword: "https://elsewhere"}}}
    )
    assert subject._local_schema_references({"properties": {"x": {keyword: "#/$defs/x"}}})


def test_private_factory_drops_every_ambient_option(monkeypatch, route):
    from fastmcp.client.transports import StreamableHttpTransport

    private_route = routes.read_engine_mcp_route(actor_id="actor-a", graph_id="u-a")
    client = subject._make_client(private_route, 17)
    assert isinstance(client.transport, StreamableHttpTransport)
    captured = {}
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: captured.update(kwargs))
    client.transport.httpx_client_factory(
        headers={"authorization": "outer bearer", "cookie": "outer cookie"},
        auth="outer auth",
        follow_redirects=True,
        timeout=900,
        unknown="ignored",
    )
    assert captured["headers"] == {"Authorization": "Bearer " + "s" * 43}
    assert captured["auth"] is None
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["timeout"].read == 17
    assert captured["timeout"].connect == 30
    assert "s" * 43 not in repr(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["ok", "tool_error", "invalid_result", "session_gone", "lost", "cancel", "redirect"]
)
async def test_real_mcp_protocol_preserves_results_and_does_not_repost(monkeypatch, route, kind):
    """Real FastMCP/MCP session over synthetic HTTP, not a fake call_tool method."""
    requests = []
    started = asyncio.Event()
    real_client = httpx.AsyncClient
    output_schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }

    async def endpoint(request):
        assert str(request.url).startswith("http://127.0.0.1:8790/mcp")
        assert request.headers["authorization"] == "Bearer " + "s" * 43
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        method = body["method"]
        requests.append(method)
        if method == "initialize":
            assert not set(body["params"].get("capabilities", {})) & {
                "sampling",
                "roots",
                "elicitation",
            }
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-engine", "version": "1"},
            }
        elif method == "notifications/initialized":
            return httpx.Response(202)
        elif method == "tools/list":
            result = {
                "tools": [
                    _tool(outputSchema=output_schema).model_dump(by_alias=True, exclude_none=True)
                ]
            }
        elif method == "tools/call":
            started.set()
            if kind == "session_gone":
                return httpx.Response(404)
            if kind == "lost":
                raise httpx.ReadError("synthetic disconnect")
            if kind == "redirect":
                return httpx.Response(307, headers={"location": "https://untrusted.example/mcp"})
            if kind == "cancel":
                await asyncio.Event().wait()
            result = {
                "content": [{"type": "text", "text": "exact result"}],
                "isError": kind == "tool_error",
            }
            if kind != "invalid_result":
                result["structuredContent"] = {"value": 7}
        else:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(endpoint),
        ),
    )
    async with _open(timeout=2) as session:
        if kind == "cancel":
            task = asyncio.create_task(session.call("read_graph", {"query": "hello"}))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(subject.EngineToolError, match="closed"):
                await session.call("read_graph", {})
        elif kind in ("invalid_result", "session_gone", "lost", "redirect"):
            with pytest.raises(subject.EngineToolError) as caught:
                await session.call("read_graph", {"query": "hello"})
            assert caught.value.outcome == "unknown"
            with pytest.raises(subject.EngineToolError, match="closed"):
                await session.call("read_graph", {})
        else:
            result = await session.call("read_graph", {"query": "hello"})
            assert result.structuredContent == {"value": 7}
            assert result.content[0].text == "exact result"
            assert result.isError == (kind == "tool_error")
    assert requests.count("tools/list") == 1
    assert requests.count("tools/call") == 1
