"""MCP event subscriptions and delivery on the trusted side (ADR-025, plan P0-4/P0-5).

The broker holds every subscription, its signing key, the delivery state and the
callback connection, in a store no box or daemon opens. The router subscribes
from the caller's verified identity. A box only reports that one of its owner's
runs completed, naming the subscriptions it re-authorized against owner files;
it never sees a callback URL or secret, and it has no network to send one.

Signing keys are broker-held records like every other broker credential today;
S28's key hierarchy seals them with the rest, so this store adds no key path.

Only identifiers leave this service. Successful workflow completion is a receipt,
not evidence that the remote agent has processed it or produced a reply.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tinyassets.mcp_event_webhooks import (
    EventError,
    callback_url,
    canonical,
    post,
    signed_headers,
    signing_key,
    verify,
)
from tinyassets.mcp_events import NAME, arguments

MAX_TTL = 86400
MAX_ATTEMPTS = 8
DELIVERY_TTL = 86400
VERIFICATION_LIMIT = 32
VERIFICATION_WINDOW = 60
MAX_SUBSCRIPTIONS = 128
#: Pending deliveries per owner; beyond it a capture is refused and stays owed in the box.
MAX_PENDING = 512
#: Deliveries per pass. Each send is bounded at 10 s, so shutdown joins at most this many.
PASS_LIMIT = 4
logger = logging.getLogger(__name__)


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


def egress_sender(binding):
    """POST through the owner's egress policy: ``binding(owner)`` is the broker's."""
    from tinyassets.broker.egress import destination

    def send(owner, url, body, headers):
        return post(url, body, headers,
                    resolve=lambda host, port: destination(binding(owner), host, port))

    return send


def _event(value):
    """A box's completion report: identifiers and a timestamp, nothing else."""
    if not isinstance(value, dict) or set(value) != {
        "command_center_id", "branch_def_id", "run_id", "event_id", "occurred",
    }:
        raise EventError(-32602, "Invalid event report")
    for field in ("command_center_id", "branch_def_id", "run_id", "event_id"):
        if not isinstance(value[field], str) or not 1 <= len(value[field]) <= 256:
            raise EventError(-32602, "Invalid event report")
    if type(value["occurred"]) not in (int, float) or not math.isfinite(value["occurred"]):
        raise EventError(-32602, "Invalid event report")
    return value


