"""The automation lease is per agent (branch), with a declared overlap policy.

Two agents in one universe run side by side; one agent never overlaps itself.
A due run whose agent is still running follows its row's ``overlap``: ``queue``
(the default, and every automation's behaviour under the old per-universe
lease), ``skip``, or ``cancel_previous``.

Driven through the real consumer (``poll_once``), the real due scan, the real
``run_due_automation`` and the real lease store. The only substitute is
``_execute``, the seam ``tinyassets.automations`` names for tests: it fakes
the graph a run executes, never the fence, the admission or the checks.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tinyassets.automations as automations_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automation_lease_dead_holder import _holder_process
from tests.test_automations import (
    NOW,
    OWNER,
    UNIVERSE,
    _consumer_with_inline_executor,
    _FakeOutcome,
    _refusal_rows,
    _seed_branch,
    _seed_owner,
)
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.automations import (
    Automation,
    AutomationStore,
    AutomationUnavailable,
    agent_lease_prefix,
    automation_lease_key,
    register_automation,
)
from tinyassets.control_plane.tick import AssignedQueueConsumer

pytestmark = pytest.mark.usefixtures("cloud_runtime")

WRITER = "branch_agent_writer"
READER = "branch_agent_reader"
BOB = "acct_bob"
BOB_UNIVERSE = "universe_bob"
BOB_AGENT = "branch_bob_agent"


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path, branch_def_id=WRITER)
    _seed_branch(tmp_path, branch_def_id=READER)
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    return tmp_path


def _automate(base: Path, branch: str, *, overlap: str = "", name: str = "a",
              universe: str = UNIVERSE, owner: str = OWNER) -> Automation:
    """A cadence registered long ago, so it is due now."""
    return register_automation(
        base, universe_id=universe, owner_principal_id=owner, name=name,
        branch_def_id=branch, interval_seconds=600, overlap=overlap, now=NOW,
    )


def _wake(base: Path, branch: str, *, overlap: str = "") -> Automation:
    return register_automation(
        base, universe_id=UNIVERSE, owner_principal_id=OWNER, name="wake",
        branch_def_id=branch, not_before="2026-01-01T00:00:00Z", overlap=overlap,
    )


class _Blocking:
    """The `_execute` seam: each run blocks until released, like a long agent."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered: list[str] = []
        self._started = 0
        self._lock = threading.Lock()

    def __call__(self, base_path, automation, provider_call, branch, inputs,
                 on_run_started=None):
        with self._lock:
            run_id = f"run_{automation.branch_def_id}_{self._started}"
            self._started += 1
        # Publish the run (and its lease run id) BEFORE counting it as entered,
        # so a test that waits for entry sees the run fully started.
        if callable(on_run_started):
            on_run_started(run_id)
        with self._lock:
            self.entered.append(automation.automation_id)
        assert self.release.wait(20), "the test never released the run"
        return _FakeOutcome(run_id=run_id, status="completed")

    def wait_for(self, count: int) -> None:
        deadline = time.monotonic() + 20
        while len(self.entered) < count and time.monotonic() < deadline:
            time.sleep(0.01)


def _threaded_consumer(base: Path, concurrency: int = 4) -> AssignedQueueConsumer:
    return AssignedQueueConsumer(base, max_concurrency=concurrency)


def _attempt_statuses(base: Path, automation_id: str) -> list[str]:
    with sqlite3.connect(AutomationStore(base).db_path) as conn:
        return [
            str(row[0]) for row in conn.execute(
                "SELECT status FROM automation_attempts WHERE automation_id = ? "
                "ORDER BY claimed_at", (automation_id,),
            )
        ]


# -- Two agents, one universe -------------------------------------------------


