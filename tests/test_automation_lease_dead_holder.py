"""The universe automation lease: reclaimed from a DEAD holder, never a live one.

A deploy recreates the container and kills a running automation. Its lease
(TTL = the 3h run timeout) stayed behind and froze every automation in that
universe until it expired. The fix makes death a checked fact: each consumer
holds an OS lock on a liveness file for its whole life, and the kernel drops
that lock however the process dies.

The holder dies for real here -- a child Python process takes the lock and the
leases, then `os._exit`s without any cleanup, the way SIGKILL leaves it.

Two gpt-6-astra refute rounds (2026-09-27) shaped the rest: a live holder
keeps its lease even past its TTL; the death proof is never deleted while a
lease names it; a run that ignored cancellation keeps its universe busy, even
for the consumer that started it, until its worker has actually ended.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tinyassets.automations as automations_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import (
    UNIVERSE,
    _consumer_with_inline_executor,
    registered,  # noqa: F401 - fixture
)
from tinyassets import process_liveness
from tinyassets.automations import (
    Automation,
    AutomationRunUnstopped,
    AutomationStore,
    automation_lease_key,
)
from tinyassets.process_liveness import liveness_path as holder_liveness_path
from tinyassets.control_plane.tick import AssignedQueueConsumer


def holder_is_provably_dead(base, holder) -> bool:
    return process_liveness.owner_state(base, holder) == process_liveness.DEAD


pytestmark = pytest.mark.usefixtures("cloud_runtime")

REPO = Path(__file__).resolve().parents[1]
DEAD = "worker_assigned_deadboot"
OTHER = f"{UNIVERSE}_second"


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def _holder_process(
    tmp_path: Path, holder: str, *, die: bool,
    universes: tuple[str, ...] = (UNIVERSE,), ttl_seconds: int = 10800,
):
    """A real process that takes the liveness lock and leases on `universes`."""
    script = textwrap.dedent(
        f"""
        import os, sys
        from datetime import datetime, timezone
        from tinyassets.automations import AutomationStore
        from tinyassets.process_liveness import hold_liveness
        base = {str(tmp_path)!r}
        held = hold_liveness(base, {holder!r})
        assert held.acquired
        for uid in {list(universes)!r}:
            assert AutomationStore(base).acquire_universe_lease(
                uid, holder={holder!r},
                now=datetime.now(timezone.utc), ttl_seconds={ttl_seconds!r},
            )
        print("held", flush=True)
        if {die!r}:
            os._exit(0)  # no finally, no release: what a kill leaves behind
        sys.stdin.readline()
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=REPO,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "held"
    return child


def _acquire(
    tmp_path: Path, universe: str = UNIVERSE, holder: str = "worker_assigned_newboot",
    *, now: datetime | None = None,
) -> bool:
    return AutomationStore(tmp_path).acquire_universe_lease(
        universe, holder=holder, now=now or datetime.now(timezone.utc),
        ttl_seconds=10800,
    )


# -- Death is proven, never guessed ----------------------------------------------


def test_a_killed_holders_leases_are_all_reclaimed_at_once(tmp_path: Path) -> None:
    """Reclaiming one lease must not destroy the proof another universe needs."""
    child = _holder_process(tmp_path, DEAD, die=True, universes=(UNIVERSE, OTHER))
    child.wait(timeout=30)

    assert _acquire(tmp_path, UNIVERSE) is True
    assert _acquire(tmp_path, OTHER) is True
    assert AutomationStore(tmp_path).universe_lease_holder(
        OTHER, now=datetime.now(timezone.utc)
    ) == "worker_assigned_newboot"


def test_a_live_holder_keeps_its_lease_however_late_its_refresh(
    tmp_path: Path,
) -> None:
    """Codex sequence 1: a live but starved holder is NOT dead."""
    child = _holder_process(tmp_path, DEAD, die=False)
    try:
        assert holder_is_provably_dead(tmp_path, DEAD) is False
        assert _acquire(tmp_path) is False
    finally:
        child.communicate("\n", timeout=30)
    # Once it exits without releasing, it is dead and the lease frees.
    assert _acquire(tmp_path) is True


def test_a_live_holder_keeps_its_lease_even_past_its_ttl(tmp_path: Path) -> None:
    """Sequence 1 after expiry: expiry means "not refreshing", not "stopped"."""
    child = _holder_process(tmp_path, DEAD, die=False)
    try:
        later = datetime.now(timezone.utc) + timedelta(hours=4)
        assert _acquire(tmp_path, now=later) is False
    finally:
        child.communicate("\n", timeout=30)


