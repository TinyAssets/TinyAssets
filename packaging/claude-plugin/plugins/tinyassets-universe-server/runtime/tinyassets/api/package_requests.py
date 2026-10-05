"""The ``install`` ask: a published command-center package, quarantined until its owner says yes.

Change ``command-center-packages`` (D5). The second half of "publish the whole
command center as a package": another account's agent finds a package and
asks; its owner sees what lands where and confirms; only then does any of it
exist in their command center, as THEIR copy.

* **Ask** (served agent). ``capture_action`` reads the package's public
  definition, loads its blob from platform storage, runs the ingestion
  boundary (``check_blob``) and plans every file's destination against the
  installer's folder. Nothing is written into the command center. The pin
  (``pending_requests._pin_consent``) is the quarantine record: it lives
  outside every agent-reachable location and holds the plan, the digest and
  the tab the platform wrote.
* **Answer** (a person's surface only). ``execute_action`` executes the PIN,
  never the pending-request row. It re-verifies the blob, re-plans and refuses
  if the plan moved, claims the pin atomically, reserves the installer's
  storage, then materialises: private remixes of each workflow under its
  published name, the UI in their library, each automation created PAUSED, and
  the files written ``O_EXCL`` without following links. Every component's new id
  is recorded as it lands, so a retry resumes rather than duplicates.

What never crosses: the publisher's credentials, private data, model choice,
or any write path back to them. A copy runs as whoever installed it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ACTION_TYPE = "install"
_MAX_ID = 200

#: The fixed consent sentence: the platform's words, not the agent's.
INSTALL_SENTENCE = (
    "Everything installs as your own copy, in this command center, and runs as you "
    "with your own connections and models. It never reaches the publisher's "
    "command center. Its automations arrive paused; nothing runs until you resume "
    "one. Files already here stay yours and are not replaced."
)

#: Wider than any package path: the install tab lists every destination in full.
_FULL = 1000


def _shown(value: Any, limit: int = 80) -> str:
    from tinyassets.api.publish_requests import _shown as shown

    return shown(value, limit)


def validate_action(action: dict[str, Any]) -> dict[str, Any]:
    """Shape only. Whether the package exists and installs is ``capture_action``'s."""
    from tinyassets.command_center_packages import agent_id

    definition_id = action.get("agent_definition_id")
    if (not isinstance(definition_id, str) or not definition_id.strip()
            or len(definition_id.strip()) > _MAX_ID):
        raise ValueError("install needs the agent_definition_id of a published package")
    return {"type": ACTION_TYPE, "agent_definition_id": definition_id.strip(),
            "agent": agent_id(action.get("agent"))}


def _load(action: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any],
                                          dict[str, Any], dict[str, bytes]]:
    """``(definition, package component, manifest, files)``, all verified.

    The component's sha256, size and file count must match the blob, and the
    blob must pass the ingestion boundary, before anything is planned from it.
    """
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import (
        PACKAGE_KIND,
        PACKAGE_TAG,
        PackageError,
        check_blob,
        read_blob,
    )
    from tinyassets.custom_agents import get_definition

    definition = get_definition(_base_path(), action["agent_definition_id"])
    if definition is None or PACKAGE_TAG not in (definition.get("tags") or []):
        raise LookupError(f"no published package is {action['agent_definition_id']}")
    component = (definition.get("components") or {}).get("package") or {}
    if component.get("kind") != PACKAGE_KIND:
        raise LookupError(f"no published package is {action['agent_definition_id']}")
    try:
        blob = read_blob(_base_path(), str(component.get("blob_sha256") or ""))
        manifest, files = check_blob(blob)
    except PackageError as exc:
        raise ValueError(f"this package cannot be installed: {exc}") from None
    if component.get("size_bytes") != len(blob) or component.get("file_count") != len(files):
        raise ValueError("this package cannot be installed: its listing does not match "
                         "its content")
    return definition, component, manifest, files


