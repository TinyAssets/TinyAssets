"""Common internal worker for a reserved run-owned execution envelope.

Not a public intake or a second queue. Origin adapters atomically reserve the
existing run and envelope, then supply a trusted current-authority preparer.
No caller-supplied graph/inputs are executed. All legacy delivery dispatch must
be fenced before moving that origin here. Managed invocation depends on the
cloud lane's validated `_execution_guard` seam; no unsafe compatibility fallback.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tinyassets import runs
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.branches import BranchDefinition
from tinyassets.owner_activity import owner_work
from tinyassets.run_admission_envelope import encode_admission_envelope
from tinyassets.run_input_origin import OriginHeld
from tinyassets.scoped_reset import (
    _assert_recovery_state_is_clean,
    acquire_maintenance_barrier,
)
from tinyassets.storage import _connect as author_connection
from tinyassets.storage import run_input_admissions as admissions
from tinyassets.storage.run_execution_lock import try_run_execution_lock

logger = logging.getLogger(__name__)
_submitted = set()
_submitted_lock = threading.Lock()


@dataclass(frozen=True)
class PreparedRunExecution:
    """Trusted origin service result, not external payload or a new grant.

    `prepare(base, envelope, *, author_conn, runs_conn)` runs under author -> runs
    fences and must check
    current origin-specific execution authority. It must not call a provider.
    Optional `bind_provider(base, envelope, branch)` runs only after the durable
    start commit and inside the freshly prepared identity, outside DB locks.
    """

    identity: Identity
    actor: str
    bind_provider: Callable | None = None
    recursion_limit: int = runs.DEFAULT_RECURSION_LIMIT
    concurrency_budget_override: int | None = None
    on_node_status: Callable | None = None


def _seed(base, run_id):
    with runs._connect(base) as conn:
        row = conn.execute(
            "SELECT owner_id,universe_id FROM run_input_admissions WHERE run_id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def _terminal_unstarted(
    base, run_id, seed, guard, status, message, *, current_attempt_started=False,
):
    # Ownership is immutable for admitted runs. Never terminalize a corrupt or
    # reassigned row as a side effect of failing the envelope ownership check.
    with runs._connect(base) as conn:
        row = conn.execute(
            "SELECT status FROM runs WHERE run_id=? AND owner_user_id=? AND queue_universe_id=?",
            (run_id, seed["owner_id"], seed["universe_id"]),
        ).fetchone()
    if row is None or row["status"] != "queued":
        return
    if not current_attempt_started:
        with runs._connect(base) as conn:
            marker = conn.execute(
                "SELECT execution_started_at,claim_token FROM run_input_admissions WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if marker is None or marker[0] is not None or marker[1] is not None:
            return  # Prior/corrupt started evidence is held, never generic failure.
    # This managed-family CAS seam is supplied by the cloud lifecycle lane.
    # It preserves concurrent cancellation and refuses running/terminal rows.
    # Do not fall back to an unconditional status write on an older runtime.
    runs.terminalize_unstarted_run(
        base,
        run_id=run_id,
        execution_guard=guard,
        status=status,
        error=message,
    )


@owner_work
def _work(base, run_id, prepare):
    _require_execution_hooks()
    # Existing reset barrier is outermost. This path must not initialize or
    # recover a data root; admission already created it under service authority.
    root = Path(base).absolute()
    with acquire_maintenance_barrier(root, exclusive=False, timeout=5.0):
        _assert_recovery_state_is_clean(root)
        _work_under_maintenance_barrier(base, run_id, prepare)


def _require_execution_hooks():
    """Refuse partial-runtime activation before durable marker or provider use."""
    try:
        invoke = getattr(runs, "_invoke_prepared_branch")
        terminal = getattr(runs, "terminalize_unstarted_run")
        if not callable(invoke) or not callable(terminal):
            raise TypeError("hooks must be callable")
        inspect.signature(invoke).bind_partial(None, _execution_guard=None)
        inspect.signature(terminal).bind_partial(
            None, run_id="", execution_guard=None, status="failed", error=""
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("compatible admitted execution hooks are required") from exc


def _work_under_maintenance_barrier(base, run_id, prepare):
    with try_run_execution_lock(base, run_id=run_id) as guard:
        if guard is None:
            return  # Another process owns execution; do not change its status.
        seed = _seed(base, run_id)
        if seed is None:
            return
        invocation_dispatched = False
        current_attempt_started = False
        try:
            with author_connection(base) as authority:
                authority.execute("BEGIN IMMEDIATE")
                with runs._connect(base) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    guard.require_held(conn)
                    state = admissions.recovery_state_in_transaction(conn, guard, **seed)
                    if state == "terminal":
                        return
                    if state == "pending":
                        envelope = admissions.load_in_transaction(conn, run_id=run_id, **seed)
                        actor = conn.execute(
                            "SELECT actor FROM runs WHERE run_id=?", (run_id,)
                        ).fetchone()[0]
                        prepared = prepare(base, envelope, author_conn=authority, runs_conn=conn)
                        if (
                            type(prepared) is not PreparedRunExecution
                            or not isinstance(prepared.identity, Identity)
                            or prepared.identity.user_id != seed["owner_id"]
                            or prepared.actor != actor
                            or type(prepared.recursion_limit) is not int
                            or prepared.recursion_limit <= 0
                            or (
                                prepared.concurrency_budget_override is not None
                                and (
                                    type(prepared.concurrency_budget_override) is not int
                                    or prepared.concurrency_budget_override <= 0
                                )
                            )
                            or (
                                prepared.on_node_status is not None
                                and not callable(prepared.on_node_status)
                            )
                        ):
                            raise ValueError("invalid prepared run identity")
                        # A preparer chooses execution bindings, never an alternate
                        # graph or input body. Reload the authoritative envelope.
                        envelope = admissions.load_in_transaction(conn, run_id=run_id, **seed)
                        snapshot = dict(envelope["snapshot"])
                        if envelope["branch_version_id"] is not None:
                            # Match runs._load_branch_version: published pins
                            # omit presentation names. Materialize only the
                            # detached runtime object, never change the pin/hash.
                            snapshot.setdefault("name", snapshot.get("branch_def_id", ""))
                        branch = BranchDefinition.from_dict(snapshot)
                        if branch.validate():
                            raise ValueError("admitted run snapshot invalid")
                        inputs = envelope["inputs"]
                        runs.preflight_required_inputs(branch, inputs)
                        if not admissions.start_in_transaction(conn, guard, **seed):
                            return
                        current_attempt_started = True
            if state == "interrupted":
                # OS ownership proves no cooperating worker holds the run, NOT
                # that an old managed namespace is empty. Even queued+started
                # is ambiguous. Cloud's exact kernel/epoch recovery owns that
                # terminal transition; retain marker/debt and never replay.
                logger.warning("Admitted run awaits exact execution recovery: %s", run_id)
                return
            if state == "cancelled":
                _terminal_unstarted(
                    base, run_id, seed, guard, "cancelled", "Cancelled before execution."
                )
                return
            with identity_context(prepared.identity):
                # This seam EXECUTES the reserved run, so it admits it. The
                # envelope is encoded from the branch materialized out of the
                # RELOADED authoritative admission envelope above -- not the
                # preparer's return value, which chooses execution bindings
                # only -- together with the execution choices actually passed
                # to _invoke_prepared_branch below and the run row's own
                # identity. Captured strictly before dispatch; guarded IS NULL,
                # so a re-prepared attempt re-supplies an identical envelope
                # and nothing is replayed.
                run_name, version_id = runs._reserved_run_admission_identity(base, run_id)
                admission_envelope = encode_admission_envelope(
                    branch,
                    recursion_limit=prepared.recursion_limit,
                    concurrency_budget_override=prepared.concurrency_budget_override,
                    run_name=run_name,
                    branch_version_id=version_id,
                )
                runs._initialize_prepared_run(
                    base, run_id=run_id, branch=branch, actor=prepared.actor,
                    admission_envelope=admission_envelope,
                )
                provider = (
                    prepared.bind_provider(base, envelope, branch)
                    if prepared.bind_provider is not None
                    else None
                )
                invocation_dispatched = True
                runs._invoke_prepared_branch(
                    base,
                    run_id=run_id,
                    branch=branch,
                    inputs=inputs,
                    actor=prepared.actor,
                    provider_call=provider,
                    recursion_limit=prepared.recursion_limit,
                    concurrency_budget_override=prepared.concurrency_budget_override,
                    on_node_status=prepared.on_node_status,
                    enqueue_universe_id=seed["universe_id"],
                    _execution_guard=guard,
                )
        except OriginHeld:
            # Invalid/unknown origin is not evidence this queued run failed or
            # is safe to retry. Preserve row and start marker for investigation.
            logger.exception("Admitted run origin remains held: %s", run_id)
        except Exception:
            logger.exception("Admitted run processing failed: %s", run_id)
            if not invocation_dispatched:
                try:
                    _terminal_unstarted(
                        base,
                        run_id,
                        seed,
                        guard,
                        "failed",
                        "Admitted run processing failed; see owner logs.",
                        current_attempt_started=current_attempt_started,
                    )
                except Exception:
                    logger.exception("Admitted run settlement failed: %s", run_id)


@owner_work
def dispatch_admitted_run(base, *, run_id, prepare, on_settled=None):
    """Submit same reserved run to the existing executor in a fresh context.

    Must be called only by a reviewed internal origin adapter, not selected from
    external input. A local Future is deduplication only, never death evidence.
    `on_settled` is a trusted notification after worker scopes unwind, NOT proof
    of terminal state. It may project existing canonical terminal truth only;
    durable projection repair remains the origin's existing recovery mechanism.
    """
    if not callable(prepare):
        raise TypeError("trusted admitted-run preparer required")
    if on_settled is not None and not callable(on_settled):
        raise TypeError("trusted admitted-run observer required")
    _require_execution_hooks()
    key = (str(runs.runs_db_path(base).resolve()), run_id)
    with _submitted_lock:
        if key in _submitted:
            return False
        _submitted.add(key)
    try:
        future = runs._get_executor(
            invocation_depth=0, pool_key=runs.run_pool_key_for_run(base, run_id),
        ).submit(
            contextvars.Context().run,
            _work,
            base,
            run_id,
            prepare,
        )
        runs._track_future(run_id, future)

        def finished(_future):
            with _submitted_lock:
                _submitted.discard(key)
            if on_settled is not None:
                try:
                    # Future completion follows worker/SQL/guard unwind, or
                    # cancellation before the worker starts. Neither proves a
                    # terminal outcome; the observer must read canonical truth.
                    on_settled(base, run_id)
                except Exception:
                    logger.exception("Admitted run settlement observation failed: %s", run_id)

        future.add_done_callback(finished)
        return True
    except BaseException:
        with _submitted_lock:
            _submitted.discard(key)
        raise
