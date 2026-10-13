"""Well-known endpoint routes for OAuth discovery.

Implements the MCP authorization discovery spec:
  - /.well-known/oauth-authorization-server (RFC 8414)
  - /.well-known/oauth-protected-resource (RFC 9728)

These endpoints let MCP clients auto-discover how to authenticate
without any pre-configuration. The client reads these metadata
documents, finds the authorization/token/registration endpoints,
and runs the OAuth flow automatically.

Usage:
    These routes are mounted on the same HTTP server as the MCP
    transport. When FastMCP runs with streamable-http, it uses
    Starlette/uvicorn under the hood — these routes integrate
    with that app.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("universe_server.auth")


def _server_url() -> str:
    """Get the public-facing server URL."""
    return os.environ.get(
        "UNIVERSE_SERVER_URL",
        f"http://localhost:{os.environ.get('UNIVERSE_SERVER_PORT', '8001')}",
    )


def authorization_server_metadata() -> dict[str, Any]:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414).

    Served at: /.well-known/oauth-authorization-server

    This tells MCP clients where to find:
      - authorization endpoint (for user consent)
      - token endpoint (for code exchange)
      - registration endpoint (for DCR)
    """
    base = _server_url()
    from tinyassets.auth.provider import supported_oauth_scopes

    return {
        "issuer": base,
        # Under /mcp so this is the existing public door, not a new /oauth prefix.
        "authorization_endpoint": f"{base}/mcp/oauth/authorize",
        "token_endpoint": f"{base}/mcp/oauth/token",
        "registration_endpoint": f"{base}/mcp/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": supported_oauth_scopes(),
        "service_documentation": f"{base}/docs",
    }


def resource_metadata_url(resource: str) -> str:
    """RFC 9728 path insertion for a protected-resource identifier.

    ``https://tinyassets.io/mcp`` is fetched at
    ``https://tinyassets.io/.well-known/oauth-protected-resource/mcp``.
    A resource with no path uses the apex document. The legacy URL under the
    resource path keeps serving the same document; it is not this URL.
    """
    from urllib.parse import urlsplit, urlunsplit

    raw = resource.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return f"{raw}/.well-known/oauth-protected-resource"
    suffix = "" if parts.path in ("", "/") else parts.path
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        "/.well-known/oauth-protected-resource" + suffix,
        "",
        "",
    ))


def protected_resource_metadata() -> dict[str, Any]:
    """OAuth Protected Resource Metadata (RFC 9728).

    Served at: /.well-known/oauth-protected-resource

    This tells MCP clients which authorization server protects this resource,
    so they know where to start the auth flow. In WorkOS mode the authorization
    server is AuthKit (not us): we advertise the AuthKit issuer + the registered
    MCP resource indicator so a client discovers AuthKit and binds tokens to
    this server. In legacy/OAuth mode we are our own authorization server.
    """
    base = _server_url()
    from tinyassets.auth.provider import supported_oauth_scopes

    auth_mode = os.environ.get("UNIVERSE_SERVER_AUTH", "").strip().lower()
    if auth_mode == "workos":
        domain = os.environ.get("WORKOS_AUTHKIT_DOMAIN", "").strip()
        resource = os.environ.get("WORKOS_MCP_RESOURCE", "").strip() or base
        authorization_servers = [base]
        if domain:
            from tinyassets.auth.workos_provider import derive_endpoints

            issuer, _ = derive_endpoints(domain)
            authorization_servers = [issuer]
        return {
            "resource": resource,
            "authorization_servers": authorization_servers,
            # AuthKit (the real AS) only grants standard OIDC scopes. Advertising
            # our internal tinyassets.* action scopes here makes clients request
            # them at authorize, and AuthKit rejects the flow with invalid_scope
            # (the dogfood failure). Per-action authorization is enforced at the
            # Resource Server via founder grants, NOT via OAuth scopes — so the
            # PRM must advertise only what AuthKit can actually issue.
            "scopes_supported": ["openid", "profile", "email", "offline_access"],
            "bearer_methods_supported": ["header"],
        }

    return {
        "resource": base,
        "authorization_servers": [base],
        "scopes_supported": supported_oauth_scopes(),
        "bearer_methods_supported": ["header"],
    }


