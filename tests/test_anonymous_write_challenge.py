"""Every request to the MCP endpoint without a valid bearer draws the 401
challenge before dispatch. There is no anonymous principal (founder,
2026-09-02): not for writes, not for reads, not for ``initialize``.

Before this file was rewritten it proved the opposite for reads: an anonymous
``read_graph`` passed to the app with its body intact, and a pre-dispatch
classifier decided which ``tools/call`` shapes to challenge. That classifier,
its body cap and the anonymous session are gone; what remains is one rule
(no bearer -> 401 on ``/mcp``) and one service principal (the canary bearer,
admitted for an exact probe allowlist and refused with 403 for anything
else).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

import pytest

from tinyassets.auth.middleware import (
    AuthContextMiddleware,
    auth_middleware,
    current_bearer_present,
    current_identity,
    current_identity_or_none,
    set_provider,
)
from tinyassets.auth.provider import CANARY, AuthProvider, DevAuthProvider, Identity
from tinyassets.auth.wiki_canary import current_wiki_canary_authority

_SUBJECT = Identity(
    user_id="founder-1",
    username="founder-1",
    capabilities=["read", "write", "costly"],
)
_CANARY_TOKEN = "c" * 32


def _canary_write_rpc(filename: str = "uptime-probe", **extra: Any) -> dict:
    return _rpc(
        "tools/call",
        "write_page",
        filename=filename,
        category="notes",
        content="roundtrip",
        dry_run=False,
        **extra,
    )


class _ResolveAlwaysProvider(AuthProvider):
    """WorkOS-shaped provider: a bearer resolves or it does not."""

    def resolve_token(self, token: str) -> Identity | None:
        return _SUBJECT if token == "valid" else None

    def is_auth_required(self) -> bool:
        return False

    def resolve_always_writes(self) -> bool:
        return True

    def register_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {"client_id": "t", **metadata}

    def create_authorization(self, *a: Any, **k: Any) -> str:
        return "c"

    def exchange_code(self, *a: Any, **k: Any) -> dict[str, Any] | None:
        return None


@pytest.fixture(autouse=True)
def _reset_auth():
    set_provider(_ResolveAlwaysProvider())
    auth_middleware(None)
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _rpc(method: str, name: str | None = None, **arguments: Any) -> dict:
    req: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if name is not None:
        req["params"] = {"name": name, "arguments": arguments}
    else:
        req["params"] = {}
    return req


def _drive(
    body: Any,
    *,
    token: str | None = None,
    path: str = "/mcp",
    method: str = "POST",
    chunks: list[bytes] | None = None,
    observe: list[Any] | None = None,
) -> tuple[list[dict], bool, bytes]:
    """Run one request through the middleware.

    Returns (sent messages, app_called, body the inner app read). ``observe``
    collects the identity the inner app saw and whether a bearer was present.
    """
    called = {"hit": False}
    seen = {"body": b""}

    async def _app(scope, receive, send):  # noqa: ANN001, ANN202
        called["hit"] = True
        if observe is not None:
            observe.append(current_identity_or_none())
            observe.append(current_bearer_present())
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            seen["body"] += message.get("body", b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent: list[dict] = []

    async def _send(msg):  # noqa: ANN001, ANN202
        sent.append(msg)

    if chunks is None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        chunks = [raw]
    pending = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]

    async def _receive():  # noqa: ANN202
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("latin1")))
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    asyncio.run(AuthContextMiddleware(_app)(scope, _receive, _send))
    return sent, called["hit"], seen["body"]


def _status(sent: list[dict]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _www_authenticate(sent: list[dict]) -> str:
    start = next(m for m in sent if m["type"] == "http.response.start")
    for k, v in start["headers"]:
        if k == b"www-authenticate":
            return v.decode("latin1")
    return ""


def _body(sent: list[dict]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def _linking_challenge(sent: list[dict]) -> str:
    payload = json.loads(_body(sent))
    return payload["result"]["_meta"]["mcp/www_authenticate"][0]


# --------------------------------------------------------------------------
# no bearer: cached tool calls get a linking result; setup calls get transport 401
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (_rpc("tools/call", "write_graph", target="goal"), 200),
        (_rpc("tools/call", "read_graph", target="status"), 200),
        (_rpc("tools/call", "get_status"), 200),
        (_rpc("initialize"), 401),
        (_rpc("tools/list"), 401),
        ([_rpc("tools/call", "read_graph"), _rpc("tools/call", "get_status")], 200),
    ],
    ids=["write", "read", "status", "initialize", "tools_list", "read_batch"],
)
def test_every_anonymous_request_is_challenged(body, expected_status):
    sent, app_called, seen = _drive(body)
    assert not app_called
    assert _status(sent) == expected_status
    assert seen == b""                          # nothing was read on nobody's behalf
    if expected_status == 200:
        payload = json.loads(_body(sent))
        items = payload if isinstance(payload, list) else [payload]
        assert all(
            "mcp/www_authenticate" in item["result"]["_meta"]
            and item["result"]["isError"] is True
            for item in items
        )
        return
    wa = _www_authenticate(sent)
    assert wa.startswith("Bearer ")
    assert "resource_metadata=" in wa
    assert "invalid_token" not in wa            # missing != invalid (RFC 6750)
    assert json.loads(_body(sent)) == {"error": "authentication_required"}


def test_chunked_anonymous_body_gets_linking_challenge_without_dispatch():
    raw = json.dumps(_rpc("tools/call", "read_graph", target="status")).encode()
    mid = len(raw) // 2
    sent, app_called, seen = _drive(raw, chunks=[raw[:mid], raw[mid:]])
    assert not app_called
    assert _status(sent) == 200
    assert seen == b""


def test_malformed_json_is_challenged_when_anonymous():
    # There is no body classification for an anonymous request any more: the
    # bearer decides, the body is irrelevant.
    sent, app_called, _ = _drive(b"{not json", chunks=[b"{not json"])
    assert not app_called
    assert _status(sent) == 401


def test_get_stream_and_delete_are_challenged_anonymously():
    for method in ("GET", "DELETE"):
        sent, app_called, _ = _drive(b"", method=method, chunks=[b""])
        assert not app_called, f"{method} must be challenged"
        assert _status(sent) == 401


def test_invalid_bearer_is_challenged_as_invalid():
    sent, app_called, _ = _drive(_rpc("tools/call", "read_graph"), token="bad")
    assert not app_called
    assert _status(sent) == 200
    assert 'error="invalid_token"' in _linking_challenge(sent)


def test_dev_provider_challenges_a_missing_bearer_too():
    set_provider(DevAuthProvider())
    sent, app_called, _ = _drive(_rpc("tools/call", "write_graph", target="goal"))
    assert not app_called
    assert _status(sent) == 200


def test_dev_provider_names_the_local_operator_for_any_bearer():
    set_provider(DevAuthProvider(user_id="operator-x"))
    observed: list[Any] = []
    sent, app_called, _ = _drive(
        _rpc("tools/call", "write_graph", target="goal"), token="anything", observe=observed,
    )
    assert app_called
    assert _status(sent) == 200
    assert observed[0].user_id == "operator-x"


def test_non_mcp_path_is_not_the_middleware_s_business():
    sent, app_called, _ = _drive(_rpc("tools/call", "write_graph"), path="/not-mcp")
    assert app_called
    assert _status(sent) == 200


def test_challenge_header_matches_connect_time_challenge(monkeypatch):
    monkeypatch.setenv("WORKOS_MCP_RESOURCE", "https://tinyassets.io/mcp")
    sent, _, _ = _drive(_rpc("tools/call", "write_page", page="x"))
    assert _linking_challenge(sent).startswith(
        'Bearer resource_metadata='
        '"https://tinyassets.io/mcp/.well-known/oauth-protected-resource"'
    )


# --------------------------------------------------------------------------
# a valid bearer: the stream reaches the app untouched
# --------------------------------------------------------------------------


def test_authenticated_write_call_passes_through():
    sent, app_called, _ = _drive(
        _rpc("tools/call", "write_graph", target="goal"), token="valid",
    )
    assert app_called
    assert _status(sent) == 200


def test_authenticated_read_call_passes_with_body_intact():
    body = _rpc("tools/call", "read_graph", target="status")
    raw = json.dumps(body).encode()
    observed: list[Any] = []
    sent, app_called, seen = _drive(body, token="valid", observe=observed)
    assert app_called
    assert _status(sent) == 200
    assert seen == raw
    assert observed[0].user_id == "founder-1"
    assert observed[1] is True


def test_large_authenticated_body_is_never_buffered_or_rejected():
    from tinyassets.auth.middleware import _MAX_PROBE_BODY_BYTES

    raw = b"z" * (_MAX_PROBE_BODY_BYTES + 1)
    sent, app_called, seen = _drive(raw, token="valid", chunks=[raw])
    assert app_called
    assert _status(sent) == 200
    assert len(seen) == len(raw)                # stream reached the app intact


# --------------------------------------------------------------------------
# the canary service principal: exact probes only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        _rpc("initialize"),
        _rpc("notifications/initialized"),
        _rpc("tools/list"),
        _rpc("tools/call", "get_status"),
        _rpc("tools/call", "read_graph", target="status"),
        _rpc("tools/call", "read_page", page="uptime-probe"),
        _canary_write_rpc(),
        [_rpc("initialize"), _rpc("tools/call", "get_status")],
        [_canary_write_rpc(), _rpc("tools/call", "read_page", page="uptime-probe")],
    ],
    ids=[
        "initialize", "initialized", "tools_list", "get_status", "read_status",
        "read_probe_page", "write_probe_page", "probe_batch", "roundtrip_batch",
    ],
)
def test_canary_bearer_is_admitted_for_exactly_the_probe_shapes(monkeypatch, body):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    raw = json.dumps(body).encode()
    observed: list[Any] = []
    sent, app_called, seen = _drive(body, token=_CANARY_TOKEN, observe=observed)
    assert app_called
    assert _status(sent) == 200
    assert seen == raw                          # buffered, checked, replayed verbatim
    assert observed[0] is CANARY
    assert observed[0].user_id == "canary"
    assert observed[0].capabilities == []
    assert observed[1] is True


@pytest.mark.parametrize(
    "body",
    [
        _rpc("tools/call", "write_graph", target="goal"),
        _rpc("tools/call", "converse", message="hi"),
        _rpc("tools/call", "read_graph", target="graph"),
        _rpc("tools/call", "read_graph", target="graphs", limit=5),
        _rpc("tools/call", "read_graph"),
        _rpc("tools/call", "get_status", universe_id="u-1"),
        _rpc("tools/call", "read_page", page="not-the-probe"),
        _canary_write_rpc(filename="uptime-probe-neighbor"),
        _canary_write_rpc(universe_id="another-scope"),
        _rpc("resources/list"),
        [_rpc("tools/call", "get_status"), _rpc("tools/call", "write_graph", target="goal")],
        [],
    ],
    ids=[
        "write", "converse", "read_graph_graph", "read_graphs", "read_graph_default",
        "status_with_args", "other_page", "neighbor_page", "extra_arg", "resources",
        "batch_with_one_bad", "empty_batch",
    ],
)
def test_canary_bearer_is_refused_outside_its_allowlist(monkeypatch, body):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    sent, app_called, _ = _drive(body, token=_CANARY_TOKEN)
    assert not app_called
    assert _status(sent) == 403                 # refused, never downgraded to nobody
    assert "error" in json.loads(_body(sent))
    assert current_identity_or_none() is None


def test_canary_bearer_is_refused_off_the_mcp_endpoint_and_off_post(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    for path, method in (("/app/settings", "POST"), ("/mcp", "GET"), ("/mcp", "DELETE")):
        sent, app_called, _ = _drive(
            _rpc("tools/call", "get_status"), token=_CANARY_TOKEN, path=path, method=method,
        )
        assert not app_called, (path, method)
        assert _status(sent) == 403, (path, method)


def test_malformed_json_under_the_canary_bearer_is_forbidden(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    sent, app_called, _ = _drive(b"{not json", token=_CANARY_TOKEN, chunks=[b"{not json"])
    assert not app_called
    assert _status(sent) == 403


def test_oversized_canary_body_answers_413(monkeypatch):
    from tinyassets.auth.middleware import _MAX_PROBE_BODY_BYTES

    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    chunk = b"y" * (_MAX_PROBE_BODY_BYTES // 2 + 1)
    sent, app_called, _ = _drive(b"", token=_CANARY_TOKEN, chunks=[chunk, chunk, chunk])
    assert not app_called
    assert _status(sent) == 413


def test_canary_token_uses_constant_time_compare(monkeypatch):
    calls: list[tuple[bytes, bytes]] = []

    def _compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    monkeypatch.setattr(secrets, "compare_digest", _compare)

    sent, app_called, _ = _drive(_canary_write_rpc(), token=_CANARY_TOKEN)

    assert app_called
    assert _status(sent) == 200
    assert calls == [(_CANARY_TOKEN.encode(), _CANARY_TOKEN.encode())]


@pytest.mark.parametrize(
    ("configured", "presented"),
    [
        (None, _CANARY_TOKEN),
        ("short", "short"),
        (_CANARY_TOKEN, "x" * 32),
    ],
    ids=["absent", "short", "mismatched"],
)
def test_canary_token_is_off_when_absent_short_or_mismatched(monkeypatch, configured, presented):
    if configured is None:
        monkeypatch.delenv("TINYASSETS_WIKI_CANARY_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", configured)

    sent, app_called, _ = _drive(_canary_write_rpc(), token=presented)

    assert not app_called
    assert _status(sent) == 200                 # linking result for an unknown bearer
    assert "invalid_token" in _linking_challenge(sent)


def test_canary_write_authority_is_set_only_for_the_exact_write(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    flags: list[bool] = []

    async def _app(scope, receive, send):  # noqa: ANN001, ANN202
        await receive()
        flags.append(current_wiki_canary_authority())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    for body in (_canary_write_rpc(), _rpc("tools/call", "get_status")):
        raw = json.dumps(body).encode()
        pending = [{"type": "http.request", "body": raw, "more_body": False}]

        async def _receive():  # noqa: ANN202
            return pending.pop(0) if pending else {"type": "http.disconnect"}

        async def _send(message):  # noqa: ANN001, ANN202
            pass

        scope = {
            "type": "http", "method": "POST", "path": "/mcp",
            "headers": [(b"authorization", f"Bearer {_CANARY_TOKEN}".encode())],
        }
        asyncio.run(AuthContextMiddleware(_app)(scope, _receive, _send))
    assert flags == [True, False]
    assert current_wiki_canary_authority() is False


def test_canary_bearer_writes_exact_reserved_draft(monkeypatch, tmp_path):
    from tinyassets import universe_server

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    tool_results: list[dict[str, Any]] = []

    async def _app(scope, receive, send):  # noqa: ANN001, ANN202
        message = await receive()
        request = json.loads(message["body"])
        arguments = request["params"]["arguments"]
        tool_results.append(json.loads(universe_server.write_page(**arguments)))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    body = json.dumps(_canary_write_rpc()).encode()
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def _receive():  # noqa: ANN202
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent: list[dict] = []

    async def _send(message):  # noqa: ANN001, ANN202
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", f"Bearer {_CANARY_TOKEN}".encode())],
    }
    asyncio.run(AuthContextMiddleware(_app)(scope, _receive, _send))

    assert _status(sent) == 200
    assert tool_results == [{
        "path": "drafts/notes/uptime-probe.md",
        "status": "drafted",
        "note": "Drafted reserved uptime canary page.",
    }]
    written_files = [
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*.md")
        if path.read_text(encoding="utf-8") == "roundtrip"
    ]
    assert written_files == [".wiki/drafts/notes/uptime-probe.md"]


def test_canary_authority_is_isolated_between_concurrent_requests(monkeypatch):
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", _CANARY_TOKEN)
    observed: dict[str, Any] = {}

    async def _run() -> None:
        canary_entered = asyncio.Event()
        release_canary = asyncio.Event()

        async def _canary_app(scope, receive, send):  # noqa: ANN001, ANN202
            await receive()
            observed["canary_active"] = current_wiki_canary_authority()
            observed["canary_identity"] = current_identity()
            canary_entered.set()
            await release_canary.wait()
            observed["canary_still_active"] = current_wiki_canary_authority()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def _user_app(scope, receive, send):  # noqa: ANN001, ANN202
            await receive()
            await canary_entered.wait()
            observed["user_active"] = current_wiki_canary_authority()
            observed["user_identity"] = current_identity()
            release_canary.set()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def _request(app, body, token):  # noqa: ANN001, ANN202
            raw = json.dumps(body).encode()
            pending = [{"type": "http.request", "body": raw, "more_body": False}]

            async def _receive():  # noqa: ANN202
                return pending.pop(0) if pending else {"type": "http.disconnect"}

            sent: list[dict] = []

            async def _send(message):  # noqa: ANN001, ANN202
                sent.append(message)

            scope = {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"authorization", f"Bearer {token}".encode())],
            }
            await AuthContextMiddleware(app)(scope, _receive, _send)
            return _status(sent)

        statuses = await asyncio.gather(
            _request(_canary_app, _canary_write_rpc(), _CANARY_TOKEN),
            _request(_user_app, _rpc("tools/call", "read_page", page="uptime-probe"), "valid"),
        )
        observed["statuses"] = statuses

    asyncio.run(_run())

    assert observed["statuses"] == [200, 200]
    assert observed["canary_active"] is True
    assert observed["canary_still_active"] is True
    assert observed["canary_identity"] is CANARY
    assert observed["user_active"] is False
    assert observed["user_identity"].user_id == "founder-1"
    assert current_wiki_canary_authority() is False
    assert current_identity_or_none() is None
