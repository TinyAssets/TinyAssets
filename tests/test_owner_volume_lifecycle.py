"""C1/C2 lifecycle metadata and interruption proofs; real mounts have a separate oracle."""

import hashlib
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tinyassets.boxes.volume_lifecycle import VolumeLifecycle
from tinyassets.broker import box_admission, owner_volumes
from tinyassets.broker.owner_identities import OwnerIdentities


class Images:
    def __init__(self):
        self.images, self.sizes, self.remote = {}, {}, {}
        self.attached = set()
        self.locks = defaultdict(threading.RLock)
        self.events = []
        self.fail_upload = False

    @contextmanager
    def operation(self, uid):
        with self.locks[uid]:
            yield

    def create(self, uid, size):
        if uid in self.images:
            raise FileExistsError
        self.events.append(("create", uid))
        self.images[uid], self.sizes[uid] = b"initial", size

    def size(self, uid):
        return self.sizes[uid]

    def attach(self, uid):
        assert uid not in self.attached and uid in self.images
        self.attached.add(uid)
        self.events.append(("attach", uid))
        return SimpleNamespace(oci_mount=lambda: {"destination": "/home/owner", "uid": uid})

    def detach(self, uid):
        self.attached.discard(uid)
        self.events.append(("detach", uid))

    def grow(self, uid, size):
        assert uid in self.attached and size >= self.sizes[uid]
        self.sizes[uid] = size
        self.events.append(("grow", uid))

    def snapshot(self, uid, snapshot_id):
        assert uid not in self.attached
        self.events.append(("snapshot", uid))
        if self.fail_upload:
            raise OSError("upload failed")
        self.remote[uid, snapshot_id] = self.images[uid]
        return snapshot_id, hashlib.sha256(self.images[uid]).hexdigest()

    def restore(self, uid, snapshot_id, sha256):
        assert uid not in self.attached
        content = self.remote[uid, snapshot_id]
        assert hashlib.sha256(content).hexdigest() == sha256
        self.images[uid] = content
        self.events.append(("restore", uid))

    def delete(self, uid):
        self.detach(uid)
        self.images.pop(uid, None)
        self.events.append(("delete", uid))

    def delete_snapshot(self, uid, snapshot_id, forget):
        self.remote.pop((uid, snapshot_id), None)
        forget()
        return True


class Admission:
    def __init__(self, identities):
        self.identities = identities

    def volume(self, owner, action="binding", **arguments):
        return owner_volumes.dispatch(self.identities, {
            "op": "BOX_VOLUME", "principal": owner, "action": action,
            "arguments": arguments})

    def owner(self, owner, *, rotate=False):
        return box_admission.owner_record(self.identities, owner, rotate=rotate)

    def retire(self, owner, uid):
        assert self.identities.owner_machine(owner) == uid
        self.identities.retire_owner(owner)

    def pending_volumes(self):
        return owner_volumes.pending(self.identities)


@pytest.fixture
def storage(tmp_path):
    tmp_path.chmod(0o700)
    broker = tmp_path / ".broker"
    broker.mkdir(mode=0o700)
    identities = OwnerIdentities(broker / "identities.db", initialize=True)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    for owner in ("alice", "bob"):
        identities.resolve(owner, allocate=True)
        identities.admission("admit", owner, owner)
        box_admission.provision(identities, tmp_path, owner, owner)

    events = []
    runtime = SimpleNamespace(stop=lambda uid: events.append(("stop", uid)),
                              shutdown=lambda uid: events.append(("shutdown", uid)))
    images = Images()
    lifecycle = VolumeLifecycle(images, Admission(identities), runtime)
    return SimpleNamespace(lifecycle=lifecycle, volumes=images, admission=lifecycle.admission,
                           identities=identities, events=events, runtime=runtime)


def sleep(storage, owner="alice"):
    uid = storage.admission.volume(owner)["uid"]
    storage.lifecycle.sleep(owner, lambda: storage.runtime.shutdown(uid))
    return storage.admission.volume(owner)


