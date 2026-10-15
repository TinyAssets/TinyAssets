"""Shared app state: one definition, many accounts, no model run on the hot path.

The app document is stored once, under the owner. Invitees do not get a copy.
A record's bytes are charged to the account that owns that record. The
definition is never charged to an invitee.

Live changes are an ordered log. ``sync`` reads that log. It does not start a
run, take a seat, or wake a box. A run draws the seat and the provider of the
account it is charged to: the account that asked, or the app owner when the
document itself schedules the run. A scheduled run records that provider. It
does not call a model.

The document is data. Kinds, who may write, which fields are secret, which
transitions exist, which numbers must move together, which fields the clock
may step, and which runs the host repeats are all fields of that document.
This module does not know what an app is for.

The owner may replace that one document at its etag. A record stays in the
app that stored it. A declared transfer may debit and credit the same numeric
field in another app the actor belongs to, when that app declares the same
transfer. Neither app is copied.

What this module will not do, because it would be a second path or a guess:

* It will not execute arbitrary rules supplied as code.
* It will not give the sandboxed screen its own network. The signed-in shell
  holds one stream and pushes deltas into the screen. The screen names an
  interest (kinds and keys); it cannot name an account or a different app.
* It will not hide a secret that the document itself copied into a public field
  when that value is a short string. Long secret strings echoed into the public
  projection are refused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from tinyassets.principals import named_principal

_log = logging.getLogger(__name__)

SCHEMA = "app/v1"
_DOC_KEYS = frozenset({
    "schema", "records", "transitions", "transfers", "timers", "groups", "sync",
    "runs", "motion",
})
_RECORD_KEYS = frozenset({
    "ownership", "read", "write", "secret", "public", "max_bytes", "server",
})
_OWNERSHIP = frozenset({"creator", "app"})
_READ = frozenset({"owner", "members", "public"})
_WRITE = frozenset({"owner", "members", "app", "transition", "transfer"})
_ACTORS = frozenset({"owner", "members", "app", "not_subject", "timer"})
_MAX_RECORD = 262144
_MAX_DOC = 262144
_SECRET_ECHO = 8
# Admit storage in batches. A same-sized live update owes only its log line
# and does not call the quota ledger until a batch is full.
_MEM_FLUSH = 65536

_WRITERS: dict[str, tuple[threading.Lock, sqlite3.Connection]] = {}
_WRITERS_GUARD = threading.Lock()
# One wake for a burst. Waiters read the ring; they are not woken per write.
_NOTIFY_EVERY = 0.005
_KICK = threading.Event()
_NOTIFIER_LOCK = threading.Lock()
_NOTIFIER_ON = False
_PENDING: dict[int, list] = {}
_LIVE = threading.Condition()
_LIVE_TICK = 0
_RINGS: dict[str, "_Ring"] = {}
_TIP: dict[str, int] = {}
_DOCS: dict[str, dict] = {}
_OWNERS: dict[str, str] = {}
_ROLE: dict[tuple[str, str], str] = {}
_RING_MAX = 8192


class _Ring:
    """Committed public deltas, indexed by sequence so a waiter copies only its tail."""

    __slots__ = ("items", "base")

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.base = 0

    def append(self, event: dict) -> None:
        seq = int(event["seq"])
        if self.items and seq != self.base + len(self.items):
            self.items.clear()
            self.base = seq
        elif not self.items:
            self.base = seq
        self.items.append(event)
        extra = len(self.items) - _RING_MAX
        if extra > 0:
            del self.items[:extra]
            self.base += extra

    def tail(self, since: int) -> list[dict] | None:
        """Events with seq > since, or None when since falls in a gap."""
        if not self.items:
            return []
        if self.base > int(since) + 1:
            return None
        start = int(since) + 1 - self.base
        if start < 0:
            start = 0
        if start >= len(self.items):
            return []
        return self.items[start:]

APP_PERMISSION = "app"
VIEW_PERMISSION = "view"


class StateConflict(RuntimeError):
    """The record changed since it was read. The caller can retry."""

    retryable = True


class _NeedFlush(Exception):
    """The account already owes a full batch. Charge it before another write."""

    def __init__(self, account: str) -> None:
        self.account = account


class _NeedPrepay(Exception):
    """This write is itself a full batch. Charge it before it commits."""

    def __init__(self, account: str, nbytes: int) -> None:
        self.account = account
        self.nbytes = nbytes


def definition_bytes(base: Path, account_id: str) -> int:
    """Bytes of app documents owned by this account. Invitees own none."""
    account = named_principal(account_id)
    if not account:
        return 0
    return _sum(
        base,
        "SELECT SUM(length(document)) FROM app_definitions WHERE owner_user_id = ?",
        (account,),
    )


def record_bytes(base: Path, account_id: str) -> int:
    """Private record bytes plus the public log rows of records this account owns."""
    account = named_principal(account_id)
    if not account:
        return 0
    private = _sum(
        base,
        "SELECT SUM(length(private_json)) FROM app_records WHERE owner_user_id = ?",
        (account,),
    )
    public = _sum(
        base,
        "SELECT SUM(length(public_json)) FROM app_log WHERE owner_user_id = ?",
        (account,),
    )
    return private + public


def definition_count(base, universe_id: str) -> int:
    """How many copies of the app document exist. The only legal count is one."""
    uid = _uid(universe_id)
    with _read(base) as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT COUNT(*) FROM app_definitions WHERE universe_id = ?", (uid,),
        ).fetchone()
    return int(row[0] if row else 0)


def save_app(base, *, actor: str, universe_id: str, document: dict) -> dict[str, Any]:
    """Store the app document once. A second copy is refused."""
    owner = _actor(actor)
    uid = _uid(universe_id)
    if owner not in _owners(base, uid):
        raise PermissionError("only the app owner can store the app")
    body = _document(document)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _MAX_DOC:
        raise ValueError("app document is too large")
    _charge(base, owner, "app_definition", len(raw.encode("utf-8")))
    with _immediate(base) as conn:
        existing = conn.execute(
            "SELECT owner_user_id FROM app_definitions WHERE universe_id = ?", (uid,),
        ).fetchone()
        if existing is not None:
            raise PermissionError("the app is already stored")
        conn.execute(
            "INSERT INTO app_definitions (universe_id, owner_user_id, document, etag)"
            " VALUES (?, ?, ?, 1)",
            (uid, owner, raw),
        )
        _replace_schedules(conn, uid, body)
    _forget(uid)
    return {"universe_id": uid, "etag": 1, "owner": owner}


def revise_app(
    base, *, actor: str, universe_id: str, document: dict, expect_etag: int,
) -> dict[str, Any]:
    """Replace the one stored document at its etag. Invitees get no copy."""
    owner = _actor(actor)
    uid = _uid(universe_id)
    if owner not in _owners(base, uid):
        raise PermissionError("only the app owner can store the app")
    body = _document(document)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    if len(raw.encode("utf-8")) > _MAX_DOC:
        raise ValueError("app document is too large")
    _charge(base, owner, "app_definition", len(raw.encode("utf-8")))
    with _immediate(base) as conn:
        row = conn.execute(
            "SELECT owner_user_id, etag FROM app_definitions WHERE universe_id = ?",
            (uid,),
        ).fetchone()
        if row is None or row["owner_user_id"] != owner:
            raise PermissionError("only the app owner can store the app")
        if int(row["etag"]) != int(expect_etag):
            raise StateConflict("record changed")
        etag = int(row["etag"]) + 1
        updated = conn.execute(
            "UPDATE app_definitions SET document = ?, etag = ?"
            " WHERE universe_id = ? AND etag = ?",
            (raw, etag, uid, int(expect_etag)),
        ).rowcount
        if updated != 1:
            raise StateConflict("record changed")
        _replace_schedules(conn, uid, body)
    _forget(uid)
    return {"universe_id": uid, "etag": etag, "owner": owner}


def mint_invite(base, *, actor: str, universe_id: str) -> dict[str, str]:
    """Mint a one-time invite. The raw token is returned once and not stored."""
    owner = _actor(actor)
    uid = _uid(universe_id)
    if owner not in _owners(base, uid):
        raise PermissionError("only the app owner can invite")
    _require_app(base, uid)
    token = secrets.token_urlsafe(32)
    with _immediate(base) as conn:
        conn.execute(
            "INSERT INTO universe_invites"
            " (token_hash, universe_id, actor_id, granted_by, created_at)"
            " VALUES (?, ?, '', ?, ?)",
            (_hash(token), uid, owner, time.time()),
        )
    return {"token": token, "universe_id": uid}


def redeem_invite(base, *, token: str, actor: str) -> dict[str, str]:
    """Redeem an invite into an app role. Does not copy the app or open an account."""
    member = _actor(actor)
    digest = _hash(token)
    if not digest:
        raise PermissionError("an invite needs an authenticated account")
    from tinyassets.daemon_server import get_founder_home

    if not get_founder_home(base, member):
        raise PermissionError("an invitee needs their own account")
    with _immediate(base) as conn:
        row = conn.execute(
            "SELECT universe_id, actor_id, revoked_at, granted_by"
            " FROM universe_invites WHERE token_hash = ?",
            (digest,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise PermissionError("invite is not valid")
        uid = row["universe_id"]
        if member in _owners(base, uid):
            raise PermissionError("the owner already has this app")
        if row["actor_id"] not in ("", member):
            raise PermissionError("invite is not valid")
        held = conn.execute(
            "SELECT permission FROM universe_acl WHERE universe_id = ? AND actor_id = ?",
            (uid, member),
        ).fetchone()
        current = held["permission"] if held is not None else ""
        if current in {"read", "write", "admin"}:
            raise PermissionError("invite cannot replace a center grant")
        now = time.time()
        if not row["actor_id"]:
            claimed = conn.execute(
                "UPDATE universe_invites SET actor_id = ?, redeemed_at = ?"
                " WHERE token_hash = ? AND actor_id = '' AND revoked_at IS NULL",
                (member, now, digest),
            ).rowcount
            if claimed != 1:
                raise PermissionError("invite is not valid")
        if current != APP_PERMISSION:
            conn.execute(
                "INSERT INTO universe_acl"
                " (universe_id, actor_id, permission, granted_at, granted_by)"
                " VALUES (?, ?, ?, ?, ?)",
                (uid, member, APP_PERMISSION, now, row["granted_by"] or ""),
            )
    _forget(uid)
    return {"universe_id": uid, "actor_id": member, "permission": APP_PERMISSION}


def grant_view(base, *, actor: str, universe_id: str, viewer: str) -> dict[str, str]:
    """Let an account see public fields only. They cannot write, and get no copy."""
    owner = _actor(actor)
    uid = _uid(universe_id)
    watch = _actor(viewer)
    if owner not in _owners(base, uid):
        raise PermissionError("only the app owner can grant a view")
    if watch in _owners(base, uid):
        raise PermissionError("the owner already sees this app")
    from tinyassets.daemon_server import get_founder_home

    if not get_founder_home(base, watch):
        raise PermissionError("a viewer needs their own account")
    with _immediate(base) as conn:
        held = conn.execute(
            "SELECT permission FROM universe_acl WHERE universe_id = ? AND actor_id = ?",
            (uid, watch),
        ).fetchone()
        current = held["permission"] if held is not None else ""
        if current in {"read", "write", "admin", APP_PERMISSION}:
            raise PermissionError("view cannot replace a stronger grant")
        if current != VIEW_PERMISSION:
            conn.execute(
                "INSERT INTO universe_acl"
                " (universe_id, actor_id, permission, granted_at, granted_by)"
                " VALUES (?, ?, ?, ?, ?)",
                (uid, watch, VIEW_PERMISSION, time.time(), owner),
            )
    _forget(uid)
    return {"universe_id": uid, "actor_id": watch, "permission": VIEW_PERMISSION}


def revoke_member(base, *, actor: str, universe_id: str, member: str) -> bool:
    """Drop an app or view grant. A center-wide grant is left untouched."""
    owner = _actor(actor)
    uid = _uid(universe_id)
    who = _actor(member)
    if owner not in _owners(base, uid):
        raise PermissionError("only the app owner can revoke")
    with _immediate(base) as conn:
        deleted = conn.execute(
            "DELETE FROM universe_acl WHERE universe_id = ? AND actor_id = ?"
            " AND permission IN (?, ?)",
            (uid, who, APP_PERMISSION, VIEW_PERMISSION),
        ).rowcount
        conn.execute(
            "UPDATE universe_invites SET revoked_at = ?"
            " WHERE universe_id = ? AND actor_id = ? AND revoked_at IS NULL",
            (time.time(), uid, who),
        )
    _forget(uid)
    return deleted > 0


def write_record(
    base, *, actor: str, universe_id: str, kind: str, key: str, body: dict,
    expect_etag: int, token: str = "",
) -> dict[str, Any]:
    """Create or replace a record. No run is started and no seat is taken."""
    member = _actor(actor)
    uid = _uid(universe_id)
    payload = _body(body)
    prepaid: dict[str, int] = {}
    last: BaseException | None = None
    for _attempt in range(8):
        try:
            return _commit_record(
                base, member=member, uid=uid, kind=kind, key=key, payload=payload,
                expect_etag=int(expect_etag), token=token, prepaid=prepaid,
            )
        except _NeedFlush as need:
            flush_due(base, need.account)
            last = need
        except _NeedPrepay as need:
            _charge(base, need.account, "app_records", need.nbytes)
            prepaid[need.account] = prepaid.get(need.account, 0) + int(need.nbytes)
            last = need
    assert last is not None
    raise last


def _commit_record(
    base, *, member: str, uid: str, kind: str, key: str, payload: dict,
    expect_etag: int, token: str, prepaid: dict[str, int],
) -> dict[str, Any]:
    with _immediate(base) as conn:
        spec = _kind_on(conn, uid, kind)
        born = spec["write"] in {"transition", "transfer"}
        if born and int(expect_etag) != 0:
            raise PermissionError("this record changes only through its declared path")
        stored_key = _stored_key(spec, member, key)
        client_raw = _project(payload, spec, None)[2]
        if token:
            replay = _replay_on(conn, member, token, client_raw)
            if replay is not None:
                return replay
        doc_owner = _doc_owner(conn, uid)
        _require_member(conn, uid, member, doc_owner)
        row = conn.execute(
            "SELECT owner_user_id, etag, kind, private_json, public_json FROM app_records"
            " WHERE universe_id = ? AND record_key = ?",
            (uid, stored_key),
        ).fetchone()
        if row is not None and row["kind"] != kind:
            raise PermissionError("record not found")
        if born and row is not None:
            raise PermissionError("this record changes only through its declared path")
        if row is None:
            if int(expect_etag) != 0:
                raise StateConflict("record changed")
            owner = member if spec["ownership"] == "creator" else doc_owner
            if not born and not _may_write(spec, member, owner, doc_owner, True):
                raise PermissionError("record not found")
            etag = 1
        else:
            owner = row["owner_user_id"]
            if int(row["etag"]) != int(expect_etag):
                raise StateConflict("record changed")
            if not _may_write(spec, member, owner, doc_owner, True):
                raise PermissionError("record not found")
            etag = int(row["etag"]) + 1
        old = json.loads(row["private_json"]) if row is not None else None
        _kept, _shown, raw_private, raw_public = _project(payload, spec, old)
        old_priv = len(row["private_json"]) if row is not None else 0
        old_pub = len(row["public_json"]) if row is not None else 0
        growth = (
            max(0, len(raw_private) - old_priv) + max(0, len(raw_public) - old_pub)
            + len(raw_public)
        )
        _admit(conn, owner, growth, prepaid)
        seq = _bump(conn, uid)
        conn.execute(
            "INSERT INTO app_records"
            " (universe_id, record_key, kind, owner_user_id, private_json,"
            "  public_json, etag, seq)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(universe_id, record_key) DO UPDATE SET"
            " private_json = excluded.private_json, public_json = excluded.public_json,"
            " etag = excluded.etag, seq = excluded.seq, kind = excluded.kind",
            (uid, stored_key, kind, owner, raw_private, raw_public, etag, seq),
        )
        _log(conn, uid, seq, stored_key, owner, kind, "write", raw_public, member)
        result = {"key": stored_key, "etag": etag, "seq": seq, "owner": owner}
        _remember(conn, member, token, client_raw, result)
    return result


def read_record(base, *, actor: str, universe_id: str, key: str) -> dict[str, Any]:
    """The caller's view of one record. Secret fields stay with the record owner."""
    member = _actor(actor)
    uid = _uid(universe_id)
    with _read(base) as conn:
        _ensure(conn)
        doc_owner = _doc_owner(conn, uid)
        role = _role(conn, uid, member, doc_owner)
        if role is None:
            raise PermissionError("record not found")
        row = conn.execute(
            "SELECT kind, owner_user_id, private_json, public_json, etag, seq"
            " FROM app_records WHERE universe_id = ? AND record_key = ?",
            (uid, key),
        ).fetchone()
        if row is None:
            raise PermissionError("record not found")
        spec = _kind_on(conn, uid, row["kind"])
        if not _may_read(spec, role, member == row["owner_user_id"]):
            raise PermissionError("record not found")
        show_private = member == row["owner_user_id"]
        body = json.loads(row["private_json"] if show_private else row["public_json"])
    return {
        "key": key, "kind": row["kind"], "owner": row["owner_user_id"],
        "etag": int(row["etag"]), "seq": int(row["seq"]), "body": body,
        "private": show_private,
    }


