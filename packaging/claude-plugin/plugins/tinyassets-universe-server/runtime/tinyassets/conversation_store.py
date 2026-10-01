"""Durable, session-anchored conversation store for the stateless universe turn.

Why this exists
---------------
The universe turn is rebuilt from scratch every call (a fresh ``claude -p`` with
the persona system prompt + the current message only). The 2026-08-08 hotfix gave
the *Slack* path a sliding window pulled from ``conversations.history`` each turn,
but that left three gaps u-tiny named itself (Slack thread 1786225160):

* the MCP ``converse`` path had **no memory at all**;
* nothing was **durable** — the Slack pull re-fetches every turn and depends on
  bot-token scopes + rate limits, so the platform owned no copy of its own
  conversation;
* there was no **session-anchored** ``(session_id, turn, role, content)`` store,
  so nothing beyond the window survived and no summary layer could be built.

This is that layer — the Vercel ``ChatbotMessagePersistence`` shape applied here:
**thread identity → persistent message store → reconstruction at turn start.**
It is the pure storage half; the bounded, untrusted-fenced *rendering* stays in
:mod:`tinyassets.conversation_memory` (already Codex-reviewed 2026-08-08). Callers
load prior turns, run the turn, then record both sides.

Contract
--------
* Per-universe isolation: one SQLite file at ``<universe_dir>/.conversation_memory.db``.
* Keyed by ``session_id`` (``slack:<channel>`` / ``converse:<universe_id>:<actor>``);
  ``turn_no`` is per-session and monotonic. ``speaker`` is DISPLAY metadata only
  (Founder vs the universe's own voice) — it is never read as authentication.
* **Memory is never consent** — stored text and optional reply-owned model
  observations grant no authority; the fenced
  not-consent formatter and the fresh-consent gate live elsewhere and are
  unchanged.
* **Best-effort, single boundary**: every function catches its own storage
  errors, logs, and degrades to "no memory this turn" (matching the hotfix
  contract) — it never raises, so callers do NOT re-wrap it (that would just
  double-log). This is the one place the fail-loud rule yields, because memory is
  a bonus layer and a blank window is strictly better than dropping the answer.

Not the custody store
---------------------
The user-controlled privacy/export/delete custody store (behind one-use
Ed25519-signed per-operation grants) is a SEPARATE concern, and this module is
deliberately NOT a consumer of it. Using that store for every-turn working
memory would mean minting + consuming a signed grant per turn — the wrong tool.
Authorization for the turn is the founder gate on the
caller (``converse`` is founder-only; history injection is founder-gated), and
per-universe DB isolation inherits the universe dir's filesystem boundary. If a
future authenticated app-conversation authority owner ships, this store is where
it integrates; until then it stays deliberately lightweight.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from tinyassets.conversation_failure import (
    TurnFailure,
    failure_column_sql,
    failure_notice,
    normalize_turn_failure,
    read_turn_failure,
    turn_failure,
)
from tinyassets.conversation_memory import DEFAULT_LIMIT, Msg
from tinyassets.providers.execution_receipt import ExecutionReceipt, normalize_execution_receipt

logger = logging.getLogger(__name__)

#: One SQLite file per universe, alongside its vault/soul.
_DB_NAME = ".conversation_memory.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    turn_no    INTEGER NOT NULL,
    speaker    TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    ts         REAL    NOT NULL,
    ext_id     TEXT    NOT NULL DEFAULT '',
    execution_json TEXT NOT NULL DEFAULT '',
    failure_json TEXT NOT NULL DEFAULT '',
    UNIQUE(session_id, turn_no)
);
CREATE INDEX IF NOT EXISTS ix_turns_session
    ON conversation_turns(session_id, turn_no);
CREATE TABLE IF NOT EXISTS conversation_backfill (
    session_id TEXT PRIMARY KEY,
    ts         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_learned (
    session_id    TEXT PRIMARY KEY,
    settled_turn  INTEGER NOT NULL,
    ts            REAL    NOT NULL
);
"""

