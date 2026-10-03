"""Dark frontend proxy contract, including real socket streaming on Linux."""

import asyncio
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tinyassets import onboarding
from tinyassets.frontend import create_app
from tinyassets.owner_socket import OwnerSocketMiddleware


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PUBLIC_HOST", "public.example")
    monkeypatch.setenv("TINYASSETS_FRONTEND_BUILD", "abc")
    monkeypatch.setenv("TINYASSETS_FRONTEND_COLOUR", "blue")
    monkeypatch.setenv("TINYASSETS_OWNER_SOCKET", "/absent/owner.sock")


def test_render_build(monkeypatch):
    monkeypatch.setattr(onboarding, "build_sha", lambda: "owner")
    assert onboarding.app_config()["build"] == "owner"
    assert '"build": "owner"' in onboarding.render_app_html()[0]

    def forbidden():
        raise AssertionError("frontend read owner receipt")

    monkeypatch.setattr(onboarding, "build_sha", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("tinyassets.storage.data_dir", forbidden)
    assert onboarding.app_config(build="abc")["build"] == "abc"
    assert '"build": "abc"' in onboarding.render_app_html(build="abc")[0]


@pytest.mark.parametrize("kind,scheme", [("http", "https"), ("websocket", "wss")])
def test_socket_metadata(kind, scheme):
    scopes = []

    async def capture(scope, receive, send):
        scopes.append(scope)

    scope = {
        "type": kind,
        "scheme": "http",
        "server": ("tcp", 80),
        "client": ("original", 1),
        "headers": [
            (b"host", b"attacker"),
            (b"x-forwarded-host", b"evil.example"),
            (b"x-ta-client-peer", b"192.0.2.1:234"),
        ],
    }
    asyncio.run(OwnerSocketMiddleware(capture)(scope, None, None))
    normalized = scopes.pop()
    assert normalized["scheme"] == scheme
    assert normalized["server"] == ("public.example", 443)
    assert normalized["client"] == ("192.0.2.1", 234)
    hosts = [value for name, value in normalized["headers"] if name.lower() == b"host"]
    assert hosts == [b"public.example"], "the caller's Host must be replaced, not joined"
    assert b"x-ta-client-peer" not in dict(normalized["headers"])
    asyncio.run(capture(scope, None, None))
    assert scopes[0] == scope
    assert scope["scheme"] == "http"
    assert dict(scope["headers"])[b"host"] == b"attacker"


@pytest.mark.parametrize(
    "resource,expected", [("https://fallback.example/mcp", "fallback.example"), ("", "original")]
)
def test_host_fallback(monkeypatch, resource, expected):
    monkeypatch.delenv("TINYASSETS_PUBLIC_HOST")
    monkeypatch.setattr(
        "tinyassets.auth.wellknown.protected_resource_metadata", lambda: {"resource": resource}
    )
    scopes = []

    async def capture(scope, receive, send):
        scopes.append(scope)

    asyncio.run(
        OwnerSocketMiddleware(capture)(
            {"type": "http", "headers": [(b"host", b"original")], "client": ("peer", 1)}, None, None
        )
    )
    assert dict(scopes[0]["headers"])[b"host"].decode() == expected
    assert scopes[0]["client"] == ("peer", 1)


def test_local_shell_and_health(monkeypatch):
    def forbidden():
        raise AssertionError("owner state accessed")

    monkeypatch.setattr(onboarding, "build_sha", forbidden)
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    with TestClient(create_app()) as client:
        for path in ("/healthz", "/app"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["x-ta-frontend"] == "blue/abc"
        assert client.get("/healthz").text == "ok"
        response = client.get("/app")
        assert '"build": "abc"' in response.text
        assert "content-security-policy" in response.headers
        assert response.headers["x-tinyassets-build"] == "abc"
        response = client.head("/app")
        assert response.content == b""
        assert response.headers["x-tinyassets-build"] == "abc"


def test_owner_configs():
    from tinyassets.universe_server import GRACEFUL_SHUTDOWN_S, _serve_configs

    async def app(scope, receive, send):
        pass

    tcp, uds = _serve_configs(app, "127.0.0.1", 8123, "/tmp/owner.sock")
    assert tcp.app is app
    assert (tcp.host, tcp.port, tcp.lifespan) == ("127.0.0.1", 8123, "auto")
    assert isinstance(uds.app, OwnerSocketMiddleware)
    assert uds.app.app is app
    assert uds.uds == "/tmp/owner.sock"
    assert uds.lifespan == "off"
    assert uds.proxy_headers is False
    assert tcp.timeout_graceful_shutdown == uds.timeout_graceful_shutdown == GRACEFUL_SHUTDOWN_S


unix_only = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "AF_UNIX"),
    reason="Real Unix socket transport requires non-Windows AF_UNIX",
)


