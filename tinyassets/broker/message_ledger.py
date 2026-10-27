"""The trusted message ledger (ADR-025, S30): who sent what to whom, and who read it.

One broker-private SQLite per owner beside ``policy.db``. Only the broker opens
it, and only the router's owner channel reaches it (``OUTSIDE_AGENTS``,
``ROOM_POST``, ``MAILBOX_RECEIPT``); the box channel has no ledger op. A sender
is the router-verified identity's ``outside_origin`` (none means the owner),
never a field of the request. Bodies live in ``conversations.py``; this ledger
keeps each body's hash, so a tampered body is detected on read.

Wakes (S3b, W3) are data: ``ROOM_POST`` opens or extends one ``ledger_wakes`` row per
recipient whose delivery method rings (``mcp_event``); ``agent_wake`` (AGENT_WAKE) sends
it on the broker's tick through the op journal, so an ambiguous send is never replayed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from tinyassets.broker.policy import account_path, erase_account_db, open_account_db

_LOG = logging.getLogger(__name__)
FILENAME = "message_ledger.db"
DELIVERY_METHODS = ("mcp_event", "routine_fire", "webhook", "a2a", "pull")
#: S3b admits the ``mailbox.message`` doorbell beside pull. Routine and webhook wakes wait
#: for the credential-vault reshape (S9, decided 2026-10-25); a2a for its own slice.
ADMITTED_DELIVERY = frozenset({"pull", "mcp_event"})
#: The admitted methods that ring; a pull agent is never woken (W3).
WAKING = frozenset({"mcp_event"})
#: A claimed send's lease: past it, the op journal decides whether a byte left.
WAKE_LEASE_S = 60
_OPEN = "state IN ('pending','rate_limited')"
SUPPRESSED = "suppressed"
#: The receipt each settled wake leaves; ``wake_sent`` alone means the outcome is unknown.
_WAKE_RECEIPTS = {"sent": "wake_accepted", "rate_limited": "rate_limited",
                  "failed": "failed", "unknown": "wake_sent"}
OPS = {"OUTSIDE_AGENTS": ("list", "register", "disable"), "ROOM_POST": ("post",),
       "MAILBOX_RECEIPT": ("read", "ack", "board")}
#: The board's receipts (S5b): a claim and a completion, mirrored by ``board_door``.
BOARD_RECEIPTS = frozenset({"claim", "complete"})
OWNER = "owner"
MAX_READ = 100
_HEX64 = re.compile(r"[0-9a-f]{64}")
_KEY_ID = re.compile(r"[0-9a-f]{16}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS outside_agents(
  agent_id TEXT PRIMARY KEY, command_center_id TEXT NOT NULL,
  outside_client TEXT NOT NULL, outside_family TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN('api_key','mcp')), source_ref TEXT NOT NULL,
  display_name TEXT NOT NULL,
  delivery_method TEXT NOT NULL
    CHECK(delivery_method IN('mcp_event','routine_fire','webhook','a2a','pull')),
  recipe_id TEXT, revision INTEGER NOT NULL, disabled_reason TEXT, created_at REAL NOT NULL,
  UNIQUE(command_center_id, outside_client, outside_family));
CREATE TABLE IF NOT EXISTS ledger_messages(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL, command_center_id TEXT NOT NULL, room_id TEXT NOT NULL,
  sender_kind TEXT NOT NULL CHECK(sender_kind IN('owner','native','outside')),
  sender_id TEXT NOT NULL, client_message_id TEXT NOT NULL, body_sha256 TEXT NOT NULL,
  parent_id TEXT, chain_root TEXT NOT NULL, chain_depth INTEGER NOT NULL,
  created_at REAL NOT NULL, UNIQUE(owner, sender_id, client_message_id));
CREATE TABLE IF NOT EXISTS ledger_recipients(
  message_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  state TEXT NOT NULL
    CHECK(state IN('queued','held_cap','woken','read','replied','failed','unknown')),
  changed_at REAL NOT NULL, PRIMARY KEY(message_id, agent_id));
CREATE TABLE IF NOT EXISTS ledger_receipts(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, agent_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('wake_sent','wake_accepted','read','ack','reply',
                                   'rate_limited','failed','claim','complete')),
  ref TEXT NOT NULL, at REAL NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS ledger_cursors(
  reader TEXT PRIMARY KEY, through_seq INTEGER NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_wakes(
  wake_id TEXT PRIMARY KEY, owner TEXT NOT NULL, agent_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN('pending','sending','sent','unknown','rate_limited',
                                     'failed','suppressed')),
  due REAL NOT NULL, op_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, receipt_json TEXT,
  created_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_wake ON ledger_wakes(owner, agent_id)
  WHERE state IN('pending','rate_limited');
CREATE INDEX IF NOT EXISTS wakes_due ON ledger_wakes(state, due);
"""


