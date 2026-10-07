"""WorkOS AuthKit Resource Server provider.

Our MCP server is a pure OAuth 2.1 Resource Server: **WorkOS AuthKit is the
Authorization Server.** This provider validates an incoming AuthKit access-token
JWT (PyJWT + JWKS) and resolves it to an :class:`Identity` whose ``user_id`` is
the token's ``sub`` — the stable WorkOS user id we use as the founder key.

The OAuth-flow methods (DCR / authorize / token exchange) are AuthKit's job, not
ours; they raise :class:`NotImplementedError` here. Clients obtain tokens from
AuthKit, which they discover via our Protected Resource Metadata.

Design + live staging values: ``docs/reference/workos-authkit-integration.md``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import jwt
from jwt import PyJWKClient

from tinyassets.auth.provider import AuthProvider, Identity
from tinyassets.principals import named_principal

logger = logging.getLogger("universe_server.auth.workos")

# Base capabilities EVERY authenticated founder holds. Per OAuth best practice
# (tokens/scopes are for authentication + coarse delegation, NOT fine-grained
# authorization — Curity/Auth0/Aserto) and the multi-tenant "first user owns the
# tenant" pattern, an authenticated founder can create and write their OWN
# universe. The REAL authorization boundary is the per-universe ownership ACL
# (permissions.universe_access_allows), which confines a founder to the universes
# they own — so granting the coarse delegated `write`/`costly` capabilities here
# is safe: cross-universe writes are still denied by the ACL. `admin` (platform
# actions) is NOT implicit; it stays RBAC-gated via the token's `permissions`
# claim. Finer per-universe collaboration roles layer on top via ACL grants, not
# OAuth scopes. (Supersedes the earlier D0b RBAC-gated-write model, which broke
# self-serve first-contact: AuthKit issues only OIDC scopes, so a self-serve
# founder never received write/costly and could not create their own universe.)
_AUTHENTICATED_BASE_CAPABILITIES = ("read", "write", "costly", "submit_request", "list")

_ALGORITHMS = ("RS256",)

# Explicit dev-only opt-out for running without audience (resource-indicator)
# binding. Production MUST register the MCP URL as a WorkOS Resource Indicator
# and set ``WORKOS_MCP_RESOURCE`` so tokens are bound to this server.
_ALLOW_NO_AUDIENCE_TRUTHY = ("1", "true", "yes", "on")
_TOKEN_REJECTION_LOG_WINDOW_SECONDS = 60.0
_TOKEN_REJECTION_CATEGORIES = frozenset({
    "algorithm",
    "audience",
    "expired",
    "invalid_subject",
    "invalid_token",
    "issuer",
    "malformed",
    "required_claim",
    "signature",
    "signing_key",
})
_token_rejection_log_lock = threading.Lock()
_token_rejection_log_state: dict[str, tuple[float, int]] = {}
_rejection_log_now = time.monotonic


def _validation_failure_category(error: jwt.PyJWTError) -> str:
    """Map PyJWT failures to a bounded category without exposing details."""
    categories = (
        (jwt.ExpiredSignatureError, "expired"),
        (jwt.InvalidAudienceError, "audience"),
        (jwt.InvalidIssuerError, "issuer"),
        (jwt.MissingRequiredClaimError, "required_claim"),
        (jwt.InvalidSignatureError, "signature"),
        (jwt.InvalidAlgorithmError, "algorithm"),
        (jwt.DecodeError, "malformed"),
    )
    for error_type, category in categories:
        if isinstance(error, error_type):
            return category
    return "invalid_token"


def _log_token_rejection(category: str) -> None:
    """Log a bounded safe reason, never token or exception material."""
    safe_category = (
        category if category in _TOKEN_REJECTION_CATEGORIES else "invalid_token"
    )
    now = _rejection_log_now()
    with _token_rejection_log_lock:
        last_logged, suppressed = _token_rejection_log_state.get(
            safe_category,
            (float("-inf"), 0),
        )
        if now - last_logged < _TOKEN_REJECTION_LOG_WINDOW_SECONDS:
            _token_rejection_log_state[safe_category] = (
                last_logged,
                suppressed + 1,
            )
            return
        _token_rejection_log_state[safe_category] = (now, 0)
    logger.warning(
        "WorkOS bearer token rejected category=%s suppressed=%d",
        safe_category,
        suppressed,
    )


def derive_endpoints(authkit_domain: str) -> tuple[str, str]:
    """Return ``(issuer, jwks_uri)`` for an AuthKit domain.

    Confirmed against live AS metadata 2026-06-26:
      ``issuer   = https://<domain>``
      ``jwks_uri = https://<domain>/oauth2/jwks``
    Accepts a bare domain (``foo.authkit.app``) or a full origin.
    """
    domain = (authkit_domain or "").strip().rstrip("/")
    if not domain:
        raise ValueError("authkit_domain must be non-empty")
    base = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
    return base, f"{base}/oauth2/jwks"


class WorkOSAuthProvider(AuthProvider):
    """Validate WorkOS AuthKit bearer JWTs; resolve ``sub`` -> founder Identity."""

    def __init__(
        self,
        *,
        issuer: str,
        jwks_uri: str,
        audience: str | None = None,
        jwks_client: Any | None = None,
        leeway: float = 60.0,
    ) -> None:
        self._issuer = issuer
        self._jwks_uri = jwks_uri
        self._audience = (audience or "").strip() or None
        self._leeway = leeway
        # PyJWKClient caches keys + handles rotation / ``kid`` matching.
        # Injectable so tests can supply a fake without network access.
        self._jwks_client = (
            jwks_client if jwks_client is not None else PyJWKClient(jwks_uri)
        )

    @classmethod
    def from_env(cls) -> "WorkOSAuthProvider":
        """Build from ``WORKOS_AUTHKIT_DOMAIN`` (+ optional ``WORKOS_MCP_RESOURCE``).

        Audience binding is **required by default** (fail closed): without a
        registered resource indicator, any valid same-issuer WorkOS token would
        authenticate as this MCP user (confused-deputy / token reuse, RFC 8707).
        Set ``WORKOS_ALLOW_NO_AUDIENCE=1`` to deliberately run without audience
        binding in local/dev only.
        """
        domain = os.environ.get("WORKOS_AUTHKIT_DOMAIN", "").strip()
        if not domain:
            raise RuntimeError(
                "WORKOS_AUTHKIT_DOMAIN is required for the WorkOS auth provider."
            )
        issuer, jwks_uri = derive_endpoints(domain)
        audience = os.environ.get("WORKOS_MCP_RESOURCE", "").strip() or None
        if audience is None:
            allow = os.environ.get("WORKOS_ALLOW_NO_AUDIENCE", "").strip().lower()
            if allow not in _ALLOW_NO_AUDIENCE_TRUTHY:
                raise RuntimeError(
                    "WORKOS_MCP_RESOURCE is required: register the MCP URL as a "
                    "WorkOS Resource Indicator and set this env var so tokens are "
                    "bound to this server (audience). Without it any same-issuer "
                    "WorkOS token would authenticate here. To run without audience "
                    "binding in dev only, set WORKOS_ALLOW_NO_AUDIENCE=1 (never in "
                    "production)."
                )
            logger.warning(
                "WORKOS_MCP_RESOURCE unset and WORKOS_ALLOW_NO_AUDIENCE enabled — "
                "audience (aud) validation is DISABLED; any same-issuer WorkOS "
                "token will authenticate. Do not use in production."
            )
        return cls(issuer=issuer, jwks_uri=jwks_uri, audience=audience)

    # --- Resource Server: the methods that matter -------------------------

    def verified_claims(self, token: str) -> dict[str, Any] | None:
        """Validate a resource bearer before inspecting any client/session claims.

        For server-side attribution and the outside-client evidence probe only.
        The returned claims are sensitive: never serialize them into a receipt.
        This does not establish client grants or a reconnect generation.
        """
        if not token or not token.strip():
            return None
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        except jwt.DecodeError:
            _log_token_rejection("malformed")
            return None
        except Exception:
            # kid mismatch or JWKS lookup/fetch failure.
            _log_token_rejection("signing_key")
            return None

        options: dict[str, Any] = {"require": ["exp", "sub"]}
        decode_kwargs: dict[str, Any] = {
            "algorithms": list(_ALGORITHMS),  # pin RS256 (alg-substitution defense)
            "issuer": self._issuer,
            "leeway": self._leeway,
            "options": options,
        }
        if self._audience:
            decode_kwargs["audience"] = self._audience
        else:
            options["verify_aud"] = False

        try:
            claims = jwt.decode(token, signing_key.key, **decode_kwargs)
        except jwt.PyJWTError as error:
            _log_token_rejection(_validation_failure_category(error))
            return None

        sub = named_principal(claims.get("sub"))
        if not sub:
            _log_token_rejection("invalid_subject")
            return None

        return claims

    def resolve_token(self, token: str) -> Identity | None:
        claims = self.verified_claims(token)
        if claims is None:
            return None
        sub = named_principal(claims.get("sub"))

        email = str(claims.get("email", "")).strip()
        username = email or sub
        display_name = str(claims.get("name", "")).strip() or username
        permissions = claims.get("permissions")
        granted = (
            [p for p in permissions if isinstance(p, str) and p.strip()]
            if isinstance(permissions, list) else []
        )
        # Capabilities = read-only base + the token's granted RBAC scopes.
        # dict.fromkeys de-dupes while preserving order.
        capabilities = list(
            dict.fromkeys([*_AUTHENTICATED_BASE_CAPABILITIES, *granted])
        )

        import sqlite3

        from tinyassets.outside_authority import OutsideRefused, current_store, origin

        try:
            outside = origin(claims)
            if outside is not None:
                authority = current_store()
                authority.observe(sub, outside)
                outside = authority.admit(sub, outside)
        except (OutsideRefused, OSError, sqlite3.Error):
            return None

        return Identity(
            user_id=sub,
            username=username,
            display_name=display_name,
            capabilities=capabilities,
            metadata={
                "auth_provider": "workos",
                "email": email,
                "org_id": claims.get("org_id"),
                "role": claims.get("role"),
                "permissions": granted,
                "iss": claims.get("iss"),
                **({"outside_origin": outside} if outside is not None else {}),
            },
        )

    def is_auth_required(self) -> bool:
        # The ASGI transport challenges every MCP request. This provider flag
        # remains false so resolve-always action grants use coarse capabilities.
        return False

    def resolve_always_writes(self) -> bool:
        # WorkOS resolves every request before dispatch; action scopes add the
        # per-operation grant check after transport authentication.
        return True

    # --- OAuth flow: AuthKit's job, not the Resource Server's --------------

    def _flow_not_ours(self, what: str) -> Any:
        raise NotImplementedError(
            f"WorkOS AuthKit is the Authorization Server; this Resource Server "
            f"does not {what}. Clients obtain tokens from AuthKit, discovered via "
            f"our Protected Resource Metadata."
        )

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return self._flow_not_ours("register clients (DCR/CIMD)")

    def create_authorization(
        self,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        return self._flow_not_ours("create authorizations")

    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict[str, Any] | None:
        return self._flow_not_ours("exchange codes for tokens")
