"""Durable activity/run binding; execution enters the sole agent session."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tinyassets import agent_activities as activities

LIVE = "live"
ENDED = "ended"


def activity_instructions(universe_dir: Path, activity: dict) -> tuple[str, dict | None]:
    """Read the migrated owner-authored prompt through the admitted box."""
    from tinyassets.control_plane.binding import file_binding
    from tinyassets.graph_compiler import _render_template
    from tinyassets.universe_files import MAX_UNIVERSE_FILE_BYTES, read_universe_text

    names = ("Activities.md", "Activities.tools.json", "Activities.error.txt")
    binding = file_binding(universe_dir)
    if binding is None:
        def read(name):
            try:
                return read_universe_text(universe_dir, name)
            except FileNotFoundError:
                return None
    else:
        snapshot = binding.provider.read_many(
            binding.handle, names, max_total=MAX_UNIVERSE_FILE_BYTES + 4096)
        binding.check_read(snapshot.generation)
        if (set(snapshot.files) | set(snapshot.missing) != set(names)
                or set(snapshot.files) & set(snapshot.missing)):
            raise PermissionError("incomplete activity instruction snapshot")

        def read(name):
            raw = snapshot.files.get(name)
            return raw.decode("utf-8") if raw is not None else None

    error = read(names[2])
    if error:
        raise PermissionError(error)
    template = read(names[0])
    if template is None:
        return (f"Work on this activity until it is done.\n\n{activity['title']}\n\n"
                f"{activity['brief']}\n\n"
                "Finish with a short result summary and file locations.", None)
    tools = read(names[1])
    node = {"tools_allowed": json.loads(tools)} if tools is not None else None
    return _render_template(template, {"title": activity["title"],
                                       "brief": activity["brief"]}), node

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
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.runs import RunCancelledError

    if record.get("outcome") == "agent retired":
        raise RunCancelledError("agent retired")

    # The first run fence precedes session.start's box admission. That call
    # resolves the named agent through the admitted box; do not read an owner
    # path on the host merely to precheck retirement.
    if current_binding() is None:
        return
    from tinyassets.addressed_agents import AgentNotAddressable, resolve
    try:
        resolve(universe_dir.parent, universe_id=universe_dir.name,
                owner=record["owner_principal"], agent_id=record["agent_id"])
    except AgentNotAddressable:
        raise RunCancelledError("agent retired") from None


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
