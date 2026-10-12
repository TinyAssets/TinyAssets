"""A node that went terminal must not launch provider work afterwards.

``_run_with_timeout`` submits every provider/source_code call to a shared
bounded ``ThreadPoolExecutor`` and measures ``timeout_s`` from ``submit()``,
not from worker-allocated start.  When the pool is saturated the 9th submit
sits in the queue burning its own deadline, ``NodeTimeoutError`` fires, the
node becomes terminal — and the queued work then *starts anyway*, holding a
worker and driving a provider call whose result nobody is waiting for.  That
is unadmitted work: it begins strictly after the deadline that admitted it.

The correction is bounded by what ``concurrent.futures`` can prove:

* **Not yet started** → ``Future.cancel()`` succeeds and the call never
  happens.  No provider was invoked, so there is no effect to settle and no
  uncertain-effect risk.
* **Already started** → ``Future.cancel()`` returns ``False`` by contract and
  the work runs to completion untouched.  Invocation settlement and
  uncertain-effect protection are preserved exactly as before; the worker
  thread is still never killed.

These tests pin both halves.  The first is the defect; the second is the
guard that the fix does not over-correct into interrupting live work.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest

from tinyassets import graph_compiler
from tinyassets.branches import NodeDefinition
from tinyassets.graph_compiler import (
    NodeTimeoutError,
    _build_prompt_template_node,
    _run_with_timeout,
)


class _EnqueueSignallingPool(concurrent.futures.ThreadPoolExecutor):
    """A pool that says when an item has been ENQUEUED.

    The queue-wait tests must know the node's call is actually sitting in the
    queue before they start timing the wait. Sleeping and assuming the caller
    thread got there first is the flake the lead review called out.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enqueued = threading.Event()

    def submit(self, fn, /, *args, **kwargs):
        future = super().submit(fn, *args, **kwargs)
        self.enqueued.set()
        return future


@pytest.fixture()
def single_worker_pool(monkeypatch):
    """Pin the shared executor to one worker so queueing is deterministic.

    Saturation is then a single occupied worker rather than a race against
    the real 8-wide pool, so neither test depends on timing luck.
    """
    pool = _EnqueueSignallingPool(
        max_workers=1, thread_name_prefix="test-node-timeout",
    )
    monkeypatch.setattr(graph_compiler, "_TIMEOUT_EXECUTOR", pool)
    try:
        yield pool
    finally:
        pool.shutdown(wait=False)


def test_queued_work_never_starts_once_its_node_is_terminal(single_worker_pool):
    """A call still in the queue at its deadline must never be launched."""
    release_blocker = threading.Event()
    victim_started = threading.Event()

    def _blocker() -> str:
        # Holds the only worker, so the victim below can only queue.
        release_blocker.wait(timeout=10.0)
        return "blocker"

    def _victim() -> str:
        victim_started.set()
        return "victim"

    blocker_future = single_worker_pool.submit(_blocker)
    try:
        with pytest.raises(NodeTimeoutError) as exc_info:
            _run_with_timeout(_victim, timeout_s=0.1, node_id="queued_node")
        assert exc_info.value.node_id == "queued_node"

        # The node is terminal. Free the worker and give the pool every
        # chance to run the abandoned item.
        release_blocker.set()
        blocker_future.result(timeout=10.0)
        started_after_terminal = victim_started.wait(timeout=1.0)
    finally:
        release_blocker.set()

    assert not started_after_terminal, (
        "work queued behind a saturated pool started AFTER its node already "
        "failed with NodeTimeoutError — the node is terminal but its provider "
        "call runs anyway, entirely outside the deadline that admitted it"
    )


class _SubmitReturnsOnceRunningPool(concurrent.futures.ThreadPoolExecutor):
    """A one-worker pool whose ``submit()`` returns only once the work is RUNNING.

    "Already running when the deadline fires" is the premise of the settle test,
    and it used to rest on the worker picking the item up inside a 0.1s
    deadline. On a loaded CI runner it sometimes did not: ``cancel()`` then
    won, the work never ran, and the test failed with ``[] == [1]``. Blocking
    ``submit()`` until the work has signalled ``running`` means the caller's
    ``future.result(timeout=...)`` only starts counting once the work is past
    the worker-entry check, so the deadline can only ever fire on running work.
    """

    def __init__(self) -> None:
        super().__init__(max_workers=1, thread_name_prefix="test-node-running")
        self.running = threading.Event()

    def submit(self, fn, /, *args, **kwargs):
        future = super().submit(fn, *args, **kwargs)
        assert self.running.wait(timeout=10.0), "submitted work never started"
        return future


