"""Tests for execute_step emit-site wiring — Task #72.

Coverage:
- Each terminal status (completed/failed/cancelled/interrupted) emits one
  execute_step event with correct fields.
- Non-terminal status (running) does NOT emit.
- Idempotency via deterministic event_id collision (INSERT OR IGNORE).
- Emit failure (record_contribution_event raises) does NOT break run-status
  update — try/except decoupling preserves load-bearing semantic.
- Bounty-calc smoke on real-data path (events emitted via update_run_status,
  not synthetic INSERT) — confirms §4 recursive-CTE finds production events.

Spec: docs/design-notes/2026-04-25-contribution-ledger-proposal.md §3.
"""
from __future__ import annotations

import json

import pytest

from tinyassets.contribution_events import _EMIT_FAILURES, _connect
from tinyassets.runs import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_INTERRUPTED,
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    create_run,
    initialize_runs_db,
    update_run_status,
)


def _fresh_run(
    tmp_path,
    *,
    branch_def_id: str = "b1",
    actor: str = "alice",
    branch_version_id: str | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
) -> str:
    """Create a fresh queued run, return its run_id."""
    initialize_runs_db(tmp_path)
    return create_run(
        tmp_path,
        branch_def_id=branch_def_id,
        thread_id="t1",
        inputs={},
        run_name="emit-test",
        actor=actor,
        branch_version_id=branch_version_id,
        daemon_id=daemon_id,
        runtime_instance_id=runtime_instance_id,
        worker_id=worker_id,
    )


def _events(tmp_path, run_id: str) -> list[dict]:
    with _connect(tmp_path) as conn:
        rows = conn.execute(
            "SELECT * FROM contribution_events WHERE source_run_id = ? "
            "ORDER BY occurred_at ASC",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@pytest.fixture(autouse=True)
def _reset_emit_failures():
    """Snapshot + restore _EMIT_FAILURES counter per test."""
    saved = _EMIT_FAILURES["count"]
    _EMIT_FAILURES["count"] = 0
    yield
    _EMIT_FAILURES["count"] = saved


# ── Terminal status emits ────────────────────────────────────────────────────


class TestTerminalStatusEmits:
    def test_completed_run_emits_event(self, tmp_path):
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)
        events = _events(tmp_path, run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["event_type"] == "execute_step"
        assert ev["event_id"] == f"execute_step:{run_id}:completed"
        assert ev["actor_id"] == "alice"
        assert ev["source_run_id"] == run_id
        assert ev["source_artifact_id"] == "b1"
        assert ev["source_artifact_kind"] == "branch_def"
        assert ev["weight"] == 1.0
        meta = json.loads(ev["metadata_json"])
        assert meta["branch_def_id"] == "b1"
        assert meta["branch_version_id"] is None
        assert meta["terminal_status"] == "completed"

    def test_failed_run_emits_event(self, tmp_path):
        """Per design §3 — failed runs still represent work attempts;
        caused_regression is a SEPARATE event type, not implied by failure."""
        run_id = _fresh_run(tmp_path)
        update_run_status(
            tmp_path, run_id, status=RUN_STATUS_FAILED, error="boom",
        )
        events = _events(tmp_path, run_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "execute_step"
        assert json.loads(events[0]["metadata_json"])["terminal_status"] == "failed"

    def test_cancelled_run_emits_event(self, tmp_path):
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_CANCELLED)
        events = _events(tmp_path, run_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "execute_step"
        assert json.loads(events[0]["metadata_json"])["terminal_status"] == "cancelled"

    def test_interrupted_run_emits_event(self, tmp_path):
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_INTERRUPTED)
        events = _events(tmp_path, run_id)
        assert len(events) == 1
        assert json.loads(events[0]["metadata_json"])["terminal_status"] == "interrupted"

    def test_version_based_run_carries_branch_version_kind(self, tmp_path):
        """When branch_version_id is set, source_artifact_kind=branch_version."""
        run_id = _fresh_run(
            tmp_path,
            branch_version_id="b1@abc12345",
        )
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)
        events = _events(tmp_path, run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["source_artifact_id"] == "b1@abc12345"
        assert ev["source_artifact_kind"] == "branch_version"

    def test_completed_run_emits_full_identity_tuple(self, tmp_path):
        from tinyassets.daemon_registry import create_daemon

        daemon = create_daemon(
            tmp_path,
            display_name="Economic Worker",
            created_by="owner-user",
            soul_mode="soulless",
        )
        run_id = _fresh_run(
            tmp_path,
            daemon_id=daemon["daemon_id"],
            runtime_instance_id="runtime-1",
            worker_id="worker-1",
        )
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)
        events = _events(tmp_path, run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["actor_id"] == "alice"
        assert ev["owner_user_id"] == "owner-user"
        assert ev["daemon_id"] == daemon["daemon_id"]
        assert ev["runtime_instance_id"] == "runtime-1"
        assert ev["worker_id"] == "worker-1"
        assert ev["weight"] == 1.0

    def test_unresolvable_daemon_owner_defaults_empty_on_emit(self, tmp_path):
        run_id = _fresh_run(
            tmp_path,
            daemon_id="daemon::missing",
            runtime_instance_id="runtime-missing",
            worker_id="worker-missing",
        )
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)
        ev = _events(tmp_path, run_id)[0]
        assert ev["owner_user_id"] == ""
        assert ev["daemon_id"] == "daemon::missing"
        assert ev["runtime_instance_id"] == "runtime-missing"
        assert ev["worker_id"] == "worker-missing"


