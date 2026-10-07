"""Protocol negotiation, streamed discovery and non-replayed tool outcomes."""
import json
from contextlib import asynccontextmanager

import pytest

from tinyassets.mcp_remote import (
    Binding,
    McpError,
    RemoteEndpoint,
    RemoteMcp,
    SessionExpired,
    messages,
    requires_approval,
)
from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome

TOOLS = [dict(name="read", inputSchema={"type": "object"}, annotations={"readOnlyHint": True}),
         dict(name="write", inputSchema={"type": "object"}, annotations={"destructiveHint": True})]


class FakeBroker:
    def __init__(self):
        self.calls = []
        self.expire = False
        self.incomplete = False
        self.version = "2025-06-18"
        self.closed = 0
        self.fail_status = None
        self.session = "opaque-session"
        self.bad_reply = False
        self.tools = json.loads(json.dumps(TOOLS))

    @asynccontextmanager
    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        request = json.loads(kwargs["request"]["body"])
        method = request["method"]
        status, session, content_type = 200, "", "application/json"
        if self.expire:
            self.expire = False
            status = 404
            result = {}
        elif method == "initialize":
            result = {"protocolVersion": self.version, "capabilities": {"tools": {}}}
            session = self.session
        elif method == "notifications/initialized":
            status, result = 202, None
        elif method == "tools/list":
            result = ({"tools": self.tools[:1], "nextCursor": "next"}
                      if not request["params"] else {"tools": self.tools[1:]})
        else:
            content_type = "text/event-stream"
            result = {"content": [{"type": "text", "text": "done"}]}
        raw = json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        if method == "tools/call":
            status = self.fail_status or status
            if self.bad_reply:
                raw = "invalid JSON"
        body = raw.encode() if content_type == "application/json" else (
            'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\r\n\r\n'
            + "data: " + raw + "\r\n\r\n").encode()
        if self.incomplete or status != 200:
            body = b""

        class Stream:
            async def head(self):
                return {"status": status, "headers": {"Content-Type": content_type,
                                                       "MCP-Session-Id": session}}

            async def body(self):
                for byte in body:
                    yield bytes([byte])
        try:
            yield Stream()
        finally:
            self.closed += 1


def client(broker, check=lambda _: None):
    return RemoteMcp(broker, Binding("g", "c", "i", RemoteEndpoint("https://example.com/mcp")),
                     check_authority=check)


@pytest.mark.asyncio
async def test_cancel_notifies_server_without_replaying_call():
    import asyncio

    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()

    async def stop(_):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await remote.call("write", {}, catalog_hash=remote.catalog_hash, op_id="cancel",
                          notify=stop)
    documents = [json.loads(call["request"]["body"]) for call in broker.calls]
    calls = [doc for doc in documents if doc["method"] == "tools/call"]
    assert len(calls) == 1
    assert documents[-1]["method"] == "notifications/cancelled"
    assert documents[-1]["params"]["requestId"] == calls[0]["id"]


@pytest.mark.asyncio
async def test_initialize_paginated_discovery_streamed_call():
    broker = FakeBroker()
    remote = client(broker)
    assert await remote.discover() == TOOLS
    progress = []

    async def notify(message):
        progress.append(message)

    assert await remote.call("read", {}, catalog_hash=remote.catalog_hash,
                             op_id="operation", notify=notify) == {
        "content": [{"type": "text", "text": "done"}]}
    assert progress[0]["method"] == "notifications/progress"
    assert broker.calls[-1]["op_id"] == "operation"
    assert broker.calls[-1]["request"]["headers"]["MCP-Session-Id"] == "opaque-session"
    assert broker.calls[-1]["request"]["headers"]["MCP-Protocol-Version"] == "2025-06-18"
    assert broker.closed == 5


@pytest.mark.asyncio
async def test_discovery_renews_but_tools_are_never_replayed():
    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()
    broker.expire = True
    assert await remote.discover() == TOOLS
    broker.expire = True
    with pytest.raises(SessionExpired):
        await remote.call("write", {}, catalog_hash=remote.catalog_hash, op_id="one")
    assert sum(json.loads(c["request"]["body"])["method"] == "tools/call"
               for c in broker.calls) == 1
    assert remote.catalog_hash == ""


@pytest.mark.asyncio
async def test_lost_result_is_uncertain_and_stale_catalog_does_not_send():
    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()
    with pytest.raises(McpError, match="stale"):
        await remote.call("write", {}, catalog_hash="old", op_id="one")
    assert len(broker.calls) == 4
    broker.incomplete = True
    with pytest.raises(AmbiguousProxyOutcome):
        await remote.call("write", {}, catalog_hash=remote.catalog_hash, op_id="two")
    assert len(broker.calls) == 5


