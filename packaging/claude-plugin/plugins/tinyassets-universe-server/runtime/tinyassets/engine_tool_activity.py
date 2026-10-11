"""Record every engine tool call for the owner's live view (harness S4).

``tinyassets.agent_activity`` owns the log. This middleware writes one row when
a call starts and completes it when the call ends, for whichever session the
launch named on its engine route (``engine_steering``). A call with no session
(stdio, an old launch) is not recorded. A failed start write refuses dispatch.
A missing terminal record leaves an
explicitly uncertain outcome for the next turn.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging

from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)

#: Set by this middleware around each call. A raw tool (read/write/edit/bash)
#: answers a refusal as plain text, so its handler records the refusal here
#: (``note_refusal``) instead of the outcome being guessed from output text.
_REFUSALS: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "tinyassets_tool_refusals", default=None)


def note_refusal(message: str) -> None:
    """A tool handler refused this call; record why for the activity log."""
    holder = _REFUSALS.get()
    if holder is not None:
        holder.append(str(message))


def _root():
    from tinyassets.engine_conversation_attention import _scope

    scope = _scope()
    return None if scope is None else scope[0]


def _start(session_key: str, tool: str, arguments) -> tuple | None:
    from tinyassets import agent_activity
    from tinyassets.engine_steering import _route_params

    try:
        root = _root()
        if root is None:
            return None
        call_id = agent_activity.started(
            root, session_key, tool, agent_activity.summarize(tool, arguments),
            turn_id=_route_params()[1])
        return root, call_id
    except Exception:  # noqa: BLE001 - log the storage cause, then refuse dispatch
        logger.warning("tool activity could not be recorded", exc_info=True)
        raise


def _finish(handle, *, ok: bool, error: str = "", result_text="") -> None:
    from tinyassets import agent_activity

    if handle is None:
        return
    try:
        agent_activity.finished(handle[0], handle[1], ok=ok, error=error, result_text=result_text)
    except Exception:  # noqa: BLE001
        logger.warning("tool activity could not be completed", exc_info=True)


def _error_text(result) -> str:
    """The first text block of an error result: its real cause."""
    for block in getattr(result, "content", None) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            return text
    return "the tool reported an error"


class ToolActivity(Middleware):
    async def on_call_tool(self, context, call_next):
        from tinyassets.engine_steering import _session_key

        session_key = _session_key()
        if not session_key:
            return await call_next(context)
        message = context.message
        tool = getattr(message, "name", "") or ""
        starting = asyncio.ensure_future(asyncio.to_thread(
            _start, session_key, tool, getattr(message, "arguments", None)))
        try:
            handle = await asyncio.shield(starting)
        except asyncio.CancelledError:
            # Cancelled while the row was being written: finish what was started.
            handle = await asyncio.gather(starting, return_exceptions=True)
            _finish(handle[0] if not isinstance(handle[0], BaseException) else None,
                    ok=False, error="cancelled")
            raise
        refusals: list[str] = []
        token = _REFUSALS.set(refusals)
        try:
            result = await call_next(context)
        except asyncio.CancelledError:
            _finish(handle, ok=False, error="cancelled before it finished")
            raise
        except Exception as exc:
            await asyncio.to_thread(_finish, handle, ok=False, error=str(exc))
            raise
        finally:
            _REFUSALS.reset(token)
        failed = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
        error = refusals[0] if refusals else (_error_text(result) if failed else "")
        text = '\n'.join(block.text for block in (getattr(result, 'content', None) or ())
                         if isinstance(getattr(block, 'text', None), str))
        await asyncio.to_thread(_finish, handle, ok=not (failed or refusals), error=error,
                                result_text=text)
        return result
