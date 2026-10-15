"""Effects fire at node time (change `sandboxed-code-node`, design D1).

Live 2026-08-30, runs 6cb4d9f48a9949be / 7a7a91c14b0d4b8b: `write_readme`
was refused before the wire and `open_pr` still fired into a GitHub 422,
leaving dangling branches - because every effect fired after the whole
graph, in storage order, with no way to stop. Now a node's effects fire the
moment it returns; a refused packet fails the node and later nodes never run;
a later node reads an ancestor's FULL response, never the 4 KiB preview.
"""

from __future__ import annotations

import json
import os as _os

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tinyassets import effectors
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.effectors import (
    EffectChain,
    EffectFailedError,
    dispatch_node_effects,
    forget_effect_chain,
    register_effect_chain,
)
from tinyassets.effectors import EffectChain as _Chain
from tinyassets.effectors import WorkspaceMount as _Mount
from tinyassets.effectors.workspace import EXTERNAL_WRITE_SINK_WORKSPACE
from tinyassets.graph_compiler import _delta_view, _graph_ancestors, compile_branch

SINK = "authenticated_external_call"


def _packet(verb="GET", **extra):
    packet = {
        "sink": SINK, "connection_id": "c1", "grant_id": "g1", "verb": verb,
        "request": {"method": verb, "url": "https://api.example.test/x"},
    }
    packet.update(extra)
    return json.dumps(packet)


def _effect_node(node_id, verb="GET", input_keys=(), **extra):
    return NodeDefinition(
        node_id=node_id, display_name=node_id,
        prompt_template=f"packet:{node_id}",
        output_keys=[f"{node_id}_packet"],
        input_keys=list(input_keys),
        effects=[SINK],
    )


#: Every run in this file executes as this actor, and the branches it builds
#: are authored by it. Code runs in the universe that authored it, so a branch
#: with no author is foreign to every actor -- which used to be invisible only
#: because `author` and the run actor both defaulted to "anonymous" and two
#: placeholders compared equal.
ACTOR = "universe:u-test"


def _linear(*nodes):
    b = BranchDefinition(name="chain", entry_point=nodes[0].node_id, author=ACTOR)
    b.node_defs = list(nodes)
    b.graph_nodes = [GraphNodeRef(id=n.node_id, node_def_id=n.node_id) for n in nodes]
    edges = [EdgeDefinition(from_node="START", to_node=nodes[0].node_id)]
    for a, c in zip(nodes, nodes[1:]):
        edges.append(EdgeDefinition(from_node=a.node_id, to_node=c.node_id))
    edges.append(EdgeDefinition(from_node=nodes[-1].node_id, to_node="END"))
    b.edges = edges
    b.state_schema = [{"name": f"{n.node_id}_packet", "type": "str"} for n in nodes]
    return b


def _provider_for(packets):
    def fake(prompt, system="", *, role="writer", fallback_response=None):
        node_id = prompt.split("packet:", 1)[1].strip()
        return packets[node_id]
    return fake


