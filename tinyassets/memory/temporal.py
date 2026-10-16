"""Temporal truth tracking -- facts with time validity windows.

A temporal fact is valid from ``valid_from`` to ``valid_until``.
Queries can ask "what was true at this time?" and "when did this change?"

Supports both ISO timestamps and narrative time references (e.g., "when
the king was crowned" or "after the siege"). Conflicts are facts about
the same entity/attribute with overlapping validity periods.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from tinyassets.okf.records import SourceRecords
from tinyassets.okf.sql_index import CanonicalConnection, canonical_methods

logger = logging.getLogger(__name__)

_TEMPORAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS temporal_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id         TEXT    NOT NULL UNIQUE,
    entity          TEXT    NOT NULL,
    attribute       TEXT    NOT NULL,
    value           TEXT    NOT NULL,
    valid_from      TEXT,
    valid_until     TEXT,
    asserted_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    asserted_by     TEXT    NOT NULL DEFAULT 'system',
    confidence      REAL    NOT NULL DEFAULT 0.5,
    supersedes      TEXT,
    source_type     TEXT    NOT NULL DEFAULT 'extracted',
    branch_id       TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_temporal_entity
    ON temporal_facts(entity, attribute);
CREATE INDEX IF NOT EXISTS idx_temporal_branch
    ON temporal_facts(branch_id);
CREATE INDEX IF NOT EXISTS idx_temporal_valid_from
    ON temporal_facts(valid_from);
CREATE INDEX IF NOT EXISTS idx_temporal_valid_until
    ON temporal_facts(valid_until);
"""