def delete_record(
    base, *, actor: str, universe_id: str, key: str, expect_etag: int,
) -> dict[str, Any]:
    """Remove a record the actor owns. The log keeps a tombstone for everyone else."""
    member = _actor(actor)
    uid = _uid(universe_id)
    with _immediate(base) as conn:
        doc_owner = _doc_owner(conn, uid)
        _require_member(conn, uid, member, doc_owner)
        row = conn.execute(
            "SELECT owner_user_id, etag, kind FROM app_records"
            " WHERE universe_id = ? AND record_key = ?",
            (uid, key),
        ).fetchone()
        if row is None or int(row["etag"]) != int(expect_etag):
            raise StateConflict("record changed")
        if row["owner_user_id"] != member and member != doc_owner:
            raise PermissionError("record not found")
        seq = _bump(conn, uid)
        conn.execute(
            "DELETE FROM app_records WHERE universe_id = ? AND record_key = ?",
            (uid, key),
        )
        _log(conn, uid, seq, key, row["owner_user_id"], row["kind"], "delete", "{}", member)
    return {"key": key, "seq": seq, "deleted": True}


def sync(
    base, *, actor: str, universe_id: str, since: int = 0, limit: int = 500,
    kinds: list[str] | None = None, keys: list[str] | None = None,
) -> dict:
    """Public changes after ``since``. ``kinds`` and ``keys`` are an interest.

    The cursor moves past every row examined, so a client that names an
    interest does not re-read the rest of the log. No run, no seat, no box.
    """
    member = _actor(actor)
    uid = _uid(universe_id)
    cap = max(1, min(int(limit), 2000))
    wanted_kinds = None if kinds is None else [str(item) for item in kinds]
    wanted_keys = None if keys is None else [str(item) for item in keys]
    with _read(base) as conn:
        _ensure(conn)
        doc_owner = _doc_owner(conn, uid)
        role = _role(conn, uid, member, doc_owner)
        if role is None:
            raise PermissionError("record not found")
        document = _load(conn, uid)
        _cache_view(uid, document, doc_owner, member, role)
        published = set(document.get("sync") or document["records"])
        sql = (
            "SELECT seq, record_key, owner_user_id, kind, op, public_json FROM app_log"
            " WHERE universe_id = ? AND seq > ?"
        )
        params: list[Any] = [uid, int(since)]
        if wanted_kinds is not None:
            if not wanted_kinds:
                return {"events": [], "cursor": int(since)}
            sql += f" AND kind IN ({', '.join('?' for _ in wanted_kinds)})"
            params.extend(wanted_kinds)
        if wanted_keys is not None:
            if not wanted_keys:
                return {"events": [], "cursor": int(since)}
            sql += f" AND record_key IN ({', '.join('?' for _ in wanted_keys)})"
            params.extend(wanted_keys)
        sql += " ORDER BY seq LIMIT ?"
        params.append(cap)
        rows = conn.execute(sql, params).fetchall()
        events = []
        for row in rows:
            kind = row["kind"]
            if kind not in published:
                continue
            spec = document["records"].get(kind)
            if spec is None:
                continue
            owner_sees = member == row["owner_user_id"]
            if not _may_read(spec, role, owner_sees):
                continue
            events.append({
                "seq": int(row["seq"]),
                "key": row["record_key"],
                "kind": kind,
                "op": row["op"],
                "owner": row["owner_user_id"],
                "body": json.loads(row["public_json"]),
            })
        cursor = int(rows[-1]["seq"]) if rows else int(since)
        tip = conn.execute(
            "SELECT seq FROM app_seq WHERE universe_id = ?", (uid,),
        ).fetchone()
    _advance_tip(uid, int(tip["seq"]) if tip else 0)
    return {"events": events, "cursor": cursor}


