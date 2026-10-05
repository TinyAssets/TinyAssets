"""Whether a provider offers OAuth for a connection, found from standards, not code.

Founder, 2026-09-24: "Our generic connector should prefer OAuth when the
provider allows for what the request is trying to accomplish, as that is less
actions for the user." There is no table of providers here.

Trust roots are the daemon-owned provider directory and standards discovery
rooted at the connection's declared hosts. An active directory entry wins;
otherwise endpoints come from standard discovery:

* RFC 9728 protected-resource metadata on a connection host
  (``/.well-known/oauth-protected-resource``, whose ``resource`` must be that
  host) names the authorization server(s); failing that, the host itself is
  tried as the issuer;
* RFC 8414 authorization-server metadata, then OpenID Connect discovery, on the
  issuer so named, whose ``issuer`` must equal it.

The requester (an agent, possibly steered by remixed or injected content) may
say only WHAT the use needs: its ``scopes``, and optionally a public
``client_id``. It can never name an authorize, token or registration URL or an
issuer: that would let it pair a real sign-in page with its own token endpoint
and collect the code, the verifier and the refresh tokens (Tier 2 review
round 1, BLOCK).

An offer exists only when the server covers the request: the authorization-code
grant with PKCE S256 for a public client, every requested scope (when the server
lists its scopes), and a client (the supplied id, or RFC 7591 dynamic
registration). Anything short of that is a reason, and the ask falls back to
key paste.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from tinyassets.connection_oauth.transport import OAuthError, request_json, validate_https_url

#: Network discovery on or off. On in production. The test suite injects False
#: (``tests/conftest.py``) so no test reaches a real host, and a test that
#: exercises discovery turns it back on against its own local fake server.
DISCOVERY_ENABLED = True
logger = logging.getLogger(__name__)

_SCOPE_RE = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]{1,128}\Z")
_CLIENT_ID_RE = re.compile(r"[\x21-\x7E]{1,256}\Z")
_MAX_SCOPES = 32
_MAX_ISSUERS = 2
_REQUEST_KEYS = frozenset({"client_id", "scopes", "use"})
#: Endpoint fields a requester may NOT supply: they are discovered, never told.
_ENDPOINT_KEYS = frozenset({
    "issuer", "authorize_url", "authorization_endpoint", "token_url", "token_endpoint",
    "registration_url", "registration_endpoint",
})


@dataclass(frozen=True)
class ServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    iss_parameter_supported: bool
    scopes_supported: tuple[str, ...] | None
    code_challenge_methods: tuple[str, ...]
    grant_types: tuple[str, ...]
    response_types: tuple[str, ...]
    client_id_metadata_document_supported: bool = False


def _strings(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise OAuthError("invalid_authorization_server_metadata")
    return tuple(value)


def validate_scopes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split()
    if not isinstance(value, list) or len(value) > _MAX_SCOPES:
        raise ValueError(f"oauth.scopes must be a list of at most {_MAX_SCOPES} scope names")
    out: list[str] = []
    for scope in value:
        if not isinstance(scope, str) or not _SCOPE_RE.match(scope):
            raise ValueError("each oauth scope must be 1-128 printable characters, no spaces")
        if scope not in out:
            out.append(scope)
    return out


def validate_request(raw: Any) -> dict[str, Any]:
    """The ``oauth`` object a ``connect`` ask may carry: what the use needs.

    ``scopes`` and an optional public ``client_id``. Endpoints and issuers are
    refused: they are discovered from the connection's own host, never told.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("oauth must be an object")
    endpoints = set(raw) & _ENDPOINT_KEYS
    if endpoints:
        raise ValueError(
            "oauth may not name " + ", ".join(sorted(endpoints)) + ": sign-in endpoints "
            "are discovered from the connection's own host (RFC 9728 / RFC 8414), "
            "never supplied"
        )
    unknown = set(raw) - _REQUEST_KEYS
    if unknown:
        raise ValueError("oauth has unknown fields: " + ", ".join(sorted(unknown))
                         + " (only scopes, use and a public client_id; a client secret "
                         "never goes through an ask)")
    out: dict[str, Any] = {}
    client_id = raw.get("client_id")
    if client_id not in (None, ""):
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
            raise ValueError("oauth.client_id must be 1-256 printable characters")
        out["client_id"] = client_id
    out["scopes"] = validate_scopes(raw.get("scopes"))
    if "use" in raw:
        use = raw["use"]
        if not isinstance(use, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", use):
            raise ValueError("oauth.use must be a named scope set")
        out["use"] = use
    return out


def _metadata_urls(issuer: str) -> list[str]:
    """RFC 8414 §3.1 inserts the well-known segment; OpenID appends it."""
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    origin = (parts.scheme, parts.netloc)
    return [
        urlunsplit((*origin, "/.well-known/oauth-authorization-server" + path, "", "")),
        urlunsplit((*origin, path + "/.well-known/openid-configuration", "", "")),
    ]


def fetch_server_metadata(issuer: str) -> ServerMetadata:
    issuer = validate_https_url(issuer)
    for url in _metadata_urls(issuer):
        status, doc = request_json("GET", url)
        if status != 200 or not isinstance(doc, dict):
            continue
        # RFC 8414 §3.3 / OIDC Discovery §4.3: the document must name the very
        # issuer it was fetched for, or it is someone else's metadata.
        if doc.get("issuer") != issuer and doc.get("issuer") != issuer.rstrip("/"):
            raise OAuthError("authorization_server_issuer_mismatch")
        try:
            authorize = validate_https_url(doc.get("authorization_endpoint"))
            token = validate_https_url(doc.get("token_endpoint"))
        except OAuthError:
            raise OAuthError("invalid_authorization_server_metadata") from None
        registration = doc.get("registration_endpoint") or ""
        if registration:
            try:
                registration = validate_https_url(registration)
            except OAuthError:
                registration = ""
        return ServerMetadata(
            issuer=issuer, authorization_endpoint=authorize, token_endpoint=token,
            registration_endpoint=registration,
            iss_parameter_supported=doc.get("authorization_response_iss_parameter_supported")
            is True,
            scopes_supported=_strings(doc.get("scopes_supported")),
            code_challenge_methods=_strings(doc.get("code_challenge_methods_supported")) or (),
            # RFC 8414 §2 defaults when omitted.
            grant_types=_strings(doc.get("grant_types_supported"))
            or ("authorization_code", "implicit"),
            response_types=_strings(doc.get("response_types_supported")) or (),
            client_id_metadata_document_supported=(
                doc.get("client_id_metadata_document_supported") is True),
        )
    raise OAuthError("no_authorization_server_metadata")


def protected_resource_issuers(host: str) -> list[str]:
    """RFC 9728: the authorization servers an API names for itself."""
    resource = f"https://{host}"
    status, doc = request_json("GET", resource + "/.well-known/oauth-protected-resource")
    if status != 200 or not isinstance(doc, dict):
        return []
    # §3.3: the metadata must be about the resource it was fetched from.
    named = doc.get("resource")
    if not isinstance(named, str) or urlsplit(named).netloc.lower() != host.lower():
        return []
    servers = doc.get("authorization_servers")
    if not isinstance(servers, list):
        return []
    out = []
    for server in servers[:_MAX_ISSUERS]:
        try:
            out.append(validate_https_url(server))
        except OAuthError:
            continue
    return out


def _metadata_for(hosts: list[str]) -> ServerMetadata:
    """Discovery rooted ONLY at the connection's declared hosts."""
    last: OAuthError = OAuthError("no_authorization_server_metadata")
    for host in hosts[:_MAX_ISSUERS]:
        candidates = protected_resource_issuers(host) or [f"https://{host}"]
        for candidate in candidates:
            try:
                return fetch_server_metadata(candidate)
            except OAuthError as exc:
                last = exc
    raise last


def _covers(metadata: ServerMetadata, scopes: list[str]) -> str:
    """Empty when the server covers the requested use, else the reason."""
    if "S256" not in metadata.code_challenge_methods:
        return "pkce_not_offered"
    if "code" not in metadata.response_types:
        return "authorization_code_not_offered"
    if "authorization_code" not in metadata.grant_types:
        return "authorization_code_not_offered"
    if metadata.scopes_supported is not None and not set(scopes) <= set(metadata.scopes_supported):
        return "scopes_not_offered"
    return ""


def resolve_offer(requested: dict[str, Any], hosts: list[str]) -> tuple[dict[str, Any] | None, str]:
    """``(offer, "")`` when OAuth covers this connection, else ``(None, reason)``.

    Endpoints come from the trusted directory or discovery rooted at ``hosts``.
    The offer is recorded on the ask, so what the owner is
    shown (every endpoint host) is exactly what the sign-in uses.
    """
    scopes = list(requested.get("scopes") or [])
    from tinyassets.connection_oauth import directory, service

    try:
        remote = service.inherited_config()
        if remote:
            offer = service.call(remote, {
                "op": "resolve", "requested": requested, "hosts": hosts,
            }).get("offer")
        else:
            offer = directory.resolve(requested, hosts)
        if offer:
            return offer, ""
    except OAuthError as exc:
        if exc.code not in {"oauth_directory_invalid", "platform_client_unavailable"}:
            raise
        # A missing usable platform registration says nothing about whether
        # the host supports the existing public-client flow. No raw RPC/config
        # exception contents belong in logs.
        logger.warning("Optional OAuth directory unavailable: %s", exc.code)
    if not DISCOVERY_ENABLED:
        return None, "discovery_unavailable"
    try:
        metadata = _metadata_for(hosts)
    except OAuthError as exc:
        return None, exc.code
    reason = _covers(metadata, scopes)
    if reason:
        return None, reason
    client_id = requested.get("client_id", "")
    registration = "" if client_id else metadata.registration_endpoint
    if not client_id and not registration:
        return None, "no_public_client"
    return {
        "issuer": metadata.issuer,
        "authorize_url": metadata.authorization_endpoint,
        "token_url": metadata.token_endpoint,
        "client_id": client_id,
        "registration_url": registration,
        "iss_parameter_supported": metadata.iss_parameter_supported,
        "scopes": scopes,
        "source": "discovered",
    }, ""


def offer_hosts(offer: dict[str, Any]) -> list[str]:
    """Every host the sign-in contacts, in order, for the owner to see."""
    hosts: list[str] = []
    for key in ("authorize_url", "token_url", "registration_url"):
        host = urlsplit(str(offer.get(key) or "")).hostname or ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def register_public_client(registration_url: str, *, redirect_uri: str,
                           scopes: list[str]) -> str:
    """RFC 7591 dynamic registration of a PUBLIC client; returns its id.

    A server that insists on a confidential client (issues a secret) is
    refused: a client secret would have nowhere safe to live in a browser flow.
    """
    body: dict[str, Any] = {
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "TinyAssets",
    }
    if scopes:
        body["scope"] = " ".join(scopes)
    status, doc = request_json("POST", registration_url, json_body=body)
    if status not in (200, 201) or not isinstance(doc, dict):
        from tinyassets.connection_oauth.transport import server_error_detail

        raise OAuthError("client_registration_failed", server_error_detail(status, doc),
                         status=status)
    client_id = doc.get("client_id")
    if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
        raise OAuthError("client_registration_failed", "no client_id in the response")
    method = doc.get("token_endpoint_auth_method", "none")
    if doc.get("client_secret") or method != "none":
        raise OAuthError("confidential_client_refused",
                         "the server registered a confidential client")
    return client_id
