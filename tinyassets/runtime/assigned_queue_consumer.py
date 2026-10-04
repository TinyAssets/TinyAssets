"""Bounded daemon-owned consumer for assigned-provider automation tasks."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tinyassets.branch_tasks_v2 import DESCRIPTOR_VALIDITY_SECONDS
from tinyassets.consumer_reason_actions import RETIRED_FLEET_CONTROL_REASON
from tinyassets.platform_runtime_provenance import (
    require_process_cloud_admission,
)

logger = logging.getLogger(__name__)

#: Universe-lease TTL when no operator run timeout is set, which is the default.
#: A lease only has to outlive the gap to its next re-stamp -- the batch refresher
#: runs every `LEASE_REFRESH_SECONDS` and the unstopped sweep every cycle -- so
#: expiry still means "nobody is refreshing", which is the property the lease is
#: for. It used to be the RUN TIMEOUT; a run finishes when it is finished
#: (founder, 2026-09-30), so there is no run duration to derive a TTL from, and a
#: TTL is not the place to invent one.
_LEASE_TTL_FLOOR_S = 900.0


def _lease_ttl_seconds() -> float:
    """The universe-lease TTL: an operator's run timeout if set, else the floor."""
    from tinyassets.automations import run_timeout_seconds

    return run_timeout_seconds() or _LEASE_TTL_FLOOR_S
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DEFAULT_GLOBAL_CONCURRENCY = 2
_DEFAULT_POLL_SECONDS = 2.0
_CONSUMER_REGISTRY_LOCK = threading.Lock()
_STARTED_CONSUMERS: weakref.WeakSet[AssignedQueueConsumer] = weakref.WeakSet()


# Supervisor heartbeat naming. Moved here from the retired host-run
# `tinyassets.cloud_worker` fleet supervisor: the served consumer is the only
# remaining writer of these beats, and `deploy/daemon-watchdog.sh` restarts
# the daemon when the freshest one goes stale.
SUPERVISOR_HEARTBEAT_FILENAME = ".worker_supervisor.json"


