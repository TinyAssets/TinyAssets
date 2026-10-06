"""The bound between 40 concurrent handlers and a 189 MB subprocess each.

Measured 2026-08-28. Each turn spawns a provider CLI at ~189 MB RSS / ~77 MB PSS, floor,
and the container has no memory limit, so an overshoot OOMs the HOST and takes the
Cloudflare tunnel with it — a total outage rather than a slow service.

The pre-existing ceiling was **8**, not the 40 I first claimed: `converse` reaches
providers through `call_provider` -> `ProviderRouter.call_sync`, which runs on a thread
pool of `_SYNC_CALL_MAX_WORKERS = 8`. That 8 is incidental — its own comment gives a
latency rationale — so raising it for throughput, exactly what chasing capacity does,
would multiply memory risk with nothing to warn you.
"""

from __future__ import annotations

import threading
import time

import pytest

from tinyassets import provider_admission as pa


@pytest.fixture(autouse=True)
def _reset():
    pa.reset_for_tests()
    yield
    pa.reset_for_tests()


def test_concurrency_never_exceeds_the_limit(monkeypatch):
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "3")
    live, peak, lock = [0], [0], threading.Lock()
    start = threading.Barrier(12)

    def worker():
        start.wait()
        with pa.provider_slot():
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1

    ts = [threading.Thread(target=worker) for _ in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert peak[0] <= 3, f"{peak[0]} concurrent provider calls with a limit of 3"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_saturation_refuses_at_production_deadline(monkeypatch, asynchronous, nested):
    """A queued cross-process descendant must retain production's refusal path."""
    import asyncio

    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "2")
    monkeypatch.setenv("TINYASSETS_PROVIDER_ADMISSION_WAIT_S", "0.05")
    assert pa._DEFAULT_WAIT_S == 20.0
    seen = []

    async def wait_async():
        async with pa.provider_slot_async(nested=nested, on_wait=seen.append):
            pytest.fail("saturated admission entered")

    with pa.provider_slot(), pa.provider_slot(nested=True):
        started = time.monotonic()
        with pytest.raises(pa.ProviderBusy, match="try again"):
            if asynchronous:
                asyncio.run(wait_async())
            else:
                with pa.provider_slot(nested=nested, on_wait=seen.append):
                    pytest.fail("saturated admission entered")
        assert 0.04 <= time.monotonic() - started < 2
        snap = pa.admission_snapshot()
        assert snap["live"] == 2
        assert snap["admitted"] == 2
        assert snap["refused"] == 1
        assert snap["waiting"] == 0
        assert len(seen) == 1
    assert pa.admission_snapshot()["live"] == 0


def test_a_blocked_caller_is_told_it_is_waiting(monkeypatch):
    """A wait nobody can see is indistinguishable from a hang."""
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    seen: list[int] = []

    def waiter():
        with pa.provider_slot(on_wait=seen.append):
            pass

    with pa.provider_slot():
        w = threading.Thread(target=waiter)
        w.start()
        time.sleep(0.15)
        assert seen == [1], f"on_wait did not fire exactly once: {seen}"
    w.join(timeout=10)
    # A caller that never queues is never told it is waiting.
    seen.clear()
    with pa.provider_slot(on_wait=seen.append):
        pass
    assert seen == []
    snap = pa.admission_snapshot()
    assert snap["wait_seconds"]["count"] >= 1, "waits are published, not just logged"


def test_diagnostic_probe_refuses_immediately(monkeypatch):
    """`try_provider_slot` refuses without waiting, for diagnostics.

    The Codex auth probe uses it: a probe that queued behind real user turns
    would be reporting on a box it was itself loading.
    """
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    with pa.provider_slot():
        with pytest.raises(pa.ProviderBusy) as exc:
            with pa.try_provider_slot():
                pass
    assert "try again" in str(exc.value)
    # And it takes a free slot when there is one.
    with pa.try_provider_slot():
        pass


