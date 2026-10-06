"""Real pre-drop Linux migration proofs; run with linux_oracle --as-root."""

import json
import os
import stat

import pytest

from deploy.role_owner_migration import MigrationRefused, migrate


@pytest.fixture
def volume(tmp_path):
    assert os.name == "posix" and os.geteuid() == 0, "requires Linux pre-drop oracle"
    tmp_path.parent.chmod(0o755)
    tmp_path.chmod(0o755)
    root = tmp_path / "data"
    root.mkdir()
    (root / ".layout.lock").touch()
    (root / ".layout.json").write_text(
        json.dumps(
            {"layout": 2, "state": "stable", "moves": {"consents_outside_command_centers": "done"}}
        )
    )
    for center in ("alice", "bob"):
        tree = root / center
        (tree / "work" / ".venv").mkdir(parents=True)
        (tree / "work" / "payload").write_bytes(center.encode())
        (tree / "work" / "execute").write_bytes(b"executable")
        (tree / "work" / "execute").chmod(0o755)
        (tree / ".credential-vault.json").write_bytes(b"protected")
        (tree / "work" / ".venv" / "python").symlink_to("/outside/sentinel")
    for parent, directories, files in os.walk(root):
        os.chown(parent, 1001, 1001)
        for name in files:
            os.chown(os.path.join(parent, name), 1001, 1001, follow_symlinks=False)
    return root


def run(root, **kwargs):
    return migrate(
        root,
        bindings={"alice": 300001, "bob": 300002},
        work={"alice": ["work"], "bob": ["work"]},
        **kwargs,
    )


