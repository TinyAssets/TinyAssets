"""One-tap OpenAI linking: the device-authorization broker + its routes.

Network is replaced with an httpx MockTransport scripted to the exact shapes the
Codex CLI source documents (codex-rs/login/src/device_code_auth.rs + server.rs).
The deposit path is exercised through the REAL ``connect_llm`` handler so the
identity/ACL gates are the ones production runs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib

import httpx
import pytest

from tinyassets.onboarding import openai_device as od


def _client_with(handler):
    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)

    return factory


def _fake_jwt(claims: dict) -> str:
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{b({'alg': 'none'})}.{b(claims)}.sig"


# ---------------------------------------------------------------- start ----


def test_start_posts_codex_client_id_and_returns_public_fields_only():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200, json={"device_auth_id": "dev-1", "user_code": "ABCD-EFGH", "interval": 3}
        )

    out = asyncio.run(od.start_device_auth(_client_with(handler)))
    assert seen["url"] == "https://auth.openai.com/api/accounts/deviceauth/usercode"
    assert seen["body"] == {"client_id": od.CODEX_CLIENT_ID}
    assert out == {
        "device_auth_id": "dev-1",
        "user_code": "ABCD-EFGH",
        "verification_url": "https://auth.openai.com/codex/device",
        "interval": 3,
    }


def test_start_404_means_device_login_unavailable():
    def handler(req):
        return httpx.Response(404)

    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(od.start_device_auth(_client_with(handler)))
    assert ei.value.code == "device_login_unavailable"
    assert ei.value.status == 503


def test_start_unreachable_is_secret_free_error():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(od.start_device_auth(_client_with(handler)))
    assert ei.value.code == "openai_unreachable"


def test_start_rejects_oversized_or_missing_fields():
    def handler(req):
        return httpx.Response(200, json={"device_auth_id": "x" * 1000, "user_code": "A"})

    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(od.start_device_auth(_client_with(handler)))
    assert ei.value.code == "device_response_invalid"


# ----------------------------------------------------------------- poll ----


def test_poll_pending_on_403_and_404():
    for status in (403, 404):

        def handler(req, status=status):
            return httpx.Response(status)

        assert (
            asyncio.run(
                od.poll_device_auth(
                    device_auth_id="d", user_code="c", client_factory=_client_with(handler)
                )
            )
            is None
        )


def test_poll_success_exchanges_with_pkce_and_builds_codex_auth_json():
    calls = []
    # Real Codex id_tokens nest the account id under the OpenAI auth claim
    # (codex-rs/login/src/token_data.rs AuthClaims) — NOT at the top level.
    id_token = _fake_jwt(
        {
            "email": "u@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_42",
                "chatgpt_plan_type": "plus",
            },
        }
    )

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/api/accounts/deviceauth/token":
            assert json.loads(req.content) == {"device_auth_id": "d", "user_code": "c"}
            return httpx.Response(
                200,
                json={
                    "authorization_code": "authcode",
                    "code_challenge": "chal",
                    "code_verifier": "verif",
                },
            )
        if req.url.path == "/oauth/token":
            form = dict(p.split("=", 1) for p in req.content.decode().split("&"))
            assert form["grant_type"] == "authorization_code"
            assert form["code"] == "authcode"
            assert form["code_verifier"] == "verif"
            assert form["client_id"] == od.CODEX_CLIENT_ID
            assert form["redirect_uri"] == "https%3A%2F%2Fauth.openai.com%2Fdeviceauth%2Fcallback"
            return httpx.Response(
                200, json={"id_token": id_token, "access_token": "acc", "refresh_token": "ref"}
            )
        raise AssertionError(f"unexpected call {req.url}")

    out = asyncio.run(
        od.poll_device_auth(device_auth_id="d", user_code="c", client_factory=_client_with(handler))
    )
    assert out is not None and set(out) == {"auth_json"}
    doc = json.loads(out["auth_json"])
    # The exact $CODEX_HOME/auth.json the Codex CLI writes after a ChatGPT login.
    assert doc["auth_mode"] == "chatgpt"
    assert doc["OPENAI_API_KEY"] is None
    assert doc["tokens"] == {
        "id_token": id_token,
        "access_token": "acc",
        "refresh_token": "ref",
        "account_id": "acct_42",
    }
    assert doc["last_refresh"].endswith("Z")
    assert [c.url.path for c in calls] == ["/api/accounts/deviceauth/token", "/oauth/token"]


def test_poll_exchange_failure_never_returns_partial_tokens():
    def handler(req):
        if req.url.path.endswith("/deviceauth/token"):
            return httpx.Response(
                200, json={"authorization_code": "a", "code_challenge": "c", "code_verifier": "v"}
            )
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(
            od.poll_device_auth(
                device_auth_id="d", user_code="c", client_factory=_client_with(handler)
            )
        )
    assert ei.value.code == "token_exchange_failed"


def test_poll_requires_fields():
    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(od.poll_device_auth(device_auth_id="", user_code="c"))
    assert ei.value.status == 400


def test_build_auth_json_tolerates_malformed_id_token():
    doc = json.loads(
        od.build_codex_auth_json(id_token="not-a-jwt", access_token="a", refresh_token="r")
    )
    assert "account_id" not in doc["tokens"]


# --------------------------------------------------------------- routes ----


def _drive(route_path: str, body: dict | None, *, identity=None, enabled=True, monkeypatch=None):
    """Drive a route handler directly with an optional pinned identity."""
    from starlette.requests import Request

    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import onboarding_routes

    if monkeypatch is not None:
        monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1" if enabled else "")
    route = next(r for r in onboarding_routes() if r.path == route_path)
    raw = json.dumps(body or {}).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": route_path,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def run():
        req = Request(scope, receive)
        if identity is not None:
            with identity_context(identity):
                return await route.endpoint(req)
        # `identity=None` means NOBODY IS HERE, which is what the 401 tests
        # assert. The ambient signed-in operator would otherwise answer for
        # them, and the route would return 200.
        from tinyassets.auth.middleware import clear_identity

        clear_identity()
        return await route.endpoint(req)

    resp = asyncio.run(run())
    return resp.status_code, json.loads(resp.body or b"{}") if resp.body.strip().startswith(
        b"{"
    ) else resp.body


def _home(monkeypatch, home="u-home"):
    """Pin the server-resolved home: the POST routes bootstrap it, the GET reads it."""
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_bootstrap_home", lambda identity: home)
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **kwargs: home)


def _user(sub="user_123"):
    from tinyassets.auth.provider import Identity

    return Identity(
        user_id=sub, username=sub, display_name=sub, capabilities=["write"], metadata={}
    )


def test_routes_404_when_app_dark(monkeypatch):
    assert (
        _drive("/app/openai/device/start", {}, enabled=False, monkeypatch=monkeypatch)[0] == 404
    )
    assert (
        _drive("/app/openai/device/poll", {}, enabled=False, monkeypatch=monkeypatch)[0] == 404
    )


def test_routes_401_without_identity(monkeypatch):
    # Anonymous callers cannot start or poll a link — there is nobody to deposit for.
    assert _drive("/app/openai/device/start", {}, monkeypatch=monkeypatch)[0] == 401
    assert (
        _drive(
            "/app/openai/device/poll",
            {"flow": "x"},
            monkeypatch=monkeypatch,
        )[0]
        == 401
    )


def test_start_route_returns_code_and_opaque_handle_only(monkeypatch):
    """The raw device tuple must never reach the browser: the response carries
    the user code (which the user must see) and an opaque flow handle."""
    od._reset_pending_for_tests()

    async def fake_start():
        return {
            "device_auth_id": "dev-secret",
            "user_code": "AB-CD",
            "verification_url": od.VERIFICATION_URL,
            "interval": 5,
        }

    monkeypatch.setattr(od, "start_device_auth", fake_start)
    _home(monkeypatch)
    status, doc = _drive(
        "/app/openai/device/start", {}, identity=_user(), monkeypatch=monkeypatch
    )
    assert status == 200 and doc["user_code"] == "AB-CD"
    assert set(doc) == {"flow", "user_code", "verification_url", "interval"}
    assert "dev-secret" not in json.dumps(doc)
    assert len(doc["flow"]) >= 32


def _started(monkeypatch, *, user, device_auth_id="dev-1", user_code="AB-CD"):
    """Start a flow as ``user`` through the route; return its opaque handle."""

    async def fake_start():
        return {
            "device_auth_id": device_auth_id,
            "user_code": user_code,
            "verification_url": od.VERIFICATION_URL,
            "interval": 5,
        }

    monkeypatch.setattr(od, "start_device_auth", fake_start)
    _home(monkeypatch)
    status, doc = _drive("/app/openai/device/start", {}, identity=user, monkeypatch=monkeypatch)
    assert status == 200
    return doc["flow"]


def test_poll_rejects_foreign_unknown_and_replayed_handles(monkeypatch):
    """Codex finding 1: a flow is bound to the identity that started it and is
    consumed on its first terminal outcome — a stolen or guessed handle, or a
    different signed-in user, gets the same 'unknown_flow' answer."""
    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user("victim"))
    polled = []

    async def fake_poll(**kw):
        polled.append(kw)
        return {
            "auth_json": od.build_codex_auth_json(id_token="t", access_token="a", refresh_token="r")
        }

    monkeypatch.setattr(od, "poll_device_auth", fake_poll)
    import tinyassets.api.llm_deposit as ld

    monkeypatch.setattr(ld, "connect_llm", lambda **kw: {"ok": True})

    # Another authenticated user presenting the victim's handle: refused, and
    # OpenAI is never polled on their behalf.
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": handle},
        identity=_user("attacker"),
        monkeypatch=monkeypatch,
    )
    assert (status, doc) == (404, {"error": "unknown_flow"})
    assert polled == []
    # An unknown handle: same answer.
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": "nope"},
        identity=_user("victim"),
        monkeypatch=monkeypatch,
    )
    assert (status, doc) == (404, {"error": "unknown_flow"})
    # The owner completes it — the daemon polled with the bound tuple…
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": handle},
        identity=_user("victim"),
        monkeypatch=monkeypatch,
    )
    assert status == 200
    assert doc["status"] == "connected" and doc["service"] == "codex"
    assert doc["serving"] == {"status": "held"}
    assert polled == [{"device_auth_id": "dev-1", "user_code": "AB-CD"}]
    # …and the handle is consumed: a replay cannot deposit again.
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": handle},
        identity=_user("victim"),
        monkeypatch=monkeypatch,
    )
    assert (status, doc) == (404, {"error": "unknown_flow"})


def test_flow_registry_expires_and_caps_per_user(monkeypatch):
    od._reset_pending_for_tests()
    h = od.register_flow(user_id="u", universe_id="u-home", device_auth_id="d", user_code="c")
    assert od.lookup_flow(h, user_id="u").device_auth_id == "d"
    with pytest.raises(od.DeviceAuthError):
        od.lookup_flow(h, user_id="someone-else")
    # expiry
    monkeypatch.setattr(
        od._time, "monotonic", lambda: od._time.time() + od.FLOW_TTL_SECONDS + 10**6
    )
    with pytest.raises(od.DeviceAuthError):
        od.lookup_flow(h, user_id="u")
    monkeypatch.undo()
    # per-user cap: the oldest pending flow is replaced, never unbounded growth
    od._reset_pending_for_tests()
    handles = [
        od.register_flow(user_id="u", universe_id="u-home", device_auth_id=f"d{i}", user_code="c")
        for i in range(od._MAX_PENDING_PER_USER + 2)
    ]
    alive = [h for h in handles if h in od._pending]
    assert len(alive) == od._MAX_PENDING_PER_USER and alive == handles[-od._MAX_PENDING_PER_USER :]
    with pytest.raises(od.DeviceAuthError):
        od.register_flow(user_id="", universe_id="u-home", device_auth_id="d", user_code="c")
    with pytest.raises(od.DeviceAuthError) as ei:  # no home -> nowhere to deposit
        od.register_flow(user_id="u", universe_id="", device_auth_id="d", user_code="c")
    assert ei.value.status == 409


def test_bounded_body_rejects_oversized_before_buffering(monkeypatch):
    """Codex finding 4: an oversized body is refused on its declared length, and
    an undeclared one is cut off while streaming — never buffered whole."""
    from starlette.requests import Request

    from tinyassets.onboarding import _read_bounded_body

    async def run(headers, chunks):
        sent = list(chunks)

        async def receive():
            body = sent.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(sent)}

        req = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/x",
                "headers": headers,
                "query_string": b"",
            },
            receive,
        )
        return await _read_bounded_body(req, 16)

    assert asyncio.run(run([(b"content-length", b"100")], [b"x" * 100])) is None
    assert asyncio.run(run([], [b"x" * 10, b"y" * 10])) is None  # cut off mid-stream
    assert asyncio.run(run([], [b"ok", b"!"])) == b"ok!"


def test_poll_route_pending_passthrough(monkeypatch):
    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user())

    async def fake_poll(**kw):
        return None

    monkeypatch.setattr(od, "poll_device_auth", fake_poll)
    for _ in range(2):  # pending keeps the flow alive for the next poll
        status, doc = _drive(
            "/app/openai/device/poll",
            {"flow": handle},
            identity=_user(),
            monkeypatch=monkeypatch,
        )
        assert (status, doc) == (200, {"status": "pending"})


def test_poll_route_deposits_as_user_and_never_echoes_credential(monkeypatch):
    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user("user_777"))
    """On approval the route deposits through the REAL connect_llm under the
    request identity; the response carries a status only. A user with no admin
    ACL on the target universe is refused by connect_llm's own gate — proving
    the gate runs — and the token text appears nowhere in the response."""
    secret = "refresh-SECRET-xyz"

    async def fake_poll(**kw):
        return {
            "auth_json": od.build_codex_auth_json(
                id_token="t", access_token="a", refresh_token=secret
            )
        }

    monkeypatch.setattr(od, "poll_device_auth", fake_poll)
    captured = {}

    def fake_connect_llm(*, universe_id="", payload=None):
        from tinyassets.auth.middleware import current_identity

        captured["actor"] = current_identity().user_id
        captured["service"] = payload["service"]
        captured["material"] = base64.b64decode(payload["auth_material_b64"]).decode()
        return {"ok": True, "service": "codex"}

    import tinyassets.api.llm_deposit as ld

    monkeypatch.setattr(ld, "connect_llm", fake_connect_llm)
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": handle},
        identity=_user("user_777"),
        monkeypatch=monkeypatch,
    )
    assert status == 200
    assert doc["status"] == "connected" and doc["service"] == "codex"
    assert doc["serving"] == {"status": "held"}
    assert captured["actor"] == "user_777" and captured["service"] == "codex"
    assert json.loads(captured["material"])["tokens"]["refresh_token"] == secret
    assert secret not in json.dumps(doc)


def test_poll_route_surfaces_connect_llm_refusal(monkeypatch):
    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user())

    async def fake_poll(**kw):
        return {
            "auth_json": od.build_codex_auth_json(id_token="t", access_token="a", refresh_token="r")
        }

    monkeypatch.setattr(od, "poll_device_auth", fake_poll)
    import tinyassets.api.llm_deposit as ld

    monkeypatch.setattr(
        ld, "connect_llm", lambda **kw: {"error": "not_found", "resource": "connection"}
    )
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": handle},
        identity=_user(),
        monkeypatch=monkeypatch,
    )
    assert status == 400 and doc == {"status": "failed", "error": "not_found"}


def test_poll_route_maps_device_errors(monkeypatch):
    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user())

    async def fake_poll(**kw):
        raise od.DeviceAuthError("openai_unreachable")

    monkeypatch.setattr(od, "poll_device_auth", fake_poll)
    status, doc = _drive(
        "/app/openai/device/poll",
        {"flow": handle},
        identity=_user(),
        monkeypatch=monkeypatch,
    )
    assert (status, doc) == (502, {"error": "openai_unreachable"})


def test_poll_lease_blocks_concurrent_poll_and_releases_on_pending():
    """Codex round-2: a second poll while one is in flight must not race into a
    second deposit — it answers 409; a pending outcome hands the lease back."""
    od._reset_pending_for_tests()
    h = od.register_flow(user_id="u", universe_id="u-home", device_auth_id="d", user_code="c")
    od.lookup_flow(h, user_id="u")  # lease taken
    with pytest.raises(od.DeviceAuthError) as ei:
        od.lookup_flow(h, user_id="u")
    assert (ei.value.code, ei.value.status) == ("poll_in_progress", 409)
    od.release_flow(h)
    assert od.lookup_flow(h, user_id="u").leased is True
    od.consume_flow(h)
    with pytest.raises(od.DeviceAuthError):
        od.lookup_flow(h, user_id="u")


def test_bounded_body_rejects_malformed_content_length():
    from starlette.requests import Request

    from tinyassets.onboarding import _read_bounded_body

    async def run(value):
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/x",
            "headers": [(b"content-length", value)],
            "query_string": b"",
        }
        return await _read_bounded_body(Request(scope, receive), 16)

    assert (
        asyncio.run(run("\u00b2".encode("utf-8"))) is None
    )  # superscript two: isdigit() True, int() raises
    assert asyncio.run(run(b"abc")) is None
    assert asyncio.run(run(b"2")) == b"{}"


def test_poll_route_releases_lease_on_unexpected_exception(monkeypatch):
    """Codex round-3: an unexpected error after the lease is taken must hand it
    back, so the user's next poll works instead of 409-ing until expiry."""
    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user())

    async def boom(**kw):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(od, "poll_device_auth", boom)
    with pytest.raises(RuntimeError):
        _drive(
            "/app/openai/device/poll",
            {"flow": handle},
            identity=_user(),
            monkeypatch=monkeypatch,
        )
    # The flow is still pending (not consumed) and NOT leased: a retry proceeds.
    assert od.lookup_flow(handle, user_id=_user().user_id).leased is True


