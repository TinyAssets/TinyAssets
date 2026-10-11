# ruff: noqa: E501 - legacy section-rule comments exceed the line limit.
"""Run orchestration for community-designed branches.

Stores run metadata and per-step events in ``<base>/.runs.db`` so Phase 4
can judge, diff, and iterate on run output. Runs are synchronous in v1
by design (see task #39 for the async follow-up) — a single
``start_run`` call compiles, invokes, and persists the final state before
returning. That makes reasoning about cancel/thread-isolation trivial:
one run per tool call, no background tasks to babysit.

DB layout:

- ``runs``   — one row per run: id, branch_def_id, status, thread_id,
               inputs_json, output_json, started_at, finished_at, error.
- ``events`` — one row per node step: run_id, step_index, node_id,
               status, started_at, finished_at, detail_json.

Concurrency-safe across processes via WAL. No long-held connection —
each operation opens, commits, closes.
"""

from __future__ import annotations

import ast
import atexit
import contextlib
import contextvars
import copy
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from tinyassets.branches import BranchDefinition
from tinyassets.graph_compiler import (
    BranchExecutionContext,
    CompilerError,
    EmptyResponseError,
    NodeEnqueueContext,
    NodeTimeoutError,
    _placeholder_keys,
    compile_branch,
    seed_initial_state,
)
from tinyassets.principals import has_named_principal, named_principal

logger = logging.getLogger(__name__)


RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_INTERRUPTED = "interrupted"

#: The statuses a run never leaves. ONE definition: it used to be written
#: twice in this module (once as string literals, once from the constants),
#: and the later binding silently won. A long-poll ends on these, a sweep
#: skips them, and a status write onto one of them is a terminal transition.
_TERMINAL_STATUSES = frozenset({
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_INTERRUPTED,
})

#: When this process could first have created a run: every run it creates
#: starts after this. A recovery sweep uses it so a process never interrupts a
#: run it is executing itself.
PROCESS_STARTED_AT = time.time()
RUN_STATUS_RESUMED = "resumed"

NODE_STATUS_PENDING = "pending"
NODE_STATUS_RUNNING = "running"
NODE_STATUS_RAN = "ran"
NODE_STATUS_FAILED = "failed"
NODE_STATUS_CANCELLED = "cancelled"

#: ``node_id`` for run events that record something about the RUN, not a node
#: (``recursion_limit_applied``, ``provider_calls``, ``concurrency_stats``,
#: ``effect``). Not a node: it carries no node status and is never rendered as
#: one. The literal used to be spelled at every emitter and every reader; the
#: one reader that did not spell it put it in run summaries as a node.
SYSTEM_EVENT_NODE_ID = "__system__"


class RunCancelledError(Exception):
    """Raised from an event_sink when a run has been cancelled so the
    graph invocation unwinds cleanly. Caught by the executor and
    reported as ``status=cancelled``."""


class RunExecutionAuthorityLost(RuntimeError):
    """Raised when a queue-backed run can no longer prove its lease authority."""


class BranchTaskRunReservationConflict(RuntimeError):
    """Raised when a queue BranchTask already owns a durable run reservation."""


def runs_db_path(base_path: str | Path) -> Path:
    return Path(base_path) / ".runs.db"


@contextlib.contextmanager
def _connect(base_path: str | Path) -> sqlite3.Connection:
    db = runs_db_path(base_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> float:
    return time.time()


def _resolve_owner_user_id(
    base_path: str | Path,
    daemon_id: str | None,
) -> str:
    clean_daemon_id = str(daemon_id or "").strip()
    if not clean_daemon_id:
        return ""
    try:
        from tinyassets.daemon_registry import get_daemon

        daemon = get_daemon(base_path, daemon_id=clean_daemon_id)
    except Exception:
        return ""
    return str(daemon.get("owner_user_id") or "")


def _prepared_run_recovery_exclusion(conn: sqlite3.Connection) -> str:
    """Same-store SQL predicate; legacy absence is not a schema-init request.

    A prepared row is owned by the common guarded start/recovery protocol even
    before family initialization. No local Future or age proves its worker dead.
    Malformed/present schemas fail loudly rather than permitting retirement.
    """
    schema = conn.execute(
        "SELECT type FROM sqlite_master WHERE name='run_input_admissions'",
    ).fetchone()
    if schema is None:
        return ""
    if schema[0] != "table":
        raise RuntimeError("invalid run-input admission schema")
    return (
        " AND NOT EXISTS (SELECT 1 FROM run_input_admissions admission "
        "WHERE admission.run_id=runs.run_id)"
    )


# ---------------------------------------------------------------------------
# Workspace storage: the terminal outbox (OpenSpec change workspace-node, D0).
# A run that held a scratch lease or a workspace job lock owes work when it
# ends: the lease is wiped and the locks released THROUGH an outbox entry
# written in the same transaction as the terminal status, never by a direct
# delete - so a crash at any point leaves a row that the sweeper finishes.
# ---------------------------------------------------------------------------

_WORKSPACE_RECONCILE_LOCK = threading.Lock()
#: (pid, resolved data root) pairs whose startup sweep SUCCEEDED. Keyed by pid
#: so a forked worker never inherits the parent's claim; reset after fork as
#: well (Codex, code round 1).
_WORKSPACE_RECONCILED: set[tuple[int, str]] = set()
_WORKSPACE_RECONCILING: set[tuple[int, str]] = set()
_WORKSPACE_STOP_REQUESTED: set[tuple[int, str]] = set()


@dataclass(frozen=True)
class _WorkspaceSweeperHandle:
    """The owned lifecycle of one periodic workspace sweeper."""

    stop_event: threading.Event
    thread: threading.Thread


_WORKSPACE_SWEEPERS: dict[tuple[int, str], _WorkspaceSweeperHandle] = {}
# Capture the deadline clock for the same reason periodic workers capture their
# sweep callable: another module mutating the shared ``time`` object must not
# redirect an already-owned lifecycle operation.
_WORKSPACE_SWEEPER_MONOTONIC = time.monotonic


def _reset_workspace_reconciliation_after_fork() -> None:
    global _WORKSPACE_RECONCILE_LOCK

    # The child inherits the mutex state but not the thread that may have held
    # it at fork time. Give the single surviving thread a fresh lock.
    _WORKSPACE_RECONCILE_LOCK = threading.Lock()
    _WORKSPACE_RECONCILED.clear()
    _WORKSPACE_RECONCILING.clear()
    _WORKSPACE_STOP_REQUESTED.clear()
    # A fork retains Python objects but not the parent's other threads. Never
    # mistake those dead handles for sweepers owned by the child.
    _WORKSPACE_SWEEPERS.clear()


if hasattr(os, "register_at_fork"):  # pragma: no branch - POSIX only
    os.register_at_fork(after_in_child=_reset_workspace_reconciliation_after_fork)
_WORKSPACE_SWEEP_INTERVAL_S = 30.0
#: Every workspace failure class the executor classifies (design D6): one
#: actionable class per refusal, all the universe's to act on.
WORKSPACE_FAILURE_KINDS: tuple[str, ...] = (
    "workspace_checkout_failed",
    "workspace_push_refused",
    "workspace_busy",
    "workspace_pool_busy",
    "workspace_quota_exceeded",
    "workspace_command_timeout",
    "workspace_provision_refused",
    "workspace_provision_failed",
    "workspace_discard_failed",
)


def _enqueue_workspace_terminal(
    conn: sqlite3.Connection, base_path: str | Path, run_id: str,
) -> int:
    """Inside the caller's terminal transaction: enqueue the release of every
    ACTIVE lease this run holds, or a lock-only release when it holds a job
    lock without a lease. Returns the number of entries written."""
    from tinyassets import workspace_pool

    workspace_pool.ensure_schema(conn)
    # Read through the CALLER's connection: this runs inside its open write
    # transaction, and a second connection taking BEGIN IMMEDIATE (as
    # get_lease does) would wait on ourselves until busy_timeout.
    rows = conn.execute(
        f"SELECT {workspace_pool._LEASE_COLUMNS} FROM workspace_leases "
        "WHERE run_id = ? AND state = 'ACTIVE'",
        (run_id,),
    ).fetchall()
    # A finished run leaves the workspace queue in its terminal transaction.
    # Counted as owed so the caller's kick nominates the next waiter.
    dequeued = workspace_pool.remove_waiter_in_transaction(conn, run_id)
    written = 0
    for row in rows:
        lease = workspace_pool._lease_from_row(row)
        workspace_pool.enqueue_terminal(
            conn, run_id=run_id, universe_id=lease.universe_id, lease=lease,
            storage_class=lease.storage_class,
        )
        written += 1
    if written:
        return written + dequeued
    locks = conn.execute(
        "SELECT key FROM workspace_locks WHERE scope = 'universe' AND run_id = ?",
        (run_id,),
    ).fetchall()
    for row in locks:
        workspace_pool.enqueue_terminal(
            conn, run_id=run_id, universe_id=row[0], lease=None,
        )
        written += 1
    return written + dequeued


def _workspace_terminal_base(
    conn: sqlite3.Connection,
    base_path: str | Path,
    run_id: str,
) -> Path | None:
    """Return the separate universe DB that owns *run_id*'s workspace state.

    Run rows are authoritative at the data root, while the workspace adapter
    deliberately contains leases, locks, and outbox rows inside the running
    universe. The terminal transition therefore has to name that second DB
    explicitly; looking for its rows through the root connection is the bug
    that left every completed workspace run holding both locks.

    ``queue_universe_id`` is server-written execution context. Older rows may
    only carry the same identity in ``actor``. Both routes still pass the
    canonical universe-id grammar before becoming a path, and an absent DB is
    not created as a side effect of a terminal transition.
    """
    try:
        row = conn.execute(
            "SELECT queue_universe_id, actor FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # Compatibility for narrow legacy/test schemas with no queue column.
        row = conn.execute(
            "SELECT actor FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    keys = set(row.keys())
    universe_id = (
        str(row["queue_universe_id"] or "").strip()
        if "queue_universe_id" in keys
        else ""
    )
    if not universe_id:
        actor = str(row["actor"] or "").strip() if "actor" in keys else ""
        if actor.startswith("universe:"):
            universe_id = actor.removeprefix("universe:").strip()
    from tinyassets.ids import is_universe_serial

    if not is_universe_serial(universe_id):
        return None
    root = Path(base_path).resolve()
    if root.name == universe_id:
        return None
    universe_base = (root / universe_id).resolve()
    if universe_base.parent != root or not runs_db_path(universe_base).is_file():
        return None
    return universe_base


def _finish_terminal_workspace_release(
    base_path: str | Path,
    run_id: str,
    workspace_base: Path | None,
    *,
    local_owed: int = 0,
) -> None:
    """Finish release work after the terminal-status transaction committed.

    A separate universe database is necessarily a second WAL transaction. Its
    failure must not escape to a caller that will reclassify the already-
    committed run, but it also must not disappear: log it and kick the exact
    universe sweep, whose root-status repair path recreates the owed entry.
    """
    if local_owed:
        _kick_workspace_sweep(base_path)
    if workspace_base is None:
        return
    try:
        with _connect(workspace_base) as workspace_conn:
            workspace_owed = _enqueue_workspace_terminal(
                workspace_conn, workspace_base, run_id
            )
    except Exception:
        logger.exception(
            "workspace terminal release enqueue failed for run %s in %s; "
            "the command center sweep will repair it",
            run_id,
            workspace_base,
        )
        # Even a young terminal lock is repairable: the sweep reads the root
        # status, so it does not rely on the one-hour unknown-run age bound.
        _kick_workspace_sweep(workspace_base)
        return
    if workspace_owed:
        _kick_workspace_sweep(workspace_base)


# ---------------------------------------------------------------------------
# Durable workspace waiting (OpenSpec change durable-workspace-wait).
#
# A root run that needs its universe's workspace takes a ticket at admission
# and does not START until the ticket is first in line and nobody else holds
# the workspace. A run that has executed no node cannot have produced an
# effect, so a restart that finds it still waiting can let it keep waiting and
# start it later without replaying anything. Waiting runs hold no executor
# worker: they are dispatched by nomination, from their durable admission.
# ---------------------------------------------------------------------------

_WORKSPACE_EFFECT = "workspace"
_WAITER_DISPATCH_LOCK = threading.Lock()
#: ``(resolved root runs db, run_id)`` claimed for dispatch in THIS process.
#: A run is dispatched once per process; a restart starts with none claimed,
#: which is exactly when a never-started waiter must be dispatched again.
_WAITER_DISPATCHED: set[tuple[str, str]] = set()


def _reset_waiter_dispatch_after_fork() -> None:
    global _WAITER_DISPATCH_LOCK
    _WAITER_DISPATCH_LOCK = threading.Lock()
    _WAITER_DISPATCHED.clear()


if hasattr(os, "register_at_fork"):  # pragma: no branch - POSIX only
    os.register_at_fork(after_in_child=_reset_waiter_dispatch_after_fork)


def _branch_needs_workspace(branch: BranchDefinition) -> bool:
    return any(
        _WORKSPACE_EFFECT in (getattr(node, "effects", None) or [])
        for node in (getattr(branch, "node_defs", None) or [])
    )


def _workspace_wait_base(base_path: str | Path, universe_id: str) -> Path | None:
    """The universe directory whose workspace database queues this run.

    Only a canonical universe id: the same rule the terminal release path
    uses, so every ticket written here is one a terminal transition removes.
    """
    from tinyassets.ids import is_universe_serial

    uid = (universe_id or "").strip()
    if not is_universe_serial(uid):
        return None
    universe_base = Path(base_path) / uid
    if not universe_base.is_dir():
        return None
    return universe_base


def _waiter_root(universe_base: str | Path) -> Path | None:
    """The data root whose runs database records a universe's runs."""
    from tinyassets.ids import is_universe_serial

    universe_base = Path(universe_base)
    if not is_universe_serial(universe_base.name):
        return None
    root = universe_base.parent
    return root if runs_db_path(root).is_file() else None


def _queues_for_workspace(
    branch: BranchDefinition,
    *,
    owner_user_id: str | None,
    universe_id: str,
    invocation_depth: int,
    workspace_parent,
) -> bool:
    """Root runs of an authenticated owner in a universe, needing a workspace.

    Children share their parent's lock, so queueing one behind its own
    parent's reservation would deadlock it.
    """
    return bool(
        owner_user_id and universe_id and invocation_depth == 0
        and workspace_parent is None and _branch_needs_workspace(branch)
    )


def _waiter_key(base_path: str | Path, run_id: str) -> tuple[str, str]:
    return (str(runs_db_path(base_path).resolve()), run_id)


def _run_start_state(base_path: str | Path, run_id: str) -> tuple[str | None, bool]:
    """``(status, cancel_requested)`` straight off the run row, no side effects."""
    with _connect(base_path) as conn:
        row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        cancelled = conn.execute(
            "SELECT 1 FROM run_cancels WHERE run_id = ?", (run_id,),
        ).fetchone() is not None
    return (None if row is None else str(row[0])), cancelled


def _claim_waiter_dispatch(base_path: str | Path, run_id: str) -> bool:
    """Claim the one dispatch a queued, uncancelled waiter gets in this process.

    Shared by the admitting request and every nomination, so the two can race
    without starting the run twice. A cancel recorded before the claim wins;
    one recorded after it is observed by the worker at start.
    """
    key = _waiter_key(base_path, run_id)
    with _WAITER_DISPATCH_LOCK:
        if key in _WAITER_DISPATCHED:
            return False
        status, cancelled = _run_start_state(base_path, run_id)
        if status != RUN_STATUS_QUEUED or cancelled:
            return False
        _WAITER_DISPATCHED.add(key)
        return True


def _waiter_dispatch_claimed(base_path: str | Path, run_id: str) -> bool:
    with _WAITER_DISPATCH_LOCK:
        return _waiter_key(base_path, run_id) in _WAITER_DISPATCHED


def _settle_waiting_run(base_path: str | Path, run_id: str, *, status: str, error: str) -> str:
    """Settle a run that never started. Only a still-``queued`` row changes."""
    try:
        update_run_status(
            base_path, run_id, status=status, error=error, finished_at=_now(),
            _expected_statuses={RUN_STATUS_QUEUED},
        )
    except RunExecutionAuthorityLost:
        current, _ = _run_start_state(base_path, run_id)
        return current or status
    return status


def workspace_wait_state(base_path: str | Path, run: dict[str, Any]) -> dict[str, Any] | None:
    """The visible wait of a queued run, or None when it is not waiting."""
    from tinyassets import workspace_pool

    if run.get("status") != RUN_STATUS_QUEUED:
        return None
    universe_base = _workspace_wait_base(base_path, str(run.get("queue_universe_id") or ""))
    if universe_base is None:
        return None
    try:
        ticket = workspace_pool.wait_ticket(runs_db_path(universe_base), str(run["run_id"]))
    except sqlite3.Error:
        logger.exception("workspace wait read failed for run %s", run.get("run_id"))
        return None
    return None if ticket is None else ticket.public()


def _never_started_waiters(base_path: str | Path, rows) -> dict[str, Path]:
    """``{run_id: universe_base}`` for queued rows holding a wait ticket.

    Such a run has executed no node, so recovery must leave it waiting rather
    than interrupt it. A row that ever reached ``running`` is not in here.
    """
    from tinyassets import workspace_pool

    waiting: dict[str, Path] = {}
    for row in rows:
        if row["status"] != RUN_STATUS_QUEUED:
            continue
        universe_base = _workspace_wait_base(base_path, str(row["queue_universe_id"] or ""))
        if universe_base is None:
            continue
        try:
            ticket = workspace_pool.wait_ticket(runs_db_path(universe_base), row["run_id"])
        except sqlite3.Error:
            # Unreadable is not "not waiting": leaving the row alone is the
            # recoverable choice; interrupting it would lose it.
            logger.exception("workspace wait read failed for run %s", row["run_id"])
            waiting[row["run_id"]] = universe_base
            continue
        if ticket is not None:
            waiting[row["run_id"]] = universe_base
    return waiting


def _kick_workspace_waiters(universe_base: str | Path) -> threading.Thread:
    """Start the universe's sweeper (the periodic backstop) and nominate now."""

    def body() -> None:
        try:
            ensure_workspace_reconciled(universe_base)
        except Exception:  # noqa: BLE001 - nomination below still runs
            logger.exception("workspace reconciliation before nomination failed")
        try:
            nominate_workspace_waiter(universe_base)
        except Exception:  # noqa: BLE001 - the periodic sweep retries
            logger.exception("workspace waiter nomination failed")

    thread = threading.Thread(target=body, name="workspace-waiters-kick", daemon=True)
    thread.start()
    return thread


def nominate_workspace_waiter(universe_base: str | Path) -> str | None:
    """Dispatch the first waiting run when the workspace is free for it.

    Returns the dispatched run id, or None. A head whose run is gone or
    terminal is removed (a dead head must never wedge the queue); a head that
    already started keeps its reservation until it checks out or ends.
    """
    from tinyassets import workspace_pool

    root = _waiter_root(universe_base)
    if root is None:
        return None
    db = runs_db_path(universe_base)
    for universe_id in workspace_pool.waiting_universes(db):
        while True:
            head = workspace_pool.head_waiter(db, universe_id)
            if head is None:
                break
            ticket, free = head
            status, cancelled = _run_start_state(root, ticket.run_id)
            if status is None or status in _TERMINAL_STATUSES:
                workspace_pool.remove_waiter(db, ticket.run_id)
                continue
            if status != RUN_STATUS_QUEUED:
                break
            if cancelled and not _waiter_dispatch_claimed(root, ticket.run_id):
                _settle_waiting_run(
                    root, ticket.run_id, status=RUN_STATUS_CANCELLED,
                    error="Cancelled while waiting for the command center workspace.",
                )
                workspace_pool.remove_waiter(db, ticket.run_id)
                continue
            if not free or not _claim_waiter_dispatch(root, ticket.run_id):
                break
            try:
                workspace_pool.mark_waiter_dispatched(db, ticket.run_id)
            except Exception:  # noqa: BLE001 - an unmarked head only stays reserved longer
                logger.exception("could not mark waiting run %s dispatched", ticket.run_id)
            _dispatch_waiting_run(root, ticket.run_id)
            return ticket.run_id
    return None


def _bind_waiting_run_provider(
    base_path: str | Path,
    *,
    run_id: str,
    universe_id: str,
    owner_id: str,
    branch: BranchDefinition,
    branch_version_id: str | None,
):
    """Fresh foreground provider authority for the owner, bound at START.

    The same binding the admitted-input path uses: the owner must still exist,
    and the session's own admission checks the run is still ``queued``.
    """
    from tinyassets.config import load_universe_config
    from tinyassets.foreground_run_provider import new_foreground_run_provider_session
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.call import bind_universe_provider_call, call_provider
    from tinyassets.storage import _connect as author_connect
    from tinyassets.storage.current_home import check_principal_not_deleted

    with author_connect(base_path) as authority:
        # The deletion fence is transaction-local by contract; the admitted-input
        # path reads it under BEGIN IMMEDIATE, and so must this one. Without it
        # every nominated waiter failed ``provider_unavailable`` (live
        # 2026-09-24, founder universe, the second of two contending runs).
        authority.execute("BEGIN IMMEDIATE")
        check_principal_not_deleted(authority, owner_id)
    session = new_foreground_run_provider_session(
        base_path, universe_id=universe_id, principal_id=owner_id,
        provider_call=call_provider,
    )
    session.prepare(run_id=run_id, branch=branch, branch_version_id=branch_version_id,
                    allowed_statuses={RUN_STATUS_QUEUED})
    universe_dir = Path(base_path) / universe_id
    return bind_universe_provider_call(
        session,
        UniverseContext(universe_dir=universe_dir, config=load_universe_config(universe_dir)),
        operation="run_graph",
    )


def _dispatch_waiting_run(base_path: str | Path, run_id: str) -> None:
    """Start a claimed waiter exactly as it was admitted. Never raises.

    Everything comes from durable state: the admission envelope (definition and
    execution choices), the stored inputs, the owner. Any failure settles the
    run ``failed`` with the reason, which also removes it from the queue.
    """
    from tinyassets.run_admission_envelope import (
        AdmissionNotReconstructable,
        resolve_admitted_execution,
    )

    try:
        with _connect(base_path) as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return
        run = _row_to_run(row)
        owner = str(run.get("owner_user_id") or "")
        universe_id = str(run.get("queue_universe_id") or "")
        try:
            admitted = resolve_admitted_execution(base_path, run)
        except AdmissionNotReconstructable as exc:
            _settle_waiting_run(base_path, run_id, status=RUN_STATUS_FAILED,
                                error=f"Could not start after waiting for the workspace: {exc}")
            return
        branch = BranchDefinition.from_dict(admitted.branch.to_dict())
        try:
            provider_call = _bind_waiting_run_provider(
                base_path, run_id=run_id, universe_id=universe_id, owner_id=owner,
                branch=branch, branch_version_id=run.get("branch_version_id") or None,
            )
        except Exception as exc:  # noqa: BLE001 - settled with the reason, never retried silently
            _settle_waiting_run(base_path, run_id, status=RUN_STATUS_FAILED,
                                error=f"Provider authority admission failed: {exc}")
            return
        inputs = run.get("inputs") or {}
        actor = str(run.get("actor") or "")

        def _worker() -> RunOutcome:
            from tinyassets.auth.middleware import identity_context
            from tinyassets.auth.provider import Identity

            with identity_context(Identity(owner, owner)):
                return _invoke_prepared_branch(
                    base_path, run_id=run_id, branch=branch, inputs=inputs,
                    actor=actor, provider_call=provider_call,
                    recursion_limit=admitted.recursion_limit,
                    concurrency_budget_override=admitted.concurrency_budget_override,
                    enqueue_universe_id=universe_id,
                )

        future = _get_executor(
            invocation_depth=0, pool_key=run_pool_key(base_path, universe_id),
        ).submit(contextvars.Context().run, _worker)
        _track_future(run_id, future)
    except Exception as exc:  # noqa: BLE001 - a nomination must never fail its caller
        logger.exception("dispatch of waiting run %s failed", run_id)
        _settle_waiting_run(base_path, run_id, status=RUN_STATUS_FAILED,
                            error=f"Could not start after waiting for the workspace: {exc}")


def _admit_workspace_waiter(
    base_path: str | Path, *, run_id: str, universe_id: str, provider_call,
) -> RunOutcome | None:
    """Queue a freshly admitted run for its universe's workspace.

    Returns None when it is this run's turn now (the caller dispatches it as
    usual, holding the reservation), or the ``queued`` outcome of a run that
    must wait. A waiting run gives back the provider authority it was admitted
    with; its turn binds fresh authority. Failing to queue fails the run
    loudly instead of running it outside the order.
    """
    from tinyassets import workspace_pool

    universe_base = _workspace_wait_base(base_path, universe_id)
    if universe_base is None:
        return None
    db = runs_db_path(universe_base)
    try:
        # The universe database the sweep (and so every hand-off) reads; a
        # first-ever workspace run may be queued before anything created it.
        initialize_runs_db(universe_base)
        workspace_pool.enqueue_waiter(db, run_id=run_id, universe_id=universe_id)
        head = workspace_pool.head_waiter(db, universe_id)
    except Exception as exc:  # noqa: BLE001 - settled with the reason below
        logger.exception("could not queue run %s for its workspace", run_id)
        head = None
        queue_error = f"Could not queue for the command center workspace: {exc}"
    else:
        queue_error = ""
    my_turn = (
        head is not None and head[0].run_id == run_id and head[1]
        and _claim_waiter_dispatch(base_path, run_id)
    )
    if my_turn:
        try:
            workspace_pool.mark_waiter_dispatched(db, run_id)
        except Exception:  # noqa: BLE001 - an unmarked head only stays reserved longer
            logger.exception("could not mark waiting run %s dispatched", run_id)
        return None
    try:
        from tinyassets.foreground_run_provider import close_foreground_run_provider

        close_foreground_run_provider(provider_call)
    except Exception:  # noqa: BLE001 - the claim expires with its lease
        logger.exception("provider claim release failed for waiting run %s", run_id)
    if queue_error:
        settled = _settle_waiting_run(
            base_path, run_id, status=RUN_STATUS_FAILED, error=queue_error,
        )
        return RunOutcome(run_id=run_id, status=settled, output={}, error=queue_error)
    return RunOutcome(run_id=run_id, status=RUN_STATUS_QUEUED, output={}, error="")


def _settle_cancelled_waiter(base_path: str | Path, run_id: str) -> None:
    """A cancel of a run that is only waiting settles it now.

    Nothing else would: a waiting run has no worker to observe the cancel. A
    run already claimed for dispatch is left to its worker, which checks the
    cancel before its first node. The holder is never touched.
    """
    status, cancelled = _run_start_state(base_path, run_id)
    if status != RUN_STATUS_QUEUED or not cancelled:
        return
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT queue_universe_id FROM runs WHERE run_id = ?", (run_id,),
        ).fetchone()
    universe_base = _workspace_wait_base(base_path, str(row[0] or "") if row else "")
    if universe_base is None:
        return
    from tinyassets import workspace_pool

    if workspace_pool.wait_ticket(runs_db_path(universe_base), run_id) is None:
        return
    if _waiter_dispatch_claimed(base_path, run_id):
        return
    _settle_waiting_run(
        base_path, run_id, status=RUN_STATUS_CANCELLED,
        error="Cancelled while waiting for the command center workspace.",
    )


#: How old a lock whose run is unknown to this database must be before the
#: sweep will release it. A lock is written before its run row on a legitimate
#: start, so anything shorter reaps live work during that race. Far above any
#: real checkout; far below the day the founder's stuck lock had reached.
_ORPHAN_LOCK_MIN_AGE_S = 3600.0


def _run_statuses_recorded_at_root(
    base_path: str | Path, run_ids: set[str],
) -> dict[str, str] | None:
    """``{run_id: status}`` for the given runs as the ROOT runs database has
    them -- the database run rows are actually written to.

    Empty when there is nowhere else to look: the sweep is already at the
    root, or no root database exists yet (then no run was ever recorded, and
    the age bound is the right evidence). ``None`` when the root COULD NOT BE
    READ. The two are not the same answer: "not found" lets the age bound
    release an old lock; "could not look" must release nothing, or a busy WAL
    or a transient read error would reap a live run's locks the moment they
    turned an hour old (Codex, review round 1, P2)."""
    if not run_ids:
        return {}
    try:
        from tinyassets.storage import data_dir

        root = Path(data_dir())
        if Path(base_path).resolve() == root.resolve():
            return {}
        db = runs_db_path(root)
        if not db.exists():
            return {}
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
        try:
            placeholders = ",".join("?" for _ in run_ids)
            rows = conn.execute(
                f"SELECT run_id, status FROM runs WHERE run_id IN ({placeholders})",
                tuple(run_ids),
            ).fetchall()
        finally:
            conn.close()
        return {str(row[0]): str(row[1]) for row in rows}
    except Exception:  # noqa: BLE001 - a sweep must never fail on a lookup
        logger.exception(
            "could not read run statuses at the root; releasing no unknown-run "
            "locks this pass"
        )
        return None


def _workspace_sweep_once(base_path: str | Path, *, claimant: str) -> int:
    """One periodic pass: enqueue leases orphaned by a terminal run that never
    reached the outbox (a crash between the two writes of an older code path),
    then process every claimable entry."""
    from tinyassets import workspace_pool
    from tinyassets.workspace_fs import RealPoolFilesystem

    db = runs_db_path(base_path)
    with _connect(base_path) as conn:
        workspace_pool.ensure_schema(conn)
        # Only SCRATCH leases: an authoritative permanent generation stays
        # ACTIVE after its run ends by design (release_lock_only), and must
        # not be re-enqueued every tick (Codex, code round 1).
        orphaned_leases = conn.execute(
            """
            SELECT l.run_id FROM workspace_leases AS l
              JOIN runs AS r ON r.run_id = l.run_id
             WHERE l.state = 'ACTIVE' AND l.storage_class = 'scratch'
               AND r.status IN ('completed', 'failed', 'cancelled', 'interrupted')
               AND NOT EXISTS (
                   SELECT 1 FROM workspace_outbox AS o
                    WHERE o.run_id = l.run_id AND o.done_at IS NULL
               )
            """
        ).fetchall()
        # And locks a finished run still holds with nothing pending to
        # release them - invisible to a lease-first query.
        orphaned_locks = conn.execute(
            """
            SELECT k.run_id FROM workspace_locks AS k
              JOIN runs AS r ON r.run_id = k.run_id
             WHERE r.status IN ('completed', 'failed', 'cancelled', 'interrupted')
               AND NOT EXISTS (
                   SELECT 1 FROM workspace_outbox AS o
                    WHERE o.run_id = k.run_id AND o.done_at IS NULL
               )
            """
        ).fetchall()
        # And locks whose run THIS database has never heard of. The query above
        # is an INNER JOIN, so it silently drops them -- and nothing else will
        # ever release a lock whose run cannot be transitioned here.
        #
        # This is the live topology, not a corner case: the workspace effector
        # keeps locks, leases and the outbox in the UNIVERSE's runs database,
        # while a run's row and its terminal status are written to the ROOT
        # one. So the terminal enqueue (`update_run_status` -> this module's
        # `_enqueue_workspace_terminal`) runs against a database that holds no
        # lock for the run, writes nothing, and every workspace run -- failed
        # or succeeded -- leaves its universe lock and host slot held.
        #
        # Live 2026-09-01: `u-01kxm1vszd8hwp7em418asq8h9` could not check out
        # anything because the lock from a run that had failed 27 hours earlier
        # was still held; that run's row sat at the root with status `failed`,
        # and the universe database's `runs` table was empty.
        #
        # So consult the root, where runs are actually recorded: a run that is
        # terminal there is released NOW (this is what lets one job follow
        # another); a run that is live there keeps its lock however old it is;
        # a run known nowhere is released only past `_ORPHAN_LOCK_MIN_AGE_S`,
        # because a lock is written BEFORE its run row on a normal start.
        unknown_run_locks = conn.execute(
            """
            SELECT k.run_id, k.acquired_at FROM workspace_locks AS k
             WHERE NOT EXISTS (
                   SELECT 1 FROM runs AS r WHERE r.run_id = k.run_id
               )
               AND NOT EXISTS (
                   SELECT 1 FROM workspace_outbox AS o
                    WHERE o.run_id = k.run_id AND o.done_at IS NULL
               )
            """
        ).fetchall()
        releasable: list[str] = []
        recorded = _run_statuses_recorded_at_root(
            base_path, {row[0] for row in unknown_run_locks}
        ) if unknown_run_locks else {}
        # A failed lookup is not "unknown": it releases nothing. The lock
        # waits for a pass that can actually read the root.
        if recorded is not None:
            cutoff = time.time() - _ORPHAN_LOCK_MIN_AGE_S
            for run_id, acquired_at in unknown_run_locks:
                status = recorded.get(run_id)
                if status is None:
                    if float(acquired_at or 0.0) < cutoff:
                        releasable.append(run_id)
                elif status in _TERMINAL_STATUSES:
                    releasable.append(run_id)
                # else: live at the root -- leave it alone.
        orphaned_locks = list(orphaned_locks) + [(r,) for r in releasable]
        for run_id in {r[0] for r in orphaned_leases} | {r[0] for r in orphaned_locks}:
            _enqueue_workspace_terminal(conn, base_path, run_id)
    done = workspace_pool.periodic_sweep(db, fs=RealPoolFilesystem(), claimant=claimant)
    # A pass that released a lock is exactly when the next waiter's turn comes;
    # a pass that released nothing is the backstop for a missed nomination.
    try:
        nominate_workspace_waiter(base_path)
    except Exception:  # noqa: BLE001 - the next pass retries
        logger.exception("workspace waiter nomination failed")
    return done


def _reconcile_push_intents(base_path: str | Path, *, when: str) -> int:
    """Settle every push whose outcome was lost (a crash between the wire and
    the receipt): ``ls-remote`` through the worker decides done/failed
    (workspace-node D1, Codex code round 2 #4). Best-effort on each pass -
    the next sweep retries what this one could not reach - and never
    blocks a startup on the network."""
    try:
        from tinyassets.workspace_worker import reconcile_push_intents

        settled = reconcile_push_intents(base_path)
    except Exception:  # noqa: BLE001 - retried on the next pass
        logger.exception("push intent reconciliation failed (%s)", when)
        return 0
    if settled:
        logger.info("push intents reconciled (%s): %s", when, settled)
    return len(settled)


def _kick_workspace_sweep(base_path: str | Path) -> threading.Thread:
    """Run one sweep pass now, on its own thread: a finished run's lock and
    lease are released within seconds, not at the next periodic tick."""
    thread = threading.Thread(
        target=_workspace_sweep_kick_body, args=(base_path,),
        name="workspace-sweep-kick", daemon=True,
    )
    thread.start()
    return thread


def _workspace_sweep_kick_body(base_path: str | Path) -> None:
    try:
        _workspace_sweep_once(base_path, claimant=f"kick:{os.getpid()}")
    except Exception:  # noqa: BLE001 - the periodic sweeper retries
        logger.exception("workspace sweep kick failed")


def _workspace_sweeper_loop(
    key: tuple[int, str],
    base_path: str | Path,
    interval_s: float,
    stop_event: threading.Event,
    sweep_once: Callable[..., int],
) -> None:
    claimant = f"sweeper:{os.getpid()}"
    try:
        while not stop_event.wait(interval_s):
            try:
                # The worker owns the callable it started with. Looking it up from
                # module globals on every tick lets a later test monkeypatch (or a
                # hot reload) redirect an already-running production worker.
                sweep_once(base_path, claimant=claimant)
            except Exception:  # noqa: BLE001 - the loop must outlive one bad pass
                logger.exception("workspace sweep failed")
    finally:
        # A worker may request its own stop from inside ``sweep_once``. It
        # cannot join itself, so retirement belongs to the loop finalizer.
        current_thread = threading.current_thread()
        with _WORKSPACE_RECONCILE_LOCK:
            handle = _WORKSPACE_SWEEPERS.get(key)
            if handle is not None and handle.thread is current_thread:
                _WORKSPACE_SWEEPERS.pop(key, None)
                _WORKSPACE_RECONCILED.discard(key)


def _start_workspace_sweeper(
    base_path: str | Path,
    interval_s: float,
    sweep_once: Callable[..., int] = _workspace_sweep_once,
) -> _WorkspaceSweeperHandle | None:
    """Start and register one stoppable sweeper for ``base_path``.

    ``sweep_once`` is deliberately captured when this function is defined.
    The periodic worker must not follow later process-global monkeypatches.
    Tests that exercise the loop can inject their own callable directly.
    """
    key = (os.getpid(), str(Path(base_path).resolve()))
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_workspace_sweeper_loop,
        args=(key, base_path, interval_s, stop_event, sweep_once),
        name="workspace-sweeper",
        daemon=True,
    )
    handle = _WorkspaceSweeperHandle(stop_event=stop_event, thread=thread)
    with _WORKSPACE_RECONCILE_LOCK:
        # A stop that arrived during startup owns this transition. Because the
        # check and thread publication share the same lock, stop cannot report
        # success and then have this reconciliation publish a worker.
        if key in _WORKSPACE_STOP_REQUESTED:
            return None
        current = _WORKSPACE_SWEEPERS.get(key)
        if current is not None and current.thread.is_alive():
            return current
        # Start while holding the lifecycle lock, then publish. A concurrent
        # stop therefore sees either no handle or an already-started thread;
        # it can never attempt to join a published-but-unstarted Thread.
        thread.start()
        _WORKSPACE_SWEEPERS[key] = handle
    return handle


def _stop_workspace_sweeper(
    base_path: str | Path,
    *,
    timeout_s: float = 5.0,
) -> bool:
    """Stop and join one periodic sweeper; return whether it has stopped.

    Removing the reconciliation marker makes a later caller rerun the startup
    barrier before starting a replacement worker.
    """
    key = (os.getpid(), str(Path(base_path).resolve()))
    with _WORKSPACE_RECONCILE_LOCK:
        handle = _WORKSPACE_SWEEPERS.get(key)
        if handle is None:
            if key in _WORKSPACE_RECONCILING:
                _WORKSPACE_STOP_REQUESTED.add(key)
            _WORKSPACE_RECONCILED.discard(key)
            return True
    handle.stop_event.set()
    if handle.thread is threading.current_thread():
        return False
    handle.thread.join(timeout=max(0.0, timeout_s))
    stopped = not handle.thread.is_alive()
    if stopped:
        with _WORKSPACE_RECONCILE_LOCK:
            current = _WORKSPACE_SWEEPERS.get(key)
            if current is handle:
                _WORKSPACE_SWEEPERS.pop(key, None)
                _WORKSPACE_RECONCILED.discard(key)
            elif current is None:
                _WORKSPACE_RECONCILED.discard(key)
            # A self-retiring old worker may already have been replaced while
            # this caller joined it. Never clear the replacement's marker.
    return stopped


def _stop_all_workspace_sweepers(*, timeout_s: float = 5.0) -> bool:
    """Stop every owned sweeper within one shared timeout budget."""
    with _WORKSPACE_RECONCILE_LOCK:
        reconciling_keys = {
            key for key in _WORKSPACE_RECONCILING if key[0] == os.getpid()
        }
        # Application teardown uses this all-worker path. Arm the same startup
        # veto as the single-worker stop before taking the handle snapshot, so
        # an in-flight reconciliation cannot publish a worker after cleanup.
        _WORKSPACE_STOP_REQUESTED.update(reconciling_keys)
        handles = [
            (key, handle)
            for key, handle in _WORKSPACE_SWEEPERS.items()
            if key[0] == os.getpid()
        ]
    if not handles:
        return True
    for _key, handle in handles:
        handle.stop_event.set()
    deadline = _WORKSPACE_SWEEPER_MONOTONIC() + max(0.0, timeout_s)
    for _key, handle in handles:
        if handle.thread is not threading.current_thread():
            handle.thread.join(
                timeout=max(0.0, deadline - _WORKSPACE_SWEEPER_MONOTONIC())
            )
    stopped = all(not handle.thread.is_alive() for _key, handle in handles)
    with _WORKSPACE_RECONCILE_LOCK:
        for key, handle in handles:
            if not handle.thread.is_alive() and _WORKSPACE_SWEEPERS.get(key) is handle:
                _WORKSPACE_SWEEPERS.pop(key, None)
                _WORKSPACE_RECONCILED.discard(key)
        late_handle = any(key in _WORKSPACE_SWEEPERS for key in reconciling_keys)
    return stopped and not late_handle


atexit.register(_stop_all_workspace_sweepers)


def _ensure_scratch_root(base: Path) -> Path:
    """The scratch pool's parent, inside the command center ``base``.

    ``<center>/workspaces/scratch`` since the owner split: a lease has to be
    the owner's own directory, only that owner's cell can create one, and a
    cell is bound to its command center. The daemon owns the pool directory
    above it and labels it for exactly that owner
    (:func:`workspace_owner_pool.prepare`), which is where creation now
    happens -- per operation, under the admitted owner identity, rather than
    once from a startup path that has no owner to name.

    Returned (not created) here so the startup sweep and the adapter still
    agree on ONE spelling of the pool root.
    """
    from tinyassets.effectors.workspace import scratch_pool_root

    return scratch_pool_root(base)


def ensure_workspace_reconciled(
    base_path: str | Path,
    *,
    start_sweeper: bool = True,
    interval_s: float = _WORKSPACE_SWEEP_INTERVAL_S,
    sweep_once: Callable[..., int] = _workspace_sweep_once,
) -> bool:
    """Once per process per data root: finish every outbox entry left by an
    earlier process (the admission barrier of the scratch-storage spec) and
    start the periodic sweeper. Returns True the first time, False after."""
    from tinyassets import workspace_pool
    from tinyassets.workspace_fs import RealPoolFilesystem

    key = (os.getpid(), str(Path(base_path).resolve()))
    with _WORKSPACE_RECONCILE_LOCK:
        if key in _WORKSPACE_RECONCILED or key in _WORKSPACE_RECONCILING:
            return False
        _WORKSPACE_RECONCILING.add(key)
    try:
        initialize_runs_db(base_path)
        _ensure_scratch_root(Path(base_path))
        db = runs_db_path(base_path)
        with _connect(base_path) as conn:
            workspace_pool.ensure_schema(conn)
        done = workspace_pool.startup_sweep(
            db, fs=RealPoolFilesystem(), claimant=f"startup:{os.getpid()}",
        )
        if done:
            logger.info("workspace startup sweep finished %d outbox entries", done)
        _reconcile_push_intents(base_path, when="startup")
        if start_sweeper:
            _start_workspace_sweeper(base_path, interval_s, sweep_once)
    except BaseException:
        # A failed attempt is not a completed one: the next caller retries.
        with _WORKSPACE_RECONCILE_LOCK:
            _WORKSPACE_RECONCILING.discard(key)
            _WORKSPACE_STOP_REQUESTED.discard(key)
        raise
    with _WORKSPACE_RECONCILE_LOCK:
        _WORKSPACE_RECONCILING.discard(key)
        stop_requested = key in _WORKSPACE_STOP_REQUESTED
        _WORKSPACE_STOP_REQUESTED.discard(key)
        # A concurrent stop after registration deliberately wins: the next
        # caller reruns the startup barrier and starts a fresh worker.
        if not stop_requested and (not start_sweeper or key in _WORKSPACE_SWEEPERS):
            _WORKSPACE_RECONCILED.add(key)
    return True


def _reconcile_workspace_on_read(base_path: str | Path) -> None:
    """A read finishes the workspace startup barrier; it never ends a run.

    Ending a run is recovery's job, and recovery acts only on proof that the
    owning process died (``recover_in_flight_runs``). A read has no such
    proof: "no Future here and no progress for an hour" took a sibling
    process's live run for dead (run-owner-proof).
    """
    initialize_runs_db(base_path)
    try:
        ensure_workspace_reconciled(base_path)
    except Exception:  # noqa: BLE001 - a read must keep serving
        logger.exception("workspace startup reconciliation failed")


def _migrate_runs_table_columns(conn: sqlite3.Connection) -> None:
    """Add every post-initial ``runs`` column, then the indexes that depend on
    them. Idempotent, concurrency-safe -- mirrors
    ``scheduler.migrate_scheduler_schema``.

    Four processes racing ``initialize_runs_db`` at boot can each read
    ``PRAGMA table_info(runs)`` before any of them has issued its ``ALTER
    TABLE``, then all try to add the same column -- every caller after the
    first to actually run the ALTER gets ``OperationalError: duplicate
    column name``. The probe and every ALTER below now run inside one
    ``BEGIN IMMEDIATE`` so only one connection can hold the write lock at a
    time; a caller that still loses a race against another *process*
    racing between two separate transactions treats "duplicate column" as
    proof the column already exists rather than a fatal error (belt and
    braces on top of the lock).

    Called from ``initialize_runs_db`` only, after the schema script and the
    ``node_edit_audit`` migration have already run on the same connection --
    the ``runs`` table is guaranteed to exist by this point.
    """
    from tinyassets.contribution_events import migrate_contribution_events_schema

    def _alter(col: str, ddl: str) -> None:
        try:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    conn.commit()  # close any implicit transaction so BEGIN IMMEDIATE can take the lock
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Migration: add run instrumentation columns. Provider telemetry
        # landed first; executor identity fields are nullable observability.
        existing_runs = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(runs)")
        }
        for col, ddl in (
            ("provider_used", "TEXT"),
            ("model",         "TEXT"),
            ("token_count",   "INTEGER"),
            ("owner_user_id", "TEXT NOT NULL DEFAULT ''"),
            ("daemon_id",     "TEXT"),
            ("runtime_instance_id", "TEXT"),
            ("worker_id",     "TEXT"),
            ("owner_token",   "TEXT"),
            ("outside_origin_json", "TEXT"),
            ("branch_task_id", "TEXT"),
            ("queue_universe_id", "TEXT"),
            ("workspace_budget_root_run_id", "TEXT"),
            ("workspace_budget_epoch", "INTEGER"),
            ("workspace_budget_closing_reason", "TEXT"),
            # PRIVATE admission envelope: the frozen definition + effective
            # execution choices a run was admitted with. Nullable and additive
            # — existing rows stay NULL ("unknown"), are never backfilled, and
            # refuse resume honestly. Deliberately absent from _row_to_run and
            # every public projection; read only via
            # tinyassets.run_admission_envelope after an ownership gate.
            ("admission_envelope_json", "TEXT"),
            # Who caused this run, recorded when it is created: the actor, or
            # for a `universe:<id>` run the principal bound for it. An engine
            # event about the run is stamped with this, never with whatever
            # identity happens to be ambient when the run ends.
            ("cause_principal", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in existing_runs:
                _alter(col, ddl)
        migrate_contribution_events_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_workspace_family "
            "ON runs(workspace_budget_root_run_id,workspace_budget_epoch,status)"
        )
        # Phase A item 6 (Task #65a) — branch_version_id on runs. NULL for
        # def-based runs (the existing path); populated only by
        # execute_branch_version_async for version-based runs. Required by
        # Task #48 contribution ledger + Task #53 route-back attribution.
        if "branch_version_id" not in existing_runs:
            _alter("branch_version_id", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_branch_version "
            "ON runs(branch_version_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_branch_task "
            "ON runs(branch_task_id) "
            "WHERE branch_task_id IS NOT NULL AND branch_task_id != ''"
        )
        # Backs latest_run_activity_for_universe's liveness read (universe
        # inspect / read_graph target=graph): a queue_universe_id + status
        # filter over the whole table, on every status-read call, is a full
        # scan without it. Created here -- AFTER the queue_universe_id
        # migration above, not in the upfront schema string -- because an
        # install whose ``runs`` table predates that column would otherwise
        # hit "no such column" on this CREATE INDEX before the ALTER ever
        # runs (the same class of hazard SCHEDULER_SCHEMA hit; see the
        # migrate_scheduler_schema comment near the top of this function).
        # CREATE INDEX IF NOT EXISTS is idempotent, so this also runs safely
        # at every daemon boot against the existing production table.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_scope_status_finished "
            "ON runs(queue_universe_id, status, finished_at)"
        )
        # Account storage measures each account's runs by owner (storage
        # accounting `run_records` / `checkpoints`); same placement rule.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_owner_user ON runs(owner_user_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def initialize_runs_db(base_path: str | Path) -> Path:
    """Ensure runs, events, and Phase 4 judgment tables exist. Idempotent."""
    schema = """
    CREATE TABLE IF NOT EXISTS runs (
        run_id         TEXT PRIMARY KEY,
        branch_def_id  TEXT NOT NULL,
        run_name       TEXT NOT NULL DEFAULT '',
        thread_id      TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'queued',
        actor          TEXT NOT NULL,
        owner_user_id  TEXT NOT NULL DEFAULT '',
        inputs_json    TEXT NOT NULL DEFAULT '{}',
        output_json    TEXT NOT NULL DEFAULT '{}',
        error          TEXT NOT NULL DEFAULT '',
        last_node_id   TEXT NOT NULL DEFAULT '',
        started_at     REAL NOT NULL,
        finished_at    REAL,
        provider_used  TEXT,
        model          TEXT,
        token_count    INTEGER,
        daemon_id      TEXT,
        runtime_instance_id TEXT,
        worker_id      TEXT,
        branch_task_id TEXT,
        queue_universe_id TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_runs_branch ON runs(branch_def_id);
    CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
    CREATE TABLE IF NOT EXISTS run_events (
        run_id         TEXT NOT NULL,
        step_index     INTEGER NOT NULL,
        node_id        TEXT NOT NULL,
        status         TEXT NOT NULL,
        started_at     REAL NOT NULL,
        finished_at    REAL,
        detail_json    TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (run_id, step_index)
    );

    CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);

    CREATE TABLE IF NOT EXISTS run_cancels (
        run_id         TEXT PRIMARY KEY,
        requested_at   REAL NOT NULL
    );

    -- run-owner-proof D4: every terminal TRANSITION of a run (a resumed run
    -- ends again), written by the trigger below in the same transaction as
    -- the status, whatever wrote it, and delivered at least once.
    CREATE TABLE IF NOT EXISTS run_terminal_outbox (
        run_id         TEXT NOT NULL,
        seq            INTEGER NOT NULL,
        status         TEXT NOT NULL,
        created_at     REAL NOT NULL,
        delivered_at   REAL,
        PRIMARY KEY (run_id, seq)
    );

    CREATE TRIGGER IF NOT EXISTS run_terminal_outbox_on_transition
    AFTER UPDATE OF status ON runs
    WHEN NEW.status IN ('completed', 'failed', 'cancelled', 'interrupted')
     AND OLD.status NOT IN ('completed', 'failed', 'cancelled', 'interrupted')
    BEGIN
        INSERT INTO run_terminal_outbox (run_id, seq, status, created_at)
        VALUES (
            NEW.run_id,
            (SELECT COALESCE(MAX(seq), 0) + 1 FROM run_terminal_outbox
              WHERE run_id = NEW.run_id),
            NEW.status,
            (julianday('now') - 2440587.5) * 86400.0
        );
    END;

    -- Phase 4: eval + iteration hooks.

    CREATE TABLE IF NOT EXISTS run_judgments (
        judgment_id    TEXT PRIMARY KEY,
        run_id         TEXT NOT NULL,
        node_id        TEXT,
        text           TEXT NOT NULL,
        tags_json      TEXT NOT NULL DEFAULT '[]',
        author         TEXT NOT NULL,
        timestamp      TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_judgments_run
        ON run_judgments(run_id);
    CREATE INDEX IF NOT EXISTS idx_judgments_node
        ON run_judgments(node_id);

    CREATE TABLE IF NOT EXISTS run_lineage (
        run_id                    TEXT PRIMARY KEY,
        parent_run_id             TEXT,
        branch_def_id             TEXT NOT NULL,
        branch_version            INTEGER NOT NULL,
        edits_since_parent_json   TEXT NOT NULL DEFAULT '[]',
        timestamp                 TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_lineage_parent
        ON run_lineage(parent_run_id);
    CREATE INDEX IF NOT EXISTS idx_lineage_branch
        ON run_lineage(branch_def_id, branch_version);

    CREATE TABLE IF NOT EXISTS node_edit_audit (
        audit_id                    TEXT PRIMARY KEY,
        branch_def_id               TEXT NOT NULL,
        version_before              INTEGER NOT NULL,
        version_after               INTEGER NOT NULL,
        nodes_changed_json          TEXT NOT NULL,
        triggered_by_judgment_id    TEXT,
        timestamp                   TEXT NOT NULL,
        node_before_json            TEXT NOT NULL DEFAULT '{}',
        node_after_json             TEXT NOT NULL DEFAULT '{}',
        edit_kind                   TEXT NOT NULL DEFAULT 'update'
    );

    CREATE INDEX IF NOT EXISTS idx_audit_branch
        ON node_edit_audit(branch_def_id);

    CREATE TABLE IF NOT EXISTS teammate_messages (
        message_id     TEXT PRIMARY KEY,
        from_run_id    TEXT NOT NULL,
        to_node_id     TEXT NOT NULL,
        message_type   TEXT NOT NULL,
        body_json      TEXT NOT NULL DEFAULT '{}',
        reply_to_id    TEXT,
        sent_at        TEXT NOT NULL,
        acked          INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_tmsg_to_node
        ON teammate_messages(to_node_id, sent_at);
    CREATE INDEX IF NOT EXISTS idx_tmsg_from_run
        ON teammate_messages(from_run_id);

    CREATE TABLE IF NOT EXISTS run_child_attachments (
        attachment_id       TEXT PRIMARY KEY,
        parent_run_id       TEXT NOT NULL,
        child_run_id        TEXT NOT NULL,
        child_branch_def_id TEXT NOT NULL,
        output_digest       TEXT NOT NULL,
        evidence_handle     TEXT NOT NULL,
        attached_at         REAL NOT NULL,
        attachment_json     TEXT NOT NULL DEFAULT '{}',
        UNIQUE(parent_run_id, child_run_id)
    );

    CREATE INDEX IF NOT EXISTS idx_child_attachments_child
        ON run_child_attachments(child_run_id);

    CREATE TABLE IF NOT EXISTS run_receipts (
        receipt_id      TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL,
        receipt_type    TEXT NOT NULL,
        subject_id      TEXT NOT NULL DEFAULT '',
        node_id         TEXT NOT NULL DEFAULT '',
        payload_json    TEXT NOT NULL DEFAULT '{}',
        created_at      REAL NOT NULL,
        -- The runs DB does not enable PRAGMA foreign_keys today, and runs
        -- are append-only. The explicit existence check in
        -- record_run_receipt is the load-bearing insert validation; this
        -- declaration is forward-compatible for future run deletion paths.
        FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_run_receipts_run
        ON run_receipts(run_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_run_receipts_type
        ON run_receipts(receipt_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_run_receipts_subject
        ON run_receipts(subject_id);
    """
    from tinyassets.branch_versions import BRANCH_VERSIONS_SCHEMA
    from tinyassets.contribution_events import CONTRIBUTION_EVENTS_SCHEMA
    from tinyassets.gate_events.schema import GATE_EVENT_SCHEMA
    from tinyassets.scheduler import SCHEDULER_SCHEMA, migrate_scheduler_schema
    schema = (
        schema
        + SCHEDULER_SCHEMA
        + BRANCH_VERSIONS_SCHEMA
        + GATE_EVENT_SCHEMA
        + CONTRIBUTION_EVENTS_SCHEMA
    )
    with _connect(base_path) as conn:
        # BEFORE the schema script, not after: SCHEDULER_SCHEMA creates an index
        # on ``branch_schedules(universe_id)``, and on an install that predates
        # that column the index fails with "no such column" — taking the whole
        # of initialize_runs_db down with it. The migration is a no-op on a
        # fresh DB (the table does not exist yet; CREATE TABLE brings the
        # columns) and idempotent on every subsequent call.
        migrate_scheduler_schema(conn)
        conn.executescript(schema)
        # Migration: older installs may predate the body-snapshot columns
        # added for rollback support. SQLite doesn't have
        # ``ADD COLUMN IF NOT EXISTS``, so probe pragma and add on-demand.
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(node_edit_audit)")
        }
        for col, ddl in (
            ("node_before_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("node_after_json",  "TEXT NOT NULL DEFAULT '{}'"),
            ("edit_kind",        "TEXT NOT NULL DEFAULT 'update'"),
        ):
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE node_edit_audit ADD COLUMN {col} {ddl}"
                )
        _migrate_runs_table_columns(conn)
    return runs_db_path(base_path)


