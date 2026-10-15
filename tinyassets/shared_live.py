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
    """Admit owed bytes on a timer. A live write does not call the ledger."""
    global _FLUSH_STARTED
    with _FLUSH_GUARD:
        if _FLUSH_STARTED:
            return
        _FLUSH_STARTED = True

        def loop() -> None:
            from tinyassets.shared_state import flush_pending

            while True:
                time.sleep(2)
                try:
                    flush_pending(_base())
                except Exception:
                    _log.exception("live storage flush failed")

        threading.Thread(target=loop, name="app-live-flush", daemon=True).start()


def _limiter():
    global _THREADS
    if _THREADS is None:
        from anyio import CapacityLimiter

        _THREADS = CapacityLimiter(1024)
    return _THREADS


async def _run(fn):
    from anyio.to_thread import run_sync

    return await run_sync(fn, limiter=_limiter())


def _dispatch(actor: str, payload: dict, base) -> dict:
    from tinyassets.shared_state import read_record, scheduled_runs, sync, write_record

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
    raise ValueError("unknown live op")


async def handle_live(request: Any) -> JSONResponse:
    """POST ``/app/api/live``. The session is the actor."""
    ensure_flusher()
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
    try:
        result = await _run(lambda: _dispatch(actor, payload, base))
    except Exception as exc:
        mapped = _guard(exc)
        if mapped is not None:
            return mapped
        raise
    return JSONResponse(result)


def _sse(page: dict) -> str:
    return "data: " + json.dumps(page, separators=(",", ":")) + "\n\n"


async def handle_live_stream(request: Any) -> Any:
    """GET ``/app/api/live/stream``. Server push of one interest, as SSE.

    The handler waits on the writer. It does not poll the log. A quiet stream
    ends after about 20 seconds so the shell reconnects with its cursor.
    """
    ensure_flusher()
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

    async def generate():
        # A first chunk makes the status line observable before the wait, so a
        # client blocked on headers cannot deadlock a writer that has not run.
        yield ": ok\n\n"
        cursor = since
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            timeout = min(2.0, max(0.05, deadline - time.monotonic()))
            try:
                page = await _run(lambda cursor=cursor, timeout=timeout: _wait(
                    base, actor, universe_id, cursor, kinds, keys, timeout,
                ))
            except Exception as exc:
                mapped = _guard(exc)
                if mapped is None:
                    raise
                detail = json.loads(mapped.body.decode("utf-8"))
                yield _sse({"events": [], "cursor": cursor, "error": detail.get("error")})
                return
            cursor = int(page.get("cursor") or cursor)
            if page.get("events"):
                yield _sse(page)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _wait(base, actor, universe_id, cursor, kinds, keys, timeout):
    from tinyassets.shared_state import wait_live

    return wait_live(
        base, actor=actor, universe_id=universe_id, since=cursor,
        kinds=kinds, keys=keys, timeout=timeout, limit=500,
    )


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
