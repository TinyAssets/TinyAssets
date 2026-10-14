"""Authoring pages and the source_code action list name the same contract."""
from __future__ import annotations

import ast
import json
import sys
import types

from tinyassets.api.app_ui import APP_UI_AUTHORING_PAGE
from tinyassets.api.authoring_pages import authoring_page_json
from tinyassets.api.branches import _BRANCH_DESIGN_GUIDE


def test_read_page_names_return_the_authoring_documents() -> None:
    guide = json.loads(authoring_page_json("branch_design_guide"))
    assert guide["page"] == "branch_design_guide"
    assert guide["content"] == _BRANCH_DESIGN_GUIDE
    assert '"sink": "workspace"' in guide["content"]
    assert '"op": "create"' in guide["content"]
    assert '"op": "checkout"' in guide["content"]
    assert '"op": "push"' in guide["content"]
    assert '"op": "discard"' in guide["content"]
    assert 'invoke_mcp_action("read"' in guide["content"]
    assert 'invoke_mcp_action("write"' in guide["content"]
    assert 'invoke_mcp_action("edit"' in guide["content"]
    assert 'invoke_mcp_action("bash"' in guide["content"]
    assert len(guide["content"].encode()) < 24_576

    ui = json.loads(authoring_page_json("/pages/app_ui.md"))
    assert ui["page"] == "app_ui"
    assert ui["content"] == APP_UI_AUTHORING_PAGE
    assert '"component"' in ui["content"]
    assert '"version": 1' in ui["content"]
    assert "must be 1" in ui["content"]
    assert len(ui["content"].encode()) < 24_576

    assert authoring_page_json("notes/something-else") == ""


def test_an_unknown_source_code_action_lists_the_served_tools() -> None:
    langgraph = types.ModuleType("langgraph")
    graph = types.ModuleType("langgraph.graph")
    graph.END = "END"
    graph.START = "START"
    graph.StateGraph = object
    sys.modules.setdefault("langgraph", langgraph)
    sys.modules.setdefault("langgraph.graph", graph)

    from tinyassets.branches import NodeDefinition
    from tinyassets.graph_compiler import (
        CompilerError,
        _build_node_mcp_invoker,
        node_mcp_supported_actions,
    )
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

    node = NodeDefinition(
        node_id="edit",
        display_name="Edit",
        source_code="def run(state):\n    return {}\n",
        output_keys=["out"],
    )
    invoke = _build_node_mcp_invoker(node, event_sink=None)
    try:
        invoke("wiki.write")
    except CompilerError as exc:
        text = str(exc)
    else:
        raise AssertionError("an unknown action must be refused")
    assert "unsupported MCP action" in text
    listed = ast.literal_eval(text.split("Supported actions:", 1)[1].strip())
    assert listed == node_mcp_supported_actions()
    assert set(BACKEND_ENGINE_CAPABILITIES) <= set(listed)
    assert "wiki.read" in listed and "read_run_file" in listed
    assert "wiki.write" not in listed

    try:
        invoke("read", path="notes.md")
    except CompilerError as exc:
        recognized = str(exc)
    else:
        raise AssertionError("read still needs an owner run")
    assert "unsupported MCP action" not in recognized
