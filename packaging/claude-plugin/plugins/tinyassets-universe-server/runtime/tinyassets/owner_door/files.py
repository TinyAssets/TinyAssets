"""Opaque, owner-only downloads of the files the agent sees in /u."""

from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from tinyassets.owner_door.routes import _BYTE_HEADERS, _HEADERS, _MAX_ARGUMENT_BYTES


async def handle_file(request):
    from tinyassets import onboarding
    from tinyassets.api import universe_file_reads as files
    from tinyassets.auth.middleware import current_identity, identity_context

    if not onboarding.onboarding_enabled():
        return Response(status_code=404, headers=_HEADERS)
    denied = onboarding._app_identity_required()
    if denied is not None:
        denied.headers.update(_HEADERS)
        return denied
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        return JSONResponse({"error": "json_required"}, 415, headers=_HEADERS)
    raw = await onboarding._read_bounded_body(request, _MAX_ARGUMENT_BYTES)
    if raw is None:
        return JSONResponse({"error": "arguments_too_large"}, 413, headers=_HEADERS)
    try:
        args = json.loads(raw)
        if (
            not isinstance(args, dict)
            or set(args) != {"graph_id", "path"}
            or not all(isinstance(value, str) and value for value in args.values())
        ):
            raise ValueError("invalid arguments")
    except (ValueError, UnicodeError):
        return JSONResponse({"error": "invalid_arguments"}, 400, headers=_HEADERS)
    identity = current_identity()

    def read():
        with identity_context(identity):
            owned = files._owner_universe(args["graph_id"])
            rel = files._relative(args["path"])
            if not isinstance(owned, tuple) or not rel:
                return None
            uid, folder = owned
            try:
                return files._read(folder.parent, uid, files._logical(folder, rel))
            except OSError:
                return None

    data = await run_in_threadpool(read)
    if data is None:
        return JSONResponse({"error": "not_found"}, 404, headers=_HEADERS)
    return Response(data, headers=dict(_BYTE_HEADERS))
