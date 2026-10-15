"""Serve HTTP chat on the shared loop with tools bound to the turn's box.

The coordinator keeps the journal; :func:`~.tool_session.open_loop_tools`
opens box tools and reaches backend capabilities through ta. A granted box
tool refuses before launch if no box provider is configured.
"""

from __future__ import annotations

import threading
from typing import Any

from tinyassets.agent_loop.box_tools import BOX_ROOT, BoxExecutor, BoxTools
from tinyassets.agent_loop.tool_session import open_loop_tools
from tinyassets.interactive_http_agent import ServedChatAgentAdapter
from tinyassets.served_tools import granted_tools, model_tools

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
    """Use the session's handle; a journal turn ID never mints another session."""
    from tinyassets.control_plane.binding import current_binding

    binding = current_binding()
    if binding is None:
        raise LookupError("no admitted session box binding")
    binding.validate(owner, binding.universe_dir.parent / command_center)
    provider, limits, handle = binding.provider, binding.limits, binding.handle
    root = getattr(handle, "root", None) or BOX_ROOT
    return BoxTools(BoxExecutor(provider, handle, limits=limits, cwd=root), root=root), root


class ThinLoopChatAdapter(ServedChatAgentAdapter):
    """Served chat on the thin loop; admission and identity are unchanged."""

    def open_tools(self, coordinator, *, timeout):
        owner = coordinator.owner
        universe_dir = coordinator.context.universe_dir
        turn_id = coordinator.turn.turn_id
        from tinyassets.control_plane.binding import current_binding

        binding = current_binding()
        return open_loop_tools(
            granted=model_tools(coordinator.config),
            capability_grant=granted_tools(coordinator.config),
            bind_box=None if binding is None else (
                lambda: bind_turn_box(owner=owner, command_center=universe_dir.name,
                                      turn_id=turn_id)
            ),
            owner=owner,
            universe_dir=universe_dir,
            engine_identity=lambda: self.engine_identity(coordinator.context,
                                                         coordinator.config),
            timeout=timeout,
            ta_turn=turn_id,
            box_session_id=None if binding is None else binding.session_id,
            model_budget=coordinator.request_budget,
            model_parent=("turn", turn_id,
                          getattr(coordinator.context.provider_request,
                                  "agent_binding_id", ""),
                          getattr(coordinator.context.provider_request,
                                  "binding_revision", None))
            if coordinator.context.provider_request is not None else None,
            **coordinator.steering(),
        )
