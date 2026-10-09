"""Standards-only OAuth discovery rooted at the connection destination."""

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
    cimd_supported: bool = False


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
        urlunsplit((*origin, "/.well-known/openid-configuration" + path, "", "")),
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
        if doc.get("issuer") != issuer:
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
            cimd_supported=doc.get("client_id_metadata_document_supported") is True,
            registration_endpoint=registration,
            iss_parameter_supported=doc.get("authorization_response_iss_parameter_supported")
            is True,
            scopes_supported=_strings(doc.get("scopes_supported")),
            code_challenge_methods=_strings(doc.get("code_challenge_methods_supported")) or (),
            # RFC 8414 §2 defaults when omitted.
            grant_types=_strings(doc.get("grant_types_supported"))
            or ("authorization_code", "implicit"),
            response_types=_strings(doc.get("response_types_supported")) or (),
        )
    raise OAuthError("no_authorization_server_metadata")


def bearer_challenge(header: str) -> dict[str, str]:
    """Parse RFC 9110 challenges without splitting commas inside quoted strings."""
    from urllib.request import parse_http_list

    selected: dict[str, str] = {}
    bearer = False
    for part in parse_http_list(header):
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*) +(?!=)(.*)$", part.strip())
        if match:
            if bearer:
                return selected
            bearer = match[1].lower() == "bearer"
            part = match[2]
        if not bearer:
            continue
        param = re.fullmatch(
            r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*("(?:[^"\\]|\\.)*"|[^ ,]+)', part.strip())
        if not param:
            raise OAuthError("invalid_resource_challenge")
        key, value = param.groups()
        key = key.lower()
        if key in selected:
            raise OAuthError("invalid_resource_challenge")
        selected[key] = re.sub(r"\\(.)", r"\1", value[1:-1]) if value.startswith('"') else value
    return selected


def _resource_metadata(resource: str, *, probe: bool) -> tuple[list[str], list[str], str]:
    from tinyassets.connection_oauth.transport import request_json_with_headers

    resource = validate_https_url(resource)
    parts = urlsplit(resource)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    challenge: dict[str, str] = {}
    if probe:
        status, _, headers = request_json_with_headers("GET", resource)
        if status in (401, 403):
            challenge = bearer_challenge(headers.get("www-authenticate", ""))
    urls = []
    if challenge.get("resource_metadata"):
        urls.append((validate_https_url(challenge["resource_metadata"]), resource))
    well_known = origin + "/.well-known/oauth-protected-resource"
    urls.extend([(well_known + parts.path.rstrip("/"), resource),
                 (origin + "/.well-known/oauth-protected-resource", origin)])
    last = None
    for url, expected_resource in dict.fromkeys(urls):
        status, doc = request_json("GET", url)
        if status != 200 or not isinstance(doc, dict):
            continue
        named = doc.get("resource")
        # A challenge can name a parent protected resource (including the
        # origin). Bind that canonical identifier, never a sibling or host.
        named_parts = urlsplit(named) if isinstance(named, str) else None
        parent = bool(named_parts and named_parts.scheme == parts.scheme
                      and named_parts.netloc == parts.netloc
                      and not named_parts.query and not named_parts.fragment
                      and (parts.path or "/").startswith(named_parts.path.rstrip("/") + "/"))
        challenged = url == challenge.get("resource_metadata")
        root = url == origin + "/.well-known/oauth-protected-resource"
        compatible = (challenged or root) and (parent or named == resource)
        if named != expected_resource and not compatible:
            last = OAuthError("protected_resource_mismatch")
            continue
        servers = _strings(doc.get("authorization_servers")) or ()
        scopes = challenge.get("scope")
        try:
            wanted = validate_scopes(scopes if scopes is not None else doc.get("scopes_supported"))
        except ValueError:
            raise OAuthError("invalid_resource_metadata") from None
        return [validate_https_url(v) for v in servers[:_MAX_ISSUERS]], wanted, named
    if last:
        raise last
    return [], [], ""


def protected_resource_issuers(host: str) -> list[str]:
    return _resource_metadata(f"https://{host}", probe=False)[0]