def _safe_worker_id(worker_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", worker_id.strip())
    return safe.strip(".-") or "default"


def supervisor_heartbeat_filename(worker_id: str | None = None) -> str:
    """Per-consumer heartbeat filename, falling back to the shared legacy name.

    A blank / unsanitizable id keeps the legacy ``.worker_supervisor.json``
    so readers that predate per-consumer beats still find one.
    """
    clean = _safe_worker_id(worker_id or "")
    if not worker_id or clean == "default":
        return SUPERVISOR_HEARTBEAT_FILENAME
    return f".worker_supervisor.{clean}.json"


def _configured_poll_seconds() -> float:
    raw = os.environ.get("TINYASSETS_ASSIGNED_QUEUE_POLL_SECONDS", "").strip()
    value = _DEFAULT_POLL_SECONDS if not raw else float(raw)
    if value <= 0:
        raise ValueError("assigned queue poll interval must be positive")
    return value


def _is_hex_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _release_build_sha() -> str:
    """The sha production is serving (release-state.json), so the beat is truthful
    when TINYASSETS_BUILD_SHA is unset (prod never sets it); zeros only when unknown."""
    try:
        from tinyassets.api.status import _load_release_state

        candidate = str(_load_release_state().get("git_sha") or "").strip().lower()
    except Exception:  # noqa: BLE001 - a missing receipt must never stop the beat
        candidate = ""
    return candidate if _is_hex_sha(candidate) else "0" * 40


def _error_reason(prefix: str, exc: BaseException) -> str:
    """`prefix:ExcType:message` with the message sanitised and bounded.

    Live 2026-08-25: prod reported only `prepare_error:PermissionError`, which
    named no cause — and prod has no Python-level log route, so the ledger row is
    the only place a cause can appear. These messages are developer-authored
    strings; filesystem paths and anything that looks like a secret are stripped
    before the row is written.
    """
    text = " ".join(str(exc).split())
    text = re.sub(r"[A-Za-z]:[\\/][^\s]*", "<path>", text)
    text = re.sub(r"(?<![\w-])/[^\s]{2,}", "<path>", text)
    text = re.sub(r"[A-Za-z0-9_-]{24,}", "<redacted>", text)
    text = text[:120].strip()
    return f"{prefix}:{type(exc).__name__}" + (f":{text}" if text else "")


def assigned_queue_refusal_freshness_seconds() -> float:
    return 5 * _configured_poll_seconds()


def assigned_queue_consumer_enabled() -> bool:
    return os.environ.get("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "").strip().lower() in _TRUTHY


def _global_concurrency() -> int:
    raw = os.environ.get("TINYASSETS_ASSIGNED_QUEUE_GLOBAL_CONCURRENCY", "").strip()
    value = _DEFAULT_GLOBAL_CONCURRENCY if not raw else int(raw)
    if not 1 <= value <= 32:
        raise ValueError("assigned queue global concurrency must be between 1 and 32")
    return value


def current_consumer_liveness(base_path: Path) -> dict[str, Any]:
    """Observe this daemon's actual coordinator, never historical tenant files.

    This is liveness of the poll loop, not proof that a particular user's work
    succeeded. Per-universe heartbeat/queue descriptor readers keep their own
    scope and authority. No identity, path, tenant count or task data escapes.
    """
    if not assigned_queue_consumer_enabled():
        return {"present": False, "phase": "disabled"}
    if os.environ.get("TINYASSETS_ENGINE_GRAPH_ID", "").strip():
        # Engine MCP children inherit the enable flag but do not own the daemon
        # coordinator. Their process-local registry cannot attest its liveness.
        return {"present": True, "alive": None, "beat_age_s": None,
                "phase": "external_coordinator", "consec_crashes": 0}
    root = Path(base_path).resolve()
    with _CONSUMER_REGISTRY_LOCK:
        consumers = [consumer for consumer in _STARTED_CONSUMERS
                     if consumer.base_path.resolve() == root]
    if not consumers:
        return {"present": True, "alive": False, "beat_age_s": None,
                "phase": "not_started", "consec_crashes": 0}
    snapshots = [consumer._liveness_snapshot() for consumer in consumers]
    # A fresh peer cannot conceal a stopped or stalled expected coordinator.
    return max(snapshots, key=lambda item: (
        item["alive"] is False, float(item["beat_age_s"] or 0.0)))


class AssignedQueueConsumer:
    """One coordinator and fixed executor; never owns the HTTP main thread."""

    def __init__(
        self,
        base_path: str | Path,
        *,
        max_concurrency: int | None = None,
        poll_seconds: float | None = None,
    ) -> None:
        self.base_path = Path(base_path)
        self.max_concurrency = max_concurrency or _global_concurrency()
        if not 1 <= self.max_concurrency <= 32:
            raise ValueError("max_concurrency must be between 1 and 32")
        resolved_poll_seconds = (
            _configured_poll_seconds() if poll_seconds is None else poll_seconds
        )
        if resolved_poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        boot = uuid.uuid4().hex
        self.boot_id = boot
        self.consumer_id = f"worker_assigned_{boot}"
        self.lease_id = f"assigned-lease:{boot}"
        self.poll_seconds = resolved_poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_concurrency,
            thread_name_prefix="assigned-queue-task",
        )
        self._lock = threading.Lock()
        self._active: dict[str, Future[Any]] = {}
        self._automation_runs: set[str] = set()
        # Universes whose automation run ignored cancellation at timeout, with
        # the runs of that batch. The universe stays leased AND busy until they
        # are terminal: re-acquiring our own lease must not admit new work
        # while a provider call we started is still running (Codex 2026-09-27).
        self._unstopped: dict[str, set[str]] = {}
        # This process's liveness lock (see `process_liveness`). Held for
        # the process lifetime so a dead holder's leases can be reclaimed.
        self._liveness: Any = None
        self._recorded: dict[str, tuple[str, float]] = {}
        self._liveness_lock = threading.Lock()
        self._started_monotonic = 0.0
        self._last_poll_completed: float | None = None
        self._last_poll_failed = False
        from tinyassets.control_plane.scheduler import ControlPlaneScheduler

        self._control_plane = ControlPlaneScheduler(self.base_path)

    def start(self) -> None:
        # Gate start() itself (Codex #6, #2516): with the flag unset, constructing +
        # start()ing a consumer must spin up NO coordinator thread — the dark guarantee
        # is "no side effect when off", not merely "no DB writes".
        if not assigned_queue_consumer_enabled():
            return
        require_process_cloud_admission(surface="assigned queue consumer")
        if self._thread is not None:
            return
        self._scavenge_orphaned_credentials()
        self._retire_fleet_controls()
        self._hold_liveness()
        self._thread = threading.Thread(
            target=self._run,
            name="assigned-queue-consumer",
            daemon=True,
        )
        self._started_monotonic = time.monotonic()
        self._thread.start()
        with _CONSUMER_REGISTRY_LOCK:
            _STARTED_CONSUMERS.add(self)

    def _hold_liveness(self) -> None:
        from tinyassets.automations import AutomationStore
        from tinyassets.process_liveness import (
            DEAD,
            LIVENESS_DIR,
            hold_liveness,
            owner_state,
            remove_if_dead,
        )
        from tinyassets.runs import in_flight_owner_tokens
        from tinyassets.singleton_lock import _pid_path

        try:
            held = hold_liveness(self.base_path, self.consumer_id)
        except Exception:  # noqa: BLE001 - without it, leases fall back to TTL
            logger.exception("consumer liveness lock unavailable")
            held = None
        if held is not None and held.acquired:
            self._liveness = held
        elif held is not None:
            # An UNLOCKED file under our own id would read as proof that this
            # live process is dead. Remove it; our leases then wait out a TTL.
            logger.error("consumer liveness lock not acquired; removing its file")
            for path in (held.path, _pid_path(held.path)):
                try:
                    path.unlink()
                except OSError:
                    pass
        # Every boot adds a file and a kill leaves it behind. A dead holder's
        # file is removed only once NO lease and NO in-flight run names it:
        # until then it is the proof another process needs to reclaim that
        # holder's lease or recover its run.
        try:
            # Probe FIRST, then read who is still named: a dead process can add
            # no reference after its probe returned dead, so a snapshot taken
            # before the probe could miss a run it committed just before dying.
            store = AutomationStore(self.base_path)

            def still_named(holder: str) -> bool:
                from tinyassets.universe_seats import holder_is_named

                return (
                    holder_is_named(self.base_path, holder)
                    or holder in store.lease_holders()
                    or holder in in_flight_owner_tokens(self.base_path)
                )

            for stale in (self.base_path / LIVENESS_DIR).glob("*.lock"):
                if stale.stem != self.consumer_id and owner_state(
                    self.base_path, stale.stem,
                ) == DEAD:
                    remove_if_dead(self.base_path, stale.stem, still_named)
        except (OSError, sqlite3.Error):
            logger.exception("consumer liveness sweep failed")

    def _release_liveness(self) -> None:
        """Drop the liveness lock only once nothing we started can still run.

        Releasing it declares this process dead to every other consumer, which
        may then take our leases. A run that survived `stop()` is still work in
        flight, so the lock stays with the process and the kernel drops it.
        """
        from tinyassets.singleton_lock import release_singleton_lock

        if self._liveness is None:
            return
        with self._lock:
            busy = bool(self._unstopped) or any(
                not future.done() for future in self._active.values()
            )
        if busy or (self._thread is not None and self._thread.is_alive()):
            return
        release_singleton_lock(self._liveness)
        self._liveness = None

    def _liveness_snapshot(self) -> dict[str, Any]:
        with self._liveness_lock:
            completed = self._last_poll_completed
            failed = self._last_poll_failed
        age = max(0.0, time.monotonic() - (
            self._started_monotonic if completed is None else completed))
        running = self._thread is not None and self._thread.is_alive()
        alive = running and not self._stop.is_set() and age <= max(
            300.0, self.poll_seconds + 120.0)
        phase = "polling" if completed is not None else "starting"
        if failed:
            phase = "poll_failed"
        if not running:
            phase = "stopped"
        elif self._stop.is_set():
            phase = "stopping"
        elif not alive:
            phase = "stalled"
        return {"present": True, "alive": alive, "beat_age_s": round(age, 1),
                "phase": phase, "consec_crashes": 0}

    def _scavenge_orphaned_credentials(self) -> None:
        """Startup reclamation of orphaned provider-launch-credential dirs a crash left
        behind, across every serving universe (Codex #4, #2516). Never blocks boot."""
        from tinyassets.credential_vault import scavenge_orphaned_launch_credentials
        from tinyassets.provider_serving_binding import list_serving_universes

        try:
            for universe_id in list_serving_universes(self.base_path):
                scavenge_orphaned_launch_credentials(self.base_path / universe_id)
        except Exception:  # noqa: BLE001 - startup reclamation must never block boot
            logger.exception("assigned queue consumer credential scavenge failed")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        # Cancel in-flight automation runs BEFORE tearing the executor down.
        # `shutdown(cancel_futures=True)` cancels only futures that have not
        # STARTED; an automation blocked in `wait_for` keeps its worker and its
        # provider authority claim (Codex ADAPT 2026-08-29 §1). The runs cancel
        # flag is cooperative and is checked between nodes, so the graph unwinds
        # and the foreground session releases its claim.
        self._cancel_automation_runs()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))
        self._executor.shutdown(wait=False, cancel_futures=True)
        # Retire only after the actual coordinator exits. A timed-out join must
        # remain visible as stopping, not disappear from expected execution.
        if self._thread is None or not self._thread.is_alive():
            with _CONSUMER_REGISTRY_LOCK:
                _STARTED_CONSUMERS.discard(self)
        self._release_liveness()

    def _cancel_automation_runs(self) -> None:
        from tinyassets.runs import request_cancel

        with self._lock:
            run_ids = sorted(self._automation_runs)
            self._automation_runs.clear()
        for run_id in run_ids:
            try:
                request_cancel(self.base_path, run_id)
            except Exception:  # noqa: BLE001 - shutdown must not raise
                logger.exception("automation run cancel failed run=%s", run_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - task scanning cannot kill daemon
                with self._liveness_lock:
                    self._last_poll_failed = True
                logger.exception("assigned queue consumer poll failed")
            else:
                with self._liveness_lock:
                    self._last_poll_completed = time.monotonic()
                    self._last_poll_failed = False
            self._stop.wait(self.poll_seconds)

    def poll_once(self) -> int:
        """Beat for every serving universe and submit its due automations.

        User-owned automations are the only background work this consumer runs.
        The epoch-2 claim pass it used to run after them executed only slices of
        fleet-era cloud automations, which nothing produces since that pump was
        retired (plan C1); it went with them (plan C2).
        """

        if not assigned_queue_consumer_enabled():
            return 0
        # A copied runtime row or cached receipt cannot authorize recovery,
        # automatic work submission or publication of compatible capacity.
        require_process_cloud_admission(surface="assigned queue consumer")
        from tinyassets.provider_serving_binding import list_serving_universes
        from tinyassets.storage.assigned_queue_refusals import (
            AssignedQueueRefusalStore,
        )

        prep_store = AssignedQueueRefusalStore(self.base_path)
        # `.pause` is the universe's pause sentinel -- the owner's control and the
        # P0 provider_exhaustion repair both write it. Every other loop honours it
        # at its boundary (fantasy_daemon, branch_registrations); before the fleet
        # was deleted the repair also `docker stop`ped the worker, so this consumer
        # is now the only background executor and must halt on it too. A run
        # already in flight finishes; nothing new is pumped or claimed.
        #
        # Liveness is not activity: a paused universe STILL publishes its heartbeat.
        # deploy/daemon-watchdog.sh restarts the daemon on a stale beat, and a
        # restart preserves `.pause` -- skipping the beat here would turn the P0
        # repair into a restart loop (Codex round 3 on the fleet prune).
        serving_universes = list_serving_universes(self.base_path)
        # Every fire is the execution owner's (target design D7/D11). A process
        # that does not hold the owner lease still beats -- liveness is not
        # activity -- but starts nothing. Today's lease is the single-process
        # adapter (always held); S8a installs the generation-fenced one.
        from tinyassets.control_plane.lease import current_owner_lease

        lease_held = current_owner_lease().held()
        automation_submitted = 0
        if lease_held:
            automation_submitted, _automation_universes = self._submit_due_automations(
                serving_universes, prep_store
            )
            # The owner also settles ended activities and starts queued ones.
            # Dispatch runs on its own thread so heartbeats need not wait.
            try:
                from tinyassets.activity_dispatcher import tick_in_background

                tick_in_background(self.base_path)
            except Exception:  # noqa: BLE001 - activities never stop the pump
                logger.exception("activity dispatch tick failed")
        for universe_id in serving_universes:
            try:
                self._publish_heartbeat(universe_id)
                # Truthful per poll: `no_serving_runtime` drives the app's
                # connect-a-model heal, and an `ok:` row overwrites it the
                # moment the universe serves (status filters `ok:` out).
                self._record_reason(
                    prep_store, f"universe:{universe_id}:-", universe_id,
                    self._no_runtime_reason(universe_id) or "ok:serving",
                )
                if self._paused(universe_id):
                    self._record_reason(
                        prep_store, f"universe:{universe_id}:-", universe_id, "paused"
                    )
                    continue
            except Exception as exc:  # noqa: BLE001 - one universe cannot stop the fleet
                logger.exception(
                    "assigned queue live-worker preparation failed universe=%s",
                    universe_id,
                )
                self._record_reason(
                    prep_store, f"universe:{universe_id}:-", universe_id,
                    _error_reason("prepare_error", exc),
                )
        if lease_held:
            # Control-plane triggers (the proactive cadence) fire on the same
            # owner tick; they read platform state only, never a universe dir.
            try:
                self._control_plane.tick()
            except Exception:  # noqa: BLE001 - triggers never stop the pump
                logger.exception("control-plane trigger tick failed")
        return automation_submitted

    def _release_universe(self, universe_id: str) -> None:
        from tinyassets.automations import AutomationStore

        try:
            AutomationStore(self.base_path).release_universe_lease(
                universe_id, holder=self.consumer_id
            )
        except Exception:  # noqa: BLE001 - an unreleased lease expires on its own
            logger.exception("command center lease release failed universe=%s", universe_id)

    def _reap_finished(self) -> tuple[int, set[str]]:
        """Drop completed futures, then report free slots and busy universes.

        A universe with an unstopped automation run is busy until that run is
        terminal, then its lease -- held long on purpose -- is released.
        """
        with self._lock:
            finished = [uid for uid, future in self._active.items() if future.done()]
            for uid in finished:
                future = self._active.pop(uid)
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - already contained, retain diagnostics
                    logger.exception("assigned queue task future failed")
            active = set(self._active)
        # AFTER reaping, not before: a batch records its unstopped run before its
        # future completes, so a future seen done here has already published it.
        # Reading `_unstopped` first raced a batch finishing in between, which
        # returned its universe as free (Codex round 2, 2026-09-27).
        busy = active | self._reap_unstopped()
        # An unstopped run's worker is still running, so it still holds a slot:
        # counting only `_active` let timed-out agents pile up past the
        # concurrency limit (Codex refute 2026-09-28, P1).
        return self.max_concurrency - len(busy), busy

    def _reap_unstopped(self) -> set[str]:
        """Lease keys whose timed-out run is still going; release the rest.

        "Still going" is the worker's own future, not the run row: a status
        can be orphan-marked while the worker runs on, and a run id with no row
        would otherwise hold its universe forever. A running universe's lease
        is re-stamped here, since its batch's refresher has already stopped.
        """
        from datetime import datetime as _dt

        from tinyassets.automations import (
            AutomationStore,
            cancel_grace_seconds,
        )
        from tinyassets.runs import get_future

        with self._lock:
            pending = {uid: set(runs) for uid, runs in self._unstopped.items()}
        running: set[str] = set()
        for universe_id, run_ids in pending.items():
            live = False
            for run_id in run_ids:
                future = get_future(run_id)
                if future is not None and not future.done():
                    live = True
            if live:
                running.add(universe_id)
                try:
                    AutomationStore(self.base_path).refresh_universe_lease(
                        universe_id,
                        holder=self.consumer_id,
                        now=_dt.now(timezone.utc),
                        # The lease only has to outlive the gap to the NEXT sweep,
                        # which re-stamps it while the run is live. It used to be
                        # derived from the run timeout; there is no run timeout any
                        # more (a run finishes when it is finished), and a TTL is
                        # not the place to invent one. An operator-set timeout is
                        # still honoured when there is one.
                        ttl_seconds=_lease_ttl_seconds() + cancel_grace_seconds(),
                    )
                except Exception:  # noqa: BLE001 - the held lease still stands
                    logger.exception(
                        "unstopped lease refresh failed universe=%s", universe_id
                    )
                continue
            with self._lock:
                self._unstopped.pop(universe_id, None)
            self._release_universe(universe_id)
        return running

    def _submit_due_automations(
        self,
        serving_universes: list[str],
        refusal_store: Any,
    ) -> tuple[int, set[str]]:
        """Submit each free agent's due user-owned automation.

        The fence is per agent (``automation_lease_key``: one branch in one
        universe), so two agents of one universe run side by side. An agent
        that is already running gets its due rows' ``overlap`` policy instead.
        Slots are handed out one agent per universe per pass, so one owner's
        many agents cannot take every slot ahead of another owner's first.

        Returns how many runs were submitted and in which universes.

        Nothing here decides authority: `due_automations` reads owner-declared
        rows, and `run_due_automation` re-derives the owner's admin, home and
        current assignment on the executor thread (D1/D3). A universe whose scan
        raises gets a named refusal and the loop continues to the next owner.
        """
        from tinyassets.automations import (
            automation_lease_key,
            due_automations,
            lease_key_universe,
            owed_since,
        )

        # Scanned even with no free slot: a due row whose agent is running must
        # still get its policy -- a `skip` spent, a `cancel_previous` sent --
        # rather than wait for a slot and then run (Codex refute 2026-09-28, P2).
        capacity, busy = self._reap_finished()
        started: set[str] = set()
        now = datetime.now(timezone.utc)
        ready_by_universe: list[tuple[str, list[tuple[str, tuple[Any, str]]]]] = []
        for universe_id in serving_universes:
            # `.pause` halts new background work for a universe; an automation is
            # exactly that.
            if self._paused(universe_id):
                continue
            try:
                due = due_automations(
                    self.base_path,
                    universe_id=universe_id,
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001 - one owner cannot stop the pump
                logger.exception(
                    "automation due scan failed universe=%s", universe_id
                )
                self._record_reason(
                    refusal_store,
                    f"universe:{universe_id}:automations",
                    universe_id,
                    _error_reason("automation_scan_error", exc),
                )
                continue
            if not due:
                continue
            ready: list[tuple[str, tuple[Any, str]]] = []
            seen: set[str] = set()
            # Longest-owed first, so neither a waiting one-shot wake nor a
            # cadence is starved by the agent's other rows.
            for automation, due_at in sorted(due, key=lambda item: owed_since(*item)):
                key = automation_lease_key(automation)
                if key in busy or self._agent_running_elsewhere(key, now):
                    self._apply_overlap(key, automation, due_at, now, refusal_store)
                    continue
                # One row per agent per poll. A second row due for the same
                # agent is judged NEXT poll, while this one runs, so its own
                # policy applies instead of it silently queueing in a batch.
                if key in seen:
                    continue
                seen.add(key)
                ready.append((key, (automation, due_at)))
            if ready:
                ready_by_universe.append((universe_id, ready))

        # Fair share across polls, not only within one: the universe running
        # the fewest agents goes first, so a slot that frees under one owner's
        # long runs does not go straight back to that owner while another
        # waits (Codex refute 2026-09-28, P1). The rotation breaks ties.
        running: dict[str, int] = {}
        for key in busy:
            owner_universe = lease_key_universe(key)
            running[owner_universe] = running.get(owner_universe, 0) + 1
        self._fair_turn = (getattr(self, "_fair_turn", 0) + 1) % max(
            1, len(ready_by_universe)
        )
        order = {
            uid: (index - self._fair_turn) % max(1, len(ready_by_universe))
            for index, (uid, _ready) in enumerate(ready_by_universe)
        }
        ready_by_universe.sort(key=lambda item: (running.get(item[0], 0), order[item[0]]))
        submitted = 0
        depth = 0
        while submitted < capacity and any(
            depth < len(ready) for _uid, ready in ready_by_universe
        ):
            for universe_id, ready in ready_by_universe:
                if submitted >= capacity or depth >= len(ready):
                    continue
                key, row = ready[depth]
                # Checked BEFORE submitting: cancelling a future a free worker
                # has already started does nothing, and that second run would
                # re-take its own lease beside the first. Only this coordinator
                # thread submits, so nothing can claim the key in between.
                with self._lock:
                    if key in self._active:
                        continue
                future = self._executor.submit(
                    self._run_automations, universe_id, [row]
                )
                with self._lock:
                    self._active[key] = future
                started.add(universe_id)
                submitted += 1
            depth += 1
        return submitted, started

    def _agent_running_elsewhere(self, key: str, now: datetime) -> bool:
        """Whether a LIVE lease on this agent is held by another process."""
        from tinyassets.automations import AutomationStore
        from tinyassets.process_liveness import DEAD, owner_state

        try:
            holder = AutomationStore(self.base_path).universe_lease_holder(key, now=now)
        except Exception:  # noqa: BLE001 - unknown is not "running"; acquire decides
            logger.exception("agent lease read failed key=%s", key)
            return False
        return bool(holder) and holder != self.consumer_id and not (
            owner_state(self.base_path, holder) == DEAD
        )

    def _apply_overlap(
        self,
        key: str,
        automation: Any,
        due_at: str,
        now: datetime,
        refusal_store: Any,
    ) -> None:
        """A due row whose agent is running: queue, skip, or cancel the runner.

        A row whose instant is already claimed IS the running occurrence -- a
        cadence's `last_due_at` moves only when its run finishes, so it stays
        due while it runs. It has no policy to apply: `cancel_previous` would
        cancel itself (Codex refute 2026-09-28, P1).
        """
        from tinyassets.automations import (
            OVERLAP_CANCEL_PREVIOUS,
            OVERLAP_SKIP,
            REFUSAL_KEY_PREFIX,
            WAITING_FOR_PREVIOUS_RUN,
            AutomationStore,
            skip_overlapping,
        )

        policy = getattr(automation, "overlap", "")
        universe_id = automation.universe_id
        try:
            if AutomationStore(self.base_path).attempt_claimed(
                automation.automation_id, due_at
            ):
                return
        except Exception:  # noqa: BLE001 - unknown means do nothing this poll
            logger.exception("attempt read failed automation=%s",
                             automation.automation_id)
            return
        # A one-shot wake under skip is not dropped: it waits like queue.
        if policy == OVERLAP_SKIP and skip_overlapping(
            self.base_path, automation, due_at, now=now,
            consumer_id=self.consumer_id,
        ) != WAITING_FOR_PREVIOUS_RUN:
            return
        reason = WAITING_FOR_PREVIOUS_RUN
        if policy == OVERLAP_CANCEL_PREVIOUS:
            from tinyassets.runs import request_cancel

            try:
                run_id = AutomationStore(self.base_path).lease_run_id(key, now=now)
                if run_id:
                    # The lease key names this universe and this branch, and
                    # only a consumer writes its run id: the cancel can only
                    # reach this owner's own running agent.
                    request_cancel(self.base_path, run_id)
                    reason = f"cancelling_previous:{run_id}"
            except Exception as exc:  # noqa: BLE001 - it retries next poll
                logger.exception("cancel_previous failed key=%s", key)
                reason = _error_reason("cancel_previous_error", exc)
        self._record_reason(
            refusal_store,
            f"{REFUSAL_KEY_PREFIX}{automation.automation_id}",
            universe_id,
            reason,
        )

    def _run_automations(
        self,
        universe_id: str,
        due: list[tuple[Any, str]],
    ) -> None:
        """Run one agent's due automations sequentially on the executor thread.

        Fenced by a DATABASE lease on the agent's key, not by `self._active`.
        That map is process-local: a restarted daemon starts with an empty one
        and would launch an automation the previous process is still running
        (Codex ADAPT 2026-08-29 §8). The lease is shared state, so both
        processes see it, and it is refreshed while the run is in flight so a
        dead holder's lease expires instead of wedging the agent forever. The
        universe's legacy lease, if held, keeps every agent out.
        """
        from datetime import datetime as _dt

        from tinyassets.automations import (
            REFUSAL_KEY_PREFIX,
            AutomationStore,
            automation_lease_key,
            run_due_automation,
        )
        from tinyassets.storage.assigned_queue_refusals import (
            AssignedQueueRefusalStore,
        )

        if not due:
            return
        key = automation_lease_key(due[0][0])
        store = AutomationStore(self.base_path)
        refusal_store = AssignedQueueRefusalStore(self.base_path)
        ttl = _lease_ttl_seconds()
        if not store.acquire_universe_lease(
            key,
            holder=self.consumer_id,
            now=_dt.now(timezone.utc),
            ttl_seconds=ttl,
            excluded_by=universe_id,
        ):
            now = _dt.now(timezone.utc)
            legacy = store.universe_lease_holder(universe_id, now=now)
            reason = (
                f"universe_busy:{legacy}" if legacy
                else f"agent_busy:{store.universe_lease_holder(key, now=now) or 'unknown'}"
            )
            self._record_reason(
                refusal_store,
                f"universe:{universe_id}:automations",
                universe_id,
                reason,
            )
            # And on each row, where its owner reads the automation.
            for automation, _due_at in due:
                self._record_reason(
                    refusal_store,
                    f"{REFUSAL_KEY_PREFIX}{automation.automation_id}",
                    universe_id,
                    reason,
                )
            return
        stop_refresh = threading.Event()
        refresher = threading.Thread(
            target=self._refresh_lease,
            args=(store, key, ttl, stop_refresh),
            name=f"automation-lease-{key}",
            daemon=True,
        )
        refresher.start()
        unreleased = False
        batch_runs: set[str] = set()

        def _started(run_id: str) -> None:
            batch_runs.add(run_id)
            self._note_automation_run(run_id)
            if run_id:
                try:
                    store.set_lease_run(key, holder=self.consumer_id, run_id=run_id)
                except Exception:  # noqa: BLE001 - only cancel_previous reads it
                    logger.exception("lease run record failed key=%s", key)

        try:
            for automation, due_at in due:
                # Re-read `.pause` BETWEEN rows: an owner who pauses mid-batch
                # expects the batch to stop, not to finish the queue first.
                if self._paused(universe_id):
                    self._record_reason(
                        refusal_store,
                        f"universe:{universe_id}:automations",
                        universe_id,
                        "paused",
                    )
                    break
                try:
                    reason = run_due_automation(
                        self.base_path,
                        automation,
                        due_at,
                        consumer_id=self.consumer_id,
                        on_run_started=_started,
                    )
                except Exception:  # noqa: BLE001 - the next automation is owed a try
                    logger.exception(
                        "automation run raised universe=%s automation=%s",
                        universe_id,
                        getattr(automation, "automation_id", ""),
                    )
                    continue
                if reason == "run_timeout_unreleased":
                    # The run ignored cancellation and is STILL calling the
                    # provider. Handing the agent to another process now would
                    # double-spend the owner's subscription, so keep the lease
                    # and stop the batch. `_reap_unstopped` keeps the agent
                    # busy -- even for us -- until the run is terminal.
                    unreleased = True
                    with self._lock:
                        self._unstopped[key] = set(batch_runs)
                    break
        finally:
            stop_refresh.set()
            refresher.join(timeout=5.0)
            if unreleased:
                logger.warning(
                    "agent %s stays leased: a timed-out automation run has "
                    "not stopped",
                    key,
                )
            else:
                try:
                    store.release_universe_lease(key, holder=self.consumer_id)
                except Exception:  # noqa: BLE001 - the lease expires on its own
                    logger.exception("automation lease release failed key=%s", key)

    def _refresh_lease(
        self,
        store: Any,
        universe_id: str,
        ttl: float,
        stop_refresh: threading.Event,
    ) -> None:
        """Re-stamp the universe lease while its batch is in flight."""
        from datetime import datetime as _dt

        from tinyassets.automations import LEASE_REFRESH_SECONDS

        while not stop_refresh.wait(LEASE_REFRESH_SECONDS):
            try:
                store.refresh_universe_lease(
                    universe_id,
                    holder=self.consumer_id,
                    now=_dt.now(timezone.utc),
                    ttl_seconds=ttl,
                )
            except Exception:  # noqa: BLE001 - a missed beat re-stamps next round
                logger.exception(
                    "automation lease refresh failed universe=%s", universe_id
                )

    def _note_automation_run(self, run_id: str) -> None:
        """Remember an in-flight automation run so `stop()` can cancel it."""
        with self._lock:
            self._automation_runs.add(run_id)

    def _paused(self, universe_id: str) -> bool:
        return (self.base_path / universe_id / ".pause").exists()

    def _record_reason(
        self,
        refusal_store: Any,
        key: str,
        universe_id: str,
        reason: str,
    ) -> None:
        # Re-record an unchanged reason only every half freshness window: the
        # status read still always sees a fresh row, without one upsert per key
        # per poll (Codex ADAPT on #2543: write amplification).
        now = time.monotonic()
        previous = self._recorded.get(key)
        window = min(
            assigned_queue_refusal_freshness_seconds(), 5 * self.poll_seconds
        )
        if (
            previous is not None
            and previous[0] == reason
            and now - previous[1] < window / 2
        ):
            return
        try:
            refusal_store.record(
                branch_task_id=key,
                universe_id=universe_id,
                reason=reason,
                observed_at=datetime.now(timezone.utc).isoformat(),
                consumer_id=self.consumer_id,
            )
            self._recorded[key] = (reason, now)
        except Exception:  # noqa: BLE001 - the ledger must never take the loop down
            logger.exception("assigned queue refusal record failed key=%s", key)

    def _no_runtime_reason(self, universe_id: str) -> str:
        """`no_serving_runtime` when the universe is not serving, else ''.

        `no_serving_runtime` only when the universe genuinely is not serving,
        by the SAME predicate a founder turn's admission uses: a READY provider
        assignment and `resolve_serving_agent_binding` finding exactly one
        serving binding created by that assignment's owner (two serving
        bindings, or serving disabled, are refusals there too -- Codex on this
        change, rounds 1 and 2). When it holds, the universe IS serving: chat,
        runs, and its own automations fire on its provider. Reporting anything
        else as "no serving provider selected" sent the founder's universe
        looking for a selection surface that does not exist (app thread,
        2026-09-02) and made the app heal re-fire on every load.
        """
        try:
            from tinyassets.provider_assignment import load_provider_assignment
            from tinyassets.provider_serving_binding import resolve_serving_agent_binding

            assignment = load_provider_assignment(self.base_path, universe_id=universe_id)
            if assignment is None or assignment.state != "ready":
                return "no_serving_runtime"
            try:
                resolve_serving_agent_binding(
                    self.base_path,
                    universe_id=universe_id,
                    owner_user_id=str(assignment.owner_user_id or ""),
                )
            except PermissionError:
                return "no_serving_runtime"
        except Exception:  # noqa: BLE001 - loud in the log, safe in behaviour
            logger.exception(
                "could not establish whether %s is serving; reporting it as not",
                universe_id,
            )
            return "no_serving_runtime"
        # Serving. (This used to report `legacy_control_tasks_parked` for the
        # fleet-era cloud-automation controls; their pump is retired and the
        # controls are stopped with a recorded reason at consumer start.)
        return ""

    def _publish_heartbeat(self, universe_id: str) -> None:
        """Write this consumer's liveness beat into the universe directory.

        Unconditional: a daemon that is polling is alive whether or not it has
        anything to execute, and `deploy/daemon-watchdog.sh` asks only that
        (a stale beat older than 900s restarts the daemon). The fleet-era
        runtime row and queue descriptor it used to carry are gone with that
        fleet; the beat keeps its fields so older readers still parse it.
        """
        from tinyassets.storage.request_admissions import (
            OPERATOR_CAPABILITY,
            QUEUE_PROTOCOL_VERSION,
        )

        now = datetime.now(timezone.utc)
        build_sha = os.environ.get("TINYASSETS_BUILD_SHA", "").strip().lower()
        if not _is_hex_sha(build_sha):
            build_sha = _release_build_sha()
        beat = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": "polling",
            "iteration": 0,
            "supervisor_started_at": "",
            "last_spawn_at": "",
            "last_exit_rc": None,
            "total_spawns": 1,
            "total_crashes": 0,
            "consec_crashes": 0,
            "subprocess_pid": os.getpid(),
            "subprocess_alive": True,
            "planned_sleep_s": self.poll_seconds,
            "queue_protocol_version": QUEUE_PROTOCOL_VERSION,
            "capabilities": [OPERATOR_CAPABILITY],
            "worker_id": self.consumer_id,
            "runtime_instance_id": "",
            "boot_id": self.boot_id,
            "build_sha": build_sha,
            "config_hash": "sha256:"
            + hashlib.sha256(
                f"{self.max_concurrency}:{self.poll_seconds}".encode("utf-8")
            ).hexdigest(),
            "universe_id": universe_id,
            "expires_at": (
                now + timedelta(seconds=DESCRIPTOR_VALIDITY_SECONDS)
            ).isoformat(),
        }
        universe = self.base_path / universe_id
        universe.mkdir(parents=True, exist_ok=True)
        filename = supervisor_heartbeat_filename(self.consumer_id)
        target = universe / filename
        temporary = universe / f"{filename}.tmp"
        temporary.write_text(json.dumps(beat), encoding="utf-8")
        temporary.replace(target)

    def _retire_fleet_controls(self) -> None:
        """Stop every fleet-era cloud-automation control, with a recorded reason.

        Their pump is retired, so a control left `active` would promise work
        nothing will ever produce. Each one is set `stopped` and gets an owner-
        visible reason -- a recorded disposition, never a silent drop. Runs at
        start; idempotent (stopped controls are left alone). One universe's
        failure does not stop the others.
        """
        from tinyassets.cloud_automation_control import CloudAutomationDesiredState
        from tinyassets.storage import db_path
        from tinyassets.storage.assigned_queue_refusals import (
            AssignedQueueRefusalStore,
        )
        from tinyassets.storage.cloud_automation_control import (
            CloudAutomationControlStore,
        )

        if not db_path(self.base_path).is_file():
            return
        try:
            store = CloudAutomationControlStore(self.base_path)
            # Every control not yet stopped -- paused ones too, which a
            # desired-active listing misses -- and no page limit a pile of
            # stopped rows could hide later ones behind (Codex refute C1, P2).
            with store.connection() as conn:
                pending = conn.execute(
                    "SELECT universe_id, automation_id FROM cloud_automation_controls "
                    "WHERE desired_state != ? ORDER BY universe_id, automation_id",
                    (CloudAutomationDesiredState.STOPPED.value,),
                ).fetchall()
        except Exception:  # noqa: BLE001 - no table, no controls to retire
            return
        refusals = AssignedQueueRefusalStore(self.base_path)
        for universe_id, automation_id in pending:
            try:
                control = store.get_control(
                    universe_id=universe_id, automation_id=automation_id,
                )
                if control is None or (
                    control.desired_state is CloudAutomationDesiredState.STOPPED
                ):
                    continue
                store.set_desired_state(
                    expected=control,
                    desired_state=CloudAutomationDesiredState.STOPPED,
                )
                refusals.record(
                    branch_task_id=f"automation:{control.automation_id}",
                    universe_id=universe_id,
                    reason=RETIRED_FLEET_CONTROL_REASON,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    consumer_id=self.consumer_id,
                )
                logger.warning(
                    "retired fleet-era cloud automation %s in %s",
                    control.automation_id, universe_id,
                )
            except Exception:  # noqa: BLE001 - one control cannot stop the rest
                logger.exception(
                    "fleet control retirement failed universe=%s automation=%s",
                    universe_id, automation_id,
                )


__all__ = [
    "AssignedQueueConsumer",
    "assigned_queue_consumer_enabled",
    "assigned_queue_refusal_freshness_seconds",
]
