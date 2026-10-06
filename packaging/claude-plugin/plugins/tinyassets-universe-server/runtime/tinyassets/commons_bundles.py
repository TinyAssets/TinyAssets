"""Author-owned catalogue identity; immutable definitions remain the version unit.

Source keys are private platform evidence, not public update consent. The release
registry and recipient policies alone authorize updates to installed copies.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid


def ensure_schema(conn):
    for sql in (
        """CREATE TABLE IF NOT EXISTS commons_bundles (
            bundle_id TEXT PRIMARY KEY, author_id TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS commons_bundle_sources (
            owner_id TEXT NOT NULL, source_key TEXT NOT NULL,
            bundle_id TEXT NOT NULL REFERENCES commons_bundles(bundle_id),
            PRIMARY KEY(owner_id, source_key))""",
        """CREATE TABLE IF NOT EXISTS commons_bundle_versions (
            definition_id TEXT PRIMARY KEY REFERENCES agent_definitions(agent_definition_id),
            bundle_id TEXT NOT NULL REFERENCES commons_bundles(bundle_id),
            version INTEGER NOT NULL CHECK(version > 0),
            previous_definition_id TEXT NOT NULL,
            UNIQUE(bundle_id, version))""",
    ):
        conn.execute(sql)


def _source_key(universe_id, action):
    kind = "package" if action.get("package") is not None else "system"
    source = action.get("ui_id") or action["name"]
    return hashlib.sha256(json.dumps([universe_id, kind, source]).encode()).hexdigest()


def _mint(conn, author):
    bundle_id = "bundle_" + uuid.uuid4().hex
    conn.execute("INSERT INTO commons_bundles VALUES (?, ?)", (bundle_id, author))
    return bundle_id


def _source_bundle(conn, author, universe_id, action):
    source = _source_key(universe_id, action)
    row = conn.execute(
        "SELECT bundle_id FROM commons_bundle_sources WHERE owner_id=? AND source_key=?",
        (author, source),
    ).fetchone()
    if row:
        return row[0]
    bundle_id = _mint(conn, author)
    conn.execute("INSERT INTO commons_bundle_sources VALUES (?, ?, ?)",
                 (author, source, bundle_id))
    return bundle_id


def publication_bundle(base_path, *, author, universe_id, action):
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        return _source_bundle(conn, author, universe_id, action)


def authorize(conn, *, author, bundle_id):
    from tinyassets.custom_agents import AgentValidationError

    row = conn.execute("SELECT author_id FROM commons_bundles WHERE bundle_id=?",
                       (bundle_id,)).fetchone()
    if row is None or row[0] != author:
        raise AgentValidationError("only the original author can publish under this bundle_id")


def next_version(base_path, bundle_id):
    from tinyassets.custom_agents import _agent_connect

    with _agent_connect(base_path) as conn:
        return conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM commons_bundle_versions WHERE bundle_id=?",
            (bundle_id,),
        ).fetchone()[0]


def append(conn, *, author, bundle_id, definition_id):
    authorize(conn, author=author, bundle_id=bundle_id)
    head = conn.execute(
        "SELECT version, definition_id FROM commons_bundle_versions "
        "WHERE bundle_id=? ORDER BY version DESC LIMIT 1", (bundle_id,),
    ).fetchone()
    conn.execute("INSERT INTO commons_bundle_versions VALUES (?, ?, ?, ?)",
                 (definition_id, bundle_id, head[0] + 1 if head else 1,
                  head[1] if head else ""))


def metadata(conn, definition_id):
    row = conn.execute(
        "SELECT bundle_id, version, previous_definition_id FROM commons_bundle_versions "
        "WHERE definition_id=?", (definition_id,),
    ).fetchone()
    if row is None:
        return {}
    current = conn.execute(
        "SELECT definition_id FROM commons_bundle_versions WHERE bundle_id=? "
        "ORDER BY version DESC LIMIT 1", (row[0],),
    ).fetchone()[0]
    return {"bundle_id": row[0], "bundle_version": row[1],
            "previous_definition_id": row[2], "current_definition_id": current}


def backfill(conn, base_path):
    """Only activated platform pins and exact receipts establish old membership.

    Run once on schema initialization. No public definition, fingerprint, blob,
    installation or recipient release record is rewritten.
    """
    from tinyassets.command_center_packages import database_path

    path = database_path(base_path)
    if not path.exists():
        return
    with sqlite3.connect(path) as pins:
        pins.row_factory = sqlite3.Row
        columns = {r[1] for r in pins.execute("PRAGMA table_info(pins)")}
        if not columns:
            return
        rows = pins.execute(
            "SELECT * FROM pins WHERE kind='publish' AND state='activated' "
            "ORDER BY activated_at, created_at, pin_id"
        ).fetchall()
    publications = []
    for pin in rows:
        action = json.loads(pin["record_json"])["action"]
        if not action.get("ui_id") and action.get("package") is None:
            continue
        receipt = json.loads(pin["progress_json"])
        if not receipt.get("published") or pin["digest"] != action.get("snapshot_digest"):
            continue
        definition_id = receipt.get("agent_definition_id")
        definition = conn.execute(
            "SELECT author_id, idempotency_key, created_at FROM agent_definitions "
            "WHERE agent_definition_id=?", (definition_id,),
        ).fetchone()
        # Pre-owner-column pins still bind an exact platform-written receipt to
        # the authenticated author of its definition. Never use today's home
        # owner or a matching public name as evidence. A conflicting explicit
        # owner, however, always refuses membership.
        owner = pin["owner_id"] if "owner_id" in columns else ""
        if (definition is None or (owner and definition[0] != owner)
                or definition[1] != "publish-request:" + pin["request_id"]):
            continue
        if metadata(conn, definition_id):
            continue
        publications.append(
            (definition[2], definition_id, definition[0], pin["universe_id"], action))
    # A delayed pin-finalization retry must not make an older definition current.
    for _, definition_id, author, universe_id, action in sorted(publications):
        bundle_id = _source_bundle(conn, author, universe_id, action)
        append(conn, author=author, bundle_id=bundle_id, definition_id=definition_id)
