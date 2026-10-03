"""The broker's durable operation record: a request is never sent twice (I14 decision 7).

Every stream names an operation by ``op_id`` (a ULID). The broker records it,
keyed by the authority namespace it was admitted under (owner principal and
command center) and bound to a digest of the canonical effective request:

* ``reserved``: an atomic insert at admission. A second open of the same
  operation, concurrent or later, finds the record and never sends.
* ``may_have_sent``: written durably BEFORE the first byte reaches the network.
  After a crash it reads as unknown, never as "not sent".
* terminal: ``completed``, ``failed``, ``cancelled`` or ``refused``, each keeping
  whether a byte may have left (``sent``).

Expiry never reopens an id. A durable cutoff only moves forward:
``max(previous, now - retention)``, so a clock that steps back cannot lower it.
An ``op_id`` stamped before the cutoff, or more than ``max_skew`` ahead of the
clock, is refused. A record is deleted only once its id is below the cutoff,
which is exactly when no open could name it again. At capacity new admissions
are refused rather than an admissible record evicted.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

RESERVED = "reserved"
MAY_HAVE_SENT = "may_have_sent"
TERMINAL = frozenset({"completed", "failed", "cancelled", "refused"})

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_VALUES = {c: i for i, c in enumerate(_CROCKFORD)}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops (
    namespace TEXT NOT NULL,
    op_id     TEXT NOT NULL,
    digest    TEXT NOT NULL,
    state     TEXT NOT NULL,
    sent      INTEGER NOT NULL DEFAULT 0,
    stamp_ms  INTEGER NOT NULL,
    PRIMARY KEY (namespace, op_id)
);
CREATE INDEX IF NOT EXISTS ops_by_stamp ON ops (stamp_ms);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""


class OpIdInvalid(ValueError):
    """Not a ULID."""


def new_op_id(clock: Callable[[], float] = time.time) -> str:
    """A fresh ULID: 48-bit millisecond timestamp, 80 random bits."""
    import secrets

    value = (int(clock() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def canonical_op_id(op_id: str) -> str:
    """The one spelling an op_id is stored under: ULIDs are case-insensitive."""
    ulid_stamp_ms(op_id)
    return op_id.upper()


def ulid_stamp_ms(op_id: str) -> int:
    """The millisecond timestamp a ULID carries in its first ten characters."""
    if not isinstance(op_id, str) or len(op_id) != 26:
        raise OpIdInvalid("op_id must be a 26-character ULID")
    value = 0
    for char in op_id.upper():
        if char not in _VALUES:
            raise OpIdInvalid("op_id must be a 26-character ULID")
    for char in op_id[:10].upper():
        value = value * 32 + _VALUES[char]
    if op_id[0] not in "01234567":
        raise OpIdInvalid("op_id timestamp overflows 48 bits")
    return value


@dataclass(frozen=True, slots=True)
class OpRecord:
    namespace: str
    op_id: str
    digest: str
    state: str
    sent: bool


@dataclass(frozen=True, slots=True)
class Admission:
    """``new`` (reserved now), ``existing`` (same request, already recorded),
    ``mismatch`` (a different request under the same id), ``expired``,
    ``future`` or ``full``. ``record`` is set for new, existing and mismatch."""

    kind: str
    record: OpRecord | None = None


class OpStore:
    """SQLite-backed operation records; one per broker state directory."""

    def __init__(self, path: Path, *, retention_s: float = 24 * 3600, max_skew_s: float = 300,
                 capacity: int = 1_000_000, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._retention_ms = int(retention_s * 1000)
        self._skew_ms = int(max_skew_s * 1000)
        self._capacity = capacity
        self._clock = clock
        self._lock = threading.Lock()
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        # may_have_sent must survive a power cut before the first byte leaves.
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _advance_cutoff(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'cutoff_ms'").fetchone()
        cutoff = max(row[0] if row else 0, self._now_ms() - self._retention_ms)
        conn.execute("INSERT INTO meta (key, value) VALUES ('cutoff_ms', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (cutoff,))
        # Deletion only below the cutoff: those ids can never be admitted again.
        conn.execute("DELETE FROM ops WHERE stamp_ms < ?", (cutoff,))
        return cutoff

    @staticmethod
    def _record(row) -> OpRecord:
        return OpRecord(row[0], row[1], row[2], row[3], bool(row[4]))

    def admit(self, namespace: str, op_id: str, digest: str) -> Admission:
        if not namespace or not digest:
            raise ValueError("a namespace and a request digest are required")
        op_id = canonical_op_id(op_id)
        stamp = ulid_stamp_ms(op_id)
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cutoff = self._advance_cutoff(conn)
                if stamp < cutoff:
                    conn.execute("COMMIT")
                    return Admission("expired")
                if stamp > self._now_ms() + self._skew_ms:
                    conn.execute("COMMIT")
                    return Admission("future")
                row = conn.execute(
                    "SELECT namespace, op_id, digest, state, sent FROM ops "
                    "WHERE namespace = ? AND op_id = ?", (namespace, op_id),
                ).fetchone()
                if row is not None:
                    conn.execute("COMMIT")
                    record = self._record(row)
                    return Admission("existing" if record.digest == digest else "mismatch",
                                     record)
                (count,) = conn.execute("SELECT COUNT(*) FROM ops").fetchone()
                if count >= self._capacity:
                    conn.execute("COMMIT")
                    return Admission("full")
                conn.execute(
                    "INSERT INTO ops (namespace, op_id, digest, state, sent, stamp_ms) "
                    "VALUES (?, ?, ?, ?, 0, ?)", (namespace, op_id, digest, RESERVED, stamp),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return Admission("new", OpRecord(namespace, op_id, digest, RESERVED, False))

    def _set(self, namespace: str, op_id: str, state: str, *, sent: bool | None) -> None:
        op_id = canonical_op_id(op_id)
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE ops SET state = ?, sent = CASE WHEN ? IS NULL THEN sent ELSE ? END "
                "WHERE namespace = ? AND op_id = ?",
                (state, sent, int(bool(sent)), namespace, op_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("no such operation")

    def mark_may_have_sent(self, namespace: str, op_id: str) -> None:
        """Durable before the first byte is written; the caller writes only after it returns."""
        self._set(namespace, op_id, MAY_HAVE_SENT, sent=True)

    def finish(self, namespace: str, op_id: str, state: str) -> None:
        if state not in TERMINAL:
            raise ValueError(f"not a terminal state: {state!r}")
        # `sent` is never cleared: once a byte may have left, it may have.
        self._set(namespace, op_id, state, sent=None)

    def status(self, namespace: str, op_id: str) -> OpRecord | str:
        """The record, or ``"expired"`` (below the cutoff), or ``"not_found"``.

        ``not_found`` within the window means the broker never admitted it;
        ``expired`` is never evidence that nothing was sent.
        """
        op_id = canonical_op_id(op_id)
        stamp = ulid_stamp_ms(op_id)
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Persisted like an admission's: an id this answers "expired"
                # for can never be admitted later, whatever the clock does.
                cutoff = self._advance_cutoff(conn)
                found = None if stamp < cutoff else conn.execute(
                    "SELECT namespace, op_id, digest, state, sent FROM ops "
                    "WHERE namespace = ? AND op_id = ?", (namespace, op_id),
                ).fetchone()
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        if stamp < cutoff:
            return "expired"
        return self._record(found) if found else "not_found"

    def recover(self) -> int:
        """After a restart: every operation that was mid-flight becomes unknown.

        ``reserved`` with nothing sent is ``refused`` (provably never sent);
        ``may_have_sent`` stays as it is, which reads as unknown.
        """
        with self._lock, closing(self._connect()) as conn:
            return conn.execute(
                "UPDATE ops SET state = 'refused' WHERE state = ? AND sent = 0", (RESERVED,),
            ).rowcount
