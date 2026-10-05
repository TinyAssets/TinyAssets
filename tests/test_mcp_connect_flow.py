"""An unknown OAuth MCP server through the owner sheet, real broker and ta."""
# ruff: noqa: F811
import asyncio
import json
import socket
import sys
from contextlib import closing
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_broker_server import broker  # noqa: F401
from tests.test_generic_oauth_connections import (
    CHALLENGE,
    OTHER,
    OWNER,
    TASKS_ASK,
    UID,
    VERIFIER,
    _as,
    _ask,
    app,  # noqa: F401
    provider,  # noqa: F401
    universes,  # noqa: F401
)
from tests.test_mcp_oauth import ENDPOINT, _post, mcp_provider  # noqa: F401
from tests.test_mcp_remote import TOOLS
from tinyassets import bound_requests, request_continuations, turn_interrupt
from tinyassets.mcp_attachment import metadata
from tinyassets.mcp_runtime import binding
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.ta_capabilities import Capabilities, ExecutionContext

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="Real Unix broker; runs-in=Linux oracle",
)


@pytest.fixture
def connected_broker(broker, app, monkeypatch, mcp_provider):
    from tinyassets.broker import supervisor
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.onboarding import session_store

    ConnectionLedger(app / "outbound.db")
    dispatchers = _Dispatchers(app, allow_test_fixtures=False)
    broker.server._ledger_for = dispatchers.ledger_for
    broker.server._dispatch_for = dispatchers.dispatch_for
    supervisor_stub = SimpleNamespace(socket_path=broker.path,
        fence=lambda: (broker.state["generation"], broker.state["token"]),
        verify_broker=lambda _: None)
    monkeypatch.setenv("TINYASSETS_CREDENTIAL_BROKER", "process")
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: supervisor_stub)
    monkeypatch.setattr(session_store, "_key", b"s" * 32)
    original = mcp_provider.route
    calls = []

    def route(method, host, path, headers, body):
        if path != "/mcp":
            return original(method, host, path, headers, body)
        token = headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in mcp_provider.access:
            return 401, {}
        doc = json.loads(body)
        calls.append(doc)
        name = doc["method"]
        if name.startswith("notifications/"):
            return 202, {}
        result = ({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
                  if name == "initialize" else
                  {"tools": TOOLS[:1], "nextCursor": "page2"}
                  if name == "tools/list" and not doc["params"] else
                  {"tools": TOOLS[1:]} if name == "tools/list" else
                  {"content": [{"type": "text", "text": "connected result"}]})
        reply = {"jsonrpc": "2.0", "id": doc["id"], "result": result}
        return 200, (b"data: " + json.dumps(reply).encode() + b"\n\n")

    monkeypatch.setattr(mcp_provider, "route", route)
    return calls


def connect(mcp_provider):
    with turn_interrupt.interactive_turn(OWNER, UID):
        asked = _ask({**TASKS_ASK, "mcp_url": ENDPOINT})
    assert asked.get("primary") == "sign_in", asked
    assert asked["server_continuation"]
    return sign_in(mcp_provider, asked["request_id"])


def sign_in(mcp_provider, request_id, expected=200):
    begun = _post("oauth_begin", {"request_id": request_id,
                                 "code_challenge": CHALLENGE})
    assert begun.status_code == 200, begun.text
    back = urlsplit(mcp_provider.authorize(begun.json()["authorize_url"]))
    code = parse_qs(back.query)["code"][0]
    done = _post("oauth_exchange", {"flow": begun.json()["flow"], "code": code,
                                    "code_verifier": VERIFIER})
    assert done.status_code == expected, done.text
    if expected != 200:
        return done.json()
    assert done.json()["mcp"]["state"] == "active"
    return done.json()


def test_paste_sign_in_catalog_call_resume_revoke_and_cross_owner(
    connected_broker, app, mcp_provider, monkeypatch,
):
    with _as(OWNER):
        done = connect(mcp_provider)
        home = app / UID
        backend = Capabilities(home, ExecutionContext(UID, OWNER, "main"), [], None, lambda: None)
        catalog = asyncio.run(backend.dispatch({"op": "catalog"}))
        tool_name = f"mcp:{done['connection_id']}:read"
        assert tool_name in {item["name"] for item in catalog["capabilities"]}
        from tinyassets import ta_cli

        monkeypatch.setattr(ta_cli, "remote",
                            lambda message: asyncio.run(backend.dispatch(message)))
        assert tool_name in {item["name"] for item in ta_cli.main(["search", "read"])}
        result = {"result": ta_cli.main([tool_name, "--json", "{}"])}
        assert "response" in result["result"], result
        assert not any(token in json.dumps(result) + json.dumps(catalog)
                       for token in mcp_provider.access)
        assert result["result"]["response"]["content"][0]["text"] == "connected result"
        assert len([c for c in connected_broker if c["method"] == "tools/call"]) == 1

        with closing(bound_requests.connect(home)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM activity_events "
                                "WHERE wake_required=1").fetchone()[0] == 1
        assert request_continuations.recover(home, run=lambda *_: {"reply": "resumed"}) == 1
        assert request_continuations.recover(
            home, run=lambda *_: pytest.fail("duplicate wake")) == 0
        current = binding(home, OWNER, done["grant_id"], done["connection_id"])
    with _as(OTHER):
        foreign = Capabilities(app / UID, ExecutionContext(UID, OTHER, "main"), [], None,
                               lambda: None)
        assert asyncio.run(foreign.dispatch({"op": "call", "name": tool_name,
                                            "arguments": {}}))["error"] == "unknown capability"
    with _as(OWNER):
        from dataclasses import asdict

        old = asdict(current.attachment)
        metadata(app, principal=OWNER, command_center=UID, grant_id=current.grant_id,
                 connection_id=current.connection_id, incarnation=current.incarnation,
                 expected=old, value={**old, "revision": old["revision"] + 1, "state": "revoked"})
        assert asyncio.run(backend.dispatch({"op": "call", "name": tool_name,
                                            "arguments": {}}))["error"] == "unknown capability"
        assert len([c for c in connected_broker if c["method"] == "tools/call"]) == 1


def test_destructive_tool_uses_protected_sheet_and_cannot_replay(
    connected_broker, app, mcp_provider,
):
    from tests.owner_answer import session_cookie
    from tests.test_inline_approvals import decision
    from tinyassets import agent_rules
    from tinyassets.onboarding import owner_sessions

    with _as(OWNER):
        done = connect(mcp_provider)
        home = app / UID
        agent_rules.set_rule(home, "app.write", agent_rules.DO, connection="unrelated-service")
        backend = Capabilities(home, ExecutionContext(UID, OWNER, "main"), [], None, lambda: None)
        with turn_interrupt.interactive_turn(OWNER, UID):
            result = asyncio.run(backend.dispatch({"op": "call",
                "name": f"mcp:{done['connection_id']}:write", "arguments": {"draft": "hello"}}))
        assert result["result"]["error_kind"] == "rule_ask_first", result
        assert not [c for c in connected_broker if c["method"] == "tools/call"]
        cookie = session_cookie().split("=", 1)[1]
        session = owner_sessions.lookup(cookie)
        preview = bound_requests.preview(home, result["result"]["request_id"], session)
        assert preview["draft"] == {"draft": "hello"}
        finished = bound_requests.decide(home, decision(preview), session)
        assert finished["phase"] == "confirmed", finished
        assert bound_requests.decide(home, decision(preview), session)["phase"] == "confirmed"
        assert len([c for c in connected_broker if c["method"] == "tools/call"]) == 1


def test_stopped_connect_cannot_activate(connected_broker, app, mcp_provider):
    with _as(OWNER):
        with turn_interrupt.interactive_turn(OWNER, UID):
            asked = _ask({**TASKS_ASK, "mcp_url": ENDPOINT})
        bound_requests.stop(app / UID, OWNER, "main")
        result = sign_in(mcp_provider, asked["request_id"], expected=409)
        assert result["error"] == "originating_task_stopped", result
        assert not connected_broker


def test_interrupted_activation_reconciles_same_attachment_and_one_wake(
    connected_broker, app, mcp_provider, monkeypatch,
):
    from tinyassets.mcp_runtime import catalog
    from tinyassets.storage import pending_requests

    real_resolve = pending_requests.resolve_request
    with _as(OWNER):
        with turn_interrupt.interactive_turn(OWNER, UID):
            asked = _ask({**TASKS_ASK, "mcp_url": ENDPOINT})
        monkeypatch.setattr(pending_requests, "resolve_request", lambda *a, **kw: False)
        result = sign_in(mcp_provider, asked["request_id"], expected=409)
        assert result["error"] == "request_storage_unavailable", result
        assert not catalog(app / UID, OWNER)  # committed metadata alone is inert
        monkeypatch.setattr(pending_requests, "resolve_request", real_resolve)
        sign_in(mcp_provider, asked["request_id"])
        assert len([c for c in connected_broker if c["method"] == "initialize"]) == 1
        assert catalog(app / UID, OWNER)
        with closing(bound_requests.connect(app / UID)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM activity_events "
                                "WHERE wake_required=1").fetchone()[0] == 1



def test_changed_catalog_rejects_old_handle_and_refreshes_tools(
    connected_broker, app, mcp_provider, monkeypatch,
):
    from tinyassets.mcp_remote import McpError

    with _as(OWNER):
        done = connect(mcp_provider)
        home = app / UID
        backend = Capabilities(home, ExecutionContext(UID, OWNER, "main"), [], None, lambda: None)
        asyncio.run(backend.dispatch({"op": "catalog"}))
        tool_name = f"mcp:{done['connection_id']}:read"
        changed = [{**TOOLS[0], "description": "new catalog revision"}, TOOLS[1]]
        monkeypatch.setattr(sys.modules[__name__], "TOOLS", changed)
        with pytest.raises(McpError, match="stale"):
            asyncio.run(backend.dispatch({"op": "call", "name": tool_name, "arguments": {}}))
        refreshed = asyncio.run(backend.dispatch({"op": "catalog"}))
        assert next(t for t in refreshed["capabilities"] if t["name"] == tool_name)[
            "description"] == "new catalog revision"
        assert not [c for c in connected_broker if c["method"] == "tools/call"]


@pytest.mark.asyncio
async def test_real_stream_cancel_records_unknown_and_notifies_server(
    connected_broker, app, mcp_provider, monkeypatch,
):
    import threading

    from tinyassets.broker.aclient import AsyncBrokerClient
    from tinyassets.broker.ops import new_op_id
    from tinyassets.mcp_runtime import remote_work

    # The app helper owns its own event loop, like the production sync answer worker.
    with _as(OWNER):
        done = await asyncio.to_thread(connect, mcp_provider)
        home = app / UID
        bound = binding(home, OWNER, done["grant_id"], done["connection_id"])
        original = mcp_provider.route
        entered, release = threading.Event(), threading.Event()
        methods = []

        def blocked(method, host, path, headers, body):
            if path == "/mcp":
                name = json.loads(body)["method"]
                methods.append(name)
                if name == "tools/call":
                    def stream():
                        yield b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                        entered.set()
                        assert release.wait(10)
                        yield original(method, host, path, headers, body)[1]

                    return 200, stream()
            return original(method, host, path, headers, body)

        monkeypatch.setattr(mcp_provider, "route", blocked)
        op_id = new_op_id()

        async def work(remote):
            await remote.discover()
            return await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id=op_id)

        task = asyncio.create_task(remote_work(home, OWNER, bound, work))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert methods.count("tools/call") == 1
            assert "notifications/cancelled" in methods
            client = AsyncBrokerClient.for_owner(app, principal=OWNER, command_center=UID)
            try:
                status = await client.status(op_id)
                assert status["side_effect_state"] == "unknown"
            finally:
                await client.close()
        finally:
            release.set()
            if not task.done():
                task.cancel()


