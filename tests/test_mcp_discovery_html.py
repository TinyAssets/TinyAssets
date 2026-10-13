"""Tests for substrate-fix #11 / Family A Phase 1.A: MCP endpoint discovery.

When a browser GETs canonical /mcp with Accept: text/html, the server should
return a discovery HTML page explaining the endpoint and how to connect.
Default curl and JSON probes should receive compact discovery JSON. MCP
transport requests (POST with JSON-RPC, GET with text/event-stream, or any
request with MCP transport/session headers) must pass through unchanged.
The retired /mcp-directory surface is never a discovery or transport route.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

# Every test here enters the serving lifespan through `client`, which now
# requires an admitted cloud runtime. The challenge/pass-through assertions
# below are unchanged and no admission guard is stubbed.
pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def app():
    from tinyassets.universe_server import create_streamable_http_app

    return create_streamable_http_app()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def test_browser_get_mcp_is_challenged_like_everything_under_mcp(client):
    """A footer visitor with Accept: text/html and no MCP headers gets the
    connect page. The page is static: it names no principal and holds no data."""
    response = client.get("/mcp", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Connect via MCP client" in response.text
    assert "authentication_required" not in response.text


def test_browser_get_mcp_directory_is_an_ordinary_404(client):
    response = client.get(
        "/mcp-directory",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert "location" not in response.headers
    assert response.text == "Not Found"


def test_default_get_mcp_is_challenged_too(client):
    """A curl-style GET is challenged the same way; discovery lives at the
    .well-known routes, which stay public."""
    response = client.get("/mcp", headers={"Accept": "*/*"})
    assert response.status_code == 401
    assert response.json() == {
        "error": "authentication_required",
        "error_description": "Sign in to TinyAssets to continue.",
    }
    well_known = client.get("/mcp/.well-known/oauth-protected-resource")
    assert well_known.status_code == 200


def test_json_get_mcp_directory_is_an_ordinary_404(client):
    response = client.get(
        "/mcp-directory",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert "location" not in response.headers
    assert response.text == "Not Found"


def test_head_mcp_is_challenged(client):
    """HEAD /mcp used to answer discovery headers to anybody; it is challenged
    like every other request to the endpoint."""
    response = client.head("/mcp", headers={"Accept": "text/html"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer ")


def test_get_with_mcp_protocol_version_header_passes_through(client):
    """Real MCP client GET with MCP-Protocol-Version header passes through to
    transport (returns whatever FastMCP returns — usually 405 or transport
    error, NOT the discovery HTML)."""
    response = client.get(
        "/mcp",
        headers={
            "Accept": "text/html",
            "MCP-Protocol-Version": "2025-03-26",
        },
    )
    # Whatever FastMCP returns; the key is it should NOT be the discovery HTML
    assert "TinyAssets MCP Server" not in response.text


def test_get_with_sse_accept_passes_through(client):
    """Streamable HTTP SSE leg should not receive discovery output."""
    response = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert "TinyAssets MCP Server" not in response.text
    assert "mcp_server_endpoint" not in response.text


def test_get_with_mcp_session_header_passes_through(client):
    """Existing Streamable HTTP sessions should not receive discovery JSON."""
    response = client.get(
        "/mcp",
        headers={
            "Accept": "application/json",
            "mcp-session-id": "session-1",
        },
    )
    assert response.status_code != 200 or "mcp_server_endpoint" not in response.text


def test_post_mcp_passes_through(client):
    """POST requests must stay owned by the MCP transport."""
    response = client.post(
        "/mcp",
        headers={"Accept": "text/html"},
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert "TinyAssets MCP Server" not in response.text
    assert "mcp_server_endpoint" not in response.text
