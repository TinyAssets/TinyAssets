"""What a trigger fire calls: the run path, through one seam (design D7, D2).

A fire never runs work itself. It calls the handler registered for its trigger
kind, which starts work on the existing run path and returns the run id
promptly; the scheduler's single-flight rule then reads that run's status from
the runs store (platform state) until it is terminal.

Today a handler starts a run in this process. Under the sealed box (S4/S11)
the same handler binds the command center's box (``BoxProvider.bind``) and
calls ``ensure_awake(handle, reason=...)`` before executing in it; the
scheduler, the trigger rows and the fence do not change. A box is woken only
from here: nothing inside a box schedules itself (``tests/control_plane_timer_inventory.py``).

Gates that need a command center's own state -- the agent is paused, it has
an activity in progress, no compute is connected -- are the HANDLER's, which
returns ``WakeResult(declined=...)``: the scheduler never reads a command
center's files to decide (D7/D8a; agreed with harness D3, 2026-10-02).

A kind with no registered handler is not fired at all -- its rows stay owed
and nothing is claimed -- so a mechanism shipped ahead of its consumer leaves
no fire rows that claim work which never ran.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class WakeRequest:
    kind: str
    trigger_key: str
    command_center_id: str
    agent_id: str
    owner_principal_id: str
    due_at: str
    owner_generation: int
    decay_state: str


@dataclass(frozen=True)
class WakeResult:
    """``run_id`` when work started; ``declined`` (a reason) when it did not.

    A declined fire still spends its window: there is never more than one
    proactive turn per cadence window, started or not (harness §4.5).
    """

    run_id: str = ""
    declined: str = ""

    def __post_init__(self) -> None:
        if bool(self.run_id) == bool(self.declined):
            raise ValueError("a WakeResult names exactly one of run_id or declined")


WakeHandler = Callable[[Path, WakeRequest], WakeResult]

_lock = threading.Lock()
_handlers: dict[str, WakeHandler] = {}


def register_wake_handler(kind: str, handler: WakeHandler, *, replace: bool = False) -> None:
    """Register the run-path entry for ``kind``. A second registration raises
    unless ``replace`` -- two consumers silently racing for one kind is the
    double-fire this module exists to prevent."""
    if not kind or not callable(handler):
        raise ValueError("register_wake_handler needs a kind and a callable")
    with _lock:
        if kind in _handlers and not replace and _handlers[kind] is not handler:
            raise ValueError(f"a wake handler for {kind!r} is already registered")
        _handlers[kind] = handler


def unregister_wake_handler(kind: str) -> None:
    with _lock:
        _handlers.pop(kind, None)


def wake_handler(kind: str) -> WakeHandler | None:
    with _lock:
        return _handlers.get(kind)


__all__ = [
    "WakeHandler",
    "WakeRequest",
    "WakeResult",
    "register_wake_handler",
    "unregister_wake_handler",
    "wake_handler",
]