@dataclass(frozen=True, slots=True)
class TemporalFact:
    """A fact with validity bounds in time.

    Attributes
    ----------
    fact_id : str
        Unique identifier.
    entity : str
        The entity this fact is about (e.g., "Arion" or "Kingdom of Ashwater").
    attribute : str
        What is being asserted (e.g., "location", "title", "status").
    value : Any
        The value of the attribute (e.g., "in the tower", "King", "dead").
    valid_from : str | None
        ISO 8601 timestamp or narrative reference when this fact becomes true.
        None means "always was true (before narrative start)".
    valid_until : str | None
        ISO 8601 timestamp or narrative reference when this fact stops being true.
        None means "still true (no end date known)".
    asserted_at : str
        ISO 8601 timestamp of when the system learned this fact.
    asserted_by : str
        Which author/agent added this fact (e.g., "writer", "editor", "user").
    confidence : float
        0.0-1.0 confidence in this fact's correctness.
    supersedes : str | None
        fact_id of a previous fact this one replaces.
    source_type : str
        How we learned this: 'canon', 'extracted', 'inferred', 'user'.
    branch_id : str | None
        Which universe/branch this fact belongs to. None = universal.
    """

    fact_id: str
    entity: str
    attribute: str
    value: Any
    asserted_at: str
    asserted_by: str = "system"
    confidence: float = 0.5
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    supersedes: Optional[str] = None
    source_type: str = "extracted"
    branch_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dict for storage or serialization."""
        return {
            "fact_id": self.fact_id,
            "entity": self.entity,
            "attribute": self.attribute,
            "value": self.value,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "asserted_at": self.asserted_at,
            "asserted_by": self.asserted_by,
            "confidence": self.confidence,
            "supersedes": self.supersedes,
            "source_type": self.source_type,
            "branch_id": self.branch_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TemporalFact:
        """Reconstruct a TemporalFact from a dict."""
        return cls(
            fact_id=d["fact_id"],
            entity=d["entity"],
            attribute=d["attribute"],
            value=d["value"],
            asserted_at=d.get("asserted_at", datetime.utcnow().isoformat()),
            asserted_by=d.get("asserted_by", "system"),
            confidence=d.get("confidence", 0.5),
            valid_from=d.get("valid_from"),
            valid_until=d.get("valid_until"),
            supersedes=d.get("supersedes"),
            source_type=d.get("source_type", "extracted"),
            branch_id=d.get("branch_id"),
        )


@canonical_methods
class TemporalFactStore:
    """Canonical owner-box store for temporal facts.

    Supports temporal queries: "what was true at time T?" and "when did
    this attribute change?"

    Parameters
    ----------
    source : SourceRecords
        Explicitly admitted, reviewed canonical owner-box records.
    """

    def __init__(self, source: SourceRecords) -> None:
        self._conn = CanonicalConnection(
            source, "temporal", lambda db: db.executescript(_TEMPORAL_SCHEMA)
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def assert_fact(self, fact: TemporalFact) -> None:
        """Store a new fact assertion.

        Parameters
        ----------
        fact : TemporalFact
            The fact to store.
        """
        self._conn.execute(
            """
            INSERT INTO temporal_facts
                (fact_id, entity, attribute, value, valid_from, valid_until,
                 asserted_at, asserted_by, confidence, supersedes,
                 source_type, branch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact.fact_id,
                fact.entity,
                fact.attribute,
                str(fact.value),
                fact.valid_from,
                fact.valid_until,
                fact.asserted_at,
                fact.asserted_by,
                fact.confidence,
                fact.supersedes,
                fact.source_type,
                fact.branch_id,
            ),
        )
        self._conn.commit()
        logger.debug("Asserted fact: %s", fact.fact_id)

    def supersede(
        self,
        old_fact_id: str,
        new_fact: TemporalFact,
    ) -> None:
        """Mark an old fact as superseded and store a new one.

        Parameters
        ----------
        old_fact_id : str
            fact_id of the fact being replaced.
        new_fact : TemporalFact
            The new fact (should have supersedes=old_fact_id).
        """
        # Verify the old fact exists.
        old = self._conn.execute(
            "SELECT fact_id FROM temporal_facts WHERE fact_id = ?",
            (old_fact_id,),
        ).fetchone()
        if not old:
            logger.warning("Attempted to supersede non-existent fact: %s", old_fact_id)
            return

        # Update old fact's valid_until if not already set.
        self._conn.execute(
            """
            UPDATE temporal_facts
            SET valid_until = ?
            WHERE fact_id = ? AND valid_until IS NULL
            """,
            (datetime.utcnow().isoformat(), old_fact_id),
        )

        # Assert the new fact.
        self.assert_fact(new_fact)
        logger.info(
            "Superseded %s with %s", old_fact_id, new_fact.fact_id
        )

    def query_at_time(
        self,
        entity: str,
        attribute: str | None = None,
        at_time: str | None = None,
        branch_id: str | None = None,
    ) -> list[TemporalFact]:
        """Get facts valid at a specific point in time.

        A fact is valid at ``at_time`` if:
        - ``valid_from`` <= ``at_time`` (or valid_from is None)
        - ``valid_until`` > ``at_time`` (or valid_until is None)

        Parameters
        ----------
        entity : str
            Entity to query.
        attribute : str | None
            Specific attribute, or None for all attributes.
        at_time : str | None
            ISO timestamp. None = current time.
        branch_id : str | None
            Filter by branch. None = query all branches.

        Returns
        -------
        list[TemporalFact]
            Facts valid at the specified time.
        """
        if at_time is None:
            at_time = datetime.utcnow().isoformat()

        query = """
            SELECT fact_id, entity, attribute, value, valid_from, valid_until,
                   asserted_at, asserted_by, confidence, supersedes,
                   source_type, branch_id
            FROM temporal_facts
            WHERE entity = ?
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_until IS NULL OR valid_until > ?)
        """
        params: list[Any] = [entity, at_time, at_time]

        if attribute is not None:
            query += " AND attribute = ?"
            params.append(attribute)

        if branch_id is not None:
            query += " AND (branch_id = ? OR branch_id IS NULL)"
            params.append(branch_id)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_temporal_fact(r) for r in rows]

    def query_current(
        self,
        entity: str,
        attribute: str | None = None,
        branch_id: str | None = None,
    ) -> list[TemporalFact]:
        """Get currently-valid facts (valid_until is None).

        Convenience method for querying at the current time.

        Parameters
        ----------
        entity : str
            Entity to query.
        attribute : str | None
            Specific attribute, or None for all.
        branch_id : str | None
            Filter by branch.

        Returns
        -------
        list[TemporalFact]
            Currently-valid facts.
        """
        query = """
            SELECT fact_id, entity, attribute, value, valid_from, valid_until,
                   asserted_at, asserted_by, confidence, supersedes,
                   source_type, branch_id
            FROM temporal_facts
            WHERE entity = ? AND valid_until IS NULL
        """
        params: list[Any] = [entity]

        if attribute is not None:
            query += " AND attribute = ?"
            params.append(attribute)

        if branch_id is not None:
            query += " AND (branch_id = ? OR branch_id IS NULL)"
            params.append(branch_id)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_temporal_fact(r) for r in rows]

    def query_history(
        self,
        entity: str,
        attribute: str | None = None,
    ) -> list[TemporalFact]:
        """Get full history of an entity/attribute, including superseded facts.

        Parameters
        ----------
        entity : str
            Entity to query.
        attribute : str | None
            Specific attribute, or None for all.

        Returns
        -------
        list[TemporalFact]
            All facts (including superseded) ordered by asserted_at desc.
        """
        query = """
            SELECT fact_id, entity, attribute, value, valid_from, valid_until,
                   asserted_at, asserted_by, confidence, supersedes,
                   source_type, branch_id
            FROM temporal_facts
            WHERE entity = ?
        """
        params: list[Any] = [entity]

        if attribute is not None:
            query += " AND attribute = ?"
            params.append(attribute)

        query += " ORDER BY asserted_at DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_temporal_fact(r) for r in rows]

    def invalidate(
        self,
        fact_id: str,
        reason: str = "",
    ) -> None:
        """Mark a fact as no longer valid.

        Sets valid_until to now if not already set.

        Parameters
        ----------
        fact_id : str
            fact_id to invalidate.
        reason : str
            Optional reason for invalidation (for audit trail).
        """
        now = datetime.utcnow().isoformat()
        self._conn.execute(
            """
            UPDATE temporal_facts
            SET valid_until = ?
            WHERE fact_id = ? AND valid_until IS NULL
            """,
            (now, fact_id),
        )
        self._conn.commit()
        logger.info(
            "Invalidated fact %s%s",
            fact_id,
            f" ({reason})" if reason else "",
        )

    def get_conflicts(
        self,
        entity: str,
        attribute: str,
        branch_id: str | None = None,
    ) -> list[tuple[TemporalFact, TemporalFact]]:
        """Find facts with overlapping validity periods (potential contradictions).

        Two facts conflict if they describe the same entity/attribute but
        have non-zero overlap in their valid_from/valid_until windows.

        Parameters
        ----------
        entity : str
            Entity to check for conflicts.
        attribute : str
            Attribute to check.
        branch_id : str | None
            Filter by branch.

        Returns
        -------
        list[tuple[TemporalFact, TemporalFact]]
            Pairs of conflicting facts.
        """
        query = """
            SELECT f1.fact_id, f1.entity, f1.attribute, f1.value,
                   f1.valid_from, f1.valid_until, f1.asserted_at,
                   f1.asserted_by, f1.confidence, f1.supersedes,
                   f1.source_type, f1.branch_id,
                   f2.fact_id, f2.entity, f2.attribute, f2.value,
                   f2.valid_from, f2.valid_until, f2.asserted_at,
                   f2.asserted_by, f2.confidence, f2.supersedes,
                   f2.source_type, f2.branch_id
            FROM temporal_facts f1
            JOIN temporal_facts f2
              ON f1.entity = f2.entity
              AND f1.attribute = f2.attribute
              AND f1.fact_id < f2.fact_id
            WHERE f1.entity = ?
              AND f1.attribute = ?
              AND (
                (f1.valid_from <= COALESCE(f2.valid_until, '9999-12-31'))
                AND (COALESCE(f1.valid_until, '9999-12-31') > f2.valid_from)
              )
        """
        params: list[Any] = [entity, attribute]

        if branch_id is not None:
            query += " AND (f1.branch_id = ? OR f1.branch_id IS NULL)"
            params.append(branch_id)

        rows = self._conn.execute(query, params).fetchall()

        conflicts: list[tuple[TemporalFact, TemporalFact]] = []
        for row in rows:
            r1 = row[:12]
            r2 = row[12:24]
            fact1 = self._row_to_temporal_fact(r1)
            fact2 = self._row_to_temporal_fact(r2)
            conflicts.append((fact1, fact2))

        logger.debug(
            "Found %d conflicts for %s.%s",
            len(conflicts),
            entity,
            attribute,
        )
        return conflicts

    @staticmethod
    def _row_to_temporal_fact(row: tuple[Any, ...]) -> TemporalFact:
        """Convert a database row to a TemporalFact."""
        return TemporalFact(
            fact_id=row[0],
            entity=row[1],
            attribute=row[2],
            value=row[3],
            valid_from=row[4],
            valid_until=row[5],
            asserted_at=row[6],
            asserted_by=row[7],
            confidence=row[8],
            supersedes=row[9],
            source_type=row[10],
            branch_id=row[11],
        )