def test_every_sleep_publishes_verified_reference_and_growth_never_shrinks(storage):
    record, mount = storage.lifecycle.prepare("alice")
    uid = record["uid"]
    assert mount["uid"] == uid
    first = sleep(storage)
    assert first["snapshot_id"] and first["snapshot_sha256"] and first["work"] is None
    storage.identities.plans.apply("alice", tier="paid", event_created=1)
    storage.lifecycle.prepare("alice")
    grown = storage.volumes.size(uid)
    assert ("grow", uid) in storage.volumes.events
    second = sleep(storage)
    assert second["snapshot_id"] != first["snapshot_id"]
    storage.identities.plans.apply("alice", tier="free", event_created=2)
    storage.lifecycle.prepare("alice")
    assert storage.volumes.size(uid) == grown


def test_failed_upload_retains_reference_and_retries_without_waking(storage):
    storage.lifecycle.prepare("alice")
    previous = sleep(storage)
    storage.lifecycle.prepare("alice")
    storage.volumes.fail_upload = True
    failed = sleep(storage)
    assert failed["snapshot_id"] == previous["snapshot_id"]
    pending_id = failed["work"]["snapshot_id"]
    assert "alice" in storage.admission.pending_volumes()
    storage.volumes.fail_upload = False
    storage.volumes.events.clear()
    storage.lifecycle.recover("alice")
    assert storage.admission.volume("alice")["snapshot_id"] == pending_id
    assert not any(event == "attach" for event, _ in storage.volumes.events)


def test_inflight_upload_cannot_publish_after_erasure(storage, monkeypatch):
    storage.lifecycle.prepare("alice")
    previous = sleep(storage)
    storage.lifecycle.prepare("alice")
    upload = storage.volumes.snapshot

    def erase_during_upload(uid, snapshot_id):
        result = upload(uid, snapshot_id)
        storage.identities.retire_owner("alice")
        return result

    monkeypatch.setattr(storage.volumes, "snapshot", erase_during_upload)
    result = sleep(storage)
    assert result["snapshot_id"] == previous["snapshot_id"]
    assert result["work"]["kind"] == "snapshot"
    assert "alice" not in storage.admission.pending_volumes()
    with pytest.raises(PermissionError, match="erased"):
        storage.lifecycle.recover("alice")