#: Same-process writers (the daemon serves both the Slack ingress and the MCP
#: converse paths in one process) are serialized per db file for a clean
#: turn_no; the UNIQUE(session_id, turn_no) constraint + retry is the
#: cross-process backstop.
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(db_path: Path) -> threading.Lock:
    key = str(db_path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _db_path(universe_dir: "str | Path") -> Path:
    return Path(universe_dir) / _DB_NAME


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    # Migrate a pre-ext_id table (older DB that predates the stable-id column).
    # Idempotent: a DB that already has the column raises "duplicate column name",
    # which we swallow QUIETLY. Any OTHER OperationalError (a locked/corrupt DB)
    # must be VISIBLE — swallowing it silently would disable ext_id reconciliation
    # forever with no diagnostic (Codex 2026-08-10). `ext_id` is the raw Slack
    # message ts — the STABLE identity sync_tail dedups on.
    try:
        conn.execute("ALTER TABLE conversation_turns ADD COLUMN ext_id TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            logger.warning("conversation_store: ext_id migration failed: %s", exc)
    # Stable-id uniqueness — the DB-level guarantee that no ext_id is ever stored
    # twice per session, so a re-synced/raced timeline cannot duplicate a turn
    # regardless of the dedup logic above it (Codex FIX2/NEW1 2026-08-10). PARTIAL
    # so the many id-less ('') rows (live-recorded founder turns) never collide.
    # Tolerated if a pre-existing DB already holds a dup: log, don't break the store.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_turns_extid "
            "ON conversation_turns(session_id, ext_id) WHERE ext_id != ''"
        )
    except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
        logger.warning(
            "conversation_store: ext_id uniqueness index not created: %s", exc
        )
    try:
        conn.execute(
            "ALTER TABLE conversation_turns ADD COLUMN execution_json TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            logger.warning("conversation_store: execution receipt migration failed: %s", exc)
    try:
        conn.execute(
            "ALTER TABLE conversation_turns ADD COLUMN failure_json TEXT NOT NULL DEFAULT ''"
        )
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            logger.warning("conversation_store: failure metadata migration failed: %s", exc)
    return conn


def _has_execution_column(conn: sqlite3.Connection) -> bool:
    # A read-only caller must never run DDL or lose a legacy transcript.
    columns = conn.execute("PRAGMA table_info(conversation_turns)")
    return any(row[1] == "execution_json" for row in columns)


def _read_messages(conn: sqlite3.Connection, session_id: str, limit: int,
                   before: int | None = None) -> list[Msg]:
    receipt_column = "execution_json" if _has_execution_column(conn) else "''"
    failure_column = failure_column_sql(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_turns)")}
    identity_column = "ext_id" if "ext_id" in columns else "''"
    # A store old enough to lack the key reports NO handle rather than a
    # position-derived stand-in: an expansion that cannot be keyed says so.
    id_column = "id" if "id" in columns else "NULL"
    has_projections = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                   "AND name='conversation_terminal_projections'").fetchone()
    select = (f"SELECT speaker, content, ts, {receipt_column}, {failure_column}, "
              f"{identity_column}, turn_no, {id_column} FROM conversation_turns ")
    if before is None:
        rows = conn.execute(
            select + "WHERE session_id = ? ORDER BY ts DESC, turn_no DESC LIMIT ?",
            (session_id, max(1, int(limit))),
        ).fetchall()
    else:
        # Keyset: strictly older than the cursor turn, in the same order the
        # first page used, so paging back returns every turn exactly once.
        rows = conn.execute(
            select + "WHERE session_id = ? AND (ts, turn_no) < "
            "(SELECT ts, turn_no FROM conversation_turns WHERE id = ? AND session_id = ?) "
            "ORDER BY ts DESC, turn_no DESC LIMIT ?",
            (session_id, int(before), session_id, max(1, int(limit))),
        ).fetchall()
    result = []
    for speaker, content, ts, raw, failure_raw, ext_id, turn_no, row_id in reversed(rows):
        receipt = None
        if speaker == "universe" and isinstance(raw, str) and 0 < len(raw) <= 4096:
            try:
                normalized = normalize_execution_receipt(json.loads(raw))
                if normalized is not None:
                    receipt = ExecutionReceipt(**normalized)
            except (ValueError, RecursionError):
                pass  # Corrupt optional metadata never discards the message text.
        parts = ext_id.split(":") if isinstance(ext_id, str) else []
        consumer_id = None
        if (has_projections and len(parts) == 3 and parts[0] == "consumer"
                and parts[2] in {"reply", "founder"} and len(parts[1]) == 32
                and all(c in "0123456789abcdef" for c in parts[1])):
            position = "founder_turn_no" if parts[2] == "founder" else "reply_turn_no"
            expected_speakers = {"founder"} if parts[2] == "founder" else {"universe", "platform"}
            if speaker in expected_speakers and conn.execute(
                "SELECT 1 FROM conversation_terminal_projections WHERE admission_id=? "
                f"AND session_id=? AND {position}=?", (parts[1], session_id, turn_no),
            ).fetchone():
                consumer_id = parts[1]
        result.append(Msg(str(speaker or ""), str(content or ""), _coerce_ts(ts), receipt,
                          read_turn_failure(speaker, failure_raw), consumer_id,
                          id=row_id if isinstance(row_id, int) else None))
    return result


def read_history_page(
    universe_dir: "str | Path",
    session_id: str,
    *,
    limit: int,
    before: int | None = None,
) -> tuple[list["Msg"], bool]:
    """One page of the owner's thread, oldest first, and whether older turns exist.

    The owner's history read. Unlike ``load_recent_readonly`` it does NOT fail
    open: a read error RAISES, because "your history could not be read" and
    "you have no history" are different things to tell an owner. ``before`` is
    a turn id (``Msg.id``) from a previous page; the page holds the ``limit``
    turns strictly older than it. The caller names ``limit``: there is no
    default page, and ``has_more`` always says whether one exists.
    """
    if not session_id:
        return [], False
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if before is not None and (type(before) is not int or before < 0):
        raise ValueError("before must be a turn id")
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return [], False
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    try:
        messages = _read_messages(conn, session_id, limit + 1, before=before)
    finally:
        conn.close()
    has_more = len(messages) > limit
    return (messages[1:] if has_more else messages), has_more


def load_recent_readonly(
    universe_dir: "str | Path",
    session_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list["Msg"]:
    """Read-only sibling of ``load_recent`` for pure-read callers (get_status).

    Opens the store with SQLite ``mode=ro`` and runs NO schema DDL/migration, so
    observing the transcript never mutates the DB (the ``_connect`` path runs
    ``executescript``/``ALTER``/``CREATE INDEX`` and would violate a pure-read
    contract). Empty on ANY trouble — missing store, missing table, or read error
    — exactly like ``load_recent``'s fail-open contract.
    """
    if not session_id:
        return []
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            messages = _read_messages(conn, session_id, limit)
        finally:
            conn.close()
        return messages
    except Exception:  # noqa: BLE001 - read-only peek is a bonus, never a blocker
        return []


def record_turn(
    universe_dir: "str | Path",
    session_id: str,
    speaker: str,
    text: str,
    *,
    ts: float | None = None,
    ext_id: str = "",
    failure: object = None,
) -> int:
    """Append one turn; return its per-session ``turn_no`` (0 if not recorded).

    Blank text is a no-op (returns 0). Best-effort: any storage failure logs and
    returns 0 rather than raising, so a memory hiccup never breaks the reply. A
    malformed ``ts`` degrades to "now" rather than raising (a bad ts must never
    cost the turn). ``ext_id`` is a stable external identity (the Slack message
    ts) used for dedup; "" when unknown.

    ``failure`` attaches the same platform-owned metadata ``record_failure``
    stores, for the one case that has no founder half to pair with: a turn whose
    server died, noticed at the next startup, where nothing ever persisted what
    the founder typed. It is retained only for a ``platform`` row, which is what
    ``read_turn_failure`` will read back, and degrades to text-only on a store
    with no ``failure_json`` column -- the same degradation ``record_failure``
    already documents.
    """
    if not isinstance(text, str) or not text.strip():
        return 0
    if not session_id:
        return 0
    normalized_failure = normalize_turn_failure(failure) if speaker == "platform" else None
    # Setup can raise too (a custom ext_id with a raising __str__, a bad-type
    # universe_dir in _db_path), and this runs OUTSIDE the retry try below, so
    # guard it — record_turn's contract is NEVER to raise into the turn
    # (Codex 2026-08-10).
    try:
        when = _when(ts)
        ext_id = str(ext_id or "")
        db_path = _db_path(universe_dir)
        lock = _lock_for(db_path)
    except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
        logger.warning("conversation_store: record setup failed", exc_info=True)
        return 0
    for attempt in range(6):
        try:
            with lock:
                conn = _connect(db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    # Stable-id idempotency: if this ext_id is already stored for
                    # the session, it's a re-sync / concurrent-sync repeat, NOT a
                    # new turn. BEGIN IMMEDIATE serialises writers (in- AND cross-
                    # process), so this check + insert is atomic — closing the
                    # read-before-write window that let two syncs both persist the
                    # same reply (Codex FIX2/NEW1 2026-08-10). Return 0 QUIETLY:
                    # nothing was appended, but nothing was dropped either.
                    if ext_id:
                        dup = conn.execute(
                            "SELECT 1 FROM conversation_turns "
                            "WHERE session_id = ? AND ext_id = ? LIMIT 1",
                            (session_id, ext_id),
                        ).fetchone()
                        if dup is not None:
                            conn.commit()
                            return 0
                    row = conn.execute(
                        "SELECT COALESCE(MAX(turn_no), 0) + 1 "
                        "FROM conversation_turns WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                    turn_no = int(row[0])
                    # Column names are fixed internal constants, never caller data.
                    columns = "session_id, turn_no, speaker, content, ts, ext_id"
                    placeholders = "?, ?, ?, ?, ?, ?"
                    values = (session_id, turn_no, str(speaker or ""), text, when, ext_id)
                    if normalized_failure is not None and (
                        failure_column_sql(conn) == "failure_json"
                    ):
                        columns += ", failure_json"
                        placeholders += ", ?"
                        values += (json.dumps(normalized_failure),)
                    conn.execute(
                        f"INSERT INTO conversation_turns ({columns}) VALUES ({placeholders})",
                        values,
                    )
                    conn.commit()
                    return turn_no
                finally:
                    conn.close()
        except sqlite3.OperationalError as exc:  # locked/busy across processes
            msg = str(exc).lower()
            transient = "lock" in msg or "busy" in msg
            if transient and attempt < 5:
                time.sleep(0.02 * (attempt + 1))
                continue
            # A non-transient OperationalError (e.g. a missing universe dir in a
            # test) is permanent — fail fast rather than burning the retry budget.
            logger.warning("conversation_store: record failed: %s", exc)
            return 0
        except sqlite3.IntegrityError as exc:  # turn_no race → retry; ext_id → stored
            # The partial UNIQUE(session_id, ext_id) index is the cross-process
            # backstop to the in-transaction check above: if it fires, the turn is
            # already stored — return 0 QUIETLY, never burn retries or warn (Codex
            # FIX2/NEW1 2026-08-10). Only a turn_no collision is a real race.
            if "ext_id" in str(exc).lower():
                return 0
            if attempt < 5:
                time.sleep(0.02 * (attempt + 1))
                continue
            # Retries exhausted on the turn_no race. Best-effort still means the
            # reply survives, but a DROPPED record is exactly what silently
            # drifts the store behind the live thread — so it must be VISIBLE
            # (WARNING), never a silent return, or a future regression hides here.
            logger.warning(
                "conversation_store: record dropped after %d turn_no races "
                "for session %s (store may drift behind the live thread)",
                attempt + 1,
                session_id,
            )
            return 0
        except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
            logger.warning("conversation_store: record failed", exc_info=True)
            return 0
    # Only reached if every attempt was a transient retry that never resolved.
    logger.warning(
        "conversation_store: record failed after %d attempts for session %s",
        6,
        session_id,
    )
    return 0


#: NOTHING here deletes a turn. The transcript at rest is the user's own record,
#: and a platform that silently drops its older half is not keeping it. The
#: 400-turn retention ceiling that used to live here deleted the oldest turns on
#: every exchange; it is gone (founder, 2026-09-30 -- an account's only limits are
#: the cloud bytes it occupies and its concurrent agent seats, and stored turns
#: are bytes, charged to tier storage like everything else).
#:
#: What a model is SENT is still bounded -- `DEFAULT_LIMIT` turns and a character
#: budget at render time. Trimming context is a prompt-shaping decision; deleting
#: history is a data-loss decision, and only the user makes that one (account
#: deletion, or an explicit per-session delete).


def record_exchange(
    universe_dir: "str | Path",
    session_id: str,
    founder_text: str,
    universe_text: str,
    *,
    ts: float | None = None,
    execution: object = None,
    interjections: "tuple[tuple[str, float], ...] | list[tuple[str, float]]" = (),
) -> bool:
    """Append a founder turn AND the universe's reply in ONE transaction.

    ``interjections`` are the owner's messages that reached the agent while it
    worked (harness S2 steering), as ``(text, sent_at)``. They are stored as
    founder turns between the message and the reply, in the order sent.

    Two independent ``record_turn`` calls can leave a founder-only half-turn
    when the second write fails (Codex 2026-08-22 #2); here both rows commit
    together or not at all. Nothing is deleted. Best-effort by contract:
    returns False and logs on any failure, never raises.
    """
    return _record_pair(universe_dir, session_id, founder_text, universe_text,
                        speaker="universe", ts=ts, execution=execution,
                        interjections=interjections)


def record_failure(
    universe_dir: "str | Path", session_id: str, founder_text: str, code: object,
    *, ts: float | None = None,
) -> bool:
    """Save the composed platform notice plus original text, without an answer receipt.

    ``code`` is a class or a full :class:`TurnFailure` record; the stored text
    is composed from the same record the metadata column keeps. True confirms
    the pair, not optional metadata: writable legacy stores can retain
    text-only platform rows when an additive migration is unavailable.
    """
    failure = code if isinstance(code, TurnFailure) else turn_failure(code)
    return _record_pair(universe_dir, session_id, founder_text, failure_notice(failure),
                        speaker="platform", ts=ts, failure=failure)


def _record_pair(
    universe_dir, session_id, founder_text, universe_text, *, speaker, ts=None,
    execution=None, failure=None, interjections=(),
) -> bool:
    """The shared transaction and retry boundary for terminal pairs."""
    if not session_id or not isinstance(founder_text, str) or not founder_text.strip():
        return False
    if not isinstance(universe_text, str) or not universe_text.strip():
        return False
    try:
        when = _when(ts)
        normalized = normalize_execution_receipt(execution)
        execution_json = (
            json.dumps(normalized, ensure_ascii=False) if normalized is not None else ""
        )
        normalized_failure = normalize_turn_failure(failure)
        failure_json = json.dumps(normalized_failure) if normalized_failure is not None else ""
        # Reads order by time, so the founder's message sits no later than the
        # first interjection and every interjection no later than the reply.
        between = [
            (str(text), min(_when(sent_at), when)) for text, sent_at in interjections
            if isinstance(text, str) and text.strip()
        ]
        founder_when = min([when, *(sent_at for _text, sent_at in between)])
        db_path = _db_path(universe_dir)
        lock = _lock_for(db_path)
    except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
        logger.warning("conversation_store: exchange setup failed", exc_info=True)
        return False
    for attempt in range(6):
        try:
            with lock:
                conn = _connect(db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT COALESCE(MAX(turn_no), 0) + 1 "
                        "FROM conversation_turns WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                    turn_no = int(row[0])
                    rows = [(session_id, turn_no, "founder", founder_text, founder_when)]
                    rows += [
                        (session_id, turn_no + 1 + index, "founder", text, sent_at)
                        for index, (text, sent_at) in enumerate(between)
                    ]
                    rows.append(
                        (session_id, turn_no + 1 + len(between), speaker, universe_text, when)
                    )
                    # Column names are fixed internal constants, never caller data.
                    # Text-only fallback preserves the speaker discriminator.
                    columns = "session_id, turn_no, speaker, content, ts, ext_id"
                    placeholders = "?, ?, ?, ?, ?, ''"
                    if _has_execution_column(conn):
                        columns += ", execution_json"
                        placeholders += ", ?"
                        rows = [(*row, "") for row in rows[:-1]] + [(*rows[-1], execution_json)]
                    if failure_column_sql(conn) == "failure_json":
                        columns += ", failure_json"
                        placeholders += ", ?"
                        rows = [(*row, "") for row in rows[:-1]] + [(*rows[-1], failure_json)]
                    conn.executemany(
                        f"INSERT INTO conversation_turns ({columns}) VALUES ({placeholders})", rows,
                    )
                    conn.commit()
                    return True
                finally:
                    conn.close()
        except (sqlite3.OperationalError, sqlite3.IntegrityError) as exc:
            msg = str(exc).lower()
            if ("lock" in msg or "busy" in msg or "unique" in msg) and attempt < 5:
                time.sleep(0.02 * (attempt + 1))
                continue
            logger.warning("conversation_store: exchange failed: %s", exc)
            return False
        except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
            logger.warning("conversation_store: exchange failed", exc_info=True)
            return False
    logger.warning("conversation_store: exchange failed after 6 attempts for %s", session_id)
    return False


def load_recent(
    universe_dir: "str | Path",
    session_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[Msg]:
    """Return the last ``limit`` turns for ``session_id``, oldest-first.

    Empty on any trouble (no store yet, missing dir, read error) — the caller
    treats ``[]`` as "no memory this turn".
    """
    if not session_id:
        return []
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return []
    try:
        conn = _connect(db_path)
        try:
            messages = _read_messages(conn, session_id, limit)
        finally:
            conn.close()
        # Order by the real Slack ts (CHRONOLOGY), turn_no only as a tiebreaker.
        # sync_tail can back-fill a missed MIDDLE turn, which gets turn_no=max+1
        # (appended last); ordering by turn_no alone would then render it out of
        # order (stored 1,3 + synced 2 -> "1,3,2"). Ordering by ts renders 1,2,3
        # (Codex 2026-08-10). Every row has a positive ts (_when falls back to now).
        # DESC from SQL → reverse to oldest-first for the formatter. Carry ts so
        # the turn knows WHEN each message was sent (SDK createdAt metadata). The
        # ts coercion stays INSIDE the try so malformed stored data degrades to
        # "no memory", never a raise (fail-open contract).
        return messages
    except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
        logger.warning("conversation_store: load failed", exc_info=True)
        return []


def sync_tail(
    universe_dir: "str | Path",
    session_id: str,
    live_messages: "list[dict]",
    *,
    limit: int = DEFAULT_LIMIT,
) -> int:
    """Reconcile the store's tail against the live timeline; append what's missing.

    Why this exists
    ---------------
    ``backfill_once`` runs EXACTLY ONCE per session (a marker row claims it). It
    imports the timeline the first time and never again. So if any later
    ``record_turn`` is dropped — a transient turn_no race that exhausts its
    retries, a crash between the founder-record and the reply-record, a turn
    taken by a path that forgot to record — the store silently drifts BEHIND the
    live thread and, because backfill is spent, never re-syncs. The universe
    keeps losing the most recent context and nothing surfaces it. That is the
    exact class of silent regression that froze u-tiny's memory at turn 130.

    This makes the LOAD path self-healing: before a turn builds its history
    block, reconcile the durable tail against the live timeline and append only
    the missing trailing turns, so a missed record can never cost recent context.

    Contract
    --------
    * ``live_messages`` is the ``[{"speaker","text","ts"}]`` shape the Slack
      timeline loader returns, oldest-first, ALREADY excluding the current
      prompt (the loader does this).
    * Bounded: only reconciles against the recent window (``limit``), never the
      whole history.
    * De-duped by the stable Slack ``ts``. A legacy id-less row may match by
      ``(speaker, text)``, but each stored row is consumed at most once.
    * Reconciled oldest-first. A failed append stops the pass immediately, so a
      later success cannot become an anchor that permanently strands the gap.
      A window with no overlap does nothing; cold import is ``backfill_once``'s
      job.
    * Best-effort: NEVER raises (the whole body is guarded) and never
      double-counts — ``appended`` only advances when ``record_turn`` actually
      persisted (returned a turn_no), so a dropped write is not logged as a sync.
    """
    try:
        return _sync_tail_impl(universe_dir, session_id, live_messages, limit=limit)
    except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
        logger.warning("conversation_store: sync_tail failed", exc_info=True)
        return 0


def _sync_tail_impl(
    universe_dir: "str | Path",
    session_id: str,
    live_messages: "list[dict]",
    *,
    limit: int,
) -> int:
    if not session_id:
        return 0
    rows = [
        (str(m.get("speaker") or ""), str(m.get("text") or "").strip(), str(m.get("ts") or ""))
        for m in (live_messages or [])
        if isinstance(m, dict) and str(m.get("text") or "").strip()
    ]
    if not rows:
        return 0
    stored = _recent_identities(
        universe_dir, session_id, limit=max(int(limit), len(rows) + 5)
    )
    if not stored:
        # Cold store: that is backfill_once's job. Appending a whole live window
        # here would both duplicate what backfill imports and race it.
        return 0
    stored_ids = {ext for _sp, _tx, ext in stored if ext}
    # Text fallback exists only for pre-stable-id rows. A Counter makes the
    # fallback a one-for-one legacy migration seam rather than a text set that
    # shadows every later message with the same words.
    legacy_pairs = Counter(
        (speaker, text)
        for speaker, text, ext in stored
        if text and not ext
    )
    # The newest stored STABLE id (a Slack ts). A legacy id-less row can only
    # stand in for an OLD live message (<= this); a live row NEWER than every
    # stored id is genuinely new and must never be swallowed by a stale text
    # match whose original has rolled out of the window (Codex FIX2 2026-08-10).
    _id_ts = [t for t in (_coerce_ts(e) for e in stored_ids) if t is not None]
    newest_id_ts = max(_id_ts) if _id_ts else None

    def _known(speaker: str, text: str, ext: str) -> bool:
        # Exact stable-id match first; consume one legacy id-less row only when
        # necessary. New daemon-side founder and universe writes both have ids.
        if ext and ext in stored_ids:
            return True
        pair = (speaker, text)
        if legacy_pairs[pair]:
            live_ts = _coerce_ts(ext)
            # Only when this live row is not newer than the newest stored id
            # (or there are no id rows yet — the pure-legacy transition start).
            if newest_id_ts is None or (live_ts is not None and live_ts <= newest_id_ts):
                legacy_pairs[pair] -= 1
                return True
        return False

    # Require some overlap so sync_tail never races cold backfill. Once overlap
    # exists, walk the entire window oldest-first; this also repairs a gap that
    # appears before a later already-stored row.
    if not any(
        (ext and ext in stored_ids) or legacy_pairs[(speaker, text)]
        for speaker, text, ext in rows
    ):
        return 0
    appended = 0
    for speaker, text, ext in rows:
        if _known(speaker, text, ext):
            continue  # already stored (id or text) — never duplicate
        if not ext:
            # Slack timeline rows always carry ts. Without one there is no safe
            # durable identity, so do not store or advance beyond this gap.
            break
        turn_no = record_turn(
            universe_dir, session_id, speaker, text, ts=_coerce_ts(ext), ext_id=ext
        )
        if not turn_no:
            # Do not count or advance beyond an unpersisted gap. The next turn
            # starts from the same row and retries it before any later message.
            break
        stored_ids.add(ext)
        appended += 1
    if appended:
        logger.info(
            "conversation_store: re-synced %d missing tail turn(s) for session %s",
            appended,
            session_id,
        )
    return appended


def _recent_identities(
    universe_dir: "str | Path", session_id: str, *, limit: int
) -> "list[tuple[str, str, str]]":
    """Recent stored turns as ``(speaker, text, ext_id)`` for sync_tail dedup.

    Separate from :func:`load_recent` because that returns render-ready ``Msg``
    objects with no ``ext_id``. Empty on any trouble (fail-open).
    """
    if not session_id:
        return []
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return []
    try:
        conn = _connect(db_path)
        try:
            fetched = conn.execute(
                "SELECT speaker, content, ext_id FROM conversation_turns "
                "WHERE session_id = ? ORDER BY ts DESC, turn_no DESC LIMIT ?",
                (session_id, max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
        # Fail-open, but NOT silent: an empty identity set disables sync_tail
        # reconciliation, so a persistently locked/failed read would let the store
        # drift behind the live thread forever with no diagnostic (Codex NEW2
        # 2026-08-10). Make it visible.
        logger.warning(
            "conversation_store: identity read failed for session %s "
            "(sync reconciliation degraded this turn)",
            session_id,
            exc_info=True,
        )
        return []
    return [
        (str(sp or ""), str(ct or "").strip(), str(ext or ""))
        for sp, ct, ext in fetched
    ]


def _coerce_ts(value: object) -> "float | None":
    """A Slack/epoch ts (str or number) as float seconds, or None.

    Catches EVERY conversion failure, not just TypeError/ValueError: e.g.
    ``float(10**10000)`` raises OverflowError, and a ts must NEVER cost the turn
    (Codex FIX1 2026-08-10). NaN/inf are rejected too (``f > 0`` is False for
    both), so a poisoned ts degrades to "now" upstream rather than storing junk.
    """
    try:
        f = float(value)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - a bad ts must never raise into the turn
        return None
    # Finite AND positive. `float("inf") > 0` is True, so without isfinite an
    # "inf" ts would be STORED and — now that load_recent orders by ts — sort
    # above every real turn, crowding valid history out of the bounded window
    # (Codex 2026-08-10). NaN is already rejected (`nan > 0` is False).
    return f if (math.isfinite(f) and f > 0) else None


def _when(ts: object) -> float:
    """A valid epoch-seconds "when" for storage — never raises, falls back to now."""
    coerced = _coerce_ts(ts)
    return coerced if coerced is not None else time.time()


def is_backfilled(universe_dir: "str | Path", session_id: str) -> bool:
    """True if this session's one-time Slack-timeline import has been claimed.

    Distinct from :func:`has_prior_turns`: a session with an empty timeline is
    still marked backfilled so we do not re-hit the Slack API every turn.
    """
    if not session_id:
        return False
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return False
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM conversation_backfill WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False
    return row is not None


def backfill_once(
    universe_dir: "str | Path",
    session_id: str,
    messages: "list[dict]",
) -> int:
    """Import a session's prior messages ONCE, atomically. Return count imported.

    The marker claim + the message inserts happen in a single transaction, so:

    * **Concurrent** cold turns cannot both import — the ``conversation_backfill``
      PRIMARY KEY makes exactly one worker win; the loser returns 0 without
      importing (fixes the duplicate-every-message race).
    * A crash mid-import rolls back the whole transaction, leaving the session
      un-backfilled and RETRYABLE rather than half-populated (fixes partial
      backfill permanently suppressing retry).

    Returns 0 if already backfilled, nothing to import, or on any storage error
    (best-effort — a missed backfill just means less memory, never a lost reply).
    ``messages`` is ``[{"speaker","text"}]`` from the Slack timeline loader.
    """
    if not session_id:
        return 0
    when = time.time()

    def _ts(m: dict) -> float:
        # Preserve the message's real send time so backfilled history carries
        # accurate "when"; fall back to now only if the loader gave none.
        try:
            v = float(m.get("ts") or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v if v > 0 else when

    rows = [
        (
            str(m.get("speaker") or ""),
            str(m.get("text") or ""),
            _ts(m),
            str(m.get("ts") or ""),  # ext_id: the stable Slack message id
        )
        for m in (messages or [])
        if isinstance(m, dict) and str(m.get("text") or "").strip()
    ]
    db_path = _db_path(universe_dir)
    lock = _lock_for(db_path)
    for attempt in range(6):
        try:
            with lock:
                conn = _connect(db_path)
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        conn.execute(
                            "INSERT INTO conversation_backfill (session_id, ts) "
                            "VALUES (?, ?)",
                            (session_id, when),
                        )
                    except sqlite3.IntegrityError:
                        conn.rollback()
                        return 0  # another worker already backfilled this session
                    base = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(turn_no), 0) "
                            "FROM conversation_turns WHERE session_id = ?",
                            (session_id,),
                        ).fetchone()[0]
                    )
                    for i, (speaker, content, ts_val, ext) in enumerate(rows, start=1):
                        conn.execute(
                            "INSERT INTO conversation_turns "
                            "(session_id, turn_no, speaker, content, ts, ext_id) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (session_id, base + i, speaker, content, ts_val, ext),
                        )
                    conn.commit()
                    return len(rows)
                finally:
                    conn.close()
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if ("lock" in msg or "busy" in msg) and attempt < 5:
                time.sleep(0.02 * (attempt + 1))
                continue
            logger.warning("conversation_store: backfill failed: %s", exc)
            return 0
        except Exception:  # noqa: BLE001 - memory is a bonus, never a blocker
            logger.warning("conversation_store: backfill failed", exc_info=True)
            return 0
    return 0


def has_prior_turns(universe_dir: "str | Path", session_id: str) -> bool:
    """True if this session already has durable history.

    Drives the Slack cold-store backfill: an empty store means "backfill from the
    Slack API once", a populated one means "the store is the source of truth".
    """
    if not session_id:
        return False
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return False
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM conversation_turns WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False
    return row is not None


# ── the learned cursor (change: deferred-learning-never-blocks-the-reply) ──────
# "The last founder turn in this session whose lesson is settled." Pending work is
# the founder turns after it, which this store already holds verbatim -- so this is
# a CURSOR, not a queue: one row per session, idempotent to advance, and a burst of
# quick turns is one pending span rather than one job each.
#
# It exists because `converse` used to spend a THIRD model round-trip on learning
# extraction after the reply text already existed, on every turn, on the founder's
# clock (measured 2026-09-25; production 2026-09-26 UTC: two recall turns at 3
# rounds each). A turn that records its own lesson with `write_brain` settles this
# cursor and skips that call; a turn that does not still runs it synchronously, so
# no lesson is ever lost.
#
# It advances ONLY on a recorded write. A crash, a refusal or a skipped write leaves
# it behind, which is the retry state -- never the reverse.


def learned_cursor(universe_dir: "str | Path", session_id: str) -> int:
    """The last settled founder turn for this session, or 0 when nothing is.

    Never raises: a missing db, an old schema or an unreadable row all read as
    "nothing settled", which costs a synchronous extraction rather than a lesson.
    """
    if not session_id:
        return 0
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return 0
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT settled_turn FROM conversation_learned WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a cursor read must never cost the turn
        logger.warning("conversation memory: learned cursor unreadable", exc_info=True)
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def latest_turn_no(universe_dir: "str | Path", session_id: str) -> int | None:
    """The highest recorded turn number for this session, 0 for none, None if unknown.

    ``None`` is NOT 0. It used to be: every failure path returned 0, which is the same
    answer as "this conversation has no turns yet" -- so an unreadable store looked
    like a brand-new conversation, the contiguity guard in
    :func:`settle_learned_cursor` compared 0 against a cursor legitimately at 0, agreed
    with itself, and the settle claimed every unsettled turn in the history. Reproduced
    on 2026-09-26 (PR #4001 review follow-up); a reader that cannot answer must say so.
    """
    if not session_id:
        return None
    db_path = _db_path(universe_dir)
    if not db_path.exists():
        return 0
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT MAX(turn_no) FROM conversation_turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - unknown, which is not the same as none
        logger.warning(
            "conversation memory: latest turn unreadable for %s", session_id, exc_info=True,
        )
        return None
    return int(row[0]) if row and row[0] is not None else 0


def settle_learned_cursor(
    universe_dir: "str | Path",
    session_id: str,
    *,
    from_turn: "int | None",
    through_turn: int | None = None,
) -> int:
    """Advance the cursor to ``through_turn`` (default: the latest turn). Returns it.

    MONOTONIC and idempotent: it never moves backwards, so a second settle for the
    same span is a no-op and two workers cannot un-settle each other. Returns the
    cursor value in force afterwards, or 0 if nothing could be written -- a failure
    here must leave the lesson owed, not claim it was learned.

    CONTIGUOUS: a watermark cannot say "turn N is settled but N-1 is not", so it must
    never CLAIM an earlier unsettled turn. A turn whose extraction FAILED leaves the
    cursor behind; if the next turn then settled to the latest row, the cursor would
    jump PAST the failed one and no drain would ever retry it (PR #4001 review --
    inert while nothing reads the cursor, but the rows written now already carry that
    meaning). ``from_turn`` is where the caller believes the cursor stands, i.e. the
    latest turn BEFORE the exchange it is settling; the advance is refused when the
    cursor is behind that. Refusing costs a redundant extraction later; claiming
    would cost the lesson.

    ``from_turn`` is a REQUIRED keyword with no default, and ``None`` means "I could
    not read it" and REFUSES. It was optional, and an omitted value skipped the
    contiguity check entirely -- the unsafe reading of an absent argument. Every
    caller now states its belief, and a caller that has none says ``None`` and is
    turned down: the one thing this must never do is claim a lesson nobody learned.
    """
    if not session_id:
        return 0
    if from_turn is None:
        logger.info(
            "conversation memory: lesson for %s not claimed -- the turn this settles "
            "could not be identified, so an earlier turn may still be owed", session_id,
        )
        return learned_cursor(universe_dir, session_id)
    target = latest_turn_no(universe_dir, session_id) if through_turn is None else int(
        through_turn
    )
    if target is None or target <= 0:
        return learned_cursor(universe_dir, session_id)
    settled = learned_cursor(universe_dir, session_id)
    if settled != int(from_turn):
        logger.info(
            "conversation memory: lesson for %s not claimed -- cursor at %d, this "
            "turn began at %d, so an earlier turn is still owed",
            session_id, settled, int(from_turn),
        )
        return settled
    db_path = _db_path(universe_dir)
    try:
        with _lock_for(db_path):
            conn = _connect(db_path)
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO conversation_learned (session_id, settled_turn, ts) "
                        "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                        "settled_turn = MAX(settled_turn, excluded.settled_turn), "
                        "ts = excluded.ts",
                        (session_id, target, time.time()),
                    )
                row = conn.execute(
                    "SELECT settled_turn FROM conversation_learned WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            finally:
                conn.close()
    except Exception:  # noqa: BLE001 - never claim a lesson was learned
        logger.warning("conversation memory: learned cursor not advanced", exc_info=True)
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def start_learned_cursor(universe_dir: "str | Path", session_id: str) -> int:
    """Settle an EXISTING conversation's cursor at its latest turn, once.

    A conversation that predates the cursor must not have its whole history
    re-extracted the first time this runs -- that would be a spend surprise on the
    founder's own credential. Only ever called for a session that has history and
    no cursor yet; a session with a cursor is untouched.

    This is the ONE caller entitled to claim a whole history, and it says so
    explicitly with ``from_turn=0`` after checking the cursor is 0 -- seeding, not a
    settle that skipped the contiguity check.
    """
    if not session_id or learned_cursor(universe_dir, session_id):
        return 0
    return settle_learned_cursor(universe_dir, session_id, from_turn=0)


__all__ = [
    "backfill_once",
    "has_prior_turns",
    "is_backfilled",
    "latest_turn_no",
    "learned_cursor",
    "load_recent",
    "read_history_page",
    "record_turn",
    "settle_learned_cursor",
    "start_learned_cursor",
    "sync_tail",
]
