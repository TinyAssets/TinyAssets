"""Accounting's broker can inspect kernel locks without write authority."""
import errno
import os

import pytest

from tinyassets import universe_files  # noqa: F401 -- detect OS support before open instrumentation
from tinyassets.process_liveness import ALIVE, DEAD, UNKNOWN, hold_liveness, owner_state
from tinyassets.singleton_lock import _unlock_fd, release_singleton_lock

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX read-only flock proof")


def test_readonly_proof_tracks_lock_and_never_opens_for_write(tmp_path, monkeypatch):
    held = hold_liveness(tmp_path, "parent")
    original = os.open

    def read_only(path, flags, *args, **kwargs):
        assert flags & os.O_ACCMODE == os.O_RDONLY
        return original(path, flags, *args, **kwargs)

    with monkeypatch.context() as probe:
        probe.setattr(os, "open", read_only)
        assert owner_state(tmp_path, "parent") == ALIVE
    _unlock_fd(held.fd)
    assert owner_state(tmp_path, "parent") == DEAD
    release_singleton_lock(held)
    assert owner_state(tmp_path, "parent") == UNKNOWN


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory", "parent_link"])
def test_hostile_proofs_are_unknown(tmp_path, kind):
    directory = tmp_path / ".consumer_liveness"
    directory.mkdir()
    target = tmp_path / "unrelated"
    target.write_text("retained")
    path = directory / "parent.lock"
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    else:
        directory.rename(tmp_path / "moved")
        directory.symlink_to(tmp_path / "moved", target_is_directory=True)
        path.write_text("")
    assert owner_state(tmp_path, "parent") == UNKNOWN
    assert target.read_text() == "retained"


def test_flock_error_is_not_liveness(tmp_path, monkeypatch):
    import fcntl

    held = hold_liveness(tmp_path, "parent")
    try:
        def error(*args):
            raise OSError(errno.EIO, "synthetic failure")

        with monkeypatch.context() as probe:
            probe.setattr(fcntl, "flock", error)
            assert owner_state(tmp_path, "parent") == UNKNOWN
    finally:
        release_singleton_lock(held)


def test_replaced_proof_is_unknown(tmp_path, monkeypatch):
    import fcntl

    held = hold_liveness(tmp_path, "parent")
    flock = fcntl.flock

    def replace_before_probe(fd, flags):
        path = tmp_path / ".consumer_liveness/parent.lock"
        path.rename(path.with_suffix(".old"))
        path.touch()
        return flock(fd, flags)

    try:
        with monkeypatch.context() as probe:
            probe.setattr(fcntl, "flock", replace_before_probe)
            assert owner_state(tmp_path, "parent") == UNKNOWN
    finally:
        release_singleton_lock(held)
