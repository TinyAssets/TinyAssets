"""Owner-issued credentials in the ordinary request inbox; never a tool result."""
from __future__ import annotations

import hashlib
import json

from tinyassets.api_keys import CAPABILITIES, MAX_NAME


def validate_action(action):
    if set(action) != {"type", "provider", "name", "scopes"}:
        raise ValueError("credential_request needs provider, name and exact scopes")
    if action["provider"] != "tinyassets":
        raise ValueError("This provider cannot issue keys here. "
                         "Use its supported secure connect flow.")
    name, scopes = action["name"], action["scopes"]
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise ValueError("Name the external agent (at most 80 characters)")
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= len(CAPABILITIES):
        raise ValueError("Choose read_graph, converse and/or run_graph")
    for scope in scopes:
        if (not isinstance(scope, dict) or set(scope) != {"universe", "agent", "capability"}
                or not isinstance(scope["universe"], str) or not scope["universe"]
                or scope["universe"] == "*" or scope["agent"] != "main"
                or not isinstance(scope["capability"], str)
                or scope["capability"] not in CAPABILITIES):
            raise ValueError("Each scope must name the command center, main and a REST capability")
    if len({s["universe"] for s in scopes}) != 1:
        raise ValueError("One credential request names one command center")
    if len({s["capability"] for s in scopes}) != len(scopes):
        raise ValueError("Duplicate capability")
    return {"type": "credential_request", "provider": "tinyassets", "name": name.strip(),
            "scopes": sorted(scopes, key=lambda s: s["capability"])}


def capture(uid, action):
    from tinyassets.api.commons_catalog import _authenticated_actor

    if any(scope["universe"] != uid for scope in action["scopes"]):
        raise ValueError("Raise this request in the command center receiving the connection")
    digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode()).hexdigest()
    return {**action, "snapshot_digest": digest, "request_owner": _authenticated_actor()}


def tab_text(action):
    return ("API key", "Create API key for " + action["name"],
            "Review this connection, then create its key here. Only you see the key; "
            "your agent receives a completion receipt without the secret.")


def owner_operation(owner, data, keys, authority):
    """Called only by the protected owner HTTP route. Never decorate/log results."""
    from tinyassets.api.helpers import _base_path, _universe_dir
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.owner_control import control
    from tinyassets.storage.pending_requests import get_request, resolve_request

    expected_fields = {"operation", "universe_id", "request_id"}
    if data.get("operation") == "request_create":
        expected_fields.add("expected_digest")
    if set(data) != expected_fields:
        raise ValueError("Invalid credential request operation")
    uid, request_id = data["universe_id"], data["request_id"]
    if not isinstance(uid, str) or not isinstance(request_id, str) or not request_id:
        raise ValueError("Command center and request required")
    base = _base_path()
    if (get_founder_home(base, owner) != uid
            and universe_access_permission(base, universe_id=uid, actor_id=owner) != "admin"):
        raise PermissionError("Current owner authority required")
    home = _universe_dir(uid)
    with control(home):
        row = get_request(home, request_id)
        if not row:
            raise PermissionError("Request unavailable")
        # This row lives in the existing protected activity/request store,
        # outside the agent's writable center. No new consent database.
        action = row["action"]
        if action.get("type") != "credential_request" or action.get("request_owner") != owner:
            raise PermissionError("Wrong request type")
        digest = action.get("snapshot_digest")
        action = validate_action({k: v for k, v in action.items()
                                  if k not in {"snapshot_digest", "request_owner"}})
        if capture(uid, action)["snapshot_digest"] != digest:
            raise PermissionError("Request changed")
        ref = json.dumps([uid, request_id])
        key_id = keys.request_key_id(owner, ref)
        existing = next((k for k in keys.list(owner, authority=authority)["keys"]
                         if k["key_id"] == key_id), None)
        operation = data["operation"]
        if operation == "request_preview":
            return {"action": action, "digest": digest, "status": row["status"],
                    "existing": existing}
        if operation == "request_revoke" and existing:
            return keys.revoke(owner, key_id, authority=authority)
        if row["status"] != "pending":
            raise PermissionError("Request already closed")
        if operation == "request_decline" and not existing:
            if not resolve_request(home, request_id, status="answered", decision="declined"):
                raise ValueError("Could not decline the request")
            return {"status": "declined"}
        if operation == "request_create":
            if data["expected_digest"] != digest:
                raise PermissionError("Review the current request before creating")
            return keys.mint(owner, name=action["name"], scopes=action["scopes"],
                             authority=authority, request_ref=ref)
        if operation == "request_finish" and existing:
            # Only this receipt reaches storage, events and the asking agent.
            receipt = {"key_id": key_id, "provider": "tinyassets",
                       "status": "revoked" if existing["revoked_at"] is not None else "created"}
            if not resolve_request(home, request_id, status="answered", answer=receipt):
                raise ValueError("Could not finish the request")
            return receipt
        raise ValueError("Invalid credential request operation")
