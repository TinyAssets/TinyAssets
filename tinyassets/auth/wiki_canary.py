"""Single-purpose authorization for the uptime canary bearer."""

from __future__ import annotations

import json
import secrets
from typing import Any

WIKI_CANARY_TOKEN_ENV = "TINYASSETS_WIKI_CANARY_TOKEN"
_MIN_TOKEN_BYTES = 32


def wiki_canary_token_matches(presented: str) -> bool:
    """Match a configured, sufficiently long token in constant time."""
    from tinyassets.operational_credentials import credential
    from tinyassets.storage import data_dir

    configured = credential(data_dir(), WIKI_CANARY_TOKEN_ENV)
    if configured is None:
        return False
    try:
        configured_bytes = configured.encode("utf-8")
        presented_bytes = presented.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(configured_bytes) < _MIN_TOKEN_BYTES:
        return False
    return secrets.compare_digest(presented_bytes, configured_bytes)


#: The canary service principal's whole world (change D4).
#: Every item of a JSON-RPC body under the canary bearer must be one of these
#: or the request is refused before dispatch. Kept this narrow on purpose: a
#: leaked canary bearer can probe liveness and nothing else.
_CANARY_METHODS = frozenset({"initialize", "notifications/initialized", "tools/list"})


def _canary_call_allowed(params: Any) -> bool:
    if not isinstance(params, dict):
        return False
    name = params.get("name")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return False
    if name == "get_status":
        return not arguments
    if name == "read_graph":
        return arguments == {"target": "status"}
    return False


def canary_request_allowed(body: bytes) -> bool:
    """Whether EVERY item of a JSON-RPC body (single or batch) is a probe the
    canary principal may make."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    items = payload if isinstance(payload, list) else [payload]
    if not items:
        return False
    for item in items:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            return False
        method = item.get("method")
        if method in _CANARY_METHODS:
            continue
        if method == "tools/call" and _canary_call_allowed(item.get("params")):
            continue
        return False
    return True
