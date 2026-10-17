"""Broker-held, credential-free source descriptors published on a box channel."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from contextvars import ContextVar

BOX_REQUEST = ContextVar("broker_box_request", default=False)


def publish(ledger, owner, center, document):
    from tinyassets.providers.definition import _validate, _verified_definition
    from tinyassets.storage.outbound_connections import GrantResolutionError

    definition = _verified_definition(
        {**document, "owner_user_id": owner, "universe_id": center}, expect_universe=center)
    if definition.access_method != "api_key_http":
        raise PermissionError("owner mode requires an HTTP broker source")
    _validate(access_method=definition.access_method, protocol=definition.protocol,
              model=definition.model, ref=definition.ref, visibility=definition.visibility)
    # Resolve the source from the broker's live grant, never from box credentials.
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        grant = conn.execute(
            "SELECT g.grant_id FROM outbound_connection_grants g "
            "JOIN outbound_connections c ON c.connection_id=g.connection_id "
            "WHERE g.grant_id=? AND g.owner_user_id=? AND g.universe_id=? "
            "AND c.owner_user_id=? AND c.connection_type='http' "
            "AND g.revoked_at IS NULL AND c.revoked_at IS NULL",
            (definition.ref, owner, center, owner),
        ).fetchone()
        if grant is None:
            raise GrantResolutionError("outbound connection grant identity mismatch")
        conn.execute("CREATE TABLE IF NOT EXISTS box_model_sources ("
                     "center TEXT NOT NULL, id TEXT NOT NULL, definition TEXT NOT NULL, "
                     "PRIMARY KEY(center, id))")
        conn.execute("INSERT INTO box_model_sources VALUES (?, ?, ?) "
                     "ON CONFLICT(center, id) DO UPDATE SET definition=excluded.definition",
                     (center, definition.id, json.dumps(definition.as_dict())))
        conn.execute("UPDATE outbound_connection_grants SET inference_required=1 "
                     "WHERE grant_id=? AND inference_required=0", (definition.ref,))
    return {"id": definition.id}


def backfill_metering(ledger, conn):
    """Only promote old grants, from shared metadata the broker can itself read."""
    from tinyassets.broker.provider_metadata import read_definitions
    from tinyassets.owner_runtime import current
    from tinyassets.providers.definition import _verified_definition

    conn.execute(
        "UPDATE outbound_connection_grants SET inference_required=1 "
        "WHERE inference_required=0 AND connection_id IN ("
        "SELECT c.connection_id FROM outbound_connections c "
        "JOIN connection_capabilities k ON k.connection_id=c.connection_id "
        "WHERE c.connection_type='http' "
        "AND k.capability_kind IN ('model_use', 'model_discovery'))")
    grants = conn.execute(
        "SELECT grant_id, owner_user_id, universe_id FROM outbound_connection_grants "
        "WHERE inference_required=0").fetchall()
    if not grants:
        return
    published = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='box_model_sources'").fetchone()
    for center in {row["universe_id"] for row in grants}:
        rows = [json.loads(row[0]) for row in conn.execute(
            "SELECT definition FROM box_model_sources WHERE center=?", (center,))
        ] if published else []
        if current() is None:
            try:
                rows.extend(read_definitions(ledger._data_root, center))
            except FileNotFoundError:
                pass
        refs = set()
        for row in rows:
            definition = _verified_definition(row, expect_universe=center)
            if definition.access_method == "api_key_http":
                refs.add((definition.ref, definition.owner_user_id))
        for grant in grants:
            if (grant["universe_id"] == center
                    and (grant["grant_id"], grant["owner_user_id"]) in refs):
                conn.execute("UPDATE outbound_connection_grants SET inference_required=1 "
                             "WHERE grant_id=? AND inference_required=0", (grant["grant_id"],))


def read(ledger, center):
    with closing(sqlite3.connect(ledger._db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='box_model_sources'").fetchone()
        if not exists:
            return []
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT definition FROM box_model_sources WHERE center=?", (center,))]
