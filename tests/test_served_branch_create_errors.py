"""A naive model's first `write_graph target=branch operation=create` must land.

LIVE EVIDENCE (2026-09-30T01:07Z, prod, free account
``u-01ky3zh1arr8qth8jee7zx63pq``, turn ``c7d6279d4af74d798375d3f13780140e``,
model ``nvidia/nemotron-3-ultra-550b-a55b:free``). The user asked, naively:
*"can you set something up that runs on its own every morning? like a short note
of what i should focus on today"*. The universe spent **16 of 21 rounds** failing
branch create and never produced a branch or an automation.

Every payload in this module is one of those rounds, replayed verbatim. The
contract asserted is the one the live loop needed and did not get: the first
reasonable attempt succeeds, and where it cannot, the error names the exact
field to change.

The rounds, and what each one was told:

* rounds 5-9, 12 -- ``{"error": "payload_json must be valid JSON."}`` with no
  decoder message, line, column or excerpt. The model's ``prompt_template``
  carried ``\\n`` escapes and an emoji; it could not see which.
* round 13 -- a spec whose one node HAD ``node_id`` was told "node spec missing
  node_id or display_name" (``display_name`` was the missing one), plus a FALSE
  cascade "Branch must have at least one node." for a spec that supplied one.
  The only suggestion was "Review this error and reshape the spec."
* round 17 -- the ``node_defs`` + ``graph_nodes`` shape the previous suggestion
  ("Add at least one node_def + graph_node entry") invited. The staging code
  never reads ``spec["graph_nodes"]``, so that advertisement was for a shape the
  validator does not accept.
* round 18 -- ``display_name`` added, and now "Entry point is required when
  branch has nodes." for a SINGLE node, whose entry point is inferable.
* round 19 -- ``entry_point`` set, no edges, and "Nodes in cycle without exit
  condition: n1." A single node with no outgoing edges is not a cycle.
* round 20 -- ``edges: [{"source": "n1", "target": "END"}]`` was told "edge spec
  missing 'from' or 'to'" without naming the keys it does accept.
"""

from __future__ import annotations

import importlib
import json
import pathlib
from pathlib import Path

import pytest

from tests.engine_authority_helpers import mock_engine_admission, seed_bound_engine

# ---------------------------------------------------------------------------
# The live payloads, verbatim
# ---------------------------------------------------------------------------

#: rounds 5-9, 12. A `prompt_template` with a raw newline and a bare control
#: character: what an LLM emits when it writes JSON containing `\n` by hand.
ROUND_5_MALFORMED = (
    '{"name": "Morning Focus", "node_defs": [{"node_id": "n1", '
    '"prompt_template": "Write a short note\non what to focus on \x01 today"}]}'
)

#: round 13. `node_id` present, `display_name` absent.
ROUND_13_NO_DISPLAY_NAME = {
    "name": "Morning Focus",
    "node_defs": [{
        "node_id": "n1",
        "type": "prompt",
        "prompt_template": "Hello",
    }],
}

#: round 17. The `node_defs` + `graph_nodes` shape the round-13 suggestion invited.
ROUND_17_NODE_DEFS_AND_GRAPH_NODES = {
    "name": "Morning Focus",
    "node_defs": [{
        "node_id": "n1",
        "type": "prompt",
        "prompt_template": "Hello",
    }],
    "graph_nodes": [{"id": "n1", "node_def_id": "n1", "position": 0}],
}

#: round 18. `display_name` added; no `entry_point`.
ROUND_18_NO_ENTRY_POINT = {
    "name": "Morning Focus",
    "node_defs": [{
        "node_id": "n1",
        "display_name": "Morning note",
        "prompt_template": "Write a short note on what to focus on today",
    }],
}

#: round 19. `entry_point` set, one node, no edges.
ROUND_19_NO_EDGES = {
    "name": "Morning Focus",
    "entry_point": "n1",
    "node_defs": [{
        "node_id": "n1",
        "display_name": "Morning note",
        "prompt_template": "Write a short note on what to focus on today",
    }],
}

#: round 20. LangGraph's own edge vocabulary: `source`/`target`.
ROUND_20_SOURCE_TARGET_EDGES = {
    "name": "Morning Focus",
    "entry_point": "n1",
    "node_defs": [{
        "node_id": "n1",
        "display_name": "Morning note",
        "prompt_template": "Write a short note on what to focus on today",
    }],
    "edges": [{"source": "n1", "target": "END"}],
}


