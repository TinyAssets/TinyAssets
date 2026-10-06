"""Publication facts for the owner; posting behaviour belongs in editable skills."""
from __future__ import annotations

import contextvars
import hashlib
import logging
import threading
from typing import Any

from tinyassets.onboarding.public_run import share_url

logger = logging.getLogger(__name__)

# No unbounded executor queue or thread per publication. The renderer retains
# its existing process/host slot and supervised wall/process/memory bounds.
_BACKGROUND_SLOT = threading.BoundedSemaphore(1)


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
    result = {"listing_id": agent["agent_definition_id"],
              "share_url": share_url(agent["agent_definition_id"]),
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
        result = dict(receipt["completion"])
    else:
        result = None
    try:
        if result is None:
            agent = get_definition(_base_path(), receipt["agent_definition_id"])
            result = completion_for(agent, action=action)
        result["share_url"] = share_url(receipt["agent_definition_id"])
        if result["preview_status"] == "owner_context_required":
            from tinyassets.api.custom_agents import _binding_access

            if universe_id and _binding_access(universe_id, write=True) is None:
                result["preview_status"] = "pending"
        return result
    except Exception:  # noqa: BLE001 - a legacy publication is already committed
        logger.warning("Published receipt metadata unavailable", exc_info=True)
        version = receipt.get("package", {}).get("version")
        update = (isinstance(version, int) and version > 1) or bool(
            action.get("release", {}).get("parent_release_id"))
        return {"listing_id": receipt["agent_definition_id"],
                "share_url": share_url(receipt["agent_definition_id"]),
                "change_kind": "update" if update else "new", "version": version,
                "preview_image_path": None, "preview_status": "unavailable"}


def start_receipt_preview(completion: dict[str, Any], *, universe_id: str,
                          request_id: str) -> None:
    """After resolution commits, enrich the owner's answer without delaying approval.

    Capture authorized immutable content and the destination on the calling
    thread; the worker never borrows ambient auth or looks up a private UI.
    """
    from tinyassets import ui_preview
    from tinyassets.api.custom_agents import _authenticated_actor, _binding_access
    from tinyassets.api.helpers import _base_path, _universe_dir
    from tinyassets.custom_agents import get_definition
    from tinyassets.storage.pending_requests import update_publication_preview

    if completion["preview_status"] != "pending":
        return
    result = dict(completion)
    udir = _universe_dir(universe_id)
    base = _base_path()

    def finish():
        try:
            update_publication_preview(udir, request_id, completion=result)
        except Exception:  # noqa: BLE001 - never undo a committed publication
            logger.warning("Publication preview receipt update failed", exc_info=True)

    try:
        agent = get_definition(_base_path(), result["listing_id"])
        if (_binding_access(universe_id, write=True) is not None
                or agent["author_id"] != _authenticated_actor()):
            result["preview_status"] = "unavailable"
            finish()
            return
        screen = next(c for c in agent["components"].values()
                      if c.get("kind") == "tinyassets.app-ui.v1")
        if not _BACKGROUND_SLOT.acquire(blocking=False):
            result["preview_status"] = "unavailable"
            finish()
            return
    except Exception:  # noqa: BLE001 - publication and resolution already committed
        logger.warning("Publication preview could not start", exc_info=True)
        result["preview_status"] = "unavailable"
        finish()
        return

    def work():
        try:
            try:
                report = ui_preview.preview_public_component(screen)
                from tinyassets.onboarding.public_run import save_preview

                save_preview(base, result["listing_id"], report["png"])
                digest = hashlib.sha256(result["listing_id"].encode()).hexdigest()[:32]
                name = "publication-" + digest
                result["preview_image_path"] = ui_preview.write_preview(udir, name, report["png"])
                result["preview_status"] = "ready"
            except Exception:  # noqa: BLE001 - preview cannot undo publication
                logger.warning("Publication preview unavailable", exc_info=True)
                result["preview_status"] = "unavailable"
            finish()
        finally:
            _BACKGROUND_SLOT.release()

    try:
        context = contextvars.copy_context()
        threading.Thread(target=context.run, args=(work,),
                         name="publication-preview", daemon=True).start()
    except Exception:  # noqa: BLE001 - thread admission failure is preview failure
        _BACKGROUND_SLOT.release()
        result["preview_status"] = "unavailable"
        finish()
