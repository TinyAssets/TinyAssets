"""Explicit per-adoption presentation-update opt-in and non-executing eligibility.

No scheduler, actor impersonation, UI mutation, provider calls or automatic
consent. Root must render and bind these policy requests in trusted owner UI.
An eligibility result is a read snapshot, never authority to call an executor.
"""

from __future__ import annotations

import json
import re
import uuid

from tinyassets import command_center_release_series as series
from tinyassets import command_center_update_registry as registry
from tinyassets.api.command_center_updates import _pin, _prepare, _scope
from tinyassets.command_center_updates import digest
from tinyassets.custom_agents import get_definition

POLICY = "presentation-updates-v1"
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS command_center_update_policies (
        owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, adoption_id TEXT NOT NULL,
        revision INTEGER NOT NULL, enabled INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
        last_request_id TEXT NOT NULL, last_digest TEXT NOT NULL,
        PRIMARY KEY(owner_id,universe_id,adoption_id))""",
    """CREATE TABLE IF NOT EXISTS command_center_policy_requests (
        owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, adoption_id TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE, snapshot_json TEXT NOT NULL, plan_digest TEXT NOT NULL,
        PRIMARY KEY(owner_id,universe_id,adoption_id))""",
)


def _schema(conn):
    series.ensure_schema(conn)
    for statement in _SCHEMA:
        conn.execute(statement)


def _policy(conn, owner, uid, adoption_id):
    row = conn.execute(
        "SELECT * FROM command_center_update_policies "
        "WHERE owner_id=? AND universe_id=? AND adoption_id=?",
        (owner, uid, adoption_id),
    ).fetchone()
    return dict(row) if row else None


def _link_snapshot(conn, base, owner, uid, adoption_id, series_id, release_id, *, readers=None):
    chain = series._chain(conn, series_id)
    releases = {item["release_id"]: item for item in chain}
    release = releases.get(release_id)
    if release is None:
        raise ValueError("release is not in the selected verified series")
    adoption = registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
    if adoption["ui_definition_id"] != release["definition_id"]:
        raise ValueError("installed UI has not adopted this exact published release")
    pin, original = _pin(base, uid, adoption["install_request_id"], readers=readers)
    read_definition = (
        readers.definition if readers is not None else lambda key: get_definition(base, key)
    )
    definition = read_definition(release["definition_id"])
    if definition is None or digest(series._normalized(definition)) != release["definition_digest"]:
        raise ValueError("registered source definition no longer matches its immutable release")
    original_definition = read_definition(original["definition_id"])
    if definition["author_id"] != original["author"]:
        raise ValueError("installed source author does not match the selected series")
    original_components = original_definition["components"]
    components = definition["components"]
    if set(original_components) != set(components) or any(
        digest(value) != digest(components[key])
        for key, value in original_components.items()
        if value["kind"] != "tinyassets.app-ui.v1"
    ):
        raise ValueError("retained installed components do not match this release")
    _, _, _, readset = _prepare(
        conn, base, owner, uid, adoption_id, release["definition_id"], readers=readers
    )
    return {
        "series_id": series_id,
        "release_id": release_id,
        "author_id": release["author_id"],
        "definition_id": release["definition_id"],
        "definition_digest": release["definition_digest"],
        "adoption_revision": adoption["revision"],
        "baseline_hash": adoption["baseline_hash"],
        "ui_id": adoption["ui_id"],
        "dependencies": readset["dependencies"],
        "source_plan_digest": pin["digest"],
    }


def _snapshot(conn, base, owner, uid, adoption_id, *, enabled, series_id, release_id):
    adoption = registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
    current = _policy(conn, owner, uid, adoption_id)
    snapshot = {
        "owner_id": owner,
        "universe_id": uid,
        "adoption_id": adoption_id,
        "policy": POLICY,
        "enabled": enabled,
        "policy_revision": current["revision"] if current else 0,
        "adoption_revision": adoption["revision"],
        "explanation": "Automatically accept eligible name/style presentation updates only; "
        "code, new permissions, components and conflicts require a decision.",
    }
    if enabled:
        snapshot["link"] = _link_snapshot(
            conn, base, owner, uid, adoption_id, series_id, release_id
        )
    return snapshot


def preview_policy(*, universe_id, adoption_id, enabled, series_id="", release_id=""):
    if type(enabled) is not bool:
        raise ValueError("presentation update policy requires an explicit boolean choice")
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        _schema(conn)
        snapshot = _snapshot(
            conn,
            base,
            owner,
            uid,
            adoption_id,
            enabled=enabled,
            series_id=series_id,
            release_id=release_id,
        )
        request_id, plan_digest = uuid.uuid4().hex, digest(snapshot)
        conn.execute(
            """INSERT INTO command_center_policy_requests VALUES (?,?,?,?,?,?)
            ON CONFLICT(owner_id,universe_id,adoption_id) DO UPDATE SET
            request_id=excluded.request_id,snapshot_json=excluded.snapshot_json,plan_digest=excluded.plan_digest""",
            (
                owner,
                uid,
                adoption_id,
                request_id,
                json.dumps(snapshot, sort_keys=True),
                plan_digest,
            ),
        )
        return {
            "request_id": request_id,
            "plan_digest": plan_digest,
            "policy": snapshot,
            "requires_explicit_consent": True,
        }


def commit_policy(*, universe_id, request_id, plan_digest, decision):
    if decision not in {"accepted", "declined"}:
        raise ValueError("explicit accepted or declined decision required")
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        _schema(conn)
        old = conn.execute(
            "SELECT adoption_id FROM command_center_update_policies "
            "WHERE owner_id=? AND universe_id=? AND last_request_id=? AND last_digest=?",
            (owner, uid, request_id, plan_digest),
        ).fetchone()
        if old:
            return {**_describe(_policy(conn, owner, uid, old[0])), "already_applied": True}
        row = conn.execute(
            "SELECT * FROM command_center_policy_requests "
            "WHERE owner_id=? AND universe_id=? AND request_id=?",
            (owner, uid, request_id),
        ).fetchone()
        if row is None:
            raise LookupError("policy request not found")
        if row["plan_digest"] != plan_digest:
            raise ValueError("policy consent digest mismatch")
        snapshot = json.loads(row["snapshot_json"])
        if decision == "declined":
            conn.execute(
                "DELETE FROM command_center_policy_requests WHERE request_id=?", (request_id,)
            )
            return {"changed": False, "decision": "declined"}
        link = snapshot.get("link", {})
        fresh = _snapshot(
            conn,
            base,
            owner,
            uid,
            row["adoption_id"],
            enabled=snapshot["enabled"],
            series_id=link.get("series_id", ""),
            release_id=link.get("release_id", ""),
        )
        if digest(fresh) != plan_digest:
            raise ValueError("policy or adoption changed since preview")
        conn.execute(
            """INSERT INTO command_center_update_policies VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(owner_id,universe_id,adoption_id) DO UPDATE SET revision=excluded.revision,
            enabled=excluded.enabled,snapshot_json=excluded.snapshot_json,
            last_request_id=excluded.last_request_id,last_digest=excluded.last_digest""",
            (
                owner,
                uid,
                row["adoption_id"],
                snapshot["policy_revision"] + 1,
                int(snapshot["enabled"]),
                json.dumps(snapshot, sort_keys=True),
                request_id,
                plan_digest,
            ),
        )
        conn.execute("DELETE FROM command_center_policy_requests WHERE request_id=?", (request_id,))
        return {
            **_describe(_policy(conn, owner, uid, row["adoption_id"])),
            "already_applied": False,
        }


def _describe(row):
    if not row:
        return {"enabled": False, "policy": POLICY, "revision": 0, "series_id": ""}
    snapshot = json.loads(row["snapshot_json"])
    return {
        "enabled": bool(row["enabled"]),
        "policy": POLICY,
        "revision": row["revision"],
        "series_id": snapshot.get("link", {}).get("series_id", ""),
        "installed_release_id": snapshot.get("progress", snapshot.get("link", {})).get(
            "release_id", ""
        ),
    }


def inspect_policy(*, universe_id, adoption_id):
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        _schema(conn)
        registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
        return _describe(_policy(conn, owner, uid, adoption_id))


def presentation_decisions(before, after):
    """Conservative product filter, not a CSS security parser; CSP remains enforced."""
    old, new = before["components"], after["components"]
    if set(old) != set(new):
        return ["components_added_or_removed"]
    reasons = []
    for key, component in old.items():
        if component["kind"] != "tinyassets.app-ui.v1":
            if component != new[key]:
                reasons.append("retained_component_changed:" + key)
            continue
        incoming = new[key]
        if {k: v for k, v in component.items() if k not in {"name", "style"}} != {
            k: v for k, v in incoming.items() if k not in {"name", "style"}
        }:
            reasons.append("executable_ui_or_capability_changed")
        if component.get("style", "") != incoming.get("style", ""):
            css = incoming.get("style", "")
            # Escapes, markup terminators, at-rules and resource functions need
            # review. This conservative filter grants no network permissions.
            if any(token in css for token in ("\\", "/*", "*/", "<", ">", "@")) or re.search(
                r"(?i)\b(url|image-set|expression|behavior|-moz-binding)\b", css
            ):
                reasons.append("style_requires_review")
    if before.get("external_origins", []) != after.get("external_origins", []):
        reasons.append("external_origins_changed")
    return sorted(set(reasons))


def check_eligibility(*, universe_id, adoption_id, target_release_id):
    """Read a current grant-bound candidate. Never applies, schedules or mints an actor."""
    base, owner, uid = _scope(universe_id)
    with registry.connect(base) as conn:
        registry.require_owner(conn, owner=owner, uid=uid)
        _schema(conn)
        policy = _policy(conn, owner, uid, adoption_id)
        registry.adoption(conn, owner=owner, uid=uid, adoption_id=adoption_id)
        if not policy or not policy["enabled"]:
            return {"eligible": False, "status": "disabled", "applied": False}
        snapshot = json.loads(policy["snapshot_json"])
        link = snapshot.get("progress", snapshot["link"])
        try:
            current = _link_snapshot(
                conn, base, owner, uid, adoption_id, link["series_id"], link["release_id"]
            )
            if current != link:
                raise ValueError("installed baseline, adoption or dependencies changed")
            chain = series._chain(conn, link["series_id"])
            by_id = {item["release_id"]: item for item in chain}
            target = by_id.get(target_release_id)
            if target is None or target["sequence"] <= by_id[link["release_id"]]["sequence"]:
                raise ValueError("target is not a newer release in the verified series")
            # Existing manual validator proves source availability and exact
            # retained dependencies under the same attached-database fence.
            _, _, _, readset = _prepare(
                conn, base, owner, uid, adoption_id, target["definition_id"]
            )
            before = get_definition(base, link["definition_id"])
            after = get_definition(base, target["definition_id"])
            if digest(series._normalized(after)) != target["definition_digest"]:
                raise ValueError("target release content does not match its registered digest")
            reasons = presentation_decisions(before, after)
        except (ValueError, LookupError) as exc:
            return {
                "eligible": False,
                "status": "requires_decision",
                "reason": str(exc),
                "applied": False,
            }
        if reasons:
            return {
                "eligible": False,
                "status": "requires_decision",
                "reasons": reasons,
                "applied": False,
            }
        if readset["current_hash"] == readset["replacement_hash"]:
            return {"eligible": False, "status": "no_content_change", "applied": False}
        return {
            "eligible": True,
            "status": "eligible_presentation_update",
            "applied": False,
            "policy": POLICY,
            "policy_revision": policy["revision"],
            "series_id": link["series_id"],
            "target_release_id": target_release_id,
            "definition_id": target["definition_id"],
            "summary": target["summary"],
            "readset_digest": digest(readset),
            "adoption_revision": readset["adoption_revision"],
        }
