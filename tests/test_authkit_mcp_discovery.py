"""AuthKit bearer through the live streamable-HTTP discovery sequence.

OAuth can succeed and tool discovery can still come back empty. A
streamable-HTTP client GETs ``/mcp`` with ``Accept: text/event-stream`` and
the fresh bearer before it POSTs ``initialize``. The pinned SDK answered
that probe 400 "Missing session ID", which clients treat as a failed
discovery and never follow with ``tools/list``. The spec answer is 405, and
the same bearer must then complete initialize + tools/list.

The token is shaped like an AuthKit user access token: issuer is the AuthKit
domain, ``aud`` is the protected-resource URL, ``scope`` is a space-delimited
string, plus ``client_id``, ``sid`` and ``jti``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.types import ListToolsResult
from starlette.testclient import TestClient

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.auth.middleware import set_provider
from tinyassets.auth.provider import DevAuthProvider
from tinyassets.auth.workos_provider import WorkOSAuthProvider
from tinyassets.universe_server import create_streamable_http_app

pytestmark = pytest.mark.usefixtures("cloud_runtime")

ISSUER = "https://unassuming-environment-16.authkit.app"
RESOURCE = "https://tinyassets.io/mcp"
CLIENT_ID = "client_01M15YZXW7G7X6X1YQ4TG87Q00"
CANONICAL_TOOLS = {
    "read_graph",
    "write_graph",
    "run_graph",
    "read_page",
    "write_page",
    "converse",
    "get_status",
}


class _FakeSigningKey:
    def __init__(self, public_key: object) -> None:
        self.key = public_key


class _FakeJWKSClient:
    def __init__(self, public_key: object) -> None:
        self._key = _FakeSigningKey(public_key)

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return self._key


@pytest.fixture
def authkit_client(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_OUTSIDE_CLIENT_CLAIM", raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    set_provider(
        WorkOSAuthProvider(
            issuer=ISSUER,
            jwks_uri=f"{ISSUER}/oauth2/jwks",
            audience=RESOURCE,
            jwks_client=_FakeJWKSClient(key.public_key()),
        )
    )
    try:
        with TestClient(create_streamable_http_app()) as client:
            yield client, key
    finally:
        set_provider(DevAuthProvider())


def _access_token(key: rsa.RSAPrivateKey, **claims: object) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": ISSUER,
        "sub": "user_01HZXAUTHKIT",
        "aud": RESOURCE,
        "client_id": CLIENT_ID,
        "scope": "openid profile email",
        "sid": "sid_01consent",
        "jti": "jti_01access",
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(claims)
    return jwt.encode(
        payload,
        key,
        algorithm="RS256",
        headers={"kid": "sso_oidc_key_pair_01", "typ": "at+jwt"},
    )


def _headers(token: str, *, session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    return headers


def _response_json(response) -> dict[str, Any]:
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    return response.json()


def _initialize_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "authkit-discovery", "version": "1"},
        },
    }


def _handshake(client: TestClient, token: str, *, path: str = "/mcp") -> str:
    initialize = client.post(
        path,
        headers=_headers(token),
        json=_initialize_body(),
        follow_redirects=False,
    )
    assert initialize.status_code == 200, initialize.text
    assert "location" not in initialize.headers
    session_id = initialize.headers["mcp-session-id"]
    initialized = client.post(
        "/mcp",
        headers=_headers(token, session_id=session_id),
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert initialized.status_code in {200, 202}
    listed = client.post(
        "/mcp",
        headers=_headers(token, session_id=session_id),
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200, listed.text
    result = ListToolsResult.model_validate(_response_json(listed)["result"])
    assert {tool.name for tool in result.tools} == CANONICAL_TOOLS
    return session_id


def test_authkit_bearer_probe_then_lists_tools(authkit_client) -> None:
    """The GET a client sends the moment OAuth returns must not end discovery."""
    client, key = authkit_client
    token = _access_token(key)

    anonymous = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"] == "authentication_required"

    probe = client.get(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        },
    )
    assert probe.status_code == 405, probe.text
    assert "Missing session ID" not in probe.text
    assert probe.json()["error"]["message"] == "Method Not Allowed"
    assert "POST" in probe.headers["allow"]
    assert probe.headers["cache-control"] == "no-store"

    _handshake(client, token)

    # A GET that already names a session is the SSE leg, not the probe.
    # An unknown id is the transport's 404, not this middleware's 405.
    unknown = client.get(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
            "mcp-session-id": "not-a-session",
        },
    )
    assert unknown.status_code == 404
    assert "Method Not Allowed" not in unknown.text


def test_authkit_bearer_on_the_slash_url_lists_tools(authkit_client) -> None:
    """``/mcp/`` is the same resource. A 307 would send the bearer to the tunnel host."""
    client, key = authkit_client
    _handshake(client, _access_token(key), path="/mcp/")


def test_client_id_audience_is_still_rejected(authkit_client) -> None:
    """An AuthKit token whose aud is the environment client id is not this resource."""
    client, key = authkit_client
    refused = client.post(
        "/mcp",
        headers=_headers(_access_token(key, aud=CLIENT_ID)),
        json=_initialize_body(),
    )
    assert refused.status_code == 401
    assert refused.json()["error"] == "invalid_token"
