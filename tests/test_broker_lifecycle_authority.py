"""C14/C15: broker lifecycle authority, asynchronous serialization and local jobs."""

import ast
import asyncio
import copy
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.admission import drive, projection
from tinyassets.broker import admission, box_admission, owner_volumes
from tinyassets.broker.deletion_jobs import STEPS, DeletionJobs
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.routing import BrokerLifecycle


class Remote:
    def __init__(self):
        self.rows = {}
        self.reads = 0

    def save(self, job):
        self.rows[job["id"]] = copy.deepcopy(job)

    def records(self):
        self.reads += 1
        return list(copy.deepcopy(self.rows).values())


@pytest.fixture
def authority(tmp_path, monkeypatch):
    root = tmp_path / ".broker"
    root.mkdir(mode=0o700)
    identities = OwnerIdentities(root / "identities.db", initialize=True)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    for owner in ("alice", "bob"):
        identities.resolve(owner, allocate=True)
        drive(identities.admit(owner, routing=projection(identities.path)))
        box_admission.provision(identities, tmp_path, owner)
    events, remote, released = [], Remote(), []
    server = SimpleNamespace(_owner_identities=identities, _data_root=tmp_path)
    server.routing = BrokerLifecycle(server)
    from tinyassets.router_credentials import routing_dispatch
    server.routing._router = lambda request: routing_dispatch(tmp_path, request)
    server.routing.publish()

    async def changed(owner):
        assert admission.read(identities, owner).state == "erased"
        assert remote.rows[admission.digest(owner)]
        events.extend(["channels_closed", "withdrawal_published"])
        return server.routing.publish()

    server.routing.changed = changed
    original = admission.mutate

    def mutate(*args, **kwargs):
        events.append("mutation")
        return original(*args, **kwargs)

    monkeypatch.setattr(admission, "mutate", mutate)

    def host(operation, owner=None, **fields):
        if operation == "status":
            return {"awake": False, "awake_count": 0, "wake_timeout": 60, "awake_cap": 6}
        if operation == "wake":
            events.append("wake:" + owner)
            return {"uid": fields["record"]["uid"]}
        if operation == "sleep":
            return {"stopped": True}
        if operation == "create":
            return {"created": True}
        if operation == "snapshot":
            return {"snapshot_id": fields["snapshot_id"], "sha256": "b" * 64}
        assert admission.read(identities, "alice").state == "erased"
        assert "withdrawal_published" in events
        assert fields["uid"] == identities.owner_machine("alice")
        if operation == "delete_snapshots":
            assert fields["snapshot_ids"] is None
            assert fields["released"] == released
        events.append(operation)
        return {"destroyed" if operation == "destroy" else "deleted": True}

    monkeypatch.setattr("tinyassets.boxes.daemon_control.request", host)
    server.routing.volumes.execute = host
    jobs = server.deletion_jobs = DeletionJobs(server, records=remote)
    for step in ("custody", "billing", "credentials", "identity"):
        monkeypatch.setattr(DeletionJobs, "_" + step, lambda self, job: "done")
    return SimpleNamespace(jobs=jobs, remote=remote, identities=identities,
                           events=events, server=server, host=host, released=released)


def test_enqueue_and_status_never_read_remote_and_erasure_mutates_once(authority):
    a = authority

    async def exercise():
        result = await a.jobs.enqueue("alice")
        assert result["status"] == "deletion_in_progress"
        assert result["completed_steps"] == ["withdrawal"]
        assert "destroy" not in a.events
        await a.server.routing.close()
        key, token = result["status_url"].split("/")[-2:]
        assert a.jobs.status(key, token)["completed_steps"] == list(STEPS)
        assert (await a.jobs.enqueue("alice"))["status"] == "deleted"
        await a.server.routing.close()
        assert a.events.count("mutation") == 1
        assert a.events.count("withdrawal_published") == 2  # once per enqueue, never resume
        assert a.events.index("mutation") < a.events.index("destroy")
        assert a.remote.reads == 0
    asyncio.run(exercise())


def test_restart_resumes_local_job_when_remote_reads_are_unavailable(authority, monkeypatch):
    a = authority
    start = a.server.routing.start
    monkeypatch.setattr(a.server.routing, "start", lambda *args: None)
    result = asyncio.run(a.jobs.enqueue("alice"))
    assert result["completed_steps"] == ["withdrawal"]
    assert "destroy" not in a.events
    monkeypatch.setattr(a.server.routing, "start", start)
    restarted = DeletionJobs(a.server, records=a.remote, clock=lambda: 10**15)

    async def exercise():
        restarted.tick()
        await a.server.routing.close()
    asyncio.run(exercise())
    assert restarted.records.load(admission.digest("alice"))["completed_steps"] == list(STEPS)
    assert a.events.count("mutation") == 1
    assert a.remote.reads == 0


def test_historical_platform_copy_recovers_from_offhost_job(authority, tmp_path):
    a = authority
    a.released.append("retained-before-erasure")
    with sqlite3.connect(a.identities.path) as db:
        db.execute("UPDATE owner_volumes SET snapshot_id=?,snapshot_sha256=? WHERE principal=?",
                   ("retained-before-erasure", "b" * 64, "alice"))
        db.execute("INSERT INTO owner_snapshots (principal,snapshot_id,snapshot_sha256,"
                   "volume_generation) VALUES (?,?,?,?)",
                   ("alice", "retained-before-erasure", "b" * 64, 1))
    backup = tmp_path / "old.db"
    with sqlite3.connect(a.identities.path) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)

    async def erase():
        await a.jobs.enqueue("alice")
        await a.server.routing.close()
    asyncio.run(erase())
    with sqlite3.connect(backup) as src, sqlite3.connect(a.identities.path) as dst:
        src.backup(dst)
    assert admission.read(a.identities, "alice").state == "admitted"
    recovered = DeletionJobs(a.server, records=a.remote)
    loaded, = a.remote.records()
    host_effects = a.events.count("delete_snapshots"), a.events.count("destroy")
    assert asyncio.run(recovered.resume_loaded_job(loaded))["status"] == "deleted"
    assert admission.read(a.identities, "alice").state == "erased"
    assert recovered.records.load(loaded["id"])["completed_steps"] == list(STEPS)
    record = owner_volumes.binding(a.identities, "alice")
    assert record["snapshot_id"] is None and record["snapshot_sha256"] is None
    with sqlite3.connect(a.identities.path) as db:
        assert db.execute("SELECT * FROM owner_snapshots WHERE principal='alice'").fetchall() == []
    assert (a.events.count("delete_snapshots"), a.events.count("destroy")) == host_effects


def test_wake_and_deletion_share_the_broker_owner_lock(authority, monkeypatch):
    a = authority
    entered, release = threading.Event(), threading.Event()

    def host(operation, owner=None, **fields):
        if operation == "wake":
            entered.set()
            assert release.wait(10)
            assert admission.read(a.identities, owner).state == "admitted"
        return a.host(operation, owner, **fields)
    monkeypatch.setattr("tinyassets.boxes.daemon_control.request", host)

    async def exercise():
        waking = asyncio.create_task(a.server.routing.wake("alice"))
        assert await asyncio.to_thread(entered.wait, 5)
        deleting = asyncio.create_task(a.jobs.enqueue("alice"))
        await asyncio.sleep(0)
        assert not deleting.done()
        assert admission.read(a.identities, "alice").state == "admitted"
        release.set()
        await asyncio.gather(waking, deleting)
        await a.server.routing.close()
        assert a.events.count("mutation") == 1
        with pytest.raises(PermissionError):
            await a.server.routing.wake("alice")
    asyncio.run(exercise())


def test_host_lifecycle_has_no_broker_state_callbacks():
    root = Path(__file__).resolve().parents[1]
    removed = {"BOX_ADMISSION_ERASE", "BOX_RETIRE_ADMISSION", "BOX_VOLUME",
               "BOX_VOLUME_PENDING", "BOX_LIFECYCLE", "RETIRE_OWNER", "ERASE_ACCOUNT",
               "CLOSE_OWNER_CHANNELS", "OWNER_ADMISSION_ERASE", "OWNER_BOX_REVOKE"}
    for relative in ("boxes/admission.py", "boxes/owner_daemons.py", "boxes/host_service.py",
                     "broker/server.py"):
        tree = ast.parse((root / "tinyassets" / relative).read_text())
        assert not {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)} & removed
    assert not (root / "tinyassets/boxes/volume_lifecycle.py").exists()
    assert not (root / "tinyassets/boxes/deletion_records.py").exists()
    tree = ast.parse((root / "tinyassets/boxes/owner_daemons.py").read_text())
    assert not {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} & {
        "wanted", "sleeping", "resume"}
    maintain = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == "maintain")
    assert not {n.attr for n in ast.walk(maintain) if isinstance(n, ast.Attribute)} & {
        "_start", "_sleep", "ensure_awake"}


@pytest.mark.parametrize("action", ["sleep", "restore"])
def test_snapshot_and_restore_hold_owner_lock_until_map_commit(authority, monkeypatch, action):
    a = authority
    lifecycle = a.server.routing.volumes
    lifecycle.volume("alice", "created")
    entered, release = threading.Event(), threading.Event()
    snapshot_id = "a" * 32

    def execute(operation, **fields):
        if operation in {"snapshot", "restore"}:
            entered.set()
            assert release.wait(10)
            assert admission.read(a.identities, "alice").state == "admitted"
            return ({"snapshot_id": fields["snapshot_id"], "sha256": "b" * 64}
                    if operation == "snapshot" else {"restored": True})
        return a.host(operation, **fields)
    lifecycle.execute = execute
    if action == "restore":
        with sqlite3.connect(a.identities.path) as db:
            db.execute("UPDATE owner_volumes SET snapshot_id=?,snapshot_sha256=?",
                       (snapshot_id, "b" * 64))
    # The completed snapshot is now explicitly released through the map.
    monkeypatch.setattr(a.jobs, "_snapshots", lambda job: None)

    async def exercise():
        first = asyncio.create_task(a.server.routing.sleep("alice") if action == "sleep"
                                    else a.server.routing.restore("alice", snapshot_id))
        assert await asyncio.to_thread(entered.wait, 5)
        deleting = asyncio.create_task(a.jobs.enqueue("alice"))
        await asyncio.sleep(0)
        assert not deleting.done()
        assert admission.read(a.identities, "alice").state == "admitted"
        release.set()
        await asyncio.gather(first, deleting)
        await a.server.routing.close()
        assert a.events.count("mutation") == 1
        assert admission.read(a.identities, "alice").state == "erased"
    asyncio.run(exercise())


def test_only_job_step_one_requests_runtime_admission_erasure():
    root = Path(__file__).resolve().parents[1] / "tinyassets/broker"
    writers = []
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "local_mutate" and len(node.args) >= 3
                    and isinstance(node.args[2], ast.Constant) and node.args[2].value == "erase"):
                writers.append(path.name)
    assert writers == ["deletion_jobs.py"]


@pytest.mark.parametrize("operation", ["snapshot", "restore"])
def test_slow_volume_work_does_not_delay_another_scheduled_wake(authority, operation):
    from tinyassets.broker.waker import Waker

    a = authority
    lifecycle = a.server.routing.volumes
    lifecycle.volume("alice", "created")
    pending = lifecycle.volume("alice", "sleep")
    if operation == "restore":
        reference = pending["work"]["snapshot_id"]
        lifecycle.volume("alice", "publish", snapshot_id=reference, sha256="b" * 64)
        lifecycle.volume("alice", "restore", snapshot_id=reference)
    entered, release = threading.Event(), threading.Event()

    def host(action, owner=None, **fields):
        if action == operation:
            entered.set()
            assert release.wait(10)
        if action == "restore":
            return {"restored": True}
        return a.host(action, owner, **fields)
    lifecycle.execute = host
    index = SimpleNamespace(identities=a.identities, due=lambda **kwargs: ["bob"],
                            woken=lambda owner, **kwargs: None)
    waker = Waker(index, None, wake=a.server.routing.wake, lifecycle_tick=a.server.routing.tick)

    async def exercise():
        await waker.tick()
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.wait_for(waker.pending["bob"], 2)
        assert "wake:bob" in a.events
        assert not a.server.routing.tasks["alice"].done()
        release.set()
        await a.server.routing.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("async_operation", [False, True])
def test_repeated_cancellation_cannot_release_owner_before_worker_finishes(
        tmp_path, async_operation):
    entered, release = threading.Event(), threading.Event()

    def blocking():
        entered.set()
        assert release.wait(5)

    async def exercise():
        notified = []

        async def mutation_and_notification():
            await admission.thread_work(blocking)
            notified.append("installed")

        operation = mutation_and_notification if async_operation else blocking
        first = asyncio.create_task(admission.owner_call(tmp_path, "alice", operation))
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(admission.owner_call(tmp_path, "alice", lambda: "second"))
        for _ in range(2):
            first.cancel()
            await asyncio.sleep(0)
        assert not first.done() and not second.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert notified == (["installed"] if async_operation else [])
        assert await second == "second"

    asyncio.run(exercise())


def test_existing_tick_releases_wake_capacity_before_snapshot_copy_finishes(authority, monkeypatch):
    from tinyassets.broker.waker import Waker

    a = authority
    lifecycle = a.server.routing.volumes
    lifecycle.volume("alice", "created")
    lifecycle.volume("alice", "sleep")
    a.server.routing.config.awake_cap = 1
    a.server.routing.awake.add("alice")
    awake = {"alice": True, "bob": False}
    owners = {a.identities.owner_machine(owner): owner for owner in awake}
    entered, stop, stopped, copying, finish = (threading.Event() for _ in range(5))

    def host(operation, owner=None, **fields):
        bound = owner if owner is not None else owners[fields["uid"]]
        if operation == "status":
            return {"awake": awake[bound], "awake_count": sum(awake.values()),
                    "awake_cap": 1, "wake_timeout": 10, "active": 0,
                    "idle_seconds": 0, "idle_limit": 900, "uptime": 0,
                    "volume_size": 1024**3}
        if operation == "sleep":
            entered.set()
            assert stop.wait(10)
            awake[bound] = False
            stopped.set()
        if operation == "snapshot":
            copying.set()
            assert finish.wait(10)
        if operation == "wake":
            awake[bound] = True
        return a.host(operation, owner, **fields)

    lifecycle.execute = host
    monkeypatch.setattr("tinyassets.boxes.daemon_control.request", host)
    index = SimpleNamespace(identities=a.identities, due=lambda **kwargs: ["bob"],
                            woken=lambda owner, **kwargs: None)
    waker = Waker(index, None, wake=a.server.routing.wake, lifecycle_tick=a.server.routing.tick)

    async def exercise():
        await waker.tick()
        assert await asyncio.to_thread(entered.wait, 2)
        assert not waker.pending["bob"].done()
        stop.set()
        assert await asyncio.to_thread(stopped.wait, 2)
        assert await asyncio.to_thread(copying.wait, 2)
        await waker.tick()
        await asyncio.wait_for(waker.pending["bob"], 2)
        assert awake["bob"] and not a.server.routing.tasks["alice"].done()
        finish.set()
        await a.server.routing.close()
    asyncio.run(exercise())


def test_mutation_without_routing_fails_before_changing_admission(authority):
    a = authority
    with pytest.raises(RuntimeError, match="requires routing"):
        drive(admission.local_mutate(a.identities.path, "alice", "erase",
            routing=None))
    assert admission.read(a.identities, "alice").state == "admitted"


def test_map_erase_is_the_only_volume_cleanup_writer_and_retains_release_on_retry(authority):
    a = authority
    snapshot_id = "a" * 32
    with sqlite3.connect(a.identities.path) as db:
        db.execute("UPDATE owner_volumes SET snapshot_id=?,snapshot_sha256=? WHERE principal=?",
                   (snapshot_id, "b" * 64, "alice"))
        admission.mutate(db, "alice", "erase")
    request = {"op": "BOX_VOLUME", "principal": "alice", "action": "erase", "arguments": {}}
    first = owner_volumes.dispatch(a.identities, request)
    assert first["snapshot_id"] is None and first["work"]["released"] == [snapshot_id]
    assert owner_volumes.dispatch(a.identities, request) == first
    source = Path(__file__).resolve().parents[1] / "tinyassets/broker/deletion_jobs.py"
    assert "UPDATE owner_volumes" not in source.read_text()
    assert "DELETE FROM owner_snapshots" not in source.read_text()

def test_routing_projection_contains_only_admitted_and_suspended_owners(authority):
    from tinyassets.routing_snapshot import RoutingSnapshot

    a = authority
    snapshot = RoutingSnapshot()
    def receiver(request):
        if request["operation"] == "routing_instance":
            return {"instance": snapshot.instance}
        revision = snapshot.install(request["instance"], request["revision"], request["records"])
        return {"revision": revision}
    a.server.routing._router = receiver
    a.server.routing.publish = BrokerLifecycle.publish.__get__(a.server.routing)
    with sqlite3.connect(a.identities.path) as db:
        admission.mutate(db, "alice", "erase")
        admission.mutate(db, "bob", "suspend", reason="billing", suspended=True)
    a.server.routing.publish()
    assert snapshot.record("alice") is None
    assert snapshot.record("bob").state == "suspended"
    with pytest.raises(LookupError):
        snapshot.route("alice")


def test_slow_status_for_one_owner_cannot_block_another_wake(authority):
    a = authority
    entered, release = threading.Event(), threading.Event()
    alice = a.identities.owner_machine("alice")
    def host(operation, owner=None, **fields):
        if operation == "status" and fields.get("uid") == alice:
            entered.set()
            assert release.wait(10)
        return a.host(operation, owner, **fields)
    a.server.routing.volumes.execute = host
    async def exercise():
        first = asyncio.create_task(a.server.routing.wake("alice"))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(a.server.routing.wake("bob"), 2)
            assert "wake:bob" in a.events and not first.done()
        finally:
            release.set()
            await first
    asyncio.run(exercise())


def test_host_has_no_capacity_decision_and_capacity_wait_has_no_host_rpc():
    root = Path(__file__).resolve().parents[1]
    host = (root / "tinyassets/boxes/owner_daemons.py").read_text()
    for removed in ("awake_cap", "idle_limit", "wake_timeout", "awake_count"):
        assert removed not in host
    tree = ast.parse((root / "tinyassets/broker/routing.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith) and any(
                ast.unparse(item.context_expr) == "self._capacity" for item in node.items):
            assert not {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} & {
                "host", "execute", "_launch"}


def test_erased_installed_resume_does_not_repeat_withdrawal(authority):
    a = authority
    async def exercise():
        result = await a.jobs.enqueue("alice")
        await a.server.routing.close()
        before = a.events.count("withdrawal_published")
        await a.jobs.resume_loaded_job(a.jobs.records.load(admission.digest("alice")))
        assert a.events.count("withdrawal_published") == before == 1
        assert result["completed_steps"] == ["withdrawal"]
    asyncio.run(exercise())
