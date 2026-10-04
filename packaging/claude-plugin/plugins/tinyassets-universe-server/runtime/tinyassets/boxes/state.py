"""The box host's own durable record: placement epochs, change generations, operation outcomes.

This is the box HOST's state, not the platform's and not the box's. It answers
`committed_generation` while a box sleeps, refuses stale epochs, and makes every
mutation idempotent by operation id.

Three rules:

* **One box host at a time.** The state directory is owned through an exclusive
  ``flock`` held for the host's whole life. A second host over the same state
  refuses to start while the first is alive. A crashed host's lock dies with it.
* **A new host reaps what the old one left running.** Each exec records its
  process group and that leader's start time. At startup, every exec still
  ``running`` whose leader is the same process (same start time, so not a reused
  pid) has its whole group killed. Only then is it marked unknown.
* **Outcomes are fenced to the host incarnation that started them.** Every time
  the box host starts, it takes a new incarnation number and marks every
  operation still ``running`` as ``unknown_after_restore``. An operation records
  the incarnation it began under, and its completion is written only if the
  operation is still ``running`` under that same incarnation. A supervisor left
  over from an older host therefore cannot overwrite "unknown" with "done".
* **An operation that may have had an effect is never forgotten.** It ends
  ``done``, either with its outcome or with a recorded failure, and a retry gets
  that record back instead of a second run. Only a refusal raised *before* any
  effect calls :meth:`abandon`.
* **A generation is coherent only while nothing is pending.** Mutations and
  running executions hold a pending count on their box. A snapshot reader
  accepts a generation only when that count was zero before and after its reads.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tinyassets.boxes.provider import BoxError, OpIdReuse

__all__ = ["BoxHostState", "op_digest"]

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS host (k TEXT PRIMARY KEY, v INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS boxes ("
    " command_center_id TEXT PRIMARY KEY,"
    " epoch INTEGER NOT NULL,"
    " generation INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ops ("
    " command_center_id TEXT NOT NULL,"
    " op_id TEXT NOT NULL,"
    " kind TEXT NOT NULL,"
    " digest TEXT NOT NULL,"
    " state TEXT NOT NULL,"  # running | done | unknown_after_restore
    " incarnation INTEGER NOT NULL,"
    " outcome TEXT,"
    " PRIMARY KEY (command_center_id, op_id))",
    "CREATE TABLE IF NOT EXISTS execs ("
    " exec_id TEXT PRIMARY KEY,"
    " command_center_id TEXT NOT NULL,"
    " op_id TEXT NOT NULL)",
)


def op_digest(kind: str, payload: Any) -> str:
    """Identity of an operation's arguments, so a reused op id with new arguments is refused."""
    blob = json.dumps([kind, payload], sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class BoxHostState:
    """One SQLite file under the box host's private state directory."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._pending: dict[str, int] = {}
        import fcntl  # POSIX only; callers refuse non-POSIX hosts before reaching here

        self._owner_fd: int | None = os.open(
            self._path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600
        )
        try:
            fcntl.flock(self._owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._owner_fd)
            self._owner_fd = None
            raise BoxError(
                f"another box host owns {self._path.parent}; one host at a time"
            ) from None
        try:
            self._open_and_recover()
        except BaseException:
            self.close()  # never keep ownership of a host that failed to start
            raise

    def _open_and_recover(self) -> None:
        with self._conn() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            row = conn.execute("SELECT v FROM host WHERE k = 'incarnation'").fetchone()
            self.incarnation = (int(row[0]) if row else 0) + 1
            conn.execute(
                "INSERT INTO host (k, v) VALUES ('incarnation', ?)"
                " ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (self.incarnation,),
            )
            # A restart: kill whatever the old host left running, then mark it unknown.
            for (outcome,) in conn.execute(
                "SELECT outcome FROM ops WHERE state = 'running' AND kind = 'exec'"
            ):
                data = json.loads(outcome) if outcome else {}
                _reap_survivor(data.get("pgid"), data.get("start_time"))
            # Content may have changed under an operation whose outcome is now unknown:
            # advance those boxes' generations so no cache or cas trusts the old one.
            conn.execute(
                "UPDATE boxes SET generation = generation + 1 WHERE command_center_id IN"
                " (SELECT DISTINCT command_center_id FROM ops WHERE state = 'running')"
            )
            conn.execute(
                "UPDATE ops SET state = 'unknown_after_restore' WHERE state = 'running'"
            )

    def close(self) -> None:
        """Give up ownership of the state directory (a clean host shutdown)."""
        if self._owner_fd is not None:
            fd, self._owner_fd = self._owner_fd, None
            os.close(fd)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    # -- pending work (in memory: it describes this host incarnation only) ----------

    def hold(self, cc: str) -> None:
        with self._lock:
            self._pending[cc] = self._pending.get(cc, 0) + 1

    def release(self, cc: str) -> None:
        with self._lock:
            left = self._pending.get(cc, 0) - 1
            if left > 0:
                self._pending[cc] = left
            else:
                self._pending.pop(cc, None)

    def pending(self, cc: str) -> int:
        with self._lock:
            return self._pending.get(cc, 0)

    # -- epochs and generations ------------------------------------------------

    def _row(self, conn: sqlite3.Connection, cc: str) -> tuple[int, int]:
        row = conn.execute(
            "SELECT epoch, generation FROM boxes WHERE command_center_id = ?", (cc,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO boxes (command_center_id, epoch, generation) VALUES (?, 1, 0)",
                (cc,),
            )
            return 1, 0
        return int(row[0]), int(row[1])

    def epoch(self, cc: str) -> int:
        with self._lock, self._conn() as conn:
            return self._row(conn, cc)[0]

    def generation(self, cc: str) -> int:
        with self._lock, self._conn() as conn:
            return self._row(conn, cc)[1]

    def bump_generation(self, cc: str) -> int:
        with self._lock, self._conn() as conn:
            _, gen = self._row(conn, cc)
            conn.execute(
                "UPDATE boxes SET generation = ? WHERE command_center_id = ?", (gen + 1, cc)
            )
            return gen + 1

    def bump_epoch(self, cc: str) -> int:
        """A destroy or re-import: every handle minted before this is stale."""
        with self._lock, self._conn() as conn:
            epoch, gen = self._row(conn, cc)
            conn.execute(
                "UPDATE boxes SET epoch = ?, generation = ? WHERE command_center_id = ?",
                (epoch + 1, gen + 1, cc),
            )
            return epoch + 1

    # -- operation outcomes ----------------------------------------------------

    def begin(self, cc: str, op_id: str, kind: str, digest: str) -> dict[str, Any] | None:
        """Record ``op_id`` as running under this incarnation, or return the earlier record.

        The caller runs the operation only when this returns None. A returned record
        is ``done`` (its outcome), ``running`` (in flight on this host) or
        ``unknown_after_restore`` (hold; never re-run).
        """
        if not isinstance(op_id, str) or not op_id or len(op_id) > 200:
            raise OpIdReuse(f"op_id must be a non-empty string of at most 200 chars, got {op_id!r}")
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT kind, digest, state, outcome FROM ops"
                " WHERE command_center_id = ? AND op_id = ?",
                (cc, op_id),
            ).fetchone()
            if row is not None:
                if row[0] != kind or row[1] != digest:
                    raise OpIdReuse(
                        f"op_id {op_id!r} was already used for a different {row[0]} operation"
                    )
                return {
                    "state": row[2],
                    "outcome": json.loads(row[3]) if row[3] else None,
                }
            conn.execute(
                "INSERT INTO ops (command_center_id, op_id, kind, digest, state, incarnation)"
                " VALUES (?, ?, ?, ?, 'running', ?)",
                (cc, op_id, kind, digest, self.incarnation),
            )
            return None

    def update(self, cc: str, op_id: str, outcome: dict[str, Any]) -> None:
        """Attach progress to an operation still running under this incarnation."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE ops SET outcome = ? WHERE command_center_id = ? AND op_id = ?"
                " AND state = 'running' AND incarnation = ?",
                (json.dumps(outcome), cc, op_id, self.incarnation),
            )

    def finish(self, cc: str, op_id: str, outcome: dict[str, Any]) -> bool:
        """Record the outcome, only if still running under this incarnation. True if written."""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE ops SET state = 'done', outcome = ?"
                " WHERE command_center_id = ? AND op_id = ?"
                " AND state = 'running' AND incarnation = ?",
                (json.dumps(outcome), cc, op_id, self.incarnation),
            )
            return cur.rowcount == 1

    def abandon(self, cc: str, op_id: str) -> None:
        """Forget an operation refused BEFORE any effect, so a corrected retry can run."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM ops WHERE command_center_id = ? AND op_id = ?"
                " AND state = 'running' AND incarnation = ?",
                (cc, op_id, self.incarnation),
            )

    def lookup(self, cc: str, op_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT kind, state, outcome FROM ops WHERE command_center_id = ? AND op_id = ?",
                (cc, op_id),
            ).fetchone()
        if row is None:
            return None
        return {"kind": row[0], "state": row[1],
                "outcome": json.loads(row[2]) if row[2] else None}

    def register_exec(self, cc: str, op_id: str, exec_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO execs (exec_id, command_center_id, op_id) VALUES (?, ?, ?)",
                (exec_id, cc, op_id),
            )

    def forget_exec(self, cc: str, exec_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM execs WHERE exec_id = ? AND command_center_id = ?", (exec_id, cc)
            )

    def find_exec(self, cc: str, exec_id: str) -> dict[str, Any] | None:
        """The exec's operation record, only if it belongs to this command center."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT e.op_id, o.state, o.outcome FROM execs e"
                " JOIN ops o ON o.command_center_id = e.command_center_id AND o.op_id = e.op_id"
                " WHERE e.exec_id = ? AND e.command_center_id = ?",
                (exec_id, cc),
            ).fetchone()
        if row is None:
            return None
        return {"op_id": row[0], "state": row[1],
                "outcome": json.loads(row[2]) if row[2] else {}}


def process_start_time(pid: int) -> int | None:
    """The kernel's start time (clock ticks since boot) for ``pid``, or None if it is gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = raw[raw.rindex(")") + 2:].split()
    return int(fields[19])  # field 22 overall: starttime


def _reap_survivor(pgid: object, start_time: object) -> None:
    """Kill a group the previous host left running, if its leader is still that same process."""
    if not isinstance(pgid, int) or pgid <= 1 or not isinstance(start_time, int):
        return
    if process_start_time(pgid) != start_time:
        return  # gone, or the pid now belongs to an unrelated process
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
