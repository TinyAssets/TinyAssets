"""#61 — per-node timeout must surface as a clean failure, not a stall.

Before the fix, a slow/hung provider call just blocked the graph forever
with no node-level timeout. The runner eventually reported "frozen"
and user-sim couldn't distinguish "slow" from "dead."

The fix:
1. ``NodeDefinition.timeout_seconds`` default raised to 300s (matches
   providers.base.ProviderConfig.timeout).
2. Compiler wraps every provider_call / source_code call in a
   concurrent.futures timeout. Overrun raises ``NodeTimeoutError``.
3. Runner catches NodeTimeoutError (including LangGraph-wrapped),
   emits a NODE_STATUS_FAILED event with reason="timeout", and sets
   run status to ``failed`` with a specific message.
"""

from __future__ import annotations

import time

import pytest

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import (
    NodeTimeoutError,
    _run_with_timeout,
    compile_branch,
)

# ─── unit: _run_with_timeout ─────────────────────────────────────────────


def test_run_with_timeout_returns_fast_calls_unchanged():
    out = _run_with_timeout(
        lambda: "ok", timeout_s=5.0, node_id="fast",
    )
    assert out == "ok"


def test_run_with_timeout_raises_node_timeout_on_overrun():
    def _slow():
        time.sleep(0.5)
        return "late"

    with pytest.raises(NodeTimeoutError) as exc_info:
        _run_with_timeout(_slow, timeout_s=0.1, node_id="slow_node")
    assert "slow_node" in str(exc_info.value)
    assert "0s" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()
    # node_id is exposed as an attribute so attribution doesn't depend on
    # the human-readable message format staying stable.
    assert exc_info.value.node_id == "slow_node"


def test_node_timeout_error_default_node_id_is_empty():
    """Constructing NodeTimeoutError without the keyword keeps node_id
    blank so the runner's fallback path (parse the message) still wins."""
    exc = NodeTimeoutError("Node 'legacy' exceeded 1s timeout.")
    assert exc.node_id == ""


def test_runs_attribute_beats_regex_for_timeout_attribution():
    """Even if the message format drifts (no `Node 'X'` substring), the
    runner still gets the right node_id because it reads the attribute."""
    from tinyassets.runs import _node_id_from_timeout_exc

    exc = NodeTimeoutError("drifted message without the quoted node", node_id="n1")
    assert _node_id_from_timeout_exc(exc) == "n1"

    # Fallback path: no attribute, but message has the quoted form.
    legacy = NodeTimeoutError("Node 'legacy' exceeded 1s timeout.")
    assert _node_id_from_timeout_exc(legacy) == "legacy"

    # Worst case: neither attribute nor parsable message.
    unknown = NodeTimeoutError("something went wrong")
    assert _node_id_from_timeout_exc(unknown) == "(timeout)"


def test_run_with_timeout_propagates_internal_errors():
    """If the wrapped fn raises, the original exception propagates
    (not wrapped as NodeTimeoutError)."""
    def _boom():
        raise ValueError("internal problem")

    with pytest.raises(ValueError, match="internal problem"):
        _run_with_timeout(_boom, timeout_s=5.0, node_id="bad")


def test_a_timeout_raised_by_the_work_is_not_the_nodes_own_deadline():
    """Live 2026-10-09: a 5s lock wait inside an agent node read as "exceeded
    2592000s timeout". Since 3.11 the builtin TimeoutError IS
    concurrent.futures.TimeoutError, so the work's own one must pass through."""
    def _busy():
        raise TimeoutError("starter file boundary is busy; retry the turn")

    with pytest.raises(TimeoutError, match="boundary is busy") as exc_info:
        _run_with_timeout(_busy, timeout_s=60.0, node_id="manager")
    assert not isinstance(exc_info.value, NodeTimeoutError)


# ─── default timeout raised to 300s ──────────────────────────────────────


def test_node_definition_default_timeout_is_300s():
    """#61: default raised from 30s to 300s so local-LLM dense calls
    (90s+ per inference) don't trip on the scaffold."""
    node = NodeDefinition(node_id="x", display_name="X")
    assert node.timeout_seconds == 300.0


# ─── integration: compiler + slow provider ──────────────────────────────


