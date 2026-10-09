"""Two-pass deletion of an owner tree (D10/D85/D218), Linux root oracle.

Pass one is the real ``remove_owned`` run as the owner identity in a child
with no capability; pass two is the daemon pass run as UID1001 the same way.
Only the launcher transport (``role_owner_delete.begin``/``finish``/``retire``)
is replaced: its admission and fence are proven by ``test_role_owner_delete``
and the production-image ``role_owner_delete_probe``. The volume is built
with the owner-split labels directly, as the migration leaves it.
"""
import os
import shutil
import stat
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_owner_delete, role_owner_delete_cell
from tinyassets import role_owner_tree_deletion as deletion
from tinyassets.broker.owner_identities import CenterUnadmitted, OwnerIdentities
from tinyassets.owner_launcher_client import OwnerLaunchRefused

pytestmark = pytest.mark.role_split

MACHINE = {"alice": 300001, "bob": 300002}
DAEMON_PASS = deletion.daemon_pass
SUBTREE_PASS = deletion.daemon_subtree_pass


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


def label(entry, machine):
    """The migration's owner label: ``machine`` owns it, and UID1001 has a
    named ACL entry the mode's group bits mask (D4 as amended by D65)."""
    from tinyassets.role_center_admission import _acl

    info = os.lstat(entry)
    os.lchown(entry, machine, machine)
    if stat.S_ISLNK(info.st_mode):
        return
    mode = stat.S_IMODE(info.st_mode)
    os.setxattr(entry, "system.posix_acl_access", _acl(
        (mode >> 6) & 7, {1001: 7}, group=(mode >> 3) & 7, mask=(mode >> 3) & 7,
        other=mode & 7))
    if stat.S_ISDIR(info.st_mode):
        os.setxattr(entry, "system.posix_acl_default", _acl(7, {1001: 7}, mask=7))


def identities(root):
    return OwnerIdentities(root / ".broker/state/owner-identities.db")


def admissions(root):
    return [(r.generation, r.event, r.principal, r.center, r.machine)
            for r in identities(root).admissions_after(0)]


def metadata(root):
    result = {}
    for parent, directories, files in os.walk(root):
        for name in [*directories, *files]:
            info = os.lstat(os.path.join(parent, name))
            result[os.path.join(parent, name)] = (info.st_uid, info.st_gid, info.st_mode,
                                                  info.st_ino)
    return result


@pytest.fixture
def volume():
    assert os.name == "posix" and os.geteuid() == 0, "requires Linux root oracle"
    parent = Path(tempfile.mkdtemp(dir="/dev/shm"))
    try:
        parent.chmod(0o755)
        root = parent / "data"
        root.mkdir()
        os.chown(root, 1001, 1001)
        state = root / ".broker/state"
        state.mkdir(parents=True)
        state.chmod(0o700)
        db = OwnerIdentities(state / "owner-identities.db", initialize=True)
        for principal in ("alice", "bob"):
            assert db.resolve(principal, allocate=True).uid == MACHINE[principal]
            db.admission("admit", principal, principal)
        for center, machine in MACHINE.items():
            tree = root / center
            (tree / "work/.venv").mkdir(parents=True)
            (tree / "work/payload").write_bytes(center.encode())
            (tree / "work/.venv/python").symlink_to("/outside/sentinel")
            runtime = tree / ".runtime/provider-launch-credentials"
            runtime.mkdir(parents=True)
            (runtime / "snapshot").write_bytes(b"sealed")
            (tree / "work").chmod(0o700)
            (tree / "work/payload").chmod(0o600)
            for path, directories, files in os.walk(tree):
                for name in [*directories, *files]:
                    entry = Path(path) / name
                    if ".runtime" in entry.parts:
                        os.lchown(entry, 1001, 1001)
                    else:
                        label(entry, machine)
            os.lchown(tree / "work/.venv/python", 1001, 1001)  # a daemon entry in owner work
            (tree / ".runtime").chmod(0o700)
            os.chown(tree, 1001, machine)
            tree.chmod(0o2750)
        yield root
    finally:
        shutil.rmtree(parent)


def owner_pass(center, machine):
    def run():
        fd = os.open(center, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            role_owner_delete_cell.remove_owned(
                fd, overflow_uid=1001,
                classify_daemon=lambda entry: os.fstat(entry).st_uid == 1001)
        finally:
            os.close(fd)

    as_user(machine, run)


class Launcher:
    """Owner cell transport; the fence is an exact-token map like the mapper's."""

    def __init__(self):
        self.fence, self.begun, self.finished, self.retired = None, [], [], []
        self.fail_finish = 0

    def begin(self, center, *, token):
        if self.fence not in (None, token):
            raise OwnerLaunchRefused("owner deletion is not quiescent or token differs")
        self.fence = token
        self.begun.append(token)
        owner_pass(center, MACHINE[center.name])
        return {"pass": "owner"}

    def retire(self, center, *, token):
        """DA6 on the real log: one retire row, then the (absent) unbind."""
        if os.path.lexists(center):
            raise RuntimeError("retire runs only after the daemon pass removed the tree")
        if self.fence not in (None, token):
            raise OwnerLaunchRefused("retire does not match the deletion fence")
        row = identities(center.parent).admission("retire", "alice", center.name)
        self.retired.append((center.name, row.generation, self.fence))
        return row.generation

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
    monkeypatch.setattr(role_owner_delete, "retire", value.retire)
    monkeypatch.setattr(role_owner_delete, "abort", value.finish)
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="alice"))
    monkeypatch.setattr(owner_identities, "owner_identity",
                        lambda root, *, principal: SimpleNamespace(
                            uid=MACHINE[principal], gid=MACHINE[principal]))
    value.daemon_failures = 0

    def daemon(root, center, *, machine):
        if value.daemon_failures:
            value.daemon_failures -= 1
            raise RuntimeError("daemon died during pass two")
        as_user(1001, lambda: DAEMON_PASS(root, center, machine=machine))
        return {"pass": "daemon"}

    monkeypatch.setattr(deletion, "daemon_pass", daemon)
    return value


