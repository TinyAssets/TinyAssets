"""The activity pre-tool fence on the engine route.

Every model-visible tool of every provider executes on the owner's engine
route: HTTP agent rounds and Codex dynamic tools through
``engine_tool_client``, Claude through its one strict MCP server. A launch
made for an activity names the activity's session (``activity:<id>``) on that
route. Once the activity yields to an owner request, pauses or stops, its
status leaves ``in_progress`` and this fence refuses every further tool call
before the handler runs. A native agent's internal loop therefore cannot act
after the yield, whichever provider runs it; it can only finish its reply.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastmcp.server.middleware import Middleware

ACTIVITY_SESSION_PREFIX = "activity:"

STOPPED = (
    "This activity is no longer running: it is waiting on your owner, paused or "
    "stopped. Call no more tools; end the turn with a one-line note of where you stopped."
)


def activity_refusal(universe_dir: Path, session_key: str) -> str | None:
    """The refusal for a tool call made under ``session_key``, or ``None``."""
    if not session_key.startswith(ACTIVITY_SESSION_PREFIX):
        return None
    from tinyassets import agent_activities

    record = agent_activities.get(universe_dir, session_key[len(ACTIVITY_SESSION_PREFIX):])
    if record is None or record["status"] != agent_activities.IN_PROGRESS:
        return STOPPED
    return None


class ActivityFence(Middleware):
    """Refuse a tool call whose activity no longer runs, before any handler."""

    def __init__(self, universe_dir: Callable[[], Path]) -> None:
        self._universe_dir = universe_dir

    async def on_call_tool(self, context, call_next):
        import asyncio

        from fastmcp.exceptions import ToolError

        from tinyassets.engine_steering import _route_params

        session_key, _turn = _route_params()
        if session_key:
            refusal = await asyncio.to_thread(activity_refusal, self._universe_dir(), session_key)
            if refusal is not None:
                raise ToolError(refusal)
        return await call_next(context)