class LedgerRefused(PermissionError):
    """A refusal whose reason the router may show the caller."""


@dataclass(frozen=True, slots=True)
class Wake:
    owner: str
    wake_id: str
    agent_id: str
    state: str
    due: float
    op_id: str | None


def erase(data_root: Path, owner: str) -> int:
    return erase_account_db(account_path(data_root, owner, FILENAME))


def _text(value, limit: int, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or not value.isprintable():
        raise ValueError(f"{name} is 1 to {limit} printable characters")
    return value


class MessageLedger:
    def __init__(self, data_root: Path, admissions) -> None:
        self._root = Path(data_root)
        self._admissions = admissions
        self._locks: dict[str, threading.Lock] = {}
        self._wake_turn = 0  # where the next wake pass starts among owners (due_wakes)

    def _open(self, owner: str):
        db = open_account_db(account_path(self._root, owner, FILENAME), self._admissions, owner)
        db.executescript(SCHEMA)
        return closing(db)

    @contextmanager
    def _write(self, owner: str):
        """One owner's writes in order, each one transaction; a raise rolls it back."""
        with self._locks.setdefault(owner, threading.Lock()), self._open(owner) as db:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")

    # -- the agent registry -------------------------------------------------

    def agents(self, owner: str) -> list[dict]:
        with self._open(owner) as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM outside_agents ORDER BY created_at, agent_id")]

    def register(self, owner: str, *, client: str, family: str, command_center_id: str,
                 source: str, source_ref: str, display_name: str, delivery_method: str,
                 recipe_id: str | None) -> dict:
        from tinyassets.connected_agents import display_name as sanitized

        _text(command_center_id, 512, "command_center_id")
        _text(source_ref, 512, "source_ref")
        if recipe_id is not None:
            _text(recipe_id, 128, "recipe_id")
        name = sanitized(display_name)
        if not name:
            raise ValueError("display_name is required: letters, digits, spaces, dots, dashes")
        if delivery_method not in DELIVERY_METHODS:
            raise ValueError("unknown delivery method")
        if delivery_method not in ADMITTED_DELIVERY:
            raise LedgerRefused("only pull and mcp_event delivery are available yet")
        if source == "api_key" and delivery_method in WAKING:
            raise LedgerRefused("an API-key agent reads by pull; mcp_event is for MCP clients")
        with self._write(owner) as db:
            row = db.execute("SELECT agent_id FROM outside_agents WHERE command_center_id=? "
                             "AND outside_client=? AND outside_family=?",
                             (command_center_id, client, family)).fetchone()
            fields = (source, source_ref, name, delivery_method, recipe_id)
            # One outside identity is one enabled agent; a second would lock both out.
            if db.execute("SELECT 1 FROM outside_agents WHERE outside_client=? AND "
                          "outside_family=? AND disabled_reason IS NULL AND command_center_id!=?",
                          (client, family, command_center_id)).fetchone():
                raise LedgerRefused("this outside identity is already an enabled agent")
            if row is None:
                agent_id = "agent_" + secrets.token_hex(8)
                db.execute("INSERT INTO outside_agents(agent_id,command_center_id,outside_client,"
                           "outside_family,source,source_ref,display_name,delivery_method,"
                           "recipe_id,revision,created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
                           (agent_id, command_center_id, client, family, *fields, time.time()))
            else:  # The same outside identity again: re-enabled, one revision on.
                agent_id = row[0]
                db.execute("UPDATE outside_agents SET source=?,source_ref=?,display_name=?,"
                           "delivery_method=?,recipe_id=?,disabled_reason=NULL,"
                           "revision=revision+1 WHERE agent_id=?", (*fields, agent_id))
            if delivery_method not in WAKING:
                self._suppress(db, owner, agent_id, "delivery_method")
            return dict(db.execute("SELECT * FROM outside_agents WHERE agent_id=?",
                                   (agent_id,)).fetchone())

    def disable(self, owner: str, *, agent_id: str, reason: str) -> dict:
        _text(reason, 200, "reason")
        with self._write(owner) as db:
            if not db.execute("UPDATE outside_agents SET disabled_reason=?,revision=revision+1 "
                              "WHERE agent_id=?", (reason, agent_id)).rowcount:
                raise LedgerRefused("agent not found")
            self._suppress(db, owner, agent_id, "disabled")
            return dict(db.execute("SELECT * FROM outside_agents WHERE agent_id=?",
                                   (agent_id,)).fetchone())

    @staticmethod
    def _reader(db, origin: dict | None) -> str:
        if origin is None:
            return OWNER
        rows = db.execute("SELECT agent_id FROM outside_agents WHERE outside_client=? AND "
                          "outside_family=? AND disabled_reason IS NULL",
                          (origin["client"], origin["family"])).fetchall()
        if len(rows) != 1:
            raise LedgerRefused("this outside identity is not one enabled agent")
        return rows[0][0]

    @staticmethod
    def _receipt(db, owner, agent_id, kind, ref, at, detail=None) -> None:
        db.execute("INSERT INTO ledger_receipts(owner,agent_id,kind,ref,at,detail_json) "
                   "VALUES (?,?,?,?,?,?)", (owner, agent_id, kind, ref, at,
                                            json.dumps(detail or {}, sort_keys=True)))

    # -- ROOM_POST (the 1:1 subset) -----------------------------------------

    def post(self, owner: str, *, origin: dict | None, room_id: str, client_message_id: str,
             body: str, parent_id: str | None, put_body) -> dict:
        _text(room_id, 128, "room_id")
        _text(client_message_id, 128, "client_message_id")
        if parent_id is not None:
            _text(parent_id, 128, "parent_id")
        if not isinstance(body, str):
            raise ValueError("body is text")
        sha = hashlib.sha256(body.encode()).hexdigest()
        with self._write(owner) as db:
            agent = db.execute("SELECT * FROM outside_agents WHERE agent_id=? AND "
                               "disabled_reason IS NULL", (room_id.removeprefix("dm:"),)
                               ).fetchone() if room_id.startswith("dm:") else None
            if agent is None:
                raise LedgerRefused("room is not an enabled agent's dm")
            if origin is None:
                kind, sender, recipient = OWNER, OWNER, agent["agent_id"]
            elif (origin["client"], origin["family"]) == (agent["outside_client"],
                                                          agent["outside_family"]):
                kind, sender, recipient = "outside", agent["agent_id"], OWNER
            else:
                raise LedgerRefused("an outside agent posts only in its own dm room")
            same = db.execute("SELECT * FROM ledger_messages WHERE owner=? AND sender_id=? "
                              "AND client_message_id=?",
                              (owner, sender, client_message_id)).fetchone()
            if same is not None:
                if (same["body_sha256"], same["room_id"], same["parent_id"]) != (
                        sha, room_id, parent_id):
                    raise LedgerRefused("conflict")
                return {"message_id": same["message_id"], "held": False, "paused_reason": "",
                        "recipients": [r[0] for r in db.execute(
                            "SELECT agent_id FROM ledger_recipients WHERE message_id=? "
                            "ORDER BY agent_id", (same["message_id"],))]}
            parent = None
            if parent_id is not None:
                parent = db.execute(
                    "SELECT m.* FROM ledger_messages m JOIN ledger_recipients r ON "
                    "r.message_id=m.message_id AND r.agent_id=? WHERE m.message_id=? "
                    "AND m.room_id=?", (sender, parent_id, room_id)).fetchone()
                if parent is None:
                    raise LedgerRefused("parent is not a message to this sender in this room")
            now = time.time()
            message_id = "msg_" + secrets.token_hex(16)
            depth = 0 if kind == OWNER else (parent["chain_depth"] if parent else 0) + 1
            put_body(message_id, room_id, body)  # body first; this commit is the truth
            db.execute("INSERT INTO ledger_messages(message_id,owner,command_center_id,room_id,"
                       "sender_kind,sender_id,client_message_id,body_sha256,parent_id,"
                       "chain_root,chain_depth,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (message_id, owner, agent["command_center_id"], room_id, kind, sender,
                        client_message_id, sha, parent_id,
                        parent["chain_root"] if parent else message_id, depth, now))
            db.execute("INSERT INTO ledger_recipients VALUES (?,?,'queued',?)",
                       (message_id, recipient, now))
            # One open wake per ringing recipient; never one for the message's own sender.
            if agent["delivery_method"] in WAKING and recipient == agent["agent_id"] != sender:
                db.execute("INSERT INTO ledger_wakes(wake_id,owner,agent_id,state,due,created_at)"
                           " SELECT ?,?,?,'pending',?,? WHERE NOT EXISTS (SELECT 1 FROM "
                           f"ledger_wakes WHERE owner=? AND agent_id=? AND {_OPEN})",
                           ("wake_" + secrets.token_hex(16), owner, recipient, now, now, owner,
                            recipient))
            if parent is not None:
                db.execute("UPDATE ledger_recipients SET state='replied',changed_at=? "
                           "WHERE message_id=? AND agent_id=?", (now, parent_id, sender))
                read = db.execute("SELECT MIN(at) FROM ledger_receipts WHERE agent_id=? AND "
                                  "kind='read' AND ref=?", (sender, parent_id)).fetchone()[0]
                self._receipt(db, owner, sender, "reply", message_id, now, {
                    "parent_id": parent_id, "reply_s": round(now - parent["created_at"], 3),
                    "read_s": None if read is None else round(read - parent["created_at"], 3)})
            return {"message_id": message_id, "recipients": [recipient], "held": False,
                    "paused_reason": ""}

    # -- wakes (S3b) ----------------------------------------------------------

    @staticmethod
    def _suppress(db, owner, agent_id, reason) -> None:
        db.execute(f"UPDATE ledger_wakes SET state='suppressed',receipt_json=? WHERE owner=? "
                   f"AND agent_id=? AND {_OPEN}", (json.dumps({"reason": reason}), owner,
                                                   agent_id))

    def mailbox_subscriber(self, owner: str, *, origin: dict, command_center_id: str,
                           agent_id: str) -> None:
        """W4: a ``mailbox.message`` subscribe names the caller's own ringing agent."""
        with self._open(owner) as db:
            if db.execute(
                    "SELECT 1 FROM outside_agents WHERE agent_id=? AND command_center_id=? AND "
                    "outside_client=? AND outside_family=? AND disabled_reason IS NULL AND "
                    "delivery_method='mcp_event'", (agent_id, command_center_id,
                                                    origin["client"], origin["family"])
            ).fetchone() is None:
                raise LedgerRefused("not this caller's mcp_event agent")

    def due_wakes(self, now: float, *, limit: int) -> list[Wake]:
        """At most one due wake per owner, its oldest, from up to ``limit`` owners.

        Each pass starts after the last owner the previous pass looked at, so an owner
        with many agents can never crowd another out. A send past its lease is due
        again for recovery.
        """
        from tinyassets.broker.admission import read

        paths = sorted((self._root / ".broker" / "accounts").glob("*/" + FILENAME))
        start = self._wake_turn % len(paths) if paths else 0
        found: list[Wake] = []
        for looked, path in enumerate(paths[start:] + paths[:start], 1):
            self._wake_turn = start + looked
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as probe:
                    row = probe.execute(
                        "SELECT owner,wake_id,agent_id,state,due,op_id FROM ledger_wakes WHERE "
                        f"({_OPEN} OR state='sending') AND due<=? ORDER BY due LIMIT 1",
                        (now,)).fetchone()
                if row is None or account_path(self._root, row[0], FILENAME) != path:
                    continue
                read(self._admissions, row[0]).require_work()
            except (sqlite3.Error, PermissionError, LookupError, ValueError):
                continue  # a ledger from before wakes, or an owner not admitted now
            found.append(Wake(*row))
            if len(found) >= limit:
                break
        return found

    def claim_wake(self, owner: str, wake_id: str, *, op_id: str,
                   now: float) -> dict | str | None:
        """Before any byte: the claimed agent's IDs and mailbox cursor, or None.

        A wake whose agent is disabled or no longer rings is suppressed here instead,
        and ``SUPPRESSED`` tells AGENT_WAKE to revoke that agent's callbacks.
        """
        with self._write(owner) as db:
            wake = db.execute(f"SELECT * FROM ledger_wakes WHERE wake_id=? AND {_OPEN} AND "
                              "due<=?", (wake_id, now)).fetchone()
            if wake is None:
                return None
            agent = db.execute("SELECT * FROM outside_agents WHERE agent_id=?",
                               (wake["agent_id"],)).fetchone()
            if agent is None or agent["disabled_reason"] or agent["delivery_method"] not in WAKING:
                self._suppress(db, owner, wake["agent_id"], "agent")
                return SUPPRESSED
            cursor = db.execute("SELECT through_seq FROM ledger_cursors WHERE reader=?",
                                (agent["agent_id"],)).fetchone()
            through = db.execute(
                "SELECT COALESCE(MAX(m.seq),0) FROM ledger_messages m JOIN ledger_recipients r "
                "ON r.message_id=m.message_id AND r.agent_id=?", (agent["agent_id"],)).fetchone()
            db.execute("UPDATE ledger_wakes SET state='sending',op_id=?,attempts=attempts+1,"
                       "due=? WHERE wake_id=?", (op_id, now + WAKE_LEASE_S, wake_id))
            return {"agent_id": agent["agent_id"], "command_center_id": agent[
                "command_center_id"], "client": agent["outside_client"], "family": agent[
                "outside_family"], "cursor": cursor[0] if cursor else 0, "through": through[0]}

    def settle_wake(self, owner: str, wake_id: str, state: str, *, now: float,
                    detail: dict) -> None:
        """A claimed wake's outcome, its receipt, and its messages ``woken`` once accepted."""
        with self._write(owner) as db:
            wake = db.execute("SELECT * FROM ledger_wakes WHERE wake_id=? AND state='sending'",
                              (wake_id,)).fetchone()
            if wake is None:
                return
            kind, due = _WAKE_RECEIPTS.get(state), now + detail.get("retry_after", 0)
            # A message posted meanwhile opened the agent's next wake: fold into that one,
            # which then waits out any Retry-After.
            if state in ("pending", "rate_limited") and db.execute(
                    "UPDATE ledger_wakes SET due=MAX(due,?),state=CASE WHEN ?='rate_limited' "
                    f"THEN 'rate_limited' ELSE state END WHERE owner=? AND agent_id=? AND {_OPEN}",
                    (due, state, owner, wake["agent_id"])).rowcount:
                state, detail = "suppressed", {**detail, "folded": True}
            db.execute("UPDATE ledger_wakes SET state=?,due=?,receipt_json=? WHERE wake_id=?",
                       (state, due, json.dumps(detail, sort_keys=True), wake_id))
            if state == "sent":
                db.execute("UPDATE ledger_recipients SET state='woken',changed_at=? WHERE "
                           "agent_id=? AND state='queued' AND message_id IN (SELECT message_id "
                           "FROM ledger_messages WHERE seq<=?)",
                           (now, wake["agent_id"], detail["through"]))
            if kind:
                self._receipt(db, owner, wake["agent_id"], kind, wake_id, now, detail)

    # -- MAILBOX_RECEIPT ----------------------------------------------------

    def read(self, owner: str, *, origin: dict | None, limit: int, bodies,
             max_bytes: int | None = None, pad_bytes: int = 0) -> dict:
        """Messages to this reader past its cursor; a reread returns the same message_ids.

        ``max_bytes`` bounds the page: whole messages (each measured as returned,
        plus ``pad_bytes`` for the caller's own wrapping) while they fit, and at
        least one; a body is never cut. Only a returned message is marked read and
        receipted, so a held-back one stays unread until a read returns it.
        """
        if type(limit) is not int or not 0 < limit <= MAX_READ:
            raise ValueError(f"limit is 1 to {MAX_READ}")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ValueError("max_bytes is a positive integer")
        if type(pad_bytes) is not int or pad_bytes < 0:
            raise ValueError("pad_bytes is a non-negative integer")
        keep = ("seq", "message_id", "room_id", "sender_kind", "sender_id", "parent_id",
                "chain_depth", "created_at")
        with self._write(owner) as db:
            reader = self._reader(db, origin)
            cursor = db.execute("SELECT through_seq FROM ledger_cursors WHERE reader=?",
                                (reader,)).fetchone()
            cursor = cursor[0] if cursor else 0
            rows = db.execute(
                "SELECT m.*, r.state FROM ledger_messages m JOIN ledger_recipients r ON "
                "r.message_id=m.message_id AND r.agent_id=? WHERE m.seq>? ORDER BY m.seq "
                "LIMIT ?", (reader, cursor, limit)).fetchall()
            texts = bodies({row["message_id"]: row["body_sha256"] for row in rows})
            # A reply's own receipt already holds its time to reply; show it, never recompute.
            replied = {ref: json.loads(detail)["reply_s"] for ref, detail in db.execute(
                "SELECT ref, detail_json FROM ledger_receipts WHERE kind='reply' AND ref IN "
                f"({','.join('?' * len(rows))})", [row["message_id"] for row in rows])}
            messages, used, now = [], 0, time.time()
            for row in rows:
                message = {**{k: row[k] for k in keep}, "reply_s": replied.get(
                    row["message_id"]), **texts[row["message_id"]]}
                used += pad_bytes + len(json.dumps(message, separators=(",", ":"), default=str,
                                                   ensure_ascii=False).encode())
                if messages and max_bytes is not None and used > max_bytes:
                    break
                messages.append(message)
                if row["state"] in ("queued", "woken"):
                    db.execute("UPDATE ledger_recipients SET state='read',changed_at=? "
                               "WHERE message_id=? AND agent_id=?",
                               (now, row["message_id"], reader))
                    self._receipt(db, owner, reader, "read", row["message_id"], now)
        return {"reader": reader, "cursor": cursor, "messages": messages,
                "more": len(messages) < len(rows)}

    def ack(self, owner: str, *, origin: dict | None, through: int) -> dict:
        if type(through) is not int or through < 0:
            raise ValueError("through is a ledger seq")
        with self._write(owner) as db:
            reader = self._reader(db, origin)
            # Bounded by this reader's own newest message: never another reader's count.
            if through > db.execute(
                    "SELECT COALESCE(MAX(m.seq),0) FROM ledger_messages m JOIN ledger_recipients "
                    "r ON r.message_id=m.message_id AND r.agent_id=?", (reader,)).fetchone()[0]:
                raise LedgerRefused("ack is past this reader's mail")
            now = time.time()
            db.execute("INSERT INTO ledger_cursors VALUES (?,?,?) ON CONFLICT(reader) DO UPDATE "
                       "SET through_seq=MAX(through_seq, excluded.through_seq), at=excluded.at",
                       (reader, through, now))
            through = db.execute("SELECT through_seq FROM ledger_cursors WHERE reader=?",
                                 (reader,)).fetchone()[0]
            self._receipt(db, owner, reader, "ack", str(through), now)
            return {"reader": reader, "through": through}

    def board_receipt(self, owner: str, *, origin: dict | None, kind: str, task_id: str,
                      revision: int) -> dict:
        """A board claim or completion, in the verified actor's name (``owner`` or its agent)."""
        if kind not in BOARD_RECEIPTS:
            raise ValueError("kind is claim or complete")
        _text(task_id, 64, "task_id")
        if type(revision) is not int or revision < 1:
            raise ValueError("revision is a task revision")
        with self._write(owner) as db:
            actor, now = self._reader(db, origin), time.time()
            self._receipt(db, owner, actor, kind, task_id, now, {"revision": revision})
            # "actor", never "agent_id": the server reads an agent_id result as a registry change.
            return {"actor": actor, "kind": kind, "ref": task_id, "at": now}


