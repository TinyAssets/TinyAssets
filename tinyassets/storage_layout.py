"""The data-layout marker: an image never runs against data it does not understand.

The universe -> command center cutover (`openspec/changes/rename-universe-to-
command-center`, design D7.2 / D10) renames tables, columns, files and ids in one
locked migration run. An image built for the old layout must never start against
renamed data: it would find no ``universes`` table and could create a blank home
for a person who already has one. The marker shipped first (C4a) as the
production baseline; layout 2 now records the consent sidecar move, before
the naming cutover:

* ``data_dir()/.layout.json`` holds ``{"layout": <n>, "state": "stable"}``,
  written the first time an image that knows layout 1 finds none.
* Every process that opens the data calls `require_layout` first. It takes a
  SHARED lock on ``data_dir()/.layout.lock`` and only then reads the marker, so
  the check and the hold are one admission: a migration (which takes the lock
  EXCLUSIVELY) can never slip between them. It refuses -- loudly, opening
  nothing -- unless it knows the layout AND the state is ``stable``; a migration
  writes ``"state": "migrating"`` durably before its first change, so a crash
  mid-run leaves a marker every process refuses. The lock is held for the
  process lifetime. Host jobs (``deploy/backup.sh``) take the same lock.
* Who calls it: every INDEPENDENT entry point that opens the data -- the
  server (``universe_server.main``), the daemon CLI (``python -m tinyassets``),
  the desktop daemon role, operator scripts and the hourly rotation, and host
  jobs via ``flock`` (``deploy/backup.sh``). A process's children (spawned
  workspace/broker workers, the per-turn engine MCP subprocess) are covered by
  their parent: they live inside its lifetime, and in a container they die with
  it, before any new container could migrate.
* POSIX uses ``flock``; Windows uses ``LockFileEx`` (shared and exclusive), so a
  local install's separate daemon and connector processes are excluded too.

``python -m tinyassets.storage_layout check [DATA_DIR]`` exits 0 when this code
may run on that data, 3 when it may not.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from tinyassets.storage import platform_state_move as psm

#: Layout 2 moves consent authority outside homes. The naming cutover must use
#: a subsequent version; reusing 2 would admit this image onto renamed data.
LAYOUT = 2
#: Layouts this code understands. A newer one means "migrated past me".
KNOWN_LAYOUTS = frozenset({1, 2})
STABLE = "stable"
MARKER = ".layout.json"
LOCK = ".layout.lock"

_held: list[int] = []


class LayoutRefused(RuntimeError):
    """This code must not run against this data."""


# ---------------------------------------------------------------------------
# The lock: shared for every reader, exclusive for initialisation / migration
# ---------------------------------------------------------------------------


def _open_lock(base: Path) -> int:
    path = Path(base) / LOCK
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    except PermissionError:
        # Created by a host job running as another user (backup.sh as root): a
        # read-only descriptor is enough to lock it.
        return os.open(path, os.O_RDONLY)
    try:
        os.chmod(path, 0o666)  # every service user and host job can open it
    except OSError:
        pass
    return fd


if sys.platform == "win32":  # pragma: no cover - exercised on Windows installs
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _LOCKFILE_EXCLUSIVE = 0x2

    def _lock(fd: int, exclusive: bool, *, blocking: bool = True) -> None:
        handle = msvcrt.get_osfhandle(fd)
        flags = _LOCKFILE_EXCLUSIVE if exclusive else 0
        if not blocking:
            flags |= 0x1  # LOCKFILE_FAIL_IMMEDIATELY
        if not _kernel32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(_Overlapped())):
            if not blocking and ctypes.get_last_error() == 33:  # ERROR_LOCK_VIOLATION
                raise BlockingIOError("layout lock held")
            raise OSError(ctypes.get_last_error(), "LockFileEx failed on the layout lock")

    def _unlock(fd: int) -> None:
        handle = msvcrt.get_osfhandle(fd)
        _kernel32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(_Overlapped()))
else:
    import fcntl

    def _lock(fd: int, exclusive: bool, *, blocking: bool = True) -> None:
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, flags | (0 if blocking else fcntl.LOCK_NB))

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# The marker
# ---------------------------------------------------------------------------


def marker_path(base: Path) -> Path:
    return Path(base) / MARKER


def read_marker(base: Path) -> dict[str, Any] | None:
    path = marker_path(base)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise LayoutRefused(f"{path} is not valid JSON ({exc}); nothing was opened") from None
    if not isinstance(document, dict):
        raise LayoutRefused(f"{path} is not a JSON object; nothing was opened")
    return document


def _write_atomically(path: Path, document: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    if hasattr(os, "O_DIRECTORY"):
        dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _validated(base: Path, document: dict[str, Any]) -> dict[str, Any]:
    layout, state = document.get("layout"), document.get("state")
    if state != STABLE:
        raise LayoutRefused(
            f"{marker_path(base)} says state={state!r}: a migration did not finish. "
            "Nothing was opened; restore the backup or finish the migration first."
        )
    if layout not in KNOWN_LAYOUTS:
        raise LayoutRefused(
            f"{marker_path(base)} says layout={layout!r}; this code understands "
            f"{sorted(KNOWN_LAYOUTS)}. Nothing was opened; run the image built for "
            "that layout."
        )
    return document


def _mark_move(base: Path, state: str) -> None:
    """Record a one-way move's state in the marker, durably, before it changes
    anything. A crash between the two writes leaves ``migrating``, which the
    next admission sees and resumes from."""
    document = read_marker(base) or {"layout": LAYOUT, "state": STABLE}
    moves = dict(document.get(psm.MOVES) or {})
    moves[psm.CONSENTS] = state
    _write_atomically(marker_path(base), {
        **document, "layout": LAYOUT,
        "state": psm.MIGRATING if state == psm.MIGRATING else STABLE,
        psm.MOVES: moves,
    })


def _validate_for_move(base: Path, document: dict[str, Any]) -> None:
    # Only OUR interrupted move is resumable. Other migrations still refuse.
    if (document.get("layout") == LAYOUT
            and document.get("state") == psm.MIGRATING
            and (document.get(psm.MOVES) or {}).get(psm.CONSENTS) == psm.MIGRATING):
        return
    _validated(base, document)


def _admit(base: Path, *, hold: bool) -> dict[str, Any]:
    """Lock shared, then read and validate; initialise under the exclusive lock."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    fd = _open_lock(base)
    try:
        while True:
            _lock(fd, exclusive=False)
            document = read_marker(base)
            if document is not None:
                _validate_for_move(base, document)
                if not psm.move_needed(document) and document.get("layout") == LAYOUT:
                    _validated(base, document)
                    break
            _unlock(fd)
            # Never queue an exclusive waiter behind an admitted process's
            # lifetime shared lock. It may have completed the move since our
            # read. Reacquire shared and inspect its result on every retry.
            try:
                _lock(fd, exclusive=True, blocking=False)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            try:
                document = read_marker(base)
                if document is not None:
                    _validate_for_move(base, document)
                if psm.move_needed(document):
                    psm.run(base, mark=lambda state: _mark_move(base, state))
                else:
                    # Fence volumes already moved by the unfenced PR image.
                    _mark_move(base, psm.DONE)
            finally:
                _unlock(fd)
    except BaseException:
        os.close(fd)
        raise
    if hold:
        _held.append(fd)
    else:
        os.close(fd)
    return document


def check(base: Path) -> dict[str, Any]:
    """The marker if this code may run on ``base`` (initialising it); no lock kept."""
    return _admit(Path(base), hold=False)


def require_layout(base: Path | None = None) -> dict[str, Any]:
    """Refuse unless this code understands the data; then hold the shared lock."""
    if base is None:
        from tinyassets.storage import data_dir

        base = data_dir()
    return _admit(Path(base), hold=True)


def release_for_tests() -> None:
    """Close every held lock (test isolation only; production holds for life)."""
    while _held:
        os.close(_held.pop())


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "check":
        print("usage: python -m tinyassets.storage_layout check [DATA_DIR]", file=sys.stderr)
        return 2
    if len(argv) > 1:
        base = Path(argv[1])
    else:
        from tinyassets.storage import data_dir

        base = data_dir()
    try:
        document = check(base)
    except LayoutRefused as exc:
        print(f"layout refused: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(document))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