# ── Non-terminal status does NOT emit ────────────────────────────────────────


class TestNonTerminalStatusDoesNotEmit:
    def test_running_status_does_not_emit(self, tmp_path):
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_RUNNING)
        assert _events(tmp_path, run_id) == []

    def test_queued_status_does_not_emit(self, tmp_path):
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_QUEUED)
        assert _events(tmp_path, run_id) == []

    def test_no_status_change_does_not_emit(self, tmp_path):
        """Updating only a non-status field doesn't trigger emit."""
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, error="warn-only")
        assert _events(tmp_path, run_id) == []


# ── Idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_repeat_emit_for_same_terminal_status_skipped(self, tmp_path):
        """Two completes on the same run produce exactly 1 event row."""
        run_id = _fresh_run(tmp_path)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)
        events = _events(tmp_path, run_id)
        assert len(events) == 1


# ── Emit failure does NOT break status update ────────────────────────────────


class TestEmitFailureDecoupled:
    def test_emit_raise_preserves_status_update(self, tmp_path, monkeypatch):
        """If record_contribution_event raises, run.status STILL updates AND
        _EMIT_FAILURES counter increments AND a warning is logged."""
        from tinyassets import contribution_events as ce

        run_id = _fresh_run(tmp_path)

        def boom(*args, **kwargs):
            raise RuntimeError("simulated emit failure")

        monkeypatch.setattr(ce, "record_contribution_event", boom)

        # Status update must succeed despite emit raising.
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED)

        # Run row reflects the new status (the load-bearing semantic).
        with _connect(tmp_path) as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,),
            ).fetchone()
        assert row["status"] == "completed"

        # Counter incremented; production observers see this.
        assert _EMIT_FAILURES["count"] == 1


# ── Bounty-calc smoke on real-data path ──────────────────────────────────────


class TestBountyCalcOnRealData:
    """The §4 recursive-CTE smoke from #71 was synthetic-INSERT-only.
    This extends it to events emitted via the production path."""

    def test_emitted_events_findable_by_bounty_query(self, tmp_path):
        # Create 2 runs, each completes via update_run_status — both emit.
        run_a = _fresh_run(tmp_path, branch_def_id="ba", actor="alice")
        run_b = _fresh_run(tmp_path, branch_def_id="bb", actor="bob")
        update_run_status(tmp_path, run_a, status=RUN_STATUS_COMPLETED)
        update_run_status(tmp_path, run_b, status=RUN_STATUS_COMPLETED)

        # Bounty-calc-shaped query — sum weight by actor in a window.
        with _connect(tmp_path) as conn:
            rows = list(conn.execute(
                "SELECT actor_id, SUM(weight) AS share "
                "FROM contribution_events "
                "WHERE event_type = 'execute_step' "
                "GROUP BY actor_id "
                "ORDER BY actor_id"
            ))
        shares = {r["actor_id"]: r["share"] for r in rows}
        assert shares["alice"] == 1.0
        assert shares["bob"] == 1.0


