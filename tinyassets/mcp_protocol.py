"""July 2026 MCP metadata and request-local URL continuation; no network custody."""
from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from tinyassets.broker.ops import new_op_id
from tinyassets.mcp_remote import (
    MAX_MESSAGE,
    VERSIONS,
    Binding,
    LegacyRequired,
    McpError,
    ProtocolRejected,
    _json,
)

PREFIX = "io.modelcontextprotocol/"
TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def encode_header(value):
    text = str(value).lower() if isinstance(value, bool) else str(value)
    if (text != text.strip() or any(not (32 <= ord(c) <= 126 or c == "\t") for c in text)
            or (text.startswith("=?base64?") and text.endswith("?="))):
        return "=?base64?" + base64.b64encode(text.encode()).decode() + "?="
    return text


def header_parameters(schema):
    """Reject annotations outside static properties chains, including duplicates."""
    found, names = [], set()

    def walk(node, path=(), reachable=True):
        if isinstance(node, list):
            for item in node:
                walk(item, path, False)
        elif isinstance(node, dict):
            if "x-mcp-header" in node:
                name = node["x-mcp-header"]
                if (not reachable or not path or not isinstance(name, str)
                        or not TOKEN.fullmatch(name) or name.lower() in names
                        or node.get("type") not in ("string", "integer", "boolean")):
                    raise ValueError("invalid MCP parameter header")
                names.add(name.lower())
                found.append((path, name, node["type"]))
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop, child in value.items():
                        walk(child, (*path, prop), reachable)
                elif isinstance(value, (dict, list)):
                    walk(value, path, False)

    walk(schema)
    return found


def request_metadata(method, params, tools, *, elicit_url):
    metadata = {PREFIX + "protocolVersion": VERSIONS[0],
                PREFIX + "clientInfo": {"name": "TinyAssets", "version": "1"},
                PREFIX + "clientCapabilities": {"elicitation": {"url": {}}} if elicit_url else {}}
    headers = {"Mcp-Method": method}
    if method == "tools/call":
        headers["Mcp-Name"] = encode_header(params["name"])
        for path, name, kind in header_parameters(tools[params["name"]]["inputSchema"]):
            value = params["arguments"]
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                continue
            if kind == "integer" and (type(value) is not int or abs(value) > 2**53 - 1):
                raise McpError("MCP header integer outside safe range")
            headers["Mcp-Param-" + name] = encode_header(value)
    return {**params, "_meta": metadata}, headers


async def probe_error(stream, request_id):
    body = bytearray()
    async for chunk in stream.body():
        body.extend(chunk)
        if len(body) > MAX_MESSAGE:
            raise McpError("MCP response too large")
    try:
        doc = _json(body)
    except McpError:
        raise LegacyRequired("MCP legacy initialization required") from None
    error = doc.get("error")
    if isinstance(error, dict) and error.get("code") in (-32020, -32021, -32022, -32601):
        if type(doc.get("id")) is not int or doc["id"] != request_id:
            raise McpError("MCP response ID mismatch")
        data = error.get("data", {})
        supported = data.get("supported", []) if isinstance(data, dict) else []
        if not isinstance(supported, list) or any(not isinstance(v, str) for v in supported):
            raise McpError("invalid MCP supported versions")
        raise ProtocolRejected(code=error["code"], supported=supported)
    raise LegacyRequired("MCP legacy initialization required")


@dataclass(frozen=True)
class UrlElicitation:
    binding: Binding
    op_id: str
    input_id: str
    url: str
    message: str


def url_requests(result):
    if "requestState" in result and not isinstance(result["requestState"], str):
        raise McpError("invalid MCP request state")
    requests = result.get("inputRequests", {})
    if not isinstance(requests, dict) or not (requests or "requestState" in result):
        raise McpError("invalid MCP input-required result")
    parsed = []
    for key, request in requests.items():
        if not isinstance(key, str) or not key or not isinstance(request, dict):
            raise McpError("invalid MCP input request")
        params = request.get("params")
        if (request.get("method") != "elicitation/create" or not isinstance(params, dict)
                or params.get("mode") != "url" or not isinstance(params.get("url"), str)
                or not isinstance(params.get("message"), str)):
            raise McpError("unsupported MCP input request")
        url = params["url"]
        try:
            parts = urlsplit(url)
            if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                    or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url):
                raise ValueError
            parts.port
        except ValueError:
            raise McpError("unsafe MCP elicitation URL") from None
        parsed.append((key, url, params["message"]))
    return parsed


async def continue_call(remote, original, result, *, op_id, notify):
    """Only explicit MRTR responses authorize a new round, never a lost response."""
    consented = set()
    # Pending data is private to this coroutine/host/binding, not a public resume token.
    async with asyncio.timeout(900):
        for _ in range(300):
            requests = url_requests(result)
            if requests and remote._elicit_url is None:
                raise McpError("MCP URL elicitation requires a protected host")
            responses = {}
            repeated = False
            for key, url, message in requests:
                await asyncio.to_thread(remote._check_authority, remote._binding)
                identity = (key, url, message)
                if identity in consented:
                    action, repeated = "accept", True
                else:
                    action = await remote._elicit_url(UrlElicitation(
                        remote._binding, op_id, key, url, message))
                if action not in ("accept", "decline", "cancel"):
                    raise McpError("invalid host elicitation response")
                if action != "accept":
                    raise McpError("MCP elicitation cancelled")
                consented.add(identity)
                responses[key] = {"action": action}
            if repeated or not requests:
                await asyncio.sleep(1)
            params = {**original}
            if "inputRequests" in result:
                params["inputResponses"] = responses
            if "requestState" in result:
                params["requestState"] = result["requestState"]
            result = await remote._rpc("tools/call", params, op_id=new_op_id(), notify=notify)
            if result.get("resultType") != "input_required":
                return result
    raise McpError("MCP continuation limit reached")
