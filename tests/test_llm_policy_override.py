"""Workflow model policies flow through the one prompt call."""

from __future__ import annotations

from typing import Any

import pytest

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
    _validate_llm_policy_shape,
)
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.graph_compiler import CompilerError, compile_branch


def _make_node(llm_policy: dict[str, Any] | None = None) -> NodeDefinition:
    return NodeDefinition(
        node_id="n1", display_name="N1", input_keys=["topic"], output_keys=["out"],
        prompt_template="Write about {topic}", llm_policy=llm_policy,
    )


def _make_branch(node: NodeDefinition, *, default_llm_policy=None) -> BranchDefinition:
    return BranchDefinition(
        name="test", domain_id="workflow", author="tester", entry_point=node.node_id,
        node_defs=[node], graph_nodes=[GraphNodeRef(id=node.node_id, node_def_id=node.node_id)],
        edges=[EdgeDefinition(from_node=node.node_id, to_node="END")],
        state_schema=[{"name": "topic", "type": "str"}, {"name": "out", "type": "str"}],
        default_llm_policy=default_llm_policy,
    )


def _run(node, *, default_policy=None, provider=None):
    seen = []
    def _provider(prompt, system, **kwargs):
        seen.append(kwargs.get("policy"))
        return "answer"
    branch = _make_branch(node, default_llm_policy=default_policy)
    app = compile_branch(branch, provider_call=provider or _provider).graph.compile()
    result = app.invoke({"topic": "cosmos"}, config={"configurable": {"thread_id": "one-call"}})
    return result, seen


def test_unpinned_prompt_uses_the_same_call():
    result, seen = _run(_make_node())
    assert result["out"] == "answer"
    assert seen == [None]


def test_node_preferred_model_is_passed_to_bound_call():
    policy = {"preferred": {"provider": "owned", "model_id": "chosen"}}
    result, seen = _run(_make_node(policy))
    assert result["out"] == "answer"
    assert seen == [policy]


def test_branch_default_applies_when_node_policy_is_unset():
    policy = {"preferred": {"provider": "owned", "model_id": "default"}}
    _, seen = _run(_make_node(), default_policy=policy)
    assert seen == [policy]


def test_node_policy_overrides_branch_default():
    default = {"preferred": {"provider": "owned", "model_id": "default"}}
    override = {"preferred": {"provider": "owned", "model_id": "node"}}
    result, seen = _run(_make_node(override), default_policy=default)
    assert seen == [override]
    assert result["out"] == "answer"


def test_missing_model_caller_fails_loudly():
    branch = _make_branch(_make_node({"preferred": {"provider": "owned"}}))
    app = compile_branch(branch, provider_call=None).graph.compile()
    with pytest.raises(CompilerError, match="no bound model call"):
        app.invoke({"topic": "cosmos"}, config={"configurable": {"thread_id": "missing"}})


def test_refusal_is_not_retried_by_the_prompt_node():
    attempts = []
    def refusing(prompt, system, **kwargs):
        attempts.append(prompt)
        raise AllProvidersExhaustedError("original refusal")
    with pytest.raises(Exception, match="original refusal"):
        _run(_make_node({"preferred": {"provider": "owned"}}), provider=refusing)
    assert len(attempts) == 1


def test_node_llm_policy_serializes_and_deserializes():
    """llm_policy round-trips through to_dict / from_dict."""
    policy = {
        "preferred": {"provider": "groq-free", "model": "llama3-8b"},
        "fallback_chain": [{"provider": "ollama-local", "trigger": "unavailable"}],
    }
    node = NodeDefinition(
        node_id="n1", display_name="N1",
        prompt_template="Say {topic}", llm_policy=policy,
    )
    d = node.to_dict()
    assert d["llm_policy"] == policy
    node2 = NodeDefinition.from_dict(d)
    assert node2.llm_policy == policy


def test_branch_default_llm_policy_serializes():
    """default_llm_policy round-trips through BranchDefinition to_dict / from_dict."""
    policy = {"preferred": {"provider": "ollama-local"}}
    node = NodeDefinition(
        node_id="n1", display_name="N1",
        prompt_template="Say hi",
    )
    branch = BranchDefinition(
        name="test", entry_point="n1",
        node_defs=[node],
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        default_llm_policy=policy,
    )
    d = branch.to_dict()
    assert d["default_llm_policy"] == policy
    branch2 = BranchDefinition.from_dict(d)
    assert branch2.default_llm_policy == policy


# ─── Part 4: validate() rejects malformed policy ─────────────────────────────


def test_validate_rejects_preferred_without_provider():
    errors = _validate_llm_policy_shape(
        {"preferred": {"model": "llama3"}}, context="test",
    )
    assert any("provider" in e for e in errors)


def test_validate_rejects_invalid_trigger():
    errors = _validate_llm_policy_shape(
        {"fallback_chain": [{"provider": "x", "trigger": "bad-trigger"}]},
        context="test",
    )
    assert any("bad-trigger" in e for e in errors)


def test_validate_rejects_difficulty_override_without_use():
    errors = _validate_llm_policy_shape(
        {"difficulty_override": [{"if_difficulty": "hard"}]},
        context="test",
    )
    assert any("difficulty_override" in e for e in errors)


def test_validate_accepts_valid_policy():
    errors = _validate_llm_policy_shape(
        {
            "preferred": {"provider": "groq-free", "model": "llama3"},
            "fallback_chain": [
                {"provider": "ollama-local", "trigger": "unavailable"},
            ],
        },
        context="test",
    )
    assert not errors


def test_validate_rejects_retired_difficulty_override_even_when_well_formed():
    errors = _validate_llm_policy_shape(
        {"difficulty_override": [
            {"if_difficulty": "hard", "use": {"provider": "claude-code"}},
        ]},
        context="test",
    )
    assert any("retired" in error for error in errors)


def test_branch_validate_catches_malformed_node_policy():
    """BranchDefinition.validate() surfaces llm_policy shape errors."""
    node = NodeDefinition(
        node_id="n1", display_name="N1",
        prompt_template="Say hi",
        llm_policy={"preferred": {}},  # missing 'provider'
    )
    branch = BranchDefinition(
        name="test", entry_point="n1",
        node_defs=[node],
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
    )
    errors = branch.validate()
    assert any("provider" in e for e in errors)


def test_branch_validate_catches_malformed_default_policy():
    """BranchDefinition.validate() surfaces default_llm_policy shape errors."""
    node = NodeDefinition(
        node_id="n1", display_name="N1",
        prompt_template="Say hi",
    )
    branch = BranchDefinition(
        name="test", entry_point="n1",
        node_defs=[node],
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        default_llm_policy={"fallback_chain": "not-a-list"},  # wrong type
    )
    errors = branch.validate()
    assert any("fallback_chain" in e for e in errors)
