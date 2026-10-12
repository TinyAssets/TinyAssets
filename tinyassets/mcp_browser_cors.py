"""Browser CORS for the public MCP surface.

Claude.ai, ChatGPT, and Claude Code talk to ``/mcp`` from their own
servers and send no ``Origin``. A browser does: the page at
``https://tinyassets.io/app`` calls same-origin ``/mcp`` with
``Authorization``, and a browser inspector or connector UI preflights
from its own origin. A preflight carries no bearer. Challenging it with
401, and answering every other response without
``Access-Control-Allow-Origin``, means the browser never shows the
client ``WWW-Authenticate`` or ``Mcp-Session-Id``.

The MCP streamable-HTTP rule is the other half: when ``Origin`` is
present and is not one of ours, the request is 403. A missing
``Origin`` is a server-side client and is left alone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_EXACT_ORIGINS = frozenset(
    json.loads(
        (Path(__file__).with_name("mcp_browser_origins.json")).read_text(encoding="utf-8")
    )["origins"]
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})

_ALLOW_METHODS = b"GET, POST, DELETE, OPTIONS"
_ALLOW_HEADERS = (
    b"authorization, content-type, accept, mcp-session-id, "
    b"mcp-protocol-version, last-event-id"
)
_EXPOSE_HEADERS = b"www-authenticate, mcp-session-id"

_FORBIDDEN_BODY = b'{"error":"origin_not_allowed"}'


def _header(scope: dict, name: str) -> str:
    target = name.lower().encode("ascii")
    for key, value in scope.get("headers") or []:
        if key.lower() == target:
            return value.decode("latin-1").strip()
    return ""


def _mcp_surface(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _resource_origin() -> str:
    raw = os.environ.get("WORKOS_MCP_RESOURCE", "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def _origin_allowed(origin: str) -> bool:
    """True for a browser Origin this connector will answer."""
    if not origin or "," in origin or origin.lower() == "null":
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return False
    if parts.username or parts.password:
        return False
    normalized = f"{parts.scheme}://{parts.netloc}".lower()
    if normalized in _EXACT_ORIGINS or normalized == _resource_origin():
        return True
    return parts.hostname.lower() in _LOOPBACK_HOSTS


def _cors_headers(origin: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin.encode("latin-1")),
        (b"access-control-allow-methods", _ALLOW_METHODS),
        (b"access-control-allow-headers", _ALLOW_HEADERS),
        (b"access-control-expose-headers", _EXPOSE_HEADERS),
        (b"access-control-max-age", b"600"),
        (b"vary", b"Origin"),
    ]


def _with_cors(message: dict, origin: str) -> dict:
    if message.get("type") != "http.response.start":
        return message
    headers = list(message.get("headers") or [])
    names = {key.lower() for key, _value in headers}
    for key, value in _cors_headers(origin):
        if key == b"vary" and b"vary" in names:
            for index, (existing, current) in enumerate(headers):
                if existing.lower() == b"vary" and b"origin" not in current.lower():
                    headers[index] = (existing, current + b", Origin")
            continue
        if key not in names:
            headers.append((key, value))
    return {**message, "headers": headers}


class McpBrowserCors:
    """Outside auth, inside origin admission. OPTIONS never needs a bearer."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope.get("type") != "http" or not _mcp_surface(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        origin = _header(scope, "origin")
        if not origin:
            await self.app(scope, receive, send)
            return
        if not _origin_allowed(origin):
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_FORBIDDEN_BODY)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    (b"vary", b"Origin"),
                ],
            })
            await send({"type": "http.response.body", "body": _FORBIDDEN_BODY})
            return

        if scope.get("method", "").upper() == "OPTIONS":
            body = b""
            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    *_cors_headers(origin),
                    (b"content-length", b"0"),
                    (b"cache-control", b"no-store"),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        async def send_cors(message):  # type: ignore[no-untyped-def]
            await send(_with_cors(message, origin))

        await self.app(scope, receive, send_cors)
