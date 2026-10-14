"""``run_graph operation="emit_event"``: the owner's session wakes their own agent.

A screen the owner built (a custom UI) needs a way to start one of the owner's
agents -- "the baker was clicked" -- without being able to run anything it names.
This is that way: it emits an ``app_event`` with a NAME, and only an automation
the owner subscribed to that exact name wakes. Every cross-user rule is the
existing event path's (``automation_events.emit``): the verified request
principal, their own subscriptions, their own current home.

What the caller learns is how many wakes were stored, never which: a UI cannot
map an owner's subscriptions beyond "something listens to this name".

The wake's ``event.viewer.account_id`` is the authenticated account. It is not
taken from ``data``, so a screen cannot claim to be another account.
"""

from __future__ import annotations

import json
from typing import Any

_NOT_FOUND = {"error": "not_found", "resource": "universe"}


def emit_event(*, universe_id: str = "", inputs_json: str = "") -> dict[str, Any]:
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _request_universe
    from tinyassets.automation_events import emit_app_event, validated_app_event
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.principals import named_principal

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "app_event"}
    principal = named_principal(permissions.current_actor_id())
    if not principal:
        return {"error": "authentication_required", "resource": "app_event"}
    try:
        document = json.loads(inputs_json or "{}")
    except (json.JSONDecodeError, RecursionError):
        return {"error": "inputs_json must be a JSON object"}
    if not isinstance(document, dict) or set(document) - {"name", "data"}:
        return {"error": "inputs_json takes exactly {\"name\", \"data\"}"}
    try:
        name, data = validated_app_event(document.get("name"), document.get("data"))
    except ValueError as exc:
        return {"error": "app_event_invalid", "detail": str(exc)}

    base = _base_path()
    uid = _request_universe(universe_id)
    # Only the caller's own home: an event is never sent into a universe the
    # caller does not own, and the refusal is the same whether it exists or not.
    if not uid or get_founder_home(base, principal) != uid:
        return dict(_NOT_FOUND)

    woke = emit_app_event(base, universe_id=uid, principal_id=principal, name=name, data=data)
    return {"emitted": True, "name": name, "woke": len(woke)}


__all__ = ["emit_event"]
