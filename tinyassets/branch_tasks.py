"""Durable BranchTask queue.

A BranchTask is a queued *execution intent*: "run branch X against
universe Y with inputs Z." Distinct from WorkTargets, which are
content (the scene, the canon repair, the plan). One WorkTarget may
spawn zero or many BranchTasks.

The queue is per-universe (``<universe>/branch_tasks.json``) — sibling
to ``work_targets.json``. All mutations go through a sidecar ``.lock``
file so concurrent ``submit_request`` + daemon-claim + mark-status
paths can't clobber one another. This is the codebase's first
file-lock primitive; the pattern is also exercised by the queue
race tests.

Startup GC moves terminal tasks older than
``ARCHIVE_AFTER_DAYS`` into ``branch_tasks_archive.json``. The
archive is append-only and never read by the dispatcher.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

QUEUE_FILENAME = "branch_tasks.json"
ARCHIVE_FILENAME = "branch_tasks_archive.json"
LOCK_FILENAME = "branch_tasks.json.lock"
# Lease window. INVARIANT: must comfortably exceed the worst-case time between
# heartbeat refreshes, which happen per graph node (not on an independent
# timer). A single writer node can run the whole provider fallback chain, each
# attempt up to ModelConfig.timeout (300s) — so ~900s worst case. 1800s gives a
# 2x margin so a long-but-healthy node is never wrongly reclaimed mid-flight
# (Codex cross-family review, 2026-06-25: lease==provider-timeout was a race).
DEFAULT_LEASE_SECONDS = 1800

# Exposed for test override (invariant 10).
ARCHIVE_AFTER_DAYS = 30

VALID_TRIGGER_SOURCES = frozenset({
    "host_request",
    "user_request",
    "owner_queued",
    "goal_pool",
    "opportunistic",
    "paid_bid",
})

VALID_STATUSES = frozenset({
    "pending", "running", "succeeded", "failed", "cancelled",
})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# Valid transitions. `pending` can go to running or cancelled;
# running can go to any terminal state. Terminal states are sinks.
_VALID_TRANSITIONS = {
    "pending": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


@dataclass
class BranchTask:
    """Durable execution-intent record.

    Reserved fields (bid, goal_id, required_llm_type, evidence_url)
    are present in v1 with empty defaults so later producers can
    populate them without migration.
    """

    branch_task_id: str
    branch_def_id: str
    universe_id: str
    inputs: dict = field(default_factory=dict)
    trigger_source: str = "user_request"
    priority_weight: float = 0.0
    pickup_signal_weight: float = 0.0
    queued_at: str = ""
    claimed_by: str = ""
    status: str = "pending"
    bid: float = 0.0
    goal_id: str = ""
    required_llm_type: str = ""
    directed_daemon_id: str = ""
    evidence_url: str = ""
    error: str = ""
    cancel_requested: bool = False
    request_type: str = "branch_run"
    deadline: str = ""
    worker_owner_id: str = ""
    executor_worker_id: str = ""
    executor_runtime_id: str = ""
    lease_expires_at: str = ""
    heartbeat_at: str = ""
    last_progress_at: str = ""
    # When the task reached a terminal status (succeeded/failed/cancelled).
    # Powers the loop-stall signal in get_status: a backlog with zero terminal
    # transitions over a window is the 2026-06-25 wedge signature. Empty for
    # pre-field rows; stamped by mark_status on terminal transition.
    terminal_at: str = ""
    rung_claim_recommendations: list[dict] = field(default_factory=list)
    # Spawn depth. 0 for user/forward-triggered tasks. A task enqueued from
    # inside a running branch (via the in-node enqueue verb) carries
    # parent_depth + 1; a depth cap bounds runaway self-enqueue chains.
    depth: int = 0
    # Spawn lineage for the per-origin enqueue cap (Codex enqueue review,
    # 2026-05-30). ``parent`` = the task whose run enqueued this one; ``origin``
    # = the root of the whole spawn chain. Both are server-set from trusted
    # dispatch context, never from branch-authored inputs.
    parent_branch_task_id: str = ""
    origin_branch_task_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BranchTask":
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def queue_path(universe_path: Path) -> Path:
    return Path(universe_path) / QUEUE_FILENAME


def archive_path(universe_path: Path) -> Path:
    return Path(universe_path) / ARCHIVE_FILENAME


def _lock_path(universe_path: Path) -> Path:
    return Path(universe_path) / LOCK_FILENAME


@contextlib.contextmanager
def _file_lock(universe_path: Path) -> Iterator[None]:
    """Cross-platform exclusive lock on a sidecar .lock file.

    Windows uses ``msvcrt.locking`` with ``LK_LOCK`` (blocking with
    retry); POSIX uses ``fcntl.flock``. The sidecar pattern avoids
    racing with the JSON-read path: opening the data file with r+ for
    a lock serializes with json-read on Windows.

    The lock file is created on demand and left in place between
    operations; that is intentional — deleting it while another
    process holds the lock would unlink the descriptor and break the
    contract.

    Opened through ``universe_files.open_lock_file``: the lock name sits in a
    directory the universe's own processes can write, and a plain
    ``O_RDWR|O_CREAT`` on it follows a planted link and CREATES the link's
    target outside this universe.
    """
    from tinyassets.universe_files import open_lock_file

    Path(universe_path).mkdir(parents=True, exist_ok=True)
    fd = open_lock_file(universe_path, LOCK_FILENAME)
    try:
        if sys.platform == "win32":
            import msvcrt
            # Lock a single byte; retry on conflict. LK_LOCK blocks up
            # to ~10s per call, then raises OSError — loop until we
            # get it.
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
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
    finally:
        os.close(fd)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_window(*, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return (
        now.isoformat(),
        (now + timedelta(seconds=lease_seconds)).isoformat(),
    )


def _read_raw(qp: Path) -> list[dict]:
    from tinyassets.universe_files import MAX_PLATFORM_FILE_BYTES, read_data_path

    try:
        # Link-free: a planted queue link refuses rather than being read.
        data_bytes = read_data_path(qp, max_bytes=MAX_PLATFORM_FILE_BYTES)
    except OSError as exc:
        raise RuntimeError(f"Failed to read {qp}: {exc}") from exc
    if data_bytes is None:
        return []
    try:
        raw = data_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"Corrupt queue at {qp}: {exc}") from exc
    if not raw.strip():
        raise RuntimeError(
            f"Corrupt queue/history at {qp}: blank, expected a JSON list"
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupt queue at {qp}: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"Queue at {qp} is not a list")
    return data


def _write_raw(qp: Path, data: list[dict]) -> None:
    from tinyassets.universe_files import write_data_path

    # Link-free, with a fresh O_EXCL temp name: a fixed ``.tmp`` could be a
    # planted link (or hardlink) that the old write truncated through.
    write_data_path(qp, json.dumps(data, indent=2, default=str))


def read_queue(universe_path: Path) -> list[BranchTask]:
    """File-locked read. Returns [] on missing file.

    Raises ``RuntimeError`` on corrupt JSON — Hard Rule 8, no silent
    fallback.
    """
    qp = queue_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(qp)
    return [BranchTask.from_dict(row) for row in raw if isinstance(row, dict)]


def append_task(universe_path: Path, task: BranchTask) -> None:
    """File-locked append. Stamps ``queued_at`` if missing."""
    if task.trigger_source not in VALID_TRIGGER_SOURCES:
        raise ValueError(f"Invalid trigger_source: {task.trigger_source}")
    if task.status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {task.status}")
    if not task.queued_at:
        task.queued_at = _now_iso()
    qp = queue_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        raw.append(task.to_dict())
        _write_raw(qp, raw)


class QueueCapExceeded(RuntimeError):
    """A queue-growth cap (global active or per-origin lineage) would be
    exceeded. The task was NOT appended."""


def _lineage_size(rows: list[dict], origin_branch_task_id: str) -> int:
    """Count one origin's distinct identified tasks plus id-less rows.

    A GC interruption may leave the same identified row in both the live
    queue and archive. De-duplicating IDs prevents that safe overlap from
    permanently consuming two lineage slots. Id-less legacy rows remain
    conservative because they cannot be proven identical.
    """
    task_ids: set[str] = set()
    idless = 0
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("origin_branch_task_id") != origin_branch_task_id
        ):
            continue
        task_id = str(row.get("branch_task_id", "") or "").strip()
        if task_id:
            task_ids.add(task_id)
        else:
            idless += 1
    return len(task_ids) + idless


def append_task_capped(
    universe_path: Path,
    task: BranchTask,
    *,
    max_active: int | None = None,
    max_lineage: int | None = None,
) -> None:
    """File-locked append with atomic queue-growth containment.

    Under a SINGLE lock: count the current queue, enforce a global
    active-task cap and a per-origin spawn-lineage cap, then append. This is
    race-free — no read-then-append TOCTOU, so concurrent enqueues cannot
    overshoot a cap. Raises :class:`QueueCapExceeded` (task not appended)
    when a cap would be exceeded.

    Caps are skipped when their argument is ``None``. The lineage cap only
    applies when ``task.origin_branch_task_id`` is set.
    """
    if task.trigger_source not in VALID_TRIGGER_SOURCES:
        raise ValueError(f"Invalid trigger_source: {task.trigger_source}")
    if task.status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {task.status}")
    if not task.queued_at:
        task.queued_at = _now_iso()
    qp = queue_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        if max_active is not None:
            active = sum(
                1 for r in raw
                if isinstance(r, dict)
                and r.get("status") in ("pending", "running")
            )
            if active >= max_active:
                raise QueueCapExceeded(
                    f"queue has {active} active task(s) (cap {max_active})"
                )
        if max_lineage is not None and task.origin_branch_task_id:
            # Terminal descendants remain cap-bearing after GC. Read the
            # archive under this same universe lock so count/check/append is
            # atomic against both enqueue and collection.
            archived = _read_raw(archive_path(universe_path))
            lineage = _lineage_size(
                [*raw, *archived],
                task.origin_branch_task_id,
            )
            if lineage >= max_lineage:
                raise QueueCapExceeded(
                    f"spawn lineage '{task.origin_branch_task_id}' already has "
                    f"{lineage} task(s) (cap {max_lineage})"
                )
        raw.append(task.to_dict())
        _write_raw(qp, raw)


def claim_task(
    universe_path: Path,
    task_id: str,
    claimer: str,
    *,
    executor_worker_id: str | None = None,
    executor_runtime_id: str | None = None,
) -> BranchTask | None:
    """File-locked claim. Returns claimed task, or None if already
    claimed / missing / not pending.
    """
    qp = queue_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict):
                continue
            if row.get("branch_task_id") != task_id:
                continue
            if row.get("status") != "pending":
                return None
            heartbeat_at, lease_expires_at = _lease_window()
            row["status"] = "running"
            row["claimed_by"] = claimer
            row["worker_owner_id"] = claimer
            row["executor_worker_id"] = executor_worker_id or ""
            row["executor_runtime_id"] = executor_runtime_id or ""
            row["heartbeat_at"] = heartbeat_at
            row["lease_expires_at"] = lease_expires_at
            _write_raw(qp, raw)
            return BranchTask.from_dict(row)
    return None


def refresh_task_heartbeat(
    universe_path: Path,
    task_id: str,
    *,
    worker_owner_id: str = "",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> BranchTask | None:
    """Refresh active lease metadata for a running task.

    Phase A is write-only observability: this does not reclaim or
    transition rows. The optional owner guard prevents a stale daemon
    from rewriting another worker's lease if identity is available.
    """
    qp = queue_path(universe_path)
    if not qp.exists():
        return None
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict):
                continue
            if row.get("branch_task_id") != task_id:
                continue
            if row.get("status") != "running":
                return None
            existing_owner = str(
                row.get("worker_owner_id") or row.get("claimed_by") or ""
            )
            if (
                worker_owner_id
                and existing_owner
                and existing_owner != worker_owner_id
            ):
                return None
            heartbeat_at, lease_expires_at = _lease_window(
                lease_seconds=lease_seconds
            )
            if worker_owner_id and not row.get("worker_owner_id"):
                row["worker_owner_id"] = worker_owner_id
            row["heartbeat_at"] = heartbeat_at
            row["lease_expires_at"] = lease_expires_at
            _write_raw(qp, raw)
            return BranchTask.from_dict(row)
    return None


def mark_task_progress(
    universe_path: Path,
    task_id: str,
    *,
    progress_at: str | None = None,
) -> BranchTask | None:
    """Stamp the most recent node-status progress for a running task."""
    qp = queue_path(universe_path)
    if not qp.exists():
        return None
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict):
                continue
            if row.get("branch_task_id") != task_id:
                continue
            if row.get("status") != "running":
                return None
            row["last_progress_at"] = progress_at or _now_iso()
            _write_raw(qp, raw)
            return BranchTask.from_dict(row)
    return None


def mark_status(
    universe_path: Path,
    task_id: str,
    *,
    status: str,
    error: str = "",
) -> None:
    """File-locked status update. Raises on invalid transition."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    qp = queue_path(universe_path)
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict):
                continue
            if row.get("branch_task_id") != task_id:
                continue
            current = row.get("status", "pending")
            if current in TERMINAL_STATUSES:
                # Idempotent finalize: a peer worker or a lease reclaim
                # already resolved this task. A duplicate finalize must
                # never crash the daemon or flip a terminal result
                # (first-writer-wins); it is a no-op. This is the
                # defence-in-depth half of the 2026-06-25 loop-wedge fix
                # (the cure is lease-aware startup recovery, which stops
                # the double-claim that produces these duplicates).
                if status != current:
                    logger.warning(
                        "mark_status: ignoring duplicate finalize "
                        "%s -> %s for task %s (already terminal; "
                        "keeping first result)",
                        current, status, task_id,
                    )
                return
            if status not in _VALID_TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"Invalid transition {current} -> {status} "
                    f"for task {task_id}"
                )
            row["status"] = status
            if status in TERMINAL_STATUSES:
                row["terminal_at"] = _now_iso()
            if error:
                row["error"] = error
            _write_raw(qp, raw)
            return
        raise KeyError(f"Task {task_id} not found")


