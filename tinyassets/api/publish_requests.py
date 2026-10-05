"""The ``publish`` ask: a universe proposes, its owner confirms, the platform publishes.

A universe that built something -- workflows, the screen that shows them, the
automations that keep them running -- could not share it: the served surface has
no publish (an agent running AS its owner could consent for them), and the app
had nowhere to press. This is the consent gate the deferral note asked for.

The shape is the existing action-bearing ask (``bind_model_access`` is the
model):

* **Ask** (served agent). The action names what to publish. ``capture_action``
  checks every item is the owner's own, pins a digest of each, and records what
  the tab must show. The platform writes the tab's text from that record
  (``tab_text``), so the agent cannot phrase the consent.
* **Answer** (a person's surface only; the served surface has no
  ``answer_request``). ``execute_action`` recomputes every digest -- anything
  edited since the tab was shown publishes nothing -- then makes each branch
  public, publishes a version of each, and publishes ONE definition that
  bundles the UI with a reference to each workflow and the trigger of each
  automation. Idempotent on the request id, so a retried confirm cannot publish
  a second definition.

What is never published: automation ``inputs``, conversations, credentials.
Files travel only in a PACKAGE: an optional ``package`` block publishes the
whole command center's files beside the rest, scrubbed of private items
(``tinyassets.command_center_packages``; change ``command-center-packages``).
A copy of anything published runs as whoever installs it.

The consent record is the platform's, not the row's: ``pending_requests._pin_consent`` stores the
action, its digest and the tab text outside the command-center folder, the rail
renders the tab from it, and the answer executes it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

BRANCH_REF_KIND = "tinyassets.branch-ref.v1"
AUTOMATION_SPEC_KIND = "tinyassets.automation-spec.v1"
UI_KIND = "tinyassets.app-ui.v1"

_MAX_NAME = 120
_MAX_DESCRIPTION = 4000
_MAX_ID = 200
logger = logging.getLogger(__name__)

#: The fixed consent sentence. The platform's words, not the agent's.
PUBLIC_SENTENCE = (
    "Anyone will be able to read and copy these. A copy runs in the copier's own "
    "universe on their own compute and never reaches yours. Publishing does not "
    "share your conversations, credentials or automation inputs."
)


def _ids(raw: Any, field: str, *, required: bool) -> list[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
        raise ValueError(f"{field} must be a list of ids")
    ids: list[str] = []
    for value in raw:
        text = value.strip()
        if not text or len(text) > _MAX_ID:
            raise ValueError(f"{field} holds an empty or over-long id")
        if text not in ids:
            ids.append(text)
    if required and not ids:
        raise ValueError(f"{field} must name at least one")
    return ids


def validate_action(action: dict[str, Any]) -> dict[str, Any]:
    """Shape only: the fields and their types. Ownership is ``capture_action``'s."""
    from tinyassets.command_center_agent_templates import selection

    name = action.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > _MAX_NAME:
        raise ValueError(f"publish needs a public name of 1-{_MAX_NAME} characters")
    description = action.get("description", "")
    if not isinstance(description, str) or len(description) > _MAX_DESCRIPTION:
        raise ValueError(f"description must be text of at most {_MAX_DESCRIPTION} characters")
    ui_id = action.get("ui_id", "")
    if ui_id is None:
        ui_id = ""
    if not isinstance(ui_id, str) or len(ui_id) > 64:
        raise ValueError("ui_id must be the id of one UI in the owner's library")
    validated = {
        "type": "publish",
        "name": name.strip(),
        "description": description.strip(),
        "branch_ids": _ids(action.get("branch_ids"), "branch_ids", required=True),
        "ui_id": ui_id.strip(),
        "automation_ids": _ids(action.get("automation_ids"), "automation_ids", required=False),
    }
    if "agent_templates" in action:
        validated["agent_templates"] = selection(action["agent_templates"])
    if action.get("package") is not None:
        from tinyassets.command_center_packages import validate_options

        validated["package"] = validate_options(action["package"])
    kind = action.get("publish_kind")
    if kind is not None:
        if kind not in ("command_center", "workflows", "system"):
            raise ValueError("publish_kind must be command_center, workflows or system")
        if kind == "command_center" and ("package" not in validated or not validated["ui_id"]):
            raise ValueError("publishing a command center needs its ui_id and an explicit "
                             "package object; review the included files before confirming")
        if kind != "command_center" and "package" in validated:
            raise ValueError("only command_center intent may include a package")
        if kind == "workflows" and validated["ui_id"]:
            raise ValueError("workflow-only publishing cannot include a screen")
        validated["publish_kind"] = kind
    if "release" in action:
        release = action["release"]
        if (not isinstance(release, dict) or "summary" not in release
                or set(release) - {"summary", "series_id", "parent_release_id"}
                or any(not isinstance(value, str) for value in release.values())):
            raise ValueError("release needs a summary and optional exact series and parent IDs")
        summary = release["summary"].strip()
        if not 1 <= len(summary) <= 2000 or any(ord(char) < 32 for char in summary):
            raise ValueError("release summary must be one line of 1 to 2000 characters")
        series_id, parent = release.get("series_id", ""), release.get("parent_release_id", "")
        if bool(series_id) != bool(parent) or len(series_id) > 100 or len(parent) > 100:
            raise ValueError("continuing a release requires both exact series and parent IDs")
        if _publication_kind(validated) != "system":
            raise ValueError("release history currently requires a component-system screen")
        validated["release"] = {"summary": summary, "series_id": series_id,
                                "parent_release_id": parent}
    return validated


