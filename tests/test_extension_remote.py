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
