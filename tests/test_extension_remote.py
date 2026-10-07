"""One activation owns remote MCP and recipient-local connection requirements."""
import asyncio
import json

import pytest

from tests.test_authenticated_external_call_effector import _setup
from tests.test_one_extension_unit import call, files
from tests.test_ta_capabilities import backend
from tinyassets.extension_capabilities import ExtensionCapabilities


def setup(tmp_path):
    _, root, _ = _setup(tmp_path)
    service = backend(root, agent="main")
    extensions = ExtensionCapabilities(service)
    installed = extensions.store.install(files(connections=[{
        "name": "api", "description": "API", "verbs": ["POST"]}], mcp_servers=[{
            "name": "remote", "description": "Remote", "transport": "remote",
            "url": "https://api.example.com/v1/messages", "slot": "api"}]))
    args = {"name": "sample", "revision": installed["revision"], "expected_generation": 0,
            "bindings": {"api": {"connection_id": "conn-http", "grant_id": "grant-http"}}}
    return service, extensions, args


def activate(service, extensions, args):
    result = call(service, "extension:activate", args)
    assert result["result"]["state"] == "active", result
    return next(row["name"] for row in extensions.catalog() if row.get("kind") == "mcp_servers")


@pytest.mark.parametrize("mutation", ["foreign", "grant", "verb", "slot"])
def test_binding_never_mints_or_widens_grants(tmp_path, mutation):
    service, extensions, args = setup(tmp_path)
    if mutation == "foreign":
        args["bindings"]["api"]["connection_id"] = "other"
    elif mutation == "grant":
        args["bindings"]["api"]["grant_id"] = "other"
    elif mutation == "verb":
        service.connections_granted = False
    else:
        args["bindings"]["other"] = args["bindings"].pop("api")
    assert "error" in call(service, "extension:activate", args)
    assert extensions.store.list()[0]["state"] == "installed"


def test_remote_discovery_calls_pinned_catalog_and_hides_headers(tmp_path, monkeypatch):
    service, extensions, args = setup(tmp_path)
    name = activate(service, extensions, args)
    from tinyassets.effectors import authenticated_external_call as aec

    sent = []
    def exchange(**kwargs):
        packet = kwargs["run_state"]["call"]
        sent.append(packet)
        request = json.loads(packet["request"]["body"])
        method = request["method"]
        result = {"initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                  "tools/list": {"tools": [{"name": "hello", "inputSchema": {"type": "object"}}]},
                  "tools/call": {"content": [{"type": "text", "text": "done"}]}}.get(method)
        return {"delivered": True, "response": {
            "status": 202 if method == "notifications/initialized" else 200,
            "headers": {"Content-Type": "application/json", "MCP-Session-Id": "private-session",
                        "Set-Cookie": "private-cookie"},
            "body": "" if result is None else json.dumps({
                "jsonrpc": "2.0", "id": request["id"], "result": result})}}
    monkeypatch.setattr(aec, "run_authenticated_external_call_effector", exchange)
    catalog = call(service, name, {"action": "discover"})["result"]
    assert catalog["tools"][0]["name"] == "hello"
    result = call(service, name, {"action": "call", "tool": "hello", "arguments": {},
                                "catalog_hash": catalog["catalog_hash"]})
    assert result["result"]["content"][0]["text"] == "done"
    assert "private-session" not in json.dumps([catalog, result])
    assert "private-cookie" not in json.dumps([catalog, result])
    assert all(p["connection_id"] == "conn-http" and p["grant_id"] == "grant-http" for p in sent)
    before = len(sent)
    result = call(service, name, {"action": "call", "tool": "hello", "arguments": {},
                                "catalog_hash": "stale"})
    assert result["result"]["error"] == "mcp_protocol_refused"
    assert all(json.loads(p["request"]["body"])["method"] != "tools/call" for p in sent[before:])
    call(service, "extension:revoke", {k: v for k, v in {**args, "expected_generation": 1}.items()
                                      if k != "bindings"})
    before = len(sent)
    assert "error" in call(service, name, {"action": "discover"})
    assert len(sent) == before


def test_remote_preserves_existing_effector_refusal(tmp_path):
    service, extensions, args = setup(tmp_path)
    name = activate(service, extensions, args)
    from tinyassets import agent_rules

    agent_rules.set_rule(service.root, "app.write", agent_rules.HAND_OFF, agent="main")
    # Real effector and an explicit owner rule; no transport is mocked.
    result = call(service, name, {"action": "discover"})["result"]
    assert result["error_kind"] == "rule_hand_off" and not result.get("delivered"), result


def test_binding_incarnation_fences_replaced_connection(tmp_path):
    service, extensions, args = setup(tmp_path)
    name = activate(service, extensions, args)
    import sqlite3
    with sqlite3.connect(service.root.parent / "outbound.db") as conn:
        conn.execute("UPDATE outbound_connections SET incarnation='replacement'")
    assert "incarnation" in call(service, name, {"action": "discover"})["error"]
    assert asyncio.run(service.dispatch({"op": "catalog"}))["extension_capabilities"]


def test_ask_first_never_captures_an_orphaned_protocol_frame(tmp_path, monkeypatch):
    from tinyassets import agent_rules, bound_requests

    service, extensions, args = setup(tmp_path)
    name = activate(service, extensions, args)
    agent_rules.set_rule(service.root, "app.write", agent_rules.ASK_FIRST, agent="main")
    monkeypatch.setattr(bound_requests, "capture", lambda *a, **k:
                        pytest.fail("MCP protocol frame must not become an approval"))
    result = call(service, name, {"action": "discover"})["result"]
    assert result["error_kind"] == "rule_ask_first"
    assert "standing rule" in result["hint"]
    assert "request_id" not in result and not result.get("delivered")


