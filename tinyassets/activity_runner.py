"""Durable activity/run binding; execution enters the sole agent session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tinyassets import agent_activities as activities

LIVE = "live"
ENDED = "ended"

class ActivityYielded(Exception):
    """This exact run yielded to its owner's request; no provider retry is due."""


@dataclass(frozen=True, slots=True)
class ActivityRunBinding:
    """Server-captured activity/run/generation, checked at HTTP agent boundaries.

    Constructed from the record found by the foreground run's start barrier,
    never from model inputs. A native CLI's internal tool loop is fenced on the
    engine route (``activity_fence``), and its call is cancelled once this
    check fails (``workflow_agent._until_activity_stops``).
    """

    universe_dir: Path
    activity_id: str
    generation: int
    run_id: str

    def check(self) -> None:
        from tinyassets.runs import is_cancel_requested

        if is_cancel_requested(self.universe_dir.parent, self.run_id):
            raise PermissionError("activity run cancelled")
        record = activities.get(self.universe_dir, self.activity_id)
        if record is not None:
            _check_agent(self.universe_dir, record)
        if activities.holds(self.universe_dir, self.activity_id, self.generation,
                            run_id=self.run_id):
            return
        record = activities.get(self.universe_dir, self.activity_id)
        if (record and record["runner_generation"] == self.generation
                and record["retiring_token"] == self.run_id
                and record["status"] in {activities.WAITING_ON_YOU, activities.SCHEDULED}):
            # An answer may already have requeued it; this retiring run still
            # ends, and the dispatcher cannot replace it until it has ended.
            raise ActivityYielded("Activity yielded to an owner request.")
        raise PermissionError("activity_runner_superseded: this run no longer holds the activity")


@dataclass(frozen=True, slots=True)
class _Owner:
    """What the automation recipe reads off an automation row."""

    universe_id: str
    owner_principal_id: str
    automation_id: str = "activity"


def owner_unavailable(base_path: Path, universe_id: str, owner_principal: str) -> str:
    """'' when the owner may run an activity now, else the reason (automation rules)."""
    from tinyassets.automations import _runtime_authority_reason

    return _runtime_authority_reason(base_path, _Owner(universe_id, owner_principal))


def _check_agent(universe_dir: Path, record: dict) -> None:
    if record.get("agent_id", "main") == "main":
        return
    from tinyassets.addressed_agents import AgentNotAddressable, resolve
    from tinyassets.runs import RunCancelledError

    try:
        resolve(universe_dir.parent, universe_id=universe_dir.name,
                owner=record["owner_principal"], agent_id=record["agent_id"])
    except AgentNotAddressable:
        raise RunCancelledError("agent retired") from None
    if record.get("outcome") == "agent retired":
        raise RunCancelledError("agent retired")


def start(base_path: Path, universe_id: str, record: dict, generation: int) -> str:
    from tinyassets.control_plane.activity_session import enqueue

    return enqueue(Path(base_path), universe_id, record, generation)


def state(base_path: Path, run_id: str) -> str:
    """``live`` while the run may still execute or settle the record, else ``ended``.

    A run whose owner process is alive or unknown stays live: only run recovery
    (a provably dead owner) or the run's own end makes it replaceable.
    """
    from tinyassets.runs import get_run

    record = get_run(base_path, run_id)
    if record is None:
        return ENDED
    return LIVE if record.get("status") in {"queued", "running", "resumed"} else ENDED


def stop(base_path: Path, run_id: str) -> None:
    from tinyassets.runs import request_cancel

    request_cancel(base_path, run_id)
