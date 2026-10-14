"""Dark single-active automation activation persistence.

This store owns only the server-authoritative activation state machine. It
does not enqueue work, resolve providers, execute tenant code, or grant an
external effect. Runtime integration remains dark until epoch-1 admission is
fenced and every independent authority owner is ready.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator

from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.storage import db_path


class AutomationActivationExecutor(str, Enum):
    TRAY = "tray"
    CLOUD = "cloud"


class AutomationActivationState(str, Enum):
    STOPPED = "stopped"
    ACTIVE = "active"


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _epoch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("epoch must be an integer >= 0")
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _require_active_transaction(conn: sqlite3.Connection) -> None:
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise ValueError("connection must hold an active transaction")


def _require_branch_subject(subject: ExecutionSubject) -> None:
    if subject.kind is not ExecutionSubjectKind.BRANCH_VERSION:
        raise ValueError("an activation subject is a branch version")


@dataclass(frozen=True, slots=True)
class AutomationActivation:
    universe_id: str
    automation_id: str
    epoch: int
    executor_class: AutomationActivationExecutor | None
    subject: ExecutionSubject | None
    lease_id: str | None
    state: AutomationActivationState
    updated_at: str

    def __post_init__(self) -> None:
        _required(self.universe_id, "universe_id")
        _required(self.automation_id, "automation_id")
        _epoch(self.epoch)
        if not isinstance(self.state, AutomationActivationState):
            raise ValueError("state must be typed")
        _required(self.updated_at, "updated_at")
        active_fields = (
            self.executor_class,
            self.subject,
            self.lease_id,
        )
        if self.state is AutomationActivationState.STOPPED:
            if any(value is not None for value in active_fields):
                raise ValueError("stopped activation must not carry active identity")
            return
        if not isinstance(
            self.executor_class,
            AutomationActivationExecutor,
        ):
            raise ValueError("executor_class must be typed")
        if not isinstance(self.subject, ExecutionSubject):
            raise ValueError("subject must be typed")
        _required(self.lease_id, "lease_id")

    @property
    def immutable_branch_version(self) -> str | None:
        """Compatibility projection; the typed subject is the sole authority."""

        if (
            self.subject is not None
            and self.subject.kind is ExecutionSubjectKind.BRANCH_VERSION
        ):
            return self.subject.ref
        return None


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS automation_activations (
    universe_id TEXT NOT NULL,
    automation_id TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK (epoch >= 0),
    executor_class TEXT
        CHECK (executor_class IN ('tray', 'cloud')),
    subject_kind TEXT
        CHECK (subject_kind IN ('branch_version')),
    subject_ref TEXT,
    subject_digest TEXT,
    immutable_branch_version TEXT,
    lease_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('stopped', 'active')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (universe_id, automation_id),
    CHECK (
        (
            state = 'stopped'
            AND executor_class IS NULL
            AND subject_kind IS NULL
            AND subject_ref IS NULL
            AND subject_digest IS NULL
            AND immutable_branch_version IS NULL
            AND lease_id IS NULL
        )
        OR
        (
            state = 'active'
            AND executor_class IS NOT NULL
            AND subject_kind IS NOT NULL
            AND subject_ref IS NOT NULL
            AND subject_digest IS NOT NULL
            AND lease_id IS NOT NULL
            AND subject_kind = 'branch_version'
            AND immutable_branch_version = subject_ref
        )
    )
)
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("automation_activations",),
    ).fetchone()
    if row is None:
        conn.execute(_CREATE_TABLE)
        return
    columns = {
        str(column[1]) for column in conn.execute("PRAGMA table_info(automation_activations)")
    }
    if not {"subject_kind", "subject_ref", "subject_digest"} <= columns:
        _migrate_untyped_subjects(conn)
    elif "'agent_runtime_manifest'" in str(row[0]):
        _migrate_subject_constraint(conn)


def _migrate_untyped_subjects(conn: sqlite3.Connection) -> None:
    """Add the typed-subject columns to a table from before they existed."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = {
            str(column[1])
            for column in conn.execute("PRAGMA table_info(automation_activations)")
        }
        if {"subject_kind", "subject_ref", "subject_digest"} <= columns:
            conn.commit()
            return
        active_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM automation_activations WHERE state = 'active'"
            ).fetchone()[0]
        )
        if active_count:
            raise RuntimeError(
                "legacy active automation activation must be stopped before typed-subject migration"
            )
        conn.execute(
            "ALTER TABLE automation_activations RENAME TO automation_activations_legacy"
        )
        conn.execute(_CREATE_TABLE)
        conn.execute(
            """
            INSERT INTO automation_activations (
                universe_id, automation_id, epoch, executor_class,
                subject_kind, subject_ref, subject_digest,
                immutable_branch_version, lease_id, state, updated_at
            )
            SELECT
                universe_id, automation_id, epoch, NULL,
                NULL, NULL, NULL, NULL, NULL, state, updated_at
            FROM automation_activations_legacy
            """
        )
        conn.execute("DROP TABLE automation_activations_legacy")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_subject_constraint(conn: sqlite3.Connection) -> None:
    """Rebuild the table without the retired agent subject (C8).

    SQLite cannot alter a CHECK in place. An active row still naming the
    retired kind is a repair case, never silently dropped; the rebuild keeps
    every row and index and runs in one transaction.
    """
    if conn.in_transaction:
        raise RuntimeError("activation schema migration needs an idle connection")
    stale = conn.execute(
        "SELECT COUNT(*) FROM automation_activations WHERE subject_kind IS NOT NULL "
        "AND subject_kind != 'branch_version'"
    ).fetchone()[0]
    if stale:
        raise RuntimeError("agent-manifest activations remain; stop them before migrating")
    indexes = [str(index[0]) for index in conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name = 'automation_activations' "
        "AND type = 'index' AND sql IS NOT NULL"
    )]
    columns = ", ".join(
        f'"{column[1]}"' for column in conn.execute("PRAGMA table_info(automation_activations)")
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_CREATE_TABLE.replace(
            "CREATE TABLE IF NOT EXISTS automation_activations (",
            "CREATE TABLE automation_activations_new (", 1))
        conn.execute(
            f"INSERT INTO automation_activations_new ({columns}) "
            f"SELECT {columns} FROM automation_activations"
        )
        conn.execute("DROP TABLE automation_activations")
        conn.execute("ALTER TABLE automation_activations_new RENAME TO automation_activations")
        for sql in indexes:
            conn.execute(sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


class AutomationActivationStore:
    """SQLite CAS owner for one activation per universe/automation pair."""

    def __init__(
        self,
        base_path: str | Path,
        *,
        busy_timeout_ms: int = 30_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_path = Path(base_path)
        self._busy_timeout_ms = int(busy_timeout_ms)
        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )
        if self._busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        path = db_path(self.base_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            for attempt in range(10):
                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 9:
                        raise
                    time.sleep(0.01 * (attempt + 1))
            _ensure_schema(conn)
            yield conn
        finally:
            conn.close()

    def create_stopped(
        self,
        *,
        universe_id: str,
        automation_id: str,
    ) -> AutomationActivation:
        """Create epoch zero, or return the existing record without mutation."""

        clean_automation_id = _required(automation_id, "automation_id")
        return self._create_stopped(
            universe_id=_required(universe_id, "universe_id"),
            automation_id=clean_automation_id,
        )

    def _create_stopped(
        self,
        *,
        universe_id: str,
        automation_id: str,
    ) -> AutomationActivation:
        at = _timestamp(self._clock())
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._create_stopped_in_transaction(
                    conn,
                    universe_id=universe_id,
                    automation_id=automation_id,
                    updated_at=at,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return row

    def _create_stopped_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        universe_id: str,
        automation_id: str,
        updated_at: str,
    ) -> AutomationActivation:
        _require_active_transaction(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO automation_activations (
                universe_id, automation_id, epoch, executor_class,
                subject_kind, subject_ref, subject_digest,
                immutable_branch_version, lease_id, state, updated_at
            ) VALUES (?, ?, 0, NULL, NULL, NULL, NULL, NULL, NULL, 'stopped', ?)
            """,
            (universe_id, automation_id, updated_at),
        )
        row = self._select(conn, universe_id, automation_id)
        if row is None:
            raise RuntimeError("automation activation insert disappeared")
        return _record(row)

    def get(
        self,
        universe_id: str,
        automation_id: str,
    ) -> AutomationActivation | None:
        clean_universe_id = _required(universe_id, "universe_id")
        clean_automation_id = _required(automation_id, "automation_id")
        with self.connection() as conn:
            row = self._select(
                conn,
                clean_universe_id,
                clean_automation_id,
            )
        return _record(row) if row is not None else None

    def get_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        universe_id: str,
        automation_id: str,
    ) -> AutomationActivation | None:
        """Read one activation while the caller retains the authority fence."""

        _require_active_transaction(conn)
        row = self._select(
            conn,
            _required(universe_id, "universe_id"),
            _required(automation_id, "automation_id"),
        )
        return _record(row) if row is not None else None

    def activate(
        self,
        *,
        expected: AutomationActivation,
        executor_class: AutomationActivationExecutor,
        subject: ExecutionSubject,
        lease_id: str,
        authority_check: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> AutomationActivation | None:
        """Activate only from an exact stopped record."""

        if not isinstance(expected, AutomationActivation):
            raise ValueError("expected must be an AutomationActivation")
        if expected.state is not AutomationActivationState.STOPPED:
            return None
        if not isinstance(executor_class, AutomationActivationExecutor):
            raise ValueError("executor_class must be typed")
        if not isinstance(subject, ExecutionSubject):
            raise ValueError("subject must be typed")
        _require_branch_subject(subject)
        return self._transition(
            expected=expected,
            executor_class=executor_class,
            subject=subject,
            lease_id=_required(lease_id, "lease_id"),
            state=AutomationActivationState.ACTIVE,
            authority_check=authority_check,
        )

    def activate_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        expected: AutomationActivation,
        executor_class: AutomationActivationExecutor,
        subject: ExecutionSubject,
        lease_id: str,
        authority_check: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> AutomationActivation | None:
        """Activate an exact stopped row inside the caller's transaction."""

        _require_active_transaction(conn)
        if not isinstance(expected, AutomationActivation):
            raise ValueError("expected must be an AutomationActivation")
        if expected.state is not AutomationActivationState.STOPPED:
            return None
        if not isinstance(executor_class, AutomationActivationExecutor):
            raise ValueError("executor_class must be typed")
        if not isinstance(subject, ExecutionSubject):
            raise ValueError("subject must be typed")
        _require_branch_subject(subject)
        return self._transition_in_transaction(
            conn,
            expected=expected,
            executor_class=executor_class,
            subject=subject,
            lease_id=_required(lease_id, "lease_id"),
            state=AutomationActivationState.ACTIVE,
            authority_check=authority_check,
        )

    def stop(
        self,
        *,
        expected: AutomationActivation,
    ) -> AutomationActivation | None:
        """Fence an exact active epoch and clear its active identity."""

        if not isinstance(expected, AutomationActivation):
            raise ValueError("expected must be an AutomationActivation")
        if expected.state is not AutomationActivationState.ACTIVE:
            return None
        return self._transition(
            expected=expected,
            executor_class=None,
            subject=None,
            lease_id=None,
            state=AutomationActivationState.STOPPED,
        )

    def rebind(
        self,
        *,
        expected: AutomationActivation,
        subject: ExecutionSubject,
        lease_id: str,
    ) -> AutomationActivation | None:
        """Advance an active epoch while retaining its executor class."""

        if not isinstance(expected, AutomationActivation):
            raise ValueError("expected must be an AutomationActivation")
        if expected.state is not AutomationActivationState.ACTIVE:
            return None
        if not isinstance(subject, ExecutionSubject):
            raise ValueError("subject must be typed")
        _require_branch_subject(subject)
        return self._transition(
            expected=expected,
            executor_class=expected.executor_class,
            subject=subject,
            lease_id=_required(lease_id, "lease_id"),
            state=AutomationActivationState.ACTIVE,
        )

    def validate_claim(
        self,
        *,
        universe_id: str,
        automation_id: str,
        epoch: int,
        executor_class: AutomationActivationExecutor | None,
        subject: ExecutionSubject | None,
        lease_id: str | None,
    ) -> bool:
        """Fail closed unless every current active identity component matches."""

        try:
            clean_universe_id = _required(universe_id, "universe_id")
            clean_automation_id = _required(
                automation_id,
                "automation_id",
            )
            clean_epoch = _epoch(epoch)
            if not isinstance(
                executor_class,
                AutomationActivationExecutor,
            ):
                return False
            if not isinstance(subject, ExecutionSubject):
                return False
            _require_branch_subject(subject)
            clean_lease_id = _required(lease_id, "lease_id")
        except ValueError:
            return False
        with self.connection() as conn:
            return self.validate_claim_in_transaction(
                conn,
                universe_id=clean_universe_id,
                automation_id=clean_automation_id,
                epoch=clean_epoch,
                executor_class=executor_class,
                subject=subject,
                lease_id=clean_lease_id,
            )

    @staticmethod
    def validate_claim_in_transaction(
        conn: sqlite3.Connection,
        *,
        universe_id: str,
        automation_id: str,
        epoch: int,
        executor_class: AutomationActivationExecutor | None,
        subject: ExecutionSubject | None,
        lease_id: str | None,
    ) -> bool:
        """Validate an activation on the caller's existing transaction."""

        try:
            clean_universe_id = _required(universe_id, "universe_id")
            clean_automation_id = _required(
                automation_id,
                "automation_id",
            )
            clean_epoch = _epoch(epoch)
            if not isinstance(
                executor_class,
                AutomationActivationExecutor,
            ):
                return False
            if not isinstance(subject, ExecutionSubject):
                return False
            _require_branch_subject(subject)
            clean_lease_id = _required(lease_id, "lease_id")
        except ValueError:
            return False
        try:
            row = conn.execute(
                """
                SELECT 1
                FROM automation_activations
                WHERE universe_id = ?
                  AND automation_id = ?
                  AND epoch = ?
                  AND executor_class = ?
                  AND subject_kind = ?
                  AND subject_ref = ?
                  AND subject_digest = ?
                  AND lease_id = ?
                  AND state = 'active'
                LIMIT 1
                """,
                (
                    clean_universe_id,
                    clean_automation_id,
                    clean_epoch,
                    executor_class.value,
                    subject.kind.value,
                    subject.ref,
                    subject.digest,
                    clean_lease_id,
                ),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None

    def _transition(
        self,
        *,
        expected: AutomationActivation,
        executor_class: AutomationActivationExecutor | None,
        subject: ExecutionSubject | None,
        lease_id: str | None,
        state: AutomationActivationState,
        authority_check: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> AutomationActivation | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = self._transition_in_transaction(
                    conn,
                    expected=expected,
                    executor_class=executor_class,
                    subject=subject,
                    lease_id=lease_id,
                    state=state,
                    authority_check=authority_check,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return result

    def _transition_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        expected: AutomationActivation,
        executor_class: AutomationActivationExecutor | None,
        subject: ExecutionSubject | None,
        lease_id: str | None,
        state: AutomationActivationState,
        authority_check: Callable[[sqlite3.Connection], bool] | None = None,
    ) -> AutomationActivation | None:
        _require_active_transaction(conn)
        at = _timestamp(self._clock())
        compatibility_branch_version = (
            subject.ref
            if subject is not None
            and subject.kind is ExecutionSubjectKind.BRANCH_VERSION
            else None
        )
        if authority_check is not None and authority_check(conn) is not True:
            return None
        if state is AutomationActivationState.ACTIVE:
            controls_table = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'cloud_automation_controls'
                """
            ).fetchone()
            if controls_table is not None:
                managed = conn.execute(
                    """
                    SELECT desired_state
                    FROM cloud_automation_controls
                    WHERE universe_id = ? AND automation_id = ?
                    """,
                    (expected.universe_id, expected.automation_id),
                ).fetchone()
                if managed is not None and managed["desired_state"] != "active":
                    return None
        cursor = conn.execute(
            """
            UPDATE automation_activations
            SET epoch = epoch + 1,
                executor_class = ?,
                subject_kind = ?,
                subject_ref = ?,
                subject_digest = ?,
                immutable_branch_version = ?,
                lease_id = ?,
                state = ?,
                updated_at = ?
            WHERE universe_id = ?
              AND automation_id = ?
              AND epoch = ?
              AND executor_class IS ?
              AND subject_kind IS ?
              AND subject_ref IS ?
              AND subject_digest IS ?
              AND immutable_branch_version IS ?
              AND lease_id IS ?
              AND state = ?
              AND updated_at = ?
            """,
            (
                executor_class.value if executor_class is not None else None,
                subject.kind.value if subject is not None else None,
                subject.ref if subject is not None else None,
                subject.digest if subject is not None else None,
                compatibility_branch_version,
                lease_id,
                state.value,
                at,
                expected.universe_id,
                expected.automation_id,
                expected.epoch,
                (
                    expected.executor_class.value
                    if expected.executor_class is not None
                    else None
                ),
                expected.subject.kind.value if expected.subject is not None else None,
                expected.subject.ref if expected.subject is not None else None,
                expected.subject.digest if expected.subject is not None else None,
                expected.immutable_branch_version,
                expected.lease_id,
                expected.state.value,
                expected.updated_at,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = self._select(conn, expected.universe_id, expected.automation_id)
        if row is None:
            raise RuntimeError("automation activation transition disappeared")
        return _record(row)

    @staticmethod
    def _select(
        conn: sqlite3.Connection,
        universe_id: str,
        automation_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT *
            FROM automation_activations
            WHERE universe_id = ? AND automation_id = ?
            """,
            (universe_id, automation_id),
        ).fetchone()


def _record(row: sqlite3.Row) -> AutomationActivation:
    executor = row["executor_class"]
    subject_kind = row["subject_kind"]
    subject = (
        ExecutionSubject(
            kind=ExecutionSubjectKind(str(subject_kind)),
            ref=str(row["subject_ref"]),
            digest=str(row["subject_digest"]),
        )
        if subject_kind is not None
        else None
    )
    return AutomationActivation(
        universe_id=str(row["universe_id"]),
        automation_id=str(row["automation_id"]),
        epoch=int(row["epoch"]),
        executor_class=(
            AutomationActivationExecutor(str(executor))
            if executor is not None
            else None
        ),
        subject=subject,
        lease_id=row["lease_id"],
        state=AutomationActivationState(str(row["state"])),
        updated_at=str(row["updated_at"]),
    )


__all__ = [
    "AutomationActivation",
    "AutomationActivationExecutor",
    "AutomationActivationState",
    "AutomationActivationStore",
    "ExecutionSubject",
    "ExecutionSubjectKind",
]