def test_two_agents_in_one_universe_run_at_the_same_time(
    home: Path, monkeypatch,
) -> None:
    writer = _automate(home, WRITER)
    reader = _automate(home, READER)
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        assert consumer.poll_once() == 2
        graph.wait_for(2)
        # Both are inside their run at once: neither waited for the other.
        assert sorted(graph.entered) == sorted(
            [writer.automation_id, reader.automation_id]
        )
        store = AutomationStore(home)
        now = datetime.now(timezone.utc)
        for automation in (writer, reader):
            assert store.universe_lease_holder(
                automation_lease_key(automation), now=now
            ) == consumer.consumer_id
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_slots_go_one_agent_per_universe_before_a_second(
    home: Path, monkeypatch,
) -> None:
    """Alice's three due agents cannot take every slot ahead of Bob's first."""
    _seed_owner(home, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_branch(home, branch_def_id=BOB_AGENT, author=BOB)
    from tests.test_automations import _copy_assignment_to

    _copy_assignment_to(home, universe_id=BOB_UNIVERSE, owner=BOB)
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE, BOB_UNIVERSE],
    )
    _seed_branch(home, branch_def_id="branch_agent_third")
    for branch in (WRITER, READER, "branch_agent_third"):
        _automate(home, branch, name=branch)
    bobs = _automate(home, BOB_AGENT, universe=BOB_UNIVERSE, owner=BOB)
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home, concurrency=2)
    try:
        assert consumer.poll_once() == 2
        graph.wait_for(2)
        assert bobs.automation_id in graph.entered
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


# -- One agent never overlaps itself -----------------------------------------------


def test_two_rows_due_for_one_agent_start_one_run(home: Path, monkeypatch) -> None:
    """With free workers, a second submission for the same agent would start at
    once and re-take the lease its own consumer holds."""
    wakes = {_wake(home, WRITER).automation_id, _wake(home, WRITER).automation_id}
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        time.sleep(0.3)  # a second run, if one was started, has entered by now
        # Created in the same second, so which one runs first is a tie.
        assert len(graph.entered) == 1 and set(graph.entered) <= wakes
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_queue_waits_for_the_running_agent_then_runs(home: Path, monkeypatch) -> None:
    first = _automate(home, WRITER, name="first")
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        second = _wake(home, WRITER)  # the same agent, due now, default queue
        assert second.overlap == "queue"
        assert consumer.poll_once() == 0
        assert graph.entered == [first.automation_id]
        assert _attempt_statuses(home, second.automation_id) == []
        graph.release.set()
        deadline = time.monotonic() + 20
        while consumer.poll_once() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        graph.wait_for(2)
        assert graph.entered == [first.automation_id, second.automation_id]
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_skip_drops_a_cadence_run_but_a_one_shot_wake_waits(
    home: Path, monkeypatch,
) -> None:
    """A cadence under skip spends the instant; a one-shot wake has only one
    fire, so it waits for the agent and then runs -- never retired unseen."""
    _automate(home, WRITER, name="first")
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        wake = _wake(home, WRITER, overlap="skip")
        cadence = _automate(home, WRITER, overlap="skip", name="cadence")
        assert consumer.poll_once() == 0
        assert _attempt_statuses(home, cadence.automation_id) == ["skipped"]
        assert AutomationStore(home).get(cadence.automation_id).retired_at == ""
        # The wake is untouched and says why it is waiting, where the owner looks.
        assert _attempt_statuses(home, wake.automation_id) == []
        assert AutomationStore(home).get(wake.automation_id).retired_at == ""
        assert _refusal_rows(home)[f"automation:{wake.automation_id}"] == (
            "waiting_for_previous_run"
        )
        assert len(graph.entered) == 1
        graph.release.set()
        deadline = time.monotonic() + 20
        while wake.automation_id not in graph.entered and time.monotonic() < deadline:
            consumer.poll_once()
            time.sleep(0.05)
        assert wake.automation_id in graph.entered
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_cancel_previous_cancels_the_agents_running_run(
    home: Path, monkeypatch,
) -> None:
    cancelled: list[str] = []
    monkeypatch.setattr("tinyassets.runs.request_cancel",
                        lambda base, run_id: cancelled.append(run_id))
    _automate(home, WRITER, name="first")
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        wake = _wake(home, WRITER, overlap="cancel_previous")
        assert consumer.poll_once() == 0
        assert cancelled == [f"run_{WRITER}_0"]
        # It waits for the running one to stop, then runs.
        graph.release.set()
        deadline = time.monotonic() + 20
        while consumer.poll_once() == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        graph.wait_for(2)
        assert graph.entered[-1] == wake.automation_id
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_cancel_previous_never_reaches_another_universes_run(
    home: Path, monkeypatch,
) -> None:
    """The run id is read from THIS agent's lease key, which names the universe."""
    store = AutomationStore(home)
    now = datetime.now(timezone.utc)
    bob_key = f"{agent_lease_prefix(BOB_UNIVERSE)}{WRITER}"
    assert store.acquire_universe_lease(
        bob_key, holder="worker_assigned_bob", now=now, ttl_seconds=3600,
    )
    store.set_lease_run(bob_key, holder="worker_assigned_bob", run_id="run_bobs")
    cancelled: list[str] = []
    monkeypatch.setattr("tinyassets.runs.request_cancel",
                        lambda base, run_id: cancelled.append(run_id))
    _wake(home, WRITER, overlap="cancel_previous")
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module, "_execute",
        lambda *a, **k: ran.append("x") or _FakeOutcome(run_id="r"),
    )
    consumer, _inline = _consumer_with_inline_executor(home)
    try:
        consumer.poll_once()
    finally:
        consumer.stop()
    assert cancelled == []
    assert ran == ["x"]  # Bob's agent is not Alice's agent


