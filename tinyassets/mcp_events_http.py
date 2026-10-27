"""MCP 2026-07-28 boundary; retain FastMCP 3's real request/tool authority.

The modern tool transport is the same FastMCP server in stateless JSON mode.
Only protocol envelopes are adapted; tool dispatch and auth are never bypassed.

``events/subscribe`` and ``events/unsubscribe`` are answered by the router
(:func:`door`, registered in ``owner_router.TRUSTED``): subscriptions live in the
broker (ADR-025). The box answers only
whether the verified caller may follow the workflow, which needs owner files.
"""

from __future__ import annotations

import base64
import json
import logging

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tinyassets.mcp_event_webhooks import EventError
from tinyassets.mcp_events import EVENTS, MAILBOX, NAME, arguments, authorize

VERSION = "2026-07-28"
LEGACY = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
FORWARDED = {"tools/list", "tools/call", "prompts/list", "prompts/get", "ping"}
logger = logging.getLogger(__name__)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-JSON number')


def _decoded(value):
    if value.startswith("=?base64?") and value.endswith("?="):
        return base64.b64decode(value[9:-2], validate=True).decode()
    return value


class Envelope(EventError):
    """A refusal before dispatch, carrying the request ID once it is known."""

    def __init__(self, ident, error):
        super().__init__(error.code, str(error), **error.data)
        self.ident = ident


def parse(headers: Headers, version, body):
    """``(document, params)`` for one modern request, validated before any dispatch."""
    ident = None
    try:
        document = json.loads(body, object_pairs_hook=_object, parse_constant=_invalid_constant)
        if (
            not isinstance(document, dict)
            or document.get("jsonrpc") != "2.0"
            or type(document.get("id")) not in (str, int)
            or not isinstance(document.get("method"), str)
        ):
            raise EventError(-32600, "Invalid JSON-RPC request")
        ident = document["id"]
        if version != VERSION:
            raise EventError(
                -32022,
                "Unsupported protocol version",
                requested=version,
                supported=[VERSION, *LEGACY],
            )
        params = document.get("params", {})
        if not isinstance(params, dict):
            raise EventError(-32602, "Invalid params")
        method = document["method"]
        meta = params.get("_meta", {})
        if not isinstance(meta, dict):
            raise EventError(-32602, "Invalid metadata")
        if (
            len(headers.getlist("mcp-protocol-version")) != 1
            or len(headers.getlist("mcp-method")) != 1
            or headers.get("mcp-method") != method
            or meta.get("io.modelcontextprotocol/protocolVersion") != version
        ):
            raise EventError(-32020, "MCP header mismatch")
        if method in {"tools/call", "prompts/get"} and (
            len(headers.getlist("mcp-name")) != 1
            or _decoded(headers.get("mcp-name", "")) != params.get("name")
        ):
            raise EventError(-32020, "MCP name header mismatch")
    except EventError as exc:
        raise Envelope(ident, exc) from None
    except (ValueError, TypeError):
        raise Envelope(ident, EventError(-32602, "Invalid request")) from None
    return document, params


def answer(ident, result):
    result["resultType"] = "complete"
    return JSONResponse(
        {"jsonrpc": "2.0", "id": ident, "result": result},
        headers={"Cache-Control": "no-store"},
    )


def refusal(ident, exc):
    """The one mapping from a refusal to a bounded JSON-RPC error (S23: never silent)."""
    if isinstance(exc, EventError):
        error, status = {"code": exc.code, "message": str(exc), "data": exc.data}, (
            404 if exc.code == -32601 else 403 if exc.code == -32024 else 400)
    elif isinstance(exc, (PermissionError, KeyError)):
        error, status = {"code": -32024, "message": "Event access unavailable"}, 403
    elif isinstance(exc, (ValueError, TypeError)):
        error, status = {"code": -32602, "message": "Invalid request"}, 400
    else:
        # Avoid traceback locals and callback URLs/secrets in errors or logs.
        logger.error("MCP event request failed")
        error, status = {"code": -32603, "message": "Event service unavailable"}, 503
    return JSONResponse({"jsonrpc": "2.0", "id": ident, "error": error}, status_code=status)


def _same_client(verified, admitted):
    """The box's admitted outside client is the router's verified one, plus its grant."""
    if verified is None or admitted is None:
        return verified is None and admitted is None
    return isinstance(admitted, dict) and all(
        admitted.get(key) == verified.get(key)
        for key in ("client", "family", "authenticated_at"))


