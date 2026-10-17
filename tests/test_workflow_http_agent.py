"""Real foreground compiler/admission/router/journal; synthetic remote transports."""

import json
import sqlite3
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
from tinyassets.storage.provider_work_authority import db_path

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


def test_foreground_work_runs_tool_then_model_under_same_receipt(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    result = run(tmp_path, monkeypatch, authenticate_request)
    if result["terminal_status"] != "completed":
        pytest.fail(result["terminal_error"] + "\n" + "\n".join(work_agent.errors))
    assert len(work_agent.wires) == 2 and len(work_agent.tools) == 1 and work_agent.closed
    turn = work_agent.latest()
    assert turn.state == "completed" and len(turn.rounds) == 2
    assert "known work result" in turn.rounds[0].tools[0].result_json
    assert "known work result" in json.dumps(work_agent.wires[1]["body"])
    with sqlite3.connect(db_path(tmp_path)) as conn:
        rows = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations ORDER BY ordinal",
        )]
    assert len(rows) == 2 and all(row["state"] == "succeeded" for row in rows)
    assert {row["receipt_id"] for row in rows} == {turn.work_receipt_id}
    assert sum(row["actual_total_tokens"] for row in rows) == 14
    # A fresh round uses the remaining aggregate, not a fresh full-turn budget.
    assert rows[1]["max_tokens"] == rows[0]["max_tokens"] - 7


@pytest.mark.parametrize("mode", ["unknown_tool", "unknown_inference", "later_capacity"])
def test_work_effect_is_not_replayed_by_compiler_policy_retry(
    tmp_path, monkeypatch, authenticate_request, work_agent, mode,
):
    work_agent.mode = mode
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed", result
    assert len(work_agent.tools) == (0 if mode == "unknown_inference" else 1)
    assert len(work_agent.wires) == (2 if mode == "later_capacity" else 1)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone() == (1,)
    if mode == "unknown_tool":
        assert work_agent.latest().state == "held_tool_unknown"
    elif mode == "later_capacity":
        assert "known work result" in work_agent.latest().rounds[0].tools[0].result_json
    else:
        assert work_agent.latest().state == "held_transport"


def test_cancellation_after_inference_prevents_first_tool(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    def cancel():
        from tinyassets.runs import request_cancel

        with sqlite3.connect(db_path(tmp_path)) as conn:
            receipt = json.loads(conn.execute(
                "SELECT record_json FROM provider_work_receipts",
            ).fetchone()[0])
        request_cancel(tmp_path, receipt["work_item_id"])

    work_agent.before_tool = cancel
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] != "completed"
    assert len(work_agent.wires) == 1 and work_agent.tools == []
    assert work_agent.latest().rounds[0].tools[0].state == "planned"


def test_member_revoked_after_known_tool_does_not_start_second_inference(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    from tinyassets.storage.outbound_connections import ConnectionLedger

    def revoke():
        ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
        ledger.revoke_grant("http_grant_" + "a" * 32)

    work_agent.after_tool = revoke
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed"
    assert len(work_agent.wires) == 1 and len(work_agent.tools) == 1
    assert "known work result" in work_agent.latest().rounds[0].tools[0].result_json


def test_no_engine_route_settles_reservation_without_inference(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    monkeypatch.setattr(engine_mcp_http, "read_engine_mcp_route", lambda **k: None)
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed"
    assert "engine_tools_unavailable" in result["terminal_error"]
    assert work_agent.wires == [] and work_agent.tools == []
    with sqlite3.connect(db_path(tmp_path)) as conn:
        rows = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations",
        )]
    assert len(rows) == 1 and rows[0]["state"] == "cancelled_before_launch"


@pytest.mark.parametrize("phase", ["adapter", "turn", "discovery", "observer"])
def test_prelaunch_failure_releases_reserved_budget(
    tmp_path, monkeypatch, authenticate_request, work_agent, phase,
):
    from tinyassets import workflow_agent

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic prelaunch failure")

    if phase == "adapter":
        monkeypatch.setattr(workflow_agent.WorkAgentAdapter, "__init__", fail)
    elif phase == "turn":
        monkeypatch.setattr(workflow_agent.WorkflowAgentTurn, "__init__", fail)
    elif phase == "observer":
        monkeypatch.setattr(workflow_agent.WorkAgentAdapter, "round_input", fail)
    else:
        work_agent.mode = "discovery_failure"
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed", result
    assert work_agent.wires == [] and work_agent.tools == []
    with sqlite3.connect(db_path(tmp_path)) as conn:
        rows = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations",
        )]
    # The single call owns one attempt; no graph-level retry can replay a
    # single-use authority after a prelaunch failure.
    assert len(rows) == 1
    assert all(row["state"] == "cancelled_before_launch" for row in rows)
    assert all(row["actual_total_tokens"] == row["actual_cost_microunits"] == 0 for row in rows)


def test_agent_rounds_cannot_exceed_existing_invocation_allowance(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    from tinyassets.providers import model_call

    open_allocation = model_call.open_allocation

    def limited_allocation(owner, universe_dir, **policy):
        return open_allocation(owner, universe_dir, **{**policy, "max_requests": 3})

    monkeypatch.setattr(model_call, "open_allocation", limited_allocation)
    work_agent.mode = "ongoing_tools"
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed", result
    assert len(work_agent.wires) == len(work_agent.tools) == 3
    with sqlite3.connect(db_path(tmp_path)) as conn:
        receipt = json.loads(conn.execute(
            "SELECT record_json FROM provider_work_receipts",
        ).fetchone()[0])
        assert conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM provider_invocation_reservations",
        ).fetchone() == (3,)
    assert receipt["max_invocations"] == 3
    assert all(tool.state == "completed"
               for step in work_agent.latest().rounds for tool in step.tools)


def test_model_without_tools_refuses_before_inference(
    tmp_path, monkeypatch, authenticate_request, work_agent,
):
    from tinyassets.providers import discovery_snapshot

    original = discovery_snapshot.read_http_discovery_document
    reads = 0

    def no_tools(**kwargs):
        nonlocal reads
        reads += 1
        document = original(**kwargs)
        # First readiness succeeds; fresh launch metadata loses capability.
        if reads > 1:
            for model in document["data"]:
                model["supported_parameters"] = []
        return document

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", no_tools)
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed", result
    assert work_agent.wires == [] and work_agent.tools == []
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM provider_invocation_reservations",
        ).fetchone() == (0,)