def request_task_cancel(universe_path: Path, task_id: str) -> bool:
    """Set ``cancel_requested=True`` on a task row.

    Cooperative-cancel flag paralleling ``runs.request_cancel``. The
    graph's stream loop polls ``is_task_cancel_requested`` and breaks
    at the next inter-node event; the daemon then finalizes the
    claimed task as ``cancelled`` rather than ``failed``.

    Returns True if the row was updated, False if the task is not
    present or is already in a terminal state (setting the flag on
    a terminal task is a no-op so stale MCP calls can't resurrect
    it).
    """
    qp = queue_path(universe_path)
    if not qp.exists():
        return False
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict):
                continue
            if row.get("branch_task_id") != task_id:
                continue
            if row.get("status") in TERMINAL_STATUSES:
                return False
            if row.get("cancel_requested"):
                return True  # idempotent
            row["cancel_requested"] = True
            _write_raw(qp, raw)
            return True
    return False


def is_task_cancel_requested(universe_path: Path, task_id: str) -> bool:
    """Return True if a cooperative-cancel has been requested.

    Read-only helper — does not mutate the queue. Safe to call in a
    hot stream loop. Missing task or missing flag → False.
    """
    qp = queue_path(universe_path)
    if not qp.exists():
        return False
    with _file_lock(universe_path):
        raw = _read_raw(qp)
    for row in raw:
        if not isinstance(row, dict):
            continue
        if row.get("branch_task_id") != task_id:
            continue
        return bool(row.get("cancel_requested", False))
    return False