def _slow_branch(timeout_s: float = 0.1) -> BranchDefinition:
    b = BranchDefinition(name="timeout-probe", entry_point="slow")
    b.node_defs = [NodeDefinition(
        node_id="slow", display_name="Slow",
        prompt_template="Wait: {x}",
        output_keys=["slow_out"],
        input_keys=["x"],
        timeout_seconds=timeout_s,
    )]
    b.graph_nodes = [GraphNodeRef(id="slow", node_def_id="slow")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="slow"),
        EdgeDefinition(from_node="slow", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "str", "default": ""},
        {"name": "slow_out", "type": "str", "default": ""},
    ]
    return b


def test_compiler_wraps_provider_call_with_node_timeout():
    """A provider that sleeps longer than the node's timeout raises
    NodeTimeoutError through the compiled graph."""
    from langgraph.checkpoint.memory import InMemorySaver

    def _slow_provider(prompt, system, *, role):
        time.sleep(0.5)
        return "late"

    branch = _slow_branch(timeout_s=0.1)
    compiled = compile_branch(branch, provider_call=_slow_provider)
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(NodeTimeoutError) as exc_info:
        runnable.invoke(
            {"x": "hi"},
            config={"configurable": {"thread_id": "t1"}},
        )
    assert "slow" in str(exc_info.value)


# ─── integration: runner catches timeout, emits event, sets run failed ───


def test_runner_emits_node_timeout_event_and_marks_run_failed(
    tmp_path, monkeypatch,
):
    """Full path: a timed-out run transitions to RUN_STATUS_FAILED with
    a timeout-specific error message and a NODE_STATUS_FAILED event
    whose detail marks reason='timeout'."""
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")

    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
    )
    from tinyassets.runs import (
        RUN_STATUS_FAILED,
        execute_branch_async,
        get_run,
        list_events,
        wait_for,
    )

    def _slow_provider(prompt, system, *, role):
        time.sleep(0.5)
        return "late"

    initialize_author_server(base)
    branch = _slow_branch(timeout_s=0.1)
    save_branch_definition(base, branch_def=branch.to_dict())
    outcome = execute_branch_async(
        base, branch=branch, inputs={"x": "hi"},
        actor="tester", provider_call=_slow_provider,
    )
    wait_for(outcome.run_id, timeout=5.0)

    record = get_run(base, outcome.run_id)
    assert record is not None
    assert record["status"] == RUN_STATUS_FAILED
    assert "timeout" in (record.get("error") or "").lower()
    assert "slow" in (record.get("error") or "")

    # Timeline has a failed event with reason=timeout.
    events = list_events(base, outcome.run_id, since_step=-1)
    timeout_events = [
        e for e in events
        if e.get("detail", {}).get("reason") == "timeout"
    ]
    assert timeout_events, (
        "Expected a run_events row with detail.reason='timeout' so the "
        "UI can distinguish timeout from a generic crash."
    )
    # The node_id in the timeout event should be the actual node, not
    # an opaque placeholder.
    assert timeout_events[0]["node_id"] == "slow"


# ─── integration: empty LLM response → node failed, run failed ───────────


def test_runner_emits_node_empty_response_event_and_marks_run_failed(
    tmp_path, monkeypatch,
):
    """BUG-004 Layer 2+3: a provider that returns empty string must produce
    a NODE_STATUS_FAILED event with reason='empty_response' and set
    run status to 'failed' — not silently mark the node 'ran' with output=''."""
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")

    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
    )
    from tinyassets.runs import (
        NODE_STATUS_FAILED,
        RUN_STATUS_FAILED,
        execute_branch_async,
        get_run,
        list_events,
        wait_for,
    )

    def _empty_provider(prompt, system, *, role):
        return ""  # simulate silent auth failure / codex 401

    initialize_author_server(base)
    branch = _slow_branch(timeout_s=5.0)
    save_branch_definition(base, branch_def=branch.to_dict())
    outcome = execute_branch_async(
        base, branch=branch, inputs={"x": "hi"},
        actor="tester", provider_call=_empty_provider,
    )
    wait_for(outcome.run_id, timeout=5.0)

    record = get_run(base, outcome.run_id)
    assert record is not None
    assert record["status"] == RUN_STATUS_FAILED
    assert "empty" in (record.get("error") or "").lower()

    events = list_events(base, outcome.run_id, since_step=-1)
    empty_events = [
        e for e in events
        if e.get("detail", {}).get("reason") == "empty_response"
    ]
    assert empty_events, (
        "Expected a run_events row with detail.reason='empty_response' "
        "so the UI can distinguish auth failure from a generic crash."
    )
    assert empty_events[0]["status"] == NODE_STATUS_FAILED
    assert empty_events[0]["node_id"] == "slow"

    # Downstream nodes must not receive empty string — output must be absent.
    ran_events = [e for e in events if e.get("status") == "ran"]
    assert not ran_events, (
        "No node should reach 'ran' status when provider returns empty"
    )