def _publication_kind(action: dict[str, Any]) -> str:
    """Describe the actual payload, including legacy asks; never add content."""
    if action.get("package") is not None:
        return "command_center"
    return "system" if action.get("ui_id") else "workflows"


#: Fields of a branch row that publishing itself changes, or pure edit
#: bookkeeping. Everything else in the STORED row -- every nested key included,
#: known to the model or not, `stats` and `version` too -- is part of what
#: becomes public, so it is part of the snapshot (astra round 3: both hold
#: arbitrary text a public read returns).
_VOLATILE_BRANCH_FIELDS = frozenset({"visibility", "published", "updated_at"})

#: The portable fields of a UI component -- exactly what the app renders
#: (``AppUI.FIELDS`` in onboarding/app_ui.js). A stored component may carry
#: anything else; none of it is published.
UI_PORTABLE_FIELDS = ("kind", "version", "ui_id", "name", "markup", "style", "script")
#: Optional fields that publish as they are: library names from the public
#: allowlist and the script type. ``assets`` is NOT one: its bytes live in the
#: publisher's private UI storage and a published copy could not load them.
UI_PORTABLE_OPTIONAL_FIELDS = ("libraries", "script_type", "workflow_refs", "agent_refs")

_CHANGED = (
    "something in this ask changed after you were shown it, so nothing was "
    "published; ask again and the tab will show what is there now"
)


def _canonical(value: Any) -> str:
    from tinyassets.custom_agents import _canonical_json

    return _canonical_json(value)


def export_ui_component(component: dict[str, Any]) -> dict[str, Any]:
    exported = {k: component[k] for k in UI_PORTABLE_FIELDS if k in component}
    missing = [k for k in UI_PORTABLE_FIELDS if k not in exported]
    if missing:
        raise ValueError(f"that UI is missing {', '.join(missing)} and cannot be published")
    if component.get("assets"):
        # Refused by name rather than published without them: a copy whose
        # textures and scripts are missing is a broken UI under the author's name.
        raise ValueError(
            "that UI loads its own files (assets), and publishing a UI with files is "
            "not supported yet; it stays private"
        )
    exported.update({k: component[k] for k in UI_PORTABLE_OPTIONAL_FIELDS if k in component})
    return json.loads(json.dumps(exported))


def _public_branch_row(raw: dict[str, Any]) -> dict[str, Any]:
    """The branch row exactly as it will read once public, minus volatile fields.

    Taken from the STORED row, never through ``BranchDefinition``: normalizing
    drops keys the model does not know, and a public row still carries them
    (astra round 2, P1: ``node_defs[0].private_note`` added after consent was
    outside the digest and published).
    """
    return json.loads(_canonical({k: v for k, v in raw.items()
                                  if k not in _VOLATILE_BRANCH_FIELDS}))


def _flipped(raw: dict[str, Any]) -> dict[str, Any]:
    return {**raw, "visibility": "public", "published": True}


def _trigger(automation: Any) -> dict[str, Any]:
    return {
        "kind": automation.trigger_kind,
        "interval_seconds": automation.interval_seconds,
        "cron_expr": automation.cron_expr,
        "event_type": automation.event_type,
        "event_filter": dict(automation.event_filter or {}),
    }


def _shown(value: Any, limit: int = 80) -> str:
    """Agent-authored text as it may appear on the tab: one line, bounded.

    Names are the agent's words inside the platform's sentence. A newline would
    let a branch named "...\\n\\nNothing here is shared." read as the platform
    speaking, so every echoed name is flattened and cut, never rendered raw.
    """
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _trigger_words(trigger: dict[str, Any]) -> str:
    if trigger["kind"] == "interval":
        return f"every {trigger['interval_seconds']} seconds"
    if trigger["kind"] == "cron":
        return f"on the schedule {trigger['cron_expr']}"
    if trigger["kind"] == "event":
        return f"when {trigger['event_type']} happens"
    return trigger["kind"]


