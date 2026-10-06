"""`BoxProvider`: the only way the platform touches a command center's box.

Target architecture D2 (`openspec/changes/target-architecture/design.md`). Every
command center runs in exactly one sealed box. The daemon reaches the box's
contents ONLY through this interface, never a host path. That is the
by-construction close of the planted-link class (#4244): a path is resolved
inside the box, so a link the agent planted can only point inside its own box.

Three rules every driver keeps:

* **Binding is not waking.** :meth:`BoxProvider.bind` checks ownership and
  returns a handle without contacting the box. :meth:`committed_generation`
  answers from the box host's own record, even while the box is suspended. So a
  turn whose cache is current never wakes a box.
* **Every operation is authenticated.** A handle carries the owning account,
  the command center and the box's placement epoch. A driver refuses a handle
  whose account does not own the command center, or whose epoch is stale
  because the box was destroyed or re-imported. A valid handle on the wrong
  turn fails at the execution boundary, not in the caller's good intentions.
* **Mutations are idempotent by operation id.** A retry with the same `op_id`
  returns the recorded outcome and never runs the operation again. An
  operation in flight across a box-host restart reports
  :attr:`ExecState.UNKNOWN_AFTER_RESTORE`; the caller holds, it does not
  re-issue. That is the same rule as the turn journal.

Box paths are absolute under :data:`BOX_ROOT` (``/cc``). They are resolved
inside the box; ``..``, empty components, NULs and anything outside ``/cc``
are refused here, before any driver sees them.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, Union, runtime_checkable

__all__ = [
    "BOX_ROOT",
    "BoxAuthError",
    "BoxBusy",
    "BoxError",
    "BoxHandle",
    "BoxNotFound",
    "BoxOperationRefused",
    "BoxPathError",
    "BoxProvider",
    "BoxUsage",
    "DestroyReceipt",
    "DirEntry",
    "DirPage",
    "ExecEvent",
    "ExecLimits",
    "ExecState",
    "ExecStatus",
    "ExportProfile",
    "FileRead",
    "FileStat",
    "FileWrite",
    "ImportReport",
    "OpIdReuse",
    "Snapshot",
    "StaleHandle",
    "StreamIn",
    "WriteConflict",
    "WriteMode",
    "box_relpath",
]

#: Where a command center's files appear inside its box.
BOX_ROOT = "/cc"

#: Bytes, or an iterable of byte chunks for streaming input.
StreamIn = Union[bytes, Iterable[bytes]]


class BoxError(OSError):
    """A box operation was refused or failed. Never a silent fallback."""


class BoxOperationRefused(BoxError):
    """The operation provably never ran: a stale epoch, a foreign handle, or a reused op id.

    Callers may treat ONLY this family as "no effect". Any other error, including a
    `BoxError` raised after an operation started, is an unknown outcome.
    """


class BoxAuthError(BoxOperationRefused):
    """The handle's account does not own the command center it names."""


class BoxBusy(BoxError):
    """The box stayed busy (a running exec or mutation) past the caller's wait bound."""


class StaleHandle(BoxOperationRefused):
    """The handle's placement epoch is no longer current (destroyed or re-imported)."""


class BoxPathError(BoxError):
    """A box path is malformed, escapes ``/cc``, or crosses a link."""


class BoxNotFound(BoxError, FileNotFoundError):
    """The box path names nothing."""


class OpIdReuse(BoxOperationRefused):
    """An operation id was reused for a different operation."""


class WriteConflict(BoxError):
    """A ``create`` found an existing file, or a ``cas`` saw a newer generation."""


class WriteMode(str, enum.Enum):
    CREATE = "create"  #: fail if the path exists
    REPLACE = "replace"  #: create or overwrite
    CAS = "cas"  #: overwrite only if the box generation equals ``expect_generation``


class ExportProfile(str, enum.Enum):
    SHARE = "share"  #: scrubbed, credentials excluded (harness §4.17); user-facing
    MIGRATION = "migration"  #: complete private state for a cell move; never user-facing


class ExecState(str, enum.Enum):
    RUNNING = "running"
    EXITED = "exited"
    UNKNOWN_AFTER_RESTORE = "unknown_after_restore"


@dataclass(frozen=True)
class BoxHandle:
    """Bound once at turn start and carried on the turn; never looked up by name per call."""

    command_center_id: str
    account_id: str
    epoch: int
    turn_id: str | None = None


@dataclass(frozen=True)
class ExecLimits:
    wall_seconds: float = 120.0
    #: Output kept per execution; the process tree is killed once it passes this.
    output_bytes: int = 64 * 1024


@dataclass(frozen=True)
class ExecEvent:
    #: ``"output"`` (stdout and stderr, merged in order) or ``"exit"``.
    kind: str
    offset: int = 0
    data: bytes = b""
    exit_code: int | None = None
    #: ``"timeout"``, ``"output_limit"``, ``"cancelled"`` or None.
    killed: str | None = None


@dataclass(frozen=True)
class ExecStatus:
    op_id: str
    exec_id: str
    state: ExecState
    exit_code: int | None = None
    killed: str | None = None