def test_a_holder_with_no_liveness_file_waits_out_its_ttl(tmp_path: Path) -> None:
    """A lease from a build before this, or a malformed id, is never guessed dead."""
    store = AutomationStore(tmp_path)
    for holder in ("worker_assigned_oldbuild", "../escape"):
        universe = f"{UNIVERSE}-{len(holder)}"
        assert _acquire(tmp_path, universe, holder)
        assert holder_is_provably_dead(tmp_path, holder) is False
        assert _acquire(tmp_path, universe) is False
        # ...and expiry still frees it: no liveness file, no liveness claim.
        later = datetime.now(timezone.utc) + timedelta(hours=4)
        assert store.acquire_universe_lease(
            universe, holder="worker_assigned_newboot", now=later, ttl_seconds=60,
        ) is True


def test_a_holder_id_cannot_name_a_path_outside_the_liveness_dir(tmp_path) -> None:
    assert holder_liveness_path(tmp_path, "../../etc/passwd") is None
    assert holder_liveness_path(tmp_path, "") is None
    assert holder_liveness_path(tmp_path, DEAD) == (
        tmp_path / process_liveness.LIVENESS_DIR / f"{DEAD}.lock"
    )


# -- The deploy sequence, through the real consumer --------------------------------


def _started_consumer(tmp_path: Path, monkeypatch) -> AssignedQueueConsumer:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes", lambda _b: []
    )
    consumer = AssignedQueueConsumer(tmp_path, max_concurrency=1, poll_seconds=0.05)
    consumer.start()
    return consumer


def test_after_a_deploy_the_new_consumer_takes_the_killed_ones_universe(
    tmp_path: Path, monkeypatch
) -> None:
    """Round 2 finding 5: the boot sweep deleted the proof before it was used."""
    killed = _holder_process(tmp_path, DEAD, die=True)
    killed.wait(timeout=30)
    consumer = _started_consumer(tmp_path, monkeypatch)
    try:
        assert holder_liveness_path(tmp_path, DEAD).exists()  # a lease names it
        assert _acquire(tmp_path, holder=consumer.consumer_id) is True
    finally:
        consumer.stop()


def test_the_boot_sweep_removes_only_unreferenced_dead_files(
    tmp_path: Path, monkeypatch
) -> None:
    unreferenced = "worker_assigned_goneboot"
    gone = _holder_process(tmp_path, unreferenced, die=True, universes=())
    gone.wait(timeout=30)
    live = _holder_process(
        tmp_path, "worker_assigned_liveboot", die=False, universes=(OTHER,)
    )
    consumer = _started_consumer(tmp_path, monkeypatch)
    try:
        assert not holder_liveness_path(tmp_path, unreferenced).exists()
        assert holder_liveness_path(tmp_path, "worker_assigned_liveboot").is_file()
        assert holder_is_provably_dead(tmp_path, consumer.consumer_id) is False
    finally:
        consumer.stop()
        live.communicate("\n", timeout=30)
    assert consumer._liveness is None
    # A clean stop released its leases itself; the file goes with the lock.
    assert not holder_liveness_path(tmp_path, consumer.consumer_id).exists()


