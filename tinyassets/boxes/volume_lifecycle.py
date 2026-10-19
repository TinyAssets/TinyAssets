"""C1/C2 orchestration over opaque images and broker-owned volume metadata."""

import logging

log = logging.getLogger(__name__)


class VolumeLifecycle:
    def __init__(self, volumes, admission, runtime):
        self.volumes, self.admission, self.runtime = volumes, admission, runtime

    def recover(self, owner):
        record = self.admission.volume(owner, "pending")
        uid = record["uid"]
        with self.volumes.operation(uid):
            record = self.admission.volume(owner)
            work = record["work"]
            if not work:
                self.prune(owner)
                return record
            if work["kind"] == "create":
                return record
            if work["kind"] == "evict":
                self.volumes.delete(uid)
                return self.admission.volume(owner, "evicted")
            if work["kind"] == "snapshot":
                self.runtime.shutdown(uid)
            else:
                self.runtime.stop(uid)
            self.volumes.detach(uid)
            if work["kind"] == "restore":
                self.volumes.restore(uid, work["snapshot_id"], work["sha256"])
                return self.admission.volume(owner, "restored", snapshot_id=work["snapshot_id"])
            self._snapshot(owner, uid)
            return self.admission.volume(owner)

    def prepare(self, owner):
        record = self.admission.volume(owner)
        uid = record["uid"]
        with self.volumes.operation(uid):
            self.recover(owner)
            binding = self.admission.volume(owner)
            if not binding["local"]:
                self.admission.volume(owner, "restore", snapshot_id=binding["snapshot_id"])
                self.recover(owner)
            record = self.admission.owner(owner, include_secret=True)
            binding = self.admission.volume(owner)
            if binding["work"] == {"kind": "create"}:
                try:
                    self.volumes.create(uid, record["quota"])
                except FileExistsError:
                    pass  # Exclusive image publication completed before the interrupted commit.
                self.admission.volume(owner, "created")
            self.admission.volume(owner, "wake")
            # A center retirement stops only the runtime. Release its retained
            # mount before starting the next sandbox; no content path is read.
            self.volumes.detach(uid)
            mount = self.volumes.attach(uid)
            # Re-run growfs even at the current image length: a previous grow
            # may have enlarged the image before its filesystem update failed.
            self.volumes.grow(uid, max(record["quota"], self.volumes.size(uid)))
            return record, mount.oci_mount()

    def sleep(self, owner, stop):
        record = self.admission.volume(owner)
        uid = record["uid"]
        with self.volumes.operation(uid):
            self.admission.volume(owner, "sleep")
            stop()
            self.volumes.detach(uid)
            try:
                self._snapshot(owner, uid)
            except Exception:
                # The image remains detached and the broker retains the work.
                # Retrying this operation never starts an owner agent.
                log.exception("owner snapshot remains pending uid=%s", uid)

    def _snapshot(self, owner, uid):
        work = self.admission.volume(owner, "pending")["work"]
        snapshot_id, sha256 = self.volumes.snapshot(uid, work["snapshot_id"])
        self.admission.volume(owner, "publish", snapshot_id=snapshot_id, sha256=sha256)
        self.prune(owner)

    def prune(self, owner):
        record = self.admission.volume(owner)
        with self.volumes.operation(record["uid"]):
            snapshots = self.admission.volume(owner, "snapshots")
            retained = {record["snapshot_id"]}
            for snapshot in snapshots:
                if len(retained) < 7:
                    retained.add(snapshot["snapshot_id"])
                if snapshot["snapshot_id"] in retained:
                    continue
                self.volumes.delete_snapshot(
                    record["uid"], snapshot["snapshot_id"],
                    lambda: self.admission.volume(
                        owner, "forget", snapshot_id=snapshot["snapshot_id"]),
                )

    def restore(self, owner, snapshot_id, stop):
        record = self.admission.volume(owner)
        with self.volumes.operation(record["uid"]):
            self.admission.volume(owner, "restore", snapshot_id=snapshot_id)
            stop()
            return self.recover(owner)

    def evict(self, owner):
        """C10: the same UID lock covers the decision, deletion and map commit."""
        record = self.admission.volume(owner)
        with self.volumes.operation(record["uid"]):
            if self.admission.volume(owner, "evict") is None:
                return False
            self.recover(owner)
            return True

    def delete(self, owner, stop):
        record = self.admission.volume(owner)
        with self.volumes.operation(record["uid"]):
            stop()
            self.admission.retire(owner, record["uid"])
            self.volumes.delete(record["uid"])
            return record["uid"]
