"""The owner-box move's irreversible boundary and paired backup proof."""

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from tinyassets import box_layout_migration as migration
from tinyassets.usage_policy import AccountType, limits_for


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


@pytest.mark.parametrize("nested", ["/data/c1/payload.txt", "/data/c1/nested"])
def test_mountinfo_refuses_file_and_directory_bind_mounts(tmp_path, nested):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "10 1 8:1 / /data rw - xfs /dev/loop0 rw\n"
        f"11 10 8:1 /source {nested} rw - xfs /dev/loop0 rw\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match=f"mount beneath owner data root: {nested}"):
        migration._refuse_nested_mounts(PurePosixPath("/data"), mountinfo)


def test_mountinfo_accepts_clean_tree(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "10 1 8:1 / /data rw - xfs /dev/loop0 rw\n"
        "11 1 8:1 / /data-other rw - xfs /dev/loop0 rw\n",
        encoding="utf-8",
    )
    migration._refuse_nested_mounts(PurePosixPath("/data"), mountinfo)


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


@pytest.mark.parametrize("refusal", ["store", "mount", "inode", "quota"])
def test_read_only_preflight_refuses_without_changing_the_layout(tmp_path, monkeypatch, refusal):
    from tinyassets import activity_shape_migration as activity_shape

    data, store = _cutover_root(tmp_path)
    original = (data / ".layout.json").read_bytes()
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration, "_owner_plan", lambda _, **_kwargs: [
        migration.OwnerMove("alice", 300001, ("c1",), 1024**3)])
    monkeypatch.setattr(activity_shape, "_pending", lambda _: [])
    monkeypatch.setattr(migration, "_mounted", lambda _: None)
    monkeypatch.setattr(migration, "_owner_inode_preflight", lambda *_: None)
    monkeypatch.setattr(migration, "_xfs_quota", lambda *_: "project report")
    if refusal == "store":
        (store / "300001").mkdir()
        reason = "box store must be empty"
    elif refusal == "mount":
        monkeypatch.setattr(migration, "_mounted", lambda _: (_ for _ in ()).throw(
            RuntimeError("XFS project quota missing")))
        reason = "XFS project quota missing"
    elif refusal == "inode":
        monkeypatch.setattr(migration, "_owner_inode_preflight", lambda *_: (
            _ for _ in ()).throw(RuntimeError("unsupported owner inode")))
        reason = "unsupported owner inode"
    else:
        monkeypatch.setattr(migration, "_xfs_quota", lambda *_: "")
        reason = "project report unavailable"
    with pytest.raises(RuntimeError, match=reason):
        migration.preflight(data, store)
    assert (data / ".layout.json").read_bytes() == original
    assert not (store / "300002").exists()


def test_read_only_preflight_accepts_clean_owner_split(tmp_path, monkeypatch):
    from tinyassets import activity_shape_migration as activity_shape

    data, store = _cutover_root(tmp_path)
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration, "_mounted", lambda _: None)
    monkeypatch.setattr(migration, "_owner_plan", lambda _, **_kwargs: [
        migration.OwnerMove("alice", 300001, ("c1",), 1024**3)])
    monkeypatch.setattr(activity_shape, "_pending", lambda _: [])
    monkeypatch.setattr(migration, "_owner_inode_preflight", lambda *_: None)
    monkeypatch.setattr(migration, "_xfs_quota", lambda *_: "project report")
    migration.preflight(data, store)
    assert not list(store.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="read-only WAL exercise runs on Linux")