# ------------------------------------------------ browser (loopback) flow ----


def test_loopback_redirect_validation():
    ok = od.valid_loopback_redirect
    assert ok("http://localhost:1455/auth/callback")
    assert ok("http://127.0.0.1:2000/auth/callback")
    assert not ok("https://localhost:1455/auth/callback")  # scheme
    assert not ok("http://evil.example:1455/auth/callback")  # host
    assert not ok("http://localhost/auth/callback")  # no port
    assert not ok("http://localhost:80/auth/callback")  # privileged port
    assert not ok("http://localhost:1455/other")  # path
    assert not ok("http://localhost:1455/auth/callback?x=1")  # query


def test_browser_authorize_params_match_codex_cli():
    q = od.browser_authorize_params(
        redirect_uri="http://localhost:1455/auth/callback", code_challenge="c", state="s"
    )
    assert q["client_id"] == od.CODEX_CLIENT_ID
    assert q["code_challenge_method"] == "S256"
    assert q["codex_cli_simplified_flow"] == "true"
    assert q["id_token_add_organizations"] == "true"
    assert q["originator"] == "codex_cli_rs"
    assert "offline_access" in q["scope"]


def test_exchange_browser_code_posts_pkce_and_builds_auth_json():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/oauth/token"
        seen["form"] = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        return httpx.Response(
            200, json={"id_token": "t", "access_token": "a", "refresh_token": "r"}
        )

    out = asyncio.run(
        od.exchange_browser_code(
            code="c1",
            code_verifier="v1",
            redirect_uri="http://localhost:1455/auth/callback",
            client_factory=_client_with(handler),
        )
    )
    assert seen["form"]["grant_type"] == "authorization_code"
    assert seen["form"]["code"] == "c1" and seen["form"]["code_verifier"] == "v1"
    assert seen["form"]["redirect_uri"] == "http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"
    assert json.loads(out["auth_json"])["tokens"]["refresh_token"] == "r"