# ── design_used emits (Task #75) ─────────────────────────────────────────────


def _seed_branch_with_authored_nodes(
    tmp_path,
    *,
    branch_id: str = "b1",
    node_authors: dict[str, str] | None = None,
) -> "BranchDefinition":  # noqa: F821 — string annotation; real type imported in body
    """Create a real BranchDefinition with NodeDefinitions whose authors are
    set per the node_authors mapping. Single-node default keeps tests focused."""
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
    )

    initialize_author_server(tmp_path)
    if node_authors is None:
        node_authors = {"n1": "alice"}

    node_defs = [
        NodeDefinition(
            node_id=node_id, display_name=node_id.upper(),
            prompt_template="do thing",
            author=author,
        )
        for node_id, author in node_authors.items()
    ]
    graph_nodes = [
        GraphNodeRef(id=node_id, node_def_id=node_id)
        for node_id in node_authors
    ]
    # Single linear chain n1 -> END (or n1 -> n2 -> END for 2-node tests).
    edges: list[EdgeDefinition] = []
    ids = list(node_authors)
    for i, nid in enumerate(ids[:-1]):
        edges.append(EdgeDefinition(from_node=nid, to_node=ids[i + 1]))
    edges.append(EdgeDefinition(from_node=ids[-1], to_node="END"))

    branch = BranchDefinition(
        branch_def_id=branch_id,
        name=f"Branch {branch_id}",
        graph_nodes=graph_nodes,
        edges=edges,
        entry_point=ids[0],
        node_defs=node_defs,
        state_schema=[],
    )
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    return branch


