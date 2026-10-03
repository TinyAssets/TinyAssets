"""A bound on how many provider subprocesses may exist at once.

Measured on the live box 2026-08-28, and this is the gap it closes:

* `converse` is a **sync** MCP tool handler, so Starlette runs it in the anyio
  threadpool, whose capacity here is **40** (confirmed in production, anyio 4.14.2).
* Each turn spawns a provider CLI subprocess costing **~189 MB RSS / ~77 MB PSS** —
  and that is the floor, measured with `--version`, before any prompt, history or
  inference.
* The container has no memory limit, so an overshoot OOMs the **host**, taking the
  Cloudflare tunnel with it — a total public outage rather than a degraded service.

**The honest ceiling was 8, not 40**, and I said 40 first. `converse` reaches providers
through `call_provider` -> `ProviderRouter.call_sync`, which runs the async chain on a
thread pool of `_SYNC_CALL_MAX_WORKERS = 8`. So 8 x 77 MB is ~620 MB beside a ~390 MB
daemon: tight on a 2 GB box, not the 3.1 GB catastrophe.

That does not make an explicit bound unnecessary, and it is worth being precise about
why. The 8 was **incidental**: its own comment says it exists to stop one slow provider
serializing other sync callers — a LATENCY rationale that happens to cap memory as a side
effect. Anyone raising it for throughput, which is exactly what someone chasing capacity
would do, would silently multiply memory risk with no sign that they had. A bound whose
stated purpose is the thing it protects can be reasoned about; one that protects by
accident cannot.

Admission keeps production's configurable wait deadline (20 seconds by default).
A resident CLI polling a queued child can exhaust the nested reserve; indefinite
admission would hang that chain. Durable continuation must retire the waiting
parent process before this deadline can be removed. Same-process blocking work
can transfer exclusive ownership, and queued waits remain visible.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

#: Concurrent provider subprocesses permitted. **Stays at 6 until a real turn's
#: high-water is measured**, and the story of why is worth keeping.
#:
#: I first derived 6 from "~77 MB PSS each", taken from four concurrent processes and
#: extrapolated linearly. Suspecting that was too conservative, I re-measured with
#: verified overlap and proposed raising it to 10. Cross-family review refuted that too,
#: and my arithmetic was the problem:
#:
#:     verified overlap 13 -> MemAvailable 874 MB
#:     verified overlap 25 -> MemAvailable 403 MB
#:
#:   * I called 786/25 = 31 MB the "marginal" cost. It is the AVERAGE. The marginal
#:     slope between the two points is (874-403)/(25-13) = **39 MB**.
#:   * Per-process cost therefore ROSE with concurrency (24 MB/process at 13, 39 MB
#:     marginal from 13 to 25). My claim that page sharing improves with concurrency was
#:     the opposite of what my own two points said. I fitted a story to two data points
#:     and got the sign wrong.
#:   * My headroom check used 2048 MB total, but the probe's real baseline was 1189 MB
#:     AVAILABLE. The missing 859 MB is roughly 390 MB of daemon plus ~469 MB of kernel,
#:     Docker, tunnel and other services — none of it spendable. Against the right
#:     baseline, 10 x 39 x 3 leaves about 19 MB (12 MB on the unrounded 39.25 slope).
#:     Either way it is not headroom.
#:
#: And `--version` is not the production process tree: a real turn runs `claude -p` with
#: a system prompt, streaming state and tool policy, and when engine MCP is enabled it
#: starts a SECOND Python/FastMCP process. So the floor I measured is not one process.
#:
#: The honest position: 6 is not proven optimal, it is proven not-yet-refuted. The number
#: moves when `get_status.provider_admission` has real turns in it — `refused` rising
#: while `peak_concurrent` sits at the limit is the evidence that would justify raising
#: it, and nothing else should.
_LIMIT_VAR = "TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS"
_DEFAULT_LIMIT = 6

_WAIT_VAR = "TINYASSETS_PROVIDER_ADMISSION_WAIT_S"
_DEFAULT_WAIT_S = 20.0
_POLL_SECONDS = 0.05


class ProviderBusy(RuntimeError):
    """Every provider slot is taken after the admission deadline or a no-wait probe."""


def _positive_int(var: str, default: int) -> int:
    raw = (os.environ.get(var) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer; using %d", var, raw, default)
        return default
    return value if value > 0 else default


def _positive_float(var: str, default: float) -> float:
    raw = (os.environ.get(var) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


#: A Condition + explicit counter rather than a BoundedSemaphore.
#:
#: A semaphore has to be REPLACED when the configured limit changes, and Codex
#: reproduced what that costs: existing holders keep the old object, so two holders
#: under limit=2 plus one admission after dropping to limit=1 gave
#: `{'limit': 1, 'live': 3}` — the advertised bound violated by its own reconfiguration.
#: A counter compared against the CURRENT limit at admission time cannot do that;
#: lowering the limit simply drains as holders finish.
_cv = threading.Condition()
_live = 0
_peak_live = 0
_admitted = 0
_refused = 0
#: Callers currently QUEUED for a slot. Declared here, not only assigned inside
#: `reset_for_tests`: a module global that only a test helper creates reads fine
#: under pytest and raises `NameError` from `admission_snapshot` in production,
#: which is how a status surface breaks with a green suite.
_waiting = 0


#: Retain headroom for nested calls without an in-process blocking parent.
#: Served run_graph queues a worker and returns; CLI/engine-MCP callers cannot
#: transfer a process-local handle. The reserve helps that first layer, but
#: cannot make arbitrary model-side polling chains deadlock-free. A suspended
#: CLI also retains its resident memory: treating it as absent is not safe.
#: In-process blocking children use exclusive transfer below instead.
_NESTED_RESERVE_VAR = "TINYASSETS_PROVIDER_NESTED_RESERVE"
_DEFAULT_NESTED_RESERVE = 1


@dataclass(eq=False)
class HeldProviderSlot:
    """Process-local ownership, never serialized into a tool or carrier.

    Like universe_seats._reenter's depth=1 predicate, ``lent`` permits only
    ONE child at a time. Each borrower receives a fresh ownership handle so
    that it can in turn lend to a grandchild without a recursion/depth cap.
    """

    parent: HeldProviderSlot | None = None
    pid: int = field(default_factory=os.getpid)
    active: bool = True
    lent: bool = False


_held_slot: ContextVar[HeldProviderSlot | None] = ContextVar("held_provider_slot", default=None)
_blocking_parent: ContextVar[HeldProviderSlot | None] = ContextVar(
    "blocking_provider_parent", default=None,
)


def blocking_parent_slot() -> HeldProviderSlot | None:
    """Explicitly lent context; merely copying a provider's context grants nothing."""
    return _blocking_parent.get()


