"""Owner-confirmed copies of public component systems, without file packages."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SYSTEM_TAG = "tinyassets.system.v1"


def _source(definition_id: str) -> tuple[dict, dict]:
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.publish_requests import AUTOMATION_SPEC_KIND, BRANCH_REF_KIND, UI_KIND
    from tinyassets.branch_versions import branch_version_is_public, get_branch_version
    from tinyassets.command_center_packages import PACKAGE_TAG
    from tinyassets.custom_agents import app_ui_renderability, app_ui_workflow_refs, get_definition

    base = _base_path()
    definition = get_definition(base, definition_id)
    if (definition is None or SYSTEM_TAG not in definition.get("tags", [])
            or PACKAGE_TAG in definition.get("tags", [])):
        raise LookupError("this is not a published component system")
    components = definition.get("components") or {}
    supported = {UI_KIND, BRANCH_REF_KIND, AUTOMATION_SPEC_KIND}
    if any(not isinstance(c, dict) or c.get("kind") not in supported
           for c in components.values()):
        raise ValueError("this system contains components this copier does not support")
    screens = [c for c in components.values() if c["kind"] == UI_KIND]
    if len(screens) != 1 or app_ui_renderability(screens[0]):
        raise ValueError("this system needs one supported screen before it can be copied")
    ui = dict(screens[0])
    if ui.get("assets"):
        raise ValueError("this screen needs file assets; its author must publish a file package")
    workflows, source_keys = [], {}
    for key, component in components.items():
        if component["kind"] != BRANCH_REF_KIND:
            continue
        version_id = component.get("published_version_id")
        if not version_id or not branch_version_is_public(base, version_id):
            raise ValueError("a required workflow version is missing or no longer public")
        version = get_branch_version(base, version_id)
        if version is None:
            raise ValueError("a required workflow version is missing or no longer public")
        source_id = version.branch_def_id
        source_keys.setdefault(source_id, []).append(key)
        workflows.append({"key": key, "name": str(component.get("name") or key),
                          "version_id": version_id})
    workflow_keys = {w["key"] for w in workflows}

    def resolve(reference: str) -> str:
        if reference in workflow_keys:
            return reference
        candidates = source_keys.get(reference, [])
        if len(candidates) == 1:
            return candidates[0]
        raise ValueError("a declared workflow reference does not name one included public workflow")

    refs = app_ui_workflow_refs(ui)
    if "workflow_refs" in ui:
        ui["workflow_refs"] = {alias: resolve(reference) for alias, reference in refs.items()}
    if any(source_id and source_id in ui.get("script", "") for source_id in source_keys):
        raise ValueError("this screen embeds a publisher workflow ID; its author must use "
                         "declared workflow_refs before it can be copied safely")
    automations = []
    for key, component in components.items():
        if component["kind"] != AUTOMATION_SPEC_KIND:
            continue
        trigger = dict(component.get("trigger") or {})
        event_filter = dict(trigger.get("event_filter") or {})
        if event_filter.get("branch_def_id"):
            event_filter["branch_def_id"] = resolve(event_filter["branch_def_id"])
        trigger["event_filter"] = event_filter
        automations.append({"key": key, "name": str(component.get("name") or key),
                            "workflow": resolve(component.get("workflow", "")),
                            "trigger": trigger, "overlap": component.get("overlap") or ""})
    return definition, {"ui": ui, "workflows": workflows, "automations": automations}


def list_systems() -> list[dict]:
    """Public metadata only; unsupported designs remain visible with a reason."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import PACKAGE_TAG
    from tinyassets.custom_agents import list_definitions

    def definitions():
        offset = 0
        while True:
            batch = list_definitions(_base_path(), tags=[SYSTEM_TAG], limit=100, offset=offset)
            yield from batch
            if len(batch) < 100:
                return
            offset += len(batch)

    rows, seen = [], set()
    for definition in definitions():
        if PACKAGE_TAG in definition.get("tags", []):
            continue
        key = (definition.get("author_id"), definition.get("name"))
        if key in seen:
            continue
        seen.add(key)
        reason = ""
        try:
            _, parts = _source(definition["agent_definition_id"])
        except (ValueError, LookupError) as exc:
            reason = str(exc)
            parts = {"workflows": [], "automations": []}
        rows.append({"agent_definition_id": definition["agent_definition_id"],
                     "publication_kind": "system", "name": definition.get("name", ""),
                     "description": definition.get("description", ""),
                     "author_id": definition.get("author_id", ""),
                     "workflow_count": len(parts["workflows"]),
                     "automation_count": len(parts["automations"]),
                     "available": not reason, "unavailable_reason": reason})
        if len(rows) == 12:
            break
    return rows


