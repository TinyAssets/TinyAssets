"""Node checkpoints — partial-credit boundaries on NodeDefinition.

Tests for the checkpoint spec: declarative reached_when predicates,
event emission, idempotency (fires at most once per run), validation,
and the checkpoint() helper for code nodes.

Spec: openspec/specs/community-patch-loop/spec.md §Node checkpoints
"""
from __future__ import annotations

from typing import Any

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import compile_branch

# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def _mk_node(
    node_id: str, output_val: str = "done", checkpoints: list | None = None,
) -> NodeDefinition:
    return NodeDefinition(
        node_id=node_id,
        display_name=node_id,
        prompt_template=f"run {node_id}: {{scene_input}}",
        output_keys=[f"{node_id}_out"],
        checkpoints=checkpoints or [],
    )


def _static_provider(val: str):
    def _call(prompt: str, system: str = "", *, role: str = "writer") -> str:
        return val
    return _call


def _single_node_branch(node: NodeDefinition, extra_state: list | None = None) -> BranchDefinition:
    schema = [
        {"name": "scene_input", "type": "str"},
        {"name": f"{node.node_id}_out", "type": "str"},
    ]
    if extra_state:
        schema.extend(extra_state)
    return BranchDefinition(
        branch_def_id="test",
        name="Test",
        node_defs=[node],
        graph_nodes=[GraphNodeRef(id=node.node_id, node_def_id=node.node_id)],
        edges=[EdgeDefinition(from_node=node.node_id, to_node="END")],
        entry_point=node.node_id,
        state_schema=schema,
    )


# ════════════════════════════════════════════════════════════════════
# Validation tests
# ════════════════════════════════════════════════════════════════════


class TestCheckpointValidation:
    def test_no_checkpoints_validates_ok(self):
        node = _mk_node("n")
        branch = _single_node_branch(node)
        assert branch.validate() == []

    def test_single_half_checkpoint_validates_ok(self):
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "half",
            "earns_fraction": 0.5,
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        assert branch.validate() == []

    def test_two_half_checkpoints_validate_ok(self):
        node = _mk_node("n", checkpoints=[
            {"checkpoint_id": "first", "earns_fraction": 0.5,
             "reached_when": {"state_key": "n_out"}},
            {"checkpoint_id": "second", "earns_fraction": 0.5,
             "reached_when": {"state_key": "n_out"}},
        ])
        branch = _single_node_branch(node)
        assert branch.validate() == []

    def test_cumulative_over_1_rejected(self):
        node = _mk_node("n", checkpoints=[
            {"checkpoint_id": "a", "earns_fraction": 0.7,
             "reached_when": {"state_key": "n_out"}},
            {"checkpoint_id": "b", "earns_fraction": 0.7,
             "reached_when": {"state_key": "n_out"}},
        ])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("cumulative" in e for e in errors), errors

    def test_duplicate_checkpoint_id_rejected(self):
        node = _mk_node("n", checkpoints=[
            {"checkpoint_id": "dup", "earns_fraction": 0.3,
             "reached_when": {"state_key": "n_out"}},
            {"checkpoint_id": "dup", "earns_fraction": 0.3,
             "reached_when": {"state_key": "n_out"}},
        ])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("duplicate" in e.lower() for e in errors), errors

    def test_missing_checkpoint_id_rejected(self):
        node = _mk_node("n", checkpoints=[{
            "earns_fraction": 0.5,
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("checkpoint_id" in e for e in errors), errors

    def test_missing_earns_fraction_rejected(self):
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "c",
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("earns_fraction" in e for e in errors), errors

    def test_missing_reached_when_rejected(self):
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "c",
            "earns_fraction": 0.5,
        }])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("reached_when" in e for e in errors), errors

    def test_reached_when_missing_state_key_rejected(self):
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "c",
            "earns_fraction": 0.5,
            "reached_when": {"value": "something"},
        }])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("state_key" in e for e in errors), errors

    def test_three_part_checkpoints_034034040_sum_108_rejected(self):
        """0.34 + 0.34 + 0.40 = 1.08 — rejected."""
        node = _mk_node("n", checkpoints=[
            {"checkpoint_id": "a", "earns_fraction": 0.34,
             "reached_when": {"state_key": "n_out"}},
            {"checkpoint_id": "b", "earns_fraction": 0.34,
             "reached_when": {"state_key": "n_out"}},
            {"checkpoint_id": "c", "earns_fraction": 0.40,
             "reached_when": {"state_key": "n_out"}},
        ])
        branch = _single_node_branch(node)
        errors = branch.validate()
        assert any("cumulative" in e for e in errors), errors