# ─────────────────────────────────────────────────────────────────────────────
# Run record shape
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RunStepEvent:
    run_id: str
    step_index: int
    node_id: str
    status: str
    started_at: float
    finished_at: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step_index": self.step_index,
            "node_id": self.node_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "detail": self.detail,
        }


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    col_names = set(row.keys())
    return {
        "run_id": row["run_id"],
        "branch_def_id": row["branch_def_id"],
        "run_name": row["run_name"],
        "thread_id": row["thread_id"],
        "status": row["status"],
        "actor": row["actor"],
        "owner_user_id": (
            row["owner_user_id"] if "owner_user_id" in col_names else ""
        ),
        "inputs": json.loads(row["inputs_json"] or "{}"),
        "output": json.loads(row["output_json"] or "{}"),
        "error": row["error"],
        "last_node_id": row["last_node_id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "provider_used": row["provider_used"] if "provider_used" in col_names else None,
        "model": row["model"] if "model" in col_names else None,
        "token_count": row["token_count"] if "token_count" in col_names else None,
        "daemon_id": row["daemon_id"] if "daemon_id" in col_names else None,
        "runtime_instance_id": (
            row["runtime_instance_id"]
            if "runtime_instance_id" in col_names
            else None
        ),
        "worker_id": row["worker_id"] if "worker_id" in col_names else None,
        "branch_version_id": (
            row["branch_version_id"]
            if "branch_version_id" in col_names
            else None
        ),
        "branch_task_id": (
            row["branch_task_id"] if "branch_task_id" in col_names else None
        ),
        "queue_universe_id": (
            row["queue_universe_id"]
            if "queue_universe_id" in col_names
            else None
        ),
    }


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    detail_raw = row["detail_json"] or "{}"
    try:
        detail = json.loads(detail_raw)
    except json.JSONDecodeError:
        detail = {}
    return {
        "step_index": row["step_index"],
        "node_id": row["node_id"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "detail": detail,
    }


VALID_RECEIPT_TYPES = frozenset({
    "source_acquisition_receipt",
    "claim_lineage_receipt",
    "revision_receipt",
})

_SOURCE_RECEIPT_FLAGS = (
    "fetched",
    "viewed",
    "verified",
    "snapshotted",
    "unavailable",
    "not_searched",
)

_DEFAULT_RECEIPT_PAYLOAD_MAX_BYTES = 65_536


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _receipt_payload_max_bytes() -> int:
    raw = os.environ.get(
        "TINYASSETS_RECEIPT_PAYLOAD_MAX_BYTES",
        str(_DEFAULT_RECEIPT_PAYLOAD_MAX_BYTES),
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "TINYASSETS_RECEIPT_PAYLOAD_MAX_BYTES must be an integer"
        ) from exc
    if value <= 0:
        raise ValueError("TINYASSETS_RECEIPT_PAYLOAD_MAX_BYTES must be positive")
    return value


def _receipt_payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _as_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    out: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{idx}] must be a string")
        item = item.strip()
        if item:
            out.append(item)
    return out


def _as_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _normalize_receipt_payload(
    receipt_type: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Normalize known receipt fields while preserving extension metadata.

    Unknown payload keys outside the documented schema round-trip unchanged
    for forward compatibility. The substrate makes no claim about their
    meaning, type, or future canonical reservation; standards and domain
    packs that need their own schema should put custom material under an
    ``extensions`` object and validate it before recording the receipt.
    """
    if receipt_type not in VALID_RECEIPT_TYPES:
        raise ValueError(
            "receipt_type must be one of: "
            f"{', '.join(sorted(VALID_RECEIPT_TYPES))}"
        )
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    normalized = dict(payload)
    subject_id = ""

    if receipt_type == "source_acquisition_receipt":
        source_ref = str(
            normalized.get("source_ref")
            or normalized.get("source")
            or normalized.get("file_ref")
            or normalized.get("corpus_ref")
            or ""
        ).strip()
        if not source_ref:
            raise ValueError(
                "source_acquisition_receipt requires source_ref, source, "
                "file_ref, or corpus_ref"
            )
        normalized["source_ref"] = source_ref
        normalized.setdefault("retrieval_timestamp", _iso_now())
        normalized.setdefault("search_scope", "")
        normalized.setdefault("snapshot_hash", "")
        normalized.setdefault("rights_state", "")
        normalized.setdefault("access_state", "")
        for flag in _SOURCE_RECEIPT_FLAGS:
            normalized[flag] = _as_bool(normalized.get(flag, False), flag)
        acquired_flags = ("fetched", "viewed", "verified", "snapshotted")
        if normalized["not_searched"] and any(
            normalized[flag] for flag in acquired_flags
        ):
            raise ValueError(
                "not_searched cannot be combined with fetched, viewed, "
                "verified, or snapshotted"
            )
        if normalized["not_searched"] and normalized["unavailable"]:
            raise ValueError(
                "not_searched cannot be combined with unavailable"
            )
        if normalized["unavailable"] and any(
            normalized[flag] for flag in acquired_flags
        ):
            raise ValueError(
                "unavailable cannot be combined with fetched, viewed, "
                "verified, or snapshotted"
            )
        subject_id = source_ref

    elif receipt_type == "claim_lineage_receipt":
        claim_id = str(normalized.get("claim_id") or "").strip()
        if not claim_id:
            raise ValueError("claim_lineage_receipt requires claim_id")
        normalized["claim_id"] = claim_id
        normalized["evidence_refs"] = _as_string_list(
            normalized.get("evidence_refs"), "evidence_refs"
        )
        normalized["imported_prior_run_claims"] = _as_string_list(
            normalized.get("imported_prior_run_claims"),
            "imported_prior_run_claims",
        )
        normalized["counter_evidence_refs"] = _as_string_list(
            normalized.get("counter_evidence_refs"), "counter_evidence_refs"
        )
        normalized["changed_claims"] = _as_string_list(
            normalized.get("changed_claims"), "changed_claims"
        )
        normalized.setdefault("confidence", "")
        normalized.setdefault("status", "")
        normalized.setdefault("rationale", "")
        subject_id = claim_id

    elif receipt_type == "revision_receipt":
        old_run_id = str(normalized.get("old_run_id") or "").strip()
        old_claim_id = str(normalized.get("old_claim_id") or "").strip()
        if not old_run_id and not old_claim_id:
            raise ValueError(
                "revision_receipt requires old_run_id or old_claim_id"
            )
        normalized["old_run_id"] = old_run_id
        normalized["old_claim_id"] = old_claim_id
        normalized["new_evidence_refs"] = _as_string_list(
            normalized.get("new_evidence_refs"), "new_evidence_refs"
        )
        normalized["affected_outputs"] = _as_string_list(
            normalized.get("affected_outputs"), "affected_outputs"
        )
        normalized["recommended_reruns"] = _as_string_list(
            normalized.get("recommended_reruns"), "recommended_reruns"
        )
        normalized.setdefault("changed_status", "")
        normalized.setdefault("changed_confidence", "")
        normalized.setdefault("rationale", "")
        subject_id = old_claim_id or old_run_id

    payload_bytes = _receipt_payload_size_bytes(normalized)
    max_bytes = _receipt_payload_max_bytes()
    if payload_bytes > max_bytes:
        raise ValueError(
            f"payload exceeds max {max_bytes} bytes (got {payload_bytes})"
        )

    return normalized, subject_id


def _row_to_receipt(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "receipt_id": row["receipt_id"],
        "run_id": row["run_id"],
        "receipt_type": row["receipt_type"],
        "subject_id": row["subject_id"],
        "node_id": row["node_id"],
        "payload": payload,
        "created_at": row["created_at"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Persistence CRUD
# ─────────────────────────────────────────────────────────────────────────────


def create_run(
    base_path: str | Path,
    *,
    branch_def_id: str,
    thread_id: str,
    inputs: dict[str, Any],
    run_name: str = "",
    actor: str,
    branch_version_id: str | None = None,
    owner_user_id: str | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    branch_task_id: str | None = None,
    queue_universe_id: str | None = None,
    _workspace_parent=None,
) -> str:
    actor = named_principal(actor)
    if not actor:
        # A run is somebody's; no synthetic principal is minted.
        raise ValueError("create_run actor must be a real principal; an unowned value is not one")
    initialize_runs_db(base_path)
    run_id = uuid.uuid4().hex[:16]
    resolved_owner_user_id = (
        str(owner_user_id or "")
        if owner_user_id is not None
        else _resolve_owner_user_id(base_path, daemon_id)
    )
    from tinyassets.workspace_family import (
        FamilyMember,
        FamilyRefused,
        UnmanagedParent,
        family_fence,
    )

    if _workspace_parent is not None and (
            type(_workspace_parent) not in (FamilyMember, UnmanagedParent)
            or (type(_workspace_parent) is FamilyMember and not owner_user_id)):
        raise FamilyRefused("child family requires authenticated typed parent identity")
    scope = (family_fence(base_path, _workspace_parent.root_run_id)
             if type(_workspace_parent) is FamilyMember else contextlib.nullcontext())
    with scope as fence:
        with _connect(base_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            _insert_run_in_transaction(
                conn, run_id=run_id, branch_def_id=branch_def_id, thread_id=thread_id,
                inputs=inputs, run_name=run_name, actor=actor,
                owner_user_id=resolved_owner_user_id, branch_version_id=branch_version_id,
                daemon_id=daemon_id, runtime_instance_id=runtime_instance_id,
                worker_id=worker_id, branch_task_id=branch_task_id,
                queue_universe_id=queue_universe_id,
                _workspace_authenticated=(owner_user_id is not None
                                          and type(_workspace_parent) is not UnmanagedParent),
                _workspace_parent=(_workspace_parent
                                   if type(_workspace_parent) is FamilyMember else None),
                _workspace_fence=fence,
            )
    return run_id


def _insert_run_in_transaction(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    branch_def_id: str,
    thread_id: str,
    inputs: dict[str, Any],
    actor: str,
    owner_user_id: str,
    run_name: str = "",
    branch_version_id: str | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    branch_task_id: str | None = None,
    queue_universe_id: str | None = None,
    _workspace_authenticated: bool = True,
    _workspace_parent=None,
    _workspace_fence=None,
) -> None:
    """Insert a run alongside a durable intent in its caller-owned transaction.

    Internal persistence seam, not an authorization or execution entry point.
    The caller initializes the runs schema before acquiring this transaction,
    derives ownership from trusted authority and commits related intent/attempt
    rows atomically. Never resolve another database or create a nested connection
    here. Ordinary create_run keeps its existing ownership-resolution contract.
    """
    if not conn.in_transaction:
        raise ValueError("run insertion requires an active caller-owned transaction")
    actor = named_principal(actor)
    if not actor:
        raise ValueError("create_run actor must be a real principal; an unowned value is not one")
    from tinyassets.workspace_family import root_enrollment_enabled

    managed_root = (_workspace_authenticated and owner_user_id and queue_universe_id
                    and _workspace_parent is None and root_enrollment_enabled(conn))
    # The creating process owns the row until an executor takes it running.
    # Its liveness lock is a file lock under the runs DB's own data root, taken
    # before the token is written anywhere (run-owner-proof D2).
    from tinyassets.process_liveness import owner_token

    owner = owner_token(Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent)
    try:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, branch_def_id, run_name, thread_id,
                status, actor, owner_user_id, inputs_json, started_at,
                branch_version_id, daemon_id, runtime_instance_id,
                worker_id, branch_task_id, queue_universe_id,
                workspace_budget_root_run_id,workspace_budget_epoch,workspace_budget_closing_reason,
                owner_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, branch_def_id, run_name, thread_id,
                RUN_STATUS_QUEUED, actor, owner_user_id,
                json.dumps(inputs, default=str), _now(),
                branch_version_id,
                daemon_id,
                runtime_instance_id,
                worker_id,
                branch_task_id,
                queue_universe_id,
                (run_id if managed_root else None),
                (1 if managed_root else None),
                ("" if managed_root else None),
                owner,
            ),
        )
        from tinyassets.outside_authority import captured_identity

        conn.execute("UPDATE runs SET outside_origin_json=? WHERE run_id=?",
                     (captured_identity(owner_user_id), run_id))
        conn.execute(
            "UPDATE runs SET cause_principal = ? WHERE run_id = ?",
            (_cause_principal(actor), run_id),
        )
    except sqlite3.IntegrityError as exc:
        if branch_task_id and "runs.branch_task_id" in str(exc):
            raise BranchTaskRunReservationConflict(
                f"BranchTask {branch_task_id!r} already has a run reservation"
            ) from exc
        raise

    if _workspace_parent is not None:
        from tinyassets.workspace_family import FamilyFence, FamilyRefused, assign_in_transaction

        if not _workspace_authenticated or type(_workspace_fence) is not FamilyFence:
            raise FamilyRefused("child insertion requires held family fence and identity")
        assign_in_transaction(conn, _workspace_fence, run_id, parent=_workspace_parent)


def _cause_principal(actor: str) -> str:
    """The principal a new run acts for: its actor, or the one bound for a
    universe's own run. '' when a universe run has nobody bound."""
    if not actor.startswith("universe:"):
        return actor
    from tinyassets.api.permissions import current_request_actor_id

    return current_request_actor_id()


def _guard_status_write(function):
    from functools import wraps

    @wraps(function)
    def guarded(base_path, run_id, **kwargs):
        with _managed_execution_scope(base_path, run_id):
            return function(base_path, run_id, **kwargs)

    return guarded


#: Server-owned record of the effect receipts earlier SEGMENTS of a run wrote.
#: A resume records its own segment's receipts at ``external_write_results``,
#: which is what a reader of "this run's effects" means; the segments before it
#: keep theirs here instead of being erased by the resume that followed them.
#: Bounded evidence only, exactly as it was already persisted - never the full
#: results the chain holds in memory, and never fed back into a chain: an
#: effect that fired before an interrupt stays unreadable to a later node
#: (design D1), and at-most-once stays ``already_fired``'s job.
PRIOR_EXTERNAL_WRITE_RESULTS_KEY = "prior_external_write_results"


def _prior_effect_receipts(base_path: str | Path, run_id: str) -> dict[str, Any]:
    """Receipts already on the run row, read the instant before a write
    replaces them.

    Straight off ``output_json`` rather than ``get_run``, which orphan-marks
    and releases workspaces - a status write must not carry those side
    effects. Both receipt keys are read, so a run resumed twice carries its
    first segment's receipts past its second.
    """
    try:
        with _connect(base_path) as conn:
            row = conn.execute(
                "SELECT output_json FROM runs WHERE run_id = ?", (run_id,),
            ).fetchone()
        if row is None or not row["output_json"]:
            return {}
        prior_output = json.loads(row["output_json"])
    except Exception:  # noqa: BLE001 - a receipt carry never breaks a status write
        logger.exception("prior effect receipt read failed for run %s", run_id)
        return {}
    if not isinstance(prior_output, dict):
        return {}
    carried: dict[str, Any] = {}
    # Order is the merge rule: an older segment's record first, then the
    # receipts written at the canonical key by the segment that just ended.
    for key in (PRIOR_EXTERNAL_WRITE_RESULTS_KEY, "external_write_results"):
        section = prior_output.get(key)
        if not isinstance(section, dict):
            continue
        for node_id, per_sink in section.items():
            if isinstance(per_sink, dict):
                carried[str(node_id)] = per_sink
    return carried


def _carry_prior_effect_receipts(
    base_path: str | Path, run_id: str, persisted: dict[str, Any],
) -> None:
    """Attach the prior segments' receipts to the output about to land.

    System-authoritative: whatever a branch left at the key is dropped first,
    the same rule ``external_write_results`` follows. A node this segment
    re-recorded is not prior - its fresh receipt is the canonical one and this
    record keeps every node the fresh evidence does not mention.
    """
    persisted.pop(PRIOR_EXTERNAL_WRITE_RESULTS_KEY, None)
    carried = _prior_effect_receipts(base_path, run_id)
    fresh = persisted.get("external_write_results")
    if isinstance(fresh, dict):
        carried = {k: v for k, v in carried.items() if k not in fresh}
    if carried:
        persisted[PRIOR_EXTERNAL_WRITE_RESULTS_KEY] = dict(sorted(carried.items()))


@_guard_status_write
def update_run_status(
    base_path: str | Path,
    run_id: str,
    *,
    status: str | None = None,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    last_node_id: str | None = None,
    finished_at: float | None = None,
    provider_used: str | None = None,
    model: str | None = None,
    token_count: int | None = None,
    _workspace_member=None,
    _expected_statuses=None,
) -> None:
    if _workspace_member is not None:
        from tinyassets.workspace_family import run_transaction

        # Reject a retired worker before touching the run-keyed effect registry.
        # The actual status transaction rechecks again after settlement.
        with run_transaction(base_path, run_id, expected=_workspace_member):
            pass
    sets: list[str] = []
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if output is not None:
        sets.append("output_json = ?")
        params.append(json.dumps(output, default=str))
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if last_node_id is not None:
        sets.append("last_node_id = ?")
        params.append(last_node_id)
    if finished_at is not None:
        sets.append("finished_at = ?")
        params.append(finished_at)
    if provider_used is not None:
        sets.append("provider_used = ?")
        params.append(provider_used)
    if model is not None:
        sets.append("model = ?")
        params.append(model)
    if token_count is not None:
        sets.append("token_count = ?")
        params.append(token_count)
    if status in (RUN_STATUS_RUNNING, RUN_STATUS_RESUMED):
        # Whoever takes the run forward owns it from here: a resume, or a
        # worker that is not the process that queued it.
        from tinyassets.process_liveness import owner_token

        sets.append("owner_token = ?")
        params.append(owner_token(base_path))
    if status in (
        RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED, RUN_STATUS_INTERRUPTED,
    ):
        # Effects fire at node time (design D1), so a run can fire a write and
        # then fail. Its engine admission settles from what actually fired
        # (write is final), and what it fired is persisted even though the run
        # did not complete - a branch it created must not vanish from the
        # record because open_pr failed after it. Without a chain (legacy
        # callers) a run that did not complete fired nothing: a read.
        try:
            from tinyassets.effectors import (
                active_effect_chain,
                forget_effect_chain,
                settle_engine_admission,
            )

            chain = active_effect_chain(run_id)
            persisted: dict[str, Any] | None = None
            if chain is not None:
                # Settle while still registered, THEN forget: a concurrent
                # terminal caller must never find "no chain" mid-settlement
                # (Codex round 3, P0). Settle waits for adapters in flight.
                chain.settle()
                forget_effect_chain(run_id)
                carry = (
                    chain.evidence or chain.already_fired or chain.rpc_calls
                ) and status != RUN_STATUS_COMPLETED
                if carry:
                    evidence = dict(chain.evidence)
                    # Merge into whatever the caller persisted (an interrupt's
                    # receipt gate, for instance) - never clobber, never drop
                    # (Codex round 2, P1).
                    persisted = dict(output) if isinstance(output, dict) else {}
                    persisted.setdefault("external_write_results", evidence)
                    rows = _collect_external_write_errors(evidence)
                    if rows:
                        persisted.setdefault("external_write_errors", rows)
                    delivered = chain.delivered_nodes()
                    if delivered:
                        # "failed after writes" is a real state the surfaces
                        # must show, not accounting policy (Codex round 1, P2).
                        persisted["failed_after_effects"] = delivered
                    # Run-wide bounds survive an interrupt: what fired in any
                    # earlier segment, the RPC count, and the invocation depth
                    # are seeded back on resume (Codex round 3, P0/P1).
                    persisted["effects_fired_before"] = sorted(
                        set(chain.already_fired) | set(evidence)
                    )
                    persisted["rpc_calls"] = int(chain.rpc_calls)
                    persisted["invocation_depth"] = int(chain.invocation_depth)
            elif status in (RUN_STATUS_FAILED, RUN_STATUS_CANCELLED):
                settle_engine_admission(run_id, [])
            if persisted is None and isinstance(output, dict):
                # A completed segment persists its own output, so the carry
                # above did not run - this write still replaces the row's
                # receipts and still has to carry the earlier ones through.
                persisted = dict(output)
            if persisted is not None:
                # Whatever this terminal write persists REPLACES output_json,
                # so a resume's receipts are the only ones left unless the
                # segments before it are carried forward here. Server-owned
                # and last, after the chain has had its say.
                _carry_prior_effect_receipts(base_path, run_id, persisted)
                encoded = json.dumps(persisted, default=str)
                if "output_json = ?" in sets:
                    params[sets.index("output_json = ?")] = encoded
                else:
                    sets.append("output_json = ?")
                    params.append(encoded)
        except Exception:  # pragma: no cover - never let accounting break a status write
            logger.exception("engine admission settle failed for run %s", run_id)
    if not sets:
        return
    params.append(run_id)
    workspace_terminal_base: Path | None = None
    owed = 0
    completed_row: Any = None
    from tinyassets.workspace_family import status_transaction

    with status_transaction(base_path, run_id, status, expected=_workspace_member,
                            expected_statuses=_expected_statuses) as conn:
        prior = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        cursor = conn.execute(
            f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ? AND status IS ?",
            (*params, prior[0] if prior is not None else None),
        )
        if prior is not None and cursor.rowcount != 1:
            raise RunExecutionAuthorityLost("Run status changed before its conditional write.")
        if (
            status in _TERMINAL_STATUSES
            and prior is not None
            and prior[0] not in _TERMINAL_STATUSES
        ):
            # The transition, not every terminal write: a later write that
            # only re-persists output must not announce the run twice.
            completed_row = True  # the trigger owed its event in this transaction
        if status in _TERMINAL_STATUSES:
            # A lease in this database is owed THROUGH the outbox in the same
            # transaction (workspace-node D0): never a direct delete.  A
            # separate universe WAL is handled after this root commit.
            owed = _enqueue_workspace_terminal(conn, base_path, run_id)
            workspace_terminal_base = _workspace_terminal_base(
                conn, base_path, run_id
            )
        # Phase 2 emit-site (Task #72): on terminal status transition, emit
        # one execute_step contribution event for attribution. Wrapped in
        # try/except so emit failure (malformed metadata, table missing,
        # etc.) never blocks a status update — status is the load-bearing
        # semantic; emit is best-effort observability. Production observers
        # grep contribution_events._EMIT_FAILURES for non-zero.
        if status in _TERMINAL_STATUSES:
            try:
                row = conn.execute(
                    "SELECT actor, owner_user_id, daemon_id, "
                    "runtime_instance_id, worker_id, branch_def_id, "
                    "branch_version_id "
                    "FROM runs WHERE run_id = ?", (run_id,),
                ).fetchone()
                if row is not None:
                    artifact_id = row["branch_version_id"] or row["branch_def_id"]
                    # Skip emit when no artifact identifier is present —
                    # no attribution path = no event (per design discipline).
                    if artifact_id:
                        from tinyassets.contribution_events import (
                            record_contribution_event,
                        )
                        artifact_kind = (
                            "branch_version" if row["branch_version_id"]
                            else "branch_def"
                        )
                        record_contribution_event(
                            base_path,
                            event_id=f"execute_step:{run_id}:{status}",
                            event_type="execute_step",
                            actor_id=named_principal(row["actor"]),
                            owner_user_id=row["owner_user_id"] or "",
                            daemon_id=row["daemon_id"] or "",
                            runtime_instance_id=row["runtime_instance_id"] or "",
                            worker_id=row["worker_id"] or "",
                            source_run_id=run_id,
                            source_artifact_id=artifact_id,
                            source_artifact_kind=artifact_kind,
                            weight=1.0,
                            occurred_at=_now(),
                            metadata_json=json.dumps({
                                "branch_def_id": row["branch_def_id"],
                                "branch_version_id": row["branch_version_id"],
                                "terminal_status": status,
                            }),
                            conn=conn,
                        )
            except Exception as exc:
                from tinyassets.contribution_events import _EMIT_FAILURES
                from tinyassets.contribution_events import _logger as _ce_logger
                _EMIT_FAILURES["count"] += 1
                _ce_logger.warning(
                    "execute_step emit failed for run %s (status=%s): %s; "
                    "status update preserved",
                    run_id, status, exc,
                )

    # The root transaction is committed before this separate universe write.
    # SQLite cannot make two WAL databases crash-atomic; the helper's sweep is
    # the recovery half of the protocol and never reclassifies the run.
    if status in _TERMINAL_STATUSES:
        _finish_terminal_workspace_release(
            base_path, run_id, workspace_terminal_base, local_owed=owed
        )
    if completed_row:
        from tinyassets.capability_health import RATES
        from tinyassets.core_capabilities import failure_code

        if status == RUN_STATUS_COMPLETED:
            RATES.record('background_run')
        elif status == RUN_STATUS_FAILED:
            RATES.record('background_run', failure_code(error or 'run failed'))
        deliver_terminal_events(base_path, run_ids=[run_id])


#: Undelivered outbox rows younger than this belong to the writer that is about
#: to deliver them; older ones are redelivered by the watcher and at boot.
TERMINAL_REDELIVERY_SECONDS = 30.0

_OWED_EVENTS = (
    "SELECT o.run_id, o.seq, o.status, r.branch_def_id, r.actor, r.queue_universe_id, "
    "r.cause_principal FROM run_terminal_outbox o JOIN runs r USING (run_id) "
    "WHERE o.delivered_at IS NULL "
)


def deliver_terminal_events(
    base_path: str | Path,
    *,
    run_ids: list[str] | None = None,
    older_than: float = TERMINAL_REDELIVERY_SECONDS,
) -> int:
    """Emit every owed terminal event, then mark it delivered. Never raises.

    ``run_ids`` delivers exactly those (the writer, right after its commit);
    otherwise every row owed for longer than ``older_than`` (the watcher, boot).
    At least once: a crash after the emit and before the mark delivers it again,
    and the consumer -- one event wake per subscription and run -- is idempotent.
    """
    try:
        with _connect(base_path) as conn:
            if run_ids is not None:
                marks = ",".join("?" for _ in run_ids) or "''"
                owed = conn.execute(
                    _OWED_EVENTS + f"AND o.run_id IN ({marks}) ORDER BY o.run_id, o.seq",
                    list(run_ids),
                ).fetchall()
            else:
                owed = conn.execute(
                    _OWED_EVENTS + "AND o.created_at <= ? ORDER BY o.run_id, o.seq",
                    (_now() - older_than,),
                ).fetchall()
    except sqlite3.Error:
        logger.exception("terminal outbox read failed")
        return 0
    delivered = 0
    for row in owed:
        # Acknowledged only after the emit succeeded: a failure leaves the row
        # owed and the watcher delivers it again (the wake is idempotent).
        if not _emit_run_completed(
            base_path, row["run_id"], row["status"], row,
            event_id=f"{row['run_id']}#{row['seq']}", strict=True,
        ):
            continue
        try:
            with _connect(base_path) as conn:
                conn.execute(
                    "UPDATE run_terminal_outbox SET delivered_at = ? "
                    "WHERE run_id = ? AND seq = ? AND delivered_at IS NULL",
                    (_now(), row["run_id"], row["seq"]),
                )
            delivered += 1
        except sqlite3.Error:
            logger.exception("terminal outbox mark failed run=%s", row["run_id"])
    return delivered


def _emit_run_completed(
    base_path: str | Path, run_id: str, status: str, row: Any,
    *, event_id: str = "", strict: bool = False,
) -> bool:
    """Wake the owner's ``run_completed`` subscriptions.

    ``event_id`` names this terminal transition (``<run>#<seq>``) so a second
    delivery of it finds the first wake. ``strict`` reports a failure instead
    of swallowing it, so the outbox keeps the event owed.
    """
    from tinyassets.automation_events import emit_run_completed

    return emit_run_completed(
        base_path,
        run_id=run_id,
        branch_def_id=str(row["branch_def_id"] or ""),
        outcome=str(status),
        actor=str(row["actor"] or ""),
        queue_universe_id=str(row["queue_universe_id"] or ""),
        cause_principal=str(row["cause_principal"] or ""),
        event_id=event_id,
        strict=strict,
    ) is not None


def terminalize_unstarted_run(
    base_path: str | Path, *, run_id: str, execution_guard,
    status: str, error: str,
) -> str:
    """Settle a proven never-dispatched attempt under its current owner guard.

    Internal prepared-worker seam, NOT orphan recovery. The caller must know
    invocation was never dispatched in this attempt. A recovered durable-start
    marker plus queued status does not prove that: it needs exact owned process
    recovery first. Only queued rows settle; resumed/running/terminal rows are
    not rewritten by a stale original attempt. Return actual status.
    """
    from tinyassets.storage.run_execution_lock import RunExecutionGuard
    from tinyassets.workspace_family import _active, close_in_transaction, run_transaction

    if type(execution_guard) is not RunExecutionGuard or execution_guard.run_id != run_id:
        raise RunExecutionAuthorityLost("Unstarted settlement requires this run's execution guard.")
    if status not in (RUN_STATUS_FAILED, RUN_STATUS_INTERRUPTED, RUN_STATUS_CANCELLED):
        raise ValueError("unstarted settlement must be failed, interrupted or requested cancelled")
    with _connect(base_path) as conn:
        execution_guard.require_held(conn)
    with run_transaction(base_path, run_id) as (conn, member, fence):
        execution_guard.require_held(conn)
        row = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise RunExecutionAuthorityLost("Unstarted settlement has no run.")
        if row[0] != RUN_STATUS_QUEUED:
            return row[0]
        cancelled = conn.execute("SELECT 1 FROM run_cancels WHERE run_id=?", (run_id,)).fetchone()
        if member is not None:
            reason = conn.execute("SELECT workspace_budget_closing_reason FROM runs WHERE run_id=?",
                                  (member.root_run_id,)).fetchone()[0]
            cancelled = cancelled or reason == "cancelled"
        if status == RUN_STATUS_CANCELLED and not cancelled:
            raise RunExecutionAuthorityLost("Unstarted cancellation has no current cancel intent.")
        settled = RUN_STATUS_CANCELLED if cancelled else status
        changed = conn.execute(
            "UPDATE runs SET status=?,error=?,finished_at=? WHERE run_id=? AND status=?",
            (settled, error, _now(), run_id, row[0]),
        )
        if changed.rowcount != 1:
            raise RunExecutionAuthorityLost("Unstarted settlement lost its expected status.")
        if member is not None:
            if member.run_id == member.root_run_id:
                close_in_transaction(conn, fence, member.epoch, settled)
            elif not _active(conn, member.root_run_id, member.epoch):
                close_in_transaction(conn, fence, member.epoch, "drained")
        owed = _enqueue_workspace_terminal(conn, base_path, run_id)
        workspace_base = _workspace_terminal_base(conn, base_path, run_id)
    _finish_terminal_workspace_release(base_path, run_id, workspace_base, local_owed=owed)
    # The status trigger owed this run's terminal event; deliver it now rather
    # than on the watcher's next pass.
    deliver_terminal_events(base_path, run_ids=[run_id])
    return settled


def record_run_receipt(
    base_path: str | Path,
    *,
    run_id: str,
    receipt_type: str,
    payload: dict[str, Any],
    node_id: str = "",
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Persist a generic, machine-checkable receipt for a run.

    Receipts deliberately record acquisition/lineage/revision facts without
    assigning truth rank. Gates and later runs can inspect the normalized
    payload and decide how to use it. Insert-time run existence is checked
    explicitly here; the run_receipts foreign key is declarative until the
    runs DB enables SQLite foreign-key enforcement.
    """
    initialize_runs_db(base_path)
    run_id = run_id.strip()
    if not run_id:
        raise ValueError("run_id is required")

    normalized, subject_id = _normalize_receipt_payload(receipt_type, payload)
    receipt_id = receipt_id or uuid.uuid4().hex[:16]
    created_at = _now()

    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"run_id '{run_id}' not found")
        conn.execute(
            """
            INSERT INTO run_receipts (
                receipt_id, run_id, receipt_type, subject_id, node_id,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                run_id,
                receipt_type,
                subject_id,
                node_id.strip(),
                json.dumps(normalized, default=str, sort_keys=True),
                created_at,
            ),
        )

    return {
        "receipt_id": receipt_id,
        "run_id": run_id,
        "receipt_type": receipt_type,
        "subject_id": subject_id,
        "node_id": node_id.strip(),
        "payload": normalized,
        "created_at": created_at,
    }


def list_run_receipts(
    base_path: str | Path,
    *,
    run_id: str = "",
    receipt_type: str = "",
    subject_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    initialize_runs_db(base_path)
    limit = min(max(1, int(limit)), 1000)
    clauses: list[str] = []
    params: list[Any] = []

    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id.strip())
    if receipt_type:
        if receipt_type not in VALID_RECEIPT_TYPES:
            raise ValueError(
                "receipt_type must be one of: "
                f"{', '.join(sorted(VALID_RECEIPT_TYPES))}"
            )
        clauses.append("receipt_type = ?")
        params.append(receipt_type)
    if subject_id:
        clauses.append("subject_id = ?")
        params.append(subject_id.strip())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT receipt_id, run_id, receipt_type, subject_id, node_id,
                   payload_json, created_at
            FROM run_receipts
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [_row_to_receipt(row) for row in rows]


def get_run(base_path: str | Path, run_id: str) -> dict[str, Any] | None:
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        result = _row_to_run(row)
        # Surface concurrency stats from the last concurrency_stats system event.
        stats_row = conn.execute(
            """
            SELECT detail_json FROM run_events
            WHERE run_id = ? AND status = 'concurrency_stats'
            ORDER BY step_index DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
    if stats_row:
        try:
            result["concurrency"] = json.loads(stats_row["detail_json"] or "{}")
        except json.JSONDecodeError:
            result["concurrency"] = None
    else:
        result["concurrency"] = None
    wait = workspace_wait_state(base_path, result)
    if wait is not None:
        result["workspace_wait"] = wait
    return result


class ChildRunAttachmentError(ValueError):
    """Structured validation failure for attach_existing_child_run."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


_RECEIPT_WAITING_VALUES = frozenset({
    "attach_required",
    "blocked_before_child_attach",
    "receipt_waiting",
    "selected_attach_required",
    "waiting_for_child_receipt",
})


def _normalise_digest(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("sha256:"):
        return value
    if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
        return f"sha256:{value.lower()}"
    return value


def _run_output_digest(output: dict[str, Any]) -> str:
    payload = json.dumps(
        output,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parent_is_receipt_waiting(output: dict[str, Any]) -> bool:
    if output.get("stable_evidence_handle"):
        return False
    for key in ("selected_child_status", "selected_branch_state", "automation_claim_status"):
        value = str(output.get(key, "")).strip().lower()
        if value in {
            "attached_completed",
            "child_attached_existing_receipt",
            "child_attached_with_handle",
        }:
            return False
    for key in (
        "parent_loop_status",
        "selected_child_status",
        "selected_branch_state",
        "automation_claim_status",
        "final_outcome_label",
    ):
        value = str(output.get(key, "")).strip().lower()
        if value in _RECEIPT_WAITING_VALUES:
            return True
        if value.endswith("_attach_required") or value.endswith("_receipt_waiting"):
            return True
    return False


def _selected_child_branch(parent_output: dict[str, Any]) -> str:
    return (
        str(parent_output.get("selected_child_branch_def_id") or "").strip()
        or str(parent_output.get("selected_loop_branch") or "").strip()
        or str(parent_output.get("child_branch_def_id") or "").strip()
    )


def attach_existing_child_run(
    base_path: str | Path,
    *,
    parent_run_id: str,
    child_run_id: str,
    child_branch_def_id: str = "",
    output_digest: str = "",
    actor: str = "",   # no default principal; the caller names one or the write refuses
) -> dict[str, Any]:
    """Validate and attach a completed child run receipt to a waiting parent.

    This is intentionally a receipt primitive. It only records provenance for
    an already-finished child.
    """
    initialize_runs_db(base_path)
    parent_run_id = parent_run_id.strip()
    child_run_id = child_run_id.strip()
    if not parent_run_id:
        raise ChildRunAttachmentError(
            "parent_run_id_required",
            "parent run_id is required.",
        )
    if not child_run_id:
        raise ChildRunAttachmentError(
            "child_run_id_required",
            "child_run_id is required.",
        )

    parent = get_run(base_path, parent_run_id)
    if parent is None:
        raise ChildRunAttachmentError(
            "parent_not_found",
            f"Parent run '{parent_run_id}' not found.",
            {"parent_run_id": parent_run_id},
        )
    child = get_run(base_path, child_run_id)
    if child is None:
        raise ChildRunAttachmentError(
            "child_not_found",
            f"Child run '{child_run_id}' not found.",
            {"child_run_id": child_run_id},
        )

    parent_output = copy.deepcopy(parent.get("output") or {})
    if not _parent_is_receipt_waiting(parent_output):
        raise ChildRunAttachmentError(
            "parent_not_receipt_waiting",
            "Parent run is not in a receipt-waiting state.",
            {
                "parent_run_id": parent_run_id,
                "parent_loop_status": parent_output.get("parent_loop_status", ""),
                "selected_child_status": parent_output.get("selected_child_status", ""),
            },
        )

    supplied_child_branch = child_branch_def_id.strip()
    expected_child_branch = _selected_child_branch(parent_output) or supplied_child_branch
    if not expected_child_branch:
        raise ChildRunAttachmentError(
            "child_branch_required",
            "child_branch_def_id is required when parent output has no selected child branch.",
            {"parent_run_id": parent_run_id},
        )
    actual_child_branch = str(child.get("branch_def_id") or "")
    if supplied_child_branch and supplied_child_branch != expected_child_branch:
        raise ChildRunAttachmentError(
            "child_branch_mismatch",
            "Supplied child branch does not match the parent selected child branch.",
            {
                "child_run_id": child_run_id,
                "expected_child_branch_def_id": expected_child_branch,
                "supplied_child_branch_def_id": supplied_child_branch,
                "actual_child_branch_def_id": actual_child_branch,
            },
        )
    if actual_child_branch != expected_child_branch:
        raise ChildRunAttachmentError(
            "child_branch_mismatch",
            "Child run branch does not match the selected child branch.",
            {
                "child_run_id": child_run_id,
                "expected_child_branch_def_id": expected_child_branch,
                "actual_child_branch_def_id": actual_child_branch,
            },
        )

    child_status = str(child.get("status") or "")
    if child_status != RUN_STATUS_COMPLETED:
        raise ChildRunAttachmentError(
            "child_not_completed",
            "Child run must be completed before it can be attached.",
            {"child_run_id": child_run_id, "child_status": child_status},
        )

    child_output = copy.deepcopy(child.get("output") or {})
    if not child_output:
        raise ChildRunAttachmentError(
            "child_output_missing",
            "Child run completed but has no output to attach.",
            {"child_run_id": child_run_id},
        )

    computed_digest = _run_output_digest(child_output)
    supplied_digest = _normalise_digest(output_digest)
    if supplied_digest and supplied_digest != computed_digest:
        raise ChildRunAttachmentError(
            "output_digest_mismatch",
            "Supplied child output digest does not match the stored child output.",
            {
                "child_run_id": child_run_id,
                "supplied_output_digest": supplied_digest,
                "computed_output_digest": computed_digest,
            },
        )

    digest_suffix = computed_digest.split(":", 1)[1][:16]
    evidence_handle = f"run-attachment:{parent_run_id}:{child_run_id}:{digest_suffix}"
    attachment_id = f"{parent_run_id}:{child_run_id}"
    attached_at = _now()
    receipt = {
        "attachment_id": attachment_id,
        "parent_run_id": parent_run_id,
        "child_run_id": child_run_id,
        "child_branch_def_id": actual_child_branch,
        "output_digest": computed_digest,
        "evidence_handle": evidence_handle,
        "attached_at": attached_at,
        "attached_by": actor,
        "provenance": "attached_existing_child",
        "automation_claim_status": "child_attached_with_handle",
    }

    with _connect(base_path) as conn:
        existing_child = conn.execute(
            """
            SELECT output_digest, evidence_handle FROM run_child_attachments
            WHERE child_run_id = ?
            LIMIT 1
            """,
            (child_run_id,),
        ).fetchone()
        if existing_child and existing_child["output_digest"] != computed_digest:
            raise ChildRunAttachmentError(
                "conflicting_child_digest",
                "Child run was already attached with a different output digest.",
                {
                    "child_run_id": child_run_id,
                    "existing_output_digest": existing_child["output_digest"],
                    "computed_output_digest": computed_digest,
                },
            )

        existing_pair = conn.execute(
            """
            SELECT output_digest, evidence_handle FROM run_child_attachments
            WHERE parent_run_id = ? AND child_run_id = ?
            """,
            (parent_run_id, child_run_id),
        ).fetchone()
        if existing_pair and existing_pair["output_digest"] != computed_digest:
            raise ChildRunAttachmentError(
                "conflicting_child_digest",
                "Child run was already attached to this parent with a different digest.",
                {
                    "parent_run_id": parent_run_id,
                    "child_run_id": child_run_id,
                    "existing_output_digest": existing_pair["output_digest"],
                    "computed_output_digest": computed_digest,
                },
            )
        if not existing_pair:
            conn.execute(
                """
                INSERT OR IGNORE INTO run_child_attachments (
                    attachment_id, parent_run_id, child_run_id,
                    child_branch_def_id, output_digest, evidence_handle,
                    attached_at, attachment_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    parent_run_id,
                    child_run_id,
                    actual_child_branch,
                    computed_digest,
                    evidence_handle,
                    attached_at,
                    json.dumps(receipt, sort_keys=True, default=str),
                ),
            )
            existing_pair = conn.execute(
                """
                SELECT output_digest, evidence_handle FROM run_child_attachments
                WHERE parent_run_id = ? AND child_run_id = ?
                """,
                (parent_run_id, child_run_id),
            ).fetchone()
        if existing_pair is None:
            raise ChildRunAttachmentError(
                "attachment_record_missing",
                "Child attachment record could not be written.",
                {"parent_run_id": parent_run_id, "child_run_id": child_run_id},
            )
        if existing_pair["output_digest"] != computed_digest:
            raise ChildRunAttachmentError(
                "conflicting_child_digest",
                "Child run was already attached to this parent with a different digest.",
                {
                    "parent_run_id": parent_run_id,
                    "child_run_id": child_run_id,
                    "existing_output_digest": existing_pair["output_digest"],
                    "computed_output_digest": computed_digest,
                },
            )
        evidence_handle = existing_pair["evidence_handle"]
        receipt["evidence_handle"] = evidence_handle

    parent_output.update({
        "selected_child_status": "attached_completed",
        "selected_branch_state": "child_attached_existing_receipt",
        "automation_claim_status": "child_attached_with_handle",
        "stable_evidence_handle": evidence_handle,
        "attached_child_run_id": child_run_id,
        "attached_child_branch_def_id": actual_child_branch,
        "attached_child_output_digest": computed_digest,
        "attached_child_output": child_output,
        "attached_child_receipt": receipt,
        "blocked_execution_record": {},
    })
    if "keep_reject_decision" in child_output:
        parent_output["attached_child_decision"] = child_output["keep_reject_decision"]

    update_run_status(base_path, parent_run_id, output=parent_output)
    return {
        "status": "attached",
        "parent_run_id": parent_run_id,
        "child_run_id": child_run_id,
        "child_branch_def_id": actual_child_branch,
        "selected_child_status": "attached_completed",
        "automation_claim_status": "child_attached_with_handle",
        "stable_evidence_handle": evidence_handle,
        "output_digest": computed_digest,
        "attached_child_output": child_output,
        "receipt": receipt,
    }


def list_runs(
    base_path: str | Path,
    *,
    branch_def_id: str = "",
    status: str = "",
    limit: int = 50,
    universe_id: str = "",
) -> list[dict[str, Any]]:
    """Newest runs first, at most ``limit`` of them.

    ``universe_id`` narrows IN SQL, before the limit. Filtering afterwards
    returned the newest ``limit`` runs of the whole deployment and then kept
    this universe's share of them -- on a shared host usually none, although
    the universe had runs. It is a superset of the caller's own universe
    predicate (``api.runs._run_universe_id``: the ``universe:<uid>`` actor,
    else the queue binding), which still runs on every row it returns.
    """
    initialize_runs_db(base_path)
    _reconcile_workspace_on_read(base_path)
    clauses: list[str] = []
    params: list[Any] = []
    if branch_def_id:
        clauses.append("branch_def_id = ?")
        params.append(branch_def_id)
    if universe_id:
        clauses.append(
            "((actor LIKE 'universe:%' AND TRIM(SUBSTR(actor, 10)) = ?)"
            " OR TRIM(COALESCE(queue_universe_id, '')) = ?)"
        )
        params.extend([universe_id, universe_id])
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM runs {where} "
            f"ORDER BY started_at DESC LIMIT ?",
            (*params, max(1, int(limit))),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def run_counts_by_branch(base_path: str | Path, *, universe_id: str) -> list[dict[str, Any]]:
    """Per workflow of ``universe_id``: how many runs, how many completed, failed
    and running, the last activity time and the newest run's status (the live
    view's project progress). Counted in SQL over every run, never a page."""
    initialize_runs_db(base_path)
    scope = ("((actor LIKE 'universe:%' AND TRIM(SUBSTR(actor, 10)) = ?)"
             " OR TRIM(COALESCE(queue_universe_id, '')) = ?)")
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT branch_def_id, COUNT(*), "
            "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status IN ('queued', 'running', 'resumed') THEN 1 ELSE 0 END), "
            "MAX(COALESCE(finished_at, started_at)) "
            f"FROM runs WHERE {scope} AND COALESCE(branch_def_id, '') != '' "
            "GROUP BY branch_def_id ORDER BY 6 DESC",
            (universe_id, universe_id),
        ).fetchall()
        out = []
        for row in rows:
            last = conn.execute(
                f"SELECT status FROM runs WHERE branch_def_id = ? AND {scope} "
                "ORDER BY started_at DESC LIMIT 1", (row[0], universe_id, universe_id),
            ).fetchone()
            out.append({"branch_def_id": row[0], "total": int(row[1] or 0),
                        "completed": int(row[2] or 0), "failed": int(row[3] or 0),
                        "running": int(row[4] or 0), "last_at": row[5],
                        "last_status": last[0] if last else ""})
    return out


def latest_run_by_name(
    base_path: str | Path,
    *,
    run_name: str,
    branch_def_id: str = "",
) -> dict[str, Any] | None:
    """Return the newest run with ``run_name``.

    Daemon BranchTasks use deterministic run names. Looking them up lets
    restart recovery distinguish "task was requeued after a crash" from
    "the branch never produced a durable run".
    """
    initialize_runs_db(base_path)
    clauses = ["run_name = ?"]
    params: list[Any] = [run_name]
    if branch_def_id:
        clauses.append("branch_def_id = ?")
        params.append(branch_def_id)
    where = " AND ".join(clauses)
    with _connect(base_path) as conn:
        row = conn.execute(
            f"""
            SELECT * FROM runs
            WHERE {where}
            ORDER BY started_at DESC LIMIT 1
            """,
            params,
        ).fetchone()
    return _row_to_run(row) if row is not None else None


# Single source of truth for the liveness lookup's query text, so the
# EXPLAIN QUERY PLAN test that asserts it uses idx_runs_scope_status_finished
# cannot silently drift from the real query the way it did in an earlier
# round (Codex ADAPT round 3, 2026-08-29, founder note: the committed
# EXPLAIN test kept explaining the OLD `status != ?` predicate after the
# predicate itself moved on). The test imports and reuses this constant
# rather than retyping the SQL.
LATEST_RUN_ACTIVITY_SQL = (
    "SELECT MAX(COALESCE(finished_at, started_at)) AS ts "
    "FROM runs WHERE queue_universe_id = ? AND status IN (?, ?)"
)


def latest_run_activity_for_universe(
    base_path: str | Path,
    *,
    universe_id: str,
) -> float | None:
    """Newest epoch-seconds activity (finished_at, else started_at) for
    runs that actually EXECUTED under ``universe_id``.

    Used by universe liveness telemetry (``_last_activity_at`` in
    ``tinyassets.api.universe``) to attribute activity to automation and
    schedule runs, which record here but never touch the retired fleet
    daemon loop's heartbeat files (``activity.log``,
    ``.runtime_status.json``).

    Scoped by ``queue_universe_id`` rather than ``actor``: the two are
    independent columns on ``runs`` with no DB-level equality invariant
    (:func:`create_run` accepts them as separate arguments), so a row
    could in principle carry one universe's ``actor`` and another's
    ``queue_universe_id``. ``queue_universe_id`` is the authoritative
    execution scope -- every universe-run entry point
    (``enqueue_universe_branch_run``, the automation attempt runner, the
    interactive ``run_branch`` MCP action) passes ``_enqueue_universe_id``
    through :func:`_execute_branch_core` to it -- so this scopes on that
    alone rather than adding an ``actor`` OR-predicate (Codex ADAPT,
    2026-08-29, reproduced the actor/queue_universe_id-mismatch leak this
    closes).

    POSITIVE allowlist -- counts ONLY ``status IN ('running', 'completed')``
    -- rather than a denylist of non-executing statuses. A denylist kept
    finding new holes across three review rounds (Codex ADAPT round 3,
    2026-08-29): a run refused at provider admission transitions
    ``queued -> failed`` in :func:`_execute_branch_core` / :func:`resume_run`
    with error text ``"Provider authority admission failed: ..."`` BEFORE
    the worker ever submits the graph for execution, so ``failed`` alone
    does not mean the run ran -- and the uptime canary was reading that
    fresh ``failed`` timestamp as real work. ``cancelled`` has the same
    ambiguity: a still-``queued`` run can be cancelled before ever running.
    ``resumed`` is excluded too, deliberately: :func:`resume_run` sets it
    AFTER provider admission succeeds but BEFORE :func:`_invoke_graph_resume`
    compiles the branch and flips the row to ``running`` -- a crash in that
    narrow window leaves the row permanently ``resumed``
    (:func:`recover_in_flight_runs` only sweeps ``queued``/``running``, not
    ``resumed``), so it is not proven to reliably imply the worker actually
    ran a node the way ``running`` does.

    This means a universe whose every run is refused at admission,
    cancelled before starting, or dies before actually executing reads as
    having NO activity -- which is exactly what the uptime canary needs: it
    exists to detect when nothing real is happening, and crediting an
    admission-refused ``failed`` row as activity was reporting the
    opposite.

    The schema has no execution-start (dequeue) timestamp or transition
    history to do better than this: ``started_at`` is stamped at
    row-creation time regardless of status (:func:`create_run`), and
    ``run_events`` pending rows are written synchronously before worker
    submission, so neither is authoritative about whether a worker actually
    ran a node. Counting genuinely-executed ``failed``/``cancelled``/
    ``interrupted``/``resumed`` runs precisely would need an authoritative
    ``execution_started_at``/``dequeued_at`` field or a transition ledger --
    not present today.

    Read-only: opens the DB with SQLite ``mode=ro`` and a short (2s) busy
    timeout, and does NOT call ``initialize_runs_db`` -- a universe that
    has never run should not gain a ``.runs.db``, and this is a liveness
    *read* on a public MCP surface that must never block behind, or wait
    long on, a writer holding the DB (Codex ADAPT: the shared read/write
    ``_connect`` used a 30s busy timeout and ran ``PRAGMA
    journal_mode=WAL`` -- a write -- on every read). A locked or missing
    DB, a malformed stored value (``float()`` runs inside the same try as
    the query -- Codex ADAPT round 2), or any other lookup error, degrades
    to ``None`` (no signal) rather than raising or blocking, matching the
    read-only fail-soft contract of ``conversation_store.load_recent_readonly``.
    """
    uid = (universe_id or "").strip()
    if not uid:
        return None
    db_path = runs_db_path(base_path)
    if not db_path.is_file():
        return None
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        row = conn.execute(
            LATEST_RUN_ACTIVITY_SQL,
            (uid, RUN_STATUS_RUNNING, RUN_STATUS_COMPLETED),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        if conn is not None:
            conn.close()


def get_run_by_branch_task_id(
    base_path: str | Path,
    *,
    branch_task_id: str,
) -> dict[str, Any] | None:
    """Return the one run atomically reserved by a queue BranchTask."""
    initialize_runs_db(base_path)
    clean_task_id = str(branch_task_id or "").strip()
    if not clean_task_id:
        return None
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE branch_task_id = ? LIMIT 1",
            (clean_task_id,),
        ).fetchone()
    return _row_to_run(row) if row is not None else None


def record_event(
    base_path: str | Path, event: RunStepEvent,
) -> None:
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_events (
                run_id, step_index, node_id, status,
                started_at, finished_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.run_id, event.step_index, event.node_id,
                event.status, event.started_at, event.finished_at,
                json.dumps(event.detail, default=str),
            ),
        )


def list_events(
    base_path: str | Path,
    run_id: str,
    *,
    since_step: int = -1,
) -> list[dict[str, Any]]:
    """Return events with ``step_index > since_step``, ascending.

    ``step_index`` is an opaque, monotonically-increasing cursor — NOT
    a node-count ordinal. One node can emit multiple events (started,
    ran, timeout, etc.) each with its own step_index, so cursor
    arithmetic ("I have N events, skip to step N") is incorrect.
    Always pass the last-seen step_index back as ``since_step``.
    """
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM run_events
            WHERE run_id = ? AND step_index > ?
            ORDER BY step_index ASC
            """,
            (run_id, int(since_step)),
        ).fetchall()
    return [_row_to_event(r) for r in rows]


# Terminal run statuses (_TERMINAL_STATUSES, defined with the status
# constants) end a long-poll immediately regardless of whether new events
# have landed. Callers don't need to wait the full max_wait_s once the run
# has resolved.


def await_run_events(
    base_path: str | Path,
    run_id: str,
    *,
    since_step: int = -1,
    max_wait_s: float = 60.0,
    poll_interval_s: float = 0.25,
) -> dict[str, Any]:
    """Long-poll for new run events. Block up to ``max_wait_s`` (#65).

    Returns as soon as any of:
    - a new event lands with ``step_index > since_step``
    - the run reaches a terminal status (completed/failed/cancelled)
    - the deadline elapses

    Returns ``{"events": [...], "status": "...", "next_cursor": N,
    "waited_s": float, "reason": "events|terminal|timeout"}``. The
    caller uses ``next_cursor`` as the next ``since_step``.

    ``step_index`` (and therefore ``next_cursor``) is an opaque,
    monotonically-increasing cursor — NOT a node-count ordinal. A
    single node may emit several events, each with its own
    step_index, so do not treat it as "number of nodes completed".
    """
    deadline = time.monotonic() + max(0.0, float(max_wait_s))
    poll_interval = max(0.05, float(poll_interval_s))
    started = time.monotonic()
    while True:
        events = list_events(base_path, run_id, since_step=since_step)
        record = get_run(base_path, run_id)
        status = (record or {}).get("status", "unknown")
        if events:
            reason = "events"
            break
        if status in _TERMINAL_STATUSES:
            reason = "terminal"
            break
        if time.monotonic() >= deadline:
            reason = "timeout"
            break
        time.sleep(poll_interval)

    next_cursor = max(
        (e.get("step_index", since_step) for e in events),
        default=since_step,
    )
    return {
        "events": events,
        "status": status,
        "next_cursor": next_cursor,
        "waited_s": round(time.monotonic() - started, 3),
        "reason": reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: judgments, lineage, node edit audit
# ─────────────────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def add_judgment(
    base_path: str | Path,
    *,
    run_id: str,
    text: str,
    node_id: str | None = None,
    tags: list[str] | None = None,
    author: str = "",
) -> dict[str, Any]:
    """Persist a user's natural-language judgment of a run or node.

    Returns the stored dict (useful for response composition).
    """
    initialize_runs_db(base_path)
    judgment_id = uuid.uuid4().hex[:16]
    ts = _iso_now()
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO run_judgments (
                judgment_id, run_id, node_id, text,
                tags_json, author, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                judgment_id, run_id, node_id, text,
                json.dumps(list(tags or []), default=str),
                author, ts,
            ),
        )
    return {
        "judgment_id": judgment_id,
        "run_id": run_id,
        "node_id": node_id,
        "text": text,
        "tags": list(tags or []),
        "author": author,
        "timestamp": ts,
    }


def list_judgments(
    base_path: str | Path,
    *,
    branch_def_id: str = "",
    run_id: str = "",
    node_id: str = "",
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Return judgments filtered by branch / run / node. At least one
    filter must be set to avoid accidental full-table scans — callers
    that want everything should pass a branch_def_id."""
    initialize_runs_db(base_path)
    if not (branch_def_id or run_id or node_id):
        return []

    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("j.run_id = ?")
        params.append(run_id)
    if node_id:
        clauses.append("j.node_id = ?")
        params.append(node_id)
    if branch_def_id:
        # Join through runs to scope by branch.
        clauses.append(
            "j.run_id IN (SELECT run_id FROM runs WHERE branch_def_id = ?)"
        )
        params.append(branch_def_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT j.judgment_id, j.run_id, j.node_id, j.text,
                   j.tags_json, j.author, j.timestamp
            FROM run_judgments j
            {where}
            ORDER BY j.timestamp DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for r in rows:
        try:
            tags = json.loads(r["tags_json"] or "[]")
        except json.JSONDecodeError:
            tags = []
        result.append({
            "judgment_id": r["judgment_id"],
            "run_id": r["run_id"],
            "node_id": r["node_id"],
            "text": r["text"],
            "tags": tags,
            "author": r["author"],
            "timestamp": r["timestamp"],
        })
    return result


def record_lineage(
    base_path: str | Path,
    *,
    run_id: str,
    parent_run_id: str | None,
    branch_def_id: str,
    branch_version: int,
    edits_since_parent: list[str] | None = None,
) -> None:
    """Store a lineage row at run start. ``parent_run_id`` is resolved by
    the caller (usually: most recent terminal run on the same branch by
    the same actor)."""
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO run_lineage (
                run_id, parent_run_id, branch_def_id, branch_version,
                edits_since_parent_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, parent_run_id, branch_def_id, int(branch_version),
                json.dumps(list(edits_since_parent or []), default=str),
                _iso_now(),
            ),
        )


def get_lineage(base_path: str | Path, run_id: str) -> dict[str, Any] | None:
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM run_lineage WHERE run_id = ?", (run_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        edits = json.loads(row["edits_since_parent_json"] or "[]")
    except json.JSONDecodeError:
        edits = []
    return {
        "run_id": row["run_id"],
        "parent_run_id": row["parent_run_id"],
        "branch_def_id": row["branch_def_id"],
        "branch_version": row["branch_version"],
        "edits_since_parent": edits,
        "timestamp": row["timestamp"],
    }


def latest_terminal_run(
    base_path: str | Path,
    *,
    branch_def_id: str,
    actor: str = "",
) -> str | None:
    """Find the most recent terminal run on this branch (optionally by
    actor) to use as ``parent_run_id`` for a new run."""
    initialize_runs_db(base_path)
    clauses = [
        "branch_def_id = ?",
        "status IN (?, ?, ?, ?)",
    ]
    params: list[Any] = [
        branch_def_id,
        RUN_STATUS_COMPLETED, RUN_STATUS_FAILED,
        RUN_STATUS_CANCELLED, RUN_STATUS_INTERRUPTED,
    ]
    if actor:
        clauses.append("actor = ?")
        params.append(actor)
    where = " AND ".join(clauses)
    with _connect(base_path) as conn:
        row = conn.execute(
            f"""
            SELECT run_id FROM runs
            WHERE {where}
            ORDER BY started_at DESC LIMIT 1
            """,
            params,
        ).fetchone()
    return row["run_id"] if row else None


def record_node_edit_audit(
    base_path: str | Path,
    *,
    branch_def_id: str,
    version_before: int,
    version_after: int,
    nodes_changed: list[str],
    triggered_by_judgment_id: str | None = None,
    node_before: dict[str, Any] | None = None,
    node_after: dict[str, Any] | None = None,
    edit_kind: str = "update",
) -> str:
    """Persist a NodeEditAudit row when a branch is edited.

    ``node_before`` / ``node_after`` are full serialized NodeDefinition
    dicts. Snapshotting the bodies means rollback can restore the exact
    previous state without re-synthesising it. ``edit_kind`` is either
    ``"update"`` (normal edit via update_node) or ``"rollback"`` (edit
    via rollback_node) so clients can distinguish forward-progress edits
    from rewinds. Returns the audit_id.
    """
    initialize_runs_db(base_path)
    audit_id = uuid.uuid4().hex[:16]
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO node_edit_audit (
                audit_id, branch_def_id, version_before, version_after,
                nodes_changed_json, triggered_by_judgment_id, timestamp,
                node_before_json, node_after_json, edit_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id, branch_def_id,
                int(version_before), int(version_after),
                json.dumps(list(nodes_changed), default=str),
                triggered_by_judgment_id, _iso_now(),
                json.dumps(node_before or {}, default=str),
                json.dumps(node_after or {}, default=str),
                edit_kind,
            ),
        )
    return audit_id


def _audit_row_to_dict(r: sqlite3.Row) -> dict[str, Any]:
    try:
        changed = json.loads(r["nodes_changed_json"] or "[]")
    except json.JSONDecodeError:
        changed = []
    try:
        before = json.loads(r["node_before_json"] or "{}")
    except json.JSONDecodeError:
        before = {}
    try:
        after = json.loads(r["node_after_json"] or "{}")
    except json.JSONDecodeError:
        after = {}
    return {
        "audit_id": r["audit_id"],
        "branch_def_id": r["branch_def_id"],
        "version_before": r["version_before"],
        "version_after": r["version_after"],
        "nodes_changed": changed,
        "triggered_by_judgment_id": r["triggered_by_judgment_id"],
        "timestamp": r["timestamp"],
        "node_before": before,
        "node_after": after,
        "edit_kind": (
            r["edit_kind"] if "edit_kind" in r.keys() else "update"
        ),
    }


def list_node_edit_audits(
    base_path: str | Path,
    *,
    branch_def_id: str,
    node_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return audit rows for a branch, optionally narrowed to a single
    node. Rows are sorted newest-first. The ``node_id`` filter uses JSON
    containment against ``nodes_changed_json`` (``update_node`` writes
    single-element lists, so equality is the common case)."""
    initialize_runs_db(base_path)
    clauses: list[str] = ["branch_def_id = ?"]
    params: list[Any] = [branch_def_id]
    if node_id:
        # nodes_changed_json stores a JSON list. Exact-match tests for
        # a single-element list as well as containment for multi-node
        # edits (future when patch_branch learns to emit audits).
        clauses.append(
            "(nodes_changed_json = ? OR nodes_changed_json LIKE ?)"
        )
        params.append(json.dumps([node_id]))
        params.append(f'%"{node_id}"%')
    where = " AND ".join(clauses)
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM node_edit_audit
            WHERE {where}
            ORDER BY timestamp DESC LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
    return [_audit_row_to_dict(r) for r in rows]


def find_node_snapshot(
    base_path: str | Path,
    *,
    branch_def_id: str,
    node_id: str,
    at_version: int,
) -> dict[str, Any] | None:
    """Locate the node body as it existed at a specific branch version.

    Strategy: the audit row whose ``version_after`` equals ``at_version``
    captures the node's ``node_after`` — that's the body at that version.
    When no row matches (e.g. the target is version 1, never edited), we
    fall back to the oldest audit row's ``node_before``.
    """
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        exact = conn.execute(
            """
            SELECT * FROM node_edit_audit
            WHERE branch_def_id = ?
              AND version_after = ?
              AND (nodes_changed_json = ? OR nodes_changed_json LIKE ?)
            ORDER BY timestamp DESC LIMIT 1
            """,
            (
                branch_def_id, int(at_version),
                json.dumps([node_id]), f'%"{node_id}"%',
            ),
        ).fetchone()
        if exact is not None:
            return _audit_row_to_dict(exact).get("node_after") or None

        oldest = conn.execute(
            """
            SELECT * FROM node_edit_audit
            WHERE branch_def_id = ?
              AND version_before = ?
              AND (nodes_changed_json = ? OR nodes_changed_json LIKE ?)
            ORDER BY timestamp ASC LIMIT 1
            """,
            (
                branch_def_id, int(at_version),
                json.dumps([node_id]), f'%"{node_id}"%',
            ),
        ).fetchone()
        if oldest is not None:
            return _audit_row_to_dict(oldest).get("node_before") or None
    return None


def node_output_from_run(
    base_path: str | Path,
    *,
    run_id: str,
    node_id: str,
) -> dict[str, Any] | None:
    """Return the output snapshot event for a specific (run_id, node_id).

    Phase 4 judgments target specific nodes, so users need the per-node
    output to judge on, not just final state.
    """
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM run_events
            WHERE run_id = ? AND node_id = ? AND status = ?
            ORDER BY step_index DESC LIMIT 1
            """,
            (run_id, node_id, NODE_STATUS_RAN),
        ).fetchone()
    if row is None:
        return None
    detail_raw = row["detail_json"] or "{}"
    try:
        detail = json.loads(detail_raw)
    except json.JSONDecodeError:
        detail = {}
    return {
        "run_id": run_id,
        "node_id": node_id,
        "step_index": row["step_index"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "detail": detail,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Cooperative cancel
# ─────────────────────────────────────────────────────────────────────────────


def request_cancel(base_path: str | Path, run_id: str) -> bool:
    initialize_runs_db(base_path)
    from tinyassets.workspace_family import _active, close_in_transaction, run_transaction

    with run_transaction(base_path, run_id) as (conn, member, fence):
        if member is not None and member.run_id == member.root_run_id:
            if _active(conn, member.root_run_id, member.epoch):
                # A completed root can still have live children. Preserve its
                # public result; root closing is the durable family stop intent.
                close_in_transaction(conn, fence, member.epoch, "cancelled")
                conn.execute(
                    "INSERT OR IGNORE INTO run_cancels (run_id, requested_at) "
                    "SELECT run_id, ? FROM runs WHERE workspace_budget_root_run_id=? "
                    "AND workspace_budget_epoch=? "
                    "AND status NOT IN ('completed','failed','cancelled','interrupted')",
                    (_now(), member.root_run_id, member.epoch),
                )
                return True
        cursor = conn.execute(
            "INSERT OR IGNORE INTO run_cancels (run_id, requested_at) "
            "SELECT run_id, ? FROM runs WHERE run_id = ? "
            "AND status NOT IN ('completed', 'failed', 'cancelled', 'interrupted')",
            (_now(), run_id),
        )
        accepted = cursor.rowcount > 0 or conn.execute(
            "SELECT 1 FROM run_cancels WHERE run_id = ?", (run_id,),
        ).fetchone() is not None
    if accepted:
        # A run waiting for the workspace has no worker to see the cancel.
        try:
            _settle_cancelled_waiter(base_path, run_id)
        except Exception:  # noqa: BLE001 - the intent is recorded; nomination settles it
            logger.exception("settling cancelled waiting run %s failed", run_id)
    return accepted


def is_cancel_requested(base_path: str | Path, run_id: str) -> bool:
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM run_cancels WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is not None:
            return True
        member = conn.execute(
            "SELECT workspace_budget_root_run_id,workspace_budget_epoch,status "
            "FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if member is None or member[0] is None:
            return False
        if member[2] in _TERMINAL_STATUSES:
            return False  # closure is not a claim that historical output was cancelled
        root = conn.execute(
            "SELECT workspace_budget_epoch,workspace_budget_closing_reason FROM runs WHERE run_id=?",
            (member[0],),
        ).fetchone()
        # A retired/unknown family must stop, never silently continue unguarded.
        return root is None or root[0] != member[1] or root[1] != ""


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous runner
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ChildFailure:
    """Structured failure info for a sub-branch invocation that didn't complete.

    Phase A item 5 / Task #76b. Embedded in :class:`RunOutcome.child_failures`
    when a parent run's invoke_branch / invoke_branch_version step encounters
    a non-completed child terminal status. The downstream graph (and Task #48
    contribution ledger's ``caused_regression`` emit) reads these to decide
    whether to propagate, default, or retry — see ``on_child_fail`` policy
    in the spec.
    """

    run_id: str
    failure_class: str  # 'child_failed' | 'child_timeout' | 'child_cancelled' | 'child_unknown'
    child_status: str  # the child's terminal RUN_STATUS_*
    partial_output: dict[str, Any] | None = None


class ChildRunAwaitTimeout(TimeoutError):
    """Raised when an awaited child run is still non-terminal at the ceiling."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        child_status: str,
        child_branch_def_id: str,
        timeout_seconds: float,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.child_status = child_status
        self.child_branch_def_id = child_branch_def_id
        self.timeout_seconds = timeout_seconds


@dataclass
class RunOutcome:
    run_id: str
    status: str
    output: dict[str, Any]
    error: str = ""
    # Phase A item 5 / Task #76b — populated when a parent run's
    # invoke_branch / invoke_branch_version step sees a non-completed child
    # terminal status. Default empty list keeps existing callers untouched
    # (no behavior change for runs without sub-branch invocations).
    child_failures: list[ChildFailure] = field(default_factory=list)


class MissingRequiredInputs(ValueError):
    """A Branch run lacks state that no guaranteed predecessor produces.

    Raised before :func:`_prepare_run` so callers can return an actionable
    refusal without minting a run id or reserving any execution resource.
    """

    failure_class = "missing_required_inputs"
    actionable_by = "chatbot"

    def __init__(
        self,
        missing_input_keys: list[str],
        input_guidance: dict[str, dict[str, Any]],
    ) -> None:
        self.missing_input_keys = sorted(set(missing_input_keys))
        self.input_guidance = {
            key: input_guidance[key]
            for key in self.missing_input_keys
        }
        joined = ", ".join(self.missing_input_keys)
        self.suggested_action = (
            "Retry with inputs_json containing the missing keys: " + joined
        )
        super().__init__(f"Required workflow inputs are missing: {joined}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "failure_class": self.failure_class,
            "missing_input_keys": self.missing_input_keys,
            "input_guidance": self.input_guidance,
            "suggested_action": self.suggested_action,
            "actionable_by": self.actionable_by,
        }


def _input_example(field_type: str) -> Any:
    normalized = (field_type or "any").strip().lower()
    if normalized in {"str", "string", "text"}:
        return ""
    if normalized in {"int", "integer", "float", "number"}:
        return 0
    if normalized in {"bool", "boolean"}:
        return False
    if normalized in {"list", "array"}:
        return []
    if normalized in {"dict", "object", "map"}:
        return {}
    return None


def _missing_input_guidance(
    branch: BranchDefinition,
    missing: set[str],
) -> dict[str, dict[str, Any]]:
    schema = {
        str(field.get("name") or "").strip(): field
        for field in branch.state_schema or []
        if isinstance(field, dict) and str(field.get("name") or "").strip()
    }
    guidance: dict[str, dict[str, Any]] = {}
    for key in sorted(missing):
        field = schema.get(key, {})
        field_type = str(field.get("type") or "any").strip() or "any"
        item: dict[str, Any] = {
            "type": field_type,
            "example": _input_example(field_type),
        }
        description = str(field.get("description") or "").strip()
        if description:
            item["description"] = description
        guidance[key] = item
    return guidance


def _straight_line_code_state_inputs(source: str) -> set[str]:
    """Return literal state lookups in the unguarded prefix of ``run``.

    This deliberately under-approximates requiredness. Lookups inside control
    flow, comprehensions, or lambdas may be protected by user code, so runtime
    remains authoritative for them.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    run_def = next(
        (
            item
            for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "run"
        ),
        None,
    )
    if run_def is None:
        return set()

    class _StrictAccessVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.keys: set[str] = set()

        def visit_Subscript(self, item: ast.Subscript) -> None:
            if (
                isinstance(item.value, ast.Name)
                and item.value.id == "state"
                and isinstance(item.ctx, ast.Load)
                and isinstance(item.slice, ast.Constant)
                and isinstance(item.slice.value, str)
            ):
                self.keys.add(item.slice.value)
            self.generic_visit(item)

        def visit_Call(self, item: ast.Call) -> None:
            if (
                isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == "state"
                and item.func.attr == "pop"
                and len(item.args) == 1
                and isinstance(item.args[0], ast.Constant)
                and isinstance(item.args[0].value, str)
            ):
                self.keys.add(item.args[0].value)
            self.generic_visit(item)

        def visit_BoolOp(self, _item: ast.BoolOp) -> None:
            return

        def visit_IfExp(self, _item: ast.IfExp) -> None:
            return

        def visit_Lambda(self, _item: ast.Lambda) -> None:
            return

        def visit_ListComp(self, _item: ast.ListComp) -> None:
            return

        def visit_SetComp(self, _item: ast.SetComp) -> None:
            return

        def visit_DictComp(self, _item: ast.DictComp) -> None:
            return

        def visit_GeneratorExp(self, _item: ast.GeneratorExp) -> None:
            return

        def visit_FunctionDef(self, _item: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, _item: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, _item: ast.ClassDef) -> None:
            return

    visitor = _StrictAccessVisitor()
    compound: tuple[type[ast.AST], ...] = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.With,
        ast.AsyncWith,
        ast.Match,
    )
    try_star = getattr(ast, "TryStar", None)
    if try_star is not None:
        compound = (*compound, try_star)
    for statement in run_def.body:
        if isinstance(statement, compound):
            break
        visitor.visit(statement)
        if isinstance(statement, (ast.Return, ast.Raise)):
            break
    return visitor.keys


def _required_node_inputs(node: Any) -> set[str]:
    """Return only inputs whose absence is statically certain to fail.

    ``input_keys`` is an access allowlist, not a requiredness declaration.
    Prompt placeholders always index state during rendering. Unguarded literal
    lookups in the straight-line prefix of a code node's ``run`` function also
    fail on absence. Guarded, dynamic, or otherwise conditional code access is
    left to runtime so preflight cannot reject a branch that handles absence.
    """
    required = set(_placeholder_keys(str(node.prompt_template or "")))
    source = str(node.source_code or "")
    if source:
        required.update(_straight_line_code_state_inputs(source))
    await_spec = node.await_run_spec if isinstance(node.await_run_spec, dict) else {}
    run_id_field = str(await_spec.get("run_id_field") or "").strip()
    if run_id_field:
        required.add(run_id_field)
    return required


def preflight_required_inputs(
    branch: BranchDefinition,
    inputs: dict[str, Any],
) -> None:
    """Refuse inputs missing on any possible graph-superstep execution.

    LangGraph ordinary fan-out executes every child in one superstep and merges
    their outputs at the barrier. Conditional fan-out chooses one target. We
    therefore explore bounded active-node frontiers rather than treating every
    incoming edge as an alternative path; that preserves parallel fan-in while
    still refusing a key absent on one conditional route. Availability is
    monotonic across supersteps, so each key needs only a boolean state.
    """
    node_ids = {ref.id for ref in branch.graph_nodes}
    if not node_ids:
        return

    node_defs = {node.node_id: node for node in branch.node_defs}
    defs_by_graph_id = {
        ref.id: node_defs.get(ref.node_def_id or ref.id)
        for ref in branch.graph_nodes
    }

    ordinary_children: dict[str, set[str]] = {
        node_id: set() for node_id in node_ids
    }
    conditional_options: dict[str, list[tuple[str, ...]]] = {
        node_id: [] for node_id in node_ids
    }
    start_targets = {branch.entry_point} if branch.entry_point in node_ids else set()

    def add_ordinary_route(source: str, target: str) -> None:
        if target not in node_ids:
            return
        if source == "START":
            start_targets.add(target)
        elif source in node_ids:
            ordinary_children[source].add(target)

    for edge in branch.edges:
        add_ordinary_route(edge.from_node, edge.to_node)
    for conditional in branch.conditional_edges:
        if conditional.from_node not in node_ids:
            continue
        options = tuple(sorted({
            target
            for target in conditional.conditions.values()
            if target in node_ids
        }))
        if options:
            conditional_options[conditional.from_node].append(options)

    initial = set(seed_initial_state(inputs, branch.state_schema).keys())
    produced: dict[str, set[str]] = {}
    required: dict[str, set[str]] = {}
    for node_id in node_ids:
        node = defs_by_graph_id.get(node_id)
        produced[node_id] = set(node.output_keys if node is not None else ())
        required[node_id] = _required_node_inputs(node) if node is not None else set()

    max_frontiers = 4096

    def next_frontiers(active: frozenset[str]) -> set[frozenset[str]] | None:
        base = {
            child
            for node_id in active
            for child in ordinary_children[node_id]
        }
        frontiers: set[frozenset[str]] = {frozenset(base)}
        for node_id in sorted(active):
            for options in conditional_options[node_id]:
                expanded: set[frozenset[str]] = set()
                for frontier in frontiers:
                    for option in options:
                        expanded.add(frontier | {option})
                        if len(expanded) > max_frontiers:
                            return None
                frontiers = expanded
        return frontiers

    def can_reach_consumer_without(key: str) -> bool:
        work: list[tuple[frozenset[str], bool]] = [
            (frozenset(start_targets), key in initial)
        ]
        seen: set[tuple[frozenset[str], bool]] = set()
        while work:
            active, available = work.pop()
            state = (active, available)
            if state in seen:
                continue
            seen.add(state)
            if len(seen) > max_frontiers:
                return True
            if not available and any(key in required[node_id] for node_id in active):
                return True
            available_after = available or any(
                key in produced[node_id] for node_id in active
            )
            following = next_frontiers(active)
            if following is None:
                return True
            work.extend(
                (frontier, available_after)
                for frontier in following
                if frontier
            )
        return False

    candidate_keys = {
        key
        for node_required in required.values()
        for key in node_required
        if key not in initial
    }
    missing = {
        key for key in candidate_keys if can_reach_consumer_without(key)
    }
    if missing:
        raise MissingRequiredInputs(
            sorted(missing),
            _missing_input_guidance(branch, missing),
        )


def _graph_node_order(branch: BranchDefinition) -> list[str]:
    return [gn.id for gn in branch.graph_nodes]


def _reserved_run_admission_identity(
    base_path: str | Path, run_id: str,
) -> tuple[str, str | None]:
    """The reserved run's own ``(run_name, branch_version_id)``.

    Internal seam for the two executing paths that dispatch a run reserved by
    an earlier durable intent (receiver delivery, admitted run input). They do
    not hold the values the original reservation wrote, and the envelope's
    binding check compares against this row — so the envelope must carry the
    row's own identity, not a freshly invented one.
    """
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT run_name, branch_version_id FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise RunExecutionAuthorityLost(
            f"Reserved run {run_id!r} vanished before admission capture."
        )
    return str(row[0] or ""), (str(row[1]) if row[1] else None)


def _settle_failed_admission_envelope(
    base_path: str | Path, exc: Exception,
) -> "RunOutcome | None":
    """Settle a run reserved before its admission envelope failed to persist.

    Returns a terminal FAILED :class:`RunOutcome` when the exception names a
    reserved run, and ``None`` when it does not -- an encoding failure is
    raised BEFORE any row exists, so there is nothing to terminalize and the
    caller re-raises. Shared by every admission seam so a capture failure can
    never leave a queued row whose graph nobody will run.
    """
    reserved = str(getattr(exc, "run_id", "") or "")
    message = f"Admission envelope capture failed: {exc}"
    if not reserved:
        return None
    with _managed_execution_scope(base_path, reserved) as guard:
        if guard is not None:
            settled = terminalize_unstarted_run(
                base_path, run_id=reserved, execution_guard=guard,
                status=RUN_STATUS_FAILED, error=message,
            )
        else:
            # No execution guard is available, so this write is pinned to the
            # QUEUED row it reserved. A run cancelled (or otherwise settled)
            # between reservation and this failure keeps its terminal status:
            # a failed envelope capture is not evidence about a row somebody
            # else already finished.
            settled = _settle_unguarded_reserved_run(base_path, reserved, message)
    return RunOutcome(run_id=reserved, status=settled, output={}, error=message)


def _settle_unguarded_reserved_run(
    base_path: str | Path, run_id: str, message: str,
) -> str:
    """Fail a still-QUEUED reserved run; return whatever status actually holds."""
    try:
        update_run_status(
            base_path, run_id, status=RUN_STATUS_FAILED, error=message,
            finished_at=_now(), _expected_statuses={RUN_STATUS_QUEUED},
        )
    except Exception:
        logger.exception(
            "Reserved run %s was no longer queued when its admission envelope "
            "failed; its existing status stands.", run_id,
        )
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,),
        ).fetchone()
    return str(row[0]) if row is not None else RUN_STATUS_FAILED


def _prepare_run(
    base_path: str | Path,
    *,
    branch: BranchDefinition,
    inputs: dict[str, Any],
    run_name: str,
    actor: str,
    owner_user_id: str | None = None,
    branch_version_id: str | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    branch_task_id: str | None = None,
    queue_universe_id: str | None = None,
    admission_envelope: str | None = None,
    _workspace_parent=None,
) -> str:
    """Write the run row + pending-node events + lineage synchronously.

    Returns the ``run_id``. Fast (~a few ms); safe to call from the MCP
    handler before handing off to a background executor.

    ``branch_version_id`` is populated only for version-based runs
    (Phase A item 6, Task #65). Def-based runs leave it as None — its meaning
    ("the user selected this published version") and everything derived from it
    (contribution attribution, delete dependencies) are unchanged.

    ``admission_envelope`` is the private durable record of what was admitted
    (see :mod:`tinyassets.run_admission_envelope`). Supply it and the run is
    resumable on the exact admitted graph; omit it and a later resume refuses
    ``admission_not_reconstructable`` rather than substituting the current
    editable definition. A capture failure raises ``AdmissionEnvelopeError``
    carrying the reserved ``run_id`` so the caller can refuse to dispatch.
    """
    initialize_runs_db(base_path)
    run_id = create_run(
        base_path,
        branch_def_id=branch.branch_def_id,
        thread_id="",
        inputs=inputs,
        run_name=run_name,
        actor=actor,
        owner_user_id=owner_user_id,
        branch_version_id=branch_version_id,
        daemon_id=daemon_id,
        runtime_instance_id=runtime_instance_id,
        worker_id=worker_id,
        branch_task_id=branch_task_id,
        queue_universe_id=queue_universe_id,
        _workspace_parent=_workspace_parent,
    )
    _initialize_prepared_run(
        base_path, run_id=run_id, branch=branch, actor=actor,
        admission_envelope=admission_envelope,
    )
    return run_id


def _initialize_prepared_run(
    base_path: str | Path, *, run_id: str, branch: BranchDefinition, actor: str,
    admission_envelope: str | None = None,
) -> None:
    """Initialize events/lineage for a newly reserved, never-executed run.

    Internal seam for durable intents. The caller must prove execution has not
    started before invoking this helper; it does not authorize retry/resume.
    Ordinary _prepare_run calls it exactly once after creating its new run.

    ``admission_envelope`` is captured here, in the same transaction that
    claims the thread_id and therefore strictly before any executable
    submission. The write is guarded on ``IS NULL``, so a durable intent that
    re-prepares a run may re-supply the identical envelope but can never
    replace the original admitted payload. A capture failure propagates as
    ``AdmissionEnvelopeError`` — there is no fail-open path that would leave a
    dispatched run with an unprovable definition.

    **Excluded seam, stated honestly.** The parameter is optional because this
    helper serves two callers: ``_prepare_run`` (every ordinary admission,
    which always supplies one) and a durable intent re-initializing an
    ALREADY-RESERVED run. A durable intent that itself admits a run must pass
    the envelope its own freeze produced; one that only re-prepares an
    already-admitted run passes nothing and the original envelope stands. A
    run reserved through this seam with no envelope is not resumable — it
    refuses ``admission_not_reconstructable`` rather than executing a
    definition nobody can prove was admitted.
    """
    from tinyassets.run_admission_envelope import (
        AdmissionEnvelopeError,
        capture_admission_envelope,
    )

    thread_id = run_id
    # The whole claim transaction is wrapped, not just the capture call:
    # ``BEGIN IMMEDIATE`` (a locked database), the thread_id UPDATE and
    # ``commit()`` all raise bare ``sqlite3.Error``, which would escape every
    # admission seam's ``except AdmissionEnvelopeError`` and leave a reserved
    # QUEUED row that no worker will ever dispatch. The run is already
    # reserved here, so the failure carries its run_id and the caller settles
    # it. Nothing is dispatched on any of these paths.
    try:
        with _connect(base_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE runs SET thread_id = ? WHERE run_id = ?",
                (thread_id, run_id),
            )
            if admission_envelope:
                try:
                    capture_admission_envelope(
                        conn, run_id=run_id, envelope=admission_envelope,
                    )
                except Exception:
                    conn.rollback()
                    raise
            conn.commit()
    except AdmissionEnvelopeError:
        raise
    except sqlite3.Error as exc:
        raise AdmissionEnvelopeError(
            f"prepared run {run_id!r} could not claim its thread durably: {exc}",
            run_id=run_id,
        ) from exc
    for step, node_id in enumerate(_graph_node_order(branch)):
        record_event(base_path, RunStepEvent(
            run_id=run_id,
            step_index=step,
            node_id=node_id,
            status=NODE_STATUS_PENDING,
            started_at=_now(),
        ))

    # Phase 4: record lineage so `compare_runs` and "what changed since
    # the last run" work. Parent is the most recent terminal run on this
    # branch by the same actor (best-effort — falls back to branch-wide
    # latest if no same-actor match).
    parent = latest_terminal_run(
        base_path, branch_def_id=branch.branch_def_id, actor=actor,
    )
    if parent is None:
        parent = latest_terminal_run(
            base_path, branch_def_id=branch.branch_def_id,
        )
    branch_version = int(getattr(branch, "version", 1) or 1)
    edits_since_parent: list[str] = []
    if parent is not None:
        parent_lineage = get_lineage(base_path, parent)
        if parent_lineage and parent_lineage["branch_version"] != branch_version:
            # Best-effort: enumerate audit rows between the versions for
            # a summary of what changed between runs.
            try:
                audits = list_node_edit_audits(
                    base_path, branch_def_id=branch.branch_def_id, limit=100,
                )
                for a in audits:
                    if (
                        a["version_before"] >= parent_lineage["branch_version"]
                        and a["version_after"] <= branch_version
                    ):
                        edits_since_parent.extend(a.get("nodes_changed", []))
            except Exception:
                logger.exception("lineage edit summary failed for %s", run_id)
    record_lineage(
        base_path,
        run_id=run_id,
        parent_run_id=parent,
        branch_def_id=branch.branch_def_id,
        branch_version=branch_version,
        edits_since_parent=edits_since_parent,
    )


#: LangGraph needs a recursion ceiling, so there is a number here; it is not a
#: limit. 100 (raised from LangGraph's stock 25 during the Tier-1 investigation,
#: BUG-019/021/022) refused a branch whose author wrote a longer loop, with
#: "Branch loop may be too deep" -- a structural cap on what someone may build,
#: and an account has exactly two limits, cloud bytes and concurrent agent seats
#: (founder, 2026-09-30).
#:
#: One million is past any graph a person writes and far below Python's own
#: limits on the structures LangGraph builds per step. What actually bounds an
#: endless loop is the run's SEAT: it holds one for as long as it runs, and its
#: owner can stop it. An author may still pass any positive
#: `recursion_limit_override` -- including a smaller one, as their own guard.
DEFAULT_RECURSION_LIMIT = 1_000_000


def _family_status_writer(member):
    """Capture execution incarnation; a late worker cannot adopt resume's epoch."""
    from functools import partial

    return partial(update_run_status, _workspace_member=member)


_RUN_EXECUTION_GUARD = contextvars.ContextVar("tinyassets_run_execution_guard", default=None)
_RUN_EXECUTION_USE = contextvars.ContextVar("tinyassets_run_execution_use", default=None)


@contextlib.contextmanager
def _execution_use_scope():
    """Pin this actual node/RPC operation, not a serialized run permission."""
    use = _RUN_EXECUTION_USE.get()
    if use is None:
        yield None
        return
    with _connect(use.database_path.parent) as conn:
        with use.hold(conn):
            yield use


@contextlib.contextmanager
def _managed_execution_scope(base_path: str | Path, run_id: str, *, provided=None):
    from tinyassets.storage.run_execution_lock import RunExecutionGuard, try_run_execution_lock

    current = _RUN_EXECUTION_GUARD.get()
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT workspace_budget_root_run_id,workspace_budget_epoch,"
            "workspace_budget_closing_reason FROM runs WHERE run_id=?", (run_id,),
        ).fetchone()
        managed = row is not None and any(value is not None for value in row)
        candidate = provided
        if candidate is None and current is not None and (
                current.run_id == run_id and current.database_path == runs_db_path(base_path).resolve()):
            candidate = current
        if candidate is not None:
            if type(candidate) is not RunExecutionGuard or candidate.run_id != run_id:
                raise RunExecutionAuthorityLost("Execution guard belongs to another run.")
            candidate.require_held(conn)
    scope = (contextlib.nullcontext(candidate) if candidate is not None or not managed
             else try_run_execution_lock(base_path, run_id=run_id))
    with scope as guard:
        if managed and guard is None:
            # Outside invocation's failure handler: never rewrite the actual
            # owner's live run just because a second dispatcher lost exclusion.
            raise RunExecutionAuthorityLost("Another worker owns this run's execution.")
        use = None
        if guard is not None:
            current_use = _RUN_EXECUTION_USE.get()
            if current_use is not None and current_use._guard is guard:
                use = current_use
            else:
                with _connect(base_path) as conn:
                    use = guard.issue_use(conn)
        token = _RUN_EXECUTION_GUARD.set(guard)
        use_token = _RUN_EXECUTION_USE.set(use)
        try:
            yield guard
        finally:
            _RUN_EXECUTION_USE.reset(use_token)
            _RUN_EXECUTION_GUARD.reset(token)


def _record_seat_wait(base_path, run_id: str, step_index: int, node_id: str,
                      detail: dict) -> None:
    """A node is waiting for its account's seat (`universe_seats`).

    A system row, never a node status: the node has not run, so it must not read
    as ``ran``. It carries the owner's waiting line, upgrade link included. A run
    cancelled while its node waits stops waiting here: the cancellation
    propagates out of the seat wait, which gives the queue position back.
    """
    record_event(base_path, RunStepEvent(
        run_id=run_id,
        step_index=step_index,
        node_id=SYSTEM_EVENT_NODE_ID,
        status="waiting_for_seat",
        started_at=_now(),
        finished_at=_now(),
        detail={"node_id": node_id, **detail},
    ))
    if is_cancel_requested(base_path, run_id):
        raise RunCancelledError(f"Run {run_id} cancelled while waiting for a seat.")


def _owns_managed_execution(function):
    from functools import wraps

    @wraps(function)
    def invoke(base_path, *, run_id, **kwargs):
        provided = kwargs.pop("_execution_guard", None)
        with _managed_execution_scope(base_path, run_id, provided=provided) as guard:
            from tinyassets.workspace_family import FamilyRefused

            try:
                from tinyassets.outside_authority import run_identity

                with run_identity(base_path, run_id):
                    return function(base_path, run_id=run_id, **kwargs)
            except FamilyRefused as exc:
                if guard is None:
                    raise
                actual = terminalize_unstarted_run(
                    base_path, run_id=run_id, execution_guard=guard,
                    status=RUN_STATUS_FAILED, error=f"Execution authority refused: {exc}",
                )
                # Running/terminal state is preserved; no late failure writer
                # may overwrite another incarnation or claim rollback of effects.
                return RunOutcome(run_id=run_id, status=actual, output={}, error=str(exc))

    return invoke


def _existing_run_outcome(base_path, run_id, error):
    with _connect(base_path) as conn:
        row = conn.execute("SELECT status,output_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return RunOutcome(run_id=run_id, status=row[0] if row else RUN_STATUS_FAILED,
                      output=json.loads(row[1] or "{}") if row else {}, error=error)


def _require_managed_start_status(base_path, run_id, expected_status):
    guard = _RUN_EXECUTION_GUARD.get()
    if guard is None:
        return
    with _connect(base_path) as conn:
        guard.require_held(conn)
        row = conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row[0] != expected_status:
            raise RunExecutionAuthorityLost("Execution lost its expected start status; no replay.")


@_owns_managed_execution
def _invoke_graph(
    base_path: str | Path,
    *,
    run_id: str,
    branch: BranchDefinition,
    inputs: dict[str, Any],
    provider_call: Callable[..., str] | None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    concurrency_budget_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    invocation_depth: int = 0,
    enqueue_context: "NodeEnqueueContext | None" = None,
    _execution_guard=None,
) -> RunOutcome:
    """Compile + invoke the graph for an already-prepared run_id.

    Blocks until the graph finishes or is cancelled. Updates run status
    to RUNNING on entry, COMPLETED / FAILED / CANCELLED on exit.
    """
    _require_managed_start_status(base_path, run_id, RUN_STATUS_QUEUED)
    thread_id = run_id
    execution_cursor = {"step": 0}
    # Telemetry accumulator: "last" feeds runs.provider_used (legacy
    # last-wins), "model" feeds the runs.model column, "calls" becomes a
    # per-run ``provider_calls`` system event (one entry per provider-served
    # node: provider, model, latency, attempts).
    provider_tracker: dict[str, Any] = {"last": None, "model": None, "calls": []}
    run_identity = {
        "owner_user_id": "",
        "daemon_id": "",
        "runtime_instance_id": "",
        "worker_id": "",
    }
    run_actor = ""
    run_universe = ""
    with _connect(base_path) as conn:
        identity_row = conn.execute(
            """
            SELECT owner_user_id, daemon_id, runtime_instance_id, worker_id,
                   actor, queue_universe_id
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    if identity_row is not None:
        run_identity = {
            "owner_user_id": identity_row["owner_user_id"] or "",
            "daemon_id": identity_row["daemon_id"] or "",
            "runtime_instance_id": identity_row["runtime_instance_id"] or "",
            "worker_id": identity_row["worker_id"] or "",
        }
        run_actor = (identity_row["actor"] or "").strip()
        run_universe = (identity_row["queue_universe_id"] or "").strip()

    # Immutable per-run execution context (invoke_branch sanitization): who this run
    # executes as, in which universe, and how trusted the running definition is. Built
    # ONCE here from the authenticated run row + the branch's author — NEVER from a
    # node spec, and re-derived fresh for each (child) run so provenance is transitive.
    # provenance is "own" when the running branch was authored by the run actor, else
    # "public-foreign" (a foreign/public/remixed branch), which delegated child
    # authorization keys off. Depth carries the recursion counter.
    _branch_author = (getattr(branch, "author", "") or "").strip()
    _provenance = _caller_provenance(base_path, run_actor, run_universe, _branch_author)
    from tinyassets.workspace_family import execution_member

    execution_context = BranchExecutionContext(
        actor=run_actor,
        universe_id=run_universe,
        caller_provenance=_provenance,
        depth=invocation_depth,
        owner_user_id=run_identity["owner_user_id"],
        definition_author=_branch_author,
        workspace_family=execution_member(base_path, run_id, for_compile=True),
    )
    update_run_status = _family_status_writer(execution_context.workspace_family)

    def _emit_node_status(node_id: str, status: str) -> None:
        if on_node_status is None:
            return
        try:
            on_node_status(node_id, status)
        except (RunCancelledError, RunExecutionAuthorityLost):
            raise
        except Exception:  # noqa: BLE001
            logger.exception(
                "Run %s node-status callback failed for %s status=%s",
                run_id, node_id, status,
            )

    # Phase 2 design_used emit (Task #75) — pre-build a graph_node_id ->
    # NodeDefinition lookup so each "ran" event can credit the artifact
    # author without scanning branch.node_defs per step. Falls back to
    # node_id matching when graph_nodes is empty (legacy single-list
    # branches). Empty NodeDefinition.author skips emit at the
    # contribution-event layer (orphan-row prevention).
    _node_def_by_id: dict[str, Any] = {}
    _defs_index = {n.node_id: n for n in branch.node_defs}
    if branch.graph_nodes:
        for gn in branch.graph_nodes:
            ref_id = gn.node_def_id or gn.id
            if ref_id in _defs_index:
                _node_def_by_id[gn.id] = _defs_index[ref_id]
    else:
        _node_def_by_id = dict(_defs_index)

    def _on_node(node_id: str, **detail: Any) -> None:
        # #60: the compiler emits TWO events per node — phase="starting"
        # before the provider call and phase="ran" after. Each event gets
        # its own step_index so polling clients see node status transition
        # pending -> running -> ran, no more "frozen for 4 minutes" gaps.
        #
        # Cooperative cancel fires only on "ran" (between nodes).
        # Cancelling mid-provider-call would orphan the LLM call; the
        # node boundary is the right checkpoint.
        phase = detail.pop("phase", "ran")
        step = execution_cursor["step"]
        execution_cursor["step"] += 1

        if phase == "starting":
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=node_id,
                status=NODE_STATUS_RUNNING,
                started_at=_now(),
                detail=detail,
            ))
            _emit_node_status(node_id, NODE_STATUS_RUNNING)
            return

        if phase == "waiting":
            _record_seat_wait(base_path, run_id, step + _PENDING_OFFSET, node_id, detail)
            return

        if phase == "effect":
            # Design D1: the node's effects fired inside its step. Recorded as
            # a system row (never a node status) so per-node status stays
            # pending -> running -> ran/failed and the snapshot's phase logic,
            # which ignores __system__, is untouched.
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=SYSTEM_EVENT_NODE_ID,
                status="effect",
                started_at=_now(),
                finished_at=_now(),
                detail={"node_id": node_id, **detail},
            ))
            return

        if phase in {"failed", "cancelled"}:
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=node_id,
                status=NODE_STATUS_CANCELLED if phase == "cancelled" else NODE_STATUS_FAILED,
                started_at=_now(),
                finished_at=_now(),
                detail=detail,
            ))
            return

        served = detail.get("provider_served")
        if served:
            provider_tracker["last"] = str(served)
            model = detail.get("provider_model")
            if model:
                provider_tracker["model"] = str(model)
            provider_tracker["calls"].append({
                "node_id": node_id,
                "provider": str(served),
                "model": str(model or ""),
                "latency_ms": detail.get("provider_latency_ms"),
                "attempts": detail.get("provider_attempts"),
                "degraded": bool(detail.get("provider_degraded", False)),
                "at": _now(),
            })
        record_event(base_path, RunStepEvent(
            run_id=run_id,
            step_index=step + _PENDING_OFFSET,
            node_id=node_id,
            status=NODE_STATUS_RAN,
            started_at=_now(),
            finished_at=_now(),
            detail=detail,
        ))
        # The node completed before this cooperative checkpoint. Preserve that
        # observation even when cancellation stops the graph's next step.
        if is_cancel_requested(base_path, run_id):
            raise RunCancelledError(f"Run {run_id} cancelled between nodes.")
        _emit_node_status(node_id, NODE_STATUS_RAN)

        # Phase 2 design_used emit (Task #75) — credit the NodeDefinition's
        # author for a successful step execution. Fires only at "ran" phase
        # for real artifact-referencing nodes. System events (node_id
        # prefixed with "__") and synthetic phases never trigger. Wrapped
        # in try/except so emit failure stays decoupled from run state
        # (mirrors Task #72 discipline).
        if node_id.startswith("__"):
            return
        nd = _node_def_by_id.get(node_id)
        if nd is None:
            return
        node_def_id = getattr(nd, "node_def_id", "") or getattr(nd, "node_id", "")
        author = getattr(nd, "author", "") or ""
        if not node_def_id or not has_named_principal(author):
            return
        try:
            from tinyassets.contribution_events import record_contribution_event
            record_contribution_event(
                base_path,
                event_id=f"design_used:{run_id}:{step}:{node_def_id}",
                event_type="design_used",
                actor_id=author,
                owner_user_id=run_identity["owner_user_id"],
                daemon_id=run_identity["daemon_id"],
                runtime_instance_id=run_identity["runtime_instance_id"],
                worker_id=run_identity["worker_id"],
                source_run_id=run_id,
                source_artifact_id=node_def_id,
                source_artifact_kind="node_def",
                weight=1.0,
                occurred_at=_now(),
                metadata_json=json.dumps({
                    "step_index": step,
                    "node_def_id": node_def_id,
                    "graph_node_id": node_id,
                }),
            )
        except Exception as exc:
            from tinyassets.contribution_events import _EMIT_FAILURES
            from tinyassets.contribution_events import _logger as _ce_logger
            _EMIT_FAILURES["count"] += 1
            _ce_logger.warning(
                "design_used emit failed for run %s step %s node %s: %s; "
                "step event preserved",
                run_id, step, node_id, exc,
            )

    # Effects fire at node time (design D1, change `sandboxed-code-node`):
    # the chain is what they leave behind - full results for later nodes,
    # bounded evidence for persistence, fired verbs for the engine budget.
    # Registered under the run id so a terminal status written from any path
    # settles and persists it.
    from tinyassets.effectors import EffectChain, register_effect_chain

    _eff_universe_hint = ""
    if enqueue_context is not None and getattr(enqueue_context, "universe_id", ""):
        _eff_universe_hint = str(enqueue_context.universe_id).strip()
    effect_chain = EffectChain(
        run_id=run_id,
        base_path=_resolve_effector_base(base_path, run_id, _eff_universe_hint),
        cloud_effect_session=_claimed_cloud_effect_session(provider_call),
        invocation_depth=int(invocation_depth or 0),
        universe_id=_eff_universe_hint or run_universe,
        # The run's own model reviews a consequential action before it fires
        # (harness D1d), admitted like any agent call.
        review_provider=provider_call,
        review_active=True,
    )
    register_effect_chain(effect_chain)
    try:
        compiled = compile_branch(
            branch,
            provider_call=provider_call,
            event_sink=_on_node,
            concurrency_budget_override=concurrency_budget_override,
            effect_chain=effect_chain,
            base_path=base_path,
            parent_run_id=run_id,
            invocation_depth=invocation_depth,
            enqueue_context=enqueue_context,
            execution_context=execution_context,
            # Threaded so an invoke node can hand it to its CHILD launch;
            # the child is where a nested prompt's provider call happens.
            on_node_status=on_node_status,
            # Polled INSIDE a running node, so the owner's cancel reaches the
            # child process and kills its jail. The inter-node check below is
            # unchanged and still catches a cancel between nodes; this is the
            # one that stops a node already 20 minutes into `ws.run`.
            should_cancel=lambda: is_cancel_requested(base_path, run_id),
        )
    except CompilerError as exc:
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED,
            error=str(exc),
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error=str(exc),
        )
    except Exception as exc:
        logger.exception("Run %s failed during compile", run_id)
        msg = f"Compile failed: {type(exc).__name__}: {exc}"
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED,
            error=msg,
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error=msg,
        )

    update_run_status(base_path, run_id, status=RUN_STATUS_RUNNING)

    # Emit recursion_limit_applied event so get_run can surface the cap used.
    record_event(base_path, RunStepEvent(
        run_id=run_id,
        step_index=0,
        node_id=SYSTEM_EVENT_NODE_ID,
        status="recursion_limit_applied",
        started_at=_now(),
        detail={"recursion_limit": recursion_limit},
    ))

    if is_cancel_requested(base_path, run_id):
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_CANCELLED,
            error="Cancelled before execution started.",
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_CANCELLED,
            output={}, error="Cancelled before execution started.",
        )

    # Phase A item 5 / Task #76b — reset the threadlocal child-retry counter
    # so this parent run starts with a fresh global cap.
    from tinyassets.graph_compiler import ChildFailedError, _retry_budget_reset
    _retry_budget_reset()

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        saver_path = str(Path(base_path) / ".langgraph_runs.db")
        Path(saver_path).parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(saver_path) as checkpointer:
            app = compiled.graph.compile(checkpointer=checkpointer)
            # BUG-085 M3: seed state_schema defaults UNDER caller inputs so
            # state_schema-declared fields with defaults are available to
            # strict-isolation prompt placeholders from step 1.
            initial_state = seed_initial_state(
                dict(inputs), getattr(branch, "state_schema", None),
            )
            result = app.invoke(
                initial_state,
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
    except RunCancelledError as exc:
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_CANCELLED,
            error=str(exc),
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_CANCELLED,
            output={}, error=str(exc),
        )
    except ChildFailedError as exc:
        # Phase A item 5 / Task #76b — sub-branch propagated a non-completed
        # child terminal status. Parent run terminates with the structured
        # ChildFailure surfaced on RunOutcome.child_failures so downstream
        # observers (Task #48 contribution-ledger caused_regression emit;
        # Task #53 route-back gate verdicts) can consume the failure.
        msg = str(exc)
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED, error=msg, finished_at=_now(),
        )
        failure = exc.failure if isinstance(exc.failure, ChildFailure) else None
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error=msg,
            child_failures=[failure] if failure is not None else [],
        )
    except ChildRunAwaitTimeout as exc:
        msg = (
            f"Child invocation receipt gate timed out after "
            f"{exc.timeout_seconds}s while child run '{exc.run_id}' was "
            f"still {exc.child_status}; parent is receipt-waiting. Attaching "
            "an existing child run is not exposed by the advertised handles."
        )
        output = {
            "parent_loop_status": "receipt_waiting",
            "selected_child_status": "child_invocation_receipt_waiting",
            "selected_branch_state": "child_invocation_receipt_waiting",
            "automation_claim_status": "child_invocation_receipt_waiting",
            "child_run_id": exc.run_id,
            "selected_child_run_id": exc.run_id,
            "selected_child_branch_def_id": exc.child_branch_def_id,
            "child_invocation_receipt_gate": {
                "status": "receipt_waiting",
                "reason": "child_run_still_running_after_timeout",
                "child_run_id": exc.run_id,
                "child_status": exc.child_status,
                "child_branch_def_id": exc.child_branch_def_id,
                "timeout_seconds": exc.timeout_seconds,
                "reclaim_action": None,
                "reclaim_gap": (
                    "Attaching an existing child run is not exposed by the "
                    "advertised handles."
                ),
            },
        }
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_INTERRUPTED,
            output=output,
            error=msg,
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id,
            status=RUN_STATUS_INTERRUPTED,
            output=output,
            error=msg,
        )
    except Exception as exc:
        # GraphRecursionError: structured error naming the applied limit.
        try:
            from langgraph.errors import GraphRecursionError as _GRE
            if isinstance(exc, _GRE):
                msg = (
                    f"GraphRecursionError: recursion limit {recursion_limit} reached. "
                    "Raise recursion_limit_override on the next run_graph call. "
                    f"Detail: {exc}"
                )
                update_run_status(
                    base_path, run_id,
                    status=RUN_STATUS_FAILED, error=msg, finished_at=_now(),
                )
                return RunOutcome(
                    run_id=run_id, status=RUN_STATUS_FAILED, output={}, error=msg,
                )
        except ImportError:
            pass
        # LangGraph may wrap RunCancelledError in its own exception.
        # Unwrap and handle uniformly.
        if _is_cancel_exception(exc):
            msg = f"Run {run_id} cancelled between nodes."
            update_run_status(
                base_path, run_id,
                status=RUN_STATUS_CANCELLED,
                error=msg,
                finished_at=_now(),
            )
            return RunOutcome(
                run_id=run_id, status=RUN_STATUS_CANCELLED,
                output={}, error=msg,
            )
        budget_stop = _request_budget_failure(exc)
        if budget_stop is not None:
            update_run_status(
                base_path, run_id, status=RUN_STATUS_FAILED,
                error=budget_stop, finished_at=_now(),
            )
            return RunOutcome(
                run_id=run_id, status=RUN_STATUS_FAILED, output={}, error=budget_stop,
            )
        # #61: surface node timeouts with a distinct reason so the user
        # can tell "your evidence-intake node hit the 300s cap" from a
        # generic crash. The NodeTimeoutError message carries the
        # node_id and timeout value.
        effect_exc = _find_effect_failed_exception(exc)
        if effect_exc is not None:
            # Design D1: a refused packet or a write the far side refused
            # failed its node; the message is already in the shape the run
            # surfaces classify (external_write_failed / _refused).
            msg = str(effect_exc)
            step = execution_cursor["step"]
            execution_cursor["step"] += 1
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=effect_exc.node_id or "(unknown)",
                status=NODE_STATUS_FAILED,
                started_at=_now(),
                finished_at=_now(),
                detail={
                    "reason": "effect_failed",
                    "sink": effect_exc.sink,
                    "error_kind": effect_exc.error_kind,
                    "message": effect_exc.error,
                },
            ))
            _emit_node_status(effect_exc.node_id or "(unknown)", NODE_STATUS_FAILED)
            update_run_status(
                base_path, run_id,
                status=RUN_STATUS_FAILED,
                error=msg,
                finished_at=_now(),
            )
            return RunOutcome(
                run_id=run_id, status=RUN_STATUS_FAILED,
                output={}, error=msg,
            )
        timeout_exc = _find_timeout_exception(exc)
        if timeout_exc is not None:
            from tinyassets.graph_compiler import WorkspaceCommandTimeout

            # A ws.run command that outlived its timeout ended the whole jail
            # (workspace-node D2/D6): its own class, not the generic node
            # timeout, so the classifier and the suggested action match.
            if isinstance(timeout_exc, WorkspaceCommandTimeout):
                reason = "workspace_command_timeout"
                msg = f"Workspace command timeout: {timeout_exc}"
            else:
                reason = "timeout"
                msg = f"Node timeout: {timeout_exc}"
            step = execution_cursor["step"]
            execution_cursor["step"] += 1
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=_node_id_from_timeout_exc(timeout_exc),
                status=NODE_STATUS_FAILED,
                started_at=_now(),
                finished_at=_now(),
                detail={"reason": reason, "message": str(timeout_exc)},
            ))
            _emit_node_status(
                _node_id_from_timeout_exc(timeout_exc),
                NODE_STATUS_FAILED,
            )
            update_run_status(
                base_path, run_id,
                status=RUN_STATUS_FAILED,
                error=msg,
                finished_at=_now(),
            )
            return RunOutcome(
                run_id=run_id, status=RUN_STATUS_FAILED,
                output={}, error=msg,
            )
        empty_exc = _find_empty_response_exception(exc)
        if empty_exc is not None:
            msg = f"Empty LLM response: {empty_exc}"
            step = execution_cursor["step"]
            execution_cursor["step"] += 1
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=empty_exc.node_id or "(unknown)",
                status=NODE_STATUS_FAILED,
                started_at=_now(),
                finished_at=_now(),
                detail={"reason": "empty_response", "message": str(empty_exc)},
            ))
            _emit_node_status(
                empty_exc.node_id or "(unknown)",
                NODE_STATUS_FAILED,
            )
            update_run_status(
                base_path, run_id,
                status=RUN_STATUS_FAILED,
                error=msg,
                finished_at=_now(),
            )
            return RunOutcome(
                run_id=run_id, status=RUN_STATUS_FAILED,
                output={}, error=msg,
            )
        logger.exception("Run %s failed at invoke", run_id)
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED,
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error=f"{type(exc).__name__}: {exc}",
        )

    output = dict(result) if isinstance(result, dict) else {"result": result}

    # Emit concurrency_stats event so get_run can surface peak + budget.
    if compiled.concurrency_tracker is not None:
        stats = compiled.concurrency_tracker.stats()
        step = execution_cursor["step"]
        execution_cursor["step"] += 1
        record_event(base_path, RunStepEvent(
            run_id=run_id,
            step_index=step + _PENDING_OFFSET,
            node_id=SYSTEM_EVENT_NODE_ID,
            status="concurrency_stats",
            started_at=_now(),
            detail=stats,
        ))

    # Model-stamp telemetry (spec §11.3 / PR-172): one system event with the
    # full per-call list so receipts can answer "which model, how long, how
    # many tries" per provider-served node.
    if provider_tracker["calls"]:
        step = execution_cursor["step"]
        execution_cursor["step"] += 1
        record_event(base_path, RunStepEvent(
            run_id=run_id,
            step_index=step + _PENDING_OFFSET,
            node_id=SYSTEM_EVENT_NODE_ID,
            status="provider_calls",
            started_at=_now(),
            detail={"calls": provider_tracker["calls"]},
        ))

    # PR-122 Phase 1 — external-write effectors.
    # After a successful run, walk node_defs that declared an ``effects``
    # list and route their outputs to the matching sink (the generic
    # authenticated call, the workspace, the wiki). Errors are surfaced into
    # the run output's ``external_write_errors`` metadata; they never
    # raise into the user-facing run status. Hard-rule #8 (fail loudly)
    # is satisfied by the structured error fields on each evidence entry.
    _quarantine_branch_authored_external_write_keys(output)
    # Empty unless a declared effect failed; see _external_write_error_summary.
    effect_error = ""
    # Every effect already fired at its node (design D1); this is its record.
    # Nothing is dispatched here - one dispatch per node, never two.
    external_write_evidence = dict(effect_chain.evidence)
    # Settlement has exactly one owner: the terminal status write below
    # (update_run_status forgets + settles the registered chain).
    if external_write_evidence:
        # PR-122 Phase 1 round-2 (Codex finding #2): the receipt is
        # system-authoritative. Overwrite unconditionally — any branch
        # that tries to forge ``external_write_results`` /
        # ``external_write_errors`` has already been moved to
        # ``_branch_authored_*`` for forensics above.
        output["external_write_results"] = external_write_evidence
        errors = _collect_external_write_errors(external_write_evidence)
        if errors:
            output["external_write_errors"] = errors
            # Hard Rule 8. The failure was already collected here and then
            # thrown away by an unconditional error="" below, so the caller
            # that launched the run - usually the universe itself - was told
            # it succeeded.
            effect_error = _external_write_error_summary(errors)

    update_run_status(
        base_path, run_id,
        status=RUN_STATUS_COMPLETED,
        output=output,
        finished_at=_now(),
        provider_used=provider_tracker["last"],
        model=provider_tracker["model"],
        error=effect_error,
    )
    return RunOutcome(
        run_id=run_id, status=RUN_STATUS_COMPLETED,
        output=output, error=effect_error,
    )


# PR-122 Phase 1 round-2 (Codex finding #2): reserved system keys
# the effector writes. If a branch already filled these in via run
# output, the values are user-authored and MUST NOT be authoritative
# — they are quarantined under ``_branch_authored_*`` so the system
# receipt is the one that lands at the canonical key.
_EXTERNAL_WRITE_RESERVED_KEYS = (
    "external_write_results",
    "external_write_errors",
    PRIOR_EXTERNAL_WRITE_RESULTS_KEY,
)


def _claimed_cloud_effect_session(provider_call: Any) -> Any | None:
    """Expose effect authority only from the exact service-issued call owner."""
    try:
        from tinyassets.cloud_automation_continuation import (
            _ClaimedCloudProviderSession,
        )
    except ImportError:
        return None
    return (
        provider_call
        if type(provider_call) is _ClaimedCloudProviderSession
        else None
    )


def _quarantine_branch_authored_external_write_keys(
    output: dict[str, Any],
) -> None:
    """Move any branch-authored reserved external-write keys aside.

    Called BEFORE the effector dispatch so that the effector's
    evidence wins at the canonical key. Branch-authored values are
    preserved under ``_branch_authored_<key>`` for forensics, with a
    warning so the operator notices the attempted forgery.
    """
    for system_key in _EXTERNAL_WRITE_RESERVED_KEYS:
        if system_key in output:
            quarantine_key = f"_branch_authored_{system_key}"
            output[quarantine_key] = output.pop(system_key)
            logger.warning(
                "branch output included reserved system key %r; "
                "moved to %r before effector ran",
                system_key,
                quarantine_key,
            )


def _resolve_effector_base(
    base_path: "str | Path",
    run_id: str,
    universe_hint: str = "",
) -> "str | Path":
    """Universe dir this run's external-write effectors bind to.

    Graphs get copied/remixed across universes, so the binding universe is the
    one RUNNING the graph, carried on the run row (``queue_universe_id``) or the
    live ``enqueue_context``. The effector treats ``base_path`` AS the universe
    dir; feeding the flat data root made capability + effector-consent gates
    look at /data and fail closed even when the owner granted them under
    /data/<universe_id>. Falls back to ``base_path`` (data root) when unknown.
    """
    uni = (universe_hint or "").strip()
    if not uni:
        try:
            with _connect(base_path) as _uconn:
                _urow = _uconn.execute(
                    "SELECT queue_universe_id FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            if _urow is not None and _urow["queue_universe_id"]:
                uni = str(_urow["queue_universe_id"]).strip()
        except Exception:
            uni = ""
    return (Path(base_path) / uni) if uni else base_path


def _run_external_write_effectors(
    branch: BranchDefinition,
    run_state: dict[str, Any],
    *,
    base_path: str | Path | None = None,
    run_id: str = "",
    cloud_effect_session: Any | None = None,
) -> dict[str, Any]:
    """Dispatch external-write effectors for ``branch`` against ``run_state``.

    ``base_path`` + ``run_id`` are passed to the effector so the Phase-2
    gates (consent + idempotency) have a universe to bind to. When
    omitted (legacy or test invocations), the effector falls back to
    dry-run for any Phase-2-shaped packet — see
    ``tinyassets.effectors.run_effects_for_branch``.

    Never raises — all errors are folded into the returned evidence map.
    Returns ``{}`` when no node declares any ``effects``.
    """
    try:
        from tinyassets.effectors import run_effects_for_branch
    except Exception:  # pragma: no cover — defensive import guard
        logger.exception("failed to import tinyassets.effectors")
        return {}
    try:
        return run_effects_for_branch(
            branch=branch,
            run_state=run_state,
            base_path=base_path,
            run_id=run_id,
            cloud_effect_session=cloud_effect_session,
        )
    except Exception:  # pragma: no cover — effectors are no-raise
        logger.exception("external-write effector dispatch crashed")
        return {}


def _external_write_error_summary(errors: list[dict[str, Any]]) -> str:
    """One line naming what failed, for the run's ``error`` field.

    A run's ``error`` is what every observer reads first - the agent that
    launched it included. Leaving it empty while the failure sits in
    ``output["external_write_errors"]`` is why a universe could launch a
    PR-opening run, be told ``completed`` with no error, and have nothing
    whatsoever happen: its node declared ``github_pull_request``, a sink that is
    not in the registry, so the dispatcher recorded ``unknown effect sink`` and
    the run reported success anyway (observed live 2026-08-29, run
    cf350418157949cd).

    Deliberately does NOT change the run's STATUS. The graph really did run, and
    flipping completed->failed changes a contract other callers may depend on;
    that is a bigger call than this fix. Naming the failure in ``error`` is the
    smallest change that stops the silence, and it is the field the agent reads.
    """
    parts = []
    for row in errors[:5]:
        node = row.get("node_id") or "?"
        sink = row.get("sink") or "?"
        detail = row.get("error") or row.get("error_kind") or "failed"
        kind = row.get("error_kind") or ""
        parts.append(f"{node}/{sink}: {detail} [{kind}]" if kind else f"{node}/{sink}: {detail}")
    more = "" if len(errors) <= 5 else f" (+{len(errors) - 5} more)"
    return "external write failed - " + "; ".join(parts) + more


def _collect_external_write_errors(
    evidence_map: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flatten the per-node evidence into a list of error rows.

    Each row: ``{"node_id": ..., "sink": ..., "error": ..., "error_kind": ...}``.
    Used to populate ``output['external_write_errors']`` for downstream
    observers (run snapshot, get_run, debugging).
    """
    errors: list[dict[str, Any]] = []
    for node_id, per_node in (evidence_map or {}).items():
        if not isinstance(per_node, dict):
            continue
        for sink, ev in per_node.items():
            if not isinstance(ev, dict):
                continue
            if ev.get("error"):
                errors.append({
                    "node_id": node_id,
                    "sink": sink,
                    "error": ev.get("error"),
                    "error_kind": ev.get("error_kind") or "unknown",
                })
                continue
            # `delivered` means the request REACHED the far side, not that it
            # succeeded: a 404/422/403 was recorded exactly like a 201 and the
            # run completed with no error at all (docs/concerns/2026-08-28-a-403-
            # effect-completes-the-run-silently.md; live 2026-08-30 a deleted
            # head branch -> 404 -> PR 422, and the universe read "completed").
            response = ev.get("response")
            status = response.get("status") if isinstance(response, dict) else None
            if ev.get("accepted_status") is True:
                # The packet declared this status acceptable (probe-then-branch);
                # node dispatch already let it through (Codex round 2, P1).
                continue
            if ev.get("delivered") is True and isinstance(status, int) and status >= 400:
                body = response.get("body")
                preview = str(body)[:160].replace("\n", " ") if body else ""
                errors.append({
                    "node_id": node_id,
                    "sink": sink,
                    "error": f"far side answered HTTP {status}: {preview}".rstrip(": "),
                    "error_kind": "far_side_error",
                    # WHICH connection answered. A `credential_rejected` run is
                    # fixed by one card naming this destination, and without it
                    # here the agent has only the url -- the API host, not the
                    # label the owner deposited the key under. Non-secret: the
                    # owner chose it and read it on the deposit tab.
                    **({"destination": str(ev["destination"])}
                       if str(ev.get("destination") or "").strip() else {}),
                })
                continue
            # A refusal before the wire that carries only an error_kind (the
            # consent gate returns dry_run + missing_consent and no `error`).
            kind = ev.get("error_kind")
            if kind and not ev.get("delivered"):
                errors.append({
                    "node_id": node_id,
                    "sink": sink,
                    "error": f"refused before the wire: {kind}",
                    "error_kind": str(kind),
                })
    return errors


def _is_cancel_exception(exc: BaseException) -> bool:
    """Detect a wrapped cancellation in a chain.

    Two classes count. ``RunCancelledError`` is raised here, between nodes.
    ``NodeCancelledError`` is raised by `graph_compiler` when the cancel
    reached a node's child process mid-run and killed its jail — matched by
    NAME because `graph_compiler` cannot import this module (it imports
    `compile_branch` from there), which is the same duck-typing
    `graph_compiler._is_cancel_exception` already uses in the other direction.

    Without the second name an in-node cancellation unwinds as a generic node
    failure, and the owner is told their workflow broke when in fact they
    stopped it.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, RunCancelledError):
            return True
        if type(cur).__name__ == "NodeCancelledError":
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _find_empty_response_exception(exc: BaseException) -> EmptyResponseError | None:
    """Walk the exception chain for an EmptyResponseError."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, EmptyResponseError):
            return cur
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return None


def _request_budget_failure(exc: BaseException) -> str | None:
    """A wrapped typed budget hold remains terminal, never an approval request."""
    from tinyassets.request_budget import RequestBudgetExceeded

    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, RequestBudgetExceeded):
            return "[request_budget_exhausted] " + current.continuation
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _find_timeout_exception(exc: BaseException) -> NodeTimeoutError | None:
    """Walk the exception chain for a NodeTimeoutError (#61).

    LangGraph wraps node errors in its own exception types; the
    underlying timeout sits on ``__cause__`` / ``__context__``.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, NodeTimeoutError):
            return cur
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return None


def _caller_provenance(
    base_path: "str | Path",
    run_actor: str,
    run_universe: str,
    branch_author: str,
) -> str:
    """``own`` when the running definition was authored by the actor the run
    executes as, OR by an admin of the universe the run executes in - the
    canonical per-universe ownership signal (``universe_owner_actor``). The
    second clause is what makes a universe's engine-authored branches its
    own: the served turn stores the founder's user id as ``author`` while the
    run executes as ``universe:<id>`` (live 2026-08-30, run
    fda6cac079c44ed9: the founder's own code node refused as foreign).
    Everything else is ``public-foreign``. Fail closed on any lookup error."""
    author = (branch_author or "").strip()
    actor = (run_actor or "").strip()
    universe = (run_universe or "").strip()
    if not author:
        return "public-foreign"
    if actor and author == actor:
        return "own"
    if universe:
        try:
            from tinyassets.api.source_channel import universe_owner_actor

            if universe_owner_actor(base_path, universe, author):
                return "own"
        except Exception:  # noqa: BLE001 - fail closed
            logger.exception("caller provenance: owner lookup failed for %s", universe)
    return "public-foreign"


def _execution_context_for_run(
    base_path: "str | Path",
    run_id: str,
    branch: "BranchDefinition",
    *,
    fallback_actor: str = "",
    invocation_depth: int = 0,
) -> "BranchExecutionContext":
    """The immutable authority a compile for EXECUTION must carry (design
    D2): who the run executes as (the persisted run row's actor, never a
    parameter when the row has one), where, and whether the running
    definition is its own (authored by that actor). The normal path builds
    this inline; resume uses this helper - a compile without it fails open
    for foreign code (Codex round 2, P0)."""
    run_actor, run_universe, owner_user_id = "", "", ""
    try:
        with _connect(base_path) as conn:
            row = conn.execute(
                "SELECT actor, queue_universe_id, owner_user_id FROM runs WHERE run_id = ?", (run_id,),
            ).fetchone()
        if row is not None:
            run_actor = (row["actor"] or "").strip()
            run_universe = (row["queue_universe_id"] or "").strip()
            owner_user_id = (row["owner_user_id"] or "").strip()
    except Exception:  # noqa: BLE001 - no row: fall back to the caller's actor
        pass
    run_actor = run_actor or (fallback_actor or "").strip()
    author = (getattr(branch, "author", "") or "").strip()
    provenance = _caller_provenance(base_path, run_actor, run_universe, author)
    # This read is deliberately outside the legacy actor-fallback handler.
    from tinyassets.workspace_family import execution_member

    return BranchExecutionContext(
        actor=run_actor,
        universe_id=run_universe,
        caller_provenance=provenance,
        depth=invocation_depth,
        owner_user_id=owner_user_id,
        definition_author=author,
        workspace_family=execution_member(base_path, run_id, for_compile=True),
    )


def _find_effect_failed_exception(exc: BaseException):
    """Walk the exception chain for an ``EffectFailedError`` (design D1):
    LangGraph wraps node errors; the effect failure sits on
    ``__cause__`` / ``__context__``."""
    try:
        from tinyassets.effectors import EffectFailedError
    except Exception:  # pragma: no cover - defensive import guard
        return None
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, EffectFailedError):
            return cur
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return None


_TIMEOUT_NODE_RE = re.compile(r"Node '([^']+)'")


def _node_id_from_timeout_exc(exc: NodeTimeoutError) -> str:
    """Return the node_id for a NodeTimeoutError.

    Prefers the ``node_id`` attribute set by the raiser (stable contract).
    Falls back to parsing the human-readable message for older callers
    that constructed the exception without the keyword — keeps backward
    compatibility with test fixtures and third-party code.
    """
    node_id = getattr(exc, "node_id", "") or ""
    if node_id:
        return node_id
    return _node_id_from_timeout_message(str(exc))


def _node_id_from_timeout_message(message: str) -> str:
    """Extract the node_id from a NodeTimeoutError message (legacy fallback).

    Fallback to ``"(timeout)"`` when the message doesn't match. The
    node_id drives which row in the run_events timeline surfaces the
    failure. Prefer :func:`_node_id_from_timeout_exc` when the exception
    object is in hand.
    """
    m = _TIMEOUT_NODE_RE.search(message)
    return m.group(1) if m else "(timeout)"


def execute_branch(
    base_path: str | Path,
    *,
    branch: BranchDefinition,
    inputs: dict[str, Any],
    run_name: str = "",
    actor: str = "",   # no default principal; the caller names one or the write refuses
    owner_user_id: str | None = None,
    provider_call: Callable[..., str] | None = None,
    recursion_limit_override: int | None = None,
    concurrency_budget_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    _invocation_depth: int = 0,
    _enqueue_universe_id: str = "",
    _parent_branch_task_id: str = "",
    _origin_branch_task_id: str = "",
    _workspace_parent=None,
) -> RunOutcome:
    """Synchronous end-to-end execution.

    Kept for callers that want the blocking contract (tests, scripts).
    The MCP handler uses :func:`execute_branch_async` instead.

    Raises nothing: validation/runtime errors are reported via
    ``RunOutcome.status``.

    Parameters
    ----------
    recursion_limit_override
        Optional override for LangGraph's recursion limit. When ``None``
        (default), uses :data:`DEFAULT_RECURSION_LIMIT` (100). Branches
        with deep conditional loops (Tier-1 Step 6) bump this.
    """
    branch = BranchDefinition.from_dict(branch.to_dict())
    preflight_required_inputs(branch, inputs)
    # Same admission contract as the async path: the envelope records THIS
    # freeze and the exact effective choices handed to _invoke_graph below,
    # so a later resume runs what was admitted rather than the current draft.
    from tinyassets.run_admission_envelope import (
        AdmissionEnvelopeError,
        encode_admission_envelope,
    )

    effective_limit = recursion_limit_override or DEFAULT_RECURSION_LIMIT
    admission_envelope = encode_admission_envelope(
        branch,
        recursion_limit=effective_limit,
        concurrency_budget_override=concurrency_budget_override,
        run_name=run_name,
        branch_version_id=None,
    )
    try:
        run_id = _prepare_run(
            base_path,
            branch=branch, inputs=inputs,
            run_name=run_name, actor=actor,
            owner_user_id=owner_user_id, queue_universe_id=_enqueue_universe_id or None,
            _workspace_parent=_workspace_parent,
            daemon_id=daemon_id,
            runtime_instance_id=runtime_instance_id,
            worker_id=worker_id,
            admission_envelope=admission_envelope,
        )
    except AdmissionEnvelopeError as exc:
        settled = _settle_failed_admission_envelope(base_path, exc)
        if settled is None:
            raise
        return settled
    enqueue_origin = (
        str(_origin_branch_task_id or "").strip()
        or str(_parent_branch_task_id or "").strip()
    )
    if _enqueue_universe_id and not enqueue_origin:
        # A trusted non-queue root (for example a universe soul loop) has no
        # parent BranchTask. Derive its lineage once from the prepared run so
        # every sibling enqueue competes for the same lifetime budget.
        enqueue_origin = f"run:{run_id}"
    enqueue_context = NodeEnqueueContext(
        universe_id=_enqueue_universe_id,
        actor=actor,
        parent_branch_task_id=_parent_branch_task_id,
        origin_branch_task_id=enqueue_origin,
    )
    return _invoke_graph(
        base_path,
        run_id=run_id, branch=branch, inputs=inputs,
        provider_call=provider_call,
        recursion_limit=effective_limit,
        concurrency_budget_override=concurrency_budget_override,
        on_node_status=on_node_status,
        invocation_depth=_invocation_depth,
        enqueue_context=enqueue_context,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Async executor pool — in-process background worker for graph runs
# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.5: the MCP tool returns a `run_id` in <1s. The graph runs in a
# background thread. `cancel_run` flips the flag, the next inter-node
# `event_sink` check unwinds the graph. Restart recovery marks in-flight
# runs as `interrupted` so clients see a clean terminal state and can
# choose to rerun.

_DEFAULT_MAX_WORKERS = 4
# Phase A item 5 / Task #76c — two-pool model. Top-level runs (depth=0)
# go to _parent_pool; sub-branch invocations (depth>=1) go to _child_pool.
# This prevents the parent-holds-its-own-slot-while-waiting-on-child
# deadlock that single-pool concurrency hit at depth>=4 with pool size 4.
_executor_lock = threading.Lock()
_parent_pool: ThreadPoolExecutor | None = None
_child_pool: ThreadPoolExecutor | None = None
_futures: dict[str, Future] = {}
_futures_lock = threading.Lock()


def _max_workers() -> int:
    raw = os.environ.get("TINYASSETS_RUN_MAX_CONCURRENT", "")
    try:
        val = int(raw) if raw else _DEFAULT_MAX_WORKERS
    except ValueError:
        val = _DEFAULT_MAX_WORKERS
    return max(1, val)


def _max_child_workers() -> int:
    """Pool size for sub-branch (depth>=1) invocations.

    Phase A item 5 / Task #76c. Default ``MAX_INVOKE_BRANCH_DEPTH + 1``.
    Env override: ``TINYASSETS_CHILD_POOL_SIZE``. A blocking version invoke
    may nest at most this deep (``graph_compiler``); nothing else is bounded
    by it.
    """
    raw = os.environ.get("TINYASSETS_CHILD_POOL_SIZE", "")
    try:
        val = int(raw) if raw else MAX_INVOKE_BRANCH_DEPTH + 1
    except ValueError:
        val = MAX_INVOKE_BRANCH_DEPTH + 1
    return max(1, val)


#: One pool pair PER ACCOUNT, beside the keyless pair above. A run whose agent
#: node waits for its account's seat waits on a worker thread; if that thread
#: belonged to a pool every account shares, one busy account's queued runs would
#: hold the threads another account's run needs -- the one thing the platform
#: may never do (memory `the-floor-is-cross-user-only`). So a run is executed by
#: its own account's pool, and an account can only ever wait behind itself.
#: The host-wide memory bound is `provider_admission`, underneath, not this.
_account_pools: dict[tuple[str, str], ThreadPoolExecutor] = {}


def run_pool_key(base_path: str | Path, universe_id: str | None) -> str:
    """The account whose pool runs work for ``universe_id``; '' for none.

    `universe_seats.account_key` -- the same owner resolver seats use, so the
    pool an account's runs wait in is exactly the account whose seats they wait
    for. A resolver failure isolates on the universe instead: that pool is still
    nobody else's.
    """
    uid = (universe_id or "").strip()
    if not uid:
        return ""
    from tinyassets.universe_seats import account_key

    try:
        return account_key(uid, root=base_path)
    except Exception:  # noqa: BLE001 - isolation must not depend on the resolver
        logger.warning("run pool: owner of %s unresolved; isolating on the command center", uid,
                       exc_info=True)
        return f"unattributed:{uid}"


def run_pool_key_for_run(base_path: str | Path, run_id: str) -> str:
    row = get_run(base_path, run_id) or {}
    return run_pool_key(base_path, row.get("queue_universe_id"))


def _get_executor(invocation_depth: int = 0, *, pool_key: str = "") -> ThreadPoolExecutor:
    """Two-pool executor lookup. Depth-0 → parent pool; depth>=1 → child pool.

    Phase A item 5 / Task #76c. Each pool is lazy-init under the shared
    ``_executor_lock``. Child pool is sized larger than parent pool by
    default so a deep sub-branch chain can't starve top-level runs.

    ``pool_key`` names the ACCOUNT (`run_pool_key`): its runs get a pool pair of
    their own, so their seat waits never occupy a thread another account's run
    is owed. The empty key is the keyless pair, for work with no universe.
    """
    global _parent_pool, _child_pool
    child = invocation_depth >= 1
    with _executor_lock:
        if pool_key:
            slot = ("child" if child else "parent", pool_key)
            pool = _account_pools.get(slot)
            if pool is None:
                pool = ThreadPoolExecutor(
                    max_workers=_max_child_workers() if child else _max_workers(),
                    thread_name_prefix=f"tinyassets-{slot[0]}-acct",
                )
                _account_pools[slot] = pool
            return pool
        if child:
            if _child_pool is None:
                _child_pool = ThreadPoolExecutor(
                    max_workers=_max_child_workers(),
                    thread_name_prefix="tinyassets-child",
                )
            return _child_pool
        if _parent_pool is None:
            _parent_pool = ThreadPoolExecutor(
                max_workers=_max_workers(),
                thread_name_prefix="tinyassets-run",
            )
        return _parent_pool


def shutdown_executor(wait: bool = True) -> None:
    """Shut down every executor pool. Used by tests and graceful shutdown.

    Phase A item 5 / Task #76c — two-pool model, and one pair per account.
    """
    global _parent_pool, _child_pool
    with _executor_lock:
        if _parent_pool is not None:
            _parent_pool.shutdown(wait=wait)
            _parent_pool = None
        if _child_pool is not None:
            _child_pool.shutdown(wait=wait)
            _child_pool = None
        pools = list(_account_pools.values())
        _account_pools.clear()
    for pool in pools:
        pool.shutdown(wait=wait)
    with _futures_lock:
        _futures.clear()


def _track_future(run_id: str, future: Future) -> None:
    with _futures_lock:
        _futures[run_id] = future

    def _on_done(_fut: Future) -> None:
        with _futures_lock:
            _futures.pop(run_id, None)

    future.add_done_callback(_on_done)


def get_future(run_id: str) -> Future | None:
    """Return the in-flight Future for a run, if any. Mostly used by tests."""
    with _futures_lock:
        return _futures.get(run_id)


def wait_for(run_id: str, timeout: float | None = None) -> None:
    """Block until the background worker for a run finishes. Test helper."""
    fut = get_future(run_id)
    if fut is not None:
        fut.result(timeout=timeout)


@_owns_managed_execution
def _invoke_prepared_branch(
    base_path: str | Path,
    *,
    run_id: str,
    branch: BranchDefinition,
    inputs: dict[str, Any],
    actor: str,
    provider_call: Callable[..., str] | None,
    recursion_limit: int,
    concurrency_budget_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    invocation_depth: int = 0,
    enqueue_universe_id: str = "",
    _execution_guard=None,
) -> RunOutcome:
    """Execute an already reserved/admitted run and settle its provider claim.

    Internal worker body, not an authorization or replay API. The caller owns
    dispatch exclusion, context and admission; this function never creates a
    run, reloads a mutable definition or submits another executor task. Normal
    foreground and durable-delivery workers share the same invocation path.
    """
    authority_lost = False
    try:
        outcome = _invoke_graph(
            base_path,
            run_id=run_id, branch=branch, inputs=inputs,
            provider_call=provider_call,
            recursion_limit=recursion_limit,
            concurrency_budget_override=concurrency_budget_override,
            on_node_status=on_node_status,
            invocation_depth=invocation_depth,
            enqueue_context=(
                NodeEnqueueContext(
                    universe_id=enqueue_universe_id,
                    actor=actor,
                    parent_branch_task_id="",
                    origin_branch_task_id=f"run:{run_id}",
                )
                if enqueue_universe_id
                else None
            ),
        )
    except RunExecutionAuthorityLost as exc:
        authority_lost = True
        outcome = _existing_run_outcome(base_path, run_id, str(exc))
    except Exception:
        # _invoke_graph normally records failures itself; preserve the outer
        # worker guard for errors that escape it.
        logger.exception("Background worker for run %s crashed", run_id)
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED,
            error="Background worker crashed; see server logs.",
            finished_at=_now(),
        )
        outcome = RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error="Background worker crashed.",
        )
    try:
        from tinyassets.foreground_run_provider import close_foreground_run_provider

        close_foreground_run_provider(provider_call)
    except Exception as exc:
        logger.exception("Foreground provider claim release failed for %s", run_id)
        message = f"Provider authority settlement failed: {exc}"
        if authority_lost:
            return _existing_run_outcome(base_path, run_id, message)
        update_run_status(
            base_path,
            run_id,
            status=RUN_STATUS_FAILED,
            error=message,
            finished_at=_now(),
            _expected_statuses={outcome.status},
        )
        outcome = RunOutcome(
            run_id=run_id,
            status=RUN_STATUS_FAILED,
            output={},
            error=message,
        )
    return outcome


def _execute_branch_core(
    base_path: str | Path,
    *,
    branch: BranchDefinition,
    inputs: dict[str, Any],
    run_name: str = "",
    actor: str = "",   # no default principal; the caller names one or the write refuses
    owner_user_id: str | None = None,
    provider_call: Callable[..., str] | None = None,
    recursion_limit_override: int | None = None,
    concurrency_budget_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    branch_version_id: str | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    _invocation_depth: int = 0,
    _enqueue_universe_id: str = "",
    _workspace_parent=None,
    _provider_parent=None,
    _lend_seat: bool = False,
) -> RunOutcome:
    """Shared async-execution core for def-based and version-based runs.

    Prepares the run row + pending-node events synchronously, then submits
    the graph invocation to the background executor. Returns within a few
    ms with ``status=queued``.

    ``branch_version_id`` is None for def-based runs (the public
    :func:`execute_branch_async`) and set for version-based runs (the
    Phase A item 6 :func:`execute_branch_version_async`).

    ``_invocation_depth`` (Phase A item 5 / Task #76c) routes the run to
    the appropriate executor pool — top-level runs (depth=0) to the
    parent pool, sub-branch invocations (depth>=1) to the child pool.
    Compiler builders pass ``depth+1`` when spawning a child run from
    inside an ``invoke_branch_spec`` / ``invoke_branch_version_spec``
    node body.
    """
    # Freeze the exact definition handed to this run. The worker and provider
    # receipt share this detached snapshot; later author-store edits cannot
    # change the admitted subject underneath an asynchronous execution.
    branch = BranchDefinition.from_dict(branch.to_dict())
    preflight_required_inputs(branch, inputs)
    # Resolve the effective execution choices BEFORE the run row exists, so the
    # envelope records the same numbers the worker below is handed — not a
    # re-derivation that could drift from them.
    effective_limit = recursion_limit_override or DEFAULT_RECURSION_LIMIT
    from tinyassets.run_admission_envelope import (
        AdmissionEnvelopeError,
        encode_admission_envelope,
    )

    admission_envelope = encode_admission_envelope(
        branch,
        recursion_limit=effective_limit,
        concurrency_budget_override=concurrency_budget_override,
        run_name=run_name,
        branch_version_id=branch_version_id,
    )
    try:
        run_id = _prepare_run(
            base_path,
            branch=branch, inputs=inputs,
            run_name=run_name, actor=actor,
            owner_user_id=owner_user_id,
            _workspace_parent=_workspace_parent,
            branch_version_id=branch_version_id,
            daemon_id=daemon_id,
            runtime_instance_id=runtime_instance_id,
            worker_id=worker_id,
            queue_universe_id=_enqueue_universe_id or None,
            admission_envelope=admission_envelope,
        )
    except AdmissionEnvelopeError as exc:
        # The admitted definition is not durable, so this run must not run.
        # Failing closed here is the whole point: dispatching would promise a
        # resumability we cannot honour.
        settled = _settle_failed_admission_envelope(base_path, exc)
        if settled is None:
            raise
        return settled

    try:
        from tinyassets.foreground_run_provider import prepare_foreground_run_provider

        provider_call = prepare_foreground_run_provider(
            provider_call,
            run_id=run_id,
            branch=branch,
            branch_version_id=branch_version_id,
            allowed_statuses={RUN_STATUS_QUEUED},
        )
    except Exception as exc:
        message = f"Provider authority admission failed: {exc}"
        with _managed_execution_scope(base_path, run_id) as guard:
            if guard is not None:
                settled = terminalize_unstarted_run(
                    base_path, run_id=run_id, execution_guard=guard,
                    status=RUN_STATUS_FAILED, error=message,
                )
            else:
                update_run_status(base_path, run_id, status=RUN_STATUS_FAILED,
                                  error=message, finished_at=_now())
                settled = RUN_STATUS_FAILED
        return RunOutcome(
            run_id=run_id,
            status=settled,
            output={},
            error=message,
        )

    if _queues_for_workspace(
        branch, owner_user_id=owner_user_id, universe_id=_enqueue_universe_id,
        invocation_depth=_invocation_depth, workspace_parent=_workspace_parent,
    ):
        waiting = _admit_workspace_waiter(
            base_path, run_id=run_id, universe_id=_enqueue_universe_id,
            provider_call=provider_call,
        )
        if waiting is not None:
            return waiting

    executor = _get_executor(
        invocation_depth=_invocation_depth,
        pool_key=run_pool_key(base_path, _enqueue_universe_id),
    )

    def _worker() -> RunOutcome:
        return _invoke_prepared_branch(
            base_path,
            run_id=run_id, branch=branch, inputs=inputs, actor=actor,
            provider_call=provider_call,
            recursion_limit=effective_limit,
            concurrency_budget_override=concurrency_budget_override,
            on_node_status=on_node_status,
            invocation_depth=_invocation_depth,
            enqueue_universe_id=_enqueue_universe_id,
        )

    # The run's worker thread must see the REQUEST's ContextVars (the
    # authenticated actor): a bare submit gives it the pool thread's empty
    # context, and a code node's RPC then resolves the daemon's env identity
    # (Codex round 3, P0).
    from tinyassets.provider_admission import independent_provider_work

    # run_graph returns queued: it has not suspended its provider. Only a
    # blocking compiler invoke may explicitly hand a slot to this worker.
    with independent_provider_work(parent_slot=_provider_parent):
        worker_context = contextvars.copy_context()
    if not _lend_seat:
        # Nor its account seat: a queued run's caller keeps running, so a borrowed
        # seat would carry two concurrent agent calls (gpt-6-astra round 2). Only
        # a blocking invoke, whose caller waits for this run, lends it.
        from tinyassets.universe_seats import detach_seat

        worker_context.run(detach_seat)
    future = executor.submit(worker_context.run, _worker)
    _track_future(run_id, future)

    return RunOutcome(
        run_id=run_id, status=RUN_STATUS_QUEUED,
        output={}, error="",
    )


def execute_branch_async(
    base_path: str | Path,
    *,
    branch: BranchDefinition,
    inputs: dict[str, Any],
    run_name: str = "",
    actor: str = "",   # no default principal; the caller names one or the write refuses
    owner_user_id: str | None = None,
    provider_call: Callable[..., str] | None = None,
    recursion_limit_override: int | None = None,
    concurrency_budget_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    _invocation_depth: int = 0,
    _enqueue_universe_id: str = "",
    _workspace_parent=None,
) -> RunOutcome:
    """Prepare a def-based run synchronously and kick off graph execution
    in the background. Returns within a few ms with ``status=queued``.

    The status will transition to ``running`` once the worker picks up
    the job, then to ``completed`` / ``failed`` / ``cancelled``. Clients
    poll ``get_run`` or ``stream_run`` for updates.

    Backed by :func:`_execute_branch_core` with ``branch_version_id=None``.
    Version-based runs use :func:`execute_branch_version_async` (Phase A
    item 6, Task #65) instead.

    Parameters
    ----------
    recursion_limit_override
        Optional override for LangGraph's recursion limit. See
        :func:`execute_branch` for rationale.
    concurrency_budget_override
        Override the branch-level concurrency_budget for this run.
    _invocation_depth
        Phase A item 5 / Task #76c — sub-branch builders pass ``depth+1``
        when spawning a child. Top-level callers leave default (0).
    """
    return _execute_branch_core(
        base_path,
        branch=branch,
        inputs=inputs,
        run_name=run_name,
        actor=actor,
        provider_call=provider_call,
        recursion_limit_override=recursion_limit_override,
        concurrency_budget_override=concurrency_budget_override,
        on_node_status=on_node_status,
        branch_version_id=None,
        owner_user_id=owner_user_id,
        _workspace_parent=_workspace_parent,
        _invocation_depth=_invocation_depth,
        _enqueue_universe_id=_enqueue_universe_id,
    )


class SnapshotSchemaDrift(Exception):
    """Raised when a published version's snapshot can't be reconstructed.

    Phase A item 6 (Task #65). Wraps the failure of
    ``BranchDefinition.from_dict(snapshot)`` when the snapshot was
    published against an older branch schema and is missing a
    now-required field, has a now-removed field, or has a type-changed
    field. Carries class-level ``failure_class`` + ``suggested_action``
    so the MCP-layer handler can read them off the class without
    instantiating a defensive copy.
    """

    failure_class = "snapshot_schema_drift"
    suggested_action = "republish at current schema version"
    actionable_by = "chatbot"


def _load_branch_version(
    base_path: str | Path,
    branch_version_id: str,
) -> BranchDefinition:
    from tinyassets.branch_versions import get_branch_version

    version = get_branch_version(base_path, branch_version_id=branch_version_id)
    if version is None:
        raise KeyError(
            f"branch_version_id {branch_version_id!r} not found in branch_versions"
        )
    try:
        snapshot = dict(version.snapshot)
        # Published snapshots contain behavior only; the compiler still
        # requires a non-empty presentation name at validation time.
        snapshot.setdefault("name", snapshot.get("branch_def_id", ""))
        return BranchDefinition.from_dict(snapshot)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SnapshotSchemaDrift(
            f"Snapshot for {branch_version_id!r} cannot be reconstructed: "
            f"{exc}. Republish at current schema version."
        ) from exc


def execute_branch_version(
    base_path: str | Path,
    *,
    branch_version_id: str,
    inputs: dict[str, Any],
    run_name: str = "",
    actor: str = "",   # no default principal; the caller names one or the write refuses
    owner_user_id: str | None = None,
    provider_call: Callable[..., str] | None = None,
    recursion_limit_override: int | None = None,
    concurrency_budget_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    daemon_id: str | None = None,
    runtime_instance_id: str | None = None,
    worker_id: str | None = None,
    _invocation_depth: int = 0,
    _enqueue_universe_id: str = "",
    _parent_branch_task_id: str = "",
    _origin_branch_task_id: str = "",
    _queue_branch_task_id: str = "",
    _workspace_parent=None,
) -> RunOutcome:
    """Execute an immutable published Branch version and block to completion."""
    branch = _load_branch_version(base_path, branch_version_id)
    preflight_required_inputs(branch, inputs)
    from tinyassets.run_admission_envelope import (
        AdmissionEnvelopeError,
        encode_admission_envelope,
    )

    effective_limit = recursion_limit_override or DEFAULT_RECURSION_LIMIT
    # branch_version_id is recorded in the envelope AND left on the run row
    # untouched -- its attribution meaning ("the user selected this published
    # version") is unchanged; the envelope adds the execution choices the
    # immutable snapshot cannot carry.
    admission_envelope = encode_admission_envelope(
        branch,
        recursion_limit=effective_limit,
        concurrency_budget_override=concurrency_budget_override,
        run_name=run_name,
        branch_version_id=branch_version_id,
    )
    try:
        run_id = _prepare_run(
            base_path,
            branch=branch,
            inputs=inputs,
            run_name=run_name,
            actor=actor,
            branch_version_id=branch_version_id,
            daemon_id=daemon_id,
            runtime_instance_id=runtime_instance_id,
            worker_id=worker_id,
            branch_task_id=_queue_branch_task_id or None,
            owner_user_id=owner_user_id,
            _workspace_parent=_workspace_parent,
            queue_universe_id=_enqueue_universe_id or None,
            admission_envelope=admission_envelope,
        )
    except AdmissionEnvelopeError as exc:
        settled = _settle_failed_admission_envelope(base_path, exc)
        if settled is None:
            raise
        return settled
    enqueue_origin = (
        str(_origin_branch_task_id or "").strip()
        or str(_parent_branch_task_id or "").strip()
    )
    if _enqueue_universe_id and not enqueue_origin:
        enqueue_origin = f"run:{run_id}"
    enqueue_context = NodeEnqueueContext(
        universe_id=_enqueue_universe_id,
        actor=actor,
        parent_branch_task_id=_parent_branch_task_id,
        origin_branch_task_id=enqueue_origin,
    )
    return _invoke_graph(
        base_path,
        run_id=run_id,
        branch=branch,
        inputs=inputs,
        provider_call=provider_call,
        recursion_limit=effective_limit,
        concurrency_budget_override=concurrency_budget_override,
        on_node_status=on_node_status,
        invocation_depth=_invocation_depth,
        enqueue_context=enqueue_context,
    )


def execute_branch_version_async(
    base_path: str | Path,
    *,
    branch_version_id: str,
    inputs: dict[str, Any],
    run_name: str = "",
    actor: str = "",   # no default principal; the caller names one or the write refuses
    owner_user_id: str | None = None,
    provider_call: Callable[..., str] | None = None,
    recursion_limit_override: int | None = None,
    on_node_status: Callable[[str, str], None] | None = None,
    _invocation_depth: int = 0,
    _enqueue_universe_id: str = "",
    _workspace_parent=None,
    _provider_parent=None,
    _lend_seat: bool = False,
) -> RunOutcome:
    """Execute a published branch_version snapshot (immutable).

    Sibling to :func:`execute_branch_async`; both wrap
    :func:`_execute_branch_core`. The version-based path loads the
    immutable snapshot from ``branch_versions``, reconstructs a
    ``BranchDefinition`` from it, and threads ``branch_version_id``
    through to the new ``runs.branch_version_id`` column for
    attribution (Task #48 / Task #53 dependencies).

    Cancellation propagation
    ------------------------
    Basic cancellation is identical to def-based runs — the run gets a
    ``run_id`` like any other; ``cancel_run(run_id)`` flips the flag in
    ``run_cancels`` and ``_invoke_graph``'s event_sink unwinds. **Parent
    gate-series cancellation does NOT propagate to child version-runs
    today.** Child runs are independent ``run_id``s; the propagation
    primitive lands when Task #53 route-back is implemented (a parent
    run that route-backs to a canonical via this helper will need
    cancellation forwarding then).

    Raises
    ------
    KeyError
        ``branch_version_id`` is not found in ``branch_versions``.
    SnapshotSchemaDrift
        The snapshot exists but cannot be reconstructed into a
        ``BranchDefinition`` because the on-disk shape predates a
        required field. The exception's ``failure_class`` and
        ``suggested_action`` class attributes name the recovery path
        ("republish at current schema version").
    """
    branch = _load_branch_version(base_path, branch_version_id)
    return _execute_branch_core(
        base_path,
        branch=branch,
        inputs=inputs,
        run_name=run_name,
        actor=actor,
        provider_call=provider_call,
        recursion_limit_override=recursion_limit_override,
        on_node_status=on_node_status,
        branch_version_id=branch_version_id,
        owner_user_id=owner_user_id,
        _workspace_parent=_workspace_parent,
        _provider_parent=_provider_parent,
        _lend_seat=_lend_seat,
        _enqueue_universe_id=_enqueue_universe_id,
        _invocation_depth=_invocation_depth,
    )


class ResumeError(Exception):
    """Raised when a resume_run call cannot proceed.

    Carries a structured ``reason`` code for programmatic handling:
    - ``not_interrupted``: run is not in INTERRUPTED status.
    - ``already_resumed``: run is already in RESUMED status (idempotent return).
    - ``not_found``: run_id does not exist.
    - ``auth_failed``: caller does not own the run.
    - ``no_checkpoint``: SqliteSaver has no checkpoint for this thread_id.
    - ``admission_not_reconstructable``: nothing durable proves what this run
      was admitted with — a pre-envelope run (with or without a
      ``branch_version_id``, which records the user's version SELECTION and
      never the per-run execution choices), a corrupt or unknown-schema
      envelope, or one whose bindings disagree with the run row. Never
      substituted with the current editable definition and never with guessed
      execution defaults.

    ``branch_version_mismatch`` is retired: the definition no longer comes from
    a version lookup that could miss, so there is no mismatch left to report.
    """

    def __init__(self, message: str, *, reason: str = "", current_status: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.current_status = current_status


def _has_checkpoint(base_path: str | Path, thread_id: str) -> bool:
    """Return True if SqliteSaver has a checkpoint for thread_id."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        saver_path = str(Path(base_path) / ".langgraph_runs.db")
        if not Path(saver_path).exists():
            return False
        with SqliteSaver.from_conn_string(saver_path) as cp:
            # LangGraph's list() yields checkpoint tuples; we just need to
            # know at least one exists.
            config = {"configurable": {"thread_id": thread_id}}
            items = list(cp.list(config))
            return bool(items)
    except Exception:
        return False


def resume_run(
    base_path: str | Path,
    *,
    run_id: str,
    actor: str,
    branch_lookup: Callable[[str, int], BranchDefinition | None],
    provider_call: Callable[..., str] | None = None,
) -> RunOutcome:
    """Resume an INTERRUPTED run from its SqliteSaver checkpoint.

    Parameters
    ----------
    run_id
        The run to resume.
    actor
        The caller's identity. Must match the run's ``actor`` field.
    branch_lookup
        **Retained for signature compatibility and deliberately not consulted.**
        It resolved the CURRENT editable definition, so an author edit between
        interruption and resume substituted a different graph under a run's
        checkpoint. The definition now comes from the run's own durable
        admission envelope; an injected lookup has no authority over what runs.
    provider_call
        Optional provider callable; same semantics as ``execute_branch``.

    Returns a ``RunOutcome`` with the resumed run's ID (same as input ``run_id``).

    Raises ``ResumeError`` on auth failure, wrong status, missing checkpoint,
    or branch version mismatch.
    """
    run = get_run(base_path, run_id)
    if run is None:
        raise ResumeError(
            f"Run '{run_id}' not found.", reason="not_found",
        )

    # Auth gate: caller must own the run.
    if run["actor"] != actor:
        raise ResumeError(
            f"Actor '{actor}' does not own run '{run_id}' "
            f"(owned by '{run['actor']}').",
            reason="auth_failed",
        )

    current_status = run["status"]

    # Idempotency: already resumed → return the same run_id, no second resume.
    if current_status == RUN_STATUS_RESUMED:
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_RESUMED,
            output=run.get("output", {}), error="",
        )

    # Status gate: only INTERRUPTED can be resumed.
    if current_status != RUN_STATUS_INTERRUPTED:
        raise ResumeError(
            f"Run '{run_id}' is '{current_status}', not 'interrupted'. "
            f"Only interrupted runs can be resumed.",
            reason="not_interrupted",
            current_status=current_status,
        )

    # Checkpoint gate.
    thread_id = run.get("thread_id") or run_id
    if not _has_checkpoint(base_path, thread_id):
        raise ResumeError(
            f"No SqliteSaver checkpoint found for run '{run_id}'. "
            "The run predates resume support or the checkpoint was evicted. "
            "Rerun from scratch with run_graph using the same branch_def_id and "
            "inputs_json.",
            reason="no_checkpoint",
        )

    # Admitted-definition gate. The authority is the run's own durable
    # admission envelope and nothing else — NOT ``branch_lookup``, which
    # resolves the CURRENT editable definition and would silently substitute
    # an edited graph, and NOT the run's ``branch_version_id``, which records
    # what the user selected rather than the execution choices admission
    # resolved. There is no legacy reconstruction path: a run with no envelope
    # refuses ``admission_not_reconstructable``. Resolved before provider
    # admission and before any effect can fire.
    from tinyassets.run_admission_envelope import (
        AdmissionNotReconstructable,
        resolve_admitted_execution,
    )

    try:
        admitted = resolve_admitted_execution(base_path, run)
    except AdmissionNotReconstructable as exc:
        raise ResumeError(str(exc), reason=exc.reason) from exc

    branch = BranchDefinition.from_dict(admitted.branch.to_dict())
    try:
        from tinyassets.foreground_run_provider import prepare_foreground_run_provider

        provider_call = prepare_foreground_run_provider(
            provider_call,
            run_id=run_id,
            branch=branch,
            branch_version_id=run.get("branch_version_id") or None,
            allowed_statuses={RUN_STATUS_INTERRUPTED},
        )
    except Exception as exc:
        raise ResumeError(
            f"Provider authority admission failed for run '{run_id}': {exc}",
            reason="provider_authority_held",
        ) from exc

    # Mark RESUMED immediately (before background work starts).
    update_run_status(base_path, run_id, status=RUN_STATUS_RESUMED)

    # Emit resume_started event.
    record_event(base_path, RunStepEvent(
        run_id=run_id,
        step_index=_PENDING_OFFSET,
        node_id="__resume__",
        status="resume_started",
        started_at=_now(),
        finished_at=_now(),
        detail={
            "resume_actor": actor,
            "resumed_at": _iso_now(),
        },
    ))

    # Background worker: re-invoke graph with None inputs to trigger resume.
    executor = _get_executor(pool_key=run_pool_key_for_run(base_path, run_id))

    def _resume_worker() -> RunOutcome:
        outcome = _invoke_graph_resume(
            base_path,
            run_id=run_id,
            branch=branch,
            thread_id=thread_id,
            provider_call=provider_call,
            recursion_limit=admitted.recursion_limit,
            concurrency_budget_override=admitted.concurrency_budget_override,
        )
        try:
            from tinyassets.foreground_run_provider import close_foreground_run_provider

            close_foreground_run_provider(provider_call)
        except Exception as exc:
            logger.exception(
                "Foreground provider claim release failed for resumed run %s",
                run_id,
            )
            message = f"Provider authority settlement failed: {exc}"
            update_run_status(
                base_path,
                run_id,
                status=RUN_STATUS_FAILED,
                error=message,
                finished_at=_now(),
                _expected_statuses={outcome.status},
            )
            return RunOutcome(
                run_id=run_id,
                status=RUN_STATUS_FAILED,
                output={},
                error=message,
            )
        return outcome

    def _owned_resume_worker() -> RunOutcome:
        with _managed_execution_scope(base_path, run_id):
            return _resume_worker()

    from tinyassets.provider_admission import independent_provider_work

    with independent_provider_work():
        worker_context = contextvars.copy_context()
    from tinyassets.universe_seats import detach_seat

    worker_context.run(detach_seat)  # a resumed run takes its own seats
    future = executor.submit(worker_context.run, _owned_resume_worker)
    _track_future(run_id, future)

    return RunOutcome(
        run_id=run_id, status=RUN_STATUS_RESUMED,
        output={}, error="",
    )


@_owns_managed_execution
def _invoke_graph_resume(
    base_path: str | Path,
    *,
    run_id: str,
    branch: BranchDefinition,
    thread_id: str,
    provider_call: Callable[..., str] | None,
    recursion_limit: int,
    concurrency_budget_override: int | None,
) -> RunOutcome:
    """Compile branch + invoke with None inputs to resume from checkpoint.

    ``recursion_limit`` and ``concurrency_budget_override`` are the run's
    ADMITTED effective choices, read from its admission envelope. They are
    required, not defaulted: a resumed segment that silently fell back to the
    platform default ran a different execution than the one admitted, which is
    the bug this slice exists to close. They reach exactly the two seams the
    non-resume :func:`_invoke_graph` uses -- ``compile_branch`` for the
    concurrency budget, ``app.invoke``'s config for the recursion limit.
    """
    _require_managed_start_status(base_path, run_id, RUN_STATUS_RESUMED)
    execution_cursor = {"step": 1000}  # offset so resume events don't collide
    provider_tracker: dict[str, Any] = {"last": None, "model": None, "calls": []}

    def _on_node(node_id: str, **detail: Any) -> None:
        phase = detail.pop("phase", "ran")
        step = execution_cursor["step"]
        execution_cursor["step"] += 1
        if phase == "ran":
            served = detail.get("provider_served")
            if served:
                provider_tracker["last"] = served
                model = detail.get("provider_model")
                if model:
                    provider_tracker["model"] = str(model)
                provider_tracker["calls"].append({
                    "node_id": node_id,
                    "provider": str(served),
                    "model": str(model or ""),
                    "latency_ms": detail.get("provider_latency_ms"),
                    "attempts": detail.get("provider_attempts"),
                    "degraded": bool(detail.get("provider_degraded", False)),
                    "at": _now(),
                })

        if phase == "starting":
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=node_id,
                status=NODE_STATUS_RUNNING,
                started_at=_now(),
                detail=detail,
            ))
            return

        if phase == "waiting":
            _record_seat_wait(base_path, run_id, step + _PENDING_OFFSET, node_id, detail)
            return

        if phase == "effect":
            # Design D1: the node's effects fired inside its step. Recorded as
            # a system row (never a node status) so per-node status stays
            # pending -> running -> ran/failed and the snapshot's phase logic,
            # which ignores __system__, is untouched.
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=SYSTEM_EVENT_NODE_ID,
                status="effect",
                started_at=_now(),
                finished_at=_now(),
                detail={"node_id": node_id, **detail},
            ))
            return

        if phase in {"failed", "cancelled"}:
            record_event(base_path, RunStepEvent(
                run_id=run_id,
                step_index=step + _PENDING_OFFSET,
                node_id=node_id,
                status=NODE_STATUS_CANCELLED if phase == "cancelled" else NODE_STATUS_FAILED,
                started_at=_now(),
                finished_at=_now(),
                detail=detail,
            ))
            return

        record_event(base_path, RunStepEvent(
            run_id=run_id,
            step_index=step + _PENDING_OFFSET,
            node_id=node_id,
            status=NODE_STATUS_RAN,
            started_at=_now(),
            finished_at=_now(),
            detail=detail,
        ))
        if is_cancel_requested(base_path, run_id):
            raise RunCancelledError(f"Run {run_id} cancelled during resume.")

    from tinyassets.effectors import EffectChain, register_effect_chain

    # Design D1: the chain is in memory, so an effect that fired BEFORE the
    # interrupt is not readable by a later node after resume - a reference to
    # it refuses rather than resolving from a truncated preview.
    _resume_universe = ""
    try:
        with _connect(base_path) as _uconn:
            _urow = _uconn.execute(
                "SELECT queue_universe_id FROM runs WHERE run_id = ?", (run_id,),
            ).fetchone()
        if _urow is not None and _urow["queue_universe_id"]:
            _resume_universe = str(_urow["queue_universe_id"]).strip()
    except Exception:  # noqa: BLE001 - unknown universe: only the per-run budget applies
        _resume_universe = ""
    effect_chain = EffectChain(
        run_id=run_id,
        base_path=_resolve_effector_base(base_path, run_id),
        cloud_effect_session=_claimed_cloud_effect_session(provider_call),
        universe_id=_resume_universe,
        review_provider=provider_call,
        review_active=True,
    )
    # What the interrupted segment already fired and spent, so "at most once
    # per run" and the RPC cap hold across the resume, and the nested depth
    # is not reset to zero (Codex round 3, P0/P1).
    try:
        _prior = get_run(base_path, run_id)
    except Exception:  # noqa: BLE001 - a missing record seeds nothing
        _prior = None
    effect_chain.seed_from_output((_prior or {}).get("output") if _prior else None)
    register_effect_chain(effect_chain)
    # A compile for EXECUTION always carries the run's authority (design D2):
    # without it a public-foreign branch's code fails OPEN on resume (Codex
    # round 2, P0). Derived from the persisted run row, never a parameter.
    resume_context = _execution_context_for_run(
        base_path, run_id, branch, invocation_depth=effect_chain.invocation_depth,
    )
    update_run_status = _family_status_writer(resume_context.workspace_family)
    try:
        compiled = compile_branch(
            branch,
            provider_call=provider_call,
            event_sink=_on_node,
            concurrency_budget_override=concurrency_budget_override,
            effect_chain=effect_chain,
            execution_context=resume_context,
            should_cancel=lambda: is_cancel_requested(base_path, run_id),
        )
    except CompilerError as exc:
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED,
            error=str(exc),
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error=str(exc),
        )

    update_run_status(base_path, run_id, status=RUN_STATUS_RUNNING)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        saver_path = str(Path(base_path) / ".langgraph_runs.db")
        with SqliteSaver.from_conn_string(saver_path) as checkpointer:
            app = compiled.graph.compile(checkpointer=checkpointer)
            # None inputs triggers resume from last checkpoint. The admitted
            # recursion limit rides the same config key _invoke_graph uses, so
            # the resumed segment gets the cap the run was admitted with.
            result = app.invoke(
                None,
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": recursion_limit,
                },
            )
    except RunCancelledError as exc:
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_CANCELLED,
            error=str(exc),
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_CANCELLED,
            output={}, error=str(exc),
        )
    except Exception as exc:
        if _is_cancel_exception(exc):
            msg = f"Run {run_id} cancelled during resume."
            update_run_status(
                base_path, run_id,
                status=RUN_STATUS_CANCELLED,
                error=msg,
                finished_at=_now(),
            )
            return RunOutcome(
                run_id=run_id, status=RUN_STATUS_CANCELLED,
                output={}, error=msg,
            )
        effect_stop = _find_effect_failed_exception(exc)
        if effect_stop is not None and effect_stop.error_kind == "request_budget_exhausted":
            msg = str(effect_stop)
        else:
            msg = _request_budget_failure(exc) or f"Resume execution failed: {exc}"
        update_run_status(
            base_path, run_id,
            status=RUN_STATUS_FAILED,
            error=msg,
            finished_at=_now(),
        )
        return RunOutcome(
            run_id=run_id, status=RUN_STATUS_FAILED,
            output={}, error=msg,
        )

    output = dict(result) if isinstance(result, dict) else {}
    # PR-122 Phase 1 — also fire external-write effectors on resume
    # completion so a re-run that finishes via resume_run still emits
    # declared PR sinks. Same no-raise contract as the primary path.
    _quarantine_branch_authored_external_write_keys(output)
    # Empty unless a declared effect failed; see _external_write_error_summary.
    effect_error = ""
    # Fired at node time (design D1); read the record, dispatch nothing.
    external_write_evidence = dict(effect_chain.evidence)
    # Settlement has exactly one owner: the terminal status write below
    # (update_run_status forgets + settles the registered chain).
    if external_write_evidence:
        # System-authoritative receipt — overwrite unconditionally
        # (see start_run for the rationale + Codex finding #2).
        output["external_write_results"] = external_write_evidence
        errors = _collect_external_write_errors(external_write_evidence)
        if errors:
            output["external_write_errors"] = errors
            effect_error = _external_write_error_summary(errors)
    update_run_status(
        base_path, run_id,
        status=RUN_STATUS_COMPLETED,
        output=output,
        finished_at=_now(),
        provider_used=provider_tracker["last"],
        model=provider_tracker["model"],
        error=effect_error,
    )
    return RunOutcome(
        run_id=run_id, status=RUN_STATUS_COMPLETED,
        output=output, error=effect_error,
    )


