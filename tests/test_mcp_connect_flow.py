"""An unknown OAuth MCP server through the owner sheet, real broker and ta."""
# ruff: noqa: F811
import asyncio
import json
import os
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
    from tinyassets import role_modes
    from tinyassets.broker import supervisor
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.onboarding import session_store

    # In-process broker fixture shares the test user group; production uses 1102.
    monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())
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
        token = (headers.get("Authorization", "").removeprefix("Bearer ")
                 or headers.get("X-Api-Key", ""))
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


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_disconnect_fences_cleanup_and_reconnect_rejects_old_packet(
    connected_broker, app, mcp_provider, monkeypatch, fail_cleanup,
):
    from tinyassets import credential_vault
    from tinyassets.api.http_connection import remove_http
    from tinyassets.mcp_runtime import packet, validate_packet
    from tinyassets.providers.connection_lifecycle import unfinished_disconnections
    from tinyassets.storage.outbound_connections import GrantResolutionError

    with _as(OWNER):
        first = connect(mcp_provider)
        home = app / UID
        old = binding(home, OWNER, first["grant_id"], first["connection_id"])
        prepared = packet(old, old.attachment.tools[0], {})
        payload = {"destination": "tasklark", "incarnation": old.incarnation}
        original = credential_vault.forget_credential
        if fail_cleanup:
            def crash(*args, **kwargs):
                raise OSError("cleanup interrupted")
            monkeypatch.setattr(credential_vault, "forget_credential", crash)
            with pytest.raises(OSError, match="interrupted"):
                remove_http(universe_id=UID, payload=payload)
            with pytest.raises(GrantResolutionError):
                validate_packet(home, OWNER, prepared)
            assert unfinished_disconnections(app, owner=OWNER, uid=UID)
            monkeypatch.setattr(credential_vault, "forget_credential", original)
        assert remove_http(universe_id=UID, payload=payload)["status"] == "removed"
        assert not unfinished_disconnections(app, owner=OWNER, uid=UID)
        with ConnectionLedger(app / "outbound.db")._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM mcp_attachments").fetchone()[0] == 0
        second = connect(mcp_provider)
        fresh = binding(home, OWNER, second["grant_id"], second["connection_id"])
        assert fresh.incarnation != old.incarnation
        from tinyassets.mcp_remote import McpError
        with pytest.raises(McpError, match="stale"):
            validate_packet(home, OWNER, prepared)
        assert remove_http(universe_id=UID, payload=payload)["error"] == "connection_changed"
        assert binding(home, OWNER, second["grant_id"], second["connection_id"]) == fresh


def test_detach_preserves_http_custody_and_durable_tombstone(connected_broker, app, mcp_provider):
    from tinyassets.api.http_connection import remove_http
    from tinyassets.broker.ledger_queries import authorized_connection
    from tinyassets.mcp_remote import McpError
    from tinyassets.mcp_runtime import activate, packet, validate_packet

    with _as(OWNER):
        done = connect(mcp_provider)
        home = app / UID
        old = binding(home, OWNER, done["grant_id"], done["connection_id"])
        before = authorized_connection(app, principal=OWNER, command_center=UID,
                                      grant_id=old.grant_id, connection_id=old.connection_id)
        payload = {"destination": "tasklark", "incarnation": old.incarnation,
                   "attachment_only": True}
        for _ in range(2):
            removed = remove_http(universe_id=UID, payload=payload)
            assert removed["attachment_removed"] and not removed["connection_removed"]
        after = authorized_connection(app, principal=OWNER, command_center=UID,
                                      grant_id=old.grant_id, connection_id=old.connection_id)
        assert after == before
        current = binding(home, OWNER, old.grant_id, old.connection_id)
        assert current.attachment.state == "revoked" and current.attachment.tools == []
        with pytest.raises(McpError):
            validate_packet(home, OWNER, packet(old, old.attachment.tools[0], {}))
        with pytest.raises(McpError, match="reconnect"):
            activate(home, OWNER, old.grant_id, old.connection_id, ENDPOINT, "tasklark",
                     old.attachment.activation_request_id)


def owner_control(data, *, cookie=None):
    import httpx
    from starlette.applications import Starlette

    from tests.owner_answer import session_cookie
    from tinyassets import onboarding

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=Starlette(routes=onboarding.onboarding_routes())),
                base_url="https://tinyassets.io") as client:
            return await client.post("/app/connections", json=data,
                headers={"Origin": "https://tinyassets.io",
                         "Cookie": session_cookie() if cookie is None else cookie})
    return asyncio.run(run())