@contextmanager
def serving(app, **kwargs):
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", **kwargs))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive(), "server exited before startup"
            assert time.monotonic() < deadline, "server startup timed out"
            time.sleep(0.01)
        yield server
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive(), "server failed to stop"


@unix_only
def test_absent_owner():
    with TestClient(create_app()) as client:
        response = client.get("/mcp")
        assert response.status_code == 503
        assert response.json() == {"error": "TinyAssets is restarting; try again in a moment."}
        assert response.headers["x-ta-frontend"] == "blue/abc"


@unix_only
def test_real_socket_proxy(monkeypatch):
    ended = threading.Event()

    async def echo(request):
        response = JSONResponse(
            {
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "headers": dict(request.headers),
                "scheme": request.url.scheme,
                "host": request.url.hostname,
                "client": list(request.client),
                "body": (await request.body()).decode(),
            }
        )
        response.raw_headers.extend(
            [
                (b"set-cookie", b"a=1"),
                (b"set-cookie", b"b=2"),
                (b"connection", b"x-private"),
                (b"x-private", b"secret"),
            ]
        )
        return response

    async def events(request):
        async def body():
            yield b"data: first\n\n"
            await asyncio.sleep(0.5)
            ended.set()
            yield b"data: last\n\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    owner = OwnerSocketMiddleware(
        Starlette(
            routes=[
                Route("/events", events),
                Route("/{path:path}", echo, methods=["GET", "POST", "PATCH"]),
            ]
        )
    )
    # Short system-temp path avoids AF_UNIX's 108-byte path limit.
    with tempfile.TemporaryDirectory(prefix="c1a-") as directory:
        path = str(Path(directory) / "o.sock")
        monkeypatch.setenv("TINYASSETS_OWNER_SOCKET", path)
        with serving(owner, uds=path), serving(create_app(), host="127.0.0.1", port=0) as frontend:
            port = frontend.servers[0].sockets[0].getsockname()[1]
            with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
                response = client.post(
                    "/echo?q=a%2Fb",
                    content=iter([b"hello", b" world"]),
                    headers={
                        "x-ta-client-peer": "attacker:999",
                        "x-forwarded-host": "evil.example",
                        "connection": "x-private",
                        "x-private": "secret",
                        "mcp-session-id": "session",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data["method"] == "POST"
                assert data["path"] == "/echo"
                assert data["query"] == "q=a%2Fb"
                assert data["body"] == "hello world"
                assert data["scheme"] == "https"
                assert data["host"] == "public.example"
                assert data["client"][0] == "127.0.0.1"
                assert "x-ta-client-peer" not in data["headers"]
                assert "x-private" not in data["headers"]
                assert data["headers"]["mcp-session-id"] == "session"
                assert response.headers.get_list("set-cookie") == ["a=1", "b=2"]
                assert "x-private" not in response.headers
                assert response.headers["x-ta-frontend"] == "blue/abc"
                assert client.request("PATCH", "/app").json()["method"] == "PATCH"
                with client.stream("GET", "/events") as response:
                    assert response.headers["x-ta-frontend"] == "blue/abc"
                    chunks = response.iter_raw()
                    assert b"first" in next(chunks)
                    assert not ended.is_set(), "SSE buffered until stream ended"
                    assert b"last" in b"".join(chunks)


@unix_only
def test_the_shell_is_proxied_when_the_owner_would_not_serve_it(monkeypatch):
    """With the onboarding flag off the owner answers 404 for /app; the frontend
    must not serve a shell the owner would refuse -- it proxies, and with no owner
    that is the honest 503, never a locally rendered page."""
    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP", raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/app")
        assert response.status_code == 503
        assert "build" not in response.text
