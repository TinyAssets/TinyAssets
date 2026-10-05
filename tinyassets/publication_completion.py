"""Publication facts for the owner; posting behaviour belongs in editable skills."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def completion_for(agent: dict[str, Any], *, universe_id: str = "",
                   action: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe a public immutable definition and optionally render its screen.

    Caller supplies a definition returned by publication, never a private UI row.
    Rendering is post-commit: failure is visible without undoing publication.
    """
    components = agent["components"]
    package = next((c for c in components.values()
                    if c.get("kind") == "tinyassets.package.v1"), {})
    version = package.get("version", agent["content_fingerprint"])
    update = (isinstance(version, int) and version > 1) or bool(
        (action or {}).get("release", {}).get("parent_release_id"))
    result = {"listing_id": agent["agent_definition_id"], "share_url": None,
              "change_kind": "update" if update else "new", "version": version,
              "preview_image_path": None, "preview_status": "no_screen"}
    screen = next((c for c in components.values()
                   if c.get("kind") == "tinyassets.app-ui.v1"), None)
    if screen is None:
        return result
    result["preview_status"] = "owner_context_required"
    return _with_preview(result, universe_id)


def _with_preview(result: dict[str, Any], universe_id: str) -> dict[str, Any]:
    from tinyassets.api.app_ui import preview_app_ui
    from tinyassets.api.custom_agents import _binding_access

    if not universe_id or result["preview_status"] in {"no_screen", "ready"}:
        return result
    try:
        if _binding_access(universe_id, write=True) is not None:
            return result
        report = preview_app_ui(universe_id=universe_id,
                                ui_id="publication:" + result["listing_id"])
        if "error" in report:
            result["preview_status"] = "unavailable"
            return result
        result["preview_image_path"] = report["screenshot"]
        result["preview_status"] = "ready"
    except Exception:  # noqa: BLE001 - publication already committed
        logger.warning("Publication preview unavailable", exc_info=True)
        result["preview_status"] = "unavailable"
    return result


def receipt_completion(receipt: dict[str, Any], *, universe_id: str,
                       action: dict[str, Any]) -> dict[str, Any]:
    """Enrich only AFTER the publish claim is durably finished, including old pins."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.custom_agents import get_definition

    if receipt.get("completion"):
        return _with_preview(dict(receipt["completion"]), universe_id)
    try:
        agent = get_definition(_base_path(), receipt["agent_definition_id"])
        return completion_for(agent, universe_id=universe_id, action=action)
    except Exception:  # noqa: BLE001 - a legacy publication is already committed
        logger.warning("Published receipt metadata unavailable", exc_info=True)
        version = receipt.get("package", {}).get("version")
        update = (isinstance(version, int) and version > 1) or bool(
            action.get("release", {}).get("parent_release_id"))
        return {"listing_id": receipt["agent_definition_id"], "share_url": None,
                "change_kind": "update" if update else "new", "version": version,
                "preview_image_path": None, "preview_status": "unavailable"}
