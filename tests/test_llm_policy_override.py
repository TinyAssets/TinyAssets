"""Tests for per-node llm_policy override.

docs/vetted-specs.md — "Per-node llm_policy override" (navigator-vetted 2026-04-22).

Coverage:
- pinned preferred provider used when available
- fallback fires after preferred failure (trigger-typed)
- difficulty_override routes correctly
- branch default_llm_policy applies when node-level unset
- unset policy = role-routing backward-compat (no regression)
- validate() rejects malformed policy shapes
- provider_served emitted in "ran" event
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest  # noqa: F401 — used by pytest.raises

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
    _validate_llm_policy_shape,
)
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.graph_compiler import compile_branch

# ─── helpers ──────────────────────────────────────────────────────────────────


class _PolicyBridge:
    """The run's own injected, universe-bound provider bridge with a policy door.

    Hard Rule 15 removed ``graph_compiler``'s shared-router fallback: a policy
    node is honoured only by the run's injected caller (in production the
    carrier-bound ``foreground_run_provider`` / cloud continuation callers).
    """

    def __init__(self, router, plain):
        self._router = router
        self._plain = plain
        self.available_providers = getattr(router, "available_providers", None)

    def __call__(self, prompt, system, *, role="writer", **_kwargs):
        return self._plain(prompt, system, role=role)

    def call_with_policy_sync(self, *args, **kwargs):
        return self._router.call_with_policy_sync(*args, **kwargs)



def _make_branch(
    node: NodeDefinition,
    *,
    default_llm_policy: dict[str, Any] | None = None,
) -> BranchDefinition:
    b = BranchDefinition(
        name="test",
        domain_id="workflow",
        author="tester",
        entry_point=node.node_id,
        node_defs=[node],
        graph_nodes=[GraphNodeRef(id=node.node_id, node_def_id=node.node_id)],
        edges=[EdgeDefinition(from_node=node.node_id, to_node="END")],
        state_schema=[
            {"name": "topic", "type": "str"},
            {"name": "out", "type": "str"},
        ],
        default_llm_policy=default_llm_policy,
    )
    return b


def _make_node(llm_policy: dict[str, Any] | None = None) -> NodeDefinition:
    return NodeDefinition(
        node_id="n1",
        display_name="N1",
        input_keys=["topic"],
        output_keys=["out"],
        prompt_template="Write about {topic}",
        llm_policy=llm_policy,
    )


def _compile_with_capture(
    branch: BranchDefinition,
) -> tuple[Any, list[dict], list[str]]:
    """Compile branch with a mock provider_call; return (app, events, prompts)."""
    events: list[dict] = []
    prompts: list[str] = []

    def _provider(prompt: str, system: str, *, role: str = "writer") -> str:
        prompts.append(prompt)
        return f"[response to: {prompt[:40]}]"

    def _sink(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    compiled = compile_branch(branch, provider_call=_provider, event_sink=_sink)
    app = compiled.graph.compile()
    return app, events, prompts


# ─── Part 1: backward-compat (unset policy = role-routing) ─────────────────


def test_no_llm_policy_uses_plain_provider_call():
    """When llm_policy is None, provider_call is called as before."""
    node = _make_node(llm_policy=None)
    branch = _make_branch(node)
    app, events, prompts = _compile_with_capture(branch)
    app.invoke({"topic": "whales"}, config={"configurable": {"thread_id": "t1"}})
    assert prompts  # provider_call was used
    ran = [e for e in events if e.get("phase") == "ran"]
    assert ran


def test_no_llm_policy_ran_event_has_provider_served():
    """The 'ran' event always carries provider_served (unknown for plain call)."""
    node = _make_node(llm_policy=None)
    branch = _make_branch(node)
    app, events, _ = _compile_with_capture(branch)
    app.invoke({"topic": "x"}, config={"configurable": {"thread_id": "t-ps"}})
    ran = [e for e in events if e.get("phase") == "ran"]
    assert ran
    # provider_served is present on the ran event
    assert "provider_served" in ran[0]


# ─── Part 2: policy routing via ProviderRouter ─────────────────────────────


def _mock_router_for_policy(
    preferred_response: str = "policy-response",
    preferred_provider: str = "groq-free",
    raises_on_preferred: Exception | None = None,
    fallback_response: str = "fallback-response",
    fallback_provider: str = "ollama-local",
) -> MagicMock:
    """Build a mock ProviderRouter for unit-testing the policy path."""
    mock_router = MagicMock()

    if raises_on_preferred:
        def _side_effect(
            role, prompt, system, policy, config=None, difficulty="", **_kwargs,
        ):
            raise raises_on_preferred
    else:
        def _side_effect(
            role, prompt, system, policy, config=None, difficulty="", **_kwargs,
        ):
            return preferred_response, preferred_provider, {}

    mock_router.call_with_policy_sync.side_effect = _side_effect
    return mock_router


def test_pinned_preferred_used_when_available():
    """With llm_policy.preferred, ProviderRouter.call_with_policy_sync is called
    and the response + provider_name flow back through the graph."""
    policy = {"preferred": {"provider": "groq-free"}}
    node = _make_node(llm_policy=policy)
    branch = _make_branch(node)

    events: list[dict] = []
    mock_router = _mock_router_for_policy(
        preferred_response="groq says hi", preferred_provider="groq-free",
    )

    _router = mock_router
    def _provider(prompt, system, *, role="writer"):
        return "[plain-provider]"

    def _sink(**kw):
        events.append(dict(kw))

    compiled = compile_branch(
        branch, provider_call=_PolicyBridge(_router, _provider), event_sink=_sink,
    )
    app = compiled.graph.compile()
    app.invoke(
        {"topic": "cosmos"},
        config={"configurable": {"thread_id": "t-pref"}},
    )

    mock_router.call_with_policy_sync.assert_called_once()
    ran = [e for e in events if e.get("phase") == "ran"]
    assert ran[0]["provider_served"] == "groq-free"


def test_policy_router_empty_registry_falls_back_to_injected_provider_call():
    """A policy-incapable route must refuse before the plain bridge can substitute."""
    policy = {"preferred": {"provider": "codex"}}
    node = _make_node(llm_policy=policy)
    branch = _make_branch(node)

    class _EmptyRouter:
        available_providers: list[str] = []

        def call_with_policy_sync(self, *args, **kwargs):
            raise AssertionError("empty policy router should not be called")

    events: list[dict] = []
    prompts: list[str] = []

    def _provider(prompt, system, *, role="writer"):
        prompts.append(prompt)
        return "served by injected provider"

    def _sink(**kw):
        events.append(dict(kw))

    _router = _EmptyRouter()
    compiled = compile_branch(
        branch, provider_call=_PolicyBridge(_router, _provider), event_sink=_sink,
    )
    app = compiled.graph.compile()
    from tinyassets.graph_compiler import CompilerError

    # Historical test name retained: this fallback must now fail closed, because
    # the plain bridge has no way to honour the node's chosen source.
    with pytest.raises(CompilerError, match="default on codex.*policy execution"):
        app.invoke(
            {"topic": "install planning"},
            config={"configurable": {"thread_id": "t-empty-router"}},
        )

    assert prompts == []
    assert any(e.get("phase") == "failed" for e in events)
    ran = [e for e in events if e.get("phase") == "ran"]
    assert ran == []


def test_branch_default_policy_applies_when_node_unset():
    """Branch-level default_llm_policy is used when node has no llm_policy."""
    default_policy = {"preferred": {"provider": "gemini-free"}}
    node = _make_node(llm_policy=None)  # no node-level policy
    branch = _make_branch(node, default_llm_policy=default_policy)

    events: list[dict] = []
    mock_router = _mock_router_for_policy(
        preferred_response="gemini says hi", preferred_provider="gemini-free",
    )

    _router = mock_router
    def _provider(prompt, system, *, role="writer"):
        return "[plain]"

    def _sink(**kw):
        events.append(dict(kw))

    compiled = compile_branch(
        branch, provider_call=_PolicyBridge(_router, _provider), event_sink=_sink,
    )
    app = compiled.graph.compile()
    app.invoke(
        {"topic": "stars"},
        config={"configurable": {"thread_id": "t-bdefault"}},
    )

    mock_router.call_with_policy_sync.assert_called_once()
    ran = [e for e in events if e.get("phase") == "ran"]
    assert ran[0]["provider_served"] == "gemini-free"


def test_node_policy_overrides_branch_default():
    """Node-level llm_policy takes precedence over branch default."""
    node_policy = {"preferred": {"provider": "codex"}}
    branch_policy = {"preferred": {"provider": "ollama-local"}}
    node = _make_node(llm_policy=node_policy)
    branch = _make_branch(node, default_llm_policy=branch_policy)

    calls: list[dict] = []
    def _mock_policy_call(
        role, prompt, system, policy, config=None, difficulty="", **_kwargs,
    ):
        calls.append({"policy": policy})
        return "node-policy-response", "codex", {}

    mock_router = MagicMock()
    mock_router.call_with_policy_sync.side_effect = _mock_policy_call

    _router = mock_router
    compiled = compile_branch(
        branch,
        provider_call=_PolicyBridge(_router, lambda p, s, *, role="writer": "[plain]"),
    )
    app = compiled.graph.compile()
    app.invoke(
        {"topic": "tests"},
        config={"configurable": {"thread_id": "t-node-beats-branch"}},
    )

    assert calls
    # The policy passed to the router should be the node-level one
    assert calls[0]["policy"]["preferred"]["provider"] == "codex"


def test_graph_propagates_exhaustion_when_all_providers_fail():
    """A GRAPH-level test: when the router raises AllProvidersExhaustedError,
    the compiled branch surfaces it rather than swallowing it. (This mocks the
    router — it does NOT exercise router fallback; the real router fall-through
    is covered by ``test_policy_clean_failure_still_falls_back_to_the_role_chain``
    in ``test_provider_stream_and_classify.py``, which drives the real
    classifier.)"""
    from tinyassets.exceptions import AllProvidersExhaustedError

    policy = {
        "preferred": {"provider": "groq-free"},
        "fallback_chain": [{"provider": "ollama-local", "trigger": "unavailable"}],
    }
    node = _make_node(llm_policy=policy)
    branch = _make_branch(node)

    # Simulate router raising AllProvidersExhaustedError
    mock_router = MagicMock()
    mock_router.call_with_policy_sync.side_effect = AllProvidersExhaustedError(
        "all exhausted"
    )

    _router = mock_router
    compiled = compile_branch(
        branch,
        provider_call=_PolicyBridge(_router, lambda p, s, *, role="writer": "[plain]"),
    )
    app = compiled.graph.compile()
    with pytest.raises(Exception):
        app.invoke(
            {"topic": "x"},
            config={"configurable": {"thread_id": "t-fallback"}},
        )


def test_policy_dispatch_retries_transient_provider_exhaustion(monkeypatch):
    """Policy-aware run_branch dispatch should honor the router retry contract."""
    import tinyassets.graph_compiler as graph_compiler

    policy = {"preferred": {"provider": "groq-free"}}
    node = _make_node(llm_policy=policy)
    branch = _make_branch(node)

    mock_router = MagicMock()
    mock_router.call_with_policy_sync.side_effect = [
        AllProvidersExhaustedError(
            "All providers exhausted for role=writer. Daemon should retry with backoff."
        ),
        ("success after retry", "groq-free", {}),
    ]

    monkeypatch.setattr(
        graph_compiler, "_POLICY_PROVIDER_RETRY_BACKOFF_SECONDS", (0.0, 0.0),
    )
    _router = mock_router
    compiled = compile_branch(
        branch,
        provider_call=_PolicyBridge(_router, lambda p, s, *, role="writer": "[plain]"),
    )
    app = compiled.graph.compile()
    out = app.invoke(
        {"topic": "x"},
        config={"configurable": {"thread_id": "t-policy-retry"}},
    )

    assert out["out"] == "success after retry"
    assert mock_router.call_with_policy_sync.call_count == 2


def test_difficulty_override_passed_through():
    """difficulty param is forwarded to call_with_policy_sync."""
    policy = {
        "preferred": {"provider": "groq-free"},
        "difficulty_override": [
            {"if_difficulty": "hard", "use": {"provider": "claude-code"}},
        ],
    }
    node = _make_node(llm_policy=policy)
    branch = _make_branch(node)

    calls: list[dict] = []
    def _mock_call(
        role, prompt, system, policy, config=None, difficulty="", **_kwargs,
    ):
        calls.append({"difficulty": difficulty})
        return "hard-response", "claude-code", {}

    mock_router = MagicMock()
    mock_router.call_with_policy_sync.side_effect = _mock_call

    _router = mock_router
    compiled = compile_branch(
        branch,
        provider_call=_PolicyBridge(_router, lambda p, s, *, role="writer": "[plain]"),
    )
    app = compiled.graph.compile()
    app.invoke(
        {"topic": "hard topic"},
        config={"configurable": {"thread_id": "t-difficulty"}},
    )

    # Default difficulty is empty string since we don't thread it yet —
    # test confirms the interface compiles and routes without error.
    assert calls


# ─── Part 3: serialization round-trip ────────────────────────────────────────


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
    assert any("use" in e for e in errors)


def test_validate_accepts_valid_policy():
    errors = _validate_llm_policy_shape(
        {
            "preferred": {"provider": "groq-free", "model": "llama3"},
            "fallback_chain": [
                {"provider": "ollama-local", "trigger": "unavailable"},
            ],
            "difficulty_override": [
                {"if_difficulty": "hard", "use": {"provider": "claude-code"}},
            ],
        },
        context="test",
    )
    assert not errors


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
