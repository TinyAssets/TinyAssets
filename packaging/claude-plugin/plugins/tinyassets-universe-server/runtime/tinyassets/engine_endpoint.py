"""Owner-pinned engine handlers in the daemon that owns the role clients."""
from __future__ import annotations

import importlib.util
import os
import secrets
import sys
from contextvars import ContextVar
from pathlib import Path

_endpoint: ContextVar[tuple | None] = ContextVar("engine_endpoint", default=None)


def current_server():
    endpoint = _endpoint.get()
    if endpoint is not None:
        return endpoint[0]
    from tinyassets import engine_mcp_server

    return engine_mcp_server


def grant_key() -> str:
    endpoint = _endpoint.get()
    if endpoint is not None:
        return endpoint[1]
    from tinyassets.served_tools import LAUNCH_GRANT_KEY_ENV

    return (os.environ.get(LAUNCH_GRANT_KEY_ENV) or "").strip()


class EngineEndpoint:
    """Independent handler globals; request and lifespan tasks retain this pin."""

    def __init__(self, actor: str, graph: str, secret: str, key: str):
        if not all((actor, graph, secret, key)):
            raise ValueError("engine endpoint requires complete authority pins")
        name = "tinyassets._engine_endpoint_" + secrets.token_hex(16)
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("engine_mcp_server.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            module._ACTOR_ID, module._GRAPH_ID = actor, graph
            self.app = module.mcp.http_app()
        except BaseException:
            sys.modules.pop(name, None)
            raise
        self.module, self.secret, self.key = module, secret, key

    def close(self):
        """Only after the serving thread and all its tasks have stopped."""
        sys.modules.pop(self.module.__name__, None)

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
        token = _endpoint.set((self.module, self.key))
        try:
            await self.app(scope, receive, send)
        finally:
            _endpoint.reset(token)
