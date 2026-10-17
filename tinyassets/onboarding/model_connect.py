"""Authenticated connection flows at the shared door; custody stays in the broker."""
import json
from dataclasses import asdict
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse

from tinyassets.onboarding import hosted_model_auth as hosted

_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


async def handle_model_connect(request):
    from tinyassets import onboarding
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.connection_oauth.flow import FlowError

    if not onboarding.onboarding_enabled():
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
    denied = onboarding._app_identity_required()
    if denied is not None:
        denied.headers.update(_HEADERS)
        return denied
    resource = str(onboarding.app_config().get("resource") or "")
    try:
        origin, public = urlsplit(request.headers.get("origin", "")), urlsplit(resource)
        allowed = (onboarding._same_origin_json(request, resource)
                   and origin.scheme == public.scheme == "https"
                   and origin.netloc == public.netloc and origin.netloc
                   and not origin.username and not origin.password
                   and not (origin.path or origin.query or origin.fragment))
    except ValueError:
        allowed = False
    if not allowed:
        return JSONResponse({"error": "same_origin_json_required"}, 403, headers=_HEADERS)
    operation = request.path_params.get("operation")
    fields = {"inline_begin": {"preset_id"}, "inline_poll": {"flow"},
              "inline_cancel": {"flow"}, "begin": {"preset_id", "code_challenge"},
              "exchange": {"flow", "code", "code_verifier"}, "resume": {"preset_id"},
              "deposit_key": {"preset_id", "key"},
              "deposit": {"definition", "secret"},
              "rotate": {"destination", "incarnation", "secret"},
              "oauth_begin": {"code_challenge"},
              "source_sign_in": {"preset_id", "code_challenge"},
              "oauth_exchange": {"flow", "code", "code_verifier"}}
    if operation not in fields:
        return JSONResponse({"error": "not_found"}, 404, headers=_HEADERS)
    optional = {"center", "request_ref"}
    if operation == "oauth_begin":
        optional |= {"definition", "destination"}
    if operation == "oauth_exchange":
        optional.add("iss")
    raw = await onboarding._read_bounded_body(request, 16384)
    try:
        data = json.loads(raw) if raw is not None else None
        if (not isinstance(data, dict)
                or not fields[operation] <= set(data) <= fields[operation] | optional
                or any(not isinstance(v, str) or not v or len(v) > 2048
                       for k, v in data.items() if k != "definition")
                or len(data.get("request_ref", "")) > 512):
            raise ValueError
        if "definition" in data and (not isinstance(data["definition"], dict)
                                    or "discovery_hosts" in data["definition"]):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        return JSONResponse({"error": "invalid_model_connection"}, 400, headers=_HEADERS)
    if operation in {"begin", "oauth_begin", "source_sign_in"} and not hosted._HANDLE.fullmatch(
            data["code_challenge"]):
        return JSONResponse({"error": "invalid_pkce_challenge"}, 400, headers=_HEADERS)
    if operation == "deposit_key" and any(not 33 <= ord(c) <= 126 for c in data["key"]):
        return JSONResponse({"error": "invalid_model_connection"}, 400, headers=_HEADERS)
    identity = current_identity()

    resolved_center = ""

    def run():
        nonlocal resolved_center

        from tinyassets.api.helpers import _base_path
        from tinyassets.broker.catalog import connections
        from tinyassets.broker.http_connect import connect_operation
        from tinyassets.broker.model_connect import request as broker_request
        from tinyassets.broker.owner_identities import connection_center
        from tinyassets.connection_oauth import flow as sign_in
        from tinyassets.onboarding import inline_model_connect as inline
        from tinyassets.onboarding.connection_definition import (
            oauth_definition,
            preset_definition,
            sign_in_definition,
        )
        from tinyassets.onboarding.owner_sessions import COOKIE, lookup, require

        with identity_context(identity):
            owner, base = identity.user_id, _base_path()
            session = lookup(request.cookies.get(COOKIE, ""))
            if session is not None and json.loads(session["identity_json"])["user_id"] != owner:
                raise FlowError("identity_conflict", 401)
            requested = request.query_params.get("center", "")
            if requested and data.get("center") and requested != data["center"]:
                raise LookupError("center not found")
            center = connection_center(base, principal=owner,
                                       center=data.get("center") or requested)
            resolved_center = center
            ref = data.get("request_ref", "")
            approval = None
            if operation in {"oauth_begin", "inline_begin", "source_sign_in"}:
                try:
                    approval = require(request, owner=owner)
                except PermissionError:
                    raise FlowError("interactive_approval_required", 403) from None

            if operation == "rotate":
                return rotate_connection(base, owner=owner, center=center,
                    destination=data["destination"], incarnation=data["incarnation"],
                    secret=data["secret"], request_ref=ref)
            if operation == "deposit":
                return deposit_connection(base, owner=owner, center=center,
                    definition=data["definition"], secret=data["secret"], request_ref=ref)

            def exchange(code, verifier):
                flow = hosted.take_flow(handle=data["flow"], owner=owner,
                                        universe_id=center, verifier=verifier)
                result = broker_request(base, owner=owner, center=center, document={
                    "action": "preset_exchange", "flow": asdict(flow), "code": code,
                    "verifier": verifier, "redirect_uri": flow.callback_url,
                    "request_ref": flow.request_ref})
                if result.get("error"):
                    raise FlowError(result["error"], 502)
                return result["projection"]

            if operation == "begin":
                return hosted.begin_flow(owner=owner, universe_id=center,
                    preset_id=data["preset_id"], challenge=data["code_challenge"],
                    public_resource=resource, request_ref=ref)
            if operation == "exchange":
                return exchange(data["code"], data["code_verifier"])
            if operation in {"inline_begin", "inline_poll", "inline_cancel"}:
                if operation == "inline_begin":
                    return inline.begin(owner=owner, home=center, preset_id=data["preset_id"],
                        resource=resource, owner_session=approval, request_ref=ref)
                result = inline.take(owner=owner, home=center, handle=data["flow"],
                                     cancel=operation == "inline_cancel")
                if result.get("status") == "ready":
                    return exchange(result["code"], result["verifier"])
                return result
            if operation in {"oauth_begin", "source_sign_in"}:
                definition = (sign_in_definition(data["preset_id"], resource)
                              if operation == "source_sign_in" else data.get("definition"))
                if definition is None:
                    rows = connections(base, principal=owner, command_center=center, limit=100)
                    match = next((r for _, r, _ in rows
                                  if r.destination == data.get("destination")), None)
                    if match is None:
                        raise FlowError("unknown_model_connection", 404)
                    definition = {"destination": match.destination,
                                  "endpoints": [e.as_dict() for e in match.allowed_endpoints]}
                definition = oauth_definition(base, owner=owner, center=center,
                                               definition=definition, public_resource=resource)
                return sign_in.begin(owner=owner, universe_id=center, request_id=ref,
                    challenge=data["code_challenge"], public_resource=resource,
                    owner_session=approval, definition=definition)
            if operation == "oauth_exchange":
                return sign_in.complete(owner=owner, universe_id=center, handle=data["flow"],
                    code=data["code"], verifier=data["code_verifier"], iss=data.get("iss", ""))
            definition = preset_definition(data["preset_id"], manual=operation == "deposit_key")
            if operation == "resume":
                rows = connections(base, principal=owner, command_center=center, limit=100)
                from tinyassets.api.http_connection import _project

                for grant, connection, _ in rows:
                    if connection.destination == definition["destination"]:
                        from tinyassets.broker.ledger_queries import granted_resource_row

                        row = granted_resource_row(base, principal=owner, command_center=center,
                                                   grant_id=grant.grant_id)
                        return {**_project(connection, grant), "request_ref": row["request_ref"]}
                raise hosted.HostedAuthError("model_authorization_required", 409)
            args = dict(principal=owner, command_center=center, **definition, request_ref=ref)
            prepared = connect_operation(base, **args, action="prepare")
            if prepared.get("error"):
                raise FlowError(prepared["error"], 409)
            result = connect_operation(base, **args, action="commit",
                                       expected=prepared["revision"], secret=data["key"])
            if result.get("error"):
                raise FlowError(result["error"], 409)
            return result["projection"]

    try:
        result = await run_in_threadpool(run)
        if result.get("status") == "provisioned" and not result.get("request_ref"):
            from tinyassets.onboarding.source_connect import model_source

            if model_source(result.get("allowed_endpoints", [])) is not None:
                result["model_confirmation"] = {"grant_id": result["grant_id"],
                                                "universe_id": resolved_center}
        return JSONResponse(result, headers=_HEADERS)
    except (hosted.HostedAuthError, FlowError) as exc:
        detail = getattr(exc, "detail", "") if operation in {"deposit", "rotate"} else ""
        return JSONResponse({"error": exc.code, **({"detail": detail} if detail else {})},
                            exc.status, headers=_HEADERS)
    except ValueError:
        return JSONResponse({"error": "invalid_model_connection"}, 400, headers=_HEADERS)
    except LookupError:
        return JSONResponse({"error": "not_found"}, 404, headers=_HEADERS)
    except Exception:  # noqa: BLE001 - credentials and provider payloads never enter errors
        return JSONResponse({"error": "model_connection_incomplete"}, 503, headers=_HEADERS)


