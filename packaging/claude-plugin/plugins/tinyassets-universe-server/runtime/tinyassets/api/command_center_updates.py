"""Explicit owner-consented UI replacements for verified component-system copies.

New hook/API functions only: route registration is owned by the integration lane.
This first executor never updates workflows, agents, automations or files. A
user-selected same-author replacement is not a publisher release lineage and
never makes an adoption eligible for automatic updates.
"""

from __future__ import annotations

import sqlite3
import time
import uuid

from tinyassets import command_center_update_registry as registry
from tinyassets.api.helpers import _base_path
from tinyassets.command_center_updates import digest, legacy_provenance
from tinyassets.custom_agents import (
    _app_ui_document,
    _canonical_json,
    _check_app_ui_fields,
    _check_assets_held,
    app_ui_renderability,
    get_definition,
)


def _scope(universe_id):
    from tinyassets.api.custom_agents import _authenticated_actor
    from tinyassets.api.pending_requests import _owner_gate

    uid, _, denial = _owner_gate(universe_id)
    if denial:
        raise PermissionError("command center not found")
    return _base_path(), _authenticated_actor(), uid


def _pin(base, uid, request_id, *, readers=None):
    from tinyassets.command_center_packages import pin_for_request

    pin = (readers.pin(uid, request_id) if readers is not None
           else pin_for_request(base, universe_id=uid, request_id=request_id))
    if pin is None:
        raise LookupError("installation not found")
    plan = pin.get("record", {}).get("action", {}).get("plan", {})
    definition = (readers.definition(plan.get("definition_id", "")) if readers is not None
                  else get_definition(base, plan.get("definition_id", "")))
    if definition is None:
        raise ValueError("immutable source definition is missing")
    legacy_provenance(pin=pin, definition=definition)
    return pin, plan


def _mapped_ui(plan, progress):
    ui = {**plan["ui"], "ui_id": progress["ui"]}
    for field, group in (("workflow_refs", "workflows"), ("agent_refs", "agents")):
        if field in ui:
            ui[field] = {alias: progress[group][key] for alias, key in ui[field].items()}
    if app_ui_renderability(ui):
        raise ValueError("replacement UI is not renderable")
    _check_app_ui_fields({"ui_library": [ui]})
    return ui


def _ui_row(conn, owner, uid):
    row = conn.execute(
        "SELECT * FROM universe_app_ui WHERE owner_user_id=? AND universe_id=?", (owner, uid)
    ).fetchone()
    return _app_ui_document(row, uid)


def _find_ui(document, ui_id):
    return next((ui for ui in document["ui_library"] if ui.get("ui_id") == ui_id), None)


