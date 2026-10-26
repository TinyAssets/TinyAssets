"""P0-2: neither door forwards a non-HTTP scope; lifespan still reaches the app."""
from __future__ import annotations

import asyncio

import pytest

from tinyassets.asgi_scope import WEBSOCKET_REFUSED
from tinyassets.owner_ingress import OwnerIngress
from tinyassets.owner_router import OwnerRouter


def _app():
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    return app, reached


def _router(app):
    def unused(*args, **kwargs):
        raise AssertionError("no box is resolved for a refused scope")

    return OwnerRouter(app, resolver=unused, admitter=unused)


def _ingress(app, tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(b"a" * 43)
    return OwnerIngress(app, secret)


def _call(door, scope):
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    asyncio.run(door(scope, receive, send))
    return sent


def _websocket():
    return {"type": "websocket", "path": "/mcp", "headers": [], "query_string": b""}


def test_owner_router_refuses_websocket_scope():
    app, reached = _app()
    assert _call(_router(app), _websocket()) == [
        {"type": "websocket.close", "code": WEBSOCKET_REFUSED}]
    assert reached == []


def test_owner_ingress_refuses_websocket_scope(tmp_path):
    app, reached = _app()
    assert _call(_ingress(app, tmp_path), _websocket()) == [
        {"type": "websocket.close", "code": WEBSOCKET_REFUSED}]
    assert reached == []


def test_lifespan_still_starts_app(tmp_path):
    for make in (_router, lambda app: _ingress(app, tmp_path)):
        app, reached = _app()
        _call(make(app), {"type": "lifespan"})
        assert reached == ["lifespan"]


def test_unknown_scope_type_fails_loudly(tmp_path):
    for make in (_router, lambda app: _ingress(app, tmp_path)):
        app, reached = _app()
        with pytest.raises(RuntimeError, match="unsupported ASGI scope type: 'webtransport'"):
            _call(make(app), {"type": "webtransport"})
        assert reached == []
