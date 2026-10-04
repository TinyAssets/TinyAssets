"""Owner-confirmed copies of public component systems, without file packages."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SYSTEM_TAG = "tinyassets.system.v1"


def _source(definition_id: str, *, readers=None) -> tuple[dict, dict]:
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.publish_requests import AUTOMATION_SPEC_KIND, BRANCH_REF_KIND, UI_KIND
    from tinyassets.automations import (
        OVERLAP_POLICIES,
        OVERLAP_QUEUE,
        AutomationUnavailable,
        _validated_event,
        _validated_trigger,
    )
    from tinyassets.branch_versions import (
        branch_version_def_id,
        branch_version_is_public,
        version_readable_by,
    )
    from tinyassets.command_center_agent_templates import (
        AGENT_REF_KIND,
        resolve_ui_refs,
        templates,
        validate_workflows,
    )
    from tinyassets.command_center_packages import PACKAGE_TAG
    from tinyassets.custom_agents import app_ui_renderability, app_ui_workflow_refs, get_definition
    from tinyassets.daemon_server import get_branch_definition

    base = readers.base if readers is not None else _base_path()
    definition = (readers.definition(definition_id) if readers is not None
                  else get_definition(base, definition_id))
    if (definition is None or SYSTEM_TAG not in definition.get("tags", [])
            or PACKAGE_TAG in definition.get("tags", [])):
        raise LookupError("this is not a published component system")
    components = definition.get("components") or {}
    supported = {UI_KIND, BRANCH_REF_KIND, AUTOMATION_SPEC_KIND, AGENT_REF_KIND}
    if not isinstance(components, dict) or any(
            not isinstance(c, dict) or not isinstance(c.get("kind"), str)
            or c["kind"] not in supported
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
        if not isinstance(version_id, str) or not version_id:
            raise ValueError("a required workflow version is missing or invalid")
        # Metadata only: a retained publication mark does not make a version
        # public after its parent branch is withdrawn. Use the shared read rule
        # as a public reader, even when the publisher is browsing their own card.
        source_id = (readers.version_parent(version_id) if readers is not None
                     else branch_version_def_id(base, version_id))
        try:
            branch = (readers.branch(source_id) if readers is not None
                      else get_branch_definition(base, branch_def_id=source_id)
                      ) if source_id else {}
        except KeyError:
            branch = {}
        if not version_readable_by(
            None, author=branch.get("author"), visibility=branch.get("visibility"),
            public=(readers.version_public(version_id) if readers is not None
                    else branch_version_is_public(base, version_id)),
        ):
            raise ValueError("a required workflow version is missing or no longer public")
        source_keys.setdefault(source_id, []).append(key)
        workflows.append({"key": key, "name": str(component.get("name") or key),
                          "version_id": version_id})
    workflow_keys = {w["key"] for w in workflows}
    agent_templates = templates(base, components, readers=readers)
    resolve_ui_refs(ui, {a["key"] for a in agent_templates})
    validate_workflows(base, workflows, readers=readers)

    def resolve(reference: str) -> str:
        if not isinstance(reference, str) or not reference:
            raise ValueError("a declared workflow reference must name one included workflow")
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
        raw_trigger = component.get("trigger")
        if not isinstance(raw_trigger, dict):
            raise ValueError("an automation trigger must be an object")
        trigger = dict(raw_trigger)
        kind = trigger.get("kind")
        if not isinstance(kind, str) or kind not in {"interval", "cron", "event"}:
            raise ValueError("an automation trigger kind is unsupported")
        if kind == "interval" and (type(trigger.get("interval_seconds")) is not int
                                   or trigger["interval_seconds"] <= 0):
            raise ValueError("an automation interval must be a positive whole number")
        field = "cron_expr" if kind == "cron" else "event_type" if kind == "event" else ""
        if field and (not isinstance(trigger.get(field), str) or not trigger[field].strip()):
            raise ValueError("an automation trigger is missing its schedule or event")
        if not isinstance(component.get("overlap", ""), str):
            raise ValueError("an automation overlap policy must be text")
        raw_filter = trigger.get("event_filter", {})
        if not isinstance(raw_filter, dict):
            raise ValueError("an automation event filter must be an object")
        event_filter = dict(raw_filter)
        if "branch_def_id" in event_filter:
            event_filter["branch_def_id"] = resolve(event_filter["branch_def_id"])
        trigger["event_filter"] = event_filter
        # Match registration semantics before any workflow/UI is materialised.
        # These shared validators are pure; registration itself would mutate the
        # recipient and must remain behind confirmation.
        overlap = component.get("overlap", "").strip() or OVERLAP_QUEUE
        try:
            if overlap not in OVERLAP_POLICIES:
                raise AutomationUnavailable("overlap_invalid")
            if kind == "event":
                trigger["event_type"], trigger["event_filter"] = _validated_event(
                    trigger["event_type"], event_filter)
            else:
                _validated_trigger(trigger.get("interval_seconds", 0) if kind == "interval"
                                   else 0, trigger.get("cron_expr", "") if kind == "cron" else "")
        except AutomationUnavailable as exc:
            raise ValueError(f"an automation configuration is invalid ({exc.reason})") from None
        automations.append({"key": key, "name": str(component.get("name") or key),
                            "workflow": resolve(component.get("workflow", "")),
                            "trigger": trigger, "overlap": overlap})
    parts = {"ui": ui, "workflows": workflows, "automations": automations}
    # Preserve old component-only pin digests when no new templates exist, so
    # a deployment cannot strand an already partially materialised legacy copy.
    if agent_templates:
        parts["agent_templates"] = agent_templates
    return definition, parts


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
                     "agent_template_count": len(parts.get("agent_templates", [])),
                     "available": not reason, "unavailable_reason": reason})
        if len(rows) == 12:
            break
    return rows


def _plan(action: dict, *, readers=None) -> dict:
    definition, parts = _source(action["agent_definition_id"], readers=readers)
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
    from tinyassets.command_center_agent_templates import consent_lines

    lines.extend(consent_lines(plan.get("agent_templates", [])))
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