# ════════════════════════════════════════════════════════════════════
# Runtime checkpoint firing tests
# ════════════════════════════════════════════════════════════════════


class TestCheckpointFiring:
    def _compile_and_run(self, branch: BranchDefinition, events: list) -> dict:
        def event_sink(**kwargs: Any) -> None:
            events.append(kwargs)

        compiled = compile_branch(
            branch,
            provider_call=_static_provider("result text"),
            event_sink=event_sink,
        )
        graph = compiled.graph.compile()
        return graph.invoke({"scene_input": "x"})

    def test_no_checkpoints_no_checkpoint_events(self):
        node = _mk_node("n")
        branch = _single_node_branch(node)
        events: list = []
        result = self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert ckpt_events == []
        assert result.get("n_out")

    def test_checkpoint_fires_when_key_present(self):
        """Checkpoint with exists=true fires when output key is set."""
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "done",
            "earns_fraction": 1.0,
            "reached_when": {"state_key": "n_out", "exists": True},
        }])
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)

        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert len(ckpt_events) == 1
        assert ckpt_events[0]["checkpoint_id"] == "done"
        assert ckpt_events[0]["earns_fraction"] == 1.0

    def test_checkpoint_fires_on_default_key_presence(self):
        """Checkpoint with only state_key (no value/exists) fires on key presence."""
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "done",
            "earns_fraction": 0.5,
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert len(ckpt_events) == 1

    def test_checkpoint_fires_on_value_match(self):
        """Checkpoint with value= fires only when state[key] == value."""
        node = NodeDefinition(
            node_id="n",
            display_name="n",
            prompt_template="run: {scene_input}",
            output_keys=["n_out"],
            checkpoints=[{
                "checkpoint_id": "exact",
                "earns_fraction": 0.5,
                "reached_when": {"state_key": "n_out", "value": "result text"},
            }],
        )
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert len(ckpt_events) == 1

    def test_checkpoint_does_not_fire_on_value_mismatch(self):
        """Checkpoint with value= does NOT fire when state[key] != value."""
        node = NodeDefinition(
            node_id="n",
            display_name="n",
            prompt_template="run: {scene_input}",
            output_keys=["n_out"],
            checkpoints=[{
                "checkpoint_id": "wrong",
                "earns_fraction": 0.5,
                "reached_when": {"state_key": "n_out", "value": "NEVER_THIS"},
            }],
        )
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert len(ckpt_events) == 0

    def test_two_checkpoints_both_fire_on_completion(self):
        """Node with two 0.5 checkpoints — both fire when both predicates match."""
        node = NodeDefinition(
            node_id="n",
            display_name="n",
            prompt_template="run: {scene_input}",
            output_keys=["n_out"],
            checkpoints=[
                {"checkpoint_id": "first", "earns_fraction": 0.5,
                 "reached_when": {"state_key": "n_out"}},
                {"checkpoint_id": "second", "earns_fraction": 0.5,
                 "reached_when": {"state_key": "n_out"}},
            ],
        )
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        fired_ids = {e["checkpoint_id"] for e in ckpt_events}
        assert fired_ids == {"first", "second"}
        total_earned = sum(e["earns_fraction"] for e in ckpt_events)
        assert abs(total_earned - 1.0) < 1e-9

    def test_fired_checkpoints_accumulated_in_state(self):
        """_fired_checkpoints is present in final state after checkpoint fires."""
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "done",
            "earns_fraction": 1.0,
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        events: list = []
        result = self._compile_and_run(branch, events)
        assert "done" in (result.get("_fired_checkpoints") or [])

    def test_checkpoint_not_fired_when_key_absent(self):
        """Checkpoint on a key the node doesn't write doesn't fire."""
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "ghost",
            "earns_fraction": 0.5,
            "reached_when": {"state_key": "nonexistent_key"},
        }])
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert ckpt_events == []

    def test_checkpoint_earns_fraction_correct_in_event(self):
        """earns_fraction from the checkpoint def is echoed in the event."""
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "partial",
            "earns_fraction": 0.3,
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        events: list = []
        self._compile_and_run(branch, events)
        ckpt_events = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert len(ckpt_events) == 1
        assert ckpt_events[0]["earns_fraction"] == 0.3