class _Adapter:
    """A stand-in for the authenticated-call effector: records what it was
    handed and answers from a script."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, *, node_id, output_keys, run_state, base_path, run_id, dry_run,
                 allowed_state_keys=None, prior_effects=None):
        self.calls.append({
            "node_id": node_id,
            "prior": {k: v for k, v in (prior_effects or {}).items()},
            "packet": run_state.get(output_keys[0]),
            "state": {k: run_state.get(k) for k in ("content", "sha")},
        })
        return json.loads(json.dumps(self.script[node_id]))


@pytest.fixture
def center(tmp_path, monkeypatch):
    """A command center whose owner's box session is bound for the test.

    Code nodes run only in the admitted owner box of the center they belong to.
    """
    from tinyassets.control_plane import binding as binding_module

    data_root = tmp_path / "data"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
    cc = data_root / "cc-effects"
    cc.mkdir(parents=True)
    with binding_module.use_binding(binding_module.file_binding(cc)):
        yield cc


def _run(branch, packets, adapter, monkeypatch, run_id="r1", base_path=None):
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    chain = EffectChain(run_id=run_id, base_path=None)
    compiled = compile_branch(branch, provider_call=_provider_for(packets), effect_chain=chain,
                              base_path=base_path)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    app.invoke({}, config={"configurable": {"thread_id": run_id}})
    return chain


_OK_GET = {"delivered": True, "verb": "GET",
           "response": {"status": 200, "body": '{"content": "aGVsbG8=", "sha": "s1"}',
                        "headers": {"set-cookie": "secret=1"}}}
_OK_PUT = {"delivered": True, "verb": "PUT", "response": {"status": 201, "body": "{}"}}


def test_compiled_workspace_effect_receives_the_root_run_cancellation_closure(monkeypatch):
    seen = []
    def cancelled():
        return False
    def adapter(*, should_cancel, **kwargs):
        seen.append(should_cancel)
        assert "should_cancel" not in kwargs["run_state"]
        return {"op": "checkout"}
    monkeypatch.setitem(effectors._EFFECTORS, EXTERNAL_WRITE_SINK_WORKSPACE, adapter)
    node = _effect_node("checkout")
    node.effects = [EXTERNAL_WRITE_SINK_WORKSPACE]
    branch = _linear(node)
    chain = EffectChain(run_id="cancel-closure", base_path=None)
    compiled = compile_branch(
        branch, provider_call=_provider_for({"checkout": json.dumps({"op": "checkout"})}),
        effect_chain=chain, should_cancel=cancelled)
    compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
        {}, config={"configurable": {"thread_id": "cancel-closure"}})
    assert seen == [cancelled]


def test_cancellation_after_model_returns_prevents_effect_dispatch(monkeypatch):
    from tinyassets.graph_compiler import NodeCancelledError
    stop = []
    adapter = _Adapter({"write": _OK_PUT})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    branch = _linear(_effect_node("write", "PUT"))
    chain = EffectChain(run_id="cancel-before-effect", base_path=None)
    def provider(*args, **kwargs):
        stop.append(True)
        return _packet("PUT")
    compiled = compile_branch(branch, provider_call=provider, effect_chain=chain,
                              should_cancel=lambda: bool(stop))
    with pytest.raises(NodeCancelledError, match="before effects") as caught:
        compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
            {}, config={"configurable": {"thread_id": "cancel-before-effect"}})
    assert caught.value.node_id == "write"
    assert adapter.calls == []
    assert chain.fired == []
    assert chain.dispatches == 0


@pytest.mark.parametrize("cancel_requested", [True, False])
def test_failed_workspace_stage_is_cancelled_only_by_root_predicate(monkeypatch, cancel_requested):
    from tinyassets.graph_compiler import NodeCancelledError
    stop = []
    def adapter(**kwargs):
        stop.append(cancel_requested)
        return {"error": "install stopped", "error_kind": "workspace_provision_failed",
                "provision_reason": "cancelled"}
    monkeypatch.setitem(effectors._EFFECTORS, EXTERNAL_WRITE_SINK_WORKSPACE, adapter)
    node = _effect_node("checkout")
    node.effects = [EXTERNAL_WRITE_SINK_WORKSPACE]
    chain = EffectChain(run_id="cancel-during-install", base_path=None)
    compiled = compile_branch(
        _linear(node), provider_call=_provider_for({"checkout": json.dumps({"op": "checkout"})}),
        effect_chain=chain, should_cancel=lambda: bool(stop and stop[0]))
    expected = NodeCancelledError if cancel_requested else effectors.EffectFailedError
    with pytest.raises(expected):
        compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
            {}, config={"configurable": {"thread_id": "cancel-during-install"}})
    assert chain.dispatches == 1


def test_workspace_adapter_forwards_cancellation_without_serializing_it(monkeypatch):
    seen = []
    def cancelled():
        return True
    def effector(**kwargs):
        seen.append(kwargs)
        return {"op": "checkout"}
    monkeypatch.setattr(effectors, "run_workspace_effector", effector)
    effectors._workspace_adapter(node_id="checkout", output_keys=[], run_state={},
                                 base_path=None, run_id="r", dry_run=False,
                                 should_cancel=cancelled)
    assert seen[0]["should_cancel"] is cancelled
    assert seen[0]["run_state"] == {}


def test_workspace_effect_receives_the_nodes_contention_wait_budget(monkeypatch):
    seen: list[float] = []

    def workspace_adapter(*, timeout_seconds, **_kwargs):
        seen.append(timeout_seconds)
        return {"dry_run": True, "op": "create"}

    monkeypatch.setitem(effectors._EFFECTORS, EXTERNAL_WRITE_SINK_WORKSPACE, workspace_adapter)
    node = NodeDefinition(
        node_id="workspace",
        display_name="workspace",
        output_keys=["workspace_packet"],
        effects=[EXTERNAL_WRITE_SINK_WORKSPACE],
        timeout_seconds=37.5,
    )
    chain = EffectChain(run_id="r-workspace", base_path=None)

    effectors._fire_node_effects(
        node,
        {"workspace_packet": {"sink": EXTERNAL_WRITE_SINK_WORKSPACE, "op": "create"}},
        chain=chain,
        schema_defaulted=set(),
    )

    assert seen == [37.5]


def test_effects_fire_in_graph_order_and_a_later_node_sees_the_full_body(monkeypatch):
    adapter = _Adapter({"fetch": _OK_GET, "write": _OK_PUT})
    chain = _run(
        _linear(_effect_node("fetch"), _effect_node("write", "PUT")),
        {"fetch": _packet("GET"), "write": _packet("PUT")},
        adapter, monkeypatch,
    )
    assert [c["node_id"] for c in adapter.calls] == ["fetch", "write"]
    # write's packet rendered against the chain: the FULL fetch result, headers and all
    assert adapter.calls[1]["prior"]["fetch"]["response"]["body"] == _OK_GET["response"]["body"]
    assert adapter.calls[0]["prior"] == {}
    assert set(chain.evidence) == {"fetch", "write"}
    assert chain.fired == [(SINK, "GET"), (SINK, "PUT")]
    assert chain.delivered_nodes() == ["fetch", "write"]


def test_a_refused_write_fails_the_node_and_later_nodes_never_fire(monkeypatch):
    refused = {"delivered": False, "error": "body transform refused: $ta.replace: old text "
               "not found in the input", "error_kind": "invalid_body_transform"}
    adapter = _Adapter({"fetch": _OK_GET, "write": refused, "open_pr": _OK_PUT})
    branch = _linear(_effect_node("fetch"), _effect_node("write", "PUT"),
                     _effect_node("open_pr", "POST"))
    with pytest.raises(Exception) as excinfo:
        _run(branch, {"fetch": _packet(), "write": _packet("PUT"), "open_pr": _packet("POST")},
             adapter, monkeypatch)
    from tinyassets.runs import _find_effect_failed_exception

    failure = _find_effect_failed_exception(excinfo.value)
    assert isinstance(failure, EffectFailedError)
    assert failure.node_id == "write" and failure.error_kind == "invalid_body_transform"
    assert str(failure).startswith("external write failed - write/authenticated_external_call: ")
    assert [c["node_id"] for c in adapter.calls] == ["fetch", "write"]   # open_pr never fired


def test_a_far_side_4xx_fails_the_node_unless_the_packet_declared_it(monkeypatch):
    miss = {"delivered": True, "verb": "GET",
            "response": {"status": 404, "body": '{"message": "Not Found"}'}}
    adapter = _Adapter({"probe": miss})
    with pytest.raises(Exception) as excinfo:
        _run(_linear(_effect_node("probe")), {"probe": _packet()}, adapter, monkeypatch)
    from tinyassets.runs import _find_effect_failed_exception

    failure = _find_effect_failed_exception(excinfo.value)
    assert failure is not None and failure.error_kind == "far_side_error"
    assert "HTTP 404" in failure.error
    # declared: the 404 is data
    chain = _run(_linear(_effect_node("probe")), {"probe": _packet(accept_statuses=[404])},
                 _Adapter({"probe": miss}), monkeypatch, run_id="r2")
    assert chain.evidence["probe"][SINK]["response"]["status"] == 404


def test_a_node_fires_its_effects_at_most_once_per_run(monkeypatch):
    adapter = _Adapter({"fetch": _OK_GET})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    chain = EffectChain(run_id="r3")
    node = _effect_node("fetch")
    state = {"fetch_packet": _packet()}
    dispatch_node_effects(chain, node, state)
    with pytest.raises(EffectFailedError, match="already fired"):
        dispatch_node_effects(chain, node, state)
    assert len(adapter.calls) == 1


def test_references_follow_graph_ancestry_not_completion_order():
    a, b, c, d = (_effect_node(n) for n in ("a", "b", "c", "d"))
    branch = BranchDefinition(name="fan", entry_point="a")
    branch.node_defs = [a, b, c, d]
    branch.graph_nodes = [GraphNodeRef(id=n, node_def_id=n) for n in "abcd"]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="a"),
        EdgeDefinition(from_node="a", to_node="b"),
        EdgeDefinition(from_node="a", to_node="c"),
        EdgeDefinition(from_node="b", to_node="d"),
        EdgeDefinition(from_node="c", to_node="d"),
        EdgeDefinition(from_node="d", to_node="END"),
    ]
    anc = _graph_ancestors(branch)
    assert anc["b"] == {"a"} and anc["c"] == {"a"} and anc["d"] == {"a", "b", "c"}
    assert "c" not in anc["b"]          # siblings are never referencable
    chain = EffectChain(run_id="r4")
    chain.results = {"a": _OK_GET, "c": _OK_GET}
    assert set(chain.prior_effects(anc["b"])) == {"a"}
    assert set(chain.prior_effects(anc["d"])) == {"a", "c"}


def test_code_nodes_see_status_and_body_but_never_headers():
    chain = EffectChain(run_id="r5")
    chain.results = {"fetch": _OK_GET}
    view = chain.effects_view({"fetch"})
    assert view["fetch"]["status"] == 200
    assert view["fetch"]["body"] == {"content": "aGVsbG8=", "sha": "s1"}   # parsed
    assert "headers" not in view["fetch"]
    assert chain.effects_view(set()) == {}


def test_delta_view_applies_the_reducers():
    view = _delta_view(
        {"log": ["a"], "meta": {"x": 1}, "n": 1},
        {"log": ["b"], "meta": {"y": 2}, "n": 2},
        append_fields={"log"}, merge_fields={"meta"},
    )
    assert view == {"log": ["a", "b"], "meta": {"x": 1, "y": 2}, "n": 2}


_EDIT_SOURCE = (
    "import base64\n"
    "def run(state, effects):\n"
    "    got = effects['fetch']['body']\n"
    "    text = base64.b64decode(got['content']).decode()\n"
    "    text = text.replace('hello', 'hello world', 1)\n"
    "    return {'content': base64.b64encode(text.encode()).decode(), 'sha': got['sha']}\n"
)


def _fetch_edit_write_branch(edit_source=_EDIT_SOURCE):
    fetch = _effect_node("fetch")
    edit = NodeDefinition(
        node_id="edit", display_name="edit", source_code=edit_source,
        input_keys=[], output_keys=["content", "sha"],
    )
    write = _effect_node("write", "PUT", input_keys=("content", "sha"))
    b = _linear(fetch, edit, write)
    b.state_schema += [{"name": "content", "type": "str"}, {"name": "sha", "type": "str"}]
    return b


def test_fetch_code_write_in_one_run_without_any_operator(monkeypatch, center):
    """The shape the whole change exists for (live 2026-08-30: four deploys of
    `$ta.*` operators to change one line). A code node reads the fetch's full
    parsed body, edits it deterministically, and the write node's packet reads
    the result from state - the model never carries the bytes."""
    adapter = _Adapter({"fetch": _OK_GET, "write": _OK_PUT})
    chain = _run(
        _fetch_edit_write_branch(),
        {"fetch": _packet(), "write": _packet("PUT")}, adapter, monkeypatch, run_id="r7",
        base_path=center,
    )
    assert [c["node_id"] for c in adapter.calls] == ["fetch", "write"]
    # aGVsbG8= is "hello"; the code node produced "hello world", and the write
    # node's packet renders against state that carries it (its input_keys)
    import base64

    write_call = adapter.calls[1]
    assert base64.b64decode(write_call["state"]["content"]) == b"hello world"
    assert write_call["state"]["sha"] == "s1"
    assert chain.evidence["write"][SINK]["delivered"] is True
    assert set(chain.evidence) == {"fetch", "write"}       # the code node has no effect row


def test_a_code_node_that_raises_fails_the_run_as_code_node_failed(monkeypatch, tmp_path):
    from tinyassets.runs import _classify_failure, execute_branch, get_run

    adapter = _Adapter({"fetch": _OK_GET, "write": _OK_PUT})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    bad = _EDIT_SOURCE.replace("got['sha']", "got['missing']")
    branch = _fetch_edit_write_branch(bad)
    packets = {"fetch": _packet(), "write": _packet("PUT")}
    outcome = execute_branch(
        tmp_path, branch=branch, inputs={}, provider_call=_provider_for(packets),
        actor=ACTOR,
    )
    assert outcome.status == "failed"
    assert "code node 'edit' failed" in outcome.error and "KeyError" in outcome.error
    assert _classify_failure(get_run(tmp_path, outcome.run_id)) == "code_node_failed"
    assert [c["node_id"] for c in adapter.calls] == ["fetch"]      # write never fired


# --- Codex round 2 (2026-08-30): the six P0s, each pinned ---------------------


def test_resume_carries_the_runs_authority_so_foreign_code_cannot_fail_open(tmp_path):
    """R2 P0: the resume path compiled without an execution context, so a
    public-foreign branch's code ran after an interrupt. The context is built
    from the persisted run row for both paths."""
    import inspect

    from tinyassets import runs

    runs.initialize_runs_db(tmp_path)
    branch = _fetch_edit_write_branch()
    branch.author = "alice"
    own_run = runs.create_run(
        tmp_path, branch_def_id="branch", thread_id="own", inputs={}, actor="alice",
    )
    foreign_run = runs.create_run(
        tmp_path, branch_def_id="branch", thread_id="foreign", inputs={}, actor="mallory",
    )
    ctx = runs._execution_context_for_run(tmp_path, own_run, branch, fallback_actor="mallory")
    assert ctx.caller_provenance == "own" and ctx.actor == "alice"
    ctx = runs._execution_context_for_run(tmp_path, foreign_run, branch, fallback_actor="alice")
    assert ctx.caller_provenance == "public-foreign" and ctx.actor == "mallory"
    assert ctx.workspace_family is None  # ordinary, unenrolled run remains supported
    from tinyassets.workspace_family import FamilyRefused

    # A caller hint cannot substitute for a missing persisted execution row.
    with pytest.raises(FamilyRefused, match="unknown family member"):
        runs._execution_context_for_run(tmp_path, "nope", branch, fallback_actor="alice")
    # the resume site passes it to compile_branch (structural pin)
    src = inspect.getsource(runs._invoke_graph_resume)
    assert "execution_context=resume_context" in src
    assert "_execution_context_for_run(" in src
    assert "invocation_depth=effect_chain.invocation_depth" in src   # R3: depth survives resume


def test_rpc_runs_the_invoker_inside_the_requests_context(monkeypatch, center):
    """R2 P0: the sandbox answers RPCs from a drain thread, which carries no
    ContextVars, so an invoker resolved the daemon's env identity instead of
    the run's actor. The node thread's context is copied into the call."""
    import contextvars

    from tinyassets import graph_compiler as gc

    who = contextvars.ContextVar("who", default="daemon-env")

    def fake_invoker(node, **_kw):
        return lambda action, **kwargs: {"actor": who.get(), "action": action}

    monkeypatch.setattr(gc, "_build_node_mcp_invoker", fake_invoker)
    who.set("alice")
    node = NodeDefinition(
        node_id="who", display_name="who", input_keys=[], output_keys=["r"],
        source_code="def run(state):\n    return {'r': invoke_mcp_action('probe', x=1)}\n",
    )
    branch = _linear(node)
    branch.state_schema = [{"name": "r", "type": "dict"}]
    chain = EffectChain(run_id="r8")
    compiled = compile_branch(branch, provider_call=None, effect_chain=chain,
                              base_path=center)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    out = app.invoke({}, config={"configurable": {"thread_id": "r8"}})
    assert out["r"] == {"actor": "alice", "action": "probe"}
    assert chain.rpc_calls == 1


def test_rpc_calls_are_counted_and_never_capped():
    """``RUN_RPC_CALLS_MAX = 5000`` is gone; the count is evidence, not a limit.

    It failed a run partway through with "too many invoke_mcp_action calls in
    this run". An account has exactly two limits -- cloud bytes and concurrent
    agent seats (founder, 2026-09-30) -- and a run is bounded by holding its seat
    for as long as it takes, not by a count of what it did with it.
    """
    import inspect

    assert not hasattr(effectors, "RUN_RPC_CALLS_MAX")
    assert "cap" not in inspect.signature(EffectChain.rpc_permit).parameters

    chain = EffectChain(run_id="r9")
    for _ in range(6000):  # past the old 5000
        chain.rpc_permit()
    assert chain.rpc_calls == 6000, "the count is kept, it just refuses nothing"


def test_a_bad_reducer_delta_fails_before_any_effect_fires(monkeypatch, center):
    """R2 P0: `_delta_view` overwrote an append field with a non-list, the PUT
    fired, then LangGraph raised on the reducer. Validate first, fire never."""
    adapter = _Adapter({"write": _OK_PUT})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    node = NodeDefinition(
        node_id="write", display_name="write", input_keys=[], output_keys=["log", "write_packet"],
        source_code=(
            "def run(state):\n"
            "    return {'log': 'not-a-list', 'write_packet': %r}\n" % _packet("PUT")
        ),
        effects=[SINK],
    )
    branch = _linear(node)
    branch.state_schema = [
        {"name": "log", "type": "list", "reducer": "append"},
        {"name": "write_packet", "type": "str"},
    ]
    chain = EffectChain(run_id="r10")
    compiled = compile_branch(branch, provider_call=None, effect_chain=chain,
                              base_path=center)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(Exception, match="reducer is append"):
        app.invoke({"log": []}, config={"configurable": {"thread_id": "r10"}})
    assert adapter.calls == []


def test_the_merge_writer_guard_runs_before_effects(monkeypatch, center):
    """R2 P0: the guard wrapped the effect wrapper, so a PUT fired before the
    guard refused an undeclared merge writer. Now guard -> effects."""
    adapter = _Adapter({"write": _OK_PUT})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    declared = NodeDefinition(
        node_id="declared", display_name="declared", input_keys=[], output_keys=["meta"],
        source_code="def run(state):\n    return {'meta': {'a': 1}}\n",
    )
    sneaky = NodeDefinition(
        node_id="write", display_name="write", input_keys=[], output_keys=["write_packet"],
        source_code=(
            "def run(state):\n"
            "    return {'meta': {'b': 2}, 'write_packet': %r}\n" % _packet("PUT")
        ),
        effects=[SINK],
    )
    branch = _linear(declared, sneaky)
    branch.state_schema = [
        {"name": "meta", "type": "dict", "reducer": "merge"},
        {"name": "write_packet", "type": "str"},
    ]
    chain = EffectChain(run_id="r11")
    compiled = compile_branch(branch, provider_call=None, effect_chain=chain,
                              base_path=center)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(Exception, match="without declaring it"):
        app.invoke({}, config={"configurable": {"thread_id": "r11"}})
    assert adapter.calls == []


def test_at_most_once_holds_under_concurrent_visits(monkeypatch):
    """R2 P0: the evidence check and the reservation were separated by the
    adapter call, so two synchronized visits both fired."""
    import threading

    started = threading.Barrier(2, timeout=5)
    release = threading.Event()

    class _Slow(_Adapter):
        def __call__(self, **kw):
            release.wait(5)
            return super().__call__(**kw)

    adapter = _Slow({"fetch": _OK_GET})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    chain = EffectChain(run_id="r12")
    node = _effect_node("fetch")
    state = {"fetch_packet": _packet()}
    errors: list[str] = []

    def visit():
        started.wait()
        try:
            dispatch_node_effects(chain, node, state)
        except EffectFailedError as exc:
            errors.append(exc.error_kind)

    threads = [threading.Thread(target=visit) for _ in range(2)]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(10)
    assert len(adapter.calls) == 1
    assert errors == ["effect_already_fired"]


def test_settlement_waits_for_an_in_flight_dispatch_and_closes_the_chain(monkeypatch):
    """R2 P0: a terminal status settled `[]` while an adapter was still
    running; the late PUT then landed on a forgotten chain as a read."""
    import threading

    inside = threading.Event()
    release = threading.Event()

    class _Blocking(_Adapter):
        def __call__(self, **kw):
            inside.set()
            release.wait(5)
            return super().__call__(**kw)

    adapter = _Blocking({"write": _OK_PUT})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    settled: list[list] = []
    monkeypatch.setattr(
        effectors, "settle_engine_admission",
        lambda rid, fired: settled.append(list(fired)),
    )
    chain = EffectChain(run_id="r13")
    node = _effect_node("write", "PUT")
    worker = threading.Thread(
        target=dispatch_node_effects, args=(chain, node, {"write_packet": _packet("PUT")}),
    )
    worker.start()
    assert inside.wait(5)
    settler = threading.Thread(target=chain.settle)
    settler.start()
    settler.join(0.3)
    assert settler.is_alive()                      # settle is waiting for the dispatch
    assert settled == []
    release.set()
    worker.join(5)
    settler.join(5)
    assert settled == [[(SINK, "PUT")]]           # the late write was counted
    assert chain.closed is True
    with pytest.raises(EffectFailedError, match="terminal"):
        dispatch_node_effects(chain, _effect_node("late"), {"late_packet": _packet()})


def test_an_accepted_status_is_not_re_reported_at_completion(monkeypatch):
    from tinyassets.runs import _collect_external_write_errors

    miss = {"delivered": True, "verb": "GET",
            "response": {"status": 404, "body": '{"message": "Not Found"}'}}
    adapter = _Adapter({"probe": miss})
    chain = _run(_linear(_effect_node("probe")), {"probe": _packet(accept_statuses=[404])},
                 adapter, monkeypatch, run_id="r14")
    assert chain.evidence["probe"][SINK]["accepted_status"] is True
    assert _collect_external_write_errors(dict(chain.evidence)) == []


def test_two_graph_nodes_sharing_a_definition_have_their_own_ancestry():
    """R2 P1: ancestry and effect identity were keyed by node_def id, so two
    graph nodes over one definition received the union of both."""
    shared = _effect_node("shared")
    fetch = _effect_node("fetch")
    branch = BranchDefinition(name="reuse", entry_point="first")
    branch.node_defs = [shared, fetch]
    branch.graph_nodes = [
        GraphNodeRef(id="first", node_def_id="shared"),
        GraphNodeRef(id="fetch", node_def_id="fetch"),
        GraphNodeRef(id="second", node_def_id="shared"),
    ]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="first"),
        EdgeDefinition(from_node="first", to_node="fetch"),
        EdgeDefinition(from_node="fetch", to_node="second"),
        EdgeDefinition(from_node="second", to_node="END"),
    ]
    anc = _graph_ancestors(branch)
    assert anc["first"] == set()
    assert anc["second"] == {"first", "fetch"}


def test_evidence_merges_into_a_callers_terminal_output(monkeypatch, tmp_path):
    """R2 P1: INTERRUPTED with a caller-supplied output dropped the chain's
    evidence; now it is merged in, never clobbering the caller's keys."""
    from tinyassets import runs

    monkeypatch.setattr(effectors, "settle_engine_admission", lambda rid, fired: None)
    runs.initialize_runs_db(tmp_path)
    created = runs.create_run(
        tmp_path, branch_def_id="b", thread_id="t15", inputs={}, run_name="x",
        actor=ACTOR,
    )
    run_id = created if isinstance(created, str) else getattr(created, "run_id", "")
    chain = EffectChain(run_id=run_id)
    chain.fired.append((SINK, "PUT"))
    chain.evidence["write"] = {SINK: {"delivered": True, "verb": "PUT",
                                      "response": {"status": 201, "body": "{}"}}}
    admission = {"attempts": 4, "lock_conflicts": 3, "retry_sleep_seconds": 0.6}
    chain.evidence["workspace"] = {"workspace": {
        "error_kind": "workspace_busy", "workspace_admission": admission,
    }}
    register_effect_chain(chain)
    runs.update_run_status(
        tmp_path, run_id, status=runs.RUN_STATUS_INTERRUPTED, error="paused",
        output={"child_invocation_receipt_gate": {"status": "receipt_waiting"}},
    )
    rec = runs.get_run(tmp_path, run_id)
    assert rec["output"]["child_invocation_receipt_gate"]["status"] == "receipt_waiting"
    assert "write" in rec["output"]["external_write_results"]
    assert rec["output"]["external_write_results"]["workspace"]["workspace"][
        "workspace_admission"
    ] == admission
    assert rec["output"]["failed_after_effects"] == ["write"]


