"""Protected owner-box native login handoff; the router only forwards this door."""
import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def finish(source_id):
    from tinyassets.api.connection_uses import select_model_if_unpowered
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.owner_runtime import current
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime
    from tinyassets.shared_self import require_founder_home

    runtime = current()
    source = SubscriptionRuntime(source_id)
    if not source.authenticated():
        raise PermissionError("native login is not connected")
    center = get_founder_home(runtime.root, runtime.principal)
    require_founder_home(runtime.root, center, runtime.principal)
    models = source.source["models"]
    definition = register_definition(
        universe_id=center, owner_user_id=runtime.principal,
        access_method="subscription_cli", protocol="cli_json", ref=source_id,
        model=models[0]["id"])
    result = select_model_if_unpowered(base=runtime.root, uid=center, actor=runtime.principal,
                                      definition_id=definition.id, model={"models": models})
    if result.get("status") not in {"serving", "unchanged"}:
        return {"status": "setup_required", "provider": "subscription_cli:" + definition.id}
    return {"status": "connected", "provider": "subscription_cli:" + definition.id}


async def handle(request):
    from tinyassets import onboarding
    from tinyassets.auth.middleware import current_identity
    from tinyassets.onboarding.owner_sessions import require
    from tinyassets.owner_runtime import current
    from tinyassets.providers.subscription_runtime import SubscriptionError, login_operation

    runtime, identity = current(), current_identity()
    if runtime is None or identity is None or identity.user_id != runtime.principal:
        return JSONResponse({"error": "owner_box_required"}, 403, headers=HEADERS)
    try:
        require(request, owner=runtime.principal)
    except PermissionError:
        return JSONResponse({"error": "interactive_approval_required"}, 403, headers=HEADERS)
    action = request.path_params["operation"]
    fields = {"begin": {"source_id"}, "poll": {"flow"}, "cancel": {"flow"},
              "code": {"flow", "code"}, "finish": {"flow"}}
    try:
        raw = await onboarding._read_bounded_body(request, 4096)
        doc = json.loads(raw) if raw is not None else None
        if (action not in fields or not isinstance(doc, dict) or set(doc) != fields[action]
                or any(not isinstance(v, str) or not 0 < len(v) <= 2048 for v in doc.values())):
            raise ValueError("invalid login request")
        if action == "finish":
            state = login_operation("poll", flow=doc["flow"])
            if state["status"] != "connected":
                raise PermissionError("native login is not complete")
            result = await run_in_threadpool(finish, state["source_id"])
        else:
            result = login_operation(action, **doc)
        return JSONResponse(result, headers=HEADERS)
    except (ValueError, KeyError, TypeError, PermissionError, SubscriptionError):
        return JSONResponse({"error": "subscription_connection_incomplete"}, 409, headers=HEADERS)
