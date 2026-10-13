"""Session trail: one JSON event per step, redacted, joined by session id."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import sqlite3
from pathlib import Path

import pytest

from tinyassets import observability as obs

ROOT = Path(__file__).resolve().parents[1]


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


def test_a_header_never_names_or_joins_a_trail(captured):
    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = obs.RequestLogMiddleware(ok)
    theirs = "s_" + "ef" * 16
    mine = "s_" + "ab" * 16
    signed = obs.sign_session(mine).encode()
    for cookie in (b"ta_session=" + theirs.encode(), b"ta_session=" + signed):
        asyncio.run(_drive(app, _scope(
            "/app", headers=[(b"cookie", cookie), (b"x-session-id", theirs.encode())],
        )))
        event = _events(captured)[-1]
        assert event["session_id"] != theirs
        assert theirs not in json.dumps(event.get("links") or {})


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
    value = cookie.split(b";", 1)[0].split(b"=", 1)[1].decode()
    session, _dot, mac = value.partition(".")
    assert obs._SESSION_RE.fullmatch(session)
    assert obs._SESSION_SIG_RE.fullmatch(mac)
    assert value == obs.sign_session(session)
    assert _events(captured)[-1]["session_id"] == session
    second = asyncio.run(_drive(
        app, _scope("/", headers=[(b"cookie", b"ta_session=" + value.encode())]),
    ))
    assert _header(second, b"set-cookie") is None
    assert _events(captured)[-1]["session_id"] == session
    assert _events(captured)[-1]["links"]["cookie"] == session
    assert obs._session_id.get() is None
    assert obs._request_active.get() is False
    # A cookie naming another trail's id without its MAC, or with a tampered
    # MAC, names no trail: a fresh signed cookie is minted instead.
    theirs = "s_" + "ef" * 16
    tampered = value[:-1] + ("A" if value[-1] != "A" else "B")
    for forged in (theirs, tampered, theirs + "." + mac):
        response = asyncio.run(_drive(
            app, _scope("/", headers=[(b"cookie", b"ta_session=" + forged.encode())]),
        ))
        reissued = _header(response, b"set-cookie")
        assert reissued is not None
        fresh = reissued.split(b";", 1)[0].split(b"=", 1)[1].decode().partition(".")[0]
        assert fresh not in {session, theirs}
        event = _events(captured)[-1]
        assert event["session_id"] == fresh
        assert "links" not in event or "cookie" not in event["links"]
        assert theirs not in json.dumps(event)


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


def _report(
    body: dict | bytes,
    *,
    cookie: str = "",
    host: str = "203.0.113.5",
    headers: list | None = None,
    content_length: str | None = "auto",
    chunks: list[bytes] | None = None,
    signed: bool = True,
):
    """A Starlette-shaped request for the beacon: headers, scope, client, stream.

    ``cookie`` is a session id; it is sent server-signed unless ``signed`` is
    off, in which case the value goes on the wire exactly as given.
    """
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    scope_headers = list(headers or [])
    if cookie:
        value = obs.sign_session(cookie) if signed else cookie
        scope_headers.append((b"cookie", f"ta_session={value}".encode()))
    pieces = chunks if chunks is not None else [raw]

    class Request:
        scope = {"type": "http", "method": "POST", "path": "/app/client-error",
                 "headers": scope_headers}
        client = type("Client", (), {"host": host})()
        served = 0

        def __init__(self) -> None:
            self.headers = {}
            if content_length == "auto":
                self.headers["content-length"] = str(len(raw))
            elif content_length is not None:
                self.headers["content-length"] = content_length

        async def stream(self):
            for piece in pieces:
                Request.served += 1
                yield piece
            yield b""

    return Request()


def _post(request):
    return asyncio.run(obs.client_error(request))


def test_client_report_binds_the_session_to_the_cookie(captured):
    mine = "s_" + "cd" * 16
    theirs = "s_" + "ef" * 16
    ok = _post(_report({"kind": "page.load", "session_id": theirs, "path": "/pricing?x=1"},
                       cookie=mine))
    assert ok.status_code == 204
    page = _events(captured)[-1]
    assert page["event"] == "session.step"
    assert page["session_id"] == mine
    assert page["page"] == "/"
    assert theirs not in json.dumps(page)
    # No cookie and no middleware: nothing to bind to.
    assert _post(_report({"kind": "page.load", "session_id": theirs})).status_code == 400
    # No cookie, but the middleware minted one for this request.
    minted = "s_" + "ab" * 16
    token = obs._session_id.set(minted)
    try:
        assert _post(_report({"kind": "page.load", "session_id": theirs})).status_code == 204
        assert _events(captured)[-1]["session_id"] == minted
        # A header naming another trail is ignored: the report lands on the
        # session minted for this request, never on the one the header names.
        spoofed = _report({"kind": "page.load"}, headers=[(b"x-session-id", theirs.encode())])
        assert _post(spoofed).status_code == 204
        assert _events(captured)[-1]["session_id"] == minted
    finally:
        obs._session_id.reset(token)
    assert _post(_report({"kind": "nope"}, cookie=mine)).status_code == 400


def test_client_report_refuses_a_forged_cookie(captured):
    from tinyassets.onboarding import session_store

    theirs = "s_" + "ef" * 16
    good = obs.sign_session(theirs)
    tampered = good[:-1] + ("A" if good[-1] != "A" else "B")
    grafted = theirs + "." + obs.sign_session("s_" + "ab" * 16).partition(".")[2]
    original = session_store._key
    session_store._key = secrets.token_bytes(32)
    try:
        foreign = obs.sign_session(theirs)
    finally:
        session_store._key = original
    assert foreign != good
    forged = (theirs, tampered, grafted, foreign, theirs + ".", theirs + ".x")
    # Without the middleware there is nothing to bind a forged cookie to.
    for value in forged:
        assert _post(_report({"kind": "page.load"}, cookie=value, signed=False)).status_code == 400
    assert not any(theirs in line for line in captured)
    # Behind the middleware the report lands on the session minted for this
    # request, never on the trail the forged cookie names.
    minted = "s_" + "cd" * 16
    token = obs._session_id.set(minted)
    try:
        for value in forged:
            ok = _post(_report({"kind": "window.error", "name": "TypeError"},
                               cookie=value, signed=False))
            assert ok.status_code == 204
            assert _events(captured)[-1]["session_id"] == minted
    finally:
        obs._session_id.reset(token)
    assert not any(theirs in line for line in captured)
    # The genuine cookie still names its own trail.
    assert _post(_report({"kind": "page.load"}, cookie=good, signed=False)).status_code == 204
    assert _events(captured)[-1]["session_id"] == theirs
    with pytest.raises(ValueError):
        obs.sign_session("not-a-session")


def test_client_report_rate_limits_per_client_address(captured):
    for index in range(30):
        cookie = "s_" + f"{index:032x}"
        assert _post(_report({"kind": "page.load"}, cookie=cookie)).status_code == 204
    assert _post(_report({"kind": "page.load"}, cookie="s_" + "9" * 32)).status_code == 429
    elsewhere = _report({"kind": "page.load"}, cookie="s_" + "9" * 32, host="198.51.100.7")
    assert _post(elsewhere).status_code == 204


def test_client_report_caps_the_body_while_streaming(captured):
    cookie = "s_" + "cd" * 16
    huge = b'{"kind":"page.load"}' + b" " * 5000
    assert _post(_report(huge, cookie=cookie)).status_code == 413
    pieces = [b" " * 1000] * 10
    unsized = _report(huge, cookie=cookie, content_length=None, chunks=pieces)
    assert _post(unsized).status_code == 413
    assert type(unsized).served == 5
    lying = _report(huge, cookie=cookie, content_length="10", chunks=pieces)
    assert _post(lying).status_code == 413
    assert type(lying).served == 5
    assert _post(_report(huge, cookie=cookie, content_length="x")).status_code == 400


def test_client_report_keeps_type_and_frames_not_the_message(captured):
    cookie = "s_" + "cd" * 16
    stack = (
        "TypeError: Cookie: workos_session=abc123xyz ('4111111111111111', 'cus_123456789')\n"
        "    at render (https://tinyassets.io/_next/app.js:1:2)\n"
        "    at run (https://tinyassets.io/_next/app.js:3:4)\n"
        "    at load (https://tinyassets.io/_next/app.js?token=abc:5:6)"
    )
    ok = _post(_report({
        "kind": "window.error",
        "path": "/app",
        "name": "TypeError",
        "text": "user@example.com bearer " + "q" * 12,
        "frames": stack,
    }, cookie=cookie))
    assert ok.status_code == 204
    report = _events(captured)[-1]
    assert report["event"] == "client.error"
    assert report["error"]["type"] == "TypeError"
    assert "message" not in report["error"]
    assert report["error"]["stack"].startswith(
        "at render (https://tinyassets.io/_next/app.js:1:2)\n"
        "at run (https://tinyassets.io/_next/app.js:3:4)\n"
        "at load (https://tinyassets.io/_next/app.js"
    )
    rendered = json.dumps(report)
    for leaked in (
        "user@example.com", "qqqq", "abc123xyz", "4111111111111111", "cus_123456789", "token=abc",
    ):
        assert leaked not in rendered
    odd = _post(_report({"kind": "react", "name": "Cookie: workos_session=abc123xyz"},
                        cookie=cookie))
    assert odd.status_code == 204
    assert _events(captured)[-1]["error"] == {"type": "Error", "stack": ""}


def test_redaction_covers_cookies_cards_and_stripe_ids():
    cookie = obs.redact("Cookie: workos_session=abc123xyz")
    assert "abc123xyz" not in cookie
    assert cookie.startswith("Cookie: [redacted]")
    assert "abc123xyz" not in obs.redact("Set-Cookie: workos_session=abc123xyz; Path=/")
    assert "abc123xyz" not in obs.redact("workos_session=abc123xyz")
    pair = obs.redact("('4111111111111111', 'cus_123456789')")
    assert pair == "('[redacted]', '[redacted]')"
    assert "4111 1111 1111 1111" not in obs.redact("card 4111 1111 1111 1111 declined")
    assert "pi_3Nx" not in obs.redact("charge pi_3NxQ2bLkdIwHu7ix0Z5Q1a2b failed")
    assert "s_" + "a" * 32 not in obs.redact("ta_session=s_" + "a" * 32 + "; Path=/")
    assert "secret-value-here-1234" not in obs.redact("x_api_key=secret-value-here-1234")
    assert obs.redact("user@example.com") == "[redacted]"
    assert obs.redact("Bearer " + "t" * 20) == "Bearer [redacted]"
    assert "eyJ" not in obs.redact("eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10)
    assert obs.redact("trace-0001 HTTP500 at /app in_progress") == (
        "trace-0001 HTTP500 at /app in_progress"
    )


def test_admission_refuses_before_the_trail_reads_a_body(captured, monkeypatch):
    from tinyassets import origin_admission

    monkeypatch.setattr(origin_admission, "cached_process_is_cloud_admitted", lambda: False)

    async def inner(scope, receive, send):
        raise AssertionError("an unadmitted request reached the app")

    app = origin_admission.PlatformOriginAdmission(obs.RequestLogMiddleware(inner))
    scope = _scope("/mcp", method="POST", headers=[
        (b"content-length", b"20"), (b"content-type", b"application/json"),
    ])
    sent: list[dict] = []

    async def receive():
        raise AssertionError("the body was read before admission")

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == origin_admission.PLATFORM_NOT_CLOUD_STATUS
    assert captured == []
    source = (ROOT / "tinyassets" / "universe_server.py").read_text(encoding="utf-8")
    assert source.index("app = RequestLogMiddleware(app)") < source.index(
        "app = PlatformOriginAdmission(app)"
    )


def test_db_trace_counts_fast_statements_on_the_request(captured):
    obs.install_db_trace()

    def run_queries(count: int) -> None:
        with sqlite3.connect(":memory:") as connection:
            for _ in range(count):
                assert connection.execute("select 1").fetchone() == (1,)

    async def query(scope, receive, send):
        run_queries(10)
        # Sync tools run in a worker thread with a copied context; their
        # statements must still reach the request's tally.
        await asyncio.to_thread(run_queries, 10)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    try:
        app = obs.RequestLogMiddleware(query)
        asyncio.run(_drive(app, _scope("/app")))
        first = _events(captured)
        assert len(first) == 1
        assert first[0]["event"] == "session.step"
        assert first[0]["http"]["db_calls"] == 20
        assert first[0]["http"]["db_ms"] >= 0
        assert obs._db_totals.get() is None

        async def no_query(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        captured.clear()
        asyncio.run(_drive(obs.RequestLogMiddleware(no_query), _scope("/app")))
        assert "db_calls" not in _events(captured)[0]["http"]
    finally:
        obs.uninstall_db_trace()


def test_db_trace_keeps_failed_and_slow_statements(captured, monkeypatch):
    obs.install_db_trace()

    async def query(scope, receive, send):
        with sqlite3.connect(":memory:") as connection:
            connection.execute("select 1")
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("select missing_column")
            with monkeypatch.context() as patch:
                ticks = iter((1.0, 1.3))
                patch.setattr(obs.time, "perf_counter", lambda: next(ticks))
                connection.execute("select 2")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    try:
        asyncio.run(_drive(obs.RequestLogMiddleware(query), _scope("/app")))
    finally:
        obs.uninstall_db_trace()
    events = _events(captured)
    assert [event["event"] for event in events] == [
        "dependency.call", "dependency.call", "session.step",
    ]
    failed, slow, step = events
    assert failed["dependency"]["operation"] == "SELECT"
    assert failed["dependency"]["error"] == "OperationalError"
    assert failed["dependency"]["target"].startswith("sqlite:")
    assert slow["dependency"]["operation"] == "SELECT"
    assert slow["dependency"]["status"] == 1
    assert slow["dependency"]["duration_ms"] >= obs.DB_SLOW_MS
    assert step["http"]["db_calls"] == 1
    assert "missing_column" not in json.dumps(events)


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


def test_db_trace_returns_the_cursor_and_keeps_the_error(captured):
    import sqlite3

    obs.install_db_trace()
    try:
        conn = sqlite3.connect(":memory:")
        assert conn.execute("select 1").fetchone() == (1,)
        conn.execute("create table t (x)")
        assert conn.executemany("insert into t values (?)", [(1,), (2,)]).rowcount == 2
        try:
            conn.execute("select nope")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("a failing statement must still raise")
        conn.close()
    finally:
        obs.uninstall_db_trace()
