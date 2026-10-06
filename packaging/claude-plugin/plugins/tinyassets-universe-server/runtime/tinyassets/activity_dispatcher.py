"""Dispatching activities: one live run each, durably, never only at boot
(harness D2; design D4).

Called on the assigned-queue consumer's tick and whenever an activity is
created or answered. For each activity that needs attention it:

* settles one whose run ENDED: a completed run completes the activity with the
  run's result; a failed run fails it; an interrupted run (its owner process
  provably died -- run recovery's rule) is resumed by a new run;
* claims a queued one (or one whose dispatcher died between claiming and
  binding), starts a run of the Activities branch and binds it.

A live run -- queued, running, or owned by a process that is alive or unknown --
is never replaced. Every step is a compare-and-set in the activity store, so
two dispatchers in one process, or a dispatcher racing the owner, cannot both
act on one activity.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from tinyassets import activity_runner as runner
from tinyassets import agent_activities as activities

logger = logging.getLogger(__name__)

#: Owner-authority refusals that end an activity, and the one that waits for
#: the owner (no model connected yet is something they fix, not a failure).
_WAITS = {"no_serving_assignment": "Connect a model so your agent can work on this."}

#: At most this many runs started per tick across all universes, so a tick
#: has bounded work; the rest wait for the next tick, in order.
STARTS_PER_TICK = 20

_universe_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_tick_lock = threading.Lock()


def _universe_lock(universe_id: str) -> threading.Lock:
    with _locks_guard:
        return _universe_locks.setdefault(universe_id, threading.Lock())


def _run_outcome(base_path: Path, run_id: str) -> tuple[str, str]:
    from tinyassets.runs import get_run

    record = get_run(base_path, run_id) or {}
    output = record.get("output") or {}
    if isinstance(output, str):
        import json

        try:
            output = json.loads(output)
        except ValueError:
            output = {}
    result = output.get("result") if isinstance(output, dict) else ""
    return str(record.get("status") or ""), str(result or record.get("error") or "")


def _settle_ended(base_path: Path, universe_id: str, record: dict) -> bool:
    """A bound run ended: complete, fail, or leave for a resume. True if settled."""
    universe_dir = base_path / universe_id
    status, result = _run_outcome(base_path, record["runner_token"])
    generation = record["runner_generation"]
    try:
        if status == "completed":
            activities.transition(universe_dir, record["activity_id"], activities.COMPLETED,
                                  generation=generation, outcome="done",
                                  result_summary=result or record["result_summary"])
            return True
        if status in {"failed", "cancelled"}:
            activities.transition(universe_dir, record["activity_id"], activities.FAILED,
                                  generation=generation, outcome=f"failed:run_{status}",
                                  result_summary=result or f"The activity run {status}.")
            return True
    except activities.ActivityRefused as exc:
        if exc.kind not in {"superseded", "invalid_transition"}:
            raise
        return True
    return False  # interrupted: claim a new run below


def _start(base_path: Path, universe_id: str, activity_id: str) -> bool:
    """Try to start a run; True if one was started."""
    universe_dir = base_path / universe_id
    record = activities.get(universe_dir, activity_id)
    if record is None:
        return False
    reason = runner.owner_unavailable(base_path, universe_id, record["owner_principal"])
    if reason and reason not in _WAITS:
        try:
            activities.transition(universe_dir, activity_id, activities.FAILED,
                                  outcome="failed:owner_lost")
        except activities.ActivityRefused:
            pass
        return False
    if reason:
        activities.note_waiting_for_seat(universe_dir, activity_id)
        return False
    generation = activities.claim(
        universe_dir, activity_id,
        replaceable=lambda run_id: runner.state(base_path, run_id) == runner.ENDED)
    if generation is None:
        return False
    try:
        runner.start(base_path, universe_id, record, generation)
    except activities.ActivityRefused as exc:
        if exc.kind == "run_refused":
            # Transient: back off, and fail after a bounded number of tries.
            activities.note_start_failure(universe_dir, activity_id, generation, str(exc))
            return False
        try:
            activities.transition(universe_dir, activity_id, activities.FAILED,
                                  generation=generation, outcome=f"failed:{exc.kind}",
                                  result_summary=str(exc))
        except activities.ActivityRefused:
            pass
        return False
    return True


def dispatch_universe(base_path: str | Path, universe_id: str, *,
                      budget: list[int] | None = None) -> None:
    """Settle ended runs and start queued activities in one universe.

    One dispatcher per universe at a time in this process (a second caller --
    the served tool's wake racing the tick -- returns at once; the store's
    claims fence any other process). ``budget`` is a shared one-item counter of
    starts left this tick.
    """
    lock = _universe_lock(universe_id)
    if not lock.acquire(blocking=False):
        return
    try:
        _dispatch_universe(Path(base_path), universe_id, budget)
        _publish_failures(Path(base_path) / universe_id)
    finally:
        lock.release()


def _dispatch_universe(base_path: Path, universe_id: str, budget: list[int] | None) -> None:
    universe_dir = base_path / universe_id
    activities.reconcile_answers(universe_dir)

    def replaceable(run_id: str) -> bool:
        return runner.state(base_path, run_id) == runner.ENDED

    for activity_id in activities.needing_a_runner(universe_dir, replaceable=replaceable):
        try:
            record = activities.get(universe_dir, activity_id)
            if record is None:
                continue
            if record["status"] == activities.IN_PROGRESS and record["runner_token"]:
                if _settle_ended(base_path, universe_id, record):
                    continue
            if budget is not None and budget[0] <= 0:
                return
            if _start(base_path, universe_id, activity_id) and budget is not None:
                budget[0] -= 1
        except Exception:  # noqa: BLE001 - one activity never stops the others
            logger.exception("activity dispatch failed universe=%s activity=%s",
                             universe_id, activity_id)


def dispatch_all(base_path: str | Path, *, starts: int = STARTS_PER_TICK) -> None:
    """Every universe that has an activity store, within one tick's budget."""
    base_path = Path(base_path)
    records = base_path / ".agent-sessions"
    if not records.is_dir():
        return
    budget = [starts]
    for store in sorted(records.glob("*/agent-activities.db")):
        universe_id = store.parent.name
        if universe_id.startswith(".") or not (base_path / universe_id).is_dir():
            continue
        try:
            dispatch_universe(base_path, universe_id, budget=budget)
        except Exception:  # noqa: BLE001 - one universe never stops the others
            logger.exception("activity dispatch failed universe=%s", universe_id)


def tick_in_background(base_path: str | Path) -> bool:
    """Run one ``dispatch_all`` off the caller's thread; skipped while the last
    one is still going. The consumer's tick never waits on activities."""
    if not _tick_lock.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            dispatch_all(base_path)
        except Exception:  # noqa: BLE001
            logger.exception("activity dispatch tick failed")
        finally:
            _tick_lock.release()

    threading.Thread(target=run, name="activity-dispatch", daemon=True).start()
    return True


def _publish_failures(universe_dir: Path) -> None:
    """Retry durable failure delivery every tick; ext_id prevents duplicate chat rows."""
    from tinyassets.addressed_agents import memory_session
    from tinyassets.conversation_store import record_turn

    cursor = None
    while True:
        page = activities.list_page(universe_dir, status=activities.FAILED, cursor=cursor)
        for record in page["activities"]:
            record_turn(
                universe_dir, memory_session(record["owner_principal"], record["agent_id"]),
                "platform",
                f"Background activity {record['title']!r} failed. "
                + (record["result_summary"] or record["outcome"]),
                ext_id=f"activity-failed:{record['activity_id']}",
            )
        cursor = page.get("next_cursor")
        if not cursor:
            return
