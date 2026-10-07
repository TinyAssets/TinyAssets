"""Synthetic dispatches prove a turn budget; daily account balances stay unknown."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests import test_interactive_http_agent as integration
from tests import test_turn_request_economy as economy
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.agent_inference import AgentInferenceRequest
from tinyassets.providers.model_selection import SelectedModel
from tinyassets.request_budget import (
    RequestBudgetExceeded,
    TurnRequestBudget,
    current_request_budget,
    request_budget_scope,
    selection_is_free,
)

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent


def run_coordinator(agent):
    from tinyassets.providers.call import call_interactive_agent_turn, make_interactive_agent_turn

    turn = make_interactive_agent_turn(
        prompt="exact user prompt", system="exact system",
        universe_context=agent.served.context, config=agent.config,
    )
    return call_interactive_agent_turn(turn)


def reserve(budget, *, source="accepted-a", free=True, purpose="reply"):
    return budget.reserve(owner="owner", universe="u-models", source_ref=source,
                          model="model", free=free, purpose=purpose)


def dispatch(budget, **kwargs):
    ordinal = reserve(budget, **kwargs)
    budget.dispatched(ordinal)
    return ordinal


def test_failures_helpers_reviews_fallbacks_share_one_parent_limit():
    budget = TurnRequestBudget("owner", "u-models", max_requests=4)
    for source, purpose, outcome in [
        ("accepted-a", "reply", "failed"),
        ("accepted-b", "reply", "succeeded"),
        ("accepted-b", "helper", "succeeded"),
        ("accepted-a", "review", "failed"),
    ]:
        ordinal = dispatch(budget, source=source, purpose=purpose)
        budget.settle(ordinal, outcome)
    with pytest.raises(RequestBudgetExceeded) as held:
        dispatch(budget, source="accepted-third-account")
    receipt = held.value.request_receipt
    assert receipt["dispatched"] == 4
    assert receipt["quota_authoritative"] is False
    assert [(s["source_ref"], s["purpose"], s["failed"]) for s in receipt["sources"]] == [
        ("accepted-a", "reply", 1), ("accepted-b", "reply", 0),
        ("accepted-b", "helper", 0), ("accepted-a", "review", 1),
    ]
    assert "none is scheduled automatically" in held.value.continuation


def test_free_learning_is_denied_before_any_reservation_even_with_room():
    budget = TurnRequestBudget("owner", "u-models")
    with pytest.raises(RequestBudgetExceeded, match="request budget") as held:
        dispatch(budget, purpose="learning")
    assert held.value.reason == "automatic_learning_disabled"
    assert budget.receipt()["reserved"] == 0
    # Paid policy is explicitly separate, never changed by the free allowance.
    ordinal = dispatch(budget, free=False, purpose="learning")
    budget.settle(ordinal, "succeeded")
    assert budget.receipt()["dispatched"] == 1


def test_repeated_failed_dispatches_stop_without_exhausting_catalogue():
    budget = TurnRequestBudget("owner", "u-models")
    for source in ("account-a", "account-a"):
        budget.settle(dispatch(budget, source=source), "failed")
    with pytest.raises(RequestBudgetExceeded) as held:
        dispatch(budget, source="account-a")
    assert held.value.reason == "consecutive_failures"
    assert budget.receipt()["dispatched"] == 2
    assert dispatch(budget, source="accepted-fallback") == 3


def test_progress_resets_failure_streak_but_does_not_reset_total():
    budget = TurnRequestBudget("owner", "u-models")
    for outcome in ("failed", "succeeded", "failed", "succeeded", "failed", "succeeded"):
        budget.settle(dispatch(budget), outcome)
    with pytest.raises(RequestBudgetExceeded) as held:
        dispatch(budget)
    assert held.value.reason in {"free_attempt_limit", "free_pool_attempt_limit"}


def test_new_dispatch_deadline_does_not_cancel_valid_progressing_reply():
    clock = [1.0]
    budget = TurnRequestBudget("owner", "u-models", deadline=2.0, clock=lambda: clock[0])
    ordinal = dispatch(budget)
    clock[0] = 3.0
    budget.settle(ordinal, "succeeded")
    with pytest.raises(RequestBudgetExceeded) as held:
        dispatch(budget)
    assert held.value.reason == "dispatch_deadline"
    assert budget.receipt()["sources"][0]["succeeded"] == 1


def test_closed_parent_prevents_reserved_child_from_dispatching():
    budget = TurnRequestBudget("owner", "u-models")
    ordinal = reserve(budget)
    budget.close()
    with pytest.raises(RequestBudgetExceeded):
        budget.dispatched(ordinal)
    assert budget.receipt()["dispatched"] == 0
    assert budget.receipt()["sources"][0]["not_sent"] == 1


def test_thread_reservations_are_atomic_and_independent_turns_do_not_share_limits():
    budget = TurnRequestBudget("owner", "u-models", max_requests=3)

    def attempt(_):
        try:
            return dispatch(budget)
        except RequestBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(40)))
    assert len([v for v in results if v is not None]) == 3
    assert budget.receipt()["dispatched"] == 3
    other = TurnRequestBudget("owner", "u-models", max_requests=3)
    assert dispatch(other) == 1


def test_context_flows_to_children_and_cannot_be_reset():
    budget = TurnRequestBudget("owner", "u-models")

    async def child():
        assert current_request_budget() is budget
        assert await asyncio.to_thread(current_request_budget) is budget

    async def parent():
        with request_budget_scope(budget):
            await asyncio.create_task(child())
            with pytest.raises(ProviderAuthorityHeldError):
                with request_budget_scope(TurnRequestBudget("owner", "u-models")):
                    pytest.fail("nested budget replacement admitted")
        assert current_request_budget() is None

    asyncio.run(parent())


def test_foreign_scope_and_unlaunched_calls_do_not_spend_or_grant_authority():
    budget = TurnRequestBudget("owner", "u-models", max_requests=1)
    with pytest.raises(ProviderAuthorityHeldError):
        budget.reserve(owner="other", universe="u-models", source_ref="foreign",
                       model="model", free=True)
    ordinal = reserve(budget)
    budget.settle(ordinal, "not_sent")
    assert dispatch(budget) == 2
    assert budget.receipt()["dispatched"] == 1


def test_paid_dispatch_policy_is_unchanged_and_unknown_price_is_not_free():
    budget = TurnRequestBudget("owner", "u-models")
    for _ in range(16):
        budget.settle(dispatch(budget, free=False), "succeeded")
    assert budget.receipt()["dispatched"] == 16
    assert not selection_is_free(None)
    assert not selection_is_free(SimpleNamespace(cost_caps=()))
    assert not selection_is_free(SimpleNamespace(cost_caps=(("request_usd", 1),)))
    assert selection_is_free(SimpleNamespace(cost_caps=(("request_usd", 0),)))


def test_text_only_encoding_needs_no_tool_capability_or_wire_fields():
    from tinyassets.providers.discovery_protocols import discovery_protocol

    contract = discovery_protocol("openrouter_user_models_v1")
    selected = SelectedModel("accepted", "model", "openrouter_user_models_v1",
                             tuple((name, 0) for name in contract.price_components),
                             "digest", 10000, supports_tools=False)
    request = AgentInferenceRequest(tools=(), tool_choice="none")
    _, body = request.encode(prompt="hi", system="", selection=selected,
                             temperature=None, max_tokens=100)
    assert "tools" not in body and "tool_choice" not in body
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["stream"] is True
    with pytest.raises(PermissionError):
        AgentInferenceRequest(tools=({"type": "function", "function": {
            "name": "read", "description": "", "parameters": {"type": "object"},
        }},)).encode(prompt="hi", system="", selection=selected,
                     temperature=None, max_tokens=100)


def test_real_coordinator_budget_stops_before_extra_tool_effect(agent):
    budget = TurnRequestBudget("owner", "u-models", max_requests=3)
    agent.requested_rounds = 20
    with request_budget_scope(budget), pytest.raises(RequestBudgetExceeded) as held:
        run_coordinator(agent)
    assert len(agent.wires) == 3
    assert len(agent.tools) == 2
    assert agent.latest().state == "held_tool_not_sent"
    assert held.value.completed_tools == tuple(name for name, _ in agent.tools)
    assert held.value.turn_requests == 3
    assert budget.receipt()["dispatched"] == 3


def test_real_text_only_coordinator_one_call_opens_no_tool_session(agent, monkeypatch):
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    def denied(*args, **kwargs):
        pytest.fail("text-only chat opened tools")

    monkeypatch.setattr(AgentTurnCoordinator, "_open_tools", denied)
    agent.requested_rounds = 0
    agent.config = replace(agent.config,
                           agent_request=AgentInferenceRequest(tools=(), tool_choice="none"))
    budget = TurnRequestBudget("owner", "u-models", max_requests=3)
    with request_budget_scope(budget):
        assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) == 1 and not agent.tools
    assert "tools" not in agent.wires[0][1]["body"]
    assert budget.receipt()["sources"][0]["purpose"] == "reply"


def test_source_allocations_preserve_paid_local_and_other_free_capacity():
    budget = TurnRequestBudget("owner", "u-models", source_limits={"free-a": 2})
    for _ in range(2):
        budget.settle(dispatch(budget, source="free-a"), "succeeded")
    with pytest.raises(RequestBudgetExceeded):
        dispatch(budget, source="free-a")
    for _ in range(40):
        budget.settle(dispatch(budget, source="paid", free=False), "succeeded")
        budget.settle(dispatch(budget, source="local", free=False), "succeeded")
    budget.settle(dispatch(budget, source="free-b"), "succeeded")
    assert budget.receipt()["dispatched"] == 83


def test_explicit_larger_source_allocation_preserves_long_useful_work():
    budget = TurnRequestBudget("owner", "u-models", source_limits={"accepted-a": 20},
                               free_pool_limit=20)
    for _ in range(16):
        budget.settle(dispatch(budget), "succeeded")
    assert budget.receipt()["dispatched"] == 16


def test_real_coordinator_close_race_records_no_dispatch_and_keeps_typed_stop(agent, monkeypatch):
    budget = TurnRequestBudget("owner", "u-models")
    begin = agent.journal.__class__.begin_round

    def close_after_journal(self, *args, **kwargs):
        result = begin(self, *args, **kwargs)
        budget.close()
        return result

    monkeypatch.setattr(agent.journal.__class__, "begin_round", close_after_journal)
    with request_budget_scope(budget), pytest.raises(RequestBudgetExceeded) as held:
        run_coordinator(agent)
    assert held.value.reason == "dispatch_closed"
    assert not agent.wires and not agent.tools
    assert agent.latest().state == "held_transport"
    assert budget.receipt()["sources"][0]["not_sent"] == 1
    assert held.value.turn_requests == 0
    import sqlite3

    from tinyassets.storage import DB_FILENAME

    with sqlite3.connect(agent.served.context.universe_dir.parent / DB_FILENAME) as db:
        states = db.execute(
            "SELECT state, actual_total_tokens, actual_cost_microunits "
            "FROM served_provider_budget_reservations"
        ).fetchall()
    # The existing monetary ledger represents release as settled zero usage.
    assert states and all(row == ("succeeded", 0, 0) for row in states)


def test_real_claim_denial_never_reserves_request(agent, monkeypatch):
    from tinyassets.auth import middleware

    def denied(*args, **kwargs):
        raise PermissionError("synthetic admission denial")

    monkeypatch.setattr(middleware, "consume_provider_request_invocation", denied)
    budget = TurnRequestBudget("owner", "u-models")
    with request_budget_scope(budget), pytest.raises(ProviderAuthorityHeldError):
        integration.run(agent)
    assert not agent.wires and not agent.tools
    assert budget.receipt()["reserved"] == 0


def test_real_cancelled_tool_keeps_one_request_and_no_automatic_followup(agent, monkeypatch):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(integration.engine_tool_client.EngineToolSession, "call", cancelled)
    budget = TurnRequestBudget("owner", "u-models")
    with request_budget_scope(budget), pytest.raises(asyncio.CancelledError):
        integration.run(agent)
    assert len(agent.wires) == 1 and not agent.tools
    assert budget.receipt()["sources"][0]["succeeded"] == 1
    assert agent.latest().state == "held_tool_unknown"


def test_real_unmetered_progress_is_not_limited_by_free_defaults(agent):
    # Synthetic host has no installed metered free allowance; zero price alone
    # must not turn a locally hosted model into a six-request account.
    agent.requested_rounds = 20
    assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) == 21 and len(agent.tools) == 20


def test_real_metered_free_turn_stops_at_source_allocation(agent, monkeypatch):
    economy.seed_budget(agent, monkeypatch, remaining=50)
    agent.requested_rounds = 20
    with pytest.raises(RequestBudgetExceeded) as held:
        run_coordinator(agent)
    assert len(agent.wires) == 6 and len(agent.tools) == 5
    assert held.value.reason in {"free_attempt_limit", "free_pool_attempt_limit"}
    assert agent.latest().state == "held_tool_not_sent"


def test_real_larger_owner_allocation_crosses_advisory_daily_zero(agent, monkeypatch):
    economy.seed_budget(agent, monkeypatch, remaining=0)
    source = agent.served.context.model_selection.connection_id
    budget = TurnRequestBudget("owner", "u-models", source_limits={source: 20}, free_pool_limit=20)
    agent.requested_rounds = 15
    with request_budget_scope(budget):
        assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) == 16 and len(agent.tools) == 15
    assert budget.receipt()["dispatched"] == 16


@pytest.mark.parametrize("identity_known", [False, True])
def test_real_mixed_pool_keeps_long_progress_on_accepted_unmetered_fallback(
    agent, monkeypatch, identity_known,
):
    import tinyassets.request_budget as budgets

    economy.seed_budget(agent, monkeypatch, remaining=50)
    first, second = economy.add_second_source(agent, monkeypatch)
    if not identity_known:
        plan = agent.served.context.agent_model_plan
        plan = replace(plan, catalog=replace(plan.catalog, connections=tuple(
            replace(connection, authenticated_account_id=None)
            for connection in plan.catalog.connections
        )))
        agent.served.context = replace(agent.served.context, agent_model_plan=plan)
    original = budgets._source_budget_facts
    monkeypatch.setattr(budgets, "_source_budget_facts", lambda ctx, **kw:
                        None if ctx.model_selection.connection_id == second.connection_id
                        else original(ctx, **kw))
    budget = TurnRequestBudget("owner", "u-models")
    agent.requested_rounds = 20
    with request_budget_scope(budget):
        assert integration.run(agent) == "finished exact answer"
    refs = [row.candidate.source_ref for row in agent.latest().rounds]
    assert refs == [first.connection_id] * 6 + [second.connection_id] * 15
    assert len(agent.tools) == 20
    assert budget.receipt()["dispatched"] == 21


def test_real_reply_retry_has_reply_purpose_and_counts_failed_http(agent, monkeypatch):
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    monkeypatch.setattr(AgentTurnCoordinator, "BAD_REPLY_BACKOFF_S", (0.0, 0.0))
    agent.requested_rounds = 0
    agent.capacity_failures[1] = 200
    agent.failure_bodies[1] = '{"error":{"code":504,"message":"Synthetic idle timeout"}}'
    budget = TurnRequestBudget("owner", "u-models")
    with request_budget_scope(budget):
        assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) == 2 and not agent.tools
    groups = budget.receipt()["sources"]
    assert len(groups) == 1
    assert groups[0]["purpose"] == "reply"
    assert groups[0]["dispatched"] == 2
    assert groups[0]["failed"] == 1 and groups[0]["succeeded"] == 1


def test_real_repeated_gateway_504_never_sweeps_catalogue(agent):
    from tinyassets.exceptions import AllProvidersExhaustedError

    agent.requested_rounds = 0
    agent.capacity_failures.update(dict.fromkeys(range(1, 10), 504))
    budget = TurnRequestBudget("owner", "u-models")
    with request_budget_scope(budget), pytest.raises(AllProvidersExhaustedError):
        integration.run(agent)
    assert len(agent.wires) <= 2 and not agent.tools
    assert budget.receipt()["dispatched"] == len(agent.wires)
    assert sum(row["failed"] for row in budget.receipt()["sources"]) == len(agent.wires)


@pytest.mark.parametrize("mode", ["explicit", "automatic"])
def test_accepted_same_connection_paid_candidate_remains_eligible(agent, monkeypatch, mode):
    import tinyassets.agent_turn_coordinator as coordinator

    alternate = integration._with_fallback(agent, monkeypatch)
    context = agent.served.context
    plan = replace(context.agent_model_plan, policy=replace(
        context.agent_model_plan.policy, mode=mode,
    ))
    context = replace(context, agent_model_plan=plan)
    turn = coordinator.AgentTurnCoordinator(
        adapter=SimpleNamespace(), router=None, prompt="", system="",
        universe_context=context, config=agent.config,
    )
    turn.owner = "owner"
    turn.request_budget = TurnRequestBudget("owner", "u-models", free_limit=1)
    source = context.model_selection.connection_id
    turn.request_budget.settle(dispatch(turn.request_budget, source=source), "succeeded")
    # Admission remains the router's job. This fixture represents a second
    # accepted model whose paid policy does not consume the free allocation.
    monkeypatch.setattr(coordinator, "candidate_is_metered_free", lambda ctx, catalog, **kw:
                        ctx.model_selection.model_id != alternate)
    candidate = turn._request_budget_fallback()
    assert candidate.model_id == alternate and candidate.connection_id == source
    assert turn.exhaustion == ()


def test_no_tool_fallback_when_other_source_allocation_is_already_spent(agent, monkeypatch):
    import tinyassets.agent_turn_coordinator as coordinator

    economy.seed_budget(agent, monkeypatch, remaining=50)
    first, second = economy.add_second_source(agent, monkeypatch)
    turn = coordinator.AgentTurnCoordinator(
        adapter=SimpleNamespace(), router=None, prompt="", system="",
        universe_context=agent.served.context, config=agent.config,
    )
    turn.owner = "owner"
    turn.request_budget = TurnRequestBudget("owner", "u-models", free_limit=1)
    for source in (first.connection_id, second.connection_id):
        turn.request_budget.settle(dispatch(turn.request_budget, source=source), "succeeded")
    assert turn._request_budget_fallback() is None


def test_receipt_buckets_by_actual_dispatch_time_instead_of_parent_creation():
    from datetime import datetime, timezone

    clock = [datetime(2026, 10, 3, 23, 59, tzinfo=timezone.utc)]
    budget = TurnRequestBudget("owner", "u-models", wall_clock=lambda: clock[0])
    budget.settle(dispatch(budget), "succeeded")
    clock[0] = datetime(2026, 10, 4, 0, 1, tzinfo=timezone.utc)
    budget.settle(dispatch(budget), "failed")
    assert [row["dispatched_at"][:10] for row in budget.receipt()["attempts"]] == [
        "2026-10-03", "2026-10-04",
    ]


@pytest.mark.parametrize("message", ["hi", "What is a tuple?", "Explain recursion"])
@pytest.mark.parametrize("choice", [None, "explicit"])
@pytest.mark.parametrize("transport", ["synthetic", "broker"])
def test_ordinary_chat_uses_one_tool_incapable_accepted_http_call(
    agent, monkeypatch, signed_in, message, choice, transport,
):
    from tinyassets import daemon_server, universe_intelligence
    from tinyassets.providers import discovery_snapshot

    root = agent.served.context.universe_dir
    if transport == "broker":
        from tests.inference_usage_helpers import broker_accounting_resolver
        from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

        monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                            broker_accounting_resolver(ApiKeyHttpProvider._resolve_proxy))
    monkeypatch.setattr(daemon_server, "get_founder_home", economy.get_founder_home)
    monkeypatch.setattr(universe_intelligence, "_universe_dir", lambda uid: root)
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    original = discovery_snapshot.read_http_discovery_document

    def text_models(**kwargs):
        document = original(**kwargs)
        if "benchmarks" not in kwargs["url"]:
            for model in document["data"]:
                model["supported_parameters"] = []
        return document

    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", text_models)
    signed_in("owner")
    model_choice = None
    if choice:
        from tinyassets.storage.model_preferences import ModelPreferences

        model_choice = ModelPreferences(
            "explicit", agent.served.context.model_selection, (),
        ).document()
    observations = []
    answer = universe_intelligence.converse(
        founder_message=message, universe_id=root.name, model_choice=model_choice,
        response_observer=observations.append,
        conversation_history=[{"role": "assistant", "content": "An old project is unfinished."}],
    )
    assert answer == "finished exact answer"
    assert len(agent.wires) == 1 and not agent.tools
    assert not agent.closed, "no engine tool session was opened"
    assert "tools" not in agent.wires[0][1]["body"]
    assert observations[0].request_receipt["dispatched"] == 1
    assert observations[0].request_receipt["sources"][0]["purpose"] == "reply"
    assert agent.latest().state == "completed"
    from tinyassets.storage.agent_request_usage import UsageStore

    stored = UsageStore(root.parent).for_subject("owner", root.name, "turn", agent.latest().turn_id)
    assert len(stored) == 1 and stored[0]["dispatched"] == 1 and stored[0]["closed"]
    assert stored[0]["usage_id"] == observations[0].request_receipt["usage_id"]
    assert [(a["purpose"], a["state"]) for a in stored[0]["attempts"]] == [
        ("reply", "succeeded"),
    ]


def test_free_pool_cannot_multiply_default_budget_across_fifty_accepted_sources():
    budget = TurnRequestBudget("owner", "u-models")
    calls = []
    for number in range(50):
        try:
            ordinal = dispatch(budget, source=f"accepted-{number}")
        except RequestBudgetExceeded:
            continue
        calls.append(number)
        budget.settle(ordinal, "failed")
    assert len(calls) == 6
    assert budget.receipt()["dispatched"] == 6
    # Exhausted free allocation is not a global cap on accepted richer capacity.
    for _ in range(30):
        budget.settle(dispatch(budget, source="local", free=False), "succeeded")
    assert budget.receipt()["dispatched"] == 36


def test_whole_parent_receipt_includes_learning_when_unmetered_work_requests_it(
    agent, monkeypatch, signed_in,
):
    from tinyassets import daemon_server, universe_intelligence

    root = agent.served.context.universe_dir
    monkeypatch.setattr(daemon_server, "get_founder_home", economy.get_founder_home)
    monkeypatch.setattr(universe_intelligence, "_universe_dir", lambda uid: root)
    signed_in("owner")
    agent.requested_rounds = 0
    observed = []
    assert universe_intelligence.converse(
        founder_message="Remember that I prefer short answers", universe_id=root.name,
        response_observer=observed.append,
    ) == "finished exact answer"
    assert len(agent.wires) == 2
    receipt = observed[0].request_receipt
    assert receipt["dispatched"] == 2
    assert [(row["purpose"], row["dispatched"]) for row in receipt["sources"]] == [
        ("reply", 1), ("learning", 1),
    ]


def test_budget_stop_returns_bounded_continuation_without_extra_model_call(agent):
    agent.requested_rounds = 20
    budget = TurnRequestBudget("owner", "u-models", max_requests=2)
    with request_budget_scope(budget):
        answer = integration.run(agent)
    assert "request budget (2 provider attempts)" in answer
    assert "none is scheduled automatically" in answer
    assert len(agent.wires) == 2 and len(agent.tools) == 1
    assert agent.latest().state == "held_tool_not_sent"


@pytest.mark.parametrize("message", [
    "What are open pull requests?", "Explain README.md", "What is the status?",
    "Continue the project", "Read my email", "What is a repository in this project?",
])
def test_real_work_is_not_demoted_to_plain_conversation(message):
    from tinyassets.universe_intelligence import _ordinary_chat

    assert not _ordinary_chat(message)


def test_final_receipt_observer_failure_keeps_earned_reply(agent, monkeypatch, signed_in):
    from tinyassets import daemon_server, universe_intelligence

    root = agent.served.context.universe_dir
    monkeypatch.setattr(daemon_server, "get_founder_home", economy.get_founder_home)
    monkeypatch.setattr(universe_intelligence, "_universe_dir", lambda uid: root)
    signed_in("owner")

    def broken_observer(response):
        assert response.request_receipt["dispatched"] == 1
        raise RuntimeError("synthetic telemetry failure")

    assert universe_intelligence.converse(
        founder_message="hi", universe_id=root.name, response_observer=broken_observer,
    ) == "finished exact answer"
    assert len(agent.wires) == 1 and not agent.tools


def test_budget_stop_observer_receives_parent_receipt(agent):
    from tinyassets.universe_intelligence import _call_writer

    agent.requested_rounds = 20
    budget = TurnRequestBudget("owner", "u-models", max_requests=2)
    observations = []
    with request_budget_scope(budget):
        answer = _call_writer(
            "Build the requested app", system="system", universe_context=agent.served.context,
            config=agent.config, response_observer=observations.append,
        )
    assert "none is scheduled automatically" in answer
    assert len(observations) == 1
    assert observations[0].request_receipt["dispatched"] == len(agent.wires) == 2
    assert observations[0].degraded and not observations[0].reported_model


def test_empty_journal_inventory_preserves_legacy_bytes_and_rejects_unsolicited_tools():
    from dataclasses import asdict

    from tests.test_agent_turn_journal import candidate, reply
    from tinyassets.storage import agent_turn_records as records

    existing = candidate()
    legacy = asdict(existing)
    legacy.pop("authority_kind")
    legacy.pop("work_receipt_id")
    assert existing.canonical_json() == records.dump({"version": 1, **legacy})
    empty = replace(existing, tools_json=records.dump({"version": 1, "tools": []}))
    assert records.RoundInput.from_json(empty.canonical_json()) == empty
    assert not empty.tool_names()
    with pytest.raises(ValueError):
        records.reply_json(reply(), empty)
    for invalid in (None, {}, "", False, [None]):
        with pytest.raises(ValueError):
            replace(existing, tools_json=records.dump({
                "version": 1, "tools": invalid,
            })).canonical_json()


def test_wrapped_work_budget_stop_has_terminal_run_classification():
    from tinyassets import runs
    from tinyassets.graph_compiler import _wrap_provider_failure

    budget = TurnRequestBudget("owner", "u-models", max_requests=1)
    budget.settle(dispatch(budget), "succeeded")
    try:
        reserve(budget)
    except RequestBudgetExceeded as exc:
        wrapped = _wrap_provider_failure("a-node", exc)
        wrapped.__cause__ = exc
    message = runs._request_budget_failure(wrapped)
    assert message.startswith("[request_budget_exhausted] ")
    assert runs._classify_failure({"status": "failed", "error": message}) == (
        "request_budget_exhausted"
    )
    assert runs.ACTIONABLE_BY["request_budget_exhausted"] == "none"
    assert runs._request_budget_failure(RuntimeError(message)) is None


@pytest.mark.parametrize("mode", ["run", "resume"])
@pytest.mark.parametrize("kind", ["inference", "effect"])
def test_run_and_resume_persist_budget_hold_without_retry(tmp_path, monkeypatch, mode, kind):
    from tests.test_graph_compiler_failed_event import _simple_branch
    from tinyassets import runs
    from tinyassets.effectors import EffectFailedError

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    branch = _simple_branch()
    calls = []
    budget = TurnRequestBudget("owner", "u-models", max_requests=1)
    budget.settle(dispatch(budget), "succeeded")

    def stop(*args, **kwargs):
        calls.append(1)
        if kind == "effect":
            raise EffectFailedError("step1", "synthetic", "budget hold",
                                    "request_budget_exhausted")
        reserve(budget)

    if mode == "run":
        outcome = runs.execute_branch(
            tmp_path, branch=branch, inputs={"x": "test"}, actor="tester", provider_call=stop,
        )
    else:
        run_id = runs.create_run(tmp_path, branch_def_id=branch.branch_def_id,
                                 thread_id="budget-resume", inputs={"x": "test"}, actor="tester")
        monkeypatch.setattr(runs, "compile_branch", lambda *a, **kw: SimpleNamespace(
            graph=SimpleNamespace(compile=lambda **kw: SimpleNamespace(invoke=stop)),
        ))
        outcome = runs._invoke_graph_resume(
            tmp_path, run_id=run_id, branch=branch, thread_id="budget-resume",
            provider_call=None, recursion_limit=runs.DEFAULT_RECURSION_LIMIT,
            concurrency_budget_override=None,
        )
    assert outcome.status == "failed"
    stored = runs.get_run(tmp_path, outcome.run_id)
    assert runs._classify_failure(stored) == "request_budget_exhausted"
    assert runs.ACTIONABLE_BY[runs._classify_failure(stored)] == "none"
    assert len(calls) == 1 and budget.receipt()["dispatched"] == 1


@pytest.mark.parametrize("host,model,owner,expected", [
    ("openrouter.ai", "lab/model:free", "owner", True),
    ("openrouter.ai", "lab/paid", "owner", False),
    ("openrouter.ai", "lab/model:free", "different-owner", False),
    ("local.example", "lab/model:free", "owner", False),
    ("api.groq.com", "lab/unknown", "owner", False),
])
def test_legacy_free_classification_requires_exact_owned_host_and_model(
    agent, host, model, owner, expected,
):
    import json

    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.request_budget import metered_free_source

    ledger = agent.served.rig.ledger
    with ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET allowed_endpoints_json=? "
                     "WHERE connection_id='conn-models'", (json.dumps([
                         {"host": host, "path_template": "/chat", "methods": ["POST"]},
                     ]),))
        conn.commit()
    context = replace(agent.served.context, model_selection=ModelRef(
        agent.served.context.model_selection.connection_id, model,
    ))
    assert metered_free_source(context, None, owner=owner) is expected


@pytest.mark.parametrize("billing", ["free", "flat"])
def test_declared_unmetered_contract_overrides_host_free_offer(agent, billing):
    from tinyassets.providers.declared_models import declared_model_contract
    from tinyassets.request_budget import metered_free_source

    contract = declared_model_contract({
        "wire": "openai_chat", "billing": billing,
        "models": [{"id": "lab/model:free", "tools": False, "context": 10000}],
    }, auth_scheme="bearer")
    selected = SelectedModel(
        provider=agent.served.context.model_selection.connection_id, model_id="lab/model:free",
        discovery_protocol="", cost_caps=(("request_usd", 0),),
        source_digest="a" * 64, context_tokens=10000, execution_contract=contract,
    )
    assert not selection_is_free(selected)
    assert not metered_free_source(agent.served.context, selected, owner="owner")