_FLIGHT_LOCK = threading.Lock()
_FLIGHT: dict[tuple, threading.Event] = {}
_PAGES: dict[tuple, dict] = {}


def _public_interest(document: dict, kinds: list[str] | None) -> bool:
    """True when every named kind is public, so members see one shared page."""
    names = list(document["records"]) if kinds is None else kinds
    if not names:
        return False
    for name in names:
        spec = document["records"].get(name)
        if spec is None or spec.get("read") != "public":
            return False
    return True


def _flight(token: tuple, build) -> dict | None:
    """One waiter builds a page. The others wait and reuse it."""
    with _FLIGHT_LOCK:
        hit = _PAGES.get(token)
        if hit is not None:
            return hit
        event = _FLIGHT.get(token)
        leader = event is None
        if leader:
            event = threading.Event()
            _FLIGHT[token] = event
    if not leader:
        event.wait(timeout=5)
        with _FLIGHT_LOCK:
            return _PAGES.get(token)
    try:
        page = build()
    except BaseException:
        with _FLIGHT_LOCK:
            _FLIGHT.pop(token, None)
        event.set()
        raise
    with _FLIGHT_LOCK:
        if page is not None:
            _PAGES[token] = page
            if len(_PAGES) > 128:
                _PAGES.clear()
                _PAGES[token] = page
        _FLIGHT.pop(token, None)
    event.set()
    return page


def _shared_page(
    base, *, actor: str, universe_id: str, since: int, limit: int,
    kinds: list[str] | None, keys: list[str] | None,
) -> dict:
    """Ring page for ``actor``. Public interests are built once per tick."""
    uid = _uid(universe_id)
    member = _actor(actor)
    document = _DOCS.get(uid)
    role = _ROLE.get((uid, member))
    if document is None or not role or not _public_interest(document, kinds):
        return _page(
            base, actor=member, universe_id=uid, since=since, limit=limit,
            kinds=kinds, keys=keys,
        )
    with _LIVE:
        tip = _TIP.get(uid, 0)
    if int(since) >= tip:
        return {"events": [], "cursor": int(since)}
    kind_key = None if kinds is None else tuple(kinds)
    key_key = None if keys is None else tuple(keys)
    token = (uid, int(since), int(limit), kind_key, key_key, int(tip))

    def build() -> dict | None:
        hit = _from_ring(uid, member, int(since), int(limit), kinds, keys)
        return hit

    shared = _flight(token, build)
    if shared is None:
        return _page(
            base, actor=member, universe_id=uid, since=since, limit=limit,
            kinds=kinds, keys=keys,
        )
    return shared


def wait_live(
    base, *, actor: str, universe_id: str, since: int = 0, limit: int = 500,
    kinds: list[str] | None = None, keys: list[str] | None = None, timeout: float = 15,
) -> dict:
    """The next matching public deltas, or nothing if ``timeout`` passes.

    A waiter is woken by the writer. It does not poll the log and it does not
    start a run. The first call with ``since`` 0 still returns only one page.
    """
    def page_at() -> dict:
        return _shared_page(
            base, actor=actor, universe_id=universe_id, since=since, limit=limit,
            kinds=kinds, keys=keys,
        )

    page = page_at()
    if page["events"]:
        return page
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        with _LIVE:
            seen = _LIVE_TICK
        page = page_at()
        if page["events"]:
            return page
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with _LIVE:
            if _LIVE_TICK == seen:
                _LIVE.wait(timeout=remaining)
    return page_at()


def scheduled_runs(base, *, actor: str, universe_id: str) -> dict[str, Any]:
    """What the app document bills to its owner. Only the owner can see it.

    A scheduled run is the owner's because the owner stored the document that
    asks for it. It draws that owner's background seats and that owner's
    provider, never an invitee's. ``capped`` means the owner's background
    seats are already full, so a due run is not queued.
    """
    member = _actor(actor)
    uid = _uid(universe_id)
    with _read(base) as conn:
        _ensure(conn)
        owner = _doc_owner(conn, uid)
        if member != owner:
            raise PermissionError("only the app owner can see what the app is billed")
        schedules = conn.execute(
            "SELECT name, every_s, next_at FROM app_schedules WHERE universe_id = ?"
            " ORDER BY name",
            (uid,),
        ).fetchall()
        inflight = conn.execute(
            "SELECT name, state, provider FROM app_runs"
            " WHERE owner_user_id = ? AND state IN ('queued', 'waiting_seat', 'running')"
            " ORDER BY created_at",
            (owner,),
        ).fetchall()
    limit = _background_limit(base, owner)
    running = [row for row in inflight if row["state"] == "running"]
    by_name = {}
    for row in inflight:
        by_name.setdefault(row["name"], row["state"])
    full = len(inflight) >= limit
    listed = []
    for row in schedules:
        state = by_name.get(row["name"], "idle")
        listed.append({
            "name": row["name"],
            "every_s": row["every_s"],
            "next_at": row["next_at"],
            "state": "capped" if state == "idle" and full else state,
            "charged_to": owner,
        })
    return {
        "charged_to": owner,
        "pays": "owner_seat_and_provider",
        "because": (
            "a run declared on the app is the owner's, so it draws the owner's "
            "background seats and the provider bound on the owner's own home"
        ),
        "background_seats": limit,
        "in_flight": len(inflight),
        "running": len(running),
        "capped": full,
        "runs": listed,
    }


