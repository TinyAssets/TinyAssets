"""Tests for project-scope persistent memory primitive.

Covers:
- project_memory_set / get / list storage layer (tinyassets/memory/project.py)
- MCP action handlers via extensions() routing (project_memory_set/get/list)
- Cross-project isolation
- Size-cap rejection
- Optimistic concurrency conflict
- Monotonic version increment
- Append-only history table
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Storage layer ─────────────────────────────────────────────────────────────


class TestProjectMemorySet:
    def _base(self, tmp_path: Path) -> Path:
        return tmp_path

    def test_set_returns_ok_status(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_set

        result = project_memory_set(tmp_path, project_id="proj1", key="k1", value="hello")
        assert result["status"] == "ok"
        assert result["version"] == 1

    def test_set_increments_version(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v1")
        result = project_memory_set(tmp_path, project_id="proj1", key="k1", value="v2")
        assert result["version"] == 2

    def test_set_accepts_dict_value(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_get, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value={"a": 1, "b": [2, 3]})
        row = project_memory_get(tmp_path, project_id="proj1", key="k1")
        assert row is not None
        assert row["value"] == {"a": 1, "b": [2, 3]}

    def test_set_version_conflict_returns_conflict(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v1")
        result = project_memory_set(
            tmp_path, project_id="proj1", key="k1", value="v2", expected_version=99
        )
        assert result.get("conflict") is True
        assert result["current_version"] == 1

    def test_set_expected_version_matches_succeeds(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v1")
        result = project_memory_set(
            tmp_path, project_id="proj1", key="k1", value="v2", expected_version=1
        )
        assert result["status"] == "ok"
        assert result["version"] == 2

    def test_there_is_no_per_project_size_cap(self, tmp_path: Path) -> None:
        """Past the old 1 MB ceiling, and the write still succeeds.

        Founder, 2026-09-30: an account has exactly two limits, cloud storage and
        concurrent agent seats. Storage is measured ONCE, over the universe, by
        the tier storage gate -- a separate per-project megabyte was a second
        number for the same thing, and it refused with ``size_cap_exceeded``.
        """
        import inspect

        from tinyassets.memory import project as mod
        from tinyassets.memory.project import project_memory_get, project_memory_set

        assert not hasattr(mod, "_DEFAULT_SIZE_CAP_BYTES")
        assert "size_cap_bytes" not in inspect.signature(project_memory_set).parameters

        big = "x" * (2 * 1024 * 1024)  # 2 MiB, twice the old cap
        result = project_memory_set(tmp_path, project_id="proj1", key="k1", value=big)
        assert result["status"] == "ok", result
        assert "error" not in result
        stored = project_memory_get(tmp_path, project_id="proj1", key="k1")
        assert stored is not None and stored["value"] == big

        # And a second key on the same project, also over the old cap.
        assert project_memory_set(
            tmp_path, project_id="proj1", key="k2", value=big
        )["status"] == "ok"

    def test_set_actor_stored(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_get, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v", actor="alice")
        row = project_memory_get(tmp_path, project_id="proj1", key="k1")
        assert row is not None
        assert row["updated_by"] == "alice"


class TestProjectMemoryGet:
    def test_get_returns_none_for_missing_key(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_get

        result = project_memory_get(tmp_path, project_id="proj1", key="missing")
        assert result is None

    def test_get_returns_stored_value(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_get, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value=42)
        row = project_memory_get(tmp_path, project_id="proj1", key="k1")
        assert row is not None
        assert row["value"] == 42
        assert row["key"] == "k1"
        assert row["project_id"] == "proj1"
        assert "updated_at" in row
        assert "version" in row

    def test_get_returns_current_value_after_update(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_get, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value="old")
        project_memory_set(tmp_path, project_id="proj1", key="k1", value="new")
        row = project_memory_get(tmp_path, project_id="proj1", key="k1")
        assert row is not None
        assert row["value"] == "new"
        assert row["version"] == 2


class TestProjectMemoryList:
    def test_list_empty_when_no_keys(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_list

        result = project_memory_list(tmp_path, project_id="proj1")
        assert result == []

    def test_list_returns_all_keys(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_list, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="a", value=1)
        project_memory_set(tmp_path, project_id="proj1", key="b", value=2)
        project_memory_set(tmp_path, project_id="proj1", key="c", value=3)
        result = project_memory_list(tmp_path, project_id="proj1")
        assert len(result) == 3
        keys = {r["key"] for r in result}
        assert keys == {"a", "b", "c"}

    def test_list_key_prefix_filter(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_list, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="foo/bar", value=1)
        project_memory_set(tmp_path, project_id="proj1", key="foo/baz", value=2)
        project_memory_set(tmp_path, project_id="proj1", key="other", value=3)
        result = project_memory_list(tmp_path, project_id="proj1", key_prefix="foo/")
        assert len(result) == 2
        assert all(r["key"].startswith("foo/") for r in result)

    def test_list_respects_limit(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_list, project_memory_set

        for i in range(10):
            project_memory_set(tmp_path, project_id="proj1", key=f"k{i}", value=i)
        result = project_memory_list(tmp_path, project_id="proj1", limit=3)
        assert len(result) == 3


class TestCrossProjectIsolation:
    def test_get_does_not_read_other_project(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_get, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k", value="proj1-val")
        result = project_memory_get(tmp_path, project_id="proj2", key="k")
        assert result is None

    def test_list_does_not_include_other_project_keys(self, tmp_path: Path) -> None:
        from tinyassets.memory.project import project_memory_list, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k", value="v")
        result = project_memory_list(tmp_path, project_id="proj2")
        assert result == []

    def test_projects_stay_isolated_without_a_cap_to_separate_them(
        self, tmp_path: Path
    ) -> None:
        """The cap used to be the only per-project accounting; isolation is not.

        Reads are still scoped by project_id, which is the invariant that mattered.
        """
        from tinyassets.memory.project import project_memory_list, project_memory_set

        assert project_memory_set(
            tmp_path, project_id="proj1", key="k", value="x" * 900
        )["status"] == "ok"
        assert project_memory_set(
            tmp_path, project_id="proj2", key="k", value="y" * 900
        )["status"] == "ok"
        assert [row["value"] for row in project_memory_list(tmp_path, project_id="proj1")] == [
            "x" * 900
        ]


class TestHistoryTable:
    def test_history_retains_all_writes(self, tmp_path: Path) -> None:
        import sqlite3

        from tinyassets.memory.project import _db_path, project_memory_set

        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v1")
        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v2")
        project_memory_set(tmp_path, project_id="proj1", key="k1", value="v3")

        db = _db_path(tmp_path)
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT version FROM project_memory_history "
            "WHERE project_id = 'proj1' AND key = 'k1' ORDER BY version"
        ).fetchall()
        conn.close()
        assert [r[0] for r in rows] == [1, 2, 3]


# ── MCP action handlers ───────────────────────────────────────────────────────


class TestExtensionsProjectMemoryGet:
    def test_missing_project_id_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_get

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_project_memory_get({"project_id": "", "key": "k"}))
        assert "error" in result

    def test_missing_key_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_get

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_project_memory_get({"project_id": "p1", "key": ""}))
        assert "error" in result

    def test_get_not_found_returns_found_false(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_get

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(
            _action_project_memory_get({"project_id": "proj1", "key": "missing"})
        )
        assert result["found"] is False

    def test_get_found_returns_found_true(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_get
        from tinyassets.memory.project import project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        project_memory_set(tmp_path, project_id="proj1", key="k1", value="hello")
        result = json.loads(
            _action_project_memory_get({"project_id": "proj1", "key": "k1"})
        )
        assert result["found"] is True
        assert result["value"] == "hello"


class TestExtensionsProjectMemorySet:
    def test_missing_project_id_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(
            _action_project_memory_set({"project_id": "", "key": "k", "value": '"v"'})
        )
        assert "error" in result

    def test_set_returns_ok(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(
            _action_project_memory_set(
                {"project_id": "proj1", "key": "k1", "value": '"hello"'}
            )
        )
        assert result["status"] == "ok"
        assert result["version"] == 1

    def test_set_with_json_value_string(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import (
            _action_project_memory_get,
            _action_project_memory_set,
        )

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _action_project_memory_set(
            {"project_id": "proj1", "key": "k1", "value": '{"x": 99}'}
        )
        result = json.loads(
            _action_project_memory_get({"project_id": "proj1", "key": "k1"})
        )
        assert result["value"] == {"x": 99}

    def test_set_conflict_on_wrong_version(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _action_project_memory_set(
            {"project_id": "proj1", "key": "k1", "value": '"v1"'}
        )
        result = json.loads(
            _action_project_memory_set(
                {
                    "project_id": "proj1",
                    "key": "k1",
                    "value": '"v2"',
                    "expected_version": "99",
                }
            )
        )
        assert result.get("conflict") is True

    def test_invalid_expected_version_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(
            _action_project_memory_set(
                {
                    "project_id": "proj1",
                    "key": "k1",
                    "value": '"v"',
                    "expected_version": "not-a-number",
                }
            )
        )
        assert "error" in result


class TestExtensionsProjectMemoryList:
    def test_missing_project_id_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_list

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_project_memory_list({"project_id": ""}))
        assert "error" in result

    def test_list_empty_project(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_list

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_project_memory_list({"project_id": "proj1"}))
        assert result["entries"] == []
        assert result["count"] == 0

    def test_list_returns_entries(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_list
        from tinyassets.memory.project import project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        project_memory_set(tmp_path, project_id="proj1", key="a", value=1)
        project_memory_set(tmp_path, project_id="proj1", key="b", value=2)
        result = json.loads(_action_project_memory_list({"project_id": "proj1"}))
        assert result["count"] == 2
        assert len(result["entries"]) == 2

    def test_list_prefix_filter_forwarded(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_project_memory_list
        from tinyassets.memory.project import project_memory_set

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        project_memory_set(tmp_path, project_id="proj1", key="foo/1", value=1)
        project_memory_set(tmp_path, project_id="proj1", key="foo/2", value=2)
        project_memory_set(tmp_path, project_id="proj1", key="bar/1", value=3)
        result = json.loads(
            _action_project_memory_list({"project_id": "proj1", "key_prefix": "foo/"})
        )
        assert result["count"] == 2
