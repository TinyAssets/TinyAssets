"""Dark frontend proxy contract, including real socket streaming on Linux."""

import asyncio
import socket
import sys
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from tinyassets import onboarding
from tinyassets.frontend import create_app


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


@pytest.mark.parametrize(
    "state", ["oa_" + "r" * 43, "oa_app_" + "r" * 43,
              "na_pending", "app.pending", "appdebug.pending"]
)
def test_auth_callbacks_are_forwarded_to_owner(monkeypatch, state):
    from tinyassets.frontend import Frontend

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")

    async def run():
        seen = []

        def owner(request):
            seen.append(str(request.url))
            return httpx.Response(200, stream=httpx.ByteStream(b"owner callback"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(owner)) as upstream:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=Frontend(upstream, "test", "blue")),
                base_url="https://tinyassets.io",
            ) as client:
                result = await client.get("/app", params={"state": state, "code": "test"})
                assert result.text == "owner callback"
                assert len(seen) == 1 and f"state={state}" in seen[0]

    asyncio.run(run())




def test_local_shell_and_health(monkeypatch):
    def forbidden():
        raise AssertionError("owner state accessed")

    monkeypatch.setattr(onboarding, "build_sha", forbidden)
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    with TestClient(create_app()) as client:
        for path in ("/healthz", "/app", "/app?state=oa_app_pending",
                     "/app?state=oa_" + "r" * 42, "/app?state=oa_" + "r" * 44):
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
def test_the_shell_is_proxied_when_the_owner_would_not_serve_it(monkeypatch):
    """With the onboarding flag off the owner answers 404 for /app; the frontend
    must not serve a shell the owner would refuse -- it proxies, and with no owner
    that is the honest 503, never a locally rendered page."""
    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP", raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/app")
        assert response.status_code == 503
        assert "build" not in response.text