def _dead_owner_rows(
    base_path: str | Path, rows: list[Any], *, started_before: float | None,
) -> list[Any]:
    """The rows whose owner is provably dead, probing each owner once."""
    from tinyassets.process_liveness import DEAD, owner_state

    states: dict[str, str] = {}
    dead: list[Any] = []
    for row in rows:
        token = row["owner_token"]
        if token:
            if token not in states:
                states[token] = owner_state(base_path, str(token))
            if states[token] == DEAD:
                dead.append(row)
        elif started_before is None or float(row["started_at"] or 0) < started_before:
            dead.append(row)
    return dead


def in_flight_owner_tokens(base_path: str | Path) -> set[str]:
    """Every owner token a queued or running run still names.

    A dead owner's liveness file is the proof its runs need to be recovered,
    so nothing may delete it while a run names it.
    """
    if not runs_db_path(base_path).is_file():
        return set()
    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        return {
            str(row["owner_token"]) for row in conn.execute(
                "SELECT DISTINCT owner_token FROM runs WHERE status IN (?, ?, ?) "
                "AND owner_token IS NOT NULL",
                (RUN_STATUS_QUEUED, RUN_STATUS_RUNNING, RUN_STATUS_RESUMED),
            )
        }


def recover_in_flight_runs(
    base_path: str | Path, *, started_before: float | None = None,
) -> int:
    """Interrupt in-flight runs whose owning process is provably dead.

    Family/prepared executions are excluded: their own protocols recover them.
    A row carrying an ``owner_token`` is interrupted only when that owner's
    liveness lock file exists and nobody holds it (run-owner-proof D3); alive
    and unknown owners are never touched, however long their run is quiet.
    A row with no token predates owner proof: it is interrupted when it started
    before ``started_before`` (the recovering process's own start), a one-time
    rule for the rows a pre-change process left. Every interruption owes its
    terminal event through the outbox, delivered after the commit.

    v1 contract: ``interrupted`` is terminal. Callers rerun with the
    same ``inputs_json`` to continue; the MCP surface exposes this via
    ``get_run.resumable=false`` (see ``_compose_run_snapshot``). Mid-run
    resume via SqliteSaver checkpoint + thread_id is a future extension
    — not available today. Hard-rule #8 (fail loudly) is satisfied by
    the descriptive error field + terminal status; do not silently
    drop interrupted runs or loop a poll expecting them to re-run.
    """
    initialize_runs_db(base_path)
    now = _now()
    releases: list[tuple[str, Path | None, int]] = []
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        prepared_exclusion = _prepared_run_recovery_exclusion(conn)
        rows = conn.execute(
            "SELECT run_id, status, queue_universe_id, branch_def_id, actor, "
            "cause_principal, owner_token, started_at FROM runs WHERE status IN (?, ?, ?) "
            "AND workspace_budget_root_run_id IS NULL AND workspace_budget_epoch IS NULL "
            "AND workspace_budget_closing_reason IS NULL" + prepared_exclusion,
            (RUN_STATUS_QUEUED, RUN_STATUS_RUNNING, RUN_STATUS_RESUMED),
        ).fetchall()
        candidates = _dead_owner_rows(base_path, rows, started_before=started_before)
        # A run still waiting for the workspace never executed a node: it keeps
        # its place instead of being interrupted, and is nominated below.
        waiting = _never_started_waiters(base_path, candidates)
        in_flight = [row["run_id"] for row in candidates if row["run_id"] not in waiting]
        count = 0
        by_id = {row["run_id"]: row for row in candidates}
        interrupted_rows: list[Any] = []
        for run_id in in_flight:
            moved = conn.execute(
                "UPDATE runs SET status = ?, error = ?, finished_at = ? "
                "WHERE run_id = ? AND status IN (?, ?, ?)",
                (
                    RUN_STATUS_INTERRUPTED,
                    "The process running this run stopped before it finished.",
                    now,
                    run_id,
                    RUN_STATUS_QUEUED, RUN_STATUS_RUNNING, RUN_STATUS_RESUMED,
                ),
            ).rowcount
            count += moved
            if moved:
                interrupted_rows.append(by_id[run_id])
        for run_id in in_flight:
            # Same-database work is atomic with the rewrite.  A separate
            # universe WAL is finished after this transaction commits.
            owed = _enqueue_workspace_terminal(conn, base_path, run_id)
            releases.append(
                (run_id, _workspace_terminal_base(conn, base_path, run_id), owed)
            )
    for run_id, workspace_base, owed in releases:
        _finish_terminal_workspace_release(
            base_path, run_id, workspace_base, local_owed=owed
        )
    # Their owners died; an owner's graph that follows them can resume.
    if interrupted_rows:
        deliver_terminal_events(
            base_path, run_ids=[str(row["run_id"]) for row in interrupted_rows],
        )
    for universe_base in sorted(set(waiting.values())):
        _kick_workspace_waiters(universe_base)
    if waiting:
        logger.info("Kept %d never-started workspace waiters queued", len(waiting))
    if count:
        logger.info("Recovered %d in-flight runs as 'interrupted'", count)
    return count