def starlette_discovery_routes() -> list[Any]:
    """Starlette ``Route`` objects for OAuth discovery, to mount on the HTTP app.

    Serves Protected Resource Metadata at both the root well-known path and the
    ``/mcp``-prefixed path some MCP clients probe, plus Authorization Server
    Metadata (used by legacy OAuth mode; in WorkOS mode clients read AuthKit's
    AS metadata discovered via PRM).
    """
    from starlette.routing import Route

    return [
        Route(
            "/.well-known/oauth-protected-resource",
            _handle_protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            # RFC 9728 path insertion. A resource of
            # https://tinyassets.io/mcp is fetched here, not under /mcp.
            "/.well-known/oauth-protected-resource/mcp",
            _handle_protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/mcp/.well-known/oauth-protected-resource",
            _handle_protected_resource_metadata,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-authorization-server",
            _handle_authz_server_metadata,
            methods=["GET"],
        ),
        Route(
            # RFC 8414 path insertion for an issuer whose path is /mcp.
            "/.well-known/oauth-authorization-server/mcp",
            _handle_authz_server_metadata,
            methods=["GET"],
        ),
        Route(
            # Mirror the PRM: in production only /mcp* is routed to the daemon,
            # so a client probing the apex AS-metadata path would 404 and lose
            # the AuthKit proxy fallback. Serve the /mcp-prefixed variant too.
            "/mcp/.well-known/oauth-authorization-server",
            _handle_authz_server_metadata,
            methods=["GET"],
        ),
        Route("/mcp/oauth/register", _handle_register, methods=["POST"]),
        Route("/mcp/oauth/authorize", _handle_authorize, methods=["GET"]),
        Route("/mcp/oauth/token", _handle_token, methods=["POST"]),
    ]


def create_wellknown_routes() -> list[dict[str, Any]]:
    """Create route definitions for well-known endpoints.

    Returns a list of route dicts that can be mounted on the
    HTTP server. Each dict has: path, method, handler.

    When FastMCP exposes its underlying Starlette app, these
    routes can be added directly. Until then, they're available
    as a data structure for manual integration.
    """
    return [
        {
            "path": "/.well-known/oauth-authorization-server",
            "method": "GET",
            "handler": _handle_authz_server_metadata,
            "description": "OAuth 2.0 Authorization Server Metadata (RFC 8414)",
        },
        {
            "path": "/.well-known/oauth-protected-resource",
            "method": "GET",
            "handler": _handle_protected_resource_metadata,
            "description": "OAuth Protected Resource Metadata (RFC 9728)",
        },
        {
            "path": "/mcp/oauth/register",
            "method": "POST",
            "handler": _handle_register,
            "description": "Dynamic Client Registration (RFC 7591)",
        },
        {
            "path": "/mcp/oauth/authorize",
            "method": "GET",
            "handler": _handle_authorize,
            "description": "Authorization endpoint",
        },
        {
            "path": "/mcp/oauth/token",
            "method": "POST",
            "handler": _handle_token,
            "description": "Token endpoint",
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Route Handlers (Starlette-compatible request/response)
# ═══════════════════════════════════════════════════════════════════════════


async def _fetch_authkit_as_metadata(issuer: str) -> dict[str, Any] | None:
    """Proxy AuthKit's Authorization Server Metadata as JSON (RFC 8414).

    WorkOS AuthKit is the authorization server; we fetch and return its
    published metadata so MCP clients that hit our AS-metadata endpoint get
    AuthKit's real endpoints. Returns None on any fetch/parse error so the
    caller can fall back to a redirect.
    """
    import asyncio

    url = f"{issuer}/.well-known/oauth-authorization-server"

    def _get() -> dict[str, Any] | None:
        import json as _json
        import urllib.request

        from tinyassets.observability import dependency

        try:
            with dependency("workos", url) as span:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    span.status = int(getattr(resp, "status", 200) or 0)
                    if span.status != 200:
                        span.error = f"HTTP{span.status}"
                        return None
                    data = _json.loads(resp.read().decode("utf-8"))
        except Exception:
            logger.warning(
                "AuthKit AS metadata proxy failed for %s", url, exc_info=True,
            )
            return None
        return data if isinstance(data, dict) else None

    return await asyncio.to_thread(_get)


def _oauth_provider() -> Any:
    """The process provider, so a code issued here is the one the transport checks."""
    from tinyassets.auth.middleware import _get_provider

    return _get_provider()


def _oauth_not_here(exc: NotImplementedError) -> Any:
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"error": "not_implemented", "error_description": str(exc)},
        status_code=501,
    )