def tab_text(action: dict[str, Any]) -> tuple[str, str, str]:
    """``(kind, title, body)`` for the tab, written from the pinned action only."""
    shown = action["shown"]
    kind = _publication_kind(action)
    label = {"command_center": "command center", "system": "workflow and screen bundle",
             "workflows": "workflows"}[kind]
    lines = [f"Publication: {label}", f"Public name: {_shown(action['name'], 120)}"]
    if action["description"]:
        lines.append(f"Description: {_shown(action['description'], 400)}")
    lines.append("These become public:")
    for w in shown["workflows"]:
        lines.append(f"- Workflow \"{w['name']}\" ({w['nodes']} steps)")
    if shown["ui"]:
        lines.append(f"- The screen \"{shown['ui']}\"")
    for a in shown["automations"]:
        lines.append(
            f"- The trigger of \"{a['name']}\": runs {a['when']} (its inputs stay private)")
    for agent in shown.get("agent_templates", []):
        lines.append(f"- Public instructions for chat agent \"{_shown(agent['name'])}\" "
                     "(private settings and model assignments stay here)")
    if shown["ui"] and not shown.get("agent_templates"):
        lines.append(
            "No chat agents are included. Publishing this screen does not include the "
            "agents in your command center, even if the screen lists them. Recipients "
            "see only their own agents, not yours. To include yours, "
            "ask your agent to select the agents' public instruction templates in a new "
            "publish request. Workflows and instruction files alone do not create chat agents."
        )
    package = shown.get("package")
    if package:
        lines.extend(_package_lines(package))
    else:
        lines.append("No files are included. This appears in the shared systems catalogue, "
                     "not the command-center package picker; its components copy separately.")
    lines.append("")
    lines.append(PUBLIC_SENTENCE)
    if package:
        lines.append(PACKAGE_SENTENCE)
    if action.get("release_link"):
        from tinyassets.command_center_release_series import consent_text

        lines.extend(["", consent_text(action["release_link"])])
    return ("Publish", f"Publish {label} \"{_shown(action['name'], 120)}\" for anyone to copy?",
            "\n".join(lines))


#: The platform's sentence about what a scrub can and cannot prove (§4.17).
#:
#: It now names what TRAVELS, because the contents rule is an allowlist
#: (``command_center_packages.ROOT_FILES``) and a sentence that lists what was
#: removed can only ever be as complete as the removal list was. The previous
#: wording promised "your brain files and platform state were left out" while
#: ``orgchart.md``, ``requests.json`` and 21 other platform root files
#: travelled. Describing the carried kinds is a claim the code can keep.
#:
#: Two exactness notes kept deliberately: "private" brain files, because
#: ``identity.md`` travels as the published roster agent's own identity; and
#: memory as conditional, because entries the owner named do travel.
PACKAGE_SENTENCE = (
    "Every file listed above becomes public: your agent and skill files, your "
    "roster agents, your published wiki pages, your app, and your own folders. "
    "Your private brain files, your memory unless you named entries, platform "
    "state, and anything else sitting in the top folder stay home. But "
    "detection cannot prove a file holds no personal information: read the "
    "list before you confirm."
)


#: Wider than any package path (``MAX_PATH_CHARS``): a listed path is never cut.
_FULL = 1000


def _package_lines(package: dict[str, Any]) -> list[str]:
    lines = [f"- These {package['file_count']} files of your command center "
             f"({package['size']}, version {package['version']}):"]
    # EVERY path, in full: default-include is acceptable only because the tab
    # lists all of it (lead, 2026-10-01). Paths are checked package paths, so
    # flattening cannot change one; the bound is the path limit, never a cut.
    for path in package["files"]:
        lines.append(f"  - {_shown(path, _FULL)}")
    if package["excluded"]:
        lines.append(f"Left out ({len(package['excluded'])}):")
        for entry in package["excluded"]:
            lines.append(f"  - {_shown(entry['path'], _FULL)}: {entry['reason']}")
    if package.get("flagged"):
        from tinyassets.command_center_packages import review_groups

        lines.append(f"Worth a look before you confirm ({len(package['flagged'])}): these "
                     "are included, but hold something that is often private. Leave any "
                     "out below.")
        for group in review_groups(package["flagged"]):
            more = group["count"] - len(group["shown"])
            tail = f", and {more} more" if more > 0 else ""
            lines.append(f"  - {group['count']} {group['kind']}: "
                         + "; ".join(_shown(s, _FULL) for s in group["shown"]) + tail)
    if package["connections"]:
        lines.append("Whoever installs it connects their own: "
                     + ", ".join(_shown(c, 60) for c in package["connections"]))
    return lines


