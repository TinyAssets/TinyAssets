"""Which of the owner's agents a conversation turn addresses (harness §4.18).

A command center can hold many agents: the main agent at its root and every
``agents/<name>/`` directory beside it (``agent_definition``). The owner talks
to whichever one they address. ``"main"`` (or nothing) is the root agent and
keeps every existing key; any other ``agent_id`` names one of those directories
(by name or by its ``settings.agent.id``), and its turn runs with that agent's
own instructions on its own thread while reading and writing the SAME brain.

The selector never stands in for ownership. The caller has already bound the
authenticated owner and the universe; this only resolves an id INSIDE that
scope, and an id that is not one of the owner's agents there (an agent another
account owns, ``settings.agent.owner``, included) is refused by name, never
quietly answered by the main agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tinyassets.agent_definition import MAIN_AGENT, AgentFile, list_agent_files, load_agent

_AGENT_PREFIX = "agent:"
_MAX_ID = 128

__all__ = ["MAIN_AGENT", "AddressedAgent", "AgentNotAddressable", "agent_of_session",
           "memory_session", "normalize_agent_id", "resolve", "roster"]


class AgentNotAddressable(LookupError):
    """``agent_id`` names nothing this owner can talk to in this universe."""


@dataclass(frozen=True)
class AddressedAgent:
    """One custom agent a turn speaks as. ``main`` is never one of these."""

    agent_id: str
    name: str
    #: The agent's own ``AGENTS.md``, verbatim.
    instructions: str
    #: Its directory name under ``agents/``: where its settings, skills and
    #: extensions live.
    agent_slug: str

    @classmethod
    def of(cls, agent: AgentFile) -> AddressedAgent:
        return cls(agent_id=agent.agent_id, name=agent.name,
                   instructions=agent.instructions, agent_slug=agent.slug)


def normalize_agent_id(agent_id: object) -> str:
    """``"main"`` for the seeded agent, else the trimmed id; refuses a malformed one."""
    if agent_id is None:
        return MAIN_AGENT
    if not isinstance(agent_id, str):
        raise AgentNotAddressable("agent_id must be a string")
    value = agent_id.strip()
    if not value or value == MAIN_AGENT:
        return MAIN_AGENT
    if len(value) > _MAX_ID or any(ch.isspace() or ch == ":" for ch in value):
        raise AgentNotAddressable(f"no agent of yours is {value[:_MAX_ID]!r}")
    return value


def _component(value: str, what: str) -> str:
    """One key component, refused if it could be read as two.

    Keys are built from parts, never parsed by searching for a delimiter: a
    component containing the delimiter (or the escape character) is refused
    here, so no owner id or agent id can make one person's key spell another's.
    """
    text = str(value or "")
    if not text or ":" in text or "%" in text or any(ch.isspace() for ch in text):
        raise AgentNotAddressable(f"{what} cannot be part of a conversation key")
    return text


def memory_session(owner: str, agent_id: str = MAIN_AGENT) -> str:
    """The conversation-memory key of ``agent_id``'s thread with ``owner``.

    The main thread keeps ``principal:<owner>`` (no migration). Another agent's
    is ``agent:<agent_id>:principal:<owner>``; its native session and steering
    key is ``thread:`` plus this, exactly like the main thread's. Both parts are
    checked by :func:`_component`, so a key has exactly one reading.
    """
    owner = _component(owner, "this account id")
    if agent_id == MAIN_AGENT:
        return f"principal:{owner}"
    return f"{_AGENT_PREFIX}{_component(agent_id, 'this agent id')}:principal:{owner}"


def agent_of_session(session_id: str, owner: str) -> str | None:
    """The agent a memory session belongs to, for ``owner``; None if not theirs.

    By construction, not by search: the key must have exactly the shape
    :func:`memory_session` builds, and rebuilding it from the parsed agent and
    ``owner`` must give the same string back.
    """
    parts = str(session_id or "").split(":")
    if len(parts) == 2:
        agent_id = MAIN_AGENT
    elif len(parts) == 4 and parts[0] == "agent" and parts[2] == "principal":
        agent_id = parts[1]
    else:
        return None
    try:
        return agent_id if memory_session(owner, agent_id) == session_id else None
    except AgentNotAddressable:
        return None


def resolve(
    base_path: str | Path, *, universe_id: str, owner: str, agent_id: object,
) -> AddressedAgent | None:
    """The agent ``agent_id`` addresses, or None for the main agent.

    Raises :class:`AgentNotAddressable` for anything that is not one of this
    owner's agents in this command center: an unknown name, another account's
    agent, a retired one, a malformed id. ``owner`` is the authenticated owner
    the caller already bound to ``universe_id``; the id is resolved inside that
    scope and nowhere else. The message never says which refusal applied, so a
    probe learns nothing.
    """
    wanted = normalize_agent_id(agent_id)
    if wanted == MAIN_AGENT:
        return None
    try:
        agent = load_agent(Path(base_path) / universe_id, wanted, owner=owner)
    except (LookupError, ValueError, OSError):
        raise AgentNotAddressable(
            f"no agent of yours is {wanted!r} in this command center") from None
    return AddressedAgent.of(agent)


def roster(base_path: str | Path, *, universe_id: str, owner: str) -> list[dict]:
    """``[{agent_id, name}]`` of the owner's agents in the command center, ``main`` first."""
    agents = [{"agent_id": MAIN_AGENT, "name": "Your agent"}]
    for agent in list_agent_files(Path(base_path) / universe_id, owner=owner):
        agents.append({"agent_id": agent.agent_id, "name": agent.name})
    return agents
