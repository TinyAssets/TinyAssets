"""Current wire semantics and host-bound URL continuation, without providers."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from tinyassets.mcp_remote import Binding, McpError, RemoteEndpoint, RemoteMcp, SignInRequired
from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome

VERSION = "2026-07-28"
PREFIX = "io.modelcontextprotocol/"
BINDING = Binding("grant", "connection", "incarnation", RemoteEndpoint("https://example.com/mcp"))
TOOLS = [{"name": "write", "inputSchema": {"type": "object"}}]


class Broker:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []
        self.closed = 0

    @asynccontextmanager
    async def stream(self, **call):
        self.calls.append(call)
        doc = json.loads(call["request"]["body"])
        status, headers, result = next(self.replies)
        payload = {"jsonrpc": "2.0", "id": doc["id"], **result}
        raw = json.dumps(payload).encode()
        if headers.get("Content-Type") == "text/event-stream":
            raw = b":keepalive\n\ndata: " + raw + b"\n\n"

        class Stream:
            admitted = True

            async def head(self):
                return {"status": status, "headers": headers}

            async def body(self):
                for index in range(0, len(raw), 3):
                    yield raw[index:index + 3]

        try:
            yield Stream()
        finally:
            self.closed += 1


def reply(result, sse=False):
    kind = "text/event-stream" if sse else "application/json"
    return 200, {"Content-Type": kind}, {"result": result}


def remote(broker, **kwargs):
    return RemoteMcp(broker, BINDING, check_authority=lambda _: None, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [False, True])
async def test_modern_discovery_has_metadata_without_initialize_or_session(sse):
    broker = Broker([reply({"tools": TOOLS}, sse)])
    client = remote(broker)
    assert await client.discover() == TOOLS
    call = broker.calls[0]["request"]
    doc = json.loads(call["body"])
    assert doc["method"] == "tools/list"
    assert doc["params"]["_meta"][PREFIX + "protocolVersion"] == VERSION
    assert call["headers"]["MCP-Protocol-Version"] == VERSION
    assert call["headers"]["Mcp-Method"] == "tools/list"
    assert "MCP-Session-Id" not in call["headers"]


@pytest.mark.asyncio
async def test_challenge_is_structured_and_not_exception_text():
    challenge = 'Bearer resource_metadata="https://example.com/.well-known/oauth-protected-resource"'
    broker = Broker([(401, {"WWW-Authenticate": challenge}, {})])
    with pytest.raises(SignInRequired) as caught:
        await remote(broker).discover()
    assert caught.value.status == 401
    assert caught.value.www_authenticate == challenge
    assert challenge not in str(caught.value)
    assert len(broker.calls) == 1


@pytest.mark.asyncio
async def test_url_consent_resumes_only_the_original_operation():
    needed = {"resultType": "input_required", "requestState": "opaque-state",
              "inputRequests": {"login": {"method": "elicitation/create", "params": {
                  "mode": "url", "url": "https://example.com/login", "message": "Sign in"}}}}
    broker = Broker([reply({"tools": TOOLS}), reply(needed, True),
                     reply({"content": [{"type": "text", "text": "done"}]})])
    seen = []

    async def consent(context):
        seen.append(context)
        assert context.binding == BINDING
        assert context.op_id == "original"
        return "accept"

    client = remote(broker, elicit_url=consent)
    await client.discover()
    result = await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="original")
    assert result["content"][0]["text"] == "done"
    assert len(seen) == 1
    first, resumed = [json.loads(c["request"]["body"]) for c in broker.calls[1:]]
    assert first["id"] != resumed["id"]
    assert resumed["params"]["requestState"] == "opaque-state"
    assert resumed["params"]["inputResponses"] == {"login": {"action": "accept"}}
    assert resumed["params"]["name"] == first["params"]["name"]
    assert broker.calls[1]["op_id"] != broker.calls[2]["op_id"]


@pytest.mark.asyncio
async def test_cancelled_host_does_not_continue():
    needed = {"resultType": "input_required", "inputRequests": {"a": {
        "method": "elicitation/create", "params": {"mode": "url",
        "url": "https://example.com/login", "message": "Sign in"}}}}
    broker = Broker([reply({"tools": TOOLS}), reply(needed)])

    async def consent(_):
        raise asyncio.CancelledError

    client = remote(broker, elicit_url=consent)
    await client.discover()
    with pytest.raises(asyncio.CancelledError):
        await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="stop")
    assert len(broker.calls) == 2
    assert broker.closed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-32020, -32021, -32022, -32601])
async def test_recognized_modern_error_does_not_blindly_initialize(code):
    broker = Broker([(400, {"Content-Type": "application/json"}, {"error": {
        "code": code, "message": "not allowed", "data": {"supported": ["2099-01-01"]}}})])
    with pytest.raises(McpError):
        await remote(broker).discover()
    assert len(broker.calls) == 1
    assert json.loads(broker.calls[0]["request"]["body"])["method"] == "tools/list"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 404, 408, 429, 500])
async def test_current_tool_failure_never_replays(status):
    broker = Broker([reply({"tools": TOOLS}), (status, {"Content-Type": "application/json"}, {})])
    client = remote(broker)
    await client.discover()
    with pytest.raises(AmbiguousProxyOutcome):
        await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one")
    assert len(broker.calls) == 2


def needed(url="https://example.com/login", state="state"):
    return {"resultType": "input_required", "requestState": state,
            "inputRequests": {"login": {"method": "elicitation/create", "params": {
                "mode": "url", "url": url, "message": "Sign in"}}}}


@pytest.mark.asyncio
async def test_revoked_authority_after_consent_blocks_continuation():
    broker = Broker([reply({"tools": TOOLS}), reply(needed())])
    revoked = False

    def check(_):
        if revoked:
            raise PermissionError("revoked")

    async def consent(_):
        nonlocal revoked
        revoked = True
        return "accept"

    client = RemoteMcp(broker, BINDING, check_authority=check, elicit_url=consent)
    await client.discover()
    with pytest.raises(PermissionError):
        await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one")
    assert len(broker.calls) == 2


@pytest.mark.asyncio
async def test_repeated_url_uses_one_consent_but_new_url_needs_consent(monkeypatch):
    from tinyassets import mcp_protocol

    delays = []

    async def pause(seconds):
        delays.append(seconds)

    monkeypatch.setattr(mcp_protocol.asyncio, "sleep", pause)
    broker = Broker([reply({"tools": TOOLS}), reply(needed()), reply(needed(state="next")),
                     reply(needed(url="https://other.example/login", state="changed")),
                     reply({"content": []})])
    seen = []

    async def consent(context):
        seen.append(context.url)
        return "accept"

    client = remote(broker, elicit_url=consent)
    await client.discover()
    assert await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one") == {
        "content": []}
    assert seen == ["https://example.com/login", "https://other.example/login"]
    assert delays == [1]
    assert [json.loads(c["request"]["body"])["params"].get("requestState")
            for c in broker.calls[2:]] == ["state", "next", "changed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://example.com/login",
                                 "https://user:pass@example.com/", "https://example.com/\nfoo"])
async def test_unsafe_url_is_never_presented_or_followed(url):
    broker = Broker([reply({"tools": TOOLS}), reply(needed(url=url))])

    async def consent(_):
        pytest.fail("unsafe URL reached protected host")

    client = remote(broker, elicit_url=consent)
    await client.discover()
    with pytest.raises(McpError, match="unsafe"):
        await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one")
    assert len(broker.calls) == 2


@pytest.mark.asyncio
async def test_continuation_lost_response_is_not_replayed():
    broker = Broker([reply({"tools": TOOLS}), reply(needed()),
                     (500, {"Content-Type": "application/json"}, {})])

    async def consent(_):
        return "accept"

    client = remote(broker, elicit_url=consent)
    await client.discover()
    with pytest.raises(AmbiguousProxyOutcome) as caught:
        await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one")
    assert len(broker.calls) == 3
    assert caught.value.op_id == broker.calls[-1]["op_id"]


@pytest.mark.asyncio
async def test_parameter_headers_are_encoded_and_invalid_tools_excluded():
    schema = {"type": "object", "properties": {"region": {"type": "string",
              "x-mcp-header": "Region"}}}
    bad = {"name": "bad", "inputSchema": {"type": "object", "allOf": [schema]}}
    tools = [{"name": "write", "inputSchema": schema}, bad]
    broker = Broker([reply({"tools": tools}), reply({"content": []})])
    client = remote(broker)
    assert await client.discover() == tools[:1]
    await client.call("write", {"region": "\r\nnot-a-header"},
                      catalog_hash=client.catalog_hash, op_id="one")
    assert broker.calls[-1]["request"]["headers"]["Mcp-Param-Region"] == (
        "=?base64?DQpub3QtYS1oZWFkZXI=?=")
    assert broker.calls[-1]["request"]["headers"]["Mcp-Name"] == "write"


@pytest.mark.asyncio
async def test_modern_cancel_closes_stream_without_notification():
    broker = Broker([reply({"tools": TOOLS}), reply({"content": []}, True)])
    client = remote(broker)
    await client.discover()
    original = broker.stream

    @asynccontextmanager
    async def stream(**kwargs):
        async with original(**kwargs) as connection:
            async def body():
                yield b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                raise AssertionError("cancel should close before requesting next bytes")
            connection.body = body
            yield connection

    broker.stream = stream

    async def stop(_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one", notify=stop)
    assert len(broker.calls) == 2
    assert broker.closed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18", "2025-03-26"])
async def test_legacy_versions_still_negotiate(version):
    from tests.test_mcp_remote import TOOLS, FakeBroker

    broker = FakeBroker()
    broker.version = version
    client = remote(broker)
    assert await client.discover() == TOOLS
    assert client._version == version
    assert json.loads(broker.calls[0]["request"]["body"])["method"] == "tools/list"
    assert json.loads(broker.calls[1]["request"]["body"])["method"] == "initialize"


@pytest.mark.asyncio
async def test_final_current_sse_response_does_not_wait_for_server_close():
    broker = Broker([reply({"tools": TOOLS})])
    client = remote(broker)
    await client.discover()
    closed = []

    @asynccontextmanager
    async def stream(**kwargs):
        doc = json.loads(kwargs["request"]["body"])

        class Stream:
            async def head(self):
                return {"status": 200, "headers": {"Content-Type": "text/event-stream"}}

            async def body(self):
                payload = {"jsonrpc": "2.0", "id": doc["id"], "result": {"content": []}}
                yield b"data: " + json.dumps(payload).encode() + b"\n\n"
                pytest.fail("client waited past final result")

        try:
            yield Stream()
        finally:
            closed.append(True)

    broker.stream = stream
    assert await client.call("write", {}, catalog_hash=client.catalog_hash, op_id="one") == {
        "content": []}
    assert closed == [True]


@pytest.mark.asyncio
async def test_sse_cr_only_final_delimiter_is_delivered():
    from tinyassets.mcp_remote import messages

    async def chunks():
        yield b'data: {"jsonrpc":"2.0","id":1,"result":{}}\r\r'

    assert [m async for m in messages(chunks(), "text/event-stream")] == [
        {"jsonrpc": "2.0", "id": 1, "result": {}}]