def transfer(
    base, *, actor: str, universe_id: str, name: str, moves: list[dict], token: str = "",
) -> dict[str, Any]:
    """Move one numeric field across records in one transaction, or change nothing."""
    member = _actor(actor)
    uid = _uid(universe_id)
    if not moves:
        raise ValueError("a transfer names at least one record")
    raw = _dump({"name": name, "moves": moves})
    replay = _replay(base, member, token, raw)
    if replay is not None:
        return replay
    with _immediate(base) as conn:
        doc_owner = _doc_owner(conn, uid)
        _require_member(conn, uid, member, doc_owner)
        document = _load(conn, uid)
        found = [item for item in document.get("transfers") or [] if item["name"] == name]
        spec = found[0] if found else None
        if spec is None:
            raise PermissionError("record not found")
        kind = spec["kind"]
        field = spec["field"]
        home_spec = document["records"][kind]
        if home_spec["write"] != "transfer":
            raise PermissionError("this record is not transferable")
        rows = []
        deltas = []
        seen: set[tuple[str, str]] = set()
        for move in moves:
            key = str(move.get("key") or "")
            delta = move.get("delta")
            etag = move.get("etag")
            move_uid = _uid(str(move["universe_id"])) if move.get("universe_id") else uid
            if type(delta) is not int or type(etag) is not int:
                raise ValueError("a transfer move needs an integer delta and etag")
            if (move_uid, key) in seen:
                raise ValueError("a transfer names each record once")
            seen.add((move_uid, key))
            record_spec = home_spec
            move_owner = doc_owner
            if move_uid != uid:
                move_owner = _doc_owner(conn, move_uid)
                _require_member(conn, move_uid, member, move_owner)
                other = _load(conn, move_uid)
                match = [
                    item for item in other.get("transfers") or [] if item["name"] == name
                ]
                if (
                    not match or match[0]["kind"] != kind or match[0]["field"] != field
                    or match[0]["conserve"] != spec["conserve"]
                ):
                    raise PermissionError("record not found")
                record_spec = other["records"][kind]
                if record_spec["write"] != "transfer":
                    raise PermissionError("this record is not transferable")
            row = conn.execute(
                "SELECT owner_user_id, etag, private_json, public_json FROM app_records"
                " WHERE universe_id = ? AND record_key = ? AND kind = ?",
                (move_uid, key, kind),
            ).fetchone()
            if row is None or int(row["etag"]) != etag:
                raise StateConflict("record changed")
            if delta < 0 and row["owner_user_id"] != member:
                raise PermissionError("record not found")
            if not _is_member(conn, move_uid, row["owner_user_id"], move_owner):
                raise PermissionError("record not found")
            rows.append((move_uid, key, row, record_spec))
            deltas.append(delta)
        if spec.get("conserve") and sum(deltas) != 0:
            raise ValueError("a conserving transfer must sum to zero")
        seqs = []
        updated = []
        for (move_uid, key, row, record_spec), delta in zip(rows, deltas):
            private = json.loads(row["private_json"])
            have = private.get(field, 0)
            if type(have) is not int:
                raise ValueError("a transfer field must be an integer")
            nxt = have + delta
            if nxt < 0:
                raise ValueError("a transfer cannot make the field negative")
            private[field] = nxt
            secret = set(record_spec["secret"]) | {"_sim_at"}
            private, public = _split(private, secret)
            raw_private = _dump(private)
            raw_public = _dump(public)
            etag = int(row["etag"]) + 1
            seq = _bump(conn, move_uid)
            conn.execute(
                "UPDATE app_records SET private_json = ?, public_json = ?, etag = ?, seq = ?"
                " WHERE universe_id = ? AND record_key = ?",
                (raw_private, raw_public, etag, seq, move_uid, key),
            )
            _log(
                conn, move_uid, seq, key, row["owner_user_id"], kind, "transfer",
                raw_public, member,
            )
            seqs.append(seq)
            updated.append({"key": key, "etag": etag, "seq": seq, field: nxt})
        result = {"name": name, "moves": updated, "seq": seqs[-1]}
        _remember(conn, member, token, raw, result)
    return result


def transition(
    base, *, actor: str, universe_id: str, key: str, name: str, expect_etag: int,
    patch: dict | None = None, token: str = "",
) -> dict[str, Any]:
    """Apply one declared transition. The caller cannot write any other field."""
    member = _actor(actor)
    uid = _uid(universe_id)
    raw = _dump({"key": key, "name": name, "patch": patch or {}})
    replay = _replay(base, member, token, raw)
    if replay is not None:
        return replay
    with _immediate(base) as conn:
        result = _apply_transition(
            conn, uid=uid, actor=member, key=key, name=name,
            expect_etag=int(expect_etag), patch=dict(patch or {}), timer=False,
        )
        _remember(conn, member, token, raw, result)
    return result


def expire_due(base, *, universe_id: str, now: float) -> list[dict[str, Any]]:
    """Apply timer transitions whose deadline has passed. This is not a run."""
    uid = _uid(universe_id)
    applied: list[dict[str, Any]] = []
    with _immediate(base) as conn:
        document = _load(conn, uid)
        for timer in document.get("timers") or []:
            rows = conn.execute(
                "SELECT record_key, private_json, etag FROM app_records"
                " WHERE universe_id = ? AND kind = ?",
                (uid, timer["kind"]),
            ).fetchall()
            for row in rows:
                body = json.loads(row["private_json"])
                deadline = body.get(timer["deadline"])
                if type(deadline) not in {int, float}:
                    continue
                if float(deadline) > float(now):
                    continue
                if str(body.get("state") or "") != timer["when_state"]:
                    continue
                applied.append(_apply_transition(
                    conn, uid=uid, actor="", key=row["record_key"],
                    name=timer["transition"], expect_etag=int(row["etag"]),
                    patch={}, timer=True,
                ))
    return applied


def claim_group(base, *, actor: str, universe_id: str, name: str) -> dict[str, Any]:
    """Match waiting records of one declared group, or leave them waiting."""
    member = _actor(actor)
    uid = _uid(universe_id)
    with _immediate(base) as conn:
        doc_owner = _doc_owner(conn, uid)
        _require_member(conn, uid, member, doc_owner)
        document = _load(conn, uid)
        spec = next((item for item in document.get("groups") or [] if item["name"] == name), None)
        if spec is None:
            raise PermissionError("record not found")
        waiting = spec["waiting"]
        state_field = spec["state"]
        mine = conn.execute(
            "SELECT record_key, owner_user_id, etag, private_json FROM app_records"
            " WHERE universe_id = ? AND kind = ? AND owner_user_id = ?",
            (uid, spec["queue"], member),
        ).fetchall()
        mine = [row for row in mine if json.loads(row["private_json"]).get(state_field) == waiting]
        if len(mine) != 1:
            raise PermissionError("record not found")
        others = conn.execute(
            "SELECT record_key, owner_user_id, etag, private_json, seq FROM app_records"
            " WHERE universe_id = ? AND kind = ? ORDER BY seq",
            (uid, spec["queue"]),
        ).fetchall()
        chosen = []
        for row in others:
            body = json.loads(row["private_json"])
            if body.get(state_field) != waiting:
                continue
            chosen.append(row)
            if len(chosen) == int(spec["size"]):
                break
        if len(chosen) < int(spec["size"]) or mine[0]["record_key"] not in {
            row["record_key"] for row in chosen
        }:
            return {"matched": False, "waiting": len(chosen)}
        group_id = uuid.uuid4().hex
        record_spec = document["records"][spec["queue"]]
        secret = set(record_spec["secret"])
        moved = []
        last = 0
        for row in chosen:
            private = json.loads(row["private_json"])
            private[state_field] = spec["matched"]
            private[spec["group"]] = group_id
            private, public = _split(private, secret)
            etag = int(row["etag"]) + 1
            seq = _bump(conn, uid)
            raw_private = _dump(private)
            raw_public = _dump(public)
            updated = conn.execute(
                "UPDATE app_records SET private_json = ?, public_json = ?, etag = ?, seq = ?"
                " WHERE universe_id = ? AND record_key = ? AND etag = ?",
                (raw_private, raw_public, etag, seq, uid, row["record_key"], int(row["etag"])),
            ).rowcount
            if updated != 1:
                raise StateConflict("record changed")
            _log(
                conn, uid, seq, row["record_key"], row["owner_user_id"],
                spec["queue"], "group", raw_public, member,
            )
            moved.append(row["record_key"])
            last = seq
    return {"matched": True, "group": group_id, "keys": moved, "seq": last}


def enqueue_run(base, *, actor: str, universe_id: str, name: str) -> dict[str, Any]:
    """Ask for a run as this account. Nothing runs until the host pumps it."""
    member = _actor(actor)
    uid = _uid(universe_id)
    label = str(name or "").strip()
    if not label or len(label) > 80:
        raise ValueError("a run needs a name")
    with _immediate(base) as conn:
        doc_owner = _doc_owner(conn, uid)
        _require_member(conn, uid, member, doc_owner)
        run_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO app_runs"
            " (run_id, universe_id, owner_user_id, provider, seat_id, state,"
            "  reason, name, created_at)"
            " VALUES (?, ?, ?, '', '', 'queued', '', ?, ?)",
            (run_id, uid, member, label, time.time()),
        )
    return {"run_id": run_id, "state": "queued", "owner": member}