@dataclass(frozen=True)
class FileRead:
    data: bytes
    generation: int
    #: Total file size; ``data`` may be a slice of it.
    size: int


@dataclass(frozen=True)
class Snapshot:
    """Several files read at ONE box generation."""

    files: Mapping[str, bytes]
    missing: tuple[str, ...]
    generation: int


@dataclass(frozen=True)
class FileWrite:
    path: str
    size: int
    generation: int


@dataclass(frozen=True)
class FileStat:
    path: str
    kind: str  #: ``"file"``, ``"dir"``, ``"link"`` or ``"other"``
    size: int
    mtime: float


@dataclass(frozen=True)
class DirEntry:
    name: str
    kind: str
    size: int


@dataclass(frozen=True)
class DirPage:
    entries: tuple[DirEntry, ...]
    #: Pass back to continue; None when the listing is complete.
    next_cursor: str | None


@dataclass(frozen=True)
class BoxUsage:
    #: Logical bytes: regular-file sizes, hard links counted once (account-storage-quota).
    logical_bytes: int
    #: The box's hard disk bound, or None for a driver that has none.
    bound_bytes: int | None
    generation: int


@dataclass(frozen=True)
class ImportReport:
    files: int
    bytes: int
    refused: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DestroyReceipt:
    command_center_id: str
    op_id: str
    files_removed: int
    new_epoch: int


def box_relpath(path: str) -> str:
    """Normalize a box path to a relative path under ``/cc``, or refuse.

    ``/cc`` itself maps to ``""`` (the root). Every other path must be
    ``/cc/<component>/...`` with no empty, ``.``, ``..`` or NUL component and no
    backslash. Refusing here means no driver can be handed an escape.
    """
    if not isinstance(path, str):
        raise BoxPathError(f"a box path must be a string, got {type(path).__name__}")
    if "\0" in path or "\\" in path:
        raise BoxPathError(f"box path {path!r} contains a NUL or a backslash")
    if path in (BOX_ROOT, BOX_ROOT + "/"):
        return ""
    prefix = BOX_ROOT + "/"
    if not path.startswith(prefix):
        raise BoxPathError(f"box path {path!r} is not under {BOX_ROOT}")
    parts = path[len(prefix):].rstrip("/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise BoxPathError(f"box path {path!r} has an empty or traversal component")
    return "/".join(parts)


@runtime_checkable
class BoxProvider(Protocol):
    """The box contract (target architecture D2). Drivers: local (dev/test), gVisor, Firecracker."""

    # binding and waking
    def bind(self, command_center_id: str, *, account_id: str,
             turn_id: str | None = None) -> BoxHandle: ...
    def committed_generation(self, handle: BoxHandle) -> int: ...
    def ensure_awake(self, handle: BoxHandle, *, reason: str) -> None: ...

    # execution lifecycle
    def start_exec(self, handle: BoxHandle, op_id: str, argv: Sequence[str], *,
                   stdin: bytes = b"", env: Mapping[str, str] | None = None,
                   cwd: str = BOX_ROOT, limits: ExecLimits = ExecLimits(),
                   interactive_stdin: bool = False) -> str: ...
    def send_stdin(self, handle: BoxHandle, exec_id: str, request_id: str,
                   data: bytes) -> None:
        """Idempotent bounded reply to this handle's running exec, outside durable files."""
        ...
    def stream(self, handle: BoxHandle, exec_id: str, *,
               from_offset: int = 0, timeout: float | None = None) -> Iterator[ExecEvent]: ...
    def cancel(self, handle: BoxHandle, exec_id: str) -> None: ...
    def exec_status(self, handle: BoxHandle, op_id: str) -> ExecStatus: ...

    # files
    def read(self, handle: BoxHandle, path: str, *, offset: int = 0,
             max_bytes: int) -> FileRead: ...
    def read_many(self, handle: BoxHandle, paths: Sequence[str], *,
                  max_total: int) -> Snapshot: ...
    def write(self, handle: BoxHandle, op_id: str, path: str, data: StreamIn, *,
              max_bytes: int, mode: WriteMode = WriteMode.REPLACE,
              expect_generation: int | None = None) -> FileWrite: ...
    def download(self, handle: BoxHandle, path: str, *,
                 chunk_bytes: int = 64 * 1024) -> Iterator[bytes]: ...
    def list(self, handle: BoxHandle, path: str, *, cursor: str | None = None,
             limit: int = 200) -> DirPage: ...
    def stat(self, handle: BoxHandle, path: str) -> FileStat | None: ...
    def remove(self, handle: BoxHandle, op_id: str, path: str) -> None: ...

    # whole box
    def export(self, handle: BoxHandle, *, profile: ExportProfile) -> Iterator[bytes]: ...
    def import_bundle(self, handle: BoxHandle, op_id: str, chunks: Iterable[bytes], *,
                      profile: ExportProfile) -> ImportReport: ...
    def usage(self, handle: BoxHandle) -> BoxUsage: ...
    def suspend(self, handle: BoxHandle) -> None: ...
    def destroy(self, handle: BoxHandle, op_id: str) -> DestroyReceipt: ...

    # host lifecycle
    def close(self) -> None: ...
