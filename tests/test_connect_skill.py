"""Starter delivery and a scripted model using the real secure connect handler."""
import asyncio
import json

import pytest

from tests import test_agent_node as harness
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import engine_mcp_server, ta_cli, universe_tools
from tinyassets.starter_skills import CONNECT_SKILL_PATH, connect_skill
from tinyassets.universe_bundle import seed_okf_bundle
from tinyassets.universe_files import write_universe_file

http_wire = harness.http_wire
work_agent = harness.work_agent
engine = harness.engine


def test_new_account_connect_skill_is_indexed_and_owner_editable(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = tmp_path / "u-new"
    seed_okf_bundle(home)
    assert (home / CONNECT_SKILL_PATH).read_text(encoding="utf-8") == connect_skill()
    assert "connect" in dict(universe_tools.skill_index(home))
    assert CONNECT_SKILL_PATH in universe_tools.harness_prompt(home)
    edited = connect_skill().replace("Connect any service, platform or API", "My connection recipe")
    write_universe_file(home, CONNECT_SKILL_PATH, edited.encode("utf-8"))
    assert dict(universe_tools.skill_index(home))["connect"].startswith("My connection recipe")
    seed_okf_bundle(home)
    assert (home / CONNECT_SKILL_PATH).read_text(encoding="utf-8") == edited


def test_existing_account_can_copy_skill_without_implicit_install(tmp_path):
    home = tmp_path / "u-existing"
    home.mkdir()
    fetched = json.loads(engine_mcp_server._handbook_read("write_graph.connect"))["text"]
    assert fetched == connect_skill()
    assert universe_tools.skill_index(home) == []
    # The normal file-writing path installs it; reads never recreate a deletion.
    write_universe_file(home, CONNECT_SKILL_PATH, fetched.encode("utf-8"))
    assert "connect" in dict(universe_tools.skill_index(home))
    (home / CONNECT_SKILL_PATH).unlink()
    assert universe_tools.skill_index(home) == []
    assert CONNECT_SKILL_PATH not in universe_tools.harness_prompt(home)


def test_ta_search_connect_discovers_the_skill_and_request_tool(monkeypatch, tmp_path):
    offered = asyncio.run(engine_mcp_server.mcp.list_tools())
    catalog = {"extension_roots": {"shared": str(tmp_path)}, "capabilities": [
        {"name": tool.name, "description": tool.description or ""} for tool in offered
    ]}
    monkeypatch.setattr(ta_cli, "remote", lambda _: catalog)
    found = {item["name"]: item for item in ta_cli.main(["search", "connect"])}
    assert "write_graph" in found
    assert "skills/connect/SKILL.md" in found["write_graph"]["description"]


@pytest.mark.usefixtures("cloud_runtime")
def test_scripted_unknown_api_service_requests_secure_entry_not_chat(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    from tinyassets.api.pending_requests import answer_request, list_requests
    from tinyassets.connection_oauth import directory, discovery
    from tinyassets.storage.pending_requests import get_request
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    # Execute the example shipped to the model, with an unregistered service.
    payload = json.loads(connect_skill().split("```json\n", 1)[1].split("```", 1)[0])
    payload["action"]["destination"] = "orchard-cloud"
    payload["action"]["endpoints"][0]["host"] = "api.orchard.example"
    payload["title"] = "Connect Orchard Cloud"
    payload["fields"][0]["url"] = "https://orchard.example/settings/api"
    checked = []
    real_resolve = directory.resolve

    def resolve(requested, hosts):
        checked.append(hosts)
        return real_resolve(requested, hosts)

    monkeypatch.setattr(directory, "resolve", resolve)
    monkeypatch.setattr(discovery, "request_json", lambda *_: (404, {}))
    engine.script = [
        [harness._call("read_graph", target="handbook", query="write_graph.connect")],
        [harness._call("write_graph", target="pending_request", operation="ask",
                       payload_json=json.dumps(payload))],
    ]
    result = harness._run(tmp_path, monkeypatch, authenticate_request, ["agent"])
    assert result["terminal_status"] == "completed", (result, engine.errors)
    assert checked == [["api.orchard.example"]]
    assert any("secure inline entry" in text for text in engine.results)
    authenticate_request("acct_alice")
    pending = list_requests(universe_id="universe_alice")["pending"]
    request = next(row for row in pending if row.get("title") == payload["title"])
    assert any(request["request_id"] in text for text in engine.results)
    assert request["action"]["type"] == "connect"
    assert [field["type"] for field in request["fields"]] == ["secret"]

    # Simulate the owner's secure form submission, outside the model wire.
    secret = "SYNTHETIC-ORCHARD-KEY-ONLY-IN-SECURE-ENTRY"
    deposited = answer_request(universe_id="universe_alice", payload=json.dumps({
        "request_id": request["request_id"], "values": {"token": secret},
    }))
    assert deposited["status"] == "answered", deposited
    home = tmp_path / "universe_alice"
    stored = get_request(home, request["request_id"])
    assert secret not in json.dumps([engine.wires, engine.results, pending, deposited, stored])
    # The same connection can be called mid-turn through ta with no credential.
    capabilities = Capabilities(home, ExecutionContext("universe_alice", "acct_alice", "main"),
                                [], None, lambda: None)
    name = f"connection:{deposited['connection_id']}:GET"
    assert name in capabilities.connections()
    calls = []

    def harmless_read(**kwargs):
        calls.append(kwargs["run_state"]["call"])
        return {"delivered": True, "response": {"status": 200, "body": '{"id":"owner"}'}}

    monkeypatch.setattr(
        "tinyassets.effectors.authenticated_external_call.run_authenticated_external_call_effector",
        harmless_read,
    )
    verified = asyncio.run(capabilities.dispatch({
        "op": "call", "name": name, "arguments": {"request": {"path": "/v1/me"}},
    }))
    assert verified["result"]["delivered"] is True
    assert calls[0]["verb"] == "GET" and calls[0]["request"] == {"path": "/v1/me"}
    assert secret not in json.dumps([calls, verified])