def test_exchange_browser_code_rejects_bad_redirect_before_network():
    def handler(req):
        raise AssertionError("must not reach the network")

    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(
            od.exchange_browser_code(
                code="c",
                code_verifier="v",
                redirect_uri="https://evil/auth/callback",
                client_factory=_client_with(handler),
            )
        )
    assert ei.value.code == "invalid_redirect_uri"


def _pkce():
    import hashlib

    verifier = "v" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def test_begin_binds_user_home_challenge_and_redirect(monkeypatch):
    od._reset_pending_for_tests()
    _home(monkeypatch, "u-mine")
    verifier, challenge = _pkce()
    body = {"code_challenge": challenge, "redirect_uri": "http://localhost:1455/auth/callback"}
    assert _drive("/app/openai/begin", body, monkeypatch=monkeypatch)[0] == 401
    status, doc = _drive(
        "/app/openai/begin", body, identity=_user("u1"), monkeypatch=monkeypatch
    )
    assert status == 200 and set(doc) == {"flow"}
    flow = od.lookup_flow(doc["flow"], user_id="u1")
    assert (flow.universe_id, flow.code_challenge, flow.redirect_uri) == (
        "u-mine",
        challenge,
        "http://localhost:1455/auth/callback",
    )
    od.release_flow(doc["flow"])
    # bad inputs / no home
    bad = dict(body, redirect_uri="https://evil/auth/callback")
    assert (
        _drive("/app/openai/begin", bad, identity=_user("u1"), monkeypatch=monkeypatch)[0]
        == 400
    )
    bad = dict(body, code_challenge="short")
    assert (
        _drive("/app/openai/begin", bad, identity=_user("u1"), monkeypatch=monkeypatch)[0]
        == 400
    )
    _home(monkeypatch, "")
    assert (
        _drive("/app/openai/begin", body, identity=_user("u1"), monkeypatch=monkeypatch)[0]
        == 409
    )