class TemporalIndex:
    """In-memory index for fast temporal queries.

    Builds from the store and provides convenience methods for:
    - "which entities changed since timestamp T?"
    - "which branches have conflicts?"
    """

    def __init__(self) -> None:
        """Initialize an empty index."""
        # entity -> sorted list of (valid_from, valid_until, fact_id)
        self._store: TemporalFactStore | None = None
        self._entity_timeline: dict[str, list[tuple[str, str, str]]] = {}
        # branch_id -> set of (entity, attribute) with conflicts
        self._conflicts_by_branch: dict[str | None, set[tuple[str, str]]] = {}
        # entity -> timestamp of last modification
        self._last_modified: dict[str, str] = {}

    def rebuild_from_store(self, store: TemporalFactStore) -> None:
        """Rebuild the index from a TemporalFactStore.

        Parameters
        ----------
        store : TemporalFactStore
            The store to index.
        """
        self._store = store
        self._entity_timeline.clear()
        self._conflicts_by_branch.clear()
        self._last_modified.clear()

        # Query all facts (this is the expensive part; run infrequently).
        # Order by asserted_at to track when facts were learned.
        query = """
            SELECT entity, valid_from, valid_until, fact_id, asserted_at
            FROM temporal_facts
            ORDER BY entity, asserted_at DESC
        """
        with store._conn.transaction():
            rows = store._conn.execute(query).fetchall()

        for entity, valid_from, valid_until, fact_id, asserted_at in rows:
            # Add to timeline (coalesce None to boundary values for sorting).
            vf = valid_from or "0000-01-01T00:00:00"
            vu = valid_until or "9999-12-31T23:59:59"
            self._entity_timeline.setdefault(entity, []).append(
                (vf, vu, fact_id)
            )
            # Track modification time using asserted_at (when we learned it).
            self._last_modified[entity] = max(
                self._last_modified.get(entity, ""), asserted_at
            )

        # Sort timelines for binary search (if needed in future).
        for timeline in self._entity_timeline.values():
            timeline.sort()

        logger.debug("TemporalIndex rebuilt: %d entities", len(self._entity_timeline))

    def entities_changed_since(self, timestamp: str) -> set[str]:
        """Get entities that have facts asserted after *timestamp*.

        Parameters
        ----------
        timestamp : str
            ISO 8601 timestamp.

        Returns
        -------
        set[str]
            Entity names with recent activity.
        """
        if self._store is not None:
            self.rebuild_from_store(self._store)
        return {
            entity
            for entity, last_ts in self._last_modified.items()
            if last_ts > timestamp
        }

    def branches_with_conflicts(self) -> dict[str, list[str]]:
        """Get branches that have temporal conflicts.

        Returns
        -------
        dict[str, list[str]]
            branch_id -> list of conflicting entity names.
        """
        # For now, return empty (would be populated during rebuild_from_store
        # if we ran conflict detection there).
        # This is a placeholder for Phase 4 expansion.
        return dict(self._conflicts_by_branch)