class EventStore:
    def __init__(self, path, *, sender, clock=time.time):
        self.path = Path(path)
        self.sender, self.clock = sender, clock

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA secure_delete=ON")  # erased keys leave no free-page copy
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, arguments TEXT NOT NULL,
                    config TEXT NOT NULL, active INTEGER NOT NULL, starts REAL NOT NULL,
                    expires REAL NOT NULL, verified REAL NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    subscription TEXT NOT NULL, event_id TEXT NOT NULL, body TEXT NOT NULL,
                    created REAL NOT NULL, due REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'pending', lease TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(subscription,event_id));
                CREATE INDEX IF NOT EXISTS event_delivery_due ON deliveries(state,due);
                CREATE TABLE IF NOT EXISTS verification_budget (
                    owner TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL);
            """)
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _open(row):
        """The callback URL, signing key, arguments and saved authority of one row."""
        config = json.loads(row["config"])
        if not isinstance(config, dict) or not {"url", "secret", "arguments"} <= set(config):
            raise ValueError("unreadable subscription")
        return config

    def _identity(self, owner, params):
        if not isinstance(params, dict) or params.get("name") != NAME:
            raise EventError(-32602, "Unknown event")
        args = arguments(params.get("arguments"))
        delivery = params.get("delivery")
        if not isinstance(delivery, dict) or delivery.get("mode") != "webhook":
            raise EventError(-32602, "Only webhook delivery is supported")
        url = callback_url(delivery.get("url"))
        ident = "sub_" + hashlib.sha256(canonical([owner, url, NAME, args]).encode()).hexdigest()
        return ident, args, url

    def _admit_verification(self, owner):
        """Charge failed attempts too, before I/O, across processes and restarts."""
        now = self.clock()
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM verification_budget WHERE started<?", (now - MAX_TTL,))
            row = conn.execute(
                "SELECT started,attempts FROM verification_budget WHERE owner=?", (owner,)
            ).fetchone()
            started, attempts = (row["started"], row["attempts"]) if row else (now, 0)
            if now >= started + VERIFICATION_WINDOW:
                started, attempts = now, 0
            if attempts >= VERIFICATION_LIMIT:
                raise EventError(-32025, "Verification limit reached", limit="verifications")
            conn.execute(
                "INSERT OR REPLACE INTO verification_budget VALUES (?,?,?)",
                (owner, started, attempts + 1),
            )

    @staticmethod
    def _at_capacity(conn, owner, previous, now):
        return (
            not (previous and previous["active"] and previous["expires"] > now)
            and conn.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE owner=? AND active=1 AND expires>?",
                (owner, now),
            ).fetchone()[0]
            >= MAX_SUBSCRIPTIONS
        )

    def subscribe(self, owner, params, *, authority=None):
        """``owner`` and ``authority`` come from the router's verified identity."""
        ident, args, url = self._identity(owner, params)
        secret = params["delivery"].get("secret")
        signing_key(secret)
        if params.get("cursor") is not None:
            raise EventError(-32602, "This event does not support replay")
        ttl = params.get("ttlMs", MAX_TTL * 1000)
        if ttl is not None and (type(ttl) is not int or ttl <= 0):
            raise EventError(-32602, "ttlMs must be a positive integer or null")
        lifetime = min(MAX_TTL * 1000, ttl) / 1000 if ttl is not None else MAX_TTL
        now = self.clock()
        with self.db() as conn:
            previous = conn.execute(
                "SELECT * FROM subscriptions WHERE id=? AND owner=?", (ident, owner)
            ).fetchone()
            if self._at_capacity(conn, owner, previous, now):
                raise EventError(-32025, "Subscription limit reached", limit="subscriptions")
        old = self._open(previous) if previous and previous["active"] else None
        verified = now
        if not (
            old
            and previous["expires"] > now
            and previous["verified"] > now - 300
            and old["secret"] == secret
        ):
            self._admit_verification(owner)
            verify(ident, url, secret, sender=lambda *sent: self.sender(owner, *sent))
        else:
            verified = previous["verified"]
        config = {"url": url, "secret": secret, "arguments": args, "authority": authority}
        if old and previous["expires"] > now and old["secret"] != secret:
            config.update(old_secret=old["secret"], rotation_until=now + 120)
        elif old and old.get("rotation_until", 0) > now:
            config.update(old_secret=old["old_secret"], rotation_until=old["rotation_until"])
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._at_capacity(conn, owner, previous, now):
                raise EventError(-32025, "Subscription limit reached", limit="subscriptions")
            current = conn.execute(
                "SELECT revision FROM subscriptions WHERE id=?", (ident,)
            ).fetchone()
            if (current[0] if current else None) != (previous["revision"] if previous else None):
                raise EventError(-32015, "Subscription changed; retry", reason="changed")
            starts = previous["starts"] if old and previous["expires"] > now else now
            conn.execute(
                "INSERT OR REPLACE INTO subscriptions VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    owner,
                    canonical({k: args[k] for k in ("command_center_id", "branch_def_id")}),
                    canonical(config),
                    1,
                    starts,
                    now + lifetime,
                    verified,
                    (current[0] + 1) if current else 1,
                ),
            )
        return {
            "id": ident,
            "refreshBefore": _iso(now + lifetime),
            "cursor": None,
            "truncated": False,
        }

    def unsubscribe(self, owner, params):
        ident, _, _ = self._identity(owner, params)
        if self.path.exists():
            with self.db() as conn:
                self._revoke(conn, ident, owner)
        return {}

    def erase_owner(self, owner):
        """Account deletion: every subscription, key and delivery of this owner."""
        if not self.path.exists():
            return 0
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM deliveries WHERE subscription IN "
                         "(SELECT id FROM subscriptions WHERE owner=?)", (owner,))
            conn.execute("DELETE FROM verification_budget WHERE owner=?", (owner,))
            return conn.execute("DELETE FROM subscriptions WHERE owner=?", (owner,)).rowcount

    @staticmethod
    def _revoke(conn, ident, owner):
        conn.execute(
            "UPDATE subscriptions SET active=0,config='',revision=revision+1 "
            "WHERE id=? AND owner=?",
            (ident, owner),
        )
        conn.execute(
            "UPDATE deliveries SET state='stopped' WHERE subscription=? "
            "AND state IN ('pending','sending')",
            (ident,),
        )

    def _candidates(self, conn, owner, event):
        """Live subscriptions for this run's scope; an unreadable one is revoked."""
        now = self.clock()
        if event["occurred"] < now - DELIVERY_TTL:
            return []
        found = []
        for row in conn.execute(
            "SELECT * FROM subscriptions WHERE owner=? AND active=1 AND starts<=? AND expires>?",
            (owner, event["occurred"], now),
        ).fetchall():
            if json.loads(row["arguments"]) != {
                "command_center_id": event["command_center_id"],
                "branch_def_id": event["branch_def_id"],
            }:
                continue
            try:
                config = self._open(row)
            except ValueError:
                self._revoke(conn, row["id"], owner)
                continue
            found.append((row, config))
        return found

    def match(self, owner, event):
        """What the box must re-authorize: arguments and saved authority, no URL or key."""
        event = _event(event)
        if not self.path.exists():
            return []
        with self.db() as conn:
            return [
                {"id": row["id"], "arguments": config["arguments"],
                 "authority": config["authority"]}
                for row, config in self._candidates(conn, owner, event)
            ]

    def capture(self, owner, event, *, allow, revoke):
        """Enqueue for re-authorized subscriptions; revoke the ones the box refused.

        Both lists name only this owner's subscriptions; an ID outside this
        run's scope is ignored, so a box cannot aim a delivery anywhere else.
        """
        event = _event(event)
        if (not isinstance(allow, list) or not isinstance(revoke, list)
                or any(not isinstance(item, str) for item in (*allow, *revoke))
                or len(allow) + len(revoke) > MAX_SUBSCRIPTIONS):
            raise EventError(-32602, "Invalid event report")
        if not self.path.exists():
            return {"queued": 0}
        body = canonical(
            {
                "eventId": "evt_" + hashlib.sha256(canonical(
                    [owner, event["command_center_id"], event["event_id"]]).encode()).hexdigest(),
                "name": NAME,
                "timestamp": _iso(event["occurred"]),
                "cursor": None,
                "data": {k: event[k] for k in ("command_center_id", "branch_def_id", "run_id")},
            }
        )
        queued = 0
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT COUNT(*) FROM deliveries d JOIN subscriptions s ON s.id=d.subscription "
                "WHERE s.owner=? AND d.state IN ('pending','sending')", (owner,)
            ).fetchone()[0] + len(allow) > MAX_PENDING:
                # The source stays owed in the box, inside its one-day horizon.
                raise EventError(-32025, "Delivery backlog limit reached", limit="deliveries")
            for row, _ in self._candidates(conn, owner, event):
                if row["id"] in revoke:
                    self._revoke(conn, row["id"], owner)
                elif row["id"] in allow:
                    queued += conn.execute(
                        "INSERT OR IGNORE INTO deliveries "
                        "(subscription,event_id,body,created,due) VALUES (?,?,?,?,?)",
                        (row["id"], json.loads(body)["eventId"], body, event["occurred"],
                         self.clock()),
                    ).rowcount
        return {"queued": queued}

    def pump(self, limit=PASS_LIMIT):
        """Claim before I/O; recover abandoned claims with stable IDs after restart."""
        if not self.path.exists():
            return 0
        now = self.clock()
        with self.db() as conn:
            # Keep dedup tombstones beyond a subscription's last possible delivery;
            # old source events cannot enter a new subscription (starts boundary).
            conn.execute(
                "DELETE FROM deliveries WHERE subscription IN "
                "(SELECT id FROM subscriptions WHERE expires<?)",
                (now - DELIVERY_TTL,),
            )
            conn.execute("DELETE FROM subscriptions WHERE expires<?", (now - DELIVERY_TTL,))
            conn.execute("DELETE FROM deliveries WHERE created<?", (now - 2 * DELIVERY_TTL,))
            for row in conn.execute(
                "SELECT id,owner FROM subscriptions WHERE active=1 AND expires<=?", (now,)
            ).fetchall():
                self._revoke(conn, row["id"], row["owner"])
            conn.execute(
                "UPDATE deliveries SET state='failed' WHERE state IN ('pending','sending') "
                "AND (created<? OR attempts>=?) AND due<=?",
                (now - DELIVERY_TTL, MAX_ATTEMPTS, now),
            )
            # One due delivery per owner per pass, oldest first, so an owner whose
            # callback hangs costs the others one bounded send, never the queue.
            rows = conn.execute(
                "SELECT subscription,event_id FROM ("
                " SELECT d.subscription,d.event_id,d.due,ROW_NUMBER() OVER ("
                "  PARTITION BY s.owner ORDER BY d.due) AS turn"
                " FROM deliveries d JOIN subscriptions s ON s.id=d.subscription"
                " WHERE d.state IN ('pending','sending') AND d.due<=?)"
                " WHERE turn=1 ORDER BY due LIMIT ?",
                (now, limit),
            ).fetchall()
        for item in rows:
            self._deliver(item["subscription"], item["event_id"])
        return len(rows)

    def _deliver(self, ident, event):
        now, lease = self.clock(), secrets.token_hex(16)
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE id=? AND active=1 AND expires>?", (ident, now)
            ).fetchone()
            if row is None:
                return
            changed = conn.execute(
                "UPDATE deliveries SET state='sending',lease=?,due=?,attempts=attempts+1 "
                "WHERE subscription=? AND event_id=? AND due<=? "
                "AND state IN ('pending','sending') AND attempts<?",
                (lease, now + 60, ident, event, now, MAX_ATTEMPTS),
            ).rowcount
            if not changed:
                return
            delivery = conn.execute(
                "SELECT * FROM deliveries WHERE subscription=? AND event_id=?", (ident, event)
            ).fetchone()
        try:
            config = self._open(row)
        except ValueError:
            with self.db() as conn:
                self._revoke(conn, ident, row["owner"])
            return
        try:
            body = delivery["body"].encode()
            old = config.get("old_secret") if config.get("rotation_until", 0) > now else None
            status, _ = self.sender(
                row["owner"],
                config["url"],
                body,
                signed_headers(
                    ident, event, body, config["secret"], old_secret=old, now=self.clock()
                ),
            )
        except Exception:
            # Redact all remote errors, secrets and callback addresses. A refused
            # egress policy retries too: the owner may still approve the host.
            status = 0
        with self.db() as conn:
            if status == 410:
                self._revoke(conn, ident, row["owner"])
                return
            state = (
                "received"
                if 200 <= status < 300
                else "pending"
                if (status in (0, 408, 429) or status >= 500)
                and delivery["attempts"] < MAX_ATTEMPTS
                else "failed"
            )
            conn.execute(
                "UPDATE deliveries SET state=?,due=?,lease='' "
                "WHERE subscription=? AND event_id=? AND lease=? AND state='sending'",
                (
                    state,
                    self.clock() + min(3600, 5 * 2 ** delivery["attempts"]),
                    ident,
                    event,
                    lease,
                ),
            )