async def door(scope, receive, send, identity, ask_box):
    """Subscribe or unsubscribe on the trusted side, from the verified identity.

    ``ask_box(document)`` forwards one request to the owner's box and returns its
    JSON-RPC result. A subscribe asks the box only whether this caller may follow
    the workflow: the callback URL and signing key never enter the box.
    """
    from tinyassets.auth.middleware import _buffer_request_body
    from tinyassets.broker.mcp_events import request as broker
    from tinyassets.storage import data_dir

    ident = None
    try:
        body, _, disconnected, oversized = await _buffer_request_body(receive)
        if oversized:
            return await Response(status_code=413)(scope, receive, send)
        if disconnected:
            return
        headers = Headers(scope=scope)
        document, params = parse(headers, headers.get("mcp-protocol-version"), body)
        ident = document["id"]
        action = document["method"].removeprefix("events/")
        authority = identity.metadata.get("outside_origin")
        # A mailbox subscribe is checked in the broker, against the agent's registry row.
        if action == "subscribe" and params.get("name") != MAILBOX:
            granted = await ask_box({**document, "params": {
                key: params[key] for key in ("_meta", "name", "arguments") if key in params}})
            if set(granted) != {"authorized", "authority", "resultType"} or granted[
                    "authorized"] is not True or not _same_client(authority, granted["authority"]):
                raise RuntimeError("box authorization answer is malformed")
            # The box admitted this client against its grants; keep that binding,
            # generation included, so capture rechecks the grant as it stood.
            authority = granted["authority"]
        result = await run_in_threadpool(broker, data_dir(), {
            "action": action, "principal": identity.user_id,
            "authority": authority, "params": params})
        response = answer(ident, result)
    except Envelope as exc:
        response = refusal(exc.ident, exc)
    except Exception as exc:
        response = refusal(ident, exc)
    await response(scope, receive, send)


class MCPEventsMiddleware:
    def __init__(self, app, modern_tools):
        self.app, self.modern_tools = app, modern_tools

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in {"/mcp", "/mcp/"}:
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        version = request.headers.get("mcp-protocol-version")
        if not version or version in LEGACY:
            return await self.app(scope, receive, send)
        if request.method != "POST":
            return await Response(status_code=405)(scope, receive, send)
        ident = None
        try:
            from tinyassets.auth.middleware import (
                _buffer_request_body,
                _replay_receive,
                current_identity,
            )

            body, messages, disconnected, oversized = await _buffer_request_body(receive)
            if oversized:
                return await Response(status_code=413)(scope, receive, send)
            if disconnected:
                return
            document, params = parse(request.headers, version, body)
            ident, method = document["id"], document["method"]
            identity = current_identity()
            if method in FORWARDED:
                # Fresh stateless request, with the ORIGINAL Authorization header and
                # body. FastMCP's current_mcp_message_identity re-resolves that bearer.
                forwarded = dict(scope, path="/mcp", raw_path=b"/mcp")
                forwarded["headers"] = [
                    (k, v)
                    for k, v in scope["headers"]
                    if k.lower()
                    not in {b"mcp-protocol-version", b"mcp-session-id", b"last-event-id"}
                ]
                forwarded["headers"].append((b"mcp-protocol-version", b"2025-11-25"))
                chunks, response_status = [], 200

                async def capture(message):
                    nonlocal response_status
                    if message["type"] == "http.response.start":
                        response_status = message["status"]
                    elif message["type"] == "http.response.body":
                        chunks.append(message.get("body", b""))

                await self.modern_tools(forwarded, _replay_receive(messages, receive), capture)
                raw_answer = b"".join(chunks)
                try:
                    forwarded_answer = json.loads(raw_answer)
                except ValueError:
                    if response_status >= 400:
                        return await Response(
                            raw_answer, status_code=response_status, media_type="text/plain",
                            headers={"Cache-Control": "no-store"},
                        )(scope, receive, send)
                    raise
                if "result" in forwarded_answer:
                    forwarded_answer["result"]["resultType"] = "complete"
                    if method.endswith("/list"):
                        forwarded_answer["result"].update(ttlMs=0, cacheScope="private")
                return await JSONResponse(
                    forwarded_answer, status_code=response_status,
                    headers={"Cache-Control": "no-store"},
                )(scope, receive, send)
            if method == "server/discover":
                result = {
                    "supportedVersions": [VERSION, *LEGACY],
                    "_meta": {
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "TinyAssets", "version": "0.1.0",
                        },
                    },
                    "capabilities": {"tools": {}, "prompts": {}, "events": {}},
                    "ttlMs": 0,
                    "cacheScope": "private",
                }
            elif method == "events/list":
                if params.get("cursor") is not None:
                    raise EventError(-32602, "Invalid catalog cursor")
                # No owner-specific identifiers or resource inventory in the catalog.
                result = {"events": [d for d, _ in EVENTS.values()], "ttlMs": 0,
                          "cacheScope": "private"}
            elif method == "events/subscribe":
                # The router asks this before it subscribes in the broker; it
                # never sends the delivery block here (see door()).
                if params.get("name") != NAME or "delivery" in params:
                    raise EventError(-32602, "Unknown event")
                from tinyassets.storage import data_dir

                authority = identity.metadata.get("outside_origin")
                await run_in_threadpool(
                    authorize, data_dir(), identity.user_id, arguments(params.get("arguments")),
                    authority)
                # The authority as this box admitted it, grant generation included,
                # so a later grant change ends the subscription (see door()).
                result = {"authorized": True, "authority": authority}
            else:
                # events/unsubscribe is the router's alone; reaching a box is a routing fault.
                raise EventError(-32601, "Method not found")
            response = answer(ident, result)
        except Envelope as exc:
            response = refusal(exc.ident, exc)
        except Exception as exc:
            response = refusal(ident, exc)
        await response(scope, receive, send)
