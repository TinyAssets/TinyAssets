"""C14: one broker lifecycle writer, local jobs, and no executor callbacks."""
import ast
import copy
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.broker import admission, box_admission
from tinyassets.broker.deletion_jobs import STEPS, DeletionJobs
from tinyassets.broker.owner_identities import OwnerIdentities


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
    identities.resolve("alice", allocate=True)
    identities.admission("admit", "alice", "alice")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    box_admission.provision(identities, tmp_path, "alice", "alice")
    events = []
    remote = Remote()
    server = SimpleNamespace(_owner_identities=identities, _data_root=tmp_path)

    def changed(owner):
        assert admission.read(identities, owner).state == "erased"
        assert remote.rows[admission.digest(owner)]
        events.extend(["channels_closed", "withdrawal_published"])

    class Projection:
        pass
    server.routing = Projection()
    server.routing.changed = changed
    admission.register_authority(identities.path, server.routing)
    original = admission.mutate

    def mutate(*args, **kwargs):
        events.append("mutation")
        return original(*args, **kwargs)

    monkeypatch.setattr(admission, "mutate", mutate)

    def host(operation, **fields):
        assert admission.read(identities, "alice").state == "erased"
        assert "withdrawal_published" in events
        assert fields == {"uid": identities.owner_machine("alice")}
        events.append(operation)
        return {"destroyed" if operation == "destroy" else "deleted": True}

    monkeypatch.setattr("tinyassets.boxes.daemon_control.request", host)
    jobs = DeletionJobs(server, records=remote)
    for step in ("custody", "billing", "credentials", "identity"):
        monkeypatch.setattr(DeletionJobs, "_" + step, lambda self, job: "done")
    return SimpleNamespace(jobs=jobs, remote=remote, identities=identities,
                           events=events, server=server)


def test_enqueue_and_status_never_read_remote_and_erasure_mutates_once(authority):
    a = authority
    result = a.jobs.enqueue("alice")
    assert result["status"] == "deleted"
    key, token = result["status_url"].split("/")[-2:]
    assert a.jobs.status(key, token)["completed_steps"] == list(STEPS)
    assert a.jobs.enqueue("alice")["status"] == "deleted"
    assert a.events.count("mutation") == 1
    assert a.events.index("mutation") < a.events.index("destroy")
    assert a.remote.reads == 0


def test_restart_resumes_local_job_when_remote_reads_are_unavailable(authority, monkeypatch):
    a = authority
    def fail(self, job):
        raise OSError("interrupted volume deletion")
    original = DeletionJobs._volume
    monkeypatch.setattr(DeletionJobs, "_volume", fail)
    assert a.jobs.enqueue("alice")["failed_step"] == "volume"
    monkeypatch.setattr(DeletionJobs, "_volume", original)
    restarted = DeletionJobs(a.server, records=a.remote, clock=lambda: 10**15)
    restarted.tick()
    job = restarted.records.load(admission.digest("alice"))
    assert job["completed_steps"] == list(STEPS)
    assert a.events.count("mutation") == 1
    assert a.remote.reads == 0


def test_historical_platform_copy_recovers_from_offhost_job(authority, tmp_path):
    a = authority
    backup = tmp_path / "old.db"
    with sqlite3.connect(a.identities.path) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    a.jobs.enqueue("alice")
    with sqlite3.connect(backup) as src, sqlite3.connect(a.identities.path) as dst:
        src.backup(dst)
    assert admission.read(a.identities, "alice").state == "admitted"
    recovered = DeletionJobs(a.server, records=a.remote)
    loaded, = a.remote.records()
    assert recovered.resume_loaded_job(loaded)["status"] == "deleted"
    assert admission.read(a.identities, "alice").state == "erased"
    assert recovered.records.load(loaded["id"])["completed_steps"] == list(STEPS)


def test_wake_and_deletion_share_the_broker_owner_lock(authority, monkeypatch):
    from tinyassets.broker.routing import RoutingAuthority

    a = authority
    routing = RoutingAuthority(a.server)
    entered, release = threading.Event(), threading.Event()
    routing.publish = lambda **kwargs: None
    routing.volumes.prepare = lambda owner, record: record
    original = __import__("tinyassets.boxes.daemon_control", fromlist=["request"]).request

    def host(operation, owner=None, **fields):
        if operation != "wake":
            return original(operation, **fields)
        entered.set()
        assert release.wait(10)
        assert admission.read(a.identities, owner).state == "admitted"
        return {"uid": fields["record"]["uid"]}

    monkeypatch.setattr("tinyassets.boxes.daemon_control.request", host)
    errors = []
    def run(function):
        try:
            function("alice")
        except BaseException as error:
            errors.append(error)
    wake = threading.Thread(target=run, args=(routing.wake,))
    deletion = threading.Thread(target=run, args=(a.jobs.enqueue,))
    wake.start()
    assert entered.wait(5)
    deletion.start()
    assert admission.read(a.identities, "alice").state == "admitted"
    release.set()
    wake.join(10)
    deletion.join(10)
    assert not wake.is_alive() and not deletion.is_alive() and not errors
    assert a.events.count("mutation") == 1
    with pytest.raises(PermissionError):
        routing.wake("alice")


def test_host_lifecycle_has_no_broker_state_callbacks():
    root = Path(__file__).resolve().parents[1]
    removed = {"BOX_ADMISSION_ERASE", "BOX_RETIRE_ADMISSION", "BOX_VOLUME",
               "BOX_VOLUME_PENDING", "BOX_LIFECYCLE", "RETIRE_OWNER", "ERASE_ACCOUNT",
               "CLOSE_OWNER_CHANNELS", "OWNER_ADMISSION_ERASE"}
    for relative in ("boxes/admission.py", "boxes/owner_daemons.py", "boxes/host_service.py",
                     "broker/server.py"):
        tree = ast.parse((root / "tinyassets" / relative).read_text())
        assert not {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)} & removed
    assert not (root / "tinyassets/boxes/volume_lifecycle.py").exists()
    assert not (root / "tinyassets/boxes/deletion_records.py").exists()


@pytest.mark.parametrize("action", ["sleep", "restore"])
def test_snapshot_and_restore_hold_owner_lock_until_map_commit(authority, monkeypatch, action):
    from tinyassets.broker import owner_volumes
    from tinyassets.broker.volume_lifecycle import VolumeLifecycle

    a = authority
    owner_volumes.dispatch(a.identities, {"op": "BOX_VOLUME", "principal": "alice",
        "action": "created", "arguments": {}})
    entered, release = threading.Event(), threading.Event()
    snapshot_id = "a" * 32
    def execute(operation, **fields):
        if operation in {"snapshot", "restore"}:
            entered.set()
            assert release.wait(10)
            assert admission.read(a.identities, "alice").state == "admitted"
            return ({"snapshot_id": fields["snapshot_id"], "sha256": "b" * 64}
                    if operation == "snapshot" else {"restored": True})
        raise AssertionError(operation)
    lifecycle = VolumeLifecycle(a.identities, execute)
    if action == "restore":
        with sqlite3.connect(a.identities.path) as db:
            db.execute("UPDATE owner_volumes SET snapshot_id=?,snapshot_sha256=?",
                       (snapshot_id, "b" * 64))
    errors = []
    def transition():
        try:
            if action == "sleep":
                lifecycle.sleep("alice")
            else:
                lifecycle.restore("alice", snapshot_id)
        except BaseException as error:
            errors.append(error)
    first = threading.Thread(target=transition)
    deleting = threading.Thread(target=lambda: a.jobs.enqueue("alice"))
    first.start()
    assert entered.wait(5)
    deleting.start()
    assert admission.read(a.identities, "alice").state == "admitted"
    release.set()
    first.join(10)
    deleting.join(10)
    assert not first.is_alive() and not deleting.is_alive() and not errors
    assert a.events.count("mutation") == 1
    assert admission.read(a.identities, "alice").state == "erased"


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