class _FrozenMonotonic:
    """``graph_compiler``'s ``time`` with a clock that does not advance.

    The worker-entry check compares ``time.monotonic()`` with a deadline taken
    before ``submit()``, so a slow thread start could still refuse the work
    before it runs. Freezing that clock keeps the check out of this test, which
    is about work that got PAST it; the check has its own tests below. The
    timeout itself is ``future.result``'s real wall-clock wait and still fires.
    """

    def __init__(self) -> None:
        self._now = time.monotonic()

    def monotonic(self) -> float:
        return self._now

    def __getattr__(self, name):
        return getattr(time, name)


def test_work_already_running_is_left_to_settle(monkeypatch):
    """Started work must run to completion — no interruption, no replay."""
    pool = _SubmitReturnsOnceRunningPool()
    monkeypatch.setattr(graph_compiler, "_TIMEOUT_EXECUTOR", pool)
    monkeypatch.setattr(graph_compiler, "time", _FrozenMonotonic())
    release_worker = threading.Event()
    call_count: list[int] = []
    finished = threading.Event()

    def _slow() -> str:
        call_count.append(1)
        pool.running.set()
        release_worker.wait(timeout=10.0)
        finished.set()
        return "settled"

    try:
        with pytest.raises(NodeTimeoutError):
            _run_with_timeout(_slow, timeout_s=0.1, node_id="running_node")

        # It was already executing when the deadline fired: the timeout must
        # not have torn it down, and it must not have been re-dispatched.
        assert call_count == [1]
        assert not finished.is_set(), "the deadline fired after the work ended"
        release_worker.set()
        assert finished.wait(timeout=10.0), (
            "in-flight work was disturbed by the deadline; settlement and "
            "uncertain-effect protection require it to finish untouched"
        )
        assert call_count == [1], "timed-out work must never be replayed"
    finally:
        release_worker.set()
        pool.shutdown(wait=False)


# ─── the residual: queue wait must come out of the provider's own cap ─────
#
# Cancelling queued work closes the case where a call spends its WHOLE budget
# in the queue. A call that waits only part of its budget still starts, and
# used to receive the node's FULL timeout as its provider cap, because the
# ModelConfig is built once per node closure. The node future then failed at
# `timeout_seconds` while the provider ran on for the length of the queue
# wait — work continuing past the deadline that admitted it, which is the same
# defect wearing a different hat.


def _node(timeout_seconds: float):
    return NodeDefinition(
        node_id="budgeted", display_name="Budgeted",
        prompt_template="Do {x}.", input_keys=["x"], output_keys=["out"],
        timeout_seconds=timeout_seconds,
    )


