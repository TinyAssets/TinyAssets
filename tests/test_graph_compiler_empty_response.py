"""BUG-004 Layer 2+3 — EmptyResponseError unit tests.

Integration coverage (runner catches EmptyResponseError, emits NODE_STATUS_FAILED
event, marks run failed) lives in test_node_timeout.py lines 207-267.

This file covers the unit-level contracts:
- EmptyResponseError is a CompilerError subclass with a node_id attribute.
- compile_branch raises EmptyResponseError when provider returns "".
- _find_empty_response_exception walks the chain across __cause__ / __context__.
- _find_timeout_exception is unaffected (no regression).
"""

from __future__ import annotations

import pytest

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import (
    CompilerError,
    EmptyResponseError,
    NodeTimeoutError,
    compile_branch,
)
from tinyassets.runs import _find_empty_response_exception, _find_timeout_exception

# ── EmptyResponseError class shape ────────────────────────────────────────────


def test_empty_response_error_is_compiler_error():
    exc = EmptyResponseError("empty", node_id="n1")
    assert isinstance(exc, CompilerError)


def test_empty_response_error_has_node_id_attribute():
    exc = EmptyResponseError("empty", node_id="my_node")
    assert exc.node_id == "my_node"


def test_empty_response_error_default_node_id_is_empty():
    exc = EmptyResponseError("empty")
    assert exc.node_id == ""


def test_empty_response_error_str_contains_message():
    exc = EmptyResponseError("LLM returned empty", node_id="n1")
    assert "LLM returned empty" in str(exc)


# ── compile_branch raises EmptyResponseError on empty provider ────────────────


def _simple_branch() -> BranchDefinition:
    b = BranchDefinition(name="empty-probe", entry_point="step1")
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


def test_empty_provider_raises_empty_response_error():
    """A provider returning '' should surface as EmptyResponseError, not
    a generic CompilerError, so the runner can record reason='empty_response'."""
    from langgraph.checkpoint.memory import InMemorySaver

    def _empty(prompt, system, *, role):
        return ""

    branch = _simple_branch()
    compiled = compile_branch(branch, provider_call=_empty)
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    with pytest.raises(EmptyResponseError) as exc_info:
        runnable.invoke(
            {"x": "test"},
            config={"configurable": {"thread_id": "t1"}},
        )
    assert exc_info.value.node_id == "step1"


def test_non_empty_provider_does_not_raise_empty_response_error():
    """Sanity: a provider returning a non-empty string must not raise."""
    from langgraph.checkpoint.memory import InMemorySaver

    def _ok(prompt, system, *, role):
        return "result"

    branch = _simple_branch()
    compiled = compile_branch(branch, provider_call=_ok)
    runnable = compiled.graph.compile(checkpointer=InMemorySaver())
    result = runnable.invoke(
        {"x": "test"},
        config={"configurable": {"thread_id": "t2"}},
    )
    assert result.get("out") == "result"


# ── _find_empty_response_exception chain walker ───────────────────────────────


def test_find_empty_response_direct():
    exc = EmptyResponseError("empty", node_id="n1")
    found = _find_empty_response_exception(exc)
    assert found is exc


def test_find_empty_response_via_cause():
    inner = EmptyResponseError("inner", node_id="n2")
    outer = RuntimeError("outer")
    outer.__cause__ = inner
    found = _find_empty_response_exception(outer)
    assert found is inner


def test_find_empty_response_via_context():
    inner = EmptyResponseError("inner", node_id="n3")
    outer = RuntimeError("outer")
    outer.__context__ = inner
    found = _find_empty_response_exception(outer)
    assert found is inner


def test_find_empty_response_returns_none_for_unrelated():
    exc = ValueError("unrelated")
    assert _find_empty_response_exception(exc) is None


def test_find_empty_response_does_not_confuse_timeout():
    exc = NodeTimeoutError("timed out", node_id="n1")
    assert _find_empty_response_exception(exc) is None


# ── timeout path unaffected (regression guard) ────────────────────────────────


def test_find_timeout_exception_unaffected_by_empty_response():
    """_find_timeout_exception must not return EmptyResponseError."""
    exc = EmptyResponseError("empty", node_id="n1")
    assert _find_timeout_exception(exc) is None


def test_find_timeout_exception_still_finds_node_timeout():
    inner = NodeTimeoutError("timeout", node_id="n4")
    outer = RuntimeError("wrapper")
    outer.__cause__ = inner
    found = _find_timeout_exception(outer)
    assert found is inner
