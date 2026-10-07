"""Fantasy Author daemon entry point.

Usage::

    python -m fantasy_author [--universe PATH] [--no-tray] [--db PATH]

Builds the universe graph, compiles it with SqliteSaver, wires the
desktop tray and dashboard, and runs the daemon loop until stopped.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import time
import warnings
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tinyassets import idle_cycle
from tinyassets.universe_soul import premise_from_soul, read_legacy_premise

# Suppress langchain-core Pydantic V1 deprecation warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality",
    category=UserWarning,
    module=r"langchain_core\._api\.deprecation",
)

# Imports below intentionally follow the warnings filter above so that
# `langchain_core` (pulled in transitively) loads with the filter already
# active. noqa: E402 acknowledges the ordering.
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

# Phase D: importing `branch_registrations` registers
# ("fantasy_author", "universe_cycle_wrapper") in the workflow domain
# registry. Must happen before _run_graph under
# TINYASSETS_UNIFIED_EXECUTION=1 can compile its Branch.
import fantasy_daemon.branch_registrations  # noqa: E402, F401
import fantasy_daemon.runtime as runtime  # noqa: E402
from fantasy_daemon.branch_registrations import clear_restartable_soft_stop  # noqa: E402
from fantasy_daemon.desktop.dashboard import DashboardHandler  # noqa: E402
from fantasy_daemon.desktop.notifications import NotificationManager  # noqa: E402
from fantasy_daemon.desktop.tray import TrayApp  # noqa: E402
from fantasy_daemon.graphs.universe import build_universe_graph  # noqa: E402
from fantasy_daemon.memory.ingestion import (  # noqa: E402
    IngestionPriority,
    ProgressiveIngestor,
)
from fantasy_daemon.memory.manager import MemoryManager  # noqa: E402
from fantasy_daemon.memory.promises import SeriesPromiseTracker  # noqa: E402
from fantasy_daemon.memory.versioning import OutputVersionStore  # noqa: E402
from fantasy_daemon.providers.claude_provider import ClaudeProvider  # noqa: E402
from fantasy_daemon.providers.codex_provider import CodexProvider  # noqa: E402
from fantasy_daemon.providers.ollama_provider import OllamaProvider  # noqa: E402
from fantasy_daemon.providers.router import ProviderRouter  # noqa: E402
from tinyassets.checkpointing import apply_configured_checkpoint_retention  # noqa: E402

logger = logging.getLogger("fantasy_author")

_UNIVERSE_CYCLE_BRANCH_IDS = frozenset(
    {
        "fantasy_author/universe-cycle",
        "fantasy_author:universe_cycle_wrapper",
    }
)

_DEFAULT_BRANCH_TASK_HEARTBEAT_INTERVAL_S = 30.0


def _first_trace(output: dict[str, Any]) -> dict[str, Any]:
    """Extract the first quality_trace entry from node output."""
    traces = output.get("quality_trace")
    if traces and isinstance(traces, list) and len(traces) > 0:
        return traces[0]
    return {}


# ---------------------------------------------------------------------------
# Provider bootstrapping
# ---------------------------------------------------------------------------


def _build_provider_router() -> ProviderRouter:
    """Instantiate a ProviderRouter with all available providers."""
    from tinyassets.providers.base import subscription_auth_health

    # Inject the subscription-login probe so the router skips dead-auth
    # providers in fallback (and fails a dead pinned writer loud) instead of
    # burning failed attempts — 2026-06-25 loop-wedge follow-up.
    router = ProviderRouter(auth_health=subscription_auth_health)

    # Subprocess providers — skip registration when the binary is absent
    # (cloud hosts). Constructors never raise; binary probe happens here
    # so the router doesn't waste 30s+ cooldown on a missing CLI.
    if ClaudeProvider.is_available():
        router.register(ClaudeProvider())
        logger.info("Registered ClaudeProvider")
    else:
        logger.info("claude binary not found — ClaudeProvider skipped")

    if CodexProvider.is_available():
        router.register(CodexProvider())
        logger.info("Registered CodexProvider")
    else:
        logger.info("codex binary not found — CodexProvider skipped")

    # Local provider.
    try:
        router.register(OllamaProvider())
    except Exception:
        logger.warning("Ollama not available; local fallback disabled")

    logger.info("Registered providers: %s", router.available_providers)
    return router


# ---------------------------------------------------------------------------
# Phase D — TINYASSETS_UNIFIED_EXECUTION flag and unified graph builder
# ---------------------------------------------------------------------------


def _workflow_unified_execution_enabled() -> bool:
    """Read the Phase D flag. Mirrors `_gates_enabled()` from
    `tinyassets/universe_server.py`.
    """
    return os.environ.get(
        "TINYASSETS_UNIFIED_EXECUTION",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _soul_loop_dispatch_enabled() -> bool:
    """Capability gate for soul-declared loop dispatch (Option A, ships dark).

    When ON, the daemon runs a souled universe's declared
    ``loop_branch_def_id`` (resolved via ``_universe_loop_dispatch``) directly
    through ``execute_branch`` — the same path that runs claimed BranchTasks,
    so the user-built loop gets its own state schema and the trusted in-node
    enqueue context. Default OFF: a soulless / legacy universe is untouched and
    keeps running the fantasy cycle. See
    docs/design-notes/2026-06-03-soul-loop-dispatch-activation-plan.md.
    """
    return os.environ.get(
        "TINYASSETS_SOUL_LOOP_DISPATCH",
        "",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _dispatcher_startup(universe_path: Path) -> None:
    """Phase E startup hook: reclaim expired-lease tasks + run GC.

    Invariant §4.3 #7 (claimed→pending recovery) and §4.3 #10
    (terminal-task archive). One-shot at ``_run_graph`` entry. Safe
    regardless of dispatcher flag: recovery and GC keep the queue
    file healthy even when the dispatcher is off.

    Uses lease-aware :func:`reclaim_expired_leases`, NOT the blanket
    :func:`recover_claimed_tasks`. The blanket reset is unsafe in the
    multi-worker fleet (each worker shares ``/data``): a starting
    worker would reset *every* ``running`` row — including tasks a live
    peer worker is mid-execution on — back to ``pending``, so the task
    gets re-claimed and double-finalized (the ``Invalid transition``
    crash + lost results that wedged the loop, 2026-06-25 incident).
    The lease-aware sweep only reclaims rows whose lease actually
    expired (a wedged/dead worker), leaving healthy peers untouched.
    """
    try:
        from tinyassets.branch_tasks import (
            garbage_collect,
            reclaim_expired_leases,
            reclaim_predecessor_tasks,
        )

        # Redeploy-churn recovery: a SIGKILLed predecessor of THIS worker_id
        # left its running tasks orphaned with a still-valid (~30min) lease. At
        # our startup we have not claimed anything yet, so any running row under
        # our own worker_id is that predecessor's orphan — reclaim it in seconds
        # instead of waiting out the TTL. Scoped to our own id, so unlike the
        # old blanket reset it never steals a live peer's task (2026-06-25 wedge).
        # Require a genuinely UNIQUE worker id. "cloud-droplet" is the historic
        # shared fallback host-user id, which several separately-started daemons
        # could all carry — and reclaiming "our own" non-unique id would steal a
        # live twin's task (Codex review). Defined locally because its previous
        # home, the host-run cloud_worker fleet, is deleted: nothing runs outside
        # a user's universe (ADR-009, 2026-08-29). Kept as an exclusion because
        # an operator may still export the shared id by hand.
        shared_fallback_worker_id = "cloud-droplet"

        worker_id = os.environ.get("TINYASSETS_WORKER_ID", "").strip()
        if worker_id and worker_id != shared_fallback_worker_id:
            reclaim_predecessor_tasks(universe_path, worker_id=worker_id)
        # reclaim_leaseless=True: startup is the one safe place to also reset
        # running rows that carry no lease (pre-lease-era / corrupt orphans),
        # which the lease-only sweep skips and would otherwise strand forever
        # (Codex cross-family review, 2026-06-25).
        reclaim_expired_leases(universe_path, reclaim_leaseless=True)
        garbage_collect(universe_path)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Phase E dispatcher_startup failed for %s",
            universe_path,
        )


def _phase_f_enabled() -> bool:
    """Read ``TINYASSETS_GOAL_POOL``. Default OFF."""
    value = os.environ.get("TINYASSETS_GOAL_POOL", "off")
    return value.strip().lower() in {"on", "1", "true", "yes"}


def _paid_market_enabled() -> bool:
    """Read ``TINYASSETS_PAID_MARKET``. Default OFF. Phase G flag."""
    value = os.environ.get("TINYASSETS_PAID_MARKET", "off")
    return value.strip().lower() in {"on", "1", "true", "yes"}


def _resolve_loop_daemon_context(
    universe_path: Path,
    universe_id: str,
) -> dict[str, Any]:
    """Resolve the daemon identity used by the autonomous loop.

    A configured env var is authoritative and fails loudly if invalid. Without
    an env override, the loop opts into the host-marked project default soul
    daemon when one exists, then falls back to the historical soulless id.
    """
    fallback = {
        "daemon_id": f"daemon-{universe_id}",
        "source": "legacy_fallback",
        "has_soul": False,
        "soul_text": "",
        "soul_hash": "",
        "domain_claims": [],
        "daemon_wiki_context": "",
        "daemon_wiki_status": {},
    }
    env_name = "TINYASSETS_LOOP_DAEMON_ID"
    override = os.environ.get(env_name, "").strip()
    if not override:
        env_name = "TINYASSETS_DAEMON_ID"
        override = os.environ.get(env_name, "").strip()

    try:
        from tinyassets.daemon_registry import (
            get_daemon,
            select_project_loop_daemon,
        )
        from tinyassets.daemon_wiki import read_daemon_wiki_context
        from tinyassets.storage import data_dir

        base_path = data_dir()
        if override:
            daemon = get_daemon(
                base_path,
                daemon_id=override,
                include_soul=True,
            )
            source = f"env:{env_name}"
        else:
            daemon = select_project_loop_daemon(base_path, include_soul=True)
            if daemon is None:
                return fallback
            source = "project_loop_default"

        wiki_context = ""
        wiki_status: dict[str, Any] = {}
        if daemon.get("has_soul"):
            wiki_packet = read_daemon_wiki_context(
                base_path,
                daemon_id=daemon["daemon_id"],
                max_chars=6000,
            )
            wiki_context = str(wiki_packet.get("context", ""))
            wiki_status = dict(wiki_packet.get("memory_status") or {})
        return {
            "daemon_id": str(daemon["daemon_id"]),
            "source": source,
            "has_soul": bool(daemon.get("has_soul")),
            "soul_text": str(daemon.get("soul_text") or ""),
            "soul_hash": str(daemon.get("soul_hash") or ""),
            "domain_claims": list(daemon.get("domain_claims") or []),
            "daemon_wiki_context": wiki_context,
            "daemon_wiki_status": wiki_status,
        }
    except KeyError as exc:
        if override:
            raise RuntimeError(
                f"{env_name}={override!r} does not match a registered daemon"
            ) from exc
        logger.exception(
            "loop daemon lookup failed for universe %s at %s",
            universe_id,
            universe_path,
        )
        return fallback
    except Exception:
        if override:
            raise
        logger.exception(
            "project loop daemon lookup failed for universe %s at %s",
            universe_id,
            universe_path,
        )
        return fallback


def _record_loop_daemon_signal(
    loop_daemon: dict[str, Any],
    *,
    universe_path: Path,
    source_id: str,
    outcome: str,
    summary: str,
    details: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort learning signal for a soul-bearing loop daemon."""
    if not loop_daemon.get("has_soul"):
        return
    daemon_id = str(loop_daemon.get("daemon_id") or "")
    if not daemon_id:
        return
    try:
        from tinyassets.daemon_wiki import record_daemon_signal
        from tinyassets.storage import data_dir

        merged_metadata = {
            "loop_daemon": True,
            "universe_path": str(Path(universe_path).resolve()),
            "daemon_source": loop_daemon.get("source", ""),
            **(metadata or {}),
        }
        record_daemon_signal(
            data_dir(),
            daemon_id=daemon_id,
            source_kind="node",
            source_id=source_id,
            outcome=outcome,
            summary=summary,
            details=details,
            metadata=merged_metadata,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "daemon wiki signal write failed for %s source=%s",
            daemon_id,
            source_id,
        )


def _run_branch_task_producers_if_enabled(universe_path: Path) -> int:
    """Phase F: call registered BranchTaskProducers at cycle boundary.

    Under flag-off, no-op. Under flag-on, reads the universe's
    subscriptions and invokes ``run_branch_task_producers_into_queue``.
    """
    if not _phase_f_enabled():
        return 0
    try:
        from tinyassets.dispatcher import run_branch_task_producers_into_queue
        from tinyassets.subscriptions import list_subscriptions

        goals = list_subscriptions(universe_path)
        return run_branch_task_producers_into_queue(
            universe_path,
            subscribed_goals=goals,
        )
    except Exception:  # noqa: BLE001
        logger.exception("run_branch_task_producers failed")
        return 0


