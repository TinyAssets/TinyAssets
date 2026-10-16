"""``start(agent_ref, input, parent)``: the one way an agent starts (S2, C8).

Every agent run begins here: a chat message (and a wake or an owner's answer,
which are chat messages the platform sends), an agent node of a workflow run,
and a child session one agent hands another with ``ta agent run``. The
``agent_ref`` is resolved exactly once, through the one definition loader
(``agent_definition.load_agent``): ``main`` is the command center root, any
other name is an ``agents/<name>/`` directory. What the agent is -- its
instructions, its settings -- comes from nowhere else.

``parent`` says who is starting the agent and under which admission, and it
selects how the turn executes:

* :class:`OwnerTurn` -- the owner's conversation turn, already admitted by
  ``universe_server.converse`` (seat, recorded message, live turn). Runs the
  served writer turn on the owner's thread and returns the reply text.
* :class:`RunStep` -- an agent node of an admitted workflow run.
  :class:`Activity` uses the same provider session for a durable activity
  without constructing a workflow. Both run the shared-self turn.
* :class:`ChildOf` -- an agent starting another agent from inside its own
  turn. The child is a durable activity of the named agent; it runs on the
  owner's normal budget as a durable activity session, so the parent
  turn is never blocked and the child's lineage is its activity record.

The executors underneath (the served writer turn, the workflow agent turn)
are C9's one loop in waiting; nothing starts them except this function.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from tinyassets.agent_definition import MAIN_AGENT, AgentFile, load_agent
from tinyassets.control_plane.binding import (
    SessionBinding,
    bind_session,
    current_binding,
    use_binding,
)


class AgentUnavailable(LookupError):
    """``agent_ref`` names no agent of this command center (or a retired one)."""


@dataclass(frozen=True)
class OwnerTurn:
    """The owner's admitted conversation turn (``universe_server.converse``)."""

    base_path: Path
    universe_id: str
    owner: str
    tier: str
    session_key: str
    execution_id: str = ""
    box_binding: SessionBinding | None = field(default=None, repr=False, compare=False)
    conversation_history: list = field(default_factory=list)
    input_method: str = "unknown"
    model_choice: dict | None = None
    response_observer: Callable | None = None
    learning_observer: Callable | None = None
    learning_failure_observer: Callable | None = None


@dataclass(frozen=True)
class RunStep:
    """One step of an admitted run: its provider session and the node it executes."""

    session: Any
    prompt_config: Any
    policy: Any
    box_binding: SessionBinding | None = field(default=None, repr=False, compare=False)
    node: dict | None = None
    activity: dict | None = None
    response_observer: Callable | None = None
    metadata_observer: Callable | None = None


@dataclass(frozen=True)
class Activity(RunStep):
    """A durable activity generation, admitted without a workflow branch."""


@dataclass(frozen=True)
class ChildOf:
    """An agent, mid-turn, handing work to another agent of the same owner."""

    base_path: Path
    universe_id: str
    owner: str
    agent_id: str
    box_binding: SessionBinding | None = field(default=None, repr=False, compare=False)
    inside_activity: bool = False


Parent = OwnerTurn | RunStep | ChildOf


def _universe_dir(parent: Parent) -> Path:
    if isinstance(parent, RunStep):
        return Path(parent.session._universe_dir)
    return Path(parent.base_path) / parent.universe_id


def _owner(parent: Parent) -> str:
    """The admitted account the agent runs for: the one whose agents it may be."""
    if isinstance(parent, RunStep):
        return parent.session._principal_id
    return parent.owner


def start(agent_ref: str, input: str, parent: Parent):
    """Start ``agent_ref`` on ``input`` under ``parent``'s admission.

    The agent is resolved among the parent's admitted account's agents only:
    on a shared command center, a co-admin's own agent is not this account's.
    """
    if not isinstance(parent, (OwnerTurn, RunStep, ChildOf)):
        raise TypeError(f"unknown parent {type(parent).__name__}")
    binding = _binding(parent)
    parent = replace(parent, box_binding=binding)
    with use_binding(binding):
        from tinyassets.memory.runtime import owner_memory_scope

        with owner_memory_scope(owner=_owner(parent), center=_universe_dir(parent).name,
                                namespace=_universe_dir(parent).name):
            return _start_bound(agent_ref, input, parent, binding)


def _start_bound(agent_ref: str, input: str, parent: Parent, binding: SessionBinding):
    """Resolve and run one agent inside its admitted owner memory lifetime."""
    try:
        try:
            with binding.coherent_reads():
                agent = load_agent(_universe_dir(parent), agent_ref, owner=_owner(parent))
        except (LookupError, ValueError, OSError) as exc:
            raise AgentUnavailable(
                f"no agent named {agent_ref!r} in this command center") from exc
        if isinstance(parent, OwnerTurn):
            return _owner_turn(agent, input, parent)
        if isinstance(parent, RunStep):
            return _run_step(agent, input, parent)
        return _child(agent, input, parent)
    finally:
        if _terminal_stop(parent):
            binding.cancel_terminal()