def test_model_independent_multiple_accounts(connected_broker, app, mcp_provider, monkeypatch):
    from tests.test_generic_oauth_connections import _get
    from tinyassets.mcp_remote import McpError
    from tinyassets.mcp_runtime import packet, validate_packet

    with _as(OWNER):
        accounts = []
        for label in ("work", "personal"):
            data = {"operation": "connect_mcp", "universe_id": UID, "destination": label,
                    "mcp_url": ENDPOINT, "auth_scheme": "bearer", "auth_header": ""}
            assert owner_control(data, cookie="").status_code == 403
            offer = owner_control(data)
            assert offer.status_code == 200, offer.text
            done = sign_in(mcp_provider, offer.json()["request_id"])
            accounts.append(binding(app / UID, OWNER, done["grant_id"], done["connection_id"]))
            assert owner_control(data).json()["error"] == "account_label_in_use"
        assert accounts[0].connection_id != accounts[1].connection_id
        from tinyassets import ta_cli

        backend = Capabilities(app / UID, ExecutionContext(UID, OWNER, "main"), [], None,
                               lambda: None)
        monkeypatch.setattr(ta_cli, "remote",
                            lambda message: asyncio.run(backend.dispatch(message)))
        found = ta_cli.main(["search", "work", "read"])
        assert len(found) == 1 and found[0]["account_label"] == "work"
        assert found[0]["server_url"] == ENDPOINT
        listed = _get("/app/connections").json()["connections"]
        assert {row["label"] for row in listed} == {"work", "personal"}
        assert all(row["mcp"]["endpoint"] == ENDPOINT for row in listed)
        data = {"operation": "detach_mcp", "universe_id": UID, "destination": "work",
                "incarnation": accounts[0].incarnation}
        assert owner_control(data).status_code == 200
        with pytest.raises(McpError):
            validate_packet(app / UID, OWNER,
                            packet(accounts[0], accounts[0].attachment.tools[0], {}))
        assert validate_packet(app / UID, OWNER,
            packet(accounts[1], accounts[1].attachment.tools[0], {}))[0] == accounts[1]
    with _as(OTHER):
        assert owner_control(data).status_code == 409


@pytest.mark.parametrize("scheme,header", [("bearer", ""), ("header", "X-Api-Key")])
def test_protected_key_mcp_and_exact_egress_slot(
    connected_broker, app, mcp_provider, monkeypatch, scheme, header,
):
    from tests.owner_answer import answer_request
    from tinyassets.broker.aclient import AsyncBrokerClient
    from tinyassets.broker.ops import new_op_id
    from tinyassets.connection_oauth import mcp
    from tinyassets.connection_oauth.transport import OAuthError
    from tinyassets.storage.outbound_connections import GrantResolutionError, SsrfValidationError
    from tinyassets.storage.pending_requests import get_request

    def no_oauth(*args):
        raise OAuthError("no_oauth", "Use protected key entry")
    monkeypatch.setattr(mcp, "discover", no_oauth)
    secret = "private-mcp-test-credential-12345"
    mcp_provider.access[secret] = 4102444800
    with _as(OWNER):
        asked = _ask({**TASKS_ASK, "mcp_url": ENDPOINT, "auth_scheme": scheme,
                      "mcp_auth_header": header})
        assert asked.get("request_id"), asked
        payload = {"request_id": asked["request_id"], "values": {"secret": secret}}
        assert answer_request(universe_id=UID, payload=payload, cookie="")["error"] == (
            "interactive_approval_required")
        done = answer_request(universe_id=UID, payload=payload)
        assert done.get("mcp", {}).get("state") == "active", done
        home = app / UID
        bound = binding(home, OWNER, done["grant_id"], done["connection_id"])
        assert bound.attachment.auth_header == header
        backend = Capabilities(home, ExecutionContext(UID, OWNER, "main"), [], None, lambda: None)
        result = asyncio.run(backend.dispatch({"op": "call",
            "name": f"mcp:{done['connection_id']}:read", "arguments": {}}))
        assert "response" in result["result"], result
        saved = get_request(home, asked["request_id"])
        assert secret not in json.dumps([saved, result, done])

        async def refused(changes):
            client = AsyncBrokerClient.for_owner(app, principal=OWNER, command_center=UID)
            request = {"url": ENDPOINT, "body": '{}', **({"header_name": header} if header else {})}
            try:
                with pytest.raises((GrantResolutionError, SsrfValidationError)):
                    async with client.stream(grant_id=bound.grant_id,
                        connection_id=bound.connection_id, verb="POST", request=request | changes,
                        op_id=new_op_id(), mcp_binding={"incarnation": bound.incarnation,
                                                      "revision": bound.attachment.revision},
                    ) as stream:
                        await stream.head()
            finally:
                await client.close()
        for changes in ({"url": "https://other.example/mcp"}, {"header_name": "X-Other-Key"},
                        {"url": ENDPOINT + "/other"}):
            asyncio.run(refused(changes))


