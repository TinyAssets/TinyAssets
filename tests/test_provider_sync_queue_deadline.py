"""Expired work must not launch from the SECOND pool either.

``graph_compiler._run_with_timeout`` closed this hole for the pool it owns
(``_TIMEOUT_EXECUTOR``, ``graph_compiler.py:340``): a worker-entry deadline
check refuses work whose deadline already passed
(``graph_compiler.py:407-412``), and ``_deadline_cfg`` subtracts the queue
wait from the provider cap it hands over (``graph_compiler.py:1464-1494``).

There is a SECOND queue on the same call path: ``ProviderRouter._thread_pool``.
Both sync wrappers now refuse expired work at worker entry and deduct queue wait
from an explicit caller cap. These tests drive the REAL wrapper pair through
``_run_with_timeout`` and stub final provider I/O; ``call``, ``call_with_policy``,
``call_sync`` and ``call_with_policy_sync`` all run unmodified.

The test callable supplies a fresh cap when pool 1 picks it up. It does not run
the compiled node's ``_deadline_cfg`` closure, which deducts first-pool waits of
at least 50ms and deliberately preserves smaller scheduling jitter. Therefore
an outer ``NodeTimeoutError`` alone does NOT prove the router's handed-in budget
expired. The expiry proofs hold pool 2 until its observed submit time plus that
cap: an upper bound on the real deadline, which is armed before submit. Both
wrappers are also exercised with a deliberately occupied first pool.

Scope, deliberately: no thread is killed and no call is replayed. Work that
got past the pool-2 worker entry before expiry must settle untouched — that
is the third test, and it is the guard against over-correcting.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tinyassets.graph_compiler import NodeTimeoutError, _run_with_timeout
from tinyassets.provider_work_authority import ProviderInvocationCarrier
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.router import ProviderRouter

# The node's remaining budget at the moment the router wrapper is entered.
# Short and real: the wrapper is given a genuine deadline, not a mocked clock.
NODE_BUDGET_S = 0.5

# How long we let a released queue item actually reach the provider before
# concluding it never will. Only an upper bound on the observation, never a
# budget granted to anything under test.
SETTLE_S = 3.0


class _RecordingProvider(BaseProvider):
    """Final provider I/O, and nothing above it.

    Registered under a name the ``writer`` chain actually contains
    (``router.py:199``) so real chain resolution selects it. ``complete`` is
    the abstract I/O boundary (``base.py:1305``); every routing, policy,
    timeout and pool decision above it stays production code.
    """

    name = "claude-code"
    family = "anthropic"

    def __init__(self, hold_s: float = 0.0) -> None:
        self._hold_s = hold_s
        self.launched = threading.Event()
        self.returned = threading.Event()
        # (monotonic_at_launch, absolute_cap_s seen, legacy timeout seen)
        self.launches: list[tuple[float, float | None, int]] = []
        self.completions = 0

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir=None,
    ) -> ProviderResponse:
        self.launches.append(
            (time.monotonic(), config.absolute_cap_s, int(config.timeout)),
        )
        self.launched.set()
        if self._hold_s:
            time.sleep(self._hold_s)
        self.completions += 1
        self.returned.set()
        return ProviderResponse(
            text="ok", provider=self.name, model="test", family=self.family,
            latency_ms=0.0,
        )


class _SubmitSignallingPool(concurrent.futures.ThreadPoolExecutor):
    """A real pool that reports when an item has been ENQUEUED.

    The point of these tests is that the router's item is sitting in pool 2's
    queue while the node's deadline burns. Sleeping and assuming the worker
    thread got there first is a flake, so the enqueue is observed rather than
    presumed. Nothing about the pool's behaviour is altered.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cond = threading.Condition()
        self.submits = 0
        self.submitted_at: list[float] = []
        # Every blocker handed out by _occupy_sole_worker, so the fixture can
        # release them all in its finally. A test that fails mid-way must not
        # leave a worker parked on a 30s wait.
        self.blockers: list[threading.Event] = []

    def submit(self, fn, /, *args, **kwargs):
        future = super().submit(fn, *args, **kwargs)
        with self._cond:
            self.submits += 1
            self.submitted_at.append(time.monotonic())
            self._cond.notify_all()
        return future

    def wait_for_submits(self, n: int, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self.submits >= n, timeout=timeout)


