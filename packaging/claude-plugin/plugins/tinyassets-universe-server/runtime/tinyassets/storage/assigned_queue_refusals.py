"""Durable, bounded visibility for assigned-consumer claim refusals."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assigned_queue_refusals (
    branch_task_id TEXT PRIMARY KEY,
    universe_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    consumer_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assigned_queue_refusals_universe_observed
ON assigned_queue_refusals(universe_id, observed_at);
"""


class AssignedQueueRefusalStore:
    """Write only after a declined claim; reads never initialize schema."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)

    def record(
        self,
        *,
        branch_task_id: str,
        universe_id: str,
        reason: str,
        observed_at: str,
        consumer_id: str,
    ) -> None:
        with sqlite3.connect(db_path(self.base_path), factory=ClosingConnection) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                INSERT INTO assigned_queue_refusals (
                    branch_task_id, universe_id, reason, observed_at, consumer_id
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(branch_task_id) DO UPDATE SET
                    universe_id = excluded.universe_id,
                    reason = excluded.reason,
                    observed_at = excluded.observed_at,
                    consumer_id = excluded.consumer_id
                """,
                (
                    branch_task_id,
                    universe_id,
                    reason,
                    observed_at,
                    consumer_id,
                ),
            )

    def fresh_reasons(
        self,
        *,
        universe_id: str,
        max_age_seconds: float,
        now: datetime | None = None,
    ) -> dict[str, str]:
        database = db_path(self.base_path)
        if not database.is_file():
            return {}
        observed_at = now or datetime.now(timezone.utc)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        cutoff = observed_at.astimezone(timezone.utc) - timedelta(
            seconds=max(0.0, max_age_seconds)
        )
        uri = f"{database.resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, factory=ClosingConnection) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                rows = conn.execute(
                    """
                    SELECT branch_task_id, reason, observed_at
                    FROM assigned_queue_refusals
                    WHERE universe_id = ? AND observed_at >= ?
                    ORDER BY observed_at DESC, branch_task_id ASC
                    LIMIT 1000
                    """,
                    (universe_id, cutoff.isoformat()),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return {}
            raise
        fresh: dict[str, str] = {}
        for row in rows:
            try:
                timestamp = datetime.fromisoformat(
                    str(row["observed_at"]).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp.astimezone(timezone.utc) >= cutoff:
                fresh.setdefault(str(row["branch_task_id"]), str(row["reason"]))
        return fresh


__all__ = ["AssignedQueueRefusalStore"]