def _plan(action: dict) -> dict:
    definition, parts = _source(action["agent_definition_id"])
    plan = {"publication_kind": "system", "definition_id": definition["agent_definition_id"],
            "author": definition.get("author_id", ""), "name": definition.get("name", ""),
            **parts, "placement": {"land": [], "keep": [], "bytes": 0}}
    # Complete public source and normalized references are pinned, without an
    # invented blob, package version or access to the publisher's live files.
    plan["digest"] = hashlib.sha256(json.dumps(
        {"definition": definition, "plan": plan}, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return plan


def capture_action(action: dict) -> dict:
    plan = _plan(action)
    return {**action, "snapshot_digest": plan["digest"], "plan": plan}


def tab_text(action: dict) -> tuple[str, str, str]:
    from tinyassets.api.package_requests import INSTALL_SENTENCE, _shown

    plan = action["plan"]
    lines = [f"Public system: {_shown(plan['name'], 120)}, "
             f"published by {_shown(plan['author'])}",
             "Component-only copy. No package or files are imported.",
             f"Screen: {_shown(plan['ui']['name'])}", "Your own private workflow copies:"]
    lines.extend(f"- {_shown(w['name'])}" for w in plan["workflows"])
    if plan["automations"]:
        lines.append("Your automations, paused until you resume them:")
        lines.extend(f"- {_shown(a['name'])}" for a in plan["automations"])
    lines.extend(["", INSTALL_SENTENCE])
    return "Copy", f"Copy the public system \"{_shown(plan['name'], 120)}\"?", "\n".join(lines)


def execute_action(uid: str, pinned: dict[str, Any]) -> dict:
    from tinyassets.api import package_requests, permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import (
        LostClaim,
        PackageError,
        claim,
        finish,
        pin_progress,
    )
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        raise PermissionError("an authenticated owner is required")
    if pinned["state"] == "activated":
        return {**pinned["progress"], "already_installed": True}
    action = pinned["record"]["action"]
    plan = action["plan"]
    if _plan(action)["digest"] != pinned["digest"]:
        raise ValueError("this public system changed since the copy was shown; ask again")
    base = _base_path()
    try:
        state, token = claim(base, universe_id=uid, pin_id=pinned["pin_id"])
    except PackageError as exc:
        raise ValueError(str(exc)) from None
    if state == "activated":
        return {**pin_progress(base, universe_id=uid, pin_id=pinned["pin_id"]),
                "already_installed": True}
    progress = pin_progress(base, universe_id=uid, pin_id=pinned["pin_id"])
    progress.setdefault("workflows", {})
    progress.setdefault("automations", {})
    progress.setdefault("files", [])
    try:
        package_requests._materialise(uid, actor, pinned["pin_id"], plan, {}, progress, token)
        receipt = {"installed": True, "publication_kind": "system", "system": plan["name"],
                   **progress}
        finish(base, universe_id=uid, pin_id=pinned["pin_id"], progress=receipt, token=token)
        return receipt
    except BaseException as exc:
        if not isinstance(exc, LostClaim):
            package_requests._release(uid, pinned["pin_id"], progress, token)
        if isinstance(exc, (PackageError, OSError)):
            raise ValueError("the component copy stopped; confirming again resumes its "
                             "recorded progress without importing any files") from None
        raise
