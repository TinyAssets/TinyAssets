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

import json
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
















# ---------------------------------------------------------------------------
# Item 2 -- display_name, the false cascade, and honest suggestions
# ---------------------------------------------------------------------------


















# ---------------------------------------------------------------------------
# Item 3 -- entry_point defaults
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# Item 4 -- a terminal node is not a cycle
# ---------------------------------------------------------------------------


















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






# ---------------------------------------------------------------------------
# Item 5 -- source/target as aliases of from/to
# ---------------------------------------------------------------------------












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














# ---------------------------------------------------------------------------
# The whole live loop, as one assertion
# ---------------------------------------------------------------------------








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


