def test_fast_provider_does_not_hit_timeout(tmp_path, monkeypatch):
    """Sanity: a provider that returns immediately with a default
    timeout does not trip the guard."""
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")

    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
    )
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        execute_branch_async,
        get_run,
        wait_for,
    )

    def _fast(prompt, system, *, role):
        return "quick"

    initialize_author_server(base)
    branch = _slow_branch(timeout_s=5.0)
    save_branch_definition(base, branch_def=branch.to_dict())
    outcome = execute_branch_async(
        base, branch=branch, inputs={"x": "hi"},
        actor="tester", provider_call=_fast,
    )
    wait_for(outcome.run_id, timeout=5.0)
    record = get_run(base, outcome.run_id)
    assert record is not None
    assert record["status"] == RUN_STATUS_COMPLETED


# ─── the node's timeout must bound the PROVIDER, not only the future ─────
#
# Previously ``_build_prompt_template_node`` built ``ModelConfig`` with the
# legacy ``timeout`` scalar only. Native streaming does NOT read that scalar as
# a wall-clock deadline (by design — see ``ModelConfig.timeout`` and
# ``StreamTimeoutProfile``); it reads ``stream_timeout_profile()`` and bounds a
# still-progressing turn by ``absolute_cap_s``, which defaults to 600s.
#
# So the two clocks disagree: ``_run_with_timeout`` fails the node at
# ``timeout_seconds`` (300s by default) and returns to the graph, while the
# provider subprocess it launched keeps streaming for up to another 300s with
# nothing waiting on it. Setting ``absolute_cap_s`` at the per-node config site
# closes the gap. It is a BUDGET, not end-to-end cancellation: executor queueing
# and provider admission both elapse before the reader's clock starts, so the
# reader can still be cut off by the future — these tests assert the cap
# reaches the provider, never that the two deadlines coincide.


def _node_with_timeout(timeout_seconds: float | None):
    kwargs = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    return NodeDefinition(
        node_id="budgeted", display_name="Budgeted",
        prompt_template="Do {x}.", input_keys=["x"], output_keys=["out"],
        **kwargs,
    )


def _bridge_config(timeout_seconds: float | None):
    """The ModelConfig a compiled node hands to an INJECTED provider bridge."""
    from tinyassets.graph_compiler import _build_prompt_template_node

    captured: dict = {}

    def fake_provider_call(prompt, system, *, role="writer", config=None):
        captured["config"] = config
        return "done"

    fn = _build_prompt_template_node(
        _node_with_timeout(timeout_seconds),
        provider_call=fake_provider_call, event_sink=None,
    )
    # A single untyped output_key takes the raw response — no JSON contract.
    assert fn({"x": "thing"}).get("out") == "done"
    cfg = captured.get("config")
    assert cfg is not None, "node config was not threaded to the provider"
    return cfg


def _policy_config(timeout_seconds: float | None):
    """The ModelConfig a compiled node hands to the POLICY router route."""
    from tinyassets.graph_compiler import _build_prompt_template_node

    captured: dict = {}

    class _RecordingRouter:
        available_providers = ["claude"]

        def call_with_policy_sync(
            self, role, prompt, system, policy, config=None, **kwargs,
        ):
            captured["config"] = config
            return ("done", "claude", {})

    fn = _build_prompt_template_node(
        _node_with_timeout(timeout_seconds),
        provider_call=_RecordingRouter(), event_sink=None,
        llm_policy={"preferred": {}},
    )
    assert fn({"x": "thing"}).get("out") == "done"
    cfg = captured.get("config")
    assert cfg is not None, "node config was not threaded to the policy router"
    return cfg


