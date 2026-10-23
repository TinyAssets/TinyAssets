"""Authenticated tool transport inside the owner daemon."""
from __future__ import annotations

import os
from contextvars import ContextVar

_model_context: ContextVar[int | None] = ContextVar("engine_model_context", default=None)


def _context_from_query(query: bytes) -> int | None:
    from urllib.parse import parse_qs

    params = parse_qs(query.decode("ascii", errors="replace"))
    values = params.get("context_tokens", [])
    if (len(values) == 1 and values[0].isascii() and values[0].isdigit()
            and len(values[0]) <= 10):
        return int(values[0]) or None
    return None


def model_context() -> int | None:
    # MCP session work can run in the lifespan task group rather than the
    # ASGI request task. Its request dependency retains the actual call URL.
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    except RuntimeError:
        return _model_context.get()
    return _context_from_query(request.scope.get("query_string", b""))


_endpoint: ContextVar[EngineEndpoint | None] = ContextVar("engine_endpoint", default=None)


def binding():
    return _endpoint.get()


_default_server = None


def current_server():
    endpoint = binding()
    if endpoint is not None:
        return endpoint
    # Direct in-box calls and tests use the same registration as HTTP.
    global _default_server
    if _default_server is None:
        _default_server = ToolSurface()
    return _default_server


class ToolSurface:
    def __init__(self):
        from tinyassets import universe_tools
        from tinyassets.control_plane.registration import engine_server

        self.module = universe_tools
        self.mcp = engine_server(universe_tools)

    def __getattr__(self, name):
        if name == "_ACTOR_ID":
            return self.module._actor_id()
        if name == "_GRAPH_ID":
            return self.module._graph_id()
        return getattr(self.module, name)


def grant_key() -> str:
    endpoint = _endpoint.get()
    if endpoint is not None:
        return endpoint.key
    from tinyassets.served_tools import LAUNCH_GRANT_KEY_ENV

    return (os.environ.get(LAUNCH_GRANT_KEY_ENV) or "").strip()


class EngineEndpoint(ToolSurface):
    """Authenticated in-box transport; pins follow request and lifespan context."""

    def __init__(self, actor: str, graph: str, secret: str, key: str):
        if not all((actor, graph, secret, key)):
            raise ValueError("engine endpoint requires complete authority pins")
        super().__init__()
        self.actor, self.graph, self.secret, self.key = actor, graph, secret, key
        self.app = self.mcp.http_app()

    def close(self):
        """The HTTP lifespan owns cleanup; no cloned modules are retained."""

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "http":
            authorization = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not self.module._bearer_ok(authorization.decode("latin-1"), self.secret):
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        elif kind != "lifespan":
            return
        context_token = _model_context.set(
            _context_from_query(scope.get("query_string", b"")))
        token = _endpoint.set(self)
        try:
            await self.app(scope, receive, send)
        finally:
            _endpoint.reset(token)
            _model_context.reset(context_token)
