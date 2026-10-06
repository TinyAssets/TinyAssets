"""The onboarding route's auth boundary, through the REAL AuthContextMiddleware.

Reproduces the Codex-found scenario (require-auth mode) end to end at the ASGI
layer: `/app` must load anonymously (no challenge) while `/mcp`, other `/mcp/*`
paths, and every `/app/*` API route still get the OAuth challenge. Complements
the handler-level tests in test_onboarding_app.py, which called the handler
directly and so could not have caught a middleware-level 401.

The app moved from `/mcp/app` to the apex `/app` on 2026-09-30, which took its
API routes out of the `/mcp/` prefix the challenge rule swept. That made this
file the guard for the move's one real security risk.
"""

from __future__ import annotations

import asyncio

import pytest

from tinyassets.auth import middleware as mw
from tinyassets.auth.provider import Identity


class _RequireAuthProvider:
    """A provider in the strictest mode: challenge every unauthenticated /mcp*."""

    def resolve_token(self, token):
        return None

    def is_auth_required(self):
        return True

    def resolve_always_writes(self):
        return False

    def writes_require_identity(self):
        return True

    def challenge_unauthenticated(self):
        return True


_SUBJECT = Identity(user_id="founder-1", username="founder-1", capabilities=["read", "write"])


class _ResolvingProvider(_RequireAuthProvider):
    """Resolves one bearer to a real subject, so a request reaches the router.

    Needed to prove ABSENCE. The bearer 401 fires in EVERY auth mode (see
    `AuthContextMiddleware`), so an anonymous request can never get past it on a
    `/mcp/*` path — and a 401 emitted before routing looks identical whether or
    not a handler sits behind the path. Only an authenticated request can tell
    "nothing is mounted here" from "something is mounted and gated".
    """

    def resolve_token(self, token):
        return _SUBJECT if token == "good-token" else None


