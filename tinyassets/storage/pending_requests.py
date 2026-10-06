"""Pending requests the agent raises for its user to answer.

Founder, 2026-08-27, refining an earlier "credential popup" idea:

    "pending-request should show up as tabs on the left side screen of the app,
    the hedder notates what it is like api in this case you tap/click them to
    expand and in this case paist in the api right there. the agent can
    construct these pending requests really how ever he likes so they can be
    used in clever ways by the agent."

So this is deliberately NOT a credential feature. It is one general primitive —
*the agent asks its user something and waits* — of which "I need an API key" is
the first kind. The agent composes the header, the prose, and the fields, so
kinds nobody has written code for still work.

Why a durable store
-------------------
The turn is stateless: the agent asks in one turn and the user may answer
minutes later, from the web app, the desktop app, or the phone. A pending
request therefore has to outlive the turn that raised it and be readable from
every surface, which is also what makes it addressable from a phone at all
(same MCP read, no second mechanism).

The one rule the agent does not get to bend
-------------------------------------------
A ``secret`` field is only permitted when the request's action actually deposits
a credential (``connect_http``), and **a secret value is never written to this
table** — it goes straight to the vault through the deposit path. Without that
rule, "construct them however you like" would let an agent — including one
steered by injected content — craft a request that asks for a password and lands
it in readable storage. The generality is the point; this is the boundary that
makes the generality safe.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from tinyassets.owner_control import serialized

logger = logging.getLogger(__name__)

_DB_NAME = ".pending_requests.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_requests (
    request_id  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    -- Optional answerable items: one request that holds several things, each
    -- with its own stable id and its own fields. Empty for every request that
    -- is a single question. See `request_item_answers`.
    items_json  TEXT NOT NULL DEFAULT '[]',
    dedupe_key  TEXT NOT NULL,
    status      TEXT NOT NULL,
    answer_json TEXT,
    feedback    TEXT,
    created_at  REAL NOT NULL,
    resolved_at REAL,
    -- Who raised it: "agent" (the universe's agent or the owner's chatbot) or
    -- "platform" (e.g. onboarding's model confirmation). Server-set, never
    -- read from the ask, because it decides what the agent may withdraw.
    origin      TEXT NOT NULL DEFAULT 'agent'
);
-- "don't ask me this again" (founder 2026-08-27). Keyed on the request's own
-- dedupe key, so it suppresses THIS ask rather than a whole category the user
-- never meant to silence.
-- A STANDING DECISION, not merely a mute. `decision` is what the user settled
-- on: "allowed" means go ahead without asking again; "declined" means do not do
-- this and do not ask again. Storing only the silence lost the answer, which
-- made every remembered decision a refusal.
CREATE TABLE IF NOT EXISTS request_suppressions (
    dedupe_key  TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    feedback    TEXT,
    decision    TEXT NOT NULL DEFAULT 'declined',
    answer_json TEXT,
    created_at  REAL NOT NULL
);
-- One item's answer. A separate table rather than a mutation of `items_json`
-- so that answering an item is an INSERT under a primary key: the same
-- "one answer counts once" guarantee the whole-request path gets from its
-- `status = 'pending'` guard, at item granularity.
--
-- An item with NO row here is not "unanswered" in storage -- it is DERIVED
-- (see `_item_state`): pending while the request is pending, unanswered once
-- the request closed. Storing it would mean writing a row to say nothing
-- happened, and a whole-request answer would have to fabricate one per item.
CREATE TABLE IF NOT EXISTS request_item_answers (
    request_id  TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    answer_json TEXT,
    feedback    TEXT,
    resolved_at REAL NOT NULL,
    PRIMARY KEY (request_id, item_id)
);
-- A lifted mute is recorded, not just applied: the agent runs as the user's
-- own principal, so "who lifted this" cannot be decided at the gate.
CREATE TABLE IF NOT EXISTS request_unmutes (
    dedupe_key TEXT NOT NULL,
    lifted_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_requests_status
    ON pending_requests(status, created_at);
CREATE TABLE IF NOT EXISTS request_answer_deliveries (
    request_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    origin_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    delivered_at REAL,
    PRIMARY KEY (request_id, event_key)
);
CREATE TABLE IF NOT EXISTS request_asking_launches (
    turn_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    origin_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS request_workflow_agents (
    workflow_id TEXT PRIMARY KEY,
    origin_json TEXT NOT NULL
);
"""

#: There is NO ceiling on how many requests may be pending. ``MAX_PENDING = 50``
#: used to refuse the fifty-first with ``too_many_pending``; it was an account
#: limit, and an account has exactly two -- the cloud bytes it occupies and its
#: concurrent agent seats (founder, 2026-09-30). A looping universe is bounded by
#: its seats, not by how many tabs it managed to open, and dedupe (the
#: ``dedupe_key`` lookup below) already collapses an identical re-ask into the
#: same row, which is what actually kept the rail readable.
#:
#: Answerable items on ONE request. Payload validation of a single ask, the same
#: class as the API layer's `_MAX_FIELDS` -- a note of 200 tasks is not a note,
#: and the notification for it has to fit. NOT an account limit: a universe may
#: raise another request (founder 2026-09-30, account limits are storage and
#: agent-run seats only).
MAX_ITEMS = 50

