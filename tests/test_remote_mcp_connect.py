"""An agent handed an MCP server link connects it for good (issue #4551).

Live 2026-10-06: asked to connect https://mcp.deepwiki.com/mcp, the agent read
"MCP server attachment ... not available yet", found nothing under
``ta search "mcp server"`` and filed an issue, although remote MCP had shipped
as an extension contribution. These hold the three halves: no served guidance
denies a capability the registry has, natural words find it, and a keyless
server connects with the owner's yes and answers in a later turn.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.test_authenticated_external_call_effector import (
    _install_inprocess_proxy,
    _install_loopback_driver,
)
from tinyassets import engine_mcp_server as engine
from tinyassets import ta_cli
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.extension_capabilities import HANDBOOK, LIFECYCLE, UI_HANDBOOK
from tinyassets.extension_manifest import parse_manifest
from tinyassets.ta_capabilities import Capabilities, ExecutionContext

OWNER, UID = "user-1", "u-mcp"
URL = "https://mcp.deepwiki.com/mcp"
MANIFEST = {"schema_version": 2, "name": "deepwiki",
            "connections": [{"name": "server", "description": "MCP endpoint",
                             "verbs": ["POST"]}],
            "mcp_servers": [{"name": "deepwiki", "description": "Ask about GitHub repositories",
                             "transport": "remote", "url": URL, "slot": "server"}]}
#: A sentence that tells the agent MCP cannot be attached. Local stdio packages
#: really are pending U1, so a sentence about them is not a denial.
_DENIAL = re.compile(r"\bMCP\b[^.]*\b(not available|unavailable|not supported|"
                     r"cannot attach|can't attach)", re.IGNORECASE)


def _remote_mcp_exists() -> bool:
    """Ask the live registry, not a frozen string, whether remote MCP is offered."""
    parse_manifest(json.dumps(MANIFEST).encode())
    return {"extension:install", "extension:activate"} <= {row["name"] for row in LIFECYCLE}


def _denials(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return [s for s in sentences if _DENIAL.search(s) and not re.search(r"stdio", s, re.I)]


def _served_guidance() -> dict[str, str]:
    from tinyassets.api import prompts
    from tinyassets.starter_skills import starter_agent_files

    tools = asyncio.run(engine.mcp.list_tools())
    texts = {f"tool:{tool.name}": engine.served_tool_guidance(tool.name) for tool in tools}
    skills = Path(engine.__file__).with_name("skills")
    texts.update({f"skill:{path.parent.name}": path.read_text(encoding="utf-8")
                  for path in skills.glob("*/SKILL.md")})
    texts.update({f"starter:{path}": text for path, text in starter_agent_files().items()})
    texts.update({"extension:help": HANDBOOK, "extension:help ui": UI_HANDBOOK})
    texts.update({f"prompt:{name}": value for name, value in vars(prompts).items()
                  if name.endswith("_PROMPT") and isinstance(value, str)})
    return texts


def test_no_served_guidance_says_mcp_attachment_is_unavailable():
    assert _remote_mcp_exists()
    # The guard catches the sentence that sent the agent to file #4551.
    assert _denials("MCP server attachment and browser login are not available yet.")
    found = {where: hits for where, text in _served_guidance().items()
             if (hits := _denials(text))}
    assert not found, found
    # And the chapter it read now carries the route.
    chapter = json.loads(engine._handbook_read("write_graph.connect"))["text"]
    assert "mcp_servers" in chapter and '"auth_scheme":"none"' in chapter


def _backend(base, agent="main"):
    return Capabilities(base / UID, ExecutionContext(UID, OWNER, agent), [], None,
                        lambda: None, capability_grant=("write_graph", "run_graph"))


def _ta(monkeypatch, service, *argv):
    monkeypatch.setattr(ta_cli, "remote", lambda message: asyncio.run(service.dispatch(message)))
    monkeypatch.setattr(ta_cli, "extensions", lambda _roots: {})
    return ta_cli.main(list(argv))


@pytest.mark.parametrize("query", ["mcp server", "connect mcp", "add mcp", "remote mcp",
                                   "mcp url"])
def test_ta_search_finds_the_mcp_install_for_natural_words(tmp_path, monkeypatch, query):
    (tmp_path / UID).mkdir()
    found = {row["name"] for row in _ta(monkeypatch, _backend(tmp_path), "search",
                                        *query.split())}
    assert "extension:install" in found, found


class _McpFixture(ThreadingHTTPServer):
    """A keyless streamable-HTTP MCP server standing in for DeepWiki."""

    def __init__(self):
        self.seen = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append((self.path, dict(self.headers), request))
                result = {
                    "initialize": {"protocolVersion": "2025-06-18",
                                   "capabilities": {"tools": {}}},
                    "tools/list": {"tools": [{"name": "ask_question",
                                              "inputSchema": {"type": "object"}}]},
                    "tools/call": {"content": [{"type": "text", "text": "answer from fixture"}]},
                }.get(request["method"])
                body = b"" if result is None else json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                self.send_response(202 if result is None else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("MCP-Session-Id", "fixture-session")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        super().__init__(("127.0.0.1", 0), Handler)


@pytest.fixture
def home(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    base = tmp_path / "data"
    (base / UID).mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    grant_universe_access(base, universe_id=UID, actor_id=OWNER, permission="admin",
                          granted_by=OWNER)
    set_founder_home(base, founder_sub=OWNER, universe_id=UID, platform_generated=True)
    return base


def _as_owner():
    return identity_context(Identity(user_id=OWNER, username=OWNER,
                                     capabilities=["tinyassets.universe.write", "write"]))


def test_keyless_remote_mcp_connects_with_owner_yes_and_works_next_turn(
        home, tmp_path, monkeypatch):
    from tests.owner_answer import answer_request as owner_answer
    from tinyassets.api.cloud_connections import cloud_connections
    from tinyassets.api.pending_requests import answer_request, request_from_user
    from tinyassets.api.source_channel import source_channel

    server = _McpFixture()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _install_loopback_driver(monkeypatch, server.server_port)
    try:
        # Turn 1, agent: the fieldless keyless ask from the connect chapter.
        with _as_owner():
            asked = request_from_user(universe_id=UID, payload=json.dumps({
                "kind": "MCP", "title": "Connect DeepWiki", "body": "So I can use it later.",
                "action": {"type": "connect", "destination": "deepwiki", "auth_scheme": "none",
                           "endpoints": [{"host": "mcp.deepwiki.com", "path_template": "/mcp",
                                          "methods": ["POST"]}]},
                "fields": []}))
            assert asked.get("request_id"), asked
            assert "with no key" in asked["grant_sentence"], asked["grant_sentence"]
            assert cloud_connections(action="list", universe_id=UID)["connections"] == []
            # The agent's own (bearer) answer is not the owner's approval.
            refused = answer_request(universe_id=UID, payload=json.dumps(
                {"request_id": asked["request_id"], "values": {}}))
            assert refused["error"] == "interactive_approval_required", refused
            # Owner: one tap in the protected card, nothing pasted.
            done = owner_answer(universe_id=UID, payload={"request_id": asked["request_id"],
                                                          "values": {}})
            assert done.get("status") == "answered", done
            [row] = cloud_connections(action="list", universe_id=UID)["connections"]
            # The existing outbound-sink consent every connection's calls need.
            approved = json.loads(source_channel(action="approve", universe_id=UID, payload={
                "channel_type": "authenticated_external_call", "destination": "deepwiki"}))
            assert approved["status"] == "granted", approved
        assert row["destination"] == "deepwiki" and row["mcp_servers"] == []

        service = _backend(home)
        encoded = base64.b64encode(json.dumps(MANIFEST).encode()).decode()
        installed = _ta(monkeypatch, service, "extension:install", "--json",
                        json.dumps({"files": {"extension.json": encoded}}))
        active = _ta(monkeypatch, service, "extension:activate", "--json", json.dumps({
            "name": "deepwiki", "revision": installed["revision"], "expected_generation": 0,
            "bindings": {"server": {"connection_id": row["connection_id"],
                                    "grant_id": row["grant_id"]}}}))
        assert active["state"] == "active", active

        # The connection now says what rides on it, where owner and agent look.
        with _as_owner():
            [row] = cloud_connections(action="list", universe_id=UID)["connections"]
        [listed] = row["mcp_servers"]
        assert listed["url"] == URL and listed["extension"] == "deepwiki"

        # Turn 2: a fresh launch finds it by name and uses it, keylessly.
        _install_inprocess_proxy(monkeypatch, db_path=home / "outbound.db",
                                 universe_dir=home / UID, grant_id=row["grant_id"],
                                 provider="http", destination="deepwiki",
                                 runtime_root=tmp_path / "runtime")
        later = _backend(home)
        [hit] = [r for r in _ta(monkeypatch, later, "search", "deepwiki")
                 if r["name"].endswith(":mcp_servers:deepwiki")]
        assert hit["name"] == listed["capability"]
        catalog = _ta(monkeypatch, later, hit["name"], "--json", '{"action":"discover"}')
        assert [tool["name"] for tool in catalog.get("tools", [])] == ["ask_question"], catalog
        result = _ta(monkeypatch, later, hit["name"], "--json", json.dumps({
            "action": "call", "tool": "ask_question",
            "arguments": {"repoName": "facebook/react", "question": "What is it?"},
            "catalog_hash": catalog["catalog_hash"]}))
        assert result["content"] == [{"type": "text", "text": "answer from fixture"}], result
        calls = [(path, headers, request) for path, headers, request in server.seen
                 if request["method"] == "tools/call"]
        assert len(calls) == 1 and calls[0][0] == "/mcp"
        assert "Authorization" not in calls[0][1]
        assert "fixture-session" not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_keyless_ask_carries_no_key_box_and_no_wider_reach(home):
    from tinyassets.api.pending_requests import request_from_user

    base = {"type": "connect", "destination": "open", "auth_scheme": "none",
            "endpoints": [{"host": "mcp.example.com", "path_template": "/mcp",
                           "methods": ["POST"]}]}
    secret = [{"name": "token", "type": "secret", "label": "Key"}]
    with _as_owner():
        for action, fields in (
                (base, secret),
                ({**base, "endpoints": None, "access": "full", "hosts": ["mcp.example.com"]}, []),
                ({**base, "git_host": "mcp.example.com"}, []),
                ({**base, "oauth": {"scopes": ["read"]}}, [])):
            refused = request_from_user(universe_id=UID, payload=json.dumps({
                "kind": "MCP", "title": "Connect", "body": "", "action": action,
                "fields": fields}))
            assert refused.get("error"), (action, fields, refused)


def test_connection_listing_survives_a_corrupt_binding_row(tmp_path):
    from tinyassets import command_center_packages as packages
    from tinyassets.extension_state import ExtensionStore, remote_mcp_by_connection

    store = ExtensionStore(tmp_path, owner=OWNER, universe=UID, agent="main")
    installed = store.install({"extension.json": json.dumps(MANIFEST).encode()})
    store.transition("deepwiki", installed["revision"], expected_generation=0, active=True,
                     bindings={"server": {"connection_id": "conn-1", "grant_id": "g",
                                          "incarnation": "i"}})
    assert [row["url"] for row in remote_mcp_by_connection(
        tmp_path, owner=OWNER, universe=UID)["conn-1"]] == [URL]
    assert remote_mcp_by_connection(tmp_path, owner="someone-else", universe=UID) == {}
    with packages._db(tmp_path) as conn:
        conn.execute("UPDATE extension_bindings SET bindings_json='{\"server\": 7}'")
    assert remote_mcp_by_connection(tmp_path, owner=OWNER, universe=UID) == {}