def metadata(root):
    result = {}

    def walk(fd, path):
        info = os.fstat(fd)
        result[str(path)] = (
            info.st_ino,
            info.st_uid,
            info.st_gid,
            info.st_mode,
            info.st_atime_ns,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        for name in os.listdir(fd):
            child = path / name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            result[str(child)] = (
                info.st_ino,
                info.st_uid,
                info.st_gid,
                info.st_mode,
                info.st_atime_ns,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            if stat.S_ISDIR(info.st_mode):
                opened = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=fd
                )
                try:
                    walk(opened, child)
                finally:
                    os.close(opened)

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        walk(fd, root)
    finally:
        os.close(fd)
    return result


def test_dry_run_apply_repeat_reverse_preserves_data_and_protected_entries(volume):
    before = metadata(volume)
    run(volume, dry_run=True)
    assert metadata(volume) == before
    protected = (volume / "alice/.credential-vault.json").stat()
    result = run(volume)
    assert result["changed"] > 0
    target = volume / "alice/work/payload"
    assert (target.stat().st_uid, target.stat().st_gid) == (300001, 300001)
    assert (volume / "alice").stat().st_uid == 1001
    assert (volume / "alice").stat().st_gid == 300001
    assert (volume / "alice/work/execute").stat().st_mode & stat.S_IXUSR
    assert os.readlink(volume / "alice/work/.venv/python") == "/outside/sentinel"
    assert (volume / "alice/.credential-vault.json").stat() == protected
    stable = metadata(volume)
    assert run(volume)["changed"] == 0
    assert metadata(volume) == stable
    assert target.read_bytes() == b"alice"
    restricted = volume / "alice/work/restrictive"
    restricted.mkdir(mode=0o700)
    (restricted / "private").write_bytes(b"engine-created")
    os.chown(restricted, 300001, 300001)
    os.chown(restricted / "private", 300001, 300001)
    (restricted / "private").chmod(0o600)
    before = metadata(volume)
    run(volume, reverse=True, dry_run=True)
    assert metadata(volume) == before
    run(volume, reverse=True)
    assert (restricted / "private").stat().st_uid == 1001
    stable = metadata(volume)
    assert run(volume, reverse=True)["changed"] == 0
    assert metadata(volume) == stable
    assert (restricted / "private").read_bytes() == b"engine-created"


def test_quarantine_keeps_every_name_inode_and_bytes_and_never_assigns_to_owner(volume):
    source = volume / "alice/work/payload"
    os.link(source, volume / "bob/work/alias")
    original = source.stat()
    result = run(volume)
    assert result["quarantined"] == ["alice/work/payload", "bob/work/alias"]
    quarantine = volume / ".role-owner-migration/quarantine"
    for path in result["quarantined"]:
        assert not (volume / path).exists()
        stored = quarantine / path
        assert stored.stat().st_ino == original.st_ino
        assert stored.stat().st_uid == 1001
        assert stored.read_bytes() == b"alice"
    stable = metadata(volume)
    assert run(volume)["changed"] == 0
    assert metadata(volume) == stable
    run(volume, reverse=True)
    assert (quarantine / "alice/work/payload").read_bytes() == b"alice"
    assert not source.exists()


@pytest.mark.parametrize(
    "step", ["journal", "marker", "quarantine-name", "ownership", "complete-journal"]
)
def test_interruption_resumes_without_lost_names(volume, step):
    os.link(volume / "alice/work/payload", volume / "bob/work/alias")

    def crash(point):
        if point == step:
            raise InterruptedError(point)

    with pytest.raises(InterruptedError):
        run(volume, after_step=crash)
    result = run(volume)
    assert len(result["quarantined"]) == 2
    assert run(volume)["changed"] == 0
    assert (volume / ".role-owner-migration/quarantine/bob/work/alias").read_bytes() == b"alice"


@pytest.mark.parametrize("case", ["unseen", "foreign", "protected", "fifo", "root-link"])
def test_unresolved_inputs_refuse_before_any_metadata_mutation(volume, case):
    target = volume / "alice/work/payload"
    if case == "unseen":
        os.link(target, volume / "outside")
    elif case == "foreign":
        os.chown(target, 300002, 300002)
    elif case == "protected":
        os.link(volume / "alice/.credential-vault.json", volume / "bob/work/alias")
    elif case == "fifo":
        os.mkfifo(volume / "alice/work/pipe")
        os.chown(volume / "alice/work/pipe", 1001, 1001)
    else:
        (volume / "alice").rename(volume / "original")
        (volume / "alice").symlink_to(volume / "original", target_is_directory=True)
    before = metadata(volume)
    with pytest.raises((MigrationRefused, OSError)):
        run(volume)
    assert metadata(volume) == before


def test_same_tree_hardlinks_preserve_relationship_and_bytes(volume):
    target = volume / "alice/work/payload"
    os.link(target, volume / "alice/work/alias")
    run(volume)
    assert target.stat().st_ino == (volume / "alice/work/alias").stat().st_ino
    assert target.stat().st_uid == 300001
    run(volume, reverse=True)
    assert target.stat().st_uid == 1001
    assert target.read_bytes() == b"alice"


@pytest.mark.parametrize("step", ["journal", "marker", "ownership", "complete-journal"])
def test_reverse_crash_resumes_and_does_not_drop_quarantine_alarm(volume, step):
    os.link(volume / "alice/work/payload", volume / "bob/work/alias")
    run(volume)

    def crash(point):
        if point == step:
            raise InterruptedError(point)

    with pytest.raises(InterruptedError):
        run(volume, reverse=True, after_step=crash)
    report = run(volume, reverse=True)
    assert report["quarantined"] == ["alice/work/payload", "bob/work/alias"]
    assert (volume / "bob/work/execute").stat().st_uid == 1001
    assert run(volume, reverse=True)["changed"] == 0


def test_resumed_journal_refuses_added_names_and_changed_binding(volume):
    def crash(point):
        if point == "journal":
            raise InterruptedError(point)

    with pytest.raises(InterruptedError):
        run(volume, after_step=crash)
    with pytest.raises(MigrationRefused, match="bindings/classification"):
        migrate(
            volume,
            bindings={"alice": 300002, "bob": 300001},
            work={"alice": ["work"], "bob": ["work"]},
        )
    (volume / "alice/work/added").write_bytes(b"new unexpected writer")
    os.chown(volume / "alice/work/added", 1001, 1001)
    before = metadata(volume)
    with pytest.raises(MigrationRefused, match="namespace changed"):
        run(volume)
    assert metadata(volume) == before


def test_real_identities_get_own_access_foreign_denial_quarantine_denial_and_rollback(volume):
    import traceback

    from deploy.role_owner_migration import ACCESS, DEFAULT, _acl

    directory = volume / "alice/work"
    os.setxattr(directory, ACCESS, _acl(7, {300002: 7}))
    os.setxattr(directory, DEFAULT, _acl(7, {300002: 7}))
    os.link(volume / "alice/work/payload", volume / "bob/work/alias")
    run(volume)

    def child(uid, action):
        pid = os.fork()
        if pid == 0:
            try:
                os.setgroups([])
                os.setresgid(uid, uid, uid)
                os.setresuid(uid, uid, uid)
                action()
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        assert os.waitpid(pid, 0)[1] == 0

    def denied(path):
        with pytest.raises(PermissionError):
            path.read_bytes()

    def alice():
        assert (directory / "execute").read_bytes() == b"executable"
        denied(volume / "bob/work/payload")
        denied(volume / "alice/.credential-vault.json")
        denied(volume / ".role-owner-migration/quarantine/alice/work/payload")
        restricted = directory / "engine"
        restricted.mkdir(mode=0o700)
        (restricted / "private").write_bytes(b"engine")
        (restricted / "private").chmod(0o600)

    # Legacy vault permissions are a separate protected-metadata substep.
    (volume / "alice/.credential-vault.json").chmod(0o600)
    child(300001, alice)
    child(300002, lambda: denied(directory / "execute"))
    child(1001, lambda: denied(volume / ".role-owner-migration/quarantine/bob/work/alias"))
    run(volume, reverse=True)

    def old_daemon():
        path = directory / "engine/private"
        assert path.read_bytes() == b"engine"
        path.write_bytes(b"old-image-writable")
        path.unlink()
        path.parent.rmdir()

    child(1001, old_daemon)


@pytest.mark.parametrize("boundary", ["chown", "access-acl", "default-acl"])
def test_crash_inside_metadata_transition_resumes(volume, monkeypatch, boundary):
    from deploy import role_owner_migration as migration

    target = volume / "alice/work"
    inode = target.stat().st_ino
    fired = False
    chown = os.fchown
    xattr = os.setxattr

    def fail(fd, point):
        nonlocal fired
        if point == boundary and os.fstat(fd).st_ino == inode and not fired:
            fired = True
            raise InterruptedError(point)

    def interrupted_chown(fd, uid, gid):
        chown(fd, uid, gid)
        fail(fd, "chown")

    def interrupted_acl(fd, name, value, *args, **kwargs):
        xattr(fd, name, value, *args, **kwargs)
        fail(fd, "access-acl" if name == migration.ACCESS else "default-acl")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fchown", interrupted_chown)
        patch.setattr(os, "setxattr", interrupted_acl)
        with pytest.raises(InterruptedError):
            run(volume)
    assert fired
    run(volume)
    assert target.stat().st_uid == 300001
    assert run(volume)["changed"] == 0


def test_interrupted_resume_refuses_foreign_relabel_before_mutation(volume):
    def crash(point):
        if point == "journal":
            raise InterruptedError(point)

    with pytest.raises(InterruptedError):
        run(volume, after_step=crash)
    target = volume / "alice/work/payload"
    os.chown(target, 300002, 300002)
    before = metadata(volume)
    with pytest.raises(MigrationRefused, match="identity changed"):
        run(volume)
    assert metadata(volume) == before
    assert target.stat().st_uid == 300002


def test_completed_migration_reinventories_legitimate_engine_writes_on_restart(volume):
    run(volume)
    target = volume / "alice/work/new"
    target.write_bytes(b"engine new file")
    os.chown(target, 300001, 300001)
    target.chmod(0o600)
    run(volume)
    assert target.stat().st_uid == 300001
    assert run(volume)["changed"] == 0
    assert target.read_bytes() == b"engine new file"


def test_new_own_file_can_reuse_a_quarantined_name_without_releasing_escrow(volume):
    target = volume / "alice/work/payload"
    os.link(target, volume / "bob/work/alias")
    run(volume)
    target.write_bytes(b"new legitimate content")
    os.chown(target, 300001, 300001)
    run(volume)
    assert target.read_bytes() == b"new legitimate content"
    assert (volume / ".role-owner-migration/quarantine/alice/work/payload").read_bytes() == b"alice"
    assert run(volume)["changed"] == 0


def test_restart_preserves_owner_execute_changes_and_marks_new_generation(volume):
    run(volume)
    path = volume / "alice/work/payload"
    path.chmod(0o700)
    marker = volume / ".layout.json"
    layout = json.loads(marker.read_text())
    layout["state"] = "stable"
    layout["roles"]["state"] = "stable"
    marker.write_text(json.dumps(layout))

    def fail(step):
        if step == "marker":
            raise RuntimeError("new-generation marker")

    with pytest.raises(RuntimeError, match="new-generation marker"):
        run(volume, after_step=fail)
    assert json.loads(marker.read_text())["state"] == "migrating"
    run(volume)
    assert path.stat().st_mode & stat.S_IXUSR
    path.chmod(0o600)
    run(volume)
    assert not path.stat().st_mode & 0o111


def test_all_substeps_share_one_continuously_held_layout_lock(volume):
    import fcntl
    import sqlite3

    from deploy.role_egress_migration import migrate_liveness, relocate, transfer_accounting
    from tinyassets import role_modes

    with sqlite3.connect(volume / "outbound.db") as db:
        db.execute("CREATE TABLE retained(value TEXT)")
        db.execute("INSERT INTO retained VALUES ('bytes retained')")
    os.chown(volume / "outbound.db", 1001, 1001)
    lock = os.open(volume / ".layout.lock", os.O_RDONLY)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def competitor():
        pid = os.fork()
        if pid == 0:
            os.close(lock)
            own = os.open(volume / ".layout.lock", os.O_RDONLY)
            try:
                fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os._exit(0)
            os._exit(1)
        assert os.waitpid(pid, 0)[1] == 0

    try:
        for reverse in (False, True):
            calls = [
                lambda: relocate(volume, reverse=reverse, layout_lock=lock),
                lambda: transfer_accounting(volume, reverse=reverse, layout_lock=lock),
                lambda: migrate_liveness(volume, modes=vars(role_modes), reverse=reverse,
                                          layout_lock=lock),
                lambda: run(volume, reverse=reverse, layout_lock=lock),
            ]
            for operation in reversed(calls) if reverse else calls:
                competitor()
                operation()
                competitor()
    finally:
        os.close(lock)
    with sqlite3.connect(volume / "outbound.db") as db:
        assert db.execute("SELECT value FROM retained").fetchone() == ("bytes retained",)


def test_egress_dry_run_preserves_directory_and_marker_atimes(volume):
    from deploy.role_egress_migration import relocate

    proxy = volume / ".outbound-proxy"
    proxy.mkdir()
    (proxy / "retained").write_bytes(b"proxy")
    for path in (volume, proxy, volume / ".layout.json"):
        os.utime(path, ns=(1, 1))
    before = metadata(volume)
    relocate(volume, dry_run=True)
    assert metadata(volume) == before


def test_substeps_refuse_another_volumes_lock(volume, tmp_path):
    from deploy import role_egress_migration as egress
    from tinyassets import role_modes

    foreign = tmp_path / "other-lock"
    foreign.touch()
    fd = os.open(foreign, os.O_RDONLY)
    before = metadata(volume)
    try:
        with pytest.raises(MigrationRefused, match="layout lock"):
            run(volume, layout_lock=fd)
        for operation in (
            lambda: egress.relocate(volume, layout_lock=fd),
            lambda: egress.transfer_accounting(volume, layout_lock=fd),
            lambda: egress.migrate_liveness(volume, modes=vars(role_modes), layout_lock=fd),
        ):
            with pytest.raises(egress.MigrationRefused, match="layout lock"):
                operation()
    finally:
        os.close(fd)
    assert metadata(volume) == before


@pytest.mark.parametrize("mode", [0o000, 0o400, 0o600, 0o640, 0o644, 0o700, 0o751, 0o755])
def test_exact_work_modes_and_original_ids_survive_both_directions(volume, mode):
    paths = [volume / "alice/work" / name for name in
             ("ordinary", "database-wal", "database-shm", "directory")]
    for path in paths:
        if path.name == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"unchanged")
        os.chown(path, 1001, 1100)
        path.chmod(mode)
    run(volume)
    for path in paths:
        expected = mode  # D211 forbids adding directory bits too.
        assert stat.S_IMODE(path.stat().st_mode) == expected
        assert (path.stat().st_uid, path.stat().st_gid) == (300001, 300001)
        if path.name != "directory":
            assert not (path.stat().st_mode & 0o6111) & ~mode
    assert run(volume)["changed"] == 0
    run(volume, reverse=True)
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == mode
        assert (path.stat().st_uid, path.stat().st_gid) == (1001, 1100)
        if path.name != "directory":
            assert path.read_bytes() == b"unchanged"
    assert run(volume, reverse=True)["changed"] == 0


