"""Closed reads of shared billing and published data; no owner-tree paths."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


class Plans:
    """Router/broker billing state, keyed by the authenticated account."""

    def __init__(self, root):
        self.path = Path(root) / ".broker" / "plans.db"

    def read(self, owner):
        if not self.path.exists():
            return {"tier": "free", "ends_at": None}
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT tier, ends_at FROM plans WHERE owner=?", (owner,)).fetchone()
        return {"tier": row[0], "ends_at": row[1]} if row else {"tier": "free", "ends_at": None}

    def apply(self, owner, *, tier, event_created, ends_at=None):
        import math

        if (tier not in {"free", "paid"} or not math.isfinite(event_created)
                or (ends_at is not None and not math.isfinite(ends_at))):
            raise ValueError("invalid plan event")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS plans (owner TEXT PRIMARY KEY, "
                       "tier TEXT NOT NULL, event_created REAL NOT NULL, ends_at REAL)")
            return db.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?) ON CONFLICT(owner) DO UPDATE SET "
                "tier=excluded.tier, event_created=excluded.event_created, "
                "ends_at=excluded.ends_at "
                "WHERE excluded.event_created > plans.event_created OR "
                "(excluded.event_created = plans.event_created AND "
                "(plans.tier != 'free' OR excluded.tier = 'free'))",
                (owner, tier, event_created, ends_at)).rowcount == 1


def read(root, owner, document):
    if root is None or set(document) != {"op", "query", "args"}:
        raise ValueError("invalid shared read")
    query, args = document["query"], document["args"]
    if not isinstance(args, dict):
        raise ValueError("invalid shared read arguments")
    if query == "plan" and not args:
        return Plans(root).read(owner)
    from tinyassets.commons_catalog import _get_definition, _list_definitions

    published = Path(root) / ".published"
    if query == "definition" and set(args) == {"definition_id", "include_catalogue"}:
        return _get_definition(published, **args)
    if query == "catalog" and set(args) == {
            "query", "tags", "exclude_tags", "author_id", "limit", "offset"}:
        return _list_definitions(published, **args)
    raise ValueError("unsupported shared read")


def remote(operation, **args):
    from tinyassets.owner_channel import request

    return request("", {"op": "BOX_READ", "query": operation, "args": args})
