"""C13/C14: broker orchestration; the host executes opaque UID operations."""
from __future__ import annotations

import logging

from tinyassets.broker import owner_volumes
from tinyassets.broker.admission import owner_lock, read

log = logging.getLogger(__name__)


class VolumeLifecycle:
    def __init__(self, identities, execute=None, *, lifecycle):
        from tinyassets.boxes.daemon_control import request

        self.lifecycle = lifecycle
        self.identities = identities
        self.execute = execute or request

    def volume(self, owner, action="binding", **arguments):
        return owner_volumes.dispatch(self.identities, {
            "op": "BOX_VOLUME", "principal": owner, "action": action, "arguments": arguments})

    async def host(self, owner, operation, **arguments):
        uid = owner_volumes.binding(self.identities, owner)["uid"]
        return await self.lifecycle.host_call(self.execute, operation, uid=uid, **arguments)

    async def recover(self, owner):
        record = self.volume(owner, "pending")
        work = record["work"]
        if work and work["kind"] == "running":
            status = await self.host(owner, "status")
            self.lifecycle.facts[owner] = status
            if status["awake"]:
                self.lifecycle.awake.add(owner)
                idle = status["idle_seconds"]
                if idle is None or (idle < self.lifecycle.config.idle_limit
                                    and status["uptime"] < 86400):
                    return record
            record = await self.sleep(owner)
            work = record["work"]
        if not work or work["kind"] == "create":
            return record
        if work["kind"] == "evict":
            await self.host(owner, "evict")
            return self.volume(owner, "evicted")
        if work["kind"] == "snapshot":
            return await self.sleep(owner)
        if work["kind"] == "restore":
            if await self.host(owner, "sleep") != {"stopped": True}:
                raise RuntimeError("owner sleep remains pending")
            await self.lifecycle.stopped(owner)
            await self.host(owner, "restore", snapshot_id=work["snapshot_id"],
                            sha256=work["sha256"])
            return self.volume(owner, "restored", snapshot_id=work["snapshot_id"])
        return record

    async def prepare(self, owner, launch):
        status = await self.host(owner, "status")
        if status["awake"] and launch["quota"] > status["volume_size"]:
            await self.sleep(owner)
        record = self.volume(owner, "pending")
        if record["work"] and record["work"]["kind"] in {"restore", "evict"}:
            record = await self.recover(owner)
        if not status["awake"]:
            await self.lifecycle.stopped(owner)
        if not record["local"]:
            self.volume(owner, "restore", snapshot_id=record["snapshot_id"])
            record = await self.recover(owner)
        if record["work"] == {"kind": "create"}:
            await self.host(owner, "create", quota=launch["quota"])
            record = self.volume(owner, "created")
        # Only the broker commits lifecycle intent; the executor reports facts.
        self.volume(owner, "wake")
        return {**launch, **{key: record[key] for key in (
            "volume_generation", "snapshot_id", "snapshot_sha256")}}

    async def sleep(self, owner):
        record = self.volume(owner, "sleep")
        if await self.host(owner, "sleep") != {"stopped": True}:
            raise RuntimeError("owner sleep remains pending")
        await self.lifecycle.stopped(owner)
        return record

    async def restore(self, owner, snapshot_id):
        self.volume(owner, "restore", snapshot_id=snapshot_id)
        return await self.recover(owner)

    async def prune(self, owner):
        record = self.volume(owner)
        retained = {record["snapshot_id"]}
        for snapshot in self.volume(owner, "snapshots"):
            if len(retained) < 7:
                retained.add(snapshot["snapshot_id"])
            if snapshot["snapshot_id"] in retained:
                continue
            result = await self.host(owner, "delete_snapshots",
                               snapshot_ids=[snapshot["snapshot_id"]], released=[])
            if result["deleted"]:
                self.volume(owner, "forget", snapshot_id=snapshot["snapshot_id"])

    async def evict(self, owner):
        if self.volume(owner, "evict") is None:
            return False
        await self.recover(owner)
        return True

    async def tick_owner(self, owner):
        async with owner_lock(self.identities.path, owner):
            if read(self.identities, owner).state not in {"admitted", "suspended"}:
                return
            record = await self.recover(owner)
        work = record["work"]
        if work and work["kind"] == "snapshot":
            # C15: copying owns only boxhostd's UID execution lock. A wake may
            # replace this intent; only its matching completed sleep can publish.
            result = await self.host(owner, "snapshot", snapshot_id=work["snapshot_id"])
            async with owner_lock(self.identities.path, owner):
                if self.volume(owner, "pending")["work"] == work:
                    self.volume(owner, "publish", **result)
        async with owner_lock(self.identities.path, owner):
            await self.prune(owner)
            await self.evict(owner)