# Fractional (sub-second authoring / tests), ordinary (the NodeDefinition
# default), and long (a deliberately patient node). All three must arrive as
# the node asked — the cap is a float knob, so unlike the legacy int `timeout`
# it needs no 1s floor to stay meaningful.
@pytest.mark.parametrize("timeout_seconds", [0.5, 300.0, 900.0])
@pytest.mark.parametrize("route", ["bridge", "policy"])
def test_node_timeout_becomes_the_provider_absolute_cap(timeout_seconds, route):
    cfg = _bridge_config(timeout_seconds) if route == "bridge" else _policy_config(
        timeout_seconds
    )
    assert cfg.absolute_cap_s == pytest.approx(timeout_seconds), (
        "the node's own timeout must be handed to the provider as its absolute "
        "cap; leaving it None lets a native stream run to the 600s library "
        "default while the node future has already failed the node"
    )
    # The resolved profile is what the streaming readers actually consult.
    profile = cfg.stream_timeout_profile()
    assert profile.absolute_cap_s == pytest.approx(timeout_seconds)
    # The legacy scalar keeps its existing floor-at-1s behaviour.
    assert cfg.timeout == max(1, int(timeout_seconds))


@pytest.mark.parametrize("route", ["bridge", "policy"])
def test_default_node_caps_the_provider_at_the_default_node_timeout(route):
    """A node that never declares a timeout gets 300s (NodeDefinition's
    default) on BOTH clocks — not 300s on the future and 600s on the stream."""
    cfg = _bridge_config(None) if route == "bridge" else _policy_config(None)
    assert NodeDefinition(node_id="x", display_name="X").timeout_seconds == 300.0
    assert cfg.stream_timeout_profile().absolute_cap_s == pytest.approx(300.0)


def test_interactive_model_config_default_cap_is_unchanged_at_600s():
    """The per-node cap is a per-node setting. An ordinary interactive
    ModelConfig must keep its 600s library backstop. Granted served turns may
    choose their own larger cap; this node-only fix must not change either
    the library default or the existing served-turn configuration."""
    from tinyassets.providers.base import DEFAULT_ABSOLUTE_CAP_S, ModelConfig

    assert DEFAULT_ABSOLUTE_CAP_S == 600.0
    assert ModelConfig().absolute_cap_s is None
    assert ModelConfig().stream_timeout_profile().absolute_cap_s == 600.0
    # A caller that only ever set the legacy scalar still gets the default.
    assert ModelConfig(timeout=300).stream_timeout_profile().absolute_cap_s == 600.0


def test_node_config_no_longer_buys_the_sync_wrapper_a_600s_budget():
    """Supporting evidence at the second clock: the sync router wrapper sizes
    its own timeout from ``max(legacy timeout, absolute cap) + 30``. With the
    cap unset a 300s node bought the wrapper 630s — over twice the node's own
    deadline."""
    from tinyassets.providers.router import _sync_call_timeout_s

    cfg = _bridge_config(300.0)
    assert _sync_call_timeout_s(cfg) == pytest.approx(330.0)


def test_a_progressing_native_stream_is_ended_by_the_nodes_own_cap():
    """The decisive behavioural test, on the native streaming path.

    A fake ``claude -p`` process emits real protocol progress every 50ms for
    ~2s, so the idle watchdog never fires and ONLY the absolute cap can end it.
    Driven with the config the compiler built for a 0.6s node, the reader must
    stop at the node's cap and kill the subprocess. Unfixed, the cap is the
    600s default, the fake stream simply runs to completion, and the real
    subprocess this stands in for would outlive its node by minutes.

    Claude is the native path exercised here; ``codex_provider`` reads
    ``profile.absolute_cap_s`` off the same generic ``ModelConfig``.
    """
    from tests.test_provider_stream_and_classify import (
        INIT,
        FakeStreamProcess,
        _line,
        _partial_text,
        _result,
        _run_stream,
    )
    from tinyassets.exceptions import InteractiveDeadlineError

    cfg = _bridge_config(0.6)
    items = [_line(INIT)]
    items += [(0.05, _line(_partial_text("x"))) for _ in range(40)]
    items += [(0.05, _line(_result("done")))]
    proc = FakeStreamProcess(items)

    started = time.monotonic()
    with pytest.raises(InteractiveDeadlineError):
        _run_stream(proc, cfg)
    elapsed = time.monotonic() - started

    assert proc.killed is True, "the capped stream must reap its subprocess"
    assert elapsed < 1.5, (
        f"ended after {elapsed:.2f}s — the node's 0.6s cap did not bound the "
        "still-progressing stream"
    )
