"""Closed reads of shared billing and published data; no owner-tree paths."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


class PlanMissing(LookupError):
    """An admitted account has no billing row; never infer a free entitlement."""


class Plans:
    """Shared billing writer and reader, keyed by authenticated owner."""

    def __init__(self, root=None, *, broker_dir=None):
        from tinyassets.owner_runtime import refuse_router_store

        refuse_router_store("plans")
        state = Path(broker_dir) if broker_dir is not None else Path(root) / ".broker"
        self.path = state.resolve() / "plans.db"

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS plans (owner TEXT PRIMARY KEY, "
                       "tier TEXT NOT NULL, event_created REAL NOT NULL, ends_at REAL, "
                       "customer_id TEXT UNIQUE, subscription_id TEXT UNIQUE, attempt TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(plans)")}
            for name in ("billing_suspended", "abuse_suspended"):
                if name not in columns:
                    db.execute(f"ALTER TABLE plans ADD COLUMN {name} INTEGER NOT NULL "
                               f"DEFAULT 0 CHECK ({name} IN (0, 1))")

    def suspended(self, owner):
        plan = self.read(owner)  # Missing plans and missing schema fail loudly.
        return bool(plan["billing_suspended"] or plan["abuse_suspended"])

    def set_suspension(self, owner, *, reason, suspended):
        if reason not in {"billing", "abuse"} or type(suspended) is not bool:
            raise ValueError("invalid account suspension")
        self.read(owner)
        with closing(self._connect()) as db, db:
            db.execute(f"UPDATE plans SET {reason}_suspended=? WHERE owner=?",
                       (int(suspended), owner))

    def attach_admission(self, db):
        """Attach before BEGIN so the identity and first plan commit together."""
        self.initialize()
        db.execute("ATTACH DATABASE ? AS billing", (str(self.path),))

    def admit_free(self, db, owner, *, first_admission):
        """Only first account admission may supply the explicit free default."""
        if first_admission:
            db.execute("INSERT OR IGNORE INTO billing.plans (owner, tier, event_created) "
                       "VALUES (?, 'free', 0)", (owner,))
        elif db.execute("SELECT 1 FROM billing.plans WHERE owner=?", (owner,)).fetchone() is None:
            raise PlanMissing(owner)

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def read(self, owner):
        if not self.path.exists():
            raise PlanMissing(owner)
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM plans WHERE owner=?", (owner,)).fetchone()
        if row is None:
            raise PlanMissing(owner)
        return dict(row)

    def set_plan(self, owner, tier, *, event_created, ends_at=None,
                 customer_id=None, subscription_id=None):
        import math

        from tinyassets.broker.owner_identities import validate_principal

        validate_principal(owner)
        if (tier not in {"free", "paid"} or not math.isfinite(event_created)
                or (ends_at is not None and not math.isfinite(ends_at))):
            raise ValueError("invalid plan event")
        self.initialize()
        with closing(self._connect()) as db, db:
            return db.execute(
                "INSERT INTO plans (owner, tier, event_created, ends_at, customer_id, "
                "subscription_id) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(owner) DO UPDATE SET "
                "tier=excluded.tier, event_created=excluded.event_created, "
                "ends_at=excluded.ends_at, "
                "customer_id=COALESCE(excluded.customer_id, plans.customer_id), "
                "subscription_id=COALESCE(excluded.subscription_id, plans.subscription_id) "
                "WHERE excluded.event_created > plans.event_created OR "
                "(excluded.event_created = plans.event_created AND "
                "(plans.tier != 'free' OR excluded.tier = 'free'))",
                (owner, tier, event_created, ends_at, customer_id, subscription_id)).rowcount == 1

    apply = set_plan
    apply_tier_event = set_plan

    def owner_for(self, *, customer_id="", subscription_id=""):
        if not self.path.exists():
            raise PlanMissing("billing reference has no owner")
        with closing(self._connect()) as db:
            rows = db.execute("SELECT owner FROM plans WHERE customer_id=? OR subscription_id=?",
                              (customer_id or None, subscription_id or None)).fetchall()
        owners = {row[0] for row in rows}
        if len(owners) != 1:
            raise PlanMissing("billing reference has no unique owner")
        return owners.pop()

    def owner_for_checkout(self, *, session_id="", attempt_id=""):
        if not self.path.exists():
            raise PlanMissing("checkout has no owner")
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT owner FROM plans WHERE "
                "CASE WHEN json_valid(attempt) THEN json_extract(attempt, '$.session_id') END = ? "
                "OR CASE WHEN json_valid(attempt) THEN "
                "json_extract(attempt, '$.attempt_id') END = ?",
                (session_id or None, attempt_id or None)).fetchall()
        if len(rows) != 1:
            raise PlanMissing("checkout has no unique owner")
        return rows[0][0]

    def current_checkout_attempt(self, owner, *, now):
        import json

        raw = self.read(owner)["attempt"]
        if raw is None:
            return None
        try:
            attempt = json.loads(raw)
            if now >= float(attempt["expires_at"]):
                return None
            return attempt
        except (TypeError, ValueError, KeyError):
            return {"__corrupt__": True}

    def begin_checkout_attempt(self, owner, *, now, attempt_id, mode, params, lease_seconds):
        import json

        self.read(owner)
        attempt = dict(attempt_id=attempt_id, created_at=now, expires_at=now + lease_seconds,
                       mode=mode, params=params, session_id=None, url=None)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if self.current_checkout_attempt(owner, now=now) is not None:
                return None
            db.execute("UPDATE plans SET attempt=? WHERE owner=?", (json.dumps(attempt), owner))
        return attempt

    def record_checkout_session(self, owner, *, attempt_id, session_id, url):
        import json

        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            attempt = self.current_checkout_attempt(owner, now=0)
            if attempt is None or attempt.get("attempt_id") != attempt_id:
                return False
            attempt.update(session_id=session_id, url=url)
            db.execute("UPDATE plans SET attempt=? WHERE owner=?", (json.dumps(attempt), owner))
            return True

    def settle_checkout_attempt(self, owner, *, session_id="", attempt_id=""):
        if not session_id and not attempt_id:
            raise ValueError("settle needs a session_id or an attempt_id")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            attempt = self.current_checkout_attempt(owner, now=0)
            if attempt is None or not (
                (session_id and attempt.get("session_id") == session_id)
                or (attempt_id and attempt.get("attempt_id") == attempt_id)
            ):
                return False
            db.execute("UPDATE plans SET attempt=NULL WHERE owner=?", (owner,))
            return True


def read(root, owner, document):
    if root is None or set(document) != {"op", "query", "args"}:
        raise ValueError("invalid shared read")
    query, args = document["query"], document["args"]
    if not isinstance(args, dict):
        raise ValueError("invalid shared read arguments")
    if query == "plan" and not args:
        plan = Plans(root).read(owner)
        return {"tier": plan["tier"], "ends_at": plan["ends_at"]}
    from tinyassets.commons_catalog import _get_definition, _list_definitions

    published = Path(root) / ".published"
    if query == "bundle_version" and set(args) == {"bundle_id"}:
        from tinyassets.commons_catalog import _definition_connect

        with _definition_connect(published) as db:
            if db is None:
                return 1
            return db.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM "
                              "commons_bundle_versions WHERE bundle_id=?",
                              (args["bundle_id"],)).fetchone()[0]
    if query == "definition" and set(args) == {"definition_id", "include_catalogue"}:
        return _get_definition(published, **args)
    if query == "catalog" and set(args) == {
            "query", "tags", "exclude_tags", "author_id", "limit", "offset"}:
        return _list_definitions(published, **args)
    raise ValueError("unsupported shared read")


def remote(operation, **args):
    from tinyassets.owner_channel import request

    return request("", {"op": "BOX_READ", "query": operation, "args": args})


def publish(root, owner, document):
    import os

    from tinyassets.boxes.layout import BROKER_READ_GID
    from tinyassets.commons_catalog import publish_definition
    from tinyassets.owner_runtime import refuse_router_store
    from tinyassets.storage import DB_FILENAME

    refuse_router_store("published")
    if root is None or set(document) != {"op", "payload", "idempotency_key"}:
        raise ValueError("invalid shared publication")
    published = Path(root) / ".published"
    published.mkdir(exist_ok=True)
    os.chown(published, -1, BROKER_READ_GID)
    os.chmod(published, 0o2750)
    result = publish_definition(published, author_id=owner, payload=document["payload"],
                                idempotency_key=document["idempotency_key"])
    # Public data is broker-written and router-readable, without WAL sidecars.
    os.chown(published / DB_FILENAME, -1, BROKER_READ_GID)
    os.chmod(published / DB_FILENAME, 0o640)
    return result