# ════════════════════════════════════════════════════════════════════
# Idempotency / resume simulation
# ════════════════════════════════════════════════════════════════════


class TestCheckpointIdempotency:
    def test_checkpoint_does_not_fire_twice_when_already_in_state(self):
        """Simulates resume: already-fired checkpoint in incoming state must not re-fire."""
        node = _mk_node("n", checkpoints=[{
            "checkpoint_id": "done",
            "earns_fraction": 1.0,
            "reached_when": {"state_key": "n_out"},
        }])
        branch = _single_node_branch(node)
        events: list = []

        def event_sink(**kwargs: Any) -> None:
            events.append(kwargs)

        compiled = compile_branch(
            branch,
            provider_call=_static_provider("result"),
            event_sink=event_sink,
        )
        graph = compiled.graph.compile()

        # First run — checkpoint should fire.
        result1 = graph.invoke({"scene_input": "x"})
        events_first = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert len(events_first) == 1

        # Second invocation with fired checkpoint pre-loaded in state.
        events.clear()
        graph.invoke({
            "scene_input": "y",
            "_fired_checkpoints": list(result1.get("_fired_checkpoints") or []),
        })
        events_second = [e for e in events if e.get("phase") == "checkpoint_reached"]
        assert events_second == [], (
            "checkpoint should not re-fire when already in _fired_checkpoints"
        )


# ════════════════════════════════════════════════════════════════════
# checkpoint() helper for code nodes
# ════════════════════════════════════════════════════════════════════


class TestCheckpointHelper:
    def test_checkpoint_helper_returns_marker_dict(self):
        from tinyassets.idempotency import checkpoint as ckpt_helper
        result = ckpt_helper("my_id", state={})
        assert "__checkpoint__" in result
        assert "my_id" in result["__checkpoint__"]

    def test_checkpoint_helper_accumulates_multiple_calls(self):
        from tinyassets.idempotency import checkpoint as ckpt_helper
        d1 = ckpt_helper("a", state={})
        d2 = ckpt_helper("b", state={**d1})
        assert set(d2["__checkpoint__"]) == {"a", "b"}

    def test_checkpoint_helper_does_not_duplicate(self):
        from tinyassets.idempotency import checkpoint as ckpt_helper
        state = {"__checkpoint__": ["already"]}
        d = ckpt_helper("new_one", state=state)
        assert "already" in d["__checkpoint__"]
        assert "new_one" in d["__checkpoint__"]


# ════════════════════════════════════════════════════════════════════
# All-or-nothing backward compat
# ════════════════════════════════════════════════════════════════════


class TestBackwardCompat:
    def test_branch_without_checkpoints_compiles_and_runs(self):
        """No regression for branches with no checkpoints."""
        branch = BranchDefinition(
            branch_def_id="compat",
            name="Compat",
            node_defs=[NodeDefinition(
                node_id="n",
                display_name="n",
                prompt_template="hello: {x}",
                output_keys=["out"],
            )],
            graph_nodes=[GraphNodeRef(id="n", node_def_id="n")],
            edges=[EdgeDefinition(from_node="n", to_node="END")],
            entry_point="n",
            state_schema=[
                {"name": "x", "type": "str"},
                {"name": "out", "type": "str"},
            ],
        )
        assert branch.validate() == []
        compiled = compile_branch(branch, provider_call=_static_provider("hi"))
        result = compiled.graph.compile().invoke({"x": "world"})
        assert result.get("out")
        assert "_fired_checkpoints" not in result