class EventDeliveries:
    """Delivery on the broker's shared clock, never in a box (P0-5).

    The waker kicks a pass every tick, and a capture kicks one at once, so a
    retry is owed to this clock alone: it needs no box awake and no other activity.
    """

    def __init__(self, store):
        self.store = store
        self._task = None
        self._again = False
        self._stopping = False

    def kick(self):
        if self._stopping:
            return
        self._again = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self):
        while self._again and not self._stopping:
            self._again = False
            try:
                sent = await asyncio.to_thread(self.store.pump, limit=PASS_LIMIT)
            except Exception:
                logger.error("MCP event delivery pass failed")
                return
            # Another pass while anything was due: each owner's next delivery takes
            # its turn. A sent delivery's next due is in the future, so this ends.
            self._again = self._again or sent > 0

    async def close(self):
        self._stopping = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)


def router_operation(store, identities, doc):
    """The router's verified caller subscribes; the request body never names an owner."""
    from tinyassets.broker.admission import require_admitted

    if set(doc) != {"op", "action", "principal", "authority", "params"}:
        raise ValueError("invalid MCP events request")
    owner, authority = doc["principal"], doc["authority"]
    if not isinstance(owner, str) or not owner or not (
            authority is None or isinstance(authority, dict)):
        raise ValueError("invalid MCP events request")
    require_admitted(identities, owner)
    if doc["action"] == "subscribe":
        return store.subscribe(owner, doc["params"], authority=authority)
    if doc["action"] == "unsubscribe":
        return store.unsubscribe(owner, doc["params"])
    raise ValueError("invalid MCP events request")


