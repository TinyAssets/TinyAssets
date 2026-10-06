"""The one adapter between an activity and what executes it (harness D2; design D3).

An activity executes as runs of the universe's *Activities* branch: one
owner-authored agent-node branch, part of the harness layer -- seeded on first
use, visible and editable like ``AGENTS.md``. That is the platform's one
canonical way to run an agent with no client attached, so admission,
credential, budget, seats, effect review and run-owner liveness all come from
the foreground run lane unchanged, started with the requestless recipe
automations use (owner binding, ``owner_run_identity``, the per-node admin
guard).

Nothing else in the activity code knows about runs: the record, the session,
status lines, effect intents and the Activity tab go through this module, so a
different substrate replaces only it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tinyassets import agent_activities as activities

BRANCH_NAME = "Activities"
AGENT_NODE_ID = "agent"
LIVE = "live"
ENDED = "ended"

_PROMPT = (
    "You are working on one of your activities in the background; your owner "
    "is not in this chat.\n\nActivity: {title}\n\n{brief}\n\n"
    "Work until it is done. When you finish, reply with a short summary of "
    "the result and where you put it."
)


class ActivityBranchInvalid(activities.ActivityRefused):
    def __init__(self, message: str) -> None:
        super().__init__(message, kind="activity_branch_invalid")


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


def branch_def_id(universe_id: str) -> str:
    return f"activities::{universe_id}"


def _seed(owner_principal: str, universe_id: str) -> dict:
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition

    return BranchDefinition(
        branch_def_id=branch_def_id(universe_id), name=BRANCH_NAME, author=owner_principal,
        visibility="private",
        description=("How your agent works on an activity in the background. Edit the "
                     "agent node's instructions to change how it works; the platform "
                     "chooses which activity a run continues."),
        graph_nodes=[GraphNodeRef(id=AGENT_NODE_ID, node_def_id=AGENT_NODE_ID)],
        edges=[EdgeDefinition(from_node=AGENT_NODE_ID, to_node="END")],
        entry_point=AGENT_NODE_ID,
        node_defs=[NodeDefinition(
            node_id=AGENT_NODE_ID, display_name="Your agent", prompt_template=_PROMPT,
            tools_allowed=["agent"], model_hint="writer", output_keys=["result"],
        )],
        state_schema=[
            {"name": "title", "type": "str", "default": ""},
            {"name": "brief", "type": "str", "default": ""},
            {"name": "result", "type": "str", "default": ""},
        ],
    ).to_dict()


def ensure_branch(base_path: Path, universe_id: str, owner_principal: str) -> Any:
    """The universe's Activities branch, seeded if it is missing.

    An owner who deleted it gets it back; one edited so it no longer has an
    agent node is refused with what to fix, never silently replaced.
    """
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import create_branch_definition_once, get_branch_definition
    from tinyassets.shared_self import _agent_nodes

    try:
        stored = get_branch_definition(base_path, branch_def_id=branch_def_id(universe_id))
    except KeyError:
        stored, _created = create_branch_definition_once(
            base_path, branch_def=_seed(owner_principal, universe_id))
    branch = BranchDefinition.from_dict(stored)
    try:
        agents = _agent_nodes(branch.to_dict())
    except ValueError as exc:
        raise ActivityBranchInvalid(
            f"Your Activities workflow's agent node cannot run ({exc}); fix it in the "
            "workflow, or delete the workflow to get the default back.") from exc
    if not agents:
        raise ActivityBranchInvalid(
            "Your Activities workflow has no agent node, so there is nothing to run an "
            "activity with. Add one back, or delete the workflow to get the default back.")
    # One agent node and nothing else: no step may run before the start
    # barrier links the run to its activity (the barrier is at the agent call).
    extra = [n for n in branch.node_defs if n.node_id not in agents]
    shaped = (
        len(agents) == 1 and not extra and len(branch.graph_nodes or []) <= 1
        and not any(getattr(n, "effects", None) or getattr(n, "invoke_branch_spec", None)
                    or getattr(n, "invoke_branch_version_spec", None)
                    for n in branch.node_defs)
    )
    if not shaped:
        raise ActivityBranchInvalid(
            "Your Activities workflow must be exactly one agent node (edit its "
            "instructions and tools freely); other steps belong in workflows the agent "
            "runs. Delete the workflow to get the default back.")
    if str(branch.author or "") != owner_principal:
        raise ActivityBranchInvalid(
            "Your Activities workflow is not authored by you, so it cannot run your "
            "activities. Delete it to get the default back.")
    return branch


def owner_unavailable(base_path: Path, universe_id: str, owner_principal: str) -> str:
    """'' when the owner may run an activity now, else the reason (automation rules)."""
    from tinyassets.automations import _runtime_authority_reason

    return _runtime_authority_reason(base_path, _Owner(universe_id, owner_principal))


def start(base_path: Path, universe_id: str, record: dict, generation: int) -> str:
    """Start a run of the Activities branch for ``record``, bind it, return its id.

    The run's agent node continues the activity only once the record names
    this run (``agent_activities.linked_generation``); a run started any other
    way -- including this one if the bind loses to a newer claim -- does not.
    """
    from tinyassets.api.permissions import owner_run_identity
    from tinyassets.automations import _authority_guard, _bind_automation_provider_call
    from tinyassets.runs import RUN_STATUS_FAILED, execute_branch_async

    base_path = Path(base_path)
    owner = record["owner_principal"]
    branch = ensure_branch(base_path, universe_id, owner)
    who = _Owner(universe_id, owner, automation_id=record["activity_id"])
    provider_call = _bind_automation_provider_call(base_path, who)
    with owner_run_identity(base_path, universe_id, owner) as bound:
        if not bound:
            raise activities.ActivityRefused("The activity's owner no longer owns this "
                                             "universe.", kind="owner_lost")
        outcome = execute_branch_async(
            base_path, branch=branch,
            inputs={"title": record["title"], "brief": record["brief"]},
            run_name=f"activity:{record['activity_id']}",
            actor=f"universe:{universe_id}", owner_user_id=owner,
            provider_call=provider_call,
            on_node_status=_authority_guard(base_path, who),
            _enqueue_universe_id=universe_id,
        )
    run_id = str(getattr(outcome, "run_id", "") or "")
    if not run_id or outcome.status == RUN_STATUS_FAILED:
        raise activities.ActivityRefused(
            f"The activity's run could not start: {getattr(outcome, 'error', '') or 'refused'}",
            kind="run_refused")
    universe_dir = base_path / universe_id
    if not activities.bind_run(universe_dir, record["activity_id"], generation, run_id):
        stop(base_path, run_id)
    return run_id


def is_activities_branch(snapshot: dict | None, universe_id: str) -> bool:
    return str((snapshot or {}).get("branch_def_id") or "") == branch_def_id(universe_id)


def linked_activity(base_path: Path, universe_id: str, run_id: str, *,
                    wait_s: float = 10.0) -> dict:
    """The start barrier: the activity this run of the Activities branch continues.

    The dispatcher binds the run moments after starting it, so a legitimate run
    finds its record within ``wait_s``; a run nothing binds -- one the agent
    started itself, or one whose dispatcher lost the claim -- is refused before
    its agent node does anything.
    """
    import time

    universe_dir = Path(base_path) / universe_id
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        record = activities.activity_for_run(universe_dir, run_id)
        if record is not None:
            return record
        if time.monotonic() >= deadline:
            raise PermissionError(
                "activity_run_unlinked: this run of your Activities workflow is not "
                "one of your activities; start an activity instead")
        time.sleep(0.1)


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
