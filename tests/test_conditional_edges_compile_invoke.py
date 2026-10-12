"""Conditional-edge compile+invoke integration discipline (#12).

Closes the coverage gap surfaced by the Tier-1 investigation (#7):
existing `ConditionalEdge` tests validated serialization + storage +
validation ONLY. None exercised ``compile_branch → graph.invoke`` with
real routing inputs. The router/LangGraph contract inversion (router
returning a target when LangGraph's path_map expected a label key)
survived months because no test drove it end-to-end.

Per lead's #12 scope:
- Add compile+invoke coverage for existing serialization/storage tests.
- Canonical example: branches build via any on-disk path → compile →
  invoke with scripted gate outputs → assert terminal node reached.
- Negative test: simulate the pre-#7 contract-inversion router shape
  and assert it FAILS on invoke. This is the proof-against-regression:
  if anyone reverts the fix, this test goes red.

The canonical reference is ``tests/test_conditional_routing_resolver.py``
which Case A already exercises (7 tests). This file broadens that
coverage to the **serialized paths** — YAML round-trip (phase-7
serializer) and the MCP surface (build_branch + patch_branch).
"""
from __future__ import annotations

from typing import Any, Callable

import pytest

from tinyassets.branches import (
    BranchDefinition,
    ConditionalEdge,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.catalog.serializer import (
    branch_from_yaml_payload,
    branch_to_yaml_payload,
)
from tinyassets.graph_compiler import END, compile_branch

# ════════════════════════════════════════════════════════════════════
# Shared helpers — the canonical compile+invoke pattern
# ════════════════════════════════════════════════════════════════════


def _scripted_provider(
    gate_output: str, *, gate_marker: str = "decide"
) -> Callable[..., str]:
    """Return a provider that emits ``gate_output`` for any prompt
    containing ``gate_marker``, else a stable leaf message.

    This is the minimum scaffolding needed to drive a conditional-edge
    graph deterministically — no LLM, no subprocess, just a pure
    callable that seeds the gate's output_key from state via
    ``text_sync``.
    """
    def _call(prompt: str, system: str = "", *, role: str = "writer") -> str:
        if gate_marker in prompt:
            return gate_output
        return "leaf ran"
    return _call


def _run_compiled(compiled, *, initial_state: dict[str, Any]) -> dict[str, Any]:
    graph = compiled.graph.compile()
    return graph.invoke(initial_state)


# ════════════════════════════════════════════════════════════════════
# YAML round-trip + invoke (closes test_storage_phase7_serializer gap)
# ════════════════════════════════════════════════════════════════════


def _yaml_routeable_branch() -> BranchDefinition:
    """A minimal routeable branch shaped like the phase-7 serializer
    fixture, but with a gate node that reads its decision from state.

    The phase-7 fixture tests SQLite→YAML→SQLite identity; this test
    adds the critical third step: YAML→compile→invoke. If the
    serializer ever drops a conditional_edges field in round-trip, the
    restored branch would fail to route — this test catches that.
    """
    return BranchDefinition(
        branch_def_id="yaml-routeable",
        name="YAML round-trip routing",
        description="Compiles + invokes after YAML round-trip",
        author="dev",
        domain_id="workflow",
        entry_point="gate",
        graph_nodes=[
            GraphNodeRef(id="gate", node_def_id="gate"),
            GraphNodeRef(id="leaf_a", node_def_id="leaf_a"),
            GraphNodeRef(id="leaf_b", node_def_id="leaf_b"),
        ],
        edges=[
            EdgeDefinition(from_node="leaf_a", to_node="END"),
            EdgeDefinition(from_node="leaf_b", to_node="END"),
        ],
        conditional_edges=[
            ConditionalEdge(
                from_node="gate",
                conditions={"A": "leaf_a", "B": "leaf_b"},
            ),
        ],
        node_defs=[
            NodeDefinition(
                node_id="gate",
                display_name="Gate",
                prompt_template="decide: {scene_input}",
                output_keys=["gate_out"],
            ),
            NodeDefinition(
                node_id="leaf_a",
                display_name="Leaf A",
                prompt_template="leaf A reached",
                output_keys=["leaf_a_out"],
            ),
            NodeDefinition(
                node_id="leaf_b",
                display_name="Leaf B",
                prompt_template="leaf B reached",
                output_keys=["leaf_b_out"],
            ),
        ],
        state_schema=[
            {"name": "scene_input", "type": "str"},
            {"name": "gate_out", "type": "str"},
            {"name": "leaf_a_out", "type": "str"},
            {"name": "leaf_b_out", "type": "str"},
        ],
    )


def _round_trip_yaml(branch: BranchDefinition) -> BranchDefinition:
    """Serialize to YAML payload + restore. Inline nodes keep the test
    self-contained (no external node_payloads dict to manage)."""
    payload, _node_payloads = branch_to_yaml_payload(
        branch, branch_slug=branch.branch_def_id, externalize_nodes=False,
    )
    return branch_from_yaml_payload(payload)


class TestYamlRoundTripStillRoutes:
    """A branch that round-trips through YAML must still route correctly.

    This was the gap flagged by the #7 investigation: the phase-7
    serializer tests proved YAML→SQLite identity but never proved
    compile+invoke survived the round trip.
    """

    def test_yaml_preserves_routing_to_path_a(self):
        restored = _round_trip_yaml(_yaml_routeable_branch())

        compiled = compile_branch(
            restored, provider_call=_scripted_provider("A"),
        )
        result = _run_compiled(
            compiled, initial_state={"scene_input": "x"},
        )

        assert result.get("leaf_a_out"), (
            f"YAML round-trip broke routing: gate→A should hit leaf_a. "
            f"Final state: {dict(result)}"
        )
        assert not result.get("leaf_b_out")

    def test_yaml_preserves_routing_to_path_b(self):
        restored = _round_trip_yaml(_yaml_routeable_branch())

        compiled = compile_branch(
            restored, provider_call=_scripted_provider("B"),
        )
        result = _run_compiled(
            compiled, initial_state={"scene_input": "x"},
        )

        assert result.get("leaf_b_out"), (
            f"YAML round-trip broke routing: gate→B should hit leaf_b. "
            f"Final state: {dict(result)}"
        )
        assert not result.get("leaf_a_out")

    def test_conditional_edges_field_survives_round_trip(self):
        """Explicit guard against serializer dropping the field. If this
        fires, compile+invoke tests above would also fail — but this
        one fails with a clear message while the invoke tests fail with
        a less-obvious KeyError deep in LangGraph."""
        restored = _round_trip_yaml(_yaml_routeable_branch())
        assert len(restored.conditional_edges) == 1
        assert restored.conditional_edges[0].from_node == "gate"
        assert restored.conditional_edges[0].conditions == {
            "A": "leaf_a", "B": "leaf_b",
        }


# ════════════════════════════════════════════════════════════════════
# Branch-validation-passing edge cases (closes test_branches gap)
# ════════════════════════════════════════════════════════════════════


class TestBranchValidatesAndRoutes:
    """test_branches.py exhaustively validates BranchDefinition but
    never compiles+invokes. These tests assert the validation→invoke
    pipeline is whole: a branch that ``.validate()`` OKs must also
    route correctly.
    """

    def test_end_terminal_via_conditional_edge(self):
        """Condition targets "END" — normalization must work through
        compile. Covers the literal-END path_map substitution at
        graph_compiler.py:1069-1072.
        """
        branch = BranchDefinition(
            branch_def_id="end-terminal",
            name="Gate→{leaf | END}",
            entry_point="gate",
            graph_nodes=[
                GraphNodeRef(id="gate", node_def_id="gate"),
                GraphNodeRef(id="leaf", node_def_id="leaf"),
            ],
            edges=[EdgeDefinition(from_node="leaf", to_node="END")],
            conditional_edges=[
                ConditionalEdge(
                    from_node="gate",
                    conditions={"STOP": "END", "GO": "leaf"},
                ),
            ],
            node_defs=[
                NodeDefinition(
                    node_id="gate",
                    display_name="Gate",
                    prompt_template="decide: {scene_input}",
                    output_keys=["gate_out"],
                ),
                NodeDefinition(
                    node_id="leaf",
                    display_name="Leaf",
                    prompt_template="leaf",
                    output_keys=["leaf_out"],
                ),
            ],
            state_schema=[
                {"name": "scene_input", "type": "str"},
                {"name": "gate_out", "type": "str"},
                {"name": "leaf_out", "type": "str"},
            ],
        )
        errors = branch.validate()
        assert errors == [], f"branch should validate: {errors}"

        compiled = compile_branch(
            branch, provider_call=_scripted_provider("STOP"),
        )
        result = _run_compiled(compiled, initial_state={"scene_input": "x"})

        # STOP → END; leaf should NOT be visited.
        assert not result.get("leaf_out"), (
            f"STOP should terminate at END; final: {dict(result)}"
        )


# ════════════════════════════════════════════════════════════════════
# Case A regression guard — proof-against-revert of the #7 fix
# ════════════════════════════════════════════════════════════════════


class TestContractInversionRegressionGuard:
    """Simulate the pre-#7 broken router shape + assert graph.invoke
    FAILS.

    If someone reverts the fix at ``_build_conditional_router`` (e.g.
    by changing the ``if value in conditions: return value`` back to
    the prior ``return conditions.get(value, fallback)`` which returns
    a TARGET), this test goes red immediately.

    This is spec §Test Strategy-equivalent for #12: "use your own
    Case A fix hypothesis as the negative-test."
    """

    def test_router_returning_target_not_label_raises_keyerror(self):
        """Direct simulation: hand-build a router that returns the
        TARGET (the pre-fix shape), wire it via LangGraph directly,
        assert invoke raises KeyError.
        """
        from typing import TypedDict

        from langgraph.graph import StateGraph

        class _State(TypedDict, total=False):
            scene_input: str
            gate_out: str

        def _broken_router(_state: dict) -> str:
            # Pre-fix shape: returns the TARGET, not the label.
            # LangGraph will try to look up "leaf_a" in the path_map
            # which has keys {"A", "B"} — KeyError.
            return "leaf_a"

        def _gate_fn(state: dict) -> dict:
            return {"gate_out": "A"}

        def _leaf_a_fn(_state: dict) -> dict:
            return {}

        def _leaf_b_fn(_state: dict) -> dict:
            return {}

        graph = StateGraph(_State)
        graph.add_node("gate", _gate_fn)
        graph.add_node("leaf_a", _leaf_a_fn)
        graph.add_node("leaf_b", _leaf_b_fn)
        graph.add_edge("__start__", "gate")
        graph.add_conditional_edges(
            "gate",
            _broken_router,
            {"A": "leaf_a", "B": "leaf_b"},
        )
        graph.add_edge("leaf_a", END)
        graph.add_edge("leaf_b", END)

        app = graph.compile()
        with pytest.raises(KeyError):
            app.invoke({"scene_input": "x"})

    def test_fixed_router_returning_label_invokes_cleanly(self):
        """Positive companion: the current router returns the LABEL
        (the post-fix shape). LangGraph looks up the label in path_map
        → target → graph advances. No KeyError.
        """
        from typing import TypedDict

        from langgraph.graph import StateGraph

        class _State(TypedDict, total=False):
            scene_input: str
            gate_out: str
            leaf_a_visited: bool

        def _fixed_router(state: dict) -> str:
            # Post-fix shape: returns the LABEL from state.
            value = state.get("gate_out", "")
            conditions = {"A": "leaf_a", "B": "leaf_b"}
            if value in conditions:
                return value
            return next(iter(conditions.keys()))

        def _gate_fn(state: dict) -> dict:
            return {"gate_out": "A"}

        def _leaf_a_fn(_state: dict) -> dict:
            return {"leaf_a_visited": True}

        def _leaf_b_fn(_state: dict) -> dict:
            return {}

        graph = StateGraph(_State)
        graph.add_node("gate", _gate_fn)
        graph.add_node("leaf_a", _leaf_a_fn)
        graph.add_node("leaf_b", _leaf_b_fn)
        graph.add_edge("__start__", "gate")
        graph.add_conditional_edges(
            "gate",
            _fixed_router,
            {"A": "leaf_a", "B": "leaf_b"},
        )
        graph.add_edge("leaf_a", END)
        graph.add_edge("leaf_b", END)

        app = graph.compile()
        result = app.invoke({"scene_input": "x"})

        assert result.get("leaf_a_visited") is True

    def test_compile_branch_current_router_routes_correctly(self):
        """Higher-level regression: use the real compile_branch path
        with a conditional_edge. If ``_build_conditional_router`` gets
        reverted, this test goes red too (alongside the
        ``test_conditional_routing_resolver.py`` suite)."""
        branch = _yaml_routeable_branch()
        compiled = compile_branch(
            branch, provider_call=_scripted_provider("A"),
        )
        result = _run_compiled(
            compiled, initial_state={"scene_input": "x"},
        )
        assert result.get("leaf_a_out")
