"""The disk budget every jailed process runs under (tool jail and provider jail).

A jailed process writes straight into its universe directory, past every
platform write path that `storage_accounting` gates. Without this, those bytes
are only counted afterwards, and one universe can fill the volume every user
shares (concern 2026-10-01-no-per-universe-disk-budget-in-jails). This is the
interim guard; a filesystem project quota is the by-construction fix.

At launch, `open_budget`:

* refuses when the shared volume is below its free-space or free-inode floor --
  the cross-user line, same as the tool jail always had;
* measures the universe's own files FRESH and reserves what still fits in the
  owning account's storage (`storage_accounting.reserve_fitted`), capped per
  launch. The reservation is pending while the process runs, so two concurrent
  launches cannot both spend the same headroom.

While the process runs, the supervisor polls `DiskBudget.breach()`:

* below the volume floor -> ``disk_limit``;
* the volume grew by more than this launch's bound since it started, or
  `WALK_SECONDS` passed since the last walk -> walk the universe directory; if
  THIS universe grew past the bound -> ``storage_limit``. The volume signal is
  only a trigger: another user's writes never kill this process, they only cost
  one walk. The timed walk is there because the signal can also be masked --
  another user DELETING while this one writes leaves the volume flat.

An account that is already full is NOT refused: the process still starts with a
small grace budget, so the owner can still talk to their agent and free space
(``rm`` makes the walk shrink). Refusing would lock the owner out of the one
tool that can fix it -- the reason `storage_accounting` never gates chat. The
caller shows `DiskBudget.notice` instead.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)

_MiB = 1024 * 1024

#: Free space the shared data volume must keep: a launch is refused below it,
#: and a running jail is killed when its writes take the volume below it.
MIN_FREE_DISK_BYTES = 1024 * _MiB
#: Free inodes the shared volume must keep: many tiny files fill the inode
#: table long before free bytes show it.
MIN_FREE_INODES = 4096
#: The most one launch may add to its universe, even with more headroom: the
#: reservation is pending while it runs, so this is also what a concurrent
#: launch of the same account cannot use.
LAUNCH_BYTES_CAP = 1024 * _MiB
#: What a launch may still add when its account is full (or the ledger cannot
#: answer): enough for a provider CLI's session files and an agent's notes.
GRACE_BYTES = 16 * _MiB
#: Size of the jail's private ``/tmp`` (RAM-backed tmpfs).
TMP_BYTES = 256 * _MiB
#: Size of every other tmpfs a jail view mounts (masks, the mount root).
MASK_TMPFS_BYTES = 16 * _MiB
#: The longest a running jail goes without its universe being walked, whatever
#: the volume says: what bounds the overshoot when the cheap trigger is masked.
WALK_SECONDS = 5.0
#: How often a running launch re-stamps its reservation; well inside
#: `storage_accounting.RESERVED_TTL_S`.
RENEW_SECONDS = 120.0

DISK_LIMIT = "disk_limit"
STORAGE_LIMIT = "storage_limit"

_STORE = "universe_files"


class DiskFloorRefused(Exception):
    """The shared volume is below its floor; nothing was started."""


def _statvfs(path: Path):
    try:
        return os.statvfs(path)
    except (AttributeError, OSError):
        return None


def free_bytes(path: Path) -> int:
    """Bytes an unprivileged writer may still use, or -1 when unmeasurable."""
    stats = _statvfs(path)
    return -1 if stats is None else int(stats.f_bavail) * int(stats.f_frsize)


def free_inodes(path: Path) -> int:
    """Free inodes, or -1 when the filesystem has no fixed inode table."""
    stats = _statvfs(path)
    if stats is None:
        return -1
    favail = getattr(stats, "f_favail", -1)
    # Some filesystems (e.g. btrfs) report 0 inodes: they have no fixed table,
    # so the inode floor does not apply -- treat as "unmeasurable", never full.
    return -1 if favail in (-1, 0) and getattr(stats, "f_files", 0) == 0 else int(favail)


def used_bytes(path: Path) -> int:
    """Bytes in use on the volume holding ``path``, or -1 when unmeasurable."""
    stats = _statvfs(path)
    if stats is None:
        return -1
    return (int(stats.f_blocks) - int(stats.f_bfree)) * int(stats.f_frsize)


def floor_breach(path: Path, *, min_free_bytes: int, min_free_inodes: int) -> str | None:
    """Which floor the shared volume is below, as a sentence, or None."""
    free = free_bytes(path)
    if 0 <= free < min_free_bytes:
        return "the shared disk is nearly full"
    inodes = free_inodes(path)
    if 0 <= inodes < min_free_inodes:
        return "the shared disk is nearly out of inodes"
    return None


#: The one top-level entry no jail can write (the provider jail masks every
#: hidden directory but ``.runtime``; the tool jail binds an allowlist): the
#: platform's own checkout staging, which a concurrent workspace node fills.
_NOT_JAIL_WRITABLE = frozenset({".workspace-staging"})


def _jail_writable_bytes(root: Path) -> int:
    """Bytes under everything a jail can write in this universe -- WIDER than
    the account's ``universe_files`` store, which leaves out ``.runtime`` and
    ``workspaces`` (the provider jail can write both)."""
    from tinyassets import storage_accounting

    return storage_accounting._walk_bytes(root, exclude_top=_NOT_JAIL_WRITABLE)


@dataclass
class DiskBudget:
    """One launch's disk budget. Poll `breach`; call `settle` exactly once."""

    root: Path
    bound: int
    start_bytes: int
    min_free_bytes: int
    min_free_inodes: int
    notice: str = ""
    reservation: object | None = None
    _volume_baseline: int = -1
    _last_walk: float = 0.0
    _last_renew: float = 0.0
    _settled: bool = field(default=False, repr=False)
    _lease_lost: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self._volume_baseline = used_bytes(self.root)
        self._last_walk = self._last_renew = time.monotonic()

    def _renew(self) -> bool:
        """Keep capacity held, stopping permanently if the ledger loses it."""
        if self._settled or self._lease_lost:
            return False
        now = time.monotonic()
        if self.reservation is None or now - self._last_renew < RENEW_SECONDS:
            return True
        from tinyassets import storage_accounting

        if not storage_accounting.renew_checked(self.reservation):
            self._lease_lost = True
            return False
        self._last_renew = now
        return True

    def growth(self) -> int:
        """Bytes everything this jail can write grew by since launch (a walk)."""
        return _jail_writable_bytes(self.root) - self.start_bytes

    def breach(self) -> str | None:
        """``disk_limit``, ``storage_limit`` or None. Cheap unless triggered."""
        if not self._renew():
            return STORAGE_LIMIT
        if floor_breach(
            self.root, min_free_bytes=self.min_free_bytes,
            min_free_inodes=self.min_free_inodes,
        ):
            return DISK_LIMIT
        used = used_bytes(self.root)
        triggered = used >= 0 and self._volume_baseline >= 0 and (
            used - self._volume_baseline > self.bound
        )
        if not triggered and time.monotonic() - self._last_walk < WALK_SECONDS:
            return None
        self._last_walk = time.monotonic()
        try:
            grown = self.growth()
        except OSError:
            _log.warning("jail disk budget: universe walk failed", exc_info=True)
            return STORAGE_LIMIT  # cannot tell whether it grew: stop
        if grown > self.bound:
            return STORAGE_LIMIT
        if used >= 0:
            # Someone else's writes moved the volume; re-arm from here so the
            # next poll does not walk again for the same bytes.
            self._volume_baseline = used - max(0, grown)
        return None

    def settle(self) -> None:
        """Release the reservation and mark the universe's files for re-measure.

        The bytes that landed are counted by the next measurement (the next
        launch measures fresh; every other gate re-measures a dirty store)."""
        if self._settled:
            return
        self._settled = True
        from tinyassets import storage_accounting

        if self.reservation is not None:
            storage_accounting.release(self.reservation)
        for store in (_STORE, "workspaces"):
            storage_accounting.touch(self.root.parent, self.root.name, store)


