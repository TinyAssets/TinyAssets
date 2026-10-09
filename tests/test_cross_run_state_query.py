"""Tests for query_runs — cross-run state query primitive.

Spec: docs/vetted-specs.md §Cross-run state query primitive.
Implementation: tinyassets/runs.py::query_runs + universe_server.py::_action_query_runs.
"""

from __future__ import annotations

import uuid
from pathlib import Path

# ── helpers ───────────────────────────────────────────────────────────────────

def _init(base_path: Path) -> None:
    from tinyassets.daemon_server import initialize_author_server
    initialize_author_server(base_path)


def _seed_run(
    base_path: Path,
    *,
    branch_def_id: str = "b1",
    status: str = "completed",
    actor: str = "alice",
    output: dict | None = None,
) -> str:
    from tinyassets.runs import create_run, update_run_status
    run_id = create_run(
        base_path,
        branch_def_id=branch_def_id,
        thread_id=uuid.uuid4().hex,
        inputs={},
        run_name="test",
        actor=actor,
    )
    if status != "queued":
        update_run_status(base_path, run_id, status=status, output=output or {})
    return run_id


# ── Per-universe ACL (private-universe runs must not leak) ──────────────────────


# ── Basic query shape ─────────────────────────────────────────────────────────


# ── Status filter ─────────────────────────────────────────────────────────────


# ── Select field projection ───────────────────────────────────────────────────


# ── Limit ─────────────────────────────────────────────────────────────────────


# ── Aggregation ───────────────────────────────────────────────────────────────


# ── INTERRUPTED exclusion invariant ──────────────────────────────────────────


# ── Pure query_runs unit tests ────────────────────────────────────────────────

class TestQueryRunsUnit:
    def test_query_runs_direct_call_empty(self, tmp_path):
        from tinyassets.runs import query_runs
        _init(tmp_path)
        result = query_runs(tmp_path)
        assert result["rows"] == []
        assert result["count"] == 0

    def test_query_runs_returns_seeded_run(self, tmp_path):
        from tinyassets.runs import query_runs
        _init(tmp_path)
        _seed_run(tmp_path, branch_def_id="b1")
        result = query_runs(tmp_path, branch_def_id="b1")
        assert result["count"] == 1

    def test_query_runs_limit_enforced(self, tmp_path):
        from tinyassets.runs import query_runs
        _init(tmp_path)
        for _ in range(10):
            _seed_run(tmp_path)
        result = query_runs(tmp_path, limit=3)
        assert result["count"] == 3

    def test_query_runs_max_limit_1000(self, tmp_path):
        from tinyassets.runs import query_runs
        _init(tmp_path)
        result = query_runs(tmp_path, limit=99999)
        assert result["count"] == 0  # empty db; just confirming no crash

    def test_query_runs_select_projection(self, tmp_path):
        from tinyassets.runs import query_runs
        _init(tmp_path)
        _seed_run(tmp_path, output={"x": 7})
        result = query_runs(tmp_path, select=["x"])
        assert result["rows"][0]["fields"]["x"] == 7

    def test_query_runs_aggregate_count(self, tmp_path):
        from tinyassets.runs import query_runs
        _init(tmp_path)
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = query_runs(
            tmp_path,
            aggregate={"fn": "count", "group_by": "status"},
        )
        assert "aggregated" in result
        groups = {r["group"]: r["value"] for r in result["aggregated"]}
        assert groups["completed"] == 1
        assert groups["failed"] == 1
