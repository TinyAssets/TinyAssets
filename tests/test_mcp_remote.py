"""Protocol negotiation, streamed discovery and non-replayed tool outcomes."""
import json
from contextlib import asynccontextmanager

import pytest

from tinyassets.mcp_attachment import Attachment
from tinyassets.mcp_remote import (
    Binding,
    McpError,
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
            session = "opaque-session"
        elif method == "notifications/initialized":
            status, result = 202, None
        elif method == "tools/list":
            result = ({"tools": TOOLS[:1], "nextCursor": "next"}
                      if not request["params"] else {"tools": TOOLS[1:]})
        else:
            content_type = "text/event-stream"
            result = {"content": [{"type": "text", "text": "done"}]}
        raw = json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result})
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
    return RemoteMcp(broker, Binding("g", "c", "i", Attachment(
        "https://example.com/mcp", "Example", state="connecting")), check_authority=check)


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
    assert broker.calls[-1]["mcp_binding"] == {"incarnation": "i", "revision": 1}
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
