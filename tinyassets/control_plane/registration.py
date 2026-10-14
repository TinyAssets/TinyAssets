"""One MCP registration boundary, with explicit public and pinned profiles.

The public profile supplies its OAuth-wrapped FunctionTools. The owner-pinned
profile supplies its confined implementations, never the public wrappers. This
module imports neither server: constructing an owner endpoint cannot bootstrap
the public daemon or replace a pinned handler with a broader public signature.
"""
from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool

from tinyassets.activity_fence import ActivityFence
from tinyassets.command_center_names import CommandCenterNames
from tinyassets.engine_conversation_attention import ConversationAttention
from tinyassets.engine_steering import OwnerSteering
from tinyassets.engine_tool_activity import ToolActivity


def register_handle(server: FastMCP, tool: FunctionTool) -> FunctionTool:
    """Install a prepared handle; authority belongs to its explicit profile."""
    server.add_tool(tool)
    return tool


def engine_server(handlers) -> FastMCP:
    """Register a fresh owner-pinned profile over one endpoint's handler globals."""
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

    server = FastMCP("tinyassets")
    # First added is outermost. Preserve admission, outside-client scope,
    # activity cancellation, hooks, bounded errors and naming on every handle.
    for middleware in (
        handlers.ResearchReadOnly(),
        ActivityFence(lambda: handlers._universe_dir_for_fence()),
        handlers.ModelInventory(),
        handlers.OutsideClientScope(),
        handlers.ExtensionHookEvents(),
        OwnerSteering(),
        ToolActivity(),
        ConversationAttention(),
        handlers.BoundedResults(),
        handlers.RefusalsAreErrors(),
        CommandCenterNames(),
    ):
        server.add_middleware(middleware)
    aliases = {"read": "read_file", "write": "write_file",
               "edit": "edit_file", "bash": "run_bash"}
    for name in BACKEND_ENGINE_CAPABILITIES:
        implementation = aliases.get(name, name)
        options = {"output_schema": None} if name in {"read", "bash"} else {}
        register_handle(server, FunctionTool.from_function(
            getattr(handlers, implementation), name=name, **options,
        ))
    server.resource("ta-bridge://request/{payload}")(handlers.remote_ta_request)
    return server
