"""The control plane's trigger table and fire ledger (design D7, D8a).

Platform state only: one SQLite database at the data root,
``.control_plane.db`` (D8a row 4, a root database). Nothing here reads or
writes a command center's own directory, so listing, scheduling and
measuring triggers never touch -- and under the sealed box never wake -- a box.

Two tables:

* ``triggers`` -- one row per trigger the control plane owns. Today that is the
  ``proactive`` kind (harness §4.5's idle research, engagement-decayed).
  User-declared schedules stay rows of ``.automations.db``, which is the same
  kind of platform-state trigger table, pumped by the same owner tick.
* ``trigger_fires`` -- one row per fire, keyed ``(trigger_key, due_at)``. The
  key is the fence: a fire is claimed by inserting it, in the same
  transaction that advances the trigger, so a restart, a second tick or a
  successor owner can never fire the same due instant twice. A claim that
  never reached its handler (the process died in between) is settled
  ``lost_on_restart`` by the next owner -- at most once, never replayed.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tinyassets.control_plane.cadence import CadencePolicy, policy_with

DB_FILENAME = ".control_plane.db"

KIND_PROACTIVE = "proactive"
TRIGGER_KINDS = frozenset({KIND_PROACTIVE})

OUTCOME_CLAIMED = "claimed"
OUTCOME_STARTED = "started"
OUTCOME_LOST = "lost_on_restart"
DECLINED_PREFIX = "declined:"
FAILED_PREFIX = "failed:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    trigger_key        TEXT PRIMARY KEY,
    kind               TEXT NOT NULL,
    command_center_id  TEXT NOT NULL,
    agent_id           TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,
    policy_override    TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    engaged_at         TEXT NOT NULL DEFAULT '',
    last_due_at        TEXT NOT NULL DEFAULT '',
    last_run_id        TEXT NOT NULL DEFAULT '',
    coalesced_total    INTEGER NOT NULL DEFAULT 0,
    -- Bumped by every write that changes when the trigger is owed (engagement,
    -- enable, override, a claim): the claim's compare-and-set reads it, so a
    -- decision made on a stale snapshot never fires.
    revision           INTEGER NOT NULL DEFAULT 1,
    updated_at         TEXT NOT NULL,
    UNIQUE (kind, command_center_id, agent_id)
);
CREATE TABLE IF NOT EXISTS trigger_fires (
    trigger_key      TEXT NOT NULL,
    due_at           TEXT NOT NULL,
    -- The person the fire acted for: account deletion sweeps by this column.
    owner_principal_id TEXT NOT NULL,
    owner_generation INTEGER NOT NULL,
    -- The claiming scheduler instance: a successor settles every claim that is
    -- not its own, even one made in the same second under the same generation.
    owner_incarnation TEXT NOT NULL,
    claimed_at       TEXT NOT NULL,
    started_at       TEXT NOT NULL DEFAULT '',
    run_id           TEXT NOT NULL DEFAULT '',
    outcome          TEXT NOT NULL,
    lag_s            REAL,
    collapsed        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (trigger_key, due_at)
);
CREATE INDEX IF NOT EXISTS trigger_fires_by_claim ON trigger_fires(claimed_at);
"""


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(stamp: str) -> datetime | None:
    if not stamp:
        return None
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


DEFAULT_AGENT_ID = "main"


def trigger_key(kind: str, command_center_id: str, agent_id: str = DEFAULT_AGENT_ID) -> str:
    """One trigger per kind, command center and agent (harness §4.18: every
    per-agent record is keyed by agent; "main" is the seeded default)."""
    return f"{kind}:{command_center_id}:{agent_id}"


@dataclass(frozen=True)
class Trigger:
    trigger_key: str
    kind: str
    command_center_id: str
    agent_id: str
    owner_principal_id: str
    enabled: bool
    policy_override: dict[str, Any]
    created_at: datetime
    engaged_at: datetime | None
    last_due_at: datetime | None
    last_run_id: str
    coalesced_total: int
    revision: int

    def policy(self, base: CadencePolicy) -> CadencePolicy:
        return policy_with(base, self.policy_override)


def _row(row: sqlite3.Row) -> Trigger:
    override = json.loads(row["policy_override"]) if row["policy_override"] else {}
    created = parse_stamp(row["created_at"])
    assert created is not None
    return Trigger(
        trigger_key=row["trigger_key"],
        kind=row["kind"],
        command_center_id=row["command_center_id"],
        agent_id=row["agent_id"],
        owner_principal_id=row["owner_principal_id"],
        enabled=bool(row["enabled"]),
        policy_override=override,
        created_at=created,
        engaged_at=parse_stamp(row["engaged_at"]),
        last_due_at=parse_stamp(row["last_due_at"]),
        last_run_id=row["last_run_id"],
        coalesced_total=int(row["coalesced_total"]),
        revision=int(row["revision"]),
    )


class TriggerOwnershipError(PermissionError):
    """The caller is not the trigger's owner."""