def test_another_process_running_the_agent_holds_it_until_it_dies(
    home: Path, monkeypatch,
) -> None:
    automation = _automate(home, WRITER)
    key = automation_lease_key(automation)
    child = _holder_process(home, "worker_assigned_otherboot", die=False,
                            universes=(key,))
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module, "_execute",
        lambda *a, **k: ran.append("x") or _FakeOutcome(run_id="r"),
    )
    consumer, _inline = _consumer_with_inline_executor(home)
    try:
        consumer.poll_once()
        assert ran == []  # a live holder keeps its agent
        child.stdin.write("\n")
        child.stdin.flush()
        child.wait(timeout=30)
        # Still unexpired, but its holder exited without releasing: the kernel
        # dropped its liveness lock, so the lease is reclaimed now, not in 3h.
        consumer.poll_once()
        assert ran == ["x"]
    finally:
        consumer.stop()


# -- Registration and surface ---------------------------------------------------


def test_an_unknown_overlap_is_refused(home: Path) -> None:
    with pytest.raises(AutomationUnavailable) as caught:
        _automate(home, WRITER, overlap="parallel")
    assert caught.value.reason == "overlap_invalid"
    assert AutomationStore(home).list(universe_id=UNIVERSE) == []


def test_the_connector_creates_and_shows_an_overlap(home: Path) -> None:
    import json

    from tinyassets.api.automations import automations
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    with identity_context(Identity(
        user_id=OWNER, username=OWNER,
        capabilities=["tinyassets.universe.write", "tinyassets.universe.admin"],
    )):
        out = automations(action="create", universe_id=UNIVERSE, payload=json.dumps({
            "name": "n", "branch_def_id": WRITER, "interval_seconds": 600,
            "overlap": "cancel_previous",
        }))
        default = automations(action="create", universe_id=UNIVERSE, payload=json.dumps({
            "name": "d", "branch_def_id": READER, "interval_seconds": 600,
        }))
    assert out["automation"]["overlap"] == "cancel_previous", out
    assert default["automation"]["overlap"] == "queue", default


def test_skip_applies_when_another_process_is_running_the_agent(
    home: Path, monkeypatch,
) -> None:
    """The overlap policy reads the SHARED lease, not only this process's map."""
    cadence = _automate(home, WRITER, overlap="skip")
    wake = _wake(home, WRITER, overlap="skip")
    child = _holder_process(home, "worker_assigned_otherboot", die=False,
                            universes=(automation_lease_key(cadence),))
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module, "_execute",
        lambda *a, **k: ran.append("x") or _FakeOutcome(run_id="r"),
    )
    consumer, _inline = _consumer_with_inline_executor(home)
    try:
        consumer.poll_once()
    finally:
        consumer.stop()
        child.stdin.write("\n")
        child.stdin.flush()
        child.wait(timeout=30)
    assert ran == []
    assert _attempt_statuses(home, cadence.automation_id) == ["skipped"]
    assert _attempt_statuses(home, wake.automation_id) == []
    assert AutomationStore(home).get(wake.automation_id).retired_at == ""


# -- Folded from the gpt-6-astra refute round (2026-09-28) -----------------------


