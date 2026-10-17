"""Offline restore must preserve identity, deletion and no-replay authority."""

import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tinyassets.boxes.provider import OpIdReuse
from tinyassets.boxes.state import BoxHostState

SPEC = importlib.util.spec_from_file_location(
    "box_store_quotas", Path(__file__).resolve().parents[1] / "deploy/box-store-quotas.py")
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)


def seed(root):
    (root / ".broker/state").mkdir(parents=True)
    (root / ".boxhost").mkdir(mode=0o700)
    (root / ".layout.json").write_text(json.dumps({"box_layout": "owner-box", "state": "stable"}))
    with sqlite3.connect(root / ".broker/state/owner-identities.db") as db:
        db.execute("CREATE TABLE owner_identities (principal TEXT, machine INT)")
        db.execute("INSERT INTO owner_identities VALUES ('alice', 300001)")
        db.execute("CREATE TABLE center_admissions (event TEXT, principal TEXT, center TEXT)")
        db.execute("INSERT INTO center_admissions VALUES ('admit', 'alice', 'a')")
        db.execute("CREATE TABLE box_records (machine INT, project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (300001,300001,1048576)")
    with sqlite3.connect(root / ".broker/credential-custody.db") as db:
        db.execute("CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY)")
    state = BoxHostState(root / ".boxhost/boxhost.db")
    state.begin("alice", "before-snapshot", "write", "digest1")
    state.finish("alice", "before-snapshot", {"response": "before"})
    state.bump_generation("alice")
    state.close()


def test_restore_retains_all_seen_operations_as_unknown_and_advances_epochs(tmp_path):
    current, staged = tmp_path / "current", tmp_path / "staged"
    seed(current)
    shutil.copytree(current, staged)
    state = BoxHostState(current / ".boxhost/boxhost.db")
    state.begin("alice", "after-snapshot", "write", "digest2")
    state.finish("alice", "after-snapshot", {"response": "after"})
    old_generation = state.bump_generation("alice")
    old_epoch = state.epoch("alice")
    state.close()
    restore.fence_restore(current, staged)
    restored = BoxHostState(staged / ".boxhost/boxhost.db")
    try:
        assert restored.epoch("alice") > old_epoch
        assert restored.generation("alice") > old_generation
        for op_id, digest in (("before-snapshot", "digest1"), ("after-snapshot", "digest2")):
            receipt = restored.begin("alice", op_id, "write", digest)
            assert receipt["state"] == "unknown_after_restore"
        with pytest.raises(OpIdReuse):
            restored.begin("alice", "after-snapshot", "write", "changed")
    finally:
        restored.close()


@pytest.mark.parametrize("authority", ["identity", "admission", "deletion", "destruction"])
def test_restore_refuses_authority_rollback_before_changing_staged_receipts(tmp_path, authority):
    current, staged = tmp_path / "current", tmp_path / "staged"
    seed(current)
    shutil.copytree(current, staged)
    if authority == "destruction":
        state = BoxHostState(current / ".boxhost/boxhost.db")
        state.begin_owner_destroy("alice", "erase", 300001, "a")
        state.close()
    else:
        database = (".broker/credential-custody.db" if authority == "deletion"
                    else ".broker/state/owner-identities.db")
        with sqlite3.connect(current / database) as db:
            db.execute({
                "identity": "INSERT INTO owner_identities VALUES ('bob',300002)",
                "admission": "INSERT INTO center_admissions VALUES ('retire','alice','a')",
                "deletion": "INSERT INTO deleted_principals VALUES ('owner-digest')",
            }[authority])
    before = (staged / ".boxhost/boxhost.db").read_bytes()
    with pytest.raises(ValueError, match="forward recovery"):
        restore.fence_restore(current, staged)
    assert (staged / ".boxhost/boxhost.db").read_bytes() == before


def test_quota_restore_allows_unmaterialized_reservations(tmp_path, monkeypatch):
    root, owners = tmp_path / "data", tmp_path / "owners"
    seed(root)
    (owners / "300001").mkdir(parents=True)
    database = root / ".broker/state/owner-identities.db"
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO box_records VALUES (300002,300002,1048576)")
    calls = []
    monkeypatch.setattr(restore.os, "chown", lambda *args: None)
    monkeypatch.setattr(restore.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(restore, "verify_owner", lambda *args: None)
    restore.reapply(owners, owners, database)
    assert any("limit -p bhard=1024k 300001" in call for call in calls)
    assert all("300002" not in " ".join(call) for call in calls)
    (owners / "300003").mkdir()
    calls.clear()
    with pytest.raises(ValueError, match="absent"):
        restore.reapply(owners, owners, database)
    assert not calls
