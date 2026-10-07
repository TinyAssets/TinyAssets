"""No-follow runtime liveness creation; separate UID denial runs in the image."""
import os
import stat

import pytest

from tinyassets import role_modes
from tinyassets.process_liveness import ALIVE, hold_liveness, owner_state
from tinyassets.singleton_lock import release_singleton_lock

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX role liveness")


@pytest.fixture(autouse=True)
def local_group(monkeypatch):
    # Unit oracle has one uid/group. The production-image probe uses real 1102.
    monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())


def test_creator_preserves_modes_under_restrictive_umask_and_live_inode(tmp_path):
    previous = os.umask(0o077)
    try:
        held = hold_liveness(tmp_path, "parent", broker_readable=True)
    finally:
        os.umask(previous)
    try:
        assert owner_state(tmp_path, "parent") == ALIVE
        assert stat.S_IMODE(os.fstat(held.fd).st_mode) == role_modes.LIVENESS_FILE_MODE
        directory = tmp_path / ".consumer_liveness"
        assert stat.S_IMODE(directory.stat().st_mode) == role_modes.LIVENESS_DIRECTORY_MODE
        assert not held.path.with_suffix(".lock.pid").exists()
    finally:
        release_singleton_lock(held)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "parent_link", "writable_parent"])
def test_hostile_creator_refuses_without_touching_outside_target(tmp_path, kind):
    outside = tmp_path / "outside"
    outside.write_text("retained")
    before = outside.stat()
    directory = tmp_path / ".consumer_liveness"
    directory.mkdir(mode=0o700)
    target = directory / "parent.lock"
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "parent_link":
        directory.rmdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    else:
        directory.chmod(0o777)
    with pytest.raises(OSError):
        hold_liveness(tmp_path, "parent", broker_readable=True)
    after = outside.stat()
    assert (after.st_uid, after.st_gid, after.st_mode) == (
        before.st_uid, before.st_gid, before.st_mode)
    assert outside.read_text() == "retained"


def test_existing_process_lock_is_upgraded_without_replacing_it(tmp_path):
    from tinyassets.process_liveness import _HELD, owner_token

    token = owner_token(tmp_path)
    held = _HELD[str(tmp_path.resolve())]
    try:
        before = os.fstat(held.fd)
        assert owner_token(tmp_path, broker_readable=True) == token
        assert os.path.samestat(before, held.path.stat())
        assert owner_state(tmp_path, token) == ALIVE
        assert stat.S_IMODE(held.path.stat().st_mode) == role_modes.LIVENESS_FILE_MODE
    finally:
        release_singleton_lock(_HELD.pop(str(tmp_path.resolve())))