def _owner_carrier() -> MagicMock:
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier.provider = "claude-code"
    carrier.role = "writer"
    carrier.operation = "run_graph"
    carrier.max_tokens = 1000
    carrier.max_cost_microunits = 1000
    carrier.selected_model = None
    carrier.native_selection = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = "claude-code"
    return carrier


_CARRIER = _owner_carrier()
# Hard Rule 15: every router call carries one universe owner's authority.
_BOUND = {
    "operation": "run_graph",
    "universe_context": UniverseContext(
        universe_dir=Path("u-sync-queue"), provider_invocation=_CARRIER,
    ),
}


@pytest.fixture(scope="module", autouse=True)
def _owner_authority():
    with patch(
        "tinyassets.providers.router._provider_invocation_carrier",
        return_value=_CARRIER,
    ):
        yield


@pytest.fixture(scope="module", autouse=True)
def _warm_the_router_once(_owner_authority):
    """Pay the router's one-time cold start before anything is timed.

    The FIRST ``call_sync`` in a process spends ~0.7s inside ``call`` on
    lazy imports and admission/auth-health initialisation, measured on this
    host on 2026-09-23. That is real work, but it is not queue wait, and these
    tests have sub-second budgets: left unpaid it lands inside whichever test
    runs first, which would attribute it to the queue in the deduction test and
    push the provider launch past the node's deadline in the already-running
    test. Module-scoped and autouse so the cost is paid once, on a throwaway
    router and the PRODUCTION pool, before any test installs its own.
    """
    ProviderRouter(providers={"claude-code": _RecordingProvider()}).call_sync(
        "writer", "warmup", "", config=ModelConfig(timeout=30), **_BOUND,
    )


@pytest.fixture()
def sync_pool(monkeypatch):
    """Install a REAL single-worker provider-sync pool.

    One worker is the whole mechanism: it makes "the pool is saturated"
    reachable with a single blocker instead of a timing race, and it is the
    same ``ThreadPoolExecutor`` class production uses (``router.py:1911``),
    at a size the test controls. No limit is raised anywhere.
    """
    pool = _SubmitSignallingPool(max_workers=1, thread_name_prefix="test-sync-pool")
    monkeypatch.setattr(ProviderRouter, "_thread_pool", pool, raising=True)
    try:
        yield pool
    finally:
        # Release first, THEN shut down. An assertion that fires before the
        # test's own release.set() would otherwise leave the sole worker
        # blocked for the full 30s wait, and `shutdown(wait=False)` does not
        # unblock a running worker — it only declines new work. Releasing here
        # keeps a failing test fast and keeps the pool's threads from
        # outliving it.
        for release in pool.blockers:
            release.set()
        pool.shutdown(wait=False)


def _occupy_sole_worker(pool: _SubmitSignallingPool) -> threading.Event:
    """Hold pool 2's only worker until the returned event is set."""
    release = threading.Event()
    started = threading.Event()

    def _blocker() -> None:
        started.set()
        release.wait(timeout=30)

    pool.blockers.append(release)
    pool.submit(_blocker)
    assert started.wait(timeout=5), "blocker never reached the sole pool worker"
    return release


@pytest.fixture(params=[0.0, 0.2], ids=["free-first-pool", "occupied-first-pool"])
def first_pool_delay(request, monkeypatch):
    """Exercise the handoff after a real, observed wait in the compiler pool."""
    if not request.param:
        yield
        return
    from tinyassets import graph_compiler

    pool = _SubmitSignallingPool(max_workers=1, thread_name_prefix="test-node-pool")
    release = _occupy_sole_worker(pool)
    enqueued = []

    def _release_after_submit():
        enqueued.append(pool.wait_for_submits(2, timeout=SETTLE_S))
        # A bounded real queue hold, started only once the tested node submitted.
        release.wait(timeout=request.param)
        release.set()

    controller = threading.Thread(target=_release_after_submit, daemon=True)
    monkeypatch.setattr(graph_compiler, "_TIMEOUT_EXECUTOR", pool)
    controller.start()
    try:
        yield
    finally:
        release.set()
        controller.join(timeout=SETTLE_S)
        pool.shutdown(wait=True)
    assert enqueued == [True], "node never entered the deliberately occupied first pool"