def _connections_you_have(actor: str) -> set[str] | None:
    """Connection names the installer already holds, or None when unknown."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.storage.outbound_connections import ConnectionLedger

    try:
        views = ConnectionLedger(Path(_base_path()) / "outbound.db").list_connection_views(
            owner_user_id=actor, active_only=True, limit=500)
    except Exception:  # noqa: BLE001 - a preview line, never a refusal
        return None
    return {v.destination for v in views}


def _components(definition: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from tinyassets.api.publish_requests import (
        AUTOMATION_SPEC_KIND,
        BRANCH_REF_KIND,
        UI_KIND,
    )
    from tinyassets.command_center_agent_templates import AGENT_REF_KIND
    from tinyassets.command_center_packages import PACKAGE_KIND

    components = definition.get("components") or {}
    supported = {AUTOMATION_SPEC_KIND, BRANCH_REF_KIND, UI_KIND, PACKAGE_KIND, AGENT_REF_KIND}
    if not isinstance(components, dict) or any(
            not isinstance(c, dict) or not isinstance(c.get("kind"), str)
            or c["kind"] not in supported
            for c in components.values()):
        raise ValueError("this package contains components this copier does not support")
    if any(c["kind"] == UI_KIND and key != "ui" for key, c in components.items()):
        raise ValueError("this package needs one declared ui component")
    workflows = [{"key": k, **c} for k, c in sorted(components.items())
                 if isinstance(c, dict) and c.get("kind") == BRANCH_REF_KIND]
    automations = [{"key": k, **c} for k, c in sorted(components.items())
                   if isinstance(c, dict) and c.get("kind") == AUTOMATION_SPEC_KIND]
    ui = components.get("ui") if isinstance(components.get("ui"), dict) else None
    if ui is not None and ui.get("kind") != UI_KIND:
        ui = None
    return {"workflows": workflows, "automations": automations, "ui": [ui] if ui else []}


def _plan(uid: str, action: dict[str, Any]) -> dict[str, Any]:
    """Everything the tab shows and the answer will do, from verified inputs."""
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.command_center_packages import (
        PackageError,
        human,
        install_review_groups,
        plan_install,
        scan_install,
    )
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        raise PermissionError("an authenticated owner is required")
    definition, component, manifest, files = _load(action)
    try:
        placement = plan_install(_universe_dir(uid), manifest, files)
    except PackageError as exc:
        raise ValueError(f"this package cannot be installed: {exc}") from None
    parts = _components(definition)
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_agent_templates import (
        resolve_ui_refs,
        templates,
        validate_workflows,
    )
    from tinyassets.custom_agents import app_ui_workflow_refs

    agent_templates = templates(_base_path(), definition.get("components") or {})
    validate_workflows(_base_path(), parts["workflows"], version_field="published_version_id")
    workflow_keys = {workflow["key"] for workflow in parts["workflows"]}
    for ui in parts["ui"]:
        resolve_ui_refs(ui, {a["key"] for a in agent_templates})
        if any(key not in workflow_keys for key in app_ui_workflow_refs(ui).values()):
            raise ValueError("package workflow_refs must name its selected workflow components")
    needs = component.get("needs") or {}
    have = _connections_you_have(actor)
    connections = [{"name": str(name), "you_have": None if have is None else name in have}
                   for name in needs.get("connections") or []]
    # The content screen runs on the verified files at quarantine time; its
    # findings join the pinned plan so the tab shows exactly what was seen.
    safety = install_review_groups(scan_install(files))
    digest = hashlib.sha256(json.dumps({
        "definition": definition["agent_definition_id"],
        "blob": component["blob_sha256"],
        "agent": action["agent"],
        "placement": placement,
        "safety": safety,
    }, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "definition_id": definition["agent_definition_id"],
        "author": str(definition.get("author_id") or ""),
        "name": str(definition.get("name") or ""),
        "version": component.get("version"),
        "size": human(component["size_bytes"]),
        "blob_sha256": component["blob_sha256"],
        "placement": placement,
        "agent_templates": agent_templates,
        "workflows": [{"key": w["key"], "name": str(w.get("name") or w["key"]),
                       "version_id": str(w.get("published_version_id") or "")}
                      for w in parts["workflows"]],
        "automations": [{"key": a["key"], "name": str(a.get("name") or a["key"]),
                         "workflow": str(a.get("workflow") or ""),
                         "trigger": a.get("trigger") or {},
                         "overlap": str(a.get("overlap") or "")}
                        for a in parts["automations"]],
        "ui": parts["ui"][0] if parts["ui"] else None,
        "model": str(needs.get("model") or ""),
        "connections": connections,
        "safety": safety,
        "digest": digest,
    }


def capture_action(uid: str, action: dict[str, Any]) -> dict[str, Any]:
    """Quarantine: verify and plan now; nothing touches the command center."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.api.system_copy_requests import SYSTEM_TAG
    from tinyassets.command_center_packages import PACKAGE_TAG
    from tinyassets.custom_agents import get_definition

    definition = get_definition(_base_path(), action["agent_definition_id"])
    tags = (definition or {}).get("tags") or []
    if SYSTEM_TAG in tags and PACKAGE_TAG not in tags:
        from tinyassets.api.system_copy_requests import capture_action as capture_system

        return capture_system(action)
    plan = _plan(uid, action)
    return {**action, "snapshot_digest": plan["digest"], "plan": plan}


