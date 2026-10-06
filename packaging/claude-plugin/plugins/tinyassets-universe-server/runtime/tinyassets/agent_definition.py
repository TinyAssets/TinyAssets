"""The one agent definition every provider adapter translates.

Founder rule 2026-10-06: one definition supplies an agent's tools,
instructions and capabilities to every provider and every agent. A provider
adapter only renders it into its own launch -- flags, config, a JSON-RPC
protocol, a model catalog -- and must leave the model-visible tools,
instructions and capabilities exactly as defined here.
``tests/test_one_agent_definition.py`` checks every registered provider kind.

The tools are the engine route's own registrations, discovered from the
owner-pinned route the turn is granted (``engine_tool_client``), so their
names, descriptions and schemas have one source. Every model-visible tool
executes on that route, which is where the per-tool fences live (owner
steering, the activity yield/pause/stop fence).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: What every agent can do, whichever provider runs it.
#:
#: ``engine_route_tools`` -- every model-visible tool executes on the owner's
#: engine route; the provider exposes no tool of its own.
#: ``activities`` -- the agent may run an activity (a background run); the
#: route refuses its tools once the activity yields, pauses or stops.
#: ``owner_steering`` -- owner messages sent mid-turn reach the agent with its
#: next tool result (the route's ``OwnerSteering``).
AGENT_CAPABILITIES = frozenset({"engine_route_tools", "activities", "owner_steering"})


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def key(self) -> str:
        """Canonical form: equal keys are the same model-visible tool."""
        return json.dumps([self.name, self.description, self.input_schema],
                          sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    tools: tuple[AgentTool, ...]
    instructions: str
    capabilities: frozenset[str] = AGENT_CAPABILITIES

    def tool_keys(self) -> tuple[str, ...]:
        return tuple(sorted(tool.key() for tool in self.tools))


def agent_tool(tool) -> AgentTool:
    """An MCP ``Tool`` (as the engine route lists it) as an :class:`AgentTool`."""
    return AgentTool(tool.name, tool.description or "", dict(tool.inputSchema or {}))


def agent_definition(tools, instructions: str) -> AgentDefinition:
    """The definition for one turn: the route's granted tools and its instructions."""
    return AgentDefinition(tuple(agent_tool(tool) for tool in tools), instructions or "")