# ---------------------------------------------------------------------------
# Harness -- the REAL served create path, end to end into storage
# ---------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authenticate_request):
    """`write_graph` bound to a real data dir, with the real build_branch behind it.

    Deliberately NOT a mocked `_extensions_impl`: every claim here is about what
    the validator does with a payload, so a double would assert the harness. The
    only mock is the separately integration-tested admission query.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    authenticate_request("tester")
    # Production's auth mode (WorkOS): resolve-always, so an authenticated
    # founder's coarse `write` grant carries `extensions.build_branch`. The
    # served handler rebinds `_current_identity` to `_REMIX_CAPABILITIES`, so a
    # legacy exact-scope provider would refuse a call production accepts —
    # i.e. the suite would assert the harness's auth mode, not the validator.
    from tinyassets.auth import middleware as mw

    provider = mw._get_provider()
    monkeypatch.setattr(provider, "is_auth_required", lambda: False)
    monkeypatch.setattr(provider, "resolve_always_writes", lambda: True)
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "tester")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-morning")
    mock_engine_admission(monkeypatch, {"u-morning"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    # Real serving binding + admin ACL on the pinned universe, not a bypass, so
    # the universe the branch is built into is one that could actually run it.
    seed_bound_engine(monkeypatch)
    yield s


def _create(served, payload) -> dict:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return json.loads(served.write_graph(
        target="branch", operation="create", payload_json=raw,
    ))


def _errors(out: dict) -> list[str]:
    """Every error string a rejection carries, whatever field it landed in."""
    if out.get("errors"):
        return [str(e) for e in out["errors"]]
    return [str(out["error"])] if out.get("error") else []


def _fixes(out: dict) -> list[str]:
    return [str(s.get("proposed_fix", "")) for s in (out.get("suggestions") or [])]


def _landed(out: dict) -> bool:
    """Did a branch actually get BUILT -- proved by the id, not by silence.

    Codex refute: the first version returned True for `{}` and for
    `{"errors": [...]}` without a `status`, so any reply shape this file did not
    anticipate would read as success and every "must land" test would pass
    vacuously. Keyed on the positive evidence a real build carries instead.
    """
    return (
        out.get("status") != "rejected"
        and not out.get("error")
        and not out.get("errors")
        and bool(out.get("branch_def_id"))
    )


# ---------------------------------------------------------------------------
# Item 1 -- a JSON parse failure names the position
# ---------------------------------------------------------------------------


def test_rounds_5_to_12_a_json_parse_error_names_line_column_and_excerpt():
    """The live refusal was six words with no position. Six rounds died on it.

    Asserted on the handler's own refusal rather than through a branch build:
    the payload never parses, so no spec exists to validate.
    """
    from tinyassets import engine_mcp_server as s

    detail = s._payload_json_error(ROUND_5_MALFORMED)
    # The decoder's own message, and where.
    assert "line" in detail and "column" in detail, detail
    # An excerpt of the offending region, so the model can see the character.
    assert "near:" in detail, detail
    # And the actual bad byte is shown (escaped -- a raw control character in a
    # tool result is not readable).
    assert "\\n" in detail or "\\x01" in detail or "\\u0001" in detail, detail


def test_the_parse_error_reaches_the_agent_through_write_graph(served):
    out = _create(served, ROUND_5_MALFORMED)
    detail = out["error"]
    assert "payload_json" in detail
    assert "line" in detail and "column" in detail, detail
    assert "near:" in detail, detail


def test_the_parse_error_excerpt_is_bounded(served):
    """A 200kB payload must not return 200kB of excerpt."""
    huge = '{"name": "' + ("x" * 50_000) + '" "description": "unclosed"}'
    out = _create(served, huge)
    assert "line" in out["error"]
    assert len(out["error"]) < 1_000, len(out["error"])


def test_the_decoders_own_message_is_bounded_too():
    """Codex refute: the EXCERPT was bounded and `exc.msg` was not.

    A 200,000-char `msg` produced a 200,234-char refusal -- riding straight into
    the agent's context, which is the cost this whole helper exists to avoid.
    Every variable-length part needs a bound, not just the obvious one.
    """
    from tinyassets import engine_mcp_server as s

    exc = json.JSONDecodeError("y" * 200_000, "{}", 0)
    assert len(s._payload_json_error("{}", exc)) < 1_000


@pytest.mark.parametrize("attr,value", [
    ("pos", None), ("pos", -100), ("pos", 10_000), ("msg", None), ("msg", 42),
])
def test_the_parse_helper_never_raises_on_a_malformed_decoder_error(attr, value):
    """It runs on the REFUSAL path, so it must never be what raises.

    Codex refute reproduced TypeError (`pos=None`), IndexError (`pos=-100`) and
    AttributeError (`msg=None`). A diagnostic that crashes converts a precise
    refusal into a 500 -- strictly worse than the bare sentence it replaced.
    """
    from tinyassets import engine_mcp_server as s

    exc = json.JSONDecodeError("bad", '{"a": 1}', 3)
    setattr(exc, attr, value)
    answer = s._payload_json_error('{"a": 1}', exc)
    assert answer.startswith("payload_json must be valid JSON")


def test_a_recursion_error_says_what_to_do():
    from tinyassets import engine_mcp_server as s

    answer = s._payload_json_error("{}", RecursionError("too deep"))
    assert "nests too deeply" in answer


def test_a_parse_error_still_refuses(served):
    """Precision is not permissiveness: the malformed payload is still rejected."""
    assert not _landed(_create(served, ROUND_5_MALFORMED))


# ---------------------------------------------------------------------------
# Item 2 -- display_name, the false cascade, and honest suggestions
# ---------------------------------------------------------------------------


def test_round_13_display_name_defaults_to_node_id_and_the_branch_lands(served):
    """The round-13 payload was a reasonable first attempt. It must succeed.

    `display_name` is a label for a human; a node that has an id has a usable
    one. Refusing the whole build over it cost the live turn four more rounds.
    """
    out = _create(served, ROUND_13_NO_DISPLAY_NAME)
    assert _landed(out), out
    assert out.get("branch_def_id") or "Built branch" in str(out.get("text", "")), out


def test_round_13_the_built_node_carries_node_id_as_its_display_name(served):
    out = _create(served, ROUND_13_NO_DISPLAY_NAME)
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert [n["display_name"] for n in stored["node_defs"]] == ["n1"]


def test_a_node_with_no_node_id_names_node_id_specifically(served):
    """The missing field is named. The live error named both and meant one."""
    out = _create(served, {
        "name": "Morning Focus",
        "node_defs": [{"display_name": "Morning note", "prompt_template": "Hello"}],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "missing 'node_id'" in joined, joined
    # display_name may be MENTIONED -- saying it defaults is useful -- but never
    # reported as missing, which is what sent round 13 at the wrong field.
    assert "missing node_id or display_name" not in joined, joined
    assert "'display_name' is optional" in joined, joined


def test_no_false_at_least_one_node_cascade_when_nodes_were_given(served):
    """The live spec supplied a node and was told it had none.

    Two errors for one defect is how round 13 became round 17: the model
    believed its `node_defs` key was unrecognized and went looking for another
    shape.
    """
    out = _create(served, {
        "name": "Morning Focus",
        # A node that genuinely cannot be built: no id at all.
        "node_defs": [{"prompt_template": "Hello"}],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "at least one node" not in joined.lower(), joined


def test_an_empty_nodes_list_still_says_at_least_one_node(served):
    """Round 11's answer was correct and must survive: no nodes means no nodes."""
    out = _create(served, {"name": "Morning Focus", "node_defs": []})
    assert not _landed(out)
    assert "at least one node" in " ".join(_errors(out)).lower()