def _cap_after_queue_wait(pool, *, route: str, node_timeout: float, wait_s: float):
    """Run one node behind an occupied worker; return its cap and wait bounds.

    Deterministic by construction rather than by timing luck: the wait is
    started only once the pool reports the node's call ENQUEUED, so the
    returned bounds hold regardless of machine load.

    Returns ``(config, lower_wait, upper_wait)`` where the true queue wait the
    node observed is provably within ``[lower_wait, upper_wait]``:

    * ``lower_wait`` is the interval the blocker was still holding the only
      worker AFTER the call was enqueued — the call cannot have waited less.
    * ``upper_wait`` is measured from before the caller even began the node to
      the instant the provider was entered — the call cannot have waited more.
    """
    captured: dict = {}
    release_blocker = threading.Event()

    def fake_provider_call(prompt, system, *, role="writer", config=None):
        captured["config"] = config
        captured["entered_at"] = time.monotonic()
        return "done"

    class _RecordingRouter:
        available_providers = ["claude"]

        def __call__(self, prompt, system, *, policy=None, config=None, **kwargs):
            captured["config"] = config
            captured["entered_at"] = time.monotonic()
            return "done"

    if route == "policy":
        fn = _build_prompt_template_node(
            _node(node_timeout), provider_call=_RecordingRouter(),
            event_sink=None, llm_policy={"preferred": {}},
        )
    else:
        fn = _build_prompt_template_node(
            _node(node_timeout), provider_call=fake_provider_call, event_sink=None,
        )

    blocker_future = pool.submit(lambda: release_blocker.wait(timeout=10.0))
    pool.enqueued.clear()  # the blocker's own submit, not the node's

    outcome: list = []
    caller_began = time.monotonic()
    caller = threading.Thread(target=lambda: outcome.append(fn({"x": "thing"})))
    caller.start()
    try:
        # Wait for the ENQUEUE, never for a guessed interval: only now is the
        # node's call provably sitting in the queue behind the held worker.
        assert pool.enqueued.wait(timeout=10.0), "node call was never enqueued"
        held_from = time.monotonic()
        time.sleep(wait_s)
        lower_wait = time.monotonic() - held_from
        release_blocker.set()
        blocker_future.result(timeout=10.0)
        caller.join(timeout=10.0)
    finally:
        release_blocker.set()

    assert outcome and outcome[0].get("out") == "done", (
        f"node did not complete through the {route} route: {outcome}"
    )
    cfg = captured.get("config")
    assert cfg is not None, "node config was not threaded to the provider"
    return cfg, lower_wait, captured["entered_at"] - caller_began


@pytest.mark.parametrize("route", ["bridge", "policy"])
def test_queue_wait_comes_out_of_the_provider_cap(single_worker_pool, route):
    """The cap must be the REMAINING budget, not merely less than the full one."""
    node_timeout = 30.0
    cfg, lower_wait, upper_wait = _cap_after_queue_wait(
        single_worker_pool, route=route, node_timeout=node_timeout, wait_s=0.4,
    )

    # Both bounds are hard, not tolerances. The cap is timeout - true_wait, and
    # lower_wait <= true_wait <= upper_wait, so the cap is pinned to the actual
    # remaining budget from both sides. "cap < full timeout" would pass on a
    # cap of 29.999s; this does not.
    assert cfg.absolute_cap_s <= node_timeout - lower_wait, (
        f"node provably queued at least {lower_wait:.3f}s but was handed "
        f"{cfg.absolute_cap_s}s of a {node_timeout}s budget — the provider "
        "outlives the node's own deadline by the difference"
    )
    assert cfg.absolute_cap_s >= node_timeout - upper_wait, (
        f"cap {cfg.absolute_cap_s}s is below the remaining budget; the node "
        f"waited at most {upper_wait:.3f}s of {node_timeout}s and must not be "
        "charged more than it waited"
    )
    # The cap the streaming path actually reads resolves to the same number:
    # a non-positive or non-finite value would silently become the 600s default.
    assert cfg.stream_timeout_profile().absolute_cap_s == pytest.approx(
        cfg.absolute_cap_s
    )


def test_remaining_budget_has_no_floor_that_regrants_the_queue_wait(
    single_worker_pool,
):
    """A sub-second node must get what is LEFT, not a floor of its own.

    The earlier correction floored the remaining budget at
    ``min(1.0, timeout_s)``. For any node at or under a second that floor IS
    the node's full timeout, so a 0.9s node that spent 0.4s queued was handed
    0.9s again — the queue wait re-granted under cover of subtracting it.
    ``ModelConfig.stream_timeout_profile()`` accepts any finite positive float,
    so no such floor is needed.
    """
    node_timeout = 0.9
    cfg, lower_wait, upper_wait = _cap_after_queue_wait(
        single_worker_pool, route="bridge", node_timeout=node_timeout, wait_s=0.4,
    )

    assert cfg.absolute_cap_s <= node_timeout - lower_wait, (
        f"sub-second node queued at least {lower_wait:.3f}s of its "
        f"{node_timeout}s budget but was handed {cfg.absolute_cap_s}s back"
    )
    assert cfg.absolute_cap_s >= node_timeout - upper_wait
    assert cfg.absolute_cap_s < node_timeout, "the cap must never be the full timeout"
    assert cfg.absolute_cap_s > 0, "the cap must stay usable, never zero"
    # A non-positive cap is discarded by the resolver in favour of the 600s
    # default, so "small but positive" is load-bearing, not cosmetic.
    assert cfg.stream_timeout_profile().absolute_cap_s == pytest.approx(
        cfg.absolute_cap_s
    )
    # The legacy int-seconds scalar cannot express a sub-second budget; it is
    # floored exactly as the node's own config is, and never raised above it.
    assert cfg.timeout <= max(1, int(node_timeout))