def build_snapshot(uid: str, action: dict[str, Any]) -> dict[str, Any]:
    """The WHOLE public payload, built from the live rows, as the caller.

    Returns the public branch rows, the exact definition payload, and ``digest``:
    sha256 of the canonical serialization of both. The definition names each
    workflow's version by the id its snapshot will mint, so nothing that becomes
    public is outside the digest. Everything is scanned for credential-shaped
    content with the scanner a definition gets -- every branch row as well as
    the bundle. Raises ``LookupError`` for anything that is not the caller's and
    ``ValueError`` for content that may not be public.
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.automations import AutomationStore
    from tinyassets.branch_versions import _canonical_snapshot, compute_content_hash
    from tinyassets.custom_agents import (
        AGENT_SCHEMA_VERSION,
        AgentValidationError,
        _check_secret_fields,
        _normalize_definition_payload,
        app_ui_workflow_refs,
        get_app_ui,
    )
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    if not actor:
        raise PermissionError("an authenticated owner is required")
    base = Path(_base_path())

    rows: dict[str, dict[str, Any]] = {}
    for bid in action["branch_ids"]:
        try:
            raw = get_branch_definition(base, branch_def_id=bid)
        except (KeyError, FileNotFoundError):
            raise LookupError(f"no branch of yours is {bid}") from None
        if (raw.get("author") or "").strip() != actor:
            # Same words as absent: an ask cannot probe another author's ids.
            raise LookupError(f"no branch of yours is {bid}")
        rows[bid] = raw

    components: dict[str, dict[str, Any]] = {}
    shown: dict[str, Any] = {"workflows": [], "ui": "", "automations": []}
    if action["ui_id"]:
        library = get_app_ui(base, owner_user_id=actor, universe_id=uid).get("ui_library") or []
        match = next((c for c in library if isinstance(c, dict)
                      and c.get("ui_id") == action["ui_id"] and c.get("kind") == UI_KIND), None)
        if match is None:
            raise LookupError(f"no UI of yours is {action['ui_id']}")
        components["ui"] = export_ui_component(match)
        shown["ui"] = _shown(components["ui"]["name"])

    keys: dict[str, str] = {}
    for n, (bid, raw) in enumerate(rows.items(), start=1):
        key = f"workflow-{n}"
        keys[bid] = key
        snapshot = _canonical_snapshot(_flipped(raw))
        content_hash = compute_content_hash(snapshot)
        name = str(raw.get("name") or bid)
        components[key] = {"kind": BRANCH_REF_KIND, "name": name,
                           "published_version_id": f"{bid}@{content_hash[:8]}"}
        shown["workflows"].append({"name": _shown(name),
                                   "nodes": len(snapshot.get("graph_nodes") or [])})

    if "ui" in components and action.get("package") is not None:
        ui = components["ui"]
        refs = app_ui_workflow_refs(ui)
        if any(bid not in keys for bid in refs.values()):
            raise ValueError("workflow_refs must name only workflows selected in this publish ask")
        if action.get("publish_kind") == "command_center" and any(
                bid in ui["script"] for bid in keys):
            raise ValueError(
                "this screen embeds a source workflow id; use whoami().workflow_refs "
                "with an explicit workflow_refs alias so installed copies use their own workflows"
            )
        if "workflow_refs" in ui:
            ui["workflow_refs"] = {alias: keys[bid] for alias, bid in refs.items()}

    store = AutomationStore(base)
    for n, automation_id in enumerate(action["automation_ids"], start=1):
        row = store.get(automation_id)
        if (row is None or row.retired_at or row.universe_id != uid
                or row.owner_principal_id != actor):
            raise LookupError(f"no automation of yours is {automation_id}")
        if row.branch_def_id not in keys:
            raise ValueError(
                f"automation {automation_id} drives a workflow this ask does not "
                "publish; add that workflow or leave the automation out"
            )
        trigger = _trigger(row)
        followed = trigger["event_filter"].get("branch_def_id")
        if followed:
            # The author's branch id means nothing in a copy; name the workflow.
            trigger["event_filter"]["branch_def_id"] = keys.get(followed, "")
        components[f"automation-{n}"] = {
            "kind": AUTOMATION_SPEC_KIND, "name": row.name,
            "workflow": keys[row.branch_def_id], "trigger": trigger, "overlap": row.overlap}
        shown["automations"].append({"name": _shown(row.name),
                                     "when": _shown(_trigger_words(trigger))})

    from tinyassets.command_center_agent_templates import export_templates, reject_nested_workflows
    from tinyassets.custom_agents import app_ui_agent_refs

    selected = action.get("agent_templates") or {}
    templates = export_templates(base, uid, actor, selected)
    if set(templates) & (set(components) | {"package"}):
        raise ValueError("an agent template key collides with another published component")
    components.update(templates)
    if templates:
        shown["agent_templates"] = list(templates.values())
    if "ui" in components:
        ui = components["ui"]
        aliases = {binding: key for key, binding in selected.items()}
        refs = app_ui_agent_refs(ui)
        if any(binding not in aliases for binding in refs.values()):
            raise ValueError("agent_refs must name only agents selected in this publish ask")
        if any(binding in ui["script"] for binding in selected.values()):
            raise ValueError("this screen embeds a source agent id; use declared agent_refs")
        if "agent_refs" in ui:
            ui["agent_refs"] = {alias: aliases[binding] for alias, binding in refs.items()}
    if "ui" in components:
        for row in rows.values():
            reject_nested_workflows(row)

    branches = {bid: _public_branch_row(raw) for bid, raw in rows.items()}
    tags = ["tinyassets.system.v1"]
    package = None
    if action.get("package") is not None:
        package = _package(uid, actor, action, branches, components, shown)
        components["package"] = package["component"]
        tags.append(package["tag"])
    definition = {"schema_version": AGENT_SCHEMA_VERSION, "name": action["name"],
                  "description": action["description"], "tags": tags,
                  "components": components}
    try:
        # One scanner for everything that becomes public (astra round 2, P1: a
        # credential in a prompt_template reached a public version while the
        # same value was refused in bundle text).
        _check_secret_fields({"branches": branches, "definition": definition})
        # The definition's own validation, run NOW, so an accept never makes
        # branches public and then fails on the bundle.
        _normalize_definition_payload(definition)
    except AgentValidationError as exc:
        raise ValueError(f"this cannot be made public: {exc}") from None
    digest = hashlib.sha256(
        _canonical({"branches": branches, "definition": definition}).encode("utf-8")
    ).hexdigest()
    return {"branches": branches, "rows": rows, "definition": definition,
            "digest": digest, "shown": shown, "package": package}


def _package(uid: str, actor: str, action: dict[str, Any], branches: dict[str, Any],
             components: dict[str, dict[str, Any]], shown: dict[str, Any]) -> dict[str, Any]:
    """The package half of a snapshot: the blob, its listing component, the tab.

    The listing component carries the blob's sha256, so the digest the owner
    approves covers every file. The version is allocated here, inside the
    digest: a publish of the same name landing first changes it, and this ask
    then publishes nothing.
    """
    from tinyassets.api.helpers import _base_path, _universe_dir
    from tinyassets.command_center_packages import (
        FORMAT_VERSION,
        N_OPAQUE,
        PACKAGE_KIND,
        PACKAGE_TAG,
        PackageError,
        build_publish_package,
        human,
        next_version,
        scan_public,
    )

    workflows = [{"key": k, "name": c["name"]} for k, c in components.items()
                 if c.get("kind") == BRANCH_REF_KIND]
    automations = [{"key": k, "name": c["name"], "workflow": c["workflow"]}
                   for k, c in components.items() if c.get("kind") == AUTOMATION_SPEC_KIND]
    try:
        built = build_publish_package(
            _universe_dir(uid), name=action["name"], description=action["description"],
            options=action["package"], branch_rows=branches, workflows=workflows,
            ui=str(components.get("ui", {}).get("name", "")), automations=automations)
        manifest = built["manifest"]
        component = {
            "kind": PACKAGE_KIND,
            "format_version": FORMAT_VERSION,
            # Allocated at ask time and pinned in the action, so a retry after
            # the version was recorded still names the same number.
            "version": int(action.get("package_version")
                           or next_version(_base_path(), actor, action["name"])),
            "blob_sha256": built["sha256"],
            "size_bytes": len(built["blob"]),
            "file_count": len(manifest["files"]),
            "agents": manifest["agents"],
            "needs": manifest["needs"],
        }
        # The final-output check: everything that becomes public, paths and
        # names included, through the same detectors as file content.
        notes: list[str] = []
        scan_public({"manifest": {k: v for k, v in manifest.items() if k != "files"},
                     "paths": [f["path"] for f in manifest["files"]],
                     "name": action["name"], "description": action["description"],
                     "components": components, "package": component}, "", notes)
        for bid, row in branches.items():
            # Every workflow string through the shared detectors, one by one:
            # a joined row lets one detection mask another (gpt-6-astra, code r1 #4).
            scan_public(row, f"workflow {_shown(row.get('name') or bid)}", notes)
    except PackageError as exc:
        raise ValueError(str(exc)) from None
    shown["package"] = {
        "file_count": component["file_count"], "size": human(component["size_bytes"]),
        "version": component["version"], "files": [f["path"] for f in manifest["files"]],
        "excluded": built["excluded"],
        "flagged": built["flagged"] + [{"path": where, "note": N_OPAQUE}
                                       for where in dict.fromkeys(notes)],
        "connections": manifest["needs"]["connections"]}
    return {"component": component, "tag": PACKAGE_TAG, "blob": built["blob"],
            "sha256": built["sha256"], "version": component["version"]}


def capture_action(uid: str, action: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the public payload now; pin its digest and what the tab shows."""
    snap = build_snapshot(uid, action)
    captured = {**action, "snapshot_digest": snap["digest"], "shown": snap["shown"]}
    if action.get("release"):
        from tinyassets.command_center_release_series import _normalized, capture_release_link
        from tinyassets.command_center_updates import digest

        link = capture_release_link(universe_id=uid, action=action, **action["release"])
        if link["release_link"]["definition_digest"] != digest(_normalized(snap["definition"])):
            raise ValueError(_CHANGED)
        captured["release_link"] = link["release_link"]
    if snap.get("package"):
        captured["package_version"] = snap["package"]["version"]
    return captured


