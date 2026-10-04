"""Trusted owner controls for explicit screen replacement and copy provenance."""
from __future__ import annotations

import json
import logging

from tinyassets.api import command_center_updates as updates

logger = logging.getLogger(__name__)


def _refused(exc: Exception) -> dict:
    return {"error": "command_center_update_refused", "detail": str(exc)}


def read_updates(*, universe_id: str = "") -> dict:
    """List actual receipts and explicit choices, never infer a release lineage."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.pending_requests import _owner_gate
    from tinyassets.api.system_copy_requests import SYSTEM_TAG
    from tinyassets.custom_agents import get_definition, list_definitions

    uid, _, denial = _owner_gate(universe_id)
    if denial is not None:
        return denial
    try:
        result = updates.inspect_adoptions(universe_id=uid)
        for item in result["adoptions"]:
            source = get_definition(_base_path(), item["ui_definition_id"])
            item["name"] = str((source or {}).get("name") or item["ui_id"])
            item["author_id"] = str((source or {}).get("author_id") or "")
            # Same-author options are choices, not inferred newer versions.
            candidates = (list_definitions(_base_path(), author_id=item["author_id"],
                                          tags=[SYSTEM_TAG], limit=100)
                          if source else [])
            item["candidates"] = [{
                "agent_definition_id": row["agent_definition_id"],
                "name": row["name"], "description": row["description"],
                "content_fingerprint": row["content_fingerprint"],
            } for row in candidates if row["agent_definition_id"] != item["ui_definition_id"]]
        from tinyassets import command_center_update_registry as registry
        from tinyassets.api.custom_agents import _authenticated_actor
        from tinyassets.command_center_packages import completed_system_copies

        with registry.connect(_base_path()) as conn:
            owner = _authenticated_actor()
            registry.require_owner(conn, owner=owner, uid=uid)
            registered = {row[0] for row in conn.execute(
                "SELECT install_request_id FROM command_center_adoptions "
                "WHERE owner_id=? AND universe_id=?", (owner, uid))}
        result["earlier_copies"] = [row for row in completed_system_copies(
            _base_path(), universe_id=uid) if row["request_id"] not in registered]
        return result
    except (ValueError, LookupError, PermissionError) as exc:
        return _refused(exc)


def after_install(*, universe_id: str, request_id: str, result: dict) -> dict:
    """Record success separately; a metadata failure cannot undo a finished copy."""
    if not result.get("installed") or result.get("publication_kind") != "system":
        return {"update_registration": "unsupported_publication"}
    try:
        adoption = updates.record_install(universe_id=universe_id, request_id=request_id)
        return {"update_registration": "recorded", "adoption": adoption}
    except Exception:  # noqa: BLE001 - installation already completed; preserve its receipt
        logger.warning("Installed center provenance could not be registered", exc_info=True)
        return {"update_registration": "unavailable",
                "update_registration_detail":
                    "Your copy is installed; update history is unavailable."}


def write_update(*, universe_id: str, operation: str, payload=None) -> dict:
    """Person-driven operations; deliberately absent from the engine tool surface."""
    from tinyassets.api.pending_requests import _owner_gate

    uid, _, denial = _owner_gate(universe_id)
    if denial is not None:
        return denial
    try:
        document = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(document, dict):
            raise ValueError("update controls require an object")
        if operation == "register_center_copy":
            fields = ("request_id",)
        elif operation == "preview_center_update":
            fields = ("adoption_id", "agent_definition_id")
        elif operation == "answer_center_update":
            fields = ("request_id", "plan_digest", "decision")
        else:
            raise ValueError("unknown update operation")
        if set(document) != set(fields) or any(
            not isinstance(document[key], str) or not document[key] or len(document[key]) > 200
            for key in fields
        ):
            raise ValueError("update controls require the exact named identifiers and decision")
        if operation == "register_center_copy":
            return {"registered": True, "adoption": updates.record_install(
                universe_id=uid, request_id=document["request_id"])}
        if operation == "preview_center_update":
            return updates.preview_update(universe_id=uid, adoption_id=document["adoption_id"],
                                          definition_id=document["agent_definition_id"])
        return updates.commit_update(universe_id=uid, request_id=document["request_id"],
                                     plan_digest=document["plan_digest"],
                                     decision=document["decision"])
    except (ValueError, LookupError, PermissionError) as exc:
        return _refused(exc)