def recover_claimed_tasks(universe_path: Path) -> int:
    """Restart recovery: reset any ``running`` rows to ``pending``.

    Claimed-but-unfinished tasks at daemon startup can't know whether
    the previous daemon finished them; safest is to re-queue. Returns
    the count reset.
    """
    qp = queue_path(universe_path)
    if not qp.exists():
        return 0
    count = 0
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if isinstance(row, dict) and row.get("status") == "running":
                row["status"] = "pending"
                row["claimed_by"] = ""
                row["worker_owner_id"] = ""
                row["lease_expires_at"] = ""
                row["heartbeat_at"] = ""
                count += 1
        if count:
            _write_raw(qp, raw)
    if count:
        logger.info(
            "branch_tasks recovery: reset %d running->pending in %s",
            count, universe_path,
        )
    return count


def reclaim_predecessor_tasks(
    universe_path: Path,
    *,
    worker_id: str,
    target_recovery_guard: Callable[[Mapping[str, object]], bool] | None = None,
) -> int:
    """Startup-only: reset ``running`` rows claimed by a PRIOR incarnation of
    *this same* ``worker_id`` back to ``pending``. Returns the count.

    A ``worker_id`` (``TINYASSETS_WORKER_ID`` — e.g. ``claude-1``/``codex-2`` in
    the compose fleet) belongs to exactly one live worker process at a time, and
    at THIS worker's startup it has not claimed anything yet. So any ``running``
    task carrying our own ``executor_worker_id`` must be an orphan our previous
    incarnation left behind — typically a redeploy that SIGKILLed the old child
    mid-node, stranding a still-valid lease for the full ~30min TTL (the
    redeploy-churn / lease≫heartbeat gap). Clearing it at startup recovers in
    seconds instead of waiting out the TTL.

    Why this is safe where the blanket :func:`recover_claimed_tasks` was not:
    that reset EVERY ``running`` row and so stole *live peers'* tasks on every
    restart, causing the 2026-06-25 double-claim / Invalid-transition wedge.
    This touches ONLY rows whose ``executor_worker_id`` equals our own id — a
    live peer has a different id and is never affected; our own previous
    incarnation is provably dead (we are its container replacement). No heartbeat
    or lease inference is needed.

    No-op when ``worker_id`` is blank: a blank id can't be scoped to "ours"
    without risking a peer's lease, so the lease TTL remains the fallback. The
    CALLER must pass a uniquely-assigned id — a shared fallback id (e.g. the
    ``cloud-droplet`` host-user default) could be held by multiple live
    supervisors, so the caller excludes it (see ``_dispatcher_startup``).
    """
    clean = (worker_id or "").strip()
    if not clean:
        return 0
    qp = queue_path(universe_path)
    if not qp.exists():
        return 0
    if target_recovery_guard is not None:
        return _guarded_reclaim(
            universe_path,
            eligible=lambda row: (
                row.get("status") == "running"
                and str(row.get("executor_worker_id") or "").strip() == clean
            ),
            target_recovery_guard=target_recovery_guard,
        )
    count = 0
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict) or row.get("status") != "running":
                continue
            if str(row.get("executor_worker_id") or "").strip() != clean:
                continue
            logger.warning(
                "branch_tasks reclaim: predecessor orphan %s held by our own "
                "worker_id=%s (prior incarnation, lease_expires_at=%s) — "
                "resetting to pending",
                row.get("branch_task_id"),
                clean,
                row.get("lease_expires_at"),
            )
            row["status"] = "pending"
            row["claimed_by"] = ""
            row["worker_owner_id"] = ""
            row["lease_expires_at"] = ""
            row["heartbeat_at"] = ""
            count += 1
        if count:
            _write_raw(qp, raw)
    return count


