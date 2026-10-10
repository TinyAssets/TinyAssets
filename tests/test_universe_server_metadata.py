from __future__ import annotations

import asyncio

from tinyassets.universe_server import mcp


def _list_tools():
    return asyncio.run(mcp.list_tools(run_middleware=False))


def _list_prompts():
    return asyncio.run(mcp.list_prompts(run_middleware=False))


class TestUniverseServerMetadata:
    def test_tool_metadata_is_directory_ready(self):
        """Every registered tool is a canonical handle with directory metadata.
        The legacy fat tools (universe, extensions, goals, gates, wiki) are no
        longer registered (2026-09-30)."""
        tools = {tool.name: tool for tool in _list_tools()}
        assert set(tools) == {"read_graph", "write_graph", "run_graph", "read_page",
                              "write_page", "converse", "get_status"}
        for tool in tools.values():
            assert tool.title, tool.name
            assert tool.annotations is not None, tool.name
            assert tool.description, tool.name

    def test_multiplexed_write_handles_disclose_aggregate_risk(self):
        tools = {tool.name: tool for tool in _list_tools()}
        for name in ("write_graph", "write_page", "run_graph", "converse"):
            hints = tools[name].annotations
            assert hints.readOnlyHint is False, name
            assert hints.destructiveHint is True, name
            assert hints.idempotentHint is False, name
            assert hints.openWorldHint is True, name
        for name in ("read_graph", "read_page", "get_status"):
            assert tools[name].annotations.readOnlyHint is True, name
        assert "delete" in tools["write_graph"].description
        assert "shared with other users" in tools["write_page"].description
        assert "external services" in tools["converse"].description
        assert "side effects" in tools["run_graph"].description

    def test_prompt_metadata_is_present(self):
        prompts = {prompt.name: prompt for prompt in _list_prompts()}

        control_station = prompts["control_station"]
        assert control_station.title == "Control Station Guide"
        assert {"control", "daemon", "multiplayer", "operations"} <= control_station.tags
        assert "TinyAssets Server" in control_station.description

        extension_guide = prompts["extension_guide"]
        assert extension_guide.title == "Extension Authoring Guide"
        assert extension_guide.tags == {
            "extensions",
            "nodes",
            "plugins",
            "tinyassets",
        }
        assert "LangGraph nodes" in extension_guide.description
