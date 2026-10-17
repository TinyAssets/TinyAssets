"""Refresh HTTP OAuth custody under thread, file and vault locks."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

logger = logging.getLogger(__name__)

#: How long a caller waits for another holder's refresh before failing loudly.
LOCK_WAIT_SECONDS = 45.0

T = TypeVar("T")


class RefreshError(Exception):
    """A refresh did not produce a usable secret. ``detail`` is secret-free."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class RefreshUnavailable(RefreshError):
    """The token endpoint could not be reached or answered 5xx.

    The stored secret is probably fine; a later attempt may work. Callers keep
    their existing TRANSIENT classification for this.
    """


class RefreshRejected(RefreshError):
    """The token endpoint refused the stored refresh token itself.

    ``invalid_grant``, a body naming the refresh token as already used, or an
    explicit instruction to sign in again. The stored secret is finished: no
    retry of it helps, and only the owner signing in again produces a new one.
    Callers raise a sign-in failure for this, never a cooldown.
    """


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(key: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def lock_directory(universe_dir: Path) -> Path:
    """Where this universe's per-credential refresh locks live."""
    return Path(universe_dir) / ".oauth-refresh"


def lock_path(universe_dir: str | Path, lock_id: str) -> Path:
    """The lock file for ONE credential, named by a digest of its identity.

    A digest rather than the identity itself: a destination or a service name can
    contain characters a file name cannot, and the lock's name is not a place to
    start sanitizing user-supplied text.
    """
    digest = hashlib.sha256(lock_id.encode("utf-8")).hexdigest()[:32]
    return lock_directory(Path(universe_dir)) / f"{digest}.lock"


@contextmanager
def file_lock(path: Path, deadline: float) -> Iterator[None]:
    """An exclusive OS lock on ``path``, polled until ``deadline``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("credential refresh lock is busy") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("credential refresh lock is busy") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _hold_vault(universe_dir: Path, deadline: float, subject: str) -> tuple[ExitStack, Any]:
    """The vault's exclusive admission, retried until ``deadline``.

    Held BEFORE the refresh token is spent (see this module's docstring). A
    caller that cannot hold it has spent nothing and may retry.
    """
    from tinyassets.credential_vault import exclusive_credential_vault

    while True:
        stack = ExitStack()
        try:
            write = stack.enter_context(exclusive_credential_vault(universe_dir))
            return stack, write
        except (TimeoutError, OSError):
            stack.close()
            if time.monotonic() >= deadline:
                raise RefreshUnavailable(
                    f"this {subject}'s vault stayed busy; nothing was spent, try again"
                ) from None
            time.sleep(0.05)


def refresh_credential(
    *,
    universe_dir: str | Path,
    lock_id: str,
    owner_user_id: str | None,
    universe_id: str,
    read: Callable[[], T],
    stale: Callable[[T], bool],
    spend: Callable[[T], T],
    records: Callable[[T], list[dict[str, Any]]],
    subject: str = "credential",
    wait_seconds: float = LOCK_WAIT_SECONDS,
    after_write: Callable[[T], None] | None = None,
) -> T:
    """Replace one stored credential's secret, exactly once across the host.

    ``read`` re-reads the stored value INSIDE the locks; ``stale`` decides on that
    re-read value whether a replacement is still needed (a holder before us may
    have done it); ``spend`` performs the network refresh and may raise
    :class:`RefreshRejected` / :class:`RefreshUnavailable`; ``records`` turns the
    rotated value into the vault records to write.

    Returns the value whose secret to use now -- the rotated one, or the one
    another holder had already written. Raises :class:`RefreshError`; a caller
    that needs its own exception type catches and re-raises.

    ``after_write`` runs once the rotated value is saved, STILL inside the
    exclusive vault hold, so whatever it records about the new bytes lands in
    the same critical section as the bytes. It must not raise: the rotation is
    already saved and is never undone for it.
    """
    universe = Path(universe_dir)
    key = f"{universe.resolve()}::{lock_id}"
    deadline = time.monotonic() + wait_seconds
    locked = False
    with _thread_lock(key):
        try:
            with file_lock(lock_path(universe, lock_id), deadline):
                locked = True
                return _refresh_locked(
                    universe=universe,
                    owner_user_id=owner_user_id,
                    universe_id=universe_id,
                    read=read,
                    stale=stale,
                    spend=spend,
                    records=records,
                    subject=subject,
                    deadline=deadline,
                    after_write=after_write,
                )
        except TimeoutError:
            if locked:
                # Ours: the vault or the write timed out while WE held the file
                # lock. Surfaced as itself rather than reported as somebody
                # else's refresh, which is a different thing to tell the owner.
                raise
            raise RefreshUnavailable(
                f"another refresh of this {subject} did not finish"
            ) from None


def _refresh_locked(
    *,
    universe: Path,
    owner_user_id: str | None,
    universe_id: str,
    read: Callable[[], T],
    stale: Callable[[T], bool],
    spend: Callable[[T], T],
    records: Callable[[T], list[dict[str, Any]]],
    subject: str,
    deadline: float,
    after_write: Callable[[T], None] | None = None,
) -> T:
    stack, write = _hold_vault(universe, deadline, subject)
    with stack:
        current = read()
        # Another holder already refreshed: use theirs, never spend the
        # (single-use) refresh token a second time.
        if not stale(current):
            return current
        fresh = spend(current)
        row = records(fresh)
        # Still holding the vault: only a storage fault can stop this write, so
        # it is retried until the deadline rather than given up once. A rotated
        # secret that is not saved is lost.
        while True:
            try:
                write(row, owner_user_id=owner_user_id, universe_id=universe_id)
            except Exception:  # noqa: BLE001 - an unsaved rotation loses the credential
                if time.monotonic() >= deadline:
                    # TERMINAL, not transient. The refresh token HAS been spent:
                    # the source rotated it and the replacement could not be
                    # stored, so what is in the vault is now dead and no retry of
                    # it can work -- only the owner signing in again. Reported as
                    # `RefreshUnavailable` this read as "try later", which is a
                    # lie about a credential that is already gone (Codex
                    # refute-review, P1 #2, second half).
                    raise RefreshRejected(
                        "the refreshed authorization could not be saved, so the "
                        "stored one is no longer usable; sign in again"
                    ) from None
                time.sleep(0.05)
                continue
            if after_write is not None:
                try:
                    after_write(fresh)
                except Exception:  # noqa: BLE001 - the saved rotation stands regardless
                    logger.warning("post-rotation bookkeeping failed for this %s", subject)
            return fresh
