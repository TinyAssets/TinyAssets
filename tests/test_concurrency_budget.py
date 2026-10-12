"""Concurrency budget + observability for fan-out nodes.

Tests for the spec: concurrency_budget field on BranchDefinition,
ConcurrencyTracker semaphore + peak tracking, concurrency_budget_override
on compile_branch, and concurrency stats in get_run.

Spec: openspec/specs/provider-routing/spec.md §Concurrency budget
"""
from __future__ import annotations

import threading
import time

from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import ConcurrencyTracker, compile_branch

# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def _static_provider(val: str = "ok"):
    def _call(prompt: str, system: str = "", *, role: str = "writer") -> str:
        return val
    return _call


def _two_node_branch() -> BranchDefinition:
    """A→B sequential branch — used for basic compile/run tests."""
    return BranchDefinition(
        branch_def_id="concurrency-test",
        name="Concurrency Test",
        node_defs=[
            NodeDefinition(
                node_id="a", display_name="A",
                prompt_template="step a: {x}", output_keys=["a_out"],
            ),
            NodeDefinition(
                node_id="b", display_name="B",
                prompt_template="step b: {a_out}", output_keys=["b_out"],
            ),
        ],
        graph_nodes=[
            GraphNodeRef(id="a", node_def_id="a"),
            GraphNodeRef(id="b", node_def_id="b"),
        ],
        edges=[
            EdgeDefinition(from_node="a", to_node="b"),
            EdgeDefinition(from_node="b", to_node="END"),
        ],
        entry_point="a",
        state_schema=[
            {"name": "x", "type": "str"},
            {"name": "a_out", "type": "str"},
            {"name": "b_out", "type": "str"},
        ],
    )


# ════════════════════════════════════════════════════════════════════
# ConcurrencyTracker unit tests
# ════════════════════════════════════════════════════════════════════


class TestConcurrencyTracker:
    def test_unbounded_no_semaphore(self):
        tracker = ConcurrencyTracker(None)
        assert tracker.budget is None
        assert tracker._semaphore is None

    def test_bounded_has_semaphore(self):
        tracker = ConcurrencyTracker(3)
        assert tracker.budget == 3
        assert tracker._semaphore is not None

    def test_acquire_release_updates_active_count(self):
        tracker = ConcurrencyTracker(None)
        assert tracker.stats()["active_now"] == 0
        tracker.acquire()
        assert tracker.stats()["active_now"] == 1
        tracker.acquire()
        assert tracker.stats()["active_now"] == 2
        tracker.release()
        assert tracker.stats()["active_now"] == 1
        tracker.release()
        assert tracker.stats()["active_now"] == 0

    def test_peak_is_monotonic(self):
        tracker = ConcurrencyTracker(None)
        tracker.acquire()
        tracker.acquire()
        tracker.release()
        tracker.release()
        # Peak stays at 2 even after releases.
        assert tracker.stats()["peak"] == 2

    def test_peak_never_decreases(self):
        tracker = ConcurrencyTracker(5)
        for _ in range(3):
            tracker.acquire()
        for _ in range(3):
            tracker.release()
        tracker.acquire()
        assert tracker.stats()["peak"] == 3

    def test_budget_blocks_beyond_cap(self):
        """Semaphore with budget=1 blocks second acquire until first releases."""
        tracker = ConcurrencyTracker(1)
        tracker.acquire()

        acquired = threading.Event()
        def _waiter():
            tracker.acquire()
            acquired.set()
            tracker.release()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        # Thread should not acquire yet.
        time.sleep(0.05)
        assert not acquired.is_set(), "second acquire should block while first holds semaphore"
        tracker.release()
        t.join(timeout=2.0)
        assert acquired.is_set(), "second acquire should succeed after first releases"

    def test_stats_returns_correct_shape(self):
        tracker = ConcurrencyTracker(4)
        tracker.acquire()
        stats = tracker.stats()
        assert stats["active_now"] == 1
        assert stats["peak"] == 1
        assert stats["budget"] == 4
        tracker.release()

    def test_unbounded_stats_budget_is_none(self):
        tracker = ConcurrencyTracker(None)
        assert tracker.stats()["budget"] is None


# ════════════════════════════════════════════════════════════════════
# compile_branch concurrency integration
# ════════════════════════════════════════════════════════════════════


class TestCompileBranchConcurrency:
    def test_no_budget_compiles_without_tracker(self):
        branch = _two_node_branch()
        compiled = compile_branch(branch, provider_call=_static_provider())
        assert compiled.concurrency_tracker is None

    def test_branch_budget_creates_tracker(self):
        branch = _two_node_branch()
        branch.concurrency_budget = 2
        compiled = compile_branch(branch, provider_call=_static_provider())
        assert compiled.concurrency_tracker is not None
        assert compiled.concurrency_tracker.budget == 2

    def test_override_creates_tracker(self):
        branch = _two_node_branch()
        assert branch.concurrency_budget is None
        compiled = compile_branch(
            branch,
            provider_call=_static_provider(),
            concurrency_budget_override=3,
        )
        assert compiled.concurrency_tracker is not None
        assert compiled.concurrency_tracker.budget == 3

    def test_override_beats_branch_budget(self):
        branch = _two_node_branch()
        branch.concurrency_budget = 5
        compiled = compile_branch(
            branch,
            provider_call=_static_provider(),
            concurrency_budget_override=1,
        )
        assert compiled.concurrency_tracker.budget == 1

    def test_branch_with_budget_runs_and_produces_output(self):
        """Smoke: budget=1 doesn't deadlock a sequential branch."""
        branch = _two_node_branch()
        branch.concurrency_budget = 1
        compiled = compile_branch(branch, provider_call=_static_provider("yes"))
        result = compiled.graph.compile().invoke({"x": "hello"})
        assert result.get("a_out")
        assert result.get("b_out")

    def test_peak_recorded_after_sequential_run(self):
        """Sequential branch: peak = 1 (no fan-out)."""
        branch = _two_node_branch()
        branch.concurrency_budget = 2
        compiled = compile_branch(branch, provider_call=_static_provider("done"))
        compiled.graph.compile().invoke({"x": "t"})
        # Sequential nodes acquire/release one at a time → peak = 1.
        assert compiled.concurrency_tracker.stats()["peak"] == 1

    def test_budget_field_on_branch_preserved_after_fork(self):
        branch = _two_node_branch()
        branch.concurrency_budget = 4
        forked = branch.fork("forked")
        # fork() uses from_dict(to_dict()), so concurrency_budget should survive.
        assert forked.concurrency_budget == 4


# ════════════════════════════════════════════════════════════════════
# BranchDefinition.concurrency_budget field
# ════════════════════════════════════════════════════════════════════


class TestBranchConcurrencyBudgetField:
    def test_default_is_none(self):
        branch = _two_node_branch()
        assert branch.concurrency_budget is None

    def test_field_serializes_in_to_dict(self):
        branch = _two_node_branch()
        branch.concurrency_budget = 3
        d = branch.to_dict()
        assert d["concurrency_budget"] == 3

    def test_field_deserializes_from_dict(self):
        branch = _two_node_branch()
        branch.concurrency_budget = 7
        restored = BranchDefinition.from_dict(branch.to_dict())
        assert restored.concurrency_budget == 7

    def test_none_budget_roundtrips(self):
        branch = _two_node_branch()
        restored = BranchDefinition.from_dict(branch.to_dict())
        assert restored.concurrency_budget is None

    def test_validate_with_budget_set_passes(self):
        branch = _two_node_branch()
        branch.concurrency_budget = 2
        assert branch.validate() == []