def reclaim_expired_leases(
    universe_path: Path,
    *,
    now: datetime | None = None,
    reclaim_leaseless: bool = False,
    target_recovery_guard: Callable[[Mapping[str, object]], bool] | None = None,
) -> int:
    """Lease-aware reclaim: reset ``running`` rows whose lease expired.

    BUG-011 Phase C / daemon-liveness-watchdog spec: ``claim_task``
    stamps a lease window and the runner refreshes it via
    ``refresh_task_heartbeat`` while alive — so a ``running`` row with
    an expired lease means its worker wedged or died mid-task. Unlike
    :func:`recover_claimed_tasks` (startup-only blanket reset), this is
    safe to call while OTHER workers are healthy: live claims keep
    their leases fresh and are never touched. Intended call site is the
    dispatcher claim path, so every claim attempt sweeps first and a
    wedged claim is reaped on the next pick instead of the next daemon
    restart. Returns the count reclaimed.

    ``reclaim_leaseless`` (startup-only): also reset ``running`` rows that
    carry NO lease / an unparsable one. Since ``claim_task`` always stamps a
    lease, a lease-less running row can only be a pre-lease-era or corrupt
    orphan — never a live peer — so it is safe to reclaim. Without this a
    lease-less row would stay ``running`` forever once startup stopped using
    the blanket :func:`recover_claimed_tasks` (Codex cross-family review,
    2026-06-25).

    A redeploy that SIGKILLs a worker mid-node strands a still-valid lease for
    the full TTL; that orphan is recovered in seconds at the replacement
    worker's startup by :func:`reclaim_predecessor_tasks`, so this lease-only
    sweep deliberately never tries to infer worker liveness from heartbeats
    (which would risk false-reaping a healthy long-node peer).
    """
    current = now or datetime.now(timezone.utc)
    qp = queue_path(universe_path)
    if not qp.exists():
        return 0
    if target_recovery_guard is not None:
        def eligible(row: Mapping[str, object]) -> bool:
            if row.get("status") != "running":
                return False
            lease_raw = str(row.get("lease_expires_at") or "")
            lease_at = _parse_iso_utc(lease_raw) if lease_raw else None
            if lease_at is not None:
                return lease_at <= current
            return reclaim_leaseless

        return _guarded_reclaim(
            universe_path,
            eligible=eligible,
            target_recovery_guard=target_recovery_guard,
        )
    count = 0
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict) or row.get("status") != "running":
                continue
            lease_raw = str(row.get("lease_expires_at") or "")
            lease_at = _parse_iso_utc(lease_raw) if lease_raw else None
            if lease_at is not None and lease_at > current:
                continue  # live lease — never touch a healthy peer
            if lease_at is None and not reclaim_leaseless:
                # No / unparsable lease. A current-code worker always stamps a
                # valid one, so this is a pre-lease-era or corrupt orphan, but
                # only the startup sweep reclaims it (no risk of racing a peer).
                continue
            expired_for = (current - lease_at) if lease_at is not None else "no-lease"
            logger.warning(
                "branch_tasks reclaim: lease expired %s on %s "
                "(claimed_by=%s heartbeat_at=%s) — resetting to pending",
                expired_for,
                row.get("branch_task_id"),
                row.get("claimed_by"),
                row.get("heartbeat_at"),
            )
            row["status"] = "pending"
            row["claimed_by"] = ""
            row["worker_owner_id"] = ""
            row["lease_expires_at"] = ""
            row["heartbeat_at"] = ""
            count += 1
        if count:
            _write_raw(qp, raw)
    return count