async def handle_model_callback(request):
    """Public shell only; GET never redeems a code, deposits, grants or enables."""
    from tinyassets import onboarding

    if not hosted.is_callback_path(request.url.path):
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
    if onboarding.onboarding_enabled():
        from tinyassets.onboarding.inline_model_connect import callback

        inline = await run_in_threadpool(callback, request)
        if inline is not None:
            return inline
    response = await onboarding._handle_app(request)
    response.headers.update(_HEADERS)
    return response


async def handle_client_metadata(request):
    """Public OAuth Client ID Metadata Document; GET grants nothing.

    Its URL IS the client id a sign-in source names when no registered client is
    configured. Constant: built from the configured public resource (never the
    request's Host), one redirect URI (the fixed generic callback), and no
    secret, because this is a public PKCE client.
    """
    from tinyassets import onboarding
    from tinyassets.connection_oauth import pkce
    from tinyassets.connection_oauth.flow import FlowError, callback_origin
    from tinyassets.onboarding.source_connect import CLIENT_METADATA_PATH

    if not onboarding.onboarding_enabled():
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
    try:
        origin = callback_origin(str(onboarding.app_config().get("resource") or ""))
    except FlowError:
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
    return JSONResponse({
        "client_id": origin + CLIENT_METADATA_PATH,
        "client_name": "TinyAssets",
        "client_uri": origin,
        "redirect_uris": [origin + pkce.CONNECT_CALLBACK_PATH],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "application_type": "web",
    }, headers={"Cache-Control": "public, max-age=3600"})


