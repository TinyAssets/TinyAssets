"""Broker-held, credential-free source descriptors published on a box channel."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from contextvars import ContextVar

BOX_REQUEST = ContextVar("broker_box_request", default=False)


def publish(ledger, owner, owner_scope, document):
    from tinyassets.providers.definition import _validate, _verified_definition
    from tinyassets.storage.outbound_connections import GrantResolutionError

    definition = _verified_definition({**document, "owner_user_id": owner})
    _validate(access_method=definition.access_method, protocol=definition.protocol,
              model=definition.model, ref=definition.ref, visibility=definition.visibility)
    # HTTP descriptors require a live grant. Native descriptors carry no broker
    # credential; their login and execution authority stay inside the owner box.
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        grant = conn.execute(
            "SELECT g.grant_id FROM outbound_connection_grants g "
            "JOIN outbound_connections c ON c.connection_id=g.connection_id "
            "WHERE g.grant_id=? AND g.owner_user_id=? "
            "AND c.owner_user_id=? AND c.connection_type='http' "
            "AND g.revoked_at IS NULL AND c.revoked_at IS NULL",
            (definition.ref, owner, owner),
        ).fetchone()
        if grant is None and definition.access_method == "api_key_http":
            raise GrantResolutionError("outbound connection grant identity mismatch")
        conn.execute("CREATE TABLE IF NOT EXISTS box_model_sources ("
                     "owner TEXT NOT NULL, id TEXT NOT NULL, definition TEXT NOT NULL, "
                     "PRIMARY KEY(owner, id))")
        conn.execute("INSERT INTO box_model_sources VALUES (?, ?, ?) "
                     "ON CONFLICT(owner, id) DO UPDATE SET definition=excluded.definition",
                     (owner, definition.id, json.dumps(definition.as_dict())))
        if definition.access_method == "api_key_http":
            conn.execute("UPDATE outbound_connection_grants SET inference_required=1 "
                         "WHERE grant_id=? AND inference_required=0", (definition.ref,))
    return {"id": definition.id}


def read(ledger, owner_scope):
    owner_scope = ledger.require_authenticated_principal_id()
    with closing(sqlite3.connect(ledger._db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='box_model_sources'").fetchone()
        if not exists:
            return []
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT definition FROM box_model_sources WHERE owner=?", (owner_scope,))]
