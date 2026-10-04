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


def test_output_discovery_and_lossless_unicode_continuation(controls):
    served, _, make = controls
    value = "α🙂\n漢字 " * 13
    rid = make(output={"story": value, "count": 4, "nested": {"x": [None, True]}})
    snapshot = content(served.read_graph(target="run", run_id=rid))
    catalog = snapshot["output_catalog"]
    assert {f["name"] for f in catalog["fields"]} == {"story", "count", "nested"}
    assert value not in json.dumps(catalog, ensure_ascii=False)
    parts, offset = [], 0
    while True:
        result = content(served.read_graph(target="run_output", run_id=rid,
                         field_name="story", output_offset=offset, output_max_chars=7))
        assert result["offset"] == offset
        assert result["length"] == len(result["chunk"]) <= 7
        assert result["total_chars"] == len(value)
        assert "value" not in result
        parts.append(result["chunk"])
        if result["next_offset"] is None:
            assert not result["truncated"]
            break
        offset = result["next_offset"]
    assert "".join(parts) == value
    for field, expected in (("count", 4), ("nested", {"x": [None, True]})):
        result = content(served.read_graph(target="run_output", run_id=rid,
                                          field_name=field))
        assert result["value"] == expected
        assert json.loads(result["chunk"]) == expected


@pytest.mark.parametrize("action", ["run", "run_output", "cancel"])
def test_pinned_run_selection_hides_foreign_public_run(controls, action):
    served, base, make = controls
    from tinyassets.runs import is_cancel_requested
    rid = make(status="running", output={"foreign-secret": "not yours"}, universe="theirs")
    if action == "cancel":
        raw = served.run_graph(operation="cancel", run_id=rid)
    else:
        raw = served.read_graph(target=action, run_id=rid)
    assert json.loads(raw) == {"error": f"Run '{rid}' not found."}
    assert not is_cancel_requested(base, rid)


def test_run_list_stays_pinned(controls):
    served, _, make = controls
    ours, theirs = make(), make(universe="theirs")
    result = json.loads(served.read_graph(target="runs"))
    assert {r["run_id"] for r in result["runs"]} == {ours}
    assert theirs not in json.dumps(result)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_cancel_requests_existing_run_without_admission(controls, status):
    served, base, make = controls
    from tinyassets.auth.provider import action_scope_for
    from tinyassets.runs import is_cancel_requested
    assert action_scope_for("extensions", "cancel_run").effect == "write"
    rid = make(status=status)
    result = content(served.run_graph(operation="cancel", run_id=rid))
    assert "error" not in result, result
    assert result["status"] == status
    assert result["cancel_requested"] is True
    assert result["terminal"] is False
    assert is_cancel_requested(base, rid)
    snapshot = content(served.read_graph(target="run", run_id=rid))
    assert snapshot["cancel_requested"] is True
    assert snapshot["status"] == status


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_terminal_cancel_does_not_insert_or_claim_cancelled(controls, status):
    served, base, make = controls
    from tinyassets.runs import is_cancel_requested
    rid = make(status=status)
    result = content(served.run_graph(operation="cancel", run_id=rid))
    assert result["status"] == status
    assert result["terminal"] is True
    assert result["cancel_requested"] is False
    assert not is_cancel_requested(base, rid)


@pytest.mark.parametrize("target", ["run", "run_output"])
def test_pinned_run_reads_require_explicit_run_id(controls, target):
    served, _, _ = controls
    assert "run_id is required" in json.loads(served.read_graph(target=target))["error"]


@pytest.mark.parametrize("kwargs", [
    {"operation": "wrong"}, {"operation": "cancel"},
    {"operation": "cancel", "run_id": "r", "branch_def_id": "b"},
    {"operation": "cancel", "run_id": "r", "inputs_json": "{}"},
    {"operation": "run", "run_id": "r"},
])
def test_cancel_rejects_ambiguous_arguments_before_admission(controls, kwargs):
    served, _, _ = controls
    assert "error" in json.loads(served.run_graph(**kwargs))


@pytest.mark.parametrize("queued", [True, False])
def test_exposed_cancel_reaches_real_executor_and_child(controls, monkeypatch, queued):
    _exercise_exposed_cancel(controls, monkeypatch, queued=queued)


def test_cancel_preserves_completed_and_unstarted_nodes(controls, monkeypatch):
    _exercise_exposed_cancel(controls, monkeypatch, queued=False, with_neighbors=True)


