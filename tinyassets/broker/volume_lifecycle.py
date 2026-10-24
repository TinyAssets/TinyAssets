"""C13/C14: broker orchestration; the host executes opaque UID operations."""
from __future__ import annotations

import logging
from contextlib import closing

from tinyassets.broker import owner_volumes
from tinyassets.broker.admission import owner_lock, read

log = logging.getLogger(__name__)

_GIB = 1024 ** 3
# C10: 100 GiB local and the $5 Spaces allowance (250 GiB included storage).
# N6 counts a full 5 GiB image per retained copy plus 20 GiB local staging.
REMOTE_CAP = 250 * _GIB
LOCAL_CAP = 100 * _GIB
FULL_IMAGE = 5 * _GIB
LOCAL_STAGING = 20 * _GIB


class VolumeBudgetRefused(PermissionError):
    """An optional checkpoint or restore does not fit the fleet storage cap."""


class VolumeLifecycle:
    def __init__(self, identities, execute=None, *, lifecycle):
        from tinyassets.boxes.daemon_control import request

        self.lifecycle = lifecycle
        self.identities = identities
        self.execute = execute or request
        self.remote_cap = REMOTE_CAP
        self.local_cap = LOCAL_CAP

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
                if not status.get("draining") and (idle is None or (
                        idle < self.lifecycle.config.idle_limit and status["uptime"] < 86400)):
                    return record
            return await self.sleep(owner)
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
        record = self.volume(owner, "pending")
        if status["awake"] and (status.get("draining") or
                launch["quota"] > status["volume_size"] or
                (record["work"] and record["work"]["kind"] == "snapshot")):
            # A failed drain closed the box's work admission. Preserve its
            # pending stop until acknowledged; marking it running strands it.
            record = await self.sleep(owner)
        pending_work = record["work"] or {}
        if pending_work.get("kind") == "evict":
            record = await self.recover(owner)
        elif pending_work.get("kind") == "restore" or pending_work.get("restore_snapshot_id"):
            record = await self._finish_restore(owner)
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
        ready = {**launch, **{key: record[key] for key in (
            "volume_generation", "snapshot_id", "snapshot_sha256")}}
        if record.get("rewind_generation") == record["volume_generation"]:
            ready["rewind"] = True
        return ready

    async def sleep(self, owner):
        record = self.volume(owner, "sleep")
        if await self.host(owner, "sleep") != {"stopped": True}:
            raise RuntimeError("owner sleep remains pending")
        await self.lifecycle.stopped(owner)
        return record

    def _require_headroom(self, owner):
        """Refuse an optional checkpoint or restore before it replaces current state."""
        del owner
        with closing(self.identities._connect(write=False)) as db:
            snapshots = db.execute("SELECT COUNT(*) FROM owner_snapshots").fetchone()[0]
            owners = db.execute("SELECT COUNT(*) FROM owner_volumes").fetchone()[0]
        remote = (snapshots + 1) * FULL_IMAGE
        local = owners * FULL_IMAGE + LOCAL_STAGING
        if remote > self.remote_cap or local > self.local_cap:
            raise VolumeBudgetRefused("snapshot storage exceeds the fleet budget")

    async def checkpoint(self, owner):
        """Explicit owner restore point: the same sleep publication, no extra archive."""
        self._require_headroom(owner)
        record = await self.sleep(owner)
        work = record["work"]
        if work and work["kind"] == "snapshot" and not work.get("restore_snapshot_id"):
            # Caller holds the owner lifecycle lock (routing.checkpoint).
            await self._publish_snapshot(owner, work, locked=True)
        return self.volume(owner, "pending")

    async def _publish_snapshot(self, owner, work, *, locked=False):
        try:
            result = await self.host(owner, "snapshot", snapshot_id=work["snapshot_id"])
        except (RuntimeError, ValueError) as exc:
            message = str(exc)
            if "local image is absent" in message:
                current = self.volume(owner, "pending")["work"] or {}
                if not current.get("restore_snapshot_id"):
                    # Leaving this intent made deploy readiness wait forever:
                    # recover retries a copy that can never be taken.
                    if locked:
                        return self.volume(owner, "image_absent")
                    async with owner_lock(self.identities.path, owner):
                        return self.volume(owner, "image_absent")
                result = None
            elif ("volume operation requires a stopped box" in message
                    or "volume must be fully detached" in message):
                # C15: the copy does not stop or detach a live box, and a
                # concurrent wake may own it. Leave the recorded intent; the
                # next recover sleeps before retrying.
                return self.volume(owner, "pending")
            else:
                raise

        def commit():
            current = self.volume(owner, "pending")["work"]
            if current != work:
                return False
            if result is None:
                if not current.get("restore_snapshot_id"):
                    return False
                self.volume(owner, "continue_restore")
                return True
            self.volume(owner, "publish", **result)
            return True

        if locked:
            published = commit()
        else:
            async with owner_lock(self.identities.path, owner):
                published = commit()
        if result is not None and not published:
            await self.host(owner, "delete_snapshots",
                            snapshot_ids=[work["snapshot_id"]], released=[])
        return self.volume(owner, "pending")

    async def _finish_restore(self, owner):
        """Publish the current image, then install. A crash resumes the same order."""
        record = self.volume(owner, "pending")
        work = record["work"] or {}
        if work.get("kind") == "snapshot" and work.get("restore_snapshot_id"):
            record = await self.sleep(owner)
            work = record["work"]
            # Caller holds the owner lifecycle lock, except direct test calls.
            record = await self._publish_snapshot(owner, work, locked=True)
            work = record["work"] or {}
            if work.get("kind") != "restore":
                # Publication failed or a wake replaced the intent. Do not install.
                return record
        if work.get("kind") == "restore":
            return await self.recover(owner)
        return record

    async def restore(self, owner, snapshot_id, expected_generation=None):
        current = self.volume(owner, "pending")
        if (expected_generation is not None
                and current["volume_generation"] != expected_generation):
            raise PermissionError("volume generation changed")
        self._require_headroom(owner)
        arguments = {"snapshot_id": snapshot_id}
        if expected_generation is not None:
            arguments["expected_generation"] = expected_generation
        self.volume(owner, "restore", **arguments)
        return await self._finish_restore(owner)

    async def prune(self, owner):
        record = self.volume(owner)
        retained = {record["snapshot_id"]}
        for snapshot in self.volume(owner, "snapshots"):
            if len(retained) < 7:
                retained.add(snapshot["snapshot_id"])
        # The selected source is an operation reference, not an eighth permanent slot.
        work = record["work"] or {}
        for key in ("restore_snapshot_id", "pre_snapshot_id"):
            if work.get(key):
                retained.add(work[key])
        for snapshot in self.volume(owner, "snapshots"):
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
            await self._publish_snapshot(owner, work)
            record = self.volume(owner, "pending")
            if record["work"] and record["work"]["kind"] == "restore":
                async with owner_lock(self.identities.path, owner):
                    await self.recover(owner)
        async with owner_lock(self.identities.path, owner):
            await self.prune(owner)
            await self.evict(owner)
