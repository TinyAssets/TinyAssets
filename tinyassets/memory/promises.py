"""Cross-book promise tracking at series level.

Narrative promises that span multiple books are promoted to series-level
state and tracked separately from per-chapter episodic memory.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from tinyassets.okf.records import SourceRecords
from tinyassets.okf.sql_index import CanonicalConnection, canonical_methods

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series_promises (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id    TEXT    NOT NULL,
    promise_id     TEXT    NOT NULL,
    description    TEXT    NOT NULL,
    created_book   INTEGER NOT NULL,
    created_chapter INTEGER NOT NULL,
    resolved_book  INTEGER,
    resolved_chapter INTEGER,
    status         TEXT    NOT NULL DEFAULT 'open',
    priority       TEXT    NOT NULL DEFAULT 'normal',
    evidence_json  TEXT    NOT NULL DEFAULT '[]',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at    TEXT,
    UNIQUE(universe_id, promise_id)
);
"""


@dataclass(frozen=True, slots=True)
class SeriesPromise:
    """A narrative promise tracked at series level."""

    promise_id: str
    description: str
    created_book: int
    created_chapter: int
    resolved_book: int | None
    resolved_chapter: int | None
    status: str
    priority: str
    evidence: list[dict[str, Any]]


@canonical_methods
class SeriesPromiseTracker:
    """Canonical owner-box cross-book promise tracking.

    Parameters
    ----------
    source : SourceRecords
        Explicitly admitted, reviewed canonical owner-box records.
    universe_id : str
        Universe namespace.
    """

    def __init__(self, source: SourceRecords, universe_id: str = "default") -> None:
        self._universe_id = universe_id
        self._conn = CanonicalConnection(source, "promises", lambda db: db.executescript(_SCHEMA))

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Create / update promises
    # ------------------------------------------------------------------

    def create_promise(
        self,
        promise_id: str,
        description: str,
        book: int,
        chapter: int,
        priority: str = "normal",
    ) -> None:
        """Register a new series-level promise."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO series_promises
                (universe_id, promise_id, description,
                 created_book, created_chapter, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (self._universe_id, promise_id, description,
             book, chapter, priority),
        )
        self._conn.commit()

    def add_evidence(
        self,
        promise_id: str,
        evidence: dict[str, Any],
    ) -> None:
        """Append evidence to an existing promise."""
        row = self._conn.execute(
            "SELECT evidence_json FROM series_promises "
            "WHERE universe_id = ? AND promise_id = ?",
            (self._universe_id, promise_id),
        ).fetchone()
        if row is None:
            return

        existing = json.loads(row[0])
        existing.append(evidence)
        self._conn.execute(
            "UPDATE series_promises SET evidence_json = ? "
            "WHERE universe_id = ? AND promise_id = ?",
            (json.dumps(existing), self._universe_id, promise_id),
        )
        self._conn.commit()

    def resolve_promise(
        self,
        promise_id: str,
        book: int,
        chapter: int,
    ) -> None:
        """Mark a promise as resolved."""
        self._conn.execute(
            "UPDATE series_promises SET status = 'resolved', "
            "resolved_book = ?, resolved_chapter = ?, "
            "resolved_at = datetime('now') "
            "WHERE universe_id = ? AND promise_id = ?",
            (book, chapter, self._universe_id, promise_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_open_promises(
        self,
        book: int | None = None,
    ) -> list[SeriesPromise]:
        """Return all open promises, optionally filtered by book."""
        if book is not None:
            rows = self._conn.execute(
                "SELECT promise_id, description, created_book, "
                "created_chapter, resolved_book, resolved_chapter, "
                "status, priority, evidence_json "
                "FROM series_promises "
                "WHERE universe_id = ? AND status = 'open' "
                "AND created_book <= ? "
                "ORDER BY created_book, created_chapter",
                (self._universe_id, book),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT promise_id, description, created_book, "
                "created_chapter, resolved_book, resolved_chapter, "
                "status, priority, evidence_json "
                "FROM series_promises "
                "WHERE universe_id = ? AND status = 'open' "
                "ORDER BY created_book, created_chapter",
                (self._universe_id,),
            ).fetchall()
        return [self._row_to_promise(r) for r in rows]

    def get_overdue_promises(
        self,
        current_book: int,
        current_chapter: int,
        max_age_chapters: int = 20,
    ) -> list[SeriesPromise]:
        """Return promises that have been open for too long."""
        all_open = self.get_open_promises()
        overdue = []
        for p in all_open:
            age = (
                (current_book - p.created_book) * 30
                + (current_chapter - p.created_chapter)
            )
            if age > max_age_chapters:
                overdue.append(p)
        return overdue

    def get_all_promises(self) -> list[SeriesPromise]:
        """Return all promises (open and resolved)."""
        rows = self._conn.execute(
            "SELECT promise_id, description, created_book, "
            "created_chapter, resolved_book, resolved_chapter, "
            "status, priority, evidence_json "
            "FROM series_promises "
            "WHERE universe_id = ? "
            "ORDER BY created_book, created_chapter",
            (self._universe_id,),
        ).fetchall()
        return [self._row_to_promise(r) for r in rows]

    # ------------------------------------------------------------------
    # Promotion from per-book to series level
    # ------------------------------------------------------------------

    def promote_from_book(
        self,
        promises: list[dict[str, Any]],
        book: int,
    ) -> int:
        """Promote per-book promises to series level.

        Returns the number of newly created series promises.
        """
        created = 0
        for p in promises:
            pid = p.get("id", p.get("promise_id", ""))
            if not pid:
                continue

            # Check if already exists at series level.
            existing = self._conn.execute(
                "SELECT 1 FROM series_promises "
                "WHERE universe_id = ? AND promise_id = ?",
                (self._universe_id, pid),
            ).fetchone()

            if existing is None:
                self.create_promise(
                    promise_id=pid,
                    description=p.get("description", str(p)),
                    book=book,
                    chapter=p.get("chapter", 1),
                    priority=p.get("priority", "normal"),
                )
                created += 1

        return created

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_promise(row: tuple) -> SeriesPromise:
        return SeriesPromise(
            promise_id=row[0],
            description=row[1],
            created_book=row[2],
            created_chapter=row[3],
            resolved_book=row[4],
            resolved_chapter=row[5],
            status=row[6],
            priority=row[7],
            evidence=json.loads(row[8]),
        )