def test_exchange_route_requires_bound_flow_and_matching_verifier(monkeypatch):
    """Codex round-2 #3: the exchange completes only for the identity that
    began the flow, with the verifier whose challenge the flow holds, using the
    flow's own redirect, into the flow's own home."""
    od._reset_pending_for_tests()
    _home(monkeypatch, "u-mine")
    verifier, challenge = _pkce()
    status, doc = _drive(
        "/app/openai/begin",
        {"code_challenge": challenge, "redirect_uri": "http://localhost:2000/auth/callback"},
        identity=_user("u1"),
        monkeypatch=monkeypatch,
    )
    handle = doc["flow"]
    seen = {}

    async def fake_exchange(**kw):
        seen.update(kw)
        return {
            "auth_json": od.build_codex_auth_json(id_token="t", access_token="a", refresh_token="r")
        }

    monkeypatch.setattr(od, "exchange_browser_code", fake_exchange)
    captured = {}
    import tinyassets.api.llm_deposit as ld

    def fake_connect_llm(*, universe_id="", payload=None):
        from tinyassets.auth.middleware import current_identity

        captured["actor"] = current_identity().user_id
        captured["universe"] = universe_id
        return {"ok": True}

    monkeypatch.setattr(ld, "connect_llm", fake_connect_llm)

    # foreign identity: unknown flow, nothing exchanged
    body = {"flow": handle, "code": "c", "code_verifier": verifier}
    assert _drive(
        "/app/openai/exchange", body, identity=_user("attacker"), monkeypatch=monkeypatch
    ) == (404, {"error": "unknown_flow"})
    assert seen == {}
    # wrong verifier: refused AND the flow is consumed (one attempt)
    wrong = dict(body, code_verifier="w" * 43)
    assert _drive(
        "/app/openai/exchange", wrong, identity=_user("u1"), monkeypatch=monkeypatch
    ) == (400, {"error": "verifier_mismatch"})
    assert (
        _drive("/app/openai/exchange", body, identity=_user("u1"), monkeypatch=monkeypatch)[0]
        == 404
    )
    # fresh flow, right verifier: exchanged with the FLOW's redirect, deposited into the FLOW's home
    status, doc = _drive(
        "/app/openai/begin",
        {"code_challenge": challenge, "redirect_uri": "http://localhost:2000/auth/callback"},
        identity=_user("u1"),
        monkeypatch=monkeypatch,
    )
    body["flow"] = doc["flow"]
    _home(monkeypatch, "u-OTHER")  # a later home change must not redirect the deposit
    status, doc = _drive(
        "/app/openai/exchange", body, identity=_user("u1"), monkeypatch=monkeypatch
    )
    assert (status, doc) == (200, {"status": "connected", "service": "codex"})
    assert seen["redirect_uri"] == "http://localhost:2000/auth/callback"
    assert (captured["actor"], captured["universe"]) == ("u1", "u-mine")
    # consumed
    assert (
        _drive("/app/openai/exchange", body, identity=_user("u1"), monkeypatch=monkeypatch)[0]
        == 404
    )


