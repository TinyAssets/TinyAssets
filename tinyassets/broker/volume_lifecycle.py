"""C13/C14: broker orchestration; the host executes opaque UID operations."""
from __future__ import annotations

import logging

from tinyassets.broker import owner_volumes
from tinyassets.broker.admission import read

log = logging.getLogger(__name__)


class VolumeLifecycle:
    def __init__(self, identities, execute=None):
        from tinyassets.boxes.daemon_control import request

        self.identities = identities
        self.execute = execute or request

    def volume(self, owner, action="binding", **arguments):
        return owner_volumes.dispatch(self.identities, {
            "op": "BOX_VOLUME", "principal": owner, "action": action, "arguments": arguments})

    def host(self, owner, operation, **arguments):
        uid = owner_volumes.binding(self.identities, owner)["uid"]
        return self.execute(operation, uid=uid, **arguments)

    def recover(self, owner):
        record = self.volume(owner, "pending")
        work = record["work"]
        if work and work["kind"] == "running":
            status = self.host(owner, "status")
            if status["awake"]:
                idle = status["idle_seconds"]
                if idle is None or (idle < status["idle_limit"] and status["uptime"] < 86400):
                    return record
            record = self.volume(owner, "sleep")
            work = record["work"]
        if not work or work["kind"] == "create":
            self.prune(owner)
            return record
        if work["kind"] == "evict":
            self.host(owner, "evict")
            return self.volume(owner, "evicted")
        if work["kind"] == "restore":
            self.host(owner, "restore", snapshot_id=work["snapshot_id"], sha256=work["sha256"])
            return self.volume(owner, "restored", snapshot_id=work["snapshot_id"])
        result = self.host(owner, "snapshot", snapshot_id=work["snapshot_id"])
        self.volume(owner, "publish", **result)
        self.prune(owner)
        return self.volume(owner)

    def prepare(self, owner, launch):
        record = self.recover(owner)
        if not record["local"]:
            self.volume(owner, "restore", snapshot_id=record["snapshot_id"])
            record = self.recover(owner)
        if record["work"] == {"kind": "create"}:
            self.host(owner, "create", quota=launch["quota"])
            record = self.volume(owner, "created")
        # Only the broker commits lifecycle intent; the executor reports facts.
        self.volume(owner, "wake")
        return {**launch, **{key: record[key] for key in (
            "volume_generation", "snapshot_id", "snapshot_sha256")}}

    def sleep(self, owner):
        self.volume(owner, "sleep")
        return self.recover(owner)

    def restore(self, owner, snapshot_id):
        self.volume(owner, "restore", snapshot_id=snapshot_id)
        return self.recover(owner)

    def prune(self, owner):
        record = self.volume(owner)
        retained = {record["snapshot_id"]}
        for snapshot in self.volume(owner, "snapshots"):
            if len(retained) < 7:
                retained.add(snapshot["snapshot_id"])
            if snapshot["snapshot_id"] in retained:
                continue
            result = self.host(owner, "delete_snapshots",
                               snapshot_ids=[snapshot["snapshot_id"]], released=[])
            if result["deleted"]:
                self.volume(owner, "forget", snapshot_id=snapshot["snapshot_id"])

    def evict(self, owner):
        if self.volume(owner, "evict") is None:
            return False
        self.recover(owner)
        return True

    def tick_owner(self, owner):
        # Called on a per-owner worker while the broker owns the async lock.
        if read(self.identities, owner).state not in {"admitted", "suspended"}:
            return
        self.recover(owner)
        self.evict(owner)
