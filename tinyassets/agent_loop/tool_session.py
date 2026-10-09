"""One turn's tools, routed by name to exactly one place.

Same surface as :class:`tinyassets.engine_tool_client.EngineToolSession`
(``tools`` plus ``call``), so :class:`AgentTurnCoordinator` journals intent,
dispatches and records outcomes exactly as it does for the engine route. The
difference is that ``call`` takes the journal position as ``op_id``
(``takes_op_id``), which the box needs to make a lost reply safe to ask about.

Routing, decided once when the session opens and never by the model:

* ``read``/``write``/``edit``/``bash`` -> the turn's bound box (:mod:`.box_tools`);
* every other granted served tool -> the existing engine route, opened only if
  the grant names one. Its gates (owner rules, auto-review, effect consent)
  stay where they already are.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from tinyassets.agent_loop.box_tools import (
    BOX_ROOT,
    BOX_TOOLS,
    BoxOperationRefused,
    BoxTools,
    box_tool_definitions,
)
from tinyassets.engine_tool_client import EngineToolError, open_engine_tools


def _text_result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structuredContent=None, isError=is_error)


def _tool(name: str, definition: dict[str, Any]) -> Tool:
    return Tool(name=name, description=definition["description"],
                inputSchema=definition["inputSchema"])


class LoopToolSession:
    """Routes one turn's tool calls; constructed only by :func:`open_loop_tools`."""

    takes_op_id = True

    def __init__(self, *, tools: tuple[Tool, ...], box: BoxTools | None,
                 engine: Any | None,
                 steer: Callable[[], str | None] = lambda: None) -> None:
        self._tools = tools
        self._box = box
        self._engine = engine
        self._steer = steer
        self._names = frozenset(tool.name for tool in tools)

    async def _steered(self, result: CallToolResult) -> CallToolResult:
        """The owner's mid-turn messages ride on a loop-served result too, exactly
        as the engine's ``OwnerSteering`` adds them to every engine result."""
        block = await asyncio.to_thread(self._steer)
        if block:
            result.content = [*result.content, TextContent(type="text", text=block)]
        return result

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(tool.model_copy(deep=True) for tool in self._tools)

    async def call(self, name: str, arguments: dict[str, Any], *, op_id: str) -> CallToolResult:
        if name not in self._names or not isinstance(arguments, dict):
            raise EngineToolError("loop_tool_not_allowed")
        if name in BOX_TOOLS:
            try:
                text = await self._box.call(name, op_id, arguments)
            except BoxOperationRefused:
                # Refused before the operation existed: provably nothing ran.
                raise EngineToolError("box_operation_refused") from None
            return await self._steered(_text_result(text))
        # The engine route delivers steering itself (``engine_steering``).
        return await self._engine.call(name, arguments)


@asynccontextmanager
async def open_loop_tools(
    *,
    granted: Sequence[str],
    bind_box: Callable[[], tuple[BoxTools, str]] | None,
    owner: str,
    universe_dir: Path,
    engine_identity: Callable[[], tuple[str, str]],
    timeout: float,
    session_key: str = "",
    turn: str = "",
    ta_turn: str = "",
    capability_grant: Sequence[str] | None = None,
) -> AsyncIterator[LoopToolSession]:
    """Open the turn's tools. ``bind_box`` binds the handle once, here.

    ``granted`` is the turn's model-visible tools in canonical order, and
    ``capability_grant`` the backend authority signed onto the engine session
    (what ``ta`` may reach; default ``granted``);
    A granted box tool with no box
    to bind is refused loudly rather than silently dropped from the turn.
    """
    granted = tuple(granted)
    box_names = tuple(name for name in granted if name in BOX_TOOLS)
    engine_names = tuple(name for name in granted if name not in BOX_TOOLS)
    if box_names and bind_box is None:
        raise EngineToolError("box_unavailable")
    async with AsyncExitStack() as stack:
        box, root = (None, BOX_ROOT)
        if box_names:
            box, root = await asyncio.to_thread(bind_box)
        engine = None
        engine_tools: dict[str, Tool] = {}
        transport_names = ((*engine_names, "bash")
                           if ta_turn and "bash" in box_names else engine_names)
        if transport_names:
            actor_id, graph_id = engine_identity()
            engine = await stack.enter_async_context(open_engine_tools(
                actor_id=actor_id, graph_id=graph_id, enabled_tools=transport_names,
                capability_grant=capability_grant, timeout=timeout,
                session_key=session_key, turn=turn,
            ))
            engine_tools = {tool.name: tool for tool in engine.tools}
        if ta_turn and "bash" in box_names:
            from tinyassets.agent_loop.box_ta import TurnBridge, engine_ta
            from tinyassets.storage import data_dir

            if (actor_id, graph_id) != (owner, universe_dir.name):
                raise EngineToolError("remote_ta_binding_refused")
            bridge = TurnBridge(owner=owner, center=universe_dir.name, turn=ta_turn,
                                handle=box._exec.handle,
                                database=data_dir() / ".remote-ta-receipts.sqlite3",
                                dispatch=lambda message: engine_ta(engine, message))
            stack.callback(bridge.close)
            box._exec.enable_ta(bridge)
        box_definitions = box_tool_definitions(root)
        tools = tuple(
            _tool(name, box_definitions[name]) if name in BOX_TOOLS else engine_tools[name]
            for name in granted
        )
        if not tools:
            raise EngineToolError("loop_tools_empty")
        yield LoopToolSession(
            tools=tools, box=box,
            engine=engine,
            steer=lambda: _take_steering(universe_dir, session_key, turn),
        )


def _take_steering(universe_dir: Path, session_key: str, turn: str) -> str | None:
    """The engine's steering rule: only the owner's thread of a live turn."""
    from tinyassets import agent_steering
    from tinyassets.engine_steering import STEERED_PREFIX

    if not (session_key.startswith(STEERED_PREFIX) and turn):
        return None
    try:
        messages = agent_steering.take(Path(universe_dir), session_key, turn)
    except Exception:  # noqa: BLE001 - a completed call never fails over steering
        return None
    return agent_steering.render(messages) if messages else None
