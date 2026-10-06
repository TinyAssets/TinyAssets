"""#60 — async runner must emit in-flight progress events.

Before the fix, ``_on_node`` fired only AFTER a node completed, so a
long-running LLM call (4+ minutes on the legal pipeline) looked frozen
to any polling client. Claude.ai displayed "still running, no new
events" until the node finished.

The fix: the compiler now emits TWO events per node — phase="starting"
before the provider call (status=running) and phase="ran" after
(status=ran). stream_run picks up the starting event immediately, so
the user sees "node X running..." instead of silence.
"""

from __future__ import annotations

import importlib

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.runs import (
    NODE_STATUS_PENDING,
    NODE_STATUS_RAN,
    NODE_STATUS_RUNNING,
    build_node_status_map,
)

#: Runs are universe-owned: `run_branch` returns
#: `branch_run_requires_universe` without one, and
#: `universe_access_denied` if it is registered but not ACL-granted.
_UNIVERSE = "universe_progress_events"

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def us_env(tmp_path, monkeypatch, authenticate_request):
    """Universe-server fixture: autowires SQLite init like the phase3
    suite. Returns (us_module, base_path)."""
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # `UNIVERSE_SERVER_USER` alone does not reach branch WRITE actions:
    # they resolve the caller via `_request_branch_actor()`, which is
    # credential-derived and documented "never an env actor". Without a
    # bound request subject they return "Authenticated branch subject
    # required." before parsing anything, and every later assertion dies on
    # a KeyError that hides the real cause.
    # `run_branch` is gated on `tinyassets.extensions.costly`, which the
    # default test credential deliberately EXCLUDES so that suites
    # asserting a costly action is refused keep asserting something.
    # This file asserts run progress, never a refusal, so it opts in.
    authenticate_request(
        "tester",
        capabilities=[
            "tinyassets.extensions.read",
            "tinyassets.extensions.write",
            "tinyassets.extensions.admin",
            "tinyassets.extensions.costly",
        ],
    )
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
    )

    udir = base / _UNIVERSE
    udir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(base, universe_id=_UNIVERSE, universe_path=udir)
    grant_universe_access(
        base,
        universe_id=_UNIVERSE,
        actor_id="tester",
        permission="write",
        granted_by="us_env",
    )

    from tinyassets import universe_server as us
    provider_calls = importlib.import_module("tinyassets.providers.call")

    importlib.reload(us)
    monkeypatch.setattr(
        provider_calls,
        "call_provider",
        lambda prompt, _system="", **_kwargs: f"fixture:{prompt}",
    )
    yield us, base
    importlib.reload(us)


def _make_recipe_branch() -> BranchDefinition:
    b = BranchDefinition(name="progress-probe", entry_point="capture")
    b.node_defs = [
        NodeDefinition(
            node_id="capture", display_name="Capture",
            prompt_template="Echo: {raw}",
            output_keys=["capture_output"],
            input_keys=["raw"],
        ),
        NodeDefinition(
            node_id="tag", display_name="Tag",
            prompt_template="Tag: {capture_output}",
            output_keys=["tag_output"],
            input_keys=["capture_output"],
        ),
    ]
    b.graph_nodes = [
        GraphNodeRef(id="capture", node_def_id="capture"),
        GraphNodeRef(id="tag", node_def_id="tag"),
    ]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="capture"),
        EdgeDefinition(from_node="capture", to_node="tag"),
        EdgeDefinition(from_node="tag", to_node="END"),
    ]
    b.state_schema = [
        {"name": "raw", "type": "str", "default": ""},
        {"name": "capture_output", "type": "str", "default": ""},
        {"name": "tag_output", "type": "str", "default": ""},
    ]
    return b


# ─── Compiler-level: event_sink receives both phases ─────────────────────


def test_compiler_emits_starting_then_ran_per_node():
    """The compiler fires event_sink twice per prompt_template node:
    once before the provider call (phase=starting), once after
    (phase=ran)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.graph_compiler import compile_branch

    events: list[dict] = []

    def _sink(**kw):
        events.append(kw)

    branch = _make_recipe_branch()
    compiled = compile_branch(
        branch,
        provider_call=lambda p, s, *, role: "ok",
        event_sink=_sink,
    )
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    runnable.invoke(
        {"raw": "x"},
        config={"configurable": {"thread_id": "t1"}},
    )

    # 2 nodes × 2 phases = 4 events, in interleaved order.
    phases_per_node: dict[str, list[str]] = {}
    for ev in events:
        phases_per_node.setdefault(ev["node_id"], []).append(ev["phase"])

    assert phases_per_node["capture"] == ["starting", "ran"]
    assert phases_per_node["tag"] == ["starting", "ran"]


def test_compiler_starting_event_includes_prompt_preview():
    """Starting events carry a prompt preview so clients can surface
    'working on: ...' context without waiting for the response."""
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.graph_compiler import compile_branch

    starting_events: list[dict] = []

    def _sink(**kw):
        if kw.get("phase") == "starting":
            starting_events.append(kw)

    branch = _make_recipe_branch()
    compiled = compile_branch(
        branch,
        provider_call=lambda p, s, *, role: "ok",
        event_sink=_sink,
    )
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    runnable.invoke(
        {"raw": "hello"},
        config={"configurable": {"thread_id": "t2"}},
    )

    assert starting_events
    # Capture node's prompt should include the rendered 'raw' value.
    capture_start = next(
        e for e in starting_events if e["node_id"] == "capture"
    )
    assert "hello" in capture_start["prompt_preview"]
    assert capture_start["role"] == "writer"


# ─── Integration: _on_node writes RUNNING then RAN rows to SQLite ────────


def test_build_node_status_map_surfaces_running_during_flight():
    """During a long-running node, only the starting event has fired.
    build_node_status_map should show that node as 'running', not
    'pending' — so the UI can say 'working on node X'."""
    declared = ["a", "b", "c"]
    events = [
        {"node_id": "a", "status": NODE_STATUS_RUNNING, "step_index": 0},
        {"node_id": "a", "status": NODE_STATUS_RAN, "step_index": 1},
        {"node_id": "b", "status": NODE_STATUS_RUNNING, "step_index": 2},
        # b hasn't finished yet; c hasn't started.
    ]
    statuses = build_node_status_map(events, declared)
    by_node = {s["node_id"]: s["status"] for s in statuses}
    assert by_node["a"] == NODE_STATUS_RAN
    assert by_node["b"] == NODE_STATUS_RUNNING
    assert by_node["c"] == NODE_STATUS_PENDING