@pytest.mark.asyncio
async def test_revocation_rechecked_before_every_call():
    broker = FakeBroker()
    revoked = False

    def check(_):
        if revoked:
            raise PermissionError("revoked")

    remote = client(broker, check)
    await remote.discover()
    revoked = True
    with pytest.raises(PermissionError):
        await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id="one")
    assert len(broker.calls) == 4


@pytest.mark.asyncio
async def test_unsupported_protocol_does_not_advertise_tools():
    broker = FakeBroker()
    broker.version = "2024-11-05"
    remote = client(broker)
    with pytest.raises(McpError, match="unsupported"):
        await remote.discover()
    assert not remote.catalog_hash
    assert len(broker.calls) == 1


@pytest.mark.asyncio
async def test_sse_multiline_and_utf8_across_chunks():
    payload = 'data: {"jsonrpc":"2.0",\ndata: "id":1,"result":{"text":"é"}}\n\n'.encode()

    async def chunks():
        for b in payload:
            yield bytes([b])

    result = [value async for value in messages(chunks(), "text/event-stream")]
    assert result == [{"jsonrpc": "2.0", "id": 1, "result": {"text": "é"}}]


@pytest.mark.parametrize("hints,expected", [({}, True), ({"readOnlyHint": True}, False),
    ({"readOnlyHint": "true"}, True), ({"readOnlyHint": True, "destructiveHint": True}, True)])
def test_annotations_supply_conservative_policy_input(hints, expected):
    assert requires_approval({"annotations": hints}) is expected


@pytest.mark.asyncio
async def test_server_schema_cannot_resolve_external_references():
    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()
    remote._tools["read"] = {"inputSchema": {"$ref": "https://metadata.internal/secret"}}
    with pytest.raises(McpError, match="arguments"):
        await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id="one")
    assert len(broker.calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 502])
async def test_post_send_http_failure_is_uncertain(status):
    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()
    broker.fail_status = status
    with pytest.raises(AmbiguousProxyOutcome):
        await remote.call("write", {}, catalog_hash=remote.catalog_hash, op_id="one")
    assert len(broker.calls) == 5


@pytest.mark.asyncio
async def test_post_send_invalid_message_or_authority_failure_is_uncertain():
    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()
    broker.bad_reply = True
    with pytest.raises(AmbiguousProxyOutcome):
        await remote.call("write", {}, catalog_hash=remote.catalog_hash, op_id="one")
    broker.bad_reply = False

    async def revoked(_):
        raise PermissionError("revoked during response")

    with pytest.raises(AmbiguousProxyOutcome):
        await remote.call("write", {}, catalog_hash=remote.catalog_hash,
                          op_id="two", notify=revoked)
    assert len(broker.calls) == 6


@pytest.mark.asyncio
async def test_auth_failure_invalidates_catalog_and_discovery_initializes_again():
    from tinyassets.mcp_remote import SignInRequired

    broker = FakeBroker()
    remote = client(broker)
    await remote.discover()
    old_hash = remote.catalog_hash
    broker.fail_status = 401
    with pytest.raises(SignInRequired):
        await remote.call("write", {}, catalog_hash=old_hash, op_id="one")
    assert not remote.catalog_hash and not remote._version and not remote._session
    with pytest.raises(McpError, match="stale"):
        await remote.call("write", {}, catalog_hash=old_hash, op_id="two")
    broker.fail_status = None
    assert await remote.discover() == TOOLS
    assert json.loads(broker.calls[5]["request"]["body"])["method"] == "initialize"


@pytest.mark.asyncio
async def test_short_session_id_does_not_reject_ordinary_results():
    broker = FakeBroker()
    broker.session = "1"
    remote = client(broker)
    assert await remote.discover() == TOOLS
    assert await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id="one")
    assert broker.calls[-1]["request"]["headers"]["MCP-Session-Id"] == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["pattern", "patternProperties"])
async def test_remote_regex_schema_is_refused_before_daemon_validation(keyword):
    broker = FakeBroker()
    broker.tools[0]["inputSchema"][keyword] = "(a+)+$" if keyword == "pattern" else {
        "(a+)+$": {"type": "string"}}
    remote = client(broker)
    with pytest.raises(McpError, match="isolated validation"):
        await remote.discover()
    assert not remote.catalog_hash