@contextmanager
def blocking_provider_child():
    """Only around a child call whose caller remains blocked until it settles.

    The handle stays in this process, like universe_seats.parent_seat_id.
    Cross-process engine-MCP calls acquire normally. The reserve still gives
    those calls headroom, but cannot prove arbitrary cross-process chains free
    of deadlock: that requires a process-aware suspension protocol.
    """
    parent = _held_slot.get() or _blocking_parent.get()
    if parent is None:
        yield
        return
    # Reserve the transfer for the entire blocking call, including thread
    # startup. A copied context used after this scope closes cannot borrow it.
    with provider_slot(nested=True, parent_slot=parent) as child:
        token = _blocking_parent.set(child)
        try:
            yield
        finally:
            _blocking_parent.reset(token)
            # A timed-out worker may still be executing. Do not resume the
            # parent on the same physical slot until that borrower settles.
            with _cv:
                while child.lent:
                    _cv.wait()
                # Close admission atomically with observing the last return.
                # A worker with a copied context must not borrow between here
                # and provider_slot's finally, after the parent resumes.
                child.active = False


@contextmanager
def independent_provider_work(*, parent_slot=None):
    """Queued work does not suspend its caller and cannot inherit its slot."""
    held = _held_slot.set(None)
    parent = _blocking_parent.set(parent_slot)
    try:
        yield
    finally:
        _blocking_parent.reset(parent)
        _held_slot.reset(held)


def _take_lease_locked(nested: bool, parent: HeldProviderSlot | None):
    if parent is not None and parent.pid == os.getpid() and parent.active and not parent.lent:
        parent.lent = True
        return HeldProviderSlot(parent=parent), _effective_limit(nested)
    limit = _take_locked(nested)
    if limit is None:
        return HeldProviderSlot(), _effective_limit(nested)
    return None, limit


def _return_lease(lease: HeldProviderSlot) -> None:
    with _cv:
        lease.active = False
        # A detached child must not make capacity disappear when its parent
        # unwinds. The last descendant returns the physical slot.
        if not lease.lent:
            while lease.parent is not None:
                lease = lease.parent
                lease.lent = False
                if lease.active:
                    _cv.notify_all()
                    return
            _release()
        _cv.notify_all()


def _effective_limit(nested: bool) -> int:
    """Outer callers cannot take the last ``reserve`` slots; nested work can."""
    limit = _positive_int(_LIMIT_VAR, _DEFAULT_LIMIT)
    if nested:
        return limit
    reserve = _positive_int(_NESTED_RESERVE_VAR, _DEFAULT_NESTED_RESERVE)
    # Never starve outer callers entirely: a reserve at or above the limit would refuse
    # every user turn to protect children that only exist because a turn ran.
    return max(1, limit - min(reserve, limit - 1))


