"""Receiver entry projections: real topology/preflight, no external provider."""

from __future__ import annotations

import copy

import pytest

from tinyassets.branches import (
    BranchDefinition,
    ConditionalEdge,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import compile_branch, seed_initial_state
from tinyassets.graph_ingress import ReceiverProjectionError, project_receiver_entry
from tinyassets.runs import MissingRequiredInputs, preflight_required_inputs


def _node(name, *, inputs=(), output=None, workspace=""):
    return NodeDefinition(
        node_id=name,
        display_name=name,
        prompt_template=name + " " + " ".join("{" + key + "}" for key in inputs),
        input_keys=list(inputs),
        output_keys=[output or name + "_out"],
        workspace=workspace,
    )


def _branch(nodes, edges, *, conditionals=(), schema=()):
    keys = {key for node in nodes for key in (*node.input_keys, *node.output_keys)}
    return BranchDefinition(
        branch_def_id="receiver-graph",
        name="Receiver fixture",
        author="receiver-owner",
        visibility="private",
        node_defs=nodes,
        graph_nodes=[GraphNodeRef(id=node.node_id) for node in nodes],
        edges=[EdgeDefinition(*edge) for edge in edges],
        conditional_edges=list(conditionals),
        entry_point=nodes[0].node_id,
        state_schema=list(schema) or [{"name": key, "type": "str"} for key in sorted(keys)],
        default_llm_policy={"preferred": {"provider": "user-chosen"}},
    )


def _invoke(branch, values=None, provider=None):
    calls = []

    class ProjectionProvider:
        def __call__(self, prompt, _system="", **kwargs):
            pytest.fail("a policy-bearing projection must use policy execution")

        def call_with_policy_sync(self, role, prompt, system, policy, config=None, **kwargs):
            # Projection preserves policy as well as topology. A plain callable
            # can no longer bypass that policy through the legacy text bridge.
            assert policy == branch.default_llm_policy
            assert policy["preferred"]["provider"] == "user-chosen"
            calls.append(prompt)
            text = provider(prompt) if provider else "handled"
            return text, "user-chosen", {}

    compiled = compile_branch(branch, provider_call=ProjectionProvider())
    result = compiled.graph.compile().invoke(seed_initial_state(values or {}, branch.state_schema))
    return result, calls


def test_internal_entry_skips_upstream_and_other_start_routes():
    original = _branch(
        [_node("upstream"), _node("receive", inputs=("topic",)), _node("finish"), _node("other")],
        [("START", "upstream"), ("START", "other"), ("upstream", "receive"),
         ("receive", "finish"), ("finish", "END"), ("other", "END")],
    )
    original.node_defs[0].effects = ["must-never-dispatch"]
    before = copy.deepcopy(original.to_dict())
    projection = project_receiver_entry(original, "receive", contract_input_keys=["topic"])
    result, calls = _invoke(projection.branch, {"topic": "actual deliverable"})
    assert result["finish_out"] == "handled"
    assert calls == ["receive actual deliverable", "finish "]
    assert projection.node_ids == ("receive", "finish")
    assert original.to_dict() == before
    assert projection.branch.author == "receiver-owner"
    assert projection.branch.visibility == "private"
    assert projection.branch.default_llm_policy == original.default_llm_policy


def test_removed_predecessor_output_must_be_a_contract_input():
    branch = _branch(
        [_node("prepare", output="topic"), _node("receive", inputs=("topic",))],
        [("prepare", "receive"), ("receive", "END")],
    )
    preflight_required_inputs(branch, {})
    with pytest.raises(MissingRequiredInputs) as caught:
        project_receiver_entry(branch, "receive")
    assert caught.value.missing_input_keys == ["topic"]
    projection = project_receiver_entry(branch, "receive", contract_input_keys=["topic"])
    with pytest.raises(MissingRequiredInputs):
        preflight_required_inputs(projection.branch, {})
    preflight_required_inputs(projection.branch, {"topic": "delivered"})


def test_receiver_default_remains_a_valid_ingress_input():
    branch = _branch(
        [_node("prepare"), _node("receive", inputs=("topic",))],
        [("prepare", "receive"), ("receive", "END")],
        schema=[{"name": "topic", "type": "str", "default": "receiver default"},
                {"name": "prepare_out", "type": "str"}, {"name": "receive_out", "type": "str"}],
    )
    projection = project_receiver_entry(branch, "receive")
    _, calls = _invoke(projection.branch)
    assert calls == ["receive receiver default"]


def test_parallel_downstream_fan_in_is_preserved():
    branch = _branch(
        [_node("upstream"), _node("receive"), _node("left"), _node("right"),
         _node("join", inputs=("left_out", "right_out"))],
        [("upstream", "receive"), ("receive", "left"), ("receive", "right"),
         ("left", "join"), ("right", "join"), ("join", "END")],
    )
    projection = project_receiver_entry(branch, "receive")
    _, calls = _invoke(projection.branch)
    assert calls[0] == "receive " and calls[-1] == "join handled handled"
    assert sorted(calls[1:-1]) == ["left ", "right "]


def test_conditional_loop_retains_actual_revisits_and_exit():
    branch = _branch(
        [_node("upstream"), _node("receive", output="route"), _node("again")],
        [("upstream", "receive"), ("again", "receive")],
        conditionals=[ConditionalEdge("receive", {"repeat": "again", "done": "END"})],
    )
    visits = 0

    def provider(prompt):
        nonlocal visits
        if prompt.startswith("receive"):
            visits += 1
            return "repeat" if visits == 1 else "done"
        return "looped"

    projection = project_receiver_entry(branch, "receive")
    _, calls = _invoke(projection.branch, provider=provider)
    assert calls == ["receive ", "again ", "receive "]


def test_shared_definition_uses_graph_placement_ids():
    branch = _branch([_node("shared")], [])
    branch.graph_nodes = [GraphNodeRef("old", "shared"), GraphNodeRef("chosen", "shared")]
    branch.entry_point = "old"
    branch.edges = [EdgeDefinition("old", "chosen"), EdgeDefinition("chosen", "END")]
    projection = project_receiver_entry(branch, "chosen")
    assert projection.node_ids == ("chosen",)
    assert [node.node_id for node in projection.branch.node_defs] == ["shared"]
    assert _invoke(projection.branch)[1] == ["shared "]


def test_projection_is_detached_and_hashes_the_entire_source():
    branch = _branch([_node("upstream"), _node("receive")],
                     [("upstream", "receive"), ("receive", "END")])
    projection = project_receiver_entry(branch, "receive")
    stable = projection.snapshot_json
    branch.default_llm_policy["preferred"]["provider"] = "different"
    exposed_copy = projection.branch
    exposed_copy.node_defs[0].prompt_template = "modified"
    assert projection.snapshot_json == stable
    assert projection.branch.node_defs[0].prompt_template == "receive "
    next_projection = project_receiver_entry(branch, "receive")
    assert projection.source_sha256 != next_projection.source_sha256
    assert len(projection.source_sha256) == len(projection.snapshot_sha256) == 64


@pytest.mark.parametrize("node_id", ["missing", "START", "END", "", None])
def test_invalid_entry_is_not_silently_replaced(node_id):
    branch = _branch([_node("receive")], [("receive", "END")])
    with pytest.raises(ReceiverProjectionError):
        project_receiver_entry(branch, node_id)


def test_removed_workspace_ancestor_is_refused_at_exposure():
    branch = _branch([_node("checkout"), _node("receive", workspace="checkout")],
                     [("checkout", "receive"), ("receive", "END")])
    with pytest.raises(ReceiverProjectionError, match="workspace.*checkout.*ancestor"):
        project_receiver_entry(branch, "receive")


def test_retained_workspace_ancestor_is_not_lost():
    branch = _branch([_node("upstream"), _node("checkout"), _node("receive", workspace="checkout")],
                     [("upstream", "checkout"), ("checkout", "receive"), ("receive", "END")])
    projection = project_receiver_entry(branch, "checkout")
    assert projection.branch.node_defs[-1].workspace == "checkout"


def test_reachable_dangling_edge_is_not_trimmed_into_a_valid_graph():
    branch = _branch([_node("receive")], [("receive", "nonexistent")])
    with pytest.raises(ReceiverProjectionError, match="nonexistent"):
        project_receiver_entry(branch, "receive")


def test_duplicate_graph_identity_is_refused():
    branch = _branch([_node("receive")], [("receive", "END")])
    branch.graph_nodes.append(GraphNodeRef("receive"))
    with pytest.raises(ReceiverProjectionError, match="Duplicate"):
        project_receiver_entry(branch, "receive")


@pytest.mark.parametrize("keys", [["unknown"], [7], "receive_out"])
def test_contract_keys_must_be_declared_state_fields(keys):
    branch = _branch([_node("receive")], [("receive", "END")])
    with pytest.raises(ReceiverProjectionError, match="contract"):
        project_receiver_entry(branch, "receive", contract_input_keys=keys)


def test_original_entry_projection_preserves_execution():
    branch = _branch([_node("receive"), _node("finish", inputs=("receive_out",))],
                     [("START", "receive"), ("receive", "finish"), ("finish", "END")])
    projection = project_receiver_entry(branch, "receive")
    assert _invoke(projection.branch) == _invoke(branch)


def test_nonselected_disconnected_component_does_not_need_repair():
    branch = _branch([_node("unrelated"), _node("receive")],
                     [("unrelated", "missing"), ("receive", "END")])
    assert branch.validate()
    projection = project_receiver_entry(branch, "receive")
    assert projection.branch.validate() == []
    assert _invoke(projection.branch)[1] == ["receive "]


def test_reachable_exitless_cycle_is_rejected():
    branch = _branch([_node("receive"), _node("again")],
                     [("receive", "again"), ("again", "receive")])
    with pytest.raises(ReceiverProjectionError, match="cycle"):
        project_receiver_entry(branch, "receive")


def test_conditional_missing_input_on_one_path_is_rejected():
    branch = _branch(
        [_node("receive", output="route"), _node("produces", output="payload"),
         _node("skips"), _node("consumer", inputs=("payload",))],
        [("produces", "consumer"), ("skips", "consumer"), ("consumer", "END")],
        conditionals=[ConditionalEdge("receive", {"one": "produces", "two": "skips"})],
    )
    with pytest.raises(MissingRequiredInputs) as caught:
        project_receiver_entry(branch, "receive")
    assert caught.value.missing_input_keys == ["payload"]


def test_graph_cannot_escape_projection_by_targeting_start():
    branch = _branch([_node("receive")], [("receive", "START"), ("receive", "END")])
    with pytest.raises(ReceiverProjectionError, match="START"):
        project_receiver_entry(branch, "receive")
