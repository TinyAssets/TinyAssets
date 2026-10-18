"""Broker custody, tombstones, and startup layout locks fail closed."""

import fcntl
import json
import os
import sqlite3

import pytest

from tinyassets.boxes.layout import hold
from tinyassets.broker.box_admission import resolve, resolve_owner
from tinyassets.broker.owner_identities import OwnerIdentities


@pytest.fixture(autouse=True)
def initialized_ledger(tmp_path, monkeypatch):
    from tinyassets.erasure_ledger import ErasureLedger

    ErasureLedger(tmp_path).initialize()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


@pytest.fixture
def owner_box(tmp_path):
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    identities.plans.apply("alice", tier="paid", event_created=1)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", ("alice", uid, uid, 1024**3))
    return identities, uid


def test_owner_public_admission_needs_no_center_and_keeps_center_channels_scoped(owner_box):
    identities, uid = owner_box
    assert resolve_owner(identities, {"op": "BOX_PUBLIC", "principal": "alice"}) == {
        "op": "BOX_ADMITTED", "uid": uid, "quota": 1024**3}
    with sqlite3.connect(identities.path) as db:
        assert db.execute("SELECT COUNT(*) FROM center_admissions").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM owner_box_secrets").fetchone() == (0,)
    with pytest.raises(PermissionError):
        resolve(identities, {"op": "BOX_ADMISSION", "principal": "alice", "center": "a"})


@pytest.mark.parametrize("column,value", [
    ("machine", 300002), ("project_id", 300002), ("machine", None),
    ("bhard_bytes", 0), ("bhard_bytes", -1024), ("bhard_bytes", 1025),
    ("bhard_bytes", 1024.5), ("bhard_bytes", "unverified"),
])
def test_owner_public_admission_refuses_unverified_box_record(owner_box, column, value):
    identities, _ = owner_box
    with sqlite3.connect(identities.path) as db:
        db.execute(f"UPDATE box_records SET {column}=?", (value,))
    with pytest.raises(PermissionError, match="verified box registry record"):
        resolve_owner(identities, {"op": "BOX_PUBLIC", "principal": "alice"})


@pytest.mark.parametrize("document", [
    {"op": "BOX_PUBLIC", "principal": "alice", "center": "a"},
    {"op": "BOX_PUBLIC", "principal": "alice", "uid": 300001},
    {"op": "BOX_ADMISSION", "principal": "alice"},
    {"op": "BOX_PUBLIC"},
    {"op": "BOX_PUBLIC", "principal": ""},
])
def test_owner_public_admission_has_an_exact_owner_keyed_request(owner_box, document):
    identities, _ = owner_box
    with pytest.raises(ValueError):
        resolve_owner(identities, document)


def test_owner_public_admission_refuses_missing_identity_plan_or_schema(owner_box):
    identities, _ = owner_box
    request = {"op": "BOX_PUBLIC", "principal": "alice"}
    with pytest.raises(PermissionError):
        resolve_owner(None, request)
    with sqlite3.connect(identities.plans.path) as db:
        db.execute("DELETE FROM plans")
    with pytest.raises(LookupError):
        resolve_owner(identities, request)
    with sqlite3.connect(identities.path) as db:
        db.execute("DROP TABLE box_records")
    with pytest.raises(sqlite3.OperationalError):
        resolve_owner(identities, request)


def test_broker_admission_requires_permanent_mapping_live_center_and_migrated_quota(tmp_path):
    tmp_path.chmod(0o700)
    identities = OwnerIdentities(tmp_path / "identities.db", initialize=True)
    identity = identities.resolve("alice", allocate=True)
    identities.admission("admit", "alice", "a")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)",
                   ("alice", identity.uid, identity.uid, 1024**3))
    request = {"op": "BOX_ADMISSION", "principal": "alice", "center": "a"}
    assert resolve(identities, request) == {"op": "BOX_ADMITTED", "uid": identity.uid,
                                           "quota": 1024**3}
    with pytest.raises(PermissionError):
        resolve(identities, {**request, "principal": "bob"})
    with pytest.raises(ValueError):
        resolve(identities, {**request, "uid": identity.uid})
    with sqlite3.connect(identities.path) as db:
        db.execute("UPDATE box_records SET project_id=project_id+1")
    with pytest.raises(PermissionError):
        resolve(identities, request)
    identities.admission("retire", "alice", "a")
    with pytest.raises(PermissionError):
        resolve(identities, request)




def test_box_layout_lock_excludes_migration_for_service_lifetime(tmp_path):
    lock = tmp_path / ".layout.lock"
    lock.touch()
    marker = tmp_path / ".layout.json"
    marker.write_text(json.dumps({"layout": 2, "state": "stable", "box_layout": "owner-box"}))
    other = os.open(lock, os.O_RDONLY)
    try:
        with hold(tmp_path):
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(other)
    marker.write_text('{"layout":2,"state":"migrating","box_layout":"owner-box"}')
    with pytest.raises(RuntimeError, match="committed"):
        with hold(tmp_path):
            pytest.fail("incomplete layout admitted")


def test_new_owner_box_reservation_is_canonical_idempotent_and_fail_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.broker.box_admission import provision

    tmp_path.chmod(0o700)
    state = tmp_path / ".broker"
    state.mkdir(mode=0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True)
    uid = identities.resolve("alice", allocate=True).uid
    identities.admission("admit", "alice", "a")
    def no_owner_read(*args):
        raise AssertionError("broker provisioning must read only shared plans")

    monkeypatch.setattr("tinyassets.universe_owner.account_type_of", no_owner_read)
    monkeypatch.setattr("tinyassets.usage_policy.limits_for",
                        lambda tier: SimpleNamespace(storage_bytes=1024**3))
    # No startup schema creation, even after authenticated admission.
    with pytest.raises(sqlite3.OperationalError):
        provision(identities, tmp_path, "alice", "a")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT UNIQUE, "
                   "project_id INT UNIQUE, bhard_bytes INT)")
    provision(identities, tmp_path, "alice", "a")
    monkeypatch.setattr("tinyassets.usage_policy.limits_for",
                        lambda tier: SimpleNamespace(storage_bytes=1024))
    provision(identities, tmp_path, "alice", "a")
    assert resolve(identities, {"op": "BOX_ADMISSION", "principal": "alice", "center": "a"}) == {
        "op": "BOX_ADMITTED", "uid": uid, "quota": 1024**3}
    with pytest.raises(PermissionError):
        provision(identities, tmp_path, "bob", "a")
    with sqlite3.connect(identities.path) as db:
        db.execute("UPDATE box_records SET project_id=project_id+1")
    with pytest.raises(PermissionError, match="reservation changed"):
        provision(identities, tmp_path, "alice", "a")
