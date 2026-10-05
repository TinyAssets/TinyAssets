"""Unpowered-safe owner connection controls; no LLM and no upstream key mutation."""

import json
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse

_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def connection_label(row: dict) -> str:
    """A name the owner recognizes; never the storage destination alone.

    The guided sign-in stores its key under ``model:<preset id>`` - a handle, not
    a name (live 2026-09-24 the Account page listed "model:openrouter_user_models_v1").
    Its label is the installed preset's own display name. Every other
    destination is the name the owner or their agent chose, shown as is.
    """
    destination = str(row.get("destination") or "")
    if destination.startswith("model:"):
        from tinyassets.onboarding.hosted_model_auth import HostedAuthError, load_preset

        try:
            return f"{load_preset(destination[len('model:'):]).display_name} (free models)"
        except (HostedAuthError, OSError, ValueError, KeyError):
            return destination[len("model:"):] or destination
    return destination


async def handle_connections(request):
    from tinyassets import onboarding
    from tinyassets.auth.middleware import current_identity, identity_context

    if not onboarding.onboarding_enabled():
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
    denied = onboarding._app_identity_required()
    if denied is not None:
        denied.headers.update(_HEADERS)
        return denied
    data = None
    if request.method == "POST":
        resource = str(onboarding.app_config().get("resource") or "")
        try:
            origin = urlsplit(request.headers.get("origin", ""))
            allowed = (
                onboarding._same_origin_json(request, resource)
                and origin.scheme == urlsplit(resource).scheme == "https"
                and not (origin.path or origin.query or origin.fragment)
            )
        except ValueError:
            allowed = False
        if not allowed:
            return JSONResponse({"error": "same_origin_json_required"}, 403, headers=_HEADERS)
        raw = await onboarding._read_bounded_body(request, 2048)
        try:
            data = json.loads(raw) if raw is not None else None
            if (
                not isinstance(data, dict)
                or set(data) != {"universe_id", "destination", "incarnation"}
                or any(not isinstance(v, str) or not v or len(v) > 200 for v in data.values())
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "invalid_connection_removal"}, 400, headers=_HEADERS)
    identity = current_identity()

    def run():
        from tinyassets.api.helpers import _base_path
        from tinyassets.api.http_connection import _project, remove_http
        from tinyassets.broker.catalog import connections
        from tinyassets.onboarding.serving import _require_current_admin
        from tinyassets.providers.connection_lifecycle import unfinished_disconnections
        from tinyassets.shared_self import require_founder_home

        with identity_context(identity):
            uid = onboarding._read_home(identity, raise_errors=True)
            if not uid or (data is not None and data["universe_id"] != uid):
                return {"error": "current_home_changed"}, 409
            base = _base_path()
            require_founder_home(base, uid, identity.user_id)
            _require_current_admin(base, universe_id=uid, owner=identity.user_id)
            if data is not None:
                result = remove_http(
                    universe_id=uid,
                    payload={
                        "destination": data["destination"],
                        "incarnation": data["incarnation"],
                    },
                )
                return result, 409 if result.get("error") else 200
            rows = []
            for grant, resource, incarnation in connections(
                    base, principal=identity.user_id, command_center=uid, limit=100):
                if (
                    resource
                    and resource.owner_user_id == identity.user_id
                    and resource.connection_type == "http"
                ):
                    rows.append(
                        {
                            **_project(resource, grant),
                            "incarnation": incarnation,
                        }
                    )
            known = {row["connection_id"] for row in rows}
            rows.extend(
                row
                for row in unfinished_disconnections(base, owner=identity.user_id, uid=uid)
                if row["connection_id"] not in known
            )
            return {"universe_id": uid,
                    "connections": [{**row, "label": connection_label(row)} for row in rows]}, 200

    try:
        result, status = await run_in_threadpool(run)
    except PermissionError:
        return JSONResponse({"error": "connection_access_denied"}, 403, headers=_HEADERS)
    except Exception:  # noqa: BLE001 - no credentials or provider payload in errors
        return JSONResponse({"error": "connection_result_unconfirmed"}, 503, headers=_HEADERS)
    return JSONResponse(result, status, headers=_HEADERS)
