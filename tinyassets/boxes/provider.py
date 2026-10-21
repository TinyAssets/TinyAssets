"""Descriptor-safe file operations local to the persistent owner box.

OwnerBoxd implements this file contract inside the owner's OS boundary; the
local driver is a development reference. Operation IDs, epochs, generations
and compare-and-swap writes protect coherent file updates. File API paths
use /cc relative to the mounted owner root. Boxhostd alone owns persistent
sandbox lifecycle; there is no file or execution forwarding interface.
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
    "ExecLimits",
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
    #: Identify hard links so byte measures count a linked file once.
    inode: int = 0
    links: int = 1


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