def after_publish(uid: str, action: dict[str, Any], result: dict[str, Any], *,
                  request_id: str) -> dict[str, Any]:
    """Report release registration separately from an already completed publication."""
    if not action.get("release") or not result.get("published"):
        return {}
    from tinyassets.command_center_release_series import record_release

    try:
        return {"release_registration": "recorded",
                "release": record_release(universe_id=uid, request_id=request_id)}
    except Exception:  # noqa: BLE001 - the publication is already complete
        logger.warning("Published release lineage could not be registered", exc_info=True)
        return {"release_registration": "unavailable",
                "release_registration_detail":
                    "Your publication is public; release history could not be linked. "
                    "It will not receive automatic-update eligibility from this publication."}


#: The owner's per-folder switches on a package tab, and their two answers.
_INCLUDE, _LEAVE_OUT = "Include", "Leave out"
_MAX_TOGGLES = 15
_LEAVE_OUT_FIELD = "leave_out"


def toggle_fields(action: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The tab's exclude switches: one per top-level folder or file of the
    package (the largest first), plus a box for any other path.

    Returns ``(fields, toggles)``; ``toggles`` maps each switch to the path it
    leaves out and rides in the pinned action, so an answer can only ever
    narrow what the owner was shown.
    """
    package = (action.get("shown") or {}).get("package")
    if not package:
        return [], {}
    counts: dict[str, int] = {}
    for path in package["files"]:
        head = path.split("/")[0]
        counts[head] = counts.get(head, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_MAX_TOGGLES]
    fields: list[dict[str, Any]] = []
    toggles: dict[str, str] = {}
    for n, (head, count) in enumerate(sorted(groups), start=1):
        name = f"out_{n}"
        folder = any(p.startswith(head + "/") for p in package["files"])
        label = f"{head}/ ({count} files)" if folder else head
        fields.append({"name": name, "label": _shown(label, 120), "type": "choice",
                       "options": [_INCLUDE, _LEAVE_OUT]})
        toggles[name] = head
    fields.append({"name": _LEAVE_OUT_FIELD, "type": "text",
                   "label": "Leave out anything else (file or folder paths, comma-separated)"})
    return fields, toggles


def _left_out(action: dict[str, Any], values: dict[str, Any]) -> list[str]:
    """The paths the owner switched off on the tab. Only paths the tab listed (or
    folders holding them) count; anything else is refused, never guessed."""
    from tinyassets.command_center_packages import PackageError, check_path

    toggles = action.get("toggles") or {}
    files = (action.get("shown") or {}).get("package", {}).get("files") or []
    chosen = [toggles[k] for k, v in values.items() if k in toggles and v == _LEAVE_OUT]
    typed = str(values.get(_LEAVE_OUT_FIELD) or "")
    for raw in typed.replace("\n", ",").split(","):
        path = raw.strip().rstrip("/")
        if not path:
            continue
        try:
            check_path(path)
        except PackageError as exc:
            raise ValueError(f"cannot leave out {path!r}: {exc}") from None
        if not any(f == path or f.startswith(path + "/") for f in files):
            raise ValueError(f"{path!r} is not in this package, so nothing was published; "
                             "check the path and confirm again")
        chosen.append(path)
    return sorted(set(chosen))


def answer_publish(uid: str, pinned: dict[str, Any], values: dict[str, Any], *,
                   request_id: str) -> dict[str, Any]:
    """The owner's confirm of a pinned publish, with any switches they turned off.

    Claimed once: a second confirm while one runs is refused, and a confirm of
    an already-published request returns its receipt.
    """
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import PackageError, claim, finish, unclaim

    action = pinned["record"]["action"]
    unknown = set(values) - set(action.get("toggles") or {}) - {_LEAVE_OUT_FIELD}
    if unknown:
        raise ValueError("this tab has no field " + ", ".join(sorted(unknown)))
    extra = _left_out(action, values) if action.get("package") else []
    base = _base_path()
    try:
        state, token = claim(base, universe_id=uid, pin_id=pinned["pin_id"])
    except PackageError as exc:
        raise ValueError(str(exc)) from None
    if state == "activated":
        return {**pinned["progress"], "already_published": True}
    try:
        receipt = execute_action(uid, action, request_id=request_id, leave_out=extra)
    except BaseException:
        unclaim(base, universe_id=uid, pin_id=pinned["pin_id"], token=token)
        raise
    try:
        finish(base, universe_id=uid, pin_id=pinned["pin_id"], progress=receipt, token=token)
    except PackageError as exc:
        raise ValueError(str(exc)) from None
    return receipt


def _store_package(actor: str, package: dict[str, Any], name: str) -> None:
    """Charge, store and list the package's blob, before anything goes public.

    Charged to the publisher's ``packages`` store unless they already own these
    exact bytes (a retry, or a republish of identical content). Over the quota,
    the refusal names the package's size, and nothing was written.
    """
    from tinyassets import storage_accounting
    from tinyassets.api.helpers import _base_path
    from tinyassets.command_center_packages import (
        PackageError,
        blob_owned,
        human,
        record_version,
        store_blob,
    )

    base = _base_path()
    blob, sha = package["blob"], package["sha256"]
    try:
        if blob_owned(base, actor, sha):
            store_blob(base, author_id=actor, blob=blob)
        else:
            account = storage_accounting.account_for_actor(base, actor)
            with storage_accounting.charged(base, account_id=account, store="packages",
                                            nbytes=len(blob)):
                store_blob(base, author_id=actor, blob=blob)
        record_version(base, author_id=actor, name=name, version=package["version"],
                       sha256=sha)
    except storage_accounting.StorageRefused as refused:
        detail = storage_accounting.visible_record(refused).get("error", "")
        raise ValueError(f"This package is {human(len(blob))}, more than your storage "
                         f"has room for, so nothing was published. {detail}") from None
    except PackageError as exc:
        raise ValueError(str(exc)) from None


def _flip_if_unchanged(snap: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """The commit point. One write transaction re-reads every branch, refuses
    unless each still equals the snapshot, and flips them all public.

    Returns each branch's raw (visibility, published) as it stood before the
    flip, so a failed publish restores exactly that -- never withdrawing a
    publication that predates this request."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import _branch_def_from_row, _connect

    prior: dict[str, tuple[Any, Any]] = {}
    with _connect(_base_path()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for bid, public_row in snap["branches"].items():
            row = conn.execute(
                "SELECT * FROM branch_definitions WHERE branch_def_id = ?", (bid,),
            ).fetchone()
            if row is None or _public_branch_row(_branch_def_from_row(row)) != public_row:
                # Raising inside the transaction rolls every flip back.
                raise ValueError(_CHANGED)
            prior[bid] = (row["visibility"], row["published"])
            conn.execute(
                "UPDATE branch_definitions SET visibility = 'public', published = 1 "
                "WHERE branch_def_id = ?", (bid,),
            )
    return prior


def _unflip(prior: dict[str, tuple[Any, Any]]) -> None:
    """Undo this request's flip only: each branch goes back to what it was.

    Only while the row still shows the flip. If the owner made the branch
    private meanwhile, that later choice stands; restoring an earlier "public"
    over it would re-expose what they just withdrew."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import _connect

    with _connect(_base_path()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for bid, (visibility, published) in prior.items():
            conn.execute(
                "UPDATE branch_definitions SET visibility = ?, published = ? "
                "WHERE branch_def_id = ? AND visibility = 'public'",
                (visibility, published, bid),
            )


def execute_action(uid: str, action: dict[str, Any], *, request_id: str,
                   leave_out: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Publish exactly the approved snapshot, or nothing. Raises to leave the ask pending.

    Order is what makes it all-or-nothing across three stores (branch rows,
    versions and definitions are separate SQLite files, and a WAL transaction is
    not atomic across attached files):

    1. Rebuild the snapshot from the live rows; its canonical digest must equal
       the approved one. Every content check, the definition's included, runs
       here -- before anything is written.
    2. Mint each version from its row AS IT WILL BE FLIPPED, UNMARKED. An
       unmarked version is readable only by its author, so this exposes nothing
       even if the branch goes public some other way later.
    3. The commit point: one transaction re-checks every row against the
       snapshot and flips them all public, or flips none.
    4. Mark exactly those versions published -- never the branch's history --
       then publish the pre-validated definition. A failure here un-marks the
       versions THIS request marked and restores each branch's prior
       visibility, so nothing this request exposed is left public and nothing
       published before it is withdrawn.
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    snap = build_snapshot(uid, action)
    if snap["digest"] != action.get("snapshot_digest"):
        raise ValueError(_CHANGED)
    if leave_out and snap.get("package"):
        # The owner switched some of it off. Narrowed from THIS verified
        # snapshot, never rebuilt from the live folder: what remains is
        # byte-for-byte what they were shown (gpt-6-astra, code r2 #1).
        snap = _narrowed(snap, list(leave_out))

    package = snap.get("package")
    if package:
        # First, before any version is minted: a package refused by quota
        # publishes nothing, and a blob stored but never listed stays charged.
        _store_package(actor, package, action["name"])
    try:
        return _publish_snapshot(actor, action, snap, request_id=request_id)
    except BaseException:
        if package:
            # Unlisted, and charged until a measurement drops it; a retry
            # re-records the same pinned version.
            from tinyassets.command_center_packages import drop_version

            drop_version(_base_path(), author_id=actor, name=action["name"],
                         version=package["version"])
        raise


def _narrowed(snap: dict[str, Any], leave_out: list[str]) -> dict[str, Any]:
    from tinyassets.command_center_packages import PackageError, narrow_package

    package = snap["package"]
    try:
        narrowed = narrow_package(package["blob"], leave_out)
    except PackageError as exc:
        raise ValueError(str(exc)) from None
    manifest = narrowed["manifest"]
    component = {**package["component"], "blob_sha256": narrowed["sha256"],
                 "size_bytes": len(narrowed["blob"]), "file_count": len(manifest["files"]),
                 "agents": manifest["agents"], "needs": manifest["needs"]}
    definition = {**snap["definition"],
                  "components": {**snap["definition"]["components"], "package": component}}
    return {**snap, "definition": definition,
            "package": {**package, "component": component, "blob": narrowed["blob"],
                        "sha256": narrowed["sha256"]}}


def _publish_snapshot(actor: str, action: dict[str, Any], snap: dict[str, Any], *,
                      request_id: str) -> dict[str, Any]:
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.api.helpers import _base_path
    from tinyassets.branch_versions import (
        branch_version_is_public,
        mark_versions_public,
        publish_branch_version,
    )

    package = snap.get("package")
    expected = {c["published_version_id"] for c in snap["definition"]["components"].values()
                if c.get("kind") == BRANCH_REF_KIND}
    versions: dict[str, str] = {}
    for bid, raw in snap["rows"].items():
        version = publish_branch_version(
            _base_path(), _flipped(raw), publisher=actor, notes=action["name"])
        versions[bid] = version.branch_version_id
    if set(versions.values()) != expected:
        raise ValueError("a version did not mint as the snapshot named it; nothing was published")

    # A mint can dedupe onto a version published before this request; only the
    # ones this request marks are this request's to take back.
    newly_marked = [v for v in versions.values()
                    if not branch_version_is_public(_base_path(), v)]
    prior = _flip_if_unchanged(snap)
    try:
        mark_versions_public(_base_path(), newly_marked)
        result = custom_agents(
            action="publish_agent", payload=json.dumps(snap["definition"]),
            idempotency_key=f"publish-request:{request_id}",
        )
        agent = result.get("agent") if isinstance(result, dict) else None
        if not agent or result.get("error"):
            detail = result.get("detail") or result.get("error")
            raise ValueError(f"the bundle was not published ({detail}); nothing was left public")
    except BaseException:
        mark_versions_public(_base_path(), newly_marked, public=False)
        _unflip(prior)
        raise
    from tinyassets.publication_completion import completion_for

    receipt = {"published": True, "agent_definition_id": agent["agent_definition_id"],
               "completion": completion_for(agent, action=action),
               "branch_versions": versions, "publication_kind": _publication_kind(action),
               "catalogue": "packages" if package else "agents"}
    if package:
        from tinyassets.command_center_packages import set_version_definition

        set_version_definition(_base_path(), author_id=actor, name=action["name"],
                               version=package["version"],
                               definition_id=agent["agent_definition_id"])
        receipt["package"] = {"version": package["version"],
                              "size_bytes": len(package["blob"]),
                              "file_count": package["component"]["file_count"]}
    return receipt


__all__ = [
    "AUTOMATION_SPEC_KIND",
    "BRANCH_REF_KIND",
    "build_snapshot",
    "capture_action",
    "answer_publish",
    "execute_action",
    "tab_text",
    "toggle_fields",
    "validate_action",
]