def test_upstream_error_mapping():
    assert od._upstream_error(400, invalid="x").status == 400
    assert od._upstream_error(429, invalid="x").status == 429
    assert od._upstream_error(503, invalid="x").status == 502
    with pytest.raises(od.DeviceAuthError) as ei:
        asyncio.run(
            od.exchange_browser_code(
                code="c" * 3000,
                code_verifier="v",
                redirect_uri="http://localhost:1455/auth/callback",
            )
        )
    assert ei.value.status == 400


# ------------------------------------------------------------ /app/me ----


def _drive_get(path, *, identity=None, monkeypatch):
    from starlette.requests import Request

    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import onboarding_routes

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    route = next(r for r in onboarding_routes() if r.path == path)
    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run():
        req = Request(scope, receive)
        if identity is not None:
            with identity_context(identity):
                return await route.endpoint(req)
        # `identity=None` means NOBODY IS HERE, which is what the 401 tests
        # assert. The ambient signed-in operator would otherwise answer for
        # them, and the route would return 200.
        from tinyassets.auth.middleware import clear_identity

        clear_identity()
        return await route.endpoint(req)

    resp = asyncio.run(run())
    return resp.status_code, json.loads(resp.body or b"{}")


def test_me_requires_identity_and_reports_engine(monkeypatch, tmp_path):
    assert _drive_get("/app/me", monkeypatch=monkeypatch)[0] == 401

    import tinyassets.api.helpers as helpers
    import tinyassets.onboarding.model_setup as setup

    (tmp_path / "u-home").mkdir()
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    # Cannot create a home (no scope) -> not connected; the public landing
    # universe must NOT read as "you're connected".
    _home(monkeypatch, "")
    monkeypatch.setattr(setup, "model_setup_state", lambda *args, **kwargs: "connected")
    status, doc = _drive_get("/app/me", identity=_user("f1"), monkeypatch=monkeypatch)
    assert (status, doc["home_bound"], doc["engine_connected"]) == (200, False, False)
    # Home (bootstrapped) without an engine -> connect gate.
    _home(monkeypatch, "u-home")
    monkeypatch.setattr(setup, "model_setup_state", lambda *args, **kwargs: "empty")
    status, doc = _drive_get("/app/me", identity=_user("f1"), monkeypatch=monkeypatch)
    assert (doc["home_bound"], doc["engine_connected"], doc["universe_id"]) == (
        True,
        False,
        "u-home",
    )
    # Home with an engine -> straight to chat.
    monkeypatch.setattr(setup, "model_setup_state", lambda *args, **kwargs: "connected")
    assert (
        _drive_get("/app/me", identity=_user("f1"), monkeypatch=monkeypatch)[1][
            "engine_connected"
        ]
        is True
    )