# ─── the other half: cancel() is a race the worker can win ────────────────
#
# Future.cancel() returns False once the pool has picked the item up. Nothing
# in `concurrent.futures` orders the caller's post-deadline cancel() against
# the worker's pickup, so cancellation ALONE cannot prove that no work starts
# after the deadline — under load the worker simply wins sometimes. These
# tests drive that race directly instead of waiting for it to happen.


class _WorkerWinsTheCancelRace:
    """An executor stub where ``cancel()`` always loses, as it does on pickup.

    The submitted callable is captured, never run, so the test itself plays
    the worker and decides exactly WHEN the pickup happens relative to the
    deadline. No scheduling luck is involved in either direction.
    """

    def __init__(self) -> None:
        self.submitted = None
        self.future: concurrent.futures.Future | None = None
        self.did_submit = threading.Event()

    def submit(self, fn, /, *args, **kwargs):
        # Keep the arguments, as a real executor does: the caller may submit
        # ``contextvars.copy_context().run`` with the work as its argument.
        self.submitted = lambda: fn(*args, **kwargs)

        class _Uncancellable(concurrent.futures.Future):
            def cancel(self) -> bool:
                # Exactly what a real Future returns once a worker has it.
                return False

        self.future = _Uncancellable()
        self.did_submit.set()
        return self.future


def test_work_reaching_a_worker_after_the_deadline_is_never_started(monkeypatch):
    """Pickup after the deadline must refuse, not launch the provider."""
    pool = _WorkerWinsTheCancelRace()
    monkeypatch.setattr(graph_compiler, "_TIMEOUT_EXECUTOR", pool)
    invoked: list[str] = []

    with pytest.raises(NodeTimeoutError) as exc_info:
        _run_with_timeout(
            lambda: invoked.append("provider call"),
            timeout_s=0.05,
            node_id="raced_node",
        )
    assert exc_info.value.node_id == "raced_node"
    assert invoked == [], "the call ran before its own deadline even elapsed"

    # The node is terminal and cancel() lost. The worker now picks the item up
    # — strictly after the deadline that admitted it.
    with pytest.raises(NodeTimeoutError):
        pool.submitted()

    assert invoked == [], (
        "a worker picked up queued work AFTER the node's deadline and launched "
        "the provider anyway; future.cancel() had already returned False, so "
        "cancellation alone cannot carry the no-new-work-after-the-deadline "
        "guarantee"
    )


def test_work_reaching_a_worker_within_its_deadline_still_runs(monkeypatch):
    """The guard must refuse only EXPIRED work, never merely queued work."""
    pool = _WorkerWinsTheCancelRace()
    monkeypatch.setattr(graph_compiler, "_TIMEOUT_EXECUTOR", pool)
    invoked: list[str] = []
    returned: list[str] = []

    def _work() -> str:
        invoked.append("provider call")
        return "settled"

    caller = threading.Thread(
        target=lambda: returned.append(
            _run_with_timeout(_work, timeout_s=30.0, node_id="in_time_node"),
        ),
    )
    caller.start()
    try:
        assert pool.did_submit.wait(timeout=10.0), "work was never submitted"
        # Play the worker, well inside the 30s deadline.
        pool.future.set_result(pool.submitted())
        caller.join(timeout=10.0)
    finally:
        if pool.future is not None and not pool.future.done():
            pool.future.cancel()

    assert invoked == ["provider call"], (
        "work picked up with budget remaining was refused; the guard must "
        "stop expired work only, not queued work"
    )
    assert returned == ["settled"]
