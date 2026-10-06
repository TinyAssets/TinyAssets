"""U2 two-pass deletion of a migrated owner tree (D10/D85), Linux root oracle.

Pass one is U1's real ``remove_owned`` run as the owner identity in a child
with no capability; pass two is the daemon pass run as UID1001 the same way.
Only the launcher transport (``role_owner_delete.begin``/``finish``) is
replaced: its admission and fence are proven by ``test_role_owner_delete``
and the production-image ``role_owner_delete_probe``.
"""
# ruff: noqa: F811 -- shared Linux root fixture
import json
import os
import traceback
from types import SimpleNamespace

import pytest

from deploy import role_volume_migration as migration
from deploy.role_owner_migration import MigrationRefused
from tests.test_role_owner_migration import metadata, volume  # noqa: F401
from tests.test_role_volume_migration import run, setup
from tinyassets import role_owner_delete, role_owner_delete_cell
from tinyassets import role_owner_tree_deletion as deletion
from tinyassets.owner_launcher_client import OwnerLaunchRefused

MACHINE = {"alice": 300001, "bob": 300002}
DAEMON_PASS = deletion.daemon_pass


def as_user(uid, call):
    """Run ``call`` in a child with only ``uid`` (no groups, so no capability)."""
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read)
        code = 0
        try:
            os.setgroups([])
            os.setresgid(uid, uid, uid)
            os.setresuid(uid, uid, uid)
            call()
        except BaseException:
            os.write(write, traceback.format_exc().encode()[-4000:])
            code = 1
        os._exit(code)
    os.close(write)
    with os.fdopen(read, "rb") as handle:
        failure = handle.read().decode()
    _, status = os.waitpid(child, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise RuntimeError(failure)


class Launcher:
    """Owner cell transport; the fence is an exact-token map like the mapper's."""

    def __init__(self):
        self.fence, self.begun, self.finished = None, [], []
        self.fail_finish = 0

    def begin(self, center, *, token):
        if self.fence not in (None, token):
            raise OwnerLaunchRefused("owner deletion is not quiescent or token differs")
        self.fence = token
        self.begun.append(token)
        machine = MACHINE[center.name]

        def owner_pass():
            fd = os.open(center, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                role_owner_delete_cell.remove_owned(
                    fd, overflow_uid=1001,
                    classify_daemon=lambda entry: os.fstat(entry).st_uid == 1001)
            finally:
                os.close(fd)

        as_user(machine, owner_pass)
        return {"pass": "owner"}

    def finish(self, center, *, token):
        if self.fail_finish:
            self.fail_finish -= 1
            raise OSError("daemon died before finish")
        if self.fence != token:
            raise OwnerLaunchRefused("deletion finish does not match quiescent fence")
        self.fence = None
        self.finished.append(token)

    def restart(self):
        self.fence = None


@pytest.fixture
def launcher(monkeypatch):
    from tinyassets.auth import middleware
    from tinyassets.broker import owner_identities

    value = Launcher()
    monkeypatch.setattr(role_owner_delete, "begin", value.begin)
    monkeypatch.setattr(role_owner_delete, "finish", value.finish)
    monkeypatch.setattr(role_owner_delete, "abort", value.finish)
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="alice"))
    monkeypatch.setattr(owner_identities, "owner_identity",
                        lambda root, *, principal: SimpleNamespace(
                            uid=MACHINE[principal], gid=MACHINE[principal]))
    real = DAEMON_PASS
    value.daemon_failures = 0

    def daemon(root, center, *, machine):
        result = []
        if value.daemon_failures:
            value.daemon_failures -= 1
            raise RuntimeError("daemon died during pass two")
        as_user(1001, lambda: result.append(real(root, center, machine=machine)))
        return {"pass": "daemon"}

    monkeypatch.setattr(deletion, "daemon_pass", daemon)
    return value