def pump_runs(base, *, limit: int = 50, now: float | None = None) -> list[dict[str, Any]]:
    """Start queued runs, including runs the document scheduled, on the charged account."""
    from tinyassets import universe_seats

    _enqueue_due(base, time.time() if now is None else float(now))
    started: list[dict[str, Any]] = []
    with _read(base) as conn:
        _ensure(conn)
        queued = conn.execute(
            "SELECT run_id, universe_id, owner_user_id, name FROM app_runs"
            " WHERE state = 'queued' ORDER BY created_at LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    for row in queued:
        starter = row["owner_user_id"]
        label = row["name"]
        sources = starter_sources(base, starter)
        if not sources:
            _set_run(base, row["run_id"], state="refused", reason="provider_not_bound")
            started.append({
                "run_id": row["run_id"], "owner": starter, "state": "refused",
                "reason": "provider_not_bound", "name": label,
            })
            continue
        key = universe_seats.account_pool(starter)
        outcome = universe_seats.try_acquire(
            f"app-run:{row['run_id']}", key,
            kind=universe_seats.KIND_AUTOMATION,
            universe_id=row["universe_id"],
            db=universe_seats.ledger_path(base),
        )
        if isinstance(outcome, universe_seats.Waiting):
            _set_run(base, row["run_id"], state="waiting_seat", reason="waiting_seat")
            started.append({
                "run_id": row["run_id"], "owner": starter, "state": "waiting_seat",
                "name": label,
            })
            continue
        provider = sorted(sources)[0]
        _set_run(
            base, row["run_id"], state="running", provider=provider, seat_id=outcome.seat_id,
        )
        started.append({
            "run_id": row["run_id"], "owner": starter, "state": "running",
            "provider": provider, "seat_id": outcome.seat_id, "name": label,
        })
    return started


def advance(base, *, universe_id: str, now: float) -> list[dict[str, Any]]:
    """Step declared numeric motion. No run, no seat, no box."""
    uid = _uid(universe_id)
    clock = float(now)
    prepaid: dict[str, int] = {}
    last: BaseException | None = None
    for _attempt in range(8):
        try:
            return _advance_once(base, uid, clock, prepaid)
        except _NeedFlush as need:
            flush_due(base, need.account)
            last = need
        except _NeedPrepay as need:
            _charge(base, need.account, "app_records", need.nbytes)
            prepaid[need.account] = prepaid.get(need.account, 0) + int(need.nbytes)
            last = need
    assert last is not None
    raise last


def _advance_once(
    base, uid: str, clock: float, prepaid: dict[str, int],
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    with _immediate(base) as conn:
        document = _load(conn, uid)
        for rule in document.get("motion") or []:
            rows = conn.execute(
                "SELECT record_key, owner_user_id, etag, private_json, public_json"
                " FROM app_records WHERE universe_id = ? AND kind = ?",
                (uid, rule["kind"]),
            ).fetchall()
            tick = float(rule["tick_s"])
            for row in rows:
                private = json.loads(row["private_json"])
                where = private.get(rule["position"])
                velocity = private.get(rule["velocity"])
                if not _vector(where) or not _vector(velocity) or len(where) != len(velocity):
                    continue
                sim = private.get("_sim_at")
                sim = float(sim) if type(sim) in {int, float} and type(sim) is not bool else 0.0
                steps = int((clock - sim) / tick)
                if steps < 1:
                    continue
                steps = min(steps, 8)
                private[rule["position"]] = [
                    float(origin) + float(speed) * tick * steps
                    for origin, speed in zip(where, velocity)
                ]
                private["_sim_at"] = sim + tick * steps
                secret = set(document["records"][rule["kind"]]["secret"]) | {"_sim_at"}
                private, public = _split(private, secret)
                raw_private = _dump(private)
                raw_public = _dump(public)
                cap = int(document["records"][rule["kind"]]["max_bytes"])
                if len(raw_private.encode("utf-8")) > cap:
                    raise ValueError("record is too large")
                etag = int(row["etag"]) + 1
                growth = (
                    max(0, len(raw_private) - len(row["private_json"]))
                    + max(0, len(raw_public) - len(row["public_json"]))
                    + len(raw_public)
                )
                _admit(conn, row["owner_user_id"], growth, prepaid)
                seq = _bump(conn, uid)
                conn.execute(
                    "UPDATE app_records SET private_json = ?, public_json = ?, etag = ?, seq = ?"
                    " WHERE universe_id = ? AND record_key = ? AND etag = ?",
                    (raw_private, raw_public, etag, seq, uid, row["record_key"], int(row["etag"])),
                )
                _log(
                    conn, uid, seq, row["record_key"], row["owner_user_id"], rule["kind"],
                    "advance", raw_public, "clock",
                )
                applied.append({
                    "key": row["record_key"], "etag": etag, "seq": seq,
                    "where": private[rule["position"]],
                })
    return applied


def finish_run(base, run_id: str) -> None:
    """Release the seat a pumped run holds. Host-side: members do not call this."""
    from tinyassets import universe_seats

    with _immediate(base) as conn:
        row = conn.execute(
            "SELECT seat_id, state FROM app_runs WHERE run_id = ?", (run_id,),
        ).fetchone()
        if row is None or row["state"] != "running":
            return
        conn.execute(
            "UPDATE app_runs SET state = 'done', seat_id = '' WHERE run_id = ?", (run_id,),
        )
        seat_id = row["seat_id"]
    if seat_id:
        universe_seats.release(seat_id, db=universe_seats.ledger_path(base))


def runs_for(base, account: str) -> list[dict[str, Any]]:
    """Runs charged to this account."""
    owner = _actor(account)
    with _read(base) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT run_id, universe_id, provider, state, reason, name FROM app_runs"
            " WHERE owner_user_id = ? ORDER BY created_at",
            (owner,),
        ).fetchall()
    return [dict(row) for row in rows]


def starter_sources(base, account: str) -> frozenset[str]:
    """Model sources bound on this account's own home. Never the app owner's."""
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.providers.served_model_plan import connected_sources

    home = get_founder_home(base, account) or ""
    if not home:
        return frozenset()
    return frozenset(connected_sources(base, Path(base) / home, account))


def _apply_transition(
    conn, *, uid: str, actor: str, key: str, name: str, expect_etag: int,
    patch: dict, timer: bool,
) -> dict[str, Any]:
    document = _load(conn, uid)
    doc_owner = _doc_owner(conn, uid)
    if not timer:
        _require_member(conn, uid, actor, doc_owner)
    rules = [item for item in document.get("transitions") or [] if item["name"] == name]
    row = conn.execute(
        "SELECT kind, owner_user_id, etag, private_json FROM app_records"
        " WHERE universe_id = ? AND record_key = ?",
        (uid, key),
    ).fetchone()
    if row is None and rules:
        spec = document["records"][rules[0]["kind"]]
        if spec["ownership"] == "creator":
            stored = _stored_key(spec, actor or doc_owner, key)
        else:
            stored = key
        if stored != key:
            row = conn.execute(
                "SELECT kind, owner_user_id, etag, private_json FROM app_records"
                " WHERE universe_id = ? AND record_key = ?",
                (uid, stored),
            ).fetchone()
            key = stored
    if row is not None and int(row["etag"]) != expect_etag:
        raise StateConflict("record changed")
    if row is None and expect_etag != 0:
        raise StateConflict("record changed")
    kind = row["kind"] if row is not None else (rules[0]["kind"] if len(rules) == 1 else "")
    rule = next((item for item in rules if item["kind"] == kind), None)
    if rule is None:
        raise PermissionError("record not found")
    record_spec = document["records"][kind]
    private = json.loads(row["private_json"]) if row is not None else {}
    owner = row["owner_user_id"] if row is not None else (
        actor if record_spec["ownership"] == "creator" else doc_owner
    )
    state = str(private.get("state") or "")
    allowed_from = rule["from"]
    if row is not None and allowed_from != ["*"] and state not in allowed_from:
        raise PermissionError("record not found")
    if not _actor_allowed(
        rule["actor"], actor=actor, owner=owner, app_owner=doc_owner,
        body={**private, **patch}, subject_field=rule.get("subject_field") or "", timer=timer,
        member=timer or _is_member(conn, uid, actor, doc_owner),
    ):
        raise PermissionError("record not found")
    unknown = set(patch) - set(rule["set"])
    if unknown or set(patch) & set(record_spec.get("server") or []):
        raise PermissionError("record not found")
    if record_spec["write"] != "transition":
        raise PermissionError("record not found")
    private.update(patch)
    private["state"] = rule["to"]
    secret = set(record_spec["secret"])
    private, public = _split(private, secret)
    _reject_echo(private, secret, public)
    raw_private = _dump(private)
    raw_public = _dump(public)
    if len(raw_private.encode("utf-8")) > int(record_spec["max_bytes"]):
        raise ValueError("record is too large")
    etag = 1 if row is None else int(row["etag"]) + 1
    seq = _bump(conn, uid)
    conn.execute(
        "INSERT INTO app_records"
        " (universe_id, record_key, kind, owner_user_id, private_json,"
        "  public_json, etag, seq)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(universe_id, record_key) DO UPDATE SET"
        " private_json = excluded.private_json, public_json = excluded.public_json,"
        " etag = excluded.etag, seq = excluded.seq",
        (uid, key, kind, owner, raw_private, raw_public, etag, seq),
    )
    _log(conn, uid, seq, key, owner, kind, "transition", raw_public, actor or "timer")
    return {"key": key, "etag": etag, "seq": seq, "state": rule["to"]}


def _actor_allowed(
    rule, *, actor: str, owner: str, app_owner: str, body: dict, subject_field: str,
    timer: bool, member: bool,
) -> bool:
    allowed = {rule} if isinstance(rule, str) else set(rule)
    if timer:
        return "timer" in allowed
    if actor == owner and "owner" in allowed:
        return True
    if actor == app_owner and "app" in allowed:
        return True
    if "members" in allowed and member:
        return True
    if "not_subject" in allowed and member:
        subject = str(body.get(subject_field) or "")
        return bool(subject) and subject != actor
    return False


def _may_write(spec: dict, actor: str, owner: str, app_owner: str, member: bool) -> bool:
    mode = spec["write"]
    if mode == "owner":
        return actor == owner
    if mode == "members":
        return member
    if mode == "app":
        return actor == app_owner
    return False


def _may_read(spec: dict, role: str, owner_sees: bool) -> bool:
    if owner_sees:
        return True
    mode = spec["read"]
    if mode == "owner":
        return False
    if mode == "public":
        return role in {"owner", "app", "view"}
    return role in {"owner", "app"}


def _role(conn, uid: str, actor: str, doc_owner: str) -> str | None:
    if actor == doc_owner:
        return "owner"
    if not actor:
        return None
    row = conn.execute(
        "SELECT permission FROM universe_acl WHERE universe_id = ? AND actor_id = ?",
        (uid, actor),
    ).fetchone()
    if row is None:
        return None
    if row["permission"] == APP_PERMISSION:
        return "app"
    if row["permission"] == VIEW_PERMISSION:
        return "view"
    if row["permission"] == "admin":
        return "owner"
    return None


def _require_member(conn, uid: str, actor: str, doc_owner: str) -> None:
    if _role(conn, uid, actor, doc_owner) != "app" and actor != doc_owner:
        if _role(conn, uid, actor, doc_owner) != "owner":
            raise PermissionError("record not found")


def _is_member(conn, uid: str, actor: str, doc_owner: str) -> bool:
    role = _role(conn, uid, actor, doc_owner)
    return role in {"owner", "app"}


def _bill_to(base, uid: str, spec: dict, actor: str) -> str:
    if spec["ownership"] == "app":
        return next(iter(_owners(base, uid)))
    return actor


def _stored_key(spec: dict, actor: str, key: str) -> str:
    local = _key(key)
    if spec["ownership"] == "creator":
        return f"{actor}/{local}"
    return local


def _split(body: dict, secret: set[str]) -> tuple[dict, dict]:
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items() if k not in secret}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return body, scrub(body)


