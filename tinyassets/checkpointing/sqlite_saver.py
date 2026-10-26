"""SqliteSaver wrapper with WAL mode, retention, and graph compilation helpers.

Uses ``SqliteSaver.from_conn_string()`` context manager pattern as required.
WAL mode is set automatically by ``SqliteSaver.setup()`` (the library already
includes ``PRAGMA journal_mode=WAL`` in its setup script).  We additionally
ensure WAL mode is verified after setup.

For on-disk databases, the WAL journal is also pre-initialized before handing
the connection string to ``SqliteSaver`` to guarantee WAL mode is active from
the very first write.

Typical usage
-------------
::

    from tinyassets.checkpointing import create_checkpointer

    # Option 1: Context manager (preferred for long-running daemon)
    with create_checkpointer("data/checkpoints.db") as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        result = graphs["scene"].invoke(input_state, config)

    # Option 2: In-memory for tests
    with create_checkpointer(":memory:") as checkpointer:
        ...

"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator

from langgraph.checkpoint.sqlite import SqliteSaver

from tinyassets.exceptions import CheckpointError

if TYPE_CHECKING:
    pass


DEFAULT_CHECKPOINT_RETENTION_KEEP_LAST = 500
CHECKPOINT_RETENTION_KEEP_LAST_ENV = "TINYASSETS_CHECKPOINT_RETENTION_KEEP_LAST"


# ---------------------------------------------------------------------------
# WAL mode initialization
# ---------------------------------------------------------------------------


def _ensure_wal_mode(db_path: str) -> None:
    """Pre-initialize WAL journal mode on an on-disk database.

    This is a no-op for ``:memory:`` databases (WAL is not supported for
    in-memory SQLite).  For file-backed databases, we open a temporary
    connection, set WAL mode, and close it before ``SqliteSaver`` takes
    ownership.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file, or ``":memory:"``.
    """
    if db_path == ":memory:":
        return

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        result = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
        if result and result[0].lower() != "wal":
            raise CheckpointError(
                f"Failed to set WAL mode on {db_path!r}: got {result[0]!r}"
            )
    finally:
        conn.close()


def verify_wal_mode(checkpointer: SqliteSaver) -> bool:
    """Verify that the checkpointer's connection is using WAL journal mode.

    Parameters
    ----------
    checkpointer : SqliteSaver
        An active SqliteSaver instance.

    Returns
    -------
    bool
        True if WAL mode is active, False otherwise.
        In-memory databases always return False (WAL not supported).
    """
    try:
        result = checkpointer.conn.execute("PRAGMA journal_mode;").fetchone()
        return result is not None and result[0].lower() == "wal"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Checkpointer factory
# ---------------------------------------------------------------------------


@contextmanager
def create_checkpointer(
    db_path: str = "checkpoints.db",
) -> Iterator[SqliteSaver]:
    """Create a SqliteSaver with WAL mode enabled.

    Uses the ``SqliteSaver.from_conn_string()`` context manager pattern
    as specified in the architecture docs.  For on-disk databases, WAL
    mode is pre-initialized before the SqliteSaver takes ownership.

    Parameters
    ----------
    db_path : str
        Path to the checkpoint database file.  Use ``":memory:"`` for
        in-memory databases (tests).  Defaults to ``"checkpoints.db"``.

    Yields
    ------
    SqliteSaver
        A configured SqliteSaver ready for ``graph.compile(checkpointer=...)``.

    Raises
    ------
    CheckpointError
        If WAL mode cannot be set on a file-backed database.

    Examples
    --------
    ::

        with create_checkpointer("data/checkpoints.db") as cp:
            compiled = graph.compile(checkpointer=cp)
            result = compiled.invoke(state, config)
    """
    _ensure_wal_mode(db_path)

    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        yield checkpointer


# ---------------------------------------------------------------------------
# Checkpoint history / resume helpers
# ---------------------------------------------------------------------------


def get_checkpoint_history(
    checkpointer: SqliteSaver,
    thread_id: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List recent checkpoints for a given thread.

    Parameters
    ----------
    checkpointer : SqliteSaver
        An active SqliteSaver instance.
    thread_id : str
        The thread ID to query checkpoints for.
    limit : int
        Maximum number of checkpoints to return (most recent first).

    Returns
    -------
    list[dict[str, Any]]
        List of checkpoint metadata dicts, each containing:
        - ``checkpoint_id``: unique checkpoint identifier
        - ``thread_id``: the thread this checkpoint belongs to
        - ``parent_checkpoint_id``: parent checkpoint (or None for root)
        - ``metadata``: arbitrary metadata stored with the checkpoint
    """
    config = {"configurable": {"thread_id": thread_id}}
    checkpoints = []
    for checkpoint_tuple in checkpointer.list(config, limit=limit):
        checkpoints.append(
            {
                "checkpoint_id": checkpoint_tuple.config["configurable"].get(
                    "checkpoint_id"
                ),
                "thread_id": thread_id,
                "parent_checkpoint_id": checkpoint_tuple.parent_config["configurable"].get(
                    "checkpoint_id"
                )
                if checkpoint_tuple.parent_config
                else None,
                "metadata": checkpoint_tuple.metadata,
            }
        )
    return checkpoints