def _try_dispatcher_pick(
    universe_path: Path,
    daemon_id: str,
) -> tuple[Any | None, dict[str, Any]]:
    """Phase F wire-up (preflight §4.10). Call the dispatcher, claim
    the picked task, return ``(claimed_task, inputs_merge)``.

    Uses ``claim_task`` (returns None on not-pending) rather than
    ``mark_status(running)`` — handles cancel-during-claim race cleanly
    per invariant 8. On race loss, returns ``(None, {})`` and logs
    ``claim_lost_to_cancel``.

    Callers should merge ``inputs_merge`` into the initial graph state
    with ``inputs`` winning on overlapping keys. Unknown keys are
    tolerated by LangGraph's initial_state.
    """
    try:
        from tinyassets.branch_tasks import claim_task, reclaim_expired_leases
        from tinyassets.branch_tasks_v2 import (
            EPOCH2_QUEUE_CONSUMER_READY,
            Epoch2BranchTaskAdapter,
            read_worker_claim_descriptor,
        )
        from tinyassets.dispatcher import (
            dispatcher_enabled,
            load_dispatcher_config,
            select_next_task,
        )

        if not dispatcher_enabled():
            return None, {}
        # Soul-loop dispatch also needs the pick active so a souled universe
        # drains the child BranchTasks its driver enqueues — otherwise the
        # driver re-runs every cycle and the queue starves (Codex review).
        if not (_workflow_unified_execution_enabled() or _soul_loop_dispatch_enabled()):
            return None, {}
        # BUG-011 Phase C: sweep expired leases before every pick so a
        # wedged worker's claim is reaped at the next dispatch attempt
        # instead of the next daemon restart. Live claims refresh their
        # leases and are never touched.
        reclaimed = reclaim_expired_leases(universe_path)
        if reclaimed:
            logger.info(
                "dispatcher_pick: reclaimed %d expired-lease task(s)",
                reclaimed,
            )
        cfg = load_dispatcher_config(universe_path)
        epoch2_adapter = None
        epoch2_context = None
        worker_id = os.environ.get("TINYASSETS_WORKER_ID", "").strip()
        runtime_instance_id = os.environ.get(
            "TINYASSETS_RUNTIME_INSTANCE_ID",
            "",
        ).strip()
        if EPOCH2_QUEUE_CONSUMER_READY is True and worker_id and runtime_instance_id:
            from tinyassets.storage import data_dir

            canonical_root = data_dir().resolve(strict=False)
            resolved_universe = universe_path.resolve(strict=False)
            if resolved_universe.parent == canonical_root:
                candidate_adapter = Epoch2BranchTaskAdapter(canonical_root)
                candidate_context = candidate_adapter.worker_claim_context(
                    worker_id=worker_id,
                    runtime_instance_id=runtime_instance_id,
                    universe_id=resolved_universe.name,
                )
                if candidate_context is not None and candidate_context.daemon_id == daemon_id:
                    epoch2_adapter = candidate_adapter
                    epoch2_context = candidate_context
                    cfg = replace(
                        cfg,
                        active_daemon_id=candidate_context.daemon_id,
                    )
                elif candidate_context is not None:
                    logger.error(
                        "dispatcher_pick: epoch2 daemon mismatch runtime=%s loop=%s",
                        candidate_context.daemon_id,
                        daemon_id,
                    )

        if epoch2_adapter is not None and epoch2_context is not None:
            live = epoch2_adapter.list_worker_active_tasks(
                universe_id=universe_path.name,
                worker_id=worker_id,
                limit=2,
            )
            if len(live) > 1:
                logger.error(
                    "dispatcher_pick: multiple live epoch2 claims for %s",
                    worker_id,
                )
                return None, {}
            if live:
                active_task = live[0]
                if active_task.status == "cancel_requested":
                    epoch2_adapter.finish(
                        active_task.branch_task_id,
                        worker_id=worker_id,
                        status="cancelled",
                        detail={"reason": "cancel_reconciled_on_restart"},
                    )
                    return None, {}
                resumed = epoch2_adapter.resume(
                    active_task.branch_task_id,
                    descriptor=epoch2_context.descriptor,
                    descriptor_reader=read_worker_claim_descriptor,
                )
                if resumed is None:
                    epoch2_adapter.finish(
                        active_task.branch_task_id,
                        worker_id=worker_id,
                        status="failed",
                        detail={"error": "resume_authority_invalid"},
                    )
                    logger.error(
                        "dispatcher_pick: refused invalid epoch2 resume %s",
                        active_task.branch_task_id,
                    )
                    return None, {}
                _bind_epoch2_executor(resumed, epoch2_context)
                logger.info(
                    "dispatcher_pick: resumed epoch2 %s worker=%s",
                    resumed.branch_task_id,
                    worker_id,
                )
                return resumed, dict(resumed.inputs or {})

            # Recovery is queue-owner maintenance over the canonical store.
            # It grants no execution authority; each recovered task must still
            # pass the exact worker + activation claim transaction below.
            recovered = epoch2_adapter.recover_expired()
            for recovered_task in recovered:
                if (
                    recovered_task.universe_id != universe_path.name
                    or recovered_task.status != "pending"
                ):
                    continue
                claimed = epoch2_adapter.claim(
                    recovered_task.branch_task_id,
                    descriptor=epoch2_context.descriptor,
                    descriptor_reader=read_worker_claim_descriptor,
                )
                if claimed is not None:
                    _bind_epoch2_executor(claimed, epoch2_context)
                    logger.info(
                        "dispatcher_pick: recovered epoch2 %s worker=%s",
                        claimed.branch_task_id,
                        worker_id,
                    )
                    return claimed, dict(claimed.inputs or {})

        picked = select_next_task(
            universe_path,
            config=cfg,
            epoch2_adapter=epoch2_adapter,
        )
        if picked is None:
            return None, {}
        if int(getattr(picked, "queue_epoch", 1)) == 2:
            if epoch2_adapter is None or epoch2_context is None:
                return None, {}
            claimed = epoch2_adapter.claim(
                picked.branch_task_id,
                descriptor=epoch2_context.descriptor,
                descriptor_reader=read_worker_claim_descriptor,
            )
            if claimed is None:
                logger.info(
                    "dispatcher_pick: epoch2 claim lost %s",
                    picked.branch_task_id,
                )
                return None, {}
            _bind_epoch2_executor(claimed, epoch2_context)
            logger.info(
                "dispatcher_pick: claimed epoch2 %s branch=%s worker=%s",
                claimed.branch_task_id,
                claimed.branch_def_id,
                worker_id,
            )
            return claimed, dict(claimed.inputs or {})
        executor_worker_id = worker_id
        executor_runtime_id = os.environ.get(
            "TINYASSETS_RUNTIME_INSTANCE_ID",
            "",
        ).strip()
        claimed = claim_task(
            universe_path,
            picked.branch_task_id,
            daemon_id,
            executor_worker_id=executor_worker_id,
            executor_runtime_id=executor_runtime_id,
        )
        if claimed is None:
            logger.info(
                "dispatcher_pick: claim_lost_to_cancel %s",
                picked.branch_task_id,
            )
            return None, {}
        logger.info(
            "dispatcher_pick: claimed %s tier=%s branch=%s",
            claimed.branch_task_id,
            claimed.trigger_source,
            claimed.branch_def_id,
        )
        return claimed, dict(claimed.inputs or {})
    except Exception:  # noqa: BLE001
        logger.exception("dispatcher_pick failed")
        return None, {}


def _branch_task_owner_id(claimed_task: Any) -> str:
    return str(
        getattr(claimed_task, "worker_owner_id", "")
        or getattr(claimed_task, "claimed_by", "")
        or ""
    )


def _bind_epoch2_executor(claimed_task: Any, context: Any) -> None:
    """Attach trusted runtime identity to the in-memory execution read model."""
    descriptor = context.descriptor
    claimed_task.executor_worker_id = descriptor.worker_id
    claimed_task.executor_runtime_id = descriptor.runtime_instance_id


def _is_epoch2_branch_task(claimed_task: Any) -> bool:
    return int(getattr(claimed_task, "queue_epoch", 1)) == 2


def _epoch2_adapter_for_universe(
    universe_path: Path,
) -> Any | None:
    from tinyassets import branch_tasks_v2

    if branch_tasks_v2.EPOCH2_QUEUE_CONSUMER_READY is not True:
        return None
    from tinyassets.storage import data_dir

    canonical_root = data_dir().resolve(strict=False)
    resolved_universe = universe_path.resolve(strict=False)
    if resolved_universe.parent != canonical_root:
        return None
    return branch_tasks_v2.Epoch2BranchTaskAdapter(canonical_root)


def _branch_task_heartbeat_interval_seconds() -> float:
    raw = (
        os.environ.get("TINYASSETS_BRANCH_TASK_HEARTBEAT_INTERVAL_S")
        or os.environ.get("TINYASSETS_BRANCH_TASK_HEARTBEAT_INTERVAL_SECONDS")
        or str(_DEFAULT_BRANCH_TASK_HEARTBEAT_INTERVAL_S)
    )
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning(
            "Invalid TINYASSETS_BRANCH_TASK_HEARTBEAT_INTERVAL_S=%r; using %.1fs",
            raw,
            _DEFAULT_BRANCH_TASK_HEARTBEAT_INTERVAL_S,
        )
        return _DEFAULT_BRANCH_TASK_HEARTBEAT_INTERVAL_S


@contextlib.contextmanager
def _continuous_branch_task_heartbeat(
    heartbeat: Callable[..., None],
    *,
    interval_seconds: float | None = None,
) -> Iterator[Callable[[], None]]:
    """Renew execution authority while a provider node blocks.

    The queue lease is an authority boundary, not telemetry.  Any renewal
    failure is retained and raised on the execution thread so a run cannot be
    reported successful after its worker stopped owning the task.
    """
    from tinyassets.runs import RunCancelledError, RunExecutionAuthorityLost

    interval = max(
        0.001,
        interval_seconds
        if interval_seconds is not None
        else _branch_task_heartbeat_interval_seconds(),
    )
    stop = threading.Event()
    failure: list[Exception] = []
    failure_lock = threading.Lock()

    def as_execution_stop(exc: Exception) -> Exception:
        if isinstance(exc, (RunCancelledError, RunExecutionAuthorityLost)):
            return exc
        return RunExecutionAuthorityLost(str(exc) or type(exc).__name__)

    def assert_authority() -> None:
        with failure_lock:
            lost = failure[0] if failure else None
        if lost is not None:
            raise lost

    def renew_loop() -> None:
        while not stop.wait(interval):
            try:
                heartbeat(force=True)
            except Exception as exc:  # noqa: BLE001
                with failure_lock:
                    if not failure:
                        failure.append(as_execution_stop(exc))
                stop.set()
                return

    try:
        heartbeat(force=True)
    except Exception as exc:  # noqa: BLE001
        raise as_execution_stop(exc) from exc
    thread = threading.Thread(
        target=renew_loop,
        name="branch-task-heartbeat",
        daemon=True,
    )
    thread.start()
    try:
        yield assert_authority
    finally:
        stop.set()
        thread.join(timeout=max(1.0, interval * 2))
        if thread.is_alive():
            with failure_lock:
                if not failure:
                    failure.append(
                        RunExecutionAuthorityLost("heartbeat renewal did not terminate cleanly")
                    )
        if sys.exc_info()[0] is None:
            assert_authority()


def _build_branch_task_observers(
    universe_path: Path,
    claimed_task: Any,
) -> tuple[Callable[..., None], Callable[[str, str], None]]:
    task_id = str(getattr(claimed_task, "branch_task_id", "") or "")
    owner_id = _branch_task_owner_id(claimed_task)
    interval = _branch_task_heartbeat_interval_seconds()
    last_heartbeat = 0.0

    def refresh_heartbeat(*, force: bool = False) -> None:
        nonlocal last_heartbeat
        from tinyassets.runs import RunCancelledError, RunExecutionAuthorityLost

        if not task_id:
            return
        now_mono = time.monotonic()
        if not force and (now_mono - last_heartbeat) < interval:
            return
        if _is_epoch2_branch_task(claimed_task):
            try:
                adapter = _epoch2_adapter_for_universe(universe_path)
                if adapter is None:
                    raise RuntimeError("epoch2 adapter unavailable")
                refreshed = adapter.heartbeat(
                    task_id,
                    worker_id=owner_id,
                )
                if refreshed is None:
                    raise RuntimeError("lease is no longer owned by this worker")
                last_heartbeat = now_mono
                if refreshed.status == "cancel_requested":
                    raise RunCancelledError(f"BranchTask {task_id} cancellation requested")
                return
            except RunCancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "epoch2 branch_task lease renewal failed for %s owner=%s",
                    task_id,
                    owner_id,
                )
                if isinstance(exc, RunExecutionAuthorityLost):
                    raise
                raise RunExecutionAuthorityLost(
                    f"BranchTask {task_id} lost lease authority: {exc}"
                ) from exc
        try:
            from tinyassets.branch_tasks import refresh_task_heartbeat

            refreshed = refresh_task_heartbeat(
                universe_path,
                task_id,
                worker_owner_id=owner_id,
            )
            if refreshed is not None:
                last_heartbeat = now_mono
        except Exception:  # noqa: BLE001
            logger.exception(
                "branch_task heartbeat refresh failed for %s owner=%s",
                task_id,
                owner_id,
            )

    def mark_node_status(node_id: str, status: str) -> None:
        if not task_id:
            return
        if _is_epoch2_branch_task(claimed_task):
            refresh_heartbeat()
            return
        try:
            from tinyassets.branch_tasks import mark_task_progress

            mark_task_progress(universe_path, task_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "branch_task progress stamp failed for %s node=%s status=%s",
                task_id,
                node_id,
                status,
            )
        refresh_heartbeat()

    return refresh_heartbeat, mark_node_status


def _finalize_claimed_task(
    universe_path: Path,
    claimed: Any,
    *,
    success: bool,
    error: str = "",
) -> str:
    """Mark a claimed BranchTask ``succeeded`` or ``failed``.

    Invoked after the graph stream completes (or errors out). A crash
    during invocation that prevents this call leaves the task in
    ``running``; restart-recovery (Phase E invariant 7) resets it to
    pending on next daemon boot.
    """
    if claimed is None:
        return ""
    requested_status = "succeeded" if success else "failed"
    if _is_epoch2_branch_task(claimed):
        adapter = _epoch2_adapter_for_universe(universe_path)
        if adapter is None:
            raise RuntimeError("epoch2 adapter unavailable")
        detail = {"error": str(error)[:2000]} if error else None
        settled = adapter.finish(
            claimed.branch_task_id,
            worker_id=_branch_task_owner_id(claimed),
            status=requested_status,
            detail=detail,
        )
        logger.info(
            "dispatcher_pick: finalized epoch2 %s -> %s",
            claimed.branch_task_id,
            settled.status,
        )
        return settled.status
    from tinyassets.branch_tasks import mark_status

    mark_status(
        universe_path,
        claimed.branch_task_id,
        status=requested_status,
        error=error,
    )
    logger.info(
        "dispatcher_pick: finalized %s -> %s",
        claimed.branch_task_id,
        requested_status,
    )
    return requested_status


def _branch_task_cancel_requested(
    universe_path: Path,
    claimed: Any,
) -> bool:
    if claimed is None:
        return False
    try:
        if _is_epoch2_branch_task(claimed):
            adapter = _epoch2_adapter_for_universe(universe_path)
            if adapter is None:
                return True
            current = adapter.get(claimed.branch_task_id)
            return current is None or current.status == "cancel_requested"
        from tinyassets.branch_tasks import is_task_cancel_requested

        return is_task_cancel_requested(
            universe_path,
            claimed.branch_task_id,
        )
    except Exception:  # noqa: BLE001 - uncertainty must stop material work
        logger.exception(
            "branch_task cancel check failed for %s",
            getattr(claimed, "branch_task_id", ""),
        )
        return True


def _cancel_claimed_task(universe_path: Path, claimed: Any) -> None:
    if _is_epoch2_branch_task(claimed):
        adapter = _epoch2_adapter_for_universe(universe_path)
        if adapter is None:
            raise RuntimeError("epoch2 adapter unavailable")
        adapter.finish(
            claimed.branch_task_id,
            worker_id=_branch_task_owner_id(claimed),
            status="cancelled",
            detail={"reason": "cancel_requested"},
        )
        return
    from tinyassets.branch_tasks import mark_status

    mark_status(
        universe_path,
        claimed.branch_task_id,
        status="cancelled",
    )