def test_a_terminal_status_settles_from_the_chain_and_forgets_it(monkeypatch, tmp_path):
    """A write that fired before the failure settles as a write - the old
    `settle(run_id, [])` read-shortcut must not run when a chain exists."""
    from tinyassets import runs

    settled = []
    monkeypatch.setattr(
        effectors, "settle_engine_admission",
        lambda rid, fired: settled.append((rid, list(fired))),
    )
    chain = EffectChain(run_id="r6")
    chain.fired.append((SINK, "POST"))
    chain.evidence["create_branch"] = {SINK: {"delivered": True, "verb": "POST",
                                             "response": {"status": 201, "body": "{}"}}}
    register_effect_chain(chain)
    runs.initialize_runs_db(tmp_path)
    runs.update_run_status(tmp_path, "r6", status=runs.RUN_STATUS_FAILED, error="x")
    assert settled == [("r6", [(SINK, "POST")])]
    assert forget_effect_chain("r6") is None            # forgotten at terminal status
    assert chain.delivered_nodes() == ["create_branch"]


# --- Codex round 3 (2026-08-30): the last review round; each finding pinned ----


def test_the_requests_context_crosses_the_executor_hop(monkeypatch, tmp_path):
    """R3 P0: the run's worker was submitted without the request's context, so
    the node thread copied an EMPTY context and the RPC saw the daemon's env
    identity. `executor.submit(copy_context().run, worker)` carries it."""
    import contextvars
    import time

    from tinyassets import graph_compiler as gc
    from tinyassets import runs

    who = contextvars.ContextVar("who_r3", default="daemon-env")

    def fake_invoker(node, **_kw):
        return lambda action, **kwargs: {"actor": who.get()}

    monkeypatch.setattr(gc, "_build_node_mcp_invoker", fake_invoker)
    who.set("alice")
    node = NodeDefinition(
        node_id="who", display_name="who", input_keys=[], output_keys=["r"],
        source_code="def run(state):\n    return {'r': invoke_mcp_action('probe')}\n",
    )
    branch = _linear(node)
    branch.state_schema = [{"name": "r", "type": "dict"}]
    queued = runs.execute_branch_async(
        tmp_path,
        branch=branch,
        inputs={},
        provider_call=None,
        actor=ACTOR,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        rec = runs.get_run(tmp_path, queued.run_id)
        if rec["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert rec["status"] == "completed", rec.get("error")
    assert rec["output"]["r"] == {"actor": "alice"}


def test_parallel_siblings_that_both_overwrite_one_field_are_refused_at_compile():
    """R3 P0: two fan-out nodes both wrote an overwrite field; both effects
    fired, then LangGraph rejected the step at the barrier."""
    from tinyassets.graph_compiler import CompilerError

    a = NodeDefinition(node_id="a", display_name="a", prompt_template="x",
                       output_keys=["shared"])
    b = NodeDefinition(node_id="b", display_name="b", prompt_template="y",
                       output_keys=["shared"])
    root = NodeDefinition(node_id="root", display_name="root", prompt_template="r",
                          output_keys=["seed"])
    branch = BranchDefinition(name="fan", entry_point="root")
    branch.node_defs = [root, a, b]
    branch.graph_nodes = [GraphNodeRef(id=n, node_def_id=n) for n in ("root", "a", "b")]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="root"),
        EdgeDefinition(from_node="root", to_node="a"),
        EdgeDefinition(from_node="root", to_node="b"),
        EdgeDefinition(from_node="a", to_node="END"),
        EdgeDefinition(from_node="b", to_node="END"),
    ]
    branch.state_schema = [{"name": "seed", "type": "str"}, {"name": "shared", "type": "str"}]
    mock = lambda *a, **k: "[mock]"  # noqa: E731
    with pytest.raises(CompilerError, match="both write 'shared'"):
        compile_branch(branch, provider_call=mock, effect_chain=EffectChain(run_id="x"))
    branch.state_schema[1]["reducer"] = "append"
    compile_branch(branch, provider_call=mock, effect_chain=EffectChain(run_id="x"))


