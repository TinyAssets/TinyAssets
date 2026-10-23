"""Protected owner-box native login handoff; the router only forwards this door."""
import json
import logging
import traceback

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
_SAFE_DETAILS = {"invalid login request", "native login is not complete",
                 "native login is not connected", "subscription sign-in required",
                 "not enough free storage to install this source",
                 "CLI package lock failed", "CLI installation failed",
                 "installed CLI version verification failed"}


def _failure_detail(exc):
    frame = traceback.extract_tb(exc.__traceback__)[-1]
    message = str(exc) if str(exc) in _SAFE_DETAILS else "connection failed"
    return f"{type(exc).__name__}@{frame.filename}:{frame.lineno}: {message}"


def finish(source_id):
    from tinyassets.api.connection_uses import select_model_if_unpowered
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.owner_runtime import current
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime
    from tinyassets.shared_self import require_founder_home

    runtime = current()
    source = SubscriptionRuntime(source_id)
    source.require_accepted()
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


def connected_sources():
    """The subscription sources this owner's box has connected (no CLI is run)."""
    from tinyassets.owner_runtime import current
    from tinyassets.providers.subscription_source import sources

    home = current().root
    return {"sources": [{"id": doc["id"], "name": doc["display_name"]}
                        for doc in sources(home).values()
                        if (home / ".ta-cli" / doc["id"] / "connection").is_file()]}


def disconnect(source_id):
    """Remove one subscription source's login and owner-local installation.

    Only the login files the source's own record declares are removed, and only
    inside the owner's box. The provider account itself is unchanged.
    """
    import shutil

    from tinyassets.owner_runtime import current
    from tinyassets.providers.subscription_source import sources

    home = current().root
    doc = sources(home)[source_id]
    resolved_home = home.resolve()
    for path in doc["state"]["paths"]:
        target = (home / path).resolve()
        if not target.relative_to(resolved_home).parts:
            raise ValueError("login state cannot be the owner home")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
    directory = home / ".ta-cli" / doc["id"]
    if not directory.resolve().relative_to(resolved_home).parts:
        raise ValueError("CLI installation cannot be the owner home")
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists():
        shutil.rmtree(directory)
    return {"status": "disconnected", "source_id": doc["id"]}


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
    fields = {"resolve": {"address"}, "begin": {"source_id"}, "poll": {"flow"}, "cancel": {"flow"},
              "code": {"flow", "code"}, "finish": {"flow"},
              "list": set(), "disconnect": {"source_id"}}
    try:
        raw = await onboarding._read_bounded_body(request, 4096)
        doc = json.loads(raw) if raw is not None else None
        if (action not in fields or not isinstance(doc, dict) or set(doc) != fields[action]
                or any(not isinstance(v, str) or not 0 < len(v) <= 2048 for v in doc.values())):
            raise ValueError("invalid login request")
        if action == "resolve":
            from tinyassets.providers.source_record import resolve

            record = resolve(doc["address"], runtime.root)
            result = ({"source_id": record.id} if "cli_login" in record.sign_in
                      else {"source": record.document()}) if record else {}
        elif action == "finish":
            state = login_operation("poll", flow=doc["flow"])
            if state["status"] != "connected":
                raise PermissionError("native login is not complete")
            result = await run_in_threadpool(finish, state["source_id"])
        elif action == "list":
            result = await run_in_threadpool(connected_sources)
        elif action == "disconnect":
            result = await run_in_threadpool(disconnect, doc["source_id"])
        else:
            result = login_operation(action, **doc)
        return JSONResponse(result, headers=HEADERS)
    except (ValueError, KeyError, TypeError, PermissionError, SubscriptionError) as exc:
        detail = _failure_detail(exc)
        logging.getLogger(__name__).warning("subscription connection %s: %s", action, detail)
        return JSONResponse({"error": "subscription_connection_incomplete", "detail": detail},
                            409, headers=HEADERS)