def test_interrupted_restore_commits_generation_before_next_mount(storage, monkeypatch):
    storage.lifecycle.prepare("alice")
    before = sleep(storage)
    uid = before["uid"]
    storage.volumes.images[uid] = b"newer"
    original = storage.admission.volume
    interrupted = False

    def interrupt(owner, action="binding", **arguments):
        nonlocal interrupted
        if action == "restored" and not interrupted:
            interrupted = True
            raise OSError("map commit interrupted")
        return original(owner, action, **arguments)

    monkeypatch.setattr(storage.admission, "volume", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        storage.lifecycle.restore("alice", before["snapshot_id"], lambda: None)
    assert storage.volumes.images[uid] == b"initial"
    assert original("alice")["volume_generation"] == before["volume_generation"]
    with pytest.raises(PermissionError, match="restore"):
        storage.admission.owner("alice")
    record, _ = storage.lifecycle.prepare("alice")
    assert record["volume_generation"] == before["volume_generation"] + 1
    assert original("alice")["work"] is None
    assert storage.volumes.events[-2:] == [("attach", uid), ("grow", uid)]


def test_restore_needs_only_published_reference_not_local_catalog_or_volume(storage):
    storage.lifecycle.prepare("alice")
    published = sleep(storage)
    uid = published["uid"]
    storage.volumes.images.pop(uid)
    with sqlite3.connect(storage.identities.path) as db:
        db.execute("DELETE FROM owner_snapshots")
    storage.volumes.events.clear()
    result = storage.lifecycle.restore("alice", published["snapshot_id"], lambda: None)
    assert result["volume_generation"] == published["volume_generation"] + 1
    assert storage.volumes.images[uid] == b"initial"
    assert all(owner == uid for _, owner in storage.volumes.events)
    assert storage.identities.admitted_owner("alice") == "alice"


def test_retention_keeps_seven_and_never_deletes_current_map_reference(storage):
    for _ in range(9):
        storage.lifecycle.prepare("alice")
        record = sleep(storage)
    retained = storage.admission.volume("alice", "snapshots")
    assert len(retained) == 7
    assert len(storage.volumes.remote) == 7
    assert (record["uid"], record["snapshot_id"]) in storage.volumes.remote
    with pytest.raises(PermissionError, match="map references"):
        storage.admission.volume("alice", "forget", snapshot_id=record["snapshot_id"])


def test_restore_old_snapshot_repoints_map_and_retention_preserves_it(storage, monkeypatch):
    storage.lifecycle.prepare("alice")
    first = sleep(storage)
    prune = storage.lifecycle.prune
    monkeypatch.setattr(storage.lifecycle, "prune", lambda owner: None)
    for _ in range(8):
        storage.lifecycle.prepare("alice")
        sleep(storage)
    restored = storage.lifecycle.restore("alice", first["snapshot_id"], lambda: None)
    assert restored["snapshot_id"] == first["snapshot_id"]
    assert restored["snapshot_sha256"] == first["snapshot_sha256"]
    prune("alice")
    assert len(storage.admission.volume("alice", "snapshots")) == 7
    assert len(storage.volumes.remote) == 7
    assert (first["uid"], first["snapshot_id"]) in storage.volumes.remote


def test_permanent_binding_and_erasure_survive_volume_deletion(storage):
    record, _ = storage.lifecycle.prepare("alice")
    uid = record["uid"]
    assert storage.lifecycle.delete("alice", lambda: storage.runtime.stop(uid)) == uid
    assert uid not in storage.volumes.images
    assert storage.admission.volume("alice")["uid"] == uid
    with sqlite3.connect(storage.identities.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="permanent"):
            db.execute("UPDATE owner_volumes SET uid=399999 WHERE principal='alice'")
        with pytest.raises(sqlite3.IntegrityError, match="permanent"):
            db.execute("DELETE FROM owner_volumes WHERE principal='alice'")
    with pytest.raises(LookupError):
        storage.admission.owner("alice")


def test_center_retirement_preserves_account_volume_recovery(storage):
    storage.lifecycle.prepare("alice")
    snapshot = sleep(storage)
    storage.identities.admission("retire", "alice", "alice")
    restored = storage.lifecycle.restore("alice", snapshot["snapshot_id"], lambda: None)
    assert restored["uid"] == snapshot["uid"]
    assert restored["volume_generation"] == snapshot["volume_generation"] + 1
    assert restored["uid"] in storage.volumes.images


def test_missing_admission_cannot_reprovision_an_existing_volume_binding(storage):
    with sqlite3.connect(storage.identities.path) as db:
        db.execute("DELETE FROM box_records WHERE principal='alice'")
    with pytest.raises(sqlite3.IntegrityError):
        box_admission.provision(storage.identities, storage.identities.path.parent.parent,
                                "alice", "alice")
    from tinyassets.broker.admission import local_mutate

    local_mutate(storage.identities.path, "alice", "erase")
    with pytest.raises(PermissionError, match="erased"):
        storage.lifecycle.recover("alice")


def test_owner_generation_refuses_pre_restore_operation_ids(tmp_path):
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxOperationRefused

    root = tmp_path / "owner"
    root.mkdir()
    first = OwnerBoxd(owner_id="alice", owner_root=root, state_dir=root / ".owner-boxd",
                      volume_generation=1)
    old_id = first.operation_prefix() + "operation"
    first.close()
    restored = OwnerBoxd(owner_id="alice", owner_root=root, state_dir=root / ".owner-boxd",
                         volume_generation=2)
    try:
        handle = restored.bind("alice")
        with pytest.raises(BoxOperationRefused, match="volume generation"):
            restored.write(handle, old_id, "/cc/stale", b"old", max_bytes=10)
        assert not (root / "stale").exists()
        restored.write(handle, restored.operation_prefix() + "operation", "/cc/current",
                       b"new", max_bytes=10)
        assert (root / "current").read_bytes() == b"new"
    finally:
        restored.close()


def test_database_validation_is_owner_local_and_retried_after_failure(tmp_path):
    from tinyassets.boxes.state import BoxHostState
    from tinyassets.owner_runtime import validate_databases

    root = tmp_path / "owner"
    root.mkdir()
    state = BoxHostState(root / ".owner-boxd" / "boxhost.db")
    bad = root / "history.db"
    bad.write_bytes(b"broken database")
    try:
        with pytest.raises(sqlite3.DatabaseError):
            state.verify_volume(2, lambda: validate_databases(root))
        bad.unlink()
        with sqlite3.connect(bad) as db:
            db.execute("CREATE TABLE history (value TEXT)")
        state.verify_volume(2, lambda: validate_databases(root))
        state.verify_volume(2, lambda: pytest.fail("already verified generation was rescanned"))
        bad.write_bytes(b"broken again")
        with pytest.raises(sqlite3.DatabaseError):
            state.verify_volume(3, lambda: validate_databases(root))
    finally:
        state.close()


@pytest.mark.parametrize("arguments", [{"uid": 300002}, {"path": "/other"},
                                       {"volume_generation": 8}])
def test_volume_protocol_refuses_caller_selected_binding(storage, arguments):
    with pytest.raises(ValueError):
        storage.admission.volume("alice", **arguments)


def age_sleep(storage, seconds):
    with sqlite3.connect(storage.identities.path) as db:
        db.execute("UPDATE owner_volumes SET asleep_at=? WHERE principal='alice'",
                   (time.time() - seconds,))


def test_cold_eviction_restores_exact_bytes_before_attach(storage):
    record, _ = storage.lifecycle.prepare("alice")
    uid = record["uid"]
    storage.volumes.images[uid] = b"owner bytes\x00\xff"
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER - 1)
    assert not storage.lifecycle.evict("alice")
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    assert "alice" in storage.admission.pending_volumes()
    assert storage.lifecycle.evict("alice")
    assert not storage.admission.volume("alice")["local"]
    assert uid not in storage.volumes.images
    storage.volumes.events.clear()
    record, _ = storage.lifecycle.prepare("alice")
    assert storage.volumes.images[uid] == b"owner bytes\x00\xff"
    assert record["volume_generation"] == before["volume_generation"] + 1
    assert storage.admission.volume("alice")["local"]
    assert storage.admission.volume("alice")["asleep_at"] is None
    events = storage.volumes.events
    assert events.index(("restore", uid)) < events.index(("attach", uid))
    assert ("create", uid) not in storage.volumes.events


