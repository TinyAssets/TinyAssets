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
















# --- pure predicate: the exemption itself ---




















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


@pytest.mark.verifies("live-mcp-connector-surface:Every handle requires a named principal")
def test_owner_router_still_links_an_anonymous_tool_call(require_auth_provider):
    """Anonymous tools/call is the hosted linking result on the router too.

    The public canary proves that contract at https://tinyassets.io/mcp.
    The owner router has no box to forward an anonymous call to, so a
    transport 401 here is the canary's HTTP 401 and a rollback of a healthy
    daemon. Initialize with no bearer stays a 401.
    """
    import json

    called = {"app": False}

    async def app(scope, receive, send):
        called["app"] = True
        await _ok_app(scope, receive, send)

    middleware = mw.AuthContextMiddleware(app, router=True)
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "converse", "arguments": {"message": "probe"}},
    }).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "method": "POST", "path": "/mcp",
        "headers": [(b"content-type", b"application/json"),
                    (b"accept", b"application/json, text/event-stream")],
    }
    asyncio.run(middleware(scope, receive, send))
    starts = [message for message in sent if message["type"] == "http.response.start"]
    bodies = [message.get("body", b"") for message in sent if message["type"] == "http.response.body"]
    assert starts[0]["status"] == 200
    payload = json.loads(b"".join(bodies))
    assert payload["id"] == 3
    assert payload["result"]["isError"] is True
    assert payload["result"]["_meta"]["mcp/www_authenticate"]
    assert called["app"] is False
    assert _drive("/mcp", "POST") == 401
