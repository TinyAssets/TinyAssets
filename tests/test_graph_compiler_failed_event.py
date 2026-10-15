"""BUG-038 / BUG-041 terminal phase="failed" event emission tests.

When a node's provider call raises, graph_compiler must emit
``event_sink(phase="failed", ...)`` BEFORE re-raising as ``CompilerError``.
Without it, the most recent event for the node is the earlier
``phase="starting"`` row, and ``build_node_status_map`` keeps the node
parked at NODE_STATUS_RUNNING even after the run flips to RUN_STATUS_FAILED
- the contradictory shape both bug pages report.

Two contracts under test:
1. The compiler-side emission: phase="failed" fires from BOTH the policy-
   aware exception handler AND the plain provider_call handler in
   tinyassets/graph_compiler.py.
2. The runs.py-side translator: phase="failed" maps to NODE_STATUS_FAILED
   in both _on_node handlers, so build_node_status_map flips the node
   to "failed" terminally.
"""

from __future__ import annotations

import pytest

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import CompilerError, compile_branch
from tinyassets.runs import (
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    RUN_STATUS_FAILED,
    build_node_status_map,
    execute_branch,
    list_events,
)


def _simple_branch() -> BranchDefinition:
    b = BranchDefinition(name="failed-event-probe", entry_point="step1")
    b.node_defs = [NodeDefinition(
        node_id="step1", display_name="Step1",
        prompt_template="Do: {x}",
        output_keys=["out"],
        input_keys=["x"],
        timeout_seconds=5.0,
    )]
    b.graph_nodes = [GraphNodeRef(id="step1", node_def_id="step1")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="step1"),
        EdgeDefinition(from_node="step1", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "str", "default": ""},
        {"name": "out", "type": "str", "default": ""},
    ]
    return b


# Compiler-side emission


def test_failed_event_emitted_on_provider_exception():
    """Plain provider_call path: when provider raises, event_sink must
    receive phase='failed' before CompilerError propagates."""
    from langgraph.checkpoint.memory import InMemorySaver

    captured: list[dict] = []

    def _sink(node_id, **detail):
        captured.append({"node_id": node_id, **detail})

    def _exhausted(prompt, system, *, role):
        raise RuntimeError("All providers exhausted for role=writer")

    branch = _simple_branch()
    compiled = compile_branch(branch, provider_call=_exhausted, event_sink=_sink)
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(CompilerError):
        runnable.invoke(
            {"x": "test"},
            config={"configurable": {"thread_id": "t-fail-plain"}},
        )

    phases = [e.get("phase") for e in captured if e["node_id"] == "step1"]
    assert "starting" in phases, f"missing starting event: {phases}"
    assert "failed" in phases, f"missing failed event: {phases}"
    failed_events = [e for e in captured if e.get("phase") == "failed"]
    assert failed_events[0]["node_id"] == "step1"
    assert "exhausted" in failed_events[0]["error"].lower()
    assert failed_events[0]["error_type"] == "RuntimeError"


def test_failed_event_emitted_on_policy_call_exception():
    from langgraph.checkpoint.memory import InMemorySaver

    captured = []
    def refusing(prompt, system, **kwargs):
        assert kwargs["policy"]["preferred"]["provider"] == "codex"
        raise RuntimeError("one call refused")

    branch = _simple_branch()
    branch.node_defs[0].llm_policy = {"preferred": {"provider": "codex"}}
    compiled = compile_branch(branch, provider_call=refusing,
                              event_sink=lambda **event: captured.append(event))
    with pytest.raises(CompilerError, match="one call refused"):
        compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
            {"x": "test"}, config={"configurable": {"thread_id": "t-fail-policy"}},
        )
    assert any(event.get("phase") == "failed" for event in captured)