# Step indices higher than the count of pending events are reserved for
# the executed events, so the two event streams don't collide on
# (run_id, step_index) primary keys.
_PENDING_OFFSET = 1_000_000


# ─────────────────────────────────────────────────────────────────────────────
# Presentation helpers
# ─────────────────────────────────────────────────────────────────────────────


def build_node_status_map(
    events: list[dict[str, Any]],
    declared_order: list[str],
) -> list[dict[str, Any]]:
    """Fold the raw event stream into a per-node status list.

    Later events dominate earlier ones: a node seen as ``ran`` wins over
    its earlier ``pending`` row. This is the shape Claude.ai visualises
    to auto-build a state diagram.

    ``__system__`` rows are NOT nodes and never appear here. Every emitter of
    one already says so ("recorded as a system row (never a node status)"), and
    two readers already re-filtered it, but this fold did not -- so a run
    summary listed ``__system__: recursion_limit_applied`` among its nodes and
    the mermaid diagram drew a box for it. Live 2026-09-30 (runs
    ``61184d8f21724915`` / ``4828ae18e2414e77``): the row reads as a failed step
    of the workflow to both a user and a chatbot. The applied limit is still
    surfaced, off the EVENT, as ``recursion_limit``
    (``api/runs._compose_run_snapshot``).
    """
    statuses: dict[str, str] = {
        nid: NODE_STATUS_PENDING
        for nid in declared_order
        if nid != SYSTEM_EVENT_NODE_ID
    }
    for ev in events:
        node_id = ev.get("node_id", "")
        if not node_id or node_id == SYSTEM_EVENT_NODE_ID:
            continue
        statuses.setdefault(node_id, NODE_STATUS_PENDING)
        current = statuses[node_id]
        incoming = ev.get("status", NODE_STATUS_PENDING)
        # Observed terminal states trump running, which trumps pending.
        priority = {
            NODE_STATUS_PENDING: 0,
            NODE_STATUS_RUNNING: 1,
            NODE_STATUS_RAN: 2,
            NODE_STATUS_FAILED: 2,
            NODE_STATUS_CANCELLED: 2,
        }
        if priority.get(incoming, 0) >= priority.get(current, 0):
            statuses[node_id] = incoming
    # Preserve declared order, then append any out-of-order nodes.
    ordered_ids = [nid for nid in declared_order if nid != SYSTEM_EVENT_NODE_ID]
    for nid in statuses:
        if nid not in ordered_ids:
            ordered_ids.append(nid)
    return [
        {"node_id": nid, "status": statuses[nid]}
        for nid in ordered_ids
    ]