def _await_handed_in_deadline(pool: _SubmitSignallingPool, budget_s: float) -> None:
    """Keep pool 2 occupied until its explicit caller budget certainly expired.

    The router arms its deadline BEFORE submit. Observing submit plus the full
    cap is an upper bound even if this thread or the first pool was delayed;
    no fixed scheduling allowance or mocked clock is involved.
    """
    with pool._cond:
        assert pool.submits >= 2, "router item must be observed in the occupied pool"
        expired_by = pool.submitted_at[-1] + budget_s
    threading.Event().wait(timeout=max(0.0, expired_by - time.monotonic()))
    assert time.monotonic() >= expired_by, "router's handed-in budget has not expired"


def _node_cfg(budget_s: float) -> ModelConfig:
    """What the compiler hands the wrapper as the node's REMAINING budget.

    Mirrors ``_deadline_cfg`` (``graph_compiler.py:1483-1494``): the legacy
    int scalar carries that field's own ``max(1, int(...))`` representation
    floor, while ``absolute_cap_s`` carries the real sub-second budget.
    """
    return ModelConfig(timeout=max(1, int(budget_s)), absolute_cap_s=budget_s)


def _drive_node(router: ProviderRouter, budget_s: float, node_id: str) -> dict:
    """Run the real timeout wrapper with an explicit provider cap.

    ``_run_with_timeout`` is the real compiler entry point; the callable it
    receives is the real router wrapper, as at ``graph_compiler.py:1534``.
    ``outcome`` lets the test see when the pool-2 item finally SETTLED —
    returned or refused — without assuming which shape a fix takes.
    """
    outcome: dict = {}

    def _node_work():
        try:
            result = router.call_sync(
                "writer", "p", "s", config=_node_cfg(budget_s), **_BOUND,
            )
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome["error"] = exc
            raise
        outcome["result"] = result
        return result

    with pytest.raises(NodeTimeoutError):
        _run_with_timeout(_node_work, timeout_s=budget_s, node_id=node_id)
    return outcome


