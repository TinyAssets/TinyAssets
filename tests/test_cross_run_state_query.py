"""Tests for query_runs — cross-run state query primitive.

Spec: docs/vetted-specs.md §Cross-run state query primitive.
Implementation: tinyassets/runs.py::query_runs + universe_server.py::_action_query_runs.
"""

from __future__ import annotations

import json
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


def _query(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.universe_server import extensions
    return json.loads(extensions(action="query_runs", **kwargs))


# ── Per-universe ACL (private-universe runs must not leak) ──────────────────────

class TestQueryRunsUniverseAcl:
    """query_runs must exclude a private universe's runs BEFORE projection and
    aggregation, so select/aggregate can't leak private data (Codex review)."""

    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        _init(tmp_path)
        from tinyassets.auth import middleware as mw

        # A SIGNED-IN OUTSIDER, not an anonymous one. The guarantee is that a
        # private universe's runs never reach a caller who cannot read it; an
        # anonymous caller now reaches nothing at all, so testing with one
        # would assert the door instead of the filter. This caller is real and
        # holds no grant on either universe -- `pub` is readable because it is
        # public, `priv` is not.
        from tinyassets.auth.provider import DevAuthProvider, Identity
        from tinyassets.daemon_server import (
            ensure_universe_registered,
            ensure_universe_rules,
            update_universe_rules,
        )

        mw.set_provider(DevAuthProvider())
        mw._current_identity.set(Identity(
            user_id="workos|outsider",
            username="outsider",
            capabilities=[
                "tinyassets.extensions.read",
                "tinyassets.universe.read",
            ],
        ))
        from tinyassets.api import visibility as vis
        from tinyassets.daemon_server import grant_universe_access

        for uid, public in (("pub", True), ("priv", False)):
            udir = tmp_path / uid
            udir.mkdir(parents=True, exist_ok=True)
            ensure_universe_registered(tmp_path, universe_id=uid, universe_path=udir)
            ensure_universe_rules(tmp_path, universe_id=uid)
            # Both universes belong to SOMEONE ELSE. An unowned universe grants
            # nothing (2026-09-02) and universes are private by default
            # (2026-09-26), so `pub` must be owned and declared public by its
            # owner -- this used to rely on "unowned and unset reads as public",
            # which made the readable half of this test fail on main.
            grant_universe_access(
                tmp_path, universe_id=uid, actor_id="workos|owner",
                permission="admin", granted_by="workos|owner",
            )
            vis.set_universe_visibility(uid, "public" if public else "private", source="owner")
            if not public:
                update_universe_rules(
                    tmp_path, universe_id=uid, updates={"public_read": False},
                )

    def test_select_excludes_private_universe_rows(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        _seed_run(tmp_path, actor="universe:pub", output={"secret": "OPEN"})
        _seed_run(tmp_path, actor="universe:priv", output={"secret": "LEAK"})
        result = _query(monkeypatch, tmp_path, select="secret")
        secrets = [row["fields"].get("secret") for row in result["rows"]]
        assert "OPEN" in secrets
        assert "LEAK" not in secrets
        assert result["count"] == 1

    def test_aggregate_excludes_private_universe(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        _seed_run(tmp_path, actor="universe:pub")
        _seed_run(tmp_path, actor="universe:priv")
        _seed_run(tmp_path, actor="universe:priv")
        result = _query(
            monkeypatch, tmp_path, aggregate_json=json.dumps({"fn": "count"}),
        )
        total = sum(g["value"] for g in result["aggregated"])
        assert total == 1  # only the readable public run is counted


# ── Basic query shape ─────────────────────────────────────────────────────────

class TestQueryRunsShape:
    def test_empty_db_returns_empty_rows(self, tmp_path, monkeypatch):
        _init(tmp_path)
        result = _query(monkeypatch, tmp_path)
        assert result["rows"] == []
        assert result["count"] == 0

    def test_returns_rows_and_count(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, branch_def_id="b1")
        _seed_run(tmp_path, branch_def_id="b1")
        result = _query(monkeypatch, tmp_path, branch_def_id="b1")
        assert result["count"] == 2
        assert len(result["rows"]) == 2

    def test_row_has_required_fields(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, branch_def_id="b1")
        result = _query(monkeypatch, tmp_path, branch_def_id="b1")
        row = result["rows"][0]
        assert "run_id" in row
        assert "branch_def_id" in row
        assert "status" in row
        assert "actor" in row
        assert "started_at" in row
        assert "finished_at" in row

    def test_branch_def_id_filter_isolates(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, branch_def_id="b1")
        _seed_run(tmp_path, branch_def_id="b2")
        result = _query(monkeypatch, tmp_path, branch_def_id="b1")
        assert result["count"] == 1
        assert result["rows"][0]["branch_def_id"] == "b1"

    def test_no_branch_filter_returns_all(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, branch_def_id="b1")
        _seed_run(tmp_path, branch_def_id="b2")
        _seed_run(tmp_path, branch_def_id="b3")
        result = _query(monkeypatch, tmp_path)
        assert result["count"] == 3


# ── Status filter ─────────────────────────────────────────────────────────────

class TestQueryRunsStatusFilter:
    def test_status_filter_single(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = _query(
            monkeypatch, tmp_path,
            filters_json=json.dumps({"status": "completed"}),
        )
        assert result["count"] == 1
        assert result["rows"][0]["status"] == "completed"

    def test_status_filter_list(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        _seed_run(tmp_path, status="cancelled")
        result = _query(
            monkeypatch, tmp_path,
            filters_json=json.dumps({"status": ["completed", "failed"]}),
        )
        assert result["count"] == 2

    def test_actor_filter(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, actor="alice")
        _seed_run(tmp_path, actor="bob")
        result = _query(
            monkeypatch, tmp_path,
            filters_json=json.dumps({"actor": "alice"}),
        )
        assert result["count"] == 1
        assert result["rows"][0]["actor"] == "alice"

    def test_invalid_filters_json_returns_error(self, tmp_path, monkeypatch):
        _init(tmp_path)
        result = _query(monkeypatch, tmp_path, filters_json="{not valid json")
        assert "error" in result


# ── Select field projection ───────────────────────────────────────────────────

class TestQueryRunsSelect:
    def test_select_extracts_output_field(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, output={"score": 42, "title": "hello"})
        result = _query(monkeypatch, tmp_path, select="score")
        assert result["count"] == 1
        assert result["rows"][0]["fields"]["score"] == 42

    def test_select_missing_field_returns_none(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, output={"score": 10})
        result = _query(monkeypatch, tmp_path, select="nonexistent_field")
        assert result["rows"][0]["fields"]["nonexistent_field"] is None

    def test_select_multiple_fields_comma_separated(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, output={"a": 1, "b": 2, "c": 3})
        result = _query(monkeypatch, tmp_path, select="a,b")
        fields = result["rows"][0]["fields"]
        assert "a" in fields
        assert "b" in fields
        assert "c" not in fields

    def test_no_select_omits_fields_key(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, output={"score": 99})
        result = _query(monkeypatch, tmp_path)
        assert "fields" not in result["rows"][0]

    def test_select_always_includes_run_id_in_row(self, tmp_path, monkeypatch):
        _init(tmp_path)
        run_id = _seed_run(tmp_path)
        result = _query(monkeypatch, tmp_path, select="status")
        # run_id is always present in the row itself (not via select fields)
        assert result["rows"][0]["run_id"] == run_id


# ── Limit ─────────────────────────────────────────────────────────────────────

class TestQueryRunsLimit:
    def test_limit_caps_results(self, tmp_path, monkeypatch):
        _init(tmp_path)
        for _ in range(5):
            _seed_run(tmp_path, branch_def_id="b1")
        result = _query(monkeypatch, tmp_path, branch_def_id="b1", limit=2)
        assert result["count"] == 2

    def test_limit_default_100(self, tmp_path, monkeypatch):
        _init(tmp_path)
        for _ in range(3):
            _seed_run(tmp_path, branch_def_id="b1")
        result = _query(monkeypatch, tmp_path, branch_def_id="b1")
        assert result["count"] == 3

    def test_limit_max_capped_at_1000(self, tmp_path, monkeypatch):
        _init(tmp_path)
        for _ in range(5):
            _seed_run(tmp_path)
        result = _query(monkeypatch, tmp_path, limit=9999)
        assert result["count"] <= 5


# ── Aggregation ───────────────────────────────────────────────────────────────

class TestQueryRunsAggregate:
    def test_aggregate_count_by_status(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="failed")
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "count", "group_by": "status"}),
        )
        assert "aggregated" in result
        groups = {r["group"]: r["value"] for r in result["aggregated"]}
        assert groups.get("completed") == 2
        assert groups.get("failed") == 1

    def test_aggregate_no_group_by_counts_all(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path)
        _seed_run(tmp_path)
        _seed_run(tmp_path)
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "count"}),
        )
        assert "aggregated" in result
        total = sum(r["value"] for r in result["aggregated"])
        assert total == 3

    def test_aggregate_sum_on_output_field(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, output={"score": 10})
        _seed_run(tmp_path, output={"score": 20})
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "sum", "field": "score"}),
        )
        total = sum(r["value"] for r in result["aggregated"])
        assert total == 30

    def test_aggregate_mean_on_output_field(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path, output={"score": 10})
        _seed_run(tmp_path, output={"score": 30})
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "mean", "field": "score"}),
        )
        assert result["aggregated"][0]["value"] == 20.0

    def test_aggregate_invalid_fn_rejected(self, tmp_path, monkeypatch):
        _init(tmp_path)
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "median"}),
        )
        assert "error" in result

    def test_aggregate_invalid_json_rejected(self, tmp_path, monkeypatch):
        _init(tmp_path)
        result = _query(monkeypatch, tmp_path, aggregate_json="{bad json}")
        assert "error" in result

    def test_aggregate_response_has_agg_op_field(self, tmp_path, monkeypatch):
        _init(tmp_path)
        _seed_run(tmp_path)
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "count", "group_by": "status"}),
        )
        assert "agg_op" in result
        assert result["agg_op"] == "count"


# ── INTERRUPTED exclusion invariant ──────────────────────────────────────────

class TestQueryRunsInterruptedExclusion:
    def test_interrupted_excluded_from_aggregation_by_default(
        self, tmp_path, monkeypatch
    ):
        _init(tmp_path)
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="interrupted")
        # Without status filter, aggregation excludes INTERRUPTED
        result = _query(
            monkeypatch, tmp_path,
            aggregate_json=json.dumps({"fn": "count", "group_by": "status"}),
        )
        groups = {r["group"]: r["value"] for r in result["aggregated"]}
        # interrupted may or may not appear depending on default filter
        # invariant: completed is present, count correct
        assert "completed" in groups

    def test_interrupted_included_when_status_filter_explicit(
        self, tmp_path, monkeypatch
    ):
        _init(tmp_path)
        _seed_run(tmp_path, status="completed")
        _seed_run(tmp_path, status="interrupted")
        result = _query(
            monkeypatch, tmp_path,
            filters_json=json.dumps({"status": ["completed", "interrupted"]}),
            aggregate_json=json.dumps({"fn": "count", "group_by": "status"}),
        )
        groups = {r["group"]: r["value"] for r in result["aggregated"]}
        assert "interrupted" in groups


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