def test_reverse_restores_original_mode_after_restart_generation(volume):
    path = volume / "alice/work/payload"
    path.chmod(0o640)
    directory = volume / "alice/work"
    original_directory = stat.S_IMODE(directory.stat().st_mode)
    run(volume)
    path.chmod(0o700)
    run(volume)
    run(volume, reverse=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert stat.S_IMODE(directory.stat().st_mode) == original_directory


@pytest.mark.parametrize("boundary", ["journal", "marker", "ownership", "complete-journal", None])
@pytest.mark.parametrize("name", ["payload", "database-wal", "database-shm", "directory"])
def test_d211_chmod_after_record_survives_resume_and_reverse(volume, boundary, name):
    path = volume / "alice/work" / name
    if name == "directory":
        path.mkdir()
    elif not path.exists():
        path.write_bytes(b"retained")
    os.chown(path, 1001, 1001)
    path.chmod(0o755 if name == "directory" else 0o644)

    def crash(step):
        if step == boundary:
            raise InterruptedError(step)

    if boundary:
        with pytest.raises(InterruptedError):
            run(volume, after_step=crash)
    else:
        run(volume)
    path.chmod(0o600)
    run(volume)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (path.stat().st_uid, path.stat().st_gid) == (300001, 300001)
    run(volume, reverse=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (path.stat().st_uid, path.stat().st_gid) == (1001, 1001)
    assert run(volume, reverse=True)["changed"] == 0


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("mode", [0o000, 0o400, 0o600, 0o640, 0o755, 0o2700])
def test_egress_permissions_never_add_mode_bits(volume, reverse, mode):
    from deploy.role_egress_migration import _permissions

    for name in ("database", "database-wal", "database-shm", "directory"):
        path = volume / "alice/work" / name
        if name == "directory":
            path.mkdir()
        else:
            path.write_bytes(b"retained")
        path.chmod(mode)
        fd = os.open(path, os.O_RDONLY)
        try:
            uid, gid = (1001, 1001) if reverse else (1002, 1101)
            _permissions(fd, uid, gid, 0o2700 if name == "directory" else 0o600)
        finally:
            os.close(fd)
        assert not stat.S_IMODE(path.stat().st_mode) & ~mode
        assert (path.stat().st_uid, path.stat().st_gid) == (uid, gid)