def tab_text(action: dict[str, Any]) -> tuple[str, str, str]:
    """``(kind, title, body)``, written from the pinned plan only."""
    plan = action["plan"]
    if plan.get("publication_kind") == "system":
        from tinyassets.api.system_copy_requests import tab_text as system_tab

        return system_tab(action)
    placement = plan["placement"]
    lines = [f"Package: {_shown(plan['name'], 120)} (version {plan['version']}, "
             f"{plan['size']}), published by {_shown(plan['author'], 80)}"]
    safety = plan.get("safety") or []
    if safety:
        lines.append("Worth a careful look before installing — this package:")
        for group in safety:
            shown = ", ".join(_shown(p, 60) for p in group["shown"])
            extra = (f" (+{group['count'] - len(group['shown'])} more)"
                     if group["count"] > len(group["shown"]) else "")
            lines.append(f"- {group['kind']}: {shown}{extra}")
    if plan["workflows"]:
        lines.append("Workflows, as your own private copies:")
        lines.extend(f"- {_shown(w['name'])}" for w in plan["workflows"])
    if plan["ui"]:
        lines.append(f"The screen \"{_shown(plan['ui'].get('name'))}\", added to your screens")
    from tinyassets.command_center_agent_templates import consent_lines

    lines.extend(consent_lines(plan.get("agent_templates", []), has_screen=bool(plan["ui"])))
    if plan["automations"]:
        lines.append("Automations, paused until you resume them:")
        lines.extend(f"- {_shown(a['name'])}" for a in plan["automations"])
    lines.append(f"Files ({len(placement['land'])}) written into this command center:")
    for entry in placement["land"]:
        lines.append(f"  - {_shown(entry['to'], _FULL)}")
    if placement["keep"]:
        lines.append(f"Already here, so kept as yours ({len(placement['keep'])}):")
        lines.extend(f"  - {_shown(p, _FULL)}" for p in placement["keep"])
    if any(p["to"].startswith("agents/") for p in placement["land"]):
        lines.append(f"Its agent's instructions and skills go under "
                     f"agents/{placement['agent_slug']}/, beside your own.")
    if plan["model"]:
        lines.append(f"It was built with the model {_shown(plan['model'], 80)}; your copy "
                     "uses your own models.")
    for c in plan["connections"]:
        state = ("you have one by that name" if c["you_have"]
                 else "you will need to connect your own" if c["you_have"] is False
                 else "connect your own if you have none")
        lines.append(f"Needs the connection {_shown(c['name'], 60)}: {state}.")
    lines.append("")
    lines.append(INSTALL_SENTENCE)
    return ("Install", f"Install \"{_shown(plan['name'], 120)}\" into this command center?",
            "\n".join(lines))


_CHANGED = (
    "this command center changed since the install was shown (a file or folder it "
    "would write appeared), so nothing was installed; ask again and the tab will "
    "show what lands now"
)


