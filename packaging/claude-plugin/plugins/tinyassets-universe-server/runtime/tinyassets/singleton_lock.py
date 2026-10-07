"""Process-lifetime singleton lock for host-wide one-instance apps.

Used by ``universe_tray.py`` to ensure a single tray + MCP + tunnel per
host. Two sidecar files at the caller-chosen *stem*:

* ``<stem>`` — lock file. OS-level exclusive lock on this file is the
  authoritative exclusion mechanism. Never read as content; on Windows
  ``msvcrt.locking`` takes a share-exclusive mode that blocks external
  reads anyway.
* ``<stem>.pid`` — plain-text PID of the holder. Informational only,
  used to surface "already running as PID N" diagnostics to the user.

Windows uses ``msvcrt.locking(LK_NBLCK)`` — non-blocking, so a second
instance fails immediately. POSIX uses ``fcntl.flock(LOCK_EX | LOCK_NB)``.

The held FD is returned so the caller can keep it alive for the
process lifetime. Closing the FD releases the OS lock; the companion
release helper also unlinks both sidecar files best-effort.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LockAcquisition:
    """Result of ``acquire_singleton_lock``.

    ``acquired`` is True when THIS process now holds the lock. ``fd`` is
    the open descriptor that must be kept alive (closing releases the
    lock). ``existing_pid`` is populated only on failure and reflects
    the PID recorded in the ``<stem>.pid`` sidecar — may be None if
    that file is missing, empty, or unreadable.
    """

    acquired: bool
    fd: Optional[int]
    path: Path
    existing_pid: Optional[int]


def _pid_path(lock_path: Path) -> Path:
    return lock_path.with_suffix(lock_path.suffix + ".pid")


def _pid_alive(pid: int) -> bool:
    """Liveness probe for *pid*. Platform-specific, best-effort."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def _read_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _lock_fd(fd: int) -> bool:
    """Non-blocking exclusive lock on *fd*. Returns True on success."""
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _lock_fd_within(fd: int, timeout: float) -> bool:
    """Exclusive lock on *fd*, waiting up to *timeout* seconds for its holder.

    Polls the non-blocking lock: neither platform offers a kernel wait with a
    caller-chosen bound. Never steals; a holder that keeps it past the bound
    still wins and this returns False.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    delay = 0.005
    while not _lock_fd(fd):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        delay = min(delay * 2, 0.1)
    return True


def _unlock_fd(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        try:
            os.lseek(fd, 0, 0)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def acquire_singleton_lock(path: Path) -> LockAcquisition:
    """Try to acquire a host-wide singleton lock at *path*.

    On success, returns ``LockAcquisition(acquired=True, fd=<open>)``.
    The caller MUST keep ``fd`` open for the lifetime of the singleton;
    closing it releases the OS lock.

    On conflict (another live process holds the lock), returns
    ``acquired=False`` with ``existing_pid`` populated from the
    ``<stem>.pid`` sidecar when readable.

    Stale lock handling: a PID sidecar without an active OS lock on the
    paired lock file is stale — acquisition succeeds and overwrites the
    PID sidecar. The OS lock itself is ground truth; the PID is a
    human-readable breadcrumb.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_path(path)

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)

    if not _lock_fd(fd):
        # Another process holds the OS lock. Read its PID sidecar for
        # diagnostics, then close our FD and report failure.
        existing_pid = _read_pid(pid_file)
        os.close(fd)
        return LockAcquisition(
            acquired=False, fd=None, path=path, existing_pid=existing_pid,
        )

    # Got the OS lock. Record our PID in the sidecar. Sidecar write
    # failure is non-fatal — the OS lock is what matters.
    try:
        pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    except OSError:
        pass

    return LockAcquisition(
        acquired=True, fd=fd, path=path, existing_pid=None,
    )


def release_singleton_lock(acq: LockAcquisition) -> None:
    """Release a held singleton lock. Safe to call on unacquired results."""
    if not acq.acquired or acq.fd is None:
        return
    _unlock_fd(acq.fd)
    try:
        os.close(acq.fd)
    except OSError:
        pass
    # Best-effort cleanup. If another process has already grabbed the
    # lock between our close and unlink that's harmless — they hold the
    # OS lock and will rewrite the PID sidecar on acquire.
    for p in (_pid_path(acq.path), acq.path):
        try:
            p.unlink()
        except OSError:
            pass


__all__ = [
    "LockAcquisition",
    "acquire_singleton_lock",
    "release_singleton_lock",
    "_pid_alive",
]