def test_no_suggestion_ever_says_to_reshape_the_spec(served):
    """"Review this error and reshape the spec" is the absence of a suggestion.

    Driven through a spec whose error takes the fallback branch, so this asserts
    the fallback itself rather than a mapped case.
    """
    out = _create(served, {
        "name": "Morning Focus",
        "entry_point": "n1",
        "node_defs": [{
            "node_id": "n1",
            "prompt_template": "Hello",
            "source_code": "def run(state, effects):\n    return {}\n",
        }],
    })
    assert not _landed(out)
    fixes = " ".join(_fixes(out))
    assert "reshape the spec" not in fixes.lower(), fixes
    # And the fallback still names concrete fields from the error it explains.
    assert "source_code" in fixes and "prompt_template" in fixes, fixes


def test_the_empty_nodes_suggestion_does_not_advertise_graph_nodes(served):
    """Round 17 followed a suggestion into a shape staging never reads.

    `_staged_branch_from_spec` reads `node_defs` / `nodes` and synthesizes the
    graph node itself; `spec["graph_nodes"]` has no reader on the create path.
    """
    out = _create(served, {"name": "Morning Focus", "node_defs": []})
    fixes = " ".join(_fixes(out))
    assert "node_defs" in fixes, fixes
    # It may only MENTION graph_nodes to say they are not passed. What it must
    # never do is instruct the caller to add one, which is what round 17 did.
    assert "do not pass graph_nodes" in fixes, fixes
    assert "add" not in fixes.lower().split("graph_node")[-1], fixes


def test_round_17_the_shape_it_was_sent_to_also_lands(served):
    """Belt and braces: the spec round 17 actually sent must not be refused.

    A suggestion no longer names `graph_nodes`, but a model that already learned
    the shape (or copied it from `read_graph target="branches"` output, which
    RETURNS `graph_nodes`) must not be punished for it.
    """
    out = _create(served, ROUND_17_NODE_DEFS_AND_GRAPH_NODES)
    assert _landed(out), out


# ---------------------------------------------------------------------------
# Item 3 -- entry_point defaults
# ---------------------------------------------------------------------------


def test_round_18_entry_point_defaults_to_the_first_node(served):
    out = _create(served, ROUND_18_NO_ENTRY_POINT)
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert stored["entry_point"] == "n1"


def test_the_default_entry_point_is_the_node_nothing_points_at(served):
    """Multi-node: the head of the chain, not merely index 0."""
    out = _create(served, {
        "name": "Two step",
        "node_defs": [
            {"node_id": "second", "prompt_template": "b"},
            {"node_id": "first", "prompt_template": "a"},
        ],
        "edges": [{"from": "first", "to": "second"}, {"from": "second", "to": "END"}],
    })
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert stored["entry_point"] == "first"


def test_an_explicit_start_edge_does_not_hide_the_head(served):
    """Codex refute: `START -> first` made `first` look pointed-at.

    With node order [second, first] every node then had an incoming edge, the
    fallback returned `graph_nodes[0]` -- `second` -- and the runtime ran
    `second` twice. START is where the run begins, not a predecessor.
    """
    out = _create(served, {
        "name": "Explicit start",
        "node_defs": [
            {"node_id": "second", "prompt_template": "b"},
            {"node_id": "first", "prompt_template": "a"},
        ],
        "edges": [
            {"from": "START", "to": "first"},
            {"from": "first", "to": "second"},
        ],
    })
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert stored["entry_point"] == "first"


def test_an_explicit_entry_point_is_never_overridden(served):
    out = _create(served, {
        "name": "Two step",
        "entry_point": "second",
        "node_defs": [
            {"node_id": "second", "prompt_template": "b"},
            {"node_id": "first", "prompt_template": "a"},
        ],
        "edges": [{"from": "second", "to": "first"}, {"from": "first", "to": "END"}],
    })
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert stored["entry_point"] == "second"


def test_a_wrong_explicit_entry_point_is_still_an_error(served):
    """The default fills an ABSENCE. It must not paper over a typo."""
    out = _create(served, {
        "name": "Morning Focus",
        "entry_point": "nope",
        "node_defs": [{"node_id": "n1", "prompt_template": "Hello"}],
    })
    assert not _landed(out)
    assert "nope" in " ".join(_errors(out))


# ---------------------------------------------------------------------------
# Item 4 -- a terminal node is not a cycle
# ---------------------------------------------------------------------------


def test_round_19_one_node_with_no_edges_is_not_a_cycle(served):
    out = _create(served, ROUND_19_NO_EDGES)
    assert _landed(out), out
    assert "cycle" not in json.dumps(out).lower()


def test_a_chain_whose_last_node_has_no_outgoing_edge_is_not_a_cycle(served):
    """LangGraph semantics: a node with no outgoing edge terminates."""
    out = _create(served, {
        "name": "Two step",
        "entry_point": "first",
        "node_defs": [
            {"node_id": "first", "prompt_template": "a"},
            {"node_id": "second", "prompt_template": "b"},
        ],
        "edges": [{"from": "first", "to": "second"}],
    })
    assert _landed(out), out


