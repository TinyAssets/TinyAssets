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

What is never published: automation ``inputs``, conversations, files,
credentials. A copy of anything published runs as whoever installs it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

BRANCH_REF_KIND = "tinyassets.branch-ref.v1"
AUTOMATION_SPEC_KIND = "tinyassets.automation-spec.v1"
UI_KIND = "tinyassets.app-ui.v1"

_MAX_NAME = 120
_MAX_DESCRIPTION = 4000
_MAX_ID = 200

#: The fixed consent sentence. The platform's words, not the agent's.
PUBLIC_SENTENCE = (
    "Anyone will be able to read and copy these. A copy runs in the copier's own "
    "universe on their own compute and never reaches yours. Publishing does not "
    "share your conversations, files, credentials or automation inputs."
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
    return {
        "type": "publish",
        "name": name.strip(),
        "description": description.strip(),
        "branch_ids": _ids(action.get("branch_ids"), "branch_ids", required=True),
        "ui_id": ui_id.strip(),
        "automation_ids": _ids(action.get("automation_ids"), "automation_ids", required=False),
    }


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
UI_PORTABLE_OPTIONAL_FIELDS = ("libraries", "script_type")

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
    lines = [f"Public name: {_shown(action['name'], 120)}"]
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
    lines.append("")
    lines.append(PUBLIC_SENTENCE)
    return ("Publish", f"Publish \"{_shown(action['name'], 120)}\" for anyone to copy?",
            "\n".join(lines))


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
        content_hash = compute_content_hash(_canonical_snapshot(_flipped(raw)))
        name = str(raw.get("name") or bid)
        components[key] = {"kind": BRANCH_REF_KIND, "name": name,
                           "published_version_id": f"{bid}@{content_hash[:8]}"}
        shown["workflows"].append({"name": _shown(name),
                                   "nodes": len(raw.get("graph_nodes") or [])})

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

    definition = {"schema_version": AGENT_SCHEMA_VERSION, "name": action["name"],
                  "description": action["description"], "tags": ["tinyassets.system.v1"],
                  "components": components}
    branches = {bid: _public_branch_row(raw) for bid, raw in rows.items()}
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
            "digest": digest, "shown": shown}


def capture_action(uid: str, action: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the public payload now; pin its digest and what the tab shows."""
    snap = build_snapshot(uid, action)
    return {**action, "snapshot_digest": snap["digest"], "shown": snap["shown"]}


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


def execute_action(uid: str, action: dict[str, Any], *, request_id: str) -> dict[str, Any]:
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
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.api.helpers import _base_path
    from tinyassets.branch_versions import (
        branch_version_is_public,
        mark_versions_public,
        publish_branch_version,
    )
    from tinyassets.principals import named_principal

    actor = named_principal(permissions.current_actor_id())
    snap = build_snapshot(uid, action)
    if snap["digest"] != action.get("snapshot_digest"):
        raise ValueError(_CHANGED)

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
    return {"published": True, "agent_definition_id": agent["agent_definition_id"],
            "branch_versions": versions}


__all__ = [
    "AUTOMATION_SPEC_KIND",
    "BRANCH_REF_KIND",
    "build_snapshot",
    "capture_action",
    "execute_action",
    "tab_text",
    "validate_action",
]
