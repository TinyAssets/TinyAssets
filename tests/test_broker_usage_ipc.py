"""Real socket accounting: atomic capacity, private tables and one-use sends."""
# ruff: noqa: F811 -- imported pytest fixtures
import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tests.test_broker_usage_source import source  # noqa: F401
from tinyassets.broker.ops import new_op_id
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.base import ModelConfig, ProviderResponse, UniverseContext
from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call
from tinyassets.request_budget import RequestBudgetExceeded, TurnRequestBudget
from tinyassets.storage.agent_request_usage import UsageStore


@pytest.fixture(autouse=True)
def local_group(monkeypatch):
    import os

    from tinyassets import role_modes

    # Unit oracle has one group; production uses the actual broker-read group.
    monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())


def make_budget(source, **policy):
    budget = TurnRequestBudget("alice", "cc-alice", **policy)
    budget.persist(source.root)
    return budget


def reserve(source, budget):
    return budget.reserve(owner="alice", universe="cc-alice",
                          source_ref=source.arguments["attempt"].source_ref,
                          model="chosen-model", free=True)


def reference(source, budget, ordinal):
    return budget.issue_reference(ordinal, grant_id="grant-a", connection_id="conn-a",
                                  verb="POST", request=source.arguments["request"],
                                  operation_id=new_op_id())


def claim(source, budget, ref, **changes):
    local = UsageStore(source.root, broker_ledger=source.ledger)
    args = dict(owner="alice", universe="cc-alice", usage_id=budget.usage_id,
                grant_id="grant-a", connection_id="conn-a", verb="POST",
                request=source.arguments["request"], operation_id=ref.operation_id)
    return local.claim_reference(ref.reference, **(args | changes))


def test_runtime_roundtrip_private_tables_source_and_one_use_retry(source):
    budget = make_budget(source)
    try:
        budget.link("turn", "turn-a")
        ordinal = reserve(source, budget)
        ref = reference(source, budget, ordinal)
        with pytest.raises(ProviderAuthorityHeldError):
            claim(source, budget, ref, owner="bob")
        dispatch = claim(source, budget, ref)
        with pytest.raises(ProviderAuthorityHeldError):
            claim(source, budget, ref)
        dispatch.check()
        dispatch.dispatched()
        dispatch.settle("failed")
        dispatch.reserve_retry()
        dispatch.dispatched()
        dispatch.settle("succeeded")
        with pytest.raises(ProviderAuthorityHeldError):
            dispatch.reserve_retry()
        assert budget.settle_invocation(ordinal, "succeeded") == 2
        receipt = budget.receipt()
        assert receipt["dispatched"] == 2
        assert [a["state"] for a in receipt["attempts"]] == ["failed", "succeeded"]
        assert budget._store.for_subject("alice", "cc-alice", "turn", "turn-a") == [receipt]
        assert budget._store.for_subject("bob", "cc-alice", "turn", "turn-a") == []
        with pytest.raises(ProviderAuthorityHeldError):
            budget._store.receipt(("bob", "cc-alice", budget.usage_id))
        assert not (source.root / ".tinyassets.db").exists()
        assert not (source.root / "outbound.db").exists()
        with source.ledger._connect() as conn:
            assert conn.execute("SELECT count(*) FROM agent_request_attempts").fetchone()[0] == 2
    finally:
        budget.close()


def test_concurrent_ipc_reservations_share_one_limit_and_committed_stop(source):
    budget = make_budget(source, max_requests=2)
    def attempt(_):
        try:
            return UsageStore(source.root).mutate(
                budget._scope, "reserve", owner="alice", universe="cc-alice",
                source_ref=source.arguments["attempt"].source_ref,
                model="chosen-model", free=True, purpose="reply")
        except RequestBudgetExceeded as exc:
            assert exc.reason == "turn_attempt_limit"
            assert exc.request_receipt["reserved"] == 2
            return None
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(8)))
        assert sorted(r for r in results if r is not None) == [1, 2]
    finally:
        budget.close()


@pytest.mark.parametrize("change", ["parent", "fence", "outage", "source", "center"])
def test_unavailable_authority_never_grants_capacity(source, monkeypatch, change):
    budget = make_budget(source)
    ordinal = reserve(source, budget)
    if change == "parent":
        budget._lease.close()
    elif change == "fence":
        source.broker.state["token"] = "stale"
    elif change == "outage":
        from tinyassets.broker import supervisor
        monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    elif change == "source":
        source.ledger.revoke_grant("grant-a")
    else:
        budget.universe = "cc-bob"
    try:
        with pytest.raises(ProviderAuthorityHeldError):
            reference(source, budget, ordinal)
        assert not (source.root / ".tinyassets.db").exists()
    finally:
        budget._lease.close()


def test_parent_close_fences_previously_claimed_dispatch(source):
    budget = make_budget(source)
    ordinal = reserve(source, budget)
    dispatch = claim(source, budget, reference(source, budget, ordinal))
    budget.close()
    with pytest.raises(RequestBudgetExceeded):
        dispatch.dispatched()
    assert budget.receipt()["dispatched"] == 0


