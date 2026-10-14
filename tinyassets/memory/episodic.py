"""Episodic memory -- canonical recent scene summaries and facts.

Stores scene-by-scene summaries, recently-extracted facts, and style
observations from the last N chapters.  Supports sliding-window
eviction to keep only hot data here; older entries are assumed to be
accessible via archival memory (KG / vectors).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tinyassets.memory.scoping import MemoryScope

from tinyassets.okf.records import SourceRecords
from tinyassets.okf.sql_index import CanonicalConnection, canonical_methods

logger = logging.getLogger(__name__)

FANTASY_DOMAIN_ID = "fantasy_author"
EPISODIC_NEUTRAL_MIGRATION_FLAG = "TINYASSETS_EPISODIC_SCHEMA_MIGRATION"


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _fantasy_episode_id(
    book_number: int,
    chapter_number: int,
    scene_number: int,
) -> str:
    return (
        f"book:{book_number}/chapter:{chapter_number}/scene:{scene_number}"
    )


def _fantasy_summary_payload(
    book_number: int,
    chapter_number: int,
    scene_number: int,
) -> dict[str, int]:
    return {
        "book_number": book_number,
        "chapter_number": chapter_number,
        "scene_number": scene_number,
    }


def _fantasy_reflection_id(chapter_number: int, scene_number: int) -> str:
    return f"chapter:{chapter_number}/scene:{scene_number}"


def _scope_tiers(
    scope: "MemoryScope | None",
) -> tuple[str | None, str | None, str | None]:
    """Return ``(goal_id, branch_id, user_id)`` from ``scope`` or all-None.

    Memory-scope Stage 2b: episodic tables bind ``universe_id`` at the
    store level, so only the three sub-tiers come from the scope
    argument. ``None`` scope → all-NULL tiers = "legacy, treat as
    universe-public" per design §4.
    """
    if scope is None:
        return (None, None, None)
    return (scope.goal_id, scope.branch_id, scope.user_id)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scene_summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id   TEXT    NOT NULL,
    domain_id     TEXT    NOT NULL DEFAULT '',
    episode_id    TEXT    NOT NULL DEFAULT '',
    sequence_number INTEGER NOT NULL DEFAULT 0,
    domain_payload TEXT   NOT NULL DEFAULT '{}',
    book_number   INTEGER,
    chapter_number INTEGER,
    scene_number  INTEGER,
    summary       TEXT    NOT NULL,
    word_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(universe_id, domain_id, episode_id),
    UNIQUE(universe_id, book_number, chapter_number, scene_number)
);

CREATE TABLE IF NOT EXISTS episodic_facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id   TEXT    NOT NULL,
    fact_id       TEXT    NOT NULL,
    entity        TEXT    NOT NULL,
    content       TEXT    NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    source_scenes TEXT    NOT NULL DEFAULT '[]',
    promoted      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(universe_id, fact_id)
);

CREATE TABLE IF NOT EXISTS style_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id   TEXT    NOT NULL,
    dimension     TEXT    NOT NULL,
    observation   TEXT    NOT NULL,
    scene_ref     TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reflections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id   TEXT    NOT NULL,
    domain_id     TEXT    NOT NULL DEFAULT '',
    episode_id    TEXT    NOT NULL DEFAULT '',
    sequence_number INTEGER NOT NULL DEFAULT 0,
    domain_payload TEXT   NOT NULL DEFAULT '{}',
    chapter_number INTEGER,
    scene_number  INTEGER,
    critique      TEXT    NOT NULL,
    reflection    TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass(frozen=True, slots=True)
class SceneSummary:
    """A scene summary retrieved from episodic memory."""

    book_number: int
    chapter_number: int
    scene_number: int
    summary: str
    word_count: int


@dataclass(frozen=True, slots=True)
class EpisodeSummary:
    """A domain-neutral episodic summary."""

    domain_id: str
    episode_id: str
    sequence_number: int
    domain_payload: dict[str, Any]
    summary: str
    word_count: int


@dataclass(frozen=True, slots=True)
class EpisodicSchemaMigrationResult:
    """Report for the host-gated episodic schema migration."""

    db_path: Path
    dry_run: bool
    schema_changed: bool
    backup_path: Path | None
    row_counts: dict[str, int]
    no_bleed_ok: bool
    no_bleed_violations: list[str]


@canonical_methods
class EpisodicMemory:
    """Canonical owner-box store for recent scene summaries and facts.

    Parameters
    ----------
    source : SourceRecords
        Explicitly admitted, reviewed canonical owner-box records.
    universe_id : str
        Scopes all reads/writes to this universe.
    window_chapters : int
        How many chapters of history to keep in the hot window.
    """

    def __init__(self, source: SourceRecords, universe_id: str = "default",
                 window_chapters: int = 5) -> None:
        self._universe_id = universe_id
        self._window_chapters = window_chapters
        self._conn = CanonicalConnection(source, "episodic", initialize_episodic_index)

    # -----------------------------------------------------------------
    # Memory-scope Stage 2b schema migration
    # -----------------------------------------------------------------

    _SCOPE_COLUMNS: tuple[tuple[str, str], ...] = (
        ("goal_id", "TEXT"),
        ("branch_id", "TEXT"),
        ("user_id", "TEXT"),
    )
    _SCOPED_TABLES: tuple[str, ...] = (
        "scene_summaries", "episodic_facts",
        "style_observations", "reflections",
    )
    _DOMAIN_NEUTRAL_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
        "scene_summaries": (
            ("domain_id", "TEXT"),
            ("episode_id", "TEXT"),
            ("sequence_number", "INTEGER"),
            ("domain_payload", "TEXT"),
        ),
        "reflections": (
            ("domain_id", "TEXT"),
            ("episode_id", "TEXT"),
            ("sequence_number", "INTEGER"),
            ("domain_payload", "TEXT"),
        ),
    }

    def _migrate_scope_columns(self) -> None:
        """Add ``goal_id`` / ``branch_id`` / ``user_id`` columns if missing.

        Non-destructive idempotent ALTER TABLE matching the KG Stage 2a
        pattern. Existing rows (including the ~60K-word sporemarch
        episodic state) keep NULL on the three new columns —
        semantically "legacy, treat as universe-public" per design §4.
        ``universe_id`` is already in the base schema, so it isn't
        re-added here. Shared index over ``(universe_id, goal_id,
        branch_id)`` is the hot read path for Stage 2c enforcement.
        """
        for table in self._SCOPED_TABLES:
            existing = {
                row[1]
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for col_name, col_type in self._SCOPE_COLUMNS:
                if col_name in existing:
                    continue
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )
        for table in self._SCOPED_TABLES:
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_scope "
                f"ON {table}(universe_id, goal_id, branch_id)"
            )
        self._conn.commit()

    def _migrate_domain_neutral_columns(self) -> None:
        """Add neutral episodic columns without rebuilding live tables.

        This is the normal-startup compatibility path. It lets old DBs keep
        serving while the host-gated rebuild migration remains explicit.
        """
        for table, columns in self._DOMAIN_NEUTRAL_COLUMNS.items():
            existing = {
                row[1]
                for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for col_name, col_type in columns:
                if col_name in existing:
                    continue
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scene_summaries_episode "
            "ON scene_summaries(universe_id, domain_id, episode_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_scene_summaries_sequence "
            "ON scene_summaries(universe_id, domain_id, sequence_number)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reflections_sequence "
            "ON reflections(universe_id, domain_id, sequence_number)"
        )
        self._conn.commit()

    def observation_dimensions(self) -> list[str]:
        """Read dimensions from the current canonical revision."""
        return [row[0] for row in self._conn.execute(
            "SELECT DISTINCT dimension FROM style_observations WHERE universe_id = ?",
            (self._universe_id,),
        )]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _scope_tiers(
        scope: "MemoryScope | None",
    ) -> tuple[str | None, str | None, str | None]:
        """Resolve the optional scope into the three episodic sub-tiers."""
        return _scope_tiers(scope)

    # ------------------------------------------------------------------
    # Scene summaries
    # ------------------------------------------------------------------

    def store_summary(
        self,
        book_number: int,
        chapter_number: int,
        scene_number: int,
        summary: str,
        word_count: int = 0,
        scope: "MemoryScope | None" = None,
    ) -> None:
        """Persist a scene summary (upsert).

        Memory-scope Stage 2b: ``scope`` tags the row with
        ``goal_id`` / ``branch_id`` / ``user_id`` when supplied.
        ``universe_id`` still comes from the store's own binding (the
        hard-invariant tier). Scope tagging is advisory until 2c.
        """
        goal_id, branch_id, user_id = self._scope_tiers(scope)
        domain_payload = _fantasy_summary_payload(
            book_number, chapter_number, scene_number
        )
        self._conn.execute(
            """
            INSERT INTO scene_summaries
                (universe_id, domain_id, episode_id, sequence_number,
                 domain_payload, book_number, chapter_number, scene_number,
                 summary, word_count, goal_id, branch_id, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(universe_id, book_number, chapter_number, scene_number)
            DO UPDATE SET summary=excluded.summary,
                          word_count=excluded.word_count,
                          domain_id=excluded.domain_id,
                          episode_id=excluded.episode_id,
                          sequence_number=excluded.sequence_number,
                          domain_payload=excluded.domain_payload,
                          goal_id=COALESCE(excluded.goal_id, scene_summaries.goal_id),
                          branch_id=COALESCE(excluded.branch_id, scene_summaries.branch_id),
                          user_id=COALESCE(excluded.user_id, scene_summaries.user_id)
            """,
            (
                self._universe_id,
                FANTASY_DOMAIN_ID,
                _fantasy_episode_id(book_number, chapter_number, scene_number),
                chapter_number,
                _json_dumps(domain_payload),
                book_number,
                chapter_number,
                scene_number,
                summary,
                word_count,
                goal_id,
                branch_id,
                user_id,
            ),
        )
        self._conn.commit()

    def store_episode_summary(
        self,
        episode_id: str,
        sequence_number: int,
        summary: str,
        word_count: int = 0,
        *,
        domain_id: str = "workflow",
        domain_payload: dict[str, Any] | None = None,
        scope: "MemoryScope | None" = None,
    ) -> None:
        """Persist a domain-neutral episode summary.

        Domain-specific coordinates stay in ``domain_payload`` and the
        optional domain registry. Shared substrate columns never require
        book/chapter/scene for non-fantasy domains.
        """
        goal_id, branch_id, user_id = self._scope_tiers(scope)
        payload = dict(domain_payload or {})
        book_number = payload.get("book_number")
        chapter_number = payload.get("chapter_number")
        scene_number = payload.get("scene_number")
        if domain_id != FANTASY_DOMAIN_ID:
            book_number = chapter_number = scene_number = None

        existing = self._conn.execute(
            "SELECT id FROM scene_summaries "
            "WHERE universe_id = ? AND domain_id = ? AND episode_id = ?",
            (self._universe_id, domain_id, episode_id),
        ).fetchone()
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO scene_summaries
                    (universe_id, domain_id, episode_id, sequence_number,
                     domain_payload, book_number, chapter_number, scene_number,
                     summary, word_count, goal_id, branch_id, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._universe_id,
                    domain_id,
                    episode_id,
                    sequence_number,
                    _json_dumps(payload),
                    book_number,
                    chapter_number,
                    scene_number,
                    summary,
                    word_count,
                    goal_id,
                    branch_id,
                    user_id,
                ),
            )
        else:
            self._conn.execute(
                """
                UPDATE scene_summaries
                SET sequence_number = ?,
                    domain_payload = ?,
                    book_number = ?,
                    chapter_number = ?,
                    scene_number = ?,
                    summary = ?,
                    word_count = ?,
                    goal_id = COALESCE(?, goal_id),
                    branch_id = COALESCE(?, branch_id),
                    user_id = COALESCE(?, user_id)
                WHERE id = ?
                """,
                (
                    sequence_number,
                    _json_dumps(payload),
                    book_number,
                    chapter_number,
                    scene_number,
                    summary,
                    word_count,
                    goal_id,
                    branch_id,
                    user_id,
                    existing[0],
                ),
            )
        self._conn.commit()

    def get_recent(
        self,
        chapter: int,
        k: int = 5,
        book: int = 1,
    ) -> list[SceneSummary]:
        """Return the *k* most recent scene summaries up to *chapter*."""
        rows = self._conn.execute(
            """
            SELECT book_number, chapter_number, scene_number,
                   summary, word_count
            FROM scene_summaries
            WHERE universe_id = ?
              AND book_number = ?
              AND chapter_number <= ?
            ORDER BY chapter_number DESC, scene_number DESC
            LIMIT ?
            """,
            (self._universe_id, book, chapter, k),
        ).fetchall()
        return [
            SceneSummary(
                book_number=r[0],
                chapter_number=r[1],
                scene_number=r[2],
                summary=r[3],
                word_count=r[4],
            )
            for r in rows
        ]

    def get_recent_episodes(
        self,
        max_sequence: int,
        k: int = 5,
        *,
        domain_id: str | None = None,
    ) -> list[EpisodeSummary]:
        """Return recent domain-neutral episode summaries."""
        params: list[Any] = [self._universe_id, max_sequence]
        domain_filter = ""
        if domain_id is not None:
            domain_filter = " AND domain_id = ?"
            params.append(domain_id)
        params.append(k)
        rows = self._conn.execute(
            f"""
            SELECT domain_id, episode_id, sequence_number,
                   domain_payload, summary, word_count
            FROM scene_summaries
            WHERE universe_id = ?
              AND sequence_number <= ?
              {domain_filter}
            ORDER BY sequence_number DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            EpisodeSummary(
                domain_id=r[0] or "",
                episode_id=r[1] or "",
                sequence_number=r[2] or 0,
                domain_payload=_json_loads(r[3]),
                summary=r[4],
                word_count=r[5],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Episodic facts
    # ------------------------------------------------------------------

    def store_fact(
        self,
        fact_id: str,
        entity: str,
        content: str,
        source_scene: str = "",
        scope: "MemoryScope | None" = None,
    ) -> None:
        """Insert or increment evidence for a fact.

        Memory-scope Stage 2b: ``scope`` tags new rows with the
        caller's sub-tiers. Existing rows' scope columns are left
        untouched on evidence increment — re-tagging a fact mid-
        evidence-chain is a semantic we don't want yet (would need a
        conflict policy Stage 2c can define).
        """
        goal_id, branch_id, user_id = self._scope_tiers(scope)
        existing = self._conn.execute(
            "SELECT evidence_count, source_scenes FROM episodic_facts "
            "WHERE universe_id = ? AND fact_id = ?",
            (self._universe_id, fact_id),
        ).fetchone()

        if existing is not None:
            count = existing[0] + 1
            scenes = json.loads(existing[1])
            if source_scene and source_scene not in scenes:
                scenes.append(source_scene)
            self._conn.execute(
                "UPDATE episodic_facts "
                "SET evidence_count = ?, source_scenes = ? "
                "WHERE universe_id = ? AND fact_id = ?",
                (count, json.dumps(scenes), self._universe_id, fact_id),
            )
        else:
            scenes = [source_scene] if source_scene else []
            self._conn.execute(
                """
                INSERT INTO episodic_facts
                    (universe_id, fact_id, entity, content,
                     evidence_count, source_scenes,
                     goal_id, branch_id, user_id)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (self._universe_id, fact_id, entity, content,
                 json.dumps(scenes),
                 goal_id, branch_id, user_id),
            )
        self._conn.commit()

    def get_facts_for_entity(self, entity: str) -> list[dict[str, Any]]:
        """Return all episodic facts mentioning *entity*."""
        rows = self._conn.execute(
            "SELECT fact_id, entity, content, evidence_count, "
            "source_scenes, promoted "
            "FROM episodic_facts "
            "WHERE universe_id = ? AND entity = ?",
            (self._universe_id, entity),
        ).fetchall()
        return [
            {
                "fact_id": r[0],
                "entity": r[1],
                "content": r[2],
                "evidence_count": r[3],
                "source_scenes": json.loads(r[4]),
                "promoted": bool(r[5]),
            }
            for r in rows
        ]

    def get_promotable_facts(self, threshold: int = 3) -> list[dict[str, Any]]:
        """Return facts with evidence_count >= *threshold* not yet promoted."""
        rows = self._conn.execute(
            "SELECT fact_id, entity, content, evidence_count, source_scenes "
            "FROM episodic_facts "
            "WHERE universe_id = ? AND promoted = 0 "
            "AND evidence_count >= ?",
            (self._universe_id, threshold),
        ).fetchall()
        return [
            {
                "fact_id": r[0],
                "entity": r[1],
                "content": r[2],
                "evidence_count": r[3],
                "source_scenes": json.loads(r[4]),
            }
            for r in rows
        ]

    def mark_promoted(self, fact_id: str) -> None:
        """Mark a fact as promoted to archival/canonical."""
        self._conn.execute(
            "UPDATE episodic_facts SET promoted = 1 "
            "WHERE universe_id = ? AND fact_id = ?",
            (self._universe_id, fact_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Style observations
    # ------------------------------------------------------------------

    def store_observation(
        self,
        dimension: str,
        observation: str,
        scene_ref: str = "",
        scope: "MemoryScope | None" = None,
    ) -> None:
        """Record a style observation from judge feedback.

        Memory-scope Stage 2b: optional ``scope`` tags the row.
        """
        goal_id, branch_id, user_id = self._scope_tiers(scope)
        self._conn.execute(
            "INSERT INTO style_observations "
            "(universe_id, dimension, observation, scene_ref, "
            " goal_id, branch_id, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._universe_id, dimension, observation, scene_ref,
             goal_id, branch_id, user_id),
        )
        self._conn.commit()

    def get_observations_by_dimension(
        self, dimension: str
    ) -> list[dict[str, str]]:
        """Return all observations for a style dimension."""
        rows = self._conn.execute(
            "SELECT observation, scene_ref, created_at "
            "FROM style_observations "
            "WHERE universe_id = ? AND dimension = ? "
            "ORDER BY created_at DESC",
            (self._universe_id, dimension),
        ).fetchall()
        return [
            {"observation": r[0], "scene_ref": r[1], "created_at": r[2]}
            for r in rows
        ]

    def count_observations(self, dimension: str) -> int:
        """Count observations for a dimension (used by promotion gates)."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM style_observations "
            "WHERE universe_id = ? AND dimension = ?",
            (self._universe_id, dimension),
        ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Reflections
    # ------------------------------------------------------------------

    def store_reflection(
        self,
        chapter_number: int,
        scene_number: int,
        critique: str,
        reflection: str,
        scope: "MemoryScope | None" = None,
    ) -> None:
        """Store a Reflexion entry from a revert cycle.

        Memory-scope Stage 2b: optional ``scope`` tags the row.
        """
        goal_id, branch_id, user_id = self._scope_tiers(scope)
        domain_payload = {
            "chapter_number": chapter_number,
            "scene_number": scene_number,
        }
        self._conn.execute(
            "INSERT INTO reflections "
            "(universe_id, domain_id, episode_id, sequence_number, "
            " domain_payload, chapter_number, scene_number, "
            " critique, reflection, goal_id, branch_id, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self._universe_id,
                FANTASY_DOMAIN_ID,
                _fantasy_reflection_id(chapter_number, scene_number),
                chapter_number,
                _json_dumps(domain_payload),
                chapter_number,
                scene_number,
                critique,
                reflection,
                goal_id,
                branch_id,
                user_id,
            ),
        )
        self._conn.commit()

    def get_recent_reflections(self, k: int = 3) -> list[dict[str, Any]]:
        """Return the *k* most recent reflections."""
        rows = self._conn.execute(
            "SELECT chapter_number, scene_number, critique, reflection, "
            "created_at "
            "FROM reflections WHERE universe_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (self._universe_id, k),
        ).fetchall()
        return [
            {
                "chapter_number": r[0],
                "scene_number": r[1],
                "critique": r[2],
                "reflection": r[3],
                "created_at": r[4],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Sliding window eviction
    # ------------------------------------------------------------------

    def evict_old_summaries(
        self,
        current_chapter: int,
        book: int = 1,
    ) -> int:
        """Remove summaries older than *window_chapters* from current.

        Returns the number of rows deleted.
        """
        cutoff = current_chapter - self._window_chapters
        if cutoff < 1:
            return 0
        cur = self._conn.execute(
            "DELETE FROM scene_summaries "
            "WHERE universe_id = ? AND book_number = ? "
            "AND chapter_number < ?",
            (self._universe_id, book, cutoff),
        )
        self._conn.commit()
        return cur.rowcount


def check_episodic_no_bleed(
    conn_or_path: sqlite3.Connection | str | Path,
    *,
    fantasy_domain_id: str = FANTASY_DOMAIN_ID,
) -> list[str]:
    """Return non-fantasy rows that still carry fantasy coordinates."""
    conn, should_close = _coerce_connection(conn_or_path)
    try:
        violations: list[str] = []
        if _has_columns(
            conn,
            "scene_summaries",
            ("domain_id", "book_number", "chapter_number", "scene_number"),
        ):
            rows = conn.execute(
                """
                SELECT id, universe_id, COALESCE(domain_id, '') AS domain_id
                FROM scene_summaries
                WHERE COALESCE(domain_id, '') != ?
                  AND (
                    book_number IS NOT NULL
                    OR chapter_number IS NOT NULL
                    OR scene_number IS NOT NULL
                  )
                """,
                (fantasy_domain_id,),
            ).fetchall()
            violations.extend(
                "scene_summaries id={id} universe={universe} domain={domain} "
                "carries book/chapter/scene coordinates".format(
                    id=row[0], universe=row[1], domain=row[2]
                )
                for row in rows
            )
        if _has_columns(
            conn,
            "reflections",
            ("domain_id", "chapter_number", "scene_number"),
        ):
            rows = conn.execute(
                """
                SELECT id, universe_id, COALESCE(domain_id, '') AS domain_id
                FROM reflections
                WHERE COALESCE(domain_id, '') != ?
                  AND (chapter_number IS NOT NULL OR scene_number IS NOT NULL)
                """,
                (fantasy_domain_id,),
            ).fetchall()
            violations.extend(
                "reflections id={id} universe={universe} domain={domain} "
                "carries chapter/scene coordinates".format(
                    id=row[0], universe=row[1], domain=row[2]
                )
                for row in rows
            )
        return violations
    finally:
        if should_close:
            conn.close()


def migrate_episodic_schema_to_domain_neutral(
    db_path: str | Path,
    *,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    legacy_domain_id: str = FANTASY_DOMAIN_ID,
) -> EpisodicSchemaMigrationResult:
    """Host-gated migration that loosens fantasy coordinates.

    ``dry_run=True`` copies the database, performs the rebuild on the copy,
    and reports the result without modifying the source. ``dry_run=False``
    creates a SQLite backup before touching the source; callers can restore
    it with :func:`rollback_episodic_schema_migration`.
    """
    source = Path(db_path)
    if str(source) == ":memory:":
        raise ValueError("episodic schema migration requires a filesystem DB")
    if not source.exists():
        raise FileNotFoundError(source)

    if dry_run:
        with tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / source.name
            _backup_sqlite_database(source, candidate)
            report = _migrate_episodic_database_in_place(
                candidate,
                dry_run=True,
                backup_path=None,
                legacy_domain_id=legacy_domain_id,
            )
            return EpisodicSchemaMigrationResult(
                db_path=source,
                dry_run=True,
                schema_changed=report.schema_changed,
                backup_path=None,
                row_counts=report.row_counts,
                no_bleed_ok=report.no_bleed_ok,
                no_bleed_violations=report.no_bleed_violations,
            )

    if os.environ.get(EPISODIC_NEUTRAL_MIGRATION_FLAG) != "1":
        raise PermissionError(
            f"Set {EPISODIC_NEUTRAL_MIGRATION_FLAG}=1 to run the "
            "host-gated episodic schema rebuild; dry_run=True does not "
            "require the flag."
        )

    backup_root = Path(backup_dir) if backup_dir is not None else source.parent
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_root / f"{source.stem}.episodic-neutral.{stamp}.bak"
    _backup_sqlite_database(source, backup_path)
    try:
        return _migrate_episodic_database_in_place(
            source,
            dry_run=False,
            backup_path=backup_path,
            legacy_domain_id=legacy_domain_id,
        )
    except Exception:
        rollback_episodic_schema_migration(source, backup_path)
        raise


def rollback_episodic_schema_migration(
    db_path: str | Path,
    backup_path: str | Path,
) -> None:
    """Restore an episodic DB from a migration backup."""
    target = Path(db_path)
    shutil.copy2(Path(backup_path), target)
    _remove_sqlite_sidecars(target)


def _migrate_episodic_database_in_place(
    db_path: Path,
    *,
    dry_run: bool,
    backup_path: Path | None,
    legacy_domain_id: str,
) -> EpisodicSchemaMigrationResult:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_scope_columns(conn)
        _ensure_domain_neutral_columns(conn)
        _rewrite_scene_summaries(conn, legacy_domain_id=legacy_domain_id)
        _rewrite_reflections(conn, legacy_domain_id=legacy_domain_id)
        conn.commit()
        violations = check_episodic_no_bleed(conn)
        return EpisodicSchemaMigrationResult(
            db_path=db_path,
            dry_run=dry_run,
            schema_changed=True,
            backup_path=backup_path,
            row_counts=_row_counts(conn),
            no_bleed_ok=not violations,
            no_bleed_violations=violations,
        )
    finally:
        conn.close()


def _rewrite_scene_summaries(
    conn: sqlite3.Connection,
    *,
    legacy_domain_id: str,
) -> None:
    if not _table_exists(conn, "scene_summaries"):
        return
    rows = conn.execute("SELECT * FROM scene_summaries ORDER BY id").fetchall()
    conn.execute("ALTER TABLE scene_summaries RENAME TO scene_summaries__old")
    conn.execute(
        """
        CREATE TABLE scene_summaries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            universe_id   TEXT    NOT NULL,
            domain_id     TEXT    NOT NULL DEFAULT '',
            episode_id    TEXT    NOT NULL DEFAULT '',
            sequence_number INTEGER NOT NULL DEFAULT 0,
            domain_payload TEXT   NOT NULL DEFAULT '{}',
            book_number   INTEGER,
            chapter_number INTEGER,
            scene_number  INTEGER,
            summary       TEXT    NOT NULL,
            word_count    INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            goal_id       TEXT,
            branch_id     TEXT,
            user_id       TEXT,
            UNIQUE(universe_id, domain_id, episode_id),
            UNIQUE(universe_id, book_number, chapter_number, scene_number)
        )
        """
    )
    for row in rows:
        domain_id = row["domain_id"] or legacy_domain_id
        book = row["book_number"]
        chapter = row["chapter_number"]
        scene = row["scene_number"]
        payload = _json_loads(row["domain_payload"])
        if not payload:
            payload = {
                "book_number": book,
                "chapter_number": chapter,
                "scene_number": scene,
            }
        episode_id = row["episode_id"] or _fantasy_episode_id(book, chapter, scene)
        sequence_number = row["sequence_number"]
        if sequence_number is None:
            sequence_number = chapter or 0
        if domain_id != FANTASY_DOMAIN_ID:
            book = chapter = scene = None
        conn.execute(
            """
            INSERT INTO scene_summaries
                (id, universe_id, domain_id, episode_id, sequence_number,
                 domain_payload, book_number, chapter_number, scene_number,
                 summary, word_count, created_at, goal_id, branch_id, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["universe_id"],
                domain_id,
                episode_id,
                sequence_number,
                _json_dumps(payload),
                book,
                chapter,
                scene,
                row["summary"],
                row["word_count"],
                row["created_at"],
                row["goal_id"],
                row["branch_id"],
                row["user_id"],
            ),
        )
    conn.execute("DROP TABLE scene_summaries__old")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scene_summaries_scope "
        "ON scene_summaries(universe_id, goal_id, branch_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scene_summaries_episode "
        "ON scene_summaries(universe_id, domain_id, episode_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scene_summaries_sequence "
        "ON scene_summaries(universe_id, domain_id, sequence_number)"
    )


def _rewrite_reflections(
    conn: sqlite3.Connection,
    *,
    legacy_domain_id: str,
) -> None:
    if not _table_exists(conn, "reflections"):
        return
    rows = conn.execute("SELECT * FROM reflections ORDER BY id").fetchall()
    conn.execute("ALTER TABLE reflections RENAME TO reflections__old")
    conn.execute(
        """
        CREATE TABLE reflections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            universe_id   TEXT    NOT NULL,
            domain_id     TEXT    NOT NULL DEFAULT '',
            episode_id    TEXT    NOT NULL DEFAULT '',
            sequence_number INTEGER NOT NULL DEFAULT 0,
            domain_payload TEXT   NOT NULL DEFAULT '{}',
            chapter_number INTEGER,
            scene_number  INTEGER,
            critique      TEXT    NOT NULL,
            reflection    TEXT    NOT NULL,
            created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
            goal_id       TEXT,
            branch_id     TEXT,
            user_id       TEXT
        )
        """
    )
    for row in rows:
        domain_id = row["domain_id"] or legacy_domain_id
        chapter = row["chapter_number"]
        scene = row["scene_number"]
        payload = _json_loads(row["domain_payload"])
        if not payload:
            payload = {
                "chapter_number": chapter,
                "scene_number": scene,
            }
        episode_id = row["episode_id"] or _fantasy_reflection_id(chapter, scene)
        sequence_number = row["sequence_number"]
        if sequence_number is None:
            sequence_number = chapter or 0
        if domain_id != FANTASY_DOMAIN_ID:
            chapter = scene = None
        conn.execute(
            """
            INSERT INTO reflections
                (id, universe_id, domain_id, episode_id, sequence_number,
                 domain_payload, chapter_number, scene_number, critique,
                 reflection, created_at, goal_id, branch_id, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["universe_id"],
                domain_id,
                episode_id,
                sequence_number,
                _json_dumps(payload),
                chapter,
                scene,
                row["critique"],
                row["reflection"],
                row["created_at"],
                row["goal_id"],
                row["branch_id"],
                row["user_id"],
            ),
        )
    conn.execute("DROP TABLE reflections__old")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reflections_scope "
        "ON reflections(universe_id, goal_id, branch_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reflections_sequence "
        "ON reflections(universe_id, domain_id, sequence_number)"
    )


def _ensure_scope_columns(conn: sqlite3.Connection) -> None:
    for table in EpisodicMemory._SCOPED_TABLES:
        if not _table_exists(conn, table):
            continue
        existing = _columns(conn, table)
        for col_name, col_type in EpisodicMemory._SCOPE_COLUMNS:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )


def _ensure_domain_neutral_columns(conn: sqlite3.Connection) -> None:
    for table, columns in EpisodicMemory._DOMAIN_NEUTRAL_COLUMNS.items():
        if not _table_exists(conn, table):
            continue
        existing = _columns(conn, table)
        for col_name, col_type in columns:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )


def _backup_sqlite_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(f"{db_path.name}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _coerce_connection(
    conn_or_path: sqlite3.Connection | str | Path,
) -> tuple[sqlite3.Connection, bool]:
    if isinstance(conn_or_path, sqlite3.Connection):
        return conn_or_path, False
    conn = sqlite3.connect(conn_or_path)
    return conn, True


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> bool:
    if not _table_exists(conn, table):
        return False
    existing = _columns(conn, table)
    return all(column in existing for column in columns)


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "scene_summaries",
        "episodic_facts",
        "style_observations",
        "reflections",
    ):
        if not _table_exists(conn, table):
            continue
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        counts[table] = int(row[0]) if row is not None else 0
    return counts


def initialize_episodic_index(conn):
    """Trusted schema for a disposable index or an explicit offline fixture."""
    view = object.__new__(EpisodicMemory)
    view._conn = conn
    conn.executescript(_SCHEMA)
    view._migrate_scope_columns()
    view._migrate_domain_neutral_columns()
