"""MCP 2026-07-28 boundary; retain FastMCP 3's real request/tool authority.

The modern tool transport is the same FastMCP server in stateless JSON mode.
Only protocol envelopes are adapted; tool dispatch and auth are never bypassed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tinyassets.mcp_event_webhooks import EventError
from tinyassets.mcp_events import DEFINITION, EventStore

VERSION = "2026-07-28"
LEGACY = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
FORWARDED = {"tools/list", "tools/call", "prompts/list", "prompts/get", "ping"}
logger = logging.getLogger(__name__)


def _decoded(value):
    if value.startswith("=?base64?") and value.endswith("?="):
        return base64.b64decode(value[9:-2], validate=True).decode()
    return value


class MCPEventsMiddleware:
    def __init__(self, app, modern_tools, *, store_factory=None):
        self.app, self.modern_tools = app, modern_tools
        self.store_factory = store_factory or self._store

    def __getattr__(self, name):
        return getattr(self.app, name)

    @staticmethod
    def _store():
        from tinyassets.storage import data_dir

        return EventStore(data_dir())

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
            from tinyassets.ingress import _invalid_constant, _object

            body, messages, disconnected, oversized = await _buffer_request_body(receive)
            if oversized:
                return await Response(status_code=413)(scope, receive, send)
            if disconnected:
                return
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
                len(request.headers.getlist("mcp-protocol-version")) != 1
                or len(request.headers.getlist("mcp-method")) != 1
                or request.headers.get("mcp-method") != method
                or meta.get("io.modelcontextprotocol/protocolVersion") != version
            ):
                raise EventError(-32020, "MCP header mismatch")
            if method in {"tools/call", "prompts/get"} and (
                len(request.headers.getlist("mcp-name")) != 1
                or _decoded(request.headers.get("mcp-name", "")) != params.get("name")
            ):
                raise EventError(-32020, "MCP name header mismatch")
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
                    answer = json.loads(raw_answer)
                except ValueError:
                    if response_status >= 400:
                        return await Response(
                            raw_answer, status_code=response_status, media_type="text/plain",
                            headers={"Cache-Control": "no-store"},
                        )(scope, receive, send)
                    raise
                if "result" in answer:
                    answer["result"]["resultType"] = "complete"
                    if method.endswith("/list"):
                        answer["result"].update(ttlMs=0, cacheScope="private")
                return await JSONResponse(
                    answer, status_code=response_status, headers={"Cache-Control": "no-store"}
                )(scope, receive, send)
            if method == "server/discover":
                result = {
                    "supportedVersions": [VERSION, *LEGACY],
                    "serverInfo": {"name": "TinyAssets", "version": "0.1.0"},
                    "capabilities": {"tools": {}, "prompts": {}, "events": {}},
                }
            elif method == "events/list":
                if params.get("cursor") is not None:
                    raise EventError(-32602, "Invalid catalog cursor")
                # No owner-specific identifiers or resource inventory in the catalog.
                result = {"events": [DEFINITION], "ttlMs": 0, "cacheScope": "private"}
            elif method in {"events/subscribe", "events/unsubscribe"}:
                store = self.store_factory()
                if method == "events/subscribe":
                    result = await run_in_threadpool(
                        store.subscribe,
                        identity.user_id,
                        params,
                        authority=identity.metadata.get("outside_origin"),
                    )
                else:
                    result = await run_in_threadpool(store.unsubscribe, identity.user_id, params)
            else:
                raise EventError(-32601, "Method not found")
            result["resultType"] = "complete"
            response = JSONResponse(
                {"jsonrpc": "2.0", "id": ident, "result": result},
                headers={"Cache-Control": "no-store"},
            )
        except EventError as exc:
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": exc.code, "message": str(exc), "data": exc.data},
                },
                status_code=404 if exc.code == -32601 else 400,
            )
        except (PermissionError, KeyError):
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": -32024, "message": "Event access unavailable"},
                },
                status_code=403,
            )
        except (ValueError, TypeError):
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": -32602, "message": "Invalid request"},
                },
                status_code=400,
            )
        except Exception:
            # Avoid traceback locals and callback URLs/secrets in errors or logs.
            logger.error("MCP event request failed")
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": ident,
                    "error": {"code": -32603, "message": "Event service unavailable"},
                },
                status_code=503,
            )
        await response(scope, receive, send)


@asynccontextmanager
async def delivery_lifespan(base):
    stop = asyncio.Event()

    async def run():
        while not stop.is_set():
            try:
                # Join at most one bounded network attempt during serving shutdown.
                await run_in_threadpool(EventStore(base).pump, limit=1)
            except Exception:
                logger.error("MCP event delivery pass failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass

    task = asyncio.create_task(run())
    try:
        yield
    finally:
        stop.set()
        await task