@pytest.mark.parametrize("state", ["pending", "restore", "erased", "unverified", "awake"])
def test_eviction_requires_completed_sleep_and_published_snapshot(storage, state):
    storage.lifecycle.prepare("alice")
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    if state == "pending":
        storage.admission.volume("alice", "sleep")
    elif state == "restore":
        storage.admission.volume("alice", "restore", snapshot_id=before["snapshot_id"])
    elif state == "erased":
        storage.identities.retire_owner("alice")
    elif state == "unverified":
        with sqlite3.connect(storage.identities.path) as db:
            db.execute("UPDATE owner_volumes SET snapshot_id=NULL,snapshot_sha256=NULL")
    else:
        storage.lifecycle.prepare("alice")
    if state in ("erased", "unverified"):
        with pytest.raises((PermissionError, ValueError)):
            storage.lifecycle.evict("alice")
    else:
        assert not storage.lifecycle.evict("alice")
    assert before["uid"] in storage.volumes.images
    assert storage.admission.volume("alice")["local"]


def test_cold_restore_failure_never_creates_empty_volume(storage):
    storage.lifecycle.prepare("alice")
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    storage.lifecycle.evict("alice")
    storage.volumes.remote.clear()
    storage.volumes.events.clear()
    with pytest.raises(KeyError):
        storage.lifecycle.prepare("alice")
    assert before["uid"] not in storage.volumes.images
    assert not storage.volumes.attached
    assert not any(event == "create" for event, _ in storage.volumes.events)
    assert storage.admission.volume("alice")["work"]["kind"] == "restore"