def test_policy_node_uses_injected_governed_call(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    calls = []
    def governed(prompt, system, **kwargs):
        calls.append(kwargs)
        return "governed result"

    monkeypatch.setattr(
        "tinyassets.graph_compiler._get_shared_router",
        lambda: (_ for _ in ()).throw(AssertionError("shared router escape")),
    )
    branch = _simple_branch()
    policy = {"preferred": {"provider": "requester-provider"}}
    branch.node_defs[0].llm_policy = policy
    compiled = compile_branch(branch, provider_call=governed)
    result = compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
        {"x": "test"}, config={"configurable": {"thread_id": "t-governed-policy"}},
    )
    assert result["out"] == "governed result"
    assert len(calls) == 1 and calls[0]["policy"] == policy
    assert calls[0]["config"] is not None


def test_plain_node_forwards_config_to_injected_kwargs_caller():
    """Authority-bearing callable objects accept node config via **kwargs."""
    from langgraph.checkpoint.memory import InMemorySaver

    class _GovernedCaller:
        def __init__(self) -> None:
            self.config = None

        def __call__(self, _prompt, _system, *, role, **kwargs):
            assert role == "writer"
            self.config = kwargs.get("config")
            return "governed result"

    caller = _GovernedCaller()
    compiled = compile_branch(_simple_branch(), provider_call=caller)
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())

    result = runnable.invoke(
        {"x": "test"},
        config={"configurable": {"thread_id": "t-governed-kwargs"}},
    )

    assert result["out"] == "governed result"
    assert caller.config is not None


def test_failed_event_emission_resilient_to_sink_exception():
    """If the event_sink itself raises while emitting phase='failed', the
    CompilerError must still propagate (no double-fault swallow)."""
    from langgraph.checkpoint.memory import InMemorySaver

    sink_calls: list[str] = []

    def _flaky_sink(node_id, **detail):
        sink_calls.append(detail.get("phase", "?"))
        if detail.get("phase") == "failed":
            raise RuntimeError("sink boom")

    def _exhausted(prompt, system, *, role):
        raise RuntimeError("All providers exhausted")

    branch = _simple_branch()
    compiled = compile_branch(
        branch, provider_call=_exhausted, event_sink=_flaky_sink,
    )
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(CompilerError):
        runnable.invoke(
            {"x": "test"},
            config={"configurable": {"thread_id": "t-fail-flaky"}},
        )
    assert "failed" in sink_calls, f"failed phase never attempted: {sink_calls}"


def test_no_failed_event_when_no_event_sink():
    """The helper must no-op cleanly when event_sink is None - nodes can
    be compiled without a sink (e.g. dry-run or unit-test paths)."""
    from langgraph.checkpoint.memory import InMemorySaver

    def _exhausted(prompt, system, *, role):
        raise RuntimeError("All providers exhausted")

    branch = _simple_branch()
    compiled = compile_branch(branch, provider_call=_exhausted, event_sink=None)
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(CompilerError):
        runnable.invoke(
            {"x": "test"},
            config={"configurable": {"thread_id": "t-fail-no-sink"}},
        )


# runs.py translator + build_node_status_map contract


def test_execute_branch_records_failed_node_event(tmp_path):
    """Provider exceptions must leave a terminal failed event in run_events."""

    def _exhausted(prompt, system, *, role):
        raise RuntimeError("All providers exhausted")

    base = tmp_path / "output"
    base.mkdir()
    outcome = execute_branch(
        base,
        branch=_simple_branch(),
        inputs={"x": "test"},
        actor="tester",
        provider_call=_exhausted,
    )

    assert outcome.status == RUN_STATUS_FAILED
    events = list_events(base, outcome.run_id, since_step=-1)
    step_events = [event for event in events if event["node_id"] == "step1"]
    statuses = [event["status"] for event in step_events]
    assert NODE_STATUS_RUNNING in statuses
    assert NODE_STATUS_FAILED in statuses

    failed = next(
        event for event in step_events
        if event["status"] == NODE_STATUS_FAILED
    )
    assert failed["detail"]["error_type"] == "RuntimeError"
    assert "exhausted" in failed["detail"]["error"].lower()


def test_build_node_status_map_flips_failed_node_terminal():
    """The actual contract closure: a synthetic event stream containing a
    starting -> failed transition for a node must produce status='failed'
    in the final map (not 'running'). This is the BUG-038/041 symptom."""
    declared = ["step1", "step2"]
    events = [
        {"node_id": "step1", "status": NODE_STATUS_RUNNING},
        {"node_id": "step1", "status": NODE_STATUS_FAILED},
    ]
    result = build_node_status_map(events, declared)
    by_id = {row["node_id"]: row["status"] for row in result}
    assert by_id["step1"] == NODE_STATUS_FAILED, (
        f"failed node should be terminal, got {by_id['step1']}"
    )