async def _handle_authz_server_metadata(request: Any) -> Any:
    """Serve authorization server metadata.

    In WorkOS mode we are NOT the authorization server — AuthKit is. Proxy
    AuthKit's published AS metadata as JSON (per WorkOS AuthKit MCP docs)
    rather than advertising self-hosted ``/mcp/oauth/*`` endpoints this Resource
    Server does not implement. If the proxy fetch fails, fall back to a
    redirect so discovery still resolves.
    """
    from starlette.responses import JSONResponse, RedirectResponse

    if os.environ.get("UNIVERSE_SERVER_AUTH", "").strip().lower() == "workos":
        domain = os.environ.get("WORKOS_AUTHKIT_DOMAIN", "").strip()
        if domain:
            from tinyassets.auth.workos_provider import derive_endpoints

            issuer, _ = derive_endpoints(domain)
            metadata = await _fetch_authkit_as_metadata(issuer)
            if metadata is not None:
                return JSONResponse(metadata)
            return RedirectResponse(
                f"{issuer}/.well-known/oauth-authorization-server",
                status_code=302,
            )
    return JSONResponse(authorization_server_metadata())


async def _handle_protected_resource_metadata(request: Any) -> Any:
    """Serve protected resource metadata."""
    from starlette.responses import JSONResponse
    return JSONResponse(protected_resource_metadata())


async def _handle_register(request: Any) -> Any:
    """Handle Dynamic Client Registration."""
    from starlette.responses import JSONResponse

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    try:
        result = _oauth_provider().register_client(body)
    except NotImplementedError as exc:
        return _oauth_not_here(exc)
    return JSONResponse(result, status_code=201)


async def _handle_authorize(request: Any) -> Any:
    """Handle authorization request.

    In a full implementation this shows a consent page. For V1 we
    auto-approve — the user's MCP client initiated the flow, and
    the tunnel/local context implies trust.
    """
    from starlette.responses import RedirectResponse

    params = dict(request.query_params)
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    scope = params.get("scope", "read write")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")

    if not client_id or not redirect_uri:
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"error": "invalid_request", "error_description": "Missing client_id or redirect_uri"},
            status_code=400,
        )

    try:
        code = _oauth_provider().create_authorization(
            client_id, redirect_uri, scope, state,
            code_challenge, code_challenge_method,
        )
    except NotImplementedError as exc:
        return _oauth_not_here(exc)
    except ValueError as exc:
        from starlette.responses import JSONResponse
        return JSONResponse(
            {"error": "invalid_client", "error_description": str(exc)},
            status_code=400,
        )

    # Redirect back to client with code
    separator = "&" if "?" in redirect_uri else "?"
    redirect_url = f"{redirect_uri}{separator}code={code}"
    if state:
        redirect_url += f"&state={state}"

    return RedirectResponse(redirect_url, status_code=302)


async def _handle_token(request: Any) -> Any:
    """Handle token exchange."""
    from starlette.responses import JSONResponse

    try:
        body = await request.form()
        body = dict(body)
    except Exception:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

    grant_type = body.get("grant_type", "")
    if grant_type != "authorization_code":
        return JSONResponse(
            {"error": "unsupported_grant_type"},
            status_code=400,
        )

    try:
        result = _oauth_provider().exchange_code(
            code=body.get("code", ""),
            client_id=body.get("client_id", ""),
            redirect_uri=body.get("redirect_uri", ""),
            code_verifier=body.get("code_verifier", ""),
        )
    except NotImplementedError as exc:
        return _oauth_not_here(exc)

    if result is None:
        return JSONResponse(
            {"error": "invalid_grant"},
            status_code=400,
        )

    return JSONResponse(result)
