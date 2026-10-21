"""Silent session renewal for the onboarding app.

AuthKit access tokens live ~5 minutes (live phone test 2026-08-22: the app
signed the founder out mid-conversation with no message). The token proxy now
keeps the refresh token in an HttpOnly cookie scoped to itself and renews on a
``grant_type=refresh_token`` request; the page never sees the refresh token.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from tinyassets import onboarding

# Captured before any test monkeypatches httpx.AsyncClient, so _Client always
# subclasses the REAL client — not a previous call's mock (which chained the
# wrong upstream when _drive ran more than once per test).
_PRISTINE_ASYNC_CLIENT = httpx.AsyncClient


def _ok(access: str, refresh: str, exp: int = 300) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": access, "refresh_token": refresh, "expires_in": exp}
    )


def _boom(_form):
    raise AssertionError("must not call AuthKit")


def _drive(
    body: dict,
    *,
    cookie: str = "",
    origin: str = "https://tinyassets.io",
    content_type: str = "application/json",
    monkeypatch,
    upstream,
    data_dir: str = "",
    provider=None,
):
    """Drive the token route with a scripted AuthKit token endpoint.

    ``data_dir`` isolates the server-side refresh-session store; pass the SAME
    value across calls to test a mint-then-refresh-by-handle sequence. When
    omitted, a throwaway temp dir keeps the test off the real data root.
    """
    import tempfile

    from starlette.requests import Request

    monkeypatch.setenv("TINYASSETS_DATA_DIR", data_dir or tempfile.mkdtemp())
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(
        onboarding,
        "app_config",
        lambda: {
            "issuer": "https://authkit.example",
            "authorization_endpoint": "https://authkit.example/oauth2/authorize",
            "token_endpoint": "https://authkit.example/oauth2/token",
            "resource": "https://tinyassets.io/mcp",
            "client_id": "client_x",
            "scopes": "openid",
            "configured": True,
            "openai": {},
        },
    )
    from types import SimpleNamespace

    from tinyassets.auth import middleware
    from tinyassets.auth.provider import Identity

    if provider is None:
        from tests.support.broker_double import seed_owner_admission
        from tinyassets.storage import data_dir

        seed_owner_admission(data_dir(), principal="owner", center="cc-owner")

    monkeypatch.setattr(middleware, "_get_provider", lambda: provider or SimpleNamespace(
        resolve_token=lambda token: Identity(user_id="owner", username="owner")))
    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        form = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        calls.append(form)
        return upstream(form)

    real_client = _PRISTINE_ASYNC_CLIENT

    class _Client(real_client):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    route = next(r for r in onboarding.onboarding_routes() if r.path == "/app/token")
    raw = json.dumps(body).encode()
    headers = [
        (b"content-type", content_type.encode()),
        (b"content-length", str(len(raw)).encode()),
        (b"host", b"tinyassets.io"),
    ]
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/app/token",
        "headers": headers,
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    resp = asyncio.run(route.endpoint(Request(scope, receive)))
    set_cookie = [v.decode() for k, v in resp.raw_headers if k == b"set-cookie"]
    return resp.status_code, json.loads(resp.body), set_cookie, calls
























