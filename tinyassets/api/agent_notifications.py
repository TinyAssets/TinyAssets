"""Informational owner updates over the existing request inbox and push path."""

from __future__ import annotations

import json
import re
from typing import Any

from tinyassets.api.pending_requests import _bad, _owner_gate, _payload


def notify(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.credential_shape import looks_like_credential
    from tinyassets.effectors.authenticated_external_call import _initiating_agent
    from tinyassets.owner_notifications import _owner_of, notify_request_raised
    from tinyassets.storage.pending_requests import create_request

    uid, home, denied = _owner_gate(universe_id)
    if denied is not None:
        return denied
    actor = permissions.current_actor_id()
    if _owner_of(_base_path(), uid) != actor:
        return {"error": "not_found", "resource": "pending_request"}
    try:
        document = _payload(payload)
        if set(document) - {"title", "body", "link_to_thread", "item_id", "attachment_ref"}:
            raise ValueError("Use title, body, optional item_id and attachment_ref; "
                             "optional link_to_thread names an agent in your own command center.")
        for key, limit in (("title", 200), ("body", 8000), ("item_id", 128),
                           ("attachment_ref", 1024)):
            value = document.get(key, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f"{key} must be text of at most {limit} characters")
        title, body = document.get("title", "").strip(), document.get("body", "").strip()
        if not title or not body:
            raise ValueError("title and body are required")
        item = document.get("item_id", "")
        if item and not re.fullmatch(r"[A-Za-z0-9_.:-]+", item):
            raise ValueError("item_id must be a chat item identifier")
        if looks_like_credential(json.dumps(document)):
            raise ValueError("Notifications are stored in the clear; "
                             "use a reference instead of credentials.")
    except ValueError as exc:
        return _bad(str(exc))
    agent = _initiating_agent(home) or "main"
    thread = agent
    if "link_to_thread" in document:
        from tinyassets.addressed_agents import AgentNotAddressable, normalize_agent_id, resolve

        try:
            thread = normalize_agent_id(document["link_to_thread"])
            resolve(_base_path(), universe_id=uid, owner=actor, agent_id=thread)
        except AgentNotAddressable as exc:
            return _bad(str(exc))
    action = {"type": "notify", "item_id": item, "thread_agent": thread,
              "attachment_ref": document.get("attachment_ref", "")}
    row = create_request(
        home, kind="Notification", title=title, body=body, fields=[],
        action=action, dedupe_key=json.dumps(["notify", title, body, action], sort_keys=True),
        agent=agent,
    )
    if not row:
        return {"error": "request_storage_unavailable"}
    if row.get("error") or row.get("settled"):
        return row
    created = row.pop("created", False)
    delivery = {"skipped": "duplicate"}
    if created:
        delivery = notify_request_raised(
            _base_path(), universe_id=uid, raised_by=actor, request=row,
        )
    return {**row, "created": created, "delivery": delivery}
