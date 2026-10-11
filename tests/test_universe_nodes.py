"""Tests for upgraded universe-level nodes.

Covers:
- worldbuild: promotion gates, fact counting, KG re-index, canon generation,
  signal-driven worldbuilding
- select_task: user overrides, stuck detection, stale world state,
  creative signal routing
- diagnose: revert patterns, recurring failures, recovery suggestions
- universe_cycle: health metrics, memory cleanup, queue management
- reflect: reflexion engine, canon quality review, signal-driven review
- commit: worldbuild signal generation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from domains.fantasy_daemon.phases.diagnose import diagnose
from domains.fantasy_daemon.phases.reflect import (
    _MAX_REWRITES_PER_CYCLE,
    _collect_reviewable_canon,
    _evaluate_canon,
    _extract_signal_topics,
    _parse_issues,
    _recently_reviewed,
    _stamp_reviewed,
    reflect,
)
from domains.fantasy_daemon.phases.select_task import select_task
from domains.fantasy_daemon.phases.universe_cycle import universe_cycle
from domains.fantasy_daemon.phases.worldbuild import (
    _MAX_DOCS_PER_CYCLE,
    WORLDBUILD_TOPICS,
    _handle_contradiction,
    _handle_expansion,
    _handle_synthesize_source,
    _identify_gaps,
    _maybe_generate_premise,
    _read_direction_notes,
    _read_premise,
    _record_synthesis_failure,
    _scan_existing_canon,
    _trigger_kg_reindex,
    _write_canon_file,
    _write_canon_marker,
    worldbuild,
)
from tests.support.fantasy_model import answer as fantasy_answer
from tinyassets.notes import add_note, update_note_status

# -----------------------------------------------------------------------
# worldbuild tests
# -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fantasy_model_transport(monkeypatch):
    from domains.fantasy_daemon.phases import _provider_stub
    from tinyassets.providers import model_call

    monkeypatch.setattr(_provider_stub, "text_sync", fantasy_answer)
    monkeypatch.setattr(model_call, "text_sync", fantasy_answer)


class TestWorldbuild:
    """Tests for the worldbuild node."""

    def test_increments_version_on_real_progress(self):
        """Phase 6 Task D: version bumps only when work actually happened.

        Injects a MemoryManager that promotes a fact so ``promoted_count``
        > 0, satisfying the made_progress guard.
        """
        mgr = MagicMock()
        promotion_result = MagicMock()
        promotion_result.promoted_facts = [{"id": "f1"}]
        promotion_result.promoted_style_rules = []
        promotion_result.asp_rule_candidates = []
        mgr.run_promotion_gates.return_value = promotion_result

        with patch("tinyassets.runtime_singletons.memory_manager", mgr):
            state = {"world_state_version": 5}
            result = worldbuild(state)
        assert result["world_state_version"] == 6

    def test_version_stays_on_no_op_cycle(self):
        """Phase 6 Task D: no signals, no generated files, no promoted
        facts → version must NOT bump. Unconditional bumping previously
        inflated telemetry and masked stuck state (STATUS 2026-04-14)."""
        state = {"world_state_version": 5}
        result = worldbuild(state)
        assert result["world_state_version"] == 5

    def test_version_stays_from_zero_on_no_op(self):
        state = {}
        result = worldbuild(state)
        assert result["world_state_version"] == 0

    def test_returns_quality_trace(self):
        state = {"world_state_version": 0}
        result = worldbuild(state)
        assert len(result["quality_trace"]) == 1
        trace = result["quality_trace"][0]
        assert trace["node"] == "worldbuild"
        assert trace["action"] == "worldbuild_real"

    def test_counts_canon_facts_from_db(self, tmp_story_db):
        """High-confidence facts in the DB should be counted."""
        from domains.fantasy_daemon.phases.world_state_db import connect, init_db, store_fact

        init_db(tmp_story_db)
        with connect(tmp_story_db) as conn:
            store_fact(conn, fact_id="f1", text="A fact", confidence=0.9,
                       scene_id="s1")
            store_fact(conn, fact_id="f2", text="Another", confidence=0.9,
                       scene_id="s1")
            store_fact(conn, fact_id="f3", text="Low conf", confidence=0.3,
                       scene_id="s1")

        state = {"world_state_version": 0, "_db_path": tmp_story_db}
        result = worldbuild(state)
        # Only f1 and f2 have confidence >= 0.8
        assert result["canon_facts_count"] == 2

    def test_runs_promotion_gates_with_manager(self):
        mgr = MagicMock()
        promotion_result = MagicMock()
        promotion_result.promoted_facts = [{"id": "f1"}]
        promotion_result.promoted_style_rules = []
        promotion_result.asp_rule_candidates = []
        mgr.run_promotion_gates.return_value = promotion_result

        with patch("tinyassets.runtime_singletons.memory_manager", mgr):
            state = {"world_state_version": 0}
            result = worldbuild(state)

        mgr.run_promotion_gates.assert_called_once()
        assert result["quality_trace"][0]["promoted_facts"] == 1

    def test_graceful_when_no_manager(self):
        state = {"world_state_version": 0}
        result = worldbuild(state)
        # Should not crash, promoted_facts should be 0
        assert result["quality_trace"][0]["promoted_facts"] == 0

    def test_graceful_when_manager_fails(self):
        from tinyassets import runtime_singletons as runtime

        mgr = MagicMock()
        mgr.run_promotion_gates.side_effect = RuntimeError("oops")

        runtime.memory_manager = mgr
        try:
            state = {"world_state_version": 0}
            result = worldbuild(state)
        finally:
            runtime.memory_manager = None
        # Should not crash. No progress happened (mgr failed, no signals,
        # no generated files), so version stays (Phase 6 Task D).
        assert result["world_state_version"] == 0
        assert result["quality_trace"][0]["promoted_facts"] == 0

    def test_read_direction_notes_returns_active_user_notes(self, tmp_path):
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        add_note(
            universe_dir,
            source="user",
            text="Keep the magic costly.",
            category="direction",
        )
        text = _read_direction_notes(universe_dir)
        assert "Keep the magic costly." in text

    def test_read_direction_notes_skips_dismissed_and_acted_on(self, tmp_path):
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        active = add_note(
            universe_dir,
            source="user",
            text="Keep the river city central.",
            category="direction",
        )
        dismissed = add_note(
            universe_dir,
            source="user",
            text="Old discarded direction.",
            category="direction",
        )
        acted_on = add_note(
            universe_dir,
            source="user",
            text="Resolved direction.",
            category="direction",
        )
        assert update_note_status(universe_dir, dismissed.id, "dismissed")
        assert update_note_status(universe_dir, acted_on.id, "acted_on")

        text = _read_direction_notes(universe_dir)
        assert active.text in text
        assert dismissed.text not in text
        assert acted_on.text not in text

    def test_noop_cycle_increments_streak(self):
        """A cycle with no signals and no generated files bumps the streak."""
        state = {"world_state_version": 0, "health": {}}
        result = worldbuild(state)
        assert result["health"]["worldbuild_noop_streak"] == 1
        assert result["health"].get("stopped") is not True
        assert result["quality_trace"][0]["noop_streak"] == 1
        assert result["quality_trace"][0]["self_paused"] is False

    def test_noop_streak_self_pauses_at_threshold(self):
        """After 3 consecutive no-op cycles, worldbuild sets health.stopped."""
        state = {
            "world_state_version": 0,
            "health": {"worldbuild_noop_streak": 2},
        }
        result = worldbuild(state)
        assert result["health"]["worldbuild_noop_streak"] == 3
        assert result["health"]["stopped"] is True
        assert result["health"]["idle_reason"] == "worldbuild_stuck"
        assert result["quality_trace"][0]["self_paused"] is True

    def test_streak_resets_when_files_generated(self, tmp_path):
        """A productive cycle (generated canon files) resets the streak to 0."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("Existing lore.", encoding="utf-8")

        state = {
            "world_state_version": 1,  # past bootstrap auto-premise
            "_universe_path": str(universe_dir),
            "_db_path": str(tmp_path / "story.db"),
            "health": {"worldbuild_noop_streak": 2},
            "premise_kernel": "An existing premise.",
        }
        # Force _generate_canon_documents to return a non-empty list so the
        # cycle counts as productive. Patching the function directly is the
        # cleanest way since it has many branches.
        with patch(
            "domains.fantasy_daemon.phases.worldbuild._generate_canon_documents",
            return_value=["canon/new_topic.md"],
        ):
            result = worldbuild(state)
        assert result["health"]["worldbuild_noop_streak"] == 0
        assert result["health"].get("stopped") is not True
        assert result["quality_trace"][0]["self_paused"] is False


# -----------------------------------------------------------------------
# worldbuild auto-premise tests
# -----------------------------------------------------------------------


