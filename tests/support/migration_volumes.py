"""Volume primitive double: real migration files, distinct mounts and opaque store.

Real XFS attach/detach and remote readback are exercised by the root rehearsal.
The double leaves mount contents accessible to test assertions after detach.
"""

import hashlib
from contextlib import nullcontext
from types import SimpleNamespace


def contents(store):
    return store.parent / (store.name + ".cutover-mounts")


class MigrationVolumes:
    def __init__(self, store):
        self.store, self.mount_root = store, contents(store)
        self.mount_root.mkdir(exist_ok=True)
        self.attached = set()
        self.events = []

    def operation(self, uid):
        return nullcontext()

    def create(self, uid, size):
        with (self.store / f"{uid}.img").open("xb") as stream:
            stream.write(str(size).encode())
        (self.mount_root / str(uid)).mkdir(mode=0o700, exist_ok=True)
        self.events.append(("create", uid))

    def size(self, uid):
        return int((self.store / f"{uid}.img").read_text())

    def attach(self, uid):
        assert uid not in self.attached
        self.attached.add(uid)
        self.events.append(("attach", uid))
        return SimpleNamespace(mountpoint=self.mount_root / str(uid))

    def detach(self, uid):
        self.attached.discard(uid)
        self.events.append(("detach", uid))

    def snapshot(self, uid, snapshot_id):
        assert uid not in self.attached
        from tinyassets.box_layout_migration import _durable_tree

        digest = hashlib.sha256(repr(_durable_tree(self.mount_root / str(uid))).encode())
        self.events.append(("snapshot", uid))
        return snapshot_id, digest.hexdigest()


def install(monkeypatch):
    from tinyassets import box_layout_migration as migration

    instances = {}

    def volumes(store):
        if store not in instances:
            instances[store] = MigrationVolumes(store)
        return instances[store]

    monkeypatch.setattr(migration, "_volumes", volumes)
    return instances
