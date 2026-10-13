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


def test_model_launch_reference_is_allocation_scoped_and_revoked_on_close(source):
    parent = make_budget(source)
    store = UsageStore(source.root)
    try:
        with pytest.raises(ProviderAuthorityHeldError):
            parent.issue_launch_reference("turn", "turn-a", "agent-a", 1)
        parent.link("turn", "turn-a")
        reference = parent.issue_launch_reference("turn", "turn-a", "agent-a", 1)
        assert parent.issue_launch_reference("turn", "turn-a", "agent-a", 1) == reference
        with pytest.raises(ProviderAuthorityHeldError, match="parent changed"):
            parent.issue_launch_reference("turn", "turn-b", "agent-a", 1)
        with pytest.raises(ProviderAuthorityHeldError):
            store.resolve_launch_reference("bob", "cc-alice", reference)
        with pytest.raises(ProviderAuthorityHeldError):
            store.resolve_launch_reference("alice", "cc-bob", reference)
        assert store.resolve_launch_reference("alice", "cc-alice", reference) == {
            "usage_id": parent.usage_id, "authority_kind": "turn",
            "authority_id": "turn-a", "agent_binding_id": "agent-a",
            "binding_revision": 1,
        }
        assert parent.receipt()["reserved"] == 0
    finally:
        parent.close()
    with pytest.raises(ProviderAuthorityHeldError, match="ended"):
        store.resolve_launch_reference("alice", "cc-alice", reference)


def test_model_launch_grant_cannot_be_edited_or_moved():
    from tinyassets.served_tools import launch_grant, model_reference_from_grant

    reference = "a" * 64
    grant = launch_grant("key", "thread", "turn", ("bash",),
                         model_reference=reference)
    assert model_reference_from_grant("key", "thread", "turn", grant) == reference
    assert model_reference_from_grant("key", "thread", "other", grant) is None
    assert model_reference_from_grant("other", "thread", "turn", grant) is None
    assert model_reference_from_grant("key", "thread", "turn",
                                      grant.replace(reference, "b" * 64)) is None


def test_ta_model_call_joins_parent_once_and_rejects_supplied_authority(source, monkeypatch):
    from tinyassets.providers.execution_receipt import ExecutionReceipt
    from tinyassets.providers.model_call import ModelResult
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    parent = make_budget(source)
    parent.link("turn", "turn-a")
    reference = parent.issue_launch_reference("turn", "turn-a", "agent-a", 1)
    observed = []

    async def routed(request, authority):
        observed.append((authority, request))
        budget = request.context.config.request_budget
        ordinal = reserve(source, budget)
        budget.dispatched(ordinal)
        budget.settle_invocation(ordinal, "succeeded")
        return ModelResult(
            ProviderResponse(text="answer", provider="connected", model="model",
                             family="test", latency_ms=1.0),
            ExecutionReceipt(provider="connected", model="model",
                             model_status="reported", usage_id=budget.usage_id,
                             parent_usage_id=authority.usage_id, outcome="succeeded"),
        )

    monkeypatch.setattr("tinyassets.providers.model_call.call", routed)
    monkeypatch.setattr("tinyassets.commons_catalog.get_binding", lambda *args, **kwargs: {
        "created_by": "alice", "revision": 1,
    })
    monkeypatch.setattr("tinyassets.provider_serving_binding.resolve_serving_agent_binding",
                        lambda *args, **kwargs: {
                            "agent_binding_id": "serving-a", "revision": 2,
                        })
    monkeypatch.setattr("tinyassets.providers.served_model_plan.connected_sources",
                        lambda *args: frozenset({"api_key_http:connected", "codex"}))
    service = Capabilities(
        source.root / "cc-alice", ExecutionContext("cc-alice", "alice", "main"),
        [], None, lambda: None, capability_grant=("bash",), model_reference=reference)
    unsigned = Capabilities(
        source.root / "cc-alice", ExecutionContext("cc-alice", "alice", "main"),
        [], None, lambda: None, capability_grant=("bash",))
    call = {"op": "call", "name": "model",
            "arguments": {"model": "api_key_http:connected/model",
                          "messages": [{"role": "user", "content": "hello"}]}}
    try:
        assert "no model authority" in asyncio.run(unsigned.dispatch(call))["error"]
        forged = asyncio.run(service.dispatch({
            **call, "arguments": {**call["arguments"], "usage_id": parent.usage_id}}))
        assert "cannot name" in forged["error"]
        assert parent.receipt()["reserved"] == 0
        result = asyncio.run(service.dispatch(call))["result"]
        assert result["text"] == "answer"
        assert result["receipt"]["parent_usage_id"] == parent.usage_id
        assert len(observed) == 1 and parent.receipt()["reserved"] == 1
        assert parent.receipt()["attempts"][0]["state"] == "succeeded"
        codex = asyncio.run(service.dispatch({
            **call, "arguments": {**call["arguments"],
                                  "model": "codex/gpt-5.6-sol"}}))
        assert codex["result"]["text"] == "answer"
        assert observed[-1][1].model.connection_id == "codex"
        assert observed[-1][1].context.universe_context.provider_request.agent_binding_id == (
            "serving-a")
    finally:
        parent.close()
    assert "ended" in asyncio.run(service.dispatch(call))["error"]


