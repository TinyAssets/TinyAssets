"""C5: broker publication, cold wake and credential retirement authority.

Only identity metadata is read here. The persistent secret never enters an
owner volume; boxhostd receives a bound launch record through its private RPC.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import closing
from pathlib import Path

from tinyassets.broker.admission import owner_call, owner_lock, read, thread_work

log = logging.getLogger(__name__)


class RoutingAuthority:
    def __init__(self, server):
        self.server = server
        self.identities = server._owner_identities
        self.root = server._data_root
        from tinyassets.broker.volume_lifecycle import VolumeLifecycle

        self.volumes = VolumeLifecycle(self.identities)
        # Orders revisions and delivery of the shared routing snapshot.
        self._publication = threading.Lock()
        self.tasks = {}
        # Shared awake-slot reservation: scheduled waiters release this condition;
        # an interactive eviction and its replacement launch reserve one slot.
        self._capacity = asyncio.Condition()
        self._reservations = {}
        self._instance = None

    def _router(self, request):
        from tinyassets.router_credentials import routing_request

        reply = routing_request(self.root, request)
        if reply.get("op") != "ROUTING_SNAPSHOT_IS":
            raise RuntimeError("router snapshot installation pending")
        return reply["result"]

    def publish(self, *, only_restart=False):
        # One sequencer orders full snapshots, including after broker restart.
        with self._publication:
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
            if installed != {"revision": revision}:
                raise RuntimeError("router did not acknowledge current snapshot")
            self._instance = instance

    def changed(self, owner):
        from tinyassets.boxes.daemon_control import request

        state = read(self.identities, owner).state
        if state != "admitted":
            asyncio.run_coroutine_threadsafe(
                self.server.withdraw_channels(owner), self.server._loop).result(30)
        try:
            self.publish()
        except (OSError, RuntimeError):
            self._instance = None
            if state == "admitted":
                raise
            log.warning("withdrawal publication pending; stopping old ingress")
        if state != "admitted":
            # C5 permits ack after all projections install OR old ingress is
            # unreachable. Stopping it also retires in-flight route copies.
            uid = self.identities.owner_machine(owner)
            if uid is None:
                raise LookupError("owner lifecycle binding missing")
            if request("stop", uid=uid) != {"stopped": True}:
                raise RuntimeError("admission withdrawal remains pending")

    def _prepare(self, owner):
        from tinyassets.broker.box_admission import owner_record

        record = owner_record(self.identities, owner, for_wake=True)
        self.publish()
        return self.volumes.prepare(owner, record)

    def _launch(self, owner, record, *, scheduled=False, image=None):
        from tinyassets.boxes.daemon_control import request

        fields = {"image": image} if image is not None else {}
        result = request("wake", owner, record=record, scheduled=scheduled, **fields)
        if result != {"uid": record["uid"]}:
            raise RuntimeError("broker-bound box did not become ready")
        return result["uid"]

    def _wake(self, owner, *, scheduled=False, image=None):
        return self._launch(owner, self._prepare(owner), scheduled=scheduled, image=image)

    def _roll(self, image, uid=None):
        from tinyassets.boxes.daemon_control import request

        if uid is None:
            return request("roll", image=image)
        with closing(self.identities._connect(write=False)) as db:
            row = db.execute("SELECT principal FROM owner_identities WHERE machine_id=?",
                             (uid,)).fetchone()
        if row is None:
            raise LookupError("roll requires a permanent owner binding")
        owner = row[0]
        read(self.identities, owner).require_work()
        status = self.volumes.host(owner, "status")
        result = {"uid": self.identities.owner_machine(owner)}
        if not status["awake"]:
            return {**result, "outcome": "asleep"}
        if status["image"] == image and not status["error"]:
            return {**result, "outcome": "rolled"}
        if not self.volumes.host(owner, "drain")["drained"]:
            return {**result, "outcome": "deferred_busy"}
        self.volumes.volume(owner, "sleep")
        self.volumes.recover(owner)
        try:
            self._wake(owner, image=image)
            return {**result, "outcome": "rolled"}
        except Exception:
            if not self.volumes.host(owner, "drain")["drained"]:
                raise RuntimeError("candidate cannot safely drain") from None
            self.volumes.volume(owner, "sleep")
            self.volumes.recover(owner)
            self._wake(owner, image=status["image"])
            return {**result, "outcome": "rolled_back", "reason": "health_failed"}

    async def wake(self, owner, *, scheduled=False, image=None):
        from tinyassets.broker.owner_volumes import pending

        async with owner_lock(self.identities.path, owner):
            record = await thread_work(self._prepare, owner)
            victim = None
            deadline = None
            async with self._capacity:
                while True:
                    status = await asyncio.to_thread(self.volumes.host, owner, "status")
                    if deadline is None:
                        deadline = asyncio.get_running_loop().time() + status["wake_timeout"]
                    if status["awake"]:
                        break
                    # Reservations are in-flight slot use, never lifecycle intent.
                    # The map's running/work record remains the only durable intent.
                    reserved = len(self._reservations)
                    for replacing in self._reservations.values():
                        if replacing is not None:
                            facts = await asyncio.to_thread(self.volumes.host, replacing, "status")
                            reserved -= int(facts["awake"])
                    if status["awake_count"] + reserved < status["awake_cap"]:
                        self._reservations[owner] = None
                        break
                    if not scheduled:
                        candidates = []
                        for candidate in await asyncio.to_thread(pending, self.identities):
                            facts = await asyncio.to_thread(self.volumes.host, candidate, "status")
                            if (facts["awake"] and facts["active"] == 0
                                    and facts["idle_seconds"] is not None
                                    and candidate not in self._reservations.values()):
                                candidates.append((facts["idle_seconds"], candidate))
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
                return await thread_work(self._launch, owner, record,
                                         scheduled=scheduled, image=image)
            finally:
                async with self._capacity:
                    self._reservations.pop(owner, None)
                    self._capacity.notify_all()

    async def _capacity_changed(self):
        async with self._capacity:
            self._capacity.notify_all()

    async def roll(self, image, uid=None):
        if uid is None:
            return await asyncio.to_thread(self._roll, image)
        with closing(self.identities._connect(write=False)) as db:
            row = db.execute("SELECT principal FROM owner_identities WHERE machine_id=?",
                             (uid,)).fetchone()
        if row is None:
            raise LookupError("roll requires a permanent owner binding")
        return await owner_call(self.identities.path, row[0], self._roll, image, uid)

    async def sleep(self, owner):
        try:
            return await owner_call(self.identities.path, owner, self.volumes.sleep, owner)
        finally:
            await self._capacity_changed()

    async def restore(self, owner, snapshot_id):
        return await owner_call(self.identities.path, owner, self.volumes.restore,
                                owner, snapshot_id)

    def start(self, owner, operation):
        """The existing tick starts at most one lifecycle task per owner."""
        if not self.identities.work_ready:
            return
        task = self.tasks.get(owner)
        if task is not None and not task.done():
            return
        self.tasks[owner] = asyncio.create_task(self._work(owner, operation))

    async def _work(self, owner, operation):
        try:
            await owner_call(self.identities.path, owner, operation, owner)
        except Exception:
            log.exception("owner lifecycle work remains pending owner=%s", owner)
        finally:
            await self._capacity_changed()

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
        self.tasks = {owner: task for owner, task in self.tasks.items() if not task.done()}
        self.server.deletion_jobs.tick()
        from tinyassets.broker.owner_volumes import pending

        for owner in await asyncio.to_thread(pending, self.identities):
            self.start(owner, self.volumes.tick_owner)
