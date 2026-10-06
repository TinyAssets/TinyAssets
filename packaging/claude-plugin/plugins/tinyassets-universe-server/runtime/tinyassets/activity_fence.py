"""The activity pre-tool fence on the engine route.

Every model-visible tool of every provider executes on the owner's engine
route: HTTP agent rounds and Codex dynamic tools through
``engine_tool_client``, Claude through its one strict MCP server. A launch
made for an activity names the activity's session (``activity:<id>``) on that
route. Once the activity yields to an owner request, pauses or stops, its
status leaves ``in_progress`` and this fence refuses every further tool call
before the handler runs. A native agent's internal loop therefore cannot act
after the yield, whichever provider runs it; it can only finish its reply.

Three more boundaries close what a single pre-call check leaves open:

* An activity's top-level calls run one at a time, so a call queued behind
  the one that yields is checked after the yield, not before it.
* A running ``bash`` polls :func:`stop_check` and is killed once the activity
  stops (``universe_tools.run_jailed``), so the rest of a command that yielded
  part-way does not keep acting.
* Every ``ta`` request from inside that command is checked again
  (``ta_capabilities.Capabilities``), including connection calls, which do not
  re-enter this route.
"""

from __future__ import annotations

import asyncio
import contextvars
import weakref
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


def stop_check(universe_dir: Path, session_key: str) -> Callable[[], str | None] | None:
    """A poll that names why ``session_key``'s activity stopped, or ``None``
    when the session is not an activity's."""
    if not session_key.startswith(ACTIVITY_SESSION_PREFIX):
        return None
    return lambda: activity_refusal(universe_dir, session_key)


#: Set while an activity's top-level call runs: a ``ta`` platform call nested
#: inside it re-enters this route and must not wait on its own parent.
_INSIDE = contextvars.ContextVar("activity_call_inside", default=False)


class ActivityFence(Middleware):
    """Refuse a tool call whose activity no longer runs, before any handler."""

    def __init__(self, universe_dir: Callable[[], Path]) -> None:
        self._universe_dir = universe_dir
        self._serial: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary())

    async def on_call_tool(self, context, call_next):
        from tinyassets.engine_steering import _route_params

        session_key, _turn = _route_params()
        if not session_key.startswith(ACTIVITY_SESSION_PREFIX):
            return await call_next(context)
        if _INSIDE.get():
            await self._admit(session_key)
            return await call_next(context)
        lock = self._serial.get(session_key)
        if lock is None:
            lock = self._serial[session_key] = asyncio.Lock()
        async with lock:
            await self._admit(session_key)
            token = _INSIDE.set(True)
            try:
                return await call_next(context)
            finally:
                _INSIDE.reset(token)

    async def _admit(self, session_key: str) -> None:
        from fastmcp.exceptions import ToolError

        refusal = await asyncio.to_thread(activity_refusal, self._universe_dir(), session_key)
        if refusal is not None:
            raise ToolError(refusal)
