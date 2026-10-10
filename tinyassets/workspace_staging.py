"""Workspace staging: created per operation, removed on EVERY exit, swept by proof.

Staging is a worker's private scratch for one checkout, push or remote probe. It
holds the credentialed clone, the bundle and the git homes, so a staging directory
left behind is both a disk leak and possibly retained credential material.
Measured 2026-09-30 on production: 334 leaked directories, 2.8 GiB, in one universe
-- every checkout that failed before its success-path ``rmtree`` left its staging
behind, and push staging was never removed at all. The boot sweep removed all
334 (2,936,213,247 bytes) on 2026-09-30 at 20:00:54Z.

The rules:

1. **Owned, and marked in use.** A staging directory is created under
   ``<base>/.workspace-staging/<owner token>/...``; the token's `process_liveness`
   lock is held before the directory exists. Every process that USES the tree --
   the parent, the spawned worker, and every git it runs (which inherit the
   worker's descriptor) -- holds a SHARED lock on the tree's ``.inuse`` file. The
   kernel releases a holder's share only when its last copy of the descriptor
   closes, so "is anything still using this?" is a fact, not a guess: a git that
   outlives its killed parent still holds it (gpt-6-astra, PR #4143 round 1).
2. **Removed on every exit.** `staging()` removes its directory in a ``finally``:
   success, failure, exception, cancellation. Removal takes the EXCLUSIVE lock
   first -- waiting briefly for a finishing worker -- and never removes a tree
   another process still holds; such a tree is queued and retried by the sweeper.
3. **Swept by proof, completely.** `sweep()` removes a token's directory only when
   its owner is proven ``dead`` AND every ``.inuse`` in it can be locked
   exclusively. ``alive``/``unknown`` owners are never touched. Removal RENAMES the
   tree to ``.trash-*`` first -- no partial tree ever survives under a usable name
   -- then deletes it; an interrupted delete is finished by the next pass.

Legacy entries (from before this layout: no token, no ``.inuse``) were written by
code that is no longer running. One is removed only when nothing in it changed
since THIS PID namespace started (the container's init), which a process that
could still be using it -- one from before the container -- cannot have survived.
Where the namespace start cannot be read, legacy entries are kept.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets import process_liveness

logger = logging.getLogger(__name__)

STAGING_DIR = ".workspace-staging"
INUSE_NAME = ".inuse"
_TRASH_PREFIX = ".trash-"
_TOKEN_NAME = re.compile(r"^proc_[0-9a-f]{24}$")
_POSIX = os.name == "posix"

#: How often the background sweeper runs after its startup pass.
SWEEP_INTERVAL_S = 600.0
#: How long an owner's own removal waits for a finishing worker to let go.
REMOVE_WAIT_S = 30.0

#: This process's shares, by staging path. Closed by `remove`.
_HELD: dict[str, int] = {}
#: Staging this process owns but could not remove yet; the sweeper retries.
_PENDING: set[str] = set()
_STATE_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# In-use locks
# --------------------------------------------------------------------------- #


def _flock(fd: int, *, exclusive: bool, blocking: bool) -> bool:
    import fcntl

    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(fd, flags)
    except OSError:
        return False
    return True


def hold_in_use(staging_dir: str | Path) -> int | None:
    """Take a SHARED lock on ``staging_dir``'s ``.inuse`` and return its fd.

    Keep the fd open for as long as the tree is used; pass it to any child that
    uses the tree. None where advisory shared locks do not exist (Windows).
    """
    if not _POSIX:
        return None
    path = Path(staging_dir) / INUSE_NAME
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    if not _flock(fd, exclusive=False, blocking=True):
        os.close(fd)
        raise OSError(f"could not mark staging in use: {staging_dir}")
    return fd


def in_use_fd(staging_dir: str | Path) -> int | None:
    """This process's share on ``staging_dir``, to pass to a git the PARENT runs
    against it (`workspace_git.inheriting`). None when not held here."""
    with _STATE_LOCK:
        return _HELD.get(str(staging_dir))


class Uninspectable(OSError):
    """A tree whose in-use locks could not be inspected. KEPT (it may be a live
    worker's) and reported, never counted as simply in use."""


def _lock_tree_exclusive(tree: Path, *, wait_s: float = 0.0) -> list[int] | None:
    """Exclusive locks on every ``.inuse`` in ``tree``, or None if any is held;
    raises `Uninspectable` when a lock could not be inspected at all.

    Held by the caller while it removes the tree, so nothing can mark it in use
    in between. Always returns [] where shared locks do not exist.
    """
    if not _POSIX:
        return []
    if not os.path.lexists(tree):
        return []
    deadline = time.monotonic() + max(0.0, wait_s)
    fds: list[int] = []
    walk_errors: list[OSError] = []

    def _abort() -> None:
        for held in fds:
            os.close(held)

    # FAIL CLOSED: a directory that cannot be walked, or an .inuse that cannot
    # be opened, is a lock we could not inspect -- and an uninspected lock may
    # be a live worker's (gpt-6-astra, PR #4143 round 2).
    for dirpath, dirnames, filenames in os.walk(
        tree, followlinks=False, onerror=walk_errors.append,
    ):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        if INUSE_NAME not in filenames:
            continue
        try:
            fd = os.open(os.path.join(dirpath, INUSE_NAME), os.O_RDWR | os.O_NOFOLLOW)
        except OSError as exc:
            _abort()
            raise Uninspectable(f"{dirpath}: in-use lock unreadable: {exc}") from None
        while not _flock(fd, exclusive=True, blocking=False):
            if time.monotonic() >= deadline:
                os.close(fd)
                _abort()
                return None
            time.sleep(0.05)
        fds.append(fd)
    if walk_errors:
        _abort()
        raise Uninspectable(f"{tree}: could not walk: {walk_errors[0]}")
    return fds


# --------------------------------------------------------------------------- #
# Create / remove
# --------------------------------------------------------------------------- #


def staging_root(base_path: str | Path) -> Path:
    return Path(base_path) / STAGING_DIR


def create(base_path: str | Path, *parts: str) -> Path:
    """A fresh staging directory owned by this process and marked in use.

    The liveness lock is taken FIRST, and the in-use share before the directory
    is handed to anyone, so there is no instant at which it exists and could be
    proven abandoned.
    """
    for part in parts:
        if not part or Path(part).name != part or part in {".", ".."}:
            raise ValueError(f"not a single path segment: {part!r}")
    root = staging_root(base_path)
    root.mkdir(parents=True, exist_ok=True)
    token = process_liveness.owner_token(root)
    path = root.joinpath(token, *parts)
    path.mkdir(parents=True, exist_ok=True)
    fd = hold_in_use(path)
    if fd is not None:
        with _STATE_LOCK:
            _HELD[str(path)] = fd
    return path


def _tree_bytes_and_newest(path: Path) -> tuple[int, float]:
    """Logical bytes and newest mtime of a tree, without following links."""
    try:
        top = os.lstat(path)
    except FileNotFoundError:
        return 0, 0.0
    total, newest = 0, top.st_mtime
    if not stat.S_ISDIR(top.st_mode):
        return (top.st_size if stat.S_ISREG(top.st_mode) else 0), newest
    stack = [path]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            newest = max(newest, st.st_mtime)
            if stat.S_ISDIR(st.st_mode):
                stack.append(Path(entry.path))
            elif stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total, newest


def _rmtree(path: Path) -> None:
    def _onerror(func, target, exc_info):  # noqa: ANN001 - shutil callback
        if isinstance(exc_info[1], FileNotFoundError):
            return  # another remover got there first
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(target)
        except FileNotFoundError:
            return

    shutil.rmtree(path, onerror=_onerror)


def _remove_completely(root: Path, path: Path) -> bool:
    """Rename ``path`` to trash inside ``root``, then delete the trash.

    The caller holds the tree's exclusive in-use locks. Returns True when nothing
    is left at either name. On False the tree is at a ``.trash-*`` name (or, if
    the rename itself failed, where it was) and a later pass finishes it.
    """
    if not os.path.lexists(path):
        return True
    trash = root / f"{_TRASH_PREFIX}{secrets.token_hex(8)}"
    try:
        os.rename(path, trash)
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("workspace staging could not be moved aside: %s", path)
        return False
    try:
        _rmtree(trash)
    except OSError:
        logger.exception("workspace staging removal incomplete; the sweep retries: %s", trash)
    if os.path.lexists(trash):
        logger.error("workspace staging still present after removal; the sweep retries: %s", trash)
        return False
    return True


def _root_of(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == STAGING_DIR:
            return parent
    raise ValueError(f"{path} is not inside a {STAGING_DIR} directory")


def _remove_locked(root: Path, path: Path, *, wait_s: float) -> bool:
    locks = _lock_tree_exclusive(path, wait_s=wait_s)
    if locks is None:
        return False  # still in use by another process
    try:
        return _remove_completely(root, path)
    finally:
        for fd in locks:
            os.close(fd)


def remove(path: str | Path, *, wait_s: float = REMOVE_WAIT_S) -> bool:
    """Remove one of this process's staging directories completely.

    Drops this process's share, then removes the tree once no other process holds
    one (waiting up to ``wait_s`` for a finishing worker). A tree still in use,
    or one whose removal failed, is queued and retried by the sweeper -- it is
    never left for process death to reclaim (gpt-6-astra, PR #4143 round 1).
    Never raises: it runs in a ``finally`` whose own exception must win.
    """
    key = str(path)
    try:
        with _STATE_LOCK:
            fd = _HELD.pop(key, None)
        if fd is not None:
            os.close(fd)
        target = Path(path)
        if _remove_locked(_root_of(target), target, wait_s=wait_s):
            with _STATE_LOCK:
                _PENDING.discard(key)
            return True
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("workspace staging removal failed for %s", path)
    with _STATE_LOCK:
        _PENDING.add(key)
    logger.warning("workspace staging not removed yet; queued for retry: %s", path)
    return False


def retry_pending() -> int:
    """Retry this process's queued removals. Returns how many completed."""
    with _STATE_LOCK:
        queued = sorted(_PENDING)
    done = 0
    for key in queued:
        if remove(key, wait_s=0.0):
            done += 1
    return done


@contextmanager
def staging(base_path: str | Path, *parts: str) -> Iterator[Path]:
    """``with staging(base, run, node) as path:`` -- removed on every exit."""
    path = create(base_path, *parts)
    try:
        yield path
    finally:
        remove(path)


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #


def _namespace_started_at() -> float | None:
    """When this PID namespace's init started (the container's start), or None.

    A process from BEFORE it -- the only kind that could be using a legacy-layout
    entry -- cannot be running inside it.
    """
    try:
        with open("/proc/stat", encoding="ascii") as handle:
            btime = next(
                float(line.split()[1]) for line in handle if line.startswith("btime ")
            )
        with open("/proc/1/stat", encoding="ascii") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        start_ticks = float(fields[19])  # field 22 overall; 20th after ")"
        return btime + start_ticks / float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError, StopIteration, AttributeError):
        return None


@dataclass
class SweepReport:
    removed: int = 0
    removed_bytes: int = 0
    kept_live: int = 0
    kept_unknown: int = 0
    failed: int = 0
    roots: list[str] = field(default_factory=list)

    def add(self, other: SweepReport) -> None:
        self.removed += other.removed
        self.removed_bytes += other.removed_bytes
        self.kept_live += other.kept_live
        self.kept_unknown += other.kept_unknown
        self.failed += other.failed
        self.roots.extend(other.roots)


def sweep(base_path: str | Path) -> SweepReport:
    """Remove every staging entry under ``base_path`` that is provably unused.

    Idempotent: a second pass over the same state removes nothing more.
    """
    report = SweepReport()
    root = staging_root(base_path)
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return report
    if not stat.S_ISDIR(info.st_mode):
        return report  # a link or a file named like the root is not ours to walk
    report.roots.append(str(root))
    legacy_cutoff: float | None = None
    legacy_cutoff_read = False
    for entry in sorted(os.listdir(root)):
        path = root / entry
        if entry == process_liveness.LIVENESS_DIR:
            continue
        if entry.startswith(_TRASH_PREFIX):
            pass  # trash is nobody's
        elif _TOKEN_NAME.match(entry):
            state = process_liveness.owner_state(root, entry)
            if state == process_liveness.ALIVE:
                report.kept_live += 1
                continue
            if state != process_liveness.DEAD:
                report.kept_unknown += 1  # never proven dead: never deleted
                continue
        else:
            if not legacy_cutoff_read:
                legacy_cutoff, legacy_cutoff_read = _namespace_started_at(), True
            _, newest = _tree_bytes_and_newest(path)
            if legacy_cutoff is None or newest >= legacy_cutoff:
                report.kept_unknown += 1
                continue
        size, _ = _tree_bytes_and_newest(path)
        # A dead owner does not prove a dead WORKER: whatever still holds the
        # tree's in-use share keeps it.
        try:
            locks = _lock_tree_exclusive(path)
        except Uninspectable as exc:
            # KEPT -- it may be a live worker's -- but loudly, and counted as a
            # failure, so a tree that is uninspectable forever is visible on
            # every pass instead of passing as "in use" (round 3, P2).
            logger.warning("workspace staging kept, locks uninspectable: %s", exc)
            report.failed += 1
            continue
        if locks is None:
            report.kept_live += 1
            continue
        try:
            removed = _remove_completely(root, path)
        finally:
            for fd in locks:
                os.close(fd)
        if removed:
            report.removed += 1
            report.removed_bytes += size
            if _TOKEN_NAME.match(entry):
                process_liveness.remove_if_dead(
                    root, entry, still_named=lambda t: os.path.lexists(root / t),
                )
        else:
            report.failed += 1
    return report


#: Broker-private, 1002:1101 2700 after the owner split: the daemon holds no
#: access to it and must not try. No workspace operation ever runs as the
#: broker, so no staging can exist there -- sweeping it only produced a
#: PermissionError traceback and a permanently non-zero `failed` count at every
#: boot (found by scripts/role_image_oracle.py on the migrated volume).
BROKER_PRIVATE_DIR = ".broker"


def sweep_data_root(data_root: str | Path) -> SweepReport:
    """Retry this process's queued removals, sweep the data root's staging and
    every universe directory's staging, and log the inventory removed."""
    base = Path(data_root)
    report = SweepReport()
    report.removed += retry_pending()
    candidates = [base]
    try:
        for child in sorted(base.iterdir()):
            if child.name == BROKER_PRIVATE_DIR:
                continue
            if child.is_dir() and not child.is_symlink():
                candidates.append(child)
    except OSError:
        logger.exception("workspace staging sweep could not list %s", base)
    for candidate in candidates:
        try:
            report.add(sweep(candidate))
        except Exception:  # noqa: BLE001 - one root must not stop the others
            logger.exception("workspace staging sweep failed under %s", candidate)
            report.failed += 1
    if report.removed or report.failed:
        logger.warning(
            "workspace staging sweep: removed %d dir(s), %d bytes; kept %d live, "
            "%d unproven; %d failed (retried next pass)",
            report.removed, report.removed_bytes, report.kept_live,
            report.kept_unknown, report.failed,
        )
    return report


_SWEEPER: threading.Thread | None = None
_STOP = threading.Event()
_SWEEPER_LOCK = threading.Lock()


def start_sweeper(data_root: str | Path, *, interval_s: float = SWEEP_INTERVAL_S) -> bool:
    """Sweep now and then every ``interval_s``, on a daemon thread. Once per
    process; returns False when one is already running."""
    global _SWEEPER
    with _SWEEPER_LOCK:
        if _SWEEPER is not None and _SWEEPER.is_alive():
            return False
        _STOP.clear()

        def _loop() -> None:
            while True:
                try:
                    sweep_data_root(data_root)
                except Exception:  # noqa: BLE001 - the next pass retries
                    logger.exception("workspace staging sweep pass failed")
                if _STOP.wait(interval_s):
                    return

        _SWEEPER = threading.Thread(target=_loop, name="workspace-staging-sweep", daemon=True)
        _SWEEPER.start()
        return True


def stop_sweeper(timeout_s: float = 5.0) -> bool:
    global _SWEEPER
    with _SWEEPER_LOCK:
        thread = _SWEEPER
        _STOP.set()
    if thread is None:
        return True
    thread.join(timeout_s)
    with _SWEEPER_LOCK:
        if _SWEEPER is thread and not thread.is_alive():
            _SWEEPER = None
    return not thread.is_alive()


__all__ = [
    "BROKER_PRIVATE_DIR",
    "INUSE_NAME",
    "STAGING_DIR",
    "SweepReport",
    "create",
    "hold_in_use",
    "remove",
    "retry_pending",
    "staging",
    "staging_root",
    "start_sweeper",
    "stop_sweeper",
    "sweep",
    "sweep_data_root",
]
