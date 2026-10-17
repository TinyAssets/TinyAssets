"""Latency evidence a completed provider turn must leave behind (2026-09-24).

The parallel-probe investigation (2026-09-24-provider-latency-rootcause.md (archived in git))
had to reconstruct node timing from CLI session transcripts because a node's
receipt recorded ``latency_ms: null`` and nothing about tool calls, and a turn
that went quiet for most of the idle bound and then recovered left no trace.
These tests pin the evidence a SUCCESSFUL turn now reports:

* the claude reader counts distinct native tool calls and records the longest
  gap between protocol events, plus the event kind that preceded it;
* a near-idle gap is logged with allowlisted scalars only;
* a prompt node's ``ran`` event carries those scalars from the real response.

Driven through the real ``ClaudeProvider._read_stream`` against the fake
subprocess protocol of ``tests/test_provider_stream_and_classify.py``.
"""

from __future__ import annotations

from tinyassets.providers.base import ModelConfig, ProviderResponse

_IDLE_S = 0.4
_PROFILE = ModelConfig(
    init_timeout_s=_IDLE_S, first_progress_s=_IDLE_S, idle_timeout_s=_IDLE_S,
    absolute_cap_s=5.0,
)


def test_provider_timing_keeps_only_allowlisted_scalars():
    from tinyassets.graph_compiler import _provider_timing

    response = ProviderResponse(
        text="secret answer text", provider="claude-code", model="m", family="anthropic",
        latency_ms=12_345.678, ttft_ms=900.0, input_tokens=19, output_tokens=10_005,
        tool_uses=29, max_silence_ms=28_000.04, max_silence_after="tool_result",
    )
    assert _provider_timing(response) == {
        "latency_ms": 12345.7, "ttft_ms": 900.0, "max_silence_ms": 28000.0,
        "input_tokens": 19, "output_tokens": 10005, "tool_uses": 29,
        "max_silence_after": "tool_result",
    }
    forged = ProviderResponse(
        text="x", provider="p", model="m", family="f", latency_ms=float("nan"),
        tool_uses=True, max_silence_after="free text from somewhere",
    )
    assert _provider_timing(forged) == {}


def test_prompt_node_ran_event_carries_provider_timing():
    """The real prompt-node closure persists the observed response's timing."""
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.graph_compiler import compile_branch

    node = NodeDefinition(
        node_id="angle", display_name="Angle", prompt_template="Say hi.",
        output_keys=["out"], model_hint="writer",
    )
    branch = BranchDefinition(
        branch_def_id="b_timing", name="timing", author="acct", visibility="private",
        graph_nodes=[GraphNodeRef(id="angle", node_def_id="angle")],
        edges=[EdgeDefinition(from_node="START", to_node="angle"),
               EdgeDefinition(from_node="angle", to_node="END")],
        entry_point="angle", node_defs=[node],
        state_schema=[{"name": "out", "type": "str", "default": ""}],
    )

    def provider_call(prompt, system="", *, role="writer", config=None,
                      response_observer=None, **_kwargs):
        response_observer(ProviderResponse(
            text="hi", provider="claude-code", model="m", family="anthropic",
            latency_ms=4200.0, output_tokens=631, tool_uses=0,
            max_silence_ms=1200.0, max_silence_after="heartbeat",
        ))
        return "hi"

    events: list[dict] = []

    def sink(**detail):
        events.append(detail)

    compiled = compile_branch(branch, provider_call=provider_call, event_sink=sink)
    compiled.graph.compile().invoke({"out": ""})
    [ran] = [e for e in events if e.get("phase") == "ran"]
    assert ran["provider_timing"] == {
        "latency_ms": 4200.0, "max_silence_ms": 1200.0, "output_tokens": 631,
        "tool_uses": 0, "max_silence_after": "heartbeat",
    }