def execute_action(uid: str, pinned: dict[str, Any]) -> dict[str, Any]:
    """Materialise exactly the pinned plan, or nothing new. Raises to leave the ask pending.

    ``pinned`` is the platform's consent record (``pin_for_request``), never the
    pending-request row.
    """
    from tinyassets import storage_accounting
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import (
        LostClaim,
        PackageError,
        claim,
        finish,
        human,
    )
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        raise PermissionError("an authenticated owner is required")
    action = pinned["record"]["action"]
    plan = action["plan"]
    if plan.get("publication_kind") == "system":
        from tinyassets.api.system_copy_requests import execute_action as copy_system

        return copy_system(uid, pinned)
    if pinned["state"] == "activated":
        return {**pinned["progress"], "already_installed": True}
    if pinned["state"] == "pinned" and _plan(uid, action)["digest"] != pinned["digest"]:
        # A resume skips this: its own earlier writes are what moved the plan.
        raise ValueError(_CHANGED)
    base = _base_path()
    try:
        state, token = claim(base, universe_id=uid, pin_id=pinned["pin_id"])
    except PackageError as exc:
        raise ValueError(str(exc)) from None
    if state == "activated":
        return {**pinned["progress"], "already_installed": True}
    progress: dict[str, Any] = dict(pinned["progress"]) if state == "activating" else {}
    if state == "activating":
        # Progress is re-read after the claim: the pre-claim copy may be stale.
        from tinyassets.command_center_packages import pin_progress

        progress = pin_progress(base, universe_id=uid, pin_id=pinned["pin_id"])
    progress.setdefault("workflows", {})
    progress.setdefault("automations", {})
    progress.setdefault("files", [])
    _, _, _, files = _load(action)
    sizes = {e["to"]: len(files[e["path"]]) for e in plan["placement"]["land"]}
    landed_before = sum(sizes.get(p, 0) for p in progress["files"])
    from tinyassets.universe_owner import owner_of

    # Files in a command center are charged to its owner, as every write into
    # it is (`api/wiki.py`); None is an unattributed universe. Only what is
    # still to land is reserved, so a resume does not charge twice.
    remaining = max(0, plan["placement"]["bytes"] - landed_before)
    try:
        reservation = storage_accounting.reserve(
            base, account_id=owner_of(base, uid), scope_id=uid, store="universe_files",
            nbytes=remaining)
    except storage_accounting.StorageRefused as refused:
        detail = storage_accounting.visible_record(refused).get("error", "")
        _release(uid, pinned["pin_id"], progress, token)
        raise ValueError(f"Installing writes {human(remaining)}, more than your storage "
                         f"has room for, so nothing was installed. {detail}") from None

    def landed_now() -> int:
        return sum(sizes.get(p, 0) for p in progress["files"]) - landed_before

    try:
        _materialise(uid, actor, pinned["pin_id"], plan, files, progress, token)
    except BaseException as exc:
        # What landed stays charged (a partial install keeps its files); only
        # the unwritten rest of the reservation is given back.
        storage_accounting.commit(reservation, min(landed_now(), remaining))
        if not isinstance(exc, LostClaim):
            _release(uid, pinned["pin_id"], progress, token)
        if isinstance(exc, (PackageError, OSError)):
            raise ValueError(f"the install stopped part way ({exc}); what landed is listed "
                             "in your command center, and confirming again resumes it"
                             ) from None
        raise
    storage_accounting.commit(reservation, min(landed_now(), remaining))
    receipt = {"installed": True, "package": plan["name"], "version": plan["version"],
               **progress}
    try:
        finish(base, universe_id=uid, pin_id=pinned["pin_id"], progress=receipt, token=token)
    except LostClaim as exc:
        raise ValueError(str(exc)) from None
    return receipt