def _exercise_exposed_cancel(controls, monkeypatch, *, queued, with_neighbors=False):
    import subprocess
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets import node_sandbox, runs
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
        kwargs["launcher"] = node_sandbox.PlainSubprocessLauncher()
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


def test_storage_cancel_does_not_insert_after_racing_finish(controls, monkeypatch):
    served, base, make = controls
    from tinyassets import runs
    rid = make(status="running")
    original = runs.request_cancel

    def finish_first(path, selected):
        runs.update_run_status(path, selected, status="completed")
        return original(path, selected)

    monkeypatch.setattr(runs, "request_cancel", finish_first)
    result = content(served.run_graph(operation="cancel", run_id=rid))
    assert result["status"] == "completed"
    assert result["terminal"] is True
    assert not runs.is_cancel_requested(base, rid)


@pytest.mark.parametrize("offset,max_chars", [(-1, 8), (0, 0), (0, 32769), (True, 8)])
def test_output_rejects_invalid_chunk_arguments(controls, offset, max_chars):
    served, _, make = controls
    rid = make(output={"x": "y"})
    assert "error" in json.loads(served.read_graph(
        target="run_output", run_id=rid, field_name="x",
        output_offset=offset, output_max_chars=max_chars,
    ))


def test_catalog_paginates_without_value_previews(controls):
    served, _, make = controls
    rid = make(output={f"field{i:03}": "private-value" for i in range(130)})
    names, offset = [], 0
    while True:
        result = content(served.read_graph(target="run_output", run_id=rid, output_offset=offset))
        assert "private-value" not in json.dumps(result)
        catalog = result["output_catalog"]
        assert len(catalog["fields"]) <= 64
        names.extend(field["name"] for field in catalog["fields"])
        if catalog["next_offset"] is None:
            break
        offset = catalog["next_offset"]
    assert names == [f"field{i:03}" for i in range(130)]


def test_canonical_cancel_still_requires_write_capability(controls):
    from tinyassets import runs, universe_server
    from tinyassets.auth.middleware import _current_identity
    from tinyassets.auth.provider import Identity

    _, base, make = controls
    rid = make(status="running")
    token = _current_identity.set(Identity(user_id="owner", username="owner",
                                           capabilities=["read", "list"]))
    try:
        result = json.loads(universe_server.run_graph(operation="cancel", run_id=rid,
                                                     graph_id="ours"))
        assert "error" in result
        assert not runs.is_cancel_requested(base, rid)
    finally:
        _current_identity.reset(token)


def test_canonical_cancel_cannot_mutate_another_legacy_actor_run(controls):
    from tinyassets import runs, universe_server
    from tinyassets.auth.middleware import _current_identity
    from tinyassets.auth.provider import Identity

    _, base, _ = controls
    rid = runs.create_run(base, branch_def_id="legacy", thread_id="legacy", inputs={},
                          actor="someone-else", owner_user_id="someone-else")
    token = _current_identity.set(Identity(user_id="owner", username="owner",
                                           capabilities=["read", "list", "write"]))
    try:
        result = json.loads(universe_server.run_graph(operation="cancel", run_id=rid))
        assert "error" in result
        assert not runs.is_cancel_requested(base, rid)
    finally:
        _current_identity.reset(token)


@pytest.mark.parametrize("kwargs", [
    {"webhook_op": "mint", "branch_def_id": "b"}, {"source_op": "create"},
    {"goal_id": "g"}, {"token": "t"}, {"recursion_limit_override": 5},
])
def test_canonical_cancel_refuses_trigger_and_launch_combinations(controls, kwargs):
    from tinyassets import runs, universe_server

    _, base, make = controls
    rid = make(status="queued")
    result = json.loads(universe_server.run_graph(operation="cancel", run_id=rid,
                                                 graph_id="ours", **kwargs))
    assert "error" in result
    assert not runs.is_cancel_requested(base, rid)


@pytest.mark.parametrize("value", ["", "line\n\"quoted\"🙂", None, False, 1.5,
                                    ["漢字", {"nested": 2}]])
def test_small_output_keeps_exact_json_type(controls, value):
    served, _, make = controls
    rid = make(output={"out": value})
    result = content(served.read_graph(target="run_output", run_id=rid, field_name="out"))
    assert type(result["value"]) is type(value)
    assert result["value"] == value
