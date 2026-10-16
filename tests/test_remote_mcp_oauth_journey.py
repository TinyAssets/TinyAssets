"""Remote MCP sign-in, end to end, on a local server.

Discovery, dynamic registration, authorization code + PKCE, token,
initialize, tools/list, and one tools/call. A browser GET of /mcp is the
connect page; a client that speaks streamable HTTP still gets the 401
challenge and can fetch the resource_metadata URL it names.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp.types import CallToolResult, ListToolsResult
from starlette.testclient import TestClient

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.owner_app_fixture import create_owner_app as create_streamable_http_app
from tinyassets.auth.middleware import set_provider
from tinyassets.auth.provider import DevAuthProvider, OAuthProvider

pytestmark = pytest.mark.usefixtures("cloud_runtime")

REDIRECT = "http://127.0.0.1:9/callback"
SERVER = "http://testserver"


def _pkce() -> tuple[str, str]:
    verifier = "a" * 64
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _json(response) -> dict[str, Any]:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    return response.json()


def _mcp(token: str, *, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_URL", SERVER)
    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "oauth")
    monkeypatch.delenv("WORKOS_MCP_RESOURCE", raising=False)
    monkeypatch.delenv("WORKOS_AUTHKIT_DOMAIN", raising=False)
    monkeypatch.setenv("UNIVERSE_SERVER_OAUTH_SUBJECT", "journey-owner")
    provider = OAuthProvider(tmp_path / "auth.db")
    set_provider(provider)
    try:
        with TestClient(create_streamable_http_app()) as test_client:
            yield test_client
    finally:
        set_provider(DevAuthProvider())


def test_oauth_discovery_through_one_tool_call(client: TestClient) -> None:
    page = client.get("/mcp", headers={"Accept": "text/html"})
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Connect via MCP client" in page.text

    anonymous = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["error"] == "authentication_required"
    challenge = anonymous.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert "resource_metadata=" in challenge
    metadata_url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
    assert metadata_url == f"{SERVER}/.well-known/oauth-protected-resource"

    prm = client.get(urlsplit(metadata_url).path)
    assert prm.status_code == 200, prm.text
    assert prm.headers["content-type"].startswith("application/json")
    inserted = client.get("/.well-known/oauth-protected-resource/mcp")
    assert inserted.status_code == 200, inserted.text
    assert inserted.json() == prm.json()
    document = prm.json()
    assert document["resource"] == SERVER
    assert document["authorization_servers"] == [SERVER]
    assert document["bearer_methods_supported"] == ["header"]

    as_meta = client.get("/.well-known/oauth-authorization-server")
    inserted_as = client.get("/.well-known/oauth-authorization-server/mcp")
    assert as_meta.status_code == 200, as_meta.text
    assert inserted_as.json() == as_meta.json()
    meta = as_meta.json()
    assert meta["authorization_endpoint"] == f"{SERVER}/mcp/oauth/authorize"
    assert meta["token_endpoint"] == f"{SERVER}/mcp/oauth/token"
    assert meta["registration_endpoint"] == f"{SERVER}/mcp/oauth/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]

    registered = client.post(
        "/mcp/oauth/register",
        json={
            "client_name": "remote-journey",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]

    verifier, code_challenge = _pkce()
    authorized = client.get(
        "/mcp/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "state": "journey-state",
            "scope": "read write",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert authorized.status_code == 302, authorized.text
    location = urlsplit(authorized.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT
    query = parse_qs(location.query)
    assert query["state"] == ["journey-state"]
    code = query["code"][0]

    token_response = client.post(
        "/mcp/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == 200, token_response.text
    access_token = token_response.json()["access_token"]
    assert token_response.json()["token_type"] == "Bearer"

    initialize = client.post(
        "/mcp",
        headers=_mcp(access_token),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "remote-journey", "version": "1"},
            },
        },
    )
    assert initialize.status_code == 200, initialize.text
    session_id = initialize.headers["mcp-session-id"]
    initialized = client.post(
        "/mcp",
        headers=_mcp(access_token, session_id=session_id),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code in {200, 202}

    listed = client.post(
        "/mcp",
        headers=_mcp(access_token, session_id=session_id),
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200, listed.text
    tools = ListToolsResult.model_validate(_json(listed)["result"])
    assert "get_status" in {tool.name for tool in tools.tools}

    called = client.post(
        "/mcp",
        headers=_mcp(access_token, session_id=session_id),
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_status", "arguments": {}},
        },
    )
    assert called.status_code == 200, called.text
    result = CallToolResult.model_validate(_json(called)["result"])
    assert result.isError is not True
    assert result.content
