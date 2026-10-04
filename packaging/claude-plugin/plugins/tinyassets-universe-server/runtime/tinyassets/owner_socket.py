"""Trusted metadata normalization for the private owner socket only."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit


class OwnerSocketMiddleware:
    def __init__(self, app):
        from tinyassets.auth.wellknown import protected_resource_metadata

        self.app = app
        host = os.environ.get("TINYASSETS_PUBLIC_HOST")
        if not host:
            host = urlsplit(protected_resource_metadata().get("resource", "")).netloc
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            scope = dict(scope)
            scope["scheme"] = "wss" if scope["type"] == "websocket" else "https"
            headers = []
            for name, value in scope.get("headers", []):
                if name.lower() == b"x-ta-client-peer":
                    value = value.decode("latin-1")
                    try:
                        ip_address(value)
                    except ValueError:
                        peer = urlsplit("//" + value)
                        scope["client"] = (peer.hostname, peer.port or 0)
                    else:
                        scope["client"] = (value, 0)
                elif not (self.host and name.lower() == b"host"):
                    headers.append((name, value))
            if self.host:
                public = urlsplit("//" + self.host)
                scope["server"] = (public.hostname, public.port or 443)
                headers.append((b"host", self.host.encode("latin-1")))
            scope["headers"] = headers
        await self.app(scope, receive, send)
