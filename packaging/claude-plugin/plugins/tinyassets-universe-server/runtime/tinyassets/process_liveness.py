"""Proof that a process is alive or dead, from an OS lock the kernel releases.

Every process that owns durable in-flight work -- an automation lease, a run, a
seat -- holds an exclusive OS lock on ``<data>/.consumer_liveness/<token>.lock``
for its whole life. The kernel drops that lock when the process dies, however it
dies, a deploy's SIGKILL included. So "its owner is dead" is a fact another
process can check (the file exists and nobody holds its lock) rather than a guess
from a heartbeat that stopped or a table row that went quiet.

Three answers, never a guess: ``alive``, ``dead`` or ``unknown``. A token with
no file (never registered, or a probe error) is ``unknown``, and ``unknown`` is
never treated as dead.

``owner_token()`` is this process's own token for runs and seats. The automation
consumer keeps its own lease-holder token; one process may hold several.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Directory under the data root holding one lock file per live owner token.
LIVENESS_DIR = ".consumer_liveness"

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"


def liveness_path(base_path: str | Path, token: str) -> Path | None:
    """The lock file that proves ``token``'s process is alive, or None.

    None for a token that is not a plain token: such a token can never be
    proven dead, so whatever it holds is honoured until it expires.
    """
    if not _TOKEN_RE.match(token or ""):
        return None
    return Path(base_path) / LIVENESS_DIR / f"{token}.lock"


def hold_liveness(base_path: str | Path, token: str, *, broker_readable=False) -> Any:
    """Take ``token``'s liveness lock. Keep the result for the process life.

    Cleanup removes a DEAD token's file, and a fresh registrant's file reads as
    dead between its creation and its lock. So after locking, the registrant
    checks that the path still names the file it locked; if cleanup unlinked
    it meanwhile, it locks a new one. Without this a live process would hold a
    lock on a deleted file, and its runs would be unprovable ("unknown")
    forever once it died (Codex refute 2026-09-30, round 2).
    """
    from tinyassets.singleton_lock import acquire_singleton_lock, release_singleton_lock

    path = liveness_path(base_path, token)
    if path is None:
        raise ValueError(f"liveness token {token!r} is not a plain token")
    for _attempt in range(50):
        if broker_readable:
            from tinyassets.singleton_lock import LockAcquisition, _lock_fd
            from tinyassets.universe_files import open_broker_liveness_lock

            fd = open_broker_liveness_lock(base_path, path.name)
            if not _lock_fd(fd):
                os.close(fd)
                time.sleep(0.02)
                continue
            held = LockAcquisition(True, fd, path, None)
        else:
            held = acquire_singleton_lock(path)
        if not held.acquired or held.fd is None:
            # Our own fresh token: only cleanup, deciding whether to remove the
            # file, can hold it. It lets go in moments.
            time.sleep(0.02)
            continue
        try:
            same = os.path.samestat(os.fstat(held.fd), os.stat(path))
        except OSError:
            same = False
        if same:
            return held
        release_singleton_lock(held)
    raise RuntimeError(f"could not keep a liveness file for token {token}")


def owner_state(base_path: str | Path, token: str) -> str:
    """``alive``, ``dead`` or ``unknown`` -- read-only, never deletes.

    The file is the proof, and one owner can hold work in many places, so a
    probe that deleted it after reclaiming ONE thing would leave every other
    thing of that dead owner unprovable (Codex round 2, 2026-09-27).

    POSIX flock needs no write access; only contention proves life. Pin the
    proof directory and refuse links/non-files before probing. A FIFO must not
    block the broker, and an unrelated flock error must not authorize another
    inference request. A replaced proof is unknown, never dead/alive.
    """
    import errno
    import fcntl

    from tinyassets.universe_files import readonly_lock_file

    path = liveness_path(base_path, token)
    if path is None:
        return UNKNOWN
    try:
        with readonly_lock_file(base_path, LIVENESS_DIR, path.name) as fd:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return UNKNOWN
                state = ALIVE
            else:
                # Closing the read-only descriptor releases the probe lock.
                state = DEAD
            return state
    except (OSError, ValueError):
        return UNKNOWN


def remove_if_dead(
    base_path: str | Path, token: str, still_named: Callable[[str], bool],
) -> bool:
    """Delete ``token``'s file if its owner is dead and nothing names it.

    The decision and the delete happen while THIS call holds the file's lock,
    so no registrant can lock the same file in between: one that opened it
    first finds, after its own lock, that the path no longer names its file
    and locks a new one (``hold_liveness``). Deciding under a probe lock that
    is released before the delete let a registrant lock the file, pass that
    check, and then lose it (Codex refute 2026-09-30, round 3).
    """
    from tinyassets.singleton_lock import _lock_fd, _pid_path, _unlock_fd

    path = liveness_path(base_path, token)
    if path is None:
        return False
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return False
    locked = False
    try:
        if not _lock_fd(fd):
            return False  # alive
        locked = True
        if still_named(token):
            return False
        try:
            path.unlink()
        except OSError:
            # Windows will not delete a file a handle holds open (ours). There a
            # registrant that has the file open blocks the delete the same way,
            # so releasing first and deleting after is equally safe.
            _unlock_fd(fd)
            locked = False
            os.close(fd)
            fd = -1
            try:
                path.unlink()
            except OSError:
                return False
        try:
            _pid_path(path).unlink()
        except OSError:
            pass
        return True
    finally:
        if fd >= 0:
            if locked:
                _unlock_fd(fd)
            os.close(fd)


# -- This process's owner token -------------------------------------------------

_TOKEN = f"proc_{secrets.token_hex(12)}"
#: The liveness lock held per data root, for the process lifetime.
_HELD: dict[str, Any] = {}
_HELD_LOCK = threading.Lock()


def _reset_after_fork() -> None:
    """A forked child is a different process: its own token, no inherited claim.

    Its copies of the parent's lock descriptors are closed: the lock belongs to
    the open file, so a child keeping a copy would keep a dead parent "alive".
    """
    global _TOKEN, _HELD_LOCK
    _TOKEN = f"proc_{secrets.token_hex(12)}"
    for held in _HELD.values():
        fd = getattr(held, "fd", None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    _HELD.clear()
    _HELD_LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):  # pragma: no branch - POSIX only
    os.register_at_fork(after_in_child=_reset_after_fork)


def owner_token(base_path: str | Path, *, broker_readable=False) -> str:
    """This process's owner token, with its liveness lock held under ``base_path``.

    Taken before the token is first written anywhere, so no row can name an
    owner whose proof does not exist yet. Fails loudly: a run stamped with a
    token nobody can prove would never be recovered.
    """
    key = str(Path(base_path).resolve())
    with _HELD_LOCK:
        if key not in _HELD:
            lock = hold_liveness(base_path, _TOKEN, broker_readable=broker_readable)
            if not getattr(lock, "acquired", False):
                raise RuntimeError(
                    f"could not take the liveness lock for owner token {_TOKEN}"
                )
            _HELD[key] = lock
        elif broker_readable:
            from tinyassets.universe_files import open_broker_liveness_lock

            # Other daemon work may have registered this process before its
            # first inference. Upgrade that same inode, preserving its lock.
            fd = open_broker_liveness_lock(base_path, _HELD[key].path.name)
            try:
                if not os.path.samestat(os.fstat(fd), os.fstat(_HELD[key].fd)):
                    raise RuntimeError("daemon liveness proof was replaced")
            finally:
                os.close(fd)
    return _TOKEN