def test_ta_model_call_without_pin_uses_owners_explicit_primary(source, monkeypatch):
    from tests.test_model_policy import NEEDS, connection, model
    from tinyassets.providers.agent_model_plan import AgentModelPlan
    from tinyassets.providers.execution_receipt import ExecutionReceipt
    from tinyassets.providers.model_policy import Catalog, ModelPolicy, ModelRef
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    parent = make_budget(source)
    parent.link("turn", "turn-default")
    reference = parent.issue_launch_reference("turn", "turn-default", "custom-a", 1)
    monkeypatch.setattr("tinyassets.commons_catalog.get_binding", lambda *args, **kwargs: {
        "created_by": "alice", "revision": 1,
    })
    monkeypatch.setattr("tinyassets.provider_serving_binding.resolve_serving_agent_binding",
                        lambda *args, **kwargs: {
                            "agent_binding_id": "serving-a", "revision": 2,
                        })
    monkeypatch.setattr("tinyassets.providers.served_model_plan.connected_sources",
                        lambda *args: frozenset({"claude-code", "codex"}))
    plan = AgentModelPlan(
        Catalog("alice", "cc-alice", (
            connection("claude-code", models=[model("opus")]),
            connection("codex", models=[model("gpt-5.6-sol")]),
        )),
        ModelPolicy(0, "explicit", (), saved_default=ModelRef("claude-code", "opus")),
        NEEDS,
    )
    observed = []
    monkeypatch.setattr("tinyassets.providers.served_model_plan.prepare_owned_model_plan",
                        lambda **kwargs: SimpleNamespace(plan=plan))

    async def routed(request, authority):
        observed.append((request, authority))
        return SimpleNamespace(text="answer", receipt=ExecutionReceipt(
            provider="claude-code", model="opus", model_status="reported"),
                               response=SimpleNamespace(agent_reply=None))

    monkeypatch.setattr("tinyassets.providers.model_call.call", routed)
    service = Capabilities(
        source.root / "cc-alice", ExecutionContext("cc-alice", "alice", "main"),
        [], None, lambda: None, capability_grant=("bash",), model_reference=reference)
    try:
        result = asyncio.run(service.dispatch({"op": "call", "name": "model", "arguments": {
            "messages": [{"role": "user", "content": "hello"}]}}))
        assert result["result"]["text"] == "answer"
        assert observed[0][0].model == ModelRef("claude-code", "opus")
        assert observed[0][0].context.universe_context.provider_request.agent_binding_id == (
            "serving-a")
    finally:
        parent.close()


def test_run_model_call_enforces_receipt_sources_before_reservation(source, monkeypatch):
    from tinyassets.provider_work_authority import ProviderWorkReceiptState
    from tinyassets.providers.execution_receipt import ExecutionReceipt
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    parent = make_budget(source)
    parent.link("run", "run-a")
    reference = parent.issue_launch_reference("run", "run-a", "receipt-a")
    receipt = SimpleNamespace(
        state=ProviderWorkReceiptState.ACTIVE, principal_id="alice",
        universe_id="cc-alice", work_item_id="run-a",
        admitted_sources=("api_key_http:admitted",),
    )
    monkeypatch.setattr(
        "tinyassets.storage.provider_work_authority."
        "SQLiteProviderWorkAuthorityStore.get_receipt",
        lambda self, _id: receipt,
    )
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.resolve_serving_agent_binding",
        lambda *args, **kwargs: {"agent_binding_id": "agent-a", "revision": 1},
    )
    monkeypatch.setattr("tinyassets.providers.served_model_plan.connected_sources",
                        lambda *args: frozenset({"api_key_http:admitted",
                                                "api_key_http:new"}))
    observed = []

    async def routed(request, authority):
        observed.append((request, authority))
        return SimpleNamespace(text="answer", receipt=ExecutionReceipt(
            provider="admitted", model="model", model_status="reported",
            parent_usage_id=authority.usage_id),
            response=SimpleNamespace(agent_reply=None))

    monkeypatch.setattr("tinyassets.providers.model_call.call", routed)
    service = Capabilities(
        source.root / "cc-alice", ExecutionContext("cc-alice", "alice", "main"),
        [], None, lambda: None, capability_grant=("bash",), model_reference=reference)
    def request(model):
        return {"op": "call", "name": "model", "arguments": {
            "model": model, "messages": [{"role": "user", "content": "hello"}]}}
    try:
        denied = asyncio.run(service.dispatch(request("api_key_http:new/model")))
        assert "admitted live source" in denied["error"]
        assert parent.receipt()["reserved"] == 0
        recovered = request("api_key_http:new/model")
        recovered["arguments"]["fallbacks"] = ["api_key_http:admitted/model"]
        assert asyncio.run(service.dispatch(recovered))["result"]["text"] == "answer"
        allowed = asyncio.run(service.dispatch(request("api_key_http:admitted/model")))
        assert allowed["result"]["text"] == "answer"
        assert observed[0][1].usage_id == parent.usage_id
        assert observed[0][0].context.launch_reference == reference
        assert len(observed) == 2
    finally:
        parent.close()


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