def test_resume_seeds_at_most_once_and_the_rpc_cap_from_the_interrupted_segment(monkeypatch):
    """R3 P0: a resumed run got an empty chain, so an effect fired before the
    interrupt could fire again and a whole new RPC budget was allowed."""
    adapter = _Adapter({"fetch": _OK_GET})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    chain = EffectChain(run_id="r16")
    chain.seed_from_output({
        "external_write_results": {"fetch": {SINK: {"delivered": True}}},
        "effects_fired_before": ["older"],
        "rpc_calls": 4999,
        "invocation_depth": 2,
    })
    assert chain.already_fired == {"fetch", "older"} and chain.invocation_depth == 2
    with pytest.raises(EffectFailedError, match="already fired"):
        dispatch_node_effects(chain, _effect_node("fetch"), {"fetch_packet": _packet()})
    assert adapter.calls == []
    # A resumed count carries forward and keeps counting; nothing refuses it.
    chain.rpc_permit()
    chain.rpc_permit()
    assert chain.rpc_calls == 5001


def test_a_same_thread_settle_inside_a_dispatch_defers_to_its_end(monkeypatch):
    """R3 P0: with an RLock, an adapter that reached settle() on its own
    thread settled an empty list; the PUT then landed on a settled chain."""
    settled: list[list] = []
    monkeypatch.setattr(
        effectors, "settle_engine_admission",
        lambda rid, fired: settled.append(list(fired)),
    )
    chain = EffectChain(run_id="r17")

    class _SettlesItself(_Adapter):
        def __call__(self, **kw):
            chain.settle()                  # same thread, mid-dispatch
            assert settled == []            # deferred, not settled empty
            return super().__call__(**kw)

    monkeypatch.setitem(effectors._EFFECTORS, SINK, _SettlesItself({"write": _OK_PUT}))
    dispatch_node_effects(chain, _effect_node("write", "PUT"), {"write_packet": _packet("PUT")})
    assert settled == [[(SINK, "PUT")]] and chain.settled and chain.closed


