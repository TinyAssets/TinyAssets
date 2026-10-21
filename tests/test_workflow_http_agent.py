"""Real foreground compiler/admission/router/journal; synthetic remote transports."""

import json
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from tests import test_work_model_selection as work_model_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets import engine_mcp_http, engine_tool_client
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS
from tinyassets.storage.agent_turn_journal import AgentTurnJournal

http_wire = work_model_tests.http_wire


pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def work_agent(tmp_path, monkeypatch, http_wire):
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    # _run_branch establishes the real serving binding; do not invent a second
    # binding before that setup. Tool admission needs the same owner's admin ACL.
    from tinyassets.daemon_server import grant_universe_ownership
    grant_universe_ownership(tmp_path, universe_id="universe_alice", owner_id="acct_alice")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    engine_mcp_http._write_routes(tmp_path, [SimpleNamespace(
        universe_id="universe_alice", owner="acct_alice", port=8790, secret="s" * 43,
    )])
    # Persona generation isn't this test's subject. Identity/route/admission are
    # real and the adapter overwrites these hostile ordinary config identities.
    monkeypatch.setattr("tinyassets.shared_self.prepare_shared_self_turn", lambda *args, **kw: (
        args[3], "work system", ModelConfig(
            engine_mcp_enabled=True, engine_mcp_actor_id="wrong-owner",
            engine_mcp_graph_id="wrong-universe", max_tokens=1024, absolute_cap_s=120,
        ),
    ))
    state = SimpleNamespace(wires=[], tools=[], mode=None, before_tool=None, after_tool=None,
                            closed=False,
                            errors=http_wire[2])
    journal = AgentTurnJournal(tmp_path)

    def latest():
        with journal._ledger.connection() as conn:
            row = conn.execute(
                "SELECT turn_id FROM agent_turns ORDER BY created_at DESC",
            ).fetchone()
        return journal.get("acct_alice", "universe_alice", row[0])

    state.latest = latest

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            state.closed = True

        def is_connected(self):
            return not state.closed

        async def list_tools_mcp(self, *, cursor=None):
            if state.mode == "discovery_failure":
                raise RuntimeError("synthetic engine discovery failure")
            return ListToolsResult(tools=[Tool(name=name, inputSchema={"type": "object"})
                                          for name in SERVED_ENGINE_MCP_TOOLS])

        async def call_tool_mcp(self, name, arguments):
            assert latest().rounds[-1].tools[0].state == "started"
            state.tools.append((name, arguments))
            if state.mode == "unknown_tool":
                raise RuntimeError("synthetic tool disconnect")
            if state.after_tool:
                state.after_tool()
            return CallToolResult(content=[TextContent(type="text", text="known work result")])

    def client(route, timeout):
        assert route.actor_id == "acct_alice" and route.graph_id == "universe_alice"
        return Client()

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            turn = latest()
            assert turn.state == "inference_started" and turn.authority_kind == "work_invocation"
            assert turn.rounds[-1].candidate.work_receipt_id == turn.work_receipt_id
            state.wires.append(document)
            if state.mode == "unknown_inference":
                return {"error": "synthetic inference disconnect"}
            if state.mode == "authentication":
                return {"status": 401, "body": '{"error":{"message":"synthetic sign-in"}}'}
            if state.mode == "all_models_full":
                return {"status": 503, "body": '{"error":{"message":"synthetic overload"}}'}
            if len(state.wires) == 2 and state.mode == "later_capacity":
                return {"status": 429, "body": '{"error":{"message":"synthetic capacity"}}'}
            if len(state.wires) == 2 and state.mode == "later_model_capacity":
                return {"status": 503, "body": '{"error":{"message":"synthetic overload"}}'}
            tools = (len(state.wires) == 1 and state.mode != "no_tools"
                     or state.mode == "ongoing_tools")
            message = {"role": "assistant", "content": None if tools else "work completed"}
            if tools:
                message["tool_calls"] = [{"id": "tool-1", "type": "function", "function": {
                    "name": "bash", "arguments": json.dumps({
                        "command": "ta call read_graph --json '{\"target\":\"status\"}'"}),
                }}]
                if state.before_tool:
                    state.before_tool()
            return {"status": 200, "body": json.dumps({
                "model": "actual-work-model", "choices": [{
                    "message": message, "finish_reason": "tool_calls" if tools else "stop",
                }], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": 0},
            })}

    monkeypatch.setattr(engine_tool_client, "_make_client", client)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: Proxy()))
    return state


def run(tmp_path, monkeypatch, authenticate_request):
    branch = _branch(node_count=1)
    branch.node_defs[0].tools_allowed = ["universe_self"]
    branch.node_defs[0].llm_policy = {"preferred": {"model": "synthetic-model"},
                                    "fallback_chain": []}
    return _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                       open_provider=True, model_access=ModelAccess("discovered"))[0]
















