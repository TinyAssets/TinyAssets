"""Account seats: how many agent calls one ACCOUNT runs at once.

Founder, 2026-09-30: an account's limits are two numbers, cloud storage and
concurrent agent seats. Over the seat count, work WAITS -- visibly, in a queue --
and is never refused or dropped. The pool is ONE per person, shared across all of
their universes; universe creation stays unlimited and free.

* **Keyed on the owning account.** `account_key` asks `universe_owner.owner_of`,
  the one resolver storage uses too. A universe no account is charged for
  (pre-ownership, ambiguous home) is its own unattributed pool: counted, never
  refused, and never merged into somebody else's.
* **Held at the agent call.** The agent node's executor (`graph_compiler`) and the
  chat turn (`universe_intelligence.converse`) hold a seat for the model call. An
  automation or wake worker takes its seat BEFORE it claims an attempt, without
  blocking the consumer thread (`try_acquire`), and the run's agent calls re-enter
  it. Nothing that merely enqueues work holds a seat.
* **Interactive first.** ``total < seats`` always; ``background < seats -
  reserve`` for background only, so a chat is never stuck behind automation; and
  an interactive waiter is ahead of every background waiter.
* **Nested calls transfer, siblings pay.** A blocking child re-enters its parent's
  seat by an exclusive depth transition; a parallel sibling that finds the seat
  already lent takes (and waits for) its own.
* **Released on proven death.** A seat's holder is this process's
  `process_liveness.owner_token` under the ledger's own root. A seat is reclaimed
  when its holder is proven dead (a crash, a deploy's SIGKILL), or when its lease
  lapsed AND the holder is not provably alive. A provably alive holder is never
  reclaimed, however stale its lease.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)
_current_seat: ContextVar = ContextVar("account_seat", default=None)

LEDGER_NAME = ".account_seats.db"

CLASS_INTERACTIVE = "interactive"
CLASS_BACKGROUND = "background"
SEAT_CLASSES = (CLASS_INTERACTIVE, CLASS_BACKGROUND)

#: What kind of agent call holds the seat. Owner-facing only -- the ceiling depends
#: on the CLASS, never on the kind, so adding a kind cannot change who waits.
KIND_CHAT_TURN = "chat_turn"
KIND_AGENT_NODE = "agent_node"
KIND_AUTOMATION = "automation"
KIND_WAKE = "wake"

#: Account keys carry their kind, so an owner id can never collide with an
#: unattributed universe's pool.
_ACCOUNT_PREFIX = "account:"
_UNATTRIBUTED_PREFIX = "unattributed:"

#: How long a seat survives its holder's silence WHEN its holder cannot be proven
#: alive. The refresher re-stamps every live seat, so a lapsed lease with no
#: liveness proof means nobody is holding it.
SEAT_LEASE_SECONDS = 120.0
#: Re-stamp cadence: a quarter of the lease.
SEAT_REFRESH_SECONDS = 30.0
#: A waiter's queue position lapses if it is not re-presented for this long. Every
#: waiter re-presents its ticket far more often (a blocking waiter every
#: `_POLL_SECONDS`, the automation pump every poll), so only an abandoned position
#: lapses -- and a lapsed live waiter only rejoins at the back, it is never dropped.
#: Short on purpose: an abandoned position ahead of live work stalls that work.
WAITER_LEASE_SECONDS = 60.0
#: How often a waiting caller re-publishes its waiting state.
WAITING_NOTICE_SECONDS = 5.0
_POLL_SECONDS = 0.25


class SeatLedgerUnusable(RuntimeError):
    """The seat store is tampered or unreadable.

    Raised, not swallowed: admitting work without a seat because the ledger is a
    symlink is exactly the evasion the check exists to stop (Hard Rule 8).
    """


@dataclass(frozen=True)
class Seat:
    """A held seat. ``reentrant`` means it re-entered a parent's seat, so
    releasing it only decrements that seat's depth."""

    seat_id: str
    account_id: str
    seat_class: str
    kind: str
    reentrant: bool = False
    depth: int = 1
    db: Path | None = None


@dataclass(frozen=True)
class Waiting:
    """No seat yet, and the queue position that guarantees one. Never a refusal."""

    ticket: int
    account_id: str
    seat_class: str
    running: int
    waiting: int
    seats: int


