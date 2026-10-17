"""Broker custody, tombstones, and startup layout locks fail closed."""

import fcntl
import json
import os
import sqlite3

import pytest

from tinyassets.boxes.layout import hold
from tinyassets.broker.box_admission import resolve
from tinyassets.broker.owner_identities import OwnerIdentities


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