def _await_settled(outcome: dict, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if outcome:
            return True
        time.sleep(0.01)
    return bool(outcome)


def test_expired_node_work_does_not_launch_from_the_provider_sync_queue(
    sync_pool, first_pool_delay,
):
    """Work still queued after its handed-in deadline must launch nothing.

    RED on ff1320d5: the item is picked up after the node is already terminal
    and drives a provider call nobody awaits — the exact condition
    ``_run_with_timeout``'s worker guard refuses one hop earlier.
    """
    provider = _RecordingProvider()
    router = ProviderRouter(providers={provider.name: provider})
    release = _occupy_sole_worker(sync_pool)

    expired_at = time.monotonic() + NODE_BUDGET_S
    outcome = _drive_node(router, NODE_BUDGET_S, "queued-past-deadline")

    # The node is terminal, and the router's item is provably still QUEUED:
    # the sole worker is held, and pool 2 took a second submit.
    assert sync_pool.wait_for_submits(2, timeout=1.0), (
        "router wrapper never submitted to the provider-sync pool; the test "
        "would prove nothing about queue wait"
    )
    assert not provider.launched.is_set(), "provider launched while pool 2 was saturated"
    assert time.monotonic() >= expired_at, "node budget had not actually elapsed"
    _await_handed_in_deadline(sync_pool, NODE_BUDGET_S)

    release.set()
    assert _await_settled(outcome, SETTLE_S), (
        "the queued provider-sync item never settled after the pool freed up"
    )

    assert provider.launches == [], (
        "provider launched AFTER its handed-in deadline passed while queued in pool 2"
    )


def test_partial_provider_sync_queue_wait_is_deducted_before_launch(sync_pool):
    """Waiting part of the budget in pool 2 must shrink the provider cap.

    RED on ff1320d5: the cap reaching the provider is the one ``_deadline_cfg``
    computed at POOL 1 pickup (``graph_compiler.py:1482``), with pool 2's wait
    never subtracted — so the call is handed budget the queue already spent.
    """
    budget_s = 1.2
    held_s = 0.6
    provider = _RecordingProvider()
    router = ProviderRouter(providers={provider.name: provider})
    release = _occupy_sole_worker(sync_pool)

    outcome: dict = {}
    entered_at: list[float] = []

    def _node_work():
        entered_at.append(time.monotonic())
        try:
            result = router.call_sync(
                "writer", "p", "s", config=_node_cfg(budget_s), **_BOUND,
            )
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome["error"] = exc
            raise
        outcome["result"] = result
        return result

    worker = threading.Thread(
        target=lambda: _run_with_timeout(
            _node_work, timeout_s=budget_s, node_id="partial-queue-wait",
        ),
        daemon=True,
    )
    worker.start()
    assert sync_pool.wait_for_submits(2, timeout=2.0), (
        "router wrapper never submitted to the provider-sync pool"
    )

    # Release BEFORE the node expires: this call is entitled to run, but only
    # on what is left of its budget.
    time.sleep(held_s)
    release.set()
    assert provider.launched.wait(timeout=SETTLE_S), "provider never launched"
    worker.join(timeout=SETTLE_S)

    assert len(provider.launches) == 1
    launched_at, cap_seen, _legacy = provider.launches[0]
    assert entered_at, "node work never started"
    queue_wait = launched_at - entered_at[0]
    assert queue_wait >= held_s * 0.8, (
        f"the call did not actually wait in pool 2 (waited {queue_wait:.3f}s)"
    )

    remaining = budget_s - queue_wait
    assert cap_seen is not None, "provider received no absolute cap at all"
    # Tolerance is one-directional: a cap at or below the remaining budget is
    # correct, a cap above it is time the queue already spent being re-granted.
    assert cap_seen <= remaining + 0.05, (
        f"provider was handed a {cap_seen:.3f}s cap with only {remaining:.3f}s "
        f"of the node's {budget_s}s budget left: pool 2's {queue_wait:.3f}s "
        "queue wait was never subtracted (router.py:1945 computes the timeout "
        "before submit; router.py:1951 arms it only at pickup)"
    )


def test_call_already_past_the_pool_worker_entry_settles_untouched(sync_pool):
    """The guard against over-correcting: started work is never interrupted.

    Green today and must STAY green under any fix. A call that reached the
    provider before expiry is not killed and not replayed — an interrupted
    provider call leaves an effect nobody can classify.

    Scope of the claim, precisely: ``complete`` holds with a blocking
    ``time.sleep``, which parks the wrapper's event loop thread, so this proves
    the call RUNS TO COMPLETION EXACTLY ONCE past the node's deadline. It does
    NOT exercise ``asyncio`` cancellation and proves nothing about it; the
    cancellation-on-sync-timeout path has its own coverage at
    ``tests/test_provider_stream_and_classify.py``
    (``test_sync_timeout_cancels_and_kills_the_subprocess``).
    """
    provider = _RecordingProvider(hold_s=NODE_BUDGET_S * 3)
    router = ProviderRouter(providers={provider.name: provider})

    outcome = _drive_node(router, NODE_BUDGET_S, "already-running")

    assert provider.launched.is_set(), "provider never started; wrong scenario"
    assert provider.returned.wait(timeout=SETTLE_S), (
        "an already-running provider call was interrupted by the node timeout"
    )
    assert _await_settled(outcome, SETTLE_S)
    assert provider.completions == 1, (
        f"in-flight call must run exactly once, saw {provider.completions} "
        "completions (a replay would double a provider side effect)"
    )
    assert len(provider.launches) == 1, "the settled call was replayed"


def test_expired_work_does_not_launch_from_call_with_policy_sync_either(
    sync_pool, first_pool_delay,
):
    """The SECOND wrapper on the same pool, covered by its own evidence.

    ``call_with_policy_sync`` (``router.py:1852``) is the wrapper the compiler's
    policy path actually uses (``graph_compiler.py:1534`` via
    ``_call_policy_router_with_retry``), and it queues on the SAME
    ``_thread_pool`` with the same pre-submit timeout computation. The earlier
    tests drive ``call_sync`` only, so on their evidence alone this wrapper was
    asserted by inspection and not proven. It is proven here.

    ``policy=None`` falls through to the standard role chain by design
    (``router.py:1666``), which keeps the policy DSL out of a test about queue
    time while leaving the wrapper itself entirely real.
    """
    provider = _RecordingProvider()
    router = ProviderRouter(providers={provider.name: provider})
    release = _occupy_sole_worker(sync_pool)

    outcome: dict = {}

    def _node_work():
        try:
            result = router.call_with_policy_sync(
                "writer", "p", "s", None, config=_node_cfg(NODE_BUDGET_S), **_BOUND,
            )
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome["error"] = exc
            raise
        outcome["result"] = result
        return result

    expired_at = time.monotonic() + NODE_BUDGET_S
    with pytest.raises(NodeTimeoutError):
        _run_with_timeout(
            _node_work, timeout_s=NODE_BUDGET_S, node_id="policy-past-deadline",
        )

    assert sync_pool.wait_for_submits(2, timeout=1.0), (
        "the policy wrapper never submitted to the provider-sync pool"
    )
    assert not provider.launched.is_set()
    assert time.monotonic() >= expired_at
    _await_handed_in_deadline(sync_pool, NODE_BUDGET_S)

    release.set()
    assert _await_settled(outcome, SETTLE_S), (
        "the queued policy item never settled after the pool freed up"
    )
    assert provider.launches == [], (
        "call_with_policy_sync launched a provider AFTER its handed-in deadline passed"
    )


def test_an_unqueued_call_keeps_the_callers_own_config(sync_pool):
    """The correction must be invisible when there was no queue wait.

    Without this, "deduct the queue wait" is indistinguishable from "shrink
    every call a little": a call that goes straight to a free worker has spent
    nothing, so the provider must see the caller's config OBJECT, untouched —
    not a rebuilt one that happens to be close. Mirrors the jitter threshold
    the compiler already applies (``graph_compiler.py:1476-1480``).
    """
    provider = _RecordingProvider()
    router = ProviderRouter(providers={provider.name: provider})
    cfg = _node_cfg(NODE_BUDGET_S * 4)

    result = router.call_sync("writer", "p", "s", config=cfg, **_BOUND)

    assert result.text == "ok"
    assert len(provider.launches) == 1
    _launched_at, cap_seen, legacy_seen = provider.launches[0]
    assert cap_seen == cfg.absolute_cap_s, (
        f"an unqueued call had its cap rewritten ({cap_seen} vs "
        f"{cfg.absolute_cap_s}); only real queue wait may be deducted"
    )
    assert legacy_seen == cfg.timeout, "an unqueued call had its legacy timeout rewritten"


def test_a_caller_without_a_deadline_is_never_refused(sync_pool):
    """No explicit cap means no deadline was handed over — never a refusal.

    ``ModelConfig()`` leaves ``absolute_cap_s`` unset; it resolves to the 600s
    backstop (``base.py:269``), which is a safety cap and not a caller's
    remaining budget. Reading the backstop — or the legacy int scalar, which has
    a ``max(1, int(...))`` floor and cannot carry a sub-second budget at all —
    as a deadline would refuse work nobody ever put a clock on. The guard must
    fire only on an EXPLICIT hand-over.
    """
    provider = _RecordingProvider()
    router = ProviderRouter(providers={provider.name: provider})
    release = _occupy_sole_worker(sync_pool)

    done: dict = {}
    worker = threading.Thread(
        target=lambda: done.setdefault(
            "result", router.call_sync(
                "writer", "p", "s", config=ModelConfig(), **_BOUND,
            ),
        ),
        daemon=True,
    )
    worker.start()
    assert sync_pool.wait_for_submits(2, timeout=2.0)

    # Wait out several multiples of the legacy-scalar floor before releasing.
    time.sleep(NODE_BUDGET_S * 2)
    release.set()
    worker.join(timeout=SETTLE_S)

    assert provider.launches, (
        "a call whose caller set no absolute cap was refused for queue wait; "
        "the 600s default backstop is not a deadline"
    )
    assert done.get("result") is not None