# --------------------------------------------------------------------------- #
# Account and tier -- the storage lane's resolver, never a second one
# --------------------------------------------------------------------------- #


def account_key(universe_id: str, *, root: str | Path) -> str:
    """The seat pool a universe's agent calls draw from.

    ``universe_owner.owner_of`` is the ONE resolver of "whose account is this
    universe charged to" (#4139). Nothing here re-derives ownership from ACL rows
    or bindings (memory `never-infer-identity-from-adjacent-tables`).
    """
    from tinyassets.universe_owner import owner_of

    uid = (universe_id or "").strip()
    if not uid:
        raise ValueError("a seat needs a universe_id")
    owner = owner_of(root, uid)
    return f"{_ACCOUNT_PREFIX}{owner}" if owner else f"{_UNATTRIBUTED_PREFIX}{uid}"


def owner_of_key(key: str) -> str | None:
    """The account id inside an account key, or None for an unattributed pool."""
    return key[len(_ACCOUNT_PREFIX):] if key.startswith(_ACCOUNT_PREFIX) else None


def tier_of_key(key: str, *, root: str | Path):
    """The `AccountType` of an account key, via ``universe_owner.account_type_of``
    -- the one per-account input policy takes. An unattributed pool is free:
    unknown never resolves to a subscription."""
    from tinyassets.universe_owner import account_type_of
    from tinyassets.usage_policy import AccountType

    owner = owner_of_key(key)
    return account_type_of(root, owner) if owner else AccountType.FREE


def limits_for_key(key: str, *, root: str | Path):
    from tinyassets.usage_policy import limits_for

    return limits_for(tier_of_key(key, root=root))


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def ledger_path(root: str | Path | None = None) -> Path:
    """The seat store under the data root (`storage.data_dir`, never the CWD)."""
    if root is None:
        from tinyassets.storage import data_dir

        root = data_dir()
    return Path(root) / LEDGER_NAME


def _trusted(db: Path) -> bool:
    """Whether the store sits inside its data dir and is not a symlink. A check
    that cannot complete is NOT trust -- it raises."""
    try:
        if db.is_symlink():
            return False
        root = os.path.realpath(db.parent)
        real = os.path.realpath(db)
        return real == root or real.startswith(root + os.sep)
    except OSError as exc:
        raise SeatLedgerUnusable(f"seat store could not be checked: {exc}") from exc


