"""Owner-scoped adoption metadata and single pending manual UI update per adoption.

Lives beside universe_app_ui so the UI, installed baseline and completion receipt
commit in one SQLite transaction. Source bodies remain in immutable definitions
and platform install pins; this registry stores identifiers/hashes, not copies.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from tinyassets.custom_agents import _agent_connect, _begin_write

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS command_center_adoptions (
        owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, adoption_id TEXT NOT NULL,
        install_request_id TEXT NOT NULL, original_definition_id TEXT NOT NULL,
        ui_definition_id TEXT NOT NULL, ui_id TEXT NOT NULL, baseline_hash TEXT NOT NULL,
        revision INTEGER NOT NULL, last_request_id TEXT NOT NULL DEFAULT '',
        last_plan_digest TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(owner_id, universe_id, adoption_id))""",
    """CREATE TABLE IF NOT EXISTS command_center_update_requests (
        owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, adoption_id TEXT NOT NULL,
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, plan_json TEXT NOT NULL,
        PRIMARY KEY(owner_id, universe_id, adoption_id), UNIQUE(request_id))""",
)


@contextmanager
def connect(base):
    with _agent_connect(base) as conn:
        for statement in _SCHEMA:
            conn.execute(statement)
        # BEGIN IMMEDIATE reserves writes in every attached database, including
        # WAL databases. Read automation dependencies through this connection so
        # an existing AutomationStore writer cannot change them before UI commit.
        # Only main is mutated: no cross-file atomic-write claim is made.
        from tinyassets.automations import automations_db_path

        automation_path = automations_db_path(base)
        if automation_path.is_file():
            conn.execute("ATTACH DATABASE ? AS retained_automations", (str(automation_path),))
        _begin_write(conn)
        yield conn


def require_owner(conn, *, owner, uid):
    row = conn.execute(
        "SELECT 1 FROM universe_acl WHERE universe_id=? AND actor_id=? AND permission='admin'",
        (uid, owner),
    ).fetchone()
    if row is None:
        raise PermissionError("command center not found")


def adoption(conn, *, owner, uid, adoption_id):
    row = conn.execute(
        "SELECT * FROM command_center_adoptions "
        "WHERE owner_id=? AND universe_id=? AND adoption_id=?",
        (owner, uid, adoption_id),
    ).fetchone()
    if row is None:
        raise LookupError("adoption not found")
    return dict(row)


def pending(conn, *, owner, uid, request_id):
    row = conn.execute(
        "SELECT * FROM command_center_update_requests "
        "WHERE owner_id=? AND universe_id=? AND request_id=?",
        (owner, uid, request_id),
    ).fetchone()
    if row is None:
        raise LookupError("update request not found")
    result = dict(row)
    result["plan"] = json.loads(result.pop("plan_json"))
    return result
