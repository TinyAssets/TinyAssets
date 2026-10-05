"""Account-total storage supervision for tool and provider jails.

Jails write outside the gated write APIs. Measure all of the owner's stores at
admission and while running; never reserve unused capacity for a launch. Nested
provider/tool calls and concurrent runs share the same total-storage quota.
The shared-volume byte/inode floors remain cross-user safety guards.

This is a polling guard, not a filesystem project quota: writes can overshoot
between polls. An already-full account may run cleanup without increasing its
initial total; each successful measurement ratchets that ceiling down to quota.
No per-call recovery allowance is minted. Accounting failures fail closed.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger(__name__)

_MiB = 1024 * 1024

#: Free space the shared data volume must keep: a launch is refused below it,
#: and a running jail is killed when its writes take the volume below it.
MIN_FREE_DISK_BYTES = 1024 * _MiB
#: Free inodes the shared volume must keep: many tiny files fill the inode
#: table long before free bytes show it.
MIN_FREE_INODES = 4096
#: Size of the jail's private ``/tmp`` (RAM-backed tmpfs).
TMP_BYTES = 256 * _MiB
#: Size of every other tmpfs a jail view mounts (masks, the mount root).
MASK_TMPFS_BYTES = 16 * _MiB
#: The longest a running jail goes without its universe being walked, whatever
#: the volume says: what bounds the overshoot when the cheap trigger is masked.
WALK_SECONDS = 5.0
#: Coalesce overlapping supervisors so volume activity cannot cause scan storms.
ACCOUNT_CHECK_SECONDS = 0.5
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


#: Protected platform directories: provider jails mask them even on first use;
#: tool jails never bind them. Concurrent platform writes are not jail growth.
_NOT_JAIL_WRITABLE = frozenset({".workspace-staging", ".credentials"})


def _jail_writable_bytes(root: Path) -> int:
    """Bytes under everything a jail can write in this universe -- WIDER than
    the account's ``universe_files`` store, which leaves out ``workspaces``.
    Runtime session/snapshot subtrees remain writable and charged."""
    from tinyassets import storage_accounting

    return storage_accounting._walk_bytes(root, exclude_top=_NOT_JAIL_WRITABLE)


@dataclass
class _AccountScan:
    lock: object = field(default_factory=threading.Lock)
    checked: float = float("-inf")
    usage: object | None = None


@lru_cache(maxsize=256)
def _account_scan(base: Path, account: str) -> _AccountScan:
    return _AccountScan()


def _account_usage(root: Path, account: str, *, force: bool = False):
    """Account totals with coalesced walks across nested/concurrent jails.

    Rewalk every owned jail-writable store (including idle homes). Other gated
    stores need measurement only when dirty/stale. The shared cache only bounds
    scan frequency, never reserves capacity; admission and exit bypass it.
    """
    from tinyassets import storage_accounting as sa

    base = root.parent
    scan = _account_scan(base, account)
    with scan.lock:
        now = time.monotonic()
        if not force and now - scan.checked < ACCOUNT_CHECK_SECONDS:
            return scan.usage
        scan.checked = float("-inf")  # a failed refresh cannot leave a reusable snapshot
        pairs = sa._scopes(base, account)
        selected = set(sa._stale_pairs(base, pairs))
        selected.update((scope, store) for scope, store in pairs
                        if store in (_STORE, "workspaces"))
        for scope, store in sorted(selected):
            sa.measure(base, scope, store)
        scan.usage = sa.usage(base, account)
        scan.checked = time.monotonic()
        return scan.usage


@dataclass
class DiskBudget:
    """One supervisor observing the owner's total; it owns no write capacity."""

    root: Path
    bound: int
    start_bytes: int
    min_free_bytes: int
    min_free_inodes: int
    account: str | None = None
    notice: str = ""
    _ceiling: int = 0
    _volume_baseline: int = -1
    _last_walk: float = 0.0
    _settled: bool = field(default=False, repr=False)
    _failed: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self._volume_baseline = used_bytes(self.root)
        self._last_walk = time.monotonic()

    def growth(self) -> int:
        return _jail_writable_bytes(self.root) - self.start_bytes

    def breach(self, *, force: bool = False) -> str | None:
        """Check total storage; ``force`` also checks short, completed calls."""
        if self._settled or self._failed:
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
        if not force and not triggered and time.monotonic() - self._last_walk < WALK_SECONDS:
            return None
        self._last_walk = time.monotonic()
        if self.account:
            try:
                current = _account_usage(
                    self.root, self.account, force=force or (triggered and self.bound > 0),
                )
            except (sqlite3.Error, OSError, ValueError):
                _log.exception("jail total storage could not be measured")
                self._failed = True
                return STORAGE_LIMIT
            # A lowered quota takes effect; cleanup can ratchet an initial
            # overage down but no launch receives new recovery write capacity.
            self._ceiling = max(current.quota_bytes, min(self._ceiling, current.used_bytes))
            self.bound = max(0, self._ceiling - current.used_bytes)
            if current.used_bytes > self._ceiling:
                return STORAGE_LIMIT
        self._volume_baseline = used
        return None

    def settle(self) -> None:
        """Mark writable stores dirty for the normal gated write paths."""
        if self._settled:
            return
        self._settled = True
        from tinyassets import storage_accounting

        for store in (_STORE, "workspaces"):
            storage_accounting.touch(self.root.parent, self.root.name, store)


def open_budget(
    universe_dir: str | os.PathLike[str],
    *,
    min_free_bytes: int | None = None,
    min_free_inodes: int | None = None,
) -> DiskBudget:
    """Admit cleanup even at quota, but never grant a per-launch write budget."""
    from tinyassets.universe_owner import owner_of

    if min_free_bytes is None:
        min_free_bytes = MIN_FREE_DISK_BYTES
    if min_free_inodes is None:
        min_free_inodes = MIN_FREE_INODES
    root = Path(universe_dir).resolve()
    below = floor_breach(root, min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes)
    if below:
        raise DiskFloorRefused(below)
    try:
        account = owner_of(root.parent, root.name)
        current = _account_usage(root, account, force=True) if account else None
        start = _jail_writable_bytes(root)
    except (sqlite3.Error, OSError, ValueError):
        _log.exception("jail storage accounting unavailable at admission")
        raise DiskFloorRefused(
            "cloud storage could not be measured; retry after accounting recovers",
        ) from None
    ceiling = max(current.quota_bytes, current.used_bytes) if current else 0
    # Unattributed universes remain governed by the shared-volume floor (the
    # existing attribution policy), not an invented per-call quota.
    bound = max(0, ceiling - current.used_bytes) if current else max(0, free_bytes(root))
    notice = ""
    if current and current.measured_bytes >= current.quota_bytes:
        notice = (
            "[this command center's owner is out of cloud storage: "
            "delete files to make room; total storage cannot grow]"
        )
    return DiskBudget(
        root=root, bound=bound, start_bytes=start, account=account,
        min_free_bytes=min_free_bytes, min_free_inodes=min_free_inodes,
        notice=notice, _ceiling=ceiling,
    )