def _guarded_reclaim(
    universe_path: Path,
    *,
    eligible: Callable[[Mapping[str, object]], bool],
    target_recovery_guard: Callable[[Mapping[str, object]], bool],
) -> int:
    """Reconcile target authority outside the queue lock, then exact-CAS rows."""
    qp = queue_path(universe_path)
    with _file_lock(universe_path):
        snapshots = [
            dict(row)
            for row in _read_raw(qp)
            if isinstance(row, dict) and eligible(row)
        ]
    approved = [
        snapshot
        for snapshot in snapshots
        if target_recovery_guard(snapshot)
    ]
    if not approved:
        return 0
    count = 0
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        for row in raw:
            if not isinstance(row, dict) or row not in approved:
                continue
            row["status"] = "pending"
            row["claimed_by"] = ""
            row["worker_owner_id"] = ""
            row["lease_expires_at"] = ""
            row["heartbeat_at"] = ""
            count += 1
        if count:
            _write_raw(qp, raw)
    return count


def _parse_iso_utc(value: str) -> datetime | None:
    """Parse an ISO timestamp; assume UTC when naive. None on failure."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def garbage_collect(
    universe_path: Path,
    *,
    archive_after_days: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Move old terminal tasks to archive.

    Pending/running tasks are never archived regardless of age.
    Constant ``archive_after_days`` exposed for test override. Returns
    ``{"archived": int, "kept": int}``.
    """
    days = (
        archive_after_days
        if archive_after_days is not None
        else ARCHIVE_AFTER_DAYS
    )
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    qp = queue_path(universe_path)
    ap = archive_path(universe_path)
    if not qp.exists():
        return {"archived": 0, "kept": 0}
    archived = 0
    with _file_lock(universe_path):
        raw = _read_raw(qp)
        keep: list[dict] = []
        to_archive: list[dict] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            status = row.get("status", "")
            queued_at = row.get("queued_at", "")
            if status in TERMINAL_STATUSES and queued_at:
                try:
                    qt = datetime.fromisoformat(queued_at)
                except ValueError:
                    keep.append(row)
                    continue
                if qt.tzinfo is None:
                    qt = qt.replace(tzinfo=timezone.utc)
                if qt < cutoff:
                    to_archive.append(row)
                    continue
            keep.append(row)
        if to_archive:
            # The archive is lifetime-lineage admission truth. Corruption must
            # fail closed rather than silently resetting lineage budgets.
            existing_archive = _read_raw(ap)
            archived_ids = {
                str(row.get("branch_task_id", "") or "").strip()
                for row in existing_archive
                if isinstance(row, dict)
                and str(row.get("branch_task_id", "") or "").strip()
            }
            merged_archive = list(existing_archive)
            for row in to_archive:
                task_id = str(row.get("branch_task_id", "") or "").strip()
                if task_id and task_id in archived_ids:
                    continue
                merged_archive.append(row)
                if task_id:
                    archived_ids.add(task_id)
            _write_raw(ap, merged_archive)
            _write_raw(qp, keep)
            archived = len(to_archive)
    if archived:
        logger.info(
            "branch_tasks GC: archived %d terminal tasks from %s",
            archived, universe_path,
        )
    return {"archived": archived, "kept": len(raw) - archived}


def new_task_id() -> str:
    """Generate a unique branch_task_id. ULID-like (time + random hex)."""
    return f"bt_{int(time.time() * 1000):013d}_{os.urandom(4).hex()}"
