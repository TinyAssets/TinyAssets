"""The sign-in half of a ``connect`` request: authorization code + PKCE.

This is the hosted first-power PKCE transport, generalized. The same store
(``pkce.flows_db``), handle grammar and callback route serve both; what changes
is that endpoints come from the offer recorded on the owner's pending request
(from the platform directory or the connection's own host, never supplied), and the
exchange is standard RFC 6749 §4.1.

1. **begin** (the owner tapped "Sign in"): the browser made a verifier and sends
   only its S256 challenge. The server re-checks the pending request is the one
   the owner was shown, gets a client id (supplied, or RFC 7591 registration of
   a public client), stores a hashed handle bound to owner, universe, request
   and the exact action, and returns the provider's authorize URL with the
   handle as ``state``.
2. The provider sends the browser back to the fixed callback
   ``/app/model-callback/connect?code=..&state=..`` (public shell only).
3. **complete**: the signed-in app posts ``state``, ``code``, the verifier and
   any RFC 9207 ``iss``. The flow is taken exactly once, ``iss`` is checked
   against the discovered issuer (required when the server advertises it), the
   code is exchanged at the DISCOVERED token URL the owner was shown, and the
   token bundle is deposited through the same answer path a pasted key uses,
   under auth scheme ``oauth2``, pinned to that token URL. The response never
   carries a token.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit

from tinyassets.connection_oauth import pkce
from tinyassets.connection_oauth.discovery import register_public_client
from tinyassets.connection_oauth.tokens import encode, exchange_code
from tinyassets.connection_oauth.transport import OAuthError

MAX_PER_OWNER = 10
MAX_PENDING = 1000


class FlowError(Exception):
    """Secret-free flow failure: a fixed code, an HTTP status, optional detail."""

    def __init__(self, code: str, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def callback_origin(public_resource: str) -> str:
    try:
        parts = urlsplit(public_resource)
        valid = (parts.scheme == "https" and parts.hostname and not parts.username
                 and not parts.password and not parts.query and not parts.fragment)
        parts.port  # noqa: B018 - reject malformed ports
    except ValueError:
        valid = False
    if not valid:
        raise FlowError("public_callback_unavailable", 503)
    return f"{parts.scheme}://{parts.netloc}"


def redirect_uri(public_resource: str) -> str:
    return callback_origin(public_resource) + pkce.CONNECT_CALLBACK_PATH


def configured_redirect_uri() -> str:
    """The redirect URI a client must register, or "" when none is configured."""
    try:
        from tinyassets.auth.wellknown import protected_resource_metadata

        return redirect_uri(str(protected_resource_metadata().get("resource") or ""))
    except (FlowError, Exception):  # noqa: BLE001 - advisory only
        return ""


def action_digest(action: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(action, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def _pending_connect(universe_id: str, request_id: str) -> dict[str, Any]:
    """The owner's pending ``connect`` request with an OAuth offer, as shown."""
    from tinyassets.api.pending_requests import _owner_gate, displayed_row_matches
    from tinyassets.storage.pending_requests import get_request

    _uid, udir, denied = _owner_gate(universe_id)
    if denied is not None:
        raise FlowError("unknown_request", 404)
    row = get_request(udir, request_id) if request_id else None
    if row is None:
        raise FlowError("unknown_request", 404)
    if row["status"] != "pending":
        raise FlowError("request_already_resolved", 409)
    action = row.get("action") or {}
    from tinyassets.api.pending_requests import _has_sign_in

    if not _has_sign_in(action):
        raise FlowError("request_has_no_sign_in", 409)
    if not displayed_row_matches(row):
        raise FlowError("request_changed", 409)
    return row


