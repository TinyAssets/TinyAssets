"""Broker custody, tombstones, and startup layout locks fail closed."""

import fcntl
import hashlib
import json
import os
import sqlite3

import pytest

from tinyassets.boxes.admission import Admission
from tinyassets.boxes.layout import hold
from tinyassets.boxes.provider import BoxAuthError
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


def test_tombstone_is_required_for_destroy_and_checked_again_after_binding(tmp_path, monkeypatch):
    admission = Admission(tmp_path, tmp_path / "broker.sock")
    monkeypatch.setattr(admission, "record", lambda *args: {"uid": 300001})
    with pytest.raises(sqlite3.Error):
        admission.grant("alice", "a", "session")
    with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
        db.execute("CREATE TABLE deleted_principals (founder_sub TEXT)")
    grant = admission.grant("alice", "a", "session")
    with pytest.raises(BoxAuthError):
        admission.grant("alice", "a", "delete", destroying=True)
    with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
        db.execute("INSERT INTO deleted_principals VALUES (?)",
                   (hashlib.sha256(b"alice").hexdigest(),))
    with pytest.raises(BoxAuthError):
        admission.authorize(grant, "a")
    assert admission.authorize(grant, "a", destroying=True)


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
