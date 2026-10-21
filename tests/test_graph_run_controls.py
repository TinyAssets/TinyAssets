"""Owned run output/control through served -> canonical -> storage dispatch."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def controls(tmp_path, monkeypatch):
    from tinyassets import engine_mcp_server as served
    from tinyassets.api.visibility import set_universe_visibility
    from tinyassets.auth import middleware
    from tinyassets.auth.provider import DevAuthProvider
    from tinyassets.daemon_server import ensure_universe_registered, grant_universe_access
    from tinyassets.runs import create_run, update_run_status

    # These rows have no worker that could settle them. Exercise the real
    # polling loop and its full default window on a simulated clock instead
    # of sleeping ten wall seconds for each cancellation-state snapshot.
    read_settled = served._read_run_settled

    def read_without_wall_wait(read):
        now = 0.0

        def advance(seconds):
            nonlocal now
            now += seconds

        return read_settled(read, clock=lambda: now, sleep=advance)

    monkeypatch.setattr(served, "_read_run_settled", read_without_wall_wait)

    class ProductionScopeProvider(DevAuthProvider):
        def resolve_always_writes(self):
            return True

    # Exercise the live resolve-always scope gate, not dev mode's bypass.
    monkeypatch.setattr(middleware, "_provider", ProductionScopeProvider())
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(served, "_GRAPH_ID", "ours")
    monkeypatch.setattr(served, "_ACTOR_ID", "owner")
    from tests.engine_authority_helpers import seed_bound_engine
    seed_bound_engine(monkeypatch)
    # Cancellation must not consume a run admission, including under exhaustion.
    def no_admission(**kwargs):
        pytest.fail("cancel reached run admission")
    monkeypatch.setattr(served, "_engine_run_admit", no_admission)
    for uid in ("ours", "theirs"):
        directory = tmp_path / uid
        directory.mkdir()
        ensure_universe_registered(tmp_path, universe_id=uid, universe_path=directory)
        set_universe_visibility(uid, "public", source="owner")
    grant_universe_access(tmp_path, universe_id="ours", actor_id="owner",
                          permission="admin", granted_by="owner")

    def make(status="completed", output=None, universe="ours"):
        rid = create_run(tmp_path, branch_def_id="test-branch", thread_id="test",
                         inputs={}, actor=f"universe:{universe}")
        update_run_status(tmp_path, rid, status=status, output=output or {})
        return rid

    return served, tmp_path, make


def content(raw):
    result = json.loads(raw)
    if "error" in result:
        return result
    assert result["untrusted"] is True
    return result["content"]




















def _exercise_exposed_cancel(controls, monkeypatch, *, queued, with_neighbors=False):
    import subprocess
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets import runs
    from tinyassets.boxes import node as node_sandbox
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition

    served, base, _ = controls
    pool = ThreadPoolExecutor(max_workers=1)
    release_queue = threading.Event()
    started = threading.Event()
    child_processes = []
    real_popen = subprocess.Popen
    sandbox_type = node_sandbox.NodeSandbox

    def observe_child(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        child_processes.append(proc)
        if len(child_processes) >= (2 if with_neighbors else 1):
            started.set()
        return proc

    def local_test_sandbox(**kwargs):
        return sandbox_type(**kwargs)

    monkeypatch.setattr(node_sandbox, "NodeSandbox", local_test_sandbox)
    monkeypatch.setattr(subprocess, "Popen", observe_child)
    monkeypatch.setattr(runs, "_get_executor", lambda **kwargs: pool)
    if queued:
        pool.submit(release_queue.wait, 20)
    branch = BranchDefinition(name="Cancel code", author="universe:ours", entry_point="instance")
    branch.node_defs = [NodeDefinition(
        node_id="definition", display_name="Sleeper", output_keys=["r"],
        source_code="def run(state):\n    import time\n    time.sleep(30)\n    return {'r': 1}\n",
    )]
    branch.graph_nodes = [GraphNodeRef(id="instance", node_def_id="definition")]
    branch.edges = [EdgeDefinition(from_node="START", to_node="instance"),
                    EdgeDefinition(from_node="instance", to_node="END")]
    branch.state_schema = [{"name": "r", "type": "int"}]
    if with_neighbors:
        branch.entry_point = "before"
        for name in ("before", "after"):
            field = f"{name}_out"
            branch.node_defs.append(NodeDefinition(
                node_id=f"{name}-definition", display_name=name, output_keys=[field],
                source_code=f"def run(state):\n    return {{{field!r}: 1}}\n",
            ))
            branch.graph_nodes.append(GraphNodeRef(id=name, node_def_id=f"{name}-definition"))
            branch.state_schema.append({"name": field, "type": "int"})
        branch.edges = [
            EdgeDefinition(from_node="START", to_node="before"),
            EdgeDefinition(from_node="before", to_node="instance"),
            EdgeDefinition(from_node="instance", to_node="after"),
            EdgeDefinition(from_node="after", to_node="END"),
        ]
    outcome = None
    try:
        outcome = runs.execute_branch_async(base, branch=branch, inputs={}, actor="universe:ours")
        if not queued:
            assert started.wait(10), runs.get_run(base, outcome.run_id).get("error")
        ack = content(served.run_graph(operation="cancel", run_id=outcome.run_id))
        assert ack["cancel_requested"] is True
        release_queue.set()
        runs.wait_for(outcome.run_id, timeout=15)
        snapshot = content(served.read_graph(target="run", run_id=outcome.run_id))
        assert snapshot["status"] == "cancelled", snapshot
        assert not any(node["status"] == "failed" for node in snapshot["node_statuses"])
        if queued:
            assert not child_processes, "queued cancellation launched a child anyway"
        else:
            assert child_processes and all(proc.poll() is not None for proc in child_processes)
            by_node = {node["node_id"]: node["status"] for node in snapshot["node_statuses"]}
            assert by_node["instance"] == "cancelled", snapshot
            cancelled = [event for event in runs.list_events(base, outcome.run_id, since_step=-1)
                         if event["status"] == "cancelled"]
            assert [event["node_id"] for event in cancelled] == ["instance"]
            assert cancelled[0]["detail"]["error_type"] == "NodeCancelledError"
            if with_neighbors:
                assert by_node["before"] == "ran"
                assert by_node["after"] == "pending"
    finally:
        release_queue.set()
        if outcome is not None:
            served.run_graph(operation="cancel", run_id=outcome.run_id)
        for proc in child_processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        pool.shutdown(wait=True)














