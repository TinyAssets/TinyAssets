"""Read a public design for an isolated owner preview, without asking or copying."""

from __future__ import annotations

import base64
import hashlib
import json


def read_preview(*, universe_id: str, definition_id: str) -> dict:
    """Return only published bytes; the browser gives them no live owner bridge."""
    from tinyassets.api import package_requests, system_copy_requests
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.pending_requests import _owner_gate
    from tinyassets.command_center_packages import PACKAGE_TAG
    from tinyassets.custom_agents import app_ui_renderability, get_definition

    _, _, denial = _owner_gate(universe_id)
    if denial is not None:
        return denial
    if not isinstance(definition_id, str) or not definition_id or len(definition_id) > 200:
        return {"error": "preview_unavailable", "detail": "Choose a published command center."}
    definition = get_definition(_base_path(), definition_id)
    tags = (definition or {}).get("tags") or []
    if not definition or not ({PACKAGE_TAG, system_copy_requests.SYSTEM_TAG} & set(tags)):
        return {"error": "preview_unavailable", "detail": "This public design is unavailable."}

    files: dict[str, bytes] = {}
    package = PACKAGE_TAG in tags
    try:
        if package:
            definition, _, _, files = package_requests._load(
                {"agent_definition_id": definition_id})
            screens = package_requests._components(definition)["ui"]
            if len(screens) != 1:
                raise ValueError("This publication has no supported visual screen.")
            ui = screens[0]
            reason = app_ui_renderability(ui)
            if reason:
                raise ValueError(reason["reason"])
            # Use public-version readability, not a retained publication mark.
            # A file package and a component system share workflow references.
            from tinyassets.branch_versions import (
                branch_version_def_id,
                branch_version_is_public,
                version_readable_by,
            )
            from tinyassets.daemon_server import get_branch_definition

            for workflow in package_requests._components(definition)["workflows"]:
                version = workflow.get("published_version_id", "")
                if not isinstance(version, str) or not version:
                    raise ValueError("A required public workflow version is invalid.")
                source = branch_version_def_id(_base_path(), version)
                try:
                    branch = (get_branch_definition(_base_path(), branch_def_id=source)
                              if source else {})
                except KeyError:
                    branch = {}
                if not version_readable_by(
                    None, author=branch.get("author"), visibility=branch.get("visibility"),
                    public=branch_version_is_public(_base_path(), version),
                ):
                    raise ValueError("A required public workflow is unavailable.")
        else:
            definition, parts = system_copy_requests._source(definition_id)
            ui = parts["ui"]

        assets = []
        for path, ref in (ui.get("assets") or {}).items():
            # A published manifest must carry the exact declared bytes. Never
            # consult the publisher's live folder or private UI blob store.
            data = files.get(path)
            if (data is None or len(data) != ref["size"]
                    or hashlib.sha256(data).hexdigest() != ref["sha256"]):
                raise ValueError("This preview needs an asset absent from its public package.")
            assets.append({"path": path, "mime_type": ref["media_type"],
                           "base64": base64.b64encode(data).decode("ascii")})
    except (ValueError, LookupError) as exc:
        return {"error": "preview_unavailable", "detail": str(exc)}

    fingerprint = hashlib.sha256(json.dumps(
        definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return {"agent_definition_id": definition_id,
            "publication_kind": "package" if package else "system",
            "name": str(definition.get("name") or ""),
            "description": str(definition.get("description") or ""),
            "author_id": str(definition.get("author_id") or ""),
            "source_fingerprint": fingerprint, "ui": ui, "assets": assets,
            "available": True, "unavailable_reason": ""}