def begin(*, owner: str, universe_id: str, request_id: str, challenge: str,
          public_resource: str, owner_session: dict | None = None) -> dict[str, Any]:
    """Start one sign-in for the owner's pending request. No token is involved."""
    if (owner_session is None
            or json.loads(owner_session.get("identity_json", "{}")).get("user_id") != owner):
        raise FlowError("interactive_approval_required", 403)
    if not owner or not universe_id:
        raise FlowError("current_home_required", 409)
    if not isinstance(challenge, str) or not pkce.HANDLE_RE.fullmatch(challenge):
        raise FlowError("invalid_pkce_challenge")
    callback = redirect_uri(public_resource)
    row = _pending_connect(universe_id, request_id)
    offer = row["action"]["oauth"]
    scopes = list(offer.get("scopes") or [])
    from tinyassets.connection_oauth.pkce import cached_client, remember_client

    client_id = offer.get("client_id") or cached_client(offer["issuer"], callback)
    if offer.get("registration_method") == "cached":
        # A stale pending card must not revive an expired registration.
        client_id = cached_client(offer["issuer"], callback) or offer.get("fallback_client_id", "")
    if not client_id:
        if not offer.get("registration_url"):
            raise FlowError("registration_required", 409)
        try:
            client_id = register_public_client(offer["registration_url"],
                                               redirect_uri=callback, scopes=scopes)
            remember_client(offer["issuer"], callback, client_id, "dcr")
        except (OAuthError, KeyError) as exc:
            code = exc.code if isinstance(exc, OAuthError) else "client_registration_failed"
            raise FlowError(code, 502, getattr(exc, "detail", "")) from None
    from tinyassets.api.helpers import _base_path

    handle = secrets.token_urlsafe(32)
    with pkce.flows_db(_base_path()) as (conn, now):
        total, mine = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(owner_user_id = ?), 0) FROM connection_oauth_flows",
            (owner,),
        ).fetchone()
        if total >= MAX_PENDING or mine >= MAX_PER_OWNER:
            raise FlowError("too_many_pending_connections", 429)
        conn.execute(
            "INSERT INTO connection_oauth_flows "
            "(handle_digest, owner_user_id, universe_id, request_id, action_digest, challenge, "
            "client_id, redirect_uri, created_at, expires_at, approved_owner) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pkce.handle_digest(handle), owner, universe_id, request_id,
             action_digest(row["action"]), challenge, client_id, callback, now,
             now + pkce.FLOW_TTL_SECONDS, owner),
        )
    query = {
        **offer.get("extra_auth_params", {}),
        "response_type": "code", "client_id": client_id, "redirect_uri": callback,
        "state": handle, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    if offer.get("resource"):
        query["resource"] = offer["resource"]
    if scopes:
        query["scope"] = " ".join(scopes)
    return {
        "flow": handle,
        "authorize_url": offer["authorize_url"] + "?" + urlencode(query),
        "authorize_host": urlsplit(offer["authorize_url"]).hostname or "",
        "expires_in": pkce.FLOW_TTL_SECONDS,
    }


def complete(*, owner: str, universe_id: str, handle: str, code: str,
             verifier: str, iss: str = "") -> dict[str, Any]:
    """Redeem the code once and deposit the tokens as the owner's answer."""
    if not isinstance(handle, str) or not pkce.HANDLE_RE.fullmatch(handle) or not owner:
        raise FlowError("unknown_sign_in", 404)
    if (not isinstance(code, str) or not 1 <= len(code) <= 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in code)):
        raise FlowError("invalid_authorization_code")
    if not isinstance(verifier, str) or not pkce.VERIFIER_RE.fullmatch(verifier):
        raise FlowError("invalid_pkce_verifier")
    from tinyassets.api.helpers import _base_path

    digest = pkce.handle_digest(handle)
    with pkce.flows_db(_base_path()) as (conn, now):
        flow = conn.execute("SELECT * FROM connection_oauth_flows WHERE handle_digest = ?",
                            (digest,)).fetchone()
        # Another user's (or an unknown) handle cannot consume the owner's flow.
        if flow is None or flow["owner_user_id"] != owner:
            raise FlowError("unknown_sign_in", 404)
        if flow["approved_owner"] != owner:
            raise FlowError("interactive_approval_required", 403)
        if flow["universe_id"] != universe_id:
            raise FlowError("current_home_changed", 409)
        if not hmac.compare_digest(pkce.challenge_for(verifier), flow["challenge"]):
            raise FlowError("invalid_pkce_verifier")
        # One redemption attempt: once taken, an uncertain outcome means sign
        # in again, never a replay of the code.
        conn.execute("DELETE FROM connection_oauth_flows WHERE handle_digest = ?", (digest,))
        flow = dict(flow)
    row = _pending_connect(universe_id, flow["request_id"])
    if not hmac.compare_digest(action_digest(row["action"]), flow["action_digest"]):
        raise FlowError("request_changed", 409)
    offer = row["action"]["oauth"]
    # RFC 9207: the authorization response names its issuer. A mismatch means
    # the code came from some other server (a mix-up); a server that
    # advertises the parameter must send it.
    if iss and not hmac.compare_digest(iss, str(offer.get("issuer") or "")):
        raise FlowError("issuer_mismatch", 409)
    if not iss and offer.get("iss_parameter_supported") is True:
        raise FlowError("issuer_missing", 409)
    try:
        bundle = exchange_code(token_url=offer["token_url"], client_id=flow["client_id"],
                               code=code, verifier=verifier, redirect_uri=flow["redirect_uri"],
                               provider_id=offer.get("provider_id", ""),
                               resource=offer.get("resource", ""), issuer=offer.get("issuer", ""),
                               scope=" ".join(offer.get("scopes") or []))
    except OAuthError as exc:
        if exc.code == "registration_required":
            from tinyassets.connection_oauth.pkce import forget_client

            forget_client(offer["issuer"], flow["client_id"])
        raise FlowError(exc.code, 502, exc.detail) from None
    if offer.get("source") == "discovered" and offer.get("registration_method") == "dcr":
        from tinyassets.connection_oauth.pkce import remember_client

        remember_client(offer["issuer"], flow["redirect_uri"], flow["client_id"],
                        offer.get("registration_method", "existing"))
    from tinyassets.api.pending_requests import answer_connect_with_token

    result = answer_connect_with_token(
        universe_id=universe_id, request_id=flow["request_id"], token=encode(bundle),
        owner_session={"approved_owner": owner, "flow_digest": digest},
    )
    if result.get("error"):
        raise FlowError(str(result["error"]), 409, str(result.get("detail") or ""))
    return result