@pytest.mark.parametrize("graph_id", ["step1", "code-instance"])
@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("failure_kind", ["exception", "timeout"])
def test_code_failure_records_actual_graph_node(
    tmp_path, monkeypatch, graph_id, parallel, failure_kind,
):
    """A real child failure must not leave a running node or use a definition ID."""
    from tinyassets.boxes import node as node_sandbox

    sandbox_type = node_sandbox.NodeSandbox

    def local_test_sandbox(**kwargs):
        # The child runs in the owner box double. The child and error are real.
        return sandbox_type(**kwargs)

    monkeypatch.setattr(node_sandbox, "NodeSandbox", local_test_sandbox)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    branch = _simple_branch()
    branch.node_defs[0].prompt_template = ""
    branch.author = "tester"
    branch.node_defs[0].source_code = (
        "def run(state):\n    raise RuntimeError('intentional-code-probe')\n"
    )
    if failure_kind == "timeout":
        branch.node_defs[0].source_code = "def run(state):\n    import time\n    time.sleep(2)\n"
        branch.node_defs[0].timeout_seconds = 0.1
    branch.entry_point = graph_id
    branch.graph_nodes = [GraphNodeRef(id=graph_id, node_def_id="step1")]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node=graph_id),
        EdgeDefinition(from_node=graph_id, to_node="END"),
    ]
    if parallel:
        branch.node_defs.append(NodeDefinition(
            node_id="sibling-definition", display_name="Sibling", output_keys=["sibling_out"],
            source_code="def run(state):\n    return {'sibling_out': 'done'}\n",
        ))
        branch.graph_nodes.append(GraphNodeRef(id="sibling", node_def_id="sibling-definition"))
        branch.edges.extend([
            EdgeDefinition(from_node="START", to_node="sibling"),
            EdgeDefinition(from_node="sibling", to_node="END"),
        ])
        branch.state_schema.append({"name": "sibling_out", "type": "str"})
    base = tmp_path / "runs"
    base.mkdir()
    # The run executes inside its owner's admitted box session: every code
    # node, parallel siblings included, carries that one binding.
    from tinyassets.control_plane import binding as binding_module

    with binding_module.use_binding(binding_module.file_binding(base)):
        outcome = execute_branch(base, branch=branch, inputs={"x": "test"}, actor="tester")
    assert outcome.status == RUN_STATUS_FAILED
    assert ("timeout" if failure_kind == "timeout" else "intentional-code-probe") in outcome.error
    events = list_events(base, outcome.run_id, since_step=-1)
    failed = [event for event in events if event["status"] == NODE_STATUS_FAILED]
    assert [event["node_id"] for event in failed] == [graph_id]
    status_map = build_node_status_map(events, [graph_id])
    assert {row["node_id"]: row["status"] for row in status_map}[graph_id] == NODE_STATUS_FAILED
    if parallel:
        sibling = [event for event in events if event["node_id"] == "sibling"]
        assert any(event["status"] == "ran" for event in sibling), sibling
        assert not any(event["status"] == NODE_STATUS_FAILED for event in sibling)


def test_build_node_status_map_failed_priority_beats_running():
    """A 'failed' status arriving after 'running' must win - priority of
    failed and ran is equal (both 2), each beats running (priority 1)."""
    declared = ["step1"]
    events = [
        {"node_id": "step1", "status": "pending"},
        {"node_id": "step1", "status": NODE_STATUS_RUNNING},
        {"node_id": "step1", "status": NODE_STATUS_FAILED},
    ]
    result = build_node_status_map(events, declared)
    assert result[0]["status"] == NODE_STATUS_FAILED


