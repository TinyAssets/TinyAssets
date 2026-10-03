"""In-node paced enqueue verb (slice 2/2 of closing the driver-branch gap).

A source_code node can append a run-request to its universe's dispatcher
queue via ``invoke_mcp_action('enqueue_branch_run', ...)`` — NOT a synchronous
spawn. The daemon's concurrency cap paces execution.

Containment (Codex enqueue review, 2026-05-30):
  * default-off capability flag, explicitly enabled by production deploy;
  * spawn-depth cap (chain length) + atomic run-wide budget (branching factor);
  * trusted current-universe targeting — never a branch-named universe (Fix 1);
  * global active-queue cap + per-origin spawn-lineage cap (Fix 2);
  * epoch-1 targets must exist and be public until request-scoped actor
    authority is durable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import tinyassets.api.helpers as helpers
import tinyassets.branch_tasks as bt
import tinyassets.daemon_server as ds
import tinyassets.runs as runs
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import (
    CompilerError,
    NodeEnqueueContext,
    compile_branch,
)

# Since change `sandboxed-code-node` the call inside the sandbox is a synchronous
# RPC to the parent, which answers with the run's authority; the child never
# holds the invoker. The contract run() sees is unchanged.
ENQUEUE_ONE = (
    "def run(state):\n"
    "    r = invoke_mcp_action('enqueue_branch_run',\n"
    "        branch_def_id='patch_loop', inputs={'bug_id': 'BUG-1'})\n"
    "    return {'status': r['status']}\n"
)

ENQUEUE_TWICE = (
    "def run(state):\n"
    "    invoke_mcp_action('enqueue_branch_run', branch_def_id='x', inputs={})\n"
    "    invoke_mcp_action('enqueue_branch_run', branch_def_id='y', inputs={})\n"
    "    return {'status': 'done'}\n"
)

ENQUEUE_FOREIGN_UNIVERSE = (
    "def run(state):\n"
    "    invoke_mcp_action('enqueue_branch_run', branch_def_id='x',\n"
    "        inputs={}, universe_id='other')\n"
    "    return {'status': 'x'}\n"
)


def _branch(src: str, tools_allowed: list[str]) -> BranchDefinition:
    b = BranchDefinition(name="drv", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only",
        display_name="Only",
        source_code=src,
        output_keys=["status"],
        tools_allowed=tools_allowed,
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [{"name": "status", "type": "str"}]
    return b


def _parallel_enqueue_branch() -> BranchDefinition:
    """Two enqueue-capable nodes that execute in the same parallel superstep."""
    root_src = "def run(state):\n    return {}\n"
    enqueue_a = (
        "def run(state):\n"
        "    invoke_mcp_action('enqueue_branch_run', "
        "branch_def_id='a', inputs={})\n"
        "    return {'status_a': 'enqueued'}\n"
    )
    enqueue_b = (
        "def run(state):\n"
        "    invoke_mcp_action('enqueue_branch_run', "
        "branch_def_id='b', inputs={})\n"
        "    return {'status_b': 'enqueued'}\n"
    )
    branch = BranchDefinition(name="parallel-drv", entry_point="root")
    branch.node_defs = [
        NodeDefinition(
            node_id="root",
            display_name="Root",
            source_code=root_src,
            output_keys=[],
        ).mark_approved(),
        NodeDefinition(
            node_id="enqueue_a",
            display_name="Enqueue A",
            source_code=enqueue_a,
            output_keys=["status_a"],
            tools_allowed=["enqueue_branch_run"],
        ).mark_approved(),
        NodeDefinition(
            node_id="enqueue_b",
            display_name="Enqueue B",
            source_code=enqueue_b,
            output_keys=["status_b"],
            tools_allowed=["enqueue_branch_run"],
        ).mark_approved(),
    ]
    branch.graph_nodes = [
        GraphNodeRef(id=node_id, node_def_id=node_id)
        for node_id in ("root", "enqueue_a", "enqueue_b")
    ]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="root"),
        EdgeDefinition(from_node="root", to_node="enqueue_a"),
        EdgeDefinition(from_node="root", to_node="enqueue_b"),
        EdgeDefinition(from_node="enqueue_a", to_node="END"),
        EdgeDefinition(from_node="enqueue_b", to_node="END"),
    ]
    branch.state_schema = [
        {"name": "status_a", "type": "str"},
        {"name": "status_b", "type": "str"},
    ]
    return branch


def _patch_storage(monkeypatch, *, branch_meta=None) -> list:
    """Capture enqueued tasks; stub the target-branch lookup as public.

    The helper calls ``append_task_capped`` (not ``append_task``) and
    ``get_branch_definition`` for the existence/visibility check — both are
    imported lazily, so patching the module attribute is enough.
    """
    captured: list = []
    monkeypatch.setattr(helpers, "_universe_dir", lambda uid: Path(f"/fake/{uid}"))
    monkeypatch.setattr(
        bt, "append_task_capped",
        lambda upath, task, **caps: captured.append((upath, task)),
    )
    meta = branch_meta if branch_meta is not None else {
        "visibility": "public", "author": "anyone",
    }
    monkeypatch.setattr(
        ds, "get_branch_definition",
        lambda base_path, *, branch_def_id: dict(meta),
    )
    return captured


def _ctx(**kw) -> NodeEnqueueContext:
    base = {"universe_id": "u", "actor": "anyone"}
    base.update(kw)
    return NodeEnqueueContext(**base)


def _run(b, *, invocation_depth=0, thread="t", context=None, base_path="/fake/base"):
    if context is None:
        context = _ctx()
    compiled = compile_branch(
        b, invocation_depth=invocation_depth,
        base_path=base_path, enqueue_context=context,
    )
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    return app.invoke({}, config={"configurable": {"thread_id": thread}})


def test_enqueue_requires_tools_allowed(monkeypatch):
    # Even enabled, the node must declare the verb in tools_allowed.
    monkeypatch.setenv("TINYASSETS_NODE_ENQUEUE_ENABLED", "on")
    b = _branch(ENQUEUE_ONE, [])  # not declared
    with pytest.raises(CompilerError) as exc:
        _run(b, thread="enq-gate")
    assert "not allowed" in str(exc.value)


def test_enqueue_requires_branch_def_id(monkeypatch):
    monkeypatch.setenv("TINYASSETS_NODE_ENQUEUE_ENABLED", "on")
    _patch_storage(monkeypatch)
    src = (
        "def run(state):\n"
        "    invoke_mcp_action('enqueue_branch_run', inputs={})\n"
        "    return {'status': 'x'}\n"
    )
    b = _branch(src, ["enqueue_branch_run"])
    with pytest.raises(CompilerError) as exc:
        _run(b, thread="enq-nobranch")
    assert "branch_def_id" in str(exc.value)


# ── Fix 1: universe targeting ────────────────────────────────────────────────

def test_enqueue_refuses_without_trusted_universe(monkeypatch):
    # Absent trusted context (e.g. a direct, non-dispatched run) → fail closed.
    monkeypatch.setenv("TINYASSETS_NODE_ENQUEUE_ENABLED", "on")
    captured = _patch_storage(monkeypatch)
    b = _branch(ENQUEUE_ONE, ["enqueue_branch_run"])
    with pytest.raises(CompilerError) as exc:
        _run(b, thread="enq-nouni", context=_ctx(universe_id=""))
    assert "trusted command center" in str(exc.value)
    assert captured == []


def test_enqueue_refuses_foreign_universe(monkeypatch):
    # A node may not target a universe other than the run's own.
    monkeypatch.setenv("TINYASSETS_NODE_ENQUEUE_ENABLED", "on")
    captured = _patch_storage(monkeypatch)
    b = _branch(ENQUEUE_FOREIGN_UNIVERSE, ["enqueue_branch_run"])
    with pytest.raises(CompilerError) as exc:
        _run(b, thread="enq-foreign")
    assert "cannot target command center" in str(exc.value)
    assert captured == []


# ── Fix 3: target branch authority (reuses existing visibility model) ─────────


# ── Fix 2: spawn lineage stamping + cap surfacing ────────────────────────────


@pytest.mark.parametrize(
    ("parent", "origin", "expected"),
    [
        ("", "", "run:run-123"),
        ("parent-task", "", "parent-task"),
        ("parent-task", "origin-task", "origin-task"),
    ],
)
def test_execute_branch_derives_stable_root_origin_with_explicit_precedence(
    tmp_path, monkeypatch, parent, origin, expected,
):
    captured = []
    sentinel = object()
    monkeypatch.setattr(runs, "_prepare_run", lambda *args, **kwargs: "run-123")

    def _capture_invoke(*args, **kwargs):
        captured.append(kwargs["enqueue_context"])
        return sentinel

    monkeypatch.setattr(runs, "_invoke_graph", _capture_invoke)
    outcome = runs.execute_branch(
        tmp_path,
        branch=_branch(ENQUEUE_ONE, ["enqueue_branch_run"]),
        inputs={},
        _enqueue_universe_id="u",
        _parent_branch_task_id=parent,
        _origin_branch_task_id=origin,
        actor="universe:u-test",
    )

    assert outcome is sentinel
    assert len(captured) == 1
    assert captured[0].parent_branch_task_id == parent
    assert captured[0].origin_branch_task_id == expected


# ── BranchTask migration-safety ──────────────────────────────────────────────

def test_branch_task_lineage_fields_roundtrip():
    # Migration-safe: new fields default to "" and survive to_dict/from_dict.
    t = bt.BranchTask(branch_task_id="x", branch_def_id="b", universe_id="u")
    assert t.depth == 0
    assert t.parent_branch_task_id == ""
    assert t.origin_branch_task_id == ""
    rt = bt.BranchTask.from_dict({
        **t.to_dict(), "depth": 3,
        "parent_branch_task_id": "P", "origin_branch_task_id": "O",
    })
    assert (rt.depth, rt.parent_branch_task_id, rt.origin_branch_task_id) == (3, "P", "O")
    # Old rows without the new fields still load.
    old = bt.BranchTask.from_dict(
        {"branch_task_id": "y", "branch_def_id": "b", "universe_id": "u"}
    )
    assert (old.depth, old.parent_branch_task_id, old.origin_branch_task_id) == (0, "", "")


# ── append_task_capped: atomic queue-growth containment (Fix 2 core) ──────────

def _task(tid, origin="", status="pending"):
    return bt.BranchTask(
        branch_task_id=tid, branch_def_id="b", universe_id="u",
        trigger_source="owner_queued", status=status,
        origin_branch_task_id=origin,
    )


def test_append_task_capped_allows_under_caps(tmp_path):
    bt.append_task_capped(tmp_path, _task("t1", origin="O"), max_active=5, max_lineage=5)
    assert len(bt.read_queue(tmp_path)) == 1


def test_append_task_capped_global_active_cap(tmp_path):
    bt.append_task_capped(tmp_path, _task("t1"), max_active=1)
    with pytest.raises(bt.QueueCapExceeded) as exc:
        bt.append_task_capped(tmp_path, _task("t2"), max_active=1)
    assert "active" in str(exc.value)
    assert len(bt.read_queue(tmp_path)) == 1  # second never landed


def test_append_task_capped_per_origin_lineage_cap(tmp_path):
    bt.append_task_capped(tmp_path, _task("t1", origin="O"), max_lineage=1)
    # Same origin → refused.
    with pytest.raises(bt.QueueCapExceeded) as exc:
        bt.append_task_capped(tmp_path, _task("t2", origin="O"), max_lineage=1)
    assert "lineage" in str(exc.value)
    # Different origin → still allowed (cap is per-origin, not global).
    bt.append_task_capped(tmp_path, _task("t3", origin="O2"), max_lineage=1)
    assert len(bt.read_queue(tmp_path)) == 2


def _write_archive(universe_path: Path, tasks: list[bt.BranchTask]) -> None:
    bt.archive_path(universe_path).write_text(
        json.dumps([task.to_dict() for task in tasks]),
        encoding="utf-8",
    )


def test_append_task_capped_counts_archived_lineage(tmp_path):
    _write_archive(tmp_path, [_task("archived-1", origin="O", status="succeeded")])

    with pytest.raises(bt.QueueCapExceeded) as exc:
        bt.append_task_capped(
            tmp_path,
            _task("next", origin="O"),
            max_active=10,
            max_lineage=1,
        )

    assert "lineage" in str(exc.value)
    assert bt.read_queue(tmp_path) == []


def test_append_task_capped_keeps_unrelated_archived_origins_admissible(tmp_path):
    _write_archive(
        tmp_path,
        [_task("other-1", origin="OTHER", status="succeeded")],
    )

    bt.append_task_capped(
        tmp_path,
        _task("ours-1", origin="OURS"),
        max_active=10,
        max_lineage=1,
    )

    assert [task.branch_task_id for task in bt.read_queue(tmp_path)] == ["ours-1"]


def test_append_task_capped_global_cap_ignores_terminal_and_archived_rows(tmp_path):
    bt.append_task(tmp_path, _task("terminal-live", origin="O", status="pending"))
    queue_file = bt.queue_path(tmp_path)
    raw = json.loads(queue_file.read_text(encoding="utf-8"))
    raw[0]["status"] = "succeeded"
    queue_file.write_text(json.dumps(raw), encoding="utf-8")
    _write_archive(
        tmp_path,
        [_task("terminal-archived", origin="O", status="succeeded")],
    )

    bt.append_task_capped(
        tmp_path,
        _task("active-1", origin="OTHER"),
        max_active=1,
        max_lineage=10,
    )
    with pytest.raises(bt.QueueCapExceeded):
        bt.append_task_capped(
            tmp_path,
            _task("active-2", origin="ANOTHER"),
            max_active=1,
            max_lineage=10,
        )

    assert {task.branch_task_id for task in bt.read_queue(tmp_path)} == {
        "terminal-live",
        "active-1",
    }


def test_append_task_capped_deduplicates_live_archive_task_id(tmp_path):
    overlap = _task("overlap", origin="O", status="succeeded")
    bt.append_task(tmp_path, overlap)
    _write_archive(tmp_path, [overlap])

    bt.append_task_capped(
        tmp_path,
        _task("next", origin="O"),
        max_active=10,
        max_lineage=2,
    )

    assert {task.branch_task_id for task in bt.read_queue(tmp_path)} == {
        "overlap",
        "next",
    }


def test_append_task_capped_corrupt_archive_fails_without_mutation(tmp_path):
    archive = bt.archive_path(tmp_path)
    archive.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError):
        bt.append_task_capped(
            tmp_path,
            _task("next", origin="O"),
            max_active=10,
            max_lineage=2,
        )

    assert archive.read_text(encoding="utf-8") == "{not-json"
    assert bt.read_queue(tmp_path) == []


@pytest.mark.parametrize("blank_content", ["", " \n\t"])
def test_append_task_capped_blank_live_queue_fails_without_mutation(
    tmp_path, blank_content,
):
    queue = bt.queue_path(tmp_path)
    queue.write_text(blank_content, encoding="utf-8")
    before = queue.read_bytes()

    with pytest.raises(RuntimeError, match="Corrupt queue"):
        bt.append_task_capped(
            tmp_path,
            _task("new", origin="O"),
            max_active=5,
            max_lineage=5,
        )

    assert queue.read_bytes() == before
    assert not bt.archive_path(tmp_path).exists()


@pytest.mark.parametrize("blank_content", ["", " \n\t"])
def test_append_task_capped_blank_archive_fails_without_mutation(
    tmp_path, blank_content,
):
    archive = bt.archive_path(tmp_path)
    archive.write_text(blank_content, encoding="utf-8")
    archive_before = archive.read_bytes()

    with pytest.raises(RuntimeError, match="Corrupt queue"):
        bt.append_task_capped(
            tmp_path,
            _task("new", origin="O"),
            max_active=5,
            max_lineage=5,
        )

    assert archive.read_bytes() == archive_before
    assert not bt.queue_path(tmp_path).exists()
