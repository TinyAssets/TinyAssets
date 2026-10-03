"""C1 of the universe -> command center rename: the public MCP edge, clean cutover.

Founder 2026-10-01: "no old ids do not keep working ... current testers ...
need to cleanly move to the new system". So a retired name is refused loudly,
naming its replacement; the advertised surface carries only current names; and
responses are respelled at the edge until the code (C3) and storage (C4) renames
make the respelling unnecessary.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tinyassets import command_center_names as names
from tinyassets import engine_mcp_server, universe_server

CANONICAL = {"read_graph", "write_graph", "run_graph", "read_page", "write_page",
             "converse", "get_status"}

#: Names still spelled the old way in advertised text because they are STORED in
#: people's branch definitions; the storage migration (C4) renames them.
_STORED_NAMES = re.compile(r"delivery_sender_universe_id")


# ---------------------------------------------------------------------------
# The rule, as functions
# ---------------------------------------------------------------------------


def test_a_retired_argument_name_is_refused_naming_its_replacement():
    with pytest.raises(names.RetiredName) as caught:
        names.refuse_retired_arguments({"universe_id": "u-1"})
    assert str(caught.value) == "renamed: universe_id is now command_center_id"
    assert caught.value.document()["error"] == "renamed"


@pytest.mark.parametrize("field,value,current", [
    ("target", "universe", "target=command_center"),
    ("target", "universe_files", "target=command_center_files"),
    ("target", " Universe_File ", "target=command_center_file"),
    ("scope", "universe", "scope=command_center"),
])
def test_a_retired_value_is_refused_naming_its_replacement(field, value, current):
    with pytest.raises(names.RetiredName) as caught:
        names.refuse_retired_arguments({field: value})
    assert caught.value.current == current


def test_current_names_pass_and_map_to_what_handlers_dispatch_on():
    names.refuse_retired_arguments({"command_center_id": "u-1", "target": "command_center"})
    assert names.internal_value("command_center") == "universe"
    assert names.internal_value("command_center_files") == "universe_files"
    assert names.internal_value("goal") == "goal"
    # A retired value is not translated: a caller that skipped the edge fails.
    assert names.internal_value("universe") == "retired:universe"


def test_responses_carry_only_current_spellings():
    out = names.public_response({
        "universe_id": "u-1",
        "universes": [{"universe_id": "u-2", "actor": "universe:u-2"}],
        "error": "no_home_universe",
        "note": "universe:not-an-actor stays as written",
    })
    assert out == {
        "command_center_id": "u-1",
        "command_centers": [{"command_center_id": "u-2", "actor": "command_center:u-2"}],
        "error": "no_home_command_center",
        "note": "universe:not-an-actor stays as written",
    }


def test_a_persons_own_content_is_never_respelled():
    assert names.verbatim("read_graph", {"target": "run_output"})
    assert names.verbatim("read_graph", {"target": "command_center_file"})
    assert names.verbatim("read", {})
    assert not names.verbatim("read_graph", {"target": "graphs"})


# ---------------------------------------------------------------------------
# Through the real servers
# ---------------------------------------------------------------------------


async def _call(server, tool, arguments):
    async with Client(server) as client:
        return await client.call_tool(tool, arguments)


def test_the_connector_refuses_universe_id_with_a_pointer():
    with pytest.raises(ToolError) as caught:
        asyncio.run(_call(universe_server.mcp, "get_status", {"universe_id": "u-x"}))
    assert json.loads(str(caught.value))["current"] == "command_center_id"


def test_the_connector_refuses_a_retired_target_with_a_pointer():
    with pytest.raises(ToolError) as caught:
        asyncio.run(_call(universe_server.mcp, "read_graph",
                          {"target": "universe_files", "graph_id": "u-x"}))
    assert json.loads(str(caught.value))["current"] == "target=command_center_files"


def test_the_engine_refuses_a_retired_target_with_a_pointer():
    with pytest.raises(ToolError) as caught:
        asyncio.run(_call(engine_mcp_server.mcp, "read_graph", {"target": "universe_file"}))
    assert json.loads(str(caught.value))["current"] == "target=command_center_file"


def test_the_advertised_surface_names_no_universe():
    async def surface():
        async with Client(universe_server.mcp) as client:
            return await client.list_tools(), await client.list_prompts()

    tools, prompts = asyncio.run(surface())
    advertised = {tool.name for tool in tools}
    assert advertised == CANONICAL
    for tool in tools:
        text = _STORED_NAMES.sub("", (tool.description or "") + json.dumps(tool.inputSchema))
        assert "universe" not in text.lower(), tool.name
    prompt_names = {prompt.name for prompt in prompts}
    assert "meet_command_center" in prompt_names and "meet_universe" not in prompt_names


def test_the_edge_respells_a_handlers_response_and_leaves_content_alone():
    """The same middleware both servers register, around handlers that still
    speak the internal names (until C3)."""
    from fastmcp import FastMCP

    server = FastMCP("edge")
    server.add_middleware(names.CommandCenterNames())

    @server.tool
    def read_graph(target: str = "status") -> str:
        return json.dumps({"universe_id": "u-1", "actor": "universe:u-1", "target": target})

    @server.tool
    def read(path: str = "") -> str:
        return json.dumps({"universe_id": "file bytes, verbatim"})

    status = json.loads(asyncio.run(_call(server, "read_graph", {})).content[0].text)
    assert status == {"command_center_id": "u-1", "actor": "command_center:u-1",
                      "target": "status"}
    output = json.loads(asyncio.run(
        _call(server, "read_graph", {"target": "run_output"})).content[0].text)
    assert output["universe_id"] == "u-1"  # a run's output is the person's content
    raw = json.loads(asyncio.run(_call(server, "read", {})).content[0].text)
    assert raw == {"universe_id": "file bytes, verbatim"}


def test_a_branch_definitions_own_fields_survive_the_round_trip():
    """A user's state field named ``universe`` is data (gpt-6-astra repro on C1)."""
    definition = {
        "universe_id": "u-1",
        "state_schema": [{"name": "universe", "type": "str"}],
        "graph_nodes": [{"id": "n", "output_mapping": {"universe": "answer"}}],
    }
    out = names.public_response({"branch": definition})
    assert out["branch"]["command_center_id"] == "u-1"
    assert out["branch"]["state_schema"] == definition["state_schema"]
    assert out["branch"]["graph_nodes"] == definition["graph_nodes"]


def test_the_engines_status_reaches_the_renamed_connector_parameter(monkeypatch):
    """The engine binds its get_status to the connector's; a missed caller was a
    TypeError (gpt-6-astra, C1)."""
    import inspect

    source = inspect.getsource(engine_mcp_server.get_status)
    assert "command_center_id=" in source and "universe_id=" not in source
    assert "command_center_id" in inspect.signature(universe_server.get_status).parameters