def _settle_claimed_direct_branch_task(
    universe_path: Path,
    claimed: Any,
    *,
    success: bool,
    error: str,
) -> tuple[bool, str]:
    """Settle direct execution without ever attesting canceled work."""
    if error == "branch_task_cancel_requested":
        _cancel_claimed_task(universe_path, claimed)
        return False, "branch_task_cancel_requested"
    if not _is_epoch2_branch_task(claimed) and _branch_task_cancel_requested(
        universe_path,
        claimed,
    ):
        _cancel_claimed_task(universe_path, claimed)
        return False, "branch_task_cancel_requested"
    settled_status = _finalize_claimed_task(
        universe_path,
        claimed,
        success=success,
        error=error,
    )
    if settled_status == "cancelled":
        return False, "branch_task_cancel_requested"
    return success, error


def _node_bid_lookup_factory(repo_root: Path):
    """Build a ``(node_def_id) -> NodeDefinition | None`` lookup.

    Walks ``<repo_root>/branches/*.yaml`` and returns the first node
    whose ``node_id`` matches. Best-effort: malformed YAMLs and
    missing directories → no match. Safe to call with any repo_root.
    """
    from tinyassets.branches import BranchDefinition, NodeDefinition

    def _lookup(node_def_id: str):
        branches_dir = Path(repo_root) / "branches"
        if not branches_dir.is_dir():
            return None
        try:
            import yaml
        except ImportError:
            return None
        for p in branches_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(data, dict):
                continue
            for node_data in data.get("node_defs", []) or []:
                if not isinstance(node_data, dict):
                    continue
                if node_data.get("node_id") == node_def_id:
                    try:
                        return NodeDefinition.from_dict(node_data)
                    except Exception:  # noqa: BLE001
                        return None
            # Also let get_node_def search compiled BranchDefinition
            try:
                branch = BranchDefinition.from_dict(data)
                node = branch.get_node_def(node_def_id)
                if node is not None:
                    return node
            except Exception:  # noqa: BLE001
                continue
        return None

    return _lookup


