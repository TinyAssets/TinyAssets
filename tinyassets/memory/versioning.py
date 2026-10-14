"""Output versioning -- save all drafts, allow rollback.

Every scene draft is versioned. The system keeps all drafts in canonical files
so that:
  - Any draft can be retrieved for comparison.
  - Rollback to a previous version is a pointer update.
  - Quality metrics per version enable trend analysis.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from tinyassets.okf.records import SourceRecords
from tinyassets.okf.sql_index import CanonicalConnection, canonical_methods

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS draft_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id   TEXT    NOT NULL,
    book_number   INTEGER NOT NULL,
    chapter_number INTEGER NOT NULL,
    scene_number  INTEGER NOT NULL,
    version       INTEGER NOT NULL,
    prose         TEXT    NOT NULL,
    word_count    INTEGER NOT NULL,
    verdict       TEXT    NOT NULL DEFAULT '',
    quality_score REAL    NOT NULL DEFAULT 0.0,
    metadata_json TEXT    NOT NULL DEFAULT '{}',
    is_current    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(universe_id, book_number, chapter_number, scene_number, version)
);

CREATE INDEX IF NOT EXISTS idx_draft_current
    ON draft_versions(universe_id, book_number, chapter_number,
                      scene_number, is_current);
"""


@dataclass(frozen=True, slots=True)
class DraftVersion:
    """A single versioned draft."""

    version: int
    prose: str
    word_count: int
    verdict: str
    quality_score: float
    is_current: bool
    created_at: str
    metadata: dict[str, Any]


@canonical_methods
class OutputVersionStore:
    """Canonical owner-box draft version store.

    Parameters
    ----------
    source : SourceRecords
        Explicitly admitted, reviewed canonical owner-box records.
    universe_id : str
        Universe namespace.
    """

    def __init__(self, source: SourceRecords, universe_id: str = "default") -> None:
        self._universe_id = universe_id
        self._conn = CanonicalConnection(source, "versions", lambda db: db.executescript(_SCHEMA))

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Save and retrieve
    # ------------------------------------------------------------------

    def save_draft(
        self,
        book: int,
        chapter: int,
        scene: int,
        prose: str,
        verdict: str = "",
        quality_score: float = 0.0,
        metadata: dict[str, Any] | None = None,
        *,
        _max_retries: int = 3,
    ) -> int:
        """Save a new draft version.  Returns the version number.

        Retries on ``OperationalError`` (database locked) up to
        *_max_retries* times with exponential backoff.
        """
        import time as _time

        last_exc: Exception | None = None
        for attempt in range(_max_retries + 1):
            try:
                return self._save_draft_inner(
                    book, chapter, scene, prose,
                    verdict, quality_score, metadata,
                )
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() or attempt >= _max_retries:
                    raise
                wait = 0.5 * (2 ** attempt)
                logger.warning(
                    "save_draft locked (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, _max_retries, wait,
                )
                _time.sleep(wait)
        raise last_exc  # type: ignore[misc]  # unreachable but satisfies mypy

    def _save_draft_inner(
        self,
        book: int,
        chapter: int,
        scene: int,
        prose: str,
        verdict: str,
        quality_score: float,
        metadata: dict[str, Any] | None,
    ) -> int:
        """Core save logic, wrapped by retry in save_draft."""
        # Find next version number.
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM draft_versions "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ?",
            (self._universe_id, book, chapter, scene),
        ).fetchone()
        next_version = row[0] + 1

        # Clear current flag on previous versions.
        self._conn.execute(
            "UPDATE draft_versions SET is_current = 0 "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ?",
            (self._universe_id, book, chapter, scene),
        )

        # Insert new version as current.
        self._conn.execute(
            """
            INSERT INTO draft_versions
                (universe_id, book_number, chapter_number, scene_number,
                 version, prose, word_count, verdict, quality_score,
                 metadata_json, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                self._universe_id, book, chapter, scene,
                next_version, prose, len(prose.split()),
                verdict, quality_score,
                json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()

        logger.debug(
            "Saved draft v%d for b%d/c%d/s%d (%d words)",
            next_version, book, chapter, scene, len(prose.split()),
        )
        return next_version

    def get_current(
        self, book: int, chapter: int, scene: int
    ) -> DraftVersion | None:
        """Return the current (active) version, or None."""
        row = self._conn.execute(
            "SELECT version, prose, word_count, verdict, quality_score, "
            "is_current, created_at, metadata_json "
            "FROM draft_versions "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ? "
            "AND is_current = 1",
            (self._universe_id, book, chapter, scene),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    def get_version(
        self, book: int, chapter: int, scene: int, version: int
    ) -> DraftVersion | None:
        """Return a specific version."""
        row = self._conn.execute(
            "SELECT version, prose, word_count, verdict, quality_score, "
            "is_current, created_at, metadata_json "
            "FROM draft_versions "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ? "
            "AND version = ?",
            (self._universe_id, book, chapter, scene, version),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_version(row)

    def get_all_versions(
        self, book: int, chapter: int, scene: int
    ) -> list[DraftVersion]:
        """Return all versions for a scene, newest first."""
        rows = self._conn.execute(
            "SELECT version, prose, word_count, verdict, quality_score, "
            "is_current, created_at, metadata_json "
            "FROM draft_versions "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ? "
            "ORDER BY version DESC",
            (self._universe_id, book, chapter, scene),
        ).fetchall()
        return [self._row_to_version(r) for r in rows]

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(
        self, book: int, chapter: int, scene: int, to_version: int
    ) -> DraftVersion | None:
        """Set *to_version* as the current version.

        Returns the rolled-back-to version, or None if not found.
        """
        target = self.get_version(book, chapter, scene, to_version)
        if target is None:
            return None

        # Clear all current flags.
        self._conn.execute(
            "UPDATE draft_versions SET is_current = 0 "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ?",
            (self._universe_id, book, chapter, scene),
        )

        # Set target as current.
        self._conn.execute(
            "UPDATE draft_versions SET is_current = 1 "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ? "
            "AND version = ?",
            (self._universe_id, book, chapter, scene, to_version),
        )
        self._conn.commit()

        logger.info(
            "Rolled back b%d/c%d/s%d to version %d",
            book, chapter, scene, to_version,
        )
        return self.get_version(book, chapter, scene, to_version)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def version_count(
        self, book: int, chapter: int, scene: int
    ) -> int:
        """Return the number of versions for a scene."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM draft_versions "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number = ? AND scene_number = ?",
            (self._universe_id, book, chapter, scene),
        ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_version(row: tuple) -> DraftVersion:
        return DraftVersion(
            version=row[0],
            prose=row[1],
            word_count=row[2],
            verdict=row[3],
            quality_score=row[4],
            is_current=bool(row[5]),
            created_at=row[6],
            metadata=json.loads(row[7]),
        )
