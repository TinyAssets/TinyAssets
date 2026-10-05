"""Streamable HTTP MCP over the credential-blind streaming broker only.

One instance belongs to one execution principal and one attachment incarnation.
No HTTP library, credentials, global endpoint cache, or automatic tool replay.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from tinyassets.broker.ops import new_op_id
from tinyassets.mcp_attachment import Attachment
from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome, GrantResolutionError

VERSIONS = ("2025-06-18", "2025-03-26")
MAX_MESSAGE = 4 * 1024 * 1024
TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


class McpError(RuntimeError):
    """Fixed, credential-free protocol failure."""


class ProtocolRejected(McpError):
    """The server returned a matching JSON-RPC error response."""


class SessionExpired(McpError):
    pass


class SignInRequired(McpError):
    pass


def _json(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise McpError("invalid MCP JSON") from None
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise McpError("invalid MCP envelope")
    return value


async def messages(chunks: AsyncIterator[bytes], content_type: str):
    """Parse bounded messages incrementally, after the broker has scanned bytes."""
    if content_type == "application/json":
        body = bytearray()
        async for chunk in chunks:
            body.extend(chunk)
            if len(body) > MAX_MESSAGE:
                raise McpError("MCP response too large")
        yield _json(body)
        return
    if content_type != "text/event-stream":
        raise McpError("unsupported MCP response type")
    buffer = bytearray()
    data = []
    size = 0
    # SSE permits CR, LF and CRLF. Keep a trailing CR until the next chunk.
    async for chunk in chunks:
        buffer.extend(chunk)
        while True:
            match = re.search(rb"\r\n|\r(?!$)|\n", buffer)
            if match is None:
                break
            line = bytes(buffer[:match.start()])
            del buffer[:match.end()]
            if not line:
                if data and b"\n".join(data):
                    yield _json(b"\n".join(data))
                data, size = [], 0
            elif line.startswith(b"data:"):
                part = line[5:]
                if part.startswith(b" "):
                    part = part[1:]
                data.append(part)
                size += len(part) + 1
            if size > MAX_MESSAGE:
                raise McpError("MCP event too large")
        if len(buffer) + size > MAX_MESSAGE:
            raise McpError("MCP event too large")
    # Incomplete events are not delivered. The caller reports uncertain outcome
    # if a tool result was lost rather than submitting the POST a second time.


@dataclass(frozen=True)
class Binding:
    grant_id: str
    connection_id: str
    incarnation: str
    attachment: Attachment


class RemoteMcp:
    def __init__(self, broker, binding: Binding, *, check_authority):
        self._broker = broker
        self._binding = binding
        self._check_authority = check_authority
        self._session = ""
        self._version = ""
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._tools: dict[str, dict] = {}
        self.catalog_hash = ""

    def _reset(self):
        self._session = self._version = ""
        self._tools = {}
        self.catalog_hash = ""

    async def _rpc(self, method, params, *, op_id, notify=None, notification=False):
        attempt = {}
        try:
            return await self._exchange(method, params, op_id=op_id, notify=notify,
                                        notification=notification, attempt=attempt)
        except (SignInRequired, SessionExpired, ProtocolRejected):
            if method in {"initialize", "notifications/initialized"}:
                self._reset()
            raise
        except BaseException as exc:
            if method in {"initialize", "notifications/initialized"}:
                self._reset()
            if isinstance(exc, asyncio.CancelledError):
                if method == "tools/call" and attempt.get("request_id") is not None:
                    # The stream context has already sent broker CANCEL. Notify
                    # the MCP server too, using a new operation, never replaying
                    # the original call. Failure cannot turn cancellation into success.
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(5):
                            await self._rpc("notifications/cancelled", {
                                "requestId": attempt["request_id"], "reason": "Cancelled",
                            }, op_id=new_op_id(), notification=True)
                raise
            stream = attempt.get("stream")
            end = getattr(stream, "end", None)
            if (isinstance(exc, GrantResolutionError) and isinstance(end, dict)
                    and end.get("stream_sent") is False and not attempt.get("response")):
                raise  # Broker proved the authority refusal preceded every network write.
            if method == "tools/call" and (
                    attempt.get("response") or getattr(stream, "admitted", False)):
                raise AmbiguousProxyOutcome("MCP tool outcome unknown; do not replay") from None
            raise

    async def _exchange(self, method, params, *, op_id, notify, notification, attempt):
        await asyncio.to_thread(self._check_authority, self._binding)
        self._sequence += 1
        request_id = self._sequence
        attempt["request_id"] = request_id
        document = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            document["id"] = request_id
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self._version:
            headers["MCP-Protocol-Version"] = self._version
        if self._session:
            headers["MCP-Session-Id"] = self._session
        binding = self._binding
        found = None
        async with self._broker.stream(
            grant_id=binding.grant_id, connection_id=binding.connection_id, verb="POST",
            request={"url": binding.attachment.endpoint, "headers": headers,
                     "body": json.dumps(document, separators=(",", ":"))},
            op_id=op_id, mcp_binding={"incarnation": binding.incarnation,
                                    "revision": binding.attachment.revision},
        ) as stream:
            attempt["stream"] = stream
            head = await stream.head()
            attempt["response"] = True
            status = head["status"]
            if status in (401, 403):
                self._reset()
                raise SignInRequired("MCP sign-in required")
            if status == 404 and self._session:
                self._reset()
                raise SessionExpired("MCP session expired; refresh the catalog")
            if notification:
                if status != 202:
                    raise McpError("MCP notification was refused")
                async for chunk in stream.body():
                    if chunk:
                        raise McpError("MCP notification returned a body")
                return None
            if status != 200:
                raise McpError("MCP request was refused")
            response_headers = {str(k).lower(): v for k, v in head["headers"].items()}
            if method == "initialize":
                session = response_headers.get("mcp-session-id", "")
                if (not isinstance(session, str) or len(session) > 4096
                        or any(not 33 <= ord(c) <= 126 for c in session)):
                    raise McpError("invalid MCP session")
                self._session = session
            content_type = str(response_headers.get("content-type", "")).split(";")[0].strip()
            async for message in messages(stream.body(), content_type):
                await asyncio.to_thread(self._check_authority, binding)
                if "method" in message:
                    if "id" in message:
                        raise McpError("MCP server requested an unadvertised client capability")
                    if notify is not None:
                        await notify(message)
                    continue
                if type(message.get("id")) is not int or message["id"] != request_id:
                    raise McpError("MCP response ID mismatch")
                if found is not None:
                    raise McpError("duplicate MCP response")
                if "error" in message:
                    error = message["error"]
                    if (not isinstance(error, dict) or type(error.get("code")) is not int
                            or not isinstance(error.get("message"), str) or "result" in message):
                        raise McpError("invalid MCP error response")
                    raise ProtocolRejected("MCP server returned a protocol error")
                if not isinstance(message.get("result"), dict):
                    raise McpError("invalid MCP result")
                found = message["result"]
        if found is None:
            if method == "tools/call":
                raise AmbiguousProxyOutcome("MCP tool outcome unknown; do not replay")
            raise McpError("MCP stream ended without a result")
        return found

    async def _initialize(self):
        self._session = self._version = ""
        reply = await self._rpc("initialize", {
            "protocolVersion": VERSIONS[0], "capabilities": {},
            "clientInfo": {"name": "TinyAssets", "version": "1"},
        }, op_id=new_op_id())
        if (reply.get("protocolVersion") not in VERSIONS
                or not isinstance(reply.get("capabilities"), dict)
                or "tools" not in reply["capabilities"]):
            self._session = ""
            raise McpError("unsupported MCP protocol or missing tools capability")
        self._version = reply["protocolVersion"]
        await self._rpc("notifications/initialized", {}, op_id=new_op_id(), notification=True)

    async def discover(self):
        async with self._lock:
            self._tools = {}
            self.catalog_hash = ""
            # Only read-only discovery is retried after an explicit session expiry.
            for attempt in range(2):
                try:
                    if not self._version:
                        await self._initialize()
                    tools, cursors, cursor = {}, set(), None
                    while True:
                        result = await self._rpc("tools/list", {"cursor": cursor} if cursor else {},
                                                 op_id=new_op_id())
                        page = result.get("tools")
                        if not isinstance(page, list):
                            raise McpError("invalid MCP catalog")
                        for tool in page:
                            if (not isinstance(tool, dict)
                                    or not isinstance(tool.get("name"), str)
                                    or not TOOL_NAME.fullmatch(tool["name"])
                                    or tool["name"] in tools
                                    or not isinstance(tool.get("inputSchema"), dict)):
                                raise McpError("invalid MCP tool")
                            _safe_schema(tool["inputSchema"])
                            tools[tool["name"]] = tool
                        if len(json.dumps(tools)) > MAX_MESSAGE:
                            raise McpError("MCP catalog too large")
                        cursor = result.get("nextCursor")
                        if cursor is None:
                            break
                        if not isinstance(cursor, str) or not cursor or cursor in cursors:
                            raise McpError("invalid MCP pagination cursor")
                        cursors.add(cursor)
                        if len(cursors) > 1000:
                            raise McpError("MCP catalog has too many pages")
                    self._tools = tools
                    self.catalog_hash = hashlib.sha256(json.dumps(
                        tools, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    return list(tools.values())
                except SessionExpired:
                    if attempt:
                        raise

    async def call(self, name: str, arguments: dict[str, Any], *, catalog_hash: str,
                   op_id: str, notify=None):
        async with self._lock:
            if (not self.catalog_hash or catalog_hash != self.catalog_hash
                    or name not in self._tools):
                raise McpError("stale MCP catalog; discover tools again")
            import jsonschema
            from referencing import Registry
            from referencing.exceptions import NoSuchResource, Unresolvable

            def refuse_reference(uri):
                raise NoSuchResource(ref=uri)

            try:
                jsonschema.validate(arguments, self._tools[name]["inputSchema"],
                                    registry=Registry(retrieve=refuse_reference))
            except (jsonschema.ValidationError, jsonschema.SchemaError,
                    Unresolvable):
                raise McpError("MCP tool arguments do not match the catalog") from None
            return await self._rpc("tools/call", {"name": name, "arguments": arguments},
                                   op_id=op_id, notify=notify)

    async def reconcile(self, op_id):
        await asyncio.to_thread(self._check_authority, self._binding)
        return await self._broker.status(op_id)


def _safe_schema(schema):
    """Do not execute an arbitrary remote regular expression in the daemon.

    Until schema checking has its own isolated CPU budget, regex-constrained
    schemas are explicitly unsupported, rather than silently under-validated.
    """
    if isinstance(schema, dict):
        if "pattern" in schema or "patternProperties" in schema:
            raise McpError("MCP schemas with regular expressions require isolated validation")
        for value in schema.values():
            _safe_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _safe_schema(value)


def requires_approval(tool):
    """Untrusted annotations inform owner policy; missing hints mean unknown effect."""
    hints = tool.get("annotations")
    return (not isinstance(hints, dict) or hints.get("readOnlyHint") is not True
            or hints.get("destructiveHint") is True)
