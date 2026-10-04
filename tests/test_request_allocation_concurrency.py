"""Bounded process probes for run-allocation lock order and fork ownership."""

import faulthandler
import gc
import multiprocessing
import threading

import pytest

from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.request_budget import RunRequestAllocation, TurnRequestBudget


def _run_bounded(target, base):
    process = multiprocessing.get_context("spawn").Process(target=target, args=(base,))
    process.start()
    try:
        process.join(timeout=15)
        assert not process.is_alive(), "allocation probe deadlocked"
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def _collect_while_child_waits_for_budget(base):
    faulthandler.dump_traceback_later(8)
    gc.disable()
    budget = TurnRequestBudget("synthetic-owner", "synthetic-universe")
    budget.persist(base)
    parent = RunRequestAllocation(budget, owns_budget=True)
    abandoned = parent.child()
    abandoned.cycle = abandoned
    del abandoned
    entered = threading.Event()
    main_thread = threading.get_ident()
    original_receipt = budget.receipt
    children, errors = [], []

    def observed_receipt():
        if threading.get_ident() != main_thread:
            entered.set()
        return original_receipt()

    def admit_child():
        try:
            children.append(parent.child())
        except BaseException as exc:
            errors.append(exc)

    budget.receipt = observed_receipt
    worker = threading.Thread(target=admit_child, daemon=True)
    try:
        with budget._lock:
            worker.start()
            assert entered.wait(3), "child did not reach the budget lock"
            # Before the fix the child held family.lock here, while this GC
            # finalizer tried to acquire it with budget._lock already held.
            gc.collect()
        worker.join(timeout=3)
        assert not worker.is_alive() and not errors
        assert len(children) == 1
        parent.close()
        assert not budget.receipt()["closed"]
        children[0].close()
        assert budget.receipt()["closed"]
    finally:
        faulthandler.cancel_dump_traceback_later()
        gc.enable()


def test_gc_release_cannot_deadlock_concurrent_child_admission(tmp_path):
    _run_bounded(_collect_while_child_waits_for_budget, tmp_path)


def _close_inherited_allocation(allocation):
    # This is an actual fork copy, including its finalizer and family object.
    # Releasing the copy must not close the parent's durable usage root.
    allocation.close()
    try:
        allocation.child()
    except ProviderAuthorityHeldError as exc:
        assert "another process" in str(exc)
    else:
        raise AssertionError("a fork copy admitted another run participant")


def _fork_and_close_copy(base):
    budget = TurnRequestBudget("synthetic-owner", "synthetic-universe")
    budget.persist(base)
    parent = RunRequestAllocation(budget, owns_budget=True)
    process = multiprocessing.get_context("fork").Process(
        target=_close_inherited_allocation, args=(parent,),
    )
    process.start()
    try:
        process.join(timeout=5)
        assert not process.is_alive(), "fork copy could not release its allocation"
        assert process.exitcode == 0
        assert not budget.receipt()["closed"]
        # The original process can still admit work under the same allowance.
        child = parent.child()
        ordinal = budget.reserve(owner=budget.owner, universe=budget.universe,
                                 source_ref="synthetic-source", model="model", free=True)
        budget.dispatched(ordinal)
        budget.settle(ordinal, "succeeded")
        parent.close()
        assert not budget.receipt()["closed"]
        child.close()
        receipt = budget.receipt()
        assert receipt["closed"] and receipt["dispatched"] == 1
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=3)
        parent.close()
        budget.close()


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires fork")
def test_fork_copy_cannot_close_or_extend_parent_allocation(tmp_path):
    # Spawn first so the actual fork occurs in a fresh single-threaded process.
    _run_bounded(_fork_and_close_copy, tmp_path)