@pytest.mark.parametrize("reply_kind", ["json", "sse", "stalled", "stalled_empty"])
def test_real_effector_remote_wire_and_outside_admission(tmp_path, monkeypatch, reply_kind):
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from tests.test_authenticated_external_call_effector import _install_inprocess_proxy
    from tests.test_outbound_ssrf_driver import _PassThroughTLS
    from tinyassets.auth.middleware import current_identity_or_none
    from tinyassets.auth.provider import Identity
    from tinyassets.outside_authority import OutsideClientAuthority
    from tinyassets.storage import outbound_connections as oc

    service, extensions, args = setup(tmp_path)
    name = activate(service, extensions, args)
    store = OutsideClientAuthority(tmp_path)
    monkeypatch.setattr("tinyassets.outside_authority.current_store", lambda: store)
    source = {"client": "outside", "family": "session", "authenticated_at": 100}
    store.observe("user-1", source)
    store.set_enabled(True)
    store.change("user-1", "outside", expected_generation=0, family="session", scopes=[
        {"universe": service.root.name, "agent": "main", "capability": capability}
        for capability in (name, "connection:conn-http:POST")])
    identity = Identity("user-1", "user-1", metadata={
        "outside_origin": store.admit("user-1", source)})
    service.outside_identity = identity
    seen, responses, identities = [], [], []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            request = json.loads(raw)
            seen.append((dict(self.headers), raw, self.path))
            method = request["method"]
            result = {
                "initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                "tools/list": {"tools": [{"name": "hello", "inputSchema": {"type": "object"}}]},
                "tools/call": {"content": [{"type": "text", "text": "done"}]},
            }.get(method)
            payload = b"" if result is None else json.dumps({
                "jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            streaming = reply_kind != "json" and method == "tools/call"
            if streaming:
                payload = b"data: " + payload + b"\n\n"
                if reply_kind == "stalled_empty":
                    payload = b": heartbeat\n\n"
            self.send_response(202 if result is None else 200)
            self.send_header("Content-Type",
                             "text/event-stream" if streaming else "application/json")
            self.send_header("MCP-Session-Id", "private-session")
            stalled = streaming and reply_kind.startswith("stalled")
            if not stalled:
                self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            if stalled:
                release.wait(5)
                self.close_connection = True

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    driver = oc._SsrfHardenedHttpDriver

    class SinkDriver(driver):
        def __call__(self, **kwargs):
            # Git fixture's loopback/TLS seam; also shorten only the transport's
            # idle window to produce a real stalled partial response promptly.
            identities.append(current_identity_or_none())
            with store.db() as conn:
                assert conn.execute("SELECT count(*) FROM outside_effects WHERE "
                                    "client='outside' AND state='in_flight'").fetchone()[0] == 1
            result = super().__call__(**{**kwargs, "reply_stream": (0.1, 5)})
            responses.append(result)
            return result

    def open_socket(address, timeout, source):
        return socket.create_connection(  # hermetic-ok: loopback stub only
            ("127.0.0.1", http.server_port), timeout)

    monkeypatch.setattr(oc, "_SsrfHardenedHttpDriver", lambda: SinkDriver(
        resolver=lambda h, p: ["127.0.0.1"], validator=lambda addr: addr,
        open_socket=open_socket, ssl_context=_PassThroughTLS()))
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    _install_inprocess_proxy(monkeypatch, db_path=tmp_path / "outbound.db",
        universe_dir=service.root, grant_id="grant-http", provider="http",
        destination="api.example.com", runtime_root=tmp_path / "runtime")
    try:
        # No ambient request identity: ta must bind its captured launch identity
        # throughout the coroutine and asyncio.to_thread effector invocation.
        catalog = call(service, name, {"action": "discover"})["result"]
        assert "tools" in catalog, catalog
        arguments = {"literal": {"$ta.ref": "must remain JSON-RPC text"}, "unicode": "caf\u00e9"}
        result = call(service, name, {"action": "call", "tool": "hello", "arguments": arguments,
                                    "catalog_hash": catalog["catalog_hash"]})["result"]
        if reply_kind == "stalled_empty":
            assert result["error"] == "mcp_outcome_unknown"
        else:
            assert result["content"] == [{"type": "text", "text": "done"}]
        assert len(seen) == 7  # two handshakes/catalogs, exactly one tool effect; no replay
        assert all(value is identity for value in identities)
        headers, raw, path = seen[-1]
        assert path == "/v1/messages"
        assert headers["Mcp-Session-Id"] == "private-session"
        assert headers["Mcp-Protocol-Version"] == "2025-06-18"
        assert headers["Accept"] == "application/json, text/event-stream"
        assert headers["Authorization"] == "Bearer real-vault-http-token"
        assert raw == json.dumps({"jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": "hello", "arguments": arguments}, "id": 4},
            separators=(",", ":")).encode()
        assert bool(responses[-1].get("stalled")) == reply_kind.startswith("stalled")
        assert "private-session" not in json.dumps(result)
        with store.db() as conn:
            assert conn.execute("SELECT count(*) FROM outside_effects WHERE "
                                "client='outside' AND state='finished'").fetchone()[0] == 7
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join(2)
