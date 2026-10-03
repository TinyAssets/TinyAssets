"""The origin's SSE response for a long tool call is not silent: it pings.

``docs/concerns/2026-08-28-converse-sse-stream-has-no-keepalive.md`` was filed
on the premise that a ``converse`` response "sends nothing at all until the
result frame". Traced on 2026-08-29: fastmcp 3 hands the POST to the MCP SDK's
``StreamableHTTPServerTransport``, which answers with
``sse_starlette.EventSourceResponse(content, data_sender_callable, headers)``
- ``ping`` unset - and sse-starlette's default is a ``: ping`` comment every
15 seconds, written by a task that runs alongside the tool.

What this proves, and what it does not (Codex review, 2026-08-29): it drives
the production app CONSTRUCTION (``create_streamable_http_app``, its own
middleware stack) under whatever package versions the run resolves
(``fastmcp>=3.0`` floats), and reads the body Starlette's ``TestClient`` hands
back - which is buffered to EOF. So it proves the origin emits the pings
before the result, at the application layer. It does NOT prove delivery
through uvicorn, cloudflared, Cloudflare or the Worker; the concern stays
open on that question until a >3-minute live ``converse`` is captured.
"""

from __future__ import annotations

import json

import anyio
import pytest
from sse_starlette.sse import EventSourceResponse
from starlette.testclient import TestClient

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

pytestmark = pytest.mark.usefixtures("cloud_runtime")

#: The daemon serves no anonymous read, so a probe driving the REAL app through
#: the real middleware has to present a bearer -- exactly as the connector does.
#: Without it every request here 401s before the transport is exercised at all,
#: which would test the auth gate instead of the keepalive.
_PROBE_BEARER = "keepalive-probe-token"

_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {_PROBE_BEARER}",
}


@pytest.fixture
def app_with_slow_tool(tmp_path, monkeypatch):
    """The production app plus one temporary tool that sleeps longer than the
    (scaled) ping interval. Removed again so the canonical tool set is not
    widened for any other test."""
    from tinyassets import universe_server as us
    from tinyassets.auth import middleware
    from tinyassets.auth.provider import AuthProvider, Identity

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    # 15s in production; scaled so the test takes seconds, not a minute.
    monkeypatch.setattr(EventSourceResponse, "DEFAULT_PING_INTERVAL", 1)

    class _ProbeProvider(AuthProvider):
        """Resolves exactly one token, the way the connector's does."""

        def resolve_token(self, token):
            if token != _PROBE_BEARER:
                return None
            return Identity(
                user_id="keepalive-probe",
                username="keepalive-probe",
                capabilities=["tinyassets.universe.read"],
            )

        def is_auth_required(self) -> bool:
            return True

        def register_client(self, metadata):
            return {"client_id": "keepalive-probe-client", **metadata}

        def create_authorization(self, *args, **kwargs):
            raise NotImplementedError("the probe never runs an OAuth dance")

        def exchange_code(self, *args, **kwargs):
            raise NotImplementedError("the probe never runs an OAuth dance")

    # monkeypatch, not set_provider: set_provider returns None, so the old
    # `previous = set_provider(...)` never restored anything and this probe
    # stayed the process's auth provider for every later test.
    monkeypatch.setattr(middleware, "_provider", _ProbeProvider())

    async def slow_probe(seconds: float = 3.0) -> str:
        await anyio.sleep(seconds)
        return "probe done"

    us.mcp.tool(name="slow_probe")(slow_probe)
    try:
        yield us.create_streamable_http_app()
    finally:
        provider = getattr(us.mcp, "local_provider", None)   # fastmcp >= 3.4
        (provider.remove_tool if provider is not None else us.mcp.remove_tool)("slow_probe")


def _rpc(method: str, params: dict | None, rid: int | None) -> str:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if rid is not None:
        msg["id"] = rid
    return json.dumps(msg)


def _open_session(client: TestClient) -> str:
    r = client.post("/mcp", content=_rpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "keepalive-probe", "version": "0"},
    }, 1), headers=_HEADERS)
    assert r.status_code == 200, r.text
    sid = r.headers.get("mcp-session-id")
    assert sid, "initialize did not open a session"
    r = client.post("/mcp", content=_rpc("notifications/initialized", None, None),
                    headers={**_HEADERS, "mcp-session-id": sid})
    assert r.status_code in (200, 202), r.text
    return sid


def test_the_response_stream_carries_pings_while_the_tool_runs(app_with_slow_tool):
    with TestClient(app_with_slow_tool) as client:
        sid = _open_session(client)
        with client.stream("POST", "/mcp", content=_rpc(
            "tools/call", {"name": "slow_probe", "arguments": {"seconds": 3.0}}, 2,
        ), headers={**_HEADERS, "mcp-session-id": sid}) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            lines = [ln for ln in r.iter_lines() if ln.strip()]

    pings = [i for i, ln in enumerate(lines) if ln.startswith(": ping")]
    results = [i for i, ln in enumerate(lines) if ln.startswith("data:")]
    assert results, lines
    result = json.loads(lines[results[-1]][len("data:"):])
    assert "probe done" in json.dumps(result)
    # Two or more keepalives, and every one of them BEFORE the result frame:
    # the stream is not silent while the tool runs.
    assert len(pings) >= 2, lines
    assert all(p < results[-1] for p in pings), lines


def test_the_tool_call_is_answered_in_sse_mode_with_the_default_ping_interval(app_with_slow_tool):
    """The proof above holds only in SSE response mode - ``json_response`` is
    decided by FastMCP settings (``FASTMCP_JSON_RESPONSE``) because the app
    omits the argument, and a JSON response has no stream to ping - and only
    while sse-starlette keeps its 15s default. Both are asserted at runtime:
    a source-string check would not see a settings flip."""
    with TestClient(app_with_slow_tool) as client:
        sid = _open_session(client)
        r = client.post("/mcp", content=_rpc(
            "tools/call", {"name": "slow_probe", "arguments": {"seconds": 0.0}}, 3,
        ), headers={**_HEADERS, "mcp-session-id": sid})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in r.headers.get("cache-control", "")


def test_sse_starlette_default_ping_interval_is_15s():
    """Runs WITHOUT the fixture, so the class attribute is the library's own.
    Production sets no override; this is the interval the proof relies on."""
    assert EventSourceResponse.DEFAULT_PING_INTERVAL == 15
