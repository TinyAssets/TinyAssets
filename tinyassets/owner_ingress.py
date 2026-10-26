"""Authenticate the router on the owner's socket before any handler runs."""
from __future__ import annotations

import base64
import hmac
import json
import re
from pathlib import Path

from starlette.responses import JSONResponse

from tinyassets.asgi_scope import refuse_non_http

BUILD_SHA_FILE = Path("/etc/tinyassets-build-sha")


class OwnerIngress:
    def __init__(self, app, secret_file: Path):
        self.app = app
        self.secret = secret_file.read_bytes().strip()
        if re.fullmatch(rb"[A-Za-z0-9_-]{43}", self.secret) is None:
            raise ValueError("invalid per-box secret file")

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return await refuse_non_http(scope, send)
        from tinyassets.auth.provider import Identity
        from tinyassets.owner_activity import activity

        headers = {}
        try:
            for name, value in scope["headers"]:
                name = name.lower()
                if name in {b"x-owner", b"x-box-secret", b"x-owner-identity",
                            b"x-tinyassets-caller-user-id"}:
                    if name in headers:
                        raise ValueError("duplicate router header")
                    headers[name] = value
            if not hmac.compare_digest(
                    headers.get(b"x-box-secret", b""), self.secret):
                raise ValueError("router secret required")
            if scope["path"] == "/_box/delete-admission" and scope["method"] == "POST":
                activity.admit_deletion_only()
                return await JSONResponse(activity.status())(scope, receive, send)
            if activity.revoked:
                raise ValueError("owner ingress is withdrawn")
            if scope["path"] == "/_box/status" and scope["method"] == "GET":
                return await JSONResponse(activity.status())(scope, receive, send)
            if (scope["path"] in {"/_box/drain", "/_box/revoke", "/_box/snapshot-stop"}
                    and scope["method"] == "POST"):
                from starlette.requests import Request

                doc = await Request(scope, receive).json()
                idle = doc.get("idle_seconds", 0)
                if type(idle) not in (int, float) or not 0 <= idle <= 86400:
                    raise ValueError("invalid idle interval")
                result = activity.status(drain=True, idle=idle,
                                         snapshot=scope["path"] == "/_box/snapshot-stop",
                                         revoke=scope["path"] == "/_box/revoke")
                return await JSONResponse(result)(scope, receive, send)
            identity = Identity(**json.loads(base64.b64decode(
                headers[b"x-owner-identity"], altchars=b"-_", validate=True)))
            authenticated = identity.user_id
            # The only source is the authenticated router, never a public bearer.
            identity.user_id = headers[b"x-owner"].decode("utf-8")
            if not identity.user_id:
                raise ValueError("router identity required")
            # One owner lives in this box: a request for anyone else is refused
            # here, by the silo itself (research: the instance authorizes each call).
            from tinyassets.owner_runtime import current

            runtime = current()
            if identity.user_id != runtime.principal:
                raise ValueError("request is not for this box's owner")
            raw_caller = headers.get(b"x-tinyassets-caller-user-id")
            if raw_caller is None:
                if authenticated != identity.user_id:
                    raise ValueError("caller header required")
                caller = identity.user_id
            else:
                try:
                    caller = raw_caller.decode("utf-8")
                except UnicodeDecodeError:
                    raise ValueError("caller is not text") from None
                if not caller or caller != authenticated:
                    raise ValueError("caller does not match the authenticated account")
            identity.metadata = {**dict(identity.metadata or {}), "caller_user_id": caller}
            if caller != runtime.principal:
                from tinyassets.app_access import visitor_surface

                if not visitor_surface(scope["path"], scope["method"]):
                    return await JSONResponse(
                        {"error": "private_surface"}, status_code=403)(
                            scope, receive, send)
        except (ValueError, KeyError, TypeError):
            return await JSONResponse({"error": "router_required"}, status_code=401)(
                scope, receive, send)
        scope = dict(scope, tinyassets_router_identity=identity,
                     tinyassets_caller_user_id=caller)
        async def proven_send(message):
            if message["type"] == "http.response.start":
                # Provenance comes from this running box and its immutable image,
                # never from router input or the shared release-state receipt.
                build = BUILD_SHA_FILE
                sha = build.read_text().strip() if build.exists() else ""
                names = {b"x-tinyassets-box-uid", b"x-tinyassets-box-sha"}
                message = {**message, "headers": [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in names
                ] + [(b"x-tinyassets-box-uid", str(runtime.uid).encode()),
                     (b"x-tinyassets-box-sha", sha.encode())]}
            await send(message)

        # Admission and drain share a lock: a successful drain cannot race a
        # new request or background turn. No control request counts as activity.
        # Waiting for an external sign-in is passive, including its page polls.
        polling_login = (scope["method"] == "POST"
                         and scope["path"] == "/app/subscription-connect/poll")
        gate = activity.enter(keep_awake=not polling_login)
        try:
            gate.__enter__()
        except RuntimeError:
            body = {"error": "box_draining"}
            if activity.snapshot_stop:
                body["retry"] = True
            return await JSONResponse(body, status_code=503)(scope, receive, proven_send)
        # A passive MCP event subscription is not a running turn. Actual MCP
        # calls and detached turns hold their own gate; an idle subscription
        # may reconnect after its box sleeps.
        if scope["method"] == "GET" and scope["path"] in {"/mcp", "/mcp/", "/mcp/events"}:
            gate.__exit__(None, None, None)
            return await self.app(scope, receive, proven_send)
        try:
            await self.app(scope, receive, proven_send)
        finally:
            gate.__exit__(None, None, None)

