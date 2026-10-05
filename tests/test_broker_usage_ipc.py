"""Real socket accounting: atomic capacity, private tables and one-use sends."""
# ruff: noqa: F811 -- imported pytest fixtures
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tests.test_broker_usage_source import source  # noqa: F401
from tinyassets.broker.ops import new_op_id
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.request_budget import RequestBudgetExceeded, TurnRequestBudget
from tinyassets.storage.agent_request_usage import UsageStore

pytestmark = pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"),
                                reason="Unix broker peer identity")


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
