"""The thin loop for served chat, and the switch that selects it.

``TINYASSETS_AGENT_LOOP=thin`` routes an HTTP-protocol chat turn through the
thin loop: the same :class:`AgentTurnCoordinator` and journal, with its tools
opened by :func:`~.tool_session.open_loop_tools` -- box tools on a handle bound
at turn start, owner reads in the loop, the rest on the engine route. Unset, or any
other value, keeps today's path. Native (CLI) turns are untouched either way:
command adapters and file-OAuth CLIs keep running as CLIs (D6).

The switch is opt-in and fails loudly: with the thin loop selected and no box
provider configured, a turn that is granted a box tool is refused before
anything runs, never quietly served by the tool jail instead.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from tinyassets.agent_loop.box_tools import BOX_ROOT, BoxExecutor, BoxTools
from tinyassets.agent_loop.owner_reads import OWNER_READ_TOOLS
from tinyassets.agent_loop.tool_session import open_loop_tools
from tinyassets.interactive_http_agent import ServedChatAgentAdapter
from tinyassets.served_tools import granted_tools

ENV_SWITCH = "TINYASSETS_AGENT_LOOP"
THIN = "thin"


def thin_loop_selected() -> bool:
    return (os.environ.get(ENV_SWITCH) or "").strip().lower() == THIN


_box_lock = threading.Lock()
_box_provider: Any = None
_box_limits: Any = None


def configure_box_provider(provider: Any, *, limits: Any) -> None:
    """Install the process's ``BoxProvider`` (D2) and its default exec limits."""
    global _box_provider, _box_limits
    with _box_lock:
        _box_provider, _box_limits = provider, limits


def configured_box_provider() -> tuple[Any, Any]:
    with _box_lock:
        return _box_provider, _box_limits


def bind_turn_box(*, owner: str, command_center: str, turn_id: str) -> tuple[BoxTools, str]:
    """Bind the turn's box ONCE; every tool call of the turn uses this handle."""
    provider, limits = configured_box_provider()
    if provider is None:
        raise LookupError("no box provider is configured")
    handle = provider.bind(command_center, account_id=owner, turn_id=turn_id)
    root = getattr(handle, "root", None) or BOX_ROOT
    return BoxTools(BoxExecutor(provider, handle, limits=limits, cwd=root), root=root), root


class ThinLoopChatAdapter(ServedChatAgentAdapter):
    """Served chat on the thin loop; admission and identity are unchanged."""

    def open_tools(self, coordinator, *, timeout):
        owner = coordinator.owner
        universe_dir = coordinator.context.universe_dir
        turn_id = coordinator.turn.turn_id
        provider, _ = configured_box_provider()
        return open_loop_tools(
            granted=granted_tools(coordinator.config),
            loop_reads=OWNER_READ_TOOLS,
            bind_box=None if provider is None else (
                lambda: bind_turn_box(owner=owner, command_center=universe_dir.name,
                                      turn_id=turn_id)
            ),
            owner=owner,
            universe_dir=universe_dir,
            engine_identity=lambda: self.engine_identity(coordinator.context,
                                                         coordinator.config),
            timeout=timeout,
            **coordinator.steering(),
        )