@pytest.mark.parametrize("failure", ["ambiguous", "refused"])
def test_approved_failure_retains_operation_and_reconciles(
    connected_broker, app, mcp_provider, monkeypatch, failure,
):
    from tests.owner_answer import session_cookie
    from tests.test_inline_approvals import decision
    from tinyassets import mcp_runtime
    from tinyassets.mcp_remote import McpError
    from tinyassets.onboarding import owner_sessions

    with _as(OWNER):
        done = connect(mcp_provider)
        home = app / UID
        backend = Capabilities(home, ExecutionContext(UID, OWNER, "main"), [], None, lambda: None)
        with turn_interrupt.interactive_turn(OWNER, UID):
            asked = asyncio.run(backend.dispatch({"op": "call",
                "name": f"mcp:{done['connection_id']}:write", "arguments": {}}))["result"]
        session = owner_sessions.lookup(session_cookie().split("=", 1)[1])
        preview = bound_requests.preview(home, asked["request_id"], session)
        if failure == "ambiguous":
            original = mcp_provider.route

            def malformed(method, host, path, headers, body):
                result = original(method, host, path, headers, body)
                if path == "/mcp" and json.loads(body)["method"] == "tools/call":
                    return 200, b"data: not-json\n\n"
                return result

            monkeypatch.setattr(mcp_provider, "route", malformed)
        else:
            async def refused(*args, **kwargs):
                raise McpError("authority changed before sending")

            monkeypatch.setattr(mcp_runtime, "remote_work", refused)
        result = bound_requests.decide(home, decision(preview), session)
        assert result["status"] == "unresolved", result
        assert result["phase"] == ("unknown" if failure == "ambiguous" else "failed")
        assert len(result["result"]["op_id"]) == 26
        if failure == "ambiguous":
            reconciled = bound_requests.preview(home, asked["request_id"], session)
            assert reconciled["result"]["op_id"] == result["result"]["op_id"]
            assert reconciled["result"]["broker_status"]["side_effect_state"] == "unknown"
            assert reconciled["approval_unavailable"]
            with pytest.raises(bound_requests.RequestRefused):
                bound_requests.decide(home, decision(reconciled), session)
        assert len([c for c in connected_broker if c["method"] == "tools/call"]) == (
            1 if failure == "ambiguous" else 0)


def test_offline_attachment_does_not_hide_platform_catalog(
    connected_broker, app, mcp_provider, monkeypatch,
):
    with _as(OWNER):
        connect(mcp_provider)
        original = mcp_provider.route

        def unavailable(method, host, path, headers, body):
            return (503, {}) if path == "/mcp" else original(method, host, path, headers, body)

        monkeypatch.setattr(mcp_provider, "route", unavailable)
        backend = Capabilities(app / UID, ExecutionContext(UID, OWNER, "main"),
                               [{"name": "platform_tool"}], None, lambda: None)
        result = asyncio.run(backend.dispatch({"op": "catalog"}))
        assert {"name": "platform_tool"} in result["capabilities"]
        assert result["connection_errors"]
        assert not any(t["name"].startswith("mcp:") for t in result["capabilities"])