def _binding(parent: Parent) -> SessionBinding | None:
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.boxes.provider import BoxAuthError

    inherited = current_binding()
    if (parent.box_binding is not None and inherited is not None
            and parent.box_binding is not inherited):
        raise BoxAuthError("child binding differs from its active parent session")
    binding = parent.box_binding or inherited
    if binding is not None:
        binding.validate(_owner(parent), _universe_dir(parent))
        return binding
    provider, limits = configured_box_provider()
    if provider is None:
        raise BoxAuthError("owner-box session provider is not configured")
    if isinstance(parent, ChildOf):
        raise BoxAuthError("a child requires its parent's admitted box binding")
    if isinstance(parent, OwnerTurn):
        execution_id = parent.execution_id
    else:
        run_id = parent.session.bound_run_id
        if not run_id:
            raise BoxAuthError("an agent step requires an admitted durable run")
        execution_id = f"run:{run_id}"
        # One provider session may execute several nodes, sometimes on different
        # worker threads. Retain its binding and operation sequence for the run.
        with parent.session._lock:
            binding = getattr(parent.session, "_box_binding", None)
            if binding is None:
                binding = bind_session(provider=provider, limits=limits,
                                       universe_dir=_universe_dir(parent), owner=_owner(parent),
                                       execution_id=execution_id)
                parent.session._box_binding = binding
            binding.validate(_owner(parent), _universe_dir(parent))
            return binding
    return bind_session(provider=provider, limits=limits,
                        universe_dir=_universe_dir(parent), owner=_owner(parent),
                        execution_id=execution_id)


def _terminal_stop(parent: Parent) -> bool:
    if isinstance(parent, OwnerTurn):
        from tinyassets.turn_interrupt import current

        live = current()
        return live is not None and live.requested()
    if isinstance(parent, RunStep):
        from tinyassets.runs import is_cancel_requested

        return is_cancel_requested(parent.session._base_path, parent.session.bound_run_id)
    return False


def _owner_turn(agent: AgentFile, message: str, parent: OwnerTurn) -> str:
    from tinyassets.addressed_agents import AddressedAgent
    from tinyassets.universe_intelligence import converse

    return converse(
        parent.universe_id, message, actor_id=parent.owner, tier=parent.tier,
        conversation_history=parent.conversation_history, input_method=parent.input_method,
        response_observer=parent.response_observer, learning_observer=parent.learning_observer,
        learning_failure_observer=parent.learning_failure_observer,
        session_key=parent.session_key,
        addressed_agent=None if agent.slug == MAIN_AGENT else AddressedAgent.of(agent),
        **({} if parent.model_choice is None else {"model_choice": parent.model_choice}),
    )


def _run_step(agent: AgentFile, prompt: str, parent: RunStep):
    from tinyassets import activity_runner
    from tinyassets.addressed_agents import AddressedAgent
    from tinyassets.shared_self import prepare_shared_self_turn
    from tinyassets.workflow_agent import call_foreground_work_agent

    session = parent.session
    if parent.activity is not None:
        prompt, legacy_node = _activity_instructions(session._universe_dir, parent.activity)
        parent = replace(parent, node=legacy_node)
    prompt, system, config = prepare_shared_self_turn(
        session._base_path, session._universe_id, session._principal_id, prompt,
        parent.prompt_config, parent.node,
        **({"activity": parent.activity} if parent.activity is not None else {}),
        agent=None if agent.slug == MAIN_AGENT else AddressedAgent.of(agent),
    )
    if not config.engine_mcp_enabled:
        raise PermissionError("an agent step runs only with engine tools")
    activity = parent.activity
    return call_foreground_work_agent(
        session, prompt=prompt, system=system, config=config, policy=parent.policy,
        **({"activity_binding": activity_runner.ActivityRunBinding(
            session._universe_dir, activity["activity_id"],
            activity["runner_generation"], session._run_id,
        )} if activity is not None else {}),
        **({"response_observer": parent.response_observer}
           if parent.response_observer is not None else {}),
        **({"metadata_observer": parent.metadata_observer}
           if parent.metadata_observer is not None else {}),
    )


def _activity_instructions(universe_dir: Path, activity: dict) -> tuple[str, dict | None]:
    from tinyassets.activity_runner import activity_instructions

    return activity_instructions(universe_dir, activity)


def _child(agent: AgentFile, input: str, parent: ChildOf) -> dict:
    from tinyassets import agent_activities as activities
    from tinyassets.api.activities import _wake

    if parent.inside_activity:
        raise activities.ActivityRefused(
            "An activity cannot start another agent yet; finish this one and say what "
            "should come next.", kind="nested_activity_unavailable")
    text = str(input or "").strip()
    title = text.splitlines()[0][:activities.MAX_TITLE].strip() if text else ""
    universe_dir = Path(parent.base_path) / parent.universe_id
    record = activities.create(
        universe_dir, owner_principal=parent.owner, title=title, brief=text,
        origin_kind="ask", origin_ref=f"agent:{parent.agent_id}", agent_id=agent.agent_id,
    )
    _wake(Path(parent.base_path), parent.universe_id)
    record = activities.get(universe_dir, record["activity_id"]) or record
    return {"activity_id": record["activity_id"], "agent_id": agent.agent_id,
            "status": record["status"],
            "hint": "Queued for background execution as that agent; read_graph "
                    "target=activities shows it, and its status reaches you as it changes."}