def test_cancelled_node_is_terminal_without_relabeling_other_nodes():
    events = [
        {"node_id": "finished", "status": "ran"},
        {"node_id": "stopped", "status": "running"},
        {"node_id": "stopped", "status": "cancelled"},
        {"node_id": "stopped", "status": "running"},
        {"node_id": "parallel", "status": "running"},
    ]
    statuses = build_node_status_map(events, ["finished", "stopped", "parallel", "unstarted"])
    assert {row["node_id"]: row["status"] for row in statuses} == {
        "finished": "ran", "stopped": "cancelled", "parallel": "running", "unstarted": "pending",
    }


def test_between_node_cancel_preserves_the_completed_node(tmp_path, monkeypatch):
    """A successful result is observed before the inter-node cancellation checkpoint."""
    import threading

    from tinyassets import runs

    stop = threading.Event()
    monkeypatch.setattr(runs, "is_cancel_requested", lambda *args: stop.is_set())

    def finish_then_cancel(prompt, system, *, role):
        stop.set()
        return "finished before cancellation was observed"

    outcome = execute_branch(
        tmp_path, branch=_simple_branch(), inputs={"x": "test"}, actor="tester",
        provider_call=finish_then_cancel,
    )
    assert outcome.status == "cancelled"
    events = list_events(tmp_path, outcome.run_id, since_step=-1)
    statuses = build_node_status_map(events, ["step1"])
    assert {row["node_id"]: row["status"] for row in statuses}["step1"] == "ran"


@pytest.mark.parametrize("terminal_phase", ["cancelled", "ran"])
def test_resume_translator_records_observed_terminal_before_cancel(
    tmp_path, monkeypatch, terminal_phase,
):
    """Drive the resume event translator with a saved run, without faking child-stop proof."""
    from types import SimpleNamespace

    from tinyassets import runs
    from tinyassets.graph_compiler import NodeCancelledError

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    branch = _simple_branch()
    rid = runs.create_run(tmp_path, branch_def_id=branch.branch_def_id,
                          thread_id="resume-cancel", inputs={"x": "test"}, actor="tester")

    def compile_events(branch, *, event_sink, **kwargs):
        def invoke(*args, **kwargs):
            event_sink("step1", phase="starting")
            runs.request_cancel(tmp_path, rid)
            event_sink("step1", phase=terminal_phase)
            if terminal_phase == "cancelled":
                raise NodeCancelledError("actually stopped", node_id="step1")
            return {}
        return SimpleNamespace(graph=SimpleNamespace(
            compile=lambda **kwargs: SimpleNamespace(invoke=invoke),
        ))

    monkeypatch.setattr(runs, "compile_branch", compile_events)
    # recursion_limit / concurrency_budget_override are REQUIRED since the
    # exact-admission slice: a resume must state the choices it was admitted
    # with rather than silently inheriting a default.
    outcome = runs._invoke_graph_resume(tmp_path, run_id=rid, branch=branch,
                                        thread_id="resume-cancel", provider_call=None,
                                        recursion_limit=runs.DEFAULT_RECURSION_LIMIT,
                                        concurrency_budget_override=None)
    assert outcome.status == "cancelled", outcome.error
    statuses = build_node_status_map(list_events(tmp_path, rid, since_step=-1), ["step1"])
    assert statuses == [{"node_id": "step1", "status": terminal_phase}]


@pytest.mark.parametrize("graph_id", ["step1", "actual_instance"])
def test_cancelled_diagram_uses_graph_instance_and_terminal_class(tmp_path, monkeypatch, graph_id):
    from tinyassets import daemon_server
    from tinyassets.api import runs as api_runs

    branch = _simple_branch()
    branch.graph_nodes[0].id = graph_id
    branch.entry_point = graph_id
    branch.edges = [EdgeDefinition(from_node="START", to_node=graph_id),
                    EdgeDefinition(from_node=graph_id, to_node="END")]
    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(daemon_server, "get_branch_definition",
                        lambda *args, **kwargs: branch.to_dict())
    diagram = api_runs._run_mermaid_from_events(branch.branch_def_id, [
        {"node_id": graph_id, "status": "cancelled"},
    ])
    assert f"class {graph_id} cancelled" in diagram
    assert "classDef cancelled" in diagram
    assert f'{graph_id}["Step1"]' in diagram