def migrated(volume):
    setup(volume)
    runtime = volume / "alice/.runtime"
    (runtime / "provider-launch-credentials").mkdir(parents=True)
    (runtime / "provider-launch-credentials/snapshot").write_bytes(b"sealed")
    for path in (runtime, runtime / "provider-launch-credentials",
                 runtime / "provider-launch-credentials/snapshot"):
        os.chown(path, 1001, 1001)
    run(volume)

    def engine_writes():
        private = volume / "alice/work/engine-private"
        private.mkdir(mode=0o700)
        (private / "secret").write_bytes(b"engine 0600")
        (private / "secret").chmod(0o600)
        (volume / "alice/work/payload").chmod(0)
        private.chmod(0o500)

    as_user(MACHINE["alice"], engine_writes)
    return volume


def bob_view(volume):
    return {k: v for k, v in metadata(volume).items() if "/bob" in k}


def test_two_pass_removes_migrated_center_capability_free(volume, launcher):
    migrated(volume)
    bob = bob_view(volume)
    receipt = deletion.delete_center(volume, "alice", principal="alice")
    assert receipt["owner_pass"] == {"pass": "owner"}
    assert receipt["daemon_pass"] == {"pass": "daemon"}
    assert receipt["fence"] == "released"
    assert not (volume / "alice").exists()
    assert launcher.begun == launcher.finished and len(launcher.begun) == 1
    assert deletion.pending(volume) == []
    assert bob_view(volume) == bob
    assert (volume / "bob/work/payload").read_bytes() == b"bob"


@pytest.mark.parametrize("crash", ["daemon-pass", "finish"])
def test_interrupted_deletion_resumes_with_its_token(volume, launcher, crash):
    migrated(volume)
    bob = bob_view(volume)
    if crash == "daemon-pass":
        launcher.daemon_failures = 1
    else:
        launcher.fail_finish = 1
    with pytest.raises((RuntimeError, OSError)):
        deletion.delete_center(volume, "alice", principal="alice")
    assert deletion.pending(volume) == ["alice"]
    token = launcher.begun[0]
    assert (volume / "alice").exists() == (crash == "daemon-pass")
    launcher.restart()  # a daemon restart also restarts the launcher's fence
    receipt = deletion.delete_center(volume, "alice", principal="alice")
    assert receipt["fence"] == ("released" if crash == "daemon-pass" else "absent")
    assert set(launcher.begun) == {token} and launcher.finished in ([token], [])
    assert not (volume / "alice").exists()
    assert deletion.pending(volume) == []
    assert bob_view(volume) == bob


def test_reverse_refuses_while_partial_deletion_pending(volume, launcher):
    migrated(volume)
    launcher.daemon_failures = 1
    with pytest.raises(RuntimeError):
        deletion.delete_center(volume, "alice", principal="alice")
    # Pass one removed the owner work; the root name and the legacy daemon
    # symlink it kept (.venv/python) await the daemon pass.
    assert sorted(p.name for p in (volume / "alice/work").rglob("*")) == [".venv", "python"]
    # The driver ran as root here; the daemon's intent store is UID1001's.
    store = volume / deletion.INTENT_DIR
    for path in (store, store / "alice.json"):
        os.chown(path, 1001, 1001)
    before = metadata(volume)
    for dry_run in (True, False):
        with pytest.raises(MigrationRefused, match="pending owner deletion"):
            run(volume, reverse=True, dry_run=dry_run)
    assert metadata(volume) == before
    # Forward startup stays admitted so the daemon can resume the deletion.
    run(volume, dry_run=True)
    assert metadata(volume) == before
    for path in (store, store / "alice.json"):
        os.chown(path, 0, 0)
    launcher.restart()
    deletion.delete_center(volume, "alice", principal="alice")
    assert not (volume / "alice").exists() and deletion.pending(volume) == []


