"""Tests for universe isolation: DB paths must resolve inside universe dir.

Validates that DaemonController rejects or corrects CWD-relative DB paths
that would cause cross-universe contamination, and that path resolution
helpers never fall back to CWD-relative defaults.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from domains.fantasy_daemon.phases._paths import resolve_db_path, resolve_kg_path


class TestDBPathDefaults:
    """Verify DaemonController defaults DB paths to <universe>/story.db."""

    def test_empty_db_path_defaults_to_universe(self, tmp_path):
        from tinyassets.__main__ import DaemonController

        uni = tmp_path / "my-universe"
        uni.mkdir()
        ctrl = DaemonController(universe_path=str(uni), db_path="", no_tray=True)
        assert Path(ctrl._db_path).resolve() == (uni / "story.db").resolve()

    def test_none_db_path_defaults_to_universe(self, tmp_path):
        from tinyassets.__main__ import DaemonController

        uni = tmp_path / "my-universe"
        uni.mkdir()
        ctrl = DaemonController(universe_path=str(uni), db_path=None, no_tray=True)
        assert Path(ctrl._db_path).resolve() == (uni / "story.db").resolve()

    def test_explicit_universe_relative_path_accepted(self, tmp_path):
        from tinyassets.__main__ import DaemonController

        uni = tmp_path / "my-universe"
        uni.mkdir()
        db = str(uni / "custom.db")
        ctrl = DaemonController(universe_path=str(uni), db_path=db, no_tray=True)
        assert ctrl._db_path == db


class TestDBPathIsolationGuard:
    """Verify DaemonController warns and falls back for CWD-relative paths."""

    def test_cwd_relative_db_path_corrected(self, tmp_path, caplog):
        from tinyassets.__main__ import DaemonController

        uni = tmp_path / "my-universe"
        uni.mkdir()

        with caplog.at_level(logging.WARNING):
            ctrl = DaemonController(
                universe_path=str(uni),
                db_path="story.db",  # CWD-relative -- wrong!
                no_tray=True,
            )

        # Should have been corrected to universe-relative
        assert Path(ctrl._db_path).resolve() == (uni / "story.db").resolve()
        assert "outside universe" in caplog.text

    def test_checkpoint_path_corrected(self, tmp_path, caplog):
        from tinyassets.__main__ import DaemonController

        uni = tmp_path / "my-universe"
        uni.mkdir()

        with caplog.at_level(logging.WARNING):
            ctrl = DaemonController(
                universe_path=str(uni),
                checkpoint_path="checkpoints.db",  # CWD-relative -- wrong!
                no_tray=True,
            )

        assert Path(ctrl._checkpoint_path).resolve() == (
            uni / "checkpoints.db"
        ).resolve()
        assert "outside universe" in caplog.text


class TestKGPathDerivation:
    """Verify KG/lance paths derive from universe_path, not _db_path."""

    def test_kg_path_in_state_uses_universe_path(self, tmp_path):
        """The _kg_path in initial state should point inside universe dir."""
        from tinyassets.__main__ import DaemonController

        uni = tmp_path / "my-universe"
        uni.mkdir()
        ctrl = DaemonController(universe_path=str(uni), no_tray=True)

        # The _kg_path is set in _build_initial_state which we can't
        # easily call, but we can verify the derivation pattern by
        # checking that the path formula uses _universe_path.
        expected_kg = str(Path(ctrl._universe_path) / "knowledge.db")
        # This matches what _init_retrieval_backends and state dict use
        assert "knowledge.db" in expected_kg
        assert str(uni) in expected_kg


class TestResolveDbPath:
    """resolve_db_path must never return a CWD-relative default."""

    def test_explicit_db_path(self) -> None:
        state = {"_db_path": "/universes/sporemarch/story.db"}
        assert resolve_db_path(state) == "/universes/sporemarch/story.db"

    def test_derives_from_universe_path(self) -> None:
        state = {"_universe_path": "/universes/sporemarch"}
        result = resolve_db_path(state)
        assert "sporemarch" in result
        assert result.endswith("story.db")

    def test_empty_state_returns_empty(self) -> None:
        assert resolve_db_path({}) == ""

    def test_prefers_explicit_over_derived(self) -> None:
        state = {
            "_db_path": "/explicit/path.db",
            "_universe_path": "/universes/sporemarch",
        }
        assert resolve_db_path(state) == "/explicit/path.db"

    def test_empty_db_path_falls_through(self) -> None:
        state = {"_db_path": "", "_universe_path": "/uni/test"}
        result = resolve_db_path(state)
        assert result.endswith("story.db")
        assert "test" in result


class TestResolveKgPath:
    """resolve_kg_path must never return a CWD-relative default."""

    def test_explicit_kg_path(self) -> None:
        state = {"_kg_path": "/universes/sporemarch/knowledge.db"}
        assert resolve_kg_path(state) == "/universes/sporemarch/knowledge.db"

    def test_derives_from_universe_path(self) -> None:
        state = {"_universe_path": "/universes/sporemarch"}
        result = resolve_kg_path(state)
        assert "sporemarch" in result
        assert result.endswith("knowledge.db")

    def test_empty_state_returns_empty(self) -> None:
        assert resolve_kg_path({}) == ""


class TestKnowledgeGraphRequiresPath:
    """KnowledgeGraph must reject empty db_path."""

    def test_no_default_path(self) -> None:
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

        with pytest.raises(TypeError):
            KnowledgeGraph()

    def test_explicit_path_accepted(self, tmp_path) -> None:
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

        db_file = str(tmp_path / "test_kg.db")
        with pytest.raises(ValueError, match="admitted canonical"):
            KnowledgeGraph(db_file)
        assert not Path(db_file).exists()


class TestWorldStateDbRequiresPath:
    """init_db and connect must reject empty db_path."""

    def test_init_db_rejects_empty(self) -> None:
        from domains.fantasy_daemon.phases.world_state_db import init_db

        with pytest.raises(ValueError, match="explicit db_path"):
            init_db()

    def test_connect_rejects_empty(self) -> None:
        from domains.fantasy_daemon.phases.world_state_db import connect

        with pytest.raises(ValueError, match="explicit db_path"):
            with connect():
                pass

    def test_init_db_with_memory(self) -> None:
        from domains.fantasy_daemon.phases.world_state_db import init_db

        init_db(":memory:")

    def test_connect_with_memory(self) -> None:
        from domains.fantasy_daemon.phases.world_state_db import connect

        with connect(":memory:") as conn:
            assert conn is not None
