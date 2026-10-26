"""C12 K1: owner files are the knowledge authority."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from tinyassets.daemon_brain import initialize_daemon_brain
from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
from tinyassets.knowledge_authority import (
    ConversionRefused,
    convert_owner_knowledge,
    parse_record,
    read_record,
    retired_store_inventory,
)
from tinyassets.memory.episodic import EpisodicMemory
from tinyassets.memory.temporal import TemporalFactStore
from tinyassets.universe_bundle import seed_okf_bundle

pytestmark = [
    pytest.mark.verifies(
        "c12-knowledge-files-export:Owner knowledge files are authoritative",
    ),
    pytest.mark.verifies(
        "c12-knowledge-files-export:Selected memory uses the same file authority",
    ),
]

_REPO = Path(__file__).resolve().parents[1]
_CREATE = re.compile(
    r"CREATE\s+(?:VIRTUAL\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_RETIRED_SOURCES = (
    "tinyassets/knowledge/knowledge_graph.py",
    "tinyassets/memory/episodic.py",
    "tinyassets/memory/project.py",
    "tinyassets/memory/promises.py",
    "tinyassets/memory/temporal.py",
    "tinyassets/memory/versioning.py",
    "tinyassets/daemon_brain.py",
)
_SHARED = (
    "tinyassets/owner_router.py",
    "tinyassets/broker/server.py",
    "tinyassets/broker/egress.py",
    "tinyassets/broker/next_wake.py",
    "tinyassets/control_plane/next_wake.py",
)


def _created_tables() -> set[str]:
    names: set[str] = set()
    for rel in _RETIRED_SOURCES:
        names.update(_CREATE.findall((_REPO / rel).read_text(encoding="utf-8")))
    return names


def test_k1_inventory_names_every_retired_store_and_skips_shared_services():
    """Every retired table has a file, operational store, or rebuild source."""
    inventory = retired_store_inventory()
    missing = sorted(
        table for table in _created_tables()
        if not any(key.endswith(":" + table) for key in inventory)
    )
    assert missing == []
    assert "daemon_brain.db:daemon_brain_entries_fts*" in inventory
    consumers = {row["consumer"] for row in inventory.values()}
    assert not consumers & set(_SHARED)
    assert inventory["file:notes.json"]["consumer"] == "tinyassets/notes.py"
    assert inventory["file:wiki"]["consumer"] == "tinyassets/api/wiki.py"
    assert inventory["episodic.db:scene_summaries"]["kind"] == "operational"
    assert inventory["episodic.db:scene_summaries"]["disposition"] == (
        "tinyassets/memory/episodic.py"
    )
    assert inventory["knowledge.db:facts"]["kind"] == "file"
    assert inventory["knowledge.db:communities"]["kind"] == "derived"
    for rel in _SHARED:
        source = (_REPO / rel).read_text(encoding="utf-8")
        assert "knowledge_authority" not in source


def test_c12_file_edit_wins(tmp_path: Path):
    """An owner edit is what a later read returns, including after the database changes."""
    database = tmp_path / "episodic.db"
    memory = EpisodicMemory(database, universe_id="owner-1")
    memory._conn.execute(
        "INSERT INTO episodic_facts (universe_id, fact_id, entity, content) "
        "VALUES (?, ?, ?, ?)",
        ("owner-1", "fact-1", "hero", "from the database"),
    )
    memory._conn.commit()
    memory.close()

    report = convert_owner_knowledge(
        tmp_path, snapshot_id="snap-1", writes_stopped=True,
    )
    source = report.source("episodic.db:episodic_facts")
    assert source.input_count == 1
    assert source.output_count == 1
    assert source.hashes[source.ids[0]]
    assert read_record(tmp_path, source.source, source.ids[0]) == "from the database"

    fact_file = next((tmp_path / "knowledge" / "memory" / "facts").glob("*.md"))
    text = fact_file.read_text(encoding="utf-8")
    head, sep, _body = text.rpartition("\n---\n")
    fact_file.write_text(head + sep + "edited by the owner\n", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE episodic_facts SET content = ? WHERE fact_id = ?",
            ("from the database changed", "fact-1"),
        )

    assert read_record(tmp_path, source.source, source.ids[0]) == "edited by the owner"
    knowledge_index = (tmp_path / "knowledge" / "index.md").read_text(encoding="utf-8")
    root_index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "okf_version" in knowledge_index
    assert 'authority": "files"' in knowledge_index or 'authority: "files"' in knowledge_index
    assert "okf_version" in root_index


def test_k1_conversion_counts_every_lifecycle_and_refuses_loss(tmp_path: Path):
    """Candidate, rejected, superseded and file-less graph facts convert, or cutover refuses."""
    graph = KnowledgeGraph(tmp_path / "knowledge.db")
    graph._conn.execute(
        "INSERT INTO facts (fact_id, text) VALUES (?, ?)",
        ("graph-fact", "graph fact with no file source"),
    )
    graph._conn.commit()
    graph._conn.close()

    memory = EpisodicMemory(tmp_path / "episodic.db", universe_id="owner-1")
    memory._conn.execute(
        "INSERT INTO scene_summaries (universe_id, summary, book_number, chapter_number, "
        "scene_number) VALUES (?, ?, 1, 1, 1)",
        ("owner-1", "operational summary stays in episodic"),
    )
    memory._conn.commit()
    memory.close()

    brain = sqlite3.connect(tmp_path / "daemon_brain.db")
    initialize_daemon_brain(brain)
    for entry_id, state in (
        ("entry-candidate", "candidate"),
        ("entry-rejected", "rejected"),
        ("entry-superseded", "superseded"),
    ):
        brain.execute(
            """
            INSERT INTO daemon_brain_entries (
                entry_id, daemon_id, memory_kind, content, content_fingerprint,
                source_type, source_id, reliability, temporal_bounds_json,
                language_type, confidence, importance, sensitivity_tier, visibility,
                promotion_state, metadata_json, created_at, updated_at
            ) VALUES (?, 'daemon-1', 'note', ?, ?, 'manual', 'manual', 'stated',
                      '{}', 'literal', 0.5, 0.5, 'normal', 'host_private', ?, '{}',
                      '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (entry_id, f"body {state}", f"fp-{entry_id}", state),
        )
    brain.commit()
    brain.close()

    report = convert_owner_knowledge(
        tmp_path, snapshot_id="snap-lifecycle", writes_stopped=True,
    )
    for source in report.sources:
        assert source.kind != "unclassified"
        assert source.unconvertible == ()
        assert source.input_count == source.output_count
        assert set(source.hashes) == set(source.ids)
    facts = report.source("knowledge.db:facts")
    assert facts.input_count == 1
    assert read_record(tmp_path, facts.source, facts.ids[0]) == (
        "graph fact with no file source"
    )
    summaries = report.source("episodic.db:scene_summaries")
    assert summaries.kind == "operational"
    assert summaries.disposition == "tinyassets/memory/episodic.py"
    assert summaries.input_count == 1
    brain_rows = report.source("daemon_brain.db:daemon_brain_entries")
    assert brain_rows.input_count == 3
    bodies = {
        read_record(tmp_path, brain_rows.source, entry_id) for entry_id in brain_rows.ids
    }
    assert bodies == {"body candidate", "body rejected", "body superseded"}
    for path in (tmp_path / "knowledge" / "brain").glob("*.md"):
        meta, _body = parse_record(path.read_text(encoding="utf-8"))
        assert meta["status"] in {"candidate", "rejected", "superseded"}
    knowledge_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "knowledge").rglob("*.md")
        if "_fingerprints" not in path.as_posix()
    )
    assert "operational summary stays in episodic" not in knowledge_text

    refused = tmp_path / "refused"
    refused.mkdir()
    bad = sqlite3.connect(refused / "knowledge.db")
    bad.execute("CREATE TABLE mystery (id TEXT)")
    bad.execute("INSERT INTO mystery (id) VALUES ('lost')")
    bad.commit()
    bad.close()
    with pytest.raises(ConversionRefused) as unclassified:
        convert_owner_knowledge(refused, snapshot_id="snap-2", writes_stopped=True)
    assert "unclassified" in str(unclassified.value)
    assert not (refused / "knowledge" / "index.md").exists()

    blank = tmp_path / "blank-id"
    blank.mkdir()
    empty = KnowledgeGraph(blank / "knowledge.db")
    empty._conn.execute("INSERT INTO entities (entity_id) VALUES ('')")
    empty._conn.commit()
    empty._conn.close()
    with pytest.raises(ConversionRefused) as lost:
        convert_owner_knowledge(blank, snapshot_id="snap-3", writes_stopped=True)
    assert lost.value.report.reasons
    assert not (blank / "knowledge" / "index.md").exists()

    stopped = tmp_path / "still-writing"
    stopped.mkdir()
    with pytest.raises(ConversionRefused):
        convert_owner_knowledge(stopped, snapshot_id="snap-4", writes_stopped=False)
    assert not (stopped / "knowledge" / "index.md").exists()