def box_operation(store, owner, doc):
    """A box reports runs for its bound owner only; the owner is the relay's."""
    if doc.get("action") == "match" and set(doc) == {"op", "action", "event"}:
        return store.match(owner, doc["event"])
    if doc.get("action") == "capture" and set(doc) == {"op", "action", "event", "allow",
                                                       "revoke"}:
        return store.capture(owner, doc["event"], allow=doc["allow"], revoke=doc["revoke"])
    raise ValueError("invalid MCP events report")


def error_answer(exc):
    """The bounded JSON-RPC error a refusal may carry: no URL, key or response."""
    if isinstance(exc, EventError):
        return {"code": exc.code, "message": str(exc), "data": exc.data}
    if isinstance(exc, (PermissionError, KeyError)):
        return {"code": -32024, "message": "Event access unavailable"}
    return {"code": -32603, "message": "Event service unavailable"}


def request(root, document):
    """Router-only broker RPC for ``events/subscribe`` and ``events/unsubscribe``."""
    import socket

    from tinyassets import rpc_frames as rf
    from tinyassets.boxes.layout import BROKER_UID
    from tinyassets.broker.server import peer_uid

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        # Verification is bounded at 10 s; the rest is local SQLite.
        channel.settimeout(30)
        channel.connect(str(Path(root) / "broker" / "broker.sock"))
        if peer_uid(channel) != BROKER_UID:
            raise PermissionError("MCP events peer is not the broker")
        channel.sendall(rf.control(rf.CONNECTION, {"op": "MCP_EVENTS", **document}))
        answer = rf.read_frame_blocking(channel)
    if answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION:
        raise RuntimeError("MCP event service unavailable")
    reply = answer.control()
    if reply.get("op") == "MCP_EVENTS_IS":
        return reply["result"]
    if reply.get("op") == "MCP_EVENTS_ERROR":
        error = reply["error"]
        raise EventError(error["code"], error["message"], **error.get("data", {}))
    raise RuntimeError("MCP event service unavailable")
