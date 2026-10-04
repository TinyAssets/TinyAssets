"""Real writer/router/adapter/journal/client composition, synthetic remote wires."""

import asyncio
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
    from tinyassets.daemon_server import set_founder_home

    base = served.rig.base
    uid = served.context.universe_dir.name
    set_founder_home(base, founder_sub="owner", universe_id=uid, platform_generated=True)
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
        tool_call=("read_graph", ' {"target": "status"} '),
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
    monkeypatch.setattr(provider_calls, "_real_router", served.router)
    monkeypatch.setattr(provider_calls, "_force_mock", False)
    return state


def run(agent, observer=None, *, greeting=False):
    if greeting:
        return universe_intelligence.converse(
            founder_message="hi", universe_id=agent.served.context.universe_dir.name,
            response_observer=observer,
        )
    return universe_intelligence._call_writer(
        "exact user prompt",
        system="exact system",
        universe_context=agent.served.context,
        config=agent.config,
        response_observer=observer,
    )


def test_actual_writer_runs_tool_then_continues_and_reports_only_final_response(agent):
    receipts = []
    assert run(agent, receipts.append) == "finished exact answer"
    assert len(agent.wires) == 2 and len(agent.tools) == 1 and agent.closed
    assert agent.latest().state == "completed"
    assert len(receipts) == 1 and receipts[0].agent_reply.stop == "completed"
    assert receipts[0].reported_model == "actual-answer-model"
    messages = agent.wires[-1][1]["body"]["messages"]
    assert json.loads(messages[-1]["content"])["content"][0]["text"] == "exact result 🪐"
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute("SELECT * FROM served_provider_budget_reservations").fetchall()
        assert len(rows) == 2


def test_more_than_two_inferences_use_sealed_real_binding_allowance(agent):
    agent.requested_rounds = 3
    assert run(agent) == "finished exact answer"
    assert len(agent.wires) == 4 and len(agent.tools) == 3
    assert len(agent.latest().rounds) == 4


def test_unknown_tool_outcome_never_replayed_or_followed_by_inference(agent):
    agent.fail_tool = True
    with pytest.raises(engine_tool_client.EngineToolError, match="unknown"):
        run(agent)
    assert len(agent.wires) == len(agent.tools) == 1
    assert agent.latest().state == "held_tool_unknown"


def test_cancelled_tool_is_held_not_replayed(agent, monkeypatch):
    async def cancelled(self, name, arguments):
        raise asyncio.CancelledError

    monkeypatch.setattr(engine_tool_client.EngineToolSession, "call", cancelled)
    with pytest.raises(asyncio.CancelledError):
        run(agent)
    assert len(agent.wires) == 1 and agent.latest().state == "held_tool_unknown"


def test_claim_refusal_records_no_launched_round_and_sends_nothing(agent, monkeypatch):
    from tinyassets.auth import middleware
    from tinyassets.exceptions import ProviderAuthorityHeldError

    def refused(*args, **kwargs):
        raise PermissionError("synthetic revoked capability")

    monkeypatch.setattr(middleware, "consume_provider_request_invocation", refused)
    with pytest.raises(ProviderAuthorityHeldError):
        run(agent)
    assert not agent.wires and not agent.tools
    assert agent.latest().state == "abandoned" and not agent.latest().rounds


def test_failed_intent_commit_never_dispatches_network(agent, monkeypatch):
    from tinyassets.exceptions import AllProvidersExhaustedError

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(AgentTurnJournal, "begin_round", fail)
    with pytest.raises(AllProvidersExhaustedError):
        run(agent)
    assert not agent.wires and not agent.tools
    assert agent.latest().state == "abandoned"
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute(
            "SELECT state, actual_total_tokens, actual_cost_microunits "
            "FROM served_provider_budget_reservations"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("succeeded", 0, 0)]


