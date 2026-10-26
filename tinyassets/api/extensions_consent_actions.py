"""MCP action handlers for effector consents (PR-122 Phase 2 Slice 1; N10).

Consents are broker-held per owner (``tinyassets.storage.effector_consents``
reads and narrows them). Three actions on the ``extensions`` MCP tool surface:

- ``grant_effector_consent`` -- refused: granting is the owner's act in the
  protected view (``/app/api/consents``), never an agent-reachable call.
- ``revoke_effector_consent(sink, destination)`` -- takes a consent back.
- ``list_effector_consents(sink?, active_only?)`` -- returns rows.

The dispatch dict shape matches the other ``_*_ACTIONS`` modules under
``tinyassets/api/``: each handler takes a single ``kwargs: dict`` and
returns a JSON string for the MCP response.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The one refusal every agent-reachable consent grant gives (N10).
PERSON_ONLY_DETAIL = (
    "Consent is the owner's own act: they grant it in the protected TinyAssets "
    "view, not through an agent call. Ask them there.")


def _command_center_dir():
    """The calling owner's command center, resolved like every other MCP read."""
    from tinyassets.api.helpers import _request_universe, _universe_dir

    return _universe_dir(_request_universe(""))


def _action_grant_effector_consent(kwargs: dict[str, Any]) -> str:
    """Refused: an agent-reachable surface never grants consent (N10)."""
    return json.dumps({
        "error": "consent_is_person_only",
        "failure_class": "consent_is_person_only",
        "actionable_by": "user",
        "detail": PERSON_ONLY_DETAIL,
    })


def _action_revoke_effector_consent(kwargs: dict[str, Any]) -> str:
    """Take back an active grant.

    Required: ``sink`` + ``destination``. Returns ``status="revoked"``
    on hit, ``status="no_active_grant"`` when no row matched (the
    chatbot can treat both as success — the desired end-state is "not
    granted", and revoking a never-granted destination already
    satisfies it).
    """
    sink = (kwargs.get("sink") or "").strip()
    destination = (kwargs.get("destination") or "").strip()
    if not sink:
        return json.dumps({
            "error": "revoke_effector_consent requires 'sink'",
            "failure_class": "missing_sink",
            "actionable_by": "chatbot",
        })
    if not destination:
        return json.dumps({
            "error": "revoke_effector_consent requires 'destination'",
            "failure_class": "missing_destination",
            "actionable_by": "chatbot",
        })
    try:
        from tinyassets.storage.effector_consents import revoke_consent
        hit = revoke_consent(
            _command_center_dir(),
            sink=sink,
            destination=destination,
        )
    except Exception as exc:
        logger.exception("revoke_effector_consent failed")
        return json.dumps({
            "error": f"revoke_effector_consent failed: {exc}",
            "failure_class": "storage_error",
            "actionable_by": "host",
        })
    return json.dumps({
        "status": "revoked" if hit else "no_active_grant",
        "sink": sink,
        "destination": destination,
    })


def _action_list_effector_consents(kwargs: dict[str, Any]) -> str:
    """List consent rows. Filters: ``sink`` (optional), ``active_only`` (default True).

    Returns ``{"consents": [...]}`` with rows ordered most-recent-grant
    first.
    """
    sink_filter = (kwargs.get("sink") or "").strip() or None
    active_raw = kwargs.get("active_only")
    if isinstance(active_raw, bool):
        active_only = active_raw
    elif isinstance(active_raw, str) and active_raw.strip():
        # Explicit string -> truthy literal opts in to active_only.
        # Anything else (including "false"/"no"/"0") opts out.
        active_only = active_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        # None or empty string -> default True.
        active_only = True
    try:
        from tinyassets.storage.effector_consents import list_consents
        rows = list_consents(
            _command_center_dir(),
            sink=sink_filter,
            active_only=active_only,
        )
    except Exception as exc:
        logger.exception("list_effector_consents failed")
        return json.dumps({
            "error": f"list_effector_consents failed: {exc}",
            "failure_class": "storage_error",
            "actionable_by": "host",
        })
    return json.dumps({
        "consents": rows,
        "sink_filter": sink_filter,
        "active_only": active_only,
    })


_EFFECTOR_CONSENT_ACTIONS: dict[str, Any] = {
    "grant_effector_consent": _action_grant_effector_consent,
    "revoke_effector_consent": _action_revoke_effector_consent,
    "list_effector_consents": _action_list_effector_consents,
}


__all__ = [
    "PERSON_ONLY_DETAIL",
    "_EFFECTOR_CONSENT_ACTIONS",
    "_action_grant_effector_consent",
    "_action_revoke_effector_consent",
    "_action_list_effector_consents",
]