def _release(uid: str, pin_id: str, progress: dict[str, Any], token: str) -> None:
    """Keep the progress for a resume; the pin stays ``activating``, unheld."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import LostClaim, record_progress, unclaim

    try:
        record_progress(_base_path(), universe_id=uid, pin_id=pin_id, progress=progress,
                        token=token)
    except LostClaim:
        return
    unclaim(_base_path(), universe_id=uid, pin_id=pin_id, token=token)


def _materialise(uid: str, actor: str, pin_id: str, plan: dict[str, Any],
                 files: dict[str, bytes], progress: dict[str, Any], token: str) -> None:
    """Each component once, recording its new id as it lands.

    ``save`` runs before AND after every effect: it renews the lease and raises
    `LostClaim` if another confirm took over, so no effect runs after that.
    """
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_agent_templates import check_targets
    from tinyassets.command_center_packages import record_progress

    check_targets(_base_path(), uid, actor, pin_id, plan.get("agent_templates", []))

    def save() -> None:
        record_progress(_base_path(), universe_id=uid, pin_id=pin_id, progress=progress,
                        token=token)

    for workflow in plan["workflows"]:
        if workflow["key"] not in progress["workflows"]:
            save()
            progress["workflows"][workflow["key"]] = _remix(pin_id, workflow)
            save()
    if plan.get("agent_templates"):
        from tinyassets.command_center_agent_templates import install

        progress.setdefault("agents", {})
        for template in plan["agent_templates"]:
            save()
            # Recheck even a recorded target: an intervening recipient edit is
            # not permission to wire a new UI to a changed agent.
            progress["agents"][template["key"]] = install(
                _base_path(), uid, actor, pin_id, template)
            save()
    if plan["ui"] and "ui" not in progress:
        ui = dict(plan["ui"])
        if "workflow_refs" in ui:
            ui["workflow_refs"] = {alias: progress["workflows"][key]
                                   for alias, key in ui["workflow_refs"].items()}
        if "agent_refs" in ui:
            ui["agent_refs"] = {alias: progress.get("agents", {})[key]
                                for alias, key in ui["agent_refs"].items()}
        if "ui_intended" not in progress:
            progress["ui_intended"] = _free_ui_id(uid, ui)
        save()
        progress["ui"] = _add_ui(uid, ui, progress["ui_intended"])
        save()
    for automation in plan["automations"]:
        if automation["key"] not in progress["automations"]:
            save()
            progress["automations"][automation["key"]] = _automation(
                uid, actor, pin_id, automation, progress["workflows"])
            save()
    _write_files(uid, plan, files, progress, save)
    save()


def _remix(pin_id: str, workflow: dict[str, str]) -> str:
    """A private copy authored by the installer, from the immutable snapshot.

    The snapshot's skills are passed explicitly (an empty list included), so
    the fork can never fall back to the source branch's live skills; the model
    policy is cleared, so the copy runs on the installer's own model.
    """
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.api.helpers import _base_path
    from tinyassets.branch_versions import get_branch_version

    version = get_branch_version(_base_path(), workflow["version_id"])
    if version is None:
        raise ValueError(f"the workflow \"{workflow['name']}\" is no longer published")
    snapshot = version.snapshot if isinstance(version.snapshot, dict) else {}
    spec = {"name": workflow["name"], "fork_from": workflow["version_id"],
            "visibility": "private", "skills": list(snapshot.get("skills") or []),
            "default_llm_policy": None}
    raw = _extensions_impl(action="build_branch", spec_json=json.dumps(spec),
                           request_id=f"package-{pin_id}-{workflow['key']}")
    result = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(result, dict):
        result = {"error": str(raw)[:200]}
    branch_id = result.get("branch_def_id") or (result.get("branch") or {}).get(
        "branch_def_id")
    if not branch_id:
        detail = result.get("error") or result.get("status")
        raise ValueError(f"the workflow \"{workflow['name']}\" could not be copied ({detail})")
    return str(branch_id)


def _library(uid: str) -> dict[str, dict[str, Any]]:
    from tinyassets.api.app_ui import read_app_ui

    library = (read_app_ui(universe_id=uid).get("app_ui") or {}).get("ui_library") or []
    return {str(c.get("ui_id")): c for c in library if isinstance(c, dict)}


def _library_ids(uid: str) -> set[str]:
    return set(_library(uid))


def _free_ui_id(uid: str, ui: dict[str, Any]) -> str:
    """A ``ui_id`` the installer's library does not hold yet."""
    taken = _library_ids(uid)
    ui_id, n = str(ui.get("ui_id") or "package-ui"), 1
    base_id = ui_id
    while ui_id in taken:
        n += 1
        ui_id = f"{base_id[:56]}-{n}"
    return ui_id


def _add_ui(uid: str, ui: dict[str, Any], ui_id: str) -> str:
    """The screen, added under the id recorded before this call.

    Already there with THIS screen's content (a crash after the add, before it
    was recorded): nothing more is added. Already there with other content (the
    owner added a screen meanwhile): that one is theirs, so this one goes under
    a fresh id rather than being silently taken for it (gpt-6-astra, code r2 #8).
    """
    from tinyassets.api.app_ui import change_app_ui
    from tinyassets.custom_agents import app_ui_etag

    library = _library(uid)
    if ui_id in library:
        if app_ui_etag(library[ui_id]) == app_ui_etag({**ui, "ui_id": ui_id}):
            return ui_id
        ui_id = _free_ui_id(uid, {**ui, "ui_id": ui_id})
    outcome = change_app_ui(universe_id=uid, operation="add_ui",
                            payload={"component": {**ui, "ui_id": ui_id}})
    if outcome.get("error"):
        detail = outcome.get("detail") or outcome["error"]
        raise ValueError(f"the screen could not be added ({detail})")
    return ui_id


