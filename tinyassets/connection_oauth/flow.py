"""Router PKCE state over credential-free definitions; exchanges run in the broker.

The flow freezes the offered endpoints and policy at the owner's protected gesture.
Its request reference is opaque: the door never opens a pending-request store.
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


def begin(*, owner: str, universe_id: str, request_id: str, challenge: str,
          public_resource: str, owner_session: dict | None = None,
          definition: dict[str, Any]) -> dict[str, Any]:
    """Start one sign-in for the offered definition. No token is involved."""
    if (owner_session is None
            or json.loads(owner_session.get("identity_json", "{}")).get("user_id") != owner):
        raise FlowError("interactive_approval_required", 403)
    if not owner or not universe_id:
        raise FlowError("current_home_required", 409)
    if not isinstance(challenge, str) or not pkce.HANDLE_RE.fullmatch(challenge):
        raise FlowError("invalid_pkce_challenge")
    from tinyassets.api.helpers import _base_path
    from tinyassets.broker.owner_identities import connection_center

    connection_center(_base_path(), principal=owner, center=universe_id)
    callback = redirect_uri(public_resource)
    offer = definition["oauth"]
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
                                               redirect_uri=callback)
            remember_client(offer["issuer"], callback, client_id, "dcr")
        except (OAuthError, KeyError) as exc:
            code = exc.code if isinstance(exc, OAuthError) else "client_registration_failed"
            raise FlowError(code, 502, getattr(exc, "detail", "")) from None
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
            "client_id, redirect_uri, created_at, expires_at, approved_owner, definition_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pkce.handle_digest(handle), owner, universe_id, request_id,
             action_digest(definition), challenge, client_id, callback, now,
             now + pkce.FLOW_TTL_SECONDS, owner, json.dumps(definition)),
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
        "flow": handle, "center": universe_id,
        "authorize_url": offer["authorize_url"] + "?" + urlencode(query),
        "authorize_host": urlsplit(offer["authorize_url"]).hostname or "",
        "expires_in": pkce.FLOW_TTL_SECONDS,
    }


def complete(*, owner: str, universe_id: str, handle: str, code: str,
             verifier: str, iss: str = "") -> dict[str, Any]:
    """Consume the flow once and ask the broker to exchange and commit custody."""
    if not isinstance(handle, str) or not pkce.HANDLE_RE.fullmatch(handle) or not owner:
        raise FlowError("unknown_sign_in", 404)
    if (not isinstance(code, str) or not 1 <= len(code) <= 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in code)):
        raise FlowError("invalid_authorization_code")
    if not isinstance(verifier, str) or not pkce.VERIFIER_RE.fullmatch(verifier):
        raise FlowError("invalid_pkce_verifier")
    from tinyassets.api.helpers import _base_path
    from tinyassets.broker.owner_identities import connection_center

    connection_center(_base_path(), principal=owner, center=universe_id)
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
    definition = json.loads(flow["definition_json"])
    if not definition or action_digest(definition) != flow["action_digest"]:
        raise FlowError("request_changed", 409)
    offer = definition["oauth"]
    # RFC 9207: the authorization response names its issuer. A mismatch means
    # the code came from some other server (a mix-up); a server that
    # advertises the parameter must send it.
    if iss and not hmac.compare_digest(iss, str(offer.get("issuer") or "")):
        raise FlowError("issuer_mismatch", 409)
    if not iss and offer.get("iss_parameter_supported") is True:
        raise FlowError("issuer_missing", 409)
    from tinyassets.broker.model_connect import request

    result = request(_base_path(), owner=owner, center=universe_id, document={
        "action": "oauth_exchange", "offer": offer, "client_id": flow["client_id"],
        "code": code, "verifier": verifier, "redirect_uri": flow["redirect_uri"],
        "request_ref": flow["request_id"], "destination": definition["destination"],
        "policy": definition["policy"],
    })
    if result.get("error"):
        if result["error"] == "registration_required":
            pkce.forget_client(offer["issuer"], flow["client_id"])
        raise FlowError(result["error"], 502)
    return result["projection"]
