"""Real writer/router/adapter/journal/client composition, synthetic remote wires."""

import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from tests import test_selected_model_authority as authority_tests
from tests.inference_usage_helpers import accounting_resolver
from tinyassets import engine_mcp_http, engine_tool_client, universe_intelligence
from tinyassets.providers import call as provider_calls
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS
from tinyassets.storage.agent_turn_journal import AgentTurnJournal

rig = authority_tests.rig
reader = authority_tests.reader
served = authority_tests.served


@pytest.fixture
def agent(served, monkeypatch):
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home

    base = served.rig.base
    uid = served.context.universe_dir.name
    set_founder_home(base, founder_sub="owner", universe_id=uid, platform_generated=True)
    grant_universe_ownership(base, universe_id=uid, owner_id="owner")
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    from tests.engine_authority_helpers import seed_engine_authority
    seed_engine_authority(base, actor="owner", graph=uid)
    engine_mcp_http._write_routes(
        base,
        [
            SimpleNamespace(
                universe_id=uid,
                owner="owner",
                port=8790,
                secret="s" * 43,
            )
        ],
    )
    journal = AgentTurnJournal(base)

    def latest():
        with journal._ledger.connection() as conn:
            row = conn.execute(
                "SELECT turn_id FROM agent_turns ORDER BY created_at DESC"
            ).fetchone()
        return journal.get("owner", uid, row[0])

    state = SimpleNamespace(
        served=served,
        journal=journal,
        latest=latest,
        wires=[],
        tools=[],
        requested_rounds=1,
        first_text=None,
        tools_per_round=1,
        fail_tool=False,
        closed=False,
        before_reply=None,
        unknown_inference=False,
        capacity_failures={},
        failure_bodies={},
        on_capacity=None,
        tool_call=("bash", json.dumps({
            "command": "ta call read_graph --json '{\"target\":\"status\"}'"})),
        config=ModelConfig(
            engine_mcp_enabled=True,
            engine_mcp_actor_id="owner",
            engine_mcp_graph_id=uid,
            max_tokens=1024,
            absolute_cap_s=120,
        ),
    )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            state.closed = True

        def is_connected(self):
            return not state.closed

        async def list_tools_mcp(self, *, cursor=None):
            return ListToolsResult(
                tools=[
                    Tool(name=name, inputSchema={"type": "object"})
                    for name in SERVED_ENGINE_MCP_TOOLS
                ]
            )

        async def call_tool_mcp(self, name, arguments):
            assert any(tool.state == "started" for tool in latest().rounds[-1].tools)
            state.tools.append((name, arguments))
            if state.fail_tool:
                raise RuntimeError("synthetic post-dispatch disconnect")
            return CallToolResult(content=[TextContent(type="text", text="exact result 🪐")])

        async def read_resource(self, uri):
            # The private ta bridge: this owner has no installed extensions.
            assert uri.startswith("ta-bridge://request/")
            payload = uri.removeprefix("ta-bridge://request/").encode()
            assert json.loads(base64.urlsafe_b64decode(payload)) == {"deliver": "extensions"}
            empty = {"extensions": [], "undelivered": []}
            return [SimpleNamespace(text=json.dumps(empty))]

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            learning = any(
                message.get("role") == "system"
                and message.get("content") == universe_intelligence._LEARNING_SYSTEM
                for message in document["body"]["messages"]
            )
            if not learning:
                assert latest().state == "inference_started"
                assert latest().rounds[-1].candidate.reservation_id
            state.wires.append((verb, document))
            if state.unknown_inference:
                return {"error": "synthetic post-dispatch disconnect"}
            if len(state.wires) in state.capacity_failures:
                if state.on_capacity is not None:
                    state.on_capacity()
                return {
                    "status": state.capacity_failures[len(state.wires)],
                    "headers": {"retry-after": "60"},
                    "body": state.failure_bodies.get(
                        len(state.wires), '{"error":{"message":"synthetic refusal"}}',
                    ),
                }
            if state.before_reply is not None:
                state.before_reply()
            tools = (not learning and bool(document["body"].get("tools"))
                     and len(state.wires) <= state.requested_rounds
                     and document["body"].get("tool_choice") != "none")
            message = {
                "role": "assistant",
                "content": "{}" if learning else None if tools else "finished exact answer",
            }
            if tools:
                if len(state.wires) == 1:
                    message["content"] = state.first_text
                message["tool_calls"] = [
                    {
                        "id": "same-wire-id" if index == 0 else f"wire-id-{index}",
                        "type": "function",
                        "function": {
                            "name": state.tool_call[0],
                            "arguments": state.tool_call[1],
                        },
                    }
                    for index in range(state.tools_per_round)
                ]
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "model": "actual-answer-model",
                        "choices": [
                            {"message": message, "finish_reason": "tool_calls" if tools else "stop"}
                        ],
                    }
                ),
            }

    monkeypatch.setattr(engine_tool_client, "_make_client", lambda *_: Client())
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *args, **kwargs: Proxy()))
    monkeypatch.setattr(provider_calls, "_router", served.router)
    # Coordinator contract tests inject the engine adapter explicitly. Production
    # construction always chooses ThinLoopChatAdapter; thin integration fixtures
    # replace state.adapter with it and bind a scripted box before calling run().
    from tinyassets.agent_loop import served_chat
    from tinyassets.agent_turn_coordinator import start_coordinator
    from tinyassets.interactive_http_agent import ServedChatAgentAdapter
    from tinyassets.turn_interrupt import current

    state.adapter = ServedChatAgentAdapter()

    def make_turn(**kwargs):
        provider_calls.register_open_providers_for(kwargs["universe_context"])
        adapter = kwargs.pop("adapter", state.adapter)
        return start_coordinator(
            adapter=adapter, router=served.router, interrupt=current(), **kwargs,
        )

    state.factory = served_chat.open_served_turn
    monkeypatch.setattr(served_chat, "open_served_turn", make_turn)
    return state


def run(agent, observer=None, *, greeting=False):
    if greeting:
        return universe_intelligence.converse(
            founder_message="hi", universe_id=agent.served.context.universe_dir.name,
            response_observer=observer,
        )
    from tinyassets.agent_loop.served_chat import run_served_writer

    return run_served_writer(
        "exact user prompt",
        system="exact system",
        universe_context=agent.served.context,
        config=agent.config,
        response_observer=observer,
    )




























def _with_fallback(agent, monkeypatch, *, empty=False):
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import Catalog, ModelPolicy, ModelRef

    original = discovery_snapshot.read_http_discovery_document
    alternate = "future-vendor/another-model"

    def added(**kwargs):
        value = original(**kwargs)
        if "models/user" in kwargs["url"]:
            value["data"].append(authority_tests.snapshot_tests._model(alternate))
        return value

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", added)
    snapshot = authority_tests.snapshot_tests._refresh(agent.served.rig)
    selected = agent.served.context.model_selection
    plan = AgentModelPlan(
        Catalog("owner", agent.served.context.universe_dir.name, (snapshot.models,)),
        ModelPolicy(
            generation=7, mode="explicit", saved_default=selected,
            fallbacks=() if empty else (ModelRef(selected.connection_id, alternate),),
        ),
        replace(
            discovery_protocol(snapshot.models.provider_scope).text_interaction, needs_tools=True,
        ),
        policy_source="saved",
    )
    agent.served.context = replace(agent.served.context, agent_model_plan=plan)
    return alternate