def _take_locked(nested: bool) -> int | None:
    """Take a slot if one is free, under ``_cv``. Returns the limit if not."""
    global _live, _peak_live, _admitted
    limit = _effective_limit(nested)
    if _live < limit:
        _live += 1
        _peak_live = max(_peak_live, _live)
        _admitted += 1
        return None
    return limit


def _try_acquire_now(*, nested: bool = False) -> tuple[bool, int]:
    """Take a slot if one is free right now. Never waits."""
    with _cv:
        limit = _take_locked(nested)
        return (True, _effective_limit(nested)) if limit is None else (False, limit)


def _acquire_waiting(*, nested: bool, on_wait, parent_slot=None) -> HeldProviderSlot:
    """Wait up to the production deadline; return exclusive ownership."""
    global _waiting
    announced = False
    started = time.monotonic()
    deadline = started + _positive_float(_WAIT_VAR, _DEFAULT_WAIT_S)
    try:
        with _cv:
            while True:
                lease, limit = _take_lease_locked(nested, parent_slot)
                if lease is not None:
                    return lease
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _refuse(limit)
                if not announced:
                    announced = True
                    _waiting += 1
                    _log.info("provider admission: all %d slots busy; waiting", limit)
                    if on_wait is not None:
                        try:
                            on_wait(limit)
                        except Exception:  # noqa: BLE001
                            _log.warning("provider admission: on_wait raised", exc_info=True)
                _cv.wait(remaining)
    finally:
        if announced:
            with _cv:
                _waiting -= 1
            _record_wait(time.monotonic() - started)


def _release() -> None:
    global _live
    with _cv:
        _live -= 1
        # notify_all, not notify: async waiters do not sit on the condition
        # variable, and a sync waiter woken for a slot another thread took must
        # re-check rather than the wake being consumed and lost.
        _cv.notify_all()


def _refuse(limit: int) -> ProviderBusy:
    global _refused
    with _cv:
        _refused += 1
    _log.warning("provider admission: all %d slots busy; refusing", limit)
    return ProviderBusy(
        f"All {limit} provider slots are busy right now. Your command center is "
        "working on other turns — try again in a moment."
    )


#: Rolling record of what actually happened here. This exists because capacity in
#: USERS is `slots / turn_duration`, and turn duration was not recorded anywhere: the
#: `started_at`/`finished_at` columns on `run_events` sit microseconds apart with one
#: event per run, so they are bookkeeping, not execution spans. Without this you can
#: state a slots number and cannot honestly state a users-per-box number.
#:
#: Deliberately in-memory and bounded. A capacity metric that itself needs a database
#: write per turn would be adding load to the thing it measures.
_MAX_SAMPLES = 512
_stats_lock = threading.Lock()
_durations: list[float] = []
#: How long queued callers actually waited. Published so a wait that has become a
#: hang is visible as a number rather than as silence.
_waits: list[float] = []


