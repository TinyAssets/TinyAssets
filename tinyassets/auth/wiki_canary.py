"""Single-purpose authorization for the reserved wiki uptime canary."""

from __future__ import annotations

import json
import secrets
from contextvars import ContextVar, Token
from typing import Any, Mapping

WIKI_CANARY_TOKEN_ENV = "TINYASSETS_WIKI_CANARY_TOKEN"
WIKI_CANARY_CATEGORY = "notes"
WIKI_CANARY_FILENAME = "uptime-probe"
WIKI_CANARY_RELATIVE_PATH = "drafts/notes/uptime-probe.md"
_MIN_TOKEN_BYTES = 32
_CANARY_ARGUMENT_KEYS = frozenset({"category", "filename", "content", "dry_run"})

_current_wiki_canary_authority: ContextVar[bool] = ContextVar(
    "tinyassets_current_wiki_canary_authority",
    default=False,
)


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


def is_exact_wiki_canary_arguments(arguments: Mapping[str, Any]) -> bool:
    """Return whether arguments select only the one reserved full-page write."""
    return (
        frozenset(arguments) == _CANARY_ARGUMENT_KEYS
        and arguments.get("category") == WIKI_CANARY_CATEGORY
        and arguments.get("filename") == WIKI_CANARY_FILENAME
        and isinstance(arguments.get("content"), str)
        and bool(arguments["content"])
        and arguments.get("dry_run") is False
    )


def is_exact_wiki_canary_request(body: bytes) -> bool:
    """Classify the one JSON-RPC request shape eligible for canary authority."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(payload, dict) or frozenset(payload) != {
        "jsonrpc",
        "id",
        "method",
        "params",
    }:
        return False
    if payload.get("jsonrpc") != "2.0" or payload.get("method") != "tools/call":
        return False
    params = payload.get("params")
    if not isinstance(params, dict) or frozenset(params) != {"name", "arguments"}:
        return False
    arguments = params.get("arguments")
    return (
        params.get("name") == "write_page"
        and isinstance(arguments, dict)
        and is_exact_wiki_canary_arguments(arguments)
    )


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
    if name == "write_page":
        return is_exact_wiki_canary_arguments(arguments)
    if name == "read_page":
        return frozenset(arguments) == {"page"} and arguments.get("page") == WIKI_CANARY_FILENAME
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


def set_wiki_canary_authority(active: bool) -> Token[bool]:
    """Set request-local canary authority and return its reset token."""
    return _current_wiki_canary_authority.set(active)


def reset_wiki_canary_authority(token: Token[bool]) -> None:
    """Restore the prior request-local authority."""
    _current_wiki_canary_authority.reset(token)


def current_wiki_canary_authority() -> bool:
    """Return whether the current request holds only canary-write authority."""
    return _current_wiki_canary_authority.get()
