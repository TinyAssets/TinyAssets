"""Deliver the owner's mid-turn messages on the next engine tool result (S2).

``tinyassets.agent_steering`` owns the queue. This middleware is where a queued
message reaches the agent: as one extra text block after the result of the
next tool call made by the turn it steers. Every tool carries it, including
``read``/``write``/``edit``/``bash`` and a call that ends in an error, so the
agent hears its owner within one tool call whatever it is doing.

Which session and which turn a call belongs to come from the engine route the
launch used (``?session=...&turn=...`` on the loopback URL), set by the
platform for that one launch and never by the model. Only the owner's chat
thread (``thread:...``) of a live turn is steered; a call from one of the
universe's own agent nodes, or with no turn, passes through untouched.

OUTERMOST, so the block is added after every other middleware has shaped the
result. Its size is bounded by ``agent_steering.DELIVERY_BUDGET_CHARS``.
"""
from __future__ import annotations

import asyncio
import logging

from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)

#: Query parameters on the engine route naming this launch's session and turn.
SESSION_PARAM = "session"
TURN_PARAM = "turn"
#: Only the owner's conversation thread is steered.
STEERED_PREFIX = "thread:"


def route_with_session(url: str, session_key: str, turn: str = "") -> str:
    """``url`` naming ``session_key`` (and the live ``turn``) for one launch."""
    from urllib.parse import quote

    key = str(session_key or "").strip()
    if not key:
        return url
    joiner = "&" if "?" in url else "?"
    out = f"{url}{joiner}{SESSION_PARAM}={quote(key, safe='')}"
    live = str(turn or "").strip()
    if live:
        out += f"&{TURN_PARAM}={quote(live, safe='')}"
    return out


def session_of(config) -> str:
    """The session key a launch continues (``ModelConfig.agent_session``), or ""."""
    return str(getattr(getattr(config, "agent_session", None), "key", "") or "")


def turn_of() -> str:
    """The live id of the served interactive turn this launch runs under, or ""."""
    from tinyassets import turn_interrupt

    live = turn_interrupt.current()
    return str(getattr(live, "live_id", "") or "")


def _route_params() -> tuple[str, str]:
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    except Exception:  # noqa: BLE001 - stdio, or no request: nothing to steer
        return "", ""
    params = request.query_params
    return (str(params.get(SESSION_PARAM) or "").strip(),
            str(params.get(TURN_PARAM) or "").strip())


def _session_key() -> str:
    return _route_params()[0]


def _take(session_key: str, turn: str) -> str | None:
    from tinyassets import agent_steering
    from tinyassets.engine_conversation_attention import _scope

    try:
        scope = _scope()
        if scope is None:
            return None
        root, _thread = scope
        messages = agent_steering.take(root, session_key, turn)
    except Exception:  # noqa: BLE001 - a completed call never fails over steering
        logger.warning("owner steering unavailable", exc_info=True)
        return None
    return agent_steering.render(messages) if messages else None


class OwnerSteering(Middleware):
    async def on_call_tool(self, context, call_next):
        from fastmcp.exceptions import ToolError

        session_key, turn = _route_params()
        steered = session_key.startswith(STEERED_PREFIX) and bool(turn)
        try:
            result = await call_next(context)
        except ToolError as exc:
            block = await asyncio.to_thread(_take, session_key, turn) if steered else None
            if block is None:
                raise
            raise ToolError(f"{exc}\n\n{block}") from exc
        if not steered:
            return result
        block = await asyncio.to_thread(_take, session_key, turn)
        if block is None:
            return result
        from mcp.types import TextContent

        result.content = [*(result.content or ()), TextContent(type="text", text=block)]
        return result
