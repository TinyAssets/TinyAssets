"""Protected first-party approval door; no MCP token minting alias."""

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from tinyassets import bound_requests
from tinyassets.auth.middleware import current_identity, identity_context
from tinyassets.onboarding.owner_sessions import HEADERS, require
from tinyassets.owner_control import ControlUnavailable


async def handle_approval(request):
    from tinyassets.api.pending_requests import _owner_gate
    from tinyassets.onboarding import _app_identity_required, _read_small_json, onboarding_enabled

    if not onboarding_enabled():
        return JSONResponse({"error": "not_found"}, status_code=404)
    denied = _app_identity_required()
    if denied is not None:
        return denied
    identity = current_identity()
    try:
        session = require(request, owner=identity.user_id)
    except PermissionError:
        return JSONResponse(
            {"error": "interactive_approval_required"}, status_code=403, headers=HEADERS
        )
    data = await _read_small_json(request, limit=80000)
    if data is None:
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=HEADERS)

    def perform():
        with identity_context(identity):
            _, home, denied = _owner_gate(str(data.get("universe_id", "")))
            if denied:
                return denied
            operation = request.path_params["operation"]
            if operation == "preview":
                return bound_requests.preview(
                    home, data.get("request_id", ""), session, scope=data.get("scope", "once")
                )
            if operation == "edit":
                return bound_requests.preview(
                    home, data.get("request_id", ""), session, draft=data.get("draft"), edit=True,
                    scope=data.get("scope", "once")
                )
            if operation == "decide":
                return bound_requests.decide(home, data, session)
            raise bound_requests.RequestRefused("Unknown approval operation.")

    try:
        result = await run_in_threadpool(perform)
        return JSONResponse(
            result, status_code=404 if result.get("error") else 200, headers=HEADERS
        )
    except bound_requests.RequestRefused as exc:
        return JSONResponse(
            {"error": "preview_required", "detail": str(exc)}, status_code=409, headers=HEADERS
        )
    except ControlUnavailable as exc:
        return JSONResponse(
            {"error": exc.kind, "retryable": True}, status_code=503, headers=HEADERS
        )
