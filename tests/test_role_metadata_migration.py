"""Protected metadata migration uses real Linux ownership and ACLs."""

import os
import socket
import stat

import pytest

from deploy import role_metadata_migration as migration
from deploy import role_owner_migration as owner
from tests.test_role_owner_migration import metadata, volume  # noqa: F401
from tinyassets import role_modes


def run(root, **kwargs):
    return migration.migrate(
        root, bindings={"alice": 300001, "bob": 300002},
        work={"alice": ["work"], "bob": ["work"]},
        owner=vars(owner), modes=vars(role_modes), **kwargs,
    )


def seed(root):
    for center in ("alice", "bob"):
        (root / center / ".credential-vault.json").chmod(0o600)
    for path in (".broker/state", ".consumer_liveness", ".universe-sidecars/alice",
                 "alice/.runtime/provider-launch-credentials/one"):
        (root / path).mkdir(parents=True, exist_ok=True)
    for path in (".broker/state/reservation", ".consumer_liveness/one",
                 "alice/.runtime/provider-launch-credentials/one/token"):
        (root / path).write_bytes(b"preserve")
    for parent, _, files in os.walk(root):
        os.chown(parent, 1001, 1001)
        for name in files:
            os.chown(os.path.join(parent, name), 1001, 1001, follow_symlinks=False)


def test_dry_apply_repeat_reverse(volume):  # noqa: F811
    seed(volume)
    before = metadata(volume)
    run(volume, dry_run=True)
    assert metadata(volume) == before
    run(volume)
    vault = volume / "alice/.credential-vault.json"
    assert (vault.stat().st_uid, vault.stat().st_gid, stat.S_IMODE(vault.stat().st_mode)) == (
        1001, 1102, 0o600)
    broker = volume / ".broker/state/reservation"
    assert (broker.stat().st_uid, stat.S_IMODE(broker.stat().st_mode)) == (1002, 0o600)
    assert os.getxattr(volume / "alice/.runtime", owner.ACCESS)
    stable = metadata(volume)
    assert run(volume)["changed"] == 0
    assert metadata(volume) == stable
    run(volume, reverse=True)
    assert (vault.stat().st_uid, vault.stat().st_gid, stat.S_IMODE(vault.stat().st_mode)) == (
        1001, 1001, 0o600)
    assert broker.stat().st_uid == 1002
    assert broker.read_bytes() == b"preserve"
    stable = metadata(volume)
    assert run(volume, reverse=True)["changed"] == 0
    assert metadata(volume) == stable


@pytest.mark.parametrize("boundary", ["metadata-journal", "metadata-marker", "metadata-entry",
                                      "metadata-cleanup", "metadata-root", "metadata-complete"])
@pytest.mark.parametrize("reverse", [False, True])
def test_every_crash_boundary(volume, boundary, reverse):  # noqa: F811
    seed(volume)
    if reverse:
        run(volume)
    obsolete = volume / ".broker/owner.json"
    obsolete.write_bytes(b"obsolete")

    def fail(step):
        if step == boundary:
            raise RuntimeError("simulated power loss")

    with pytest.raises(RuntimeError, match="simulated power loss"):
        run(volume, reverse=reverse, after_step=fail)
    run(volume, reverse=reverse)
    assert not obsolete.exists()
    stable = metadata(volume)
    assert run(volume, reverse=reverse)["changed"] == 0
    assert metadata(volume) == stable
    assert (volume / "alice/work/payload").read_bytes() == b"alice"


def test_cleanup_unlinks_only_known_names_without_following(volume, tmp_path):  # noqa: F811
    seed(volume)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched")
    before = sentinel.stat()
    (volume / ".broker/owner.json").symlink_to(sentinel)
    relay = volume / ".universe-sidecars/alice/egress-12.sock"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(relay))
    result = run(volume)
    assert result["cleanup"] == [".broker/owner.json", ".universe-sidecars/alice/egress-12.sock"]
    assert sentinel.stat() == before
    assert not relay.exists()
    assert sentinel.read_bytes() == b"untouched"


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "fifo"])
def test_protected_aliases_refuse_before_mutation(volume, kind):  # noqa: F811
    seed(volume)
    target = volume / "alice/hostile"
    if kind == "hardlink":
        os.link(volume / "alice/.credential-vault.json", target)
    elif kind == "symlink":
        target.symlink_to("/outside")
    else:
        os.mkfifo(target)
    before = metadata(volume)
    with pytest.raises(owner.MigrationRefused, match="metadata (link/special entry|hardlink)"):
        run(volume)
    assert metadata(volume) == before