class TriggerStore:
    """Rows for one data root. Reads never create the database."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)

    @property
    def db_path(self) -> Path:
        return self.base_path / DB_FILENAME

    def _connect(self, *, create: bool) -> sqlite3.Connection | None:
        if not create and not self.db_path.is_file():
            return None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(_SCHEMA)
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect(create=True)
        assert conn is not None
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    # -- enrolment and owner controls --------------------------------------

    def ensure(
        self,
        kind: str,
        *,
        command_center_id: str,
        owner_principal_id: str,
        now: datetime,
        agent_id: str = DEFAULT_AGENT_ID,
    ) -> Trigger:
        """The trigger for ``kind`` on this command center, created if absent.

        Idempotent. A row that names a different owner is refused loudly: a
        command center has one owner, and silently re-owning a trigger would
        move whose engagement and whose clock decide its fires.
        """
        if kind not in TRIGGER_KINDS:
            raise ValueError(f"unknown trigger kind {kind!r}")
        if not command_center_id or not owner_principal_id or not agent_id:
            raise ValueError("a trigger needs a command center, an agent and an owner")
        key = trigger_key(kind, command_center_id, agent_id)
        stamp = _iso(now)
        with self._write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO triggers (trigger_key, kind, command_center_id, "
                "agent_id, owner_principal_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, kind, command_center_id, agent_id, owner_principal_id, stamp, stamp),
            )
            row = conn.execute(
                "SELECT * FROM triggers WHERE trigger_key = ?", (key,)
            ).fetchone()
        trigger = _row(row)
        if trigger.owner_principal_id != owner_principal_id:
            raise TriggerOwnershipError(
                f"trigger {key} belongs to another principal"
            )
        return trigger

    def get(self, key: str) -> Trigger | None:
        conn = self._connect(create=False)
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT * FROM triggers WHERE trigger_key = ?", (key,)).fetchone()
        finally:
            conn.close()
        return None if row is None else _row(row)

    def list(self, *, kind: str | None = None, enabled_only: bool = False) -> list[Trigger]:
        conn = self._connect(create=False)
        if conn is None:
            return []
        clauses, args = [], []
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            rows = conn.execute(
                f"SELECT * FROM triggers{where} ORDER BY trigger_key", args,
            ).fetchall()
        finally:
            conn.close()
        return [_row(row) for row in rows]

    def _owner_update(self, key: str, principal_id: str, sql: str, args: tuple) -> Trigger:
        with self._write() as conn:
            row = conn.execute(
                "SELECT owner_principal_id FROM triggers WHERE trigger_key = ?", (key,)
            ).fetchone()
            if row is None or row["owner_principal_id"] != principal_id:
                # One refusal for absent and foreign: no probing other owners' rows.
                raise TriggerOwnershipError(f"no trigger {key} for this principal")
            conn.execute(sql, (*args, key))
        updated = self.get(key)
        assert updated is not None
        return updated

    def set_enabled(self, key: str, *, principal_id: str, enabled: bool, now: datetime) -> Trigger:
        return self._owner_update(
            key, principal_id,
            "UPDATE triggers SET enabled = ?, updated_at = ?, revision = revision + 1 "
            "WHERE trigger_key = ?",
            (1 if enabled else 0, _iso(now)),
        )

    def set_cadence_override(
        self,
        key: str,
        *,
        principal_id: str,
        overrides: dict[str, Any] | None,
        base: CadencePolicy,
        now: datetime,
    ) -> Trigger:
        """The owner's per-command-center cadence; ``None`` restores the default.

        Validated against ``base`` before it is stored, so a row never holds a
        policy that would raise at every tick.
        """
        policy_with(base, overrides or {})
        encoded = json.dumps(overrides, sort_keys=True) if overrides else ""
        return self._owner_update(
            key, principal_id,
            "UPDATE triggers SET policy_override = ?, updated_at = ?, "
            "revision = revision + 1 WHERE trigger_key = ?",
            (encoded, _iso(now)),
        )

    def note_engagement(self, command_center_id: str, *, principal_id: str, at: datetime) -> int:
        """The owner interacted: decay resets to engaged. Returns rows updated.

        Only the trigger's own owner counts. A visitor's or collaborator's
        message is not the owner's engagement, and an older stamp never
        replaces a newer one.
        """
        conn = self._connect(create=False)
        if conn is None:
            return 0
        stamp = _iso(at)
        try:
            cursor = conn.execute(
                "UPDATE triggers SET engaged_at = ?, updated_at = ?, revision = revision + 1 "
                "WHERE command_center_id = ? AND owner_principal_id = ? AND engaged_at < ?",
                (stamp, stamp, command_center_id, principal_id, stamp),
            )
            return int(cursor.rowcount or 0)
        finally:
            conn.close()

    # -- the fence ---------------------------------------------------------

    def claim_fire(
        self,
        key: str,
        *,
        due_at: datetime,
        expected_revision: int,
        owner_generation: int,
        owner_incarnation: str,
        collapsed: int,
        now: datetime,
        admit: Callable[[], bool] | None = None,
    ) -> bool:
        """Claim the fire for ``due_at``. False when it is not this caller's.

        One transaction: the trigger must still be at the revision the caller
        decided on -- no engagement, enable or override change, and no other
        claim, since -- and the ``(trigger_key, due_at)`` row must not exist.
        Both pass, or nothing is written. Admission runs after the writer lock
        is held, because waiting for it can carry a wake outside active hours.
        The unresolved-claim barrier preserves single flight if finishing fails.
        """
        due = _iso(due_at)
        with self._write() as conn:
            if admit is not None and not admit():
                return False
            row = conn.execute(
                "SELECT revision, enabled, owner_principal_id FROM triggers "
                "WHERE trigger_key = ?", (key,)
            ).fetchone()
            if row is None or not row["enabled"] or int(row["revision"]) != int(expected_revision):
                return False
            inserted = conn.execute(
                "INSERT OR IGNORE INTO trigger_fires (trigger_key, due_at, owner_principal_id, "
                "owner_generation, owner_incarnation, claimed_at, outcome, collapsed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, due, row["owner_principal_id"], int(owner_generation), owner_incarnation,
                 _iso(now), OUTCOME_CLAIMED, int(collapsed)),
            ).rowcount
            if not inserted:
                return False
            conn.execute(
                "UPDATE triggers SET last_due_at = ?, last_run_id = ?, revision = revision + 1, "
                "coalesced_total = coalesced_total + ?, updated_at = ? WHERE trigger_key = ?",
                (due, f"claim:{due}", int(collapsed), _iso(now), key),
            )
        return True

    def finish_fire(
        self, key: str, *, due_at: datetime, outcome: str, run_id: str, now: datetime,
    ) -> None:
        due = _iso(due_at)
        lag = max(0.0, (now - due_at).total_seconds())
        with self._write() as conn:
            conn.execute(
                "UPDATE trigger_fires SET outcome = ?, run_id = ?, started_at = ?, lag_s = ? "
                "WHERE trigger_key = ? AND due_at = ? AND outcome = ?",
                (outcome, run_id, _iso(now), lag, key, due, OUTCOME_CLAIMED),
            )
            conn.execute(
                "UPDATE triggers SET last_run_id = ?, updated_at = ? "
                "WHERE trigger_key = ? AND last_due_at = ?",
                (run_id, _iso(now), key, due),
            )

    def settle_lost_claims(self, *, owner_generation: int, owner_incarnation: str) -> int:
        """Settle claims an earlier owner never started. Returns how many.

        A claim is an earlier owner's when its generation is older, or -- under
        today's single-process lease, where the generation never moves -- when
        another scheduler instance made it. Only one owner runs at a time, so
        such a claim is not in flight. Its handler may or may not have run, so
        it is recorded lost, never fired again. Clear only the barriers naming
        these claims, atomically with settlement, so later windows can proceed.
        """
        conn = self._connect(create=False)
        if conn is None:
            return 0
        try:
            conn.execute("BEGIN IMMEDIATE")
            lost = conn.execute(
                "SELECT trigger_key, due_at FROM trigger_fires WHERE outcome = ? "
                "AND (owner_generation < ? OR owner_incarnation != ?)",
                (OUTCOME_CLAIMED, int(owner_generation), owner_incarnation),
            ).fetchall()
            cursor = conn.execute(
                "UPDATE trigger_fires SET outcome = ? WHERE outcome = ? "
                "AND (owner_generation < ? OR owner_incarnation != ?)",
                (OUTCOME_LOST, OUTCOME_CLAIMED, int(owner_generation), owner_incarnation),
            )
            conn.executemany(
                "UPDATE triggers SET last_run_id = '' "
                "WHERE trigger_key = ? AND last_run_id = ?",
                [(row["trigger_key"], f"claim:{row['due_at']}") for row in lost],
            )
            conn.execute("COMMIT")
            return int(cursor.rowcount or 0)
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def fires_since(self, since: datetime) -> list[dict[str, Any]]:
        conn = self._connect(create=False)
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM trigger_fires WHERE claimed_at >= ? ORDER BY claimed_at",
                (_iso(since),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]


def window_start(now: datetime, hours: int = 24) -> datetime:
    return now - timedelta(hours=hours)


__all__ = [
    "DB_FILENAME",
    "DECLINED_PREFIX",
    "DEFAULT_AGENT_ID",
    "FAILED_PREFIX",
    "KIND_PROACTIVE",
    "OUTCOME_CLAIMED",
    "OUTCOME_LOST",
    "OUTCOME_STARTED",
    "TRIGGER_KINDS",
    "Trigger",
    "TriggerOwnershipError",
    "TriggerStore",
    "parse_stamp",
    "trigger_key",
    "window_start",
]