@pytest.fixture
def require_auth_provider():
    saved = mw._provider
    mw.set_provider(_RequireAuthProvider())
    try:
        yield
    finally:
        mw._provider = saved


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def _drive(path: str, method: str = "GET") -> int:
    """Drive one anonymous request through AuthContextMiddleware; return status."""
    app = mw.AuthContextMiddleware(_ok_app)
    scope = {"type": "http", "method": method, "path": path, "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts, "no response.start emitted"
    return starts[0]["status"]


def test_onboarding_path_not_challenged_in_require_auth_mode(require_auth_provider):
    # The blocker Codex found: without the exemption this returned 401.
    assert _drive("/app") == 200


def test_mcp_endpoint_still_challenges_anonymous(require_auth_provider):
    # The exemption must be scoped: the transport endpoint still challenges.
    assert _drive("/mcp") == 401


def test_other_mcp_subpath_still_challenges(require_auth_provider):
    assert _drive("/mcp/something-else") == 401


def test_retired_app_path_challenges_through_the_middleware(require_auth_provider):
    # /mcp/app is not the app any more and holds no exemption: it is an ordinary
    # /mcp/* path, so anonymously it gets the connector challenge.
    assert _drive("/mcp/app") == 401


def test_the_retired_path_is_indistinguishable_from_any_absent_mcp_path(monkeypatch):
    """Asserted through the REAL middleware stack, not by reading source.

    The retirement claim is "nothing is mounted at /mcp/app, and it gets no
    special handling". The observable form of that is: it answers *exactly* what
    a path that was never there answers. Anything else — a redirect, a
    distinguishable status, a body that mentions the app — would be a carve-out
    for the retired path, i.e. the back-compat this move removes.

    Driven with the app's own route table mounted underneath, so the assertion
    is about the composed stack rather than a predicate.
    """
    from starlette.applications import Starlette

    from tinyassets import onboarding

    # The app is dark-flagged; with it OFF even `/app` is a 404, and "everything
    # is 404" would prove nothing about the retired path.
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")

    saved = mw._provider
    mw.set_provider(_RequireAuthProvider())
    try:
        inner = Starlette(routes=list(onboarding.onboarding_routes()))
        app = mw.AuthContextMiddleware(inner)

        def observe(path: str, bearer: str | None = None) -> tuple[int, dict[str, str]]:
            headers = (
                [(b"authorization", b"Bearer " + bearer.encode())] if bearer else []
            )
            scope = {
                "type": "http", "method": "GET", "path": path, "headers": headers,
                "query_string": b"", "root_path": "", "scheme": "https",
                "server": ("testserver", 443), "client": ("1.2.3.4", 1234),
                "app": inner,
            }

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            sent: list[dict] = []

            async def send(message):
                sent.append(message)

            asyncio.run(app(scope, receive, send))
            start = [m for m in sent if m["type"] == "http.response.start"][0]
            headers = {
                k.decode().lower(): v.decode() for k, v in start.get("headers", [])
            }
            return start["status"], headers

        retired = ("/mcp/app", "/mcp/app/me", "/mcp/app/token",
                   "/mcp/app/billing/webhook", "/mcp/app/account/delete")

        # (a) In challenge mode the connector's 401 fires before routing, so the
        #     retired path must be indistinguishable from a path never mounted.
        control_status, control_headers = observe("/mcp/definitely-never-existed")
        for path in retired:
            status, headers = observe(path)
            assert status == control_status, (path, status, control_status)
            assert "location" not in headers, path
            assert ("www-authenticate" in headers) == (
                "www-authenticate" in control_headers
            ), path

        # (b) That alone would NOT catch a mounted alias — a 401 before routing
        #     looks the same whether or not a handler sits behind it. So drive
        #     the same paths AUTHENTICATED, which gets past the challenge into
        #     the router, and require the router's own not-found.
        mw.set_provider(_ResolvingProvider())
        live_status, live_headers = observe("/app", "good-token")
        assert live_status == 200, (
            "sanity: the live app path must actually serve for this to mean anything"
        )
        assert "x-tinyassets-build" in live_headers, (
            "sanity: the build header is the marker the retired path must lack, "
            "so it has to be present on what IS served"
        )
        assert observe("/mcp/definitely-never-existed", "good-token")[0] == 404, (
            "control must reach the router"
        )
        for path in retired:
            status, headers = observe(path, "good-token")
            assert status == 404, (path, status)
            assert "location" not in headers, path
            # The app stamps this on what it serves; the retired path must not.
            assert "x-tinyassets-build" not in headers, path
    finally:
        mw._provider = saved


def test_app_api_route_challenges_through_the_middleware(require_auth_provider):
    # The real ASGI path, not just the predicate: an app API route with no
    # bearer must still be refused after the move off /mcp/app.
    assert _drive("/app/me") == 401


def test_personal_file_routes_challenge_through_real_middleware(require_auth_provider):
    assert _drive("/app/soul") == 401
    assert _drive("/app/memory") == 401


# --- pure predicate: the exemption itself ---


def test_challenge_path_exempts_only_the_app_route():
    assert mw._auth_challenge_path("/app") is False
    # The same-origin PKCE token-exchange proxy runs before any bearer exists.
    assert mw._auth_challenge_path("/app/token") is False
    assert mw._auth_challenge_path("/mcp") is True
    assert mw._auth_challenge_path("/mcp/") is True
    assert mw._auth_challenge_path("/app/callback") is True  # not the served path
    assert mw._auth_challenge_path("/app/token/x") is True  # exact-match only, no prefix bypass
    assert mw._auth_challenge_path("/.well-known/oauth-protected-resource") is False


def test_app_api_subtree_is_challenged_after_the_apex_move():
    """The app left the /mcp/ prefix on 2026-09-30, so the challenge rule that
    used to cover its API routes by accident now has to name them.

    This is the regression the move could have silently introduced: every one of
    these reached the middleware as a /mcp/app/... path before and got a 401
    with no bearer. If the move had only renamed routes, they would answer
    anonymously.
    """
    for path in (
        "/app/me",
        "/app/billing/checkout",
        "/app/billing/cancel",
        "/app/billing/status",
        "/app/account/delete",
        "/app/connections",
        "/app/files",
        "/app/serving/bind",
        "/app/models/preferences",
        "/app/voice/session",
        "/app/voice/status",
        "/app/trace",
        "/app/model-connect/connect",
        "/app/openai/device/start",
    ):
        assert mw._auth_challenge_path(path) is True, path


def test_app_challenge_is_anchored_on_the_segment_boundary():
    """Apex website paths that merely start with "app" are not app routes.

    `/apple-touch-icon.png` is a real file the site serves; a prefix test would
    have started 401ing it the moment the Worker route widened. In dev, where
    every path routes to the daemon, that would be a live 401 on a site asset.
    """
    for path in ("/apple-touch-icon.png", "/app-ads.txt", "/apps", "/appx/deep"):
        assert mw._auth_challenge_path(path) is False, path
    # Case matters: /APP/me is not this route, so it must not be treated as one.
    assert mw._auth_challenge_path("/APP/me") is False


def test_a_query_or_fragment_bearing_path_fails_closed():
    """An ASGI `scope["path"]` cannot carry these — the server splits the query
    and the client never sends the fragment.

    Asserted anyway because the OLD rule challenged them (they matched the
    `/mcp/` prefix), and a boundary predicate that becomes *more permissive*
    than the one it replaces is the wrong direction even on unreachable input.
    """
    for path in ("/app?subscribed=1", "/app#frag", "/app?", "/app#"):
        assert mw._auth_challenge_path(path) is True, path


def test_the_app_subtree_verdict_matches_the_pre_move_mcp_app_verdict():
    """Differential against the rule this replaces.

    The old predicate covered the app only because it sat under `/mcp/`. For
    every route suffix the app actually serves, the verdict for `/app<suffix>`
    must equal what `/mcp/app<suffix>` used to get — that equality is the whole
    safety claim of the move, and it is not visible by reading either rule alone.
    """
    def pre_move_verdict(path: str) -> bool:
        # The rule as it stood before the move, reconstructed from its parts so
        # this stays a comparison and not a restatement of the new code.
        if path in mw._DISCOVERY_PATHS:
            return False
        if path.startswith("/mcp/app/model-callback/"):
            handle = path[len("/mcp/app/model-callback/"):]
            if len(handle) == 43 and "/" not in handle:
                return False
        if path in ("/mcp/app", "/mcp/app/token", "/mcp/app/billing/webhook"):
            return False
        return path == "/mcp" or path.startswith("/mcp/")

    suffixes = [
        "", "/", "/token", "/token/x", "/me", "/me/", "/ui-frame", "/settings",
        "/billing/webhook", "/billing/webhook/x", "/billing/checkout",
        "/billing/cancel", "/billing/status", "/account/delete",
        "/connections", "/files", "/serving/bind", "/models/preferences",
        "/voice/status", "/voice/session", "/trace",
        "/openai/device/start", "/openai/device/poll", "/openai/begin",
        "/openai/exchange", "/model-connect/deposit_key",
        "/model-callback/" + "f" * 43, "/model-callback/short",
        "/model-callback/", "/../mcp/tools", "/./me", "//me",
    ]
    # The ONE intended divergence: the custom-UI bootstrap. It was challenged
    # before the move too, which was itself the bug -- an <iframe src> carries
    # no bearer, so every installed UI rendered `authentication_required` (live
    # 2026-10-01). It is a static, self-sandboxing page that holds no identity.
    intended_public = {"/ui-frame"}
    for suffix in suffixes:
        if suffix in intended_public:
            assert mw._auth_challenge_path("/app" + suffix) is False, suffix
            continue
        assert mw._auth_challenge_path("/app" + suffix) is pre_move_verdict(
            "/mcp/app" + suffix
        ), suffix


def test_the_connector_family_verdicts_are_untouched_by_the_move():
    """Hard Rule 11: `/mcp` is not part of this change."""
    expected = {
        "/mcp": True,
        "/mcp/": True,
        "/mcp/anything": True,
        "/mcp/pulse": True,
        "/mcp/.well-known/oauth-protected-resource": False,
        "/.well-known/oauth-protected-resource": False,
        "/": False,
        "/not-mcp": False,
        "/catalog": False,
    }
    for path, challenged in expected.items():
        assert mw._auth_challenge_path(path) is challenged, path


def test_retired_mcp_app_paths_are_challenged_not_exempt():
    """No back-compat: the old path keeps no carve-out of its own.

    It falls through to the ordinary /mcp/ rule, so an anonymous caller gets the
    connector's 401 rather than an exemption into a route that no longer exists.
    """
    for path in ("/mcp/app", "/mcp/app/token", "/mcp/app/billing/webhook", "/mcp/app/me"):
        assert mw._auth_challenge_path(path) is True, path


def test_billing_webhook_carve_out_moved_with_the_app():
    # Stripe POSTs with no MCP bearer; exactly one path stays open, and it is
    # the new one.
    assert mw._auth_challenge_path("/app/billing/webhook") is False
    assert mw._auth_challenge_path("/app/billing/webhook/x") is True


def _drive_h(path: str, method: str = "POST", headers: dict | None = None) -> int:
    """Like _drive but with request headers, to exercise the bearer path."""
    app = mw.AuthContextMiddleware(_ok_app)
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "path": path, "headers": hdrs}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts, "no response.start emitted"
    return starts[0]["status"]


def test_hook_route_ignores_foreign_bearer(require_auth_provider, monkeypatch):
    """A generic channel POSTing to /mcp/hooks/<token> with its OWN Authorization:
    Bearer must NOT be 401'd by the MCP challenge — the unguessable URL token +
    author-gated handler are the boundary (Codex inbound review). Without the
    carve-out an invalid bearer returned 401 before the hook handler ran."""
    tok = "a" * 43
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    # foreign bearer + valid hook path -> reaches the downstream app (200)
    assert _drive_h("/mcp/hooks/" + tok, headers={"Authorization": "Bearer foreign.xyz"}) == 200
    # the same foreign bearer on the MCP endpoint is still challenged (401)
    assert _drive_h("/mcp", headers={"Authorization": "Bearer foreign.xyz"}) == 401
    # inbound OFF -> no carve-out, the hook path with a foreign bearer is 401'd
    monkeypatch.delenv("TINYASSETS_INBOUND_ENABLED", raising=False)
    assert _drive_h("/mcp/hooks/" + tok, headers={"Authorization": "Bearer foreign.xyz"}) == 401
