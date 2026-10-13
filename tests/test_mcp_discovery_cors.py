"""Browser CORS on /mcp and the apex OAuth discovery documents."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tinyassets.auth.middleware import _DISCOVERY_PATHS
from tinyassets.mcp_browser_cors import _DISCOVERY_PATHS as _CORS_DISCOVERY
from tinyassets.mcp_browser_cors import McpBrowserCors

_PATHS = (
    "/mcp",
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/mcp",
    "/mcp/oauth/register",
)


async def _answer(request):
    return JSONResponse(
        {"ok": True},
        status_code=401,
        headers={
            "WWW-Authenticate": 'Bearer realm="mcp"',
            "Mcp-Session-Id": "sess-1",
        },
    )


def _client() -> TestClient:
    routes = [
        Route(path, _answer, methods=["GET", "POST", "OPTIONS"])
        for path in _PATHS
    ]
    return TestClient(McpBrowserCors(Starlette(routes=routes)))


def test_discovery_paths_stay_in_step_with_the_auth_exemption():
    assert _CORS_DISCOVERY <= _DISCOVERY_PATHS
    assert "/.well-known/oauth-protected-resource/mcp" in _DISCOVERY_PATHS


def test_preflight_allows_the_mcp_headers_for_each_allowlisted_origin():
    client = _client()
    origins = (
        "https://claude.ai",
        "https://chatgpt.com",
        "http://localhost:3000",
        "http://127.0.0.1:6274",
    )
    for path in _PATHS:
        for origin in origins:
            response = client.options(
                path,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": (
                        "Authorization, Mcp-Session-Id, Mcp-Protocol-Version"
                    ),
                },
            )
            assert response.status_code == 204, (path, origin, response.text)
            assert response.headers["access-control-allow-origin"] == origin
            allowed = response.headers["access-control-allow-headers"]
            assert "authorization" in allowed
            assert "mcp-session-id" in allowed
            assert "mcp-protocol-version" in allowed
            exposed = response.headers["access-control-expose-headers"]
            assert "mcp-session-id" in exposed
            assert "www-authenticate" in exposed


def test_a_challenged_response_exposes_the_session_and_authenticate_headers():
    client = _client()
    response = client.get(
        "/.well-known/oauth-protected-resource/mcp",
        headers={"Origin": "https://claude.ai"},
    )
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "https://claude.ai"
    exposed = response.headers["access-control-expose-headers"]
    assert "mcp-session-id" in exposed
    assert "www-authenticate" in exposed
    assert response.headers["www-authenticate"].startswith("Bearer ")
    assert response.headers["mcp-session-id"] == "sess-1"


def test_an_unlisted_origin_is_refused_on_discovery_too():
    client = _client()
    response = client.get(
        "/.well-known/oauth-authorization-server",
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "origin_not_allowed"
    assert "access-control-allow-origin" not in response.headers

    preflight = client.options(
        "/.well-known/oauth-protected-resource/mcp",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type, mcp-protocol-version",
        },
    )
    assert preflight.status_code == 403
    assert preflight.json()["error"] == "origin_not_allowed"
    assert "access-control-allow-origin" not in preflight.headers


def test_a_missing_origin_is_left_alone():
    client = _client()
    response = client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers
