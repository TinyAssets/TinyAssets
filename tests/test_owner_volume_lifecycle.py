"""C1/C2 lifecycle metadata and interruption proofs; real mounts have a separate oracle."""

import hashlib
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tests.support.admission import drive, projection
from tinyassets.broker import box_admission, owner_volumes
from tinyassets.broker.admission import local_mutate
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.routing import BrokerLifecycle


class Images:
    def __init__(self):
        self.images, self.sizes, self.remote = {}, {}, {}
        self.attached = set()
        self.locks = defaultdict(threading.RLock)
        self.events = []
        self.fail_upload = False

    @contextmanager
    def operation(self, uid, *, blocking=True):
        if not self.locks[uid].acquire(blocking=blocking):
            raise BlockingIOError("volume operation in progress")
        try:
            yield
        finally:
            self.locks[uid].release()

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
        if uid not in self.images:
            raise FileNotFoundError("local image is absent")
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

    def delete_snapshots(self, uid, snapshot_ids, released):
        for key in list(self.remote):
            if key[0] == uid and (snapshot_ids is None or key[1] in snapshot_ids):
                self.remote.pop(key)
        return True


class Admission:
    def __init__(self, identities):
        self.identities = identities

    def volume(self, owner, action="binding", **arguments):
        return owner_volumes.dispatch(self.identities, {
            "op": "BOX_VOLUME", "principal": owner, "action": action,
            "arguments": arguments})

    def owner(self, owner):
        return box_admission.owner_record(self.identities, owner)

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
        drive(identities.admit(owner, routing=projection(identities.path)))
        box_admission.provision(identities, tmp_path, owner)

    events = []
    runtime = SimpleNamespace(stop=lambda uid: events.append(("stop", uid)),
                              shutdown=lambda uid: events.append(("shutdown", uid)))
    images = Images()
    from tinyassets.boxes.gvisor_runtime import GvisorRuntime
    from tinyassets.boxes.host_service import HostService

    runtime.box_id = GvisorRuntime.box_id
    live = set()
    manager = SimpleNamespace(condition=threading.Condition(threading.RLock()), runtime=runtime,
                              boxes={}, status=lambda uid: {"uid": uid, "awake": uid in live,
                                  "idle_seconds": 0, "active": 0, "uptime": 0,
                                  "volume_size": images.sizes.get(uid)})
    service = HostService(None, images, None, manager)
    def execute(operation, **fields):
        result = service.lifecycle({"lifecycle": operation, **fields}, 1002)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result
    routing = BrokerLifecycle(SimpleNamespace(_owner_identities=identities, _data_root=tmp_path))
    lifecycle = routing.volumes
    lifecycle.execute = execute
    runtime.alive = lambda uid: uid in live
    runtime.shutdown = lambda uid: (events.append(("shutdown", uid)), live.discard(uid))
    return SimpleNamespace(lifecycle=lifecycle, volumes=images, admission=Admission(identities),
                           identities=identities, events=events, runtime=runtime, live=live)


def start(storage, owner="alice"):
    record = drive(storage.lifecycle.prepare(
        owner, box_admission.owner_record(storage.identities, owner, for_wake=True)))
    uid = record["uid"]
    storage.volumes.detach(uid)
    mount = storage.volumes.attach(uid)
    storage.volumes.grow(uid, max(record["quota"], storage.volumes.size(uid)))
    storage.live.add(uid)
    return record, mount.oci_mount()


def sleep(storage, owner="alice"):
    uid = storage.lifecycle.volume(owner)["uid"]
    storage.runtime.shutdown(uid)
    storage.volumes.detach(uid)
    storage.live.discard(uid)
    try:
        drive(storage.lifecycle.tick_owner(owner))
    except (OSError, RuntimeError, PermissionError):
        pass  # Explicit retry tests inspect retained work and the prior reference.
    return storage.lifecycle.volume(owner)


@pytest.mark.verifies('owner-daemon-in-box:C1')
def test_every_sleep_publishes_verified_reference_and_growth_never_shrinks(storage):
    record, mount = start(storage)
    uid = record["uid"]
    assert mount["uid"] == uid
    first = sleep(storage)
    assert first["snapshot_id"] and first["snapshot_sha256"] and first["work"] is None
    storage.identities.plans.apply("alice", tier="paid", event_created=1)
    start(storage)
    grown = storage.volumes.size(uid)
    assert ("grow", uid) in storage.volumes.events
    second = sleep(storage)
    assert second["snapshot_id"] != first["snapshot_id"]
    storage.identities.plans.apply("alice", tier="free", event_created=2)
    start(storage)
    assert storage.volumes.size(uid) == grown


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_failed_upload_retains_reference_and_retries_without_waking(storage):
    start(storage)
    previous = sleep(storage)
    start(storage)
    storage.volumes.fail_upload = True
    failed = sleep(storage)
    assert failed["snapshot_id"] == previous["snapshot_id"]
    pending_id = failed["work"]["snapshot_id"]
    assert "alice" in storage.admission.pending_volumes()
    storage.volumes.fail_upload = False
    storage.volumes.events.clear()
    drive(storage.lifecycle.tick_owner("alice"))
    assert storage.lifecycle.volume("alice")["snapshot_id"] == pending_id
    assert not any(event == "attach" for event, _ in storage.volumes.events)


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_inflight_upload_cannot_publish_after_erasure(storage, monkeypatch):
    start(storage)
    previous = sleep(storage)
    start(storage)
    upload = storage.volumes.snapshot

    def erase_during_upload(uid, snapshot_id):
        result = upload(uid, snapshot_id)
        drive(local_mutate(storage.identities.path, "alice", "erase",
            routing=projection(storage.identities.path)))
        return result

    monkeypatch.setattr(storage.volumes, "snapshot", erase_during_upload)
    result = sleep(storage)
    assert result["snapshot_id"] == previous["snapshot_id"]
    assert result["work"]["kind"] == "snapshot"
    assert "alice" not in storage.admission.pending_volumes()
    with pytest.raises(PermissionError, match="erased"):
        drive(storage.lifecycle.recover("alice"))


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_interrupted_restore_commits_generation_before_next_mount(storage, monkeypatch):
    start(storage)
    before = sleep(storage)
    uid = before["uid"]
    storage.volumes.images[uid] = b"newer"
    original = storage.lifecycle.volume
    interrupted = False

    def interrupt(owner, action="binding", **arguments):
        nonlocal interrupted
        if action == "restored" and not interrupted:
            interrupted = True
            raise OSError("map commit interrupted")
        return original(owner, action, **arguments)

    monkeypatch.setattr(storage.lifecycle, "volume", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        drive(storage.lifecycle.restore("alice", before["snapshot_id"]))
    assert storage.volumes.images[uid] == b"initial"
    assert original("alice")["volume_generation"] == before["volume_generation"]
    with pytest.raises(PermissionError, match="restore"):
        storage.admission.owner("alice")
    record, _ = start(storage)
    assert record["volume_generation"] == before["volume_generation"] + 1
    assert original("alice")["work"] == {"kind": "running"}
    assert storage.volumes.events[-2:] == [("attach", uid), ("grow", uid)]


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_restore_needs_only_published_reference_not_local_catalog_or_volume(storage):
    start(storage)
    published = sleep(storage)
    uid = published["uid"]
    storage.volumes.images.pop(uid)
    with sqlite3.connect(storage.identities.path) as db:
        db.execute("DELETE FROM owner_snapshots")
    storage.volumes.events.clear()
    result = drive(storage.lifecycle.restore("alice", published["snapshot_id"]))
    assert result["volume_generation"] == published["volume_generation"] + 1
    assert storage.volumes.images[uid] == b"initial"
    assert all(owner == uid for _, owner in storage.volumes.events)
    from tinyassets.broker.admission import read

    assert read(storage.identities, "alice").state == "admitted"


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_retention_keeps_seven_and_never_deletes_current_map_reference(storage):
    for _ in range(9):
        start(storage)
        record = sleep(storage)
    retained = storage.lifecycle.volume("alice", "snapshots")
    assert len(retained) == 7
    assert len(storage.volumes.remote) == 7
    assert (record["uid"], record["snapshot_id"]) in storage.volumes.remote
    with pytest.raises(PermissionError, match="map references"):
        storage.lifecycle.volume("alice", "forget", snapshot_id=record["snapshot_id"])


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_restore_old_snapshot_repoints_map_and_retention_preserves_it(storage, monkeypatch):
    start(storage)
    first = sleep(storage)
    prune = storage.lifecycle.prune
    async def no_prune(owner):
        pass
    monkeypatch.setattr(storage.lifecycle, "prune", no_prune)
    for _ in range(8):
        start(storage)
        sleep(storage)
    restored = drive(storage.lifecycle.restore("alice", first["snapshot_id"]))
    assert restored["snapshot_id"] == first["snapshot_id"]
    assert restored["snapshot_sha256"] == first["snapshot_sha256"]
    drive(prune("alice"))
    assert len(storage.lifecycle.volume("alice", "snapshots")) == 7
    assert len(storage.volumes.remote) == 7
    assert (first["uid"], first["snapshot_id"]) in storage.volumes.remote


@pytest.mark.verifies('owner-daemon-in-box:T3')
def test_permanent_binding_and_erasure_survive_volume_deletion(storage):
    record, _ = start(storage)
    uid = record["uid"]
    drive(local_mutate(storage.identities.path, "alice", "erase",
        routing=projection(storage.identities.path)))
    storage.runtime.stop(uid)
    storage.volumes.delete(uid)
    assert uid not in storage.volumes.images
    assert storage.lifecycle.volume("alice")["uid"] == uid
    with sqlite3.connect(storage.identities.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="permanent"):
            db.execute("UPDATE owner_volumes SET uid=399999 WHERE principal='alice'")
        with pytest.raises(sqlite3.IntegrityError, match="permanent"):
            db.execute("DELETE FROM owner_volumes WHERE principal='alice'")
    with pytest.raises(LookupError):
        storage.admission.owner("alice")


@pytest.mark.verifies('owner-daemon-in-box:T3')
def test_missing_admission_cannot_reprovision_an_existing_volume_binding(storage):
    with sqlite3.connect(storage.identities.path) as db:
        db.execute("DELETE FROM box_records WHERE principal='alice'")
    with pytest.raises(sqlite3.IntegrityError):
        box_admission.provision(storage.identities, storage.identities.path.parent.parent,
                                "alice")
    from tinyassets.broker.admission import local_mutate

    drive(local_mutate(storage.identities.path, "alice", "erase",
        routing=projection(storage.identities.path)))
    with pytest.raises(PermissionError, match="erased"):
        drive(storage.lifecycle.recover("alice"))


@pytest.mark.verifies('owner-daemon-in-box:C2')
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


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_owner_scratch_named_like_a_store_never_blocks_startup(tmp_path):
    # Real production data held agent scratch text named .effector_consents.db
    # deep under a center's .tmp; the box must still start.
    from tinyassets.owner_centers import Centers
    from tinyassets.owner_runtime import validate_databases

    root = tmp_path / "owner"
    root.mkdir()
    Centers(root).create("c1")
    scratch = root / "c1" / ".tmp" / "a" / "b"
    scratch.mkdir(parents=True)
    (scratch / ".effector_consents.db").write_text("not a database")
    validate_databases(root)
    with sqlite3.connect(root / "history.db") as db:
        db.execute("CREATE TABLE history (value TEXT)")
    validate_databases(root)


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("arguments", [{"uid": 300002}, {"path": "/other"},
                                       {"volume_generation": 8}])
def test_volume_protocol_refuses_caller_selected_binding(storage, arguments):
    with pytest.raises(ValueError):
        storage.lifecycle.volume("alice", **arguments)


def age_sleep(storage, seconds):
    with sqlite3.connect(storage.identities.path) as db:
        db.execute("UPDATE owner_volumes SET asleep_at=? WHERE principal='alice'",
                   (time.time() - seconds,))


@pytest.mark.verifies('owner-daemon-in-box:C10')
def test_cold_eviction_restores_exact_bytes_before_attach(storage):
    record, _ = start(storage)
    uid = record["uid"]
    storage.volumes.images[uid] = b"owner bytes\x00\xff"
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER - 1)
    assert not drive(storage.lifecycle.evict("alice"))
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    assert "alice" in storage.admission.pending_volumes()
    assert drive(storage.lifecycle.evict("alice"))
    assert not storage.lifecycle.volume("alice")["local"]
    assert uid not in storage.volumes.images
    storage.volumes.events.clear()
    record, _ = start(storage)
    assert storage.volumes.images[uid] == b"owner bytes\x00\xff"
    assert record["volume_generation"] == before["volume_generation"] + 1
    assert storage.lifecycle.volume("alice")["local"]
    assert storage.lifecycle.volume("alice")["asleep_at"] is None
    events = storage.volumes.events
    assert events.index(("restore", uid)) < events.index(("attach", uid))
    assert ("create", uid) not in storage.volumes.events


@pytest.mark.verifies('owner-daemon-in-box:C10')
@pytest.mark.parametrize("state", ["pending", "restore", "erased", "unverified", "awake"])
def test_eviction_requires_completed_sleep_and_published_snapshot(storage, state):
    start(storage)
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    if state == "pending":
        storage.lifecycle.volume("alice", "sleep")
    elif state == "restore":
        storage.lifecycle.volume("alice", "restore", snapshot_id=before["snapshot_id"])
    elif state == "erased":
        drive(local_mutate(storage.identities.path, "alice", "erase",
            routing=projection(storage.identities.path)))
    elif state == "unverified":
        with sqlite3.connect(storage.identities.path) as db:
            db.execute("UPDATE owner_volumes SET snapshot_id=NULL,snapshot_sha256=NULL")
    else:
        start(storage)
    if state in ("erased", "unverified"):
        with pytest.raises((PermissionError, ValueError)):
            drive(storage.lifecycle.evict("alice"))
    else:
        assert not drive(storage.lifecycle.evict("alice"))
    assert before["uid"] in storage.volumes.images
    assert storage.lifecycle.volume("alice")["local"]


@pytest.mark.verifies('owner-daemon-in-box:C10')
def test_cold_restore_failure_never_creates_empty_volume(storage):
    start(storage)
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    drive(storage.lifecycle.evict("alice"))
    storage.volumes.remote.clear()
    storage.volumes.events.clear()
    with pytest.raises(RuntimeError):
        start(storage)
    assert before["uid"] not in storage.volumes.images
    assert not storage.volumes.attached
    assert not any(event == "create" for event, _ in storage.volumes.events)
    assert storage.lifecycle.volume("alice")["work"]["kind"] == "restore"


@pytest.mark.verifies('owner-daemon-in-box:C10')
def test_interrupted_eviction_resumes_before_cold_wake(storage, monkeypatch):
    start(storage)
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    dispatch = storage.lifecycle.volume

    def interrupted(owner, action="binding", **arguments):
        if action == "evicted":
            raise OSError("commit interrupted")
        return dispatch(owner, action, **arguments)

    monkeypatch.setattr(storage.lifecycle, "volume", interrupted)
    with pytest.raises(OSError):
        drive(storage.lifecycle.evict("alice"))
    assert before["uid"] not in storage.volumes.images
    monkeypatch.setattr(storage.lifecycle, "volume", dispatch)
    start(storage)
    assert storage.volumes.images[before["uid"]] == b"initial"


@pytest.mark.verifies('owner-daemon-in-box:C10')
@pytest.mark.parametrize("first", ["wake", "evict"])
def test_concurrent_wake_and_eviction_share_owner_serialization(storage, monkeypatch, first):
    import asyncio

    from tinyassets.broker.admission import owner_call

    start(storage)
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
    action = "wake" if first == "wake" else "evict"
    dispatch = storage.lifecycle.volume

    def paused(owner, operation="binding", **arguments):
        if operation == action and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return dispatch(owner, operation, **arguments)

    monkeypatch.setattr(storage.lifecycle, "volume", paused)
    methods = {"wake": lambda owner: start(storage, owner),
               "evict": lambda owner: drive(storage.lifecycle.evict(owner))}
    async def exercise():
        one = asyncio.create_task(owner_call(storage.identities.path, "alice", methods[first],
                                             "alice"))
        assert await asyncio.to_thread(entered.wait, 10)
        second_started.set()
        two = asyncio.create_task(owner_call(storage.identities.path, "alice",
                                             methods["evict" if first == "wake" else "wake"],
                                             "alice"))
        await asyncio.sleep(0)
        assert second_started.is_set() and not two.done()
        release.set()
        await asyncio.gather(one, two)
    asyncio.run(exercise())
    assert storage.volumes.images[before["uid"]] == b"initial"
    assert storage.lifecycle.volume("alice")["local"]
    assert before["uid"] in storage.volumes.attached


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_owner_restore_keeps_revoked_key_and_refuses_write_and_bash_replay(storage, tmp_path):
    import shutil

    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxOperationRefused

    start(storage)
    published = sleep(storage)

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
    other = storage.lifecycle.volume("bob")
    restored = drive(storage.lifecycle.restore("alice", published["snapshot_id"]))
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
    assert storage.lifecycle.volume("bob") == other
    assert restored["volume_generation"] == published["volume_generation"] + 1


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Undo points use automatic sleep and explicit owner checkpoints')
def test_checkpoint_publishes_catalog_and_failed_upload_stays_pending(storage):
    start(storage)
    automatic = sleep(storage)
    listed = storage.lifecycle.volume("alice", "snapshots")
    assert listed[0]["snapshot_id"] == automatic["snapshot_id"]
    assert listed[0]["published_at"]
    start(storage)
    uid = automatic["uid"]
    storage.volumes.images[uid] = b"checkpoint"
    saved = drive(storage.lifecycle.checkpoint("alice"))
    assert saved["work"] is None
    assert saved["snapshot_id"] != automatic["snapshot_id"]
    catalog = storage.lifecycle.volume("alice", "snapshots")
    assert catalog[0]["snapshot_id"] == saved["snapshot_id"]
    assert catalog[0]["sha256"] == hashlib.sha256(b"checkpoint").hexdigest()
    assert catalog[0]["published_at"] >= listed[0]["published_at"]
    start(storage)
    storage.volumes.fail_upload = True
    with pytest.raises(RuntimeError, match="upload failed"):
        drive(storage.lifecycle.checkpoint("alice"))
    pending = storage.lifecycle.volume("alice")
    assert pending["snapshot_id"] == saved["snapshot_id"]
    assert pending["work"]["kind"] == "snapshot"
    assert "restore_snapshot_id" not in pending["work"]
    assert all(row["snapshot_id"] != pending["work"]["snapshot_id"]
               for row in storage.lifecycle.volume("alice", "snapshots"))
    storage.volumes.fail_upload = False
    drive(storage.lifecycle.tick_owner("alice"))
    published = storage.lifecycle.volume("alice")
    assert published["work"] is None
    assert published["snapshot_id"] == pending["work"]["snapshot_id"]
    assert storage.volumes.remote[uid, published["snapshot_id"]] == b"checkpoint"


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Undo shares seven-snapshot retention and the C10 budget')
def test_optional_actions_refuse_without_fleet_headroom(storage):
    from tinyassets.broker.volume_lifecycle import LOCAL_CAP, REMOTE_CAP, VolumeBudgetRefused

    start(storage)
    before = sleep(storage)
    uid = before["uid"]
    start(storage)
    storage.volumes.images[uid] = b"kept"
    generation = storage.lifecycle.volume("alice")["volume_generation"]
    storage.lifecycle.remote_cap = 0
    with pytest.raises(VolumeBudgetRefused, match="fleet budget"):
        drive(storage.lifecycle.restore("alice", before["snapshot_id"]))
    refused = storage.lifecycle.volume("alice")
    assert storage.volumes.images[uid] == b"kept"
    assert refused["snapshot_id"] == before["snapshot_id"]
    assert refused["volume_generation"] == generation
    assert refused["work"] == {"kind": "running"}
    storage.lifecycle.remote_cap = REMOTE_CAP
    storage.lifecycle.local_cap = 0
    with pytest.raises(VolumeBudgetRefused, match="fleet budget"):
        drive(storage.lifecycle.checkpoint("alice"))
    assert storage.volumes.images[uid] == b"kept"
    assert storage.lifecycle.volume("alice")["snapshot_id"] == before["snapshot_id"]
    assert storage.lifecycle.volume("alice")["work"] == {"kind": "running"}
    storage.lifecycle.local_cap = LOCAL_CAP
    published = sleep(storage)
    assert published["work"] is None
    assert published["snapshot_id"] != before["snapshot_id"]


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Undo shares seven-snapshot retention and the C10 budget')
def test_pending_restore_keeps_the_selected_source_until_completion(storage, monkeypatch):
    start(storage)
    first = sleep(storage)
    original = storage.lifecycle.prune

    async def no_prune(owner):
        pass

    monkeypatch.setattr(storage.lifecycle, "prune", no_prune)
    for _ in range(8):
        start(storage)
        sleep(storage)
    storage.lifecycle.volume("alice", "restore", snapshot_id=first["snapshot_id"])
    with pytest.raises(PermissionError, match="map references"):
        storage.lifecycle.volume("alice", "forget", snapshot_id=first["snapshot_id"])
    drive(original("alice"))
    held = storage.lifecycle.volume("alice", "snapshots")
    assert len(held) == 8
    assert first["snapshot_id"] in {row["snapshot_id"] for row in held}
    assert (first["uid"], first["snapshot_id"]) in storage.volumes.remote
    restored = drive(storage.lifecycle.restore("alice", first["snapshot_id"]))
    assert restored["snapshot_id"] == first["snapshot_id"]
    assert restored["work"] is None
    drive(original("alice"))
    finished = storage.lifecycle.volume("alice", "snapshots")
    assert len(finished) == 7
    assert len(storage.volumes.remote) == 7
    assert first["snapshot_id"] in {row["snapshot_id"] for row in finished}


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Publish current state before restoring')
def test_wake_refuses_while_restore_is_pending(storage):
    start(storage)
    before = sleep(storage)
    pending = storage.lifecycle.volume("alice", "restore", snapshot_id=before["snapshot_id"])
    assert pending["work"]["kind"] == "snapshot"
    assert pending["work"]["restore_snapshot_id"] == before["snapshot_id"]
    with pytest.raises(PermissionError, match="restore must complete"):
        storage.lifecycle.volume("alice", "wake")
    with pytest.raises(PermissionError, match="owner restore has not committed"):
        storage.admission.owner("alice")
    kept = storage.lifecycle.volume("alice")
    assert kept["work"]["snapshot_id"] == pending["work"]["snapshot_id"]
    start(storage, "bob")
    sleep(storage, "bob")
    start(storage, "bob")
    storage.lifecycle.volume("bob", "sleep")
    woken = storage.lifecycle.volume("bob", "wake")
    assert woken["work"] == {"kind": "running"}


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Publish current state before restoring')
def test_pre_restore_publication_precedes_install_and_survives_restart(storage):
    start(storage)
    before = sleep(storage)
    uid = before["uid"]
    start(storage)
    storage.volumes.images[uid] = b"current"
    order = []
    snapshot, restore = storage.volumes.snapshot, storage.volumes.restore

    def ordered_snapshot(owner_uid, snapshot_id):
        order.append(("snapshot", storage.volumes.images[owner_uid]))
        return snapshot(owner_uid, snapshot_id)

    def ordered_restore(owner_uid, snapshot_id, sha256):
        order.append(("restore", snapshot_id))
        return restore(owner_uid, snapshot_id, sha256)

    storage.volumes.snapshot = ordered_snapshot
    storage.volumes.restore = ordered_restore
    storage.volumes.fail_upload = True
    with pytest.raises(RuntimeError, match="upload failed"):
        drive(storage.lifecycle.restore("alice", before["snapshot_id"]))
    failed = storage.lifecycle.volume("alice")
    assert storage.volumes.images[uid] == b"current"
    assert failed["snapshot_id"] == before["snapshot_id"]
    assert failed["volume_generation"] == before["volume_generation"]
    assert failed["work"]["kind"] == "snapshot"
    assert failed["work"]["restore_snapshot_id"] == before["snapshot_id"]
    assert ("restore", before["snapshot_id"]) not in order
    storage.volumes.fail_upload = False
    order.clear()
    resumed = drive(storage.lifecycle.restore("alice", before["snapshot_id"]))
    assert order == [("snapshot", b"current"), ("restore", before["snapshot_id"])]
    assert storage.volumes.images[uid] == b"initial"
    assert resumed["snapshot_id"] == before["snapshot_id"]
    assert resumed["volume_generation"] == before["volume_generation"] + 1
    assert resumed["rewind_generation"] == resumed["volume_generation"]
    published = hashlib.sha256(b"current").hexdigest()
    assert published in {row["sha256"] for row in storage.lifecycle.volume("alice", "snapshots")}
    record, _ = start(storage)
    assert record["rewind"] is True


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Only protected owner confirmation reaches restore')
def test_broker_rechecks_owner_session_before_restore(storage, tmp_path):
    import json

    from tinyassets.broker.policy import PolicyRefused, PolicyStore
    from tinyassets.broker.volume_restore import operation

    start(storage)
    before = sleep(storage)
    uid = before["uid"]
    start(storage)
    storage.volumes.images[uid] = b"live"
    store = PolicyStore(tmp_path, storage.identities)
    store.bootstrap_owner("alice")
    cookie = "n6-owner-session-" + "cd" * 16
    session_hash = hashlib.sha256(cookie.encode()).hexdigest()
    folder = tmp_path / ".runtime"
    folder.mkdir(mode=0o700)
    with sqlite3.connect(folder / "owner-sessions.db") as db:
        db.execute("CREATE TABLE owner_sessions (session_hash TEXT, identity_json TEXT, "
                   "expires_at REAL)")
        db.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                   (session_hash, json.dumps({"user_id": "alice"}), time.time() + 3600))
    seen = []
    execute = storage.lifecycle.execute

    def watch(operation_name, **fields):
        seen.append(fields)
        return execute(operation_name, **fields)

    storage.lifecycle.execute = watch
    routing = SimpleNamespace(volumes=storage.lifecycle, checkpoint=storage.lifecycle.checkpoint,
                              restore=storage.lifecycle.restore)
    server = SimpleNamespace(_policy_store=store, _owner_identities=storage.identities,
                             routing=routing)
    proof = {"session_hash": session_hash}

    def request(action, **fields):
        return {"op": "OWNER_VOLUME", "action": action, "principal": "alice",
                "session_proof": proof, **fields}

    catalog = drive(operation(server, request("catalog")))
    assert catalog["volume_generation"] == before["volume_generation"]
    assert catalog["snapshots"][0]["snapshot_id"] == before["snapshot_id"]
    assert catalog["snapshots"][0]["published_at"]
    forged = dict(proof, session_hash="ab" * 32)
    with pytest.raises(PolicyRefused, match="owner session"):
        drive(operation(server, {**request("restore", snapshot_id=before["snapshot_id"],
                                           expected_generation=before["volume_generation"]),
                                 "session_proof": forged}))
    with pytest.raises(PolicyRefused, match="owner session"):
        drive(operation(server, {**request("restore", snapshot_id=before["snapshot_id"],
                                           expected_generation=before["volume_generation"]),
                                 "principal": "bob"}))
    with pytest.raises(PermissionError, match="invalid volume request"):
        drive(operation(server, {**request("restore", snapshot_id=before["snapshot_id"],
                                           expected_generation=before["volume_generation"]),
                                 "owner": "alice"}))
    with pytest.raises(PermissionError, match="volume generation changed"):
        drive(operation(server, request("restore", snapshot_id=before["snapshot_id"],
                                        expected_generation=before["volume_generation"] + 3)))
    proposed = drive(operation(server, request("proposal", snapshot_id=before["snapshot_id"])))
    assert proposed["generation"] >= 1
    assert storage.volumes.images[uid] == b"live"
    assert storage.lifecycle.volume("alice")["work"] == {"kind": "running"}
    result = drive(operation(server, request(
        "restore", snapshot_id=before["snapshot_id"],
        expected_generation=before["volume_generation"])))
    assert result["snapshot_id"] == before["snapshot_id"]
    assert result["rewind"] is True
    assert storage.volumes.images[uid] == b"initial"
    assert all("session_proof" not in fields and "session" not in fields for fields in seen)
    receipt_text = json.dumps(store.receipts("alice"))
    assert cookie not in receipt_text
    assert "restore" in receipt_text
    with sqlite3.connect(store._path("alice")) as db:
        stored = db.execute("SELECT session_id FROM receipts WHERE kind='restore'").fetchone()
    assert stored == (session_hash,)


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Only protected owner confirmation reaches restore')
def test_box_channel_and_bad_fence_cannot_restore(storage, tmp_path):
    import json

    from tinyassets.broker.fence import Fence
    from tinyassets.broker.server import _Connection
    from tinyassets.rpc_frames import HEADER_BYTES

    sent = {}

    async def send_async(frame):
        sent["frame"] = frame

    conn = SimpleNamespace(
        _role="box", _box_owner="alice", send_async=send_async,
        _server=SimpleNamespace(_owner_identities=storage.identities))
    drive(_Connection._connection_op(conn, "OWNER_VOLUME", {"op": "OWNER_VOLUME"}))
    document = json.loads(sent["frame"][HEADER_BYTES:])
    assert document == {"op": "OWNER_VOLUME_REFUSED",
                        "reason": "restore is not available on the box channel"}
    fence = Fence(tmp_path / "fence.json", verify_lease_proof=lambda *_args: True)
    server = SimpleNamespace(_fence=fence, _policy_store=None, _owner_identities=None)
    refused = drive(_Connection._volume_operation(
        SimpleNamespace(_server=server),
        {"op": "OWNER_VOLUME", "action": "restore", "principal": "alice",
         "session_proof": {"session_hash": "ab" * 32}, "snapshot_id": "a" * 32,
         "expected_generation": 1, "generation": 1, "token": "stale"}))
    assert refused["op"] == "OWNER_VOLUME_REFUSED"
    assert refused["reason"] == "invalid fence"


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Only protected owner confirmation reaches restore')
def test_app_restore_route_uses_the_owner_session(monkeypatch):
    import asyncio
    import json

    from starlette.requests import Request

    from tests.support.onboarding import _user
    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import onboarding_routes

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr("tinyassets.owner_runtime.current", lambda: None)
    calls = []

    def require(request, owner=None):
        assert owner == "alice"
        return {"session_hash": "ab" * 32}

    def submit(root, owner, action, proof, **fields):
        calls.append((owner, action, proof, fields))
        return {"snapshot_id": fields.get("snapshot_id"), "pending": False}

    monkeypatch.setattr("tinyassets.onboarding.owner_sessions.require", require)
    monkeypatch.setattr("tinyassets.broker.volume_restore.submit", submit)

    def post(path, body, *, origin="https://tinyassets.io"):
        route = next(item for item in onboarding_routes() if item.path == path)
        payload = json.dumps(body).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        scope = {"type": "http", "method": "POST", "path": path, "query_string": b"",
                 "headers": [(b"content-type", b"application/json"),
                             (b"origin", origin.encode()), (b"host", b"tinyassets.io")]}

        async def run():
            with identity_context(_user("alice")):
                return await route.endpoint(Request(scope, receive))

        response = asyncio.run(run())
        return response.status_code, json.loads(response.body or b"{}")

    status, body = post("/app/owner/restore",
                        {"snapshot_id": "abc", "expected_generation": 4})
    assert status == 200
    assert calls == [("alice", "restore", {"session_hash": "ab" * 32},
                      {"snapshot_id": "abc", "expected_generation": 4})]
    status, body = post("/app/owner/restore",
                        {"snapshot_id": "abc", "expected_generation": 4, "owner": "bob"})
    assert status == 400 and body["error"] == "invalid_restore"
    assert len(calls) == 1
    status, body = post("/app/owner/restore", {"snapshot_id": "abc", "expected_generation": 4},
                        origin="https://evil.example")
    assert status == 403 and body["error"] == "cross_origin_rejected"
    monkeypatch.setattr("tinyassets.owner_runtime.current", lambda: SimpleNamespace())
    status, body = post("/app/owner/checkpoint", {})
    assert status == 403 and body["error"] == "restore_requires_owner_session"
    monkeypatch.setattr("tinyassets.owner_runtime.current", lambda: None)

    def refuse(request, owner=None):
        raise PermissionError("interactive owner session required")

    monkeypatch.setattr("tinyassets.onboarding.owner_sessions.require", refuse)
    status, body = post("/app/owner/restore-points", {})
    assert status == 403 and body["error"] == "interactive_owner_session_required"
    assert len(calls) == 1


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Waiting calls and restored work never replay automatically')
def test_waiting_calls_and_restored_queues_do_not_replay(tmp_path, monkeypatch):
    import asyncio
    import base64
    import json
    from datetime import datetime, timezone

    from tinyassets.boxes.restore_settle import propose_restore, result_path, resume, rewind_paused
    from tinyassets.control_plane.activities import dispatch_universe
    from tinyassets.control_plane.scheduler import ControlPlaneScheduler
    from tinyassets.control_plane.triggers import TriggerStore
    from tinyassets.owner_activity import activity
    from tinyassets.owner_ingress import OwnerIngress
    from tinyassets.owner_runtime import validate_databases
    from tinyassets.runs import pause_queued

    root = tmp_path / "owner"
    root.mkdir()
    broken = root / "history.db"
    broken.write_bytes(b"broken database")
    with pytest.raises(sqlite3.DatabaseError):
        validate_databases(root)
    assert not result_path(root).exists()
    broken.unlink()
    with sqlite3.connect(broken) as db:
        db.execute("CREATE TABLE history (value TEXT)")
    validate_databases(root)
    with sqlite3.connect(root / ".runs.db") as db:
        db.execute("CREATE TABLE runs (status TEXT, error TEXT)")
        db.execute("INSERT INTO runs VALUES ('queued', NULL)")
        db.execute("INSERT INTO runs VALUES ('running', NULL)")
    with sqlite3.connect(root / ".pending_requests.db") as db:
        db.execute("CREATE TABLE pending_requests (status TEXT, resolved_at REAL)")
        db.execute("INSERT INTO pending_requests VALUES ('pending', NULL)")
    triggers = TriggerStore(root)
    connection = triggers._connect(create=True)
    connection.execute(
        "INSERT INTO triggers (trigger_key, kind, command_center_id, agent_id, "
        "owner_principal_id, enabled, created_at, updated_at) "
        "VALUES ('daily', 'proactive', 'center', 'agent', 'alice', 1, 't', 't')")
    connection.commit()
    connection.close()
    from tinyassets.boxes.restore_settle import settle_rewind

    document = settle_rewind(root, 4)
    assert document["outcome"] == "paused"
    assert document["generation"] == 4
    assert document["invalidated_approvals"] == 1
    assert document["paused_triggers"] == 1
    assert document["paused_runs"] == 1
    assert rewind_paused(root)
    with sqlite3.connect(root / ".runs.db") as db:
        assert db.execute("SELECT status FROM runs WHERE status='queued'").fetchone() is None
        assert db.execute("SELECT status FROM runs WHERE status='paused_by_restore'"
                          ).fetchone() is not None
    with sqlite3.connect(root / ".pending_requests.db") as db:
        assert db.execute("SELECT status FROM pending_requests").fetchone()[0] == "invalidated"
    assert pause_queued(root) == 0
    called = []
    monkeypatch.setattr("tinyassets.control_plane.activities._dispatch_universe",
                        lambda *args, **kwargs: called.append(args))
    dispatch_universe(root, "center")
    scheduler = ControlPlaneScheduler.__new__(ControlPlaneScheduler)
    scheduler.store = SimpleNamespace(base_path=root)
    report = ControlPlaneScheduler.tick(scheduler, now=datetime.now(timezone.utc))
    assert called == []
    assert report.fired == []
    assert report.lease_held is True
    with pytest.raises(PermissionError, match="fresh generation"):
        resume(root, 4, "3:old-operation")
    with pytest.raises(PermissionError, match="restore generation"):
        resume(root, 3, "3:old-operation")
    resumed = resume(root, 4, "4:fresh-operation")
    assert resumed["outcome"] == "resumed"
    assert resumed["resumed_by"] == "4:fresh-operation"
    assert not rewind_paused(root)
    dispatch_universe(root, "center")
    assert called
    with sqlite3.connect(root / ".runs.db") as db:
        assert db.execute("SELECT status, error FROM runs WHERE status='paused_by_restore'"
                          ).fetchone() == ("paused_by_restore", "paused_by_restore")
    proposal_root = tmp_path / "proposal"
    proposal_root.mkdir()
    proposal = propose_restore(proposal_root, "a" * 32)
    assert proposal["kind"] == "restore"
    assert proposal["action"]["type"] == "restore_proposal"
    flags = ("draining", "revoked", "deleting", "deletion_only", "snapshot_stop", "active")
    saved = {name: getattr(activity, name) for name in flags}
    saved_waiters = list(activity.waiters)
    try:
        activity.waiters.clear()
        for name in flags:
            setattr(activity, name, False if name != "active" else 0)
        waiting = activity.waiting("op-old", 2)
        activity.status(snapshot=True)
        assert waiting["result"] == {"error": "interrupted_by_restore", "operation_id": "op-old",
                                     "volume_generation": 2}
        parked = activity.waiting("op-during", 2)
        assert parked["result"]["error"] == "interrupted_by_restore"
        secret = tmp_path / "box-secret"
        secret.write_bytes(b"a" * 43)
        monkeypatch.setattr("tinyassets.owner_runtime.current",
                            lambda: SimpleNamespace(principal="alice", uid=7))
        token = base64.urlsafe_b64encode(json.dumps({
            "user_id": "alice", "username": "alice", "capabilities": []}).encode())
        messages = []

        async def send(message):
            messages.append(message)

        async def app(scope, receive, send_message):
            raise AssertionError("a draining box admitted work")

        ingress = OwnerIngress(app, secret)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        asyncio.run(ingress({
            "type": "http", "method": "GET", "path": "/app", "query_string": b"",
            "headers": [(b"x-box-secret", b"a" * 43), (b"x-owner", b"alice"),
                        (b"x-owner-identity", token)],
        }, receive, send))
        body = b"".join(message.get("body", b"") for message in messages
                        if message["type"] == "http.response.body")
        assert json.loads(body) == {"error": "box_draining", "retry": True}
        assert messages[0]["status"] == 503
    finally:
        activity.waiters[:] = saved_waiters
        for name, value in saved.items():
            setattr(activity, name, value)


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Only protected owner confirmation reaches restore')
def test_undo_confirmation_uses_the_whole_box_copy():
    import subprocess
    from pathlib import Path

    script = r"""
const fs = require("fs");
const src = fs.readFileSync("tinyassets/onboarding/app/undo.js", "utf8");
const match = src.match(/function restoreConfirmation\(time\) \{\n[\s\S]*?\n  \}/);
if (!match) process.exit(2);
eval(match[0]);
const time = "October 23, 2026, 10:00 AM";
const copy = restoreConfirmation(time);
const expected = "Restore the whole box to " + time + "? "
  + "Later files, chat/history, skills, packages and schedules will rewind. "
  + "Your current state is saved as a restore point first, "
  + "available until normal retention removes it. "
  + "External sends, purchases and publications cannot be undone.";
if (copy !== expected) {
  console.error(copy);
  process.exit(1);
}
if (!src.includes("expected_generation: catalog.volume_generation")) process.exit(3);
"""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "-e", script], cwd=root, capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr + result.stdout
    page = (root / "tinyassets/onboarding/app.html").read_text(encoding="utf-8")
    assert 'id="btn-save-restore-point"' in page
    assert 'id="restore-points"' in page


@pytest.mark.verifies(
    'owner-box-snapshot-undo:Waiting calls and restored work never replay automatically')
def test_rewind_launch_flag_is_only_owner_undo(storage, tmp_path, monkeypatch):
    import os
    import socket
    from pathlib import Path

    from tinyassets.boxes.gvisor_runtime import GvisorRuntime

    start(storage)
    before = sleep(storage)
    age_sleep(storage, owner_volumes.COLD_AFTER + 1)
    assert drive(storage.lifecycle.evict("alice"))
    cold, _ = start(storage)
    assert "rewind" not in cold
    assert storage.lifecycle.volume("alice")["rewind_generation"] is None
    start(storage)
    storage.volumes.images[before["uid"]] = b"after-cold"
    undone = drive(storage.lifecycle.restore("alice", before["snapshot_id"]))
    assert undone["rewind_generation"] == undone["volume_generation"]
    again, _ = start(storage)
    assert again["rewind"] is True
    runsc = tmp_path / "runsc"
    runsc.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runsc.chmod(0o755)
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    runtime = GvisorRuntime(runsc=runsc, rootfs=rootfs, state_root=tmp_path / "state",
                            bundle_root=tmp_path / "bundle",
                            cgroup_parent=Path("/sys/fs/cgroup/ta-n6-test"))
    relay = tmp_path / "relay.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(relay))
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "http://localhost")
    mount = {"destination": "/home/owner"}
    try:
        rewound = runtime._config(
            {"process": {}, "mounts": []}, uid=300001, gid=300001, volume_mount=mount,
            volume_generation=again["volume_generation"], relay=relay, memory_bytes=1024 ** 3,
            rewind=True)
        plain = runtime._config(
            {"process": {}, "mounts": []}, uid=300002, gid=300002, volume_mount=mount,
            volume_generation=1, relay=relay, memory_bytes=1024 ** 3, rewind=False)
    finally:
        sock.close()
    assert "TINYASSETS_VOLUME_REWIND=1" in rewound["process"]["env"]
    assert "TINYASSETS_VOLUME_REWIND=1" not in plain["process"]["env"]
    assert os.environ["UNIVERSE_SERVER_URL"] == "http://localhost"
