"""Durable owner-scoped MCP webhook subscriptions and terminal-run fanout.

Only identifiers leave this service. Successful workflow completion is a receipt,
not evidence that the remote agent has processed it or produced a reply.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tinyassets.mcp_event_webhooks import (
    EventError,
    callback_url,
    canonical,
    post,
    signed_headers,
    signing_key,
    verify,
)

NAME = "workflow.succeeded"
MAX_TTL = 86400
MAX_ATTEMPTS = 8
DELIVERY_TTL = 86400
VERIFICATION_LIMIT = 32
VERIFICATION_WINDOW = 60
logger = logging.getLogger(__name__)
_IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 256}
_FILTER = {
    "type": "object",
    "maxProperties": 16,
    "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
}
DEFINITION = {
    "name": NAME,
    "delivery": ["webhook"],
    "description": "An owned workflow completed successfully. Select an exact command center "
    "and workflow, and optional exact input/output fields (e.g. a successful inbox-save "
    "receipt). Replies are suppressed. Contains IDs only; read authorized run/inbox data "
    "with read_graph. Delivery acknowledges receipt, not an agent response.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "command_center_id": _IDENTIFIER,
            "branch_def_id": _IDENTIFIER,
            "input_equals": _FILTER,
            "output_equals": _FILTER,
        },
        "required": ["command_center_id", "branch_def_id"],
        "additionalProperties": False,
    },
    "payloadSchema": {
        "type": "object",
        "properties": {k: _IDENTIFIER for k in ("command_center_id", "branch_def_id", "run_id")},
        "required": ["command_center_id", "branch_def_id", "run_id"],
        "additionalProperties": False,
    },
}


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _scalar_matches(actual, expected):
    if not isinstance(actual, (str, int, float, bool, type(None))):
        return False
    try:
        return canonical(actual) == canonical(expected)
    except (ValueError, TypeError):
        return False


def arguments(value):
    if not isinstance(value, dict) or set(value) - {
        "command_center_id",
        "branch_def_id",
        "input_equals",
        "output_equals",
    }:
        raise EventError(-32602, "Invalid event arguments")
    for field in ("command_center_id", "branch_def_id"):
        if not isinstance(value.get(field), str) or not 1 <= len(value[field]) <= 256:
            raise EventError(-32602, "Exact command center and workflow IDs are required")
    for field in ("input_equals", "output_equals"):
        filters = value.get(field, {})
        if (
            not isinstance(filters, dict)
            or len(filters) > 16
            or any(
                not isinstance(k, str)
                or not 1 <= len(k) <= 128
                or not isinstance(v, (str, int, float, bool, type(None)))
                for k, v in filters.items()
            )
        ):
            raise EventError(-32602, "Filters must contain at most 16 scalar equality fields")
    try:
        if len(canonical(value).encode()) > 8192:
            raise ValueError
    except ValueError:
        raise EventError(-32602, "Event arguments exceed bounds") from None
    return value


def authorize(base, owner, args, authority=None):
    """Re-evaluate the persisted owner and exact resource grant; never use a fallback actor."""
    from tinyassets.auth.provider import Identity
    from tinyassets.branch_versions import branch_readable_by
    from tinyassets.daemon_server import (
        get_branch_definition,
        universe_access_permission,
        universe_is_private,
    )
    from tinyassets.outside_authority import OutsideClientAuthority
    from tinyassets.principals import named_principal
    from tinyassets.universe_owner import owner_of

    if not named_principal(owner) or owner.startswith("universe:"):
        raise PermissionError("Named owner required")
    uid = args["command_center_id"]
    if owner_of(base, uid) != owner or (
        universe_is_private(base, universe_id=uid)
        and universe_access_permission(base, universe_id=uid, actor_id=owner) != "admin"
    ):
        raise PermissionError("Owned command center required")
    branch = get_branch_definition(base, branch_def_id=args["branch_def_id"])
    if not branch_readable_by(
        owner, author=branch.get("author"), visibility=branch.get("visibility")
    ):
        raise PermissionError("Workflow unavailable")
    if authority:
        identity = Identity(owner, owner, metadata={"outside_origin": authority})
        OutsideClientAuthority(base).admit(
            identity.user_id, authority, universe=uid, agent="main", capability="read_graph"
        )


def _key():
    from tinyassets.onboarding.session_store import durable_seal_key

    return hmac.digest(durable_seal_key(), b"tinyassets:mcp-events:v1", "sha256")


def db_path(base):
    return Path(base) / ".runtime" / "mcp-events.sqlite3"


class EventStore:
    def __init__(
        self, base, *, key_provider=_key, authorizer=authorize, sender=post, clock=time.time
    ):
        self.base = Path(base)
        self.path = db_path(base)
        self.key_provider, self.authorizer = key_provider, authorizer
        self.sender, self.clock = sender, clock

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, arguments TEXT NOT NULL,
                    sealed BLOB NOT NULL, active INTEGER NOT NULL, starts REAL NOT NULL,
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

    def _seal(self, owner, ident, value):
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self.key_provider()).encrypt(
            nonce, canonical(value).encode(), canonical([owner, ident]).encode()
        )

    def _open(self, row):
        sealed = row["sealed"]
        return json.loads(
            AESGCM(self.key_provider()).decrypt(
                sealed[:12], sealed[12:], canonical([row["owner"], row["id"]]).encode()
            )
        )

    def _identity(self, owner, params):
        if params.get("name") != NAME:
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

    def subscribe(self, owner, params, *, authority=None):
        ident, args, url = self._identity(owner, params)
        secret = params["delivery"].get("secret")
        signing_key(secret)
        if params.get("cursor") is not None:
            raise EventError(-32602, "This event does not support replay")
        ttl = params.get("ttlMs", MAX_TTL * 1000)
        if ttl is not None and (type(ttl) is not int or ttl <= 0):
            raise EventError(-32602, "ttlMs must be a positive integer or null")
        lifetime = min(MAX_TTL * 1000, ttl) / 1000 if ttl is not None else MAX_TTL
        self.authorizer(self.base, owner, args, authority)
        self.key_provider()  # Refuse before callback I/O if restart persistence is unavailable.
        now = self.clock()
        with self.db() as conn:
            previous = conn.execute(
                "SELECT * FROM subscriptions WHERE id=? AND owner=?", (ident, owner)
            ).fetchone()
            if (
                not (previous and previous["active"] and previous["expires"] > now)
                and conn.execute(
                    "SELECT COUNT(*) FROM subscriptions WHERE owner=? AND active=1 AND expires>?",
                    (owner, now),
                ).fetchone()[0]
                >= 128
            ):
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
            verify(ident, url, secret, sender=self.sender)
        else:
            verified = previous["verified"]
        self.authorizer(self.base, owner, args, authority)  # Grants can change during verification.
        config = {"url": url, "secret": secret, "arguments": args, "authority": authority}
        if old and previous["expires"] > now and old["secret"] != secret:
            config.update(old_secret=old["secret"], rotation_until=now + 120)
        elif old and old.get("rotation_until", 0) > now:
            config.update(old_secret=old["old_secret"], rotation_until=old["rotation_until"])
        sealed = self._seal(owner, ident, config)
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                not (previous and previous["active"] and previous["expires"] > now)
                and conn.execute(
                    "SELECT COUNT(*) FROM subscriptions WHERE owner=? AND active=1 AND expires>?",
                    (owner, now),
                ).fetchone()[0]
                >= 128
            ):
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
                    sealed,
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

    @staticmethod
    def _revoke(conn, ident, owner):
        conn.execute(
            "UPDATE subscriptions SET active=0,sealed=X'',revision=revision+1 "
            "WHERE id=? AND owner=?",
            (ident, owner),
        )
        conn.execute(
            "UPDATE deliveries SET state='stopped' WHERE subscription=? "
            "AND state IN ('pending','sending')",
            (ident,),
        )

    def capture(self, *, owner, universe, branch, run_id, event_id, occurred, inputs, outputs):
        if not self.path.exists():
            return
        if (
            not owner
            or not universe
            or not isinstance(inputs, dict)
            or not isinstance(outputs, dict)
            or inputs.get("operation") == "reply"
            or isinstance(inputs.get("data"), dict)
            and inputs["data"].get("operation") == "reply"
        ):
            return
        now = self.clock()
        if occurred < now - DELIVERY_TTL:
            return
        body = canonical(
            {
                "eventId": "evt_"
                + hashlib.sha256(canonical([owner, universe, event_id]).encode()).hexdigest(),
                "name": NAME,
                "timestamp": _iso(occurred),
                "cursor": None,
                "data": {"command_center_id": universe, "branch_def_id": branch, "run_id": run_id},
            }
        )
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM subscriptions WHERE owner=? AND active=1 "
                "AND starts<=? AND expires>?",
                (owner, occurred, now),
            ).fetchall()
            for row in rows:
                scope = json.loads(row["arguments"])
                if scope != {"command_center_id": universe, "branch_def_id": branch}:
                    continue
                try:
                    config = self._open(row)
                except (InvalidTag, ValueError):
                    self._revoke(conn, row["id"], owner)
                    continue
                args = config["arguments"]
                try:
                    self.authorizer(self.base, owner, args, config["authority"])
                except (PermissionError, KeyError):
                    self._revoke(conn, row["id"], owner)
                    continue
                if not all(
                    k in actual and _scalar_matches(actual[k], v)
                    for field, actual in (("input_equals", inputs), ("output_equals", outputs))
                    for k, v in args.get(field, {}).items()
                ):
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO deliveries "
                    "(subscription,event_id,body,created,due) VALUES (?,?,?,?,?)",
                    (row["id"], json.loads(body)["eventId"], body, occurred, now),
                )

    def pump(self, limit=32):
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
            rows = conn.execute(
                "SELECT subscription,event_id FROM deliveries "
                "WHERE state IN ('pending','sending') AND due<=? "
                "ORDER BY due LIMIT ?",
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
            self.authorizer(self.base, row["owner"], config["arguments"], config["authority"])
            with self.db() as conn:
                live = conn.execute(
                    "SELECT 1 FROM subscriptions WHERE id=? AND active=1 "
                    "AND revision=? AND expires>?",
                    (ident, row["revision"], self.clock()),
                ).fetchone()
            if not live:
                return  # A refresh/revoke invalidated this claim; never send a stale secret.
            body = delivery["body"].encode()
            old = config.get("old_secret") if config.get("rotation_until", 0) > now else None
            status, _ = self.sender(
                config["url"],
                body,
                signed_headers(
                    ident, event, body, config["secret"], old_secret=old, now=self.clock()
                ),
            )
        except (PermissionError, KeyError):
            with self.db() as conn:
                self._revoke(conn, ident, row["owner"])
            return
        except Exception:
            # Redact all remote errors, secrets and callback addresses.
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


def capture_terminal(base, row):
    """Called before acknowledging the existing terminal outbox; errors keep it owed."""
    if row["status"] != "completed" or not db_path(base).exists():
        return
    universe = str(row["queue_universe_id"] or "")
    actor = str(row["actor"] or "")
    if actor.startswith("universe:"):
        universe = actor[len("universe:") :]
    EventStore(base).capture(
        owner=str(row["cause_principal"] or ""),
        universe=universe,
        branch=row["branch_def_id"],
        run_id=row["run_id"],
        event_id=f"{row['run_id']}#{row['seq']}",
        occurred=row["created_at"],
        inputs=json.loads(row["inputs_json"]),
        outputs=json.loads(row["output_json"]),
    )
