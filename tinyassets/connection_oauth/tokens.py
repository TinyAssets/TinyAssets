"""OAuth 2.0 tokens for a connection: the vault encoding, the exchange, and refresh.

The vault keeps ONE opaque string per connection (``vault://http/<name>``). For
an ``oauth2`` connection that string is a JSON bundle::

    {"scheme": "oauth2", "v": 1, "access_token", "token_type", "expires_at",
     "refresh_token", "token_url", "client_id", "scope"}

The token URL and client id live in the bundle, not on the readable connection
row, because the refresh token is sent there: only the owner's completed
sign-in writes them, and nothing an agent can configure moves them.

Refresh is generic and happens inside the credential-blind broker, before the
access token is within :data:`REFRESH_SKEW_SECONDS` of expiry and again once
when the service answers 401. It is **single-flight per connection** across
threads and processes (a per-connection lock, and a re-read of the vault inside
it), so concurrent calls never spend a single-use refresh token twice. A rotated
refresh token is written back through the vault's atomic write before the new
access token is used. The vault's exclusive admission is taken BEFORE the
refresh token is spent, so a token the provider rotates can always be saved: if
the vault cannot be held, nothing is spent and the call fails retryably.

Refresh ordering and custody writes live in ``tinyassets.broker.tokens``.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote_plus

from tinyassets.connection_oauth.transport import (
    OAuthError,
    request_json,
    server_error_detail,
    validate_https_url,
)

SCHEME = "oauth2"
REFRESH_SKEW_SECONDS = 60.0
#: How long a caller waits for another holder's refresh before failing loudly.
LOCK_WAIT_SECONDS = 45.0
_MAX_TOKEN_CHARS = 8192


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    token_url: str
    client_id: str
    refresh_token: str = ""
    expires_at: float | None = None
    scope: str = ""
    token_type: str = "Bearer"
    provider_id: str = ""
    resource: str = ""
    issuer: str = ""

    def expiring(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False  # unknown lifetime: refresh only when the service says 401
        return (now if now is not None else time.time()) >= self.expires_at - REFRESH_SKEW_SECONDS

    def secret_values(self) -> tuple[str, ...]:
        return tuple(v for v in (self.access_token, self.refresh_token) if v)


def encode(bundle: TokenBundle) -> str:
    return json.dumps({
        "scheme": SCHEME, "v": 1, "access_token": bundle.access_token,
        "token_type": bundle.token_type, "expires_at": bundle.expires_at,
        "refresh_token": bundle.refresh_token, "token_url": bundle.token_url,
        "client_id": bundle.client_id, "scope": bundle.scope,
        **({"provider_id": bundle.provider_id} if bundle.provider_id else {}),
        **({"resource": bundle.resource} if bundle.resource else {}),
        **({"issuer": bundle.issuer} if bundle.issuer else {}),
    }, sort_keys=True, separators=(",", ":"))


def looks_like_bundle(text: Any) -> bool:
    """True for the deposited oauth2 encoding. Never returns or logs values."""
    if not isinstance(text, str) or not text.lstrip().startswith("{"):
        return False
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("scheme") == SCHEME


def _token(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= _MAX_TOKEN_CHARS
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError("token is not a printable single value")
    return value


def decode(text: str) -> TokenBundle:
    if not looks_like_bundle(text):
        raise ValueError("credential is not an oauth2 token bundle")
    doc = json.loads(text)
    expires = doc.get("expires_at")
    if expires is not None and (isinstance(expires, bool) or not isinstance(expires, (int, float))):
        raise ValueError("expires_at must be a number")
    refresh = doc.get("refresh_token") or ""
    return TokenBundle(
        access_token=_token(doc.get("access_token")),
        token_url=validate_https_url(doc.get("token_url")),
        client_id=_token(doc.get("client_id")),
        refresh_token=_token(refresh) if refresh else "",
        expires_at=float(expires) if expires is not None else None,
        scope=str(doc.get("scope") or ""),
        token_type=str(doc.get("token_type") or "Bearer"),
        provider_id=str(doc.get("provider_id") or ""),
        resource=validate_https_url(doc["resource"]) if doc.get("resource") else "",
        issuer=validate_https_url(doc["issuer"]) if doc.get("issuer") else "",
    )


def _token_response(status: int, doc: Any, *, secrets: tuple[str, ...]) -> dict[str, Any]:
    if status != 200 or not isinstance(doc, dict):
        recovery = {"invalid_client": "registration_required",
                    "unauthorized_client": "registration_required",
                    "invalid_grant": "reconnect_required", "invalid_scope": "insufficient_scope"}
        error = doc.get("error") if isinstance(doc, dict) else ""
        error = error if isinstance(error, str) else ""
        code = recovery.get(error, "token_request_failed")
        raise OAuthError(code, server_error_detail(status, doc, secrets),
                         status=status)
    try:
        _token(doc.get("access_token"))
    except ValueError:
        raise OAuthError("token_response_invalid", "no usable access_token") from None
    token_type = str(doc.get("token_type") or "Bearer")
    if token_type.lower() != "bearer":
        # A sender-constrained type (DPoP, MAC) needs proof the broker does not
        # build. Refused loudly rather than sent as something it is not.
        raise OAuthError("unsupported_token_type", "token_type must be Bearer")
    return doc


def _expires_at(doc: dict[str, Any], now: float) -> float | None:
    value = doc.get("expires_in")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return now + float(value)


def _request_token(token_url: str, client_id: str, provider_id: str,
                   form: dict[str, str], secrets: tuple[str, ...]) -> dict[str, Any]:
    """Authenticate only at a re-pinned directory token endpoint."""
    basic_auth = None
    sensitive: tuple[str, ...] = ()
    if provider_id:
        from tinyassets.connection_oauth.directory import registered, secret

        row = registered(provider_id, client_id=client_id, token_url=token_url)
        value = secret(row["client_secret_env"])
        encoded = base64.b64encode(
            f"{quote_plus(client_id)}:{quote_plus(value)}".encode()).decode()
        sensitive = (value, quote_plus(value), encoded)
        if row["token_endpoint_auth_method"] == "client_secret_basic":
            basic_auth = (quote_plus(client_id), quote_plus(value))
            form.pop("client_id", None)
        else:
            form["client_secret"] = value
    status, doc = request_json("POST", token_url, form=form, secrets=secrets + sensitive,
                               **({"basic_auth": basic_auth} if basic_auth else {}))
    if provider_id:
        # A confidential endpoint's response is untrusted: even a success can
        # echo the client credential in a token, scope, or an error description.
        # No provider prose is needed to diagnose a confidential exchange.
        if status != 200 or not isinstance(doc, dict):
            raise OAuthError("token_request_failed", f"HTTP {status}", status=status)
        def echoes(value: Any) -> bool:
            if isinstance(value, str):
                return any(s and s in value for s in sensitive)
            if isinstance(value, dict):
                return any(echoes(k) or echoes(v) for k, v in value.items())
            if isinstance(value, list):
                return any(echoes(v) for v in value)
            return False

        if echoes(doc):
            raise OAuthError("token_response_invalid")
    return _token_response(status, doc, secrets=secrets + sensitive)


def exchange_code(*, token_url: str, client_id: str, code: str, verifier: str,
                  redirect_uri: str, provider_id: str = "", resource: str = "",
                  issuer: str = "", scope: str = "") -> TokenBundle:
    """RFC 6749 §4.1.3 with the RFC 7636 verifier; exactly one attempt."""
    now = time.time()
    doc = _request_token(token_url, client_id, provider_id, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": verifier,
        **({"resource": validate_https_url(resource)} if resource else {}),
    }, (code, verifier))
    return TokenBundle(
        access_token=doc["access_token"], token_url=token_url, client_id=client_id,
        refresh_token=_token(doc["refresh_token"]) if doc.get("refresh_token") else "",
        expires_at=_expires_at(doc, now),
        scope=str(doc["scope"] if doc.get("scope") is not None else scope),
        provider_id=provider_id, resource=resource, issuer=issuer,
    )


def refresh(bundle: TokenBundle) -> TokenBundle:
    """RFC 6749 §6. A server that rotates returns a new refresh token; one that
    does not leaves the old one valid, so it is kept."""
    if not bundle.refresh_token:
        raise OAuthError("reconnect_required")
    now = time.time()
    secrets = bundle.secret_values()
    doc = _request_token(bundle.token_url, bundle.client_id, bundle.provider_id, {
        "grant_type": "refresh_token", "refresh_token": bundle.refresh_token,
        "client_id": bundle.client_id,
        **({"resource": bundle.resource} if bundle.resource else {}),
    }, secrets)
    rotated = doc.get("refresh_token")
    return replace(
        bundle, access_token=doc["access_token"],
        refresh_token=_token(rotated) if rotated else bundle.refresh_token,
        expires_at=_expires_at(doc, now),
        scope=str(doc["scope"] if doc.get("scope") is not None else bundle.scope),
    )
