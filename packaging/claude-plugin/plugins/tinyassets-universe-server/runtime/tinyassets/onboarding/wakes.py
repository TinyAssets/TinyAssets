"""The signed-in owner's complete follow-up list and cancellation door."""
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse


async def handle_wakes(request):
    from tinyassets import agent_wakes
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.auth.middleware import current_identity
    from tinyassets.onboarding import (
        _NO_STORE,
        _app_identity_required,
        _read_home,
        onboarding_enabled,
    )

    if not onboarding_enabled():
        return PlainTextResponse("Not Found", status_code=404)
    denied = _app_identity_required()
    if denied is not None:
        return denied
    owner = current_identity().user_id
    home_id = await run_in_threadpool(_read_home, current_identity())
    if not home_id:
        return JSONResponse({"error": "no_home"}, status_code=404, headers=_NO_STORE)
    home = _universe_dir(home_id)
    try:
        if request.method == "POST":
            from tinyassets.onboarding.owner_sessions import require

            require(request, owner=owner)
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"wake_id"}:
                raise ValueError("cancellation requires only wake_id")
            await run_in_threadpool(agent_wakes.cancel, home, owner, body["wake_id"])
        rows = await run_in_threadpool(agent_wakes.listing, home, owner)
        return JSONResponse({"wakes": rows}, headers=_NO_STORE)
    except PermissionError:
        return JSONResponse({"error": "owner_session_or_wake_unavailable"},
                            status_code=403, headers=_NO_STORE)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, headers=_NO_STORE)