def test_bootstrap_home_uses_first_contact_provisioning(monkeypatch):
    """The gate's destination is created the same way the conversation entry
    creates it (ensure_founder_home), under the request identity; "" when the
    identity cannot create one."""
    import tinyassets.api.first_contact as fc
    import tinyassets.api.helpers as helpers
    from tinyassets.onboarding import _bootstrap_home

    seen = {}

    def fake_ensure(base, founder):
        from tinyassets.auth.middleware import current_identity

        seen["founder"] = founder
        seen["ctx"] = current_identity().user_id
        return "u-new"

    monkeypatch.setattr(fc, "ensure_founder_home", fake_ensure)
    monkeypatch.setattr(helpers, "_base_path", lambda: pathlib.Path("."))
    assert _bootstrap_home(_user("f9")) == "u-new"
    assert seen == {"founder": "f9", "ctx": "f9"}
    monkeypatch.setattr(
        fc, "ensure_founder_home", lambda b, f: (_ for _ in ()).throw(RuntimeError("x"))
    )
    assert _bootstrap_home(_user("f9")) == ""


def test_me_is_read_only_and_bootstrap_happens_on_begin(monkeypatch):
    """Codex round-3 #2: a status GET must not create a universe; provisioning
    belongs to the POST the user acts on."""
    import tinyassets.onboarding as onboarding

    calls = []
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **kwargs: "")
    monkeypatch.setattr(
        onboarding, "_bootstrap_home", lambda identity: calls.append("boot") or "u-new"
    )
    status, doc = _drive_get("/app/me", identity=_user("f1"), monkeypatch=monkeypatch)
    assert (status, doc["home_bound"], calls) == (200, False, [])
    od._reset_pending_for_tests()
    _verifier, challenge = _pkce()
    body = {"code_challenge": challenge, "redirect_uri": "http://localhost:1455/auth/callback"}
    status, doc = _drive(
        "/app/openai/begin", body, identity=_user("f1"), monkeypatch=monkeypatch
    )
    assert status == 200 and calls == ["boot"]
    assert od.lookup_flow(doc["flow"], user_id="f1").universe_id == "u-new"


