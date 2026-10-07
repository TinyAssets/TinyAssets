"""Long-form served-agent guidance is REACHABLE, not resident.

Measured 2026-09-25 (`tests/test_converse_turn_cost.py`): the engine
tool-definition block is re-sent on every model round-trip of every served
founder turn, it was 63,383 B, and `write_graph`'s manual was 38,513 chars of it
— 61% of the block for one handle. Production confirmed the loop shape on
2026-09-26 UTC: two recall turns on the free universe ran 3 rounds each.

So the manual moved into handbook chapters served by
``read_graph target="handbook"``. These tests hold that the description really
got smaller and that every chapter is still reachable.

Change: `openspec/changes/engine-tool-manual-on-demand/`.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import types

import pytest

from tinyassets import engine_mcp_server as engine
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

#: How to put the chapters back where they were: the resident index sits exactly
#: where they used to start, and the tail resumes at this line. This pair IS the
#: documented reconstruction order — the test is the place that records it.
INDEX_ANCHOR = "THE HANDBOOK."
TAIL_ANCHOR = "A branch is a stored graph SHAPE"

#: Chapter order as the resident index names them. `delivering` was added
#: 2026-09-26 with open receivers: an agent asked to let other users send its
#: universe something reached for a public webhook because no chapter named the
#: cross-user delivery primitive. Appended last, so the reconstruction order the
#: tests below assert is unchanged. `interfaces` (user-authored app UIs) was
#: appended after it the same day, for the same reason. `systems` (2026-09-30):
#: asked for an always-on team of agents with its own screen, the universe built
#: an external service and asked for hosting, because nothing mapped that request
#: onto agent nodes, automations, files and an app UI.
#:
#: `branches` (2026-09-30) is FIRST, because it is the chapter for the call every
#: other chapter presupposes -- including `systems`, which points at it for the
#: concrete create syntax. It exists because a free account's naive first ask
#: ("something that runs every morning") spent 16 of 21 rounds failing
#: `operation="create"`, and `read_graph target="handbook" query="write_graph.branch"`
#: answered "no chapter 'branch'" -- there was no worked example anywhere the
#: model could reach.
SPLIT_CHAPTER_ORDER = (
    "branches", "connections", "code_nodes", "workspaces", "delivering",
    "interfaces", "systems",
)
# New handbook content is not part of the historical docstring relocation.
CHAPTER_ORDER = (*SPLIT_CHAPTER_ORDER, "connect", "share-after-publish", "capabilities")

def _parameter_descriptions(handle: str) -> list[str]:
    """Every parameter description in the advertised schema, possibly empty."""
    async def _read() -> list[str]:
        for tool in await engine.mcp.list_tools():
            if tool.name == handle:
                schema = tool.parameters if isinstance(tool.parameters, dict) else {}
                return [
                    str(spec["description"])
                    for spec in (schema.get("properties") or {}).values()
                    if isinstance(spec, dict) and spec.get("description")
                ]
        raise AssertionError(f"no served handle named {handle!r}")

    return asyncio.run(_read())


def _description(handle: str) -> str:
    async def _read() -> str:
        for tool in await engine.mcp.list_tools():
            if tool.name == handle:
                return tool.description or ""
        raise AssertionError(f"no served handle named {handle!r}")

    return asyncio.run(_read())


# ---------------------------------------------------------------------------
# The chapters are where the index says
# ---------------------------------------------------------------------------


def _source_docstring(name: str = "write_graph") -> str:
    """The RAW docstring from the shipped source, before any MCP layer sees it.

    The relocation this module guards is a SOURCE edit, so prove it against source.
    Reading the advertised description instead made this test a claim about FastMCP:
    on 3.4.x it extracts the `Args:` block into the parameter schema, consuming the
    `Args:` header and turning each parameter name into a schema KEY, so a word check
    over the advertised text reported 17 "lost" words in CI that the split had not
    touched. What FastMCP chooses to relocate is its business; what this change
    relocated is the question.
    """
    import ast

    source = pathlib.Path(engine.__file__).read_text(encoding="utf-8")
    node = next(
        item for item in ast.walk(ast.parse(source))
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_docstring(node, clean=False) or ""





#: The two shapes a FastMCP version can hand us for the same docstring: 3.2.0 leaves
#: the `Args:` block in `description`, 3.4.x extracts it into the parameter schema
#: (and drops the `<param>:` labels). Both are built from the SAME source docstring
#: and both are asserted, so neither host is the one this module happens to run on.
@pytest.mark.parametrize("placement", ["in_description", "in_schema"])
def test_the_reader_finds_guidance_under_either_fastmcp_placement(monkeypatch, placement):
    """`served_tool_guidance` reads the schema too, not only the description.

    A reader that looks at one field answers "is the agent told this?" differently
    per host — which is how two of these tests went RED in Linux CI while passing on
    Windows. Constructed rather than skipped: the first version of this test
    `pytest.skip`ped on the extracting version, i.e. it went quiet on exactly the
    host (and the production runtime) whose behaviour it was written to cover.
    """
    doc = _source_docstring()
    head, _, args_block = doc.partition("Args:")
    assert args_block, "write_graph lost its Args block"
    if placement == "in_description":
        tool = types.SimpleNamespace(
            name="write_graph", description=doc, parameters={"properties": {}},
        )
    else:
        tool = types.SimpleNamespace(
            name="write_graph",
            description=head,
            parameters={"properties": {
                # 3.4 keys each parameter and puts its prose in `description`,
                # without the `<param>:` label the docstring carried.
                "payload_json": {"description": "Args: " + args_block},
            }},
        )
    others = [
        other for other in asyncio.run(engine.mcp.list_tools()) if other.name != "write_graph"
    ]

    async def _list_tools():
        return [tool, *others]

    monkeypatch.setattr(engine.mcp, "list_tools", _list_tools)
    reachable = engine.served_tool_guidance("write_graph")
    # Wherever the Args block landed, the reader has it.
    assert "Args:" in reachable
    for parameter in ("payload_json", "expected_revision", "idempotency_key"):
        assert parameter in reachable
    # And the chapters are still appended under both placements.
    for name in CHAPTER_ORDER:
        probe = max(
            (line.strip() for line in engine.SERVED_TOOL_CHAPTERS["write_graph"][name]
             .splitlines()), key=len,
        )
        assert probe in reachable


def test_the_chapters_are_still_where_the_index_says_they_were():
    """Relocation is allowed, arbitrary reshuffling is not: order still holds.

    Structural, with no line count: how much of the docstring lands in the
    description versus the parameter schema depends on the FastMCP version
    (3.2.0 keeps `Args:` in the description, 3.4.x extracts it), so a line total
    asserts a different thing on each host — which is how this first went red in
    CI while passing locally.
    """
    description = _description("write_graph")
    at = description.index(INDEX_ANCHOR)
    head, rest = description[:at], description[at:]
    tail = TAIL_ANCHOR + rest.split(TAIL_ANCHOR, 1)[1]
    chapters = engine.SERVED_TOOL_CHAPTERS["write_graph"]
    recomposed = head + "".join(chapters[name] for name in SPLIT_CHAPTER_ORDER) + tail
    # The reconstruction reads in the original order: the operation catalogue
    # before the chapters, the delete/parity tail after them.
    assert recomposed.index('operation="create"') < recomposed.index("CODE NODES")
    assert recomposed.index("CODE NODES") < recomposed.index(TAIL_ANCHOR)
    assert recomposed.index("CODE NODES") < recomposed.index("WORKSPACES.")


def test_served_tool_guidance_answers_for_every_served_handle():
    """One call answers "is the agent told this?" — for all 14, not just the split one."""
    for handle in SERVED_ENGINE_MCP_TOOLS:
        guidance = engine.served_tool_guidance(handle)
        assert _description(handle) in guidance
    with_chapters = engine.served_tool_guidance("write_graph")
    for name in CHAPTER_ORDER:
        assert engine.SERVED_TOOL_CHAPTERS["write_graph"][name] in with_chapters


def test_an_unknown_handle_raises_instead_of_returning_empty():
    """A silent "" would let a test assert reachability for a name that is absent."""
    with pytest.raises(KeyError, match="no served handle"):
        engine.served_tool_guidance("write_graphh")


# ---------------------------------------------------------------------------
# The description really got smaller, and the split is a partition
# ---------------------------------------------------------------------------


def test_moved_chapters_are_gone_from_the_per_round_description():
    """The saving is real: chapter text is not also resident."""
    description = _description("write_graph")
    chapters = engine.SERVED_TOOL_CHAPTERS["write_graph"]
    for name, text in chapters.items():
        # Compare on a distinctive interior line, so this cannot pass merely
        # because indentation differs.
        probe = max(
            (line.strip() for line in text.splitlines()), key=len,
        )
        assert len(probe) > 40
        assert probe not in description, f"{name} is still resident"
    assert len(description) < 12_000, len(description)


def test_the_resident_index_names_every_chapter_and_how_to_fetch_it():
    """A chapter the description does not point at is a chapter nobody fetches."""
    description = _description("write_graph")
    assert INDEX_ANCHOR in description
    for name in engine.SERVED_TOOL_CHAPTERS["write_graph"]:
        assert name in description
    assert 'read_graph target="handbook"' in description


# ---------------------------------------------------------------------------
# The handbook read contract
# ---------------------------------------------------------------------------


def test_the_index_lists_every_chapter_that_exists():
    payload = json.loads(engine._handbook_read(""))
    assert payload["handbook"] == {
        handle: sorted(chapters)
        for handle, chapters in engine.SERVED_TOOL_CHAPTERS.items()
    }
    assert 'query="<handle>.<chapter>"' in payload["read_one"]


def test_a_chapter_comes_back_verbatim_and_untruncated():
    for name in CHAPTER_ORDER:
        payload = json.loads(engine._handbook_read(f"write_graph.{name}"))
        assert payload["handle"] == "write_graph"
        assert payload["chapter"] == name
        assert payload["text"] == engine.SERVED_TOOL_CHAPTERS["write_graph"][name]


def test_the_bound_read_handle_actually_serves_the_handbook(monkeypatch):
    """END TO END through the real `read_graph`, not the helper behind it.

    PR #4000 review, mutation finding: disabling the `if normalized == "handbook"`
    branch left the whole suite green (210 passed), because every handbook test
    called `_handbook_read` directly. The agent would have been told to fetch
    chapters it could not reach. This test drives the handle the agent drives.
    """
    monkeypatch.setattr(engine, "_binding_error", lambda: None)
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-handbook", raising=False)

    index = json.loads(engine.read_graph(target="handbook"))
    assert sorted(index["handbook"]["write_graph"]) == sorted(CHAPTER_ORDER)

    for name in CHAPTER_ORDER:
        chapter = json.loads(
            engine.read_graph(target="handbook", query=f"write_graph.{name}")
        )
        assert chapter["text"] == engine.SERVED_TOOL_CHAPTERS["write_graph"][name]

    refused = json.loads(engine.read_graph(target="handbook", query="write_graph.nope"))
    assert "no chapter 'nope'" in refused["error"]


def test_the_handbook_route_still_refuses_an_unbound_caller(monkeypatch):
    """`_binding_error()` runs BEFORE the handbook branch; keep it that way."""
    monkeypatch.setattr(engine, "_binding_error", lambda: json.dumps({"error": "unbound"}))
    assert json.loads(engine.read_graph(target="handbook"))["error"] == "unbound"


def test_an_unknown_name_is_refused_and_names_what_is_available():
    unknown_handle = json.loads(engine._handbook_read("read_graph.connections"))
    assert "no handbook for 'read_graph'" in unknown_handle["error"]
    assert "write_graph" in unknown_handle["handbook"]
    unknown_chapter = json.loads(engine._handbook_read("write_graph.secrets"))
    assert "no chapter 'secrets'" in unknown_chapter["error"]
    assert sorted(CHAPTER_ORDER) == unknown_chapter["chapters"]


def test_the_handbook_carries_no_universe_state_and_no_secret():
    """Static text: it cannot leak, and there is nothing to write."""
    for query in ("", "write_graph.connections", "write_graph.code_nodes"):
        payload = engine._handbook_read(query)
        assert "vault://" not in payload
        assert "Bearer " not in payload
    assert "handbook" in engine._PINNED_READ_TARGETS
    # Read-only by construction: the write handle has no handbook target, so the
    # refusal names the targets it does support rather than writing anything.
    answer = engine.write_graph(target="handbook", operation="create")
    assert json.loads(answer)["error"]
    for text in engine.SERVED_TOOL_CHAPTERS["write_graph"].values():
        probe = max((line.strip() for line in text.splitlines()), key=len)
        assert probe not in answer


# ---------------------------------------------------------------------------
# One path for every account, and the public surface is untouched
# ---------------------------------------------------------------------------


def test_the_resident_block_does_not_vary_by_anything():
    """Founder rule: all accounts behave the same. Assembly reads no context."""
    first = [_description(name) for name in SERVED_ENGINE_MCP_TOOLS]
    second = [_description(name) for name in SERVED_ENGINE_MCP_TOOLS]
    assert first == second
    assert engine._handbook_read("") == engine._handbook_read("")


def test_the_public_connector_description_is_untouched_and_uncoupled():
    """Chatbot clients read the public surface; an engine split must not reach it."""
    from tinyassets import universe_server

    public = universe_server.write_graph.__doc__ or ""
    served = engine.write_graph.__doc__ or ""
    # Independent docstrings on independent functions, not derived from each other.
    assert public != served
    assert INDEX_ANCHOR not in public
    assert "handbook" not in public
    # The public manual is still whole: it never carried the engine's chapters,
    # and the relocation did not shrink it (measured 16,623 chars on 2026-09-25).
    assert len(public) > 16_000