def test_cancel_previous_never_cancels_its_own_running_occurrence(
    home: Path, monkeypatch,
) -> None:
    """A cadence stays due while it runs (last_due_at moves on finish); a poll in
    between must not read its own run as a previous one (refute P1)."""
    cancelled: list[str] = []
    monkeypatch.setattr("tinyassets.runs.request_cancel",
                        lambda base, run_id: cancelled.append(run_id))
    _automate(home, WRITER, overlap="cancel_previous")
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        assert consumer.poll_once() == 0
        assert cancelled == []
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_a_freed_slot_goes_to_the_owner_running_fewer_agents(
    home: Path, monkeypatch,
) -> None:
    """Across polls, not only within one: Alice already runs an agent, so the
    one free slot goes to Bob's waiting agent, not Alice's second (refute P1)."""
    from tests.test_automations import _copy_assignment_to

    _seed_owner(home, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_branch(home, branch_def_id=BOB_AGENT, author=BOB)
    _copy_assignment_to(home, universe_id=BOB_UNIVERSE, owner=BOB)
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE, BOB_UNIVERSE],
    )
    _automate(home, WRITER, name="alice-first")
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home, concurrency=2)
    try:
        # Only Alice is due: her first agent takes a slot.
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        _automate(home, READER, name="alice-second")
        bobs = _automate(home, BOB_AGENT, universe=BOB_UNIVERSE, owner=BOB)
        assert consumer.poll_once() == 1
        graph.wait_for(2)
        assert graph.entered[1] == bobs.automation_id
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_an_unstopped_run_still_holds_its_slot(home: Path, monkeypatch) -> None:
    """A timed-out run whose worker ignored cancellation is still running, so
    it counts against concurrency (refute P1)."""
    from concurrent.futures import Future

    worker: Future = Future()
    monkeypatch.setattr("tinyassets.runs.get_future",
                        lambda rid: worker if rid == "run_stuck" else None)
    consumer = _threaded_consumer(home, concurrency=2)
    try:
        consumer._unstopped[f"{agent_lease_prefix(UNIVERSE)}{WRITER}"] = {"run_stuck"}
        free, busy = consumer._reap_finished()
        assert free == 1
        assert busy == {f"{agent_lease_prefix(UNIVERSE)}{WRITER}"}
    finally:
        worker.set_result(None)
        consumer.stop(timeout=10)


def test_skip_applies_even_when_every_slot_is_full(home: Path, monkeypatch) -> None:
    """With no free slot the scan still runs, so the policy is not deferred
    until a slot frees and the wake then runs anyway (refute P2)."""
    _automate(home, WRITER, name="first")
    graph = _Blocking()
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home, concurrency=1)
    try:
        assert consumer.poll_once() == 1
        graph.wait_for(1)
        cadence = _automate(home, WRITER, overlap="skip", name="cadence")
        assert consumer.poll_once() == 0
        assert _attempt_statuses(home, cadence.automation_id) == ["skipped"]
    finally:
        graph.release.set()
        consumer.stop(timeout=10)


def test_lease_keys_cannot_collide_across_universes() -> None:
    """(U, B::C) and (U::B, C) are different agents, and one universe's prefix
    reaches none of another's keys (refute P2)."""
    from tinyassets.automations import lease_key_universe

    def key(universe: str, branch: str) -> str:
        return f"{agent_lease_prefix(universe)}{branch}"

    assert key("U", "B::C") != key("U::B", "C")
    assert not key("U::B", "C").startswith(agent_lease_prefix("U"))
    assert not key("U:1:x", "C").startswith(agent_lease_prefix("U"))
    assert lease_key_universe(key("U::B", "C")) == "U::B"
    assert lease_key_universe(key("u_1", "b")) == "u_1"
    assert lease_key_universe("universe_bare") == "universe_bare"