def test_an_unacquired_liveness_lock_leaves_no_false_death_proof(
    tmp_path: Path, monkeypatch
) -> None:
    """Round 2 finding 8: an unlocked file under a live id reads as dead."""
    from tinyassets.singleton_lock import LockAcquisition

    def refused(base, holder):
        path = holder_liveness_path(base, holder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        return LockAcquisition(acquired=False, fd=None, path=path, existing_pid=None)

    monkeypatch.setattr(process_liveness, "hold_liveness", refused)
    consumer = _started_consumer(tmp_path, monkeypatch)
    try:
        assert consumer._liveness is None
        assert not holder_liveness_path(tmp_path, consumer.consumer_id).exists()
        assert holder_is_provably_dead(tmp_path, consumer.consumer_id) is False
    finally:
        consumer.stop()


def test_the_consumer_runs_a_universe_whose_holder_was_killed(
    tmp_path: Path, registered: Automation, monkeypatch  # noqa: F811
) -> None:
    child = _holder_process(tmp_path, DEAD, die=True)
    child.wait(timeout=30)
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module, "run_due_automation",
        lambda base, automation, due_at, **_k: ran.append(due_at) or "ok:ran:r1",
    )
    consumer, _inline = _consumer_with_inline_executor(tmp_path)
    try:
        consumer._run_automations(UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")])
    finally:
        consumer.stop()

    assert ran == ["2026-08-29T12:10:00+00:00"]


# -- Codex sequence 2: our own unstopped run -----------------------------------


def _leave_an_unstopped_run(tmp_path, registered, monkeypatch):  # noqa: F811
    def stuck(base, automation, provider_call, branch, inputs, on_run_started=None):
        on_run_started("run_stuck")
        raise AutomationRunUnstopped("ignored cancellation")

    monkeypatch.setattr(automations_module, "_runtime_authority_reason", lambda *_a: "")
    monkeypatch.setattr(automations_module, "_execute", stuck)
    monkeypatch.setattr(
        automations_module, "_bind_automation_provider_call", lambda *_a: None
    )
    monkeypatch.setattr(automations_module, "_load_branch", lambda *_a: None)
    consumer, _inline = _consumer_with_inline_executor(tmp_path)
    consumer._run_automations(UNIVERSE, [(registered, "2026-08-29T12:10:00+00:00")])
    return consumer


def _worker(monkeypatch, run_id: str) -> Future:
    """The runs module's in-flight future for `run_id` -- the worker itself."""
    future: Future = Future()
    monkeypatch.setattr(
        "tinyassets.runs.get_future", lambda rid: future if rid == run_id else None
    )
    return future


def test_an_unstopped_run_keeps_its_universe_busy_for_its_own_consumer(
    tmp_path: Path, registered: Automation, monkeypatch  # noqa: F811
) -> None:
    worker = _worker(monkeypatch, "run_stuck")
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _b: [UNIVERSE],
    )
    consumer = _leave_an_unstopped_run(tmp_path, registered, monkeypatch)
    key = automation_lease_key(registered)
    assert consumer._unstopped == {key: {"run_stuck"}}
    later: list[str] = []
    monkeypatch.setattr(
        automations_module, "run_due_automation",
        lambda base, automation, due_at, **_k: later.append(due_at) or "ok:ran:r2",
    )
    store = AutomationStore(tmp_path)
    try:
        consumer.poll_once()  # the automation is due, but its last run still runs
        assert later == []
        assert store.universe_lease_holder(
            key, now=datetime.now(timezone.utc)
        ) == consumer.consumer_id
        assert consumer._reap_finished()[1] == {key}

        worker.set_result(None)  # the worker itself has ended
        consumer.poll_once()  # released, and the universe is free again
        assert consumer._unstopped == {}
        assert len(later) == 1
    finally:
        consumer.stop()


def test_a_run_row_marked_terminal_does_not_free_a_running_worker(
    tmp_path: Path, registered: Automation, monkeypatch  # noqa: F811
) -> None:
    """Round 2 finding 7: the row can be orphan-marked while the worker runs on."""
    _worker(monkeypatch, "run_stuck")
    monkeypatch.setattr(
        "tinyassets.runs.get_run", lambda *_a, **_k: {"status": "interrupted"}
    )
    consumer = _leave_an_unstopped_run(tmp_path, registered, monkeypatch)
    try:
        assert consumer._reap_finished()[1] == {automation_lease_key(registered)}
    finally:
        consumer.stop()


def test_a_batch_finishing_during_the_reap_is_still_seen_as_busy(
    tmp_path: Path, monkeypatch
) -> None:
    """Round 2 finding 3: `_unstopped` was read BEFORE the futures were reaped."""
    _worker(monkeypatch, "run_stuck")
    consumer, _inline = _consumer_with_inline_executor(tmp_path)

    class _FinishesWhenAsked:
        # The batch publishes its unstopped run, then its future completes --
        # exactly between the two reads the old order made.
        def done(self):
            consumer._unstopped[UNIVERSE] = {"run_stuck"}
            return True

        def result(self):
            return None

    consumer._active[UNIVERSE] = _FinishesWhenAsked()
    try:
        assert consumer._reap_finished()[1] == {UNIVERSE}
    finally:
        consumer._unstopped.clear()
        consumer.stop()


def test_stop_keeps_liveness_while_an_unstopped_run_is_alive(
    tmp_path: Path, registered: Automation, monkeypatch  # noqa: F811
) -> None:
    consumer = _leave_an_unstopped_run(tmp_path, registered, monkeypatch)
    consumer._hold_liveness()
    try:
        consumer.stop()
        assert consumer._liveness is not None
        assert holder_is_provably_dead(tmp_path, consumer.consumer_id) is False
    finally:
        consumer._unstopped.clear()
        consumer._release_liveness()
    assert consumer._liveness is None
