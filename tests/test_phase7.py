"""Tests for Phase 7 polish modules: ingestion, versioning, promises."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tinyassets.memory.ingestion import (
    IngestionPriority,
    ProgressiveIngestor,
)
from tinyassets.memory.promises import SeriesPromiseTracker
from tinyassets.memory.versioning import OutputVersionStore

# =====================================================================
# ProgressiveIngestor
# =====================================================================


class TestProgressiveIngestor:
    def _make_canon_dir(self, tmp_path: Path) -> Path:
        canon = tmp_path / "canon"
        canon.mkdir()
        (canon / "world.md").write_text(
            "# Characters\n\nRyn is a scout.\n\n"
            "# Magic System\n\nThe Web connects all things.\n\n"
            "# Locations\n\nThe Northern Gate guards the pass.\n",
            encoding="utf-8",
        )
        (canon / "notes.txt").write_text(
            "Some plain text notes about the story.\n",
            encoding="utf-8",
        )
        return canon

    def test_survey_finds_files(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        state = ingestor.survey()
        assert state.total_files == 2
        assert state.total_sections > 0
        assert state.survey_complete

    def test_survey_splits_sections(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        ingestor.survey()
        # world.md has 3 headings (Characters, Magic System, Locations)
        # plus potentially a pre-heading section
        headings = [s.heading for s in ingestor.state.sections]
        assert "Characters" in headings
        assert "Magic System" in headings

    def test_triage_assigns_priorities(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        ingestor.survey()
        ingestor.triage(
            immediate_keywords=["characters"],
            background_keywords=["magic"],
        )
        for s in ingestor.state.sections:
            if "characters" in s.heading.lower():
                assert s.priority == IngestionPriority.IMMEDIATE
            elif "magic" in s.heading.lower():
                assert s.priority == IngestionPriority.BACKGROUND

    def test_get_next_batch(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        ingestor.survey()
        ingestor.triage(immediate_keywords=["characters"])
        batch = ingestor.get_next_batch(IngestionPriority.IMMEDIATE)
        assert len(batch) >= 1
        assert all(
            s.priority == IngestionPriority.IMMEDIATE for s in batch
        )

    def test_mark_ingested(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        ingestor.survey()
        section = ingestor.state.sections[0]
        ingestor.mark_ingested(section)
        assert section.ingested
        assert ingestor.state.ingested_sections == 1

    def test_check_for_new_files(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        ingestor.survey()
        initial_count = ingestor.state.total_sections

        # Add a new file.
        (canon / "extra.md").write_text(
            "# New Content\n\nSome new material.\n",
            encoding="utf-8",
        )
        new_files = ingestor.check_for_new_files()
        assert len(new_files) == 1
        assert ingestor.state.total_sections > initial_count

    def test_survey_nonexistent_dir(self):
        ingestor = ProgressiveIngestor("/nonexistent/path", "test")
        state = ingestor.survey()
        assert state.total_files == 0

    def test_progress_tracking(self, tmp_path: Path):
        canon = self._make_canon_dir(tmp_path)
        ingestor = ProgressiveIngestor(canon, "test")
        ingestor.survey()
        assert ingestor.state.progress == 0.0
        for s in ingestor.state.sections:
            ingestor.mark_ingested(s)
        assert ingestor.state.progress == 1.0


# =====================================================================
# OutputVersionStore
# =====================================================================


class TestOutputVersionStore:
    def test_save_and_get_current(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        v = store.save_draft(1, 1, 1, "Draft one", verdict="accept")
        assert v == 1
        current = store.get_current(1, 1, 1)
        assert current is not None
        assert current.prose == "Draft one"
        assert current.is_current

    def test_multiple_versions(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "Draft one")
        store.save_draft(1, 1, 1, "Draft two")
        store.save_draft(1, 1, 1, "Draft three")

        current = store.get_current(1, 1, 1)
        assert current.version == 3
        assert current.prose == "Draft three"

        all_v = store.get_all_versions(1, 1, 1)
        assert len(all_v) == 3

    def test_rollback(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "Draft one")
        store.save_draft(1, 1, 1, "Draft two")

        rolled = store.rollback(1, 1, 1, to_version=1)
        assert rolled is not None
        assert rolled.prose == "Draft one"
        assert rolled.is_current

        current = store.get_current(1, 1, 1)
        assert current.version == 1

    def test_rollback_nonexistent(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        result = store.rollback(1, 1, 1, to_version=99)
        assert result is None

    def test_version_count(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        assert store.version_count(1, 1, 1) == 0
        store.save_draft(1, 1, 1, "draft")
        assert store.version_count(1, 1, 1) == 1

    def test_get_specific_version(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "version one")
        store.save_draft(1, 1, 1, "version two")
        v1 = store.get_version(1, 1, 1, 1)
        assert v1 is not None
        assert v1.prose == "version one"

    def test_quality_score_stored(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "draft", quality_score=0.85)
        current = store.get_current(1, 1, 1)
        assert current.quality_score == 0.85

    def test_metadata_stored(self, canonical_source):
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "draft", metadata={"key": "value"})
        current = store.get_current(1, 1, 1)
        assert current.metadata == {"key": "value"}


# =====================================================================
# SeriesPromiseTracker
# =====================================================================


class TestSeriesPromiseTracker:
    def test_create_and_query(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("p1", "Hero finds the sword", 1, 3)
        open_promises = tracker.get_open_promises()
        assert len(open_promises) == 1
        assert open_promises[0].promise_id == "p1"

    def test_resolve_promise(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("p1", "Hero finds the sword", 1, 3)
        tracker.resolve_promise("p1", book=2, chapter=10)
        open_promises = tracker.get_open_promises()
        assert len(open_promises) == 0

        all_promises = tracker.get_all_promises()
        assert len(all_promises) == 1
        assert all_promises[0].status == "resolved"

    def test_add_evidence(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("p1", "Sword quest", 1, 1)
        tracker.add_evidence("p1", {"scene": "b1c3s1", "note": "mentioned"})
        all_p = tracker.get_all_promises()
        assert len(all_p[0].evidence) == 1

    def test_overdue_promises(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("old", "Ancient promise", 1, 1)
        tracker.create_promise("new", "Recent promise", 3, 25)
        overdue = tracker.get_overdue_promises(
            current_book=3, current_chapter=30, max_age_chapters=20
        )
        assert len(overdue) == 1
        assert overdue[0].promise_id == "old"

    def test_filter_by_book(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("p1", "Book 1 promise", 1, 1)
        tracker.create_promise("p2", "Book 3 promise", 3, 1)
        book1 = tracker.get_open_promises(book=1)
        assert len(book1) == 1
        assert book1[0].promise_id == "p1"

    def test_promote_from_book(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        promises = [
            {"id": "p1", "description": "Promise one", "chapter": 5},
            {"id": "p2", "description": "Promise two", "chapter": 10},
        ]
        created = tracker.promote_from_book(promises, book=1)
        assert created == 2
        assert len(tracker.get_open_promises()) == 2

        # Promoting again should not duplicate.
        created = tracker.promote_from_book(promises, book=1)
        assert created == 0

    def test_duplicate_create_ignored(self, canonical_source):
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("p1", "Promise", 1, 1)
        tracker.create_promise("p1", "Duplicate", 1, 1)
        all_p = tracker.get_all_promises()
        assert len(all_p) == 1
        assert all_p[0].description == "Promise"  # Original kept


# =====================================================================
# Integration: Wiring tests
# =====================================================================


class TestCommitVersionStoreWiring:
    """Test that commit node saves drafts to version store."""

    def test_save_to_version_store_called(self, canonical_source):
        from domains.fantasy_daemon.phases.commit import _save_to_version_store
        from tinyassets import runtime_singletons as runtime

        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        state = {
            "book_number": 1,
            "chapter_number": 2,
            "scene_number": 3,
        }
        runtime.version_store = store
        try:
            _save_to_version_store(state, "Some prose here.", "accept", 0.9)
        finally:
            runtime.version_store = None

        current = store.get_current(1, 2, 3)
        assert current is not None
        assert current.prose == "Some prose here."
        assert current.verdict == "accept"
        assert current.quality_score == 0.9

    def test_save_to_version_store_absent(self):
        """No crash when version_store is not set."""
        from domains.fantasy_daemon.phases.commit import _save_to_version_store
        from tinyassets import runtime_singletons as runtime

        runtime.version_store = None
        _save_to_version_store({}, "prose", "accept", 0.8)  # should not raise

    def test_save_to_version_store_error_handled(self):
        """Errors in version store are caught gracefully."""
        from domains.fantasy_daemon.phases.commit import _save_to_version_store
        from tinyassets import runtime_singletons as runtime

        broken_store = MagicMock()
        broken_store.save_draft.side_effect = RuntimeError("DB error")
        runtime.version_store = broken_store
        try:
            _save_to_version_store({}, "prose", "accept", 0.5)  # should not raise
        finally:
            runtime.version_store = None


class TestBookClosePromiseWiring:
    """Test that book_close promotes promises to series level."""

    def test_promote_promises_on_close(self, canonical_source):
        from domains.fantasy_daemon.phases.book_close import book_close
        from tinyassets import runtime_singletons as runtime

        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        state = {
            "book_number": 1,
            "chapters_completed": 10,
            "extracted_promises": [
                {"id": "p1", "description": "Hero finds sword", "chapter": 5},
                {"id": "p2", "description": "Villain returns", "chapter": 8},
            ],
        }
        runtime.memory_manager = None
        runtime.promise_tracker = tracker
        try:
            result = book_close(state)
        finally:
            runtime.memory_manager = None
            runtime.promise_tracker = None
        assert "book_summary" in result

        open_promises = tracker.get_open_promises()
        assert len(open_promises) == 2

    def test_book_close_no_tracker(self):
        """No crash when promise_tracker is not set."""
        from domains.fantasy_daemon.phases.book_close import book_close
        from tinyassets import runtime_singletons as runtime

        runtime.memory_manager = None
        runtime.promise_tracker = None
        result = book_close({
            "book_number": 1,
            "chapters_completed": 5,
        })
        assert "book_summary" in result

    def test_book_close_empty_promises(self, canonical_source):
        """Graceful handling when no promises to promote."""
        from domains.fantasy_daemon.phases.book_close import book_close
        from tinyassets import runtime_singletons as runtime

        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        runtime.memory_manager = None
        runtime.promise_tracker = tracker
        try:
            result = book_close({
                "book_number": 1,
                "chapters_completed": 5,
                "extracted_promises": [],
            })
        finally:
            runtime.memory_manager = None
            runtime.promise_tracker = None
        assert "book_summary" in result
        assert len(tracker.get_all_promises()) == 0


# Removed 2026-04-13: TestDaemonControllerIngestion referenced methods
# that never existed on `DaemonController`:
#   - `_bootstrap_universe_runtime_files`
#   - `_bootstrap_retrieval_indices`
#   - `_run_progressive_ingestion`
# `git log --all -S` for each of those names returns nothing. These are
# not accidentally removed production methods — they were never in
# version control. Deleted per planner decision memo
# `orphaned_test_phase7_decision.md (archived in git)`. Do not resurrect.


pytestmark = pytest.mark.usefixtures("canonical_runtime")
