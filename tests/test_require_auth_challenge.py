"""The MCP endpoint challenges a missing bearer, always.

Until 2026-09-02 the challenge was a provider OPTION (``WORKOS_REQUIRE_AUTH``):
without it the connector connected anonymously and first-contact never fired.
There is no anonymous principal now, so challenging is the transport's rule
and no provider can opt out of it. Discovery routes and the other exempt
paths (the D3 table in ``openspec/changes/no-anonymous-principal/design.md``)
keep their own named authentication.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tinyassets.auth.middleware import (
    AuthContextMiddleware,
    _auth_challenge_path,
    auth_middleware,
    set_provider,
)
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity

_SUBJECT = Identity(user_id="founder-1", username="founder-1", capabilities=["read", "write"])


class _ResolveAlwaysProvider(AuthProvider):
    """A resolve-always provider. ``opt_out`` mimics the retired
    ``challenge_unauthenticated() -> False`` option: the transport ignores it."""

    def __init__(self, *, opt_out: bool = False) -> None:
        self._opt_out = opt_out

    def resolve_token(self, token: str) -> Identity | None:
        return _SUBJECT if token == "valid" else None

    def is_auth_required(self) -> bool:
        return False

    def resolve_always_writes(self) -> bool:
        return True

    def challenge_unauthenticated(self) -> bool:  # retired hook, deliberately kept here
        return not self._opt_out

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "t", **metadata}

    def create_authorization(self, *a: Any, **k: Any) -> str:
        return "c"

    def exchange_code(self, *a: Any, **k: Any) -> dict[str, Any] | None:
        return None


@pytest.fixture(autouse=True)
def _reset_auth():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _drive(
    path: str,
    *,
    token: str | None,
    method: str = "POST",
    body: bytes = b"",
    accept: str | None = None,
) -> tuple[list[dict], bool]:
    """Run one request through AuthContextMiddleware; return (sent, app_called)."""
    called = {"hit": False}

    async def _app(scope, receive, send):  # noqa: ANN001, ANN202
        called["hit"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent: list[dict] = []

    async def _send(msg):  # noqa: ANN001, ANN202
        sent.append(msg)

    delivered = False

    async def _receive():  # noqa: ANN202
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body}

    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("latin1")))
    if accept is not None:
        headers.append((b"accept", accept.encode("latin-1")))
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    asyncio.run(AuthContextMiddleware(_app)(scope, _receive, _send))
    return sent, called["hit"]


def _status(sent: list[dict]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _www_authenticate(sent: list[dict]) -> str:
    start = next(m for m in sent if m["type"] == "http.response.start")
    for k, v in start["headers"]:
        if k == b"www-authenticate":
            return v.decode("latin1")
    return ""


def _body(sent: list[dict]) -> bytes:
    return b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )


def test_a_browser_get_reaches_the_connect_page_and_a_client_does_not():
    set_provider(_ResolveAlwaysProvider())
    page, page_called = _drive(
        "/mcp", token=None, method="GET", accept="text/html",
    )
    assert page_called
    assert _status(page) == 200
    client, client_called = _drive(
        "/mcp", token=None, method="GET",
        accept="application/json, text/event-stream",
    )
    assert not client_called
    assert _status(client) == 401
    assert "resource_metadata=" in _www_authenticate(client)


def test_missing_token_on_mcp_is_challenged():
    set_provider(_ResolveAlwaysProvider())
    sent, app_called = _drive("/mcp", token=None)
    assert not app_called                       # request never reached the app
    assert _status(sent) == 401
    wa = _www_authenticate(sent)
    assert "resource_metadata=" in wa
    assert "invalid_token" not in wa            # missing != invalid (RFC 6750)


def test_there_is_no_opt_out_of_the_challenge():
    set_provider(_ResolveAlwaysProvider(opt_out=True))
    sent, app_called = _drive("/mcp", token=None)
    assert not app_called
    assert _status(sent) == 401


def test_the_provider_base_class_has_no_challenge_switch():
    assert not hasattr(AuthProvider, "challenge_unauthenticated")


def test_discovery_routes_stay_public():
    set_provider(_ResolveAlwaysProvider())
    for path in (
        "/.well-known/oauth-protected-resource",
        "/mcp/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/mcp/oauth/register",
        "/mcp/oauth/authorize",
        "/mcp/oauth/token",
    ):
        sent, app_called = _drive(path, token=None)
        assert app_called, f"{path} must not be challenged"
        assert _status(sent) == 200
    sent, app_called = _drive("/mcp/oauth/register/extra", token=None, method="POST")
    assert not app_called
    assert _status(sent) == 401


def test_valid_token_on_mcp_passes_through():
    set_provider(_ResolveAlwaysProvider())
    sent, app_called = _drive("/mcp", token="valid")
    assert app_called
    assert _status(sent) == 200


def test_invalid_token_is_challenged_as_invalid():
    set_provider(_ResolveAlwaysProvider())
    sent, app_called = _drive("/mcp", token="bad")
    assert not app_called
    assert _status(sent) == 401
    challenge = _www_authenticate(sent)
    assert 'error="invalid_token"' in challenge
    assert "error_description=" in challenge
    body = json.loads(_body(sent))
    assert body["error"] == "invalid_token"
    assert body["error_description"]


def test_cached_hosted_tool_call_gets_linking_challenge_without_dispatch(monkeypatch):
    monkeypatch.setenv("WORKOS_MCP_RESOURCE", "https://tinyassets.io/mcp")
    set_provider(_ResolveAlwaysProvider())
    request = json.dumps({
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {"name": "get_status", "arguments": {}},
    }).encode()

    sent, app_called = _drive("/mcp", token=None, body=request)

    assert not app_called
    assert _status(sent) == 200
    payload = json.loads(_body(sent))
    assert payload["id"] == 42
    assert payload["result"]["isError"] is True
    challenges = payload["result"]["_meta"]["mcp/www_authenticate"]
    assert len(challenges) == 1
    assert (
        'resource_metadata="https://tinyassets.io/'
        '.well-known/oauth-protected-resource/mcp"'
    ) in challenges[0]
    assert 'error="invalid_token"' in challenges[0]
    assert "error_description=" in challenges[0]


def test_mixed_unauthenticated_batch_stays_on_transport_challenge():
    set_provider(_ResolveAlwaysProvider())
    request = json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]).encode()

    sent, app_called = _drive("/mcp", token=None, body=request)

    assert not app_called
    assert _status(sent) == 401


def test_every_advertised_tool_is_oauth_only():
    from tinyassets.universe_server import mcp

    tools = asyncio.run(mcp.list_tools(run_middleware=True))
    assert tools
    expected = [{
        "type": "oauth2",
        "scopes": ["openid", "profile", "email", "offline_access"],
    }]
    for tool in tools:
        wire = tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
        assert wire["securitySchemes"] == expected, tool.name
        assert wire["_meta"]["securitySchemes"] == expected, tool.name
        assert all(scheme["type"] != "noauth" for scheme in wire["securitySchemes"])


def test_dev_mode_challenges_a_missing_token_too():
    # Dev mode used to resolve nobody to an anonymous reader. It resolves ANY
    # bearer to the named operator and still refuses none at all.
    set_provider(DevAuthProvider(user_id="operator"))
    sent, app_called = _drive("/mcp", token=None)
    assert not app_called
    assert _status(sent) == 401
    sent, app_called = _drive("/mcp", token="whatever")
    assert app_called
    assert _status(sent) == 200


@pytest.mark.parametrize(
    ("path", "challenged"),
    [
        ("/mcp", True),
        ("/mcp/", True),
        ("/mcp/anything", True),
        ("/app", False),                       # the SPA shell loads before sign-in
        ("/app/token", False),                 # its PKCE exchange
        ("/app/settings", True),               # every other app route needs the bearer
        ("/app/billing/webhook", False),       # Stripe-signed
        ("/app/client-error", False),          # browser trail beacon
        ("/app/client-errors", True),
        ("/app/client-error/x", True),
        ("/app/billing/checkout", True),
        ("/mcp/pulse", True),                      # service-principal release facts
        ("/mcp/pulse/", True),                     # exact path, nothing under it
        ("/mcp/pulse/extra", True),
        ("/mcp/.well-known/oauth-protected-resource", False),
        ("/.well-known/oauth-protected-resource", False),
        ("/.well-known/oauth-protected-resource/mcp", False),
        ("/.well-known/oauth-authorization-server", False),
        ("/.well-known/oauth-authorization-server/mcp", False),
        ("/not-mcp", False),
        ("/", False),
        # The retired path (moved to the apex /app on 2026-09-30) keeps no
        # carve-out: it is an ordinary /mcp/* path now.
        ("/mcp/app", True),
        ("/mcp/app/token", True),
        ("/mcp/app/billing/webhook", True),
        # Apex website paths that merely start with "app" are not app routes.
        ("/apple-touch-icon.png", False),
        ("/app-ads.txt", False),
    ],
)
def test_exempt_table_is_exact_paths_not_prefixes(path, challenged, monkeypatch):
    from tinyassets.auth import middleware as mw

    monkeypatch.setattr(mw, "_inbound_hooks_enabled", lambda: False)
    assert _auth_challenge_path(path) is challenged, path


def test_hook_routes_are_exempt_only_when_their_feature_is_on(monkeypatch):
    from tinyassets.auth import middleware as mw

    monkeypatch.setattr(mw, "_inbound_hooks_enabled", lambda: False)
    assert _auth_challenge_path("/mcp/hooks/abc123") is True

    monkeypatch.setattr(mw, "_inbound_hooks_enabled", lambda: True)
    assert _auth_challenge_path("/mcp/hooks/abc123") is False
    assert _auth_challenge_path("/mcp/hooks/abc123/deeper") is True   # one segment only
    assert _auth_challenge_path("/mcp/hooks/") is True


def test_the_retired_connect_deposit_paths_are_challenged(monkeypatch):
    """The dark /mcp/connect browser form was deleted (2026-09-30): nothing
    reached it and its flag was set in no deployment. Its paths are ordinary
    /mcp paths now, so the bearer challenge covers them with no flag to flip."""
    monkeypatch.setenv("TINYASSETS_CONNECT_DEPOSIT_ENABLED", "1")
    for path in ("/mcp/connect", "/mcp/connect/login", "/mcp/connect/callback"):
        assert _auth_challenge_path(path) is True, path


def test_challenge_metadata_url_is_the_rfc_9728_path(monkeypatch):
    # RFC 9728 inserts the well-known segment. The legacy path under /mcp
    # still serves the same document; the header no longer points there.
    from tinyassets.auth.middleware import _challenge_prm_url

    monkeypatch.setenv("WORKOS_MCP_RESOURCE", "https://tinyassets.io/mcp")
    assert (
        _challenge_prm_url()
        == "https://tinyassets.io/.well-known/oauth-protected-resource/mcp"
    )


def test_challenge_header_is_exact(monkeypatch):
    monkeypatch.setenv("WORKOS_MCP_RESOURCE", "https://tinyassets.io/mcp")
    set_provider(_ResolveAlwaysProvider())
    sent, _ = _drive("/mcp", token=None)
    assert _www_authenticate(sent) == (
        'Bearer resource_metadata='
        '"https://tinyassets.io/.well-known/oauth-protected-resource/mcp"'
    )
    assert "error=" not in _www_authenticate(sent)
    assert json.loads(_body(sent)) == {
        "error": "authentication_required",
        "error_description": "Sign in to TinyAssets to continue.",
    }


def test_workos_provider_no_longer_reads_a_require_auth_switch(monkeypatch):
    from tinyassets.auth.workos_provider import WorkOSAuthProvider

    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "example.authkit.app")
    monkeypatch.setenv("WORKOS_ALLOW_NO_AUDIENCE", "1")
    monkeypatch.setenv("WORKOS_REQUIRE_AUTH", "0")
    provider = WorkOSAuthProvider.from_env()
    assert not hasattr(provider, "challenge_unauthenticated")
    set_provider(provider)
    sent, app_called = _drive("/mcp", token=None)
    assert not app_called
    assert _status(sent) == 401
