"""Owner-box discovery; returns credential-free source metadata to the page."""
import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


async def handle_model_discovery(request):
    from tinyassets import onboarding
    from tinyassets.auth.middleware import current_identity
    from tinyassets.connection_oauth.model_discovery import address_url, discover
    from tinyassets.connection_oauth.transport import OAuthError
    from tinyassets.onboarding.owner_sessions import require
    from tinyassets.owner_runtime import admitted
    from tinyassets.providers.source_record import SourceRecord, resolve

    headers = {"Cache-Control": "no-store"}
    try:
        runtime = admitted()
        owner = current_identity().user_id
        require(request, owner=owner)
        raw = await onboarding._read_bounded_body(request, 16384)
        data = json.loads(raw)
        if not {"address"} <= set(data) <= {"address", "source"}:
            raise ValueError("invalid discovery fields")

        def run():
            if "source" in data:
                record = SourceRecord.parse(data["source"])
                if (record.origin != "declared" or address_url(data["address"]) not in
                        {address_url(a) for a in record.addresses}):
                    raise ValueError("source does not answer address")
                return record
            return resolve(data["address"], runtime.root) or discover(data["address"])

        record = await run_in_threadpool(run)
        return JSONResponse({"source": record.document()}, headers=headers)
    except PermissionError:
        return JSONResponse({"error": "interactive_approval_required"}, 403, headers=headers)
    except OAuthError as exc:
        return JSONResponse({"error": exc.code}, 409, headers=headers)
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"error": "invalid_source_record"}, 400, headers=headers)


async def handle_connection_discovery(request):
    from tinyassets import onboarding
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.broker.catalog import connections
    from tinyassets.connection_oauth.flow import FlowError
    from tinyassets.onboarding.connection_definition import oauth_definition, sign_in_definition
    from tinyassets.onboarding.owner_sessions import require
    from tinyassets.owner_runtime import admitted

    headers = {"Cache-Control": "no-store"}
    try:
        runtime, identity = admitted(), current_identity()
        require(request, owner=identity.user_id)
        data = json.loads(await onboarding._read_bounded_body(request, 16384))
        if set(data) not in ({"definition"}, {"destination"}, {"preset_id"}):
            raise ValueError("invalid discovery fields")
        resource = onboarding.app_config()["resource"]

        def run():
            with identity_context(identity):
                if "preset_id" in data:
                    definition = sign_in_definition(data["preset_id"], resource)
                elif "definition" in data:
                    definition = data["definition"]
                else:
                    rows = connections(runtime.root, principal=identity.user_id,
                                       command_center=identity.user_id, limit=100)
                    match = next((r for _, r, _ in rows
                                  if r.destination == data["destination"]), None)
                    if match is None:
                        raise FlowError("unknown_model_connection", 404)
                    definition = {"destination": match.destination,
                                  "endpoints": [ep.as_dict() for ep in match.allowed_endpoints]}
                return oauth_definition(runtime.root, owner=identity.user_id,
                    center=identity.user_id, definition=definition, public_resource=resource)

        return JSONResponse({"definition": await run_in_threadpool(run)}, headers=headers)
    except PermissionError:
        return JSONResponse({"error": "interactive_approval_required"}, 403, headers=headers)
    except FlowError as exc:
        return JSONResponse({"error": exc.code}, exc.status, headers=headers)
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"error": "invalid_connection_definition"}, 400, headers=headers)