def deposit_connection(base, *, owner, center, definition, secret, request_ref=""):
    """Generic shared-door deposit; no owner file or pending-request read."""
    from tinyassets.api.http_connection import (
        _DEPOSITABLE_AUTH_SCHEMES,
        _secret_shape_error,
        connection_definition,
        extract_url_secret,
    )
    from tinyassets.broker.http_connect import connect_operation
    from tinyassets.connection_oauth.flow import FlowError
    from tinyassets.storage.outbound_connections import _parse_allowed_endpoints

    allowed = {"destination", "auth_scheme", "endpoints", "access", "scopes", "git_host"}
    if (set(definition) - allowed
            or not {"destination", "endpoints"} <= set(definition)):
        raise ValueError("invalid connection definition")
    scheme = definition.get("auth_scheme", "bearer")
    if not isinstance(scheme, str) or scheme not in _DEPOSITABLE_AUTH_SCHEMES:
        raise FlowError("unsupported_auth_scheme", 400)
    result = connection_definition({
        **{k: v for k, v in definition.items() if k != "endpoints"},
        "allowed_endpoints": definition["endpoints"]})
    if result.get("error"):
        raise FlowError(result["error"], 400, result.get("detail", ""))
    endpoints = _parse_allowed_endpoints(result["policy"]["endpoints"])
    if scheme == "url_secret":
        secret, error = extract_url_secret(secret, endpoints)
    else:
        error = _secret_shape_error(scheme, secret)
    if error or not secret.strip():
        raise FlowError("connection_setup_invalid", 400, error or "secret is required")
    args = dict(principal=owner, command_center=center, **result, request_ref=request_ref)
    prepared = connect_operation(base, **args, action="prepare")
    if prepared.get("error"):
        raise FlowError(prepared["error"], 409)
    result = connect_operation(base, **args, action="commit",
                               expected=prepared["revision"], secret=secret)
    if result.get("error"):
        raise FlowError(result["error"], 409)
    return result["projection"]


def rotate_connection(base, *, owner, center, destination, incarnation, secret, request_ref=""):
    from tinyassets.api.http_connection import extract_url_secret
    from tinyassets.broker.catalog import connections
    from tinyassets.broker.http_connect import custody_operation
    from tinyassets.connection_oauth.flow import FlowError

    found = next(((g, r, i) for g, r, i in connections(
        base, principal=owner, command_center=center) if r.destination == destination), None)
    if found is None:
        raise FlowError("not_found", 404)
    grant, resource, current = found
    if current != incarnation:
        raise FlowError("connection_changed", 409)
    if resource.auth_scheme == "url_secret":
        secret, error = extract_url_secret(secret, resource.allowed_endpoints)
        if error:
            raise FlowError("connection_setup_invalid", 400, error)
    result = custody_operation(base, principal=owner, command_center=center,
        destination=destination, incarnation=incarnation, secret=secret,
        action="rotate", request_ref=request_ref)
    if result.get("error"):
        raise FlowError(result["error"], 409)
    from tinyassets.api.http_connection import _git_scopes_in, _project

    return {**_project(resource, grant), "status": "rotated", "request_ref": request_ref,
            "git_scopes": sorted(_git_scopes_in(json.dumps(list(resource.scopes))))}
