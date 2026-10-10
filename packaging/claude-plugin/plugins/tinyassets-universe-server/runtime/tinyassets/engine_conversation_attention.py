"""Put ``owner_unread`` on every JSON engine tool result.

How a running universe agent learns its owner said something new: the count of
the owner's messages it has not read rides on the next result it reads, so
steering lands at a tool boundary and nothing is interrupted
(``tinyassets.conversation_attention`` owns the count and what marks a message
read).

One field, inserted as the first key of the result's single JSON object; the
rest of the text passes through byte-for-byte. OUTERMOST, so a conversation
read is acknowledged only from the bounded text the agent actually received,
and a refusal raised by ``RefusalsAreErrors`` carries the count too. Carried
by no result whose text is a file's or a command's bytes
(``_RAW_CONTENT_TOOLS``), and by none from a server without current serving
authority. A count that cannot be read is left off, never reported as zero.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)

FIELD = "owner_unread"


def with_count(text, count):
    """``text`` (a JSON object) with ``owner_unread`` as its first key, or None."""
    if count is None or not isinstance(text, str):
        return None
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        document = json.loads(stripped)
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict) or FIELD in document:
        return None
    head = '{"%s": %d' % (FIELD, int(count))
    return head + "}" if not document else head + ", " + stripped[1:].lstrip()


def _scope():
    """(universe root, owner thread) from the verified server pins, or None."""
    from tinyassets.engine_endpoint import current_server

    server = current_server()

    if server._binding_error():
        return None
    from tinyassets.api.branches import _base_path
    from tinyassets.shared_self import require_founder_home

    root = require_founder_home(_base_path(), server._GRAPH_ID, server._ACTOR_ID)
    return root, f"principal:{server._ACTOR_ID}"


def _observe(text, reading):
    from tinyassets.conversation_attention import acknowledge, returned_page, unread_count

    try:
        scope = _scope()
        if scope is None:
            return None
        root, session = scope
        if reading:
            acknowledge(root, session, returned_page(text))
        return unread_count(root, session)
    except Exception:  # noqa: BLE001 - never fail a completed call over a count
        logger.warning("owner_unread unavailable", exc_info=True)
        return None


class ConversationAttention(Middleware):
    async def on_call_tool(self, context, call_next):
        from fastmcp.exceptions import ToolError

        from tinyassets.engine_mcp_server import _RAW_CONTENT_TOOLS

        message = context.message
        tool = getattr(message, "name", "") or ""
        arguments = getattr(message, "arguments", None) or {}
        reading = (
            tool == "read_graph"
            and str(arguments.get("target", "")).strip().lower() == "conversation"
        )
        try:
            result = await call_next(context)
        except ToolError as exc:
            marked = with_count(str(exc), await asyncio.to_thread(_observe, None, False))
            if marked is None:
                raise
            raise ToolError(marked) from exc
        if tool in _RAW_CONTENT_TOOLS:
            return result
        blocks = list(result.content or ())
        text = getattr(blocks[0], "text", None) if len(blocks) == 1 else None
        if not isinstance(text, str):
            return result
        marked = with_count(text, await asyncio.to_thread(_observe, text, reading))
        if marked is None:
            return result
        result.content = [blocks[0].model_copy(update={"text": marked})]
        structured = result.structured_content
        if isinstance(structured, dict):
            result.structured_content = {
                key: marked if value == text else value
                for key, value in structured.items()
            }
        return result