@pytest.mark.parametrize("live_writer", [False, True])
def test_preflight_reads_wal_without_writing_data(tmp_path, monkeypatch, live_writer):
    data, store = _cutover_root(tmp_path)
    identity = data / ".broker/state/owner-identities.db"
    author = data / ".tinyassets.db"
    tier = data / "c1/.subscription_state.db"
    statements = {
        identity: (
            "CREATE TABLE owner_identities (principal TEXT, machine_id INTEGER);"
            "CREATE TABLE center_admissions (generation INTEGER, event TEXT, "
            "principal TEXT, center TEXT, machine INTEGER);"
            "INSERT INTO owner_identities VALUES ('alice', 300001);"
            "INSERT INTO center_admissions VALUES (1, 'admit', 'alice', 'c1', 300001);"
        ),
        author: (
            "CREATE TABLE founder_home (founder_sub TEXT, universe_id TEXT);"
            "CREATE TABLE branch_definitions (branch_def_id TEXT);"
            "INSERT INTO founder_home VALUES ('alice', 'c1');"
        ),
        tier: (
            "CREATE TABLE subscription_meta (key TEXT, value TEXT);"
            "INSERT INTO subscription_meta VALUES ('tier', 'paid');"
        ),
    }
    writer = None
    try:
        if live_writer:
            child = (
                "import sqlite3, sys; connections=[]; "
                "\nfor index in range(1, len(sys.argv), 2):\n"
                " db=sqlite3.connect(sys.argv[index]); "
                "db.execute('PRAGMA journal_mode=WAL'); "
                "db.execute('PRAGMA wal_autocheckpoint=0'); "
                "db.executescript(sys.argv[index+1]); connections.append(db)\n"
                "print('ready', flush=True); sys.stdin.readline()"
            )
            arguments = [item for path, sql in statements.items() for item in (str(path), sql)]
            writer = subprocess.Popen(  # noqa: S603 -- isolated local SQLite fixture
                [sys.executable, "-u", "-c", child, *arguments],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            assert writer.stdout is not None
            assert writer.stdout.readline() == "ready\n"
            for path in statements:
                assert path.with_name(path.name + "-shm").is_file()
        else:
            for path, sql in statements.items():
                # Abrupt exit leaves committed WAL frames; remove the stale index
                # as on a quiescent archive restored without its -shm sidecar.
                child = (
                    "import os, sqlite3, sys; "
                    "db=sqlite3.connect(sys.argv[1]); "
                    "db.execute('PRAGMA journal_mode=WAL'); "
                    "db.execute('PRAGMA wal_autocheckpoint=0'); "
                    "db.executescript(sys.argv[2]); os._exit(0)"
                )
                subprocess.run([sys.executable, "-c", child, str(path), sql], check=True)
                path.with_name(path.name + "-shm").unlink()
                assert path.with_name(path.name + "-wal").is_file()
        original = {p.relative_to(data): p.read_bytes() for p in data.rglob("*") if p.is_file()}
        modes = {p: stat.S_IMODE(p.stat().st_mode) for p in (data, *data.rglob("*"))}
        for path in modes:
            path.chmod(0o444 if path.is_file() else 0o555)
        real_lstat = Path.lstat

        def owner_lstat(path):
            info = real_lstat(path)
            if path == data / "c1":
                return os.stat_result((
                    info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                    1001, 300001, info.st_size, info.st_atime, info.st_mtime, info.st_ctime,
                ))
            return info

        monkeypatch.setattr(Path, "lstat", owner_lstat)
        monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
        monkeypatch.setattr(migration, "_mounted", lambda _: None)
        monkeypatch.setattr(migration, "_owner_inode_preflight", lambda *_: None)
        monkeypatch.setattr(migration, "_xfs_quota", lambda *_: "project report")
        plan = migration._owner_plan(data, readonly=True)
        assert plan[0].quota == limits_for(AccountType.SUBSCRIPTION).storage_bytes
        migration.preflight(data, store)
        after = {p.relative_to(data): p.read_bytes() for p in data.rglob("*") if p.is_file()}
        assert after == original
    finally:
        if "modes" in locals():
            for path, mode in modes.items():
                path.chmod(mode)
        if writer is not None:
            writer.communicate(input="\n", timeout=10)


def test_preflight_refuses_unreadable_founder_home(tmp_path):
    data, _ = _cutover_root(tmp_path)
    from tinyassets.sqlite_readonly import readonly_db
    from tinyassets.universe_owner import account_type_of

    with pytest.raises(FileNotFoundError):
        account_type_of(data, "alice", db_reader=readonly_db)


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
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    monkeypatch.setattr(migration, "_xfs_quota", lambda _store, command:
                        f"Setting up project {command.rsplit(' ', 1)[-1]} "
                        if command.startswith("project ") else "project report")
    monkeypatch.setattr(migration, "verify_tree", lambda *args: None)
    monkeypatch.setattr(migration, "verify_owner", lambda *args: None)

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


def test_census_reports_each_layout_and_does_not_invent_a_zero(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "center-a").mkdir()
    code = migration.check(data)
    census = migration.layout_census(data)
    assert code == migration.ABSENT
    assert census["box_layout"] == "absent"
    assert census["pending"] is None
    assert census["counts"]["owner-split"]["centers"] == 1
    assert census["counts"]["owner-split"]["admitted_owners"] is None
    assert census["identity_error"] == "identity database absent"
    assert census["counts"]["owner-box"]["registry_present"] is False

    (data / ".layout.json").write_text('{"layout": 2, "state": "stable"}')
    assert migration.check(data) == migration.PENDING
    (data / ".layout.json").write_text(
        '{"layout": 2, "state": "stable", "box_layout": "owner-box"}')
    assert migration.check(data) == 0
    (data / ".layout.json").write_text("{")
    assert migration.check(data) == migration.UNREADABLE


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


def test_owner_split_fixture_census_moves_old_layout_and_refuses_absent_or_ambiguous(
    tmp_path, monkeypatch,
):
    """A real owner-split tree is detected and moved from the census.

    The agent-files binding count on this tree is zero. Absent and ambiguous
    censuses are not a skip: check refuses them instead of reporting owner-box.
    """
    from tinyassets.agent_file_migration import pending

    data = tmp_path / "data"
    store = tmp_path / "owners"
    center = data / "home"
    center.mkdir(parents=True)
    (center / "note.txt").write_text("owner sentence")
    (data / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "stable", "box_layout": "owner-split",
    }))
    (data / ".layout.lock").touch()
    store.mkdir()
    _identities(
        data / ".broker" / "state" / "owner-identities.db", "person", 300001, "home",
    )
    assert pending(data) == 0

    code = migration.check(data)
    census = migration.layout_census(data)
    assert code == migration.PENDING
    assert census["pending"] is True
    assert census["box_layout"] == "owner-split"
    assert census["marker_state"] == "stable"
    assert census["counts"]["owner-split"]["centers"] == 1
    assert census["counts"]["owner-split"]["admitted_owners"] == 1
    assert census["counts"]["owner-box"]["registry_present"] is False

    absent = tmp_path / "absent"
    (absent / "home").mkdir(parents=True)
    assert migration.check(absent) == migration.ABSENT
    assert migration.layout_census(absent)["pending"] is None
    assert migration.layout_census(absent)["box_layout"] == "absent"

    ambiguous = tmp_path / "ambiguous"
    ambiguous.mkdir()
    (ambiguous / ".layout.json").write_text("{")
    assert migration.check(ambiguous) == migration.UNREADABLE
    assert migration.layout_census(ambiguous)["pending"] is None
    (ambiguous / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "migrating", "box_layout": "owner-split",
    }))
    assert migration.check(ambiguous) == migration.UNREADABLE
    (ambiguous / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "stable", "box_layout": "neither",
    }))
    assert migration.check(ambiguous) == migration.UNREADABLE

    real_stat = os.stat

    def stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        # fwalk and the inode preflight stat names through dir_fd, so path is
        # an int. Only the center directory's path stat needs the owner ids.
        if isinstance(path, int) or not isinstance(path, (str, os.PathLike)):
            return info
        target = Path(path)
        if target.name == "home" and target.parent == data:
            return os.stat_result((
                info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                1001, 300001, info.st_size, info.st_atime, info.st_mtime, info.st_ctime,
            ))
        return info

    def run(command, *args, **kwargs):
        argv = list(command)
        if argv and argv[0] == "rsync" and "--dry-run" not in argv:
            source = Path(argv[-2])
            target = Path(argv[-1])
            for item in source.iterdir():
                dest = target / item.name
                if item.is_dir() and not item.is_symlink():
                    shutil.copytree(item, dest)
                elif item.is_file():
                    dest.write_bytes(item.read_bytes())
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "chown", lambda *args: None)
    monkeypatch.setattr(migration.os, "geteuid", lambda: 0)
    monkeypatch.setattr(migration, "_mounted", lambda *args: None)
    monkeypatch.setattr(migration.subprocess, "run", run)
    monkeypatch.setattr(migration, "_xfs_quota", lambda _store, command:
                        f"Setting up project {command.rsplit(' ', 1)[-1]} "
                        if command.startswith("project ") else "project report")
    monkeypatch.setattr(migration, "verify_tree", lambda *args: None)
    monkeypatch.setattr(migration, "verify_owner", lambda *args: None)
    from tinyassets import activity_shape_migration as activity_shape

    monkeypatch.setattr(activity_shape, "_pending", lambda _: ["home"])

    def convert_before_copy(_data, roots):
        assert roots == {"home": data / "home"}
        (roots["home"] / "Activities.md").write_text("Owner activity instructions")

    monkeypatch.setattr(activity_shape, "convert", convert_before_copy)
    data_backup = tmp_path / "tinyassets-snapshot-2026-10-15T00-00-00Z.tar.gz"
    box_backup = tmp_path / "tinyassets-box-store-2026-10-15T00-00-00Z.tar.gz"
    _archive(data_backup, b"data snapshot")
    _archive(box_backup, b"box snapshot")

    migration.apply(data, store, data_backup, box_backup)

    assert (store / "300001" / "home" / "note.txt").read_text() == "owner sentence"
    assert (store / "300001" / "home" / "Activities.md").read_text() == (
        "Owner activity instructions")
    assert (data / "home" / "note.txt").read_text() == "owner sentence"
    marker = json.loads((data / ".layout.json").read_text())
    assert marker["box_layout"] == "owner-box"
    assert marker["state"] == "stable"
    assert marker["activity_shape"] == "v1"
    moved = migration.layout_census(data)
    assert moved["box_layout"] == "owner-box"
    assert moved["pending"] is False
    assert migration.check(data) == 0
    with sqlite3.connect(data / ".broker" / "state" / "owner-identities.db") as db:
        assert db.execute(
            "SELECT principal, machine, project_id FROM box_records"
        ).fetchall() == [("person", 300001, 300001)]


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


