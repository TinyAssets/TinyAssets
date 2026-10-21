"""BUG-038 / BUG-041 terminal phase="failed" event emission tests.

When a node's provider call raises, graph_compiler must emit
``event_sink(phase="failed", ...)`` BEFORE re-raising as ``CompilerError``.
Without it, the most recent event for the node is the earlier
``phase="starting"`` row, and ``build_node_status_map`` keeps the node
parked at NODE_STATUS_RUNNING even after the run flips to RUN_STATUS_FAILED
- the contradictory shape both bug pages report.

Two contracts under test:
1. The compiler-side emission: phase="failed" fires from BOTH the policy-
   aware exception handler AND the plain provider_call handler in
   tinyassets/graph_compiler.py.
2. The runs.py-side translator: phase="failed" maps to NODE_STATUS_FAILED
   in both _on_node handlers, so build_node_status_map flips the node
   to "failed" terminally.
"""

from __future__ import annotations


from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)


def _simple_branch() -> BranchDefinition:
    b = BranchDefinition(name="failed-event-probe", entry_point="step1")
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


# Compiler-side emission














# runs.py translator + build_node_status_map contract
