def _dependencies(conn, owner, uid, source_plan, progress):
    """Fence recipient targets used by this UI, including private edits/policy.

    No dependency body is returned to the browser. Runtime statistics and clocks
    do not affect behavior; all definition and binding configuration fields do.
    These are current read-set hashes, not claims of an unedited source baseline.
    """
    result = {}
    for item in source_plan["workflows"]:
        target = progress["workflows"][item["key"]]
        row = conn.execute(
            "SELECT * FROM branch_definitions WHERE branch_def_id=?", (target,)
        ).fetchone()
        if row is None or row["author"] != owner or row["fork_from"] != item["version_id"]:
            raise ValueError(
                "an installed workflow dependency is missing or has different provenance"
            )
        result["workflow:" + item["key"]] = digest(
            {k: row[k] for k in row.keys() if k not in {"stats_json", "created_at", "updated_at"}}
        )
    for item in source_plan.get("agent_templates", []):
        target = progress["agents"][item["key"]]
        row = conn.execute(
            "SELECT * FROM agent_bindings WHERE agent_binding_id=? AND universe_id=?", (target, uid)
        ).fetchone()
        if (
            row is None
            or row["created_by"] != owner
            or row["agent_definition_id"] != item["agent_definition_id"]
        ):
            raise ValueError("an installed agent dependency is missing or has different provenance")
        result["agent:" + item["key"]] = digest(
            {k: row[k] for k in row.keys() if k not in {"created_at", "updated_at"}}
        )
    if source_plan["automations"]:
        attached = {row[1] for row in conn.execute("PRAGMA database_list")}
        if "retained_automations" not in attached:
            raise ValueError("installed automation store is missing")
        # Actual recipient configuration, not merely the install progress IDs.
        # Clock/run-result counters are observational; authority and scheduling
        # fields (including desired state, revision and retirement) are fenced.
        runtime_fields = {
            "created_at",
            "updated_at",
            "last_due_at",
            "last_due_local",
            "last_run_id",
            "last_reason",
            "last_finished_at",
            "consecutive_failures",
        }
        for item in source_plan["automations"]:
            target = progress["automations"][item["key"]]
            try:
                row = conn.execute(
                    "SELECT * FROM retained_automations.automations WHERE automation_id=?",
                    (target,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                raise ValueError("installed automation store schema is unavailable") from exc
            if (
                row is None
                or row["owner_principal_id"] != owner
                or row["universe_id"] != uid
                or row["retired_at"]
                or row["branch_def_id"] != progress["workflows"][item["workflow"]]
            ):
                raise ValueError(
                    "an installed automation dependency is missing, retired or changed"
                )
            result["automation:" + item["key"]] = digest(
                {key: row[key] for key in row.keys() if key not in runtime_fields}
            )
    return result


def record_install(*, universe_id: str, request_id: str) -> dict:
    """Idempotent post-activation hook; refuses to bless a preexisting private edit."""
    base, owner, uid = _scope(universe_id)
    pin, plan = _pin(base, uid, request_id)
    expected = _mapped_ui(plan, pin["progress"])
    adoption_id = digest({"owner": owner, "universe": uid, "pin": pin["pin_id"]})
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        existing = conn.execute(
            "SELECT 1 FROM command_center_adoptions "
            "WHERE owner_id=? AND universe_id=? AND adoption_id=?",
            (owner, uid, adoption_id),
        ).fetchone()
        if not existing:
            current = _find_ui(_ui_row(conn, owner, uid), expected["ui_id"])
            if current is None or digest(current) != digest(expected):
                raise ValueError(
                    "installed UI was edited or deleted; explicit conflict resolution required"
                )
            _dependencies(conn, owner, uid, plan, pin["progress"])
            conn.execute(
                """INSERT INTO command_center_adoptions
                (owner_id,universe_id,adoption_id,install_request_id,original_definition_id,
                 ui_definition_id,ui_id,baseline_hash,revision) VALUES (?,?,?,?,?,?,?,?,1)""",
                (
                    owner,
                    uid,
                    adoption_id,
                    request_id,
                    plan["definition_id"],
                    plan["definition_id"],
                    expected["ui_id"],
                    digest(expected),
                ),
            )
        return _inspect(conn, owner, uid, adoption_id)


def _inspect(conn, owner, uid, adoption_id):
    row = registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
    current = _find_ui(_ui_row(conn, owner, uid), row["ui_id"])
    return {
        "adoption_id": adoption_id,
        "ui_id": row["ui_id"],
        "revision": row["revision"],
        "ui_definition_id": row["ui_definition_id"],
        "retained_definition_id": row["original_definition_id"],
        "lineage": "exact-install-and-user-selected-replacements",
        "automatic_updates": False,
        "private_edit": current is None or digest(current) != row["baseline_hash"],
    }


def inspect_adoptions(*, universe_id: str) -> dict:
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT adoption_id FROM command_center_adoptions "
                "WHERE owner_id=? AND universe_id=?",
                (owner, uid),
            )
        ]
        return {"adoptions": [_inspect(conn, owner, uid, key) for key in ids]}


def _prepare(conn, base, owner, uid, adoption_id, definition_id, *, readers=None):
    from tinyassets.api.system_copy_requests import _plan

    installed = registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
    pin, original = _pin(base, uid, installed["install_request_id"], readers=readers)
    proposed = _plan({"agent_definition_id": definition_id}, readers=readers)
    if proposed["author"] != original["author"]:
        raise ValueError("replacement must name an exact public definition by the same author")
    # Whole-system dependency changes cannot be disguised as a safe UI-only patch.
    for field in ("workflows", "automations", "agent_templates"):
        if proposed.get(field, []) != original.get(field, []):
            raise ValueError(
                f"replacement changes {field}; this manual adapter updates existing UI only"
            )
    for field in ("workflow_refs", "agent_refs"):
        if proposed["ui"].get(field, {}) != original["ui"].get(field, {}):
            raise ValueError(
                "replacement changes UI dependency bindings; explicit dependency update required"
            )
    document = _ui_row(conn, owner, uid)
    current = _find_ui(document, installed["ui_id"])
    if current is None or digest(current) != installed["baseline_hash"]:
        raise ValueError(
            "installed UI was edited or deleted; explicit conflict resolution required"
        )
    replacement = _mapped_ui(proposed, pin["progress"])
    if replacement.get("assets"):
        raise ValueError("UI assets require a separately supported file update")
    _check_assets_held(conn, owner, [replacement])
    readset = _dependencies(conn, owner, uid, original, pin["progress"])
    plan = {
        "adoption_id": adoption_id,
        "adoption_revision": installed["revision"],
        "definition_id": definition_id,
        "source_digest": proposed["digest"],
        "definition_name": proposed["name"],
        "author": proposed["author"],
        "ui_id": installed["ui_id"],
        "current_hash": digest(current),
        "replacement_hash": digest(replacement),
        "library_revision": document["revision"],
        "library_hash": digest(document["ui_library"]),
        "dependencies": readset,
        "retained_definition_id": original["definition_id"],
        "automatic_updates": False,
        "selection": ["ui"],
        "link_kind": "user-selected-replacement",
        "retained_components": {
            field: [item["key"] for item in original.get(field, [])]
            for field in ("workflows", "automations", "agent_templates")
        },
        "permission_decision": "Replace screen code only; no new grants or automatic execution.",
    }
    return installed, document, replacement, plan


