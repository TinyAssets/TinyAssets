"""Provider authority follows the exact authenticated MCP POST, never its session."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from mcp.server.lowlevel.server import request_ctx
from starlette.requests import Request

from tinyassets.auth import middleware as auth
from tinyassets.auth.provider import Identity


@contextmanager
def message(*, method="POST", path="/mcp", headers=(), **scope):
    request = Request({"type": "http", "method": method, "path": path,
                       "headers": list(headers), "scheme": "http",
                       "server": ("owner", 80), "query_string": b"", **scope})
    token = request_ctx.set(SimpleNamespace(request=request))
    try:
        yield
    finally:
        request_ctx.reset(token)


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_private_post_uses_its_owner_ingress_identity_without_bearer(path, monkeypatch):
    owner = Identity(user_id="owner", username="owner")
    monkeypatch.setattr(auth, "resolve_token", lambda *_: pytest.fail("public auth called"))
    with message(path=path, tinyassets_router_identity=owner):
        assert auth.current_mcp_message_identity() is owner
    with message():
        assert auth.current_mcp_message_identity() is None


@pytest.mark.parametrize("method,path", [("GET", "/mcp"), ("POST", "/api"),
                                         ("POST", "/mcp/events")])
def test_router_identity_cannot_authorize_another_transport_message(method, path):
    with message(method=method, path=path,
                 tinyassets_router_identity=Identity(user_id="owner", username="owner")):
        assert auth.current_mcp_message_identity() is None


@pytest.mark.parametrize("identity", [{"user_id": "owner"}, Identity(user_id="", username="")])
def test_invalid_authenticated_scope_is_refused(identity):
    with message(tinyassets_router_identity=identity):
        assert auth.current_mcp_message_identity() is None


def test_raw_forwarding_headers_and_ambient_identity_are_not_router_proof():
    token = auth._current_identity.set(Identity(user_id="ambient", username="ambient"))
    try:
        with message(headers=[(b"x-owner", b"spoofed"), (b"x-box-secret", b"fake"),
                              (b"x-owner-identity", b"fake")]):
            assert auth.current_mcp_message_identity() is None
    finally:
        auth._current_identity.reset(token)


def test_public_bearer_still_resolves_on_the_exact_message(monkeypatch):
    owner = Identity(user_id="owner", username="owner")
    monkeypatch.setattr(auth, "resolve_token", lambda token: owner if token == "valid" else None)
    monkeypatch.setattr(auth, "wiki_canary_token_matches", lambda _: False)
    with message(headers=[(b"authorization", b"Bearer valid")]):
        assert auth.current_mcp_message_identity() is owner
    with message(headers=[(b"authorization", b"Bearer valid")] * 2):
        assert auth.current_mcp_message_identity() is None


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T1')