def test_known_result_storage_failure_never_repeats_the_action(agent, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic result storage failure")

    monkeypatch.setattr(AgentTurnJournal, "finish_tool", fail)
    with pytest.raises(RuntimeError, match="result storage"):
        run(agent)
    assert len(agent.wires) == len(agent.tools) == 1
    assert agent.latest().rounds[-1].tools[0].state == "started"


def test_home_revoked_after_inference_refuses_tool_and_does_not_recreate_home(agent):
    from tinyassets.storage.current_home import CurrentHomeChanged

    def revoke():
        with agent.journal._ledger.connection() as conn:
            conn.execute("DELETE FROM founder_home WHERE founder_sub = 'owner'")

    agent.before_reply = revoke
    with pytest.raises(CurrentHomeChanged):
        run(agent)
    assert len(agent.wires) == 1 and not agent.tools
    assert agent.latest().state == "inference_started"


def test_agent_requires_discovered_tool_support_before_network(agent, monkeypatch):
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers import discovery_snapshot

    original = discovery_snapshot.read_http_discovery_document

    def text_only(**kwargs):
        document = original(**kwargs)
        if "benchmarks" not in kwargs["url"]:
            for model in document["data"]:
                model["supported_parameters"] = []
        return document

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", text_only)
    with pytest.raises(ProviderAuthorityHeldError):
        run(agent)
    assert not agent.wires and not agent.tools and agent.latest().state == "abandoned"


def test_unknown_inference_outcome_is_not_settled_as_zero_spend(agent):
    from tinyassets.exceptions import AllProvidersExhaustedError

    agent.unknown_inference = True
    with pytest.raises(AllProvidersExhaustedError):
        run(agent)
    assert len(agent.wires) == 1 and not agent.tools
    assert agent.latest().state == "held_transport"
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute("SELECT state FROM served_provider_budget_reservations").fetchall()
        assert [row[0] for row in rows] == ["indeterminate"]


@pytest.mark.parametrize("failure", ["claim", "intent", "history"])
def test_later_pre_intent_failure_closes_known_progress(agent, monkeypatch, failure):
    from tinyassets.auth import middleware
    from tinyassets.exceptions import AllProvidersExhaustedError, ProviderAuthorityHeldError
    from tinyassets.interactive_http_agent import InteractiveHttpAgentTurn
    from tinyassets.storage.agent_turn_journal import reset_blockers

    target, name = {
        "claim": (middleware, "consume_provider_request_invocation"),
        "intent": (AgentTurnJournal, "begin_round"),
        "history": (InteractiveHttpAgentTurn, "_history"),
    }[failure]
    original = getattr(target, name)

    def refuse_later(*args, **kwargs):
        if agent.tools:
            raise PermissionError("synthetic later pre-intent failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, refuse_later)
    expected = {
        "claim": ProviderAuthorityHeldError,
        "intent": AllProvidersExhaustedError,
        "history": PermissionError,
    }[failure]
    with pytest.raises(expected):
        run(agent)
    turn = agent.latest()
    assert turn.state == "abandoned" and len(turn.rounds) == 1
    assert len(agent.wires) == len(agent.tools) == 1
    assert turn.rounds[0].tools[0].state == "completed"
    assert "exact result" in turn.rounds[0].tools[0].result_json
    with agent.journal._ledger.connection() as conn:
        assert reset_blockers(conn, "owner", agent.served.context.universe_dir.name) == []


@pytest.mark.parametrize("status,scope", [(402, "account"), (429, "unknown"), (503, "model")])
def test_capacity_scope_survives_real_router_without_false_spend(agent, status, scope):
    from tinyassets.exceptions import AllProvidersExhaustedError

    agent.capacity_failures[1] = status
    with pytest.raises(AllProvidersExhaustedError) as error:
        run(agent)
    assert error.value.capacity_scope == scope
    assert error.value.retry_after == 60
    assert error.value.attempts[-1].capacity_scope == scope
    assert len(agent.wires) == 1 and not agent.tools
    assert agent.latest().state == "held_transport"
    provider = agent.served.context.model_selection.connection_id
    remaining = agent.served.router._quota.cooldown_remaining(provider)
    # A source-wide cooldown needs proof the source is unhealthy. ``model``
    # scope never had it; ``unknown`` scope on a source that cannot spend no
    # longer counts as it either, because cooling the connection also skips the
    # sibling model the turn would try next (live 2026-09-25).
    assert (remaining > 0) == (scope == "account")
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute(
            "SELECT state, actual_total_tokens, actual_cost_microunits "
            "FROM served_provider_budget_reservations"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("succeeded", 0, 0)]


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


def test_model_capacity_continues_known_tools_without_replay(agent, monkeypatch):
    alternate = _with_fallback(agent, monkeypatch)
    agent.capacity_failures[2] = 503
    assert run(agent) == "finished exact answer"
    assert len(agent.wires) == 3 and len(agent.tools) == 1
    turn = agent.latest()
    assert turn.state == "completed" and turn.policy_generation == 7
    assert turn.policy_source == "saved"
    assert [round.state for round in turn.rounds] == ["received", "failed", "received"]
    body = agent.wires[-1][1]["body"]
    assert body["model"] == alternate
    assert json.loads(body["messages"][-1]["content"])["content"][0]["text"] == "exact result 🪐"
    assert all(
        set(wire[1]["body"]["provider"]["max_price"].values()) == {"0"}
        for wire in agent.wires
    )
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute(
            "SELECT actual_total_tokens FROM served_provider_budget_reservations"
        ).fetchall()
        assert len(rows) == 3
        assert [row[0] for row in rows].count(0) == 1


def test_account_capacity_skips_sibling_models_and_keeps_known_results(agent, monkeypatch):
    """Exhausted credit is the ACCOUNT, and about money: no sibling is tried.

    429 used to be asserted here too. Its scope is ``unknown``, not ``account``,
    and on a source that cannot spend the turn now narrows it to the model that
    failed -- tests/test_free_model_sibling_retry.py owns that case.
    """
    from tinyassets.exceptions import AllProvidersExhaustedError

    _with_fallback(agent, monkeypatch)
    agent.capacity_failures[2] = 402
    with pytest.raises(AllProvidersExhaustedError):
        run(agent)
    assert len(agent.wires) == 2 and len(agent.tools) == 1
    assert agent.latest().rounds[0].tools[0].state == "completed"


def test_explicit_empty_fallback_stays_empty(agent, monkeypatch):
    from tinyassets.exceptions import AllProvidersExhaustedError

    _with_fallback(agent, monkeypatch, empty=True)
    agent.capacity_failures[1] = 503
    with pytest.raises(AllProvidersExhaustedError):
        run(agent)
    assert len(agent.wires) == 1 and not agent.tools


def test_replacement_revalidates_revoked_discovery_authority(agent, monkeypatch):
    from tinyassets.exceptions import ProviderAuthorityHeldError

    _with_fallback(agent, monkeypatch)
    agent.capacity_failures[2] = 503
    agent.on_capacity = lambda: agent.served.rig.ledger.revoke_grant("grant-models")
    with pytest.raises(ProviderAuthorityHeldError):
        run(agent)
    assert len(agent.wires) == 2 and len(agent.tools) == 1
    assert agent.latest().state == "held_transport"


def test_paid_replacement_is_rejected_after_fresh_discovery(agent, monkeypatch):
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers import discovery_snapshot

    alternate = _with_fallback(agent, monkeypatch)
    original = discovery_snapshot.read_http_discovery_document

    def withdrawn(**kwargs):
        value = original(**kwargs)
        if len(agent.wires) >= 2 and "models/user" in kwargs["url"]:
            for model in value["data"]:
                if model["id"] == alternate:
                    model["pricing"]["prompt"] = "0.001"
        return value

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", withdrawn)
    agent.capacity_failures[2] = 503
    with pytest.raises(ProviderAuthorityHeldError):
        run(agent)
    assert len(agent.wires) == 2 and len(agent.tools) == 1


def test_advisory_plan_cannot_cross_owner_boundary(agent, monkeypatch):
    _with_fallback(agent, monkeypatch)
    plan = agent.served.context.agent_model_plan
    agent.served.context = replace(
        agent.served.context, agent_model_plan=replace(
            plan, catalog=replace(plan.catalog, owner_id="another-owner"),
        ),
    )
    with pytest.raises(ValueError, match="scope mismatch"):
        run(agent)
    assert not agent.wires and not agent.tools


@pytest.mark.parametrize("unknown", ["inference", "tool"])
def test_fallback_plan_does_not_replay_unknown_outcomes(agent, monkeypatch, unknown):
    from tinyassets.exceptions import AllProvidersExhaustedError

    _with_fallback(agent, monkeypatch)
    agent.unknown_inference = unknown == "inference"
    agent.fail_tool = unknown == "tool"
    expected = (
        AllProvidersExhaustedError
        if unknown == "inference"
        else engine_tool_client.EngineToolError
    )
    with pytest.raises(expected):
        run(agent)
    assert len(agent.wires) == 1
    assert len(agent.tools) == (unknown == "tool")


def test_conflicting_plan_and_incoming_selection_refuses_before_launch(agent, monkeypatch):
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.model_policy import ModelRef

    alternate = _with_fallback(agent, monkeypatch)
    agent.served.context = replace(
        agent.served.context,
        model_selection=ModelRef(agent.served.context.model_selection.connection_id, alternate),
    )
    with pytest.raises(ProviderAuthorityHeldError, match="contradicts"):
        run(agent)
    assert not agent.wires and not agent.tools


def test_the_journal_records_the_agent_the_context_was_built_for(agent):
    """Harness §4.18: the per-agent journal must say WHICH agent ran the turn.

    The ``agent_turns.agent_id`` column has been per-agent since #4228, but the
    served adapter left it at its ``main`` default, so a custom agent's turn was
    recorded as main -- and the per-agent journal, the status projection and the
    owner's history all read the wrong agent.

    The value comes from ``UniverseContext.agent_id``, which authenticated
    ingress sets, and from nowhere else.
    """
    universe_intelligence._call_writer(
        "exact user prompt",
        system="exact system",
        universe_context=replace(agent.served.context, agent_id="a-weaver"),
        config=agent.config,
    )
    turn = agent.latest()
    assert turn.state == "completed"
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute("SELECT turn_id, agent_id FROM agent_turns").fetchall()
    assert [row[1] for row in rows] == ["a-weaver"], (
        f"the journal recorded {[r[1] for r in rows]!r}, not the addressed agent")


def test_a_context_with_no_addressed_agent_still_records_main(agent):
    """The default is main only because ingress had no addressed agent."""
    assert agent.served.context.agent_id == "main", "the rig's context is not the main case"
    run(agent)
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute("SELECT agent_id FROM agent_turns").fetchall()
    assert [row[0] for row in rows] == ["main"]


def test_the_context_is_the_only_source_of_the_agent(agent, monkeypatch):
    """Not the Stop registry, and not a session key.

    A live turn registered under a DIFFERENT agent must not change what the
    journal records: ``turn_interrupt`` is in-process state a workflow-node turn
    does not have, so reading it would make attribution depend on whether a Stop
    happened to be registrable. The context is set at ingress; nothing else gets
    a vote.
    """
    from tinyassets import addressed_agents, engine_steering, turn_interrupt

    uid = agent.served.context.universe_dir.name
    # A session key that NAMES a different agent, so "not a session key" is
    # actually exercised rather than only asserted in prose: a
    # session-first/context-fallback implementation would pass without this
    # (Codex refute of this PR, finding E).
    monkeypatch.setattr(
        engine_steering, "_session_key",
        lambda: f"thread:{addressed_agents.memory_session('owner', 'a-someone-else')}",
    )
    with turn_interrupt.interactive_turn("owner", uid, agent_id="a-someone-else"):
        universe_intelligence._call_writer(
            "exact user prompt",
            system="exact system",
            universe_context=replace(agent.served.context, agent_id="a-weaver"),
            config=agent.config,
        )
    with agent.journal._ledger.connection() as conn:
        rows = conn.execute("SELECT agent_id FROM agent_turns").fetchall()
    assert [row[0] for row in rows] == ["a-weaver"], (
        "the journal followed the Stop registry instead of the context")


def test_context_keeps_existing_positional_provider_fields(tmp_path):
    """Adding attribution must not rebind an existing positional carrier."""
    from tinyassets.providers.base import UniverseContext

    config, invocation, request, served, selection, plan = (object() for _ in range(6))
    context = UniverseContext(tmp_path, config, invocation, request, served, selection, plan)
    assert context.universe_dir == tmp_path
    assert context.config is config
    assert context.provider_invocation is invocation
    assert context.provider_request is request
    assert context.served_provider is served
    assert context.model_selection is selection
    assert context.agent_model_plan is plan
    assert context.agent_id == "main"
    assert replace(context, agent_id="a-weaver").agent_id == "a-weaver"