_VALID_STATUSES = frozenset({
    RUN_STATUS_QUEUED, RUN_STATUS_RUNNING, RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED, RUN_STATUS_CANCELLED, RUN_STATUS_INTERRUPTED,
})
_VALID_AGGREGATES = frozenset({"count", "mean", "sum", "rate"})
_MAX_QUERY_LIMIT = 1000
_DEFAULT_QUERY_LIMIT = 100


def query_runs(
    base_path: str | Path,
    *,
    branch_def_id: str = "",
    filters: dict[str, Any] | None = None,
    select: list[str] | None = None,
    aggregate: dict[str, Any] | None = None,
    limit: int = _DEFAULT_QUERY_LIMIT,
    row_filter: Callable[[Any], bool] | None = None,
) -> dict[str, Any]:
    """Query runs table with optional field projection + simple aggregation.

    Spec: docs/vetted-specs.md §Cross-run state query primitive.

    Returns:
        {"rows": [...], "count": N} for plain queries.
        {"aggregated": [...], "count": N, "group_by": field, "agg_op": op}
        for aggregate queries.

    Invariants:
        - INTERRUPTED runs excluded from aggregation unless status filter
          explicitly includes them.
        - limit default 100, max 1000.
        - select fields extracted from output_json via JSON path.
        - aggregate.fn in {"count", "mean", "sum", "rate"}.
    """
    initialize_runs_db(base_path)
    filters = filters or {}
    select = select or []
    limit = min(max(1, limit), _MAX_QUERY_LIMIT)

    clauses: list[str] = []
    params: list[Any] = []

    if branch_def_id:
        clauses.append("branch_def_id = ?")
        params.append(branch_def_id)

    if "status" in filters:
        status_val = filters["status"]
        if isinstance(status_val, list):
            placeholders = ",".join("?" * len(status_val))
            clauses.append(f"status IN ({placeholders})")
            params.extend(status_val)
        else:
            clauses.append("status = ?")
            params.append(status_val)

    if "actor" in filters:
        clauses.append("actor = ?")
        params.append(filters["actor"])

    if "since" in filters:
        clauses.append("started_at >= ?")
        params.append(float(filters["since"]))

    if "until" in filters:
        clauses.append("started_at <= ?")
        params.append(float(filters["until"]))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect(base_path) as conn:
        rows = conn.execute(
            f"SELECT run_id, branch_def_id, status, actor, queue_universe_id, "
            f"started_at, finished_at, output_json "
            f"FROM runs {where} "
            f"ORDER BY started_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()

    # Apply the caller's row-level access filter BEFORE any projection or
    # aggregation, so a denied universe's runs never contribute to selected
    # fields or aggregate values (security: no leak via select/aggregate).
    if row_filter is not None:
        rows = [r for r in rows if row_filter(r)]

    def _extract_fields(output_str: str, fields: list[str]) -> dict[str, Any]:
        try:
            state = json.loads(output_str) if output_str else {}
        except (json.JSONDecodeError, TypeError):
            state = {}
        if not fields:
            return {}
        return {f: state.get(f) for f in fields}

    _RUN_COLUMNS = frozenset({
        "run_id", "branch_def_id", "status", "actor", "started_at", "finished_at",
    })

    def _row_value(r: Any, field: str) -> Any:
        if field in _RUN_COLUMNS:
            return r[field]
        try:
            state = json.loads(r["output_json"]) if r["output_json"] else {}
        except (json.JSONDecodeError, TypeError):
            state = {}
        return state.get(field)

    if aggregate:
        group_by = aggregate.get("group_by", "")
        agg_op = aggregate.get("fn", aggregate.get("op", "count"))
        agg_field = aggregate.get("field", "")

        groups: dict[Any, list[Any]] = {}
        for r in rows:
            gv = _row_value(r, group_by) if group_by else "_all"
            av = _row_value(r, agg_field) if agg_field else 1.0
            groups.setdefault(gv, []).append(av)

        def _agg(values: list[Any], op: str) -> Any:
            nums = [v for v in values if isinstance(v, (int, float))]
            if op == "count":
                return len(values)
            if op == "sum":
                return sum(nums) if nums else 0
            if op == "mean":
                return sum(nums) / len(nums) if nums else None
            if op == "rate":
                total = len(rows) if rows else 1
                return len(values) / total if total else None
            return len(values)

        aggregated = [
            {"group": gv, "value": _agg(vals, agg_op), "n": len(vals)}
            for gv, vals in sorted(groups.items(), key=lambda kv: str(kv[0]))
        ]
        return {
            "aggregated": aggregated,
            "count": len(aggregated),
            "group_by": group_by,
            "agg_op": agg_op,
        }

    result_rows = []
    for r in rows:
        row_dict: dict[str, Any] = {
            "run_id": r["run_id"],
            "branch_def_id": r["branch_def_id"],
            "status": r["status"],
            "actor": r["actor"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
        }
        if select:
            row_dict["fields"] = _extract_fields(r["output_json"], select)
        result_rows.append(row_dict)

    return {"rows": result_rows, "count": len(result_rows)}


# ─────────────────────────────────────────────────────────────────────────────
# Sub-branch invocation helpers
# ─────────────────────────────────────────────────────────────────────────────

#: Sizes the shared sub-branch pool (``_max_child_workers`` is this + 1). It
#: is no longer a depth cap (plan item 6): sub-branch runs are metered per
#: universe instead. Only a BLOCKING version invoke, which waits on this pool
#: while holding one of its threads, is bounded -- by the pool's size.
MAX_INVOKE_BRANCH_DEPTH = 5


def poll_child_run_status(
    base_path: str | Path,
    run_id: str,
    *,
    timeout_seconds: float | None = 300.0,
    poll_interval: float = 1.0,
    expected_actor: str | None = None,
    expected_universe_id: str | None = None,
) -> dict[str, Any]:
    """Block until *run_id* reaches a terminal status or *timeout_seconds* elapses.

    ``timeout_seconds=None`` waits until the run is terminal.

    Returns the run record dict (same shape as ``get_run``).
    Raises ``TimeoutError`` if the run does not terminate in time.
    Raises ``KeyError`` if the run does not exist at poll time.

    When ``expected_actor`` / ``expected_universe_id`` are supplied (invoke_branch
    task 4.2), a run whose ``actor`` / ``queue_universe_id`` does not match is treated
    as ABSENT — the SAME ``KeyError`` as a missing run, so a foreign run id planted in
    parent state cannot be awaited and the await surface is not an existence oracle.
    """
    want_actor = (expected_actor or "").strip()
    want_universe = (expected_universe_id or "").strip()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    while True:
        record = get_run(base_path, run_id)
        if record is None:
            raise KeyError(f"Child run '{run_id}' not found in runs DB.")
        if want_actor and (str(record.get("actor") or "").strip() != want_actor):
            raise KeyError(f"Child run '{run_id}' not found in runs DB.")
        if want_universe and (
            str(record.get("queue_universe_id") or "").strip() != want_universe
        ):
            raise KeyError(f"Child run '{run_id}' not found in runs DB.")
        if record.get("status") in _TERMINAL_STATUSES:
            return record
        if deadline is None:
            time.sleep(poll_interval)
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChildRunAwaitTimeout(
                f"await_branch_run: child run '{run_id}' did not complete "
                f"within {timeout_seconds}s.",
                run_id=run_id,
                child_status=str(record.get("status") or ""),
                child_branch_def_id=str(record.get("branch_def_id") or ""),
                timeout_seconds=timeout_seconds,
            )
        time.sleep(min(poll_interval, remaining))


# ─── Teammate messaging ───────────────────────────────────────────────────────

_VALID_MESSAGE_TYPES = frozenset({
    "request", "response", "broadcast",
    "plan_approval_request", "plan_approval_response",
    "shutdown_request", "shutdown_response",
})


def post_teammate_message(
    base_path: str | Path,
    *,
    from_run_id: str,
    to_node_id: str,
    message_type: str,
    body: dict[str, Any],
    reply_to_id: str | None = None,
) -> dict[str, Any]:
    """Insert a teammate message. Returns the stored message record."""
    import uuid
    from datetime import datetime, timezone

    if not from_run_id:
        raise ValueError("from_run_id is required.")
    if not to_node_id:
        raise ValueError("to_node_id is required.")
    if message_type not in _VALID_MESSAGE_TYPES:
        raise ValueError(
            f"Unknown message_type {message_type!r}; "
            f"valid: {sorted(_VALID_MESSAGE_TYPES)}"
        )
    try:
        body_json = json.dumps(body)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"body must be JSON-serializable: {exc}") from exc

    run_record = get_run(base_path, from_run_id)
    if run_record is None:
        raise KeyError(f"from_run_id '{from_run_id}' not found in runs DB.")

    message_id = str(uuid.uuid4())
    sent_at = datetime.now(timezone.utc).isoformat()

    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO teammate_messages
                (message_id, from_run_id, to_node_id, message_type,
                 body_json, reply_to_id, sent_at, acked)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (message_id, from_run_id, to_node_id, message_type,
             body_json, reply_to_id, sent_at),
        )
    return {
        "message_id": message_id,
        "from_run_id": from_run_id,
        "to_node_id": to_node_id,
        "message_type": message_type,
        "body": body,
        "reply_to_id": reply_to_id,
        "sent_at": sent_at,
        "acked": False,
    }


