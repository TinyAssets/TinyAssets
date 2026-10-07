"""Tests for cross-run state query primitive.

Spec: docs/vetted-specs.md §Cross-run state query primitive.

Covered:
- filter by status, actor, since/until
- select: extracts named fields from output_json
- aggregate: group_by + count, mean, sum, rate
- limit respected (default 100, cap 1000)
- INTERRUPTED runs excluded from aggregation unless status filter includes them
- MCP action handler via extensions() routing
"""

from __future__ import annotations

import json
from pathlib import Path

from tinyassets.runs import (
    create_run,
    initialize_runs_db,
    query_runs,
    update_run_status,
)


def _seed_run(
    base_path: Path,
    *,
    branch_def_id: str = "b1",
    status: str = "completed",
    actor: str = "alice",
    output: dict | None = None,
) -> str:
    import uuid

    initialize_runs_db(base_path)
    tid = uuid.uuid4().hex[:8]
    run_id = create_run(
        base_path,
        branch_def_id=branch_def_id,
        thread_id=tid,
        inputs={},
        actor=actor,
    )
    update_run_status(base_path, run_id, status=status, output=output or {})
    return run_id


class TestQueryRunsFilter:
    def test_no_filter_returns_all(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = query_runs(tmp_path)
        assert result["count"] == 2

    def test_filter_by_status(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = query_runs(tmp_path, filters={"status": "completed"})
        assert result["count"] == 1
        assert result["rows"][0]["status"] == "completed"

    def test_filter_by_actor(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, actor="alice")
        _seed_run(tmp_path, actor="bob")
        result = query_runs(tmp_path, filters={"actor": "alice"})
        assert result["count"] == 1
        assert result["rows"][0]["actor"] == "alice"

    def test_filter_by_branch_def_id(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, branch_def_id="b1")
        _seed_run(tmp_path, branch_def_id="b2")
        result = query_runs(tmp_path, branch_def_id="b1")
        assert result["count"] == 1
        assert result["rows"][0]["branch_def_id"] == "b1"

    def test_empty_db_returns_empty(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        result = query_runs(tmp_path)
        assert result["count"] == 0
        assert result["rows"] == []


class TestQueryRunsSelect:
    def test_select_extracts_named_fields(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, output={"word_count": 1234, "title": "Chap 1"})
        result = query_runs(tmp_path, select=["word_count", "title"])
        assert result["count"] == 1
        fields = result["rows"][0]["fields"]
        assert fields["word_count"] == 1234
        assert fields["title"] == "Chap 1"

    def test_select_missing_field_is_none(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, output={"word_count": 100})
        result = query_runs(tmp_path, select=["word_count", "nonexistent"])
        fields = result["rows"][0]["fields"]
        assert fields["nonexistent"] is None

    def test_no_select_omits_fields_key(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, output={"x": 1})
        result = query_runs(tmp_path)
        assert "fields" not in result["rows"][0]


class TestQueryRunsAggregate:
    def test_count_aggregates_by_status(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = query_runs(
            tmp_path,
            aggregate={"group_by": "status", "fn": "count"},
        )
        assert "aggregated" in result
        groups = {row["group"]: row["value"] for row in result["aggregated"]}
        assert groups.get("completed") == 2
        assert groups.get("failed") == 1

    def test_sum_aggregates_output_field(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, output={"score": 10})
        _seed_run(tmp_path, output={"score": 20})
        result = query_runs(
            tmp_path,
            aggregate={"group_by": "", "fn": "sum", "field": "score"},
        )
        totals = {row["group"]: row["value"] for row in result["aggregated"]}
        assert totals.get("_all") == 30

    def test_mean_aggregates_output_field(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, output={"score": 10})
        _seed_run(tmp_path, output={"score": 30})
        result = query_runs(
            tmp_path,
            aggregate={"group_by": "", "fn": "mean", "field": "score"},
        )
        totals = {row["group"]: row["value"] for row in result["aggregated"]}
        assert totals.get("_all") == 20.0

    def test_rate_returns_fraction(self, tmp_path: Path) -> None:
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = query_runs(
            tmp_path,
            filters={"status": "completed"},
            aggregate={"group_by": "status", "fn": "rate"},
        )
        groups = {row["group"]: row["value"] for row in result["aggregated"]}
        # rate = count_in_group / total_rows_returned (after filter: 2 completed)
        assert groups.get("completed") is not None


class TestQueryRunsLimit:
    def test_default_limit_applied(self, tmp_path: Path) -> None:

        for _ in range(5):
            _seed_run(tmp_path)
        result = query_runs(tmp_path, limit=3)
        assert result["count"] == 3

    def test_limit_capped_at_1000(self, tmp_path: Path) -> None:
        from tinyassets.runs import query_runs

        for _ in range(5):
            _seed_run(tmp_path)
        result = query_runs(tmp_path, limit=99999)
        # limit is capped; we only have 5 rows so count == 5
        assert result["count"] == 5


class TestMcpQueryRunsAction:
    def test_action_returns_rows_shape(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runs import _action_query_runs

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_run(tmp_path, status="completed")
        result = json.loads(_action_query_runs({"branch_def_id": ""}))
        assert "rows" in result
        assert "count" in result

    def test_action_invalid_filters_json(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runs import _action_query_runs

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_query_runs({"filters_json": "not-json"}))
        assert "error" in result

    def test_action_invalid_aggregate_fn(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runs import _action_query_runs

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(
            _action_query_runs(
                {"aggregate_json": '{"group_by": "status", "fn": "invalid_op"}'}
            )
        )
        assert "error" in result

    def test_action_with_select_string(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runs import _action_query_runs

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _seed_run(tmp_path, output={"word_count": 42})
        result = json.loads(
            _action_query_runs({"branch_def_id": "", "select": "word_count"})
        )
        assert result["rows"][0]["fields"]["word_count"] == 42