def test_the_auth_probe_is_the_only_non_waiting_caller():
    """Mutation guard: a user-facing path must not quietly adopt the refusing form."""
    import pathlib as _pathlib

    from tinyassets.providers import base as provider_base

    src = _pathlib.Path(provider_base.__file__).read_text(encoding="utf-8")
    assert "with try_provider_slot():" in src, "the probe must not queue"
    for module_name in ("tinyassets.providers.router",):
        import importlib

        mod = importlib.import_module(module_name)
        text = _pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "try_provider_slot" not in text, (
            f"{module_name} serves user turns; it must use deadline admission"
        )


def test_a_slot_is_released_when_the_call_raises(monkeypatch):
    """A slot leaked on an error is permanent capacity loss — and errors happen
    precisely when the system is already under load."""
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    with pytest.raises(ValueError):
        with pa.provider_slot():
            raise ValueError("provider blew up")
    with pa.provider_slot():  # must not raise ProviderBusy
        pass


def test_a_waiter_is_admitted_when_a_slot_frees(monkeypatch):
    """Bounding must not mean refusing everyone under transient load."""
    monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
    admitted = []

    def waiter():
        with pa.provider_slot():
            admitted.append(True)

    holder_done = threading.Event()

    def holder():
        with pa.provider_slot():
            time.sleep(0.1)
        holder_done.set()

    h = threading.Thread(target=holder)
    h.start()
    time.sleep(0.02)
    w = threading.Thread(target=waiter)
    w.start()
    h.join(timeout=10)
    w.join(timeout=10)
    assert admitted == [True], "a waiter was refused even though a slot freed up"


def test_the_default_fits_the_box_it_runs_on(monkeypatch):
    """Sized from memory, not from a thread count.

    The pre-existing ceiling was the router's `_SYNC_CALL_MAX_WORKERS = 8` thread pool
    — incidental, since its own comment gives a latency rationale, so raising it for
    throughput would have multiplied memory risk invisibly. This bound is explicitly
    about the ~77 MB each subprocess costs.
    """
    monkeypatch.delenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", raising=False)
    from tinyassets.providers import router

    assert pa._DEFAULT_LIMIT <= router._SYNC_CALL_MAX_WORKERS, (
        "a bound above the thread pool that already gates these calls would never bind "
        "— the pool would cap concurrency first and the explicit limit would be decor"
    )
    # Baseline is what is AVAILABLE, not what is installed. My first version of this
    # assertion budgeted against 2048 MB of total RAM, but the live box reported only
    # 1189 MB available at idle — the other ~469 MB is kernel, Docker, the tunnel and
    # friends, none of which the daemon may spend. Against the wrong baseline the test
    # happily admitted a limit of 17, which is ~400 MB past the real ceiling.
    #
    # Cost per process is the MARGINAL slope measured on the live box between two
    # verified-overlap points, (874-403)/(25-13) = 39 MB — not the 786/25 = 31 MB
    # AVERAGE I first mistook it for. The average understates, because the observed
    # per-process cost ROSE with concurrency rather than falling.
    MEASURED_AVAILABLE_MB = 1189
    MARGINAL_MB_PER_PROCESS = 39
    REAL_TURN_MULTIPLIER = 3  # `--version` loads no prompt, no history, no MCP child

    # "Fits" is not "safe". Merely staying under the available figure admitted a limit
    # of 10, which the module's own comment calls no headroom at all (~19 MB left) — the
    # test protected 6 while still licensing the number Codex had just rejected. Demand
    # an explicit floor of genuinely spare memory instead, so the assertion agrees with
    # the reasoning it is supposed to enforce.
    MIN_HEADROOM_MB = 300

    worst_case = pa._DEFAULT_LIMIT * MARGINAL_MB_PER_PROCESS * REAL_TURN_MULTIPLIER
    spare = MEASURED_AVAILABLE_MB - worst_case
    assert spare >= MIN_HEADROOM_MB, (
        f"{pa._DEFAULT_LIMIT} concurrent turns could need {worst_case} MB of the "
        f"{MEASURED_AVAILABLE_MB} MB available, leaving {spare} MB — under the "
        f"{MIN_HEADROOM_MB} MB floor this box needs for everything else it does"
    )


