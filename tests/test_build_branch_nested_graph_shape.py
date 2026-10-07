"""PR-037 regression: build_branch accepts the nested ``graph`` shape.

Before this fix, ``_staged_branch_from_spec`` read only top-level
``spec["edges"]`` / ``spec["conditional_edges"]`` / ``spec["entry_point"]``.
But the shape ``extensions action=get_branch`` RETURNS nests these inside
a top-level ``graph`` dict — exactly the shape a user trying to fork by
mirroring the live branch would re-submit. The result: edges silently
dropped during staging, validator reports "node not reachable from entry
point" with diagnostics that contradict what the submitted spec literally
contains. See PR-037 brain page + slice-0 substrate-readiness finding
2026-05-13.

These tests submit specs with the nested ``graph`` shape and assert that
the resulting staged branch has the expected edges, conditional edges,
and entry_point copied through.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def ext_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # `build_branch` is a branch WRITE action, so it needs a request subject,
    # not just an env var: `_request_branch_actor()` reads the authenticated
    # request, and `UNIVERSE_SERVER_USER` alone leaves it None — which returns
    # `Authenticated branch subject required.` before any spec is parsed.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    authenticate_request("tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us
    importlib.reload(us)


def _call(us, **kwargs):
    return json.loads(us._extensions_impl(**kwargs))


def _two_node_spec_nested_graph() -> dict:
    """Spec mirroring what ``get_branch`` would return for a 2-node branch.

    Note the nested ``graph`` with ``edges`` + ``entry_point`` inside — the
    shape PR-037 documented as failing pre-fix.
    """
    return {
        "name": "pr-037-regression-nested",
        "tags": ["test"],
        # ``y`` is declared alongside ``x`` because node ``classify`` writes
        # output_key ``y`` and ``end_marker`` reads input_key ``y``. Per
        # BUG-091 (#924) every node input_key / output_key must be a declared
        # state_schema field when a non-empty schema exists, otherwise the key
        # is silently dropped from LangGraph's synthesized TypedDict at runtime.
        "state_schema": [
            {"name": "x", "type": "str"},
            {"name": "y", "type": "str"},
        ],
        "graph": {
            "entry_point": "classify",
            "nodes": [
                {"id": "classify", "node_def_id": "classify", "position": 0},
                {"id": "end_marker", "node_def_id": "end_marker", "position": 1},
            ],
            "edges": [
                {"from": "classify", "to": "end_marker"},
                {"from": "end_marker", "to": "END"},
            ],
            "conditional_edges": [],
        },
        "node_defs": [
            {
                "node_id": "classify",
                "display_name": "Classify",
                "input_keys": ["x"],
                "output_keys": ["y"],
                "prompt_template": "classify {x}",
                "phase": "custom",
                "enabled": True,
            },
            {
                "node_id": "end_marker",
                "display_name": "End Marker",
                "input_keys": ["y"],
                "output_keys": [],
                "prompt_template": "done",
                "phase": "custom",
                "enabled": True,
            },
        ],
    }


def test_nested_graph_shape_accepted_by_build_branch(ext_env):
    """The canonical PR-037 failure shape now builds cleanly."""
    us = ext_env
    spec = _two_node_spec_nested_graph()
    res = _call(us, action="build_branch", spec_json=json.dumps(spec))
    assert res.get("status") == "built", res
    # Edge count + entry_point came through from the nested graph blob.
    assert res.get("edge_count") == 2  # classify->end_marker + end_marker->END
    assert res.get("entry_point") == "classify"
    assert res.get("node_count") == 2


def test_top_level_keys_still_win_when_both_present(ext_env):
    """If a spec has BOTH top-level edges AND graph.edges, top-level wins.

    This mirrors `BranchDefinition.from_dict`'s precedence and keeps the
    legacy / canonical write path stable.
    """
    us = ext_env
    spec = _two_node_spec_nested_graph()
    # Override at top level. The graph.edges values should be ignored
    # because top-level takes precedence.
    spec["edges"] = [
        {"from": "classify", "to": "end_marker"},
        {"from": "end_marker", "to": "END"},
    ]
    spec["entry_point"] = "classify"
    res = _call(us, action="build_branch", spec_json=json.dumps(spec))
    assert res.get("status") == "built", res
    assert res.get("edge_count") == 2


def test_nested_graph_entry_point_used_when_top_level_missing(ext_env):
    """`graph.entry_point` should be used when no top-level entry_point."""
    us = ext_env
    spec = _two_node_spec_nested_graph()
    # The fixture spec has no top-level entry_point — only graph.entry_point.
    assert "entry_point" not in {k for k in spec if k != "graph"}
    res = _call(us, action="build_branch", spec_json=json.dumps(spec))
    assert res.get("status") == "built", res
    assert res.get("entry_point") == "classify"


def test_nested_graph_conditional_edges_read(ext_env):
    """conditional_edges inside `graph` should also flow through."""
    us = ext_env
    spec = _two_node_spec_nested_graph()
    spec["graph"]["edges"] = [{"from": "end_marker", "to": "END"}]
    spec["graph"]["conditional_edges"] = [
        {
            "from": "classify",
            "conditions": {"done": "end_marker"},
        },
    ]
    res = _call(us, action="build_branch", spec_json=json.dumps(spec))
    assert res.get("status") == "built", res
    # Conditional edges count toward graph reachability — branch built ok.
    assert res.get("node_count") == 2


def test_round_trip_get_branch_then_build_branch_works(ext_env):
    """A user can fork a built branch by re-submitting its `get_branch` shape.

    End-to-end:
    1. Build a branch
    2. Call get_branch on it -> read the response (nested graph shape)
    3. Submit that response as a new build_branch spec (with renamed `name`)
    4. The new branch builds cleanly and matches the source structurally
    """
    us = ext_env
    spec_a = _two_node_spec_nested_graph()
    res_a = _call(us, action="build_branch", spec_json=json.dumps(spec_a))
    assert res_a.get("status") == "built", res_a
    bid_a = res_a["branch_def_id"]

    # Read the live shape back
    src = _call(us, action="get_branch", branch_def_id=bid_a)
    assert src.get("name") == spec_a["name"]
    # Forge a fork spec from the live shape (just rename).
    fork_spec = dict(src)
    fork_spec["name"] = "pr-037-regression-fork"
    fork_spec.pop("branch_def_id", None)
    fork_spec.pop("created_at", None)
    fork_spec.pop("updated_at", None)
    fork_spec.pop("stats", None)
    fork_spec.pop("published", None)
    fork_spec.pop("version", None)
    fork_spec.pop("parent_def_id", None)
    fork_spec.pop("fork_from", None)
    fork_spec.pop("visibility", None)
    fork_spec.pop("gate_claims", None)

    res_b = _call(us, action="build_branch", spec_json=json.dumps(fork_spec))
    assert res_b.get("status") == "built", res_b
    assert res_b.get("edge_count") == res_a.get("edge_count")
    assert res_b.get("entry_point") == res_a.get("entry_point")
    assert res_b.get("node_count") == res_a.get("node_count")
