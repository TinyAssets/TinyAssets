"""Tier A app rooms, answered on the trusted side (ADR-025, ADR-030; registry row S34).

A published app's room is the ``room:<slug>:main`` collection in its publisher's
broker-held conversation store. ``/app/<slug>/state`` is an ``owner_router.TRUSTED``
registration: the router picks the store from the published directory and the
writer from its verified identity, exactly as it once picked the box, so a room
read, write or pull never reaches, asks or wakes a box. A room record is shared;
a player record is private to the player who wrote it.
"""
from __future__ import annotations

import hashlib
import json
import re

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

ROOM = "main"
MAX_VALUE = 64 * 1024
PAGE = 100
_LOCAL = re.compile(r"[A-Za-z0-9_-]{1,64}")
_HEADERS = {"Cache-Control": "no-store"}


def _row_id(scope, key, caller: str) -> str:
    if not isinstance(key, str) or _LOCAL.fullmatch(key) is None:
        raise ValueError("invalid state key")
    if scope == "room":
        return f"room:{key}"
    if scope == "player":
        return f"player:{hashlib.sha256(caller.encode()).hexdigest()[:16]}:{key}"
    raise ValueError("invalid state scope")


def write(request, slug: str, *, scope, key, value, expected_etag, caller: str) -> dict:
    """Compare-and-swap one record; ``request`` is the publisher's collection call."""
    if not isinstance(expected_etag, str) or not isinstance(value, dict):
        raise ValueError("invalid state write")
    body = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(body.encode()) > MAX_VALUE:
        raise ValueError("state value is too large")
    row = request("write", collection=f"room:{slug}:{ROOM}", row_id=_row_id(scope, key, caller),
                  body=body, expected_etag=expected_etag, private=scope == "player")["row"]
    return {"scope": scope, "key": key, "etag": row["etag"], "seq": row["seq"],
            "writer": row["writer"]}


def clear(request, slug: str, *, writer=None) -> dict:
    """The publisher clears the room, or one player's rows in it."""
    if writer is not None and (not isinstance(writer, str) or not writer):
        raise ValueError("invalid writer")
    return request("clear", collection=f"room:{slug}:{ROOM}", writer=writer)


def pull(request, slug: str, *, since: int) -> dict:
    """Room records, and the caller's own player records, after ``since``."""
    if type(since) is not int or since < 0:
        raise ValueError("invalid sync cursor")
    page = request("pull", collection=f"room:{slug}:{ROOM}", after=since, limit=PAGE)
    records = []
    for row in page["rows"]:
        scope, _, rest = row["row_id"].partition(":")
        records.append({"scope": scope, "key": rest.partition(":")[2] if scope == "player"
                        else rest, "value": json.loads(row["body"]), "etag": row["etag"],
                        "writer": row["writer"], "seq": row["seq"]})
    return {"seq": page["cursor"], "records": records}


def _answer(body: dict, status: int = 200) -> JSONResponse:
    headers = dict(_HEADERS, **({"Retry-After": "5"} if status == 503 else {}))
    return JSONResponse(body, status_code=status, headers=headers)


async def handle(request) -> JSONResponse:
    """``GET /app/<slug>/state?since=`` pulls; ``POST`` compare-and-swaps one record.

    ``DELETE`` (``{}`` or ``{"writer": ...}``) is the publisher's own, from their
    protected owner session: it clears the room or one player's rows.
    """
    from functools import partial

    from tinyassets.app_access import validate_slug
    from tinyassets.auth.middleware import current_identity_or_none, published_box_owner
    from tinyassets.broker.client import BrokerRefused, CollectionConflict, collection_request
    from tinyassets.onboarding import _read_small_json, owner_sessions
    from tinyassets.storage import data_dir

    identity = current_identity_or_none()
    if identity is None:  # as the router answers an unsigned owner door
        return _answer({"error": "not_found"}, 404)
    caller = identity.user_id
    # A bearer beside another account's session cookie is refused, as at the box door.
    session = await run_in_threadpool(owner_sessions.lookup,
                                      request.cookies.get(owner_sessions.COOKIE, ""))
    if session is not None and json.loads(session["identity_json"])["user_id"] != caller:
        return _answer({"error": "identity_conflict"}, 401)
    try:
        slug = validate_slug(request.path_params["slug"])
    except ValueError:
        return _answer({"error": "not_found"}, 404)
    try:
        root = data_dir()
        # A published slug is its publisher's room; any other is the caller's own.
        owner = await run_in_threadpool(published_box_owner, root, request.scope["path"], caller)
        call = partial(collection_request, root, identity, owner=owner)
        if request.method == "DELETE":
            if owner != caller:
                return _answer({"error": "private_surface"}, 403)
            try:
                await run_in_threadpool(owner_sessions.require, request, owner=caller)
            except PermissionError:
                return _answer({"error": "interactive_owner_session_required"}, 403)
            body = await _read_small_json(request)
            if body is None or set(body) - {"writer"}:
                raise ValueError("invalid room clear")
            result = await run_in_threadpool(clear, call, slug, **body)
        elif request.method == "GET":
            raw = request.query_params.get("since", "0")
            if not raw.isdecimal():
                raise ValueError("invalid sync cursor")
            result = await run_in_threadpool(pull, call, slug, since=int(raw))
        else:
            body = await _read_small_json(request, limit=2 * MAX_VALUE)
            if body is None or set(body) != {"scope", "key", "value", "expected_etag"}:
                raise ValueError("invalid state write")
            result = await run_in_threadpool(write, call, slug, caller=caller, **body)
    except CollectionConflict as exc:
        return _answer({"error": "conflict", "etag": exc.etag}, 409)
    except BrokerRefused as exc:
        if str(exc) == "ledger unavailable":
            return _answer({"error": "room_unavailable"}, 503)
        return _answer({"error": "refused", "reason": str(exc)}, 403)
    except ValueError:
        return _answer({"error": "invalid"}, 400)
    except (OSError, RuntimeError):
        return _answer({"error": "room_unavailable"}, 503)
    return _answer(result)
