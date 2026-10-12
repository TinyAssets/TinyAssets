"""OAuth discovery: WorkOS-aware Protected Resource Metadata + route mounting.

Regression for the founder-identity slice-4 Codex review: in WorkOS mode the PRM
must advertise AuthKit as the authorization server, and the well-known routes
must actually be mounted (they previously 404'd).
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.testclient import TestClient

from tinyassets.auth.wellknown import (
    protected_resource_metadata,
    starlette_discovery_routes,
)

AUTHKIT_DOMAIN = "inventive-van-62-staging.authkit.app"
AUTHKIT_ISSUER = "https://inventive-van-62-staging.authkit.app"
MCP_RESOURCE = "https://tinyassets.io/mcp"


def test_prm_default_points_at_self(monkeypatch):
    monkeypatch.delenv("UNIVERSE_SERVER_AUTH", raising=False)
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    prm = protected_resource_metadata()
    assert prm["resource"] == "https://tinyassets.io"
    assert prm["authorization_servers"] == ["https://tinyassets.io"]


def test_prm_workos_points_at_authkit(monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "workos")
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", AUTHKIT_DOMAIN)
    monkeypatch.setenv("WORKOS_MCP_RESOURCE", MCP_RESOURCE)
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    prm = protected_resource_metadata()
    # Authorization server is AuthKit, not us.
    assert prm["authorization_servers"] == [AUTHKIT_ISSUER]
    # Resource is the registered MCP resource indicator (token audience).
    assert prm["resource"] == MCP_RESOURCE
    assert prm["bearer_methods_supported"] == ["header"]
    # Advertise ONLY scopes AuthKit can grant — internal tinyassets.* action
    # scopes here cause the client's authorize to fail invalid_scope (dogfood).
    assert prm["scopes_supported"] == ["openid", "profile", "email", "offline_access"]
    assert not any(s.startswith("tinyassets.") for s in prm["scopes_supported"])


def test_prm_workos_without_resource_falls_back_to_base(monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "workos")
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", AUTHKIT_DOMAIN)
    monkeypatch.delenv("WORKOS_MCP_RESOURCE", raising=False)
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    prm = protected_resource_metadata()
    assert prm["authorization_servers"] == [AUTHKIT_ISSUER]
    assert prm["resource"] == "https://tinyassets.io"


def test_discovery_routes_include_prm_paths():
    paths = {r.path for r in starlette_discovery_routes()}
    assert "/.well-known/oauth-protected-resource" in paths
    assert "/.well-known/oauth-protected-resource/mcp" in paths
    assert "/mcp/.well-known/oauth-protected-resource" in paths
    assert "/.well-known/oauth-authorization-server" in paths
    # /mcp-prefixed AS metadata too — prod routes only /mcp*, so the apex path
    # 404s there and a client would lose the AuthKit proxy fallback.
    assert "/mcp/.well-known/oauth-authorization-server" in paths


def test_authz_server_metadata_proxies_authkit_json_in_workos(monkeypatch):
    # WorkOS mode: our AS-metadata endpoint returns AuthKit's metadata as 200
    # JSON (proxy), not a redirect. Fetch is stubbed to avoid network.
    import tinyassets.auth.wellknown as wk

    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "workos")
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", AUTHKIT_DOMAIN)

    async def _fake(issuer):  # noqa: ANN001, ANN202
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth2/authorize",
            "token_endpoint": f"{issuer}/oauth2/token",
        }

    monkeypatch.setattr(wk, "_fetch_authkit_as_metadata", _fake)
    app = Starlette(routes=starlette_discovery_routes())
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-authorization-server",
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.json()["issuer"] == AUTHKIT_ISSUER


def test_authz_server_metadata_falls_back_to_redirect_when_proxy_fails(monkeypatch):
    import tinyassets.auth.wellknown as wk

    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "workos")
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", AUTHKIT_DOMAIN)

    async def _fail(issuer):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(wk, "_fetch_authkit_as_metadata", _fail)
    app = Starlette(routes=starlette_discovery_routes())
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-authorization-server",
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert r.headers["location"] == (
        f"{AUTHKIT_ISSUER}/.well-known/oauth-authorization-server"
    )


def test_authz_server_metadata_self_hosted_when_not_workos(monkeypatch):
    monkeypatch.delenv("UNIVERSE_SERVER_AUTH", raising=False)
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    app = Starlette(routes=starlette_discovery_routes())
    client = TestClient(app)
    r = client.get(
        "/.well-known/oauth-authorization-server",
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.json()["issuer"] == "https://tinyassets.io"


def test_prm_routes_served_200(monkeypatch):
    # The mounting fix: the well-known paths must resolve (not 404).
    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "workos")
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", AUTHKIT_DOMAIN)
    monkeypatch.setenv("WORKOS_MCP_RESOURCE", MCP_RESOURCE)
    app = Starlette(routes=starlette_discovery_routes())
    client = TestClient(app)
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/mcp/.well-known/oauth-protected-resource",
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        body = resp.json()
        assert body["authorization_servers"] == [AUTHKIT_ISSUER]
