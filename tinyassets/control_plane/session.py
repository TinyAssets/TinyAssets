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
* :class:`RunStep` -- a step of an admitted workflow run (an agent node, or
  the Activities branch continuing an activity). Runs the shared-self turn on
  the run's provider session and returns the provider's response.
* :class:`ChildOf` -- an agent starting another agent from inside its own
  turn. The child is a durable activity of the named agent; it runs on the
  owner's normal budget as a run step of the Activities branch, so the parent
  turn is never blocked and the child's lineage is its activity record.

The executors underneath (the served writer turn, the workflow agent turn)
are C9's one loop in waiting; nothing starts them except this function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from tinyassets.agent_definition import MAIN_AGENT, AgentFile, load_agent


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
    node: dict | None = None
    activity: dict | None = None
    response_observer: Callable | None = None
    metadata_observer: Callable | None = None


@dataclass(frozen=True)
class BackgroundStep:
    """An agent node of a queued (background) run, on the queue consumer's session."""

    session: Any
    prompt_config: Any
    policy: Any
    node: dict | None = None


@dataclass(frozen=True)
class ChildOf:
    """An agent, mid-turn, handing work to another agent of the same owner."""

    base_path: Path
    universe_id: str
    owner: str
    agent_id: str
    inside_activity: bool = False


Parent = OwnerTurn | RunStep | BackgroundStep | ChildOf


def _universe_dir(parent: Parent) -> Path:
    if isinstance(parent, RunStep):
        return Path(parent.session._universe_dir)
    if isinstance(parent, BackgroundStep):
        return Path(parent.session._base_path) / parent.session._task.universe_id
    return Path(parent.base_path) / parent.universe_id


def start(agent_ref: str, input: str, parent: Parent):
    """Start ``agent_ref`` on ``input`` under ``parent``'s admission."""
    try:
        agent = load_agent(_universe_dir(parent), agent_ref)
    except (LookupError, ValueError, OSError) as exc:
        raise AgentUnavailable(f"no agent named {agent_ref!r} in this command center") from exc
    if isinstance(parent, OwnerTurn):
        return _owner_turn(agent, input, parent)
    if isinstance(parent, RunStep):
        return _run_step(agent, input, parent)
    if isinstance(parent, BackgroundStep):
        return _background_step(agent, input, parent)
    if isinstance(parent, ChildOf):
        return _child(agent, input, parent)
    raise TypeError(f"unknown parent {type(parent).__name__}")


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


def _background_step(agent: AgentFile, prompt: str, parent: BackgroundStep):
    from tinyassets.addressed_agents import AddressedAgent
    from tinyassets.shared_self import prepare_shared_self_turn
    from tinyassets.workflow_agent import call_background_work_agent

    session = parent.session
    prompt, system, config = prepare_shared_self_turn(
        session._base_path, session._task.universe_id, session._task.actor_id, prompt,
        parent.prompt_config, parent.node,
        agent=None if agent.slug == MAIN_AGENT else AddressedAgent.of(agent),
    )
    return call_background_work_agent(
        session, prompt=prompt, system=system, config=config, policy=parent.policy,
    )


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