def _reject_echo(body: dict, secret: set[str], public: dict) -> None:
    public_raw = _dump(public)
    for key in secret:
        value = body.get(key)
        if isinstance(value, str) and len(value) >= _SECRET_ECHO and value in public_raw:
            raise PermissionError("a secret value was copied into the public view")


def _project(payload: dict, spec: dict, old: dict | None) -> tuple[dict, dict, str, str]:
    """Merge server-owned fields, then split the public projection."""
    merged = {key: value for key, value in payload.items() if key != "_sim_at"}
    if old is not None:
        for field in spec.get("server") or []:
            if field in old:
                merged[field] = old[field]
        if "_sim_at" in old:
            merged["_sim_at"] = old["_sim_at"]
    secret = set(spec["secret"]) | {"_sim_at"}
    private, public = _split(merged, secret)
    _reject_echo(merged, secret, public)
    raw_private = _dump(private)
    raw_public = _dump(public)
    if len(raw_private.encode("utf-8")) > int(spec["max_bytes"]):
        raise ValueError("record is too large")
    return private, public, raw_private, raw_public


def _vector(value: Any) -> bool:
    if not isinstance(value, list) or not value or len(value) > 16:
        return False
    return all(type(item) in {int, float} for item in value)


def _declared_runs(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, list):
        raise ValueError("runs must be a list")
    cleaned = []
    names: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("a scheduled run must be an object")
        name = str(rule.get("name") or "").strip()
        if not name or len(name) > 80 or name in names:
            raise ValueError("a scheduled run needs a name")
        if rule.get("actor") != "app":
            raise ValueError("a scheduled run is charged to the app owner")
        every = rule.get("every_s")
        if type(every) not in {int, float} or float(every) < 1 or float(every) > 86400:
            raise ValueError("a scheduled run needs an interval of at least 1 second")
        names.add(name)
        cleaned.append({"name": name, "actor": "app", "every_s": float(every)})
    return cleaned


def _declared_motion(rules: Any, kinds: set[str]) -> list[dict[str, Any]]:
    if not isinstance(rules, list):
        raise ValueError("motion must be a list")
    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("a motion step must be an object")
        kind = rule.get("kind")
        position = str(rule.get("position") or "")
        velocity = str(rule.get("velocity") or "")
        if kind not in kinds or not position or not velocity or position == velocity:
            raise ValueError("a motion step names a position and a velocity")
        tick = rule.get("tick_s")
        if type(tick) not in {int, float} or float(tick) <= 0 or float(tick) > 3600:
            raise ValueError("a motion step needs a tick")
        cleaned.append({
            "kind": kind, "position": position, "velocity": velocity, "tick_s": float(tick),
        })
    return cleaned


def _replace_schedules(conn, uid: str, body: dict) -> None:
    conn.execute("DELETE FROM app_schedules WHERE universe_id = ?", (uid,))
    for rule in body.get("runs") or []:
        conn.execute(
            "INSERT INTO app_schedules (universe_id, name, every_s, next_at)"
            " VALUES (?, ?, ?, 0)",
            (uid, rule["name"], rule["every_s"]),
        )


def _enqueue_due(base, now: float) -> None:
    """Queue document runs whose interval has elapsed and that are not already in flight."""
    with _immediate(base) as conn:
        rows = conn.execute(
            "SELECT universe_id, name, every_s FROM app_schedules WHERE next_at <= ?",
            (float(now),),
        ).fetchall()
        for row in rows:
            uid = row["universe_id"]
            name = row["name"]
            live = conn.execute(
                "SELECT 1 FROM app_runs WHERE universe_id = ? AND name = ?"
                " AND state IN ('queued', 'waiting_seat', 'running')",
                (uid, name),
            ).fetchone()
            if live is not None:
                continue
            owner = _doc_owner(conn, uid)
            limit = _background_limit(base, owner)
            inflight = conn.execute(
                "SELECT COUNT(*) FROM app_runs WHERE owner_user_id = ?"
                " AND state IN ('queued', 'waiting_seat', 'running')",
                (owner,),
            ).fetchone()[0]
            if int(inflight) >= limit:
                continue
            conn.execute(
                "INSERT INTO app_runs"
                " (run_id, universe_id, owner_user_id, provider, seat_id, state,"
                "  reason, name, created_at)"
                " VALUES (?, ?, ?, '', '', 'queued', '', ?, ?)",
                (uuid.uuid4().hex, uid, owner, name, float(now)),
            )
            conn.execute(
                "UPDATE app_schedules SET next_at = ? WHERE universe_id = ? AND name = ?",
                (float(now) + float(row["every_s"]), uid, name),
            )


