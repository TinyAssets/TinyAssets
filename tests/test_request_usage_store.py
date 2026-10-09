"""Synthetic cross-process accounting primitives; no credentials or network."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.definition import _definition_id
from tinyassets.request_budget import RequestBudgetExceeded, TurnRequestBudget
from tinyassets.storage.agent_request_usage import UsageStore

_FIELDS = dict(universe_id="home", owner_user_id="owner", access_method="api_key_http",
               protocol="openai_chat", ref="grant", model="default-model")
SOURCE = "api_key_http:" + _definition_id(**_FIELDS)
WIRE = {"body": {"model": "model"}}


@pytest.fixture(autouse=True)
def installed_source(tmp_path):
    # Synthetic installed descriptor and grant; no credentials or real accounts.
    root = tmp_path / "home"
    root.mkdir(exist_ok=True)
    (root / "provider_definitions.json").write_text(json.dumps([
        dict(_FIELDS, id=SOURCE.removeprefix("api_key_http:"), visibility="private",
             created_at="2026-10-04T00:00:00+00:00"),
    ]))
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "owner")
    ledger.create_connection(
        connection_id="connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
        destination="compute:synthetic", credential_ref="vault://http/synthetic",
        allowed_endpoints=[dict(host="models.example", path_template="/v1/chat",
                                methods=["POST"])])
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="owner", universe_id="home")


@pytest.fixture
def budget(tmp_path):
    value = TurnRequestBudget("owner", "home")
    value.persist(tmp_path)
    return value


def reserve(budget, *, source=SOURCE, free=True, purpose="reply"):
    return budget.reserve(owner=budget.owner, universe=budget.universe, source_ref=source,
                          model="model", free=free, purpose=purpose)


def ticket(budget, ordinal):
    from tinyassets.broker.ops import new_op_id

    return budget.issue_reference(ordinal, grant_id="grant", connection_id="connection",
                                  verb="POST", request=WIRE,
                                  operation_id=new_op_id())


def claim(budget, ref, **changes):
    fields = dict(owner=budget.owner, universe=budget.universe, usage_id=budget.usage_id,
                  grant_id="grant", connection_id="connection", verb="POST",
                  request=WIRE, operation_id=ref.operation_id)
    fields.update(changes)
    return _broker_store(budget._store.base).claim_reference(ref.reference, **fields)


def _broker_store(base):
    """Claim and retry are broker-local primitives, never daemon IPC commands."""
    from tinyassets.storage.outbound_connections import ConnectionLedger

    return UsageStore(base, broker_ledger=ConnectionLedger(
        base / ".broker" / "outbound.db", data_root=base))


@pytest.mark.parametrize("change", [
    {"owner": "other"}, {"universe": "other"}, {"usage_id": "another-parent"},
    {"grant_id": "other"}, {"connection_id": "other"}, {"verb": "GET"},
    {"request": {"body": "substituted"}}, {"operation_id": "different-operation"},
])
def test_reference_rejects_scope_and_request_substitution_without_consuming_it(budget, change):
    ordinal = reserve(budget)
    ref = ticket(budget, ordinal)
    with pytest.raises(ProviderAuthorityHeldError):
        claim(budget, ref, **change)
    dispatch = claim(budget, ref)
    dispatch.dispatched()
    budget.settle_invocation(ordinal, "succeeded")
    assert budget.receipt()["dispatched"] == 1


def test_reference_and_dispatch_are_single_use(budget):
    ordinal = reserve(budget)
    ref = ticket(budget, ordinal)
    dispatch = claim(budget, ref)
    with pytest.raises(ProviderAuthorityHeldError):
        claim(budget, ref)
    dispatch.dispatched()
    with pytest.raises(ValueError):
        dispatch.dispatched()
    budget.settle_invocation(ordinal, "succeeded")
    assert budget.receipt()["dispatched"] == 1


def test_shared_store_reservation_is_atomic_across_independent_connections(budget):
    def attempt(number):
        store = UsageStore(budget._store.base)
        try:
            return store.mutate(budget._scope, "reserve", owner="owner", universe="home",
                                source_ref=f"source-{number}", model="model", free=True,
                                purpose="reply")
        except RequestBudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        ordinals = [item for item in pool.map(attempt, range(50)) if item is not None]
    assert sorted(ordinals) == list(range(1, 7))
    for number in range(20):
        ordinal = reserve(budget, source=f"local-{number}", free=False)
        budget.dispatched(ordinal)
        budget.settle(ordinal, "succeeded")
    assert budget.receipt()["dispatched"] == 20


def test_oauth_retry_has_separate_outcome_without_double_counting(budget):
    first = reserve(budget)
    dispatch = claim(budget, ticket(budget, first))
    dispatch.dispatched()
    dispatch.settle("failed")
    # Another helper can reserve between the first attempt and its retry.
    helper = reserve(budget, purpose="helper")
    dispatch.reserve_retry()
    dispatch.dispatched()
    budget.settle_invocation(first, "succeeded")
    budget.dispatched(helper)
    budget.settle(helper, "failed")
    assert [(a["purpose"], a["state"]) for a in budget.receipt()["attempts"]] == [
        ("reply", "failed"), ("helper", "failed"), ("reply", "succeeded"),
    ]
    assert budget.receipt()["dispatched"] == 3


def test_retry_stop_is_before_any_new_reserved_attempt(tmp_path):
    budget = TurnRequestBudget("owner", "home", free_pool_limit=1)
    budget.persist(tmp_path)
    ordinal = reserve(budget)
    dispatch = claim(budget, ticket(budget, ordinal))
    dispatch.dispatched()
    dispatch.settle("failed")
    with pytest.raises(RequestBudgetExceeded):
        dispatch.reserve_retry()
    assert budget.receipt()["reserved"] == budget.receipt()["dispatched"] == 1


def test_parent_close_cancels_new_dispatch_but_keeps_inflight_settlement(budget):
    first, second = reserve(budget), reserve(budget)
    live = claim(budget, ticket(budget, first))
    waiting = claim(budget, ticket(budget, second))
    live.dispatched()
    budget.close()
    with pytest.raises(RequestBudgetExceeded):
        waiting.dispatched()
    budget.settle_invocation(first, "unknown")
    budget.settle_invocation(second, "unknown")
    assert [a["state"] for a in budget.receipt()["attempts"]] == ["unknown", "not_sent"]
    assert budget.receipt()["dispatched"] == 1


def test_router_settlement_revokes_unconsumed_ticket_before_refund(budget):
    first = reserve(budget)
    ref = ticket(budget, first)
    budget.settle_invocation(first, "unknown")
    with pytest.raises(ProviderAuthorityHeldError):
        claim(budget, ref)
    assert budget.receipt()["dispatched"] == 0
    assert budget.receipt()["attempts"][0]["state"] == "not_sent"


def test_dispatch_day_and_source_purpose_survive_reloading(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 3, 23, 59, tzinfo=timezone.utc)]
    original_load = UsageStore._load

    def broker_clock(store, conn, scope, **kwargs):
        return original_load(store, conn, scope, **dict(kwargs, wall_clock=lambda: clock[0]))

    monkeypatch.setattr(UsageStore, "_load", broker_clock)
    budget = TurnRequestBudget("owner", "home", wall_clock=lambda: clock[0])
    budget.persist(tmp_path)
    for purpose in ("reply", "review"):
        ordinal = reserve(budget, purpose=purpose)
        budget.dispatched(ordinal)
        budget.settle(ordinal, "failed")
        clock[0] = datetime(2026, 10, 4, 0, 1, tzinfo=timezone.utc)
    budget.link("turn", "turn-id")
    stored = UsageStore(tmp_path).for_subject("owner", "home", "turn", "turn-id")[0]
    assert [a["dispatched_at"][:10] for a in stored["attempts"]] == ["2026-10-03", "2026-10-04"]
    assert [(r["source_ref"], r["purpose"], r["failed"]) for r in stored["sources"]] == [
        (SOURCE, "reply", 1), (SOURCE, "review", 1),
    ]
    assert UsageStore(tmp_path).for_subject("stranger", "home", "turn", "turn-id") == []


def test_dead_parent_is_unknown_and_cannot_restart(budget, monkeypatch):
    ordinal = reserve(budget)
    dispatch = claim(budget, ticket(budget, ordinal))
    dispatch.dispatched()
    monkeypatch.setattr("tinyassets.process_liveness.owner_state", lambda *args: "dead")
    receipt = budget.receipt()
    assert receipt["closed"] and receipt["attempts"][0]["state"] == "unknown"
    with pytest.raises(RequestBudgetExceeded):
        reserve(budget)
    with pytest.raises(RequestBudgetExceeded):
        dispatch.reserve_retry()


def test_persist_and_reserve_share_one_lock(tmp_path):
    import threading

    budget = TurnRequestBudget("owner", "home")
    entered = threading.Event()
    original_lock = budget._lock
    main_thread = threading.get_ident()

    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() != main_thread:
                entered.set()
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    budget._lock = ObservedLock()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with budget._lock:
            pending = pool.submit(reserve, budget)
            assert entered.wait(2)
            budget.persist(tmp_path)
        assert pending.result(timeout=2) == 1
    assert budget.receipt()["reserved"] == 1
    assert reserve(budget) == 2


@pytest.mark.parametrize("changes", [
    {"grant_id": "other"}, {"connection_id": "other"},
    {"request": {"body": {"model": "other"}}}, {"verb": "GET"},
])
def test_reference_cannot_relabel_reserved_source(budget, changes):
    from tinyassets.broker.ops import new_op_id

    ordinal = reserve(budget)
    fields = dict(grant_id="grant", connection_id="connection", request=WIRE,
                  verb="POST", operation_id=new_op_id())
    fields.update(changes)
    with pytest.raises(ProviderAuthorityHeldError):
        budget.issue_reference(ordinal, **fields)
    assert budget.receipt()["dispatched"] == 0
    ticket(budget, ordinal)  # Rejection did not consume the valid reservation.


def test_paid_reservation_cannot_be_used_for_other_free_source(budget):
    ordinal = reserve(budget, source="local:paid", free=False)
    with pytest.raises(ProviderAuthorityHeldError):
        ticket(budget, ordinal)
    assert budget.receipt()["dispatched"] == 0


@pytest.mark.parametrize("persistent", [False, True])
def test_late_success_does_not_reset_newer_failure_streak(tmp_path, persistent):
    budget = TurnRequestBudget("owner", "home")
    if persistent:
        budget.persist(tmp_path)
    first, second, third = [reserve(budget) for _ in range(3)]
    for ordinal in (first, second, third):
        budget.dispatched(ordinal)
    budget.settle(second, "failed")
    budget.settle(third, "unknown")
    budget.settle(first, "succeeded")
    with pytest.raises(RequestBudgetExceeded, match="request budget") as stopped:
        reserve(budget)
    assert stopped.value.reason == "consecutive_failures"
    assert reserve(budget, source="local", free=False) == 4


def test_store_connections_close_without_garbage_collection(budget):
    import gc
    from pathlib import Path

    gc.collect()
    before = len(list(Path("/proc/self/fd").iterdir()))
    gc.disable()
    try:
        for _ in range(30):
            budget.receipt()
        assert len(list(Path("/proc/self/fd").iterdir())) <= before + 1
    finally:
        gc.enable()


def test_broker_ledger_context_closes_after_commit_and_rollback(tmp_path):
    ledger = _broker_store(tmp_path)._ledger
    with ledger._connect() as committed:
        committed.execute("CREATE TABLE close_proof (value INTEGER)")
        committed.execute("INSERT INTO close_proof VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        committed.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="rollback"):
        with ledger._connect() as rolled_back:
            rolled_back.execute("INSERT INTO close_proof VALUES (2)")
            raise RuntimeError("rollback")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        rolled_back.execute("SELECT 1")
    with ledger._connect() as current:
        assert [row[0] for row in current.execute("SELECT value FROM close_proof")] == [1]


@pytest.mark.parametrize("action", ["claim", "issue"])
def test_inferred_parent_close_is_durable_after_rejected_reference(budget, monkeypatch, action):
    ordinal = reserve(budget)
    ref = ticket(budget, ordinal) if action == "claim" else None
    with monkeypatch.context() as patch:
        patch.setattr("tinyassets.process_liveness.owner_state", lambda *args: "unknown")
        with pytest.raises(RequestBudgetExceeded):
            claim(budget, ref) if ref is not None else ticket(budget, ordinal)
    assert budget.receipt()["closed"]
    with pytest.raises(RequestBudgetExceeded):
        claim(budget, ref) if ref is not None else ticket(budget, ordinal)
    assert budget.receipt()["dispatched"] == 0


def test_failed_sqlite_close_resets_context_and_fences_copied_dispatch(budget, monkeypatch):
    from tinyassets.request_budget import current_request_budget, request_budget_scope

    ordinal = reserve(budget)
    ref = ticket(budget, ordinal)
    dispatch = claim(budget, ref)
    original = budget._store.mutate

    def fail_close(scope, operation, *args, **kwargs):
        if operation == "close":
            raise sqlite3.OperationalError("synthetic close write failure")
        return original(scope, operation, *args, **kwargs)

    monkeypatch.setattr(budget._store, "mutate", fail_close)
    with pytest.raises(sqlite3.OperationalError), request_budget_scope(budget):
        assert current_request_budget() is budget
    assert current_request_budget() is None
    with pytest.raises(RequestBudgetExceeded):
        dispatch.dispatched()
    with pytest.raises(RequestBudgetExceeded):
        reserve(budget)
    assert budget.receipt()["dispatched"] == 0


def test_closed_parent_allows_inflight_success_to_settle(budget):
    ordinal = reserve(budget)
    dispatch = claim(budget, ticket(budget, ordinal))
    dispatch.dispatched()
    budget.close()
    budget.settle_invocation(ordinal, "succeeded")
    assert budget.receipt()["attempts"][0]["state"] == "succeeded"
    dispatch.settle("failed")
    assert budget.receipt()["attempts"][0]["state"] == "succeeded"


def _gc_during_lease_registration(base):
    import gc
    from unittest.mock import patch

    from tinyassets.process_liveness import hold_liveness
    from tinyassets.storage.agent_request_usage import ParentUsageLease

    gc.disable()
    abandoned = TurnRequestBudget("owner", "home")
    with patch("tinyassets.storage.agent_request_usage.UsageStore",
               return_value=_broker_store(base)):
        abandoned.persist(base)
    abandoned.cycle = abandoned
    del abandoned

    def collecting_hold(*args):
        gc.collect()
        return hold_liveness(*args)

    with patch("tinyassets.process_liveness.hold_liveness", collecting_hold):
        lease = ParentUsageLease(base, "gc-registration-test")
        lease.close()


def test_gc_finalizer_cannot_deadlock_lease_registration(tmp_path):
    import multiprocessing

    process = multiprocessing.get_context("spawn").Process(
        target=_gc_during_lease_registration, args=(tmp_path,),
    )
    process.start()
    process.join(timeout=10)
    try:
        assert not process.is_alive(), "lease registration deadlocked its finalizer"
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def _reserve_in_process(arguments):
    base, scope, number = arguments
    try:
        return _broker_store(base).mutate(scope, "reserve", owner=scope[0], universe=scope[1],
                                          source_ref=f"source-{number}", model="model", free=True,
                                          purpose="reply")
    except RequestBudgetExceeded:
        return None


def test_independent_processes_share_the_same_parent_free_allocation(budget):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn"),
    ) as pool:
        results = list(pool.map(_reserve_in_process,
                                [(budget._store.base, budget._scope, n) for n in range(24)]))
    assert sorted(n for n in results if n is not None) == list(range(1, 7))
    assert budget.receipt()["reserved"] == 6
    assert budget.receipt()["dispatched"] == 0
    independent = TurnRequestBudget("owner", "home")
    independent.persist(budget._store.base)
    assert reserve(independent) == 1
    independent.close()


def test_admitted_run_children_keep_one_budget_until_final_release(budget):
    from tinyassets.request_budget import RunRequestAllocation

    parent = RunRequestAllocation(budget, owns_budget=True)
    child = parent.child()
    parent.close()
    assert child.budget is budget and not budget.receipt()["closed"]
    with pytest.raises(ProviderAuthorityHeldError, match="already released"):
        parent.child()
    for _ in range(6):
        ordinal = reserve(child.budget)
        child.budget.dispatched(ordinal)
        child.budget.settle(ordinal, "succeeded")
    sibling = child.child()
    with pytest.raises(RequestBudgetExceeded):
        reserve(sibling.budget)
    child.close()
    assert not budget.receipt()["closed"]
    sibling.close()
    assert budget.receipt()["closed"] and budget.receipt()["dispatched"] == 6


def test_explicit_parent_stop_fences_admitted_children(budget):
    from tinyassets.request_budget import RunRequestAllocation

    parent = RunRequestAllocation(budget, owns_budget=True)
    child = parent.child()
    ordinal = reserve(child.budget)
    budget.close()
    with pytest.raises(RequestBudgetExceeded):
        child.budget.dispatched(ordinal)
    with pytest.raises(ProviderAuthorityHeldError):
        child.child()
    assert budget.receipt()["dispatched"] == 0
    parent.close()
    child.close()


def test_borrowed_run_allocation_cannot_close_its_chat_owner(budget):
    from tinyassets.request_budget import RunRequestAllocation

    parent = RunRequestAllocation(budget)
    child = parent.child()
    parent.close()
    child.close()
    assert not budget.receipt()["closed"]
    budget.close()
    assert budget.receipt()["closed"]


def test_concurrent_run_child_admission_and_parent_release_never_reopens(budget):
    import threading

    from tinyassets.request_budget import RunRequestAllocation

    parent = RunRequestAllocation(budget, owns_budget=True)
    barrier = threading.Barrier(16)

    def race(index):
        barrier.wait(timeout=5)
        if index == 0:
            parent.close()
            return None
        try:
            return parent.child()
        except ProviderAuthorityHeldError:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        children = [c for c in pool.map(race, range(16)) if c is not None]
    with pytest.raises(ProviderAuthorityHeldError):
        parent.child()
    for child in children:
        assert child.budget is budget
        child.close()
    assert budget.receipt()["closed"]


def test_abandoned_admitted_run_releases_without_leaking_parent_liveness(budget):
    import gc

    from tinyassets.request_budget import RunRequestAllocation

    parent = RunRequestAllocation(budget, owns_budget=True)
    child = parent.child()
    parent.close()
    assert not budget.receipt()["closed"]
    del child
    gc.collect()
    assert budget.receipt()["closed"]