def test_joined_child_attempt_settles_once_on_parent(source):
    parent = make_budget(source)
    try:
        child = TurnRequestBudget.join(source.root, "alice", "cc-alice", parent.usage_id)
        ordinal = reserve(source, child)
        child.dispatched(ordinal)
        assert child.settle_invocation(ordinal, "succeeded") == 1
        assert child.settle_invocation(ordinal, "succeeded") == 1
        assert parent.receipt()["dispatched"] == 1
        assert [a["state"] for a in parent.receipt()["attempts"]] == ["succeeded"]
    finally:
        parent.close()


def test_join_rejects_another_owner_or_center_before_reservation(source):
    parent = make_budget(source)
    try:
        for owner, center in (("bob", "cc-alice"), ("alice", "cc-bob")):
            with pytest.raises(ProviderAuthorityHeldError):
                TurnRequestBudget.join(source.root, owner, center, parent.usage_id)
        assert parent.receipt()["reserved"] == 0
    finally:
        parent.close()


def test_parent_stop_fences_a_joined_child(source):
    parent = make_budget(source)
    child = TurnRequestBudget.join(source.root, "alice", "cc-alice", parent.usage_id)
    ordinal = reserve(source, child)
    parent.close()
    with pytest.raises(RequestBudgetExceeded):
        child.dispatched(ordinal)
    assert parent.receipt()["attempts"][0]["state"] == "not_sent"


def test_child_release_does_not_close_parent(source):
    parent = make_budget(source)
    try:
        child = TurnRequestBudget.join(source.root, "alice", "cc-alice", parent.usage_id)
        child.close()
        assert parent.receipt()["closed"] is False
        with pytest.raises(ProviderAuthorityHeldError):
            reserve(source, child)
        assert reserve(source, parent) == 1
    finally:
        parent.close()


def test_model_call_opens_and_settles_its_durable_root(source):
    class Router:
        async def call(self, role, prompt, system, config, **kwargs):
            budget = config.request_budget
            assert budget.usage_id
            ordinal = reserve(source, budget)
            budget.dispatched(ordinal)
            budget.settle_invocation(ordinal, "succeeded")
            return ProviderResponse(
                text="answer", provider="codex", model="gpt", family="openai",
                latency_ms=1.0, reported_model="gpt", request_receipt=budget.receipt(),
            )

    context = UniverseContext(
        universe_dir=source.root / "cc-alice",
        served_provider=SimpleNamespace(owner_user_id="alice"),
    )
    request = ModelCall(None, (), ModelContext(
        prompt="review", config=ModelConfig(), universe_context=context, router=Router(),
    ))
    with patch("tinyassets.providers.call.register_open_providers_for"):
        result = asyncio.run(call(request, Parent(
            owner="alice", command_center="cc-alice", subject=("run", "run-a"),
        )))
    assert result.receipt.usage_id
    stored = UsageStore(source.root).for_subject("alice", "cc-alice", "run", "run-a")
    assert len(stored) == 1
    assert stored[0]["closed"] and stored[0]["dispatched"] == 1
    assert stored[0]["attempts"][0]["state"] == "succeeded"


def test_model_call_cannot_join_another_owners_root(source):
    parent = make_budget(source)
    try:
        context = UniverseContext(
            universe_dir=source.root / "cc-alice",
            served_provider=SimpleNamespace(owner_user_id="bob"),
        )
        request = ModelCall(None, (), ModelContext(
            prompt="review", config=ModelConfig(), universe_context=context,
            router=SimpleNamespace(call=lambda *args, **kwargs: pytest.fail("dispatched")),
        ))
        with patch("tinyassets.providers.call.register_open_providers_for"):
            with pytest.raises(ProviderAuthorityHeldError):
                asyncio.run(call(request, Parent(
                    owner="bob", command_center="cc-alice", usage_id=parent.usage_id,
                )))
        assert parent.receipt()["reserved"] == 0
    finally:
        parent.close()


def test_model_call_rejects_a_different_caller_budget_before_it_persists(source):
    parent = make_budget(source)
    other = TurnRequestBudget("alice", "cc-alice")
    try:
        context = UniverseContext(
            universe_dir=source.root / "cc-alice",
            served_provider=SimpleNamespace(owner_user_id="alice"),
        )
        request = ModelCall(None, (), ModelContext(
            prompt="review", config=ModelConfig(request_budget=other),
            universe_context=context,
            router=SimpleNamespace(call=lambda *args, **kwargs: pytest.fail("dispatched")),
        ))
        with patch("tinyassets.providers.call.register_open_providers_for"):
            with pytest.raises(PermissionError, match="allocation changed"):
                asyncio.run(call(request, Parent(
                    owner="alice", command_center="cc-alice", usage_id=parent.usage_id,
                )))
        assert other.usage_id is None and parent.receipt()["reserved"] == 0
    finally:
        parent.close()


def test_wire_cannot_invoke_private_store_or_supply_database_path(source):
    from tinyassets.broker.client import BrokerRefused

    budget = make_budget(source)
    try:
        with pytest.raises(ProviderAuthorityHeldError):
            budget._store.mutate(budget._scope, "reserve", owner="bob", universe="cc-alice",
                                  source_ref="foreign", model="model", free=True, purpose="reply")
        with pytest.raises(ValueError):
            source.broker.client.usage(budget.usage_id, {"action": "_connection"})
        with pytest.raises(ValueError):
            source.broker.client.usage(budget.usage_id,
                                       {"action": "receipt", "path": "/data/other"})
        with pytest.raises(BrokerRefused):
            source.broker.client.usage("unknown", {"action": "receipt"})
    finally:
        budget.close()
