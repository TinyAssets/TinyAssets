"""Tests for `_query_world_db` source-of-truth routing.

Context: Task #18 surfaced a user-visible bug where querying a universe
for 'characters' reported "Table 'entities' not found in world-state DB".
Root cause: `_query_world_db` was pointing `characters -> entities` inside
`story.db`, but `entities` lives in `knowledge.db`, and the real character
data is in `story.db::character_states`.

The routing consults story.db and admitted canonical knowledge records in
priority order. First table with rows wins; an empty reviewed table still
returns an empty result instead of a missing-table diagnostic.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tinyassets.api.universe as us


@pytest.fixture
def universe_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    return base


def _make_table(
    db_path: Path, table: str, columns: list[str], rows: list[tuple],
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        col_defs = ", ".join(f"{c} TEXT" for c in columns)
        conn.execute(f"CREATE TABLE {table} ({col_defs})")
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})", rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_characters_routes_to_character_states_in_story_db(
    universe_base: Path,
) -> None:
    """Regression guard for Task #18 — the exact symptom a user reported."""
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(
        udir / "story.db",
        "character_states",
        ["name", "state"],
        [("Kaela", "tense"), ("Rin", "calm")],
    )

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="characters",
    ))
    assert out["count"] == 2
    assert out["source"] == "story.db::character_states"
    assert {r["name"] for r in out["results"]} == {"Kaela", "Rin"}


def test_facts_routes_to_extracted_facts_in_story_db(
    universe_base: Path,
) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(
        udir / "story.db",
        "extracted_facts",
        ["fact_text"],
        [("The tower is bone.",), ("The sea is salt.",)],
    )

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="facts",
    ))
    assert out["count"] == 2
    assert out["source"] == "story.db::extracted_facts"


def test_facts_falls_back_to_canonical_knowledge_when_story_db_empty(
    universe_base: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If commit-pipeline extracted_facts exists but is empty AND the KG
    facts table has rows, we prefer the KG data. This is the reason for
    ordered candidate lists — first candidate with rows wins.
    """
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(udir / "story.db", "extracted_facts", ["fact_text"], [])
    monkeypatch.setattr(us, "_canonical_world_rows", lambda uid, table: [
        {"text": "KG fact 1"}, {"text": "KG fact 2"},
    ])

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="facts",
    ))
    assert out["count"] == 2
    assert out["source"] == "knowledge.db::facts"


def test_characters_falls_back_to_knowledge_entities_when_story_empty(
    universe_base: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(udir / "story.db", "character_states", ["name"], [])
    monkeypatch.setattr(us, "_canonical_world_rows", lambda uid, table: [
        {"name": "Kaela", "type": "character"},
    ])

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="characters",
    ))
    assert out["count"] == 1
    assert out["source"] == "knowledge.db::entities"


def test_empty_table_returns_empty_results_not_missing_error(
    universe_base: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the table exists in the primary store but is empty AND no
    fallback has data, we return zero rows with a note — never the old
    'Table X not found in world-state DB' message.
    """
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(udir / "story.db", "character_states", ["name"], [])
    monkeypatch.setattr(us, "_canonical_world_rows", lambda uid, table: [])

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="characters",
    ))
    assert out["count"] == 0
    assert out["source"] == "story.db::character_states"
    assert "not found" not in (out.get("note") or "")


def test_missing_story_db_uses_empty_canonical_table(
    universe_base: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewed canonical table exists even when story.db does not."""
    udir = universe_base / "u"
    udir.mkdir()
    monkeypatch.setattr(us, "_canonical_world_rows", lambda uid, table: [])

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="characters",
    ))
    assert out["count"] == 0
    assert out["source"] == "knowledge.db::entities"
    assert "no rows yet" in out["note"]


def test_timeline_reports_no_store_yet(universe_base: Path) -> None:
    """timeline has no backing table anywhere in the current schema.
    The previous code claimed 'Table timeline not found' which framed
    it as a bug. It is not — it's just unimplemented. Surface it that way.
    """
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(udir / "story.db", "character_states", ["name"], [])

    out = json.loads(us._action_query_world(
        universe_id="u", query_type="timeline",
    ))
    assert out["count"] == 0
    assert "No store for query_type='timeline' yet" in out["note"]


def test_unknown_query_type_defaults_to_facts(universe_base: Path) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(
        udir / "story.db", "extracted_facts",
        ["fact_text"], [("a fact",)],
    )
    out = json.loads(us._action_query_world(
        universe_id="u", query_type="nonsense",
    ))
    assert out["count"] == 1
    assert out["source"] == "story.db::extracted_facts"


def test_filter_text_still_works(universe_base: Path) -> None:
    udir = universe_base / "u"
    udir.mkdir()
    _make_table(
        udir / "story.db", "character_states",
        ["name", "notes"],
        [("Kaela", "the tense one"), ("Rin", "the calm one")],
    )
    out = json.loads(us._action_query_world(
        universe_id="u", query_type="characters", filter_text="calm",
    ))
    assert out["count"] == 1
    assert out["results"][0]["name"] == "Rin"