class TestWorldbuildAutoPremise:
    """Tests for auto-premise generation from canon files."""

    def test_generates_premise_from_canon(self, tmp_path):
        """When PROGRAM.md is missing and canon has files, generate premise."""
        from domains.fantasy_daemon.phases.worldbuild import _maybe_generate_premise

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("A realm of shattered glass.", encoding="utf-8")
        (canon_dir / "chars.md").write_text("Ryn, a glass-walker scout.", encoding="utf-8")

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
            "premise_kernel": "",
        }

        with patch(
            "domains.fantasy_daemon.phases._provider_stub.text_sync",
            return_value="A story of Ryn navigating the shattered glass realm.",
        ):
            result = _maybe_generate_premise(state)

        assert result == "A story of Ryn navigating the shattered glass realm."
        assert (universe_dir / "PROGRAM.md").exists()
        assert "Ryn" in (universe_dir / "PROGRAM.md").read_text(encoding="utf-8")
        assert "Ryn" in (universe_dir / "soul.md").read_text(encoding="utf-8")

    def test_skips_when_program_md_exists(self, tmp_path):
        """Should not generate if PROGRAM.md already exists."""
        from domains.fantasy_daemon.phases.worldbuild import _maybe_generate_premise

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        (universe_dir / "PROGRAM.md").write_text("Existing premise.", encoding="utf-8")
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("Some content.", encoding="utf-8")

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
            "premise_kernel": "",
        }
        result = _maybe_generate_premise(state)
        assert result == ""

    def test_skips_when_premise_kernel_set(self, tmp_path):
        """Should not generate if premise_kernel is already in state."""
        from domains.fantasy_daemon.phases.worldbuild import _maybe_generate_premise

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("Some content.", encoding="utf-8")

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
            "premise_kernel": "Already have a premise.",
        }
        result = _maybe_generate_premise(state)
        assert result == ""

    def test_skips_when_version_positive(self, tmp_path):
        """Should only fire on first worldbuild pass (version == 0)."""
        from domains.fantasy_daemon.phases.worldbuild import _maybe_generate_premise

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("Some content.", encoding="utf-8")

        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
            "premise_kernel": "",
        }
        result = _maybe_generate_premise(state)
        assert result == ""

    def test_skips_when_no_canon_files(self, tmp_path):
        """Should not generate if canon/ is empty."""
        from domains.fantasy_daemon.phases.worldbuild import _maybe_generate_premise

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
            "premise_kernel": "",
        }
        result = _maybe_generate_premise(state)
        assert result == ""

    def test_worldbuild_propagates_auto_premise(self, tmp_path):
        """worldbuild() should include premise in return when auto-generated."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("Glass realm lore.", encoding="utf-8")

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
            "premise_kernel": "",
            "workflow_instructions": {},
            "_db_path": str(tmp_path / "story.db"),
        }

        with patch(
            "domains.fantasy_daemon.phases._provider_stub.text_sync",
            return_value="A tale of the glass realm.",
        ):
            result = worldbuild(state)

        assert result.get("premise_kernel") == "A tale of the glass realm."
        assert result["workflow_instructions"]["premise"] == "A tale of the glass realm."
        assert result["quality_trace"][0]["auto_premise"] is True


# -----------------------------------------------------------------------
# worldbuild canon generation tests
# -----------------------------------------------------------------------


class TestWorldbuildCanonGeneration:
    """Tests for creative worldbuilding (canon document generation)."""

    def _make_universe(self, tmp_path: Path, premise: str = "") -> Path:
        """Create a minimal universe directory with optional premise."""
        universe_dir = tmp_path / "test-universe"
        universe_dir.mkdir()
        if premise:
            (universe_dir / "PROGRAM.md").write_text(premise, encoding="utf-8")
        return universe_dir

    def test_generates_canon_files(self, tmp_path):
        """Worldbuild should create canon files when premise exists."""
        universe_dir = self._make_universe(
            tmp_path, "A dark fantasy world with warring kingdoms."
        )
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        canon_dir = universe_dir / "canon"
        assert canon_dir.exists()
        generated = result["quality_trace"][0]["generated_files"]
        assert len(generated) > 0
        assert len(generated) <= _MAX_DOCS_PER_CYCLE
        # Files should actually exist on disk
        for filename in generated:
            assert (canon_dir / filename).exists()
            content = (canon_dir / filename).read_text(encoding="utf-8")
            assert len(content) > 0

    def test_skips_existing_topics(self, tmp_path):
        """Should not regenerate topics that already have canon files."""
        universe_dir = self._make_universe(
            tmp_path, "An epic fantasy premise."
        )
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        # Pre-create the first two topics
        (canon_dir / "characters.md").write_text("# Characters\n\nExisting.", encoding="utf-8")
        (canon_dir / "locations.md").write_text("# Locations\n\nExisting.", encoding="utf-8")

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        # Should not have regenerated characters or locations
        assert "characters.md" not in generated
        assert "locations.md" not in generated
        # Should have generated the next topics in priority order
        if generated:
            assert generated[0] == "factions.md"

    def test_skips_when_no_premise(self, tmp_path):
        """No generation when there is no premise."""
        universe_dir = self._make_universe(tmp_path)  # No premise
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        assert generated == []

    def test_skips_when_no_universe_path(self):
        """No generation when universe_path is missing."""
        state = {"world_state_version": 0}
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        assert generated == []

    def test_falls_back_to_premise_kernel(self, tmp_path):
        """Should use premise_kernel from state if PROGRAM.md is absent."""
        universe_dir = self._make_universe(tmp_path)  # No PROGRAM.md
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
            "premise_kernel": "A story about ancient dragons.",
        }
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        assert len(generated) > 0

    def test_reads_direction_notes_into_prompt(self, tmp_path):
        """Direction notes should be read when present."""
        from tinyassets.notes import add_note

        universe_dir = self._make_universe(
            tmp_path, "Fantasy world premise."
        )
        add_note(
            universe_dir, source="user", text="Focus on the magic system.",
            category="direction",
        )
        result = _read_direction_notes(universe_dir)
        assert "magic system" in result

    def test_no_notes_returns_empty(self, tmp_path):
        """Missing notes should return empty string, not crash."""
        universe_dir = self._make_universe(tmp_path)
        result = _read_direction_notes(universe_dir)
        assert result == ""

    def test_all_topics_covered_skips_generation(self, tmp_path):
        """When all topics exist, nothing should be generated."""
        universe_dir = self._make_universe(
            tmp_path, "A complete world."
        )
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        for topic in WORLDBUILD_TOPICS:
            (canon_dir / f"{topic}.md").write_text(
                f"# {topic}\n\nContent.", encoding="utf-8"
            )

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        assert generated == []

    def test_generates_at_most_max_per_cycle(self, tmp_path):
        """Should never generate more than _MAX_DOCS_PER_CYCLE files."""
        universe_dir = self._make_universe(
            tmp_path, "A world with many gaps."
        )
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        assert len(generated) <= _MAX_DOCS_PER_CYCLE

    def test_graceful_when_provider_fails(self, tmp_path):
        """LLM failure should not crash the node."""
        universe_dir = self._make_universe(
            tmp_path, "A premise for testing failures."
        )
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        with patch(
            "domains.fantasy_daemon.phases.worldbuild._call_for_worldbuild",
            side_effect=RuntimeError("LLM down"),
        ):
            result = worldbuild(state)
        # Node should still complete without crashing.
        # Provider failure = no signals acted, no files, no promotions → version stays (Task D).
        assert result["world_state_version"] == 0
        generated = result["quality_trace"][0]["generated_files"]
        assert generated == []

    def test_scan_existing_canon_normalizes_names(self, tmp_path):
        """Scan should normalize filenames to match topic slugs."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "Magic-System.md").write_text("content", encoding="utf-8")
        (canon_dir / "characters.md").write_text("content", encoding="utf-8")

        existing = _scan_existing_canon(canon_dir)
        assert "magic_system" in existing
        assert "characters" in existing

    def test_scan_empty_canon_dir(self, tmp_path):
        """Empty canon dir returns empty set."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        assert _scan_existing_canon(canon_dir) == set()

    def test_scan_nonexistent_canon_dir(self, tmp_path):
        """Nonexistent canon dir returns empty set."""
        canon_dir = tmp_path / "nonexistent"
        assert _scan_existing_canon(canon_dir) == set()

    def test_identify_gaps_returns_priority_order(self):
        """Gaps should follow WORLDBUILD_TOPICS priority."""
        existing = {"characters", "locations"}
        gaps = _identify_gaps(existing)
        assert gaps[0] == "factions"
        assert "characters" not in gaps
        assert "locations" not in gaps

    def test_identify_gaps_all_covered(self):
        """No gaps when everything is covered."""
        existing = set(WORLDBUILD_TOPICS)
        assert _identify_gaps(existing) == []

    def test_write_canon_file_sanitizes_traversal_filename(self, tmp_path):
        """LLM-derived canon filenames must not escape canon_dir."""
        canon_dir = tmp_path / "canon"

        _write_canon_file(canon_dir, "../../escape.md", "# Escaped")

        assert (canon_dir / "escape.md").read_text(encoding="utf-8") == "# Escaped"
        assert not (tmp_path / "escape.md").exists()

    def test_write_canon_file_rejects_empty_safe_filename(self, tmp_path):
        """A filename that sanitizes to empty is not written."""
        canon_dir = tmp_path / "canon"

        try:
            _write_canon_file(canon_dir, "../..", "# Empty")
        except ValueError as exc:
            assert "non-empty safe slug" in str(exc)
        else:
            raise AssertionError("expected unsafe empty canon filename to fail")

    def test_write_canon_file_rejects_marker_symlink_escape(self, tmp_path):
        """The provenance marker write must stay under canon_dir too."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        try:
            (canon_dir / ".escape.md.reviewed").symlink_to(tmp_path / "marker-outside")
        except (OSError, NotImplementedError) as exc:
            # Windows without the "Create symbolic links" privilege (or other
            # platforms that forbid symlink creation) cannot set up this
            # fixture; the containment check itself is still covered on
            # symlink-capable platforms (CI/Linux).
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        try:
            _write_canon_file(canon_dir, "escape.md", "# Escaped")
        except ValueError as exc:
            assert "canon marker escapes" in str(exc)
        else:
            raise AssertionError("expected escaped marker path to fail")
        assert not (canon_dir / "escape.md").exists()

    def test_write_canon_marker_rejects_symlink_escape(self, tmp_path):
        """A symlinked ``.reviewed`` marker must not redirect the write out.

        ``_write_canon_marker`` is the provenance-marker path used by the
        contradiction/expansion overwrites. Without containment, a symlinked
        ``.{name}.reviewed`` pointing outside canon_dir would let a write
        escape (Codex ADAPT finding).
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        outside = tmp_path / "marker-outside"
        try:
            (canon_dir / ".magic_system.md.reviewed").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            # Windows without the "Create symbolic links" privilege (or other
            # platforms that forbid symlink creation) cannot set up this
            # fixture; the containment check itself is still exercised on
            # symlink-capable platforms (CI/Linux).
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        with pytest.raises(ValueError, match="canon marker escapes"):
            _write_canon_marker(canon_dir, "magic_system.md", model="claude")
        assert not outside.exists()

    def test_handle_contradiction_rejects_symlinked_existing_file(self, tmp_path):
        """A matched canon ``.md`` that is a symlink out of canon_dir is rejected.

        ``_handle_contradiction`` reads then overwrites an existing canon file
        located by slug match. ``f.is_file()`` / ``read_text`` / ``write_text``
        all follow symlinks, so a symlinked match could read or clobber a file
        outside canon_dir. Containment must reject it before any I/O.

        The actual rejection happens one step earlier than the overwrite: the
        slug-match scan (``iter_canon_files``) skips the escaping symlink as a
        candidate (``tinyassets/ingestion/canon_io.py``), so no existing match
        is found and this falls through to ``_handle_new_element``'s own
        containment check on the (also escaping) fresh filename -- raising
        with ``kind="filename"``, not ``kind="existing file"``. The match
        below asserts the fixed, kind-independent tail of
        ``resolve_within_canon``'s message (``tinyassets/ingestion/
        canon_names.py``) rather than a specific ``kind``, so it stays true
        regardless of which of the two paths actually raises.
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        outside = tmp_path / "secret.md"
        outside.write_text("# Outside secret", encoding="utf-8")
        try:
            (canon_dir / "magic_system.md").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        with pytest.raises(ValueError, match="escapes canon directory"):
            _handle_contradiction(
                canon_dir, "magic_system", "detail", "premise", {}
            )
        # The outside target must be untouched.
        assert outside.read_text(encoding="utf-8") == "# Outside secret"

    def test_handle_expansion_rejects_symlinked_existing_file(self, tmp_path):
        """Same containment guard on the expansion overwrite path.

        See ``test_handle_contradiction_rejects_symlinked_existing_file``
        above for why the match is the kind-independent message tail.
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        outside = tmp_path / "secret.md"
        outside.write_text("# Outside secret", encoding="utf-8")
        try:
            (canon_dir / "magic_system.md").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        with pytest.raises(ValueError, match="escapes canon directory"):
            _handle_expansion(
                canon_dir, "magic_system", "detail", "premise", {}
            )
        assert outside.read_text(encoding="utf-8") == "# Outside secret"

    # ------------------------------------------------------------------
    # Round-2 containment: every canon READ path resolves + contains the
    # path BEFORE filesystem I/O (a symlinked file can leak outside content
    # into the LLM prompt / index / overwrite-guard). Codex round-2 finding.
    # ------------------------------------------------------------------

    def test_handle_synthesize_source_rejects_symlinked_source(self, tmp_path):
        """A signal-controlled ``source_file`` symlink out of canon is rejected.

        ``_handle_synthesize_source`` joins ``source_file`` under
        ``canon/sources`` and reads it. ``read_bytes`` follows symlinks, so a
        symlinked source entry pointing outside canon_dir would feed external
        content into synthesis. Containment must reject it before any read,
        so synthesis reports no documents (the read never happens).
        """
        canon_dir = tmp_path / "canon"
        sources_dir = canon_dir / "sources"
        sources_dir.mkdir(parents=True)
        outside = tmp_path / "secret.txt"
        outside.write_text("EXTERNAL SECRET", encoding="utf-8")
        try:
            (sources_dir / "evil.txt").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        # Returns False (refused) and never reads the symlink target.
        assert _handle_synthesize_source(canon_dir, "evil.txt", "premise", {}) is False
        assert outside.read_text(encoding="utf-8") == "EXTERNAL SECRET"

    def test_handle_synthesize_source_rejects_traversal_source(self, tmp_path):
        """A ``../`` traversal in ``source_file`` is rejected on any platform.

        No symlink privilege needed: ``.resolve()`` collapses the ``..`` and
        the containment check lands the path outside canon_dir.
        """
        canon_dir = tmp_path / "canon"
        (canon_dir / "sources").mkdir(parents=True)
        outside = tmp_path / "secret.txt"
        outside.write_text("EXTERNAL SECRET", encoding="utf-8")

        assert (
            _handle_synthesize_source(canon_dir, "../../secret.txt", "premise", {})
            is False
        )
        assert outside.read_text(encoding="utf-8") == "EXTERNAL SECRET"

    def test_record_synthesis_failure_rejects_symlinked_manifest(self, tmp_path):
        """A symlinked ``.manifest.json`` must not redirect the read/write out.

        ``_record_synthesis_failure`` reads and then rewrites the manifest;
        both follow symlinks. Containment must reject the escape before any
        I/O so the outside target is left untouched.
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        outside = tmp_path / "manifest-outside.json"
        try:
            (canon_dir / ".manifest.json").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        # No raise: the function logs + returns; the escape must not be written.
        _record_synthesis_failure(canon_dir, "source.pdf")
        assert not outside.exists()

    def test_maybe_generate_premise_skips_symlinked_canon(self, tmp_path):
        """A symlinked canon ``.md`` must not leak into the premise prompt.

        ``_maybe_generate_premise`` reads a 500-char sample of each canon
        ``.md``; ``read_text`` follows symlinks. With ONLY an escaping symlink
        present, the file is skipped, ``canon_files`` is empty, and the
        function returns "" without ever invoking the provider.
        """
        universe_dir = tmp_path / "universe"
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        outside = tmp_path / "secret.md"
        outside.write_text("EXTERNAL SECRET PREMISE SOURCE", encoding="utf-8")
        try:
            (canon_dir / "leak.md").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        called = {"provider": False}

        def _fail_provider(*args, **kwargs):  # pragma: no cover - must not run
            called["provider"] = True
            return "should not happen"

        with patch(
            "domains.fantasy_daemon.phases._provider_stub.text_sync",
            _fail_provider,
        ):
            result = _maybe_generate_premise(
                {"_universe_path": str(universe_dir), "world_state_version": 0}
            )
        assert result == ""
        assert called["provider"] is False

    def test_trigger_kg_reindex_skips_symlinked_canon(self, tmp_path):
        """A symlinked canon ``.md`` must not be read into the KG/vector index.

        ``_trigger_kg_reindex`` reads each canon ``.md`` and passes the text to
        ``index_text``. ``read_text`` follows symlinks, so an escaping symlink
        would index external content. Containment skips it: with only an
        escaping symlink present, ``index_text`` is never called.
        """
        from tinyassets import runtime_singletons as runtime

        universe_dir = tmp_path / "universe"
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        outside = tmp_path / "secret.md"
        outside.write_text("EXTERNAL SECRET TO INDEX", encoding="utf-8")
        try:
            (canon_dir / "leak.md").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        indexed = {"count": 0}

        def _fake_index_text(*args, **kwargs):  # pragma: no cover - must not run
            indexed["count"] += 1
            return {"entities": 0, "edges": 0, "facts": 0, "chunks_indexed": 0}

        # Provide a non-None backend so the early "no backends" return is skipped.
        sentinel = object()
        with patch.object(runtime, "knowledge_graph", sentinel), \
                patch.object(runtime, "vector_store", sentinel), \
                patch.object(runtime, "embed_fn", None), \
                patch(
                    "tinyassets.ingestion.indexer.index_text", _fake_index_text
                ):
            _trigger_kg_reindex({"_universe_path": str(universe_dir)})
        assert indexed["count"] == 0

    def test_scan_existing_canon_skips_symlinked_escape(self, tmp_path):
        """A symlinked canon ``.md`` escaping canon_dir is not counted.

        Without the guard, a symlinked ``magic_system.md -> /outside`` would
        register a spurious covered topic and suppress legitimate regeneration.
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        outside = tmp_path / "magic_system.md"
        outside.write_text("# Outside", encoding="utf-8")
        try:
            (canon_dir / "magic_system.md").symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not permitted on this platform: {exc}")

        assert _scan_existing_canon(canon_dir) == set()

    # ------------------------------------------------------------------
    # Containment ORDERING: containment must run BEFORE the ``is_file``
    # stat. ``Path.is_file()`` follows symlinks (it calls ``os.stat`` on
    # the target), so an entry that is itself an escaping symlink must be
    # resolved + rejected *before* any stat touches the external target.
    # Every canon enumeration now routes through
    # ``tinyassets.ingestion.canon_io.iter_canon_files`` (the chokepoint),
    # which calls ``resolve_within_canon`` per entry before touching
    # ``is_file``. These tests patch the chokepoint's resolver to reject one
    # specific filename, then assert ``Path.is_file`` is never invoked for
    # that rejected entry — proving the resolve-first ordering through the
    # shared chokepoint. No symlink privilege is required, so they run live
    # on Windows too.
    # ------------------------------------------------------------------

    @staticmethod
    def _ordering_spy(reject_name: str):
        """Build (patch target, is_file spy, events) for the ordering proof.

        Returns ``(patch_target_ctx, spy_is_file, events)`` where
        ``patch_target_ctx`` is a callable producing the ``unittest.mock.patch``
        context manager that swaps the chokepoint resolver. ``events`` records
        ``("resolve", name)`` and ``("is_file", name)`` in call order. The
        patched resolver raises ``ValueError`` for ``reject_name`` (simulating
        an escaping entry) and resolves everything else normally.
        """
        from tinyassets.ingestion.canon_names import (
            resolve_within_canon as _real_resolve,
        )

        events: list[tuple[str, str]] = []

        def fake_resolve(canon_dir, name, kind="path"):
            events.append(("resolve", name))
            if name == reject_name:
                raise ValueError(f"canon {kind} escapes canon directory: {name!r}")
            return _real_resolve(canon_dir, name, kind=kind)

        real_is_file = Path.is_file

        def spy_is_file(self):
            events.append(("is_file", self.name))
            return real_is_file(self)

        return fake_resolve, spy_is_file, events

    def test_scan_existing_canon_resolves_before_stat(self, tmp_path):
        """``_scan_existing_canon`` resolves containment before ``is_file``.

        Runs live on Windows: a rejected entry must never reach ``is_file``,
        and a legitimate sibling must still be counted.
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "escape.md").write_text("# stand-in for escaping entry", encoding="utf-8")
        (canon_dir / "magic_system.md").write_text("# Magic", encoding="utf-8")

        fake_resolve, spy_is_file, events = self._ordering_spy("escape.md")
        with patch(
            "tinyassets.ingestion.canon_io.resolve_within_canon",
            fake_resolve,
        ), patch.object(Path, "is_file", spy_is_file):
            existing = _scan_existing_canon(canon_dir)

        # The rejected entry is dropped; the legitimate sibling survives.
        assert "magic_system" in existing
        assert "escape" not in existing
        # No ``is_file`` for the rejected entry, and for it ``resolve`` precedes
        # any stat (the entry is skipped before any stat).
        assert ("is_file", "escape.md") not in events
        assert ("resolve", "escape.md") in events
        # For the legitimate entry, resolve precedes its is_file.
        ri = events.index(("resolve", "magic_system.md"))
        fi = events.index(("is_file", "magic_system.md"))
        assert ri < fi

    def test_maybe_generate_premise_resolves_before_stat(self, tmp_path):
        """``_maybe_generate_premise`` resolves containment before ``is_file``."""
        universe_dir = tmp_path / "universe"
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / "escape.md").write_text("EXTERNAL", encoding="utf-8")
        (canon_dir / "world.md").write_text("A realm of glass.", encoding="utf-8")

        fake_resolve, spy_is_file, events = self._ordering_spy("escape.md")

        def _fail_provider(*args, **kwargs):  # provider may or may not run
            return "premise"

        with patch(
            "tinyassets.ingestion.canon_io.resolve_within_canon",
            fake_resolve,
        ), patch.object(Path, "is_file", spy_is_file), patch(
            "domains.fantasy_daemon.phases._provider_stub.text_sync",
            _fail_provider,
        ):
            _maybe_generate_premise(
                {"_universe_path": str(universe_dir), "world_state_version": 0}
            )

        assert ("is_file", "escape.md") not in events
        assert ("resolve", "escape.md") in events
        ri = events.index(("resolve", "world.md"))
        fi = events.index(("is_file", "world.md"))
        assert ri < fi

    def test_trigger_kg_reindex_resolves_before_stat(self, tmp_path):
        """``_trigger_kg_reindex`` resolves containment before ``is_file``."""
        from tinyassets import runtime_singletons as runtime

        universe_dir = tmp_path / "universe"
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir(parents=True)
        (canon_dir / "escape.md").write_text("EXTERNAL", encoding="utf-8")
        (canon_dir / "world.md").write_text("Lore to index.", encoding="utf-8")

        fake_resolve, spy_is_file, events = self._ordering_spy("escape.md")
        indexed_names: list[str] = []

        def _fake_index_text(*args, **kwargs):
            indexed_names.append(kwargs.get("source_id", ""))
            return {"entities": 0, "edges": 0, "facts": 0, "chunks_indexed": 0}

        sentinel = object()
        with patch.object(runtime, "knowledge_graph", sentinel), \
                patch.object(runtime, "vector_store", sentinel), \
                patch.object(runtime, "embed_fn", None), \
                patch(
                    "tinyassets.ingestion.canon_io.resolve_within_canon",
                    fake_resolve,
                ), \
                patch.object(Path, "is_file", spy_is_file), \
                patch("tinyassets.ingestion.indexer.index_text", _fake_index_text):
            _trigger_kg_reindex({"_universe_path": str(universe_dir)})

        # Escaping entry never stat'd nor indexed; legitimate entry indexed.
        assert ("is_file", "escape.md") not in events
        assert ("resolve", "escape.md") in events
        assert "world" in indexed_names
        assert "escape" not in indexed_names

    def test_handle_contradiction_resolves_before_stat(self, tmp_path):
        """``_handle_contradiction`` resolves containment before ``is_file``.

        The escaping entry's slug also matches the topic; the resolve-first
        guard must skip it (no stat, no read) and fall through to new-element
        handling rather than reading the escaping target.
        """
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "magic_system.md").write_text("# real canon", encoding="utf-8")

        fake_resolve, spy_is_file, events = self._ordering_spy("magic_system.md")
        with patch(
            "tinyassets.ingestion.canon_io.resolve_within_canon",
            fake_resolve,
        ), patch.object(Path, "is_file", spy_is_file), patch(
            "domains.fantasy_daemon.phases.worldbuild._handle_new_element",
        ) as new_el:
            _handle_contradiction(canon_dir, "magic_system", "detail", "premise", {})

        # The escaping match was skipped before any stat, so no canon content
        # was found and the new-element path ran instead.
        assert ("is_file", "magic_system.md") not in events
        assert ("resolve", "magic_system.md") in events
        new_el.assert_called_once()

    def test_handle_expansion_resolves_before_stat(self, tmp_path):
        """``_handle_expansion`` resolves containment before ``is_file``."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "magic_system.md").write_text("# real canon", encoding="utf-8")

        fake_resolve, spy_is_file, events = self._ordering_spy("magic_system.md")
        with patch(
            "tinyassets.ingestion.canon_io.resolve_within_canon",
            fake_resolve,
        ), patch.object(Path, "is_file", spy_is_file), patch(
            "domains.fantasy_daemon.phases.worldbuild._handle_new_element",
        ) as new_el:
            _handle_expansion(canon_dir, "magic_system", "detail", "premise", {})

        assert ("is_file", "magic_system.md") not in events
        assert ("resolve", "magic_system.md") in events
        new_el.assert_called_once()

    def test_read_premise_from_file(self, tmp_path):
        """Should read premise from PROGRAM.md."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        (universe_dir / "PROGRAM.md").write_text(
            "My epic story premise.", encoding="utf-8"
        )
        premise = _read_premise(universe_dir, {})
        assert premise == "My epic story premise."

    def test_read_premise_fallback_to_state(self, tmp_path):
        """Should fall back to premise_kernel when no PROGRAM.md."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        state = {"premise_kernel": "Fallback premise."}
        premise = _read_premise(universe_dir, state)
        assert premise == "Fallback premise."

    def test_generated_files_in_trace(self, tmp_path):
        """quality_trace should include the list of generated filenames."""
        universe_dir = self._make_universe(
            tmp_path, "A world to build."
        )
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        trace = result["quality_trace"][0]
        assert "generated_files" in trace
        assert isinstance(trace["generated_files"], list)

    def test_uses_universe_path_key_fallback(self, tmp_path):
        """Should fall back to 'universe_path' when '_universe_path' missing."""
        universe_dir = self._make_universe(
            tmp_path, "A fallback path test."
        )
        state = {
            "world_state_version": 0,
            "universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        generated = result["quality_trace"][0]["generated_files"]
        assert len(generated) > 0

    def test_creates_canon_dir_if_missing(self, tmp_path):
        """Canon dir should be created automatically if it does not exist."""
        universe_dir = self._make_universe(
            tmp_path, "Canon dir creation test."
        )
        canon_dir = universe_dir / "canon"
        assert not canon_dir.exists()

        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        worldbuild(state)
        assert canon_dir.exists()


# -----------------------------------------------------------------------
# select_task tests
# -----------------------------------------------------------------------


class TestSelectTask:
    """Tests for the select_task node."""

    def test_defaults_to_worldbuild_when_new(self):
        """New universe (version 0, few facts) should worldbuild first."""
        state = {"task_queue": [], "health": {}}
        result = select_task(state)
        assert result["task_queue"] == ["worldbuild"]

    def test_defaults_to_idle_when_no_user_task(self):
        """Without a user-directed task, daemon idles (not auto-write)."""
        state = {"task_queue": [], "health": {"cycles_completed": 1},
                 "canon_facts_count": 10, "world_state_version": 1}
        result = select_task(state)
        assert result["task_queue"] == ["idle"]

    def test_writes_when_user_directed(self):
        """User-directed write task should proceed."""
        state = {"task_queue": ["write"], "health": {"cycles_completed": 1},
                 "canon_facts_count": 10, "world_state_version": 1,
                 "workflow_instructions": {"next_task": "write"}}
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_preserves_existing_queue(self):
        state = {"task_queue": ["worldbuild", "write"], "health": {}}
        result = select_task(state)
        assert result["task_queue"][0] == "worldbuild"

    def test_stuck_pushes_diagnose(self):
        state = {"task_queue": ["write"], "health": {"stuck_level": 4}}
        result = select_task(state)
        assert result["task_queue"][0] == "diagnose"
        # write should still be in the queue
        assert "write" in result["task_queue"]

    def test_stuck_level_3_does_not_trigger(self):
        """stuck_level must be > 3, not >= 3."""
        state = {"task_queue": ["write"], "health": {"stuck_level": 3, "cycles_completed": 1},
                 "canon_facts_count": 10, "world_state_version": 1,
                 "workflow_instructions": {"next_task": "write"}}
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_bootstrap_skipped_after_first_cycle(self):
        """After cycles_completed > 0, bootstrap worldbuild should NOT fire.

        This prevents the daemon from looping on worldbuild when canon
        files fail to write but the cycle has already completed once.
        """
        state = {
            "task_queue": ["write"],
            "health": {"cycles_completed": 1},
            "world_state_version": 0,
            "canon_facts_count": 0,
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        # Should NOT worldbuild -- bootstrap already attempted
        assert result["task_queue"][0] == "write"

    def test_bootstrap_skipped_when_version_positive(self):
        """version > 0 means worldbuild already returned — skip bootstrap."""
        state = {
            "task_queue": ["write"],
            "health": {},
            "world_state_version": 1,
            "canon_facts_count": 0,
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_bootstrap_skipped_when_canon_files_exist(self, tmp_path):
        """After first cycle, canon files on disk skip bootstrap."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text("World lore.", encoding="utf-8")
        (canon_dir / "magic.md").write_text("Magic system.", encoding="utf-8")

        state = {
            "task_queue": ["write"],
            "health": {"cycles_completed": 1},
            "world_state_version": 1,
            "canon_facts_count": 0,
            "_universe_path": str(tmp_path),
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_stale_world_pushes_worldbuild(self):
        """Chapters far outpacing worldbuilds triggers worldbuild (fallback)."""
        state = {
            "task_queue": ["write"],
            "health": {},
            "world_state_version": 0,
            "total_chapters": 15,
        }
        result = select_task(state)
        assert result["task_queue"][0] == "worldbuild"

    def test_world_state_not_stale_when_fresh(self):
        state = {
            "task_queue": ["write"],
            "health": {},
            "world_state_version": 3,
            "total_chapters": 5,
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_user_override_from_instructions(self):
        state = {
            "task_queue": ["write"],
            "health": {},
            "workflow_instructions": {"next_task": "reflect"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "reflect"

    def test_invalid_override_idles(self):
        """Invalid override falls through to idle (daemon is directed)."""
        state = {
            "task_queue": ["write"],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "workflow_instructions": {"next_task": "invalid_task"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "idle"

    def test_stuck_takes_priority_over_stale(self):
        """stuck_level > 3 should take priority over stale world state."""
        state = {
            "task_queue": ["write"],
            "health": {"stuck_level": 5},
            "world_state_version": 0,
            "total_chapters": 20,
        }
        # But user_override has the highest priority
        result = select_task(state)
        assert result["task_queue"][0] == "diagnose"

    def test_user_override_takes_priority_over_stuck(self):
        state = {
            "task_queue": ["write"],
            "health": {"stuck_level": 5},
            "workflow_instructions": {"next_task": "reflect"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "reflect"

    def test_returns_quality_trace(self):
        state = {"task_queue": ["write"], "health": {"cycles_completed": 1},
                 "canon_facts_count": 10, "world_state_version": 1,
                 "workflow_instructions": {"next_task": "write"}}
        result = select_task(state)
        assert len(result["quality_trace"]) == 1
        trace = result["quality_trace"][0]
        assert trace["node"] == "select_task"
        assert trace["selected"] == "write"

    def test_empty_state_worldbuilds_first(self):
        """Completely empty state should worldbuild, not write."""
        state = {}
        result = select_task(state)
        assert result["task_queue"] == ["worldbuild"]


# -----------------------------------------------------------------------
# diagnose tests
# -----------------------------------------------------------------------


class TestDiagnose:
    """Tests for the diagnose node."""

    def test_basic_recovery_resets_stuck_level(self):
        state = {"health": {"stuck_level": 4}, "quality_trace": []}
        result = diagnose(state)
        # stuck_level should be reduced (4 - 2 = 2)
        assert result["health"]["stuck_level"] == 2
        assert "recovery_suggestions" in result["health"]
        assert len(result["health"]["recovery_suggestions"]) >= 1

    def test_revert_pattern_detected(self):
        trace = [
            {"node": "commit", "verdict": "revert"},
            {"node": "commit", "verdict": "revert"},
            {"node": "commit", "verdict": "revert"},
        ]
        state = {"health": {"stuck_level": 4}, "quality_trace": trace}
        result = diagnose(state)

        suggestions = result["health"]["recovery_suggestions"]
        types = [s["type"] for s in suggestions]
        assert "revert_pattern" in types

    def test_no_revert_pattern_below_threshold(self):
        trace = [
            {"node": "commit", "verdict": "revert"},
            {"node": "commit", "verdict": "accept"},
            {"node": "commit", "verdict": "revert"},
        ]
        state = {"health": {"stuck_level": 4}, "quality_trace": trace}
        result = diagnose(state)

        suggestions = result["health"]["recovery_suggestions"]
        types = [s["type"] for s in suggestions]
        assert "revert_pattern" not in types

    def test_recurring_structural_failures(self):
        trace = [
            {
                "node": "commit",
                "verdict": "revert",
                "structural_checks": [
                    {"name": "pacing", "passed": False},
                    {"name": "continuity", "passed": True},
                ],
            },
            {
                "node": "commit",
                "verdict": "revert",
                "structural_checks": [
                    {"name": "pacing", "passed": False},
                ],
            },
        ]
        state = {"health": {"stuck_level": 3}, "quality_trace": trace}
        result = diagnose(state)

        assert "pacing" in result["health"]["recurring_failures"]
        assert result["health"]["recurring_failures"]["pacing"] == 2

    def test_general_stuck_when_no_patterns(self):
        state = {"health": {"stuck_level": 3}, "quality_trace": []}
        result = diagnose(state)

        suggestions = result["health"]["recovery_suggestions"]
        types = [s["type"] for s in suggestions]
        assert "general_stuck" in types

    def test_stuck_level_never_goes_negative(self):
        state = {"health": {"stuck_level": 1}, "quality_trace": []}
        result = diagnose(state)
        assert result["health"]["stuck_level"] >= 0

    def test_recent_reverts_counted(self):
        trace = [
            {"node": "commit", "verdict": "accept"},
            {"node": "orient", "action": "orient"},  # non-commit ignored
            {"node": "commit", "verdict": "revert"},
            {"node": "commit", "verdict": "revert"},
        ]
        state = {"health": {"stuck_level": 3}, "quality_trace": trace}
        result = diagnose(state)
        assert result["health"]["recent_reverts"] == 2

    def test_empty_health_defaults(self):
        state = {}
        result = diagnose(state)
        assert result["health"]["stuck_level"] >= 0
        assert "recovery_suggestions" in result["health"]


# -----------------------------------------------------------------------
# universe_cycle tests
# -----------------------------------------------------------------------


class TestUniverseCycle:
    """Tests for the universe_cycle node."""

    def test_continues_when_queue_empty_after_pop(self):
        """Daemon runs indefinitely -- empty queue does not stop it."""
        state = {
            "health": {},
            "task_queue": ["write"],
            "total_words": 1000,
            "total_chapters": 5,
        }
        result = universe_cycle(state)
        assert result["health"]["stopped"] is False
        assert result["task_queue"] == []

    def test_continues_when_queue_has_more(self):
        state = {
            "health": {},
            "task_queue": ["write", "worldbuild"],
            "total_words": 500,
            "total_chapters": 2,
        }
        result = universe_cycle(state)
        assert result["health"]["stopped"] is False
        assert result["task_queue"] == ["worldbuild"]

    def test_updates_health_metrics(self):
        state = {
            "health": {},
            "task_queue": ["write"],
            "total_words": 5000,
            "total_chapters": 10,
        }
        result = universe_cycle(state)
        assert result["health"]["total_words"] == 5000
        assert result["health"]["total_chapters"] == 10
        assert result["health"]["cycles_completed"] == 1

    def test_increments_cycles_completed(self):
        state = {
            "health": {"cycles_completed": 3},
            "task_queue": ["write"],
            "total_words": 0,
            "total_chapters": 0,
        }
        result = universe_cycle(state)
        assert result["health"]["cycles_completed"] == 4

    def test_calls_memory_cleanup(self):
        from tinyassets import runtime_singletons as runtime

        mgr = MagicMock()
        mgr.evict_old_data.return_value = 5

        runtime.memory_manager = mgr
        try:
            state = {
                "health": {},
                "task_queue": ["write"],
                "total_words": 0,
                "total_chapters": 3,
            }
            result = universe_cycle(state)
        finally:
            runtime.memory_manager = None
        mgr.evict_old_data.assert_called_once_with(current_chapter=3)
        assert result["quality_trace"][0]["evicted_records"] == 5

    def test_graceful_when_no_manager(self):
        state = {
            "health": {},
            "task_queue": ["write"],
            "total_words": 0,
            "total_chapters": 0,
        }
        result = universe_cycle(state)
        assert result["quality_trace"][0]["evicted_records"] == 0

    def test_graceful_when_manager_fails(self):
        from tinyassets import runtime_singletons as runtime

        mgr = MagicMock()
        mgr.evict_old_data.side_effect = RuntimeError("fail")

        runtime.memory_manager = mgr
        try:
            state = {
                "health": {},
                "task_queue": ["write"],
            }
            result = universe_cycle(state)
        finally:
            runtime.memory_manager = None
        assert result["quality_trace"][0]["evicted_records"] == 0

    def test_returns_quality_trace(self):
        state = {
            "health": {},
            "task_queue": ["write"],
            "total_words": 100,
            "total_chapters": 1,
        }
        result = universe_cycle(state)
        assert len(result["quality_trace"]) == 1
        trace = result["quality_trace"][0]
        assert trace["node"] == "universe_cycle"
        assert trace["completed_task"] == "write"
        assert trace["stopped"] is False

    def test_empty_queue_does_not_stop(self):
        """Daemon runs indefinitely -- empty queue does not stop."""
        state = {"health": {}, "task_queue": []}
        result = universe_cycle(state)
        assert result["health"]["stopped"] is False

    def test_pops_front_task(self):
        state = {
            "health": {},
            "task_queue": ["worldbuild", "write", "reflect"],
        }
        result = universe_cycle(state)
        assert result["task_queue"] == ["write", "reflect"]
        assert result["quality_trace"][0]["completed_task"] == "worldbuild"

    def test_forwards_world_state_version(self):
        """world_state_version must persist across the cycle boundary."""
        state = {
            "health": {},
            "task_queue": ["worldbuild"],
            "world_state_version": 3,
        }
        result = universe_cycle(state)
        assert result["world_state_version"] == 3
        assert result["quality_trace"][0]["world_state_version"] == 3

    def test_forwards_world_state_version_default(self):
        """Missing world_state_version defaults to 0."""
        state = {"health": {}, "task_queue": ["write"]}
        result = universe_cycle(state)
        assert result["world_state_version"] == 0


# -----------------------------------------------------------------------
# reflect tests
# -----------------------------------------------------------------------


class TestReflect:
    """Tests for the reflect node."""

    def test_returns_quality_trace(self):
        """Reflect should always return a quality_trace entry."""
        state = {}
        result = reflect(state)
        assert "quality_trace" in result
        assert len(result["quality_trace"]) == 1
        trace = result["quality_trace"][0]
        assert trace["node"] == "reflect"

    def test_reflexion_runs_with_manager(self):
        from tinyassets import runtime_singletons as runtime

        mgr = MagicMock()
        reflexion_result = MagicMock()
        reflexion_result.critique = "Test critique text"
        reflexion_result.updated_weights = {"continuity_check": 1.5}
        mgr.run_reflexion.return_value = reflexion_result

        runtime.memory_manager = mgr
        try:
            state = {}
            result = reflect(state)
        finally:
            runtime.memory_manager = None

        mgr.run_reflexion.assert_called_once_with(state)
        assert result["quality_trace"][0]["reflexion_ran"] is True

    def test_reflexion_skipped_without_manager(self):
        state = {}
        result = reflect(state)
        assert result["quality_trace"][0]["reflexion_ran"] is False

    def test_reflexion_failure_is_graceful(self):
        from tinyassets import runtime_singletons as runtime

        mgr = MagicMock()
        mgr.run_reflexion.side_effect = RuntimeError("boom")

        runtime.memory_manager = mgr
        try:
            result = reflect({})
        finally:
            runtime.memory_manager = None

        assert result["quality_trace"][0]["reflexion_ran"] is False

    def test_no_crash_on_empty_state(self):
        """Reflect must never crash, even with an empty state dict."""
        result = reflect({})
        assert isinstance(result, dict)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 0


class TestReflectCanonReview:
    """Tests for canon quality review in the reflect node."""

    def _make_universe(
        self, tmp_path: Path, premise: str = "", canon_files: dict[str, str] | None = None
    ) -> Path:
        """Create a minimal universe directory with optional premise and canon."""
        universe_dir = tmp_path / "test-universe"
        universe_dir.mkdir()
        if premise:
            (universe_dir / "PROGRAM.md").write_text(premise, encoding="utf-8")
        if canon_files:
            canon_dir = universe_dir / "canon"
            canon_dir.mkdir()
            for name, content in canon_files.items():
                (canon_dir / name).write_text(content, encoding="utf-8")
        return universe_dir

    def test_reviews_canon_files(self, tmp_path):
        """Should review existing canon files and report count."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A dark fantasy world.",
            canon_files={
                "characters.md": "# Characters\n\nGeneric content.",
                "locations.md": "# Locations\n\nSome places.",
            },
        )
        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        trace = result["quality_trace"][0]
        assert trace["canon_files_reviewed"] == 2

    def test_skips_without_universe_path(self):
        """No universe path means no review."""
        result = reflect({})
        assert result["quality_trace"][0]["canon_files_reviewed"] == 0

    def test_skips_without_canon_dir(self, tmp_path):
        """No canon directory means no review."""
        universe_dir = self._make_universe(tmp_path, premise="A story.")
        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 0

    def test_skips_without_premise(self, tmp_path):
        """No premise means no review."""
        universe_dir = self._make_universe(
            tmp_path,
            canon_files={"characters.md": "# Characters"},
        )
        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 0

    def test_rewrites_drifted_files(self, tmp_path):
        """When evaluate_canon finds issues, files should be rewritten."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A high-tech sci-fi world.",
            canon_files={
                "characters.md": "# Characters\n\nGeneric fantasy folk.",
            },
        )

        issues = [
            {
                "filename": "characters.md",
                "reason": "Characters are generic fantasy, not sci-fi",
                "severity": 8,
            }
        ]

        state = {"_universe_path": str(universe_dir)}
        with patch(
            "domains.fantasy_daemon.phases.reflect._evaluate_canon",
            return_value=issues,
        ):
            result = reflect(state)

        trace = result["quality_trace"][0]
        assert "characters.md" in trace["canon_files_rewritten"]
        # File should still exist after rewrite
        canon_dir = universe_dir / "canon"
        assert (canon_dir / "characters.md").exists()

    def test_limits_rewrites_per_cycle(self, tmp_path):
        """Should rewrite at most _MAX_REWRITES_PER_CYCLE files."""
        canon_files = {
            f"topic_{i}.md": f"# Topic {i}\n\nShallow content."
            for i in range(5)
        }
        universe_dir = self._make_universe(
            tmp_path,
            premise="A complex world.",
            canon_files=canon_files,
        )

        issues = [
            {"filename": f"topic_{i}.md", "reason": "shallow", "severity": 7}
            for i in range(5)
        ]

        state = {"_universe_path": str(universe_dir)}
        with patch(
            "domains.fantasy_daemon.phases.reflect._evaluate_canon",
            return_value=issues,
        ):
            result = reflect(state)

        trace = result["quality_trace"][0]
        assert len(trace["canon_files_rewritten"]) <= _MAX_REWRITES_PER_CYCLE

    def test_graceful_when_evaluate_fails(self, tmp_path):
        """LLM evaluation failure should not crash the node."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A premise.",
            canon_files={"characters.md": "# Characters"},
        )
        state = {"_universe_path": str(universe_dir)}
        with patch(
            "domains.fantasy_daemon.phases.reflect._evaluate_canon",
            side_effect=RuntimeError("LLM down"),
        ):
            result = reflect(state)

        # Should not crash
        assert result["quality_trace"][0]["canon_files_reviewed"] == 0

    def test_graceful_when_rewrite_fails(self, tmp_path):
        """Rewrite failure should not crash and should not corrupt file."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A premise.",
            canon_files={"characters.md": "# Original Content"},
        )
        original = (universe_dir / "canon" / "characters.md").read_text(
            encoding="utf-8"
        )

        issues = [
            {"filename": "characters.md", "reason": "drift", "severity": 9}
        ]

        state = {"_universe_path": str(universe_dir)}
        with (
            patch(
                "domains.fantasy_daemon.phases.reflect._evaluate_canon",
                return_value=issues,
            ),
            patch(
                "domains.fantasy_daemon.phases.reflect._rewrite_canon_file",
                side_effect=RuntimeError("rewrite failed"),
            ),
        ):
            result = reflect(state)

        # Should not crash
        assert result["quality_trace"][0]["canon_files_rewritten"] == []
        # Original file should be intact
        current = (universe_dir / "canon" / "characters.md").read_text(
            encoding="utf-8"
        )
        assert current == original

    def test_uses_premise_kernel_fallback(self, tmp_path):
        """Should use premise_kernel from state if PROGRAM.md absent."""
        universe_dir = self._make_universe(
            tmp_path,
            canon_files={"characters.md": "# Characters"},
        )
        state = {
            "_universe_path": str(universe_dir),
            "premise_kernel": "A world of ancient magic.",
        }
        result = reflect(state)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 1

    def test_non_md_files_ignored(self, tmp_path):
        """Only .md files in canon/ should be reviewed."""
        universe_dir = self._make_universe(
            tmp_path, premise="A story."
        )
        canon_dir = universe_dir / "canon"
        canon_dir.mkdir()
        (canon_dir / "characters.md").write_text("# Chars", encoding="utf-8")
        (canon_dir / "notes.txt").write_text("notes", encoding="utf-8")
        (canon_dir / "data.json").write_text("{}", encoding="utf-8")

        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 1


class TestCanonReviewHelpers:
    """Tests for reflect node internal helper functions."""

    def test_parse_issues_valid_json(self):
        """Should parse a valid JSON array of issues."""
        raw = json.dumps([
            {"filename": "a.md", "reason": "drift", "severity": 7},
            {"filename": "b.md", "reason": "shallow", "severity": 3},
        ])
        issues = _parse_issues(raw)
        assert len(issues) == 2
        # Should be sorted by severity descending
        assert issues[0]["filename"] == "a.md"
        assert issues[1]["filename"] == "b.md"

    def test_parse_issues_empty_array(self):
        """Empty array means no issues."""
        assert _parse_issues("[]") == []

    def test_parse_issues_invalid_json(self):
        """Invalid JSON should return empty list, not crash."""
        assert _parse_issues("not json at all") == []

    def test_parse_issues_strips_code_fences(self):
        """Should handle markdown code fences around JSON."""
        raw = '```json\n[{"filename": "a.md", "reason": "drift", "severity": 5}]\n```'
        issues = _parse_issues(raw)
        assert len(issues) == 1
        assert issues[0]["filename"] == "a.md"

    def test_parse_issues_missing_filename(self):
        """Items without filename should be skipped."""
        raw = json.dumps([
            {"reason": "no filename", "severity": 5},
            {"filename": "good.md", "reason": "ok", "severity": 3},
        ])
        issues = _parse_issues(raw)
        assert len(issues) == 1
        assert issues[0]["filename"] == "good.md"

    def test_parse_issues_defaults(self):
        """Missing reason/severity should get defaults."""
        raw = json.dumps([{"filename": "a.md"}])
        issues = _parse_issues(raw)
        assert issues[0]["reason"] == "unspecified issue"
        assert issues[0]["severity"] == 5

    def test_parse_issues_non_list(self):
        """Non-list JSON should return empty list."""
        assert _parse_issues('{"not": "a list"}') == []

    def test_collect_reviewable_canon_no_signals_returns_all(self, tmp_path):
        """Without signal_topics, all canon files are reviewable."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "chars.md").write_text("# Characters", encoding="utf-8")
        (canon_dir / "locations.md").write_text("# Locations", encoding="utf-8")
        _stamp_reviewed(canon_dir / "chars.md")  # Stamp should NOT block

        result = _collect_reviewable_canon(canon_dir)
        assert "chars.md" in result
        assert "locations.md" in result

    def test_collect_reviewable_canon_with_signal_topics(self, tmp_path):
        """With signal_topics, only matching files are reviewable."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "characters.md").write_text("# Characters", encoding="utf-8")
        (canon_dir / "locations.md").write_text("# Locations", encoding="utf-8")
        (canon_dir / "magic_system.md").write_text("# Magic", encoding="utf-8")

        result = _collect_reviewable_canon(canon_dir, signal_topics={"characters"})
        assert "characters.md" in result
        assert "locations.md" not in result
        assert "magic_system.md" not in result

    def test_collect_reviewable_canon_includes_unreviewed(self, tmp_path):
        """Files never reviewed should be included."""
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "chars.md").write_text("# Characters", encoding="utf-8")

        result = _collect_reviewable_canon(canon_dir)
        assert "chars.md" in result

    def test_recently_reviewed_always_false(self, tmp_path):
        """_recently_reviewed always returns False (cooldown removed)."""
        f = tmp_path / "test.md"
        f.write_text("content", encoding="utf-8")
        _stamp_reviewed(f)
        assert _recently_reviewed(f, 999999999.0) is False

    def test_recently_reviewed_no_marker(self, tmp_path):
        """No marker file means not recently reviewed."""
        f = tmp_path / "test.md"
        f.write_text("content", encoding="utf-8")
        assert _recently_reviewed(f, 999999999.0) is False

    def test_stamp_reviewed_creates_marker(self, tmp_path):
        """Stamping should create a sidecar marker file."""
        f = tmp_path / "test.md"
        f.write_text("content", encoding="utf-8")
        _stamp_reviewed(f)
        marker = tmp_path / ".test.md.reviewed"
        assert marker.exists()
        data = json.loads(marker.read_text(encoding="utf-8"))
        assert "reviewed_at" in data

    def test_evaluate_canon_returns_list(self, tmp_path):
        """_evaluate_canon should return a list (possibly empty in mock mode)."""
        canon_files = {"chars.md": "# Characters\n\nContent."}
        premise = "A dark fantasy world."
        # In test mode (_FORCE_MOCK=True), text_sync returns the
        # fallback which is "[]", so _evaluate_canon returns []
        issues = _evaluate_canon(canon_files, premise)
        assert isinstance(issues, list)


# -----------------------------------------------------------------------
# commit worldbuild signals tests
# -----------------------------------------------------------------------


class TestCommitWorldbuildSignals:
    """Tests for worldbuild signal generation in the commit node."""

    def _make_universe(self, tmp_path: Path, canon_files: dict[str, str] | None = None) -> Path:
        universe_dir = tmp_path / "test-universe"
        universe_dir.mkdir()
        (universe_dir / "PROGRAM.md").write_text("A fantasy world.", encoding="utf-8")
        if canon_files:
            canon_dir = universe_dir / "canon"
            canon_dir.mkdir()
            for name, content in canon_files.items():
                (canon_dir / name).write_text(content, encoding="utf-8")
        return universe_dir

    def test_commit_returns_worldbuild_signals_key(self, tmp_story_db):
        """Commit output should always contain worldbuild_signals."""
        from domains.fantasy_daemon.phases.commit import commit

        state = {
            "draft_output": {
                "scene_id": "s1",
                "prose": "Ryn walked through the Northern Gate.",
                "word_count": 50,
            },
            "_db_path": tmp_story_db,
        }
        result = commit(state)
        assert "worldbuild_signals" in result
        assert isinstance(result["worldbuild_signals"], list)

    def test_signals_empty_without_universe_path(self, tmp_story_db):
        """No signals generated when no universe_path is set."""
        from domains.fantasy_daemon.phases.commit import commit

        state = {
            "draft_output": {
                "scene_id": "s1",
                "prose": "Ryn walked through the gate.",
                "word_count": 50,
            },
            "_db_path": tmp_story_db,
        }
        result = commit(state)
        assert result["worldbuild_signals"] == []

    def test_signals_empty_without_canon(self, tmp_path, tmp_story_db):
        """No signals when there is no canon directory."""
        from domains.fantasy_daemon.phases.commit import commit

        universe_dir = self._make_universe(tmp_path)
        state = {
            "draft_output": {
                "scene_id": "s1",
                "prose": "Ryn walked through the gate.",
                "word_count": 50,
            },
            "_universe_path": str(universe_dir),
            "_db_path": tmp_story_db,
        }
        result = commit(state)
        assert result["worldbuild_signals"] == []

    def test_signals_count_in_quality_trace(self, tmp_story_db):
        """quality_trace should include signal count."""
        from domains.fantasy_daemon.phases.commit import commit

        state = {
            "draft_output": {
                "scene_id": "s1",
                "prose": "Ryn walked.",
                "word_count": 10,
            },
            "_db_path": tmp_story_db,
        }
        result = commit(state)
        trace = result["quality_trace"][0]
        assert "worldbuild_signals" in trace
        assert isinstance(trace["worldbuild_signals"], int)

    def test_parse_worldbuild_signals_valid(self):
        """Valid JSON should be parsed into signals."""
        from domains.fantasy_daemon.phases.commit import _parse_worldbuild_signals

        raw = json.dumps([
            {"type": "new_element", "topic": "character", "detail": "Serakh found"},
            {"type": "contradiction", "topic": "magic_system", "detail": "Touch vs range"},
        ])
        signals = _parse_worldbuild_signals(raw)
        assert len(signals) == 2
        assert signals[0]["type"] == "new_element"
        assert signals[1]["type"] == "contradiction"

    def test_parse_worldbuild_signals_empty(self):
        """Empty array should return empty list."""
        from domains.fantasy_daemon.phases.commit import _parse_worldbuild_signals

        assert _parse_worldbuild_signals("[]") == []

    def test_parse_worldbuild_signals_invalid_json(self):
        """Invalid JSON should return empty list, not crash."""
        from domains.fantasy_daemon.phases.commit import _parse_worldbuild_signals

        assert _parse_worldbuild_signals("not json") == []

    def test_parse_worldbuild_signals_bad_types_filtered(self):
        """Signals with invalid type should be filtered out."""
        from domains.fantasy_daemon.phases.commit import _parse_worldbuild_signals

        raw = json.dumps([
            {"type": "new_element", "topic": "char", "detail": "ok"},
            {"type": "invalid_type", "topic": "x", "detail": "skip me"},
            {"type": "expansion", "topic": "lore", "detail": "ok too"},
        ])
        signals = _parse_worldbuild_signals(raw)
        assert len(signals) == 2
        assert signals[0]["type"] == "new_element"
        assert signals[1]["type"] == "expansion"

    def test_parse_worldbuild_signals_strips_code_fences(self):
        """Should handle markdown code fences around JSON."""
        from domains.fantasy_daemon.phases.commit import _parse_worldbuild_signals

        raw = '```json\n[{"type": "new_element", "topic": "a", "detail": "b"}]\n```'
        signals = _parse_worldbuild_signals(raw)
        assert len(signals) == 1

    def test_persist_worldbuild_signals(self, tmp_path):
        """Signals should be persisted to disk."""
        from domains.fantasy_daemon.phases.commit import _persist_worldbuild_signals

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()

        signals = [{"type": "new_element", "topic": "char", "detail": "test"}]
        _persist_worldbuild_signals({"_universe_path": str(universe_dir)}, signals)

        signals_file = universe_dir / "enrichment_signals.json"
        assert signals_file.exists()
        data = json.loads(signals_file.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["type"] == "new_element"

    def test_persist_appends_to_existing(self, tmp_path):
        """New signals should be appended to existing ones."""
        from domains.fantasy_daemon.phases.commit import _persist_worldbuild_signals

        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        legacy_file = universe_dir / "worldbuild_signals.json"
        legacy_file.write_text(
            json.dumps([{"type": "expansion", "topic": "x", "detail": "old"}]),
            encoding="utf-8",
        )

        new = [{"type": "new_element", "topic": "y", "detail": "new"}]
        _persist_worldbuild_signals({"_universe_path": str(universe_dir)}, new)

        signals_file = universe_dir / "enrichment_signals.json"
        data = json.loads(signals_file.read_text(encoding="utf-8"))
        assert len(data) == 2

    def test_persist_graceful_without_universe_path(self):
        """Persist should not crash without universe_path."""
        from domains.fantasy_daemon.phases.commit import _persist_worldbuild_signals

        _persist_worldbuild_signals({}, [{"type": "new_element", "topic": "a", "detail": "b"}])


# -----------------------------------------------------------------------
# select_task signal-driven tests
# -----------------------------------------------------------------------


class TestSelectTaskSignals:
    """Tests for signal-driven task selection."""

    def test_worldbuild_signals_in_state_trigger_worldbuild(self):
        """Signals in state should route to worldbuild."""
        state = {
            "task_queue": ["write"],
            "health": {},
            "canon_facts_count": 10,
            "worldbuild_signals": [
                {"type": "new_element", "topic": "char", "detail": "test"},
            ],
        }
        result = select_task(state)
        assert result["task_queue"][0] == "worldbuild"
        trace = result["quality_trace"][0]
        assert trace["reason"] == "enrichment_signals"

    def test_worldbuild_signals_on_disk_trigger_worldbuild(self, tmp_path):
        """Signals in file should route to worldbuild."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        signals_file = universe_dir / "worldbuild_signals.json"
        signals_file.write_text(
            json.dumps([{"type": "contradiction", "topic": "magic", "detail": "conflict"}]),
            encoding="utf-8",
        )
        state = {
            "task_queue": ["write"],
            "health": {},
            "canon_facts_count": 10,
            "_universe_path": str(universe_dir),
        }
        result = select_task(state)
        assert result["task_queue"][0] == "worldbuild"
        assert result["quality_trace"][0]["reason"] == "enrichment_signals"

    def test_empty_signals_file_does_not_trigger(self, tmp_path):
        """Empty signals file should not trigger worldbuild."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        (universe_dir / "worldbuild_signals.json").write_text("[]", encoding="utf-8")
        state = {
            "task_queue": ["write"],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_stuck_takes_priority_over_signals(self):
        """stuck_level > 3 should take priority over signals."""
        state = {
            "task_queue": ["write"],
            "health": {"stuck_level": 5},
            "worldbuild_signals": [
                {"type": "new_element", "topic": "char", "detail": "test"},
            ],
        }
        result = select_task(state)
        assert result["task_queue"][0] == "diagnose"

    def test_user_override_takes_priority_over_signals(self):
        """User override should take priority over signals."""
        state = {
            "task_queue": ["write"],
            "health": {},
            "worldbuild_signals": [
                {"type": "new_element", "topic": "char", "detail": "test"},
            ],
            "workflow_instructions": {"next_task": "reflect"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "reflect"

    def test_no_signals_no_staleness_idles(self):
        """Without signals, staleness, or user task, daemon idles."""
        state = {
            "task_queue": [],
            "health": {},
            "canon_facts_count": 10,
            "world_state_version": 5,
            "total_chapters": 5,
        }
        result = select_task(state)
        assert result["task_queue"] == ["idle"]

    def test_corrupt_signals_file_ignored(self, tmp_path):
        """Corrupt signals file should be ignored gracefully."""
        universe_dir = tmp_path / "universe"
        universe_dir.mkdir()
        (universe_dir / "worldbuild_signals.json").write_text(
            "not valid json", encoding="utf-8"
        )
        state = {
            "task_queue": ["write"],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"

    def test_old_chapter_threshold_is_high(self):
        """Chapter-count staleness threshold should be high (low priority)."""
        # With threshold at 15, 10 chapters should NOT trigger
        state = {
            "task_queue": ["write"],
            "health": {},
            "world_state_version": 1,
            "total_chapters": 10,
            "canon_facts_count": 10,
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"


# -----------------------------------------------------------------------
# Global (cross-universe) synthesis tests
# -----------------------------------------------------------------------


class TestGlobalSynthesisQueue:
    """Tests for cross-universe synthesis scanning."""

    def test_cross_universe_synthesis_switches(self, tmp_path):
        """When idle, daemon should detect synthesis needed in another universe."""
        # Current universe: no signals
        current = tmp_path / "universe-a"
        current.mkdir()

        # Other universe: has pending synthesis
        other = tmp_path / "universe-b"
        other.mkdir()
        (other / "worldbuild_signals.json").write_text(
            json.dumps([{"type": "synthesize_source", "file": "notes.pdf"}]),
            encoding="utf-8",
        )

        state = {
            "task_queue": [],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "universe_path": str(current),
        }
        result = select_task(state)
        assert result["task_queue"][0] == "worldbuild"
        assert result.get("switch_universe") == "universe-b"
        assert "cross_universe_synthesis" in result["quality_trace"][0]["reason"]

    def test_no_cross_universe_when_all_clear(self, tmp_path):
        """When no sibling has synthesis, daemon idles."""
        current = tmp_path / "universe-a"
        current.mkdir()

        other = tmp_path / "universe-b"
        other.mkdir()
        # Empty signals
        (other / "worldbuild_signals.json").write_text("[]", encoding="utf-8")

        state = {
            "task_queue": [],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "universe_path": str(current),
        }
        result = select_task(state)
        assert result["task_queue"] == ["idle"]
        assert result.get("switch_universe") is None

    def test_cross_universe_skips_non_synthesis_signals(self, tmp_path):
        """Non-synthesis signals in other universes don't trigger switch."""
        current = tmp_path / "universe-a"
        current.mkdir()

        other = tmp_path / "universe-b"
        other.mkdir()
        (other / "worldbuild_signals.json").write_text(
            json.dumps([{"type": "new_element", "topic": "char"}]),
            encoding="utf-8",
        )

        state = {
            "task_queue": [],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "universe_path": str(current),
        }
        result = select_task(state)
        assert result["task_queue"] == ["idle"]

    def test_cross_universe_ignores_current(self, tmp_path):
        """Current universe's own signals don't trigger cross-universe switch."""
        current = tmp_path / "universe-a"
        current.mkdir()
        # Current has signals, but they're handled by the normal path (step 0)
        (current / "worldbuild_signals.json").write_text(
            json.dumps([{"type": "synthesize_source", "file": "notes.pdf"}]),
            encoding="utf-8",
        )

        state = {
            "task_queue": [],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "universe_path": str(current),
        }
        result = select_task(state)
        # Should be caught by step 0 (local synthesis), not cross-universe
        assert result["task_queue"][0] == "worldbuild"
        assert result.get("switch_universe") is None

    def test_cross_universe_no_path_idles(self):
        """Without universe_path, cross-universe scan is skipped."""
        state = {
            "task_queue": [],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 5,
        }
        result = select_task(state)
        assert result["task_queue"] == ["idle"]

    def test_user_directed_write_skips_cross_universe(self, tmp_path):
        """User-directed write takes priority over cross-universe synthesis."""
        current = tmp_path / "universe-a"
        current.mkdir()

        other = tmp_path / "universe-b"
        other.mkdir()
        (other / "worldbuild_signals.json").write_text(
            json.dumps([{"type": "synthesize_source", "file": "notes.pdf"}]),
            encoding="utf-8",
        )

        state = {
            "task_queue": ["write"],
            "health": {"cycles_completed": 1},
            "canon_facts_count": 10,
            "world_state_version": 1,
            "universe_path": str(current),
            "workflow_instructions": {"next_task": "write"},
        }
        result = select_task(state)
        assert result["task_queue"][0] == "write"
        assert result.get("switch_universe") is None


# -----------------------------------------------------------------------
# worldbuild signal-driven tests
# -----------------------------------------------------------------------


class TestWorldbuildSignalDriven:
    """Tests for signal-driven worldbuilding."""

    def _make_universe(
        self, tmp_path: Path, premise: str = "",
        canon_files: dict[str, str] | None = None,
        signals: list[dict[str, Any]] | None = None,
    ) -> Path:
        universe_dir = tmp_path / "test-universe"
        universe_dir.mkdir()
        if premise:
            (universe_dir / "PROGRAM.md").write_text(premise, encoding="utf-8")
        if canon_files:
            canon_dir = universe_dir / "canon"
            canon_dir.mkdir()
            for name, content in canon_files.items():
                (canon_dir / name).write_text(content, encoding="utf-8")
        if signals:
            (universe_dir / "worldbuild_signals.json").write_text(
                json.dumps(signals), encoding="utf-8"
            )
        return universe_dir

    def test_acts_on_signals_from_state(self, tmp_path):
        """Should act on signals from state dict."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A fantasy world with magic.",
            canon_files={"magic_system.md": "# Magic System\n\nBasic magic."},
        )
        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
            "worldbuild_signals": [
                {"type": "expansion", "topic": "magic_system",
                 "detail": "New spell types revealed"},
            ],
        }
        result = worldbuild(state)
        assert result["quality_trace"][0]["signals_acted"] > 0

    def test_acts_on_signals_from_file(self, tmp_path):
        """Should act on signals from disk file."""
        signals = [
            {"type": "new_element", "topic": "artifacts", "detail": "A golden sword"},
        ]
        universe_dir = self._make_universe(
            tmp_path,
            premise="A fantasy world.",
            canon_files={"characters.md": "# Characters\n\nHeroes."},
            signals=signals,
        )
        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        assert result["quality_trace"][0]["signals_acted"] > 0

    def test_clears_signals_after_acting(self, tmp_path):
        """Signals file should be cleared after acting on them."""
        signals = [
            {"type": "new_element", "topic": "creatures", "detail": "A dragon"},
        ]
        universe_dir = self._make_universe(
            tmp_path,
            premise="A dragon-filled world.",
            canon_files={"characters.md": "# Chars"},
            signals=signals,
        )
        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
        }
        worldbuild(state)
        # Signals migrate forward: the daemon reads the legacy file via the
        # deprecation fallback, then writes the cleared queue to the canonical
        # enrichment_signals.json (what the next cycle will read).
        signals_file = universe_dir / "enrichment_signals.json"
        data = json.loads(signals_file.read_text(encoding="utf-8"))
        assert data == []

    def test_returns_empty_worldbuild_signals(self, tmp_path):
        """Return value should have empty worldbuild_signals (consumed)."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A world.",
            signals=[{"type": "new_element", "topic": "x", "detail": "y"}],
        )
        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        assert result["worldbuild_signals"] == []

    def test_falls_back_to_gap_filling(self, tmp_path):
        """Without signals, should use gap-filling behavior."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A world with gaps.",
        )
        state = {
            "world_state_version": 0,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        assert result["quality_trace"][0]["signals_acted"] == 0
        # Should have generated gap-fill files instead
        assert len(result["quality_trace"][0]["generated_files"]) > 0

    def test_graceful_when_signal_handling_fails(self, tmp_path):
        """Signal handler failure should not crash the node."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A world.",
            signals=[{"type": "contradiction", "topic": "magic_system", "detail": "conflict"}],
        )
        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
        }
        with patch(
            "domains.fantasy_daemon.phases.worldbuild._handle_contradiction",
            side_effect=RuntimeError("LLM down"),
        ):
            result = worldbuild(state)
        # Should complete without crashing.
        # Signal-handler failure = 0 work done → version stays at the pre-cycle value (Task D).
        assert result["world_state_version"] == 1

    def test_max_signals_per_cycle(self, tmp_path):
        """Should not act on more than _MAX_DOCS_PER_CYCLE signals."""
        signals = [
            {"type": "new_element", "topic": f"topic_{i}", "detail": f"detail {i}"}
            for i in range(10)
        ]
        universe_dir = self._make_universe(
            tmp_path,
            premise="A world.",
            canon_files={"characters.md": "# Chars"},
            signals=signals,
        )
        state = {
            "world_state_version": 1,
            "_universe_path": str(universe_dir),
        }
        result = worldbuild(state)
        assert result["quality_trace"][0]["signals_acted"] <= _MAX_DOCS_PER_CYCLE


# -----------------------------------------------------------------------
# reflect signal-driven tests
# -----------------------------------------------------------------------


class TestReflectSignalDriven:
    """Tests for signal-driven reflection (no timer cooldown)."""

    def _make_universe(
        self, tmp_path: Path, premise: str = "",
        canon_files: dict[str, str] | None = None,
        signals: list[dict[str, Any]] | None = None,
    ) -> Path:
        universe_dir = tmp_path / "test-universe"
        universe_dir.mkdir()
        if premise:
            (universe_dir / "PROGRAM.md").write_text(premise, encoding="utf-8")
        if canon_files:
            canon_dir = universe_dir / "canon"
            canon_dir.mkdir()
            for name, content in canon_files.items():
                (canon_dir / name).write_text(content, encoding="utf-8")
        if signals:
            (universe_dir / "worldbuild_signals.json").write_text(
                json.dumps(signals), encoding="utf-8"
            )
        return universe_dir

    def test_reviews_all_files_without_signals(self, tmp_path):
        """Without signals, all canon files should be reviewed."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A dark fantasy world.",
            canon_files={
                "characters.md": "# Characters\n\nContent.",
                "locations.md": "# Locations\n\nContent.",
            },
        )
        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 2

    def test_focuses_on_signal_topics(self, tmp_path):
        """With signals, only matching canon files should be reviewed."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A dark fantasy world.",
            canon_files={
                "characters.md": "# Characters\n\nContent.",
                "locations.md": "# Locations\n\nContent.",
                "magic_system.md": "# Magic\n\nContent.",
            },
            signals=[
                {"type": "contradiction", "topic": "characters", "detail": "conflict"},
            ],
        )
        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        # Should only review the characters file (matching signal topic)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 1

    def test_recently_stamped_files_still_reviewed(self, tmp_path):
        """Stamped files should NOT be skipped (cooldown removed)."""
        universe_dir = self._make_universe(
            tmp_path,
            premise="A dark fantasy world.",
            canon_files={"characters.md": "# Characters\n\nContent."},
        )
        # Stamp the file
        _stamp_reviewed(universe_dir / "canon" / "characters.md")

        state = {"_universe_path": str(universe_dir)}
        result = reflect(state)
        assert result["quality_trace"][0]["canon_files_reviewed"] == 1

    def test_extract_signal_topics_from_state(self, tmp_path):
        """Should extract topics from worldbuild_signals in state."""
        universe_dir = self._make_universe(tmp_path)
        state = {
            "worldbuild_signals": [
                {"type": "new_element", "topic": "characters"},
                {"type": "expansion", "topic": "magic_system"},
            ],
        }
        topics = _extract_signal_topics(state, universe_dir)
        assert topics == {"characters", "magic_system"}

    def test_extract_signal_topics_from_file(self, tmp_path):
        """Should extract topics from signals file on disk."""
        universe_dir = self._make_universe(
            tmp_path,
            signals=[
                {"type": "contradiction", "topic": "factions"},
            ],
        )
        topics = _extract_signal_topics({}, universe_dir)
        assert topics == {"factions"}

    def test_extract_signal_topics_none_when_no_signals(self, tmp_path):
        """Should return None when no signals exist."""
        universe_dir = self._make_universe(tmp_path)
        topics = _extract_signal_topics({}, universe_dir)
        assert topics is None

    def test_model_tier_guard_still_works(self, tmp_path):
        """Model quality tier guard should still prevent weak overwrites."""
        from tinyassets import runtime_singletons as runtime

        universe_dir = self._make_universe(
            tmp_path,
            premise="A dark fantasy world.",
            canon_files={"characters.md": "# Characters\n\nContent."},
        )
        # Mark file as written by a strong model
        marker = universe_dir / "canon" / ".characters.md.reviewed"
        marker.write_text(
            json.dumps({"reviewed_at": 0, "model": "claude-code"}),
            encoding="utf-8",
        )

        # Severity 5 is too low for a tier-3 gap (ollama→claude needs ≥8)
        issues = [{"filename": "characters.md", "reason": "drift", "severity": 5}]
        # Set current provider to a weaker model.  The reflect code reads
        # state["provider"] only when runtime.memory_manager is not None.
        mgr = MagicMock()
        reflexion_result = MagicMock()
        reflexion_result.critique = ""
        reflexion_result.updated_weights = {}
        mgr.run_reflexion.return_value = reflexion_result

        runtime.memory_manager = mgr
        try:
            state = {"_universe_path": str(universe_dir), "provider": "ollama-local"}
            with patch(
                "domains.fantasy_daemon.phases.reflect._evaluate_canon",
                return_value=issues,
            ):
                result = reflect(state)
        finally:
            runtime.memory_manager = None

        # Weak model should NOT have rewritten the strong model's file
        trace = result["quality_trace"][0]
        assert "characters.md" not in trace["canon_files_rewritten"]