def _metadata_for(hosts: list[str]) -> tuple[ServerMetadata, str, list[str]]:
    """Concrete endpoint URLs preserve resource identity; bare hosts are legacy."""
    last = OAuthError("no_authorization_server_metadata")
    for host in hosts[:_MAX_ISSUERS]:
        resource = host if host.startswith("https://") else f"https://{host}"
        parts = urlsplit(resource)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        try:
            candidates, scopes, identified = _resource_metadata(
                resource, probe=host.startswith("https://"))
        except OAuthError as exc:
            last = exc
            if exc.code == "protected_resource_mismatch":
                continue
            candidates, scopes, identified = [], [], ""
            if host.startswith("https://"):
                # A failed endpoint probe/challenge must not hide legacy root
                # metadata. Retry without probing, just as for a bare host.
                try:
                    candidates, scopes, identified = _resource_metadata(origin, probe=False)
                except OAuthError as root_exc:
                    last = root_exc
                    if root_exc.code == "protected_resource_mismatch":
                        continue
        for candidate in candidates or [origin]:
            try:
                selected = identified if candidates else resource
                return fetch_server_metadata(candidate), selected, scopes
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
    return ""


def resolve_offer(requested: dict[str, Any], hosts: list[str]) -> tuple[dict[str, Any] | None, str]:
    """``(offer, "")`` when OAuth covers this connection, else ``(None, reason)``.

    Endpoints come from the trusted directory or discovery rooted at ``hosts``.
    The offer is recorded on the ask, so what the owner is
    shown (every endpoint host) is exactly what the sign-in uses.
    """
    scopes = list(requested.get("scopes") or [])
    directory_hosts = [urlsplit(h).hostname if h.startswith("https://") else h for h in hosts]
    from tinyassets.connection_oauth import directory, service

    try:
        remote = service.inherited_config()
        if remote:
            offer = service.call(remote, {
                "op": "resolve", "requested": requested, "hosts": directory_hosts,
            }).get("offer")
        else:
            offer = directory.resolve(requested, directory_hosts)
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
        metadata, resource, resource_scopes = _metadata_for(hosts)
        scopes = scopes or resource_scopes
    except OAuthError as exc:
        return None, exc.code
    reason = _covers(metadata, scopes)
    if reason:
        return None, reason
    client_id = requested.get("client_id", "")
    from tinyassets.connection_oauth.flow import callback_origin, configured_redirect_uri
    from tinyassets.connection_oauth.pkce import cached_client

    callback = configured_redirect_uri()
    method = "existing"
    if not client_id:
        try:
            client_id = cached_client(metadata.issuer, callback)
        except OAuthError as exc:
            return None, exc.code
        if client_id:
            method = "cached"
    cimd = ""
    if metadata.cimd_supported and callback:
        from tinyassets.onboarding.source_connect import CLIENT_METADATA_PATH

        cimd = callback_origin(callback) + CLIENT_METADATA_PATH
    if not client_id and cimd:
        client_id = cimd
        method = "cimd"
    registration = metadata.registration_endpoint if not client_id or method == "cached" else ""
    if not client_id and not registration:
        return None, "registration_required"
    return {
        "issuer": metadata.issuer,
        "authorize_url": metadata.authorization_endpoint,
        "token_url": metadata.token_endpoint,
        "client_id": client_id,
        "registration_url": registration,
        "iss_parameter_supported": metadata.iss_parameter_supported,
        "scopes": scopes,
        "source": "discovered",
        "resource": resource,
        "registration_method": method if client_id else "dcr",
        **({"fallback_client_id": cimd} if method == "cached" and cimd else {}),
    }, ""


def offer_hosts(offer: dict[str, Any]) -> list[str]:
    """Every host the sign-in contacts, in order, for the owner to see."""
    hosts: list[str] = []
    for key in ("authorize_url", "token_url", "registration_url"):
        host = urlsplit(str(offer.get(key) or "")).hostname or ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def register_public_client(registration_url: str, *, redirect_uri: str) -> str:
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
        "application_type": "web",
    }
    # This client is shared by issuer/callback; owner scopes belong only on
    # each authorization request, never on the shared registration.
    status, doc = request_json("POST", registration_url, json_body=body)
    if status not in (200, 201) or not isinstance(doc, dict):
        from tinyassets.connection_oauth.transport import server_error_detail

        code = ("registration_required" if status in (400, 401, 403)
                else "client_registration_failed")
        raise OAuthError(code, server_error_detail(status, doc), status=status)
    client_id = doc.get("client_id")
    if not isinstance(client_id, str) or not _CLIENT_ID_RE.match(client_id):
        raise OAuthError("client_registration_failed", "no client_id in the response")
    method = doc.get("token_endpoint_auth_method", "none")
    if doc.get("client_secret") or method != "none":
        raise OAuthError("confidential_client_refused",
                         "the server registered a confidential client")
    return client_id
