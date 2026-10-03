"""Authenticated same-origin ingress for browser sign-in, unpowered-safe.

Two flows share it: the bundled first-power preset (``begin``/``exchange``/
``resume``/``deposit_key``) and signing in to answer ANY pending ``connect``
request whose provider offers OAuth (``oauth_begin``/``oauth_exchange``,
``connection_oauth.flow``). ``source_sign_in`` raises the platform's own such
request for an installed sign-in source card, so the card is one tap.
"""

import json
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, PlainTextResponse

from tinyassets.onboarding import hosted_model_auth as hosted

_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


async def handle_model_connect(request):
    from tinyassets import onboarding
    from tinyassets.api.helpers import _base_path, _universe_dir
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.onboarding.model_bootstrap import complete_bootstrap
    from tinyassets.onboarding.model_setup import model_setup_state
    from tinyassets.shared_self import require_founder_home

    if not onboarding.onboarding_enabled():
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
    denied = onboarding._app_identity_required()
    if denied is not None:
        denied.headers.update(_HEADERS)
        return denied
    resource = str(onboarding.app_config().get("resource") or "")
    try:
        origin = urlsplit(request.headers.get("origin", ""))
        public = urlsplit(resource)
        allowed = (origin.scheme == public.scheme == "https" and origin.netloc == public.netloc
                   and origin.netloc and not (origin.path or origin.query or origin.fragment)
                   and not origin.username and not origin.password
                   and request.headers.get("content-type", "").split(";")[0].strip()
                   == "application/json")
    except ValueError:
        allowed = False
    if not allowed:
        return JSONResponse({"error": "same_origin_json_required"}, 403, headers=_HEADERS)
    operation = request.path_params.get("operation")
    fields = {"begin": {"preset_id", "code_challenge"},
              "exchange": {"flow", "code", "code_verifier"}, "resume": {"preset_id"},
              "deposit_key": {"preset_id", "key"},
              "oauth_begin": {"request_id", "code_challenge"},
              "source_sign_in": {"preset_id"},
              "oauth_exchange": {"flow", "code", "code_verifier"}}
    # RFC 9207 ``iss`` rides the exchange when the provider returned one.
    optional = {"oauth_exchange": {"iss"}}.get(operation, set())
    if operation not in fields:
        return JSONResponse({"error": "not_found"}, 404, headers=_HEADERS)
    raw = await onboarding._read_bounded_body(request, 8192)
    try:
        data = json.loads(raw) if raw is not None else None
        if (not isinstance(data, dict)
                or not fields[operation] <= set(data) <= fields[operation] | optional
                or any(not isinstance(v, str) or not v or len(v) > 2048 for v in data.values())):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        return JSONResponse({"error": "invalid_model_connection"}, 400, headers=_HEADERS)
    if operation in {"begin", "oauth_begin"} and not hosted._HANDLE.fullmatch(
            data["code_challenge"]):
        return JSONResponse({"error": "invalid_pkce_challenge"}, 400, headers=_HEADERS)
    if operation == "deposit_key" and any(not 33 <= ord(char) <= 126 for char in data["key"]):
        return JSONResponse({"error": "invalid_model_connection"}, 400, headers=_HEADERS)
    identity = current_identity()

    def scope(*, create=False, expected="", empty=False):
        home = onboarding._read_home(identity, raise_errors=True)
        if not home and create:
            home = onboarding._bootstrap_home(identity)
        if not home or (expected and expected != home):
            raise hosted.HostedAuthError("current_home_changed", 409)
        base = _base_path()
        require_founder_home(base, home, identity.user_id)
        if empty and model_setup_state(base, universe=_universe_dir(home), uid=home,
                                       owner=identity.user_id) not in {"empty", "disconnected"}:
            raise hosted.HostedAuthError("model_setup_changed", 409)
        return base, home

    def begin():
        with identity_context(identity):
            # Reject unknown data before creating even an inert home.
            hosted.load_preset(data["preset_id"])
            _, home = scope(create=True, empty=True)
            return hosted.begin_flow(owner=identity.user_id, universe_id=home,
                                     preset_id=data["preset_id"], challenge=data["code_challenge"],
                                     public_resource=resource)

    def take():
        with identity_context(identity):
            _, home = scope(empty=True)
            return hosted.take_flow(handle=data["flow"], owner=identity.user_id,
                                    universe_id=home, verifier=data["code_verifier"])

    def deposit_key():
        with identity_context(identity):
            from tinyassets.onboarding.source_connect import connect_source
            from tinyassets.providers.free_sources import source_preset

            source = source_preset(data["preset_id"])
            if source is not None:
                base, home = scope(create=True)
                return connect_source(base=base, uid=home, owner=identity.user_id,
                                      preset=source, key=data["key"])
            # Only trusted installed data can opt in; validate before home creation.
            preset = hosted.load_preset(data["preset_id"], require_manual_key=True)
            base, home = scope(create=True, empty=True)
            return complete_bootstrap(base=base, uid=home, owner=identity.user_id,
                                      preset=preset, key=data["key"])

    def complete(preset, *, expected="", expected_digest="", key=None):
        with identity_context(identity):
            if expected_digest and preset.digest != expected_digest:
                raise hosted.HostedAuthError("model_connection_preset_changed", 409)
            base, home = scope(expected=expected)
            return complete_bootstrap(base=base, uid=home, owner=identity.user_id,
                                      preset=preset, key=key)

    def oauth_begin():
        from tinyassets.connection_oauth import flow as sign_in

        with identity_context(identity):
            _, home = scope()
            return sign_in.begin(owner=identity.user_id, universe_id=home,
                                 request_id=data["request_id"],
                                 challenge=data["code_challenge"], public_resource=resource)

    def source_sign_in():
        from tinyassets.onboarding.source_connect import raise_sign_in_ask
        from tinyassets.providers.free_sources import sign_in_preset

        with identity_context(identity):
            # Only installed data names endpoints; reject unknown ids before
            # creating even an inert home.
            preset = sign_in_preset(data["preset_id"])
            if preset is None:
                raise hosted.HostedAuthError("unknown_model_connection", 404)
            _, home = scope(create=True)
            return {"status": "sign_in_required",
                    "request": raise_sign_in_ask(uid=home, preset=preset,
                                                 public_resource=resource)}

    def oauth_exchange():
        from tinyassets.connection_oauth import flow as sign_in
        from tinyassets.onboarding.source_connect import offer_signed_in_source

        with identity_context(identity):
            base, home = scope()
            done = sign_in.complete(owner=identity.user_id, universe_id=home,
                                    handle=data["flow"], code=data["code"],
                                    verifier=data["code_verifier"],
                                    iss=data.get("iss", ""))
            # A source card's sign-in on a command center that already runs on
            # something: the new source joins the agent only on the owner's
            # explicit confirmation, exactly like a pasted-key card.
            try:
                confirmation = offer_signed_in_source(
                    base=base, uid=home, owner=identity.user_id,
                    request_id=str(done.get("request_id") or ""), completed=done,
                    public_resource=resource)
            except Exception:  # noqa: BLE001 - post-deposit failures must not fail sign-in
                # The sign-in itself landed; expose no internal exception details.
                return {**done, "confirmation_error": "model_confirmation_requires_review"}
            if confirmation is not None:
                done = {**done, "confirmation": confirmation["request"]}
            return done

    from tinyassets.connection_oauth.flow import FlowError

    try:
        if operation == "oauth_begin":
            result = await run_in_threadpool(oauth_begin)
        elif operation == "source_sign_in":
            result = await run_in_threadpool(source_sign_in)
        elif operation == "oauth_exchange":
            result = await run_in_threadpool(oauth_exchange)
        elif operation == "begin":
            result = await run_in_threadpool(begin)
        elif operation == "deposit_key":
            result = await run_in_threadpool(deposit_key)
        elif operation == "resume":
            result = await run_in_threadpool(complete, hosted.load_preset(data["preset_id"]))
        else:
            flow = await run_in_threadpool(take)
            key = await hosted.exchange_key(flow=flow, code=data["code"],
                                            verifier=data["code_verifier"])
            result = await run_in_threadpool(complete, hosted.load_preset(flow.preset_id),
                                             expected=flow.universe_id,
                                             expected_digest=flow.preset_digest, key=key)
        return JSONResponse(result, headers=_HEADERS)
    except hosted.HostedAuthError as exc:
        return JSONResponse({"error": exc.code}, exc.status, headers=_HEADERS)
    except FlowError as exc:
        # The provider's own bounded words, never a token: the owner reads
        # why their sign-in did not complete.
        body = {"error": exc.code, **({"detail": exc.detail} if exc.detail else {})}
        return JSONResponse(body, exc.status, headers=_HEADERS)
    except PermissionError:
        return JSONResponse({"error": "model_connection_requires_recovery"}, 409, headers=_HEADERS)
    except Exception:  # noqa: BLE001 - credentials and upstream errors never enter logs or JSON
        return JSONResponse({"error": "model_connection_incomplete"}, 503, headers=_HEADERS)


async def handle_model_callback(request):
    """Public shell only; GET never redeems a code, deposits, grants or enables."""
    from tinyassets import onboarding

    if not hosted.is_callback_path(request.url.path):
        return PlainTextResponse("Not Found", 404, headers=_HEADERS)
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