_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_seats (
    seat_id      TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL,
    universe_id  TEXT NOT NULL DEFAULT '',
    seat_class   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    run_id       TEXT NOT NULL DEFAULT '',
    holder       TEXT NOT NULL,
    depth        INTEGER NOT NULL DEFAULT 1,
    acquired_at  REAL NOT NULL,
    expires_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS account_seats_account ON account_seats(account_id);

-- `ticket` is AUTOINCREMENT so "longest-owed" is an integer comparison: two
-- waiters enqueued in the same millisecond still have a total order.
CREATE TABLE IF NOT EXISTS seat_waiters (
    ticket       INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   TEXT NOT NULL,
    universe_id  TEXT NOT NULL DEFAULT '',
    seat_class   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    run_id       TEXT NOT NULL DEFAULT '',
    holder       TEXT NOT NULL,
    enqueued_at  REAL NOT NULL,
    expires_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS seat_waiters_account
    ON seat_waiters(account_id, seat_class, ticket);
"""


def _schema_statements() -> list[str]:
    """`_SCHEMA` as single statements, with its comments dropped."""
    lines = [line for line in _SCHEMA.splitlines() if not line.lstrip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


_SCHEMA_STATEMENTS = _schema_statements()


def _connect(db: Path) -> sqlite3.Connection:
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # A data dir that does not exist yet must not mean "no limit".
        raise SeatLedgerUnusable(f"seat store parent unusable: {exc}") from exc
    if not _trusted(db):
        raise SeatLedgerUnusable(f"seat store is not inside its data dir: {db}")
    try:
        # Autocommit mode: `_txn` issues BEGIN IMMEDIATE / COMMIT itself, so the
        # driver never opens or closes a transaction behind its back.
        conn = sqlite3.connect(str(db), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn
    except sqlite3.Error as exc:
        raise SeatLedgerUnusable(f"seat store could not be opened: {exc}") from exc


@contextmanager
def _txn(db: Path):
    """One `BEGIN IMMEDIATE` per operation, held until COMMIT.

    The schema is created with single `execute` calls INSIDE the transaction.
    `executescript` must never be used here: it COMMITS any open transaction
    before it runs, which silently ended the write lock the moment it was taken
    -- every reap, count and insert after it ran outside any transaction, so two
    releases of a lent seat could both read depth 2 and leave a depth-0 row that
    held a seat forever, and two acquirers could both take the last seat.
    """
    conn = _connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _holder(root: Path) -> str:
    """This process's owner token, its liveness lock held under ``root``."""
    from tinyassets.process_liveness import owner_token

    return owner_token(root)


def _reap(conn: sqlite3.Connection, now: float, root: Path) -> None:
    """Reclaim seats whose holder is proven dead, or whose lease lapsed with no
    proof of life; drop waiters that are dead or abandoned.

    Probed once per distinct holder: every seat a process holds shares its token.
    """
    from tinyassets.process_liveness import ALIVE, DEAD, owner_state

    states: dict[str, str] = {}

    def state(holder: str) -> str:
        if holder not in states:
            states[holder] = owner_state(root, holder)
        return states[holder]

    for row in conn.execute("SELECT seat_id, holder, expires_at FROM account_seats").fetchall():
        verdict = state(str(row["holder"]))
        if verdict == DEAD or (verdict != ALIVE and float(row["expires_at"]) < now):
            conn.execute("DELETE FROM account_seats WHERE seat_id = ?", (row["seat_id"],))
    for row in conn.execute("SELECT ticket, holder, expires_at FROM seat_waiters").fetchall():
        if state(str(row["holder"])) == DEAD or float(row["expires_at"]) < now:
            conn.execute("DELETE FROM seat_waiters WHERE ticket = ?", (row["ticket"],))


def _count(conn: sqlite3.Connection, table: str, account_id: str, seat_class: str | None = None,
           before: int | None = None) -> int:
    sql = f"SELECT COUNT(*) FROM {table} WHERE account_id = ?"
    args: list[object] = [account_id]
    if seat_class is not None:
        sql += " AND seat_class = ?"
        args.append(seat_class)
    if before is not None:
        sql += " AND ticket < ?"
        args.append(before)
    return int(conn.execute(sql, args).fetchone()[0])


def _admits(conn: sqlite3.Connection, account_id: str, seat_class: str, *,
            seats: int, reserve: int) -> bool:
    """Capacity for one more seat of ``seat_class``.

        total_live < seats                        always
        background_live < seats - reserve         background only

    A reserve bounds the class it constrains, not the total: comparing TOTAL live
    against ``seats - reserve`` refused background work while a seat sat free
    because two chats were running (astra round 1, finding 6).
    """
    if _count(conn, "account_seats", account_id) >= seats:
        return False
    if seat_class == CLASS_INTERACTIVE:
        return True
    return _count(conn, "account_seats", account_id, CLASS_BACKGROUND) < max(1, seats - reserve)


def _ahead_of(conn: sqlite3.Connection, account_id: str, seat_class: str,
              ticket: int | None) -> int:
    """Waiters entitled to a seat before this caller.

    Every interactive waiter is ahead of every background waiter, whatever the
    tickets say; within a class, lower tickets first. ``ticket`` None ("not queued
    yet") counts every waiter of the relevant classes, so a fresh arrival never
    overtakes the front of the queue.
    """
    interactive = _count(
        conn, "seat_waiters", account_id, CLASS_INTERACTIVE,
        before=ticket if seat_class == CLASS_INTERACTIVE else None,
    )
    if seat_class == CLASS_INTERACTIVE:
        return interactive
    return interactive + _count(conn, "seat_waiters", account_id, CLASS_BACKGROUND, before=ticket)


def _reenter(conn: sqlite3.Connection, account_id: str, parent_seat_id: str, now: float,
             lease_s: float, holder: str, parent_depth: int) -> bool:
    """Lend a parent's seat to ONE blocking nested call.

    Account, holder AND depth must all match. A seat id alone is never a
    capability: account-only would let one account's code name another's seat,
    holder-only would let another account in this process do it. ``depth =
    parent_depth`` makes the loan exclusive: a sibling that finds the seat already
    lent takes its own, because it really is concurrent work.
    """
    updated = conn.execute(
        "UPDATE account_seats SET depth = depth + 1, expires_at = ? "
        "WHERE seat_id = ? AND account_id = ? AND holder = ? AND depth = ?",
        (now + lease_s, parent_seat_id, account_id, holder, parent_depth),
    )
    return updated.rowcount == 1


def acquire(
    account_id: str,
    *,
    seat_class: str = CLASS_BACKGROUND,
    kind: str = KIND_AGENT_NODE,
    universe_id: str = "",
    run_id: str = "",
    ticket: int | None = None,
    parent_seat_id: str | None = None,
    parent_depth: int = 1,
    seats: int | None = None,
    reserve: int | None = None,
    db: Path | None = None,
    now: float | None = None,
    lease_s: float = SEAT_LEASE_SECONDS,
) -> Seat | Waiting:
    """Take a seat for ``account_id`` now, or hold a queue position.

    Never refuses for want of a seat. ``ticket`` re-presents a position already
    held. One `BEGIN IMMEDIATE` does reap, re-entry, count, ceiling, queue position
    and insert, so two acquisitions cannot both see the last seat free.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("a seat needs an account_id")
    if seat_class not in SEAT_CLASSES:
        raise ValueError(f"seat class must be one of {SEAT_CLASSES}, not {seat_class!r}")
    db = db or ledger_path()
    root = db.parent
    moment = time.time() if now is None else now
    if seats is None or reserve is None:
        limits = limits_for_key(account_id, root=root)
        seats = limits.seats if seats is None else seats
        reserve = limits.interactive_reserve if reserve is None else reserve
    total = max(1, int(seats))
    held_back = min(max(0, int(reserve)), total - 1)
    # Before the transaction: taking the liveness lock is filesystem work that must
    # not happen while holding the write lock.
    holder = _holder(root)
    with _txn(db) as conn:
        _reap(conn, moment, root)
        if parent_seat_id and _reenter(
            conn, account_id, parent_seat_id, moment, lease_s, holder, parent_depth
        ):
            if ticket is not None:
                conn.execute("DELETE FROM seat_waiters WHERE ticket = ?", (ticket,))
            return Seat(parent_seat_id, account_id, seat_class, kind,
                        reentrant=True, depth=parent_depth + 1, db=db)
        running = _count(conn, "account_seats", account_id)
        if ticket is not None and conn.execute(
            "SELECT 1 FROM seat_waiters WHERE ticket = ? AND account_id = ? AND holder = ?",
            (ticket, account_id, holder),
        ).fetchone() is None:
            # Lapsed, or never ours: a ticket is only honoured for the account and
            # process that took it.
            ticket = None
        ahead = _ahead_of(conn, account_id, seat_class, ticket)
        if ahead == 0 and _admits(conn, account_id, seat_class, seats=total, reserve=held_back):
            seat_id = secrets.token_hex(12)
            conn.execute(
                "INSERT INTO account_seats (seat_id, account_id, universe_id, seat_class, "
                "kind, run_id, holder, depth, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (seat_id, account_id, universe_id, seat_class, kind, run_id, holder,
                 moment, moment + lease_s),
            )
            if ticket is not None:
                conn.execute("DELETE FROM seat_waiters WHERE ticket = ?", (ticket,))
            return Seat(seat_id, account_id, seat_class, kind, db=db)
        if ticket is not None:
            conn.execute(
                "UPDATE seat_waiters SET expires_at = ? WHERE ticket = ?",
                (moment + WAITER_LEASE_SECONDS, ticket),
            )
        else:
            cur = conn.execute(
                "INSERT INTO seat_waiters (account_id, universe_id, seat_class, kind, run_id, "
                "holder, enqueued_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, universe_id, seat_class, kind, run_id, holder, moment,
                 moment + WAITER_LEASE_SECONDS),
            )
            ticket = int(cur.lastrowid or 0)
        return Waiting(
            ticket=ticket, account_id=account_id, seat_class=seat_class, running=running,
            waiting=_count(conn, "seat_waiters", account_id), seats=total,
        )


def refresh(seat_id: str, *, db: Path | None = None, now: float | None = None) -> bool:
    """Re-stamp a held seat's lease. False means it was already reaped."""
    seat_id = (seat_id or "").strip()
    if not seat_id:
        return False
    db = db or ledger_path()
    moment = time.time() if now is None else now
    holder = _holder(db.parent)
    with _txn(db) as conn:
        cur = conn.execute(
            "UPDATE account_seats SET expires_at = ? WHERE seat_id = ? AND holder = ?",
            (moment + SEAT_LEASE_SECONDS, seat_id, holder),
        )
        return cur.rowcount == 1


#: ``_release_once`` gave back one level of a LENT seat; the row, and the
#: parent's claim on it, remain. Truthy, so callers reading "released" still do.
_DEPTH_RETURNED = "depth_returned"


def _release_once(seat_id: str, db: Path) -> bool | str | None:
    """One release attempt: True released, ``_DEPTH_RETURNED`` a lent seat's
    depth given back (the parent still holds it), False no such seat of ours,
    None the store could not be written -- try again."""
    try:
        holder = _holder(db.parent)
        with _txn(db) as conn:
            row = conn.execute(
                "SELECT depth FROM account_seats WHERE seat_id = ? AND holder = ?",
                (seat_id, holder),
            ).fetchone()
            if row is None:
                return False
            if int(row["depth"]) > 1:
                conn.execute(
                    "UPDATE account_seats SET depth = depth - 1 WHERE seat_id = ?", (seat_id,)
                )
                return _DEPTH_RETURNED
            else:
                conn.execute("DELETE FROM account_seats WHERE seat_id = ?", (seat_id,))
            return True
    except (SeatLedgerUnusable, sqlite3.Error, OSError, RuntimeError):
        return None


def release(seat_id: str, *, db: Path | None = None) -> bool:
    """Give a seat back: decrement a lent seat's depth, delete it at the last one.

    Only this process's own seat: the holder must match. Never raises -- it runs
    from a `finally`. A release the store refuses (locked, briefly unwritable) is
    NOT dropped: this process is alive, so nothing else would ever reclaim the
    seat, and it would be charged to the account until the process died. It is
    queued and retried by the refresher until it lands.
    """
    seat_id = (seat_id or "").strip()
    if not seat_id:
        return False
    db = db or ledger_path()
    outcome = _release_once(seat_id, db)
    if outcome is None:
        # Still registered: whether this was the last level or a nested loan is
        # unknown until the store answers, and only the retry learns which.
        _log.warning("seat %s could not be released yet; retrying until it is", seat_id)
        _queue_retry(seat_id, db)
        return False
    _settle_registration(seat_id, outcome)
    return bool(outcome)


def _settle_registration(seat_id: str, outcome: bool | str) -> None:
    """Stop refreshing a seat once a release has actually removed it.

    A nested call returning its loan (``_DEPTH_RETURNED``) leaves the row -- the
    seat id is the PARENT's and the parent is still working. Dropping it from
    the refresh set there let the parent's lease lapse under a live provider
    call, so anything trusting the lease (a deploy's in-flight check, a
    sibling's reaper) read it as finished.
    """
    if outcome is _DEPTH_RETURNED:
        return
    with _held_lock:
        _held.pop(seat_id, None)


def abandon(ticket: int | None, *, db: Path | None = None) -> bool:
    """Give up a queue position -- only for a caller that decided not to run."""
    if ticket is None:
        return False
    try:
        with _txn(db or ledger_path()) as conn:
            return conn.execute(
                "DELETE FROM seat_waiters WHERE ticket = ?", (int(ticket),)
            ).rowcount == 1
    except (SeatLedgerUnusable, sqlite3.Error):
        return False


def holder_is_named(root: str | Path, holder: str) -> bool:
    """Whether any seat or waiter still names ``holder``. The liveness cleanup
    keeps a dead token's proof until then, or its seats would become unprovable."""
    db = ledger_path(root)
    if not db.exists():
        return False
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        for table in ("account_seats", "seat_waiters"):
            try:
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE holder = ? LIMIT 1", (holder,)
                ).fetchone():
                    return True
            except sqlite3.OperationalError:
                continue
    return False


# --------------------------------------------------------------------------- #
# What the owner sees
# --------------------------------------------------------------------------- #


def waiting_message(*, running: int, tier: str) -> str:
    """The one line an owner sees while work waits for a seat.

    "Waiting for a free seat (2 running) — [Upgrade](...) for more seats." The link
    sits inside the message -- never a banner, card or modal (founder, 2026-09-30)
    -- and is absent on the top tier, where `usage_policy.upgrade_url` is None.
    """
    from tinyassets.usage_policy import upgrade_sentence

    head = f"Waiting for a free seat ({int(running)} running)"
    tail = upgrade_sentence(tier, what="seats")
    return f"{head} — {tail}" if tail else f"{head}."


def occupancy(account_id: str, *, db: Path | None = None, universe_id: str = "",
              now: float | None = None) -> dict[str, object]:
    """Seats, running and waiting for one account -- read-only, reaps nothing.

    ``universe_id`` adds ``chat_waiting``: whether a chat turn in THAT universe is
    queued, so a surface shows the waiting line only on the conversation waiting.
    """
    account_id = (account_id or "").strip()
    db = db or ledger_path()
    limits = limits_for_key(account_id, root=db.parent)
    out: dict[str, object] = {
        "seats": limits.seats,
        "background_seats": limits.background_seats,
        "running": 0,
        "waiting": 0,
        "interactive_waiting": 0,
        "tier": limits.name,
        "upgrade_url": _upgrade_url(limits.name),
    }
    if universe_id:
        out["chat_waiting"] = False
    if not account_id or not db.exists():
        return out
    moment = time.time() if now is None else now
    try:
        if not _trusted(db):
            raise SeatLedgerUnusable("untrusted seat ledger")
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            out["running"] = _count(conn, "account_seats", account_id)
            live = "SELECT COUNT(*) FROM seat_waiters WHERE account_id = ? AND expires_at >= ?"
            out["waiting"] = int(conn.execute(live, (account_id, moment)).fetchone()[0])
            out["interactive_waiting"] = int(conn.execute(
                live + " AND seat_class = ?", (account_id, moment, CLASS_INTERACTIVE),
            ).fetchone()[0])
            if universe_id:
                out["chat_waiting"] = bool(conn.execute(
                    live + " AND seat_class = ? AND universe_id = ?",
                    (account_id, moment, CLASS_INTERACTIVE, universe_id),
                ).fetchone()[0])
    except (SeatLedgerUnusable, sqlite3.Error, OSError):
        out["availability"] = "unavailable"
        return out
    if out["waiting"]:
        out["message"] = waiting_message(running=int(out["running"]), tier=limits.name)
    return out


def _upgrade_url(tier: str) -> str | None:
    from tinyassets.usage_policy import upgrade_url

    return upgrade_url(tier)


def status_for_owner(universe_id: str, *, root: str | Path, actor_id: str) -> dict | None:
    """The seat block a status surface shows, or None when ``actor_id`` is not the
    universe's owning account: an account's occupancy spans all of its universes,
    and a co-admin of one of them has no business reading the rest."""
    key = account_key(universe_id, root=root)
    if not actor_id or owner_of_key(key) != actor_id:
        return None
    return occupancy(key, db=ledger_path(root), universe_id=universe_id)


# --------------------------------------------------------------------------- #
# The refresher: one thread for every seat this process owns
# --------------------------------------------------------------------------- #
# Not started on import: a self-starting periodic thread pollutes monkeypatched
# globals in whatever test runs next. `_register` starts it.
_held_lock = threading.Lock()
_held: dict[str, Path] = {}
_refresher: threading.Thread | None = None
_refresher_stop = threading.Event()


_pending_releases: list[tuple[str, Path]] = []
#: How soon a refused release is tried again.
_RETRY_SECONDS = 1.0


def _retry_pending_releases() -> None:
    """Try every queued release once; keep the ones the store still refuses."""
    with _held_lock:
        pending = list(_pending_releases)
        _pending_releases.clear()
    failed = []
    for seat_id, db in pending:
        outcome = _release_once(seat_id, db)
        if outcome is None:
            failed.append((seat_id, db))
        else:
            _settle_registration(seat_id, outcome)
    if failed:
        with _held_lock:
            _pending_releases.extend(failed)


def _refresh_loop() -> None:
    next_refresh = time.monotonic() + SEAT_REFRESH_SECONDS
    while not _refresher_stop.wait(_RETRY_SECONDS):
        _retry_pending_releases()
        if time.monotonic() < next_refresh:
            continue
        next_refresh = time.monotonic() + SEAT_REFRESH_SECONDS
        with _held_lock:
            current = dict(_held)
        for seat_id, db in current.items():
            try:
                if not refresh(seat_id, db=db):
                    with _held_lock:
                        _held.pop(seat_id, None)
            except (SeatLedgerUnusable, sqlite3.Error, OSError, RuntimeError):
                _log.warning("seat refresh failed for %s", seat_id)


def _ensure_refresher() -> None:
    """Start the refresh thread if it is not running. Call with `_held_lock`."""
    global _refresher
    if _refresher is not None and _refresher.is_alive():
        return
    _refresher_stop.clear()
    _refresher = threading.Thread(target=_refresh_loop, name="account-seat-refresh", daemon=True)
    _refresher.start()


def _queue_retry(seat_id: str, db: Path) -> None:
    with _held_lock:
        _pending_releases.append((seat_id, db))
        _ensure_refresher()


def _register(seat: Seat) -> None:
    """Keep an OWNED seat stamped. A lent seat is its parent's to stamp."""
    if seat.reentrant:
        return
    with _held_lock:
        _held[seat.seat_id] = seat.db or ledger_path()
        _ensure_refresher()


def stop_refresher() -> None:
    """Stop the refresh thread. For tests; held seats still end by release or
    proven death."""
    global _refresher
    _refresher_stop.set()
    thread = _refresher
    if thread is not None:
        thread.join(timeout=2.0)
    with _held_lock:
        _refresher = None
        _held.clear()
        _pending_releases.clear()


# --------------------------------------------------------------------------- #
# Waiting, holding, and the contextvar that carries a seat to nested calls
# --------------------------------------------------------------------------- #


def current_seat() -> Seat | None:
    return _current_seat.get()


def acquire_blocking(
    account_id: str,
    *,
    seat_class: str = CLASS_BACKGROUND,
    kind: str = KIND_AGENT_NODE,
    universe_id: str = "",
    run_id: str = "",
    ticket: int | None = None,
    parent: Seat | None = None,
    wait_s: float | None = None,
    on_waiting: Callable[[Waiting], None] | None = None,
    seats: int | None = None,
    reserve: int | None = None,
    db: Path | None = None,
) -> Seat | Waiting:
    """`acquire`, waiting for a seat. ``wait_s=None`` waits until served; a float
    gives back the :class:`Waiting` (its position kept) when it elapses.

    ``parent`` is the seat this caller is nested in; re-entry is retried on every
    poll, so a sibling that found the seat lent takes it as soon as it is back.
    ``on_waiting`` is told when waiting starts and every `WAITING_NOTICE_SECONDS`.
    A notice that fails never costs the seat -- except a run's cancellation
    (``RunCancelledError``), which is how a cancelled run stops waiting: its queue
    position is given up and the cancellation propagates.
    """
    ticket_box: list[int | None] = [ticket]
    try:
        return _wait_for_seat(
            account_id, seat_class=seat_class, kind=kind, universe_id=universe_id,
            run_id=run_id, ticket_box=ticket_box, parent=parent, wait_s=wait_s,
            on_waiting=on_waiting, seats=seats, reserve=reserve, db=db,
        )
    except BaseException:
        abandon(ticket_box[0], db=db)
        raise


def _is_cancellation(exc: BaseException) -> bool:
    # By name, as `graph_compiler._is_cancel_exception` does: `runs` imports this.
    return type(exc).__name__ == "RunCancelledError"


def _wait_for_seat(account_id, *, seat_class, kind, universe_id, run_id, ticket_box,
                   parent, wait_s, on_waiting, seats, reserve, db) -> Seat | Waiting:
    deadline = None if wait_s is None else time.monotonic() + max(0.0, wait_s)
    notified_at: float | None = None
    ticket = ticket_box[0]
    if parent is not None and parent.account_id != account_id:
        parent = None
    while True:
        outcome = acquire(
            account_id, seat_class=seat_class, kind=kind, universe_id=universe_id,
            run_id=run_id, ticket=ticket,
            parent_seat_id=parent.seat_id if parent else None,
            parent_depth=parent.depth if parent else 1,
            seats=seats, reserve=reserve, db=db,
        )
        if isinstance(outcome, Seat):
            _register(outcome)
            return outcome
        ticket = ticket_box[0] = outcome.ticket
        now_m = time.monotonic()
        if on_waiting is not None and (
            notified_at is None or now_m - notified_at >= WAITING_NOTICE_SECONDS
        ):
            notified_at = now_m
            try:
                on_waiting(outcome)
            except Exception as exc:  # noqa: BLE001 - a notice is not a step
                if _is_cancellation(exc):
                    raise
                _log.warning("waiting notice failed for %s", account_id, exc_info=True)
        if deadline is not None and now_m >= deadline:
            return outcome
        time.sleep(_POLL_SECONDS)


def detach_seat() -> None:
    """Clear the current seat in THIS context. For a copied context handed to
    work that runs alongside its caller (a queued run), which must take its own
    seats rather than borrow one its still-running caller is using."""
    _current_seat.set(None)


@contextmanager
def carrying(seat: Seat):
    """Make ``seat`` the current one for the body WITHOUT releasing it after:
    for a caller whose release is owned elsewhere (the agent node's future).
    A blocking call nested inside re-enters it rather than waiting for a seat its
    own blocked parent holds."""
    token = _current_seat.set(seat)
    try:
        yield seat
    finally:
        _current_seat.reset(token)


@contextmanager
def bound(seat: Seat):
    """Make ``seat`` the current one for the body (nested calls re-enter it) and
    release it on every exit path. A crash is covered by proven death."""
    token = _current_seat.set(seat)
    try:
        yield seat
    finally:
        _current_seat.reset(token)
        release(seat.seat_id, db=seat.db)


@contextmanager
def hold(
    account_id: str,
    *,
    seat_class: str = CLASS_BACKGROUND,
    kind: str = KIND_AGENT_NODE,
    universe_id: str = "",
    run_id: str = "",
    on_waiting: Callable[[Waiting], None] | None = None,
    seats: int | None = None,
    reserve: int | None = None,
    db: Path | None = None,
):
    """Wait for a seat (no deadline), hold it for the body, release it after.

    No deadline, because work that has queued must not bounce back: a bounce reads
    as a refusal however it is worded. An interactive wait is bounded in practice
    by the reserve -- a chat only ever waits behind another chat.
    """
    seat = acquire_blocking(
        account_id, seat_class=seat_class, kind=kind, universe_id=universe_id,
        run_id=run_id, parent=current_seat(), wait_s=None, on_waiting=on_waiting,
        seats=seats, reserve=reserve, db=db,
    )
    with bound(seat):
        yield seat


# Positions held by pollers that must not block (the automation pump). Keyed by
# the caller's durable work id, so the next poll re-presents the same ticket.
_tickets_lock = threading.Lock()
_tickets: dict[str, int] = {}


def try_acquire(
    work_key: str,
    account_id: str,
    *,
    seat_class: str = CLASS_BACKGROUND,
    kind: str = KIND_AUTOMATION,
    universe_id: str = "",
    db: Path | None = None,
) -> Seat | Waiting:
    """One non-blocking attempt, keeping ``work_key``'s queue position between
    polls. For a worker whose work stays durable and due while it waits, so the
    wait holds no thread and claims no attempt."""
    with _tickets_lock:
        ticket = _tickets.get(work_key)
    outcome = acquire(
        account_id, seat_class=seat_class, kind=kind, universe_id=universe_id,
        ticket=ticket, db=db,
    )
    with _tickets_lock:
        if isinstance(outcome, Seat):
            _tickets.pop(work_key, None)
        else:
            _tickets[work_key] = outcome.ticket
    if isinstance(outcome, Seat):
        _register(outcome)
    return outcome


__all__ = [
    "CLASS_BACKGROUND",
    "CLASS_INTERACTIVE",
    "KIND_AGENT_NODE",
    "KIND_AUTOMATION",
    "KIND_CHAT_TURN",
    "KIND_WAKE",
    "LEDGER_NAME",
    "SEAT_CLASSES",
    "SEAT_LEASE_SECONDS",
    "SEAT_REFRESH_SECONDS",
    "WAITER_LEASE_SECONDS",
    "WAITING_NOTICE_SECONDS",
    "Seat",
    "SeatLedgerUnusable",
    "Waiting",
    "abandon",
    "account_key",
    "acquire",
    "acquire_blocking",
    "bound",
    "carrying",
    "current_seat",
    "detach_seat",
    "hold",
    "holder_is_named",
    "ledger_path",
    "limits_for_key",
    "occupancy",
    "owner_of_key",
    "refresh",
    "release",
    "status_for_owner",
    "stop_refresher",
    "tier_of_key",
    "try_acquire",
    "waiting_message",
]
