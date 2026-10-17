"""The shared door: authenticated identity -> broker admission -> Unix HTTP stream.

This module never interprets a request body or imports an owner store. Boxhostd
will own ``resolve_box``'s wake step; socket naming has just this one home.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import anyio
import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
        b"te", b"trailer", b"transfer-encoding", b"upgrade"}
_TRUSTED = {b"x-owner", b"x-box-secret", b"x-owner-identity"}
_SHARED_APP = {
    "/app", "/app/client-error", "/app/token", "/app/native-sign-in",
    "/app/owner-sign-in", "/app/owner-sign-in/complete", "/app/canary-sign-in",
    "/app/oauth/client-metadata.json", "/app/ui-frame", "/app/sw.js",
    # Sign-in navigation: its rows live in the router's owner-sessions store.
    "/app/approval-handoff", "/app/connections",
}
_SHARED_APP_PREFIXES = ("/app/billing/", "/app/run/", "/app/m/", "/app/model-callback/",
                        "/app/approval-handoff/")


def hook_token(path: str) -> str | None:
    prefix = "/hooks/inbound/"
    if path.startswith(prefix):
        token = path[len(prefix):]
        if token and "/" not in token:
            return token
    return None


def forwarded_path(path: str) -> bool:
    """Owner doors are removed from the shared route table, not shadowed."""
    if path.startswith("/app/"):
        return path not in _SHARED_APP and not path.startswith(_SHARED_APP_PREFIXES)
    return (path in {"/mcp", "/mcp/", "/mcp/events", "/api"}
            or path.startswith(("/api/", "/hooks/inbound/")))


@dataclass(frozen=True)
class BoxRoute:
    socket: Path
    secret: str


def resolve_box(owner: str) -> BoxRoute:
    from tinyassets.broker.owner_identities import owner_box_route
    from tinyassets.storage import data_dir

    uid, secret = owner_box_route(data_dir(), principal=owner)
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    return BoxRoute(root / str(uid) / "app.sock", secret)


def _headers(headers, *, private=False):
    blocked = set(_HOP)
    for name, value in headers:
        if name.lower() == b"connection":
            blocked.update(piece.strip().lower() for piece in value.split(b","))
    if private:
        blocked.update(_TRUSTED | {b"authorization", b"cookie"})
    return [(name, value) for name, value in headers if name.lower() not in blocked]


class OwnerRouter:
    def __init__(self, app, *, resolver=resolve_box):
        self.app, self.resolver = app, resolver

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not forwarded_path(scope["path"]):
            return await self.app(scope, receive, send)
        from tinyassets.auth.middleware import current_identity_or_none

        identity = current_identity_or_none()
        token = hook_token(scope["path"])
        if token is not None:
            from tinyassets.router_credentials import hook_identity
            from tinyassets.storage import data_dir

            identity = await run_in_threadpool(hook_identity, data_dir(), token)
        if identity is None:
            return await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send)
        if scope["path"].startswith("/app/"):
            from tinyassets.onboarding.owner_sessions import COOKIE, lookup

            session = await run_in_threadpool(lookup, Request(scope).cookies.get(COOKIE, ""))
            if session is not None:
                if json.loads(session["identity_json"])["user_id"] != identity.user_id:
                    return await JSONResponse({"error": "identity_conflict"}, status_code=401)(
                        scope, receive, send)
                # Protected-view proof is shared session state; its consumer in
                # the box retains the existing exact-origin/expiry checks.
                from dataclasses import replace

                identity = replace(identity, metadata={**identity.metadata,
                                                       "router_owner_session": session})
        try:
            route = await run_in_threadpool(self.resolver, identity.user_id)
        except LookupError:
            return await JSONResponse({"error": "not_found"}, status_code=404)(
                scope, receive, send)
        except (OSError, RuntimeError):
            return await JSONResponse({"error": "box_unavailable"}, status_code=503)(
                scope, receive, send)
        await forward(scope, receive, send, identity, route)


async def forward(scope, receive, send, identity, route: BoxRoute):
    """One reverse proxy for every owner door, with bounded streaming and cancellation."""
    headers = _headers(scope["headers"], private=True)
    headers.extend([
        (b"x-owner", identity.user_id.encode("utf-8")),
        (b"x-box-secret", route.secret.encode("ascii")),
        (b"x-owner-identity", base64.urlsafe_b64encode(
            json.dumps(asdict(identity), separators=(",", ":")).encode("utf-8"))),
    ])
    target = scope.get("raw_path", scope["path"].encode("utf-8"))
    if scope.get("query_string"):
        target += b"?" + scope["query_string"]
    url = httpx.URL("http://owner").copy_with(raw_path=target)
    started = False
    try:
        transport = httpx.AsyncHTTPTransport(uds=str(route.socket))
        timeout = httpx.Timeout(connect=5, read=None, write=30, pool=5)
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            async with client.stream(scope["method"], url, headers=headers,
                                     content=Request(scope, receive).stream()) as response:
                await send({"type": "http.response.start", "status": response.status_code,
                            "headers": _headers(response.headers.raw)})
                started = True
                async with anyio.create_task_group() as tasks:
                    async def disconnect():
                        while (await receive())["type"] != "http.disconnect":
                            pass
                        tasks.cancel_scope.cancel()

                    tasks.start_soon(disconnect)
                    async for chunk in response.aiter_raw():
                        await send({"type": "http.response.body", "body": chunk,
                                    "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
                    tasks.cancel_scope.cancel()
    except ClientDisconnect:
        return
    except httpx.HTTPError:
        if started:
            raise  # A truncated stream must never look like a successful response.
        await JSONResponse({"error": "box_unavailable"}, status_code=503)(scope, receive, send)