def read_teammate_messages(
    base_path: str | Path,
    *,
    node_id: str = "",
    since: str | None = None,
    message_types: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return messages for node_id (or all if node_id is empty/broadcast)."""
    initialize_runs_db(base_path)
    clauses: list[str] = []
    params: list[Any] = []

    if node_id:
        clauses.append("(to_node_id = ? OR to_node_id = '*')")
        params.append(node_id)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if message_types:
        placeholders = ",".join("?" * len(message_types))
        clauses.append(f"message_type IN ({placeholders})")
        params.extend(message_types)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = min(max(1, limit), 1000)

    with _connect(base_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM teammate_messages {where} "
            f"ORDER BY sent_at ASC LIMIT ?",
            [*params, limit],
        ).fetchall()

    results = []
    for r in rows:
        try:
            body = json.loads(r["body_json"])
        except (json.JSONDecodeError, TypeError):
            body = {}
        results.append({
            "message_id": r["message_id"],
            "from_run_id": r["from_run_id"],
            "to_node_id": r["to_node_id"],
            "message_type": r["message_type"],
            "body": body,
            "reply_to_id": r["reply_to_id"],
            "sent_at": r["sent_at"],
            "acked": bool(r["acked"]),
        })
    return results


def ack_teammate_message(
    base_path: str | Path,
    *,
    message_id: str,
    node_id: str,
) -> dict[str, Any]:
    """Mark message as acked. Idempotent. Returns acked_at timestamp."""
    from datetime import datetime, timezone

    initialize_runs_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM teammate_messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"message_id '{message_id}' not found.")
        if row["to_node_id"] != node_id and row["to_node_id"] != "*":
            raise PermissionError(
                f"node_id '{node_id}' cannot ack message addressed to "
                f"'{row['to_node_id']}'."
            )
        acked_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE teammate_messages SET acked = 1 WHERE message_id = ?",
            (message_id,),
        )
    return {"message_id": message_id, "acked_at": acked_at}


_ROUTING_EVIDENCE_CAVEAT = (
    "provider_used is populated for runs that used the policy router; "
    "token_count and model are not yet collected (no LLM billing hooks). "
    "latency_ms is derived from started_at / finished_at timestamps."
)

_ROUTING_EVIDENCE_LIMIT_CAP = 50
_ROUTING_EVIDENCE_DEFAULT_LIMIT = 10


# BUG-029: canonical failure_class → actionable_by mapping.
# Imported by `tinyassets.universe_server` so both run-failure classifiers
# (typed-exception path + string-pattern path + this list_recent_runs
# path) emit the same `actionable_by` for the same failure_class.
#
# Values:
#   "host"    — server operator must act (creds, binaries, approvals).
#   "chatbot" — chatbot can fix via another tool call (switch llm_type,
#               raise recursion_limit, retry, republish version).
#   "user"    — chatbot can only escalate raw error to the human user;
#               human judgment may identify a recovery.
#   "none"    — terminal: no fix exists, outcome is final. Chatbot must
#               NOT suggest retry or escalate to user — the run is dead
#               by design (e.g. cancelled by request).
#
# A failure_class missing from this map gets `actionable_by="user"` —
# safe-default escalate, never silently drop the field. Use "none"
# explicitly when the failure is genuinely unrecoverable; the default
# is a conservative "ask the human."
ACTIONABLE_BY: dict[str, str] = {
    # host — server-side configuration / credentials / binaries
    "empty_llm_response": "host",
    "provider_unavailable": "host",
    "provider_subprocess_failed": "host",
    "provider_exhausted": "host",
    "sandbox_unavailable": "host",
    "node_not_approved": "host",
    "permission_denied:approval_required": "host",
    "permission_denied:auth_expired": "host",
    # chatbot: the fix is another tool call (select/bind a registered provider),
    # never a host credential rotation.
    "permission_denied:provider_not_bound": "chatbot",
    # Same owner, different action: their own saved order ran out of capacity.
    # Never "host" -- no host-side change can refill a user's own source.
    "work_model_exhausted": "chatbot",
    # chatbot — recoverable via another tool call
    "code_node_failed": "chatbot",
    "node_not_accepted": "chatbot",
    "effect_budget_exhausted": "chatbot",
    # workspace-node D6: every workspace refusal is the universe's to act on.
    "workspace_checkout_failed": "chatbot",
    "workspace_push_refused": "chatbot",
    "workspace_busy": "chatbot",
    "workspace_pool_busy": "chatbot",
    "workspace_quota_exceeded": "chatbot",
    "workspace_command_timeout": "chatbot",
    "workspace_provision_refused": "chatbot",
    "workspace_provision_failed": "chatbot",
    "workspace_discard_failed": "chatbot",
    "quota_exhausted": "chatbot",
    "provider_overloaded": "chatbot",
    "provider_error": "chatbot",
    "recursion_limit": "chatbot",
    "timeout": "chatbot",
    "context_length_exceeded": "chatbot",
    "state_mutation_conflict": "chatbot",
    "compile_error": "chatbot",
    "snapshot_schema_drift": "chatbot",
    "interrupted": "chatbot",
    "child_receipt_waiting": "chatbot",
    # chatbot — an effect failed for a reason the universe can fix: the far side
    # answered 4xx/5xx (a deleted branch, a missing PR), a packet field, a
    # transform, a stale sha. Live 2026-08-30: every such failure read as
    # "user", so the universe stopped and asked the founder after each one.
    "external_write_failed": "chatbot",
    # user — an effect was refused by AUTHORITY the universe does not hold: a
    # consent it lacks, a grant that is missing/revoked/too narrow, an
    # allow-list or SSRF refusal, its soul's own limits. Only the founder can
    # change that, and the request rail is the channel.
    "external_write_refused": "user",
    # user — the owner's own Custom Rules (harness D1a) stopped the effect:
    # "ask first" waits for their approval, "hand off" is theirs to perform.
    "rule_requires_approval": "user",
    "rule_hand_off": "user",
    # host — the rules store could not be read, so the effect was refused rather
    # than allowed; nothing in the branch or the founder's grants is wrong.
    "rules_unreadable": "host",
    # user — the check before a consequential action could not run (no model
    # connected, or it failed), so the action was held rather than sent.
    "auto_review_unavailable": "user",
    # user — the stored key itself is finished: expired, revoked at the provider,
    # or no longer accepted. Neither a retry (same dead key) nor a widening (the
    # grant was never the problem) can change it; only a new secret can, and only
    # the owner has one. Live 2026-09-16: this landed in `external_write_failed`
    # -> "chatbot", so the universe retried twice and then said "please
    # reconnect" in chat prose, and the connection stayed dead for ten days.
    "credential_rejected": "user",
    # user — the destination's EDGE refused our client before the application
    # saw the request. Nothing in the packet is wrong, the key was never
    # presented to anything that reads keys, and an identical retry gets an
    # identical block. The two real repairs are both the owner's: a client
    # string this service accepts, declared on the connection, or an allowance
    # at the destination. Live 2026-09-30: Cloudflare answered a UA-less POST
    # `error code: 1010`, which landed in `external_write_failed` -> "chatbot",
    # so the universe was told it was "a reason you can fix", retried twice,
    # and the founder never heard about it.
    "destination_blocked_client": "user",
    # user — opaque/internal; chatbot escalates raw error for human judgment
    "unknown": "user",
    "error": "user",
    # none — terminal by design; no fix exists, no escalation needed
    "cancelled": "none",
    "request_budget_exhausted": "none",
}


EXTERNAL_WRITE_FAILED_ACTION = (
    "An effect failed for a reason you can fix. Read external_write_errors for "
    "the node, HTTP status and body: a 404 on a head branch means it no longer "
    "exists (create a fresh one), a 422 usually means the commit or PR it depends "
    "on did not happen, a packet error names the field. Fix that and run again "
    "yourself, in this turn - this is yours to fix. Try at most twice for the same "
    "failure; if it fails the same way a third time, stop and report exactly what "
    "is stuck. Ask the founder only for something only they have (a grant, a "
    "decision)."
)

EFFECT_BUDGET_EXHAUSTED_ACTION = (
    "This run (or this command center's last hour) has used its outbound budget - the "
    "error names which one. Split the work across runs, fetch less per run, or wait "
    "for the hourly window to clear; the budget is usage, not a limit on your graph."
)

WORKSPACE_SUGGESTED_ACTIONS: dict[str, str] = {
    "workspace_checkout_failed": (
        "The checkout did not complete - the error names auth, transport, "
        "verification or fit. Check the repository name and ref, that the "
        "connection holds git_read for exactly that repository, and that it "
        "fits the 4 GiB lease; then run again yourself."
    ),
    "workspace_push_refused": (
        "The push was refused: the default branch is never a target, the ref "
        "must be tiny/<command-center-id>/<slug> and fast-forward, and the bundle must "
        "verify. Commit on a fresh tiny/ branch from the checked-out ref and "
        "push again; host branch protection is the repository owner's to change."
    ),
    "workspace_busy": (
        "Another workspace job of this command center (or the host's single slot) is "
        "running. Wait for it to finish and run again; do not split the same "
        "job across parallel branches."
    ),
    "workspace_pool_busy": (
        "The shared scratch pool is full right now, or startup reconciliation is "
        "still running. Wait a minute and run again; permanent workspaces "
        "(storage: command center) do not use the pool."
    ),
    "workspace_quota_exceeded": (
        "A storage or hourly workspace bound was reached - the error names which "
        "(the 4 GiB lease, the command center's permanent quota, or the hourly jobs/"
        "bytes). Check out less, discard what you no longer need, or wait for "
        "the window named in the error to clear."
    ),
    "workspace_command_timeout": (
        "A ws.run command outlived its timeout and the whole sandbox was ended. "
        "Run a narrower command, raise the node's timeout_seconds (up to 1800) "
        "in place with write_graph (operation=patch, payload "
        '[{"op":"update_node","node_id":"<node>","timeout_seconds":1800}]), '
        "or split the work across nodes; then run again."
    ),
    "workspace_provision_refused": (
        "Provisioning was refused before any network: the manifest line or "
        "lockfile entry named is outside the admitted grammar (registry-pinned "
        "name==version with sha256 hashes; npm registry tarballs with sha512), "
        "or the workspace_provision consent is missing. Fix the manifest in a "
        "commit or raise the consent in the request rail."
    ),
    "workspace_provision_failed": (
        "The resolver or the offline install failed after admission - a wheel "
        "missing for this platform, a cache bound, or an install error in the "
        "sandbox. Read the tail, pin a version that ships a wheel, and run again."
    ),
    "workspace_discard_failed": (
        "The workspace could not be discarded; its capability is already "
        "revoked and the sweeper will retry. Nothing to fix in the branch - if "
        "it persists, report the lease named in the error."
    ),
}

EXTERNAL_WRITE_REFUSED_ACTION = (
    "An effect was refused by authority you do not hold - a consent, a grant that "
    "is missing, revoked or too narrow, an allow-list or SSRF refusal. Retrying "
    "cannot change that. Raise the exact ask in the request rail (extend_http for "
    "a wider reach on a destination you already hold, connect_http for a new one, "
    "or the consent the refusal names), then continue when it is answered."
)

# error_kind values (from the adapter's summary line) that mean the far side of
# the refusal is AUTHORITY the founder holds, not something the universe can fix.
RULE_REQUIRES_APPROVAL_ACTION = (
    "Your owner's rules ask first for this action. Raise ONE request in the rail "
    "that says exactly what you will do and where, continue other work, and run "
    "this again once they approve. Do not retry before then."
)
RULE_HAND_OFF_ACTION = (
    "Your owner's rules hand this action to them: they do it themselves. Raise a "
    "request telling them exactly what to do and where, prepare everything else, "
    "and do not perform or retry the action yourself."
)
AUTO_REVIEW_UNAVAILABLE_ACTION = (
    "The check before this action could not run on your owner's model, so "
    "nothing was sent. Make sure a model is connected, then raise one request "
    "describing the action; do not retry it blindly."
)
REQUEST_BUDGET_EXHAUSTED_ACTION = (
    "The parent turn reached its request budget. The action was not sent. "
    "Preserve recorded progress and report the stop. The owner can request a "
    "continuation; do not retry, schedule a continuation, or ask for approval."
)
RULES_UNREADABLE_ACTION = (
    "Your owner's rules could not be read, so nothing was sent. Nothing in the "
    "branch is wrong; report it and try again later."
)
#: error_kind -> failure class for a refusal by the owner's Custom Rules.
_RULE_REFUSAL_CLASSES = (
    # The auto-review (harness D1d) asked for the owner's approval: the same
    # remedy as an ask-first rule.
    ("auto_review_needs_approval", "rule_requires_approval"),
    ("auto_review_unavailable", "auto_review_unavailable"),
    ("request_budget_exhausted", "request_budget_exhausted"),
    ("rule_ask_first", "rule_requires_approval"),
    ("rule_hand_off", "rule_hand_off"),
    ("rules_unreadable", "rules_unreadable"),
)

_EXTERNAL_WRITE_REFUSED_KINDS = (
    "missing_consent",
    "soul_authority_denied",
    "no_universe_authority",
)
_EXTERNAL_WRITE_REFUSED_WORDS = (
    "consent", "grant", "revoked", "allowlist", "allow-list", "ssrf",
    "not allowed", "scope", "authority refused",
)

#: A CDN edge block, by the one wire format that states it unambiguously.
#:
#: Cloudflare sits in front of a large share of the services a universe builds
#: a channel to (and in front of this platform), and it names its own refusals:
#: the body is literally `error code: NNNN`. The CLIENT-block family is what
#: this class is for -- 1010 (browser/client refused, the one seen live on
#: 2026-09-30), 1012/1013, the 1006-1008 bans, and 1020 (an access rule).
#:
#: This class earns its place regardless of how OFTEN an edge blocks: whenever
#: one does, the advice the row carried before was wrong in every part (see
#: DESTINATION_BLOCKED_CLIENT_ACTION). Frequency changes the priority, never
#: whether the guidance should be correct.
#:
#: Deliberately NOT here: 1015, which is rate limiting. Its repair is to slow
#: down, not to change who we say we are, and guessing them into one class
#: would give one of them the wrong advice. Another CDN's shape gets added when
#: there is a reproduction to add it from -- a guessed pattern in a classifier
#: is a wrong answer waiting for the first real one.
_CDN_CLIENT_BLOCK_CODES = ("1006", "1007", "1008", "1010", "1012", "1013", "1020")
_CDN_CLIENT_BLOCK_RE = re.compile(
    r"error code:\s*(" + "|".join(_CDN_CLIENT_BLOCK_CODES) + r")\b"
)

DESTINATION_BLOCKED_CLIENT_ACTION = (
    "The destination's CDN blocked this request at its edge - the service "
    "itself never saw it. Nothing in your packet is wrong and the stored key "
    "was never presented, so DO NOT rotate or widen anything, and do not retry: "
    "an identical request gets an identical block. Say so plainly and name the "
    "two fixes, both of which are the owner's. Either this service wants a "
    "particular client string, which is declared ONCE on the connection (its "
    "constant headers) rather than per call - raise that as the ask - or the "
    "destination has to allow calls from this platform, which only they can do "
    "at their end. Report the destination and the edge's own error code from "
    "the external_write_errors row so they have something to act on."
)

CREDENTIAL_REJECTED_ACTION = (
    "The far side REJECTED the key stored for this connection - it is expired, "
    "revoked, or no longer accepted. Retrying sends the same dead key and "
    "widening the grant changes nothing: the only fix is a new secret, and only "
    "the owner has one. Do not retry, and do not answer them in prose about "
    "reconnecting something. Raise the ONE card that fixes it, now, in this turn: "
    'write_graph target="pending_request" operation="ask" with '
    '{"action": {"type": "rotate_http", "destination": "<the destination on the '
    'external_write_errors row>"}} and one secret field labelled the way that '
    "service labels its key. It keeps the connection, its endpoints and its "
    "scopes - only the key changes, and they paste once. Then continue when it "
    "is answered."
)

#: A DELIVERED response's status, exactly as ``first_effect_failure`` and
#: ``_collect_external_write_errors`` write it. This phrase exists for no other
#: case, which is what separates "the far side rejected our key" from a refusal
#: the platform made before the wire (those carry a bracketed `[kind]` instead).
_DELIVERED_STATUS_RE = re.compile(r"far side answered http (\d{3})")

#: Generic credential vocabulary -- no service or vendor name appears here, and
#: nothing else in the body is read.
_CREDENTIAL_NOUN = r"(?:tokens?|credentials?|api[ _-]?keys?|bearer|authorization|auth|grant)"
_CREDENTIAL_DEAD = r"(?:invalid|expired|revoked|unauthori[sz]ed|bad|rejected)"
#: At most ONE intervening word, in either order. One word is what the real
#: strings need (`expired_access_token`, `revoked_access_token`, `bad
#: credentials`, `invalid api key`, `token_revoked`, and `invalid or expired
#: token` -- whose `expired token` is adjacent). Two words would admit `invalid
#: repository for token`, which is a permission problem, not a dead key.
_CREDENTIAL_GAP = r"[ _\-]+(?:[a-z0-9]+[ _\-]+)?"
#: `_` is a word character, so `\b` cannot bracket `invalid_token`. These
#: lookarounds treat `_` and `-` as separators, which is what an API error code
#: actually uses.
_CREDENTIAL_DEAD_RE = re.compile(
    rf"(?<![a-z]){_CREDENTIAL_DEAD}(?![a-z]){_CREDENTIAL_GAP}(?<![a-z]){_CREDENTIAL_NOUN}(?![a-z])"
    rf"|(?<![a-z]){_CREDENTIAL_NOUN}(?![a-z]){_CREDENTIAL_GAP}(?<![a-z]){_CREDENTIAL_DEAD}(?![a-z])"
)
#: The COPULAR form, which a one-word gap cannot reach and a wider gap must not
#: be used for. Verbatim from a real 403 body (AWS, "Unknown/Missing Access Key
#: or Session Token"): *"The security token included in the request is
#: invalid."* -- five words between `token` and `invalid`, which the compact rule
#: deliberately will not span (Codex refute-review, P1 #3).
#:
#: The extra reach is safe here in a way it is not for the compact rule, because
#: a copula binds its predicate to its SUBJECT: `<credential noun> ... is
#: invalid` says the credential is invalid, where `invalid <2 words> token` may
#: be saying something else is invalid. Bounded by characters and stopped at a
#: sentence break so it cannot cross clauses.
_CREDENTIAL_DEAD_COPULA_RE = re.compile(
    rf"(?<![a-z]){_CREDENTIAL_NOUN}(?![a-z])[^.;!?]{{0,48}}?"
    r"(?:is|was|are|were|has been|have been)[ _\-]+"
    r"(?:(?:no longer|not)[ _\-]+valid|invalid|expired|revoked|rejected"
    r"|unauthori[sz]ed)(?![a-z])"
)
#: A hard cap on one row's body: the persisted error row previews 160 characters
#: and the raised message 200, each plus a ` [kind]` tail.
_CREDENTIAL_BODY_WINDOW = 220


def _credential_body(lower: str, match: re.Match) -> str:
    """The body of the delivered row ``match`` names, and no other row's.

    A summary carries up to five rows joined by ``"; "``, and a body preview can
    itself contain a semicolon -- so the row boundary is taken as the start of the
    NEXT delivered-status phrase (or the end of the string), then capped. Codex
    refute-review, P1 #2: a fixed 220-character window let a short-bodied 403
    borrow ``invalid_token`` out of a following 404 and classify as a dead key.

    A body long enough to be cut by the producer's own 160/200-character preview
    can still lose its marker past that cut. That is a MISS, which leaves the row
    in its existing class -- the safe direction, and not fixable from the string.
    """
    rest = lower[match.end():]
    following = _DELIVERED_STATUS_RE.search(rest)
    end = following.start() if following else len(rest)
    return rest[:min(end, _CREDENTIAL_BODY_WINDOW)]


def _credential_rejected(lower: str) -> bool:
    """Whether a row of this summary is a delivered response saying the secret
    we presented is finished.

    A 401 means it unconditionally (RFC 7235: the request lacked valid
    authentication credentials, and no retry of the same secret fixes that). A
    403 counts only when the body names the credential ITSELF as invalid,
    revoked or expired -- most 403s say "this key may not do that", which is a
    widening or a provider-side permission, and asking the owner to replace a
    working key would be the wrong ask.
    """
    for match in _DELIVERED_STATUS_RE.finditer(lower):
        status = match.group(1)
        if status == "401":
            return True
        if status == "403":
            body = _credential_body(lower, match)
            if _CREDENTIAL_DEAD_RE.search(body) or _CREDENTIAL_DEAD_COPULA_RE.search(body):
                return True
    return False


def _classify_external_write(lower: str) -> str:
    """Split the "external write failed - ..." summary into the founder's
    (refused by authority) and the universe's (failed, fixable)."""
    for kind in WORKSPACE_FAILURE_KINDS:
        # One actionable class per workspace refusal (workspace-node D6).
        if f"[{kind}]" in lower:
            return kind
    if "[effect_budget_exhausted]" in lower:
        return "effect_budget_exhausted"
    for kind, failure_class in _RULE_REFUSAL_CLASSES:
        if f"[{kind}]" in lower:
            return failure_class
    for kind in _EXTERNAL_WRITE_REFUSED_KINDS:
        if f"[{kind}]" in lower:
            return "external_write_refused"
    # BEFORE both nets below. A CDN edge block is a DELIVERED 4xx, so the
    # credential check reads it next and the refusal-word net reads it after
    # that -- and 1020's own wording ("access denied") is one word away from
    # both. It is neither: the application never saw the request. Matching the
    # edge's own error code first is exact, so it cannot be borrowed by either
    # heuristic (live 2026-09-30).
    if _CDN_CLIENT_BLOCK_RE.search(lower):
        return "destination_blocked_client"
    # BEFORE the refusal-word net below, which is a heuristic over the whole
    # line: a revoked token's own body says "revoked", so a dead key classified
    # as an authority refusal and the agent was told to raise `extend_http` --
    # widening a grant that was never the problem (live 2026-09-16).
    if _credential_rejected(lower):
        return "credential_rejected"
    if any(word in lower for word in _EXTERNAL_WRITE_REFUSED_WORDS):
        return "external_write_refused"
    return "external_write_failed"


def external_write_suggested_action(failure_class: str) -> str:
    if failure_class in WORKSPACE_SUGGESTED_ACTIONS:
        return WORKSPACE_SUGGESTED_ACTIONS[failure_class]
    if failure_class == "effect_budget_exhausted":
        return EFFECT_BUDGET_EXHAUSTED_ACTION
    if failure_class == "external_write_refused":
        return EXTERNAL_WRITE_REFUSED_ACTION
    if failure_class == "rule_requires_approval":
        return RULE_REQUIRES_APPROVAL_ACTION
    if failure_class == "rule_hand_off":
        return RULE_HAND_OFF_ACTION
    if failure_class == "rules_unreadable":
        return RULES_UNREADABLE_ACTION
    if failure_class == "request_budget_exhausted":
        return REQUEST_BUDGET_EXHAUSTED_ACTION
    if failure_class == "auto_review_unavailable":
        return AUTO_REVIEW_UNAVAILABLE_ACTION
    if failure_class == "destination_blocked_client":
        return DESTINATION_BLOCKED_CLIENT_ACTION
    if failure_class == "credential_rejected":
        return CREDENTIAL_REJECTED_ACTION
    if failure_class == "external_write_failed":
        return EXTERNAL_WRITE_FAILED_ACTION
    return ""

_EMPTY_LLM_RESPONSE_ACTION = (
    "Ask the host to check get_status provider availability/cooldowns and fix "
    "provider credentials or CLI, then rerun; only switch llm_type if get_status "
    "shows another provider available."
)


def _classify_failure(run: dict) -> str:
    """Return a short failure class string from a run record."""
    error = run.get("error") or ""
    status = run.get("status", "")
    if status == RUN_STATUS_CANCELLED:
        return "cancelled"
    if status == RUN_STATUS_INTERRUPTED:
        output = run.get("output") or {}
        if isinstance(output, dict):
            gate = output.get("child_invocation_receipt_gate")
            if isinstance(gate, dict) and gate.get("status") == "receipt_waiting":
                return "child_receipt_waiting"
        return "interrupted"
    if not error:
        return ""
    lower = error.lower()
    if lower.startswith("[request_budget_exhausted] "):
        return "request_budget_exhausted"
    if lower.startswith("external write failed"):
        return _classify_external_write(lower)
    from tinyassets.exceptions import WorkModelExhaustedError

    if WorkModelExhaustedError.MESSAGE in lower:
        # The owner's own order ran out. Its evidence suffix names the owner's
        # model ids and classified capacity classes ("credit_exhausted"), so
        # this narrow known prefix must precede every substring net below: a
        # model id containing "timeout" is not a timed-out run.
        return "work_model_exhausted"
    from tinyassets.providers.model_pins import PIN_REFUSAL_MARKER

    if PIN_REFUSAL_MARKER in lower:
        # A node pin naming no single source; the same class `api.runs` gives.
        return "permission_denied:provider_not_bound"
    from tinyassets.exceptions import AllProvidersExhaustedError, ProviderAuthorityHeldError
    from tinyassets.providers.diagnostics import CHAIN_STATE_MARKER, held_attempt_diagnosis

    diagnosis = held_attempt_diagnosis(error)
    if diagnosis is not None:
        # A held or single-source attempt persists its redacted `[chain_state]:`
        # evidence, and the SHARED typed classifier reads the attempt's own
        # class off it. `api.runs` returns this same string for this same row:
        # the nets below would instead read a word out of the provider's
        # free-text detail, the owner's model id or their connection id -- a
        # model called "timeout-model" that failed auth is not a timed-out run,
        # and one surface saying `timeout` while the other says `auth_invalid`
        # is the defect.
        return diagnosis.run_class
    if (ProviderAuthorityHeldError.ATTEMPT_MESSAGE in lower
            or AllProvidersExhaustedError.NO_WIDENING_MESSAGE in lower):
        # A single-source raise whose cause this daemon does not name keeps its
        # existing generic class (an exhausted chain is a true thing to say),
        # but it is still classified on the typed message, never on the
        # evidence JSON's free text.
        lower = lower.split(CHAIN_STATE_MARKER, 1)[0]
    if "empty" in lower and ("llm" in lower or "response" in lower or "provider" in lower):
        return "empty_llm_response"
    if lower.startswith("workspace command timeout"):
        # Before the generic timeout: the jail was ended for a ws.run that
        # outlived its budget (workspace-node D6).
        return "workspace_command_timeout"
    if "timeout" in lower:
        return "timeout"
    if "exhausted" in lower or "cooldown" in lower:
        return "provider_exhausted"
    if any(f"code runs only in the {word} that authored it" in lower
           for word in ("command center", "universe")):  # pre-rename records
        # A public foreign branch with code was run directly (design D2): the
        # fix is a remix, one tool call away.
        return "node_not_accepted"
    if "code node '" in lower:
        # A source_code node's sandboxed run failed (design D2): the universe
        # wrote that code and can fix it - the message carries the stderr tail.
        return "code_node_failed"
    if "sandbox" in lower or "bwrap" in lower:
        return "sandbox_unavailable"
    return "error"


def _routing_evidence_text(run: dict, latency_ms: float | None) -> str:
    """Render a 1-line chatbot-legible summary for a run record."""
    rid = run.get("run_id", "?")
    status = run.get("status", "?")
    bid = run.get("branch_def_id", "?")
    if latency_ms is not None:
        lat = f"{latency_ms / 1000:.2f}s"
        return f"{rid} — {status} in {lat} on {bid}"
    return f"{rid} — {status} on {bid}"


def list_recent_runs(
    base_path: str | Path,
    *,
    branch_def_id: str = "",
    limit: int = _ROUTING_EVIDENCE_DEFAULT_LIMIT,
) -> list[dict]:
    """Return last-N run records shaped for the get_routing_evidence MCP action.

    Each record includes derived ``latency_ms`` (from timestamps), a
    ``failure_class`` label, a ``suggested_action`` hint, and a ``caveat``
    noting absent provider/token fields. Limit is capped at
    ``_ROUTING_EVIDENCE_LIMIT_CAP`` to prevent token blowout.
    """
    effective_limit = min(max(1, int(limit)), _ROUTING_EVIDENCE_LIMIT_CAP)
    raw = list_runs(base_path, branch_def_id=branch_def_id, limit=effective_limit)

    results: list[dict] = []
    for run in raw:
        started = run.get("started_at")
        finished = run.get("finished_at")
        latency_ms: float | None = None
        if started is not None and finished is not None:
            try:
                # started_at / finished_at may be Unix float or ISO string.
                def _to_float(v: object) -> float | None:
                    if isinstance(v, (int, float)):
                        return float(v)
                    s = str(v)
                    if "T" in s:
                        from datetime import datetime as _dt
                        try:
                            return _dt.fromisoformat(s).timestamp()
                        except ValueError:
                            return _dt.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()
                    try:
                        return float(s)
                    except ValueError:
                        return None
                s_ts = _to_float(started)
                f_ts = _to_float(finished)
                if s_ts is not None and f_ts is not None:
                    latency_ms = (f_ts - s_ts) * 1000
            except Exception:  # noqa: BLE001 — best-effort
                pass

        failure_class = _classify_failure(run)
        suggested_action = ""
        if failure_class == "empty_llm_response":
            suggested_action = _EMPTY_LLM_RESPONSE_ACTION
        elif failure_class == "provider_exhausted":
            suggested_action = "Wait for provider cooldown or add an alternative provider."
        elif failure_class == "timeout":
            suggested_action = "Increase node timeout or simplify the prompt."
        elif failure_class == "node_not_accepted":
            suggested_action = (
                "This branch's code was authored elsewhere. Remix it into your command center "
                "(write_graph with fork_from) and run your copy."
            )
        elif failure_class == "code_node_failed":
            suggested_action = (
                "Your code node raised or exited non-zero; the error carries its stderr "
                "tail. Fix run() in that node with write_graph "
                "(operation=patch, payload op=update_node) and run again."
            )
        elif failure_class == "sandbox_unavailable":
            suggested_action = "Enable unprivileged user namespaces or run on a bwrap-capable host."
        elif failure_class == "cancelled":
            suggested_action = "Run was cancelled by request."
        elif failure_class == "interrupted":
            suggested_action = (
                "Run was interrupted. Resume is not exposed by the advertised "
                "handles; rerun it with run_graph."
            )
        elif failure_class == "child_receipt_waiting":
            suggested_action = (
                "Wait for the child run to complete. Attaching an existing "
                "child run is not exposed by the advertised handles."
            )
        elif failure_class in ("external_write_failed", "external_write_refused",
                               "rule_requires_approval", "rule_hand_off",
                               "rules_unreadable", "auto_review_unavailable",
                               "request_budget_exhausted"):
            suggested_action = external_write_suggested_action(failure_class)
        elif failure_class == "error":
            suggested_action = "Check error field for details; re-run after fixing root cause."

        results.append({
            "text": _routing_evidence_text(run, latency_ms),
            "run_id": run.get("run_id"),
            "branch_def_id": run.get("branch_def_id"),
            "run_name": run.get("run_name"),
            "status": run.get("status"),
            "actor": run.get("actor"),
            "started_at": started,
            "finished_at": finished,
            "latency_ms": latency_ms,
            "error": run.get("error"),
            "last_node_id": run.get("last_node_id"),
            "failure_class": failure_class,
            "suggested_action": suggested_action,
            # Empty failure_class → empty actionable_by (run wasn't a
            # failure). Otherwise look it up; default to "user" for any
            # class not in the table so the field is never silently
            # dropped.
            "actionable_by": (
                ACTIONABLE_BY.get(failure_class, "user") if failure_class else ""
            ),
            "provider_used": run.get("provider_used"),
            "token_count": run.get("token_count"),
            "caveat": _ROUTING_EVIDENCE_CAVEAT,
        })

    return results


__all__ = [
    "RUN_STATUS_QUEUED",
    "RUN_STATUS_RUNNING",
    "RUN_STATUS_COMPLETED",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_CANCELLED",
    "RUN_STATUS_INTERRUPTED",
    "NODE_STATUS_PENDING",
    "NODE_STATUS_RUNNING",
    "NODE_STATUS_RAN",
    "NODE_STATUS_FAILED",
    "ACTIONABLE_BY",
    "BranchTaskRunReservationConflict",
    "ChildRunAttachmentError",
    "ChildRunAwaitTimeout",
    "RunCancelledError",
    "RunExecutionAuthorityLost",
    "MissingRequiredInputs",
    "RunOutcome",
    "RunStepEvent",
    "VALID_RECEIPT_TYPES",
    # Phase 4 storage helpers
    "add_judgment",
    "attach_existing_child_run",
    "build_node_status_map",
    "create_run",
    "execute_branch",
    "execute_branch_version",
    "execute_branch_async",
    "find_node_snapshot",
    "get_future",
    "get_lineage",
    "get_run",
    "get_run_by_branch_task_id",
    "initialize_runs_db",
    "is_cancel_requested",
    "latest_terminal_run",
    "list_events",
    "list_judgments",
    "list_node_edit_audits",
    "list_run_receipts",
    "list_runs",
    "latest_run_by_name",
    "list_recent_runs",
    "node_output_from_run",
    "record_event",
    "record_lineage",
    "record_node_edit_audit",
    "record_run_receipt",
    "recover_in_flight_runs",
    "ensure_workspace_reconciled",
    "request_cancel",
    "runs_db_path",
    "shutdown_executor",
    "update_run_status",
    "wait_for",
    "query_runs",
    "poll_child_run_status",
    "MAX_INVOKE_BRANCH_DEPTH",
    "post_teammate_message",
    "preflight_required_inputs",
    "read_teammate_messages",
    "ack_teammate_message",
]