def _human(size: int) -> str:
    from tinyassets import storage_accounting

    return storage_accounting._human(size)


def open_budget(
    universe_dir: str | os.PathLike[str],
    *,
    min_free_bytes: int | None = None,
    min_free_inodes: int | None = None,
) -> DiskBudget:
    """The budget for one jailed launch in ``universe_dir``, or refuse.

    Raises `DiskFloorRefused` when the shared volume is below a floor. A full
    account is not refused: it gets `GRACE_BYTES` and a `notice` to show.
    """
    from tinyassets import storage_accounting
    from tinyassets.universe_owner import owner_of

    if min_free_bytes is None:
        min_free_bytes = MIN_FREE_DISK_BYTES
    if min_free_inodes is None:
        min_free_inodes = MIN_FREE_INODES
    root = Path(universe_dir).resolve()
    below = floor_breach(root, min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes)
    if below:
        raise DiskFloorRefused(below)
    base, universe_id = root.parent, root.name
    notice = ""
    reservation = None
    try:
        account = owner_of(base, universe_id)
        # FRESH: this universe's files are what a jail writes, and the last
        # jailed run only marked them dirty.
        storage_accounting.measure(base, universe_id, _STORE)
        reservation, bound = storage_accounting.reserve_fitted(
            base, account_id=account, scope_id=universe_id, store=_STORE,
            cap=LAUNCH_BYTES_CAP, minimum=1,
        )
        bound = max(int(bound), GRACE_BYTES)
    except storage_accounting.StorageRefused as refused:
        bound = GRACE_BYTES
        notice = _full_notice(refused)
    except (sqlite3.Error, OSError, ValueError):
        _log.exception("jail disk budget: storage ledger unavailable")
        bound = GRACE_BYTES
        notice = (
            f"[storage accounting is unavailable right now, so this call may add at "
            f"most {_human(GRACE_BYTES)} to the command center]"
        )
    try:
        start = _jail_writable_bytes(root)
    except OSError:
        if reservation is not None:
            storage_accounting.release(reservation)
        raise DiskFloorRefused("the command center could not be measured") from None
    return DiskBudget(
        root=root, bound=bound, start_bytes=int(start),
        min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes,
        notice=notice, reservation=reservation,
    )


def _full_notice(refused) -> str:
    from tinyassets import storage_accounting

    if refused.record.get("failure_class") != storage_accounting.FAILURE_QUOTA:
        return (
            f"[storage accounting could not answer, so this call may add at most "
            f"{_human(GRACE_BYTES)} to the command center]"
        )
    # Never the owner's numbers here: the caller may be a collaborator.
    return (
        "[this command center's owner is out of cloud storage: this call may add at most "
        f"{_human(GRACE_BYTES)}, and is stopped past that. Delete files to make room, "
        "or the owner can upgrade]"
    )

