"""Broker-held, credential-free source descriptors published on a box channel."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from contextvars import ContextVar

BOX_REQUEST = ContextVar("broker_box_request", default=False)


def publish(ledger, owner, center, document):
    from tinyassets.broker.ledger_queries import GRANTED_RESOURCE, local_query
    from tinyassets.providers.definition import _validate, _verified_definition

    definition = _verified_definition(
        {**document, "owner_user_id": owner, "universe_id": center}, expect_universe=center)
    if definition.access_method != "api_key_http":
        raise PermissionError("owner mode requires an HTTP broker source")
    _validate(access_method=definition.access_method, protocol=definition.protocol,
              model=definition.model, ref=definition.ref, visibility=definition.visibility)
    # Resolve the source from the broker's live grant, never from box credentials.
    local_query(ledger, query=GRANTED_RESOURCE, principal=owner,
                command_center=center, grant_id=definition.ref)
    with ledger._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS box_model_sources ("
                     "center TEXT NOT NULL, id TEXT NOT NULL, definition TEXT NOT NULL, "
                     "PRIMARY KEY(center, id))")
        conn.execute("INSERT INTO box_model_sources VALUES (?, ?, ?) "
                     "ON CONFLICT(center, id) DO UPDATE SET definition=excluded.definition",
                     (center, definition.id, json.dumps(definition.as_dict())))
    return {"id": definition.id}


def read(ledger, center):
    with closing(sqlite3.connect(ledger._db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='box_model_sources'").fetchone()
        if not exists:
            return []
        return [json.loads(row[0]) for row in conn.execute(
            "SELECT definition FROM box_model_sources WHERE center=?", (center,))]