def _try_execute_claimed_node_bid(
    universe_path: Path,
    claimed_task: Any,
    daemon_id: str,
) -> tuple[bool, str]:
    """Phase G: execute a NodeBid BranchTask (sentinel-prefixed).

    Never raises — all paths return ``(success, error)``. Writes:

    - Output JSON at ``<universe>/bid_outputs/<bid_id>/output.json``
      (via the executor).
    - Settlement record at
      ``<repo_root>/settlements/<bid_id>__<daemon_id>.yaml``.
    - Execution-log entry in ``<universe>/bid_execution_log.json``
      (per-universe daemon-local; distinct from the repo-root
      ``settlements/`` immutable ledger).
    - Status update on the NodeBid YAML at
      ``<repo_root>/bids/<bid_id>.yaml``.
    """
    try:
        from tinyassets.bid.execution_log import append_execution_log_entry
        from tinyassets.bid.node_bid import (
            claim_node_bid,
            git_has_remote,
            read_node_bid,
            update_node_bid_status,
        )
        from tinyassets.bid.settlements import (
            SettlementExistsError,
            record_settlement_event,
        )
        from tinyassets.executors.node_bid import execute_node_bid
        from tinyassets.producers.goal_pool import repo_root_path

        inputs = dict(claimed_task.inputs or {})
        node_bid_id = str(inputs.get("__node_bid_id", ""))
        node_def_id = str(inputs.get("__node_def_id", ""))
        if not node_bid_id or not node_def_id:
            return False, "node_bid_missing_internal_keys"

        try:
            repo_root = repo_root_path(Path(universe_path))
        except RuntimeError as exc:
            return False, f"repo_root_not_resolvable: {exc}"

        runtime_instance_id = os.environ.get(
            "TINYASSETS_RUNTIME_INSTANCE_ID",
            "",
        ).strip()
        worker_id = os.environ.get("TINYASSETS_WORKER_ID", "").strip()
        owner_user_id = ""
        try:
            from tinyassets.daemon_registry import get_daemon
            from tinyassets.storage import data_dir

            daemon = get_daemon(data_dir(), daemon_id=daemon_id)
            owner_user_id = str(daemon.get("owner_user_id") or "")
        except Exception:  # noqa: BLE001
            owner_user_id = ""

        # Preflight §4.1 #1: atomic claim via git-rename + push BEFORE
        # execution. claim_node_bid returns None on race loss; we
        # fall through to "claim_race_lost" so the BranchTask is
        # marked failed and the next cycle proceeds.
        claimed_bid = claim_node_bid(
            repo_root,
            node_bid_id,
            daemon_id,
            owner_user_id=owner_user_id,
            runtime_instance_id=runtime_instance_id,
            worker_id=worker_id,
        )
        if claimed_bid is None:
            # Bid YAML may have been deleted between producer emit
            # and claim attempt (or race was lost, or status != open).
            # Read-only fallback is ONLY safe when no remote is
            # configured (single-process / local-test repo). In
            # multi-daemon prod with a remote, a lost push followed by
            # `git reset --hard origin/<branch>` can restore a stale
            # view where the winning daemon's claim hasn't propagated
            # locally yet — so a local "status == open" check would
            # bypass the race and cause B to execute what A already
            # claimed. Reviewer-flagged race-bypass fix (option c).
            if git_has_remote(repo_root):
                return False, "claim_race_lost"
            existing = read_node_bid(repo_root, node_bid_id)
            if existing is None or existing.status != "open":
                return False, "claim_race_lost"
            bid = existing
        else:
            bid = claimed_bid

        result = execute_node_bid(
            bid,
            node_lookup_fn=_node_bid_lookup_factory(repo_root),
            output_dir=Path(universe_path),
        )
        success = result.status == "succeeded"

        # Preflight §4.1 #5b + invariant 8: immutable settlement
        # record via tinyassets/settlements.py. `outcome_status` is the
        # authoritative field ("succeeded" | "failed"); v1 records
        # refuse overwrites so the audit trail stays byte-stable
        # across token-launch migrations.
        from datetime import datetime, timezone

        completed_at = datetime.now(timezone.utc).isoformat()
        try:
            record_settlement_event(
                repo_root,
                bid,
                result,
                daemon_id,
                owner_user_id=owner_user_id,
                runtime_instance_id=runtime_instance_id,
                worker_id=worker_id,
            )
        except SettlementExistsError:
            # Already recorded — idempotent finalize path (e.g.
            # restart-recovery claiming a running bid). Preserve
            # the original record.
            logger.info(
                "node_bid: settlement already exists for %s__%s; keeping original (immutable v1)",
                node_bid_id,
                daemon_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("node_bid: settlement write failed")

        # Ledger append (best-effort).
        try:
            append_execution_log_entry(
                Path(universe_path),
                {
                    "bid_id": node_bid_id,
                    "node_def_id": node_def_id,
                    "daemon_id": daemon_id,
                    "success": success,
                    "bid_amount": float(bid.bid or 0.0),
                    "evidence_url": result.evidence_url,
                    "completed_at": completed_at,
                    "error": result.error,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("node_bid: ledger append failed")

        # NodeBid YAML status update (best-effort).
        try:
            update_node_bid_status(
                repo_root,
                node_bid_id,
                status="succeeded" if success else "failed",
                evidence_url=result.evidence_url,
            )
        except Exception:  # noqa: BLE001
            logger.exception("node_bid: status update failed")

        logger.info(
            "node_bid: executed %s status=%s evidence=%s",
            node_bid_id,
            result.status,
            result.evidence_url,
        )
        return success, result.error
    except Exception as exc:  # noqa: BLE001
        logger.exception("_try_execute_claimed_node_bid failed")
        return False, f"node_bid_execution_exception: {exc}"


def _should_execute_claimed_branch_directly(claimed_task: Any) -> bool:
    branch_def_id = str(getattr(claimed_task, "branch_def_id", "") or "")
    if not branch_def_id or branch_def_id in _UNIVERSE_CYCLE_BRANCH_IDS:
        return False
    request_type = str(getattr(claimed_task, "request_type", "") or "branch_run")
    return request_type in {"branch_run", "run_branch", "bug_investigation"}


def _branch_task_inputs_for_execution(claimed_task: Any) -> dict[str, Any]:
    inputs = dict(getattr(claimed_task, "inputs", {}) or {})
    request_type = str(getattr(claimed_task, "request_type", "") or "branch_run")
    if request_type == "bug_investigation" and not str(inputs.get("request_text") or "").strip():
        from tinyassets.bug_investigation import build_run_payload

        inputs = build_run_payload(inputs)
    return inputs


def _coerce_bug_investigation_patch_packet(packet: Any) -> dict[str, Any]:
    if isinstance(packet, dict) and packet:
        return packet
    if isinstance(packet, str) and packet.strip():
        return {"implementation_sketch": packet.strip()}
    return {}


def _bug_investigation_patch_packet(output: dict[str, Any]) -> dict[str, Any]:
    """Return the completed investigation packet from known output shapes."""
    for key in ("patch_packet", "candidate_patch_packet", "child_candidate_patch_packet"):
        packet = _coerce_bug_investigation_patch_packet(output.get(key))
        if packet:
            return packet
    child_output = output.get("attached_child_output")
    if isinstance(child_output, dict):
        return _bug_investigation_patch_packet(child_output)
    coding_packet = output.get("coding_packet")
    if isinstance(coding_packet, dict):
        packet: dict[str, Any] = {}
        summary = str(coding_packet.get("candidate_packet_summary") or "").strip()
        if summary:
            packet["implementation_sketch"] = summary
        tests = coding_packet.get("expected_tests")
        if isinstance(tests, list):
            test_plan = "\n".join(f"- {str(item).strip()}" for item in tests if str(item).strip())
        else:
            test_plan = str(tests or "").strip()
        if test_plan:
            packet["test_plan"] = test_plan
        if packet:
            return packet
    return {}


def _maybe_attach_bug_investigation_patch_packet(
    claimed_task: Any,
    run_status: str,
    run_output: dict[str, Any],
) -> dict[str, Any]:
    """Attach completed bug-investigation output to the source wiki page."""
    request_type = str(getattr(claimed_task, "request_type", "") or "branch_run")
    if request_type != "bug_investigation":
        return {"status": "skipped", "reason": "not_bug_investigation"}
    if run_status != "completed":
        return {"status": "skipped", "reason": f"run_status:{run_status}"}

    inputs = dict(getattr(claimed_task, "inputs", {}) or {})
    bug_id = str(inputs.get("bug_id") or run_output.get("bug_id") or "").strip()
    if not bug_id:
        return {"status": "skipped", "reason": "missing_bug_id"}

    patch_packet = _bug_investigation_patch_packet(run_output)
    if not patch_packet:
        return {"status": "skipped", "reason": "missing_patch_packet"}

    try:
        from tinyassets.bug_investigation import attach_patch_packet_comment

        return attach_patch_packet_comment(bug_id, patch_packet)
    except Exception as exc:  # noqa: BLE001
        logger.exception("bug_investigation patch-packet attach failed")
        return {"status": "error", "bug_id": bug_id, "error": str(exc)}


def _try_execute_claimed_branch_task(
    universe_path: Path,
    claimed_task: Any,
    daemon_id: str,
    on_node_status: Callable[[str, str], None] | None = None,
    branch_task_heartbeat: Callable[..., None] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Execute a claimed BranchTask's requested branch.

    The default universe wrapper remains the long-running creative loop.
    Community goal-pool tasks, however, carry an explicit branch_def_id
    and must produce a durable TinyAssets run for that branch before the
    queue row is marked terminal.
    """
    physical_universe_id = Path(universe_path).name
    persisted_universe_id = str(getattr(claimed_task, "universe_id", "") or "").strip()
    if not physical_universe_id or persisted_universe_id != physical_universe_id:
        return (
            False,
            "branch_task_universe_mismatch",
            {
                "physical_universe_id": physical_universe_id,
                "persisted_universe_id": persisted_universe_id,
            },
        )
    try:
        from tinyassets.api.branches import _resolve_branch_id
        from tinyassets.branches import BranchDefinition
        from tinyassets.daemon_server import get_branch_definition
        from tinyassets.runs import (
            RUN_STATUS_COMPLETED,
            BranchTaskRunReservationConflict,
            RunCancelledError,
            RunExecutionAuthorityLost,
            execute_branch,
            execute_branch_version,
            get_run_by_branch_task_id,
            latest_run_by_name,
        )
        from tinyassets.storage import data_dir

        base_path = data_dir()
        requested = str(getattr(claimed_task, "branch_def_id", "") or "")
        is_epoch2 = _is_epoch2_branch_task(claimed_task)
        branch_version_id = str(
            getattr(claimed_task, "automation_branch_version", "") or ""
        ).strip()
        branch = None
        if is_epoch2:
            if not branch_version_id:
                return (
                    False,
                    "missing_immutable_branch_version",
                    {
                        "requested_branch_def_id": requested,
                    },
                )
            # The transactional admission already bound this task to an exact
            # published snapshot.  Never resolve the mutable branch head for
            # epoch 2 execution.
            branch_def_id = requested
        else:
            branch_def_id = _resolve_branch_id(requested, base_path)
            if not branch_def_id:
                return (
                    False,
                    f"branch_not_found: {requested}",
                    {
                        "requested_branch_def_id": requested,
                    },
                )
            try:
                source_dict = get_branch_definition(
                    base_path,
                    branch_def_id=branch_def_id,
                )
            except KeyError:
                return (
                    False,
                    f"branch_not_found: {branch_def_id}",
                    {
                        "branch_def_id": branch_def_id,
                    },
                )
            branch = BranchDefinition.from_dict(source_dict)
            errors = branch.validate()
            if errors:
                return (
                    False,
                    "branch_validation_failed",
                    {
                        "branch_def_id": branch_def_id,
                        "validation_errors": errors,
                    },
                )

        run_name = f"branch-task-{claimed_task.branch_task_id}"
        if is_epoch2:
            actor = str(getattr(claimed_task, "actor_id", "") or "").strip()
            if not actor:
                return (
                    False,
                    "missing_admission_actor",
                    {
                        "branch_task_id": str(claimed_task.branch_task_id),
                    },
                )
        else:
            # Same rule on the legacy path: the universe is the actor. An
            # environment variable must never name a principal.
            if not physical_universe_id:
                return (
                    False,
                    "missing_admission_actor",
                    {"branch_task_id": str(claimed_task.branch_task_id)},
                )
            actor = f"universe:{physical_universe_id}"
        executor_worker_id = str(
            getattr(claimed_task, "executor_worker_id", "") or "",
        )
        executor_runtime_id = str(
            getattr(claimed_task, "executor_runtime_id", "") or "",
        )
        expected_epoch2_identity = {
            "run_name": run_name,
            "branch_task_id": str(claimed_task.branch_task_id),
            "branch_def_id": branch_def_id,
            "branch_version_id": branch_version_id,
            "queue_universe_id": physical_universe_id,
            "actor": actor,
            "daemon_id": daemon_id,
            "runtime_instance_id": executor_runtime_id,
            "worker_id": executor_worker_id,
        }

        def reconcile_existing_run(
            existing_run: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any]]:
            if is_epoch2:
                mismatches = [
                    field
                    for field, expected in expected_epoch2_identity.items()
                    if str(existing_run.get(field) or "") != str(expected or "")
                ]
                if mismatches:
                    return (
                        False,
                        "existing_run_identity_mismatch",
                        {
                            "branch_def_id": branch_def_id,
                            "branch_version_id": branch_version_id,
                            "run_id": existing_run.get("run_id") or "",
                            "run_status": existing_run.get("status") or "unknown",
                            "identity_mismatches": mismatches,
                            "reused_existing_run": False,
                        },
                    )
            if existing_run.get("status") != RUN_STATUS_COMPLETED:
                existing_status = str(existing_run.get("status") or "unknown")
                return (
                    False,
                    f"existing_run_requires_reconciliation:{existing_status}",
                    {
                        "branch_def_id": branch_def_id,
                        "branch_version_id": branch_version_id,
                        "run_id": existing_run.get("run_id") or "",
                        "run_status": existing_status,
                        "actor": existing_run.get("actor") or "",
                        "reused_existing_run": True,
                    },
                )
            output = existing_run.get("output", {})
            metadata = {
                "branch_def_id": branch_def_id,
                "run_id": existing_run["run_id"],
                "run_status": existing_run["status"],
                "actor": existing_run.get("actor") or "",
                "reused_existing_run": True,
            }
            if is_epoch2:
                metadata["branch_version_id"] = branch_version_id
            attach_result = _maybe_attach_bug_investigation_patch_packet(
                claimed_task,
                existing_run["status"],
                output if isinstance(output, dict) else {},
            )
            if attach_result.get("status") != "skipped":
                metadata["wiki_patch_packet"] = attach_result
            logger.info(
                "dispatcher_pick: reused completed branch task run %s branch=%s run=%s",
                claimed_task.branch_task_id,
                branch_def_id,
                existing_run["run_id"],
            )
            return True, "", metadata

        if is_epoch2:
            existing_run = get_run_by_branch_task_id(
                base_path,
                branch_task_id=str(claimed_task.branch_task_id),
            )
        else:
            existing_run = latest_run_by_name(
                base_path,
                run_name=run_name,
                branch_def_id=branch_def_id,
            )
        if existing_run:
            return reconcile_existing_run(existing_run)

        provider_call: Any = None
        try:
            from domains.fantasy_daemon.phases._provider_stub import (
                call_provider as provider_call,
            )
        except ImportError:
            provider_call = None
        automation_id = str(getattr(claimed_task, "automation_id", "") or "").strip()
        if is_epoch2 and automation_id:
            # A slice of a fleet-era cloud automation. That layer is retired
            # (plan C1/C2): its controls are stopped and nothing produces new
            # slices, so a leftover one fails with the reason, not a run.
            return (
                False,
                "cloud_automation_retired",
                {
                    "automation_id": automation_id,
                    "branch_task_id": str(claimed_task.branch_task_id),
                },
            )

        if is_epoch2:
            from tinyassets.runtime.claimed_branch_execution import (
                ClaimedBranchExecutorIdentity,
                execute_claimed_branch_task,
            )

            return execute_claimed_branch_task(
                base_path,
                claimed_task,
                ClaimedBranchExecutorIdentity(
                    daemon_id=daemon_id,
                    worker_id=executor_worker_id,
                    runtime_instance_id=executor_runtime_id,
                    on_node_status=on_node_status,
                    heartbeat=(
                        (lambda: branch_task_heartbeat(force=True))
                        if branch_task_heartbeat is not None
                        else None
                    ),
                    heartbeat_interval_seconds=(
                        _branch_task_heartbeat_interval_seconds()
                    ),
                ),
                provider_call,
            )

        execution_kwargs = {
            "inputs": _branch_task_inputs_for_execution(claimed_task),
            "run_name": run_name,
            "actor": actor,
            "daemon_id": daemon_id,
            "runtime_instance_id": executor_runtime_id,
            "worker_id": executor_worker_id,
            "provider_call": provider_call,
            "on_node_status": on_node_status,
            # Carry spawn depth across the queue boundary so an in-node enqueue
            # from this run is depth+1 and the depth cap can bound the chain.
            "_invocation_depth": int(getattr(claimed_task, "depth", 0) or 0),
            # Trusted enqueue context: universe authority comes from the
            # physical queue directory after its persisted row value matched;
            # lineage comes from the claimed task. A source node cannot
            # redirect descendants by editing task metadata or inputs.
            "_enqueue_universe_id": physical_universe_id,
            "_parent_branch_task_id": str(getattr(claimed_task, "branch_task_id", "") or ""),
            "_origin_branch_task_id": str(getattr(claimed_task, "origin_branch_task_id", "") or ""),
        }
        try:
            if is_epoch2:
                execution_kwargs["_queue_branch_task_id"] = str(claimed_task.branch_task_id)
                if branch_task_heartbeat is not None:
                    with _continuous_branch_task_heartbeat(
                        branch_task_heartbeat,
                    ) as assert_authority:

                        def authority_checked_node_status(
                            node_id: str,
                            status: str,
                        ) -> None:
                            assert_authority()
                            if on_node_status is not None:
                                on_node_status(node_id, status)
                            assert_authority()

                        execution_kwargs["on_node_status"] = authority_checked_node_status
                        outcome = execute_branch_version(
                            base_path,
                            branch_version_id=branch_version_id,
                            **execution_kwargs,
                        )
                        assert_authority()
                else:
                    outcome = execute_branch_version(
                        base_path,
                        branch_version_id=branch_version_id,
                        **execution_kwargs,
                    )
            else:
                assert branch is not None
                outcome = execute_branch(
                    base_path,
                    branch=branch,
                    **execution_kwargs,
                )
        except BranchTaskRunReservationConflict:
            reserved_run = get_run_by_branch_task_id(
                base_path,
                branch_task_id=str(claimed_task.branch_task_id),
            )
            if reserved_run is None:
                return (
                    False,
                    "run_reservation_conflict_missing",
                    {
                        "branch_def_id": branch_def_id,
                        "branch_version_id": branch_version_id,
                    },
                )
            return reconcile_existing_run(reserved_run)
        except RunCancelledError as exc:
            return (
                False,
                "branch_task_cancel_requested",
                {
                    "branch_def_id": branch_def_id,
                    "branch_version_id": branch_version_id,
                    "cancel_requested": True,
                    "cancel_detail": str(exc),
                },
            )
        except RunExecutionAuthorityLost as exc:
            return (
                False,
                "branch_task_authority_lost",
                {
                    "branch_def_id": branch_def_id,
                    "branch_version_id": branch_version_id,
                    "authority_error": str(exc),
                },
            )
        metadata = {
            "branch_def_id": branch_def_id,
            "run_id": outcome.run_id,
            "run_status": outcome.status,
            "actor": actor,
        }
        if is_epoch2:
            metadata["branch_version_id"] = branch_version_id
        attach_result = _maybe_attach_bug_investigation_patch_packet(
            claimed_task,
            outcome.status,
            outcome.output if isinstance(outcome.output, dict) else {},
        )
        if attach_result.get("status") != "skipped":
            metadata["wiki_patch_packet"] = attach_result
        success = outcome.status == RUN_STATUS_COMPLETED
        error = (
            outcome.error if outcome.error else ("" if success else f"run_status:{outcome.status}")
        )
        logger.info(
            "dispatcher_pick: executed branch task %s branch=%s run=%s status=%s",
            claimed_task.branch_task_id,
            branch_def_id,
            outcome.run_id,
            outcome.status,
        )
        return success, error, metadata
    except Exception as exc:  # noqa: BLE001
        logger.exception("_try_execute_claimed_branch_task failed")
        return (
            False,
            f"branch_task_execution_exception: {exc}",
            {
                "universe_path": str(Path(universe_path)),
                "daemon_id": daemon_id,
            },
        )


def _dispatcher_observe(universe_path: Path) -> None:
    """Phase E cycle-boundary observation.

    Logs the dispatcher's "would-have-picked" task to activity.log.
    Observational only — never alters graph invocation under the
    default flag matrix (Phase D off OR Phase E off). Preflight
    §4.1 #4 flag matrix.
    """
    try:
        from tinyassets.dispatcher import (
            dispatcher_enabled,
            load_dispatcher_config,
            select_next_task,
        )

        if not dispatcher_enabled():
            return
        cfg = load_dispatcher_config(universe_path)
        picked = select_next_task(universe_path, config=cfg)
        if picked is None:
            logger.info("dispatcher_observational: no eligible BranchTask")
        else:
            logger.info(
                "dispatcher_observational: would pick %s tier=%s score-tier=%s",
                picked.branch_task_id,
                picked.trigger_source,
                cfg.tier_weights.get(picked.trigger_source, 0.0),
            )
    except Exception:  # noqa: BLE001
        logger.exception("dispatcher_observe failed")


def _build_unified_graph_builder() -> Any:
    """Load the fantasy universe-cycle Branch seed and compile it.

    Returns an uncompiled ``StateGraph`` (``CompiledBranch.graph``)
    compatible with the direct-path ``build_universe_graph()`` return
    so the caller can attach its own SqliteSaver checkpointer. Under
    flag-on the outer graph has ONE node, the opaque
    ``universe_cycle_wrapper`` that runs the full inner graph per
    invocation.

    R11 hard-fail contract (preflight §4.8): if the Branch can't be
    loaded OR the domain registry is missing the wrapper callable,
    the exception propagates out. No silent fallthrough to the
    direct path.
    """
    import yaml

    from tinyassets.branches import BranchDefinition
    from tinyassets.graph_compiler import compile_branch

    seed_path = Path(__file__).parent / "branches" / "universe_cycle.yaml"
    if not seed_path.exists():
        raise FileNotFoundError(
            f"Phase D seed Branch not found at {seed_path}. Cannot compile unified graph."
        )
    raw = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    branch = BranchDefinition.from_dict(raw)
    compiled_branch = compile_branch(branch)
    return compiled_branch.graph


# ---------------------------------------------------------------------------
# Graph execution
# ---------------------------------------------------------------------------


class DaemonController:
    """Controls the universe graph execution lifecycle.

    Wires together:
    - Universe graph with SqliteSaver checkpointing
    - MemoryManager for hierarchical memory
    - ProviderRouter for LLM calls
    - TrayApp + DashboardHandler for desktop integration
    """

    def __init__(
        self,
        universe_path: str,
        db_path: str | None = "",
        checkpoint_path: str | None = "",
        no_tray: bool = False,
        premise: str = "",
        log_callback: Any = None,
        pinned_provider: str = "",
    ) -> None:
        self._universe_path = universe_path
        # Default DB paths inside the universe directory (not CWD)
        self._db_path = db_path or str(Path(universe_path) / "story.db")
        self._checkpoint_path = checkpoint_path or str(Path(universe_path) / "checkpoints.db")

        # Guard: DB paths must resolve inside the universe directory.
        # A CWD-relative path like "story.db" would silently load stale
        # data from the wrong universe.
        uni_resolved = Path(universe_path).resolve()
        db_resolved = Path(self._db_path).resolve()
        if not db_resolved.is_relative_to(uni_resolved):
            logger.warning(
                "DB path %s resolves outside universe %s — "
                "this will cause cross-universe contamination. "
                "Falling back to %s/story.db",
                db_resolved,
                uni_resolved,
                uni_resolved,
            )
            self._db_path = str(uni_resolved / "story.db")

        cp_resolved = Path(self._checkpoint_path).resolve()
        if not cp_resolved.is_relative_to(uni_resolved):
            logger.warning(
                "Checkpoint path %s resolves outside universe %s — "
                "falling back to %s/checkpoints.db",
                cp_resolved,
                uni_resolved,
                uni_resolved,
            )
            self._checkpoint_path = str(uni_resolved / "checkpoints.db")

        self._no_tray = no_tray
        self._premise = premise
        self._log_callback = log_callback
        self._activity_log_path = Path(universe_path) / "activity.log"

        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._ready = threading.Event()
        self._tray: TrayApp | None = None
        self._dashboard: DashboardHandler | None = None
        self._notifications: NotificationManager | None = None
        self._memory: MemoryManager | None = None
        self._router: ProviderRouter | None = None
        self._version_store: OutputVersionStore | None = None
        self._promise_tracker: SeriesPromiseTracker | None = None
        self._current_scene_id: str = ""
        self._last_verdict: str = ""
        self._last_provider_used: str = ""
        self._last_eval_score: float = 0.0
        self._cached_creative_briefing: str = ""
        self._universe_id: str = Path(universe_path).name or "default"
        self._heartbeat_thread: threading.Thread | None = None
        self._pending_universe_switch: str = ""
        self._last_status_write: float = 0.0
        self._STATUS_WRITE_COOLDOWN: float = 5.0  # seconds
        self._pinned_provider: str = pinned_provider
        self._runtime_status_path = Path(universe_path) / ".runtime_status.json"
        self._runtime_status_thread: threading.Thread | None = None

    def start(self) -> None:
        """Initialize all subsystems and run the graph."""
        universe_id = Path(self._universe_path).stem or "default"

        # Load per-universe config.yaml
        from fantasy_daemon.config import load_universe_config

        runtime.universe_config = load_universe_config(self._universe_path)
        logger.info(
            "Universe config: temperature=%.1f, timeout=%ds, scenes_target=%d, chapters_target=%d",
            runtime.universe_config.temperature,
            runtime.universe_config.timeout,
            runtime.universe_config.scenes_target,
            runtime.universe_config.chapters_target,
        )

        # Memory manager
        self._memory = MemoryManager(
            universe_id=universe_id,
            db_path=self._db_path,
        )

        # Provider router
        self._router = _build_provider_router()

        # Inject router into the provider stub module so nodes can use it
        try:
            from tinyassets.providers.call import set_provider_router

            set_provider_router(self._router)
        except ImportError:
            pass

        # Output version store
        self._version_store = OutputVersionStore(
            db_path=self._db_path,
            universe_id=universe_id,
        )

        # Series promise tracker
        self._promise_tracker = SeriesPromiseTracker(
            db_path=self._db_path,
            universe_id=universe_id,
        )

        # Set runtime singletons so nodes can access non-serializable objects
        runtime.memory_manager = self._memory
        runtime.version_store = self._version_store
        runtime.promise_tracker = self._promise_tracker

        # Retrieval backends
        self._init_retrieval_backends()

        # Progressive ingestion: prime knowledge graph with canon data
        self._run_progressive_ingestion(universe_id)

        # RAPTOR tree: build multi-level summaries from indexed content
        self._build_raptor_tree()

        # Desktop integration
        if not self._no_tray:
            self._tray = TrayApp(
                on_start=self._on_tray_start,
                on_pause=self._on_tray_pause,
                on_resume=self._on_tray_resume,
                on_quit=self._on_tray_quit,
                output_dir=self._universe_path,
            )
            self._tray.start()

        self._dashboard = DashboardHandler(
            tray=self._tray,
            log_callback=self._combined_log,
        )
        self._dashboard.metrics.seed_from_db(self._db_path, self._universe_path)
        self._notifications = NotificationManager(tray=self._tray)

        # Phase H: re-register NodeBidProducer with a real node-lookup
        # function so producer-side sandbox layers 1+2 become load-
        # bearing (closes G.1 follow-up #1 per preflight §4.1 #5).
        # Import-time registration happens with node_lookup_fn=None;
        # here we swap in the repo-root-aware lookup that walks
        # <repo_root>/branches/*.yaml. Fail-open invariant preserved:
        # if the lookup raises, the producer-side helper catches and
        # the bid passes through to executor-side re-validation.
        try:
            from tinyassets.producers.goal_pool import repo_root_path
            from tinyassets.producers.node_bid import (
                paid_market_enabled,
            )
            from tinyassets.producers.node_bid import (
                register_if_enabled as _register_node_bid_producer,
            )

            if paid_market_enabled():
                try:
                    _repo_root = repo_root_path(Path(self._universe_path))
                    _lookup_fn = _node_bid_lookup_factory(_repo_root)
                except RuntimeError:
                    _lookup_fn = None
                _register_node_bid_producer(node_lookup_fn=_lookup_fn)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Phase H: NodeBidProducer lookup-fn wiring failed "
                "(fail-open to import-time default)",
            )

        # Signal that initialization is complete (API can now serve)
        self._ready.set()

        # Run the graph
        self._run_graph(universe_id)

    def _init_retrieval_backends(self) -> None:
        """Initialize retrieval backends as runtime singletons.

        Creates KnowledgeGraph, VectorStore, and embed_fn. Each is
        optional -- failures are logged but don't block startup.

        All paths are resolved absolutely under the universe directory so
        that CWD changes during daemon runtime cannot silently redirect
        writes to a cross-universe file. story.db/checkpoints.db already
        have this guard (__init__); knowledge.db and lancedb were missing
        it (regression pre-2026-04-11 that let every KG write land in an
        unowned file at whatever the process CWD happened to be).
        """
        uni_resolved = Path(self._universe_path).resolve()
        kg_path = str(uni_resolved / "knowledge.db")
        lance_path = str(uni_resolved / "lancedb")

        # Knowledge graph
        try:
            from fantasy_daemon.knowledge.knowledge_graph import KnowledgeGraph

            runtime.knowledge_graph = KnowledgeGraph(kg_path)
            logger.info("KnowledgeGraph initialized at %s", kg_path)
        except Exception as e:
            logger.warning("KnowledgeGraph init failed: %s", e)

        # Vector store (LanceDB)
        try:
            from fantasy_daemon.retrieval.vector_store import VectorStore

            runtime.vector_store = VectorStore(db_path=lance_path)
            logger.info("VectorStore initialized at %s", lance_path)
        except Exception as e:
            logger.warning("VectorStore init failed: %s", e)

        # Embedding function (Ollama)
        try:
            from fantasy_daemon.providers.ollama_provider import OllamaProvider

            ollama = OllamaProvider()

            def _sync_embed(text: str) -> list[float]:
                import asyncio
                import concurrent.futures

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None

                if loop is not None and loop.is_running():
                    # Inside an async context (LangGraph node) — run in
                    # a worker thread to avoid nested event-loop errors.
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        return pool.submit(lambda: asyncio.run(ollama.embed(text))).result()
                else:
                    new_loop = asyncio.new_event_loop()
                    try:
                        return new_loop.run_until_complete(ollama.embed(text))
                    finally:
                        new_loop.close()

            runtime.embed_fn = _sync_embed
            logger.info("Ollama embed_fn ready")
        except Exception as e:
            logger.debug("Ollama embed_fn not available: %s", e)

    def _build_raptor_tree(self) -> None:
        """Build a RAPTOR tree from canon files.

        Reads canon/*.md, splits into paragraphs, embeds, and builds
        the tree.  Uses the shared ``rebuild_raptor_from_canon`` helper
        so the worldbuild node can trigger the same rebuild.
        """
        from fantasy_daemon.knowledge.raptor import rebuild_raptor_from_canon

        canon_dir = str(Path(self._universe_path) / "canon")
        universe_id = Path(self._universe_path).name or "default"
        rebuild_raptor_from_canon(
            canon_dir=canon_dir,
            embed_fn=runtime.embed_fn,
            universe_id=universe_id,
        )

    def _run_progressive_ingestion(self, universe_id: str) -> None:
        """Survey and ingest canon files before writing begins."""
        canon_dir = Path(self._universe_path) / "canon"
        if not canon_dir.exists():
            logger.debug("No canon directory at %s; skipping ingestion", canon_dir)
            return

        try:
            ingestor = ProgressiveIngestor(canon_dir, universe_id)
            ingestor.survey()
            ingestor.triage()

            # Ingest the first batch of immediate-priority sections
            batch = ingestor.get_next_batch(IngestionPriority.IMMEDIATE)
            for section in batch:
                ingestor.mark_ingested(section)

            logger.info(
                "Progressive ingestion: surveyed %d sections, ingested %d immediate",
                ingestor.state.total_sections,
                ingestor.state.ingested_sections,
            )
        except Exception as e:
            logger.warning("Progressive ingestion failed: %s", e)

    def _heartbeat_loop(self) -> None:
        """Periodically update status.json so external tools know we're alive.

        Runs in a daemon thread; exits when ``_stop_event`` fires.
        """
        while not self._stop_event.wait(timeout=30):
            try:
                self._write_status_file()
            except Exception:
                logger.debug("Heartbeat status write failed", exc_info=True)

    def _try_execute_soul_loop(self, universe_id: str) -> bool:
        """Run a souled universe's declared loop branch via execute_branch.

        Returns True if soul-loop dispatch HANDLED this cycle (the universe is
        soul-declared with a real, non-legacy loop branch — we ran it, or
        refused loudly), so the caller skips the fantasy cycle. Returns False
        when soul-loop dispatch does not apply (no soul, no declared loop, or
        the declared loop is the legacy fantasy wrapper) — the caller falls
        through to the existing fantasy path unchanged.

        Mirrors ``_try_execute_claimed_branch_task`` but sources the branch
        from the universe soul instead of a claimed BranchTask. The run is a
        root activation: its in-node enqueue context is THIS universe
        (trusted), with empty parent/origin so any enqueued children start a
        fresh spawn lineage.
        """
        universe_path = Path(self._universe_path)
        try:
            from tinyassets.api.universe import (
                LEGACY_FANTASY_LOOP_BRANCH_DEF_ID,
                _universe_loop_dispatch,
            )

            loop_branch_def_id, _info = _universe_loop_dispatch(universe_path)
        except Exception:
            logger.exception("soul_loop: loop-dispatch resolution failed")
            return False

        if not loop_branch_def_id or loop_branch_def_id == LEGACY_FANTASY_LOOP_BRANCH_DEF_ID:
            # No soul / no declared loop / legacy loop → not our path.
            return False

        try:
            from tinyassets.branches import BranchDefinition
            from tinyassets.daemon_server import get_branch_definition
            from tinyassets.runs import execute_branch
            from tinyassets.storage import data_dir

            base_path = data_dir()
            try:
                source_dict = get_branch_definition(
                    base_path,
                    branch_def_id=loop_branch_def_id,
                )
            except KeyError:
                logger.error(
                    "soul_loop: declared loop branch %s not found; refusing "
                    "to run the fantasy fallback for souled universe %s",
                    loop_branch_def_id,
                    universe_id,
                )
                return True  # handled (refuse) — do NOT run fantasy fallback

            branch = BranchDefinition.from_dict(source_dict)
            errors = branch.validate()
            if errors:
                logger.error(
                    "soul_loop: declared loop branch %s failed validation: %s",
                    loop_branch_def_id,
                    errors,
                )
                return True

            provider_call: Any = None
            try:
                from domains.fantasy_daemon.phases._provider_stub import (
                    call_provider as provider_call,
                )
            except ImportError:
                provider_call = None

            # The universe runs its OWN soul loop, so the run is recorded as
            # that universe -- never as an ambient environment value, which
            # would forge attribution to whatever the host happened to export
            # (Codex code review round 2, P0). There is no anonymous actor to
            # fall back to.
            actor = f"universe:{universe_id}"
            outcome = execute_branch(
                base_path,
                branch=branch,
                inputs={},
                run_name=f"soul-loop-{universe_id}",
                actor=actor,
                provider_call=provider_call,
                # Root activation for the universe's own loop: trusted in-node
                # enqueue context is THIS universe; empty parent/origin so an
                # enqueued child starts a fresh spawn lineage.
                _enqueue_universe_id=universe_id,
                _parent_branch_task_id="",
                _origin_branch_task_id="",
            )
            logger.info(
                "soul_loop: ran declared loop branch=%s universe=%s run=%s status=%s",
                loop_branch_def_id,
                universe_id,
                getattr(outcome, "run_id", ""),
                getattr(outcome, "status", ""),
            )
            return True
        except Exception:
            logger.exception(
                "soul_loop: execution of declared loop %s failed",
                loop_branch_def_id,
            )
            # Souled + declared: do NOT silently fall back to the fantasy
            # cycle. Treat the cycle as handled (errored), logged loud.
            return True

    def _run_graph(self, universe_id: str) -> None:
        """Build and execute the universe graph.

        Under ``TINYASSETS_UNIFIED_EXECUTION=1`` (Phase D) the graph is
        built via the standard Branch compile path: load the seed
        ``fantasy_author/branches/universe_cycle.yaml``, call
        ``compile_branch(branch)``, and use its ``graph`` where the
        direct path uses ``build_universe_graph()`` today. Everything
        after (SqliteSaver compile, initial_state, stream loop,
        pause/stop, heartbeat, dashboard events) is identical.

        Pause/stop regression under flag-on: wrapper-boundary
        granularity only (preflight §4.10). Checkpoint regression
        under flag-on: only the six boundary fields persist across
        wrapper invocations; mid-cycle state is rebuilt on resume
        via the normal dispatch path (preflight §4.11). Both
        regressions are accepted for v1 by lead direction; flag is
        off by default, opt-in only.

        Option A (soul-loop dispatch, ships dark): if this universe is
        soul-declared with a real ``loop_branch_def_id``, the daemon runs that
        user-built branch via ``execute_branch`` *in place of the fantasy
        cycle* — but ONLY after the normal dispatcher pick has had its chance
        to claim any pending child BranchTasks the driver enqueued (see the
        no-claim slot below). Gated behind ``TINYASSETS_SOUL_LOOP_DISPATCH``; off
        by default so soulless/legacy universes are unchanged. The queue is
        never starved: pick drains children first, the driver only runs when
        there is nothing to claim.
        """
        if _workflow_unified_execution_enabled():
            graph_builder = _build_unified_graph_builder()
        else:
            graph_builder = build_universe_graph()

        output_dir = Path(self._universe_path)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Phase E: startup recovery + GC + observational dispatcher read.
        # Runs regardless of dispatcher flag so the queue file stays
        # healthy across daemon lifecycles (preflight §4.3 #7 + #10).
        _dispatcher_startup(output_dir)
        # Phase F: run BranchTaskProducers at boundary so pool posts
        # land in the queue before we observe/pick.
        _run_branch_task_producers_if_enabled(output_dir)
        _dispatcher_observe(output_dir)

        with SqliteSaver.from_conn_string(self._checkpoint_path) as checkpointer:
            compiled = graph_builder.compile(checkpointer=checkpointer)

            # Resolve premise: explicit value > compatibility file > soul.md.
            premise = self._premise
            if not premise:
                premise = (
                    read_legacy_premise(output_dir).strip() or premise_from_soul(output_dir).strip()
                )

            # TinyAssets configuration
            workflow = {"premise": premise}

            # Seed initial counters from dashboard metrics (already
            # seeded from DB or disk scan in start()) so graph state
            # reflects existing work after a universe switch.
            dm = self._dashboard.metrics if self._dashboard else None

            initial_state: dict[str, Any] = {
                "universe_id": universe_id,
                "universe_path": str(output_dir),
                "active_series": None,
                "series_completed": [],
                "world_state_version": 0,
                "canon_facts_count": 0,
                "total_words": dm.total_words if dm else 0,
                "total_chapters": dm.chapters_complete if dm else 0,
                "health": {},
                "task_queue": ["write"],
                "universal_style_rules": [],
                "cross_series_facts": [],
                "enrichment_signals": [],
                "worldbuild_signals": [],  # deprecated alias; kept for the window
                "premise_kernel": premise,
                "workflow_instructions": workflow,
                # Internal config (serializable scalars only)
                "_universe_path": str(output_dir),
                "_db_path": self._db_path,
                "_kg_path": str(Path(self._universe_path) / "knowledge.db"),
            }

            config = {
                "configurable": {"thread_id": universe_id},
                "recursion_limit": 10000,
            }

            # Recover counters from existing checkpoint so a daemon
            # restart doesn't regress total_words / total_chapters.
            try:
                existing = compiled.get_state(config)
                if existing and existing.values:
                    ev = existing.values
                    for key in (
                        "total_words",
                        "total_chapters",
                        "world_state_version",
                        "canon_facts_count",
                    ):
                        ckpt_val = ev.get(key, 0)
                        cur_val = initial_state.get(key, 0)
                        if isinstance(ckpt_val, int) and ckpt_val > cur_val:
                            initial_state[key] = ckpt_val
                    # Preserve active_series if checkpoint had one
                    if ev.get("active_series"):
                        initial_state["active_series"] = ev["active_series"]
                    # Preserve series_completed list
                    if ev.get("series_completed"):
                        initial_state["series_completed"] = ev["series_completed"]
                    if isinstance(ev.get("health"), dict):
                        initial_state["health"] = dict(ev["health"])
                    logger.info(
                        "Resumed from checkpoint: words=%d, chapters=%d",
                        initial_state["total_words"],
                        initial_state["total_chapters"],
                    )
            except Exception:
                logger.debug("No existing checkpoint to resume from", exc_info=True)
            initial_state = clear_restartable_soft_stop(initial_state)

            if self._dashboard:
                self._dashboard.handle_event(
                    {
                        "type": "phase_start",
                        "phase": "universe_start",
                    }
                )

            # Start heartbeat thread so status.json stays fresh during
            # long-running scenes (can be 6+ minutes with Claude).
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
            )
            self._heartbeat_thread.start()

            # Faster cadence runtime_status.json for the tray provider
            # bridge (updates ~every 5s independent of node events).
            self._write_runtime_status()
            self._runtime_status_thread = threading.Thread(
                target=self._runtime_status_loop,
                daemon=True,
            )
            self._runtime_status_thread.start()

            # Phase F wire-up (preflight §4.10): attempt dispatcher
            # pick BEFORE the stream. If a task is claimed, merge its
            # inputs into initial_state (inputs win on overlap).
            # Unclaimed picks fall through to default graph behavior.
            loop_daemon_context = _resolve_loop_daemon_context(
                output_dir,
                universe_id,
            )
            daemon_id = loop_daemon_context["daemon_id"]
            initial_state.update(
                {
                    "daemon_id": daemon_id,
                    "daemon_soul_text": loop_daemon_context.get("soul_text", ""),
                    "daemon_soul_hash": loop_daemon_context.get("soul_hash", ""),
                    "daemon_domain_claims": loop_daemon_context.get(
                        "domain_claims",
                        [],
                    ),
                    "daemon_wiki_context": loop_daemon_context.get(
                        "daemon_wiki_context",
                        "",
                    ),
                    "daemon_wiki_status": loop_daemon_context.get(
                        "daemon_wiki_status",
                        {},
                    ),
                }
            )
            logger.info(
                "loop daemon identity: %s source=%s has_soul=%s",
                daemon_id,
                loop_daemon_context.get("source", ""),
                loop_daemon_context.get("has_soul", False),
            )
            claimed_task, claimed_inputs = _try_dispatcher_pick(
                output_dir,
                daemon_id,
            )
            claimed_failed_reason = ""
            cancel_requested_during_run = False

            def branch_task_heartbeat(*, force: bool = False) -> None:
                return None

            def branch_task_node_status(node_id: str, status: str) -> None:
                return None

            if claimed_task is not None:
                branch_task_heartbeat, branch_task_node_status = _build_branch_task_observers(
                    output_dir, claimed_task
                )
                branch_task_heartbeat(force=True)
            if claimed_task is not None and claimed_inputs:
                # inputs win on overlap; unknown keys tolerated by
                # LangGraph initial_state.
                initial_state.update(claimed_inputs)
                logger.info(
                    "dispatcher_pick: seeded initial_state with %d keys from task %s",
                    len(claimed_inputs),
                    claimed_task.branch_task_id,
                )

            # Phase G: NodeBid tasks route to execute_node_bid instead
            # of the Branch wrapper stream. Sentinel prefix on
            # branch_def_id is set by the NodeBidProducer.
            from tinyassets.producers.node_bid import NODE_BID_SENTINEL_PREFIX

            if claimed_task is not None and claimed_task.branch_def_id.startswith(
                NODE_BID_SENTINEL_PREFIX
            ):
                branch_task_heartbeat()
                nb_success, nb_error = _try_execute_claimed_node_bid(
                    output_dir,
                    claimed_task,
                    daemon_id,
                )
                branch_task_heartbeat(force=True)
                _record_loop_daemon_signal(
                    loop_daemon_context,
                    universe_path=output_dir,
                    source_id=f"node_bid:{claimed_task.branch_task_id}",
                    outcome="passed" if nb_success else "failed",
                    summary=(
                        f"NodeBid {claimed_task.branch_task_id} "
                        f"{'succeeded' if nb_success else 'failed'}."
                    ),
                    details=nb_error,
                    metadata={
                        "branch_def_id": claimed_task.branch_def_id,
                        "trigger_source": claimed_task.trigger_source,
                    },
                )
                _finalize_claimed_task(
                    output_dir,
                    claimed_task,
                    success=nb_success,
                    error=nb_error,
                )
                claimed_task = None  # prevent double-finalization
                self._cleanup()
                return

            if claimed_task is not None and _should_execute_claimed_branch_directly(claimed_task):
                branch_success, branch_error, branch_metadata = _try_execute_claimed_branch_task(
                    output_dir,
                    claimed_task,
                    daemon_id,
                    on_node_status=branch_task_node_status,
                    branch_task_heartbeat=branch_task_heartbeat,
                )
                if not _is_epoch2_branch_task(claimed_task):
                    branch_task_heartbeat(force=True)
                branch_success, branch_error = _settle_claimed_direct_branch_task(
                    output_dir,
                    claimed_task,
                    success=branch_success,
                    error=branch_error,
                )
                _record_loop_daemon_signal(
                    loop_daemon_context,
                    universe_path=output_dir,
                    source_id=claimed_task.branch_task_id,
                    outcome="passed" if branch_success else "failed",
                    summary=(
                        f"Branch task {claimed_task.branch_task_id} "
                        f"{'succeeded' if branch_success else 'failed'} "
                        "via direct branch execution."
                    ),
                    details=branch_error,
                    metadata={
                        "branch_def_id": claimed_task.branch_def_id,
                        "trigger_source": claimed_task.trigger_source,
                        **branch_metadata,
                    },
                )
                claimed_task = None  # prevent wrapper finalization
                self._cleanup()
                return

            # Idle-cycle single-flight (double-logging root cause,
            # 2026-07-14): with no claimed task, everything below is the
            # per-universe idle heartbeat (soul-loop driver or fantasy
            # cycle). Claims are file-locked but this tail was not, so
            # every healthy fleet worker duplicated it. Skip when another
            # holder is mid-cycle (run lock — covers cycles longer than
            # the freshness window) or a DIFFERENT worker stamped recently;
            # own stamps never block, so solo tray/droplet cadence is
            # unchanged. The winning slot holds an OS lock for the cycle's
            # lifetime; _cleanup releases it (process death also releases).
            if claimed_task is None and idle_cycle.single_flight_enabled():
                slot = idle_cycle.try_acquire_idle_cycle_slot(output_dir)
                if not slot.acquired:
                    logger.info(
                        "idle cycle skipped (single-flight): %s",
                        slot.reason,
                    )
                    self._cleanup()
                    return
                self._idle_cycle_slot = slot
                logger.info("idle cycle slot: %s", slot.reason)

            # No-claim slot: nothing pending to drain this cycle. For a
            # soul-declared universe, run its declared loop branch (the driver)
            # via execute_branch instead of the fantasy cycle. Reached only
            # when no BranchTask was claimed, so child tasks the driver
            # enqueues are always drained first by the pick on later cycles.
            if (
                claimed_task is None
                and _soul_loop_dispatch_enabled()
                and self._try_execute_soul_loop(universe_id)
            ):
                self._cleanup()
                return

            try:
                for event in compiled.stream(initial_state, config=config):
                    branch_task_heartbeat()
                    if self._stop_event.is_set():
                        logger.info("Stop signal received, shutting down")
                        break

                    # Cooperative cancel: a MCP queue_cancel on a
                    # running task sets cancel_requested on the row;
                    # observed at next inter-node event, same shape as
                    # _stop_event. Finalized as "cancelled" (not
                    # "failed") via the claimed_failed_reason=""
                    # branch in the finally block.
                    if claimed_task is not None:
                        if _branch_task_cancel_requested(
                            output_dir,
                            claimed_task,
                        ):
                            logger.info(
                                "queue_cancel observed on running task %s",
                                claimed_task.branch_task_id,
                            )
                            cancel_requested_during_run = True
                            break

                    # Pause support (thread event or .pause flag file)
                    pause_file = Path(self._universe_path) / ".pause"
                    while (
                        self._paused.is_set() or pause_file.exists()
                    ) and not self._stop_event.is_set():
                        branch_task_heartbeat()
                        self._paused.wait(timeout=1.0)

                    # Activity/status handling must run even in --no-tray
                    # cloud-worker mode; dashboard emission is gated inside
                    # _handle_node_output.
                    if isinstance(event, dict):
                        for node_name, node_output in event.items():
                            self._handle_node_output(node_name, node_output)
                            if claimed_task is not None:
                                branch_task_node_status(node_name, "ran")

                    # Phase E: at each cycle boundary (marked by the
                    # `universe_cycle` node in the direct graph, or
                    # the `universe_cycle_wrapper` in the unified
                    # path), observe the dispatcher's top pick so
                    # users see the decision in activity.log.
                    if isinstance(event, dict) and (
                        "universe_cycle" in event or "universe_cycle_wrapper" in event
                    ):
                        _dispatcher_observe(output_dir)

            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt received")
                claimed_failed_reason = "keyboard_interrupt"
            except RuntimeError as e:
                # LangGraph's internal ThreadPoolExecutor raises this when
                # the interpreter is shutting down and the executor rejects
                # new checkpoint writes.  Not actionable — log and exit.
                if "cannot schedule new futures" in str(e):
                    logger.info("Executor shutdown during graph stream (expected on exit)")
                    claimed_failed_reason = "executor_shutdown"
                else:
                    logger.exception("Graph execution failed")
                    if self._notifications:
                        self._notifications.error(f"Graph execution failed: {e}")
                    claimed_failed_reason = f"runtime_error: {e}"
            except Exception as exc:
                logger.exception("Graph execution failed")
                if self._notifications:
                    self._notifications.error("Graph execution failed")
                claimed_failed_reason = f"exception: {exc}"
            finally:
                # Phase F wire-up: finalize the claimed task status
                # based on whether the stream completed cleanly.
                # Phase E cooperative-cancel: a cancel observed in the
                # stream loop finalizes as "cancelled", not "failed".
                if claimed_task is not None:
                    if cancel_requested_during_run:
                        try:
                            _cancel_claimed_task(output_dir, claimed_task)
                            logger.info(
                                "dispatcher_pick: finalized %s -> cancelled",
                                claimed_task.branch_task_id,
                            )
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "cooperative-cancel finalize failed; falling back to failed",
                            )
                            _finalize_claimed_task(
                                output_dir,
                                claimed_task,
                                success=False,
                                error="cancel_finalize_failed",
                            )
                        _record_loop_daemon_signal(
                            loop_daemon_context,
                            universe_path=output_dir,
                            source_id=claimed_task.branch_task_id,
                            outcome="cancelled",
                            summary=(
                                f"Branch task {claimed_task.branch_task_id} "
                                "was cancelled during the loop run."
                            ),
                            metadata={
                                "branch_def_id": claimed_task.branch_def_id,
                                "trigger_source": claimed_task.trigger_source,
                            },
                        )
                    else:
                        _finalize_claimed_task(
                            output_dir,
                            claimed_task,
                            success=not claimed_failed_reason,
                            error=claimed_failed_reason,
                        )
                        _record_loop_daemon_signal(
                            loop_daemon_context,
                            universe_path=output_dir,
                            source_id=claimed_task.branch_task_id,
                            outcome=("failed" if claimed_failed_reason else "passed"),
                            summary=(
                                f"Branch task {claimed_task.branch_task_id} "
                                f"{'failed' if claimed_failed_reason else 'passed'} "
                                "in the loop run."
                            ),
                            details=claimed_failed_reason,
                            metadata={
                                "branch_def_id": claimed_task.branch_def_id,
                                "trigger_source": claimed_task.trigger_source,
                            },
                        )
                try:
                    prune_result = apply_configured_checkpoint_retention(
                        checkpointer,
                        universe_id,
                    )
                    if prune_result and prune_result.checkpoints_deleted:
                        logger.info(
                            "checkpoint_retention: thread=%s keep_last=%d "
                            "deleted_checkpoints=%d deleted_writes=%d",
                            prune_result.thread_id,
                            prune_result.keep_last_n,
                            prune_result.checkpoints_deleted,
                            prune_result.writes_deleted,
                        )
                except Exception:  # noqa: BLE001 — retention is protective
                    logger.warning(
                        "checkpoint_retention: pruning failed for %s",
                        universe_id,
                        exc_info=True,
                    )
                self._cleanup()

                # Handle cross-universe synthesis switch
                if self._pending_universe_switch:
                    self._trigger_universe_switch(self._pending_universe_switch)

    def _handle_node_output(self, node_name: str, output: dict[str, Any]) -> None:
        """Translate graph node outputs into dashboard events and log lines."""
        # Track scene ID and verdict (no dashboard dependency)
        if "orient_result" in output:
            orient = output["orient_result"] or {}
            self._current_scene_id = str(orient.get("scene_id", ""))

        if "verdict" in output:
            self._last_verdict = output["verdict"]
            commit_result = output.get("commit_result") or {}
            self._last_eval_score = commit_result.get("structural_score", self._last_eval_score)

        # Track the most recently used LLM provider
        try:
            from tinyassets.providers.call import get_last_provider

            last_provider = get_last_provider()
            if last_provider:
                self._last_provider_used = last_provider
        except ImportError:
            pass

        # Activity log (always -- writes to activity.log for the API)
        self._emit_node_log(node_name, output)

        # Dashboard events (only when dashboard is available)
        if self._dashboard is not None:
            self._dashboard.handle_event(
                {
                    "type": "phase_start",
                    "phase": node_name,
                }
            )

            # run_book returns total_words/total_chapters at the universe
            # level — update dashboard metrics directly since subgraph
            # scene/chapter events don't propagate to the universe stream.
            if "total_words" in output or "total_chapters" in output:
                new_words = output.get("total_words", 0)
                new_chapters = output.get("total_chapters", 0)
                if new_words > self._dashboard.metrics.total_words:
                    self._dashboard.metrics.total_words = new_words
                    self._dashboard.metrics.update_wph()
                if new_chapters > self._dashboard.metrics.chapters_complete:
                    self._dashboard.metrics.chapters_complete = new_chapters

            if "draft_output" in output:
                draft = output["draft_output"] or {}
                self._dashboard.handle_event(
                    {
                        "type": "draft_progress",
                        "word_count": draft.get("word_count", 0),
                    }
                )

            if "verdict" in output and output["verdict"] == "accept":
                self._dashboard.handle_event(
                    {
                        "type": "scene_complete",
                        "scene_number": output.get("scene_number", 0),
                        "word_count": output.get("draft_output", {}).get("word_count", 0),
                    }
                )

            if "chapter_summary" in output and output["chapter_summary"]:
                self._dashboard.handle_event(
                    {
                        "type": "chapter_complete",
                        "chapter": output.get("chapter_number", 0),
                    }
                )

            if "book_summary" in output and output["book_summary"]:
                self._dashboard.handle_event(
                    {
                        "type": "book_complete",
                        "title": output.get("book_summary", ""),
                    }
                )

        # Generate creative briefing when a chapter completes
        if "chapter_summary" in output and output["chapter_summary"]:
            self._generate_creative_briefing(output)

        # Check for cross-universe synthesis switch request
        health = output.get("health", {})
        switch_target = health.get("switch_to_universe", "")
        if switch_target and node_name in ("universe_cycle", "universe_cycle_wrapper"):
            self._pending_universe_switch = switch_target
            self._stop_event.set()
            logger.info(
                "Cross-universe synthesis: stopping to switch to %s",
                switch_target,
            )

        # Write status file for external tools (throttled to avoid I/O storm)
        now = time.monotonic()
        if now - self._last_status_write >= self._STATUS_WRITE_COOLDOWN:
            self._last_status_write = now
            self._write_status_file()
            self._write_progress_file()

    def _combined_log(self, line: str) -> None:
        """Log a line to both the UI callback and activity.log."""
        if self._log_callback is not None:
            try:
                self._log_callback(line)
            except Exception:
                pass
        self._write_activity_log(line)

    def _write_activity_log(self, line: str) -> None:
        """Append a line to the activity.log file."""
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with open(self._activity_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {line}\n")
        except OSError:
            logger.debug("Failed to write activity.log", exc_info=True)

    @property
    def daemon_state(self) -> str:
        """Return the current daemon state."""
        if self._stop_event.is_set():
            return "idle"
        if not self._ready.is_set():
            return "initializing"
        if self._paused.is_set():
            return "paused"
        return "running"

    def _write_status_file(self) -> None:
        """Write status.json to the universe directory.

        Uses the shared ``_status_lock`` from ``_activity`` to prevent
        corruption when the heartbeat thread and node threads write
        concurrently.
        """
        from fantasy_daemon.nodes._activity import _status_lock

        summary = self._dashboard.summary() if self._dashboard else {}
        # Memory tier counts (lightweight — no LLM calls)
        memory_tiers: dict[str, int] = {}
        if self._memory is not None:
            try:
                core_count = sum(len(v) for v in self._memory.core._store.values())
                memory_tiers["core"] = core_count
            except Exception:
                memory_tiers["core"] = 0
            try:
                row = self._memory.episodic._conn.execute(
                    "SELECT COUNT(*) FROM scene_summaries"
                ).fetchone()
                memory_tiers["episodic"] = row[0] if row else 0
            except Exception:
                memory_tiers["episodic"] = 0

        status = {
            "current_phase": summary.get("current_phase", "idle"),
            "word_count": summary.get("total_words", 0),
            "chapters_complete": summary.get("chapters_complete", 0),
            "scenes_complete": summary.get("scenes_complete", 0),
            "accept_rate": summary.get("accept_rate", 0.0),
            "current_scene_id": self._current_scene_id,
            "verdict": self._last_verdict,
            "provider": self.active_provider_label,
            "current_provider": self._last_provider_used,
            "last_eval_score": self._last_eval_score,
            "memory_tiers": memory_tiers,
            "daemon_state": self.daemon_state,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        status_path = Path(self._universe_path) / "status.json"
        with _status_lock:
            try:
                status_path.write_text(
                    json.dumps(status, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.debug("Failed to write status.json", exc_info=True)

    def _write_runtime_status(self) -> None:
        """Write .runtime_status.json atomically for external status consumers.

        Separate from status.json — this is the tray/lead contract for
        provider visibility: pid, pinned provider (if any), last provider
        used, label, and timestamp. Atomic write via tmp + os.replace.
        """
        payload = {
            "pid": os.getpid(),
            "provider": self._pinned_provider,
            "last_used_provider": self._last_provider_used,
            "active_provider_label": self.active_provider_label,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        tmp_path = self._runtime_status_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp_path, self._runtime_status_path)
        except OSError:
            logger.debug("Failed to write .runtime_status.json", exc_info=True)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _runtime_status_loop(self) -> None:
        """Refresh .runtime_status.json every 5s until stop."""
        while not self._stop_event.wait(timeout=5):
            try:
                self._write_runtime_status()
            except Exception:
                logger.debug("runtime_status loop write failed", exc_info=True)

    def _remove_runtime_status(self) -> None:
        """Delete .runtime_status.json on clean shutdown."""
        try:
            self._runtime_status_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to remove .runtime_status.json", exc_info=True)

    def _write_progress_file(self) -> None:
        """Write progress.md to the universe directory.

        Human-readable creative briefing updated alongside status.json
        after every node output.  Includes a stats header and, when
        available, a cached creative briefing generated after each
        chapter completes.
        """
        summary = self._dashboard.summary() if self._dashboard else {}
        words = summary.get("total_words", 0)
        chapters = summary.get("chapters_complete", 0)
        scenes = summary.get("scenes_complete", 0)
        accept_rate = summary.get("accept_rate", 0.0)

        # Use authoritative daemon_state — dashboard phase can lag behind
        # when the stop event fires.
        is_running = self.daemon_state == "running"

        book = 1

        lines = [
            "# Writing Progress",
            "",
            f"Book {book}, Chapter {chapters + 1} in progress."
            if is_running
            else f"Book {book}, {chapters} chapters complete.",
            f"{words:,} words across {scenes} scenes.",
            f"Accept rate {accept_rate:.0%}.",
            "",
            f"Current phase: {self.daemon_state}.",
            f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        ]

        if self._cached_creative_briefing:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append(self._cached_creative_briefing)

        progress_path = Path(self._universe_path) / "progress.md"
        try:
            progress_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
            )
        except OSError:
            logger.debug("Failed to write progress.md", exc_info=True)

    def _generate_creative_briefing(self, output: dict[str, Any]) -> None:
        """Generate and cache a creative briefing after chapter completion.

        Reads recent chapter prose and the chapter summary, calls the
        provider to produce a structured briefing with story summary,
        active characters, open plot threads, and recent creative
        decisions.  Falls back to the chapter summary alone on failure.
        """
        from tinyassets.providers.call import call_provider

        chapter_summary = output.get("chapter_summary", "")
        if not chapter_summary:
            return

        # Read recent prose from disk
        prose_excerpt = self._read_recent_prose()

        # Read recent activity for creative decision context
        recent_activity = self._read_recent_activity(20)

        system = (
            "You are a creative writing assistant generating a story briefing. "
            "Given a chapter summary, recent prose, and activity log, produce a "
            "structured briefing in Markdown with these sections:\n"
            "## Story So Far\nA 3-5 sentence summary of everything that has happened.\n"
            "## Active Characters\nBullet list of characters active in recent chapters "
            "with a one-line note on each.\n"
            "## Open Plot Threads\nBullet list of unresolved storylines or promises.\n"
            "## Recent Creative Decisions\nBullet list of notable authorial choices "
            "(POV shifts, tone changes, new worldbuilding introduced).\n\n"
            "Be specific — name characters, places, and events. Keep it concise."
        )

        prompt_parts = [f"## Latest Chapter Summary\n{chapter_summary}"]
        if prose_excerpt:
            prompt_parts.append(f"## Recent Prose (excerpt)\n{prose_excerpt[:3000]}")
        if recent_activity:
            prompt_parts.append(f"## Recent Activity Log\n{recent_activity}")
        prompt = "\n\n".join(prompt_parts)

        fallback = f"## Story So Far\n{chapter_summary}"

        try:
            briefing = call_provider(
                prompt,
                system,
                role="extract",
                fallback_response=fallback,
            )
            if briefing and briefing.strip():
                self._cached_creative_briefing = briefing.strip()
            else:
                self._cached_creative_briefing = fallback
        except Exception as e:
            logger.warning("Creative briefing generation failed: %s", e)
            self._cached_creative_briefing = fallback

    def _read_recent_prose(self) -> str:
        """Read the most recent scene prose from disk.

        Scenes are stored as per-scene files inside chapter subdirectories:
        ``output/book-N/chapter-NN/scene-NN.md``.  Returns the latest
        scene file's content from the latest chapter in the latest book.
        """
        output_dir = Path(self._universe_path) / "output"
        if not output_dir.exists():
            return ""
        try:
            # Find the latest book directory
            book_dirs = sorted(
                [d for d in output_dir.iterdir() if d.is_dir() and d.name.startswith("book-")],
                key=lambda d: d.name,
                reverse=True,
            )
            if not book_dirs:
                return ""
            # Find the latest chapter directory in the latest book
            chapter_dirs = sorted(
                [d for d in book_dirs[0].iterdir() if d.is_dir() and d.name.startswith("chapter-")],
                key=lambda d: d.name,
                reverse=True,
            )
            if not chapter_dirs:
                return ""
            # Find the latest scene file in the latest chapter
            scene_files = sorted(
                [f for f in chapter_dirs[0].iterdir() if f.is_file() and f.suffix == ".md"],
                key=lambda f: f.name,
                reverse=True,
            )
            if not scene_files:
                return ""
            return scene_files[0].read_text(encoding="utf-8")
        except OSError as e:
            logger.debug("Failed to read recent prose: %s", e)
            return ""

    def _read_recent_activity(self, lines: int = 20) -> str:
        """Read the last N lines from activity.log."""
        try:
            if not self._activity_log_path.exists():
                return ""
            text = self._activity_log_path.read_text(encoding="utf-8")
            all_lines = text.strip().splitlines()
            return "\n".join(all_lines[-lines:])
        except OSError:
            return ""

    def _emit_node_log(self, node_name: str, output: dict[str, Any]) -> None:
        """Build a human-readable log line for a node output.

        Routes through ``_combined_log`` so every node event is written
        to ``activity.log`` regardless of whether a dashboard is connected.
        """
        if node_name == "orient":
            orient = output.get("orient_result", {})
            scene_id = orient.get("scene_id", "?")
            overdue = len(orient.get("overdue_promises", []))
            arc = orient.get("arc_position", "?")
            parts = [f"Orient: Analyzing scene {scene_id}"]
            if overdue:
                parts.append(f"{overdue} overdue promise{'s' if overdue != 1 else ''}")
            parts.append(f"{arc} arc position")
            self._combined_log(" -- ".join(parts))

        elif node_name == "plan":
            plan = output.get("plan_output", {})
            alts = plan.get("alternatives_considered", 0)
            score = plan.get("best_score", 0.0)
            suffix = "s" if alts != 1 else ""
            self._combined_log(
                f"Plan: Generated {alts} beat alternative{suffix}, best score {score:.2f}"
            )

        elif node_name == "draft":
            draft = output.get("draft_output", {})
            wc = draft.get("word_count", 0)
            is_rev = draft.get("is_revision", False)
            prefix = "Draft (revision)" if is_rev else "Draft"
            self._combined_log(f"{prefix}: Writing {wc:,} words...")

        elif node_name == "commit":
            result = output.get("commit_result", {})
            verdict = output.get("verdict", "?")
            score = result.get("structural_score", 0.0)
            hard = result.get("hard_failure", False)
            if hard:
                warnings = result.get("warnings", [])
                reason = warnings[0] if warnings else "structural failure"
                self._combined_log(f"Commit: Hard failure ({reason}) -- REVERT")
            elif verdict == "accept":
                self._combined_log(f"Commit: Structural score {score:.2f} -- ACCEPT")
            elif verdict == "second_draft":
                self._combined_log(f"Commit: Structural score {score:.2f} -- SECOND DRAFT")
            elif verdict == "revert":
                self._combined_log(f"Commit: Structural score {score:.2f} -- REVERT")

        elif node_name == "select_task":
            trace = _first_trace(output)
            queue = output.get("task_queue", ["?"])
            selected = trace.get("selected", queue[0] if queue else "?")
            reason = trace.get("reason", "default")
            self._combined_log(f"Select task: {selected} (reason={reason})")

        elif node_name == "worldbuild":
            trace = _first_trace(output)
            signals_acted = trace.get("signals_acted", 0)
            generated = trace.get("generated_files", [])
            version = trace.get("world_state_version", "?")
            if signals_acted:
                self._combined_log(
                    f"Worldbuild: Acted on {signals_acted} signal(s), version {version}"
                )
            elif generated:
                self._combined_log(
                    f"Worldbuild: Generated {len(generated)} canon file(s), version {version}"
                )
            else:
                self._combined_log(f"Worldbuild: No changes, version {version}")

        elif node_name == "universe_cycle_wrapper":
            health = output.get("health", {})
            if not isinstance(health, dict):
                health = {}
            reason = str(health.get("idle_reason") or "continue")
            stopped = bool(health.get("stopped", False))
            total_words = output.get("total_words", 0)
            total_chapters = output.get("total_chapters", 0)
            # Substrate-fix #12 Family A Phase 1.B: additive neutral fields
            # alongside the legacy reason for first-observer legibility.
            # The legacy `reason` field stays for back-compat; consumers can
            # opt into reason_code / display_reason for domain-neutral text.
            if stopped and not total_words and not total_chapters:
                reason_code = "no_active_work"
                display_reason = "idle_no_active_work"
            else:
                reason_code = reason
                display_reason = reason
            self._combined_log(
                "Universe cycle wrapper: completed "
                f"(stopped={stopped}, reason={reason}, "
                f"reason_code={reason_code}, "
                f"display_reason={display_reason}, "
                f"words={total_words}, "
                f"chapters={total_chapters})"
            )

        elif node_name == "reflect":
            trace = _first_trace(output)
            reviewed = trace.get("canon_files_reviewed", 0)
            rewritten = trace.get("canon_files_rewritten", [])
            reflexion = trace.get("reflexion_ran", False)
            parts = ["Reflect:"]
            if reflexion:
                parts.append("reflexion ran")
            parts.append(f"{reviewed} canon file(s) reviewed")
            if rewritten:
                parts.append(f"{len(rewritten)} rewritten")
            self._combined_log(" -- ".join(parts))

    def _cleanup(self) -> None:
        """Release all resources."""
        # Release the idle-cycle single-flight run lock (held for the
        # cycle's lifetime so long cycles exclude other workers; process
        # death would release it too — this handles in-process reuse).
        slot = getattr(self, "_idle_cycle_slot", None)
        if slot is not None:
            self._idle_cycle_slot = None
            slot.release()

        # Final status/progress write so external tools see idle state
        try:
            self._write_status_file()
            self._write_progress_file()
        except Exception:
            logger.debug("Final status write on cleanup failed", exc_info=True)

        # Stop the 5s runtime-status heartbeat. _stop_event is already set
        # by the shutdown path, so the thread's wait() returns promptly;
        # a bounded join keeps shutdown snappy even if a write is in flight.
        if self._runtime_status_thread is not None:
            self._runtime_status_thread.join(timeout=6)

        # Runtime-status file is a liveness signal — remove on clean exit
        # so the tray sees "no daemon" rather than stale provider text.
        self._remove_runtime_status()

        # Close retrieval backends before resetting runtime
        if runtime.knowledge_graph is not None:
            try:
                runtime.knowledge_graph.close()
            except Exception:
                pass
        runtime.reset()
        if self._memory:
            self._memory.close()
        if self._version_store:
            self._version_store.close()
        if self._promise_tracker:
            self._promise_tracker.close()
        if self._tray:
            self._tray.stop()
        logger.info("Daemon shutdown complete")

    def _trigger_universe_switch(self, target_universe: str) -> None:
        """Request the API to start a new daemon on a different universe.

        Called after cleanup when cross-universe synthesis is needed.
        Uses the API's internal _start_daemon_for function if available.
        """
        logger.info(
            "Triggering cross-universe switch: %s -> %s",
            self._universe_id,
            target_universe,
        )
        try:
            from fantasy_daemon.api import _start_daemon_for

            _start_daemon_for(target_universe)
            logger.info("Started daemon on %s for synthesis", target_universe)
        except Exception as e:
            logger.warning(
                "Cross-universe switch to %s failed: %s. "
                "Synthesis will be picked up on next manual start.",
                target_universe,
                e,
            )

    @property
    def active_provider_label(self) -> str:
        """Return a human-readable label for the current provider status."""
        if self._router is None:
            return "Mock (no LLM connected)"
        providers = self._router.available_providers
        if not providers:
            return "Mock (no LLM connected)"
        return ", ".join(providers)

    # ------------------------------------------------------------------
    # Tray callbacks
    # ------------------------------------------------------------------

    def _on_tray_start(self) -> None:
        logger.info("Tray: start requested")

    def _on_tray_pause(self) -> None:
        logger.info("Tray: pause requested")
        self._paused.set()

    def _on_tray_resume(self) -> None:
        logger.info("Tray: resume requested")
        self._paused.clear()

    def _on_tray_quit(self) -> None:
        logger.info("Tray: quit requested")
        self._stop_event.set()
        self._paused.clear()  # Unblock if paused


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# Public ingress: removed (cloud-only policy)
# ---------------------------------------------------------------------------
# This process must never publish a public ingress or enroll itself as a
# Cloudflare connector, even momentarily: a connector that receives a public
# request has already absorbed public availability, and refusing afterwards is
# too late. The local tunnel launcher (``_start_tunnel``) and its drain/stop
# helpers were deleted rather than gated, so there is no reachable code path
# that spawns ``cloudflared`` from a client, tray or daemon start.
#
# Cloud ingress is unaffected: it is owned by the hosted deployment
# (``deploy/compose.yml`` + ``scripts/run-tunnel.sh``), which this module does
# not touch. Local API and testing runs are also unaffected -- they simply bind
# a local port and publish nothing.
# ---------------------------------------------------------------------------

LOCAL_TUNNEL_REMOVED_MESSAGE = (
    "Local Cloudflare tunnel startup has been removed: this process must never "
    "publish a public ingress or enroll as a Cloudflare connector. Use the "
    "cloud-hosted app at https://tinyassets.io/mcp instead. Local API and "
    "testing runs still work -- drop --tunnel / --tunnel-name."
)


def _refuse_local_tunnel_request(source: str) -> None:
    """Fail loudly when a caller asks this process to publish a public ingress.

    Parameters
    ----------
    source : str
        Where the request came from, for the log line (a CLI flag, tray mode).

    Raises
    ------
    SystemExit
        Always. The option is still recognised so the caller gets a migration
        message rather than a bare argparse error, but it is never accepted:
        silently ignoring it would let a caller believe a tunnel was started.
    """
    logger.error("%s (requested via %s)", LOCAL_TUNNEL_REMOVED_MESSAGE, source)
    raise SystemExit(LOCAL_TUNNEL_REMOVED_MESSAGE)


def _run_tray_mode(args: argparse.Namespace) -> None:
    """Tray-only mode: API + daemon with system tray icon.

    Designed for the desktop shortcut (pythonw.exe / .pyw): no console
    window, no Tkinter launcher GUI.  Everything runs from the system
    tray with right-click controls.

    Flow: tray icon appears -> API starts (bound locally) -> daemon
    auto-selects a universe.  No public ingress is ever published; a
    tunnel request refuses before anything starts.
    """
    if getattr(args, "tunnel", False) or getattr(args, "tunnel_name", ""):
        _refuse_local_tunnel_request("--tunnel/--tunnel-name in tray mode")
    import uvicorn

    from fantasy_daemon.api import app, configure

    base_path = str(Path(args.universe).resolve())
    Path(base_path).mkdir(parents=True, exist_ok=True)

    # Resolve which universe to write
    active_file = Path(base_path) / ".active_universe"
    universe_path = ""
    if active_file.exists():
        try:
            uid = active_file.read_text(encoding="utf-8").strip()
            candidate = Path(base_path) / uid
            if uid and candidate.is_dir():
                universe_path = str(candidate)
        except OSError:
            pass

    if not universe_path:
        try:
            for entry in sorted(Path(base_path).iterdir()):
                if entry.is_dir() and (
                    (entry / "PROGRAM.md").exists() or (entry / "soul.md").exists()
                ):
                    universe_path = str(entry)
                    break
        except OSError:
            pass

    # Shared state for the tray to control
    shutdown_event = threading.Event()
    controller: DaemonController | None = None
    tray: TrayApp | None = None
    uvicorn_server: Any = None

    def _on_tray_pause() -> None:
        if controller is not None:
            controller._paused.set()
        if tray is not None:
            tray.update_status("Paused")

    def _on_tray_resume() -> None:
        if controller is not None:
            controller._paused.clear()
        if tray is not None:
            tray.update_status("Running")

    def _on_tray_quit() -> None:
        logger.info("Tray: quit requested")
        shutdown_event.set()
        if controller is not None:
            controller._stop_event.set()
            controller._paused.clear()
        if uvicorn_server is not None:
            uvicorn_server.should_exit = True

    # Create tray icon
    universe_name = Path(universe_path).name if universe_path else ""
    tray = TrayApp(
        on_pause=_on_tray_pause,
        on_resume=_on_tray_resume,
        on_quit=_on_tray_quit,
        output_dir=universe_path or base_path,
    )
    if universe_name:
        tray.update_extended_status(universe_name=universe_name)
    tray.start()
    tray.update_status("Starting...")

    # Start daemon if we have a universe
    daemon_thread = None
    if universe_path:
        controller = DaemonController(
            universe_path=universe_path,
            no_tray=True,  # We manage tray ourselves
            premise=args.premise,
            pinned_provider=args.provider,
        )
        # Wire dashboard to update tray status
        controller._tray = tray
        daemon_thread = threading.Thread(
            target=controller.start,
            name="daemon",
            daemon=False,
        )
        daemon_thread.start()

    # Configure API
    configure(
        base_path=base_path,
        api_key=os.environ.get("FA_API_KEY", ""),
        daemon=controller,
        daemon_thread=daemon_thread,
    )

    port = args.port

    tray.update_status("Running")

    # Periodic tray status update from dashboard metrics
    def _tray_status_poller() -> None:
        while not shutdown_event.is_set():
            time.sleep(5)
            if controller is not None and controller._dashboard is not None:
                summary = controller._dashboard.summary()
                words = summary.get("total_words", 0)
                phase = summary.get("current_phase", "idle")
                if tray is not None:
                    tray.update_extended_status(word_count=words, phase=phase)

    threading.Thread(target=_tray_status_poller, daemon=True).start()

    # Run uvicorn (blocks until shutdown)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=port,
        log_level="info",
    )
    uvicorn_server = uvicorn.Server(config)

    # Handle SIGINT/SIGTERM
    def _signal_handler(sig: int, frame: Any) -> None:
        _on_tray_quit()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    uvicorn_server.run()

    # Cleanup after uvicorn exits
    shutdown_event.set()
    if controller is not None:
        controller._stop_event.set()
        controller._paused.clear()
    if daemon_thread is not None:
        daemon_thread.join(timeout=5)
    if tray is not None:
        tray.stop()


def _main_unfenced() -> None:
    parser = argparse.ArgumentParser(
        prog="fantasy-author",
        description="Fantasy Author -- autonomous fiction generation daemon",
    )
    parser.add_argument(
        "--universe",
        default="output/default-universe",
        help="Path to the universe output directory",
    )
    parser.add_argument(
        "--db",
        default="",
        help="Path to the world state SQLite database (default: <universe>/story.db)",
    )
    parser.add_argument(
        "--checkpoint-db",
        default="",
        help="Path to the LangGraph checkpoint database (default: <universe>/checkpoints.db)",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run without the system tray icon",
    )
    parser.add_argument(
        "--premise",
        default="",
        help="Story premise / prompt (overrides PROGRAM.md or soul.md in universe dir)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Start the MCP server instead of the writing daemon",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the daemon + FastAPI HTTP server",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="API server bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="API server port (default: 8000)",
    )
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help=(
            "REMOVED -- local Cloudflare tunnels are no longer started; "
            "use the cloud-hosted app at https://tinyassets.io/mcp"
        ),
    )
    parser.add_argument(
        "--tunnel-name",
        default="",
        help=(
            "REMOVED -- local Cloudflare tunnels are no longer started; "
            "use the cloud-hosted app at https://tinyassets.io/mcp"
        ),
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Tray-only mode: API + daemon with system tray icon, no console",
    )
    parser.add_argument(
        "--universe-server",
        action="store_true",
        help="Start the TinyAssets MCP server (remote MCP interface)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=8001,
        help="TinyAssets MCP server port (default: 8001)",
    )
    parser.add_argument(
        "--mcp-transport",
        default="streamable-http",
        choices=["streamable-http", "sse", "stdio"],
        help="MCP transport protocol (default: streamable-http)",
    )
    parser.add_argument(
        "--provider",
        default="",
        help=(
            "Pin the writer role to a single provider (no fallback). "
            "Known: claude-code, codex, ollama-local. Omit for the default "
            "fallback chain."
        ),
    )

    args = parser.parse_args()

    # Public ingress is cloud-only: refuse the obsolete request loudly rather
    # than accepting the flag and starting nothing.
    if args.tunnel:
        _refuse_local_tunnel_request("--tunnel")
    if args.tunnel_name:
        _refuse_local_tunnel_request("--tunnel-name")

    # Apply --provider pin via TINYASSETS_PIN_WRITER env var so the router
    # consults it per-call instead of mutating FALLBACK_CHAINS at import.
    # Env-var routing survives subprocess boundaries for future multi-
    # daemon tray work and keeps the module shape stable.
    if args.provider:
        from fantasy_daemon.providers import router as _router_mod

        known = set().union(*_router_mod.FALLBACK_CHAINS.values())
        if args.provider not in known:
            parser.error(
                f"--provider {args.provider!r} is not a known provider. Known: {sorted(known)}"
            )
        os.environ["TINYASSETS_PIN_WRITER"] = args.provider
        logger.info(
            "Writer role pinned to provider %s via TINYASSETS_PIN_WRITER",
            args.provider,
        )

    # Logging setup
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.mcp:
        os.environ.setdefault("TINYASSETS_UNIVERSE", args.universe)
        from fantasy_daemon.mcp_server import main as mcp_main

        mcp_main()
        return

    if args.universe_server:
        # Set base path for the TinyAssets MCP server (resolves universe directories)
        base = str(Path(args.universe).resolve())
        os.environ.setdefault("TINYASSETS_DATA_DIR", base)
        logger.info(
            "Starting TinyAssets MCP server on %s:%d (transport=%s, base=%s)",
            args.host,
            args.mcp_port,
            args.mcp_transport,
            base,
        )

        from fantasy_daemon.universe_server import main as us_main

        us_main(
            host=args.host,
            port=args.mcp_port,
            transport=args.mcp_transport,
        )
        return

    if args.tray:
        _run_tray_mode(args)
        return

    if args.serve:
        import uvicorn

        from fantasy_daemon.api import app, configure

        # Resolve base path: --universe is the base directory in serve mode
        base_path = str(Path(args.universe).resolve())
        Path(base_path).mkdir(parents=True, exist_ok=True)

        # Resolve which universe to write in.  Priority:
        # 1. .active_universe file (persisted from last session)
        # 2. First universe subdir that has a PROGRAM.md or soul.md
        # 3. Don't start a daemon — let the API handle it on demand
        active_file = Path(base_path) / ".active_universe"
        universe_path = ""
        if active_file.exists():
            try:
                uid = active_file.read_text(encoding="utf-8").strip()
                candidate = Path(base_path) / uid
                if uid and candidate.is_dir():
                    universe_path = str(candidate)
                    logger.info("Resuming daemon on persisted universe: %s", uid)
            except OSError:
                pass

        if not universe_path:
            # Scan for a universe with a premise
            try:
                for entry in sorted(Path(base_path).iterdir()):
                    if entry.is_dir() and (
                        (entry / "PROGRAM.md").exists() or (entry / "soul.md").exists()
                    ):
                        universe_path = str(entry)
                        logger.info("Auto-selected universe with premise: %s", entry.name)
                        break
            except OSError:
                pass

        controller = None
        daemon_thread = None
        if universe_path:
            controller = DaemonController(
                universe_path=universe_path,
                no_tray=True,
                premise=args.premise,
                pinned_provider=args.provider,
            )
            daemon_thread = threading.Thread(
                target=controller.start,
                name="daemon",
                daemon=False,
            )
            daemon_thread.start()
        else:
            logger.info(
                "No active universe found; daemon will start when a "
                "universe is selected via the API"
            )

        # Configure API layer with base path
        configure(
            base_path=base_path,
            api_key=os.environ.get("FA_API_KEY", ""),
            daemon=controller,
            daemon_thread=daemon_thread,
        )

        # Graceful shutdown: signal daemon to stop when uvicorn exits
        def _serve_signal_handler(sig: int, frame: Any) -> None:
            logger.info("Signal %d received, stopping daemon", sig)
            if controller is not None:
                controller._stop_event.set()
                controller._paused.clear()

        signal.signal(signal.SIGINT, _serve_signal_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _serve_signal_handler)

        logger.info(
            "Starting API server on %s:%d serving universes from '%s'",
            args.host,
            args.port,
            base_path,
        )
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        # After uvicorn exits, ensure daemon cleanup and wait for thread
        if controller is not None:
            controller._stop_event.set()
            controller._paused.clear()
        if daemon_thread is not None:
            daemon_thread.join(timeout=5)
        return

    controller = DaemonController(
        universe_path=args.universe,
        db_path=args.db,
        checkpoint_path=args.checkpoint_db,
        no_tray=args.no_tray,
        premise=args.premise,
        pinned_provider=args.provider,
    )

    # Handle SIGINT/SIGTERM gracefully
    def _signal_handler(sig: int, frame: Any) -> None:
        logger.info("Signal %d received, stopping", sig)
        controller._stop_event.set()
        controller._paused.clear()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    controller.start()


def _writer_root_from_argv(argv: list[str]) -> Path:
    universe = "output/default-universe"
    for index, argument in enumerate(argv):
        if argument == "--universe" and index + 1 < len(argv):
            universe = argv[index + 1]
            break
        if argument.startswith("--universe="):
            universe = argument.split("=", 1)[1]
            break
    universe_path = Path(universe).absolute()
    if "--serve" in argv or "--universe-server" in argv:
        return universe_path
    return universe_path.parent


def main() -> None:
    """Fence every legacy daemon/API command for its full writer lifetime."""

    from tinyassets.scoped_reset import prepare_service_writer_barrier

    writer_barrier = prepare_service_writer_barrier(_writer_root_from_argv(sys.argv[1:]))
    try:
        _main_unfenced()
    finally:
        writer_barrier.release()


if __name__ == "__main__":
    main()