def test_a_dispatch_that_outlives_the_settle_wait_resettles_as_a_write(monkeypatch):
    """R3 P0/P1: settle waits a bounded time for adapters in flight and does
    not serialise unrelated effects; a dispatch that finishes after the wait
    re-settles, and a write settlement is final."""
    import threading

    settled: list[list] = []
    monkeypatch.setattr(
        effectors, "settle_engine_admission",
        lambda rid, fired: settled.append(list(fired)),
    )
    monkeypatch.setattr(EffectChain, "SETTLE_WAIT_S", 0.2)
    release = threading.Event()

    class _Slow(_Adapter):
        def __call__(self, **kw):
            release.wait(5)
            return super().__call__(**kw)

    monkeypatch.setitem(effectors._EFFECTORS, SINK, _Slow({"write": _OK_PUT}))
    chain = EffectChain(run_id="r18")
    worker = threading.Thread(
        target=dispatch_node_effects,
        args=(chain, _effect_node("write", "PUT"), {"write_packet": _packet("PUT")}),
    )
    worker.start()
    chain.settle()                          # gives up after 0.2 s
    assert settled == [[]] and chain.closed
    release.set()
    worker.join(5)
    assert settled == [[], [(SINK, "PUT")]]  # the late write re-settled


def test_two_unrelated_effects_run_in_parallel(monkeypatch):
    """R3 P1: a run-wide dispatch lock serialised independent effect nodes."""
    import threading
    import time

    class _Sleepy(_Adapter):
        def __call__(self, **kw):
            time.sleep(0.25)
            return super().__call__(**kw)

    monkeypatch.setitem(effectors._EFFECTORS, SINK, _Sleepy({"a": _OK_GET, "b": _OK_GET}))
    chain = EffectChain(run_id="r19")
    t0 = time.monotonic()
    threads = [
        threading.Thread(
            target=dispatch_node_effects,
            args=(chain, _effect_node(n), {f"{n}_packet": _packet()}),
        )
        for n in ("a", "b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert time.monotonic() - t0 < 0.45      # parallel, not 0.5 s serialised
    assert set(chain.evidence) == {"a", "b"}


def test_a_write_settlement_promotes_an_admission_a_read_reclassified(tmp_path):
    """R3 P0: reclassify_read then settle_write left the admission row a
    read. A write settlement is final in both directions."""
    import sqlite3

    from tinyassets import engine_admissions as ea

    db = tmp_path / "ledger.db"
    ticket = ea.admit("u1", db=db)
    assert ticket > 0
    ea.attach_run(ticket, "run-w", db=db)

    def kind():
        row = sqlite3.connect(db).execute(
            "select kind from admissions where run_id='run-w'"
        ).fetchone()
        return row[0]

    ea.reclassify_read("run-w", db=db)
    assert kind() == "read"
    ea.settle_write("run-w", db=db)
    assert kind() == "write"
    ea.reclassify_read("run-w", db=db)      # a later read changes nothing
    assert kind() == "write"


def test_the_legacy_dispatcher_fires_once_per_graph_node(monkeypatch):
    """R3 P1: the chain-less dispatcher iterated node_defs, so two graph nodes
    over one definition were one effect."""
    from tinyassets.effectors import run_effects_for_branch

    adapter = _Adapter({"shared_def": _OK_GET})
    monkeypatch.setitem(effectors._EFFECTORS, SINK, adapter)
    shared = _effect_node("shared_def")
    branch = BranchDefinition(name="legacy", entry_point="g1")
    branch.node_defs = [shared]
    branch.graph_nodes = [GraphNodeRef(id="g1", node_def_id="shared_def"),
                          GraphNodeRef(id="g2", node_def_id="shared_def")]
    branch.edges = [EdgeDefinition(from_node="START", to_node="g1"),
                    EdgeDefinition(from_node="g1", to_node="g2"),
                    EdgeDefinition(from_node="g2", to_node="END")]
    evidence = run_effects_for_branch(
        branch=branch, run_state={"shared_def_packet": _packet()}, base_path=None, run_id="",
    )
    assert set(evidence) == {"g1", "g2"} and len(adapter.calls) == 2


def test_source_code_compile_refusals_are_the_universes_to_fix():
    """R3 P1: a 50 KB / disallowed-pattern refusal was classified as an
    approval the host must grant, which no longer exists."""
    from tinyassets.api.runs import _classify_run_outcome_error

    cls, action = _classify_run_outcome_error("Node 'x' source_code exceeds 50KB")
    assert cls == "code_node_failed" and "op=update_node" in action
    assert "patch_node" not in action
    cls, _ = _classify_run_outcome_error(
        "Node 'x' source_code contains disallowed pattern: 'open('"
    )
    assert cls == "code_node_failed"
    cls, _ = _classify_run_outcome_error("Approval required for source_code node")
    assert cls == "node_not_approved"       # legacy shape, still classified


def test_a_universes_engine_authored_branch_is_its_own(monkeypatch, tmp_path):
    """Live 2026-08-30 09:27Z, run fda6cac079c44ed9: the founder's universe
    built a code node and its own run refused it as public-foreign - the
    branch's author is the founder's user id, the run executes as
    `universe:<id>`. Own = authored by the actor OR by an admin of the run's
    universe (the canonical ownership signal); a stranger stays foreign."""
    from tinyassets import runs
    from tinyassets.api import source_channel

    admins = {("u-1", "user_founder")}
    monkeypatch.setattr(
        source_channel, "universe_owner_actor",
        lambda base, universe_id, actor: (universe_id, actor) in admins,
    )
    prov = runs._caller_provenance
    assert prov(tmp_path, "universe:u-1", "u-1", "user_founder") == "own"
    assert prov(tmp_path, "universe:u-1", "u-1", "user_stranger") == "public-foreign"
    assert prov(tmp_path, "user_founder", "", "user_founder") == "own"
    assert prov(tmp_path, "universe:u-2", "u-2", "user_founder") == "public-foreign"
    assert prov(tmp_path, "universe:u-1", "u-1", "") == "public-foreign"
    # and the execution context built for a run uses it
    branch = _fetch_edit_write_branch()
    branch.author = "user_founder"
    runs.initialize_runs_db(tmp_path)
    run_id = runs.create_run(
        tmp_path, branch_def_id="branch", thread_id="owned", inputs={},
        actor="universe:u-1", queue_universe_id="u-1",
    )
    ctx = runs._execution_context_for_run(tmp_path, run_id, branch)
    assert ctx.caller_provenance == "own"
    no_universe_run = runs.create_run(
        tmp_path, branch_def_id="branch", thread_id="unscoped", inputs={},
        actor="universe:u-1",
    )
    ctx = runs._execution_context_for_run(tmp_path, no_universe_run, branch)
    assert ctx.caller_provenance == "public-foreign"  # actor text does not confer a universe


# ---------------------------------------------------------------------------
# Atomic capability acquisition (Codex code round 3, P0 #2)
#
# The bug is not "a revoked mount is still readable" - it is worse and quieter.
# A discard closes the descriptors; the NEXT checkout opens directories and the
# runtime hands back the same fd numbers; a node still holding them is now
# reading another branch's repository. A dup is what makes the number
# unreusable while it is held.
# ---------------------------------------------------------------------------


def _open_fd(tmp_path, name: str) -> int:
    """A real descriptor. Not a directory one - Windows cannot open those - but
    the refcount rules do not care what is on the other end."""
    path = tmp_path / name
    path.write_text(name, encoding="utf-8")
    return _os.open(path, _os.O_RDONLY)


def _is_open(fd: int) -> bool:
    try:
        _os.fstat(fd)
        return True
    except OSError:
        return False


def _registered(chain, tmp_path, key: str = "checkout"):
    repo_fd = _open_fd(tmp_path, f"{key}-repo")
    lease_fd = _open_fd(tmp_path, f"{key}-lease")
    mount = _Mount(
        node_id=key,
        bind_source=str(tmp_path / "repo"),
        lease_fd=lease_fd,
        repo_fd=repo_fd,
    )
    chain.register_workspace(key, mount)
    return mount


def test_an_acquisition_hands_back_duplicates_not_the_originals(tmp_path) -> None:
    chain = _Chain()
    mount = _registered(chain, tmp_path)
    with chain.acquire_workspace("checkout") as held:
        assert held.mount is not mount
        assert held.mount.repo_fd != mount.repo_fd
        assert held.mount.lease_fd != mount.lease_fd
        # Same open file, different number: that is what makes the number
        # unreusable while this holder lives.
        assert _os.fstat(held.mount.repo_fd).st_ino == _os.fstat(mount.repo_fd).st_ino
        duplicate = held.mount.repo_fd
    assert not _is_open(duplicate), "the dup outlived its release"
    assert _is_open(mount.repo_fd), "releasing a holder closed the original"
    chain.close_workspaces()


def test_a_discard_during_a_held_use_does_not_close_the_dup(tmp_path) -> None:
    """The node keeps reading. Closing under it is how the fd number gets
    reused by the next checkout while it is still being read."""
    chain = _Chain()
    mount = _registered(chain, tmp_path)
    held = chain.acquire_workspace("checkout")
    assert held is not None

    chain.revoke_workspace("checkout")

    assert _is_open(held.mount.repo_fd), "the discard closed the holder's dup"
    assert _is_open(mount.repo_fd), "the original closed with a holder outstanding"
    # And the capability is gone for anyone asking now.
    assert chain.workspace_mount("checkout") is None
    assert chain.acquire_workspace("checkout") is None

    held.release()
    assert not _is_open(held.mount.repo_fd)
    assert not _is_open(mount.repo_fd), "the deferred close never happened"
    assert not _is_open(mount.lease_fd)


def test_a_double_release_does_not_decrement_twice(tmp_path) -> None:
    """Releasing twice must not close the capability under the OTHER holder.

    The refcount is what a second decrement corrupts - and the damage lands on
    a descriptor that is still in use, which is the same fd-reuse hazard by a
    different route. (A double close alone would be masked: the mount's own
    ``close`` nulls its fields first, so this asserts the count, not the fd.)
    """
    chain = _Chain()
    mount = _registered(chain, tmp_path)
    first = chain.acquire_workspace("checkout")
    second = chain.acquire_workspace("checkout")
    chain.revoke_workspace("checkout")

    first.release()
    first.release()                          # the mistake this guards

    assert _is_open(mount.repo_fd), "the second release closed it under a holder"
    assert chain.workspace_holds.get("checkout") == 1

    second.release()
    assert not _is_open(mount.repo_fd)
    assert chain.workspace_holds.get("checkout") is None


def test_the_originals_close_exactly_once(tmp_path) -> None:
    """A double close lands on whatever number the runtime handed out next."""
    chain = _Chain()
    _registered(chain, tmp_path)
    held = chain.acquire_workspace("checkout")
    chain.revoke_workspace("checkout")
    chain.revoke_workspace("checkout")      # idempotent
    held.release()
    chain.close_workspaces()

    # A fresh descriptor would land on the freed number if anything closed it
    # a second time; opening one and checking it survives is the assertion.
    canary = _open_fd(tmp_path, "canary")
    try:
        chain.close_workspaces()
        held.release()
        assert _is_open(canary), "something closed a descriptor it did not own"
    finally:
        _os.close(canary)


def test_a_reused_fd_number_is_not_reachable_through_a_stale_acquisition(
    tmp_path,
) -> None:
    """The actual attack: discard, let the next checkout take the same number,
    and try to reach it through the capability the first branch held."""
    chain = _Chain()
    first = _registered(chain, tmp_path, "checkout")
    first_repo_fd = first.repo_fd

    held = chain.acquire_workspace("checkout")
    assert held is not None
    held.release()
    chain.revoke_workspace("checkout")
    assert not _is_open(first_repo_fd)

    # The next checkout, which the runtime is free to hand the same number.
    second = _registered(chain, tmp_path, "checkout")
    assert _is_open(second.repo_fd)

    # The stale holder cannot acquire against the key it used to own without
    # going through the chain, and the chain hands out the CURRENT mount - so
    # there is no path from the old acquisition to the new repository.
    assert held._released is True
    fresh = chain.acquire_workspace("checkout")
    assert fresh is not None
    assert _os.fstat(fresh.mount.repo_fd).st_ino == _os.fstat(second.repo_fd).st_ino
    fresh.release()
    chain.close_workspaces()


def test_acquiring_a_key_that_was_never_registered_is_none(tmp_path) -> None:
    chain = _Chain()
    assert chain.acquire_workspace("nobody") is None
    assert chain.acquire_workspace("") is None


def test_two_threads_acquire_and_release_without_a_double_close(tmp_path) -> None:
    """Contention is the whole point of doing this under the chain's lock."""
    import threading

    chain = _Chain()
    mount = _registered(chain, tmp_path)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def churn() -> None:
        try:
            barrier.wait(timeout=30)
            for _ in range(40):
                held = chain.acquire_workspace("checkout")
                if held is None:
                    return
                assert _is_open(held.mount.repo_fd)
                held.release()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    def revoke() -> None:
        try:
            barrier.wait(timeout=30)
            chain.revoke_workspace("checkout")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(3)]
    threads.append(threading.Thread(target=revoke))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], errors
    assert chain.acquire_workspace("checkout") is None
    assert not _is_open(mount.repo_fd), "the originals never closed"


def test_settling_with_an_outstanding_acquisition_defers_the_close(
    tmp_path,
) -> None:
    chain = _Chain()
    mount = _registered(chain, tmp_path)
    held = chain.acquire_workspace("checkout")

    assert chain.close_workspaces() == 1
    assert _is_open(mount.repo_fd), "settle closed a capability still in use"
    assert chain.acquire_workspace("checkout") is None

    held.release()
    assert not _is_open(mount.repo_fd)
    assert not _is_open(mount.lease_fd)


def test_a_mount_without_descriptors_acquires_as_itself(tmp_path) -> None:
    """Windows, and every test double: the path form has nothing to duplicate
    and nothing whose number could be reused."""
    chain = _Chain()
    mount = _Mount(node_id="checkout", bind_source=str(tmp_path / "repo"))
    chain.register_workspace("checkout", mount)
    with chain.acquire_workspace("checkout") as held:
        assert held.mount is mount
        assert held.mount.bind_source == str(tmp_path / "repo")
    assert chain.workspace_mount("checkout") is mount