FIELD_TYPES = frozenset({"text", "secret", "choice"})


#: Columns added to a table AFTER it first shipped, as
#: ``(table, column, declaration)``.
#:
#: ``CREATE TABLE IF NOT EXISTS`` silently leaves an existing table exactly as it
#: was, so a column added to ``_SCHEMA`` never reaches a database that already
#: exists — and every live universe has one. On 2026-08-28 that took the rail's
#: front door down in production: #2636 added ``decision`` and ``answer_json``
#: with no migration, so every ``create_request`` raised
#: ``sqlite3.OperationalError: no such column: decision``, was swallowed by the
#: catch-all below, and reached the agent as the generic
#: ``request_storage_unavailable``. It could not raise a single request.
#:
#: Add a row here whenever you add a column to ``_SCHEMA``. Names are module
#: constants, never caller input, so the interpolation below is safe.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("request_suppressions", "decision", "TEXT NOT NULL DEFAULT 'declined'"),
    ("request_suppressions", "answer_json", "TEXT"),
    ("pending_requests", "origin", "TEXT NOT NULL DEFAULT 'agent'"),
    ("pending_requests", "items_json", "TEXT NOT NULL DEFAULT '[]'"),
    # Which agent asked (harness §4.18): answers route back to it, and its
    # dedupe and "don't ask again" are its own. ``main`` is only the seed.
    ("pending_requests", "agent", "TEXT NOT NULL DEFAULT 'main'"),
    ("pending_requests", "asking_context_json", "TEXT NOT NULL DEFAULT '{}'"),
)


def scoped_dedupe_key(dedupe_key: str, agent: str = "main") -> str:
    """The dedupe key for one agent's ask. The seeded agent's keys are the ones
    already stored, so nothing it was told "don't ask again" comes back."""
    agent = (agent or "main").strip() or "main"
    return dedupe_key if agent == "main" else f"agent:{agent}:{dedupe_key}"

#: Who may raise a request. Only ``agent`` requests can be withdrawn by the
#: agent; a platform-raised ask is the platform's to clear.
ORIGIN_AGENT = "agent"
ORIGIN_PLATFORM = "platform"
ORIGINS = frozenset({ORIGIN_AGENT, ORIGIN_PLATFORM})


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema, in place."""
    seen: dict[str, set[str]] = {}
    for table, column, declaration in _ADDED_COLUMNS:
        columns = seen.get(table)
        if columns is None:
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            seen[table] = columns
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            columns.add(column)


#: Schema version. Bumped when a DATA migration ships, not for a new column
#: (``_ADDED_COLUMNS`` handles those and is idempotent by inspection).
_SCHEMA_VERSION = 1


def _canonical_itemless_key(stored: str) -> str | None:
    """The five-element form of a six-element key whose items are empty.

    ``None`` for anything else -- and "anything else" is everything except the
    one exact shape this exists for.

    Why it exists: the version that introduced items put ``items`` in EVERY
    request's dedupe key, including the empty list, and it deployed. That
    changed the identity of every itemless request, so such a row now
    deduplicates against nothing, its execution pin refuses it, and a standing
    decision recorded against it never matches again (gpt-6-astra, 2026-09-30;
    its production census found zero such rows retained, but the writer was
    live, so they were reachable until this ships).

    A ONE-TIME REWRITE rather than accepting two shapes at each lookup. Three
    places compare these keys -- the pending dedupe, the suppression lookup and
    the execution pin -- and teaching each of them "try another shape" widens
    the pin, which is the one thing that must stay exact. After this runs there
    is one identity per request again.
    """
    if not stored.endswith(",[]]"):
        return None  # cheap reject before parsing anything
    try:
        parsed = json.loads(stored)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(parsed, list) or len(parsed) != 6 or parsed[5] != []:
        return None
    rewritten = json.dumps(parsed[:5], sort_keys=True, separators=(",", ":"))
    # Only when the six-element form was itself canonical. A key we cannot
    # reproduce is a key we do not understand, and rewriting it would destroy
    # an identity rather than restore one.
    if json.dumps(parsed, sort_keys=True, separators=(",", ":")) != stored:
        return None
    return rewritten


def _migrate_itemless_keys(conn: sqlite3.Connection) -> int:
    """Rewrite the deployed six-element itemless keys. Runs once per database.

    Gated on ``PRAGMA user_version`` so the scan is not paid on every open.
    Covers ``pending_requests`` AND ``request_suppressions``: fixing the pin
    alone leaves duplicate tabs and dead standing decisions, which is most of
    the damage.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= _SCHEMA_VERSION:
        return 0
    rewritten = 0
    try:
        for table in ("pending_requests", "request_suppressions"):
            rows = conn.execute(
                f"SELECT rowid, dedupe_key FROM {table} WHERE dedupe_key LIKE '%,[]]'"
            ).fetchall()
            for rowid, stored in rows:
                canonical = _canonical_itemless_key(str(stored or ""))
                if canonical is None:
                    continue
                try:
                    conn.execute(
                        f"UPDATE {table} SET dedupe_key = ? WHERE rowid = ?",
                        (canonical, rowid),
                    )
                except sqlite3.IntegrityError:
                    # A suppression is keyed on dedupe_key and the canonical
                    # one already exists. The canonical row is the live one, so
                    # drop the stale duplicate rather than fail the open.
                    conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
                rewritten += 1
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        # COMMIT HERE, not at the caller's exit. `_db` is opened re-entrantly --
        # `create_request` calls `get_request` while holding its own
        # transaction -- so leaving the marker uncommitted means the inner open
        # still sees version 0, tries to migrate, and deadlocks on the outer
        # connection's write lock. Committing makes the marker visible to the
        # nested open, which then skips.
        conn.commit()
    except sqlite3.OperationalError as exc:
        # Another connection is mid-write. The marker is unset, so the next
        # open retries; the rewrite is idempotent, so retrying is free. Never
        # fail a read because a one-time repair had to wait.
        logger.info("pending_requests: itemless key rewrite deferred: %s", exc)
        return 0
    if rewritten:
        logger.info(
            "pending_requests: rewrote %d itemless dedupe key(s) to their "
            "five-element form", rewritten,
        )
    return rewritten