def test_a_real_cycle_with_no_exit_is_still_refused(served):
    """The check earns its place: two nodes pointing at each other, no END."""
    out = _create(served, {
        "name": "Loop",
        "entry_point": "a",
        "node_defs": [
            {"node_id": "a", "prompt_template": "a"},
            {"node_id": "b", "prompt_template": "b"},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out)).lower()
    assert "cycle" in joined
    assert "a" in joined and "b" in joined


def test_a_loop_leaves_through_a_conditional_edge(served):
    """The CORRECT way out of a loop: a router that can pick END."""
    out = _create(served, {
        "name": "Loop with exit",
        "entry_point": "a",
        "node_defs": [
            {"node_id": "a", "prompt_template": "a"},
            {"node_id": "b", "prompt_template": "b"},
        ],
        "edges": [{"from": "a", "to": "b"}],
        "conditional_edges": [
            {"from": "b", "conditions": {"done": "END", "retry": "a"}},
        ],
    })
    assert _landed(out), out


def test_a_second_plain_edge_to_end_does_not_leave_a_loop(served):
    """A PRE-EXISTING false accept, found by Codex and confirmed by the oracle.

    `a -> b`, `b -> a`, `b -> END` as three PLAIN edges was accepted before this
    change. The installed langgraph raises `GraphRecursionError` on it: a plain
    edge is unconditional, so `b -> a` and `b -> END` both fire and the loop
    never ends. Refusing it at authoring time is strictly better than a
    recursion error part-way through somebody's morning run.
    """
    out = _create(served, {
        "name": "Loop with a plain END edge",
        "entry_point": "a",
        "node_defs": [
            {"node_id": "a", "prompt_template": "a"},
            {"node_id": "b", "prompt_template": "b"},
        ],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},
            {"from": "b", "to": "END"},
        ],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "cycle" in joined.lower()
    # And the error says what a plain edge does, so the fix is derivable.
    assert "conditional edge" in joined and "always" in joined


def test_a_self_loop_beside_a_terminating_node_is_refused(served):
    """Codex refute: crediting implicit terminals admitted `a -> a` + `a -> tail`.

    The oracle raises `InvalidUpdateError`: `a` fans out to itself and to
    `tail` unconditionally, so it re-enters itself forever.
    """
    out = _create(served, {
        "name": "Self loop",
        "entry_point": "a",
        "node_defs": [
            {"node_id": "a", "prompt_template": "a"},
            {"node_id": "tail", "prompt_template": "t"},
        ],
        "edges": [{"from": "a", "to": "a"}, {"from": "a", "to": "tail"}],
    })
    assert not _landed(out)
    assert "cycle" in " ".join(_errors(out)).lower()


def test_a_two_node_loop_beside_a_terminating_node_is_refused(served):
    """Same class as the self-loop: `a <-> b` plus `a -> tail` (InvalidUpdateError)."""
    out = _create(served, {
        "name": "Loop plus tail",
        "entry_point": "a",
        "node_defs": [
            {"node_id": "a", "prompt_template": "a"},
            {"node_id": "b", "prompt_template": "b"},
            {"node_id": "tail", "prompt_template": "t"},
        ],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "a"},
            {"from": "a", "to": "tail"},
        ],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "a" in joined and "b" in joined


def test_a_cycle_reached_from_a_terminal_free_node_is_still_refused(served):
    """The narrowing is exact: only a node with NO outgoing edge terminates.

    `a -> b -> c -> b`: `a` terminates through nothing, but `b` and `c` are a
    genuine closed loop and must still be named.
    """
    out = _create(served, {
        "name": "Tail loop",
        "entry_point": "a",
        "node_defs": [
            {"node_id": "a", "prompt_template": "a"},
            {"node_id": "b", "prompt_template": "b"},
            {"node_id": "c", "prompt_template": "c"},
        ],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "c"},
            {"from": "c", "to": "b"},
        ],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "cycle" in joined.lower()
    assert "b" in joined and "c" in joined


# ---------------------------------------------------------------------------
# Item 4, differentially: LangGraph itself is the oracle
# ---------------------------------------------------------------------------

#: (label, nodes, simple edges, conditional edges, does LangGraph terminate?)
#:
#: The answer column is MEASURED, not asserted from reading: each row is built
#: as a real ``StateGraph`` and invoked. Both directions of this table were
#: wrong at some point in one day -- the first two rows were refused by the
#: original validator, and rows 3-5 were accepted by one or other version of
#: the fix -- so the table is the executable spec for
#: ``_nodes_that_cannot_terminate`` rather than a list of examples.
_TERMINATION_TABLE = [
    ("single node, no edges", ["n1"], [], {}, True),
    ("chain, tail has no out-edge", ["a", "b"], [("a", "b")], {}, True),
    ("self loop plus a tail", ["a", "t"], [("a", "a"), ("a", "t")], {}, False),
    ("two-node loop plus a tail", ["a", "b", "t"],
     [("a", "b"), ("b", "a"), ("a", "t")], {}, False),
    ("loop with a PLAIN edge to END", ["a", "b"],
     [("a", "b"), ("b", "a"), ("b", "END")], {}, False),
    ("loop with a CONDITIONAL exit", ["a", "b"],
     [("a", "b")], {"b": {"done": "END", "retry": "a"}}, True),
    ("a -> b -> c -> b", ["a", "b", "c"],
     [("a", "b"), ("b", "c"), ("c", "b")], {}, False),
]


def _langgraph_terminates(nodes, edges, conditional) -> bool:
    """Build the shape for real and see whether `invoke` comes back."""
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    state = TypedDict("S", {"x": str}, total=False)
    graph = StateGraph(state)
    for node in nodes:
        graph.add_node(node, lambda s: {"x": "y"})
    graph.add_edge(START, nodes[0])
    for src, dst in edges:
        graph.add_edge(src, END if dst == "END" else dst)
    for src, mapping in conditional.items():
        routes = {k: (END if v == "END" else v) for k, v in mapping.items()}
        graph.add_conditional_edges(src, lambda s: next(iter(routes)), routes)
    try:
        compiled = graph.compile()
        compiled.invoke({"x": ""}, {"recursion_limit": 12})
    except Exception:  # noqa: BLE001 - any failure to finish is "does not terminate"
        return False
    return True


@pytest.mark.parametrize("label,nodes,edges,conditional,terminates", _TERMINATION_TABLE)
def test_langgraph_still_agrees_with_the_table(
    label, nodes, edges, conditional, terminates,
):
    """The oracle column is re-measured, so an upgrade cannot silently rot it."""
    assert _langgraph_terminates(nodes, edges, conditional) is terminates, label


@pytest.mark.parametrize("label,nodes,edges,conditional,terminates", _TERMINATION_TABLE)
def test_the_validator_agrees_with_langgraph(
    served, label, nodes, edges, conditional, terminates,
):
    """DIFFERENTIAL: a shape builds here exactly when it runs there.

    This is the assertion that would have caught both regressions in item 4 --
    the original false refusal AND the false accepts the first fix introduced --
    without anyone having to think of the specific graph.
    """
    spec = {
        "name": f"oracle {label}",
        "node_defs": [{"node_id": n, "prompt_template": n} for n in nodes],
        "edges": [{"from": s, "to": d} for s, d in edges],
    }
    if conditional:
        spec["conditional_edges"] = [
            {"from": src, "conditions": mapping}
            for src, mapping in conditional.items()
        ]
    out = _create(served, spec)
    assert _landed(out) is terminates, (label, out.get("errors") or out)


# ---------------------------------------------------------------------------
# Item 5 -- source/target as aliases of from/to
# ---------------------------------------------------------------------------


def test_round_20_source_and_target_are_accepted_edge_keys(served):
    out = _create(served, ROUND_20_SOURCE_TARGET_EDGES)
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    # `source`/`target` land as the canonical stored edge, so the alias is an
    # input spelling and never a second persisted shape.
    assert stored["graph"]["edges"] == [{"from": "n1", "to": "END"}]


def test_a_conditional_edge_accepts_source_too(served):
    out = _create(served, {
        "name": "Router",
        "entry_point": "route",
        "node_defs": [
            {"node_id": "route", "prompt_template": "pick"},
            {"node_id": "left", "prompt_template": "l"},
        ],
        "conditional_edges": [{
            "source": "route",
            "conditions": {"yes": "left", "no": "END"},
        }],
        "edges": [{"source": "left", "target": "END"}],
    })
    assert _landed(out), out


def test_an_edge_missing_both_keys_names_every_accepted_spelling(served):
    out = _create(served, {
        "name": "Morning Focus",
        "entry_point": "n1",
        "node_defs": [{"node_id": "n1", "prompt_template": "Hello"}],
        "edges": [{"frm": "n1", "twoo": "END"}],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    for accepted in ("'from'", "'to'", "'source'", "'target'"):
        assert accepted in joined, (accepted, joined)


def test_an_edge_missing_only_its_origin_names_only_origin_keys(served):
    """Precision cuts both ways: do not send the caller at a key that IS set."""
    out = _create(served, {
        "name": "Morning Focus",
        "entry_point": "n1",
        "node_defs": [{"node_id": "n1", "prompt_template": "Hello"}],
        "edges": [{"frm": "n1", "to": "END"}],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "'source'" in joined, joined
    assert "'target'" not in joined, joined


def test_a_conditional_edge_missing_its_source_names_both_spellings(served):
    out = _create(served, {
        "name": "Morning Focus",
        "entry_point": "n1",
        "node_defs": [{"node_id": "n1", "prompt_template": "Hello"}],
        "conditional_edges": [{"conditions": {"yes": "END"}}],
    })
    assert not _landed(out)
    joined = " ".join(_errors(out))
    assert "from" in joined and "source" in joined, joined


# ---------------------------------------------------------------------------
# Item 6 -- the handbook chapter's examples are real
# ---------------------------------------------------------------------------


def _chapter_specs() -> list[dict]:
    """Every JSON object in the `branches` chapter's `payload_json=` examples.

    Parsed out of the served text rather than duplicated here: a copy would let
    the chapter drift from the thing this test proves works.
    """
    from tinyassets import engine_mcp_server as s

    text = s.SERVED_TOOL_CHAPTERS["write_graph"]["branches"]
    specs: list[dict] = []
    for start in range(len(text)):
        if text[start] != "{":
            continue
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        blob = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(blob, dict) and blob.get("node_defs"):
                        specs.append(blob)
                    break
    return specs


def test_the_branches_chapter_exists_and_is_reachable():
    from tinyassets import engine_mcp_server as s

    payload = json.loads(s._handbook_read("write_graph.branches"))
    assert payload["chapter"] == "branches"
    assert len(payload["text"]) > 500


def test_the_branches_chapter_carries_a_one_node_and_a_two_node_example():
    specs = _chapter_specs()
    assert len(specs) >= 2, [s.get("name") for s in specs]
    sizes = {len(spec["node_defs"]) for spec in specs}
    assert 1 in sizes and 2 in sizes, sizes


def test_every_branches_chapter_example_round_trips_through_the_real_create(served):
    """The example is verified against the validator, not against my memory.

    This is the assertion item 6 asks for: each documented spec is submitted to
    the SAME served create path the agent calls, and must land.
    """
    specs = _chapter_specs()
    assert specs
    for spec in specs:
        out = _create(served, spec)
        assert _landed(out), (spec.get("name"), out)


def _chapter_cron_payload() -> dict:
    """The chapter's automation example, parsed out of the served text."""
    from tinyassets import engine_mcp_server as s

    text = s.SERVED_TOOL_CHAPTERS["write_graph"]["branches"]
    for start in range(len(text)):
        if text[start] != "{":
            continue
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        blob = json.loads(text[start:end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(blob, dict) and blob.get("cron_expr"):
                        return blob
                    break
    raise AssertionError("the branches chapter has no cron automation example")


def test_the_chapters_cron_expression_passes_the_real_trigger_validator():
    """Item 6's second half: the SCHEDULE field is validated, not just quoted.

    `automations.register` reaches `_validated_trigger` only AFTER five
    authority/readiness gates (consumer flag, admin ACL, founder home, serving
    assignment, branch ownership), none of which says anything about the field
    shape this chapter documents. So the field validator is driven directly --
    that is the code that decides whether `cron_expr` is well formed.
    """
    from tinyassets.automations import TRIGGER_CRON, _validated_trigger

    payload = _chapter_cron_payload()
    # The documented shape, exactly. `timezone` joined on 2026-09-30
    # (`automation-schedule-timezone`): it is OPTIONAL -- omitted means the
    # owner's own zone -- but the example shows it, because a universe that
    # cannot see a zone field is a universe that tells its user "7am server
    # time", which is the live defect that change exists for.
    assert set(payload) == {
        "name", "branch_def_id", "cron_expr", "timezone",
    }, payload
    from tinyassets.schedule_timezone import is_known_timezone

    assert is_known_timezone(payload["timezone"]), payload
    kind, seconds, expr = _validated_trigger(0, payload["cron_expr"])
    assert kind == TRIGGER_CRON
    assert seconds == 0
    assert expr == payload["cron_expr"]


def test_the_chapters_cron_expression_fires_once_a_day(served):
    """"every morning" must actually mean daily, not every minute.

    A five-field expression is easy to write as `* 7 * * *` (sixty runs) instead
    of `0 7 * * *` (one). Measured with the scheduler's own gap function.
    """
    from tinyassets.automations import cron_min_gap_seconds

    assert cron_min_gap_seconds(_chapter_cron_payload()["cron_expr"]) == 86_400


def test_the_chapters_branch_is_registrable_as_an_automation_target(served):
    """The branch the chapter builds satisfies what automation create requires.

    `register` refuses `branch_not_readable` / `branch_not_owned`, which is the
    part of the scheduling story the BRANCH spec is responsible for: the example
    must build a branch the actor can then schedule.
    """
    from tinyassets.api.branches import _resolve_readable_branch
    from tinyassets.api.helpers import _base_path

    branch = _create(served, _chapter_specs()[0])
    assert _landed(branch), branch
    resolved = _resolve_readable_branch(branch["branch_def_id"], str(_base_path()))
    assert resolved is not None, "the chapter's branch is not readable back"
    assert str(resolved[1].get("author") or "").strip() == "tester"


def test_the_chapter_names_the_revision_field_a_read_actually_returns():
    """Codex refute: the chapter said `expected_revision` is REPORTED.

    An automation read returns `revision` (`api/automations.py:220`);
    `expected_revision` is what pause/resume/delete SEND. Naming the wrong one
    sends the agent looking for a key that is not in the reply.
    """
    from tinyassets.api import automations as api

    source = pathlib.Path(api.__file__).read_text(encoding="utf-8")
    assert '"revision": automation.revision,' in source

    from tinyassets import engine_mcp_server as s

    text = s.SERVED_TOOL_CHAPTERS["write_graph"]["branches"]
    assert "``revision``" in text
    assert "beside the ``expected_revision``" not in text


def test_the_chapter_is_named_in_the_resident_index():
    """A chapter the description does not point at is one nobody fetches."""
    import asyncio

    from tinyassets import engine_mcp_server as s

    async def _desc():
        for tool in await s.mcp.list_tools():
            if tool.name == "write_graph":
                return tool.description or ""
        raise AssertionError("write_graph is not served")

    assert "branches" in asyncio.run(_desc())


def test_the_chapter_is_not_resident():
    """Served on demand: the text must not ride every model round-trip."""
    import asyncio

    from tinyassets import engine_mcp_server as s

    async def _desc():
        for tool in await s.mcp.list_tools():
            if tool.name == "write_graph":
                return tool.description or ""
        raise AssertionError("write_graph is not served")

    description = asyncio.run(_desc())
    chapter = s.SERVED_TOOL_CHAPTERS["write_graph"]["branches"]
    probe = max((line.strip() for line in chapter.splitlines()), key=len)
    assert len(probe) > 40
    assert probe not in description


# ---------------------------------------------------------------------------
# The whole live loop, as one assertion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,payload", [
    ("round_13", ROUND_13_NO_DISPLAY_NAME),
    ("round_17", ROUND_17_NODE_DEFS_AND_GRAPH_NODES),
    ("round_18", ROUND_18_NO_ENTRY_POINT),
    ("round_19", ROUND_19_NO_EDGES),
    ("round_20", ROUND_20_SOURCE_TARGET_EDGES),
])
def test_every_well_formed_live_round_now_lands(served, label, payload):
    """Rounds 13-20 each sent a reasonable spec. Every one of them now builds.

    Separately parametrized so a partial fix reports WHICH round still fails.
    """
    out = _create(served, payload)
    assert _landed(out), (label, out)


def test_the_first_reasonable_attempt_needs_exactly_one_round(served):
    """The shortest spec a naive model would write: name + one prompt node."""
    out = _create(served, {
        "name": "Morning Focus",
        "node_defs": [{
            "node_id": "note",
            "prompt_template": "Write a short note on what to focus on today",
        }],
    })
    assert _landed(out), out


def test_importing_the_module_twice_does_not_change_the_contract():
    """Guard against a chapter registered at import time under a reload."""
    from tinyassets import engine_mcp_server as s

    before = sorted(s.SERVED_TOOL_CHAPTERS["write_graph"])
    importlib.reload(s)
    assert sorted(s.SERVED_TOOL_CHAPTERS["write_graph"]) == before


# ---------------------------------------------------------------------------
# Follow-up, 2026-09-30: the FIRST create attempt crashed opaquely
#
# Turn f3617ca3a91d4acab30eea8dbbeb2663 round 3, prod 6235666b -- the SAME free
# account, on the run that did then succeed once it read the `branches` chapter.
# Its first attempt got `{"error": "branch build rejected (AttributeError)."}`:
# an exception class name, which tells the model nothing and is exactly the
# shape of refusal this module exists to remove.
# ---------------------------------------------------------------------------

#: The live payload. `state_schema` is a MAPPING of name -> type, which is a
#: reasonable thing to write and is not the list of field objects staging
#: iterates; `type: "prompt"` and `"string"` are likewise natural guesses.
TURN_F3617CA3_ROUND_3 = {
    "name": "Morning Focus Note",
    "nodes": [{
        "node_id": "generate_note",
        "type": "prompt",
        "prompt_template": "Write a short note on what to focus on today",
        "tools_allowed": [],
        "output_keys": ["focus_note"],
    }],
    "edges": [],
    "state_schema": {"focus_note": "string"},
}


def test_the_live_first_attempt_builds(served):
    out = _create(served, TURN_F3617CA3_ROUND_3)
    assert _landed(out), out


def test_a_state_schema_mapping_becomes_the_declared_fields(served):
    """name -> type is accepted and stored as the canonical field list."""
    out = _create(served, TURN_F3617CA3_ROUND_3)
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert [(f["name"], f["type"]) for f in stored["state_schema"]] == [
        ("focus_note", "str"),
    ]


def test_the_fields_wrapper_shape_is_also_accepted(served):
    """`_sanitize_served_branch_spec` already tolerates it, so staging must too."""
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = {"fields": [{"name": "focus_note", "type": "str"}]}
    out = _create(served, spec)
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert [(f["name"], f["type"]) for f in stored["state_schema"]] == [
        ("focus_note", "str"),
    ]


def test_a_bare_string_state_field_names_the_field(served):
    """`state_schema: ["focus_note"]` -- a name with no type is still a name."""
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = ["focus_note"]
    out = _create(served, spec)
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert [f["name"] for f in stored["state_schema"]] == ["focus_note"]


@pytest.mark.parametrize("written,stored", [
    ("string", "str"), ("text", "str"), ("integer", "int"),
    ("number", "float"), ("boolean", "bool"), ("array", "list"),
    ("object", "dict"), ("str", "str"),
])
def test_json_schema_type_names_are_accepted_silently(served, written, stored):
    """These are EXACT synonyms, not guesses, so they earn no notice.

    A model asked for a schema writes JSON Schema's vocabulary. `"string"` cost
    the live turn a whole rejection.
    """
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = {"focus_note": written}
    out = _create(served, spec)
    assert _landed(out), out
    assert out.get("notices") == [], out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    kept = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert kept["state_schema"][0]["type"] == stored


def test_a_guessed_type_builds_and_says_what_it_guessed(served):
    """A typo is a NOTICE, not a rejection: the field was accepted and stored.

    CONTRACT CHANGED 2026-09-30 (was: rejected — `test_composite_branch_actions`
    asserted that). Refusing a whole build over a value the code had already
    resolved is the same defect class as the rest of this module; the author
    still needs telling, so it rides back as `notices`.
    """
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = [{"name": "focus_note", "type": "strang"}]
    out = _create(served, spec)
    assert _landed(out), out
    notices = " ".join(out.get("notices") or [])
    assert "strang" in notices and "str" in notices, out
    # And it is visible in the prose the author reads, not only in a field.
    assert "strang" in out.get("text", ""), out


def test_a_notice_is_never_reported_as_an_error(served):
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = [{"name": "focus_note", "type": "strang"}]
    out = _create(served, spec)
    assert not out.get("errors"), out
    assert out.get("status") != "rejected", out


def test_the_notice_sentinel_never_reaches_the_author(served):
    """The internal marker is stripped -- a NUL in a tool result is not prose."""
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = [{"name": "focus_note", "type": "strang"}]
    blob = json.dumps(_create(served, spec))
    assert "notice\\u0000" not in blob and "\x00" not in blob, blob


def test_a_caller_cannot_forge_a_notice_out_of_a_real_error(served):
    """The obvious hole in a sentinel-prefix design: put the sentinel in a VALUE.

    `_STATE_COERCION_NOTICE` is a `\\x00`-delimited PREFIX, and the classifier is
    `err.startswith(...)`. Caller text is always interpolated AFTER literal
    text, so it cannot reach position 0 -- this test is what holds that true if
    anyone reorders those f-strings. A real error carrying the sentinel must
    still be an error.
    """
    from tinyassets.api.branches import _STATE_COERCION_NOTICE

    spec = dict(TURN_F3617CA3_ROUND_3)
    # A field with NO name is a genuine error. Its type carries the sentinel.
    spec["state_schema"] = [{"type": _STATE_COERCION_NOTICE + "forged"}]
    out = _create(served, spec)
    assert not _landed(out), out
    assert out.get("errors"), out
    assert not any("forged" in n for n in out.get("notices") or []), out


@pytest.mark.parametrize("field", ["name", "type"])
def test_caller_text_is_escaped_before_it_is_quoted_back(served, field):
    """An error names the value sent, so caller bytes reach a tool result.

    Found while probing the sentinel: the classification was safe, but the
    caller's raw `\\x00` was echoed verbatim into the message. Escaped now, for
    the same reason `_payload_json_error` escapes its excerpt -- a raw control
    character in a tool result is unreadable and moves a terminal cursor.
    """
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = [{
        "name": "focus_note", "type": "str",
        field: "a\x00b\nc\tstrang",
    }]
    out = _create(served, spec)
    # The MESSAGE channels only. `attempted_spec` is a deliberate verbatim echo
    # of what arrived -- that is how a caller tells a dropped key from a
    # rejected one -- so it carries the caller's bytes by design.
    messages = json.dumps({
        key: out.get(key)
        for key in ("text", "error", "errors", "notices", "suggestions")
    })
    assert "\x00" not in messages and "\\u0000" not in messages, messages


def test_no_caller_of_the_applicator_leaks_the_sentinel(served):
    """EVERY caller of `_apply_state_field_spec` must read the notice prefix.

    Codex refute, PR #4123: I changed the applicator's return contract and
    updated ONE of its two callers. The patch path (`_apply_patch_op` ->
    `add_state_field`) passed the raw string through, so a coerced type both
    leaked `\\x00notice\\x00` into the author's text and REJECTED a patch the
    build path accepts. Structural, not a second instance: the sentinel is
    grepped out of the source, so a third caller fails this test.
    """
    import pathlib
    import re

    from tinyassets.api import branches as api

    source = pathlib.Path(api.__file__).read_text(encoding="utf-8")
    # Sites that RETURN the applicator's value to a caller of their own.
    callers = re.findall(r"= _apply_state_field_spec\(|return _apply_state_field_spec\(", source)
    assert len(callers) >= 2, callers
    # And every place that consumes such a value tests the prefix.
    assert source.count("startswith(_STATE_COERCION_NOTICE)") >= 2, (
        "a caller of _apply_state_field_spec does not classify the notice prefix"
    )


def test_a_patched_coercion_is_a_notice_not_a_rejection(served):
    """The patch path, driven for real: coerce a type, keep the patch."""
    built = _create(served, {
        "name": "Patch me",
        "node_defs": [{"node_id": "n1", "prompt_template": "hello"}],
    })
    assert _landed(built), built
    out = json.loads(served.write_graph(
        target="branch", operation="patch", branch_id=built["branch_def_id"],
        payload_json=json.dumps([
            {"op": "add_state_field", "name": "focus", "type": "strang"},
        ]),
    ))
    assert out.get("status") == "patched", out
    assert any("strang" in n["notice"] for n in out.get("notices") or []), out
    blob = json.dumps(out)
    assert "\x00" not in blob and "\\u0000" not in blob, blob


def test_a_patched_json_schema_synonym_is_silent(served):
    built = _create(served, {
        "name": "Patch me too",
        "node_defs": [{"node_id": "n1", "prompt_template": "hello"}],
    })
    assert _landed(built), built
    out = json.loads(served.write_graph(
        target="branch", operation="patch", branch_id=built["branch_def_id"],
        payload_json=json.dumps([
            {"op": "add_state_field", "name": "focus", "type": "string"},
        ]),
    ))
    assert out.get("status") == "patched", out
    assert out.get("notices") == [], out


@pytest.mark.parametrize("bad_field,value", [
    ("reducer", {"bad": 1}),
    ("name", {"bad": 1}),
    ("description", ["not", "a", "string"]),
])
def test_a_mapping_state_schema_does_not_bypass_the_text_field_guard(
    served, bad_field, value,
):
    """The shapes the SANITIZER knows must be the shapes the BUILDER knows.

    Codex refute, PR #4123 (found mid-probe): `_sanitize_served_branch_spec`
    validates that a state field's text metadata really is text -- a dict there
    "persists malformed" -- but it looked for a LIST, so it skipped a mapping
    entirely. Accepting the mapping in the builder without teaching the
    sanitizer would have routed a hostile field around that guard. Asserted as
    EQUIVALENCE: the mapping shape refuses exactly what the list shape refuses.
    """
    field = {"name": "focus", "type": "str", bad_field: value}
    as_list = _create(served, {**TURN_F3617CA3_ROUND_3, "state_schema": [field]})
    as_map = _create(served, {**TURN_F3617CA3_ROUND_3, "state_schema": {"focus": field}})
    assert not _landed(as_list), as_list
    assert not _landed(as_map), ("mapping bypassed the guard", as_map)
    assert bad_field in json.dumps(as_map), as_map


def test_quoted_caller_text_is_bounded(served):
    """A 50kB type name must not become the error message."""
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = [{"name": "focus_note", "type": "z" * 50_000}]
    out = _create(served, spec)
    blob = json.dumps(out)
    assert len(blob) < 8_000, len(blob)


@pytest.mark.parametrize("schema", [
    42,
    "focus_note",
    {"focus_note": {"nested": "object"}},
    [[["focus_note"]]],
    [None],
])
def test_an_unusable_state_schema_returns_a_precise_error(served, schema):
    """Whatever arrives, the answer names state_schema -- never a class name."""
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = schema
    out = _create(served, spec)
    blob = json.dumps(out)
    for noise in ("AttributeError", "TypeError", "KeyError", "ValueError"):
        assert noise not in blob, (noise, out)
    if not _landed(out):
        assert "state_schema" in blob, out


@pytest.mark.parametrize("key,value", [
    ("nodes", {"generate_note": {"prompt_template": "x"}}),
    ("edges", {"generate_note": "END"}),
    ("conditional_edges", {"generate_note": {"done": "END"}}),
    ("edges", ["generate_note"]),
    ("node_defs", [None]),
    ("io_manifest", 7),
    ("skills", "not-a-list"),
    ("tags", {"a": 1}),
])
def test_no_container_shape_returns_a_bare_exception_class(served, key, value):
    """The RULE, not one instance: a class name is not a diagnosis.

    `write_graph` wraps the build in `except (AttributeError, TypeError,
    ValueError, KeyError)` and returns `branch build rejected (<class>)`. That
    backstop must stay -- a served handler must never propagate -- but reaching
    it means some container shape crashed instead of being validated, and the
    model is handed a word it cannot act on. Every one of these must come back
    as either a build or a message naming the key.
    """
    spec = dict(TURN_F3617CA3_ROUND_3)
    spec["state_schema"] = []
    spec[key] = value
    out = _create(served, spec)
    blob = json.dumps(out)
    for noise in ("AttributeError", "TypeError", "KeyError", "ValueError"):
        assert noise not in blob, (key, value, out)
    if not _landed(out):
        assert key in blob or "must be" in blob, (key, value, out)
