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
        db.execute("CREATE TABLE owner_box_revocations (principal TEXT PRIMARY KEY, deleted INT)")
    with sqlite3.connect(root / ".broker/credential-custody.db") as db:
        db.execute("CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY)")
    state = BoxHostState(root / ".boxhost/boxhost.db")
    state.begin("alice", "before-snapshot", "write", "digest1")
    state.finish("alice", "before-snapshot", {"response": "before"})
    state.bump_generation("alice")
    state.close()


@pytest.mark.parametrize("snapshot_has_journal", [True, False])
def test_restore_retains_all_seen_operations_as_unknown_and_advances_epochs(
        tmp_path, monkeypatch, snapshot_has_journal):
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxError, StaleHandle

    # Ownership is exercised by the real root/XFS rehearsal; this synthetic
    # receipt proof also runs as CI's unprivileged user.
    ownership = []
    monkeypatch.setattr(restore.os, "chown", lambda path, uid, gid: ownership.append((uid, gid)))
    current, staged = tmp_path / "current", tmp_path / "staged"
    live_trees, restored_trees = tmp_path / "live", tmp_path / "restored"
    seed(current)
    shutil.copytree(current, staged)
    for uid, owner in ((300001, "alice"), (300002, "bob")):
        tree = live_trees / str(uid)
        tree.mkdir(parents=True)
        (tree / "note").write_bytes(b"snapshot")
        if snapshot_has_journal:
            core = OwnerBoxd(owner_id=owner, owner_root=tree, state_dir=tree / ".owner-boxd")
            handle = core.bind(owner)
            core.write(handle, "before-snapshot", "/cc/note", b"snapshot", max_bytes=100)
            core.close()
    shutil.copytree(live_trees, restored_trees)
    old = {}
    for uid, owner in ((300001, "alice"), (300002, "bob")):
        tree = live_trees / str(uid)
        core = OwnerBoxd(owner_id=owner, owner_root=tree, state_dir=tree / ".owner-boxd")
        handle = core.bind(owner)
        core.write(handle, "after-snapshot", "/cc/note", owner.encode(), max_bytes=100)
        old[owner] = (handle, core._state.epoch(owner), core._state.generation(owner))
        core.close()
    shared_before = (staged / ".boxhost/boxhost.db").read_bytes()
    restore.fence_restore(current, staged, cutover_store=live_trees, restored_store=restored_trees)
    assert (staged / ".boxhost/boxhost.db").read_bytes() == shared_before
    assert {(300001, 300001), (300002, 300002)} <= set(ownership)
    for uid, owner in ((300001, "alice"), (300002, "bob")):
        tree = restored_trees / str(uid)
        for restart in range(2):
            core = OwnerBoxd(owner_id=owner, owner_root=tree, state_dir=tree / ".owner-boxd")
            try:
                assert core._state.epoch(owner) > old[owner][1]
                assert core._state.generation(owner) > old[owner][2]
                with pytest.raises(StaleHandle):
                    core.write(old[owner][0], "stale", "/cc/note", b"bad", max_bytes=100)
                handle = core.bind(owner)
                expected = b"snapshot" if restart == 0 else b"later work"
                assert (tree / "note").read_bytes() == expected
                for op_id, body in [("after-snapshot", owner.encode())] + (
                        [("before-snapshot", b"snapshot")] if snapshot_has_journal else []):
                    with pytest.raises(BoxError, match="not re-run"):
                        core.write(handle, op_id, "/cc/note", body, max_bytes=100)
                    with sqlite3.connect(tree / ".owner-boxd/boxhost.db") as db:
                        assert db.execute("SELECT state FROM ops WHERE op_id=?", (op_id,)
                                          ).fetchone() == ("unknown_after_restore",)
                    assert (tree / "note").read_bytes() == expected
                with pytest.raises(OpIdReuse):
                    core.write(handle, "after-snapshot", "/cc/note", b"changed", max_bytes=100)
                if restart == 0:
                    core.write(handle, "fresh-write", "/cc/note", b"later work", max_bytes=100)
            finally:
                core.close()


def test_restore_refuses_conflicting_owner_receipts_without_changing_target(tmp_path):
    current, staged = tmp_path / "current", tmp_path / "staged"
    for root, digest in ((current, "live"), (staged, "snapshot")):
        state = BoxHostState(root / "300001/.owner-boxd/boxhost.db")
        state.begin("alice", "same-id", "write", digest)
        state.finish("alice", "same-id", {"response": digest})
        state.close()
    target = staged / "300001/.owner-boxd/boxhost.db"
    before = target.read_bytes()
    with pytest.raises(ValueError, match="conflicting arguments"):
        restore.fence_owner_journals(current, staged)
    assert target.read_bytes() == before


@pytest.mark.parametrize("member", ["directory", "database", "sidecar"])
def test_restore_refuses_linked_owner_journals(tmp_path, member):
    current, staged = tmp_path / "current", tmp_path / "staged"
    journal = current / "300001/.owner-boxd"
    state = BoxHostState(journal / "boxhost.db")
    state.close()
    staged.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"unchanged")
    if member == "directory":
        journal.rename(journal.with_name("saved"))
        journal.symlink_to(journal.with_name("saved"), target_is_directory=True)
    else:
        path = journal / ("boxhost.db" if member == "database" else "boxhost.db-wal")
        path.unlink(missing_ok=True)
        path.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe owner journal"):
        restore.fence_owner_journals(current, staged)
    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize("authority", ["identity", "admission", "deletion", "destruction",
                                       "router_deletion", "revocation"])
def test_restore_refuses_authority_rollback_before_changing_staged_receipts(tmp_path, authority):
    current, staged = tmp_path / "current", tmp_path / "staged"
    seed(current)
    shutil.copytree(current, staged)
    if authority == "router_deletion":
        from tinyassets.router_credentials import DeletedOwners

        DeletedOwners(current).add("alice", {"unfinished_phases": ["owner_tree"]})
    elif authority == "destruction":
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
                "revocation": "INSERT INTO owner_box_revocations VALUES ('alice',1)",
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