def agent_wake(ledger: MessageLedger, events, ops, wake: Wake, *, now: float) -> str:
    """AGENT_WAKE (W3): one wake through broker egress, under the op journal (``ops.py``).

    may-have-sent is durable before the first byte, so a send abandoned past its lease
    is ``unknown`` unless the journal proves no byte left, and an unknown wake is never
    replayed: the agent's next message opens its next wake. A 429 waits out Retry-After.
    """
    from tinyassets.broker.ops import OpRecord, new_op_id

    namespace = "agent_wake:" + wake.owner
    if wake.state == "sending":
        record = ops.status(namespace, wake.op_id) if wake.op_id else "not_found"
        unsent = record == "not_found" or isinstance(record, OpRecord) and not record.sent
        state = "pending" if unsent else "unknown"
        ledger.settle_wake(wake.owner, wake.wake_id, state, now=now, detail={"recovered": True})
        return state
    op_id = new_op_id()
    if ops.admit(namespace, op_id, hashlib.sha256(wake.wake_id.encode()).hexdigest()
                 ).kind != "new":
        raise RuntimeError("the op journal refused the wake")
    target = ledger.claim_wake(wake.owner, wake.wake_id, op_id=op_id, now=now)
    if not isinstance(target, dict):
        ops.finish(namespace, op_id, "refused")
        if target == SUPPRESSED:  # disabled or off mcp_event: no callback may outlive it
            events.revoke_agent(wake.owner, wake.agent_id)
        return SUPPRESSED
    state, detail = events.send_wake(wake.owner, target, wake.wake_id,
                                     mark=partial(ops.mark_may_have_sent, namespace, op_id))
    ledger.settle_wake(wake.owner, wake.wake_id, state, now=now,
                       detail={**detail, "through": target["through"]})
    ops.finish(namespace, op_id, {"sent": "completed", "suppressed": "refused"}.get(
        state, "failed"))
    return state


