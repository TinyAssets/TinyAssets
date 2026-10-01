"""Deliver the owner's mid-turn messages on the next engine tool result (S2).

``tinyassets.agent_steering`` owns the queue. This middleware is where a queued
message reaches the agent: as one extra text block after the result of the
next tool call made by the session it steers. Every tool carries it, including
``read``/``write``/``edit``/``bash``, so the agent hears its owner within one
tool call whatever it is doing.

Which session a call belongs to comes from the engine route the launch used
(``?session=`` on the loopback URL, set by the platform, never by the model).
Only the owner's chat thread (``thread:...``) is steered; a call from one of the
universe's own agent nodes, or with no session, passes through untouched.

OUTERMOST, so the block is added after every other middleware has shaped the
result and a bounded result still carries the message whole.
"""
from __future__ import annotations

import asyncio
import logging

from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)

#: Query parameter on the engine route naming the session of this launch.
SESSION_PARAM = "session"
#: Only the owner's conversation thread is steered.
STEERED_PREFIX = "thread:"


def route_with_session(url: str, session_key: str) -> str:
    """``url`` naming ``session_key``, for a launch that continues that session."""
    from urllib.parse import quote

    key = str(session_key or "").strip()
    if not key:
        return url
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}{SESSION_PARAM}={quote(key, safe='')}"


def session_of(config) -> str:
    """The session key a launch continues (``ModelConfig.agent_session``), or ""."""
    return str(getattr(getattr(config, "agent_session", None), "key", "") or "")


def _session_key() -> str:
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    except Exception:  # noqa: BLE001 - stdio, or no request: nothing to steer
        return ""
    return str(request.query_params.get(SESSION_PARAM) or "").strip()


def _take(session_key: str) -> str | None:
    from tinyassets import agent_steering
    from tinyassets.engine_conversation_attention import _scope

    try:
        scope = _scope()
        if scope is None:
            return None
        root, _thread = scope
        messages = agent_steering.take(root, session_key)
    except Exception:  # noqa: BLE001 - a completed call never fails over steering
        logger.warning("owner steering unavailable", exc_info=True)
        return None
    return agent_steering.render(messages) if messages else None


class OwnerSteering(Middleware):
    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        session_key = _session_key()
        if not session_key.startswith(STEERED_PREFIX):
            return result
        block = await asyncio.to_thread(_take, session_key)
        if block is None:
            return result
        from mcp.types import TextContent

        result.content = [*(result.content or ()), TextContent(type="text", text=block)]
        return result
