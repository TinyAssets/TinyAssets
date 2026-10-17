"""Credential-free definitions for the standalone door, without owner files."""
from urllib.parse import urlsplit

from tinyassets.onboarding.hosted_model_auth import HostedAuthError


def policy(endpoints, scheme="bearer"):
    from tinyassets.storage.outbound_connections import _parse_allowed_endpoints

    parsed = _parse_allowed_endpoints(endpoints)
    return dict(auth_scheme=scheme, scopes=sorted({m for ep in parsed for m in ep.methods}),
                endpoints=[ep.as_dict() for ep in parsed], access_mode="exact", git_host="")


def preset_definition(preset_id, *, manual=False):
    from tinyassets.onboarding.hosted_model_auth import endpoint_policy, load_preset
    from tinyassets.providers.free_sources import source_preset

    source = source_preset(preset_id)
    if source is not None:
        base = urlsplit(source["base_url"])
        return {"destination": "model-" + source["id"], "policy": policy([
            {"host": base.netloc, "path_template": base.path + path, "methods": [method]}
            for path, method in (("/chat/completions", "POST"), ("/models", "GET"))])}
    preset = load_preset(preset_id, require_manual_key=manual)
    return {"destination": "model:" + preset.id, "policy": policy(endpoint_policy(preset))}


def oauth_definition(base, *, owner, center, definition, public_resource):
    """Discover only from the connection definition's resource endpoints."""
    from tinyassets.broker.model_connect import request
    from tinyassets.connection_oauth.discovery import discover_offer, validate_request
    from tinyassets.connection_oauth.flow import FlowError, redirect_uri

    if (not isinstance(definition, dict)
            or set(definition) - {"destination", "endpoints", "oauth", "discovery_hosts"}
            or not {"destination", "endpoints"} <= set(definition)):
        raise FlowError("invalid_connection_definition")
    from tinyassets.api.http_connection import _DESTINATION_RE

    if not isinstance(definition["destination"], str) or not _DESTINATION_RE.fullmatch(
            definition["destination"]):
        raise FlowError("invalid_connection_definition")
    selected = policy(definition["endpoints"], "oauth2")
    requested = validate_request(definition.get("oauth"))
    hosts = sorted({ep["host"] for ep in selected["endpoints"]})
    offer = request(base, owner=owner, center=center, document={
        "action": "oauth_offer", "requested": requested, "hosts": hosts})["offer"]
    if offer is None:
        resources = [
            "https://" + ep["host"] + ep["path_template"]
            if not any(c in ep["path_template"] for c in ("{", "*")) else ep["host"]
            for ep in selected["endpoints"]]
        offer, reason = discover_offer(
            requested, definition.get("discovery_hosts") or resources,
            callback=redirect_uri(public_resource))
        if offer is None:
            raise FlowError(reason, 409)
    return {"destination": definition["destination"], "policy": selected, "oauth": offer}


def sign_in_definition(preset_id, public_resource):
    from tinyassets.onboarding.source_connect import sign_in_action
    from tinyassets.providers.free_sources import sign_in_preset

    preset = sign_in_preset(preset_id)
    if preset is None:
        raise HostedAuthError("unknown_model_connection", 404)
    action = sign_in_action(preset, public_resource)
    return {key: action[key] for key in ("destination", "endpoints", "oauth")} | {
        "discovery_hosts": [preset["sign_in"]["issuer_host"]]}
