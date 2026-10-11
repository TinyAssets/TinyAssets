"""Real compiler/provider observer plumbing with deterministic transport responses."""

import threading

import pytest

from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.graph_compiler import _build_prompt_template_node, compile_branch
from tinyassets.providers.base import ProviderResponse


@pytest.mark.parametrize("policy", [None, {"preferred": {"provider": "owned"}}])
def test_parallel_compiler_nodes_keep_actual_response_receipts_separate(policy):
    barrier = threading.Barrier(2)
    events = []

    class ModelBridge:
        def __call__(self, prompt, system, **kwargs):
            barrier.wait(timeout=5)
            kwargs["response_observer"](ProviderResponse(
                prompt + " reply", prompt, "configured-alias", "test", 0,
                reported_model="reported-" + prompt,
            ))
            return prompt + " reply"

    branch = BranchDefinition(
        name="Parallel receipt fixture", default_llm_policy=policy, entry_point="a",
        node_defs=[NodeDefinition(node_id=name, display_name=name, prompt_template=name,
                                 output_keys=[name + "_out"]) for name in ("a", "b")],
        graph_nodes=[GraphNodeRef(id=name, node_def_id=name) for name in ("a", "b")],
        edges=[edge for name in ("a", "b") for edge in (
            EdgeDefinition(from_node="START", to_node=name),
            EdgeDefinition(from_node=name, to_node="END"),
        )], state_schema=[{"name": name + "_out", "type": "str"} for name in ("a", "b")],
    )
    graph = compile_branch(branch, provider_call=ModelBridge(),
                           event_sink=lambda **event: events.append(event)).graph.compile()
    assert graph.invoke({}) == {"a_out": "a reply", "b_out": "b reply"}
    ran = {event["node_id"]: event for event in events if event["phase"] == "ran"}
    for name in ("a", "b"):
        assert ran[name]["execution"]["provider"] == name
        assert ran[name]["execution"]["reported_model"] == "reported-" + name
        assert "requested_model" not in ran[name]["execution"]


def test_legacy_configured_model_without_response_observation_is_unknown():
    events = []
    class OldBridge:
        def __call__(self, prompt, system, **kwargs):
            return "answer"

    node = _build_prompt_template_node(
        NodeDefinition(node_id="answer", display_name="Answer", prompt_template="question",
                       output_keys=["reply"]),
        provider_call=OldBridge(), event_sink=lambda **event: events.append(event),
        llm_policy={"preferred": {"provider": "codex"}},
    )
    assert node({}) == {"reply": "answer"}
    assert events[-1].get("execution") is None


@pytest.mark.parametrize("reported", ["", "actual-model"])
def test_router_metadata_keeps_configured_and_reported_model_distinct(reported):
    from tinyassets.providers.router import ProviderRouter

    response = ProviderResponse("answer", "owned", "configured-alias", "test", 0,
                                reported_model=reported)
    meta = ProviderRouter._call_meta(response, 2)
    assert meta["model"] == "configured-alias"
    assert meta["execution"]["model"] == reported
    assert meta["execution"]["model_status"] == ("reported" if reported else "unknown")
    assert meta["execution"]["resolved_model"] == "configured-alias"
