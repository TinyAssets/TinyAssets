"""The owner-box move's irreversible boundary and paired backup proof."""

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tinyassets import box_layout_migration as migration


def _archive(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")


def test_paired_snapshot_requires_both_real_digests(tmp_path):
    data = tmp_path / "tinyassets-snapshot-2026-10-14T00-00-00Z.tar.gz"
    box = tmp_path / "tinyassets-box-store-2026-10-14T00-00-00Z.tar.gz"
    _archive(data, b"data snapshot")
    _archive(box, b"box snapshot")
    migration._verified_pair(data, box)
    box.write_bytes(b"corrupted box snapshot")
    with pytest.raises(RuntimeError, match="digest mismatch"):
        migration._verified_pair(data, box)


def _cutover_root(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    store = tmp_path / "owners"
    (data / ".broker" / "state").mkdir(parents=True)
    (data / "c1").mkdir()
    (data / "c1" / "note.txt").write_text("owner content")
    (data / ".layout.json").write_text('{"layout": 2, "state": "stable", '
                                       '"box_layout": "owner-split"}')
    (data / ".layout.lock").touch()
    store.mkdir()
    sqlite3.connect(data / ".broker" / "state" / "owner-identities.db").close()
    return data, store


def test_cross_owner_inode_refuses_before_marker_or_destination_write(tmp_path, monkeypatch):
    import os

    data, store = _cutover_root(tmp_path)
    (data / "c2").mkdir()
    os.link(data / "c1/note.txt", data / "c2/borrowed.txt")
    original = (data / ".layout.json").read_bytes()
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration, "_verified_pair", lambda *args: None)
    monkeypatch.setattr(migration, "_mounted", lambda *args: None)
    monkeypatch.setattr(migration, "_owner_plan", lambda *args: [
        migration.OwnerMove("alice", 300001, ("c1",), 1024**3),
        migration.OwnerMove("bob", 300002, ("c2",), 1024**3)])
    with pytest.raises(RuntimeError, match=r"cross-owner inode: .*note.txt.*borrowed.txt"):
        migration.apply(data, store, tmp_path / "snapshot", tmp_path / "boxes")
    assert (data / ".layout.json").read_bytes() == original
    assert not list(store.iterdir())


@pytest.mark.parametrize("copy_fails", [False, True])
def test_apply_commits_only_after_copy_or_leaves_restore_marker(
    tmp_path, monkeypatch, copy_fails,
):
    data, store = _cutover_root(tmp_path)
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration.os, "chown", lambda *args: None)
    monkeypatch.setattr(migration, "_verified_pair", lambda *args: None)
    monkeypatch.setattr(migration, "_mounted", lambda *args: None)
    monkeypatch.setattr(
        migration, "_owner_plan",
        lambda *args: [migration.OwnerMove("person", 300001, ("c1",), 2 * 1024**3)],
    )
    monkeypatch.setattr(
        migration.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )

    def copy(source, target, uid):
        if copy_fails:
            raise OSError("copy failed after the move began")
        (target / "note.txt").write_bytes((source / "note.txt").read_bytes())

    monkeypatch.setattr(migration, "_copy", copy)
    arguments = (data, store, tmp_path / "data.tar.gz", tmp_path / "box.tar.gz")
    if copy_fails:
        with pytest.raises(OSError, match="copy failed"):
            migration.apply(*arguments)
    else:
        migration.apply(*arguments)
    marker = json.loads((data / ".layout.json").read_text())
    if copy_fails:
        assert marker["state"] == "migrating"
        assert not (data / ".broker" / "box-registry.json").exists()
    else:
        assert marker["state"] == "stable"
        assert marker["box_layout"] == "owner-box"
        assert (store / "300001" / "c1" / "note.txt").read_text() == "owner content"
        with sqlite3.connect(data / ".broker" / "state" / "owner-identities.db") as db:
            assert db.execute("SELECT principal, machine FROM box_records").fetchall() == [
                ("person", 300001)
            ]
    assert (data / "c1" / "note.txt").read_text() == "owner content"


def test_xfs_mount_accepts_real_prjquota_output(monkeypatch, tmp_path):
    def mount(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "xfs rw,relatime,prjquota\n", "")

    monkeypatch.setattr(migration.subprocess, "run", mount)
    migration._mounted(tmp_path)

    def discard(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 0, "xfs rw,relatime,prjquota,discard\n", ""
        )

    monkeypatch.setattr(migration.subprocess, "run", discard)
    with pytest.raises(RuntimeError, match="no discard"):
        migration._mounted(tmp_path)
