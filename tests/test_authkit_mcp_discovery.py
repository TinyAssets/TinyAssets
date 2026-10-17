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
from tests.owner_app_fixture import create_owner_app as create_streamable_http_app
from tinyassets.auth.middleware import set_provider
from tinyassets.auth.provider import DevAuthProvider
from tinyassets.auth.workos_provider import WorkOSAuthProvider

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


def _cors(response) -> str:
    return response.headers.get("access-control-allow-origin", "")


def test_browser_preflight_is_not_an_auth_challenge(authkit_client) -> None:
    client, _key = authkit_client
    preflight = client.options(
        "/mcp",
        headers={
            "Origin": "https://claude.ai",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type, mcp-session-id",
        },
    )
    assert preflight.status_code == 204, preflight.text
    assert _cors(preflight) == "https://claude.ai"
    allow_headers = preflight.headers["access-control-allow-headers"]
    assert "authorization" in allow_headers
    assert "mcp-session-id" in allow_headers
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "www-authenticate" in preflight.headers["access-control-expose-headers"]

    chatgpt = client.options(
        "/mcp/",
        headers={
            "Origin": "https://chatgpt.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert chatgpt.status_code == 204
    assert _cors(chatgpt) == "https://chatgpt.com"

    local = client.options(
        "/mcp",
        headers={
            "Origin": "http://127.0.0.1:6274",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert local.status_code == 204
    assert _cors(local) == "http://127.0.0.1:6274"


def test_unlisted_browser_origin_is_refused(authkit_client) -> None:
    client, key = authkit_client
    token = _access_token(key)
    refused = client.post(
        "/mcp",
        headers={**_headers(token), "Origin": "https://evil.example"},
        json=_initialize_body(),
    )
    assert refused.status_code == 403
    assert refused.json()["error"] == "origin_not_allowed"
    assert _cors(refused) == ""


def test_missing_origin_still_completes_discovery(authkit_client) -> None:
    client, key = authkit_client
    token = _access_token(key)
    _handshake(client, token)
    probe = client.get(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        },
    )
    assert probe.status_code == 405
    assert _cors(probe) == ""


def test_allowed_origin_can_read_the_auth_challenge(authkit_client) -> None:
    client, _key = authkit_client
    anonymous = client.get(
        "/mcp",
        headers={"Accept": "text/event-stream", "Origin": "https://tinyassets.io"},
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["error"] == "authentication_required"
    assert _cors(anonymous) == "https://tinyassets.io"
    assert "www-authenticate" in anonymous.headers["access-control-expose-headers"]


def test_expired_access_token_is_invalid_token(authkit_client) -> None:
    client, key = authkit_client
    expired = client.post(
        "/mcp",
        headers=_headers(_access_token(key, exp=int(time.time()) - 60)),
        json=_initialize_body(),
    )
    assert expired.status_code == 401
    assert expired.json()["error"] == "invalid_token"


def test_deleting_a_session_retires_it(authkit_client) -> None:
    client, key = authkit_client
    token = _access_token(key)
    session_id = _handshake(client, token)
    deleted = client.delete("/mcp", headers=_headers(token, session_id=session_id))
    assert deleted.status_code in {200, 202, 204}, deleted.text
    again = client.post(
        "/mcp",
        headers=_headers(token, session_id=session_id),
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
    )
    assert again.status_code == 404


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
