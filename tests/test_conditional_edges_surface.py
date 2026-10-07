"""Tests for exposing `conditional_edges` on build_branch + patch_branch.

STATUS.md Approved-bugs 2026-04-22 — the MCP surface now reads and
patches `conditional_edges`, closing the half-built feature. Schema
and validator already existed; this wires the ingress + patch ops.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from tinyassets.branches import BranchDefinition
from tinyassets.graph_compiler import compile_branch


@pytest.fixture
def branch_env(tmp_path, monkeypatch, authenticate_request):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # Branch mutation requires a credential-derived subject. Without one the
    # extensions surface returns
    # `{"error": "Authenticated branch subject required."}` and these tests
    # die before reaching their own concern. The conftest default grants
    # extensions read/write/admin; `extensions.costly` is deliberately NOT
    # granted, so a costly-refusal test would still assert something.
    authenticate_request("tester")

    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, Path(tmp_path)
    importlib.reload(us)


def _call(us, action, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


def _conditional_edges(got: dict) -> list:
    """Pull conditional_edges out of a get_branch response.

    Storage nests them under ``graph.conditional_edges`` (graph_json in
    sqlite) — the reader does not surface them at top level.
    """
    return got.get("graph", {}).get("conditional_edges", [])


def _scripted_provider(gate_output: str) -> Callable[..., str]:
    def _call(prompt: str, system: str = "", *, role: str = "writer") -> str:
        if "decide" in prompt:
            return gate_output
        return "leaf ran"

    return _call


def _compile_and_invoke_stored_branch(
    branch_payload: dict[str, Any],
    *,
    gate_output: str,
) -> dict[str, Any]:
    branch = BranchDefinition.from_dict(branch_payload)
    compiled = compile_branch(
        branch,
        provider_call=_scripted_provider(gate_output),
    )
    return compiled.graph.compile().invoke({"scene_input": "x"})


def _three_node_spec() -> dict:
    """Baseline valid 2-node branch (router→leaf→END). Tests that
    exercise patch ops mutate this baseline — they add ``right``
    first via add_node before the conditional edge patch."""
    return {
        "name": "Router",
        "node_defs": [
            {"node_id": "router", "display_name": "Router",
             "prompt_template": "decide: {scene_input}",
             "output_keys": ["route"]},
            {"node_id": "left", "display_name": "Left",
             "prompt_template": "L",
             "output_keys": ["left_out"]},
        ],
        "edges": [
            {"from": "router", "to": "left"},
            {"from": "left", "to": "END"},
        ],
        "state_schema": [
            {"name": "scene_input", "type": "str"},
            {"name": "route", "type": "str"},
            {"name": "left_out", "type": "str"},
        ],
        "entry_point": "router",
    }


def _router_spec_no_regular_edges() -> dict:
    """Spec for tests that supply conditional_edges in the build itself."""
    return {
        "name": "Router",
        "node_defs": [
            {"node_id": "router", "display_name": "Router",
             "prompt_template": "decide: {scene_input}",
             "output_keys": ["route"]},
            {"node_id": "left", "display_name": "Left",
             "prompt_template": "L",
             "output_keys": ["left_out"]},
            {"node_id": "right", "display_name": "Right",
             "prompt_template": "R",
             "output_keys": ["right_out"]},
        ],
        "edges": [
            {"from": "left", "to": "END"},
            {"from": "right", "to": "END"},
        ],
        "state_schema": [
            {"name": "scene_input", "type": "str"},
            {"name": "route", "type": "str"},
            {"name": "left_out", "type": "str"},
            {"name": "right_out", "type": "str"},
        ],
        "entry_point": "router",
    }


def test_build_branch_accepts_conditional_edges(branch_env):
    us, _ = branch_env
    spec = _router_spec_no_regular_edges()
    spec["conditional_edges"] = [
        {"from": "router", "conditions": {"a": "left", "b": "right"}},
    ]
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "built", result
    bid = result["branch_def_id"]

    got = _call(us, "get_branch", branch_def_id=bid)
    assert _conditional_edges(got) == [
        {"from": "router", "conditions": {"a": "left", "b": "right"}}
    ]
    invoked = _compile_and_invoke_stored_branch(got, gate_output="b")
    assert invoked.get("right_out")
    assert not invoked.get("left_out")


def test_patch_branch_add_conditional_edge_appends(branch_env):
    us, _ = branch_env
    result = _call(
        us, "build_branch", spec_json=json.dumps(_three_node_spec()),
    )
    assert result["status"] == "built", result
    bid = result["branch_def_id"]

    # Replace the original linear router->left edge with conditional
    # branching in one transaction.
    patch_result = _call(
        us, "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([
            {"op": "add_node", "node_id": "right", "display_name": "Right",
             "prompt_template": "R",
             "output_keys": ["right_out"]},
            {"op": "add_edge", "from": "right", "to": "END"},
            {"op": "add_state_field", "name": "right_out", "type": "str"},
            {"op": "remove_edge", "from": "router", "to": "left"},
            {"op": "add_conditional_edge",
             "from": "router",
             "conditions": {"a": "left", "b": "right"}},
        ]),
    )
    assert patch_result.get("status") == "patched", patch_result

    got = _call(us, "get_branch", branch_def_id=bid)
    assert _conditional_edges(got) == [
        {"from": "router", "conditions": {"a": "left", "b": "right"}}
    ]
    invoked = _compile_and_invoke_stored_branch(got, gate_output="b")
    assert invoked.get("right_out")
    assert not invoked.get("left_out")


def test_patch_remove_conditional_edge_with_outcome_removes_mapping(branch_env):
    us, _ = branch_env
    # Two outcomes route to the same leaf so removing one keeps the
    # graph reachable (the other still points at ``leaf``).
    spec = {
        "name": "Router",
        "node_defs": [
            {"node_id": "router", "display_name": "Router",
             "prompt_template": "decide"},
            {"node_id": "leaf", "display_name": "Leaf",
             "prompt_template": "L"},
        ],
        "edges": [{"from": "leaf", "to": "END"}],
        "conditional_edges": [
            {"from": "router", "conditions": {"a": "leaf", "b": "leaf"}},
        ],
        "entry_point": "router",
    }
    built = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert built["status"] == "built", built
    bid = built["branch_def_id"]

    patch_result = _call(
        us, "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "remove_conditional_edge",
            "from": "router",
            "outcome": "a",
        }]),
    )
    assert patch_result.get("status") == "patched", patch_result

    got = _call(us, "get_branch", branch_def_id=bid)
    assert _conditional_edges(got) == [
        {"from": "router", "conditions": {"b": "leaf"}}
    ]


def test_patch_remove_conditional_edge_without_outcome_removes_entire_edge(
    branch_env,
):
    us, _ = branch_env
    # Keep a regular router→leaf edge so the graph stays valid after
    # the conditional edge is wiped entirely.
    spec = {
        "name": "Router",
        "node_defs": [
            {"node_id": "router", "display_name": "Router",
             "prompt_template": "decide"},
            {"node_id": "leaf", "display_name": "Leaf",
             "prompt_template": "L"},
        ],
        "edges": [
            {"from": "router", "to": "leaf"},
            {"from": "leaf", "to": "END"},
        ],
        "conditional_edges": [
            {"from": "router", "conditions": {"a": "leaf"}},
        ],
        "entry_point": "router",
    }
    built = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert built["status"] == "built", built
    bid = built["branch_def_id"]

    patch_result = _call(
        us, "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "remove_conditional_edge",
            "from": "router",
        }]),
    )
    assert patch_result.get("status") == "patched", patch_result

    got = _call(us, "get_branch", branch_def_id=bid)
    assert _conditional_edges(got) == []


def test_validation_rejects_conditional_edge_referencing_nonexistent_node(
    branch_env,
):
    us, _ = branch_env
    spec = _router_spec_no_regular_edges()
    spec["conditional_edges"] = [
        {"from": "router", "conditions": {"a": "ghost_target"}},
    ]
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    # build_branch runs validation post-apply and rejects.
    assert result["status"] == "rejected"
    errors = result.get("errors") or []
    assert any("ghost_target" in e for e in errors), errors


def test_patch_rejects_empty_conditions_dict(branch_env):
    us, _ = branch_env
    built = _call(us, "build_branch",
                  spec_json=json.dumps(_three_node_spec()))
    bid = built["branch_def_id"]

    result = _call(
        us, "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "add_conditional_edge",
            "from": "router",
            "conditions": {},
        }]),
    )
    assert result.get("status") != "patched"
    assert "conditions" in json.dumps(result).lower()


def test_add_conditional_edge_twice_merges_outcomes(branch_env):
    """Adding outcomes one at a time merges onto the existing edge.

    Callers often add outcomes incrementally. We keep one
    ConditionalEdge per `from` node and merge new outcomes in, so the
    stored shape mirrors LangGraph's routing semantics (one router → one
    edge bundle). To keep each intermediate state valid we route both
    outcomes to the same leaf, then add a third outcome to the same
    leaf in the second patch.
    """
    us, _ = branch_env
    bid = _call(us, "build_branch",
                spec_json=json.dumps(_three_node_spec()))["branch_def_id"]

    first = _call(
        us, "patch_branch", branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "add_conditional_edge",
            "from": "router",
            "conditions": {"a": "left"},
        }]),
    )
    assert first.get("status") == "patched", first
    second = _call(
        us, "patch_branch", branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "add_conditional_edge",
            "from": "router",
            "conditions": {"b": "left"},
        }]),
    )
    assert second.get("status") == "patched", second

    got = _call(us, "get_branch", branch_def_id=bid)
    assert _conditional_edges(got) == [
        {"from": "router", "conditions": {"a": "left", "b": "left"}}
    ]


def test_remove_conditional_edge_missing_outcome_errors(branch_env):
    us, _ = branch_env
    spec = {
        "name": "Router",
        "node_defs": [
            {"node_id": "router", "display_name": "Router",
             "prompt_template": "decide"},
            {"node_id": "leaf", "display_name": "Leaf",
             "prompt_template": "L"},
        ],
        "edges": [{"from": "leaf", "to": "END"}],
        "conditional_edges": [
            {"from": "router", "conditions": {"a": "leaf"}},
        ],
        "entry_point": "router",
    }
    built = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert built["status"] == "built", built
    bid = built["branch_def_id"]

    result = _call(
        us, "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "remove_conditional_edge",
            "from": "router",
            "outcome": "does_not_exist",
        }]),
    )
    assert result.get("status") != "patched"
    assert "does_not_exist" in json.dumps(result)


def test_remove_conditional_edge_missing_from_errors(branch_env):
    us, _ = branch_env
    bid = _call(us, "build_branch",
                spec_json=json.dumps(_three_node_spec()))["branch_def_id"]

    result = _call(
        us, "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "remove_conditional_edge",
            "from": "router",
        }]),
    )
    assert result.get("status") != "patched"


def test_self_loop_is_accepted_as_the_retry_primitive(branch_env):
    """Self-loop happy path. Conditional edges are the loop/gate
    primitive — a node routing back to itself is valid, not a reject.
    Navigator re-tightened spec 2026-04-22 pinning this behavior."""
    us, _ = branch_env
    spec = {
        "name": "Retry",
        "node_defs": [
            {"node_id": "N1", "display_name": "N1",
             "prompt_template": "attempt"},
        ],
        "conditional_edges": [
            {"from": "N1", "conditions": {"retry": "N1", "done": "END"}},
        ],
        "entry_point": "N1",
    }
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "built", result
    bid = result["branch_def_id"]

    got = _call(us, "get_branch", branch_def_id=bid)
    assert _conditional_edges(got) == [
        {"from": "N1", "conditions": {"retry": "N1", "done": "END"}}
    ]


def test_cycle_via_conditional_edge_is_accepted(branch_env):
    """Cycles through conditional edges are the loop primitive too —
    A→B→(conditional back to A or forward to END) must validate."""
    us, _ = branch_env
    spec = {
        "name": "Loop",
        "node_defs": [
            {"node_id": "A", "display_name": "A", "prompt_template": "a"},
            {"node_id": "B", "display_name": "B", "prompt_template": "b"},
        ],
        "edges": [{"from": "A", "to": "B"}],
        "conditional_edges": [
            {"from": "B", "conditions": {"loop": "A", "done": "END"}},
        ],
        "entry_point": "A",
    }
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "built", result
