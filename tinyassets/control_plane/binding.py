"""An admitted session's owner-box binding, carried into every execution task.

Paths describe content; they never mint a handle. Only the session admission
binds one. A copied context carries that same object, including into to_thread;
explicit parents carry it across executor boundaries which do not copy context.
"""

from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxHandle,
    BoxProvider,
    ExecLimits,
    WriteConflict,
)


@dataclass(eq=False)
class SessionBinding:
    provider: BoxProvider
    handle: BoxHandle
    limits: ExecLimits
    universe_dir: Path
    _sequence: int = field(default=0, init=False, repr=False)
    _guard: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _ended: bool = field(default=False, init=False, repr=False)

    @property
    def session_id(self) -> str:
        return self.handle.turn_id

    def validate(self, owner: str, universe_dir: Path) -> None:
        if (self._ended or self.handle.account_id != owner
                or self.handle.command_center_id != Path(universe_dir).name
                or self.universe_dir != _path(universe_dir) or not self.session_id):
            raise BoxAuthError("session binding differs from admitted owner/center")

    def operation_id(self, purpose: str) -> str:
        # The binding's ordinal survives thread handoffs. Reconnecting the same
        # session starts at the same ordinal: a changed operation is refused by
        # the host digest instead of being silently replayed under a fresh ID.
        with self._guard:
            if self._ended:
                raise BoxAuthError("session has ended")
            self._sequence += 1
            return f"session:{self.session_id}:{self._sequence}:{purpose}"

    def cancel_terminal(self) -> None:
        """An explicit owner stop; transport loss must never call this method."""
        with self._guard:
            if not self._ended:
                self.provider.end_session(self.handle)
                self._ended = True

    @contextmanager
    def coherent_reads(self):
        """An agent definition may not mix files from two owner generations."""
        generation = self.provider.committed_generation(self.handle)
        token = _READ_GENERATION.set((self, generation))
        try:
            yield
            if self.provider.committed_generation(self.handle) != generation:
                raise WriteConflict("agent files changed during session admission")
        finally:
            _READ_GENERATION.reset(token)

    def check_read(self, generation: int) -> None:
        pinned = _READ_GENERATION.get()
        if pinned is not None and pinned[0] is self and generation != pinned[1]:
            raise WriteConflict("agent files changed during session admission")

    def check_roster(self) -> None:
        pinned = _READ_GENERATION.get()
        if pinned is not None and pinned[0] is self:
            self.check_read(self.provider.committed_generation(self.handle))


_CURRENT: ContextVar[SessionBinding | None] = ContextVar("owner_box_session", default=None)
#: The admitted session per (owner, center): one seat per owner (S26) means one
#: live top-level session, and children inherit its binding. The engine server
#: resolves a tool call's handle here; a request carries no handle of its own.
_ACTIVE: dict[tuple[str, str], list[SessionBinding]] = {}
_ACTIVE_GUARD = threading.Lock()
_READ_GENERATION: ContextVar[tuple | None] = ContextVar("owner_box_read_generation", default=None)


def _path(path: Path) -> Path:
    # The logical root need not exist on the frontend. Never follow a host link
    # to decide which admitted owner is selected.
    return Path(os.path.abspath(path))


def current_binding() -> SessionBinding | None:
    return _CURRENT.get()


@contextmanager
def use_binding(binding: SessionBinding | None):
    token = _CURRENT.set(binding)
    key = None
    if binding is not None:
        key = (binding.handle.account_id, binding.handle.command_center_id)
        with _ACTIVE_GUARD:
            _ACTIVE.setdefault(key, []).append(binding)
    try:
        yield binding
    finally:
        _CURRENT.reset(token)
        if key is not None:
            with _ACTIVE_GUARD:
                stack = _ACTIVE.get(key, [])
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index] is binding:
                        del stack[index]
                        break
                if not stack:
                    _ACTIVE.pop(key, None)


def active_binding(owner: str, center: str) -> SessionBinding | None:
    """The live admitted session of ``owner`` on ``center``, or None."""
    with _ACTIVE_GUARD:
        stack = _ACTIVE.get((owner, center), [])
        return stack[-1] if stack else None


def bind_session(*, provider, limits, universe_dir, owner, execution_id):
    if not isinstance(execution_id, str) or not execution_id.strip():
        raise BoxAuthError("session admission requires a stable execution ID")
    root = _path(universe_dir)
    # Bound and unambiguous for the host's 200-byte identity limit.
    session_id = hashlib.sha256(execution_id.encode()).hexdigest()
    handle = provider.bind(root.name, account_id=owner, turn_id=session_id)
    binding = SessionBinding(provider, handle, limits, root)
    binding.validate(owner, root)
    return binding


def file_binding(root: Path) -> SessionBinding | None:
    """Resolve an existing capability, never bind by a path's basename."""
    root = _path(root)
    binding = current_binding()
    if binding is not None:
        if root == binding.universe_dir:
            binding.validate(binding.handle.account_id, root)
            return binding
        if root.is_relative_to(binding.universe_dir):
            raise BoxAuthError("owner file access must use its admitted root")
        if root.parent == binding.universe_dir.parent and not root.name.startswith("."):
            raise BoxAuthError("owner file access differs from admitted session")
    # Owner files never fall back to a host path, even if a caller lost its
    # context while handing work to an executor.
    from tinyassets.storage import data_dir

    if root.parent == _path(data_dir()) and not root.name.startswith("."):
        raise BoxAuthError("owner files require an admitted session binding")
    return None