def test_real_owner_reads_only_its_snapshot_and_cannot_write_it(volume):  # noqa: F811
    import traceback

    seed(volume)
    owner.migrate(volume, bindings={"alice": 300001, "bob": 300002},
                  work={"alice": ["work"], "bob": ["work"]})
    run(volume)
    snapshot = volume / "alice/.runtime/provider-launch-credentials/one/token"
    for uid in (300001, 300002):
        child = os.fork()
        if child == 0:
            try:
                os.setgroups([])
                os.setresgid(uid, uid, uid)
                os.setresuid(uid, uid, uid)
                if uid == 300001:
                    assert snapshot.read_bytes() == b"preserve"
                    with pytest.raises(PermissionError):
                        snapshot.write_bytes(b"forbidden")
                else:
                    with pytest.raises(PermissionError):
                        snapshot.read_bytes()
                with pytest.raises(PermissionError):
                    (volume / "alice/.credential-vault.json").read_bytes()
                with pytest.raises(PermissionError):
                    list((volume / "alice/.runtime").iterdir())
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        assert os.waitpid(child, 0)[1] == 0


@pytest.mark.parametrize("mode", [0o400, 0o600, 0o640, 0o644, 0o700, 0o755])
def test_sidecar_files_preserve_modes(volume, mode):  # noqa: F811
    seed(volume)
    directory = volume / ".universe-sidecars/alice"
    directory.chmod(0o750)
    paths = [directory / name for name in ("state", "state-wal", "state-shm")]
    for path in paths:
        path.write_bytes(b"retained")
        os.chown(path, 1001, 1001)
        path.chmod(mode)
    run(volume)
    assert stat.S_IMODE(directory.stat().st_mode) == (0o750 & role_modes.SIDECAR_DIRECTORY_MODE)
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == mode
        assert not (path.stat().st_mode & 0o6111) & ~mode
    assert run(volume)["changed"] == 0
    run(volume, reverse=True)
    assert stat.S_IMODE(directory.stat().st_mode) == (0o750 & role_modes.SIDECAR_DIRECTORY_MODE)
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == mode
        assert (path.stat().st_uid, path.stat().st_gid) == (1001, 1001)
        assert path.read_bytes() == b"retained"
    assert run(volume, reverse=True)["changed"] == 0


@pytest.mark.parametrize("boundary", ["metadata-journal", "metadata-marker",
                                      "metadata-entry", None])
@pytest.mark.parametrize("name", ["alice/.credential-vault.json", ".consumer_liveness/one",
                                  ".universe-sidecars/alice/state-wal",
                                  ".universe-sidecars/alice/state-shm"])
def test_d211_current_mode_wins_over_saved_metadata(volume, boundary, name):  # noqa: F811
    seed(volume)
    path = volume / name
    path.write_bytes(b"retained")
    os.chown(path, 1001, 1001)
    path.chmod(0o644)

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
    run(volume, reverse=True)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (path.stat().st_uid, path.stat().st_gid) == (1001, 1001)
    assert path.read_bytes() == b"retained"


@pytest.mark.parametrize("name", ["alice/.credential-vault.json", ".consumer_liveness/one"])
def test_reverse_replacement_inode_uses_legacy_ids_and_current_mode(volume, name):  # noqa: F811
    seed(volume)
    run(volume)
    path = volume / name
    replacement = path.with_name("replacement")
    replacement.write_bytes(b"new generation")
    os.chown(replacement, 1001, role_modes.BROKER_READ_GID)
    replacement.chmod(0o600)
    replacement.replace(path)
    run(volume, reverse=True)
    assert (path.stat().st_uid, path.stat().st_gid) == (1001, 1001)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == b"new generation"
