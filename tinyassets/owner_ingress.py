"""Authenticate the router on the owner's socket before any handler runs."""
from __future__ import annotations

import base64
import hmac
import json
import re
from pathlib import Path

from starlette.responses import JSONResponse


class OwnerIngress:
    def __init__(self, app, secret_file: Path):
        self.app = app
        self.secret = secret_file.read_bytes().strip()
        if re.fullmatch(rb"[A-Za-z0-9_-]{43}", self.secret) is None:
            raise ValueError("invalid per-box secret file")

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from tinyassets.auth.provider import Identity
        from tinyassets.owner_activity import activity

        headers = {}
        try:
            for name, value in scope["headers"]:
                name = name.lower()
                if name in {b"x-owner", b"x-box-secret", b"x-owner-identity"}:
                    if name in headers:
                        raise ValueError("duplicate router header")
                    headers[name] = value
            if activity.revoked or not hmac.compare_digest(
                    headers.get(b"x-box-secret", b""), self.secret):
                raise ValueError("router secret required")
            if scope["path"] == "/_box/status" and scope["method"] == "GET":
                return await JSONResponse(activity.status())(scope, receive, send)
            if scope["path"] in {"/_box/drain", "/_box/revoke"} and scope["method"] == "POST":
                from starlette.requests import Request

                doc = await Request(scope, receive).json()
                idle = doc.get("idle_seconds", 0)
                if type(idle) not in (int, float) or not 0 <= idle <= 86400:
                    raise ValueError("invalid idle interval")
                result = activity.status(drain=True, idle=idle,
                                         revoke=scope["path"] == "/_box/revoke")
                return await JSONResponse(result)(scope, receive, send)
            identity = Identity(**json.loads(base64.b64decode(
                headers[b"x-owner-identity"], altchars=b"-_", validate=True)))
            # The only source is the authenticated router, never a public bearer.
            identity.user_id = headers[b"x-owner"].decode("utf-8")
            if not identity.user_id:
                raise ValueError("router identity required")
            # One owner lives in this box: a request for anyone else is refused
            # here, by the silo itself (research: the instance authorizes each call).
            from tinyassets.owner_runtime import current

            runtime = current()
            if runtime is None or identity.user_id != runtime.principal:
                raise ValueError("request is not for this box's owner")
        except (ValueError, KeyError, TypeError):
            return await JSONResponse({"error": "router_required"}, status_code=401)(
                scope, receive, send)
        scope = dict(scope, tinyassets_router_identity=identity)
        # Admission and drain share a lock: a successful drain cannot race a
        # new request or background turn. No control request counts as activity.
        gate = activity.enter()
        try:
            gate.__enter__()
        except RuntimeError:
            return await JSONResponse({"error": "box_draining"}, status_code=503)(
                scope, receive, send)
        # A passive MCP event subscription is not a running turn. Actual MCP
        # calls and detached turns hold their own gate; an idle subscription
        # may reconnect after its box sleeps.
        if scope["method"] == "GET" and scope["path"] in {"/mcp", "/mcp/", "/mcp/events"}:
            gate.__exit__(None, None, None)
            return await self.app(scope, receive, send)
        try:
            if scope["path"] == "/_box/account/delete" and scope["method"] == "POST":
                response = await delete_local_account(identity)
                await response(scope, receive, send)
            else:
                await self.app(scope, receive, send)
        finally:
            gate.__exit__(None, None, None)


async def delete_local_account(identity):
    from starlette.concurrency import run_in_threadpool

    from tinyassets.account_deletion import (
        AccountDeletionBlocked,
        AccountDeletionError,
        delete_account,
    )
    from tinyassets.auth.middleware import identity_context
    from tinyassets.owner_runtime import current

    def erase():
        with identity_context(identity):
            return delete_account(current().root, founder_sub=identity.user_id)

    try:
        receipt = await run_in_threadpool(erase)
        return JSONResponse(receipt)
    except AccountDeletionBlocked as exc:
        return JSONResponse({"error": "deletion_blocked", "reasons": str(exc).split("; ")},
                            status_code=409)
    except AccountDeletionError as exc:
        return JSONResponse({"error": "deletion_refused", "detail": str(exc)}, status_code=409)
