"""Research capabilities at the served MCP boundary and below the tool jail."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastmcp import Client

from tests.engine_authority_helpers import mock_engine_admission
from tinyassets import engine_mcp_server as server
from tinyassets import engine_steering, research_turn, universe_tools
from tinyassets.research_capability import ALLOWED, is_research_session


@pytest.fixture
def research(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_ACTOR_ID", "owner")
    monkeypatch.setattr(server, "_GRAPH_ID", "u-test")
    mock_engine_admission(monkeypatch, {"u-test"})
    monkeypatch.setattr(engine_steering, "_session_key", lambda: "research:agent:turn")


@pytest.mark.parametrize("key,expected", [
    ("research:agent:turn", True), ("research:", True),
    ("thread:research:agent:turn", False), ("", False),
])
def test_session(key, expected):
    assert is_research_session(key) is expected


@pytest.mark.asyncio
async def test_allowed_read(research, monkeypatch):
    from tinyassets import universe_server

    seen = []
    monkeypatch.setattr(universe_server, "read_graph",
                        lambda **kw: seen.append(kw) or '{"found": true}')
    async with Client(server.mcp) as client:
        result = await client.call_tool("read_graph", {"target": "graph"})
    assert not result.is_error
    assert json.loads(result.content[0].text)["found"] is True
    assert seen == [{"target": "graph", "graph_id": "u-test"}]


@pytest.mark.parametrize("tool,arguments", [
    ("write", {"path": "x", "content": "x"}),
    ("edit", {"path": "x", "old_text": "x", "new_text": "y"}),
    ("bash", {"command": "echo hello"}),
    ("run_graph", {}),
    ("write_graph", {"target": "branch", "operation": "create"}),
    ("write_graph", {"target": "pending_request", "operation": "ask"}),
    ("write_graph", {"target": "proposal", "operation": "answer"}),
    ("read_graph", {"target": "pending_requests"}),
    ("read_graph", {"target": "new_future_target"}),
    ("source_channel", {}), ("connect_compute", {}),
    ("get_status", {}), ("write_brain", {}), ("remix_shape", {}),
])
@pytest.mark.asyncio
async def test_served_refusals(research, tool, arguments):
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    assert result.is_error
    assert json.loads(result.content[0].text) == {
        "error": "research_is_read_only", "allowed": ALLOWED,
    }


@pytest.mark.asyncio
async def test_regular_write_unaffected(research, monkeypatch):
    monkeypatch.setattr(engine_steering, "_session_key", lambda: "thread:owner")
    monkeypatch.setattr(universe_tools, "write_file", lambda *a, **kw: "written")
    async with Client(server.mcp) as client:
        result = await client.call_tool("write", {"path": "x", "content": "y"})
    assert result.content[0].text == "written"


@pytest.mark.parametrize("op,args", [
    (universe_tools.write_file, ("x", "y")),
    (universe_tools.edit_file, ("x", "y", "z")),
    (universe_tools.bash, ("echo read-only",)),
])
def test_jail_refuses_before_runner(research, monkeypatch, tmp_path, op, args):
    def forbidden(*a, **kw):
        pytest.fail("research must not enter the runner")

    monkeypatch.setattr(universe_tools, "RUNNER", forbidden)
    assert json.loads(op(tmp_path, *args, agent_id="main"))["error"] == "research_is_read_only"


@pytest.mark.parametrize("paused,busy,compute,declined,calls", [
    (True, True, False, "paused", ["paused"]),
    (False, True, False, "busy", ["paused", "busy"]),
    (False, False, False, "no_compute", ["paused", "busy", "compute"]),
    (False, False, True, "not_wired", ["paused", "busy", "compute"]),
])
def test_wake_order(monkeypatch, tmp_path, paused, busy, compute, declined, calls):
    seen = []
    monkeypatch.setattr(research_turn, "_paused", lambda *a: seen.append("paused") or paused)
    monkeypatch.setattr(research_turn, "_busy", lambda *a: seen.append("busy") or busy)
    monkeypatch.setattr(research_turn, "_has_compute",
                        lambda *a: seen.append("compute") or compute)
    req = {"command_center_id": "u-test", "agent_id": "agent",
           "owner_principal_id": "owner", "trigger_key": "tick", "due_at": "now"}
    assert research_turn.handle_research_wake(tmp_path, req) == {"declined": declined}
    assert seen == calls


def test_pause_sentinel(tmp_path):
    assert not research_turn._paused(tmp_path, "agent")
    (tmp_path / ".pause").touch()
    assert research_turn._paused(tmp_path, "agent")


@pytest.mark.parametrize("state,expected", [(None, False), ("ready", True), ("stale", False)])
def test_serving_assignment(monkeypatch, tmp_path, state, expected):
    from tinyassets import provider_assignment

    monkeypatch.setattr(provider_assignment, "load_provider_assignment",
                        lambda *a, **kw: SimpleNamespace(state=state) if state else None)
    assert research_turn._has_compute(tmp_path, "u-test") is expected