def test_exchange_rejects_malformed_verifier_before_leasing(monkeypatch):
    """Codex round-3 #3: a non-ASCII / short verifier is refused up front and
    the flow stays usable (not leased, not consumed)."""
    od._reset_pending_for_tests()
    _home(monkeypatch, "u-mine")
    _verifier, challenge = _pkce()
    status, doc = _drive(
        "/app/openai/begin",
        {"code_challenge": challenge, "redirect_uri": "http://localhost:1455/auth/callback"},
        identity=_user("u1"),
        monkeypatch=monkeypatch,
    )
    handle = doc["flow"]
    for bad in ("short", "v" * 43 + "\u00e9", "v" * 200):
        status, doc = _drive(
            "/app/openai/exchange",
            {"flow": handle, "code": "c", "code_verifier": bad},
            identity=_user("u1"),
            monkeypatch=monkeypatch,
        )
        assert (status, doc) == (400, {"error": "invalid_code_verifier"})
    assert (
        od.lookup_flow(handle, user_id="u1").leased is True
    )  # still there, lease free before this
    assert od.verifier_matches_challenge("v" * 43 + "\u00e9", challenge) is False


def test_trace_route_is_identity_scoped_allowlisted_and_rate_limited(monkeypatch, caplog):
    import logging

    import tinyassets.onboarding as onboarding

    onboarding._trace_buckets.clear()
    assert _drive("/app/trace", {"step": "openai.finish"}, monkeypatch=monkeypatch)[0] == 401
    with caplog.at_level(logging.WARNING, logger="tinyassets.onboarding"):
        status, doc = _drive(
            "/app/trace",
            {"step": "openai.callback", "detail": "code\nerror\x00 " + "d" * 500},
            identity=_user("f1"),
            monkeypatch=monkeypatch,
        )
    assert (status, doc) == (200, {"ok": True})
    line = next(r.getMessage() for r in caplog.records if "app-trace" in r.getMessage())
    assert "user=f1 step=openai.callback detail=code error" in line
    assert "\n" not in line and len(line) < 320
    # only known steps
    assert (
        _drive(
            "/app/trace", {"step": "x<script>"}, identity=_user("f1"), monkeypatch=monkeypatch
        )[0]
        == 400
    )
    # per-identity window
    for _ in range(onboarding._TRACE_BUCKET_MAX):
        _drive(
            "/app/trace",
            {"step": "openai.finish"},
            identity=_user("f2"),
            monkeypatch=monkeypatch,
        )
    assert (
        _drive(
            "/app/trace",
            {"step": "openai.finish"},
            identity=_user("f2"),
            monkeypatch=monkeypatch,
        )[0]
        == 429
    )
    assert (
        _drive(
            "/app/trace",
            {"step": "openai.finish"},
            identity=_user("f3"),
            monkeypatch=monkeypatch,
        )[0]
        == 200
    )


# --------------------------------------------------------------------------- #
# The reconnect card's target: the card names it, the caller's ACL decides.
# --------------------------------------------------------------------------- #


def _grant_admin(base, universe_id, actor):
    from tinyassets import daemon_server

    (base / universe_id).mkdir(parents=True, exist_ok=True)
    daemon_server.grant_universe_access(
        base, universe_id=universe_id, actor_id=actor,
        permission="admin", granted_by=actor,
    )