def test_interrupted_eviction_resumes_before_cold_wake(storage, monkeypatch):
    storage.lifecycle.prepare("alice")
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    dispatch = storage.admission.volume

    def interrupted(owner, action="binding", **arguments):
        if action == "evicted":
            raise OSError("commit interrupted")
        return dispatch(owner, action, **arguments)

    monkeypatch.setattr(storage.admission, "volume", interrupted)
    with pytest.raises(OSError):
        storage.lifecycle.evict("alice")
    assert before["uid"] not in storage.volumes.images
    monkeypatch.setattr(storage.admission, "volume", dispatch)
    storage.lifecycle.prepare("alice")
    assert storage.volumes.images[before["uid"]] == b"initial"


@pytest.mark.parametrize("first", ["wake", "evict"])
def test_concurrent_wake_and_eviction_share_owner_serialization(storage, monkeypatch, first):
    from concurrent.futures import ThreadPoolExecutor

    storage.lifecycle.prepare("alice")
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    action = "wake" if first == "wake" else "evict"
    dispatch = storage.admission.volume

    def paused(owner, operation="binding", **arguments):
        if operation == action and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return dispatch(owner, operation, **arguments)

    monkeypatch.setattr(storage.admission, "volume", paused)
    methods = {"wake": storage.lifecycle.prepare, "evict": storage.lifecycle.evict}
    def second():
        second_started.set()
        return methods["evict" if first == "wake" else "wake"]("alice")
    with ThreadPoolExecutor(2) as pool:
        one = pool.submit(methods[first], "alice")
        assert entered.wait(10)
        two = pool.submit(second)
        assert second_started.wait(10)
        assert not two.done()
        release.set()
        one.result(timeout=10)
        two.result(timeout=10)
    assert storage.volumes.images[before["uid"]] == b"initial"
    assert storage.admission.volume("alice")["local"]
    assert before["uid"] in storage.volumes.attached


def test_owner_restore_keeps_revoked_key_and_refuses_write_and_bash_replay(storage, tmp_path):
    import shutil

    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxOperationRefused

    storage.lifecycle.prepare("alice")
    published = sleep(storage)
    from tinyassets.erasure_ledger import ErasureLedger
    ErasureLedger(tmp_path).initialize()
    keys = ApiKeyStore(tmp_path)
    key = keys.mint("alice", name="snapshot key", scopes=[], authority=None)
    assert keys.resolve(key["key"]) is not None
    owner = tmp_path / "owner-mount"
    owner.mkdir()
    (owner / "note").write_bytes(b"snapshot content")
    snapshot = tmp_path / "snapshot-content"
    shutil.copytree(owner, snapshot)
    core = OwnerBoxd(owner_id="alice", owner_root=owner, state_dir=owner / ".owner-boxd",
                     volume_generation=published["volume_generation"])
    old = core.bind("alice")
    write_id, bash_id = core.operation_prefix() + "write", core.operation_prefix() + "bash"
    core.write(old, write_id, "/cc/note", b"after snapshot", max_bytes=100)
    core.consume_operation(old, bash_id, "bash", {"command": "echo once"})
    core.close()
    keys.revoke("alice", key["key_id"], authority=None)
    assert keys.resolve(key["key"]) is None
    other = storage.admission.volume("bob")
    restored = storage.lifecycle.restore("alice", published["snapshot_id"], lambda: None)
    shutil.rmtree(owner)
    shutil.copytree(snapshot, owner)
    core = OwnerBoxd(owner_id="alice", owner_root=owner, state_dir=owner / ".owner-boxd",
                     volume_generation=restored["volume_generation"])
    try:
        with pytest.raises(BoxOperationRefused):
            core.write(old, write_id, "/cc/note", b"replay", max_bytes=100)
        handle = core.bind("alice")
        with pytest.raises(BoxOperationRefused, match="volume generation"):
            core.write(handle, write_id, "/cc/note", b"replay", max_bytes=100)
        with pytest.raises(BoxOperationRefused, match="volume generation"):
            core.consume_operation(handle, bash_id, "bash", {"command": "echo once"})
        assert (owner / "note").read_bytes() == b"snapshot content"
        core.write(handle, core.operation_prefix() + "fresh", "/cc/note", b"fresh", max_bytes=100)
        assert (owner / "note").read_bytes() == b"fresh"
    finally:
        core.close()
    assert keys.resolve(key["key"]) is None
    assert storage.admission.volume("bob") == other
    assert restored["volume_generation"] == published["volume_generation"] + 1