def bob_view(volume):
    return {k: v for k, v in metadata(volume).items() if "/bob" in k}


def test_two_pass_removes_center_capability_free(volume, launcher):
    bob = bob_view(volume)
    receipt = deletion.delete_center(volume, "alice", principal="alice")
    assert receipt["owner_pass"] == {"pass": "owner"}
    assert receipt["daemon_pass"] == {"pass": "daemon"}
    assert receipt["fence"] == "released"
    assert not (volume / "alice").exists()
    assert launcher.begun == launcher.finished and len(launcher.begun) == 1
    # DA6: retired under the held fence, before finish released it.
    assert launcher.retired == [("alice", receipt["retired"], launcher.begun[0])]
    assert admissions(volume)[-1] == (receipt["retired"], "retire", "alice", "alice", 300001)
    assert deletion.pending(volume) == []
    assert bob_view(volume) == bob
    assert (volume / "bob/work/payload").read_bytes() == b"bob"


@pytest.mark.parametrize("crash", ["daemon-pass", "finish"])
def test_interrupted_deletion_resumes_with_its_token(volume, launcher, crash):
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
    # One retire row, ever: the tree-gone resume repeats it idempotently.
    assert [row[1:4] for row in admissions(volume)].count(("retire", "alice", "alice")) == 1


def test_daemon_pass_refuses_foreign_and_undeleted_entries(volume, launcher):
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
    with pytest.raises(RuntimeError, match="foreign or undeleted entry: alice/work/late"):
        as_user(1001, lambda: DAEMON_PASS(volume, "alice", machine=300001))
    assert (volume / "alice/work/late").read_bytes() == b"owner"
    assert bob_view(volume) == bob and (volume / "bob/work/payload").read_bytes() == b"bob"


def test_wrong_owner_identity_never_runs_pass_two(volume):
    before = metadata(volume)
    with pytest.raises(RuntimeError, match="not this owner's migrated root"):
        as_user(1001, lambda: DAEMON_PASS(volume, "alice", machine=300002))
    assert metadata(volume) == before


def test_another_principals_pending_intent_is_refused(volume, launcher, monkeypatch):
    from tinyassets.auth import middleware

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
    launcher.daemon_failures = 1
    with pytest.raises(RuntimeError):
        deletion.delete_center(volume, "alice", principal="alice")
    token = launcher.begun[0]
    assert deletion.abort_center(volume, "alice", principal="alice") == {
        "center": "alice", "aborted": True, "partial": True}
    assert launcher.finished == [token] and deletion.pending(volume) == []
    assert (volume / "alice").is_dir()  # never rollback, never claimed deleted


def test_a_missing_center_is_retired_without_a_pass(volume, launcher, monkeypatch):
    """F1 (b): an admitted center whose tree is gone is retired, nothing else."""
    def retire(center, *, token):
        row = identities(center.parent).admission("retire", "alice", center.name)
        launcher.retired.append((center.name, row.generation, None))
        return row.generation

    monkeypatch.setattr(role_owner_delete, "retire", retire)
    shutil.rmtree(volume / "alice")
    receipt = deletion.delete_center(volume, "alice", principal="alice")
    assert receipt["missing"] and receipt["fence"] == "absent"
    assert launcher.begun == [] and launcher.finished == []
    assert launcher.retired == [("alice", receipt["retired"], None)]
    assert deletion.pending(volume) == []


def test_a_never_admitted_treeless_center_has_nothing_to_delete(volume, launcher, monkeypatch):
    def retire(center, *, token):
        raise CenterUnadmitted("the admission log never admitted this center")

    monkeypatch.setattr(role_owner_delete, "retire", retire)
    assert deletion.delete_center(volume, "carol", principal="alice") is None
    assert launcher.retired == [] and deletion.pending(volume) == []


def test_subtree_two_pass_removes_only_that_subtree(volume):
    """Pool reclamation: pass one over one owner subtree, then the daemon pass."""
    work = volume / "alice/work"
    bob, sibling = bob_view(volume), (volume / "alice/.runtime").stat()
    owner_pass(work, MACHINE["alice"])
    assert sorted(p.name for p in work.rglob("*")) == [".venv", "python"]
    # The work root sits beneath the daemon-owned center root, so the daemon
    # removes it after the owner emptied what it wrote.
    as_user(1001, lambda: SUBTREE_PASS(work, machine=300001))
    assert not work.exists()
    assert (volume / "alice/.runtime").stat().st_ino == sibling.st_ino
    assert bob_view(volume) == bob
    as_user(1001, lambda: SUBTREE_PASS(work, machine=300001))  # repeat is a no-op


def test_subtree_daemon_pass_refuses_undeleted_owner_entries(volume):
    work = volume / "alice/work"
    # Without pass one the owner's entries stay masked or undeleted: refuse.
    with pytest.raises(RuntimeError, match="owner tree deletion failed: work|undeleted entry"):
        as_user(1001, lambda: SUBTREE_PASS(work, machine=300001))
    assert (work / "payload").read_bytes() == b"alice"
    assert stat.S_ISDIR(work.lstat().st_mode)