def test_k1_canonical_writers_are_gone():
    """The named SQL knowledge writers are gone. Operational stores remain."""
    graph = (_REPO / "tinyassets/knowledge/knowledge_graph.py").read_text(encoding="utf-8")
    episodic = (_REPO / "tinyassets/memory/episodic.py").read_text(encoding="utf-8")
    project = (_REPO / "tinyassets/memory/project.py").read_text(encoding="utf-8")
    temporal = (_REPO / "tinyassets/memory/temporal.py").read_text(encoding="utf-8")
    brain = (_REPO / "tinyassets/daemon_brain.py").read_text(encoding="utf-8")
    bundle = (_REPO / "tinyassets/universe_bundle.py").read_text(encoding="utf-8")
    assert "def add_entity" not in graph
    assert "def add_edge" not in graph
    assert "def add_facts" not in graph
    assert "def store_fact" not in episodic
    assert "def mark_promoted" not in episodic
    assert "def store_summary" in episodic
    assert "def store_observation" in episodic
    assert "def store_reflection" in episodic
    assert "def project_memory_set" not in project
    assert "def assert_fact" not in temporal
    assert "def supersede" not in temporal
    assert "def invalidate" not in temporal
    assert not re.search(r"INSERT INTO daemon_brain_entries\b", brain)
    assert "UPDATE daemon_brain_entries" not in brain
    assert "Creation only, not a turn-time default" not in bundle
    assert not hasattr(KnowledgeGraph, "add_entity")
    assert not hasattr(EpisodicMemory, "store_fact")
    assert not hasattr(EpisodicMemory, "mark_promoted")
    assert not hasattr(TemporalFactStore, "assert_fact")
    assert not hasattr(TemporalFactStore, "supersede")
    assert not hasattr(TemporalFactStore, "invalidate")


def test_seed_keeps_an_owner_edit(tmp_path: Path):
    """Repeating the OKF seed does not replace a file the owner edited."""
    seed_okf_bundle(tmp_path)
    index = tmp_path / "index.md"
    edited = index.read_text(encoding="utf-8") + "\nowner edit\n"
    index.write_text(edited, encoding="utf-8")
    seed_okf_bundle(tmp_path)
    kept = index.read_text(encoding="utf-8")
    assert "owner edit" in kept
    assert "okf_version" in kept
