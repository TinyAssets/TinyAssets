"""Bounded retained-file metadata, not complete attribution or quota authority."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from tinyassets import workspace_fs as fs
from tinyassets import workspace_owner_pool as owner_pool
from tinyassets import workspace_pool as pool
from tinyassets.ttl_memo import TTLMemo, read_ttl

MAX_ENTRIES = 10_000
MAX_LEASE_ROWS = 256
MAX_DEPTH = 64
SCAN_SECONDS = 0.2
MAX_CACHE_AGE_SECONDS = 60.0
_memo = TTLMemo(max_entries=128)
_scan_slots = threading.BoundedSemaphore(2)
_CATEGORIES = ("permanent_workspaces", "provider_runtime", "other_universe_files", "scratch")


def _base() -> dict:
    return {
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": "universe_files_and_lease_attributed_scratch",
        "unit": "logical_regular_file_bytes",
        "not_atomic_snapshot": True,
        "exclusions": ["shared_root_records", "unattributed_scratch_or_staging",
                       "allocated_disk_blocks", "billing_attribution"],
        "hard_link_attribution": "first_category_in_declared_order",
        "category_order": list(_CATEGORIES),
        "accounting_note": (
            "Observed footprint includes platform provider runtime and scratch; "
            "it is not account usage or evidence that the account quota is full. "
            "Provider runtime includes protected credential materialization/cache."
        ),
    }


def _unavailable(reason: str) -> dict:
    return {**_base(), "availability": "unavailable", "reasons": [reason]}


def _identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


class _BoundReached(Exception):
    pass


class _Walker:
    def __init__(self):
        self.deadline = time.monotonic() + SCAN_SECONDS
        self.entries = 0
        self.seen: set[tuple[int, int]] = set()
        self.reasons: set[str] = set()
        self.categories = {name: {"observed_logical_bytes": 0, "files_observed": 0}
                           for name in _CATEGORIES}

    def check(self):
        if time.monotonic() >= self.deadline:
            self.reasons.add("time_bound")
            raise _BoundReached
        if self.entries >= MAX_ENTRIES:
            self.reasons.add("entry_bound")
            raise _BoundReached

    def child(self, parent: int, name: str, category: str, depth: int = 0,
              *, missing_is_absent: bool = False, exclude=()):
        self.check()
        self.entries += 1
        observed = False
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            observed = True
            identity = _identity(info)
            if identity in self.seen:
                return
            if stat.S_ISREG(info.st_mode):
                self.seen.add(identity)
                self.categories[category]["observed_logical_bytes"] += info.st_size
                self.categories[category]["files_observed"] += 1
            elif stat.S_ISDIR(info.st_mode):
                if depth >= MAX_DEPTH:
                    self.reasons.add("depth_bound")
                    return
                child = fs.open_subdir_nofollow(parent, name)
                try:
                    if _identity(os.fstat(child)) != identity:
                        self.reasons.add("directory_changed")
                        return
                    self.seen.add(identity)
                    self.walk(child, category, depth + 1, exclude=exclude)
                finally:
                    os.close(child)
            else:
                self.reasons.add("links_or_special_files")
        except FileNotFoundError:
            # A derived path may legitimately be absent after cleanup.
            if observed or not missing_is_absent:
                self.reasons.add("entry_changed")
            return
        except OSError:
            self.reasons.add("entry_unreadable")

    def walk(self, directory: int, category: str, depth: int = 0, *, exclude=()):
        before = os.fstat(directory)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    self.check()
                    if entry.name not in exclude:
                        self.child(directory, entry.name, category, depth)
            after = os.fstat(directory)
            if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                self.reasons.add("directory_changed")
        except OSError:
            self.reasons.add("entry_unreadable")

    def result(self) -> dict:
        return {
            **_base(), "availability": "partial" if self.reasons else "observed",
            "reasons": sorted(self.reasons), "categories": self.categories,
            "observed_logical_bytes": sum(
                item["observed_logical_bytes"] for item in self.categories.values()
            ),
            "entries_visited": self.entries,
        }


def _scratch(walker: _Walker, universe_fd: int, root: Path, uid: str, readonly):
    try:
        with readonly(root / uid / ".runs.db") as conn:
            rows = conn.execute(
                "SELECT CASE WHEN length(lease_id)<=255 THEN lease_id END, "
                "CASE WHEN length(CAST(generation AS TEXT))<=20 THEN generation END "
                "FROM workspace_leases "
                "WHERE universe_id=? AND storage_class=? AND state<>? "
                "ORDER BY lease_id LIMIT ?",
                (uid, pool.STORAGE_SCRATCH, pool.STATE_AVAILABLE, MAX_LEASE_ROWS + 1),
            ).fetchall()
    except (OSError, sqlite3.Error):
        walker.reasons.add("lease_records_unavailable")
        return
    if len(rows) > MAX_LEASE_ROWS:
        walker.reasons.add("lease_row_bound")
    for lease_id, generation in rows[:MAX_LEASE_ROWS]:
        walker.check()
        if (not isinstance(lease_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", lease_id)
                or not re.fullmatch(r"[0-9]{1,20}", str(generation))):
            walker.reasons.add("invalid_lease_record")
            continue
        generation = int(generation)
        # Derive names using the canonical layout, never database path strings.
        source, quarantine = pool.scratch_paths(Path(owner_pool.SCRATCH_DIR),
                                                lease_id, generation)
        try:
            # Since the owner split the pool is inside the command center:
            # ``workspaces/scratch``, beside the permanent generations.
            workspaces_fd = fs.open_subdir_nofollow(universe_fd, pool.WORKSPACES_DIR)
            try:
                scratch_fd = fs.open_subdir_nofollow(workspaces_fd, owner_pool.SCRATCH_DIR)
            finally:
                os.close(workspaces_fd)
            try:
                walker.child(scratch_fd, source.name, "scratch", missing_is_absent=True)
                try:
                    quarantine_fd = fs.open_subdir_nofollow(scratch_fd, pool.QUARANTINE_DIR)
                except FileNotFoundError:
                    continue
                try:
                    walker.child(quarantine_fd, quarantine.name, "scratch", missing_is_absent=True)
                finally:
                    os.close(quarantine_fd)
            finally:
                os.close(scratch_fd)
        except FileNotFoundError:
            continue
        except OSError:
            walker.reasons.add("scratch_unreadable")


def _measure(root: Path, uid: str, identity: tuple[int, int], readonly) -> dict:
    if not _scan_slots.acquire(blocking=False):
        return _unavailable("contention")
    try:
        walker = _Walker()
        root_fd = fs.open_dir_nofollow(root)
        try:
            universe_fd = fs.open_subdir_nofollow(root_fd, uid)
            try:
                if _identity(os.fstat(universe_fd)) != identity:
                    return _unavailable("directory_changed")
                try:
                    walker.child(universe_fd, pool.WORKSPACES_DIR, "permanent_workspaces",
                                 missing_is_absent=True,
                                 exclude=(owner_pool.SCRATCH_DIR,))
                    walker.child(universe_fd, ".credentials", "provider_runtime",
                                 missing_is_absent=True)
                    walker.walk(universe_fd, "other_universe_files",
                                exclude=(pool.WORKSPACES_DIR, ".credentials"))
                    _scratch(walker, universe_fd, root, uid, readonly)
                except _BoundReached:
                    pass
                return walker.result()
            finally:
                os.close(universe_fd)
        finally:
            os.close(root_fd)
    except NotImplementedError:
        return _unavailable("unsupported_safe_traversal")
    except OSError:
        return _unavailable("root_unreadable")
    finally:
        _scan_slots.release()


def observe(root: Path, uid: str, *, readonly) -> dict:
    """Internal seam: caller MUST verify current admin authority before each call."""
    ttl = min(MAX_CACHE_AGE_SECONDS, max(0.0, read_ttl(
        "TINYASSETS_STORAGE_SNAPSHOT_TTL_S", MAX_CACHE_AGE_SECONDS,
    )))
    try:
        info = (root / uid).stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            return _unavailable("root_unreadable")
        identity = _identity(info)
        key = repr((str(root), uid, identity))
        result = _memo.get(key, lambda: _measure(root, uid, identity, readonly), ttl=ttl)
        if result is None:
            result = _unavailable("contention")
    except OSError:
        result = _unavailable("root_unreadable")
    result["cache_max_age_seconds"] = ttl
    return result
