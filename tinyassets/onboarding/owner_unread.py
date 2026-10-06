"""Authenticated, account-bound unread counts and exact-ID view acknowledgements."""
from __future__ import annotations


async def handle_unread(request):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, Response

    from tinyassets import onboarding
    from tinyassets.api.helpers import _base_path
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.shared_self import require_founder_home
    from tinyassets.storage.owner_unread import attention

    headers = {"Cache-Control": "no-store"}
    if not onboarding.onboarding_enabled():
        return Response(status_code=404, headers=headers)
    denied = onboarding._app_identity_required()
    if denied is not None:
        return denied
    identity = current_identity()
    if request.method == "POST":
        resource = str(onboarding.app_config().get("resource") or "")
        if not onboarding._same_origin_json(request, resource):
            return JSONResponse({"error": "cross_origin_rejected"}, 403, headers=headers)
        data = await onboarding._read_small_json(request)
    else:
        data = dict(request.query_params)
    if not isinstance(data, dict) or not isinstance(data.get("universe"), str):
        return JSONResponse({"error": "invalid_request"}, 400, headers=headers)
    messages, asks = data.get("messages", []), data.get("asks", [])
    if any(not isinstance(ids, list) or len(ids) > 500 or
           any(not isinstance(i, str) or len(i) > 200 for i in ids)
           for ids in (messages, asks)):
        return JSONResponse({"error": "invalid_receipts"}, 400, headers=headers)

    def run():
        from tinyassets.api.pending_requests import list_requests

        base = _base_path()
        root = require_founder_home(base, data["universe"], identity.user_id)
        with identity_context(identity):
            projection = list_requests(universe_id=data["universe"])
        if "pending" not in projection:
            raise RuntimeError("unread_requests_unavailable")
        return attention(base, root, identity.user_id, data["universe"],
                         messages=messages, asks=asks, pending=projection["pending"])

    try:
        result = await run_in_threadpool(run)
    except PermissionError:
        return JSONResponse({"error": "not_found"}, 404, headers=headers)
    return JSONResponse(result, headers=headers)