def test_an_event_wake_keeps_its_subscriptions_overlap(home: Path) -> None:
    """A subscription's wakes are its agent acting, so they carry its policy."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.runs import RUN_STATUS_RUNNING, create_run, update_run_status

    with identity_context(Identity(user_id=OWNER, username=OWNER)):
        register_automation(
            home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="follow",
            branch_def_id=READER, event_type="run_completed",
            event_filter={"branch_def_id": WRITER}, overlap="skip",
        )
        run_id = create_run(home, branch_def_id=WRITER, thread_id="t", inputs={},
                            actor=OWNER)
    update_run_status(home, run_id, status=RUN_STATUS_RUNNING)
    update_run_status(home, run_id, status="completed", finished_at=1.0)
    [wake] = [a for a in AutomationStore(home).list(universe_id=UNIVERSE)
              if a.trigger_kind == "once"]
    assert (wake.branch_def_id, wake.overlap) == (READER, "skip")


class _SelfWakingRun:
    """The `_execute` seam for a branch that re-wakes itself through its own
    `run_completed` subscription. Like a real run, it emits the event at its
    terminal transition -- while the consumer still holds the agent -- and then
    waits until the test lets it finish."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.entered: list[str] = []
        self.emitted = threading.Event()
        self.finish = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, base_path, automation, provider_call, branch, inputs,
                 on_run_started=None):
        from tinyassets.automation_events import emit_run_completed

        with self._lock:
            run_id = f"run_loop_{len(self.entered)}"
            self.entered.append(automation.automation_id)
        if callable(on_run_started):
            on_run_started(run_id)
        emit_run_completed(
            self.base, run_id=run_id, branch_def_id=automation.branch_def_id,
            outcome="completed", actor=OWNER, queue_universe_id=UNIVERSE,
            cause_principal=OWNER,
        )
        self.emitted.set()
        assert self.finish.wait(20), "the test never finished the run"
        self.finish.clear()
        return _FakeOutcome(run_id=run_id, status="completed")