def test_a_bad_limit_falls_back_rather_than_disabling_the_bound(monkeypatch):
    for bad in ("0", "-5", "banana", ""):
        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", bad)
        assert pa._positive_int(pa._LIMIT_VAR, pa._DEFAULT_LIMIT) == pa._DEFAULT_LIMIT


class TestTurnDurationInstrumentation:
    """The missing half of every users-per-box claim.

    Capacity in USERS is `slots / turn_duration`. Slots were knowable; turn duration was
    not recorded anywhere — `run_events.started_at/finished_at` sit microseconds apart
    with one event per run, so they are bookkeeping, not execution spans. This context
    manager brackets exactly the provider subprocess's lifetime, which makes it the one
    honest place to measure it.
    """

    def test_it_records_how_long_a_turn_held_its_slot(self):
        with pa.provider_slot():
            time.sleep(0.05)
        snap = pa.admission_snapshot()
        assert snap["admitted"] == 1
        assert snap["samples"] == 1
        assert snap["attempt_seconds"]["p50"] >= 0.04

    def test_a_failed_turn_is_still_timed(self):
        """A turn that dies after 40 s occupied a slot for 40 s. Excluding failures
        would flatter the numbers in exactly the conditions worth measuring."""
        with pytest.raises(ValueError):
            with pa.provider_slot():
                time.sleep(0.05)
                raise ValueError("provider died")
        assert pa.admission_snapshot()["samples"] == 1

    def test_it_publishes_no_derived_throughput_figure(self, monkeypatch):
        """It used to report `limit / p50` as sustainable throughput. That is wrong
        twice over (Codex, 2026-08-28): Little's Law wants effective concurrency over
        MEAN service time, not a limit over a median; and these samples are provider
        ATTEMPTS, so a fallback chain or judge ensemble contributes several per user
        turn. A number that reads authoritative and is not is worse than no number."""
        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "10")
        with pa.provider_slot():
            time.sleep(0.02)
        snap = pa.admission_snapshot()
        assert "sustainable_turns_per_second" not in snap
        assert snap["sample_unit"] == "provider attempt, not user turn"
        assert "mean" in snap["attempt_seconds"], "Little's Law needs the mean"

    def test_declined_waits_are_counted_separately_from_turns(self, monkeypatch):
        """Refusals include diagnostic probes and admission deadlines."""
        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")
        with pa.provider_slot():
            with pytest.raises(pa.ProviderBusy):
                with pa.try_provider_slot():
                    pass
        snap = pa.admission_snapshot()
        assert snap["refused"] == 1
        assert snap["admitted"] == 1, "a declined probe must not count as a turn"

    def test_peak_concurrency_is_observed_not_assumed(self, monkeypatch):
        """Whether the bound actually binds is a fact about production, not a setting."""
        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "4")
        start = threading.Barrier(3)

        def worker():
            start.wait()
            with pa.provider_slot():
                time.sleep(0.08)

        ts = [threading.Thread(target=worker) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        assert pa.admission_snapshot()["peak_concurrent"] == 3

    def test_the_sample_buffer_is_bounded(self):
        """An unbounded list on a hot path is a leak, and this one is on every turn."""
        for _ in range(pa._MAX_SAMPLES + 50):
            with pa.provider_slot():
                pass
        assert pa.admission_snapshot()["samples"] <= pa._MAX_SAMPLES

    def test_live_returns_to_zero(self):
        """A live counter that drifts up makes the bound look saturated forever."""
        for _ in range(5):
            with pa.provider_slot():
                pass
        with pytest.raises(ValueError):
            with pa.provider_slot():
                raise ValueError("boom")
        assert pa.admission_snapshot()["live"] == 0


class TestTheAsyncSlotDoesNotStallItsOwnLoop:
    """Codex REJECT 2026-08-28, finding 2 — the one that made the cure worse.

    A blocking `acquire()` inside async router code stalls the event loop, so a waiter
    prevents the very holder it is waiting for from finishing. Reproduced with limit 1,
    a 200 ms wait and 10 ms of admitted work: `[0.201, 'ProviderBusy']`. It reaches
    production through `call_judge_ensemble`, which gathers admission-taking tasks onto
    one loop.
    """

    def test_two_coroutines_on_one_loop_do_not_starve_each_other(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "1")

        async def one():
            async with pa.provider_slot_async():
                await asyncio.sleep(0.01)
            return "ok"

        async def main():
            return await asyncio.gather(one(), one(), return_exceptions=True)

        got = asyncio.run(main())
        assert got == ["ok", "ok"], (
            f"a waiter starved the holder on the same loop: {got}"
        )


class TestTheBoundActuallyBindsInTheRouter:
    """Codex REJECT 2026-08-28, finding 6 — my unit tests were decorative.

    Codex replaced every router admission context with `nullcontext`, disabling
    enforcement completely, and all 13 tests still passed. They proved the primitive
    worked and said nothing about production using it. This asserts the wiring at the
    place the mutant attacked.
    """

    def test_every_provider_dispatch_is_inside_the_bound(self):
        import pathlib

        from tinyassets.providers import router

        src = pathlib.Path(router.__file__).read_text(encoding="utf-8")
        # Every call site of a provider's `complete`, however its awaitable is
        # then awaited (directly, or through the owner's Stop for a chat turn).
        dispatches = src.count("provider.complete(")
        guarded = src.count("async with _provider_slot(")
        assert dispatches > 0
        assert guarded == dispatches, (
            f"{dispatches} provider dispatches but {guarded} inside the bound — an "
            "unguarded dispatch spawns a subprocess the limit never counted"
        )
        assert "with _provider_slot(" not in src.replace("async with _provider_slot(", ""), (
            "a SYNC slot in async router code stalls the event loop"
        )

    def test_a_busy_refusal_is_not_charged_as_a_provider_failure(self):
        """Finding 1: acquiring after `before_provider_launch` meant a refusal
        abandoned the budget, cooled a provider that never started, and reached the
        caller as AllProvidersExhaustedError instead of an actionable 'busy'."""
        import pathlib

        from tinyassets.providers import router

        src = pathlib.Path(router.__file__).read_text(encoding="utf-8")
        body = src.split("async with _provider_slot(", 1)[1][:900]
        assert "before_launch()" in body, (
            "before_provider_launch must happen INSIDE the slot, or a refusal charges "
            "a launch that never occurred"
        )
        # Deadline refusals release reservations for launches that never ran.
        assert "except _ProviderBusy:" in src, "a busy refusal must propagate, not be classified"
        assert "try_provider_slot" not in src, "user turns must use deadline admission"


class TestTheLifecycleGapsCodexFound:
    """Codex REJECT 2026-08-28, findings 3 and 4 — a slot freed while its subprocess
    lived, and a real `codex exec` that spawned outside the bound entirely."""

    def test_cancellation_kills_the_subprocess_it_accounted_for(self):
        """Reproduced as `{'slot_live': 0, 'subprocess_killed': False}`: the slot came
        back while the ~189 MB process was still running, so the bound drifted further
        from reality with every cancellation until the box ran out of memory it
        believed was free."""
        import pathlib

        from tinyassets.providers import codex_provider

        src = pathlib.Path(codex_provider.__file__).read_text(encoding="utf-8")
        # Anchor on the streamed call (2026-08-29: `communicate()` under a
        # wall-clock `wait_for` is gone; the invariant this guards - a
        # BaseException exit still kills the subprocess - is unchanged).
        # Window widened from 1400: the legacy communicate() branch now sits
        # between the streamed call and the BaseException guard it protects.
        body = src.split("await _stream_codex_exec(", 1)[1][:2600]
        assert "except BaseException:" in body, (
            "only asyncio.TimeoutError was cleaned up; cancellation is the case that "
            "actually happens, and CancelledError is a BaseException"
        )
        # It must actually kill, not merely re-raise.
        after = body.split("except BaseException:", 1)[1]
        assert "_terminate(proc)" in after

    def test_an_already_dead_process_does_not_mask_the_real_exception(self):
        """`proc.kill()` on a finished process raises ProcessLookupError on POSIX,
        which would replace CancelledError with something confusing."""
        from tinyassets.providers.codex_provider import _terminate

        class _Dead:
            returncode = 0

            def kill(self):
                raise ProcessLookupError("already reaped")

        _terminate(_Dead())  # must not raise

    def test_the_auth_probe_is_single_flighted_for_real(self, monkeypatch):
        """A bare lock only SERIALIZES. Codex reproduced two simultaneous misses
        spawning two probes 81 ms apart, because the second took the lock after the
        first released and then ran its own. The second caller has to wait for the
        first's ANSWER."""
        import threading as _t

        from tinyassets.providers import base

        base._auth_probe_memo.invalidate()
        calls, lock = [], _t.Lock()

        def slow_probe(timeout_s):
            with lock:
                calls.append(1)
            time.sleep(0.05)
            return {"status": "ok", "detail": "fake"}

        monkeypatch.setattr(base, "_codex_live_auth_probe_uncached", slow_probe)
        start = _t.Barrier(6)
        out = []

        def worker():
            start.wait()
            out.append(base._codex_live_auth_probe(1.0))

        ts = [_t.Thread(target=worker) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        assert len(out) == 6, "every caller must get an answer"
        assert len(calls) == 1, (
            f"{len(calls)} probes for 6 simultaneous callers — serialized, not "
            "single-flighted; each spawns a real ~189 MB codex exec"
        )
        base._auth_probe_memo.invalidate()


class TestTheBoundBindsBehaviourally:
    """Codex beat my source-shape assertions TWICE.

    First with a `nullcontext` plugin, then by replacing the router's imported
    `_provider_slot` at runtime with an async no-op: two judge providers overlapped at
    `peak=2` under limit 1, admission reported `admitted=0`, and all 19 tests still
    passed. Source counting cannot see a runtime swap. This drives the real router and
    asserts the ADMISSION LEDGER moved — which no substitution can fake.
    """

    def test_a_real_router_call_holds_a_slot_during_the_provider_call(self, monkeypatch):
        """Constructs a real ProviderRouter and awaits a real dispatch.

        Three weaker versions lost to Codex first: a `nullcontext` plugin, a runtime
        replacement of the module attribute, and finally a LOCAL shadow of
        `_provider_slot` inside each dispatch method — which defeats source counting
        AND `router._provider_slot is provider_slot_async`, because both still look
        right. My previous attempt exercised the primitive and never entered the router
        at all, so it passed the shadow mutant too.

        The only thing a bypass cannot fake is the ledger moving while the provider is
        executing, so that is what this asserts, from inside `complete()` on a real
        router call.
        """
        import asyncio

        from tinyassets.providers.base import ModelConfig, ProviderResponse
        from tinyassets.providers.router import ProviderRouter

        pa.reset_for_tests()
        seen = {}

        class _Observing:
            name = "codex"
            family = "openai"

            async def complete(self, prompt, system, config, *, universe_dir=None):
                seen["live"] = pa.admission_snapshot()["live"]
                return ProviderResponse(
                    text="ok", provider="codex", model="fake", family="openai",
                    latency_ms=0.0,
                )

        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from tinyassets.provider_work_authority import ProviderInvocationCarrier
        from tinyassets.providers.base import UniverseContext

        # Hard Rule 15: a real dispatch needs an owner's authority.
        carrier = MagicMock(spec=ProviderInvocationCarrier)
        carrier._receipt = MagicMock(principal_id="owner")
        carrier.provider = "codex"
        carrier.role = "judge"
        carrier.operation = "run_graph"
        carrier.max_tokens = 10
        carrier.max_cost_microunits = 5
        carrier.selected_model = None
        carrier.native_selection = None
        carrier.settlement_owner = None
        carrier.validate_for_call.return_value = "codex"

        router = ProviderRouter(providers={"codex": _Observing()})
        with patch("tinyassets.providers.router._provider_invocation_carrier",
                   return_value=carrier):
            asyncio.run(router.call_judge_ensemble(
                "p", "s", ModelConfig(max_tokens=10), operation="run_graph",
                universe_context=UniverseContext(
                    universe_dir=Path("u-admission"), provider_invocation=carrier,
                ),
            ))

        # Deliberately NOT a skip on failure: a test that opts out when it cannot reach
        # the provider is the same decorative failure in a new costume.
        assert "live" in seen, "the router never reached the provider; test proves nothing"
        assert seen["live"] >= 1, (
            "a real router dispatch executed the provider while the admission ledger "
            "showed no slot held — the bound is bypassed on this path"
        )

    def test_nested_work_may_use_the_reserve_that_outer_turns_cannot(self, monkeypatch):
        """Codex round 3: my deferral of nested starvation was not defensible, because
        `run_graph` children already carry a typed `provider_invocation` carrier — the
        distinction is available exactly where it is needed. Six outer holders produced
        six AllProvidersExhaustedError and zero nested launches."""
        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "2")
        monkeypatch.setenv("TINYASSETS_PROVIDER_NESTED_RESERVE", "1")

        with pa.provider_slot():  # one outer turn: outer limit is 2-1 = 1
            # The immediate probe observes the same outer limit without waiting
            # for the admission deadline.
            with pytest.raises(pa.ProviderBusy):
                with pa.try_provider_slot():
                    pass
            with pa.provider_slot(nested=True):  # its child may use the reserve
                pass

    def test_the_reserve_can_never_starve_outer_turns_entirely(self, monkeypatch):
        """A reserve at or above the limit would refuse every user turn to protect
        children that only exist because a turn ran."""
        monkeypatch.setenv("TINYASSETS_MAX_CONCURRENT_PROVIDER_CALLS", "2")
        monkeypatch.setenv("TINYASSETS_PROVIDER_NESTED_RESERVE", "99")
        assert pa._effective_limit(nested=False) >= 1
        with pa.provider_slot():
            pass

    def test_a_carrier_bearing_call_is_recognised_as_nested(self):
        """The router decides nested-ness from the carrier; if that reading breaks, the
        reserve silently stops applying and the starvation returns."""
        from tinyassets.providers.router import _is_nested

        class _Ctx:
            provider_invocation = object()

        class _Plain:
            provider_invocation = None

        assert _is_nested(_Ctx()) is True
        assert _is_nested(_Plain()) is False
        assert _is_nested(None) is False


def test_the_snapshot_works_on_a_fresh_import_with_no_test_reset():
    """Every counter must exist at import, not only after `reset_for_tests`.

    `_waiting` was assigned only inside `reset_for_tests`, which every test in
    this file calls -- so the suite was green while `admission_snapshot()` raised
    `NameError` in production, where nothing calls a test helper. Reloading the
    module is the only way to observe the real import-time state.
    """
    import importlib

    fresh = importlib.reload(pa)
    snap = fresh.admission_snapshot()
    for key in ("limit", "admitted", "refused", "live", "waiting", "peak_concurrent"):
        assert key in snap, key
    assert snap["waiting"] == 0