def admission_snapshot() -> dict:
    """What the bound has actually seen. Surfaced through `get_status`.

    Deliberately reports NO derived throughput figure. The obvious one, `limit / p50`,
    is wrong twice over (Codex, 2026-08-28): Little's Law needs effective concurrency
    over MEAN service time, not the limit over a median; and these samples are provider
    *attempts*, so a fallback chain or a judge ensemble contributes several samples per
    user turn while fast failures inflate the median and slow ones deflate it. Reporting
    the raw observations and letting a human do the arithmetic beats publishing a number
    that reads authoritative and is not.
    """
    with _cv:
        live, peak, admitted, refused = _live, _peak_live, _admitted, _refused
        waiting = _waiting
    with _stats_lock:
        d = sorted(_durations)
        w = sorted(_waits)
    out = {
        "limit": _positive_int(_LIMIT_VAR, _DEFAULT_LIMIT),
        "admitted": admitted,
        "refused": refused,
        "live": live,
        "waiting": waiting,
        "peak_concurrent": peak,
        "samples": len(d),
        "sample_unit": "provider attempt, not user turn",
    }
    if w:
        out["wait_seconds"] = {
            "count": len(w),
            "p50": round(w[len(w) // 2], 2),
            "max": round(w[-1], 2),
        }
    if d:
        def _q(p: float) -> float:
            # One indexing rule for every quantile. Mixing `len//2` for the median with
            # `int(len*p)-1` for the rest made p90 come out BELOW p50 on small samples
            # (two samples gave p50=0.03, p90=0.01) — a monotonicity violation that
            # reads as a measurement bug in whatever consumes it.
            idx = min(len(d) - 1, max(0, math.ceil(p * len(d)) - 1))
            return round(d[idx], 2)

        out["attempt_seconds"] = {
            "p50": _q(0.50),
            "p90": _q(0.90),
            "p99": _q(0.99),
            "mean": round(sum(d) / len(d), 2),
            "max": round(d[-1], 2),
        }
    return out


def reset_for_tests() -> None:
    global _live, _peak_live, _admitted, _refused, _waiting
    with _cv:
        _live = _peak_live = _admitted = _refused = _waiting = 0
    with _stats_lock:
        _durations.clear()
        _waits.clear()


@contextmanager
def provider_slot(*, nested: bool = False, on_wait=None, parent_slot=None):
    """Wait for one provider-subprocess slot, or raise ProviderBusy at the deadline.

    **Blocking.** Only for callers that are already on a worker thread. Async callers
    must use :func:`provider_slot_async`, or they stall their event loop — Codex
    reproduced exactly that: with a blocking acquire, two coroutines gathered on one
    loop refused each other because the waiter prevented the holder from finishing.

    ``on_wait(limit)`` fires once if this call has to queue, so a surface with a user
    in front of it can say so.

    Released on every exit path, including exceptions — a slot leaked on an error is a
    permanent capacity loss, and errors are exactly when the system is already busy.
    """
    lease = _acquire_waiting(nested=nested, on_wait=on_wait, parent_slot=parent_slot)
    token = _held_slot.set(lease)
    started = time.monotonic()
    try:
        yield lease
    finally:
        _held_slot.reset(token)
        _return_lease(lease)
        _record(time.monotonic() - started)


@contextmanager
def try_provider_slot(*, nested: bool = False):
    """Hold a slot IF one is free right now, else raise :class:`ProviderBusy`.

    For diagnostics only. A probe that queues behind real user turns is reporting on
    a box it is itself loading, and the answer it eventually gives is about the past.
    """
    ok, limit = _try_acquire_now(nested=nested)
    if not ok:
        raise _refuse(limit)
    started = time.monotonic()
    try:
        yield
    finally:
        _release()
        _record(time.monotonic() - started)


@asynccontextmanager
async def provider_slot_async(*, nested: bool = False, on_wait=None, parent_slot=None):
    """Wait up to the admission deadline without blocking the event loop.

    The wait costs no thread, so other coroutines on the same loop — notably the ones
    already holding slots — keep running and can release. A blocking acquire here
    turned the bound into a self-inflicted deadlock at any limit.

    ``on_wait(limit)`` fires once if this call has to queue.
    """
    # Poll with a NON-blocking attempt and yield between tries, rather than handing a
    # blocking acquire to `asyncio.to_thread`. Cancelling a `to_thread` await cancels
    # only the await: the orphaned worker goes on to acquire a slot nobody will ever
    # release. Codex reproduced exactly that — `waiter_body_entered=False, admitted=2,
    # live=1` — a permanent leak I introduced while fixing the loop-blocking bug.
    #
    # A 50 ms poll costs nothing next to a provider call measured in seconds, and it is
    # cancellation-safe by construction: nothing is in flight to abandon.
    global _waiting
    announced = False
    started_wait = time.monotonic()
    deadline = started_wait + _positive_float(_WAIT_VAR, _DEFAULT_WAIT_S)
    try:
        while True:
            with _cv:
                lease, limit = _take_lease_locked(nested, parent_slot)
            if lease is not None:
                break
            if not announced:
                announced = True
                with _cv:
                    _waiting += 1
                _log.info("provider admission: all %d slots busy; awaiting one", limit)
                if on_wait is not None:
                    try:
                        on_wait(limit)
                    except Exception:  # noqa: BLE001
                        _log.warning("provider admission: on_wait raised", exc_info=True)
            if time.monotonic() >= deadline:
                raise _refuse(limit)
            await asyncio.sleep(_POLL_SECONDS)
    finally:
        if announced:
            with _cv:
                _waiting -= 1
            _record_wait(time.monotonic() - started_wait)
    token = _held_slot.set(lease)
    started = time.monotonic()
    try:
        yield lease
    finally:
        _held_slot.reset(token)
        _return_lease(lease)
        _record(time.monotonic() - started)


def _record(elapsed: float) -> None:
    """Time every exit, failures included. A turn that died after 40 s occupied a slot
    for 40 s; excluding it would flatter the numbers in the conditions worth measuring."""
    with _stats_lock:
        _durations.append(elapsed)
        if len(_durations) > _MAX_SAMPLES:
            del _durations[: len(_durations) - _MAX_SAMPLES]


def _record_wait(elapsed: float) -> None:
    """How long a queued caller waited. Bounded like `_durations`: an unbounded list
    behind an unbounded wait would be the second leak of the same shape."""
    with _stats_lock:
        _waits.append(elapsed)
        if len(_waits) > _MAX_SAMPLES:
            del _waits[: len(_waits) - _MAX_SAMPLES]