def test_a_self_waking_loop_survives_the_wake_its_own_run_fires(
    home: Path, monkeypatch,
) -> None:
    """Replays 2026-09-28 22:43:46 on the founder's universe: a `run_completed`
    subscription (overlap skip) on its own branch. The run's completion stored
    the next one-shot wake while that run still held the agent; the next poll
    applied skip, and the wake retired as `skipped_overlap` -- the 24/7 loop
    ended silently. The wake must wait for the agent and run, every time."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    with identity_context(Identity(user_id=OWNER, username=OWNER)):
        register_automation(
            home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="loop",
            branch_def_id=WRITER, event_type="run_completed",
            event_filter={"branch_def_id": WRITER}, overlap="skip",
        )
    _wake(home, WRITER, overlap="skip")
    graph = _SelfWakingRun(home)
    monkeypatch.setattr(automations_module, "_execute", graph)
    consumer = _threaded_consumer(home)
    try:
        for turn in range(3):
            deadline = time.monotonic() + 20
            while len(graph.entered) <= turn and time.monotonic() < deadline:
                consumer.poll_once()
                time.sleep(0.02)
            assert len(graph.entered) == turn + 1, f"the loop stopped after {turn} runs"
            assert graph.emitted.wait(20)
            graph.emitted.clear()
            # The overlap window: the next wake is due, its predecessor still runs.
            assert consumer.poll_once() == 0
            wakes = [a for a in AutomationStore(home).list(
                universe_id=UNIVERSE, include_retired=True) if a.trigger_kind == "once"]
            assert not [a for a in wakes if a.pause_reason == "skipped_overlap"], wakes
            waiting = [a for a in wakes if not a.retired_at]
            assert len(waiting) == 1, wakes
            assert _refusal_rows(home)[f"automation:{waiting[0].automation_id}"] == (
                "waiting_for_previous_run"
            )
            graph.finish.set()
        assert len(set(graph.entered)) == 3, "each run was a different wake"
    finally:
        graph.finish.set()
        consumer.stop(timeout=10)


def _first_run(home: Path, monkeypatch) -> list[str]:
    ran: list[str] = []
    monkeypatch.setattr(
        automations_module, "_execute",
        lambda base, automation, *a, **k: ran.append(automation.automation_id)
        or _FakeOutcome(run_id="r"),
    )
    consumer, _inline = _consumer_with_inline_executor(home)
    try:
        consumer.poll_once()
    finally:
        consumer.stop()
    return ran


def _served_at(home: Path, automation: Automation, moment: datetime) -> None:
    """The cadence last ran at ``moment`` (its fence advanced there)."""
    with sqlite3.connect(AutomationStore(home).db_path) as conn:
        conn.execute("UPDATE automations SET last_due_at = ? WHERE automation_id = ?",
                     (moment.isoformat(), automation.automation_id))


def test_a_waiting_wake_runs_before_a_cadence_served_since(
    home: Path, monkeypatch,
) -> None:
    """A wake kept waiting is not starved by the agent's own short cadence,
    which falls due again on every free poll (refute P2, round 1)."""
    cadence = register_automation(
        home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="fast",
        branch_def_id=WRITER, interval_seconds=1, now=NOW,
    )
    later = NOW + timedelta(minutes=1)
    wake = register_automation(
        home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="wake",
        branch_def_id=WRITER, not_before=later.isoformat(), overlap="skip", now=later,
    )
    _served_at(home, cadence, datetime.now(timezone.utc) - timedelta(seconds=2))
    listed = [a.automation_id for a in AutomationStore(home).list(universe_id=UNIVERSE)]
    assert listed == [cadence.automation_id, wake.automation_id], "precondition"
    assert _first_run(home, monkeypatch) == [wake.automation_id]


def test_a_cadence_is_not_starved_by_a_stream_of_fresh_wakes(
    home: Path, monkeypatch,
) -> None:
    """An interval's due_at collapses onto its LATEST instant, so ordering by it
    would let a self-waking chain's fresh wake win every free poll forever. It
    is ordered by its first UNSERVED instant instead (refute P2, round 2)."""
    cadence = register_automation(
        home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="fast",
        branch_def_id=WRITER, interval_seconds=1, now=NOW,
    )
    fresh = datetime.now(timezone.utc) - timedelta(seconds=3)
    _served_at(home, cadence, fresh - timedelta(seconds=10))
    wake = register_automation(
        home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="wake",
        branch_def_id=WRITER, not_before=fresh.isoformat(), overlap="skip", now=fresh,
    )
    # Precondition: by due_at alone the fresh wake looks older than the cadence.
    due = dict((a.automation_id, d) for a, d in automations_module.due_automations(
        home, universe_id=UNIVERSE, now=datetime.now(timezone.utc)))
    assert due[wake.automation_id] < due[cadence.automation_id], due
    assert _first_run(home, monkeypatch) == [cadence.automation_id]


def test_a_cron_row_is_not_starved_by_a_stream_of_fresh_wakes(
    home: Path, monkeypatch,
) -> None:
    """A cron row is due only inside its minute, and its due_at is that minute's
    bucket, so a wake created in the previous minute always looked older and a
    self-waking chain could take every minute from it (refute P2, round 3)."""
    cron = register_automation(
        home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="minutely",
        branch_def_id=WRITER, cron_expr="* * * * *", now=NOW,
    )
    bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    before = bucket - timedelta(seconds=1)
    wake = register_automation(
        home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="wake",
        branch_def_id=WRITER, not_before=before.isoformat(), overlap="skip", now=before,
    )
    due = dict((a.automation_id, d) for a, d in automations_module.due_automations(
        home, universe_id=UNIVERSE, now=datetime.now(timezone.utc)))
    assert due[wake.automation_id] < due[cron.automation_id], due
    assert _first_run(home, monkeypatch) == [cron.automation_id]


def test_a_row_that_cannot_take_its_agent_says_so_on_the_row(
    home: Path, monkeypatch,
) -> None:
    """A lost lease race is shown where the owner reads the automation, not
    only under the universe key (refute P2)."""
    wake = _wake(home, WRITER, overlap="skip")
    store = AutomationStore(home)
    assert store.acquire_universe_lease(
        UNIVERSE, holder="worker_legacy_elsewhere",
        now=datetime.now(timezone.utc), ttl_seconds=600,
    )
    monkeypatch.setattr(
        "tinyassets.control_plane.tick.AssignedQueueConsumer."
        "_agent_running_elsewhere", lambda self, key, now: False,
    )
    ran: list[str] = []
    monkeypatch.setattr(automations_module, "_execute",
                        lambda *a, **k: ran.append("x") or _FakeOutcome(run_id="r"))
    consumer, _inline = _consumer_with_inline_executor(home)
    try:
        consumer.poll_once()
    finally:
        consumer.stop()
    assert ran == []
    assert _refusal_rows(home).get(f"automation:{wake.automation_id}", "").startswith(
        "universe_busy:"
    ), _refusal_rows(home)
    assert store.get(wake.automation_id).retired_at == ""
