"""MCP resource discovery and public-client registration, without provider code."""
from urllib.parse import urlsplit, urlunsplit

from tinyassets.connection_oauth.discovery import (
    _covers,
    fetch_server_metadata,
    register_public_client,
    validate_request,
)
from tinyassets.connection_oauth.transport import OAuthError, request_json, validate_https_url


def discover(endpoint, requested):
    from tinyassets.connection_oauth import discovery

    endpoint = validate_https_url(endpoint)
    requested = validate_request(requested)
    if not discovery.DISCOVERY_ENABLED:
        raise OAuthError("discovery_unavailable")
    parts = urlsplit(endpoint)
    origin = (parts.scheme, parts.netloc)
    urls = list(dict.fromkeys([
        urlunsplit((*origin, "/.well-known/oauth-protected-resource" + parts.path, "", "")),
        urlunsplit((*origin, "/.well-known/oauth-protected-resource", "", "")),
    ]))
    for url in urls:
        status, document = request_json("GET", url)
        if status != 200 or not isinstance(document, dict):
            continue
        # Never accept metadata for a neighboring resource on the same host.
        if document.get("resource") != endpoint:
            raise OAuthError("protected_resource_mismatch")
        issuers = document.get("authorization_servers")
        if (not isinstance(issuers, list) or not 1 <= len(issuers) <= 2
                or not all(isinstance(issuer, str) for issuer in issuers)):
            raise OAuthError("invalid_protected_resource_metadata")
        metadata = None
        for issuer in issuers:
            try:
                metadata = fetch_server_metadata(validate_https_url(issuer))
                break
            except OAuthError:
                continue
        if metadata is None:
            raise OAuthError("no_authorization_server_metadata")
        scopes = requested.get("scopes", [])
        reason = _covers(metadata, scopes)
        if reason:
            raise OAuthError(reason)
        if not (metadata.registration_endpoint or metadata.client_id_metadata_document_supported
                or requested.get("client_id")):
            raise OAuthError("no_public_client")
        return {
            "issuer": metadata.issuer, "authorize_url": metadata.authorization_endpoint,
            "token_url": metadata.token_endpoint,
            "registration_url": metadata.registration_endpoint,
            "client_id": requested.get("client_id", ""),
            "cimd_supported": metadata.client_id_metadata_document_supported,
            "iss_parameter_supported": metadata.iss_parameter_supported,
            "scopes": scopes, "source": "discovered", "resource": endpoint,
        }
    raise OAuthError("no_protected_resource_metadata")


def select_client(offer, *, redirect_uri):
    """DCR, then server-advertised CIMD, then explicitly configured public ID."""
    if offer.get("registration_url"):
        try:
            return register_public_client(offer["registration_url"], redirect_uri=redirect_uri,
                                          scopes=list(offer.get("scopes") or []))
        except OAuthError:
            # A failed exchange is never retried. Registration can fall back to
            # another advertised client identity without handling any token.
            pass
    if offer.get("cimd_supported") is True:
        from tinyassets.connection_oauth.flow import callback_origin
        from tinyassets.onboarding.source_connect import CLIENT_METADATA_PATH

        return callback_origin(redirect_uri) + CLIENT_METADATA_PATH
    if offer.get("client_id"):
        return offer["client_id"]
    raise OAuthError("mcp_client_registration_required")
