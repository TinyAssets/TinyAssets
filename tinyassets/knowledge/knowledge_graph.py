"""Knowledge graph core -- SQLite persistence + igraph construction.

Entities and edges are persisted in SQLite.  An igraph.Graph is built
on demand from the stored relationships for Leiden clustering and
HippoRAG (Personalized PageRank).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence

import igraph as ig

from tinyassets.knowledge.models import (
    FactWithContext,
    GraphEdge,
    GraphEntity,
)
from tinyassets.knowledge.tag_matrix import knowledge_tags_from_mapping


def _attach_row_tags(target: Any, row: sqlite3.Row) -> None:
    """Attach flat tag metadata from a SQLite row to a result object."""
    tags = knowledge_tags_from_mapping(dict(row))
    if isinstance(target, dict):
        metadata = tags.as_row_metadata()
        target.update(metadata)
        return
    setattr(target, "tag_universes", list(tags.universes))
    setattr(target, "tag_domains", list(tags.domains))
    setattr(target, "tag_shapes", list(tags.shapes))
    setattr(target, "tag_general", tags.general)
    setattr(target, "tag_commons", tags.commons)
    setattr(target, "tag_private_canon", tags.private_canon)
    setattr(
        target,
        "promotion_record",
        (
            json.dumps(tags.promotion_record.__dict__)
            if tags.promotion_record is not None
            else ""
        ),
    )


class KnowledgeGraph:
    """SQLite-backed knowledge graph with igraph construction.

    Parameters
    ----------
    db_path
        Path to the SQLite database file.  Created if it doesn't exist.
    """

    def __init__(self, db_path: str | Path = "") -> None:
        if not db_path:
            raise ValueError(
                "KnowledgeGraph requires an explicit db_path. "
                "CWD-relative defaults cause cross-universe contamination."
            )
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    # Memory-scope Stage 2a: the four scope columns added to every
    # archival KG table. Migration is idempotent — existing rows
    # inherit NULL (= broadest scope) except ``universe_id`` which
    # gets backfilled from the parent directory name when the caller
    # invokes :meth:`migrate_scope_columns`.
    _SCOPE_COLUMNS: tuple[tuple[str, str], ...] = (
        ("universe_id", "TEXT"),
        ("goal_id", "TEXT"),
        ("branch_id", "TEXT"),
        ("user_id", "TEXT"),
    )
    _TAG_COLUMNS: tuple[tuple[str, str], ...] = (
        ("tag_universes", "TEXT NOT NULL DEFAULT '[]'"),
        ("tag_domains", "TEXT NOT NULL DEFAULT '[]'"),
        ("tag_shapes", "TEXT NOT NULL DEFAULT '[]'"),
        ("tag_general", "INTEGER NOT NULL DEFAULT 0"),
        ("tag_commons", "INTEGER NOT NULL DEFAULT 0"),
        ("tag_private_canon", "INTEGER NOT NULL DEFAULT 0"),
        ("promotion_record", "TEXT NOT NULL DEFAULT ''"),
    )
    _SCOPED_TABLES: tuple[str, ...] = (
        "entities", "edges", "facts", "communities",
    )

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
                entity_id   TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL DEFAULT 'unknown',
                access_tier INTEGER NOT NULL DEFAULT 0,
                public_description  TEXT NOT NULL DEFAULT '',
                hidden_description  TEXT NOT NULL DEFAULT '',
                secret_description  TEXT NOT NULL DEFAULT '',
                aliases     TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS edges (
                source          TEXT NOT NULL,
                target          TEXT NOT NULL,
                relation_type   TEXT NOT NULL DEFAULT '',
                access_tier     INTEGER NOT NULL DEFAULT 0,
                temporal_scope  TEXT NOT NULL DEFAULT 'always',
                pov_characters  TEXT NOT NULL DEFAULT '[]',
                weight          REAL NOT NULL DEFAULT 1.0,
                valid_from_chapter INTEGER,
                valid_to_chapter   INTEGER,
                PRIMARY KEY (source, target, relation_type),
                FOREIGN KEY (source) REFERENCES entities(entity_id),
                FOREIGN KEY (target) REFERENCES entities(entity_id)
            );

            CREATE TABLE IF NOT EXISTS facts (
                fact_id             TEXT PRIMARY KEY,
                text                TEXT NOT NULL,
                source_type         TEXT NOT NULL DEFAULT 'narrator_claim',
                narrator            TEXT,
                narrator_reliability REAL NOT NULL DEFAULT 1.0,
                valid_from_chapter  INTEGER,
                valid_to_chapter    INTEGER,
                truth_value_initial TEXT,
                truth_value_final   TEXT,
                truth_value_revealed INTEGER,
                language_type       TEXT NOT NULL DEFAULT 'literal',
                narrative_function  TEXT NOT NULL DEFAULT 'world_fact',
                importance          REAL NOT NULL DEFAULT 0.5,
                weight              TEXT NOT NULL DEFAULT 'color',
                hardness            TEXT NOT NULL DEFAULT 'soft',
                horizon             TEXT NOT NULL DEFAULT 'scene',
                provenance          TEXT NOT NULL DEFAULT 'generated',
                confidence          REAL NOT NULL DEFAULT 0.5,
                seeded_scene        TEXT NOT NULL DEFAULT '',
                access_tier         INTEGER NOT NULL DEFAULT 0,
                pov_characters      TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS communities (
                community_id INTEGER NOT NULL,
                entity_id    TEXT NOT NULL,
                resolution   REAL NOT NULL DEFAULT 1.0,
                summary      TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (community_id, entity_id)
            );
            """
        )
        # Memory-scope Stage 2a: add the 4 scope columns to every
        # archival table if they don't already exist. SQLite has no
        # ADD COLUMN IF NOT EXISTS, so probe table_info first. Columns
        # are nullable on purpose — existing rows inherit NULL (=
        # broadest scope, pre-migration semantics).
        for table in self._SCOPED_TABLES:
            existing = {
                row["name"]
                for row in self._conn.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            for col_name, col_type in self._SCOPE_COLUMNS:
                if col_name in existing:
                    continue
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )
            existing = {
                row["name"]
                for row in self._conn.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            for col_name, col_type in self._TAG_COLUMNS:
                if col_name in existing:
                    continue
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                )
        # Shared index over (universe_id, goal_id, branch_id) per
        # design-note §4 — this is the hot read path.
        for table in self._SCOPED_TABLES:
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_scope "
                f"ON {table}(universe_id, goal_id, branch_id)"
            )
        self._conn.commit()

    def migrate_scope_columns(self, universe_id: str) -> int:
        """Backfill ``universe_id`` on existing rows that have NULL.

        Stage 2a migration per design-note §4: existing databases
        predate the scope columns. New installs get NULL on every
        row; the caller (usually `fantasy_author/__main__.py` on
        daemon boot) passes the universe_id derived from the parent
        directory so pre-existing canon becomes universe-scoped.

        Returns the total number of rows updated across all scoped
        tables. Idempotent — rows whose ``universe_id`` already
        matches are untouched.
        """
        if not universe_id:
            raise ValueError(
                "migrate_scope_columns requires a non-empty universe_id."
            )
        total = 0
        for table in self._SCOPED_TABLES:
            cursor = self._conn.execute(
                f"UPDATE {table} SET universe_id = ? "
                f"WHERE universe_id IS NULL",
                (universe_id,),
            )
            total += cursor.rowcount
        self._conn.commit()
        return total

    def get_entity(self, entity_id: str) -> GraphEntity | None:
        """Retrieve an entity by ID. A knowledge file outranks the database."""
        from tinyassets.knowledge_authority import files_are_authority, read_graph_entity

        owner = Path(self._db_path).parent
        filed = read_graph_entity(owner, entity_id)
        if filed is not None:
            aliases = filed.get("aliases", [])
            if isinstance(aliases, str):
                aliases = json.loads(aliases or "[]")
            return GraphEntity(
                entity_id=str(filed.get("entity_id") or entity_id),
                entity_type=str(filed.get("entity_type") or "unknown"),
                access_tier=int(filed.get("access_tier") or 0),
                public_description=str(filed.get("public_description") or ""),
                hidden_description=str(filed.get("hidden_description") or ""),
                secret_description=str(filed.get("secret_description") or ""),
                aliases=list(aliases or []),
            )
        if files_are_authority(owner):
            return None
        row = self._conn.execute(
            "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            return None
        return GraphEntity(
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            access_tier=row["access_tier"],
            public_description=row["public_description"],
            hidden_description=row["hidden_description"],
            secret_description=row["secret_description"],
            aliases=json.loads(row["aliases"]),
        )

    def query_entities(
        self,
        entity_type: str | None = None,
        access_tier: int | None = None,
    ) -> list[GraphEntity]:
        """Query entities with optional filters."""
        sql = "SELECT * FROM entities WHERE 1=1"
        params: list = []
        if entity_type is not None:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        if access_tier is not None:
            sql += " AND access_tier <= ?"
            params.append(access_tier)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            GraphEntity(
                entity_id=r["entity_id"],
                entity_type=r["entity_type"],
                access_tier=r["access_tier"],
                public_description=r["public_description"],
                hidden_description=r["hidden_description"],
                secret_description=r["secret_description"],
                aliases=json.loads(r["aliases"]),
            )
            for r in rows
        ]

    def get_edges(
        self,
        entity_id: str | None = None,
        relation_type: str | None = None,
        chapter_number: int | None = None,
        access_tier: int | None = None,
    ) -> list[GraphEdge]:
        """Query edges with optional filters including temporal/epistemic."""
        sql = "SELECT * FROM edges WHERE 1=1"
        params: list = []
        if entity_id is not None:
            sql += " AND (source = ? OR target = ?)"
            params.extend([entity_id, entity_id])
        if relation_type is not None:
            sql += " AND relation_type = ?"
            params.append(relation_type)
        if chapter_number is not None:
            sql += (
                " AND (valid_from_chapter IS NULL OR valid_from_chapter <= ?)"
                " AND (valid_to_chapter IS NULL OR valid_to_chapter >= ?)"
            )
            params.extend([chapter_number, chapter_number])
        if access_tier is not None:
            sql += " AND access_tier <= ?"
            params.append(access_tier)
        rows = self._conn.execute(sql, params).fetchall()
        edges: list[GraphEdge] = []
        for r in rows:
            edge = GraphEdge(
                source=r["source"],
                target=r["target"],
                relation_type=r["relation_type"],
                access_tier=r["access_tier"],
                temporal_scope=r["temporal_scope"],
                pov_characters=json.loads(r["pov_characters"]),
                weight=r["weight"],
                valid_from_chapter=r["valid_from_chapter"],
                valid_to_chapter=r["valid_to_chapter"],
            )
            _attach_row_tags(edge, r)
            edges.append(edge)
        return edges

    def query_facts(
        self,
        chapter_number: int | None = None,
        access_tier: int | None = None,
        character_id: str | None = None,
        hardness: str | None = None,
        language_type: str | None = None,
    ) -> list[FactWithContext]:
        """Query facts with epistemic and temporal filtering."""
        sql = "SELECT * FROM facts WHERE 1=1"
        params: list = []

        if chapter_number is not None:
            sql += (
                " AND (valid_from_chapter IS NULL OR valid_from_chapter <= ?)"
                " AND (valid_to_chapter IS NULL OR valid_to_chapter >= ?)"
            )
            params.extend([chapter_number, chapter_number])
        if access_tier is not None:
            sql += " AND access_tier <= ?"
            params.append(access_tier)
        if hardness is not None:
            sql += " AND hardness = ?"
            params.append(hardness)
        if language_type is not None:
            sql += " AND language_type = ?"
            params.append(language_type)

        rows = self._conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            from tinyassets.knowledge.models import (
                LanguageType,
                NarrativeFunction,
                SourceType,
            )

            fact = FactWithContext(
                fact_id=r["fact_id"],
                text=r["text"],
                source_type=SourceType(r["source_type"]),
                narrator=r["narrator"],
                narrator_reliability=r["narrator_reliability"],
                valid_from_chapter=r["valid_from_chapter"],
                valid_to_chapter=r["valid_to_chapter"],
                truth_value_initial=r["truth_value_initial"],
                truth_value_final=r["truth_value_final"],
                truth_value_revealed=r["truth_value_revealed"],
                language_type=LanguageType(r["language_type"]),
                narrative_function=NarrativeFunction(r["narrative_function"]),
                importance=r["importance"],
                weight=r["weight"],
                hardness=r["hardness"],
                horizon=r["horizon"],
                provenance=r["provenance"],
                confidence=r["confidence"],
                seeded_scene=r["seeded_scene"],
                access_tier=r["access_tier"],
                pov_characters=json.loads(r["pov_characters"]),
            )
            # Apply character-level epistemic filtering
            if character_id is not None:
                if fact.pov_characters and character_id not in fact.pov_characters:
                    continue
            _attach_row_tags(fact, r)
            results.append(fact)
        return results

    # ------------------------------------------------------------------
    # Query-layer methods (used by ArchivalMemory)
    # ------------------------------------------------------------------

    def hipporag_query(
        self,
        entities: list[str],
        k: int = 20,
    ) -> list[dict]:
        """Retrieve facts via Personalized PageRank on the KG.

        Builds the igraph, runs PPR seeded from *entities*, then returns
        up to *k* related facts as dicts with ``fact_id``, ``text``,
        ``importance``, and ``ppr_score``.
        """
        from tinyassets.knowledge.hipporag import HippoRAG

        hippo = HippoRAG(self)
        result = hippo.query(entity_mentions=entities, top_k=k)

        # Convert FactWithContext objects to plain dicts for the caller
        facts_out: list[dict] = []
        ranked_lookup = {
            r["entity_id"]: r["ppr_score"]
            for r in result.get("ranked_entities", [])
        }
        for fact in result.get("related_facts", []):
            facts_out.append({
                "fact_id": fact.fact_id,
                "text": fact.text,
                "importance": fact.importance,
                "source_type": fact.source_type.value,
                "ppr_score": max(
                    (ranked_lookup.get(eid, 0.0) for eid in entities),
                    default=0.0,
                ),
            })
        return facts_out[:k]

    def raptor_query(
        self,
        query: str,
        k: int = 5,
    ) -> list[dict]:
        """Retrieve summaries from the RAPTOR tree.

        Returns an empty list until a RAPTOR tree is built and attached.
        """
        # RAPTOR tree integration deferred — needs embedding pipeline
        return []

    def get_open_promises(
        self,
        overdue: bool = False,
    ) -> list[dict]:
        """Return open narrative promises from the facts table.

        A promise is a fact with ``narrative_function`` in
        ('foreshadowing', 'promise') whose ``truth_value_final`` is NULL
        (i.e., not yet resolved).

        When *overdue* is True, only returns promises seeded more than
        20 chapters ago (based on ``valid_from_chapter``).
        """
        sql = (
            "SELECT fact_id, text, importance, narrative_function, "
            "valid_from_chapter, seeded_scene "
            "FROM facts "
            "WHERE narrative_function IN ('foreshadowing', 'promise') "
            "AND truth_value_final IS NULL"
        )
        params: list = []

        if overdue:
            # Find the highest chapter number in the DB as proxy for "current"
            row = self._conn.execute(
                "SELECT MAX(valid_from_chapter) FROM facts"
            ).fetchone()
            current_ch = row[0] if row and row[0] is not None else 0
            threshold = max(current_ch - 20, 0)
            sql += " AND valid_from_chapter IS NOT NULL AND valid_from_chapter <= ?"
            params.append(threshold)

        sql += " ORDER BY importance DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "fact_id": r[0],
                "text": r[1],
                "importance": r[2],
                "narrative_function": r[3],
                "valid_from_chapter": r[4],
                "seeded_scene": r[5],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # igraph construction
    # ------------------------------------------------------------------

    def build_igraph(
        self,
        chapter_number: int | None = None,
        access_tier: int | None = None,
    ) -> ig.Graph:
        """Build an igraph.Graph from stored edges.

        Uses TupleList for stable entity-to-index mapping.  Entity names
        are stored as the ``name`` vertex attribute.
        """
        edges = self.get_edges(
            chapter_number=chapter_number,
            access_tier=access_tier,
        )
        if not edges:
            return ig.Graph(directed=False)

        tuple_list = [
            (e["source"], e["target"], e["weight"]) for e in edges
        ]
        graph = ig.Graph.TupleList(tuple_list, directed=False, weights=True)

        # Store relation types and access tiers as edge attributes
        edge_attrs: dict[str, list] = {
            "relation_type": [],
            "access_tier": [],
            "temporal_scope": [],
        }
        for e in edges:
            edge_attrs["relation_type"].append(e["relation_type"])
            edge_attrs["access_tier"].append(e["access_tier"])
            edge_attrs["temporal_scope"].append(e["temporal_scope"])

        for attr, values in edge_attrs.items():
            graph.es[attr] = values

        return graph

    # ------------------------------------------------------------------
    # Epistemic access
    # ------------------------------------------------------------------

    def get_epistemic_access(
        self,
        character: str,
        chapter: int,
        knowledge_level: int = 0,
    ) -> dict:
        """Return what a character can and cannot know at a given chapter.

        Returns a dict with ``accessible_facts`` and ``accessible_entities``.
        """
        accessible_facts = self.query_facts(
            chapter_number=chapter,
            access_tier=knowledge_level,
            character_id=character,
        )
        accessible_entities = self.query_entities(access_tier=knowledge_level)
        return {
            "character": character,
            "chapter": chapter,
            "accessible_facts": accessible_facts,
            "accessible_entities": accessible_entities,
        }

    # ------------------------------------------------------------------
    # Community storage (Leiden)
    # ------------------------------------------------------------------

    def store_communities(self, communities: Sequence[Any]) -> None:
        """Replace stored communities with new detection results.

        Clears the existing communities table and writes the new set.
        Each community has ``community_id``, ``entities``, ``resolution``,
        and optionally ``summary``.
        """
        self._conn.execute("DELETE FROM communities")
        for c in communities:
            cid = c.community_id if hasattr(c, "community_id") else c.get("community_id", 0)
            entities = c.entities if hasattr(c, "entities") else c.get("entities", [])
            res = c.resolution if hasattr(c, "resolution") else c.get("resolution", 1.0)
            summary = c.summary if hasattr(c, "summary") else c.get("summary", "")
            for eid in entities:
                self._conn.execute(
                    "INSERT OR REPLACE INTO communities "
                    "(community_id, entity_id, resolution, summary) "
                    "VALUES (?, ?, ?, ?)",
                    (cid, eid, res, summary),
                )
        self._conn.commit()

    def get_communities(self) -> list[dict[str, Any]]:
        """Return all communities as a list of dicts."""
        rows = self._conn.execute(
            "SELECT community_id, GROUP_CONCAT(entity_id) as entities, "
            "resolution, summary FROM communities "
            "GROUP BY community_id ORDER BY community_id"
        ).fetchall()
        return [
            {
                "community_id": r["community_id"],
                "entities": r["entities"].split(",") if r["entities"] else [],
                "resolution": r["resolution"],
                "summary": r["summary"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()