def _document(document: dict) -> dict:
    if not isinstance(document, dict) or set(document) - _DOC_KEYS:
        raise ValueError("app document has an unknown field")
    if document.get("schema") != SCHEMA:
        raise ValueError("app document schema is not app/v1")
    records = document.get("records")
    if not isinstance(records, dict) or not records:
        raise ValueError("app document needs records")
    cleaned = []
    for kind, spec in records.items():
        _key(str(kind))
        if not isinstance(spec, dict) or set(spec) - _RECORD_KEYS:
            raise ValueError("record kind has an unknown field")
        if spec.get("ownership") not in _OWNERSHIP or spec.get("read") not in _READ:
            raise ValueError("record kind is not readable")
        if spec.get("write") not in _WRITE:
            raise ValueError("record kind is not writable")
        secret = list(spec.get("secret") or [])
        public = list(spec.get("public") or [])
        server = list(spec.get("server") or [])
        if not all(isinstance(item, str) and item for item in secret + public + server):
            raise ValueError("record fields must be named")
        if len(set(server)) != len(server):
            raise ValueError("record fields must be named")
        max_bytes = int(spec.get("max_bytes") or 4096)
        if max_bytes < 1 or max_bytes > _MAX_RECORD:
            raise ValueError("record max_bytes is out of range")
        cleaned.append((kind, {
            "ownership": spec["ownership"], "read": spec["read"], "write": spec["write"],
            "secret": secret, "public": public, "server": server, "max_bytes": max_bytes,
        }))
    kinds = {kind for kind, _ in cleaned}
    transitions = []
    for rule in document.get("transitions") or []:
        if not isinstance(rule, dict):
            raise ValueError("a transition must be an object")
        actor = rule.get("actor")
        actors = {actor} if isinstance(actor, str) else set(actor or [])
        if not actors or not actors <= _ACTORS:
            raise ValueError("a transition actor is unknown")
        if rule.get("kind") not in kinds or not str(rule.get("name") or "").strip():
            raise ValueError("a transition names a record kind")
        if "not_subject" in actors and not str(rule.get("subject_field") or "").strip():
            raise ValueError("not_subject needs a subject field")
        source = rule.get("from")
        if source != ["*"] and not (
            isinstance(source, list) and all(isinstance(item, str) for item in source)
        ):
            raise ValueError("a transition needs a from state")
        transitions.append({
            "name": str(rule["name"]), "kind": rule["kind"], "from": list(source),
            "to": str(rule.get("to") or ""), "actor": actor, "set": list(rule.get("set") or []),
            "subject_field": str(rule.get("subject_field") or ""),
        })
    transfers = []
    for rule in document.get("transfers") or []:
        if rule.get("kind") not in kinds or not str(rule.get("name") or "").strip():
            raise ValueError("a transfer names a record kind")
        if not str(rule.get("field") or "").strip() or type(rule.get("conserve")) is not bool:
            raise ValueError("a transfer needs a field and conserve")
        transfers.append({
            "name": str(rule["name"]), "kind": rule["kind"], "field": str(rule["field"]),
            "conserve": rule["conserve"],
        })
    timers = []
    for rule in document.get("timers") or []:
        if rule.get("kind") not in kinds:
            raise ValueError("a timer names a record kind")
        timers.append({
            "kind": rule["kind"], "deadline": str(rule["deadline"]),
            "when_state": str(rule["when_state"]), "transition": str(rule["transition"]),
        })
    groups = []
    for rule in document.get("groups") or []:
        if rule.get("queue") not in kinds:
            raise ValueError("a group names a record kind")
        size = rule.get("size")
        if type(size) is not int or size < 2:
            raise ValueError("a group needs a size of at least 2")
        groups.append({
            "name": str(rule["name"]), "queue": rule["queue"], "size": size,
            "waiting": str(rule.get("waiting") or "waiting"),
            "matched": str(rule.get("matched") or "matched"),
            "state": str(rule.get("state") or "state"),
            "group": str(rule.get("group") or "group"),
        })
    runs = _declared_runs(document.get("runs") or [])
    motion = _declared_motion(document.get("motion") or [], kinds)
    sync = document.get("sync")
    if sync is None:
        sync_kinds = sorted(kinds)
    else:
        if not isinstance(sync, list) or not set(sync) <= kinds:
            raise ValueError("sync names record kinds")
        sync_kinds = list(sync)
    return {
        "schema": SCHEMA,
        "records": {kind: spec for kind, spec in cleaned},
        "transitions": transitions,
        "transfers": transfers,
        "timers": timers,
        "groups": groups,
        "runs": runs,
        "motion": motion,
        "sync": sync_kinds,
    }


def _kind(base, uid: str, kind: str) -> dict:
    with _read(base) as conn:
        _ensure(conn)
        return _kind_on(conn, uid, kind)


def _kind_on(conn, uid: str, kind: str) -> dict:
    spec = _load(conn, uid)["records"].get(kind)
    if spec is None:
        raise PermissionError("record not found")
    return spec


def _load(conn, uid: str) -> dict:
    row = conn.execute(
        "SELECT document FROM app_definitions WHERE universe_id = ?", (uid,),
    ).fetchone()
    if row is None:
        raise PermissionError("record not found")
    return json.loads(row["document"])


def _doc_owner(conn, uid: str) -> str:
    row = conn.execute(
        "SELECT owner_user_id FROM app_definitions WHERE universe_id = ?", (uid,),
    ).fetchone()
    if row is None:
        raise PermissionError("record not found")
    return row["owner_user_id"]


def _require_app(base, uid: str) -> None:
    if definition_count(base, uid) != 1:
        raise PermissionError("the app is not stored")


def _owners(base, uid: str) -> set[str]:
    from tinyassets.universe_owner import owner_of

    charged = owner_of(base, uid)
    return {charged} if charged else set()


def _log(conn, uid, seq, key, owner, kind, op, public, actor) -> None:
    conn.execute(
        "INSERT INTO app_log"
        " (universe_id, seq, record_key, owner_user_id, kind, op, public_json, actor_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, seq, key, owner, kind, op, public, actor),
    )
    pending = _PENDING.get(id(conn))
    if pending is not None:
        pending.append({
            "universe": uid,
            "seq": int(seq),
            "key": key,
            "kind": kind,
            "op": op,
            "owner": owner,
            "body": json.loads(public),
        })


def _bump(conn, uid: str) -> int:
    row = conn.execute(
        "SELECT seq FROM app_seq WHERE universe_id = ?", (uid,),
    ).fetchone()
    if row is None:
        conn.execute("INSERT INTO app_seq (universe_id, seq) VALUES (?, 1)", (uid,))
        return 1
    seq = int(row["seq"]) + 1
    conn.execute("UPDATE app_seq SET seq = ? WHERE universe_id = ?", (seq, uid))
    return seq


def _replay(base, actor: str, token: str, payload: str) -> dict | None:
    if not token:
        return None
    with _read(base) as conn:
        _ensure(conn)
        return _replay_on(conn, actor, token, payload)


def _replay_on(conn, actor: str, token: str, payload: str) -> dict | None:
    if not token:
        return None
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    row = conn.execute(
        "SELECT payload_hash, result_json FROM app_idempotency"
        " WHERE actor_id = ? AND token = ?",
        (actor, token),
    ).fetchone()
    if row is None:
        return None
    if row["payload_hash"] != digest:
        raise PermissionError("this retry does not match the original write")
    return json.loads(row["result_json"])


def _remember(conn, actor: str, token: str, payload: str, result: dict) -> None:
    if not token:
        return
    conn.execute(
        "INSERT INTO app_idempotency (actor_id, token, payload_hash, result_json)"
        " VALUES (?, ?, ?, ?)",
        (
            actor, token,
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            _dump(result),
        ),
    )


def _set_run(base, run_id: str, *, state: str, reason: str = "", provider: str = "",
             seat_id: str = "") -> None:
    with _immediate(base) as conn:
        conn.execute(
            "UPDATE app_runs SET state = ?, reason = ?, provider = ?, seat_id = ?"
            " WHERE run_id = ? AND state IN ('queued', 'waiting_seat')",
            (state, reason, provider, seat_id, run_id),
        )


def _charge(base, account: str, store: str, nbytes: int) -> None:
    if nbytes <= 0:
        return
    from tinyassets.storage_accounting import charge_now

    charge_now(base, account_id=account, store=store, nbytes=nbytes)


def _sum(base, sql: str, params: tuple) -> int:
    try:
        with _read(base) as conn:
            _ensure(conn)
            row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