def test_xfs_quota_refuses_zero_exit_with_mount_error(monkeypatch, tmp_path):
    def failed_setup(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "", "xfs_quota: cannot setup path for mount")

    monkeypatch.setattr(migration.subprocess, "run", failed_setup)
    with pytest.raises(RuntimeError, match="cannot setup path for mount"):
        migration._xfs_quota(tmp_path, "project -s -p /box-store/300001 300001")


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


@pytest.mark.skipif(
    shutil.which("rsync") is None,
    reason="real migration copy needs rsync; owner=c6-owner-box runs-in=linux-ci",
)
def test_guest_agent_home_omits_package_auth_files(tmp_path):
    from tinyassets.external_agents.registry import PACKAGES

    source, guest = tmp_path / "agent-home", tmp_path / "guest"
    (source / "codex").mkdir(parents=True)
    guest.mkdir()
    (source / "codex" / "auth.json").write_text("old refresh token")
    (source / "codex" / "sessions.json").write_text("session state")
    paths = tuple(f"{package.row['auth']['home_dir']}/{name}"
                  for package in PACKAGES.values()
                  for name in package.row["auth"]["files"])

    migration._copy(source, guest, os.getuid(), extra_excludes=paths)

    assert not (guest / "codex" / "auth.json").exists()
    assert (guest / "codex" / "sessions.json").read_text() == "session state"


@pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0
                    or shutil.which("rsync") is None,
                    reason="private ownership proof requires Linux root and rsync")
def test_real_copy_places_vault_only_in_platform_custody(tmp_path):
    source, guest, private = (tmp_path / name for name in ("center", "guest", "private"))
    source.mkdir()
    guest.mkdir()
    (source / ".credential-vault.json").write_text("long-lived-refresh-token")
    (source / ".credentials" / "codex").mkdir(parents=True)
    (source / ".credentials" / "codex" / "auth.json").write_text("old-token")
    (source / "note.txt").write_text("owner content")

    migration._copy_credentials(source, private)
    migration._copy(source, guest, 300001)

    assert (private / ".credential-vault.json").read_text() == "long-lived-refresh-token"
    assert (private / ".credentials" / "codex" / "auth.json").read_text() == "old-token"
    assert stat.S_IMODE(private.stat().st_mode) == 0o710
    assert stat.S_IMODE((private / ".credential-vault.json").stat().st_mode) == 0o640
    assert not (guest / ".credential-vault.json").exists()
    assert not (guest / ".credentials").exists()
    assert (guest / "note.txt").read_text() == "owner content"


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


def test_private_copy_selects_only_credential_sources(tmp_path, monkeypatch):
    source, private = tmp_path / "center", tmp_path / "private"
    source.mkdir()
    (source / "note.txt").write_text("owner content")
    (source / ".credential-vault.json").write_text("token")
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(migration.subprocess, "run", run)
    monkeypatch.setattr(migration.os, "chown", lambda *args: None)
    migration._copy_credentials(source, private)

    assert len(commands) == 2
    assert "--exclude=*" in commands[0]
    assert "--include=/.credential-vault.json" in commands[0]
    assert "--include=/.credentials/***" in commands[0]
    assert all("note.txt" not in word for word in commands[0])