def test_daemon_pass_refuses_foreign_and_undeleted_entries(volume, launcher):
    migrated(volume)
    sentinel = volume / "bob/work/payload"
    os.link(sentinel, volume / "alice/foreign-hardlink")
    (volume / "alice/to-bob").symlink_to(volume / "bob/work")
    os.lchown(volume / "alice/to-bob", 1001, 1001)
    bob = bob_view(volume)
    with pytest.raises(RuntimeError, match="foreign or undeleted entry: alice/foreign-hardlink"):
        as_user(1001, lambda: DAEMON_PASS(volume, "alice", machine=300001))
    assert bob_view(volume) == bob
    assert (volume / "alice/foreign-hardlink").stat().st_ino == sentinel.stat().st_ino
    # An owner entry inside owner work (pass one's job) is never removed here.
    (volume / "alice/foreign-hardlink").unlink()
    launcher.begin(volume / "alice", token="a" * 32)
    as_user(300001, lambda: (volume / "alice/work/late").write_bytes(b"owner"))
    bob = bob_view(volume)
    with pytest.raises(RuntimeError, match="foreign or undeleted entry: alice/work/late"):
        as_user(1001, lambda: DAEMON_PASS(volume, "alice", machine=300001))
    assert (volume / "alice/work/late").read_bytes() == b"owner"
    assert bob_view(volume) == bob and (volume / "bob/work/payload").read_bytes() == b"bob"


def test_wrong_owner_identity_never_runs_pass_two(volume):
    migrated(volume)
    before = metadata(volume)
    with pytest.raises(RuntimeError, match="not this owner's migrated root"):
        as_user(1001, lambda: DAEMON_PASS(volume, "alice", machine=300002))
    assert metadata(volume) == before


def test_legacy_and_reversed_layouts_keep_existing_traversal(volume, launcher):
    setup(volume)
    assert deletion.delete_center(volume, "alice", principal="alice") is None
    run(volume)
    run(volume, reverse=True)
    assert deletion.delete_center(volume, "alice", principal="alice") is None
    assert launcher.begun == [] and not (volume / deletion.INTENT_DIR).exists()


def test_unverified_migration_refuses_before_any_intent(volume, launcher):
    migrated(volume)
    layout = json.loads((volume / ".layout.json").read_text())
    layout["roles"]["state"] = layout["state"] = "migrating"
    (volume / ".layout.json").write_text(json.dumps(layout))
    with pytest.raises(deletion.OwnerTreeDeletionRefused, match="not verified"):
        deletion.delete_center(volume, "alice", principal="alice")
    assert launcher.begun == [] and deletion.pending(volume) == []


def test_another_principals_pending_intent_is_refused(volume, launcher, monkeypatch):
    from tinyassets.auth import middleware

    migrated(volume)
    launcher.daemon_failures = 1
    with pytest.raises(RuntimeError):
        deletion.delete_center(volume, "alice", principal="alice")
    monkeypatch.setattr(middleware, "current_identity", lambda: SimpleNamespace(user_id="bob"))
    with pytest.raises(deletion.OwnerTreeDeletionRefused, match="different principal"):
        deletion.delete_center(volume, "alice", principal="bob")
    with pytest.raises(deletion.OwnerTreeDeletionRefused, match="no matching"):
        deletion.abort_center(volume, "alice", principal="bob")
    assert deletion.pending(volume) == ["alice"]


def test_explicit_abort_records_partial_loss_and_releases(volume, launcher):
    migrated(volume)
    launcher.daemon_failures = 1
    with pytest.raises(RuntimeError):
        deletion.delete_center(volume, "alice", principal="alice")
    token = launcher.begun[0]
    assert deletion.abort_center(volume, "alice", principal="alice") == {
        "center": "alice", "aborted": True, "partial": True}
    assert launcher.finished == [token] and deletion.pending(volume) == []
    assert (volume / "alice").is_dir()  # never rollback, never claimed deleted


def test_intent_store_name_is_one_fact():
    assert migration.DELETION_INTENTS == deletion.INTENT_DIR


@pytest.mark.parametrize("reverse", [False, True])
def test_completed_deletion_fails_closed_at_next_migration(volume, launcher, reverse):
    """D218: a shrunken principal set is D216's open admission contract."""
    migrated(volume)
    deletion.delete_center(volume, "alice", principal="alice")
    before = metadata(volume)
    with pytest.raises(MigrationRefused, match="authority changed"):
        run(volume, reverse=reverse, dry_run=True)
    assert metadata(volume) == before