def _body(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("a record body is an object")
    # A client-supplied actor is data, never the authority for the write.
    return dict(body)


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _actor(value: str) -> str:
    actor = named_principal(value)
    if not actor:
        raise PermissionError("an authenticated account is required")
    return actor


def _uid(value: str) -> str:
    uid = str(value or "").strip()
    if not uid or uid != Path(uid).name or uid.startswith(".") or len(uid) > 80:
        raise ValueError("not an app id")
    return uid


def _key(value: str) -> str:
    key = str(value or "").strip()
    if not key or "/" in key or len(key) > 80 or any(ord(ch) < 32 for ch in key):
        raise ValueError("not a record key")
    return key


def _hash(token: str) -> str:
    raw = str(token or "").strip()
    if not raw or len(raw) > 200:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_SCHEMA_READY: set[str] = set()


def _ensure(conn) -> None:
    """Create tables once per database file. Later calls do not take a write lock."""
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    if key in _SCHEMA_READY:
        return
    _ensure_sql(conn)
    if key:
        _SCHEMA_READY.add(key)


def _ensure_sql(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_definitions (
            universe_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            document TEXT NOT NULL,
            etag INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_records (
            universe_id TEXT NOT NULL,
            record_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            private_json TEXT NOT NULL,
            public_json TEXT NOT NULL,
            etag INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            PRIMARY KEY (universe_id, record_key)
        );
        CREATE TABLE IF NOT EXISTS app_log (
            universe_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            record_key TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            op TEXT NOT NULL,
            public_json TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            PRIMARY KEY (universe_id, seq)
        );
        CREATE TABLE IF NOT EXISTS app_seq (
            universe_id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_idempotency (
            actor_id TEXT NOT NULL,
            token TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            result_json TEXT NOT NULL,
            PRIMARY KEY (actor_id, token)
        );
        CREATE TABLE IF NOT EXISTS app_runs (
            run_id TEXT PRIMARY KEY,
            universe_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            seat_id TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_schedules (
            universe_id TEXT NOT NULL,
            name TEXT NOT NULL,
            every_s REAL NOT NULL,
            next_at REAL NOT NULL,
            PRIMARY KEY (universe_id, name)
        );
        CREATE TABLE IF NOT EXISTS app_byte_due (
            owner_user_id TEXT PRIMARY KEY,
            bytes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS universe_invites (
            token_hash TEXT PRIMARY KEY,
            universe_id TEXT NOT NULL,
            actor_id TEXT NOT NULL DEFAULT '',
            granted_by TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            redeemed_at REAL,
            revoked_at REAL
        );
        """
    )


@contextmanager
def _read(base) -> Iterator[sqlite3.Connection]:
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.storage import db_path

    initialize_author_server(base)
    conn = sqlite3.connect(db_path(base), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        yield conn
    finally:
        conn.close()


def _writer(base) -> tuple[threading.Lock, sqlite3.Connection]:
    """One connection per database. Schema is applied once, outside a transaction."""
    key = os.fspath(base)
    with _WRITERS_GUARD:
        found = _WRITERS.get(key)
        if found is not None:
            return found
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.storage import db_path

    initialize_author_server(base)
    path = str(db_path(base))
    with _WRITERS_GUARD:
        found = _WRITERS.get(key)
        if found is not None:
            return found
        conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        _ensure(conn)
        pair = (threading.Lock(), conn)
        _WRITERS[key] = pair
        return pair


@contextmanager
def _immediate(base) -> Iterator[sqlite3.Connection]:
    """Serialize writers on one connection, then wake stream waiters after commit."""
    lock, conn = _writer(base)
    published = False
    token = id(conn)
    with lock:
        _PENDING[token] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
            published = list(_PENDING.get(token) or [])
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            _PENDING.pop(token, None)
            raise
        _PENDING.pop(token, None)
    if published:
        _publish(published)


def _forget(uid: str) -> None:
    """Drop cached reads so the next sync sees a new document or membership."""
    _DOCS.pop(uid, None)
    _OWNERS.pop(uid, None)
    for key in [item for item in _ROLE if item[0] == uid]:
        _ROLE.pop(key, None)


def _cache_view(uid: str, document: dict, owner: str, actor: str, role: str) -> None:
    _DOCS[uid] = document
    _OWNERS[uid] = owner
    _ROLE[(uid, actor)] = role or ""


def _advance_tip(uid: str, seq: int) -> None:
    with _LIVE:
        _TIP[uid] = max(_TIP.get(uid, 0), int(seq))


def _ensure_notifier() -> None:
    global _NOTIFIER_ON
    if _NOTIFIER_ON:
        return
    with _NOTIFIER_LOCK:
        if _NOTIFIER_ON:
            return
        threading.Thread(target=_notify_loop, name="live-notify", daemon=True).start()
        _NOTIFIER_ON = True


def _notify_loop() -> None:
    """Wake stream waiters on a short tick so a burst is one fan-out."""
    while True:
        _KICK.wait()
        time.sleep(_NOTIFY_EVERY)
        _KICK.clear()
        with _LIVE:
            _LIVE.notify_all()


def _publish(events: list[dict]) -> None:
    """Hand committed public deltas to waiters. The log remains the record.

    The wake is coalesced. A waiter reads the ring when it runs, so skipping
    a per-write wake does not drop a committed delta.
    """
    global _LIVE_TICK
    with _LIVE:
        for event in events:
            uid = event["universe"]
            ring = _RINGS.get(uid)
            if ring is None:
                ring = _Ring()
                _RINGS[uid] = ring
            ring.append(event)
            _TIP[uid] = max(_TIP.get(uid, 0), int(event["seq"]))
        _LIVE_TICK += 1
    _ensure_notifier()
    _KICK.set()


def _show(document: dict, role: str, actor: str, event: dict) -> bool:
    kind = event["kind"]
    published = set(document.get("sync") or document["records"])
    if kind not in published:
        return False
    spec = document["records"].get(kind)
    if spec is None:
        return False
    return _may_read(spec, role, actor == event["owner"])


def _from_ring(
    uid: str, actor: str, since: int, limit: int,
    kinds: list[str] | None, keys: list[str] | None,
) -> dict | None:
    """Deltas this process already published. None when the log has to be read."""
    if uid not in _DOCS or uid not in _TIP or (uid, actor) not in _ROLE:
        return None
    role = _ROLE[(uid, actor)]
    if not role:
        raise PermissionError("record not found")
    document = _DOCS[uid]
    with _LIVE:
        tip = _TIP.get(uid, 0)
        ring = _RINGS.get(uid)
        if int(since) >= tip:
            return {"events": [], "cursor": int(since)}
        # Tip can move from a log read before this process has published.
        # An empty tail then means the log, not the ring, has the rows.
        items = ring.tail(int(since)) if ring is not None else None
    if not items:
        return None
    wanted_kinds = None if kinds is None else set(kinds)
    wanted_keys = None if keys is None else set(keys)
    if wanted_kinds is not None and not wanted_kinds:
        return {"events": [], "cursor": int(since)}
    if wanted_keys is not None and not wanted_keys:
        return {"events": [], "cursor": int(since)}
    cap = max(1, min(int(limit), 2000))
    matched = []
    high = int(since)
    examined = 0
    for event in items:
        if event["seq"] <= int(since):
            continue
        high = event["seq"]
        examined += 1
        kind_ok = wanted_kinds is None or event["kind"] in wanted_kinds
        key_ok = wanted_keys is None or event["key"] in wanted_keys
        if kind_ok and key_ok and _show(document, role, actor, event):
            matched.append({
                "seq": event["seq"], "key": event["key"], "kind": event["kind"],
                "op": event["op"], "owner": event["owner"],
                "body": event["body"],
            })
        if examined >= cap:
            break
    return {"events": matched, "cursor": high}


def _page(
    base, *, actor: str, universe_id: str, since: int, limit: int,
    kinds: list[str] | None, keys: list[str] | None,
) -> dict:
    member = _actor(actor)
    uid = _uid(universe_id)
    hit = _from_ring(uid, member, int(since), int(limit), kinds, keys)
    if hit is not None:
        return hit
    return sync(
        base, actor=member, universe_id=uid, since=since, limit=limit,
        kinds=kinds, keys=keys,
    )


def _due_on(conn, account: str) -> int:
    row = conn.execute(
        "SELECT bytes FROM app_byte_due WHERE owner_user_id = ?", (account,),
    ).fetchone()
    return int(row["bytes"]) if row else 0


def _owe(conn, account: str, nbytes: int) -> None:
    if nbytes <= 0:
        return
    conn.execute(
        "INSERT INTO app_byte_due (owner_user_id, bytes) VALUES (?, ?)"
        " ON CONFLICT(owner_user_id) DO UPDATE SET bytes = bytes + excluded.bytes",
        (account, int(nbytes)),
    )


def _admit(conn, account: str, growth: int, prepaid: dict[str, int]) -> None:
    """Record owed bytes, or refuse to commit until a full batch is charged."""
    covered = int(prepaid.get(account, 0))
    if covered >= growth > 0:
        left = covered - growth
        if left:
            prepaid[account] = left
        else:
            prepaid.pop(account, None)
        return
    if _due_on(conn, account) >= _MEM_FLUSH:
        raise _NeedFlush(account)
    if growth >= _MEM_FLUSH:
        raise _NeedPrepay(account, growth)
    _owe(conn, account, growth)


def flush_due(base, account: str) -> int:
    """Charge one account's owed bytes outside the writer lock.

    A crash after the charge and before the subtraction charges twice. A crash
    before the charge leaves the owed row for the next flush. The write that
    filled the batch already waited for this charge, so a refusal stops it.
    """
    person = named_principal(account)
    if not person:
        return 0
    with _read(base) as conn:
        _ensure(conn)
        due = _due_on(conn, person)
    if due <= 0:
        return 0
    _charge(base, person, "app_records", due)
    with _immediate(base) as conn:
        current = _due_on(conn, person)
        take = min(current, due)
        if take:
            conn.execute(
                "UPDATE app_byte_due SET bytes = bytes - ? WHERE owner_user_id = ?",
                (take, person),
            )
    return due


def flush_pending(base) -> None:
    """Charge every account that owes bytes. One refusal does not skip the rest."""
    with _read(base) as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT owner_user_id FROM app_byte_due WHERE bytes > 0",
        ).fetchall()
    for row in rows:
        try:
            flush_due(base, row["owner_user_id"])
        except Exception:
            _log.exception("shared-state storage flush failed for %s", row["owner_user_id"])


def _background_limit(base, owner: str) -> int:
    from tinyassets.universe_seats import account_pool, limits_for_key

    return int(limits_for_key(account_pool(owner), root=base).background_seats)
