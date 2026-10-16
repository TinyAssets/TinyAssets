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

        headers = {}
        try:
            for name, value in scope["headers"]:
                name = name.lower()
                if name in {b"x-owner", b"x-box-secret", b"x-owner-identity"}:
                    if name in headers:
                        raise ValueError("duplicate router header")
                    headers[name] = value
            if not hmac.compare_digest(headers.get(b"x-box-secret", b""), self.secret):
                raise ValueError("router secret required")
            identity = Identity(**json.loads(base64.b64decode(
                headers[b"x-owner-identity"], altchars=b"-_", validate=True)))
            # The only source is the authenticated router, never a public bearer.
            identity.user_id = headers[b"x-owner"].decode("utf-8")
            if not identity.user_id:
                raise ValueError("router identity required")
        except (ValueError, KeyError, TypeError):
            return await JSONResponse({"error": "router_required"}, status_code=401)(
                scope, receive, send)
        scope = dict(scope, tinyassets_router_identity=identity)
        await self.app(scope, receive, send)