def preview_update(
    *, universe_id: str, adoption_id: str, definition_id: str, selected: tuple[str, ...] = ("ui",)
) -> dict:
    """Prepare one owner-visible consent; no UI/component writes or inference."""
    if tuple(selected) != ("ui",):
        raise ValueError("this adapter supports selecting the existing UI only")
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        _, _, _, plan = _prepare(conn, base, owner, uid, adoption_id, definition_id)
        request_id, plan_digest = uuid.uuid4().hex, digest(plan)
        conn.execute(
            """INSERT INTO command_center_update_requests
            (owner_id,universe_id,adoption_id,request_id,plan_digest,plan_json) VALUES (?,?,?,?,?,?)
            ON CONFLICT(owner_id,universe_id,adoption_id) DO UPDATE SET
            request_id=excluded.request_id,plan_digest=excluded.plan_digest,plan_json=excluded.plan_json""",
            (owner, uid, adoption_id, request_id, plan_digest, _canonical_json(plan)),
        )
        return {
            "request_id": request_id,
            "plan_digest": plan_digest,
            "plan": plan,
            "requires_explicit_consent": True,
            "applied": False,
        }


def commit_update(*, universe_id: str, request_id: str, plan_digest: str, decision: str) -> dict:
    """One atomic UI/baseline/receipt write; no partial-component success state."""
    if decision not in {"accepted", "declined"}:
        raise ValueError("an explicit accepted or declined decision is required")
    base, owner, uid = _scope(universe_id)
    from tinyassets import storage_accounting

    reservation = None
    try:
        # Reserve outside the registry write lock; storage accounting uses the
        # same account lock/store. Recompute and fence ALL state inside below.
        with registry.connect(base) as conn:
            registry.require_owner(conn, owner=owner, uid=uid)
            old = conn.execute(
                """SELECT adoption_id FROM command_center_adoptions
                WHERE owner_id=? AND universe_id=? AND last_request_id=? AND last_plan_digest=?""",
                (owner, uid, request_id, plan_digest),
            ).fetchone()
            if old:
                return {
                    "applied": True,
                    "already_applied": True,
                    "adoption": _inspect(conn, owner, uid, old[0]),
                }
            pending = registry.pending(conn, owner=owner, uid=uid, request_id=request_id)
            if pending["plan_digest"] != plan_digest:
                raise ValueError("update consent digest mismatch")
            if decision == "declined":
                conn.execute(
                    "DELETE FROM command_center_update_requests WHERE request_id=?", (request_id,)
                )
                return {"applied": False, "decision": "declined"}
            _, document, replacement, fresh = _prepare(
                conn, base, owner, uid, pending["adoption_id"], pending["plan"]["definition_id"]
            )
            if digest(fresh) != plan_digest:
                raise ValueError("update changed since preview; inspect it again")
            library = [
                replacement if item["ui_id"] == fresh["ui_id"] else item
                for item in document["ui_library"]
            ]
            library_json = _canonical_json(library)
        account = owner if storage_accounting.is_account(base, owner) else None
        reservation = storage_accounting.reserve(
            base,
            account_id=account,
            scope_id=account or "",
            store="ui_library",
            nbytes=len(library_json.encode()),
        )
        with registry.connect(base) as conn:
            registry.require_owner(conn, owner=owner, uid=uid)
            pending = registry.pending(conn, owner=owner, uid=uid, request_id=request_id)
            installed, document, replacement, fresh = _prepare(
                conn, base, owner, uid, pending["adoption_id"], pending["plan"]["definition_id"]
            )
            if pending["plan_digest"] != plan_digest or digest(fresh) != plan_digest:
                raise ValueError("update changed since preview; inspect it again")
            library = [
                replacement if item["ui_id"] == fresh["ui_id"] else item
                for item in document["ui_library"]
            ]
            _check_app_ui_fields({"ui_library": library})
            _check_assets_held(conn, owner, library)
            written = conn.execute(
                """UPDATE universe_app_ui SET ui_library_json=?,revision=revision+1,updated_at=?
                WHERE owner_user_id=? AND universe_id=? AND revision=?""",
                (_canonical_json(library), time.time(), owner, uid, document["revision"]),
            ).rowcount
            if written != 1:
                raise ValueError("recipient UI revision changed")
            written = conn.execute(
                """UPDATE command_center_adoptions SET ui_definition_id=?,baseline_hash=?,
                revision=revision+1,last_request_id=?,last_plan_digest=?
                WHERE owner_id=? AND universe_id=? AND adoption_id=? AND revision=?""",
                (
                    fresh["definition_id"],
                    fresh["replacement_hash"],
                    request_id,
                    plan_digest,
                    owner,
                    uid,
                    installed["adoption_id"],
                    installed["revision"],
                ),
            ).rowcount
            if written != 1:
                raise ValueError("recipient adoption revision changed")
            conn.execute(
                "DELETE FROM command_center_update_requests WHERE request_id=?", (request_id,)
            )
            result = {
                "applied": True,
                "already_applied": False,
                "adoption": _inspect(conn, owner, uid, installed["adoption_id"]),
            }
    except BaseException:
        if reservation is not None:
            storage_accounting.release(reservation)
        raise
    storage_accounting.commit(reservation)
    return result