def _db(universe_dir: Path) -> sqlite3.Connection:
    from tinyassets.storage.request_migration import ensure_protected

    conn = sqlite3.connect(ensure_protected(universe_dir), timeout=10.0)
    conn.executescript(_SCHEMA)
    _ensure_columns(conn)
    _migrate_itemless_keys(conn)
    return conn


@serialized
def create_request(
    universe_dir: Path,
    *,
    kind: str,
    title: str,
    body: str,
    fields: list[dict[str, Any]],
    action: dict[str, Any],
    dedupe_key: str,
    origin: str = ORIGIN_AGENT,
    items: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
    agent: str | None = None,
) -> dict[str, Any] | None:
    """Record one pending request. Returns the row, or None on storage failure.

    ``request_id`` is set by a caller that minted the id in platform-owned
    storage first (a pinned publish or install ask): the row is created under
    it and never deduplicated onto an existing row, whose id would come from
    this agent-writable store.

    ``agent`` is the asking agent, derived by the caller from the turn, never
    from the request's own text; its dedupe and suppressions are its own.

    Deduplicated on ``dedupe_key`` while pending, so an agent retrying the same
    ask does not open a second identical tab.

    ``items`` are the request's answerable parts, already validated by the
    caller (:func:`tinyassets.api.pending_requests._validated_items`). They are
    stored verbatim so the ids the agent chose are the ids it reads back.

    A returned row carries ``created``: ``True`` when this call inserted it,
    ``False`` when it deduplicated onto a tab that was already up. The caller
    strips it before answering -- it describes THIS call, not the request.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown request origin {origin!r}")
    from tinyassets.effectors.authenticated_external_call import _initiating_agent
    from tinyassets.request_answers import capture

    agent = (agent or _initiating_agent(universe_dir) or "main").strip() or "main"
    asking_context = capture(universe_dir, agent)
    dedupe_key = scoped_dedupe_key(dedupe_key, agent)
    try:
        with _db(universe_dir) as conn:
            # A user who said "don't ask me this again" must not be asked again.
            # The agent is TOLD, rather than silently ignored, so it can stop
            # trying and say so instead of looping.
            #
            settled = conn.execute(
                "SELECT feedback, decision, answer_json FROM request_suppressions "
                "WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            # A standing ALLOW is only meaningful when the agent can act on it.
            # For an action-bearing ask the ANSWER IS THE ACT -- nothing widens a
            # grant, deposits a key or removes one except answering the tab, and
            # the served agent has no route to any of them. So a suppressed
            # allow returned "standing yes, proceed" and then nothing happened:
            # harmless-looking on extend_http, and on remove_http it tells the
            # owner a credential is gone while it sits in the vault. The tab is
            # the mechanism, so for those the tab opens anyway.
            #
            # A standing DECLINE is untouched, and deliberately: "do not do this
            # and stop asking" is honoured by doing nothing, which needs no
            # route. Refusing it too would make "stop asking me to connect
            # GitHub" an ask that returns forever -- the trap the mute exists to
            # prevent, and an existing test said so.
            if (
                settled
                and (settled[1] or "declined") == "allowed"
                and str((action or {}).get("type") or "answer") != "answer"
            ):
                settled = None
            if settled:
                # The user already settled this. Hand back WHAT they decided so
                # the agent can act on a standing yes, instead of only learning
                # that it may not ask.
                return {
                    "settled": True,
                    "decision": settled[1] or "declined",
                    "feedback": settled[0] or "",
                    "answer": json.loads(settled[2]) if settled[2] else None,
                }
            existing = None if request_id else conn.execute(
                "SELECT request_id FROM pending_requests "
                "WHERE status = 'pending' AND dedupe_key = ? LIMIT 1",
                (dedupe_key,),
            ).fetchone()
            if existing:
                # The SAME tab, already up. `created` tells the caller which of
                # the two this is, because "a request was raised" and "the one
                # you raised before is still waiting" are different events --
                # only the first is something to notify the owner about.
                same = get_request(universe_dir, existing[0])
                return {**same, "created": False} if same else None
            row_id = request_id or "req_" + uuid.uuid4().hex[:24]
            conn.execute(
                "INSERT INTO pending_requests (request_id, kind, title, body, "
                "fields_json, action_json, items_json, dedupe_key, status, "
                "answer_json, created_at, resolved_at, origin, agent, asking_context_json) "
                "VALUES (?,?,?,?,?,?,?,?,'pending',NULL,?,NULL,?,?,?)",
                (
                    row_id,
                    kind,
                    title,
                    body,
                    json.dumps(fields),
                    json.dumps(action),
                    json.dumps(list(items or [])),
                    dedupe_key,
                    time.time(),
                    origin,
                    agent,
                    json.dumps(asking_context),
                ),
            )
        fresh = get_request(universe_dir, row_id)
        return {**fresh, "created": True} if fresh else None
    except Exception as exc:  # noqa: BLE001 - never break the turn that asked
        # Carry the REASON, do not just log it. On 2026-08-28 this returned a
        # bare None, the API turned it into the generic
        # ``request_storage_unavailable``, and the agent was told only that
        # storage was unavailable — so it retried the identical call, failed
        # identically, and stopped. The actual fault was one line
        # (``no such column: decision``) and sat only in a container log nobody
        # was tailing. A schema fault is not sensitive; withholding it just
        # costs an hour.
        logger.exception("pending_requests: create failed")
        return {"error": "request_storage_unavailable", "detail": str(exc)}


#: An item the owner has not acted on. ``pending`` while the request is still
#: open, ``unanswered`` once it closed without this item being touched. Derived,
#: never stored: a whole-request answer must not fabricate an item answer.
ITEM_PENDING = "pending"
ITEM_UNANSWERED = "unanswered"


def _item_answers(conn: sqlite3.Connection, request_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT item_id, status, answer_json, feedback, resolved_at "
        "FROM request_item_answers WHERE request_id = ?",
        (request_id,),
    ).fetchall()
    return {
        str(r[0]): {
            "status": str(r[1]),
            "answer": json.loads(r[2]) if r[2] else None,
            "feedback": r[3],
            "resolved_at": r[4],
        }
        for r in rows
    }


def _item_state(
    items: list[dict[str, Any]],
    answers: dict[str, dict[str, Any]],
    request_status: str,
) -> dict[str, dict[str, Any]]:
    """Per item, what came back -- kept SEPARATE from what was asked.

    ``items`` is projected verbatim because it is part of the tuple the dedupe
    key hashes and ``displayed_row_matches`` re-derives; folding answers into
    those objects would make the row stop reproducing what the owner was shown
    the moment they answered one item, and the request would refuse to execute.
    """
    untouched = ITEM_PENDING if request_status == "pending" else ITEM_UNANSWERED
    state = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "")
        state[item_id] = answers.get(item_id) or {
            "status": untouched, "answer": None, "feedback": None,
            "resolved_at": None,
        }
    return state


def _project(row: Any, answers: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    informational = json.loads(row[5] or "{}").get("type") == "notify"
    items = json.loads(row[13] or "[]") if len(row) > 13 else []
    if not isinstance(items, list):
        items = []
    return {
        "request_id": row[0],
        "kind": row[1],
        "title": row[2],
        "body": row[3],
        "fields": json.loads(row[4] or "[]"),
        "action": json.loads(row[5] or "{}"),
        "status": row[6],
        "answer": json.loads(row[7]) if row[7] else None,
        "created_at": row[8],
        "resolved_at": row[9],
        "feedback": row[10],
        "dedupe_key": row[11],
        "origin": row[12] or ORIGIN_AGENT,
        "agent": (row[14] if len(row) > 14 else None) or "main",
        "asking_context": json.loads(row[15]) if len(row) > 15 else {},
        "server_continuation": bool(len(row) > 15 and json.loads(row[15])),
        "items": items,
        "item_answers": _item_state(items, answers or {}, str(row[6])),
        "informational": informational,
        "requires_answer": not informational,
    }


_SELECT = (
    "SELECT request_id, kind, title, body, fields_json, action_json, status, "
    "answer_json, created_at, resolved_at, feedback, dedupe_key, origin, "
    "items_json, agent, asking_context_json FROM pending_requests"
)


def _projected(conn: sqlite3.Connection, rows: list[Any]) -> list[dict[str, Any]]:
    """Project rows, reading item answers only for rows that have items.

    A request with no items -- which is almost all of them -- costs no extra
    query, so adding items charges nothing to the rail's existing reads.
    """
    out = []
    for row in rows:
        has_items = bool(row[13] and row[13] not in ("[]", "null"))
        projected = _project(row, _item_answers(conn, str(row[0])) if has_items else None)
        if projected['action'].get('type') in {'connect', 'connect_http'}:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(pending_requests)')}
            if 'context_json' in columns:
                context = conn.execute(
                    'SELECT context_json FROM pending_requests WHERE request_id=?',
                    (projected['request_id'],)).fetchone()
                projected['server_continuation'] |= bool(context and json.loads(context[0]))
        if projected['action'].get('type') == 'approve_action':
            from tinyassets.bound_requests import RequestRefused, card
            original_factory = conn.row_factory
            conn.row_factory = sqlite3.Row
            try:
                projected.update(card(conn, projected['request_id']))
                projected['body'] = ''
            except (RequestRefused, sqlite3.OperationalError, IndexError):
                projected.update(title='A fresh protected preview is required', body='',
                                 action={'type': 'approve_action'}, phase='preview_required')
            finally:
                conn.row_factory = original_factory
        out.append(projected)
    return out


def get_request(universe_dir: Path, request_id: str) -> dict[str, Any] | None:
    try:
        with _db(universe_dir) as conn:
            row = conn.execute(
                f"{_SELECT} WHERE request_id = ?", (request_id,)
            ).fetchone()
            return _projected(conn, [row])[0] if row else None
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: get failed", exc_info=True)
        return None


def list_pending(universe_dir: Path) -> list[dict[str, Any]]:
    """EVERY pending row, oldest first: the rail reads top to bottom in the order asked.

    No page and no default page. A page size here was a cut on the owner's own
    queue: the connector passed its default ``limit=30`` straight through, so a
    31st request was never shown (2026-09-30), and the reconcile callers had to
    remember ``limit=None`` to see them all. A queue is read whole.

    A storage failure RAISES. It used to log and return ``[]``, which a caller
    cannot tell from "nothing is waiting on you", so an unreadable queue drew an
    empty rail. The owner surface has to be able to say it could not load.
    A universe that has never had a request has no store yet, and that one IS
    empty.
    """
    from tinyassets.agent_activities import store_path

    if not (Path(universe_dir) / _DB_NAME).exists() and not store_path(universe_dir).is_file():
        return []
    with _db(universe_dir) as conn:
        rows = conn.execute(
            f"{_SELECT} WHERE status IN ('pending','approved','unresolved','deferred') "
            "ORDER BY created_at ASC"
        ).fetchall()
        return _projected(conn, rows)


def find_by_action_type(
    universe_dir: Path, action_type: str, *, limit: int = 0
) -> list[dict[str, Any]]:
    """Every request of one ACTION TYPE, any status, newest first.

    For deciding whether a platform-seeded ask is owed. ``dedupe_key`` cannot
    answer that: it is a hash of the rendered text, so rewording a title makes
    every past answer stop matching and a user who already declined gets asked
    again on the next deploy. The action's type (and the identifier inside it) is
    what the decision was actually about.

    ``limit=0`` means NO limit, and it is the default. A capped history is not a
    sound basis for "has this already been decided": with a cap of 50, fifty
    newer asks of the same type hid an earlier refusal and the platform re-asked
    it (gpt-6-astra refute round on PR #4121). The result set is bounded by the
    number of distinct asks of one action type, which is small by construction,
    not by how long the universe has existed.

    ``LIKE`` is a cheap prefilter over the JSON column; the type is then compared
    exactly against the parsed action, so a request whose BODY happens to quote
    the type never matches. The prefilter reads ``action_json`` only, so nothing
    a user or agent writes in a title or body can reach it.
    """
    if not action_type:
        return []
    sql = f"{_SELECT} WHERE action_json LIKE ? ORDER BY created_at DESC"
    params: tuple[Any, ...] = (f'%"{action_type}"%',)
    if limit:
        sql += " LIMIT ?"
        params += (max(1, int(limit)),)
    try:
        with _db(universe_dir) as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: find_by_action_type failed", exc_info=True)
        return []
    found = [_project(row) for row in rows]
    return [row for row in found if (row["action"] or {}).get("type") == action_type]


@serialized
def retire_platform_request(
    universe_dir: Path, request_id: str, *, reason: str = ""
) -> bool:
    """The PLATFORM takes down an ask of its own that no longer applies.

    The twin of ``withdraw_request``, which moves only ``origin='agent'`` rows so
    the agent cannot clear an ask raised for it. This moves only
    ``origin='platform'`` rows, for the mirror-image reason: when the thing the
    platform was offering changes, the obsolete card has to go, and neither
    ``answered`` nor ``dismissed`` is honest about it -- the owner decided
    nothing. Writes no standing decision and emits no answered event, so nothing
    downstream reads it as a reply.
    """
    try:
        with _db(universe_dir) as conn:
            cur = conn.execute(
                "UPDATE pending_requests SET status = 'withdrawn', feedback = ?, "
                "resolved_at = ? WHERE request_id = ? AND status = 'pending' "
                "AND origin = ?",
                (reason or None, time.time(), request_id, ORIGIN_PLATFORM),
            )
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: retire_platform failed", exc_info=True)
        return False


@serialized
def resolve_request(
    universe_dir: Path,
    request_id: str,
    *,
    status: str,
    answer: dict[str, Any] | None = None,
    feedback: str = "",
    dont_ask_again: bool = False,
    decision: str = "",
) -> bool:
    """Close a request. Only a PENDING row moves, so one answer counts once.

    ``answer`` holds the NON-secret field values, for the agent to read back.
    Secret values never reach this function.
    """
    if status not in {"answered", "dismissed"}:
        return False
    try:
        with _db(universe_dir) as conn:
            # An itemised request reads back as ONE answer whichever way the
            # owner worked through it, so whatever items they already answered
            # ride into the closing answer. Items they never touched contribute
            # nothing -- the projection derives `unanswered` for those, rather
            # than this inventing a value for them.
            stored = _item_answers(conn, request_id)
            merged = dict(answer) if answer else {}
            if stored:
                merged["items"] = {
                    item_id: {"status": state["status"], "answer": state["answer"],
                              "feedback": state["feedback"]}
                    for item_id, state in stored.items()
                }
            cur = conn.execute(
                "UPDATE pending_requests SET status = ?, answer_json = ?, "
                "feedback = ?, resolved_at = ? "
                "WHERE request_id = ? AND status = 'pending'",
                (
                    status,
                    json.dumps(merged) if merged else None,
                    feedback or None,
                    time.time(),
                    request_id,
                ),
            )
            if cur.rowcount <= 0:
                return False
            from tinyassets.connection_continuations import answered

            answered(conn, request_id, decision or status)
            from tinyassets.request_answers import enqueue

            enqueue(conn, request_id, {"status": status, "decision": decision,
                                      "answer": merged, "feedback": feedback})
            row = conn.execute(
                "SELECT kind, title, dedupe_key FROM pending_requests "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if dont_ask_again:
                if row:
                    conn.execute(
                        "INSERT OR REPLACE INTO request_suppressions "
                        "(dedupe_key, kind, title, feedback, decision, "
                        "answer_json, created_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            row[2], row[0], row[1], feedback or None,
                            decision or ("allowed" if status == "answered"
                                         else "declined"),
                            json.dumps(answer) if answer else None,
                            time.time(),
                        ),
                    )
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: resolve failed", exc_info=True)
        return False
    # Every surface answers through here, so one emit wakes a subscribed
    # branch whichever surface the owner used. Never raises.
    from tinyassets.automation_events import emit_pending_request_answered

    emit_pending_request_answered(
        universe_dir, request_id=request_id,
        kind=str(row[0]) if row else "", status=status,
    )
    # And take it off the owner's OTHER devices. Same seam as the emit, for the
    # same reason: every surface resolves through here, so one call covers them
    # all and there is one definition of when a notification is cleared.
    from tinyassets.owner_notifications import clear_for_universe_dir

    clear_for_universe_dir(universe_dir, request_id=request_id)
    # An activity waiting on this request goes back in the queue (harness D2).
    _requeue_waiting_activity(universe_dir, request_id)
    return True


def _requeue_waiting_activity(universe_dir: Path, request_id: str) -> None:
    try:
        from tinyassets import agent_activities

        if agent_activities.answered_request(universe_dir, request_id):
            from tinyassets.activity_dispatcher import tick_in_background

            tick_in_background(Path(universe_dir).parent)
    except Exception:  # noqa: BLE001 - the answer stands; the tick re-queues later
        logger.warning("pending_requests: activity re-queue failed", exc_info=True)


@serialized
def resolve_item(
    universe_dir: Path,
    request_id: str,
    item_id: str,
    *,
    status: str,
    answer: dict[str, Any] | None = None,
    feedback: str = "",
) -> dict[str, Any]:
    """Resolve ONE item of a pending request. One answer per item, ever.

    The request stays ``pending`` unless this was its last unresolved item, in
    which case it closes as ``answered`` in the same transaction -- so there is
    no window where every item is answered and the tab is still up.

    Returns ``{"item_id", "status", "request_status", "remaining"}``, or an
    ``error`` envelope naming why nothing moved. A second answer for the same
    item reports ``item_already_resolved`` and does NOT overwrite the first:
    the PRIMARY KEY is the guarantee, not the check above it.
    """
    if status not in {"answered", "dismissed"}:
        return {"error": "item_status_invalid", "detail": "answered or dismissed"}
    closed = False
    try:
        with _db(universe_dir) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, items_json, kind FROM pending_requests "
                "WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return {"error": "not_found", "resource": "pending_request"}
            if str(row[0]) != "pending":
                return {"error": "already_resolved", "status": str(row[0])}
            try:
                items = json.loads(row[1] or "[]")
            except (json.JSONDecodeError, TypeError):
                items = []
            ids = [
                str(i.get("item_id") or "") for i in items if isinstance(i, dict)
            ]
            if item_id not in ids:
                # Uniform with the request-level miss: an id that is not on this
                # request is simply not found, so probing ids learns nothing.
                return {"error": "not_found", "resource": "request_item"}
            cur = conn.execute(
                "INSERT OR IGNORE INTO request_item_answers "
                "(request_id, item_id, status, answer_json, feedback, resolved_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    request_id, item_id, status,
                    json.dumps(answer) if answer else None,
                    feedback or None, time.time(),
                ),
            )
            if cur.rowcount <= 0:
                return {"error": "item_already_resolved", "item_id": item_id}
            resolved = {
                str(r[0]) for r in conn.execute(
                    "SELECT item_id FROM request_item_answers WHERE request_id = ?",
                    (request_id,),
                )
            }
            remaining = [i for i in ids if i not in resolved]
            if not remaining:
                answers = _item_answers(conn, request_id)
                conn.execute(
                    "UPDATE pending_requests SET status = 'answered', "
                    "answer_json = ?, resolved_at = ? "
                    "WHERE request_id = ? AND status = 'pending'",
                    (
                        json.dumps({"items": {
                            k: {"status": v["status"], "answer": v["answer"],
                                "feedback": v["feedback"]}
                            for k, v in answers.items()
                        }}),
                        time.time(), request_id,
                    ),
                )
                closed = True
            kind = str(row[2] or "")
            from tinyassets.request_answers import enqueue

            enqueue(conn, request_id, {"item_id": item_id, "status": status,
                                      "answer": answer, "feedback": feedback},
                    key="item:" + item_id)
    except Exception as exc:  # noqa: BLE001 - report the reason, never break the turn
        logger.warning("pending_requests: resolve_item failed", exc_info=True)
        return {"error": "request_storage_unavailable", "detail": str(exc)}
    # ONE emit, carrying the item. A closing item would otherwise wake an
    # unfiltered subscription twice for a single act.
    from tinyassets.automation_events import emit_pending_request_answered

    emit_pending_request_answered(
        universe_dir, request_id=request_id, kind=kind,
        status="answered" if closed else "pending", item_id=item_id,
    )
    # Only a CLOSING item clears the notification. A notification names the
    # REQUEST, so answering one item of fifty changes nothing a device is
    # displaying, and pushing a silent clear per item made a 50-item note cost
    # 51 wakeups per device (gpt-6-astra, 2026-09-29). Per-item state is what
    # the rail shows when the app is opened.
    if closed:
        from tinyassets.owner_notifications import clear_for_universe_dir

        clear_for_universe_dir(universe_dir, request_id=request_id)
    return {
        "item_id": item_id,
        "status": status,
        "request_status": "answered" if closed else "pending",
        "remaining": len(remaining),
    }


@serialized
def withdraw_request(
    universe_dir: Path, request_id: str, *, reason: str = ""
) -> dict[str, Any]:
    """The agent takes back an ask it raised and no longer needs.

    One guarded UPDATE: only a row that is still ``pending`` AND was raised by
    the agent moves, so a withdrawal racing the owner's answer cannot both win,
    and a platform-raised ask stays up. Writes no standing decision, so the
    agent may ask again later. On a miss, says why.
    """
    try:
        with _db(universe_dir) as conn:
            cur = conn.execute(
                "UPDATE pending_requests SET status = 'withdrawn', feedback = ?, "
                "resolved_at = ? WHERE request_id = ? AND status = 'pending' "
                "AND origin = ?",
                (reason or None, time.time(), request_id, ORIGIN_AGENT),
            )
            moved = cur.rowcount > 0
    except Exception as exc:  # noqa: BLE001 - report, never raise into the turn
        logger.warning("pending_requests: withdraw failed", exc_info=True)
        return {"error": "request_storage_unavailable", "detail": str(exc)}
    row = get_request(universe_dir, request_id)
    if moved and row is not None:
        # A withdrawn ask's notification is STALE, so it comes down. Leaving it
        # up meant the phone still showed a request that no longer existed, and
        # tapping it opened nothing (gpt-6-astra, 2026-09-30). Withdrawal used
        # to deliberately skip this, but that was to protect a per-device latch
        # that no longer exists; there is no reason left not to clear.
        from tinyassets.owner_notifications import clear_for_universe_dir

        clear_for_universe_dir(universe_dir, request_id=request_id)
        return row
    if row is None:
        return {"error": "not_found", "resource": "pending_request"}
    if row["status"] != "pending":
        return {"error": "already_resolved", "status": row["status"]}
    return {"error": "not_withdrawable", "origin": row["origin"],
            "detail": "this ask was raised by the platform, not by you"}


def update_publication_preview(universe_dir: Path, request_id: str, *,
                               completion: dict[str, Any]) -> bool:
    """Enrich exactly the resolved publish receipt, then wake its existing readers.

    A single SQLite transaction serializes this metadata-only update. Do not
    reacquire the approval's owner-control lock on the background thread.
    """
    with _db(universe_dir) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT answer_json, kind, action_json FROM pending_requests "
            "WHERE request_id=? AND status='answered'", (request_id,),
        ).fetchone()
        if row is None or json.loads(row[2]).get("type") != "publish":
            return False
        answer = json.loads(row[0] or "{}")
        previous = answer.get("completion", {})
        if (previous.get("listing_id") != completion["listing_id"]
                or previous.get("preview_status") != "pending"):
            return False
        answer["completion"] = completion
        conn.execute("UPDATE pending_requests SET answer_json=?, resolved_at=? WHERE request_id=?",
                     (json.dumps(answer), time.time(), request_id))
    from tinyassets.automation_events import emit_pending_request_answered

    emit_pending_request_answered(universe_dir, request_id=request_id,
                                  kind=str(row[1]), status="answered")
    _requeue_waiting_activity(universe_dir, request_id)
    return True


def list_resolved(universe_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Recently answered requests — how the agent reads what it was told."""
    try:
        with _db(universe_dir) as conn:
            rows = conn.execute(
                f"{_SELECT} WHERE status != 'pending' "
                "ORDER BY resolved_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            return _projected(conn, rows)
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: list_resolved failed", exc_info=True)
        return []


@serialized
def record_unmute(universe_dir: Path, dedupe_key: str) -> None:
    """Record that a mute was lifted, so the lift is visible in the rail."""
    try:
        with _db(universe_dir) as conn:
            conn.execute(
                "INSERT INTO request_unmutes (dedupe_key, lifted_at) VALUES (?,?)",
                (dedupe_key, time.time()),
            )
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: record_unmute failed", exc_info=True)


def list_unmutes(universe_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    try:
        with _db(universe_dir) as conn:
            rows = conn.execute(
                "SELECT dedupe_key, lifted_at FROM request_unmutes "
                "ORDER BY lifted_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [{"dedupe_key": r[0], "lifted_at": r[1]} for r in rows]
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: list_unmutes failed", exc_info=True)
        return []


def list_suppressions(universe_dir: Path) -> list[dict[str, Any]]:
    """What the user has said not to be asked again — visible, so it is undoable.

    Every row: a 50-row cap hid the 51st decision, which the owner then could
    not see to undo. Model doors page this (``engine_read_views.project_access``).
    """
    try:
        with _db(universe_dir) as conn:
            rows = conn.execute(
                "SELECT dedupe_key, kind, title, feedback, decision, created_at "
                "FROM request_suppressions ORDER BY created_at DESC"
            ).fetchall()
        return [
            {"dedupe_key": r[0], "kind": r[1], "title": r[2],
             "feedback": r[3], "decision": r[4], "created_at": r[5]}
            for r in rows
        ]
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: list_suppressions failed", exc_info=True)
        return []


@serialized
def unsuppress(universe_dir: Path, dedupe_key: str) -> bool:
    """Undo a "don't ask again". A standing refusal the user cannot lift is a trap."""
    try:
        with _db(universe_dir) as conn:
            cur = conn.execute(
                "DELETE FROM request_suppressions WHERE dedupe_key = ?", (dedupe_key,)
            )
            return cur.rowcount > 0
    except Exception:  # noqa: BLE001
        logger.warning("pending_requests: unsuppress failed", exc_info=True)
        return False


__all__ = [
    "FIELD_TYPES",
    "ITEM_PENDING",
    "ITEM_UNANSWERED",
    "MAX_ITEMS",
    "create_request",
    "find_by_action_type",
    "get_request",
    "list_pending",
    "list_resolved",
    "list_suppressions",
    "list_unmutes",
    "record_unmute",
    "resolve_item",
    "resolve_request",
    "retire_platform_request",
    "unsuppress",
    "withdraw_request",
]
