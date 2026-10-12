"""Session trail: one JSON event per step, redacted, joined by session id."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3

import pytest

from tinyassets import observability as obs


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def captured():
    obs.configure()
    handler = _Capture()
    obs._log.handlers[:] = [handler]
    obs._log.propagate = False
    obs._report_buckets.clear()
    obs.reset_commit_cache()
    yield handler.lines
    obs._report_buckets.clear()
    obs.reset_commit_cache()


def _events(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines]


def _scope(path: str, method: str = "GET", headers: list | None = None, query: bytes = b"") -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "query_string": query,
        "scheme": "https",
    }


async def _drive(app, scope: dict, body: bytes = b"") -> list[dict]:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _header(sent: list[dict], name: bytes) -> bytes | None:
    for message in sent:
        if message.get("type") != "http.response.start":
            continue
        for key, value in message.get("headers") or []:
            if key.lower() == name:
                return value
    return None


def test_trace_is_echoed_or_minted(captured):
    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = obs.RequestLogMiddleware(ok)
    sent = asyncio.run(_drive(app, _scope("/app", headers=[(b"x-trace-id", b"trace-abc")])))
    assert _header(sent, b"x-trace-id") == b"trace-abc"
    event = _events(captured)[-1]
    assert event["trace_id"] == "trace-abc"
    assert event["schema"] == obs.SCHEMA
    assert event["http"]["route"] == "/app"
    assert event["http"]["status"] == 204

    sent = asyncio.run(_drive(app, _scope("/app", headers=[(b"x-trace-id", b"short")])))
    minted = _header(sent, b"x-trace-id")
    assert minted is not None and minted != b"short"
    assert obs._TRACE_RE.fullmatch(minted.decode())


def test_session_cookie_is_minted_once_and_then_reused(captured):
    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = obs.RequestLogMiddleware(ok)
    first = asyncio.run(_drive(app, _scope("/")))
    cookie = _header(first, b"set-cookie")
    assert cookie is not None
    assert cookie.startswith(b"ta_session=s_")
    assert b"HttpOnly" not in cookie
    session = cookie.split(b";", 1)[0].split(b"=", 1)[1]
    second = asyncio.run(_drive(
        app, _scope("/", headers=[(b"cookie", b"ta_session=" + session)]),
    ))
    assert _header(second, b"set-cookie") is None
    assert _events(captured)[-1]["session_id"] == session.decode()
    assert obs._session_id.get() is None
    assert obs._request_active.get() is False


def test_redaction_strips_secrets_and_keeps_the_commit(captured, monkeypatch):
    monkeypatch.setenv("TINYASSETS_BUILD_SHA", "a" * 40)
    obs.reset_commit_cache()
    text = obs.redact(
        "bearer supersecret user@example.com "
        "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxIn0.signature1 "
        "sk_live_abcdefghij https://example.com/a?code=1 "
        + "a" * 40
    )
    assert "supersecret" not in text
    assert "user@example.com" not in text
    assert "eyJ" not in text
    assert "sk_live_" not in text
    assert "code=1" not in text
    assert "a" * 40 in text
    assert obs.route_for("/mcp/hooks/not-a-slug") == "/mcp/hooks/{token}"
    assert "/hooks/{token}" in obs.route_for("/mcp/hooks/deadbeef")


def test_starlette_records_the_stack_before_the_error_propagates(captured):
    from starlette.applications import Starlette
    from starlette.routing import Route

    async def boom(request):
        raise RuntimeError("boom-marker")

    app = Starlette(routes=[Route("/x", boom)])
    app.add_middleware(obs.StashExceptionMiddleware)
    wrapped = obs.RequestLogMiddleware(app)
    scope = _scope("/x")
    scope.update(client=("127.0.0.1", 1), server=("test", 80), http_version="1.1")
    with pytest.raises(RuntimeError, match="boom-marker"):
        asyncio.run(_drive(wrapped, scope))
    event = _events(captured)[-1]
    assert event["http"]["status"] == 500
    assert event["error"]["type"] == "RuntimeError"
    assert "boom-marker" in event["error"]["stack"]


def test_error_stack_is_stashed_and_redacted(captured):
    class Boom:
        async def __call__(self, scope, receive, send):
            raise RuntimeError("bearer " + "z" * 20)

    class Catch:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            try:
                await self.app(scope, receive, send)
            except Exception:
                await send({"type": "http.response.start", "status": 500, "headers": []})
                await send({"type": "http.response.body", "body": b""})

    app = obs.RequestLogMiddleware(Catch(obs.StashExceptionMiddleware(Boom())))
    asyncio.run(_drive(app, _scope("/mcp")))
    event = _events(captured)[-1]
    assert event["http"]["status"] == 500
    assert event["error"]["type"] == "RuntimeError"
    assert "RuntimeError" in event["error"]["stack"]
    assert "zzzz" not in event["error"]["stack"]
    assert event["commit"]


def test_rejected_bearer_records_age_and_issuer_not_the_token(captured):
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": "https://auth.example.test",
        "iat": 1_700_000_000,
        "exp": 1_800_000_000,
        "sub": "user@example.com",
    }).encode()).rstrip(b"=").decode()
    token = f"eyJhbGciOiJub25lIn0.{payload}.signature"

    async def denied(scope, receive, send):
        obs.observe_bearer(token)
        obs.observe_identity(None)
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = obs.RequestLogMiddleware(denied)
    headers = [(b"authorization", f"Bearer {token}".encode())]
    asyncio.run(_drive(app, _scope("/mcp", "POST", headers)))
    event = _events(captured)[-1]
    assert event["http"]["status"] == 401
    assert event["step"] == "mcp.other"
    assert event["context"]["token_iss"] == "https://auth.example.test"
    assert event["context"]["signed_in"] is False
    assert "error" not in event
    rendered = json.dumps(event)
    assert token not in rendered
    assert "user@example.com" not in rendered


def test_startup_and_checkout_link(captured):
    obs.emit_startup()
    token = obs._request_active.set(True)
    session = obs._session_id.set("s_" + "ab" * 16)
    try:
        obs.emit_checkout_attempt("attempt-secret-value")
    finally:
        obs._session_id.reset(session)
        obs._request_active.reset(token)
    events = _events(captured)
    assert events[0]["event"] == "process.startup"
    assert events[0]["session_id"] is None
    link = events[1]
    assert link["event"] == "session.link"
    assert "attempt-secret-value" not in json.dumps(link)
    assert link["links"]["checkout"].startswith("c_")
    assert link["links"]["checkout"] == obs.link_token("c_", "attempt-secret-value")


def test_client_report_validates_and_rate_limits(captured):
    session = "s_" + "cd" * 16

    async def post(body: dict | bytes):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()

        class Request:
            headers = {"content-length": str(len(raw))}

            async def body(self):
                return raw

        return await obs.client_error(Request())

    denied = asyncio.run(post({"kind": "nope", "session_id": session}))
    assert denied.status_code == 400
    ok = asyncio.run(post({
        "kind": "page.load", "session_id": session, "path": "/pricing?x=1",
    }))
    assert ok.status_code == 204
    page = _events(captured)[-1]
    assert page["event"] == "session.step"
    assert page["session_id"] == session
    assert page["page"] == "/"
    leaked = asyncio.run(post({
        "kind": "window.error",
        "session_id": session,
        "path": "/app",
        "name": "Error",
        "text": "user@example.com bearer " + "q" * 12,
        "frames": "sk_test_" + "m" * 12,
    }))
    assert leaked.status_code == 204
    report = _events(captured)[-1]
    rendered = json.dumps(report)
    assert "user@example.com" not in rendered
    assert "qqqq" not in rendered
    assert "sk_test_" not in rendered
    for _ in range(28):
        assert asyncio.run(post({"kind": "page.load", "session_id": session})).status_code == 204
    assert asyncio.run(post({"kind": "page.load", "session_id": session})).status_code == 429
    huge = b'{"kind":"page.load"}' + b" " * 5000
    assert asyncio.run(post(huge)).status_code == 413


def test_db_trace_logs_the_verb_during_a_request(captured):
    obs.install_db_trace()
    active = obs._request_active.set(True)
    session = obs._session_id.set("s_" + "ef" * 16)
    try:
        connection = sqlite3.connect(":memory:")
        row = connection.execute("select 1").fetchone()
        assert row == (1,)
    finally:
        obs._session_id.reset(session)
        obs._request_active.reset(active)
        obs.uninstall_db_trace()
    calls = [event for event in _events(captured) if event["event"] == "dependency.call"]
    assert len(calls) == 1
    assert calls[0]["dependency"]["operation"] == "SELECT"
    assert calls[0]["dependency"]["target"].startswith("sqlite:")
    assert "select 1" not in json.dumps(calls[0])


def test_tools_list_step_and_client(captured):
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"clientInfo": {"name": "Claude"}},
    }).encode()

    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"user-agent", b"Claude-User"),
    ]
    app = obs.RequestLogMiddleware(ok)
    asyncio.run(_drive(app, _scope("/mcp", "POST", headers), body))
    event = _events(captured)[-1]
    assert event["step"] == "mcp.tools_list"
    assert event["context"]["client"] == "claude"
