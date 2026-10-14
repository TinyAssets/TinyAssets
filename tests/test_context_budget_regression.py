"""Regression gate for the 48535-token context-bundle overflow (BUG-024).

Prod observed a constant ~48535-token CoreMemory payload overflowing
the 15k-token budget on every scene. Two root causes (task #3):

1. Unbounded SQL: `get_all_characters` / `get_active_promises` returned
   every row. With many characters carrying knowledge_facts JSON blobs,
   the payload grew without bound.
2. Duplicate storage: `_assemble_orient` loaded characters directly into
   CoreMemory AND embedded the same list inside world_state. Every
   character was counted twice by `core.estimated_tokens()`.

Fix (task #5):
- `_build_world_state_snapshot` no longer embeds `characters` (dedup).
- `get_all_characters` + `get_active_promises` enforce LIMIT 25
  (defense-in-depth, so growth in other universes can't regress).

This file locks both in. If either regresses, CoreMemory balloons again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from domains.fantasy_daemon.phases.orient import _build_world_state_snapshot
from domains.fantasy_daemon.phases.world_state_db import (
    _MAX_WORLD_STATE_ENTITIES,
    add_promise,
    connect,
    get_active_promises,
    get_all_characters,
    init_db,
    upsert_character,
)
from tinyassets.memory.manager import MAX_CONTEXT_TOKENS, MemoryManager


class TestBuildWorldStateSnapshotDedup:
    def test_characters_not_embedded_in_snapshot(self):
        snapshot = _build_world_state_snapshot(
            chapter_number=3,
            scene_number=2,
            characters=[{"id": "ryn", "name": "Ryn", "bio": "x" * 10_000}],
            active_promises=[{"text": "open the gate"}],
            recent_scenes=[{"scene_id": "s1", "summary": "short"}],
            chapter_avg_words=900,
        )

        assert "characters" not in snapshot
        assert snapshot["active_promises"] == [{"text": "open the gate"}]
        assert snapshot["chapter_number"] == 3


class TestWorldStateDBLimits:
    @pytest.fixture
    def tmp_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "story.db"
        init_db(str(db_path))
        return db_path

    def test_get_all_characters_respects_limit(self, tmp_db: Path):
        with connect(str(tmp_db)) as conn:
            for i in range(_MAX_WORLD_STATE_ENTITIES + 10):
                upsert_character(
                    conn,
                    character_id=f"char-{i:03d}",
                    name=f"Char {i}",
                    last_updated_scene=f"b1c1s{i}",
                )
            chars = get_all_characters(conn)

        assert len(chars) == _MAX_WORLD_STATE_ENTITIES

    def test_get_active_promises_respects_limit(self, tmp_db: Path):
        with connect(str(tmp_db)) as conn:
            for i in range(_MAX_WORLD_STATE_ENTITIES + 10):
                add_promise(
                    conn,
                    promise_id=f"p-{i:03d}",
                    text=f"Promise {i}",
                    created_scene="b1c1s1",
                    importance=0.5 + (i * 0.001),
                )
            active = get_active_promises(conn)

        assert len(active) == _MAX_WORLD_STATE_ENTITIES

    def test_get_active_promises_top_n_by_importance(self, tmp_db: Path):
        """LIMIT 25 + ORDER BY importance DESC keeps the most important."""
        with connect(str(tmp_db)) as conn:
            add_promise(
                conn, promise_id="low", text="low",
                created_scene="b1c1s1", importance=0.1,
            )
            add_promise(
                conn, promise_id="high", text="high",
                created_scene="b1c1s1", importance=0.99,
            )
            for i in range(_MAX_WORLD_STATE_ENTITIES + 5):
                add_promise(
                    conn, promise_id=f"mid-{i}", text=f"mid-{i}",
                    created_scene="b1c1s1", importance=0.5,
                )
            active = get_active_promises(conn)

        ids = [p["id"] for p in active]
        assert "high" in ids
        assert "low" not in ids


class TestBundleUnderBudgetWithHeavyUniverse:
    """End-to-end gate: even a universe with many large characters
    and promises produces a bundle whose CoreMemory fits the budget.

    Before the fix, ~30 characters with ~2 KB of knowledge_facts each
    would overflow budget (measured 48535 tokens in prod). After the
    fix: LIMIT caps rows, dedup halves the footprint.
    """

    def test_characters_not_duplicated_in_core(self, canonical_source, tmp_path: Path):
        """Regression gate for the 2x-duplication root cause.

        Core should hold each character once (under `characters`), not
        once under `characters` AND once nested inside `world_state`.
        """
        big_fact = "x" * 2000
        state_characters = [
            {
                "id": f"char-{i:03d}",
                "name": f"Character {i}",
                "location": "unknown",
                "emotional_state": "neutral",
                "knowledge_facts": [big_fact, big_fact],
                "last_updated_scene": f"b1c1s{i}",
            }
            for i in range(50)
        ]
        # Note: world_state intentionally kept tiny so this test isolates
        # the character-dedup gate from promise-payload size.
        world_state = _build_world_state_snapshot(
            chapter_number=1,
            scene_number=1,
            characters=state_characters,
            active_promises=[{"text": "tiny", "importance": 0.5}],
            recent_scenes=[],
            chapter_avg_words=900,
        )

        mgr = MemoryManager(
            universe_id="heavy",
            source=canonical_source("episodic", key=":memory:", namespaces=["heavy", "heavy-db"]),
        )
        try:
            state = {
                "chapter_number": 1,
                "orient_result": {
                    "characters": state_characters,
                    "world_state": world_state,
                    "warnings": [],
                },
            }
            mgr.assemble_context("orient", state)
            tokens = mgr.core.estimated_tokens()
        finally:
            mgr.close()

        chars_only_tokens = sum(
            len(str(c)) for c in state_characters
        ) // 4
        # Fix target: core ≈ chars_only + tiny world_state overhead.
        # Pre-fix (duplication): core would be ≈ 2 × chars_only.
        # We allow +5% slack for world_state + dict stringification noise.
        assert tokens < int(chars_only_tokens * 1.1), (
            f"Core tokens ({tokens}) should be ≈ characters-only "
            f"(~{chars_only_tokens}); if much larger, characters are "
            "being duplicated somewhere."
        )

    def test_bundle_under_budget_after_db_limit(self, canonical_source, tmp_path: Path):
        """End-to-end: universe with far more rows than the limit still
        produces a bundle whose CoreMemory fits the 15k-token budget.

        Uses realistic per-row sizes — characters carry ~10 short fact IDs
        (not multi-KB blobs; those live in extracted_facts). Promises
        carry a 200-char text.
        """
        db_path = tmp_path / "story.db"
        init_db(str(db_path))

        with connect(str(db_path)) as conn:
            for i in range(60):
                upsert_character(
                    conn,
                    character_id=f"char-{i:03d}",
                    name=f"Character {i}",
                    location=f"location-{i % 5}",
                    emotional_state="pensive",
                    knowledge_facts=[f"fact-{i}-{j:04d}" for j in range(10)],
                    last_updated_scene=f"b1c1s{i}",
                )
            for i in range(60):
                add_promise(
                    conn,
                    promise_id=f"p-{i}",
                    text=(
                        f"Promise {i}: the protagonist must reach the gate "
                        "before the stormglass cracks under the rising tide."
                    ),
                    created_scene="b1c1s1",
                    importance=0.5,
                )
            characters = get_all_characters(conn)
            promises = get_active_promises(conn)

        assert len(characters) == _MAX_WORLD_STATE_ENTITIES
        assert len(promises) == _MAX_WORLD_STATE_ENTITIES

        world_state = _build_world_state_snapshot(
            chapter_number=1,
            scene_number=1,
            characters=characters,
            active_promises=promises,
            recent_scenes=[],
            chapter_avg_words=900,
        )

        mgr = MemoryManager(
            universe_id="heavy-db",
            source=canonical_source("episodic", key=":memory:", namespaces=["heavy", "heavy-db"]),
        )
        try:
            state = {
                "chapter_number": 1,
                "orient_result": {
                    "characters": characters,
                    "world_state": world_state,
                    "warnings": [],
                },
            }
            mgr.assemble_context("orient", state)
            tokens = mgr.core.estimated_tokens()
        finally:
            mgr.close()

        assert tokens < MAX_CONTEXT_TOKENS, (
            f"Core tokens ({tokens}) exceeds budget ({MAX_CONTEXT_TOKENS}); "
            "BUG-024 has regressed."
        )
