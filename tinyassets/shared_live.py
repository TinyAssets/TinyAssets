"""HTTP door for shared live state.

The actor is the signed-in session. A body field never names the account.
The sandboxed screen does not call this route. The signed-in shell holds one
stream and pushes the deltas in. This is not an MCP handle.

``python -m tinyassets.shared_live`` serves these same handlers for a
measurement. It refuses to start unless ``TINYASSETS_LIVE_TEST_AUTH`` is ``1``,
and the bearer it accepts (``acct:<user id>``) is not a production credential.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from starlette.responses import JSONResponse, StreamingResponse

_log = logging.getLogger(__name__)
_FLUSH_STARTED = False
_FLUSH_GUARD = threading.Lock()
_THREADS: Any = None
_TICK_ON = False


def _nodelay() -> None:
    """Send small live frames immediately.

    Nagle holds a short write until the previous one is acknowledged, and
    delayed ACKs turn that into a tenth of a second. A position update is
    one short frame.
    """
    import socket
    import sys

    frame = sys._getframe()
    while frame is not None:
        owner = frame.f_locals.get("self")
        transport = getattr(owner, "transport", None)
        get = getattr(transport, "get_extra_info", None)
        sock = get("socket") if get is not None else None
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                return
            return
        frame = frame.f_back


def _base():
    from tinyassets.storage import data_dir

    return data_dir()


def _actor() -> str:
    from tinyassets.auth.middleware import current_identity

    ident = current_identity()
    user = (getattr(ident, "user_id", "") or "").strip()
    if not user:
        raise PermissionError("Authentication required")
    return user


def _error(status: int, message: str, *, retryable: bool = False) -> JSONResponse:
    body: dict[str, Any] = {"error": message}
    if retryable:
        body["retryable"] = True
    return JSONResponse(body, status_code=status)


def _guard(exc: BaseException) -> JSONResponse | None:
    from tinyassets.shared_state import StateConflict
    from tinyassets.storage_accounting import StorageRefused

    if isinstance(exc, StateConflict):
        return _error(409, "record changed", retryable=True)
    if isinstance(exc, StorageRefused):
        return _error(409, "storage limit")
    if isinstance(exc, PermissionError):
        text = str(exc) or "record not found"
        status = 401 if "Authentication required" in text else 403
        return _error(status, text)
    if isinstance(exc, ValueError):
        return _error(400, str(exc))
    return None


def _interest(params: Any, name: str) -> list[str] | None:
    if name not in params:
        return None
    raw = params.get(name) or ""
    if raw == "":
        return []
    return [part for part in str(raw).split(",") if part]


def ensure_flusher() -> None:
    """Checkpoint transient rows and admit owed bytes off the hot path."""
    global _FLUSH_STARTED
    with _FLUSH_GUARD:
        if _FLUSH_STARTED:
            return
        _FLUSH_STARTED = True

        def loop() -> None:
            from tinyassets.shared_state import checkpoint_all, flush_pending

            tick = 0
            while True:
                time.sleep(1)
                tick += 1
                try:
                    checkpoint_all(_base())
                except Exception:
                    _log.exception("live checkpoint failed")
                if tick % 2:
                    continue
                try:
                    flush_pending(_base())
                except Exception:
                    _log.exception("live storage flush failed")

        threading.Thread(target=loop, name="app-live-flush", daemon=True).start()


def _ensure_tick() -> None:
    """One loop task copies the snapshot and hands the same bytes to every stream."""
    global _TICK_ON
    if _TICK_ON:
        return
    _TICK_ON = True
    import asyncio

    from tinyassets.shared_state import _shared_page, publish_snaps

    async def tick() -> None:
        while True:
            await asyncio.sleep(0.01)
            try:
                publish_snaps()
                groups: dict[tuple, list[_Out]] = {}
                for sub in list(_SUBS):
                    if sub.closed:
                        continue
                    kind_key = None if sub.kinds is None else tuple(sub.kinds)
                    key_key = None if sub.keys is None else tuple(sub.keys)
                    groups.setdefault(
                        (sub.universe, int(sub.cursor), kind_key, key_key), [],
                    ).append(sub)
                for (universe, cursor, kind_key, key_key), members in groups.items():
                    sample = members[0]
                    page = _shared_page(
                        sample.base, actor=sample.actor, universe_id=universe,
                        since=cursor, limit=500,
                        kinds=None if kind_key is None else list(kind_key),
                        keys=None if key_key is None else list(key_key),
                    )
                    if not page.get("events"):
                        continue
                    text = _sse(page)
                    nxt = int(page.get("cursor") or cursor)
                    for sub in members:
                        # A stream still holding a frame keeps its cursor.
                        # Advancing it would drop the events in that frame.
                        if sub.queue.qsize() >= 1:
                            continue
                        sub.cursor = nxt
                        sub.queue.put_nowait(text)
            except Exception:
                _log.exception("live snapshot failed")

    asyncio.get_running_loop().create_task(tick())


def _limiter():
    global _THREADS
    if _THREADS is None:
        from anyio import CapacityLimiter

        _THREADS = CapacityLimiter(4096)
    return _THREADS


async def _run(fn):
    from anyio.to_thread import run_sync

    return await run_sync(fn, limiter=_limiter())


def _dispatch(actor: str, payload: dict, base) -> dict:
    from tinyassets.shared_state import (
        checkpoint,
        read_record,
        scheduled_runs,
        sync,
        warm_live,
        write_record,
    )

    op = str(payload.get("op") or "")
    universe_id = str(payload.get("universe_id") or "")
    if op == "write":
        body = payload.get("body")
        return write_record(
            base, actor=actor, universe_id=universe_id,
            kind=str(payload.get("kind") or ""),
            key=str(payload.get("key") or ""),
            body=body if isinstance(body, dict) else {},
            expect_etag=int(payload.get("expect_etag") or 0),
            token=str(payload.get("token") or ""),
        )
    if op == "sync":
        return sync(
            base, actor=actor, universe_id=universe_id,
            since=int(payload.get("since") or 0),
            limit=int(payload.get("limit") or 500),
            kinds=payload.get("kinds"),
            keys=payload.get("keys"),
        )
    if op == "read":
        return read_record(
            base, actor=actor, universe_id=universe_id,
            key=str(payload.get("key") or ""),
        )
    if op == "scheduled":
        return scheduled_runs(base, actor=actor, universe_id=universe_id)
    if op == "checkpoint":
        warm_live(base, actor=actor, universe_id=universe_id)
        return {"checkpointed": checkpoint(base, universe_id)}
    raise ValueError("unknown live op")


async def handle_live(request: Any) -> JSONResponse:
    """POST ``/app/api/live``. The session is the actor."""
    ensure_flusher()
    _nodelay()
    try:
        actor = _actor()
        payload = await request.json()
    except PermissionError as exc:
        return _error(401, str(exc) or "Authentication required")
    except json.JSONDecodeError:
        return _error(400, "a live request is an object")
    if not isinstance(payload, dict):
        return _error(400, "a live request is an object")
    base = _base()
    _ensure_tick()
    try:
        # Transient applies are a few dictionary operations. Hopping to a
        # worker thread costs more than the apply, and a burst of those hops
        # queues on the loop. Durable writes still take the sqlite lock inside
        # the call; they are not the motion path.
        result = _dispatch(actor, payload, base)
    except Exception as exc:
        mapped = _guard(exc)
        if mapped is not None:
            return mapped
        raise
    return JSONResponse(result)


_SSE_LOCK = threading.Lock()
_SSE_CACHE: dict[tuple, tuple[dict, str]] = {}
_SUBS: list["_Out"] = []


class _Out:
    """One signed-in stream. The tick pushes shared frames; the stream does not poll."""

    __slots__ = ("base", "actor", "universe", "cursor", "kinds", "keys", "queue", "closed")

    def __init__(self, base, actor, universe, cursor, kinds, keys) -> None:
        import asyncio

        self.base = base
        self.actor = actor
        self.universe = universe
        self.cursor = int(cursor)
        self.kinds = kinds
        self.keys = keys
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False


def _sse(page: dict) -> str:
    """One encoding of a shared page. Sockets then write the same bytes."""
    events = page.get("events") or []
    first = events[0]["seq"] if events else 0
    last = events[-1]["seq"] if events else 0
    key = (id(page), page.get("cursor"), len(events), first, last)
    with _SSE_LOCK:
        hit = _SSE_CACHE.get(key)
        if hit is not None and hit[0] is page:
            return hit[1]
        text = "data: " + json.dumps(page, separators=(",", ":")) + "\n\n"
        if len(_SSE_CACHE) > 64:
            _SSE_CACHE.clear()
        _SSE_CACHE[key] = (page, text)
        return text


async def handle_live_stream(request: Any) -> Any:
    """GET ``/app/api/live/stream``. Server push of one interest, as SSE.

    One tick builds a page and enqueues that text on every matching stream.
    A quiet stream ends after about 20 seconds so the shell reconnects with
    its cursor. The screen still has no socket of its own.
    """
    ensure_flusher()
    _nodelay()
    try:
        actor = _actor()
    except PermissionError as exc:
        return _error(401, str(exc) or "Authentication required")
    params = request.query_params
    universe_id = str(params.get("universe_id") or "")
    try:
        since = int(params.get("since") or 0)
    except ValueError:
        return _error(400, "since must be an integer")
    kinds = _interest(params, "kinds")
    keys = _interest(params, "keys")
    base = _base()
    _ensure_tick()
    try:
        await _run(lambda: _prime(base, actor, universe_id))
    except Exception as exc:
        mapped = _guard(exc)
        if mapped is not None:
            return mapped
        raise

    sub = _Out(base, actor, universe_id, since, kinds, keys)
    _SUBS.append(sub)

    async def generate():
        # A first chunk makes the status line observable before the wait, so a
        # client blocked on headers cannot deadlock a writer that has not run.
        # The roster and transient rows were loaded before this.
        import asyncio

        yield ": ok\n\n"
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    return
                timeout = min(2.0, max(0.05, deadline - time.monotonic()))
                try:
                    text = await asyncio.wait_for(sub.queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                yield text
        finally:
            sub.closed = True
            try:
                _SUBS.remove(sub)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _prime(base, actor, universe_id) -> None:
    from tinyassets.shared_state import warm_live

    warm_live(base, actor=actor, universe_id=universe_id)


def measurement_app():
    """The real handlers behind the real auth middleware, with a test bearer.

    Production does not call this. The daemon mounts ``handle_live`` and
    ``handle_live_stream`` on its own provider.
    """
    if os.environ.get("TINYASSETS_LIVE_TEST_AUTH") != "1":
        raise RuntimeError("measurement server is not enabled")
    from starlette.applications import Starlette
    from starlette.routing import Route

    from tinyassets.auth.middleware import AuthContextMiddleware, set_provider
    from tinyassets.auth.provider import Identity

    class _AcctBearer:
        """Resolve ``acct:<user id>`` for a measurement process only."""

        def resolve_token(self, token):
            if not token or not str(token).startswith("acct:"):
                return None
            user = str(token)[5:]
            if not user:
                return None
            return Identity(user_id=user, username=user, capabilities=["read", "write"])

        def is_auth_required(self):
            return True

        def resolve_always_writes(self):
            return False

        def writes_require_identity(self):
            return True

        def challenge_unauthenticated(self):
            return True

    set_provider(_AcctBearer())
    app = Starlette(routes=[
        Route("/app/api/live", handle_live, methods=["POST"]),
        Route("/app/api/live/stream", handle_live_stream, methods=["GET"]),
    ])
    return AuthContextMiddleware(app)


def main() -> None:
    if os.environ.get("TINYASSETS_LIVE_TEST_AUTH") != "1":
        raise SystemExit("measurement server is not enabled")
    import uvicorn

    port = int(os.environ["PORT"])
    uvicorn.run(
        measurement_app(), host="127.0.0.1", port=port,
        log_level="warning", access_log=False,
    )


if __name__ == "__main__":
    main()
