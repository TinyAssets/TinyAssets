"""C5: broker publication, cold wake and credential retirement authority.

Only identity metadata is read here. The persistent secret never enters an
owner volume; boxhostd receives a bound launch record through its private RPC.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import closing
from pathlib import Path

from tinyassets.broker.admission import owner_call, owner_lock, read, thread_work
from tinyassets.broker.lifecycle_config import THIN_BYTES

log = logging.getLogger(__name__)

# A per-owner drain failure may name one of these. The message is never kept.
_DRAIN_STAGES = frozenset({"lock", "status", "sleep"})
_DRAIN_CLASSES = frozenset({
    "TimeoutError", "BlockingIOError", "InterruptedError", "OSError", "BoxError",
    "BoxBusy", "KeyError", "RuntimeError", "LookupError", "PermissionError",
    "ValueError", "TypeError", "AttributeError", "OperationalError",
    "IntegrityError", "DatabaseError", "ProgrammingError", "Error", "error",
})


class BrokerLifecycle:
    def __init__(self, server):
        self.server = server
        self.identities = server._owner_identities
        self.root = server._data_root
        from tinyassets.broker.lifecycle_config import LifecycleConfig
        from tinyassets.broker.owner_volumes import awake_owners
        from tinyassets.broker.volume_lifecycle import VolumeLifecycle

        self.config = LifecycleConfig()
        self.volumes = VolumeLifecycle(self.identities, lifecycle=self)
        # On restart, pending stops also reserve a slot until sleep acknowledges.
        self.awake = set(awake_owners(self.identities, stopping=True))
        self.facts = {}
        self.tasks = {}
        # Shared awake-slot reservation: scheduled waiters release this condition;
        # an interactive eviction and its replacement launch reserve one slot.
        self._capacity = asyncio.Condition()
        self._background_host_slots = asyncio.Semaphore(self.config.volume_concurrency)
        self._reservations = {}
        self._instance = None

    def _router(self, request):
        from tinyassets.router_credentials import routing_request

        reply = routing_request(self.root, request)
        if reply.get("op") != "ROUTING_SNAPSHOT_IS":
            raise RuntimeError("router snapshot installation pending")
        return reply["result"]

    def publish(self, *, only_restart=False):
        instance = self._router({"operation": "routing_instance"})["instance"]
        if only_restart and instance == self._instance:
            return
        records = {}
        with closing(self.identities._connect(write=True)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE routing_sequence SET revision=revision+1")
            revision = db.execute("SELECT revision FROM routing_sequence").fetchone()[0]
            from tinyassets.broker.admission import admission, digest

            run = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
            for owner, uid, secret in db.execute(
                    "SELECT i.principal,i.machine_id,s.secret FROM owner_identities i "
                    "LEFT JOIN owner_box_secrets s ON s.principal=i.principal"):
                state = (admission(db, owner).state if self.identities.work_ready
                         else "absent")
                if state not in {"admitted", "suspended"}:
                    continue
                if state == "admitted" and not secret:
                    raise RuntimeError("admitted owner is missing its ingress credential")
                records[digest(owner)] = {"state": state,
                                  "socket": str(run / str(uid) / "app.sock"),
                                  "secret": secret if state == "admitted" else None}
        installed = self._router({"operation": "routing_install", "instance": instance,
                                  "revision": revision, "records": records})
        if installed["revision"] < revision:
            raise RuntimeError("router did not acknowledge current snapshot")
        self._instance = instance

        return {"instance": instance, "revision": revision}

    def withdrawal_installed(self, receipt):
        if not receipt:
            return False
        current = self._router({"operation": "routing_instance"})
        return (current["instance"] == receipt["instance"]
                and current["revision"] >= receipt["revision"])

    async def changed(self, owner):
        from tinyassets.boxes.daemon_control import request

        state = read(self.identities, owner).state
        if state != "admitted":
            await self.server.withdraw_channels(owner)
        receipt = None
        try:
            receipt = await thread_work(self.publish)
        except (OSError, RuntimeError):
            self._instance = None
            if state == "admitted":
                raise
            log.warning("withdrawal publication pending; stopping old ingress")
        if state != "admitted":
            uid = self.identities.owner_machine(owner)
            if uid is None:
                raise LookupError("owner lifecycle binding missing")
            if await self.host_call(request, "stop", uid=uid) != {"stopped": True}:
                raise RuntimeError("admission withdrawal remains pending")
            await self.stopped(owner)
        return receipt

    async def _prepare(self, owner):
        from tinyassets.broker.box_admission import owner_record

        record = owner_record(self.identities, owner, for_wake=True)
        await thread_work(self.publish)
        return await self.volumes.prepare(owner, record)

    def _launch(self, owner, record, *, scheduled=False):
        from tinyassets.boxes.daemon_control import request

        store = getattr(self.server, "_policy_store", None)
        pending = store is not None and store.import_pending(owner)
        if pending:
            # N10: boxhostd lifts the box's old authority before the box starts.
            record = {**record, "import_authority": True}
        result = request("wake", owner, record=record, scheduled=scheduled)
        if {k: v for k, v in result.items() if k != "authority"} != {"uid": record["uid"]}:
            raise RuntimeError("broker-bound box did not become ready")
        if pending:
            self._settle_import(store, owner, result.get("authority"))
        return result["uid"]

    @staticmethod
    def _settle_import(store, owner, payload):
        """Import under this wake's owner lock, before any box operation can run."""
        from tinyassets.broker.policy import PolicyRefused

        if payload is None:
            return  # A box already running lifted nothing; the import stays pending.
        fields = ("source_digest", "entries", "consents")
        if (not isinstance(payload, dict) or set(payload) != {*fields, "errors"}
                or payload["errors"] != 0):
            errors = payload.get("errors") if isinstance(payload, dict) else "invalid"
            store.abandon_import(owner, reason=f"extraction errors: {errors}")
            return
        try:
            store.import_authority(owner, {key: payload[key] for key in fields})
        except PolicyRefused as exc:
            store.abandon_import(owner, reason=str(exc))

    async def wake(self, owner, *, scheduled=False):
        from tinyassets.boxes.daemon_control import REQUEST_DRIVEN

        token = REQUEST_DRIVEN.set(not scheduled)
        try:
            return await self._wake(owner, scheduled=scheduled)
        finally:
            REQUEST_DRIVEN.reset(token)

    async def _wake(self, owner, *, scheduled=False):
        from tinyassets.broker.owner_volumes import awake_owners

        async with owner_lock(self.identities.path, owner):
            record = await self._prepare(owner)
            victim = None
            deadline = asyncio.get_running_loop().time() + self.config.wake_timeout
            async with self._capacity:
                while owner not in self.awake:
                    if self._charged() + THIN_BYTES <= self.config.memory_budget:
                        self._reservations[owner] = None
                        break
                    if not scheduled:
                        candidates = [(self.facts[candidate]["idle_seconds"], candidate)
                            for candidate in awake_owners(self.identities)
                            if candidate in self.awake
                            and candidate not in self._reservations.values()
                            and self.facts.get(candidate, {}).get("active") == 0
                            and self.facts[candidate]["idle_seconds"] is not None]
                        if candidates:
                            victim = max(candidates)[1]
                            self._reservations[owner] = victim
                            break
                    try:
                        await asyncio.wait_for(self._capacity.wait(),
                            max(0, deadline - asyncio.get_running_loop().time()))
                    except TimeoutError:
                        from tinyassets.boxes.provider import BoxBusy

                        raise BoxBusy("owner wake capacity deadline exceeded") from None
            try:
                if victim is not None:
                    await owner_call(self.identities.path, victim, self.volumes.sleep, victim)
                # An uncertain launch still occupies its reserved slot until
                # recover observes it stopped or a stop/sleep acknowledges it.
                self.awake.add(owner)
                uid = await self.host_call(self._launch, owner, record, scheduled=scheduled)
                return uid
            finally:
                async with self._capacity:
                    self._reservations.pop(owner, None)
                    self._capacity.notify_all()

    def _charged(self):
        """Memory held by awake and reserved boxes, each at least the thin class."""
        return sum(max(THIN_BYTES, self.facts.get(owner, {}).get("memory_bytes") or 0)
                   for owner in self.awake | self._reservations.keys())

    async def observed(self, owner, status):
        """Record a pass's facts; lower memory or an idle box can admit a waiting wake."""
        async with self._capacity:
            self.facts[owner] = status
            self._capacity.notify_all()

    async def stopped(self, owner):
        # Only called after a completed sleep/stop or recover's stopped fact.
        async with self._capacity:
            self.awake.discard(owner)
            self.facts.pop(owner, None)
            self._capacity.notify_all()

    async def host_call(self, function, *args, **kwargs):
        """Queue background work on the event loop, preserving workers for wakes."""
        from tinyassets.boxes.daemon_control import REQUEST_DRIVEN

        if REQUEST_DRIVEN.get():
            return await thread_work(function, *args, **kwargs)
        # The host has one additional reserved slot for a requested wake. Do
        # not occupy the shared executor while waiting for background capacity.
        slots = getattr(self, "_background_host_slots", None)
        if slots is None:
            slots = self._background_host_slots = asyncio.Semaphore(
                self.config.volume_concurrency)
        async with slots:
            return await thread_work(function, *args, **kwargs)

    async def readiness(self):
        """Include durable work that the next waker tick has not dispatched.

        A platform backup pins snapshot rows that prune is not allowed to
        delete. Those rows stay above the seven-copy cap until backup
        retention drops the manifest, which this gate cannot age out. They
        are not unfinished deploy work. An unreadable pin catalog still
        counts, so a missing report cannot hide a snapshot prune can delete.
        """
        from tinyassets.broker.owner_volumes import actionable_excess, pending, pending_counts

        if not self.identities.work_ready:
            return {"ready": False, "blocked": "admission"}
        tasks = sum(not task.done() for task in self.tasks.values())
        owners = await asyncio.to_thread(
            pending, self.identities, include_running=False)
        if not tasks and not owners:
            return {"ready": True}
        counts = (await asyncio.to_thread(
            pending_counts, self.identities, include_running=False) if owners else {})
        if counts.get("excess_snapshots"):
            try:
                # lifecycle_control gives this readiness call 5 seconds total.
                report = await asyncio.wait_for(
                    self.host_call(self.volumes.execute, "pins"), timeout=3)
                actionable = await asyncio.to_thread(
                    actionable_excess, self.identities, report["pinned"])
            except Exception:
                log.exception(
                    "pinned snapshot report unavailable; excess rows still block readiness")
            else:
                if actionable:
                    counts["excess_snapshots"] = actionable
                else:
                    del counts["excess_snapshots"]
        if not tasks and not counts:
            return {"ready": True}
        return {"ready": False, "tasks": tasks, "pending": counts}

    def _drain_cause(self, stage, exc):
        """Fixed ``stage:Class`` token. The exception text is not part of it."""
        if stage not in _DRAIN_STAGES:
            stage = "sleep"
        kind = "error"
        for base in type(exc).__mro__:
            name = base.__name__
            if name in _DRAIN_CLASSES and name != "error":
                kind = name
                break
        return f"{stage}:{kind}"

    async def _failed_sleep_outcome(self, owner):
        """Whether a failed sleep left an intent readiness would wait on.

        ``sleep`` records ``kind=snapshot`` before the host stop. An awake box
        did not stop, so that intent is withdrawn back to ``running`` (which
        ``pending`` does not count) and the owner is deferred. A box that
        already stopped keeps the intent for the ordinary publish. Anything
        else, including a restore intent, still blocks.
        """
        try:
            async with owner_lock(self.identities.path, owner):
                work = self.volumes.volume(owner, "pending")["work"] or {}
                kind = work.get("kind")
                if kind != "snapshot":
                    return "block" if kind in {"restore", "evict", "erase"} else "defer"
                if work.get("restore_snapshot_id"):
                    return "block"
                status = await self.volumes.host(owner, "status")
                if not isinstance(status, dict) or "awake" not in status:
                    return "block"
                if not status["awake"]:
                    return "slept"
                self.volumes.volume(owner, "sleep_refused")
                after = self.volumes.volume(owner, "pending")["work"] or {}
                if after.get("kind") == "running":
                    return "defer"
                return "block"
        except Exception:
            log.warning("owner drain sleep:error")
            return "block"

    async def _drain_outcome(self, owner, stage):
        """Defer only when this owner leaves readiness nothing to wait on."""
        if stage == "sleep":
            return await self._failed_sleep_outcome(owner)
        try:
            async with owner_lock(self.identities.path, owner):
                work = self.volumes.volume(owner, "pending")["work"] or {}
                kind = work.get("kind")
                if kind in {"restore", "evict", "erase"} or work.get("restore_snapshot_id"):
                    return "block"
                if kind != "snapshot":
                    return "defer"
        except Exception:
            log.warning("owner drain sleep:error")
            return "block"
        # A prior sleep already recorded a plain snapshot. Withdraw it only
        # when the box is still awake, same as a sleep that just failed.
        return await self._failed_sleep_outcome(owner)

    async def deploy(self, image, canary=None):
        """C12: publish once, then use ordinary sleep; wake remains demand-driven."""
        from tinyassets.boxes.daemon_control import request

        await self.host_call(request, "select_image", image=image)
        with closing(self.identities._connect(write=False)) as db:
            owners = [row[0] for row in db.execute("SELECT principal FROM owner_volumes")]
        counts = {"slept": 0, "asleep": 0, "busy": 0, "unresponsive": 0, "incomplete": 0}
        causes = {}
        canary_incomplete = 0
        for owner in owners:
            stage = "lock"
            try:
                async with owner_lock(self.identities.path, owner):
                    stage = "status"
                    status = await self.volumes.host(owner, "status")
                    if not status["awake"]:
                        counts["asleep"] += 1
                        continue
                    # boxhostd reports None when the box does not answer; a host
                    # without the field reads as idle, as before.
                    active = status.get("active", 0)
                    if active is None:
                        # Awake but its own status does not answer: never drained
                        # on a guess (no sleep intent for readiness to wait on),
                        # and one owner's wedged box never blocks the fleet.
                        counts["unresponsive"] += 1
                        continue
                    if type(active) is int and active > 0:
                        # Confirmed mid-turn: never drained, and no sleep intent is
                        # left for readiness to wait on. It starts the selected
                        # image at its next ordinary sleep and wake.
                        counts["busy"] += 1
                        continue
                    stage = "sleep"
                    await self.volumes.sleep(owner)
                    counts["slept"] += 1
            except Exception as exc:
                # A lock or status error writes no sleep intent. A sleep error
                # may have, and an earlier sleep may have left one. Only an
                # owner with nothing for readiness to wait on is deferred; the
                # canary is never deferred.
                outcome = await self._drain_outcome(owner, stage)
                token = self._drain_cause(stage, exc)
                log.warning("owner drain %s", token)
                if outcome == "slept":
                    counts["slept"] += 1
                    continue
                causes[token] = causes.get(token, 0) + 1
                is_canary = isinstance(canary, str) and owner == canary
                if outcome == "defer" and not is_canary:
                    counts["unresponsive"] += 1
                else:
                    counts["incomplete"] += 1
                    if is_canary:
                        canary_incomplete = 1
        if causes:
            counts["incomplete_causes"] = causes
        if canary_incomplete:
            counts["canary_incomplete"] = 1
        return counts

    async def sleep(self, owner):
        return await owner_call(self.identities.path, owner, self.volumes.sleep, owner)

    async def checkpoint(self, owner):
        return await owner_call(self.identities.path, owner, self.volumes.checkpoint, owner)

    async def restore(self, owner, snapshot_id, expected_generation=None):
        return await owner_call(self.identities.path, owner, self.volumes.restore,
                                owner, snapshot_id, expected_generation=expected_generation)

    def start(self, owner, operation, *, recovery=False, serialized=True):
        """The existing tick starts at most one lifecycle task per owner."""
        if not self.identities.work_ready and not recovery:
            return
        task = self.tasks.get(owner)
        if task is not None and not task.done():
            return task
        self.tasks[owner] = asyncio.create_task(self._work(owner, operation, serialized))
        return self.tasks[owner]

    async def _work(self, owner, operation, serialized):
        try:
            if serialized:
                return await owner_call(self.identities.path, owner, operation, owner)
            return await operation(owner)
        except Exception:
            log.exception("owner lifecycle work remains pending owner=%s", owner)

    async def close(self):
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def tick(self):
        # C3/C14: reuse the existing tick for startup retries, before any
        # ordinary lifecycle work. No recovery execution RPC or extra clock.
        if not self.identities.work_ready:
            import json

            from tinyassets.restore_recovery import platform_recovery

            layout = json.loads((self.root / ".layout.json").read_text())
            if layout["state"] == "recovering":
                try:
                    report = json.loads((self.root / ".platform-recovery.json").read_text())
                except ValueError:
                    # C3: an interrupted receipt write cannot complete startup;
                    # retry the same idempotent phase on the existing tick.
                    report = {}
                expected = layout["recovery"]
                if any(report.get(key) != value for key, value in expected.items()):
                    await platform_recovery(self.server, backup_time=expected["backup_time"],
                                            now=expected["restore_time"])
                return
            if layout["state"] != "stable":
                raise RuntimeError("platform recovery admission is closed")
            self.identities.work_ready = True
            self._instance = None
        try:
            await asyncio.to_thread(self.publish, only_restart=True)
        except Exception:
            self._instance = None
            log.exception("routing snapshot publication failed")
        self.tasks = {owner: task for owner, task in self.tasks.items() if not task.done()}
        self.server.deletion_jobs.tick()
        from tinyassets.broker.owner_volumes import pending

        for owner in await asyncio.to_thread(pending, self.identities):
            self.start(owner, self.volumes.tick_owner, serialized=False)