def _automation(uid: str, actor: str, pin_id: str, spec: dict[str, Any],
                workflows: dict[str, str]) -> str:
    """The automation against the installer's copy, stored PAUSED in one insert."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.automations import AutomationUnavailable, register_automation

    branch_id = workflows.get(spec["workflow"])
    if not branch_id:
        raise ValueError(f"the automation \"{spec['name']}\" drives a workflow this "
                         "package does not carry")
    trigger = dict(spec["trigger"] or {})
    event_filter = dict(trigger.get("event_filter") or {})
    if event_filter.get("branch_def_id"):
        # Published as a workflow KEY; in a copy it names the installer's copy.
        event_filter["branch_def_id"] = workflows.get(event_filter["branch_def_id"], "")
    kind = str(trigger.get("kind") or "")
    try:
        row = register_automation(
            _base_path(), universe_id=uid, owner_principal_id=actor, name=spec["name"],
            branch_def_id=branch_id,
            interval_seconds=int(trigger.get("interval_seconds") or 0) if kind == "interval"
            else 0,
            cron_expr=str(trigger.get("cron_expr") or "") if kind == "cron" else "",
            event_type=str(trigger.get("event_type") or "") if kind == "event" else "",
            event_filter=event_filter if kind == "event" else None,
            overlap=spec.get("overlap") or "",
            event_key=f"package:{uid}:{actor}:{pin_id}:{spec['key']}",
            paused_reason="installed from a package; resume it when you are ready",
        )
    except AutomationUnavailable as exc:
        raise ValueError(f"the automation \"{spec['name']}\" could not be created "
                         f"({exc.reason})") from None
    if row.universe_id != uid or row.owner_principal_id != actor:
        # A replayed key must name THIS installer's row, never anyone else's.
        raise ValueError(f"the automation \"{spec['name']}\" could not be created")
    return row.automation_id


def _write_files(uid: str, plan: dict[str, Any], files: dict[str, bytes],
                 progress: dict[str, Any], save: Any) -> None:
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.command_center_packages import write_new_file

    udir = _universe_dir(uid)
    done = set(progress["files"])
    kept = progress.setdefault("kept", [])
    for entry in plan["placement"]["land"]:
        if entry["to"] in done:
            continue
        save()
        if write_new_file(udir, entry["to"], files[entry["path"]]):
            progress["files"].append(entry["to"])
        elif entry["to"] not in kept:
            kept.append(entry["to"])
    for path in plan["placement"]["keep"]:
        if path not in kept:
            kept.append(path)


def list_packages(*, query: str = "", author: str = "", limit: int = 30,
                  offset: int = 0) -> list[dict[str, Any]]:
    """The listing: one row per current bundle, from its immutable definition.

    Name, description and author are the publisher's words; size, version, file
    count, agents and needs are the platform's summary. Nothing else of the
    publisher's is here.
    """
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import PACKAGE_KIND, PACKAGE_TAG, human
    from tinyassets.custom_agents import list_definitions

    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    bounded_limit = max(1, min(int(limit), 100))

    def definitions():
        start = 0
        while True:
            batch = list_definitions(_base_path(), query=query, tags=[PACKAGE_TAG],
                                     author_id=author, limit=100, offset=start)
            yield from batch
            if len(batch) < 100:
                break
            start += len(batch)

    rows = []
    matched = 0
    for definition in definitions():
        component = (definition.get("components") or {}).get("package") or {}
        if component.get("kind") != PACKAGE_KIND:
            continue
        matched += 1
        if matched <= offset:
            continue
        rows.append({
            **{key: definition[key] for key in (
                "bundle_id", "bundle_version", "previous_definition_id", "current_definition_id"
            ) if key in definition},
            "agent_definition_id": definition["agent_definition_id"],
            "name": definition.get("name", ""),
            "description": definition.get("description", ""),
            "author_id": definition.get("author_id", ""),
            "version": definition.get("bundle_version", component.get("version")),
            "size": human(int(component.get("size_bytes") or 0)),
            "file_count": component.get("file_count"),
            "agents": component.get("agents") or [],
            "needs": component.get("needs") or {},
            "created_at": definition.get("created_at"),
        })
        if len(rows) >= bounded_limit:
            break
    return rows


__all__ = [
    "ACTION_TYPE",
    "INSTALL_SENTENCE",
    "capture_action",
    "execute_action",
    "list_packages",
    "tab_text",
    "validate_action",
]