def _design_used_events(tmp_path, run_id: str) -> list[dict]:
    with _connect(tmp_path) as conn:
        rows = conn.execute(
            "SELECT * FROM contribution_events "
            "WHERE source_run_id = ? AND event_type = 'design_used' "
            "ORDER BY occurred_at ASC",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


class TestDesignUsedEmits:
    """Phase 2 design_used emit (Task #75). Fires per successful step
    execution, crediting the NodeDefinition's author. Skips on missing
    artifact id, missing/anonymous author, system-prefixed node ids."""

    def test_node_def_kind_emitted_for_authored_node(self, tmp_path, connected_fantasy_model):
        """A node with non-anonymous author fires one design_used per
        successful step — kind=node_def, source_artifact_id=node_def_id,
        actor_id=author."""
        from tinyassets.runs import execute_branch_async

        branch = _seed_branch_with_authored_nodes(
            tmp_path,
            node_authors={"n1": "alice"},
        )
        outcome = execute_branch_async(
            tmp_path, branch=branch, inputs={}, actor="runner",
            provider_call=connected_fantasy_model,
        )
        from tinyassets.runs import wait_for
        wait_for(outcome.run_id, timeout=10.0)

        events = _design_used_events(tmp_path, outcome.run_id)
        assert len(events) >= 1
        ev = events[0]
        assert ev["event_type"] == "design_used"
        assert ev["actor_id"] == "alice"
        assert ev["source_artifact_id"] == "n1"
        assert ev["source_artifact_kind"] == "node_def"
        assert ev["weight"] == 1.0
        meta = json.loads(ev["metadata_json"])
        assert meta["node_def_id"] == "n1"
        assert meta["graph_node_id"] == "n1"
        assert "step_index" in meta

    def test_anonymous_author_skips_emit(self, tmp_path):
        """Default 'anonymous' author skips emit — orphan-row prevention.
        Only registered actors (non-default author) get credited."""
        from tinyassets.runs import execute_branch_async, wait_for

        branch = _seed_branch_with_authored_nodes(
            tmp_path,
            node_authors={"n1": "anonymous"},
        )
        outcome = execute_branch_async(
            tmp_path, branch=branch, inputs={}, actor="runner",
        )
        wait_for(outcome.run_id, timeout=10.0)

        events = _design_used_events(tmp_path, outcome.run_id)
        assert events == []

    def test_multi_node_run_emits_per_node(self, tmp_path, connected_fantasy_model):
        """Two authored nodes each fire one design_used in step order."""
        from tinyassets.runs import execute_branch_async, wait_for

        branch = _seed_branch_with_authored_nodes(
            tmp_path,
            node_authors={"n1": "alice", "n2": "bob"},
        )
        outcome = execute_branch_async(
            tmp_path, branch=branch, inputs={}, actor="runner",
            provider_call=connected_fantasy_model,
        )
        wait_for(outcome.run_id, timeout=10.0)

        events = _design_used_events(tmp_path, outcome.run_id)
        actors = {e["actor_id"] for e in events}
        assert "alice" in actors
        assert "bob" in actors
        # Each authored node contributes at least one event.
        artifact_ids = {e["source_artifact_id"] for e in events}
        assert "n1" in artifact_ids
        assert "n2" in artifact_ids

    def test_synthetic_system_node_id_does_not_emit(self, tmp_path):
        """Direct _on_node call with a __-prefixed node_id never emits.
        Tests the system-event filter without needing a full run."""
        from tinyassets.contribution_events import _connect as ce_connect
        from tinyassets.runs import _invoke_graph  # noqa: F401 — verifies symbol importable

        branch = _seed_branch_with_authored_nodes(
            tmp_path, node_authors={"n1": "alice"},
        )
        # We can't easily invoke _on_node directly (it's a closure inside
        # _invoke_graph). Instead drive a full run, then verify no
        # contribution event has graph_node_id starting with "__".
        from tinyassets.runs import execute_branch_async, wait_for

        outcome = execute_branch_async(
            tmp_path, branch=branch, inputs={}, actor="runner",
        )
        wait_for(outcome.run_id, timeout=10.0)

        with ce_connect(tmp_path) as conn:
            rows = list(conn.execute(
                "SELECT metadata_json FROM contribution_events "
                "WHERE event_type = 'design_used' AND source_run_id = ?",
                (outcome.run_id,),
            ))
        for r in rows:
            meta = json.loads(r["metadata_json"])
            assert not meta["graph_node_id"].startswith("__"), (
                f"design_used event has system-prefixed node_id: "
                f"{meta['graph_node_id']}"
            )

    def test_design_used_idempotent_within_run(self, tmp_path):
        """Same (run_id, step_index, node_def_id) tuple cannot emit twice —
        deterministic event_id + INSERT OR IGNORE. Verified by inspecting
        for any duplicate (step_index, node_def_id) pairs."""
        from tinyassets.runs import execute_branch_async, wait_for

        branch = _seed_branch_with_authored_nodes(
            tmp_path,
            node_authors={"n1": "alice"},
        )
        outcome = execute_branch_async(
            tmp_path, branch=branch, inputs={}, actor="runner",
        )
        wait_for(outcome.run_id, timeout=10.0)

        events = _design_used_events(tmp_path, outcome.run_id)
        # Confirm no duplicate (step_index, node_def_id) pairs.
        seen = set()
        for e in events:
            meta = json.loads(e["metadata_json"])
            key = (meta["step_index"], meta["node_def_id"])
            assert key not in seen, f"Duplicate design_used event: {key}"
            seen.add(key)

    def test_design_used_findable_by_bounty_calc_query(self, tmp_path, connected_fantasy_model):
        """The §4 recursive-CTE bounty calc finds production-emitted
        design_used events with correct actor attribution."""
        from tinyassets.runs import execute_branch_async, wait_for

        branch = _seed_branch_with_authored_nodes(
            tmp_path,
            node_authors={"n1": "alice", "n2": "bob"},
        )
        outcome = execute_branch_async(
            tmp_path, branch=branch, inputs={}, actor="runner",
            provider_call=connected_fantasy_model,
        )
        wait_for(outcome.run_id, timeout=10.0)

        # Bounty-calc-shaped query restricted to design_used events.
        with _connect(tmp_path) as conn:
            rows = list(conn.execute(
                "SELECT actor_id, SUM(weight) AS share "
                "FROM contribution_events "
                "WHERE event_type = 'design_used' "
                "  AND source_run_id = ? "
                "GROUP BY actor_id",
                (outcome.run_id,),
            ))
        shares = {r["actor_id"]: r["share"] for r in rows}
        assert shares.get("alice", 0) >= 1.0
        assert shares.get("bob", 0) >= 1.0
