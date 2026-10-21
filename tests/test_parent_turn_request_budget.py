"""Synthetic dispatches prove a turn budget; daily account balances stay unknown."""

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests import test_interactive_http_agent as integration
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.model_selection import SelectedModel
from tinyassets.request_budget import (
    RequestBudgetExceeded,
    TurnRequestBudget,
    _RequestLedger,
    selection_is_free,
)

_BudgetClass = TurnRequestBudget


@pytest.fixture(autouse=True)
def durable_test_allocations(request, monkeypatch):
    """Pure reducer checks and real turn checks use their respective boundaries."""
    def allocate(owner, universe, **policy):
        if "agent" not in request.fixturenames:
            return _RequestLedger(owner, universe, **policy)
        root = request.getfixturevalue("agent").served.context.universe_dir.parent
        budget = _BudgetClass(owner, universe, **policy)
        budget.persist(root)
        return budget

    monkeypatch.setattr(sys.modules[__name__], "TurnRequestBudget", allocate)

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent




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






def test_real_claim_denial_never_reserves_request(agent, monkeypatch):
    from tinyassets.auth import middleware

    def denied(*args, **kwargs):
        raise PermissionError("synthetic admission denial")

    monkeypatch.setattr(middleware, "consume_provider_request_invocation", denied)
    budget = TurnRequestBudget("owner", "u-models")
    agent.config = replace(agent.config, request_budget=budget)
    with pytest.raises(ProviderAuthorityHeldError):
        integration.run(agent)
    assert not agent.wires and not agent.tools
    assert budget.receipt()["reserved"] == 0












def test_real_reply_retry_has_reply_purpose_and_counts_failed_http(agent, monkeypatch):
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    monkeypatch.setattr(AgentTurnCoordinator, "BAD_REPLY_BACKOFF_S", (0.0, 0.0))
    agent.requested_rounds = 0
    agent.capacity_failures[1] = 200
    agent.failure_bodies[1] = '{"error":{"code":504,"message":"Synthetic idle timeout"}}'
    budget = TurnRequestBudget("owner", "u-models")
    agent.config = replace(agent.config, request_budget=budget)
    assert integration.run(agent) == "finished exact answer"
    assert len(agent.wires) == 2 and not agent.tools
    groups = budget.receipt()["sources"]
    assert len(groups) == 1
    assert groups[0]["purpose"] == "reply"
    assert groups[0]["dispatched"] == 2
    assert groups[0]["failed"] == 1 and groups[0]["succeeded"] == 1








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
    if owner != "owner":
        with pytest.raises(ProviderAuthorityHeldError,
                           match="source budget authority is unavailable"):
            metered_free_source(context, None, owner=owner)
    else:
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
