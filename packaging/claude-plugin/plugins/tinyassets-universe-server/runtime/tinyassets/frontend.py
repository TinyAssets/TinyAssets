"""Opt-in stateless shell server and streaming owner-socket proxy."""

import os
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse

from tinyassets.onboarding import app_response, onboarding_enabled

_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}


def _end_to_end(headers):
    excluded = set(_HOP_HEADERS)
    for name, value in headers:
        if name.lower() == b"connection":
            excluded.update(part.strip().lower() for part in value.split(b","))
    return [(name, value) for name, value in headers if name.lower() not in excluded]


class Frontend:
    def __init__(self, client, build, colour):
        self.client = client
        self.build = build
        self.identity = f"{colour}/{build}".encode("latin-1")

    async def __call__(self, scope, receive, send):
        async def stamped(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, v) for k, v in message["headers"] if k.lower() != b"x-ta-frontend"
                ] + [(b"x-ta-frontend", self.identity)]
            await send(message)

        request = Request(scope, receive)
        # The shell is served here only where the owner would serve it; with the
        # onboarding flag off the owner answers 404, so the request is proxied.
        if (request.method in {"GET", "HEAD"} and request.url.path == "/app"
                and onboarding_enabled()):
            response = app_response(build=self.build)
        elif request.method == "GET" and request.url.path == "/healthz":
            response = PlainTextResponse("ok")
        else:
            headers = [
                (k, v) for k, v in _end_to_end(scope["headers"]) if k.lower() != b"x-ta-client-peer"
            ]
            if request.client:
                host, port = request.client
                peer = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
                headers.append((b"x-ta-client-peer", peer.encode("ascii")))
            path = scope.get("raw_path", scope["path"].encode("utf-8"))
            query = scope.get("query_string", b"")
            url = httpx.URL("http://owner").copy_with(
                raw_path=path + (b"?" + query if query else b"")
            )
            upstream_request = httpx.Request(
                request.method, url, headers=headers, content=request.stream()
            )
            try:
                upstream = await self.client.send(upstream_request, stream=True)
            except httpx.TransportError:
                response = JSONResponse(
                    {"error": "TinyAssets is restarting; try again in a moment."},
                    status_code=503,
                )
            else:
                response = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code)
                response.raw_headers = _end_to_end(upstream.headers.raw)
                try:
                    await response(scope, receive, stamped)
                finally:
                    await upstream.aclose()
                return
        await response(scope, receive, stamped)


def create_app():
    socket_path = os.environ["TINYASSETS_OWNER_SOCKET"]
    build = os.environ["TINYASSETS_FRONTEND_BUILD"]
    if not socket_path or not build:
        raise ValueError("Owner socket and frontend build must be nonempty")
    client = httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=socket_path, retries=0),
        timeout=None,
        follow_redirects=False,
    )

    @asynccontextmanager
    async def lifespan(app):
        async with client:
            yield

    app = Starlette(lifespan=lifespan)
    app.router.default = Frontend(
        client, build, os.environ.get("TINYASSETS_FRONTEND_COLOUR") or "frontend"
    )
    return app


def main():
    import uvicorn

    uvicorn.run(
        create_app(), host="0.0.0.0", port=int(os.environ.get("TINYASSETS_FRONTEND_PORT", "8011")),
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
