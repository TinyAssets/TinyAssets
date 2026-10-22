"""Address-based model API discovery using the shared bounded HTTPS transport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from tinyassets.connection_oauth.transport import OAuthError, request_json, validate_https_url


def addresses():
    """Installed address suggestions, without a provider-specific dispatch path."""
    return json.loads(Path(__file__).parent.parent.joinpath(
        "providers/model_discovery_addresses.json").read_text("utf-8"))


def address_url(address):
    if not isinstance(address, str):
        raise OAuthError("invalid_oauth_url")
    address = address.strip()
    return validate_https_url(address if "://" in address else "https://" + address).rstrip("/")


def catalogue_rows(payload):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if (not isinstance(rows, list) or not rows or len(rows) > 10000
            or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                   or not row["id"] or len(row["id"]) > 200
                   or not row["id"].isprintable() or row["id"] != row["id"].strip()
                   for row in rows)
            or len({row["id"] for row in rows}) != len(rows)):
        raise OAuthError("no_model_api_found")
    return rows


def discover(address):
    """Only a catalogue or an explicit metadata advertisement establishes an API.

    ``models_endpoint`` and ``chat_completions_endpoint`` are optional provider
    metadata extensions, not claims that RFC 9728 standardizes model APIs.
    """
    address = address_url(address)
    parts = urlsplit(address)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    candidates = []
    for metadata_url in dict.fromkeys([
            origin + "/.well-known/oauth-protected-resource" + parts.path,
            origin + "/.well-known/oauth-protected-resource"]):
        status, doc = request_json("GET", metadata_url)
        if status != 200 or not isinstance(doc, dict):
            continue
        named = doc.get("resource")
        if named not in {address, origin}:
            raise OAuthError("protected_resource_mismatch")
        if doc.get("models_endpoint") and doc.get("chat_completions_endpoint"):
            return definition(validate_https_url(doc["models_endpoint"]),
                              validate_https_url(doc["chat_completions_endpoint"]), address)
    if parts.path.endswith("/models"):
        candidates.append(address)
    else:
        candidates.append(address + "/models")
    candidates.append(origin + "/v1/models")
    for url in dict.fromkeys(candidates):
        status, doc = request_json("GET", url)
        if status != 200:
            continue
        try:
            catalogue_rows(doc)
        except OAuthError:
            continue
        return definition(url, url.removesuffix("/models") + "/chat/completions", address)
    raise OAuthError("no_model_api_found")


def definition(models_url, inference_url, address):
    from tinyassets.onboarding.connection_definition import policy

    endpoints = []
    for url, method in ((models_url, "GET"), (inference_url, "POST")):
        parts = urlsplit(validate_https_url(url))
        if any(c in parts.path for c in "*{}"):
            raise OAuthError("oauth_endpoint_not_permitted")
        endpoints.append({"host": parts.hostname, "path_template": parts.path,
                          "methods": [method]})
    # Parse now: rejects wildcards, private names and template authority.
    selected = policy(endpoints)
    if len({ep["host"] for ep in selected["endpoints"]}) != 1:
        raise OAuthError("model_api_hosts_unsupported")
    return {"destination": "model-" + hashlib.sha256(inference_url.encode()).hexdigest()[:24],
            "endpoints": selected["endpoints"], "discovery_hosts": [address],
            "models_url": models_url, "inference_url": inference_url}


def source(endpoints, destination):
    """Recognize a generic compatible pair from the granted exact endpoint policy."""
    gets = [ep for ep in endpoints if ep.get("methods") == ["GET"]]
    posts = [ep for ep in endpoints if ep.get("methods") == ["POST"]]
    if len(endpoints) != 2 or len(gets) != 1 or len(posts) != 1:
        return None
    get, post = gets[0], posts[0]
    inference = "https://" + post["host"] + post["path_template"]
    if destination != "model-" + hashlib.sha256(inference.encode()).hexdigest()[:24]:
        return None
    return {"name": post["host"], "catalogue_url": "https://" + get["host"]
            + get["path_template"], "wire": "chat_messages",
            "discovered": True, "offer": "Use the models this account offers.",
            "billing_note": "Calls follow your provider's billing settings. "
                            "TinyAssets cannot measure or enforce this source's charges. "
                            "Confirm only if your account has no per-call charges "
                            "or paid usage is disabled."}