def test_a_sign_in_lands_in_the_universe_the_card_names(tmp_path, monkeypatch):
    """The card exists to repair ONE named connection, so it names its universe.

    Before, the route always chose the caller's home, so a card for a second universe
    could not repair it (Codex refute-review round 1, P1 #5).
    """
    from tinyassets.api.helpers import _base_path
    from tinyassets.onboarding import openai_device

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    base = _base_path()
    identity = _user("owner-a")
    _grant_admin(base, "u-second", "owner-a")

    async def fake_start():
        return {"device_auth_id": "dev-secret", "user_code": "AB-CD",
                "verification_url": "https://sign-in.example.net/device", "interval": 5}

    monkeypatch.setattr(openai_device, "start_device_auth", fake_start)
    status, body = _drive(
        "/app/openai/device/start",
        {"service": "codex", "universe_id": "u-second"},
        identity=identity, monkeypatch=monkeypatch,
    )
    assert status == 200, body
    # The flow is bound to the universe the CARD named, not to the caller's home.
    flow = openai_device.lookup_flow(body["flow"], user_id="owner-a")
    assert flow.universe_id == "u-second"
    openai_device.release_flow(body["flow"])


def test_another_users_universe_is_refused_not_redirected(tmp_path, monkeypatch):
    """The cross-user negative. A universe the caller does not administer must FAIL,
    never be quietly replaced with their own -- a silent substitution would deposit a
    credential somewhere the owner never asked for."""
    from tinyassets.api.helpers import _base_path
    from tinyassets.onboarding import openai_device

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    base = _base_path()
    _grant_admin(base, "u-theirs", "owner-b")

    monkeypatch.setattr(openai_device, "start_device_auth", lambda: pytest.fail(
        "no flow may be started for a universe the caller does not administer"))
    status, body = _drive(
        "/app/openai/device/start",
        {"service": "codex", "universe_id": "u-theirs"},
        identity=_user("owner-a"), monkeypatch=monkeypatch,
    )
    assert status == 403, body
    assert body.get("error") == "sign_in_universe_not_yours"


def test_a_service_this_daemon_cannot_sign_into_is_refused(tmp_path, monkeypatch):
    """A claude-service card. Anthropic's terms forbid a third-party subscription
    OAuth, so there is no brokered sign-in for it -- the card offers the ordinary
    shapes, and a request reaching this route for it must be refused rather than
    completing the one service this route does know."""
    from tinyassets.onboarding import openai_device

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(openai_device, "start_device_auth", lambda: pytest.fail(
        "a sign-in was started for a service this route cannot deposit"))
    status, body = _drive(
        "/app/openai/device/start", {"service": "claude"},
        identity=_user("owner-a"), monkeypatch=monkeypatch,
    )
    assert status == 400, body
    assert body.get("error") == "sign_in_unsupported_for_service"


def test_no_named_universe_still_uses_the_callers_own_home(tmp_path, monkeypatch):
    """The setup card names no universe, and that path must keep working."""
    from tinyassets.onboarding import openai_device

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    async def fake_start():
        return {"device_auth_id": "dev-secret", "user_code": "AB-CD",
                "verification_url": "https://sign-in.example.net/device", "interval": 5}

    monkeypatch.setattr(openai_device, "start_device_auth", fake_start)
    status, body = _drive(
        "/app/openai/device/start", {"service": "codex"},
        identity=_user("owner-a"), monkeypatch=monkeypatch,
    )
    assert status == 200, body
    flow = openai_device.lookup_flow(body["flow"], user_id="owner-a")
    assert flow.universe_id, "the caller's own home should have been resolved"
    openai_device.release_flow(body["flow"])


@pytest.mark.parametrize("offer_error", [False, True])
def test_poll_preserves_deposit_and_exposes_pending_model_consent(monkeypatch, offer_error):
    from tinyassets.onboarding import source_connect

    od._reset_pending_for_tests()
    handle = _started(monkeypatch, user=_user())

    async def fake_poll(**kwargs):
        return {"auth_json": "private-credential"}

    def offer(**kwargs):
        assert kwargs["owner"] == _user().user_id
        assert kwargs["service"] == "codex"
        if offer_error:
            raise RuntimeError("private-exception")
        return {"request": {"request_id": "consent-1", "title": "Use subscription",
                            "action": {"type": "bind_model_access"}}}

    monkeypatch.setattr(od, "poll_device_auth", fake_poll)
    monkeypatch.setattr(od, "deposit_codex_auth_json", lambda *a, **kw: {
        "ok": True, "serving": {"status": "held", "detail": "private-detail"}})
    monkeypatch.setattr(source_connect, "offer_subscription_source", offer)
    status, doc = _drive("/app/openai/device/poll", {"flow": handle},
                         identity=_user(), monkeypatch=monkeypatch)
    assert status == 200 and doc["status"] == "connected"
    assert doc["serving"] == {"status": "held"}
    assert "private" not in json.dumps(doc)
    if offer_error:
        assert doc["confirmation_error"] == "model_confirmation_requires_review"
        assert "confirmation" not in doc
    else:
        assert doc["confirmation"]["action"]["type"] == "bind_model_access"
