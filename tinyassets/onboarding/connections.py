"""Standalone shared-door connection inventory and broker disconnect controls."""

import json
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse

from tinyassets.broker.client import BrokerRefused

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
        from tinyassets.api.http_connection import _project
        from tinyassets.broker.catalog import connections
        from tinyassets.broker.disconnect import disconnect
        from tinyassets.broker.owner_identities import connection_center
        from tinyassets.onboarding.owner_sessions import COOKIE, lookup

        with identity_context(identity):
            session = lookup(request.cookies.get(COOKIE, ""))
            if (session is not None
                    and json.loads(session["identity_json"])["user_id"] != identity.user_id):
                return {"error": "identity_conflict"}, 401
            base = _base_path()
            requested_center = request.query_params.get("center", "")
            if data is not None and requested_center and requested_center != data["universe_id"]:
                raise LookupError("center not found")
            uid = connection_center(
                base, principal=identity.user_id,
                center=data["universe_id"] if data is not None else
                requested_center)
            if data is not None:
                args = dict(principal=identity.user_id, command_center=uid,
                            destination=data["destination"])
                snapshot = disconnect(base, **args)
                if snapshot["resource"] is not None:
                    if snapshot["incarnation"] != data["incarnation"]:
                        return {"error": "connection_changed"}, 409
                    disconnect(base, **args, action="fence",
                               incarnation=data["incarnation"])
                result = disconnect(base, **args, action="erase",
                                    incarnation=data["incarnation"])
                return {"status": "removed", "connection_removed": result["removed"],
                        "secrets_removed": result["secrets_removed"]}, 200
            inventory = connections(
                base, principal=identity.user_id, command_center=uid, limit=100,
                include_revoked=True)
            rows = [{**_project(resource, grant), "incarnation": incarnation,
                     "cleanup_pending": resource.revoked_at is not None,
                     "status": "cleanup_pending" if resource.revoked_at is not None
                     else "provisioned"}
                    for grant, resource, incarnation in inventory
                    if resource.connection_type == "http"]
            return {"universe_id": uid,
                    "connections": [{**row, "label": connection_label(row)} for row in rows]}, 200

    try:
        result, status = await run_in_threadpool(run)
    except LookupError:
        return JSONResponse({"error": "not_found"}, 404, headers=_HEADERS)
    except BrokerRefused:
        return JSONResponse({"error": "connection_result_unconfirmed"}, 503, headers=_HEADERS)
    except PermissionError:
        return JSONResponse({"error": "connection_access_denied"}, 403, headers=_HEADERS)
    except Exception:  # noqa: BLE001 - no credentials or provider payload in errors
        return JSONResponse({"error": "connection_result_unconfirmed"}, 503, headers=_HEADERS)
    return JSONResponse(result, status, headers=_HEADERS)