def _origin(value) -> dict | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"client", "family"}
            or not all(isinstance(v, str) and _HEX64.fullmatch(v) for v in value.values())):
        raise ValueError("invalid outside origin")
    return value


def _registrant(source, source_ref, origin) -> tuple[str, str]:
    """The registered identity, as the router verified it.

    An MCP agent's is its verified ``outside_origin``. An API-key agent's is the key's
    own origin: the router, which alone holds the key store, checked that the key is
    the owner's live key (``api_keys.agent_origin``). Nothing here reads that store.
    """
    if source not in {"mcp", "api_key"}:
        raise ValueError("source is api_key or mcp")
    if origin is None:  # MCP: P0-3 lifts this once the outside-client claim is configured.
        raise LedgerRefused("an agent registers only from its verified outside identity")
    if source == "api_key":
        from tinyassets.api_keys import _client, _family

        if not isinstance(source_ref, str) or not _KEY_ID.fullmatch(source_ref):
            raise ValueError("an api_key source_ref is the key id")
        if (origin["client"], origin["family"]) != (_client(source_ref), _family(source_ref)):
            raise LedgerRefused("the origin is not this API key's")
    return origin["client"], origin["family"]


def router_operation(ledger: MessageLedger, conversations, sessions, doc: dict) -> dict:
    """The router's verified caller on the owner channel. The owner and the sender come
    from its identity (``principal``, ``origin``), never from the request's fields."""
    from tinyassets.broker.admission import require_admitted
    from tinyassets.broker.owner_identities import validate_principal

    op, action = doc.get("op"), doc.get("action")
    if action not in OPS.get(op, ()):
        raise ValueError("unsupported ledger operation")
    owner, origin = doc.get("principal"), _origin(doc.get("origin"))
    validate_principal(owner)
    require_admitted(ledger._admissions, owner)
    fields = {k: v for k, v in doc.items() if k not in {"op", "action", "principal", "origin"}}

    def exactly(*names):
        if set(fields) != set(names):
            raise ValueError("invalid ledger request")

    if op == "OUTSIDE_AGENTS" and action == "list":
        exactly()
        return {"agents": ledger.agents(owner)}
    if op == "OUTSIDE_AGENTS":
        proof = fields.pop("session_proof", None)
        try:
            if not isinstance(proof, dict) or sessions is None:
                raise LedgerRefused("interactive owner session required")
            sessions._session(owner, proof)  # the live router row, as policy writes check it
            if action == "disable":
                exactly("agent_id", "reason")
                return ledger.disable(owner, **fields)
            exactly("command_center_id", "source", "source_ref", "display_name",
                    "delivery_method", "recipe_id")
            client, family = _registrant(fields["source"], fields["source_ref"], origin)
            return ledger.register(owner, client=client, family=family, **fields)
        except (PermissionError, ValueError):
            # A refused widening leaves a receipt, as a refused policy write does.
            try:
                sessions.receipt_refusal(owner, "outside_agents." + action,
                                         {**fields, "origin": origin})
            except Exception:  # noqa: BLE001 - refuse regardless of receipt failure
                _LOG.exception("outside-agent refusal receipt unavailable")
            raise
    if op == "ROOM_POST":
        exactly("room_id", "client_message_id", "body", "parent_id")
        return ledger.post(owner, origin=origin, put_body=partial(conversations.put, owner),
                           **fields)
    if action == "board":
        exactly("kind", "task_id", "revision")
        return ledger.board_receipt(owner, origin=origin, **fields)
    if action == "read":
        if "limit" not in fields or not set(fields) <= {"limit", "max_bytes", "pad_bytes"}:
            raise ValueError("invalid ledger request")
        return ledger.read(owner, origin=origin, bodies=partial(conversations.bodies, owner),
                           **fields)
    exactly("through")
    return ledger.ack(owner, origin=origin, through=fields["through"])
