"""The owner-box move's irreversible boundary and paired backup proof."""

import hashlib
import os
import shutil
import socket
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.migration_volumes import install
from tinyassets import box_layout_migration as migration


def _root_migration(monkeypatch) -> None:
    install(monkeypatch)
    # Only the migration entry point impersonates root. Identity stores must
    # still validate the real uid that owns this unprivileged fixture.
    monkeypatch.setattr(migration, "os", SimpleNamespace(**(vars(os) | {"geteuid": lambda: 0})))


def _archive(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n")








def _cutover_root(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    store = tmp_path / "owners"
    (data / ".broker" / "state").mkdir(parents=True)
    (data / "c1").mkdir()
    (data / "c1" / "note.txt").write_text("owner content")
    (data / ".layout.json").write_text('{"layout": 2, "state": "stable", '
                                       '"box_layout": "owner-split", "split": "owner-split"}')
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
    _root_migration(monkeypatch)
    monkeypatch.setattr(migration, "_verified_pair", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "_mounted", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration, "_owner_plan", lambda *args: [
        migration.OwnerMove("alice", 300001, ("c1",), 1024**3),
        migration.OwnerMove("bob", 300002, ("c2",), 1024**3)])
    with pytest.raises(RuntimeError, match=r"cross-owner inode: .*note.txt.*borrowed.txt"):
        migration.apply(data, store, tmp_path / "snapshot", tmp_path / "boxes")
    assert (data / ".layout.json").read_bytes() == original
    assert not list(store.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="owner inode preflight requires Linux")
def test_cross_copy_and_external_hard_links_refuse(tmp_path):
    data, _ = _cutover_root(tmp_path)
    (data / "c2").mkdir()
    os.link(data / "c1/note.txt", data / "c2/borrowed.txt")
    plan = [migration.OwnerMove("alice", 300001, ("c1", "c2"), 1024**3)]
    with pytest.raises(RuntimeError, match="cross-copy hard link"):
        migration._owner_inode_preflight(data, plan)

    (data / "c2/borrowed.txt").unlink()
    os.link(data / "c1/note.txt", data / "outside.txt")
    with pytest.raises(RuntimeError, match="hard link leaves the move sources"):
        migration._owner_inode_preflight(data, plan)


@pytest.mark.skipif(os.name != "posix", reason="owner inode preflight requires Linux")
@pytest.mark.parametrize("kind", ["fifo", "socket"])
def test_special_owner_inodes_refuse_before_copy(tmp_path, kind):
    data, _ = _cutover_root(tmp_path)
    path = data / "c1" / kind
    if kind == "fifo":
        os.mkfifo(path)
        listener = None
    else:
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
    try:
        with pytest.raises(RuntimeError, match="unsupported owner inode"):
            migration._owner_inode_preflight(
                data, [migration.OwnerMove("alice", 300001, ("c1",), 1024**3)])
    finally:
        if listener is not None:
            listener.close()






def _identities(path: Path, principal: str, uid: int, center: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE owner_identities ("
            "principal TEXT PRIMARY KEY NOT NULL, "
            "machine_id INTEGER NOT NULL UNIQUE "
            "CHECK(machine_id BETWEEN 300001 AND 399999))"
        )
        db.execute(
            "CREATE TABLE center_admissions ("
            "generation INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event TEXT NOT NULL CHECK (event IN ('admit','retire')), "
            "principal TEXT NOT NULL, center TEXT NOT NULL, "
            "machine INTEGER NOT NULL CHECK(machine BETWEEN 300001 AND 399999), "
            "UNIQUE(center, event))"
        )
        db.execute(
            "INSERT INTO owner_identities (principal, machine_id) VALUES (?, ?)",
            (principal, uid),
        )
        db.execute(
            "INSERT INTO center_admissions (event, principal, center, machine) "
            "VALUES ('admit', ?, ?, ?)",
            (principal, center, uid),
        )






@pytest.mark.skipif(
    shutil.which("rsync") is None,
    reason="real migration copy needs rsync; owner=c6-owner-box runs-in=linux-ci",
)
def test_guest_copy_omits_persistent_credential_sources(tmp_path):
    source, guest = tmp_path / "center", tmp_path / "guest"
    source.mkdir()
    guest.mkdir()
    (source / "note.txt").write_text("owner content")
    (source / ".credential-vault.json").write_text("long-lived-token")
    (source / ".credentials" / "codex").mkdir(parents=True)
    (source / ".credentials" / "codex" / "auth.json").write_text("refresh-token")
    (source / ".credentials.json").write_text("other-token")
    (source / ".oauth-refresh").write_text("refresh-token")
    (source / ".runtime" / "provider-launch-credentials").mkdir(parents=True)
    (source / ".runtime" / "provider-launch-credentials" / "auth.json").write_text(
        "ephemeral-token")

    migration._copy(source, guest, os.getuid())

    assert (guest / "note.txt").read_text() == "owner content"
    assert all(not (guest / name).exists() for name in migration._CREDENTIAL_ENTRIES)
    assert not (guest / ".runtime").exists()






@pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0
                    or shutil.which("rsync") is None,
                    reason="special-bit ownership copy requires Linux root and rsync")
def test_real_copy_preserves_setgid_sticky_and_setuid_modes(tmp_path):
    source, guest = tmp_path / "center", tmp_path / "guest"
    source.mkdir()
    guest.mkdir()
    modes = {"setgid": 0o2750, "sticky": 0o1777, "setuid": 0o4750}
    for name in ("setgid", "sticky"):
        (source / name).mkdir()
        os.chmod(source / name, modes[name])
    (source / "setuid").write_text("fixture, never executed")
    os.chmod(source / "setuid", modes["setuid"])

    migration._copy(source, guest, 300001)

    for name, mode in modes.items():
        assert stat.S_IMODE((guest / name).stat().st_mode) == mode
        assert (guest / name).stat().st_uid == 300001


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T4')
