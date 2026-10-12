"""Every registered universe-server tool takes its title and hints from one table."""

from __future__ import annotations

import asyncio

from tinyassets.mcp_tool_annotations import TOOL_ANNOTATIONS
from tinyassets.universe_server import mcp

_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def test_every_registered_tool_has_a_title_every_hint_and_a_table_entry() -> None:
    tools = asyncio.run(mcp.list_tools(run_middleware=False))
    assert tools
    for tool in tools:
        assert tool.name in TOOL_ANNOTATIONS, (
            f"{tool.name} has no entry in TOOL_ANNOTATIONS"
        )
        row = TOOL_ANNOTATIONS[tool.name]
        assert isinstance(row.title, str) and row.title.strip(), tool.name
        assert isinstance(tool.title, str) and tool.title.strip(), tool.name
        assert tool.title == row.title, tool.name
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert isinstance(annotations.title, str) and annotations.title.strip(), tool.name
        assert annotations.title == row.title, tool.name
        for hint in _HINTS:
            advertised = getattr(annotations, hint, None)
            recorded = getattr(row, hint, None)
            assert isinstance(recorded, bool), f"{tool.name}.{hint} missing from the table"
            assert isinstance(advertised, bool), f"{tool.name}.{hint}"
            assert advertised is recorded, f"{tool.name}.{hint}"
    read_page = next(tool for tool in tools if tool.name == "read_page")
    assert read_page.annotations.openWorldHint is True