@pytest.mark.parametrize("classification", [False, True])
def test_owner_can_edit_unknown_effect_default(connected_broker, app, mcp_provider, classification):
    from tinyassets import agent_rules
    from tinyassets.mcp_runtime import packet, policy

    with _as(OWNER):
        done = connect(mcp_provider)
        home = app / UID
        bound = binding(home, OWNER, done["grant_id"], done["connection_id"])
        tool = next(t for t in bound.attachment.tools if t["name"] == "write")
        prepared = packet(bound, tool, {"draft": "hello"})
        assert policy(home, OWNER, "main", prepared)[2] == agent_rules.ASK_FIRST
        if classification:
            agent_rules.declare_kind(home, done["connection_id"], "read", confirm=True)
        else:
            agent_rules.set_rule(home, "app.write", agent_rules.DO,
                                 connection=done["connection_id"])
        assert policy(home, OWNER, "main", prepared)[2] == agent_rules.DO
        backend = Capabilities(home, ExecutionContext(UID, OWNER, "main"), [], None, lambda: None)
        result = asyncio.run(backend.dispatch({"op": "call",
            "name": f"mcp:{done['connection_id']}:write", "arguments": {"draft": "hello"}}))
        assert "response" in result["result"]


@pytest.mark.parametrize("preexisting_mcp", [False, True])
def test_answer_cannot_replace_another_connections_key(
    connected_broker, app, mcp_provider, preexisting_mcp,
):
    from tests.owner_answer import answer_request
    from tests.test_http_connection_provisioning import _http_records
    from tinyassets.api.http_connection import connect_http

    with _as(OWNER):
        if preexisting_mcp:
            connect(mcp_provider)
        else:
            created = connect_http(universe_id=UID, payload={"destination": "tasklark",
                "secret": "original-independent-key", "auth_scheme": "bearer",
                "allowed_endpoints": [{"host": urlsplit(ENDPOINT).netloc,
                                       "path_template": "/original", "methods": ["POST"]}]})
            assert "error" not in created
        ledger = ConnectionLedger(app / "outbound.db")
        before = _http_records(app / UID)
        with ledger._connect() as db:
            rows = [tuple(row) for row in db.execute("SELECT * FROM outbound_connections")]
        asked = _ask({**TASKS_ASK, "mcp_url": ENDPOINT}, fields=None)
        refused = answer_request(universe_id=UID, payload={
            "request_id": asked["request_id"], "values": {"secret": "replacement-key"}})
        assert refused["error"] == "account_label_in_use", refused
        assert _http_records(app / UID) == before
        with ledger._connect() as db:
            assert [tuple(row) for row in db.execute("SELECT * FROM outbound_connections")] == rows


def test_deposit_and_draft_commit_together_before_activation(
    connected_broker, app, mcp_provider, monkeypatch,
):
    from tinyassets import mcp_runtime
    from tinyassets.mcp_remote import McpError

    with _as(OWNER):
        asked = _ask({**TASKS_ASK, "mcp_url": ENDPOINT})
        real = mcp_runtime.activate
        def interrupted(*args, **kwargs):
            raise McpError("crashed before initialization")
        monkeypatch.setattr(mcp_runtime, "activate", interrupted)
        refused = sign_in(mcp_provider, asked["request_id"], expected=409)
        assert refused["error"] == "mcp_activation_unavailable"
        with ConnectionLedger(app / "outbound.db")._connect() as db:
            saved = json.loads(db.execute(
                "SELECT descriptor_json FROM mcp_attachments").fetchone()[0])
            assert saved["state"] == "draft"
            assert saved["activation_request_id"] == asked["request_id"]
            incarnation = db.execute("SELECT incarnation FROM outbound_connections").fetchone()[0]
        monkeypatch.setattr(mcp_runtime, "activate", real)
        done = sign_in(mcp_provider, asked["request_id"])
        assert binding(app / UID, OWNER, done["grant_id"], done["connection_id"]).incarnation == (
            incarnation)
