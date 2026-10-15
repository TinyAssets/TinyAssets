"""Regression coverage for the single-writer ``merge`` reducer contract."""

from __future__ import annotations

import random

import pytest
from langgraph.graph import END, START, StateGraph

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import (
    CompilerError,
    _build_state_typeddict,
    compile_branch,
)


@pytest.fixture
def center(tmp_path, monkeypatch):
    """A command center whose owner's box session is bound for the test.

    Code nodes run only in the admitted owner box of the center they belong to.
    """
    from tinyassets.control_plane import binding as binding_module

    data_root = tmp_path / "data"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
    cc = data_root / "cc-reducer"
    cc.mkdir(parents=True)
    with binding_module.use_binding(binding_module.file_binding(cc)):
        yield cc


_UPDATES = (
    {"shared": "alpha", "alpha_only": 1},
    {"shared": "omega", "omega_only": 2},
)


def _raw_fan_in(updates: list[dict[str, object]]) -> dict[str, object]:
    """Apply concurrent updates through the production LangGraph state type."""
    state_type = _build_state_typeddict(
        [{"name": "facts", "type": "dict", "reducer": "merge"}],
    )
    graph = StateGraph(state_type)
    for index, update in enumerate(updates):
        node_id = f"writer_{index}"
        graph.add_node(
            node_id,
            lambda _state, update=update: {"facts": update},
        )
        graph.add_edge(START, node_id)
        graph.add_edge(node_id, END)
    return graph.compile().invoke({})["facts"]


def _source_node(node_id: str, update: dict[str, object]) -> NodeDefinition:
    source = f"def run(state):\n    return {{'facts': {update!r}}}"
    return NodeDefinition(
        node_id=node_id,
        display_name=node_id,
        source_code=source,
        output_keys=["facts"],
    ).mark_approved()


def _branch_with_merge_writers(
    updates: list[dict[str, object]],
) -> BranchDefinition:
    nodes = [
        _source_node(f"writer_{index}", update)
        for index, update in enumerate(updates)
    ]
    return BranchDefinition(
        name="merge fan-in",
        entry_point="writer_0",
        node_defs=nodes,
        graph_nodes=[
            GraphNodeRef(id=node.node_id, node_def_id=node.node_id)
            for node in nodes
        ],
        edges=[
            EdgeDefinition(from_node="START", to_node=node.node_id)
            for node in nodes
        ] + [
            EdgeDefinition(from_node=node.node_id, to_node="END")
            for node in nodes
        ],
        state_schema=[
            {"name": "facts", "type": "dict", "reducer": "merge"},
        ],
    )


def test_merge_reducer_rejects_nonconvergent_fan_in() -> None:
    forward = _raw_fan_in(list(_UPDATES))
    reverse = _raw_fan_in(list(reversed(_UPDATES)))
    assert forward != reverse, "the regression setup must expose order dependence"

    try:
        compile_branch(_branch_with_merge_writers(list(_UPDATES)))
    except CompilerError as exc:
        assert "single writer" in str(exc)
    else:
        pytest.fail(
            "non-convergent merge fan-in was accepted: "
            f"forward={forward!r}, reverse={reverse!r}",
        )


def test_single_merge_writer_converges_across_randomized_graph_order(center) -> None:
    rng = random.Random(20260610)
    outcomes: list[dict[str, object]] = []

    for _ in range(32):
        nodes = [
            NodeDefinition(
                node_id="merge_writer",
                display_name="merge_writer",
                source_code=(
                    "def run(state):\n"
                    "    return {'facts': {'shared': 'stable', 'only': 1}}"
                ),
                output_keys=["facts"],
            ).mark_approved(),
            NodeDefinition(
                node_id="other_writer",
                display_name="other_writer",
                source_code="def run(state):\n    return {'note': 'ready'}",
                output_keys=["note"],
            ).mark_approved(),
        ]
        rng.shuffle(nodes)
        graph_nodes = [
            GraphNodeRef(id=node.node_id, node_def_id=node.node_id)
            for node in nodes
        ]
        edges = [
            EdgeDefinition(from_node="START", to_node="merge_writer"),
            EdgeDefinition(from_node="START", to_node="other_writer"),
            EdgeDefinition(from_node="merge_writer", to_node="END"),
            EdgeDefinition(from_node="other_writer", to_node="END"),
        ]
        rng.shuffle(edges)
        branch = BranchDefinition(
            name="single merge writer",
            entry_point="merge_writer",
            node_defs=nodes,
            graph_nodes=graph_nodes,
            edges=edges,
            state_schema=[
                {"name": "facts", "type": "dict", "reducer": "merge"},
                {"name": "note", "type": "str"},
            ],
        )

        outcomes.append(
            compile_branch(branch, base_path=center).graph.compile().invoke({})["facts"],
        )

    assert outcomes == [{"shared": "stable", "only": 1}] * 32


def test_merge_reducer_rejects_an_undeclared_second_writer_at_runtime(center) -> None:
    declared_writer = _source_node("declared_writer", _UPDATES[0])
    undeclared_writer = NodeDefinition(
        node_id="undeclared_writer",
        display_name="undeclared_writer",
        source_code=(
            "def run(state):\n"
            f"    return {{'facts': {_UPDATES[1]!r}}}"
        ),
        output_keys=["note"],
    ).mark_approved()
    branch = BranchDefinition(
        name="undeclared merge writer",
        entry_point="declared_writer",
        node_defs=[declared_writer, undeclared_writer],
        graph_nodes=[
            GraphNodeRef(id=node.node_id, node_def_id=node.node_id)
            for node in (declared_writer, undeclared_writer)
        ],
        edges=[
            EdgeDefinition(from_node="START", to_node="declared_writer"),
            EdgeDefinition(from_node="START", to_node="undeclared_writer"),
            EdgeDefinition(from_node="declared_writer", to_node="END"),
            EdgeDefinition(from_node="undeclared_writer", to_node="END"),
        ],
        state_schema=[
            {"name": "facts", "type": "dict", "reducer": "merge"},
            {"name": "note", "type": "str"},
        ],
    )

    app = compile_branch(branch, base_path=center).graph.compile()
    with pytest.raises(CompilerError, match="without declaring it"):
        app.invoke({})