def make_resume_config(
    thread_id: str,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    """Build a LangGraph config dict for resuming from a checkpoint.

    Parameters
    ----------
    thread_id : str
        The thread ID to resume.
    checkpoint_id : str or None
        Specific checkpoint ID to resume from.  If None, resumes from
        the latest checkpoint for the thread.

    Returns
    -------
    dict[str, Any]
        Config dict suitable for ``compiled_graph.invoke({}, config)``.
    """
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if checkpoint_id is not None:
        config["configurable"]["checkpoint_id"] = checkpoint_id
    return config


# ---------------------------------------------------------------------------
# Checkpoint retention policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckpointPruneResult:
    """Summary of a direct SQLite checkpoint-retention pass."""

    thread_id: str
    keep_last_n: int
    checkpoints_before: int
    checkpoints_deleted: int
    writes_deleted: int
    namespaces_seen: tuple[str, ...] = ()
    skipped_reason: str = ""


def configured_checkpoint_retention_keep_last() -> int | None:
    """Return configured checkpoint retention count.

    ``None`` means retention is disabled. The default is intentionally
    bounded because unbounded LangGraph checkpoint history can fill the
    daemon host disk. Set ``TINYASSETS_CHECKPOINT_RETENTION_KEEP_LAST=0`` to
    disable retention for a local diagnostic run.
    """
    raw = os.environ.get(CHECKPOINT_RETENTION_KEEP_LAST_ENV, "").strip()
    if not raw:
        return DEFAULT_CHECKPOINT_RETENTION_KEEP_LAST
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHECKPOINT_RETENTION_KEEP_LAST
    if value <= 0:
        return None
    return value


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _sqlite_delete_many(
    conn: sqlite3.Connection,
    sql: str,
    params: list[tuple[object, ...]],
) -> int:
    if not params:
        return 0
    before = conn.total_changes
    conn.executemany(sql, params)
    return conn.total_changes - before


def prune_checkpoint_history(
    conn: sqlite3.Connection,
    thread_id: str,
    *,
    keep_last_n: int,
    checkpoint_ns: str | None = None,
) -> CheckpointPruneResult:
    """Prune old LangGraph SQLite checkpoints without loading checkpoint blobs.

    This is the production retention path. It operates directly on the
    ``checkpoints`` and ``writes`` tables so a multi-GB checkpoint database can
    be bounded without deserializing every historical checkpoint into process
    memory. The newest ``keep_last_n`` checkpoints are preserved per namespace.
    """
    if keep_last_n < 1:
        raise ValueError("keep_last_n must be >= 1")

    checkpoint_cols = _table_columns(conn, "checkpoints")
    if not {"thread_id", "checkpoint_id"}.issubset(checkpoint_cols):
        return CheckpointPruneResult(
            thread_id=thread_id,
            keep_last_n=keep_last_n,
            checkpoints_before=0,
            checkpoints_deleted=0,
            writes_deleted=0,
            skipped_reason="missing_checkpoints_table",
        )

    has_checkpoint_ns = "checkpoint_ns" in checkpoint_cols
    where = "thread_id = ?"
    params: list[object] = [thread_id]
    if checkpoint_ns is not None and has_checkpoint_ns:
        where += " AND checkpoint_ns = ?"
        params.append(checkpoint_ns)

    ns_select = ", checkpoint_ns" if has_checkpoint_ns else ""
    rows = conn.execute(
        f"SELECT rowid, checkpoint_id{ns_select} "
        f"FROM checkpoints WHERE {where} ORDER BY rowid DESC",
        tuple(params),
    ).fetchall()
    namespaces_seen = tuple(
        sorted({str(row[2]) for row in rows})
    ) if has_checkpoint_ns else ()

    grouped: dict[str, list[tuple[int, str, str]]] = {}
    for row in rows:
        ns = str(row[2]) if has_checkpoint_ns else ""
        grouped.setdefault(ns, []).append((int(row[0]), str(row[1]), ns))

    delete_rows: list[tuple[int, str, str]] = []
    for group_rows in grouped.values():
        delete_rows.extend(group_rows[keep_last_n:])

    if not delete_rows:
        return CheckpointPruneResult(
            thread_id=thread_id,
            keep_last_n=keep_last_n,
            checkpoints_before=len(rows),
            checkpoints_deleted=0,
            writes_deleted=0,
            namespaces_seen=namespaces_seen,
        )

    checkpoint_delete_params = [(rowid,) for rowid, _cp_id, _ns in delete_rows]
    checkpoints_deleted = _sqlite_delete_many(
        conn,
        "DELETE FROM checkpoints WHERE rowid = ?",
        checkpoint_delete_params,
    )

    writes_deleted = 0
    writes_cols = _table_columns(conn, "writes")
    if {"thread_id", "checkpoint_id"}.issubset(writes_cols):
        if "checkpoint_ns" in writes_cols:
            writes_deleted = _sqlite_delete_many(
                conn,
                (
                    "DELETE FROM writes "
                    "WHERE thread_id = ? AND checkpoint_ns = ? "
                    "AND checkpoint_id = ?"
                ),
                [(thread_id, ns, cp_id) for _rowid, cp_id, ns in delete_rows],
            )
        else:
            writes_deleted = _sqlite_delete_many(
                conn,
                "DELETE FROM writes WHERE thread_id = ? AND checkpoint_id = ?",
                [(thread_id, cp_id) for _rowid, cp_id, _ns in delete_rows],
            )

    conn.commit()
    return CheckpointPruneResult(
        thread_id=thread_id,
        keep_last_n=keep_last_n,
        checkpoints_before=len(rows),
        checkpoints_deleted=checkpoints_deleted,
        writes_deleted=writes_deleted,
        namespaces_seen=namespaces_seen,
    )


def apply_configured_checkpoint_retention(
    checkpointer: SqliteSaver,
    thread_id: str,
) -> CheckpointPruneResult | None:
    """Apply env-configured direct retention to an active SqliteSaver."""
    keep_last_n = configured_checkpoint_retention_keep_last()
    if keep_last_n is None:
        return None
    return prune_checkpoint_history(
        checkpointer.conn,
        thread_id,
        keep_last_n=keep_last_n,
    )


@dataclass
class CheckpointRetentionPolicy:
    """Custom checkpoint retention policy.

    Keeps the last ``keep_last_n`` checkpoints per thread, plus any
    checkpoints whose metadata contains a ``"named"`` key (these are
    user-pinned or milestone checkpoints that should never be pruned).

    Parameters
    ----------
    keep_last_n : int
        Number of most-recent checkpoints to keep per thread.
    named_checkpoints : set[str]
        Set of checkpoint IDs that should never be pruned.
    """

    keep_last_n: int = 20
    named_checkpoints: set[str] = field(default_factory=set)

    def mark_named(self, checkpoint_id: str) -> None:
        """Mark a checkpoint as named (protected from pruning).

        Parameters
        ----------
        checkpoint_id : str
            The checkpoint ID to protect.
        """
        self.named_checkpoints.add(checkpoint_id)

    def unmark_named(self, checkpoint_id: str) -> None:
        """Remove named protection from a checkpoint.

        Parameters
        ----------
        checkpoint_id : str
            The checkpoint ID to unprotect.
        """
        self.named_checkpoints.discard(checkpoint_id)

    def apply(
        self,
        checkpointer: SqliteSaver,
        thread_id: str,
    ) -> int:
        """Apply retention policy to a thread's checkpoints.

        Deletes checkpoints that exceed ``keep_last_n`` and are not named.
        Named checkpoints (by ID or by metadata ``"named"`` key) are always
        retained.

        Parameters
        ----------
        checkpointer : SqliteSaver
            An active SqliteSaver instance.
        thread_id : str
            The thread to prune.

        Returns
        -------
        int
            Number of checkpoints deleted.
        """
        config = {"configurable": {"thread_id": thread_id}}
        all_checkpoints = list(checkpointer.list(config))

        if len(all_checkpoints) <= self.keep_last_n:
            return 0

        # Checkpoints are returned most-recent-first
        to_consider = all_checkpoints[self.keep_last_n :]
        deleted = 0

        for cp in to_consider:
            cp_id = cp.config["configurable"].get("checkpoint_id")

            # Never delete named checkpoints
            if cp_id in self.named_checkpoints:
                continue

            # Never delete checkpoints with "named" in metadata
            if cp.metadata and cp.metadata.get("named"):
                continue

            # Delete this checkpoint from the database directly
            try:
                checkpointer.conn.execute(
                    "DELETE FROM checkpoints WHERE thread_id = ? AND checkpoint_id = ?",
                    (thread_id, cp_id),
                )
                checkpointer.conn.execute(
                    "DELETE FROM writes WHERE thread_id = ? AND checkpoint_id = ?",
                    (thread_id, cp_id),
                )
                deleted += 1
            except Exception:
                # Non-critical: if we can't delete, skip
                continue

        if deleted > 0:
            checkpointer.conn.commit()

        return deleted
