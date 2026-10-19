"""Local-driver specifics that the portable contract suite does not cover.

These reach the local driver's own construction options (busy wait) or simulate
a box-host restart with a second driver instance over the same state. The
behaviour is part of the contract; the way it is provoked here is local-only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tinyassets.boxes import (
    BoxError,
    WriteMode,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix" or sys.platform == "win32" or not Path("/proc/self/fd").is_dir(),
    reason="the local box driver needs Linux",
)

OWNERS = {"cc-a": "acct-a", "cc-b": "acct-b"}


def _local(tmp_path: Path, **kw):
    from tinyassets.boxes.local import LocalBoxProvider

    return LocalBoxProvider(boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
                            owner_of=OWNERS.get, allow_unisolated=True, **kw)






def test_a_published_create_is_never_reported_as_failed(tmp_path, monkeypatch):
    host = _local(tmp_path)
    handle = host.bind("cc-a", account_id="acct-a")
    real_unlink = os.unlink

    def unlink_tmp_fails(path, *a, **k):
        if str(path).startswith(".boxtmp-"):
            raise OSError(2, "temp already gone")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(os, "unlink", unlink_tmp_fails)
    wrote = host.write(handle, "w1", "/cc/new.txt", b"hi", max_bytes=10,
                       mode=WriteMode.CREATE)
    monkeypatch.setattr(os, "unlink", real_unlink)
    assert wrote.generation == 1
    assert host.read(handle, "/cc/new.txt", max_bytes=10).data == b"hi"
    assert host.write(handle, "w1", "/cc/new.txt", b"hi", max_bytes=10,
                      mode=WriteMode.CREATE) == wrote  # recorded, not forgotten
    host.close()


def test_unacknowledged_construction_is_refused(tmp_path):
    from tinyassets.boxes.local import LocalBoxProvider

    with pytest.raises(BoxError):
        LocalBoxProvider(boxes_root=tmp_path / "b", state_dir=tmp_path / "s",
                         owner_of=OWNERS.get)
    assert not (tmp_path / "b").exists()


_CRASHING_HOST = """
import os, sys
from pathlib import Path
from tinyassets.boxes.local import LocalBoxProvider
root = Path(sys.argv[1])
host = LocalBoxProvider(boxes_root=root / "boxes", state_dir=root / "state",
                        owner_of={"cc-a": "acct-a"}.get, allow_unisolated=True)
h = host.bind("cc-a", account_id="acct-a")
host.ensure_awake(h, reason="test uncertain write")
host._state.begin("cc-a", "w1", "write", "digest")
(root / "boxes/cc-a/partial.txt").write_bytes(b"partial mutation")
os._exit(0)
"""




def test_one_box_host_at_a_time(tmp_path):
    first = _local(tmp_path)
    with pytest.raises(BoxError):
        _local(tmp_path)
    first.close()
    _local(tmp_path).close()  # after a clean shutdown the next host starts




def test_a_failed_partial_destroy_stales_every_handle_and_is_recorded(tmp_path, monkeypatch):
    from tinyassets.boxes import boxd as local_mod

    host = _local(tmp_path)
    handle = host.bind("cc-a", account_id="acct-a")
    for i in range(3):
        host.write(handle, f"w{i}", f"/cc/f{i}.txt", b"x", max_bytes=10)
    real = local_mod._remove_beneath
    calls = {"n": 0}

    def flaky(parent_fd, name, path, depth=0):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(5, "simulated I/O error")
        return real(parent_fd, name, path, depth)

    monkeypatch.setattr(local_mod, "_remove_beneath", flaky)
    with pytest.raises(OSError):
        host.destroy(handle, "d1")
    monkeypatch.setattr(local_mod, "_remove_beneath", real)
    with pytest.raises(BoxError):  # old handle: the epoch moved because part of the box is gone
        host.read(handle, "/cc/f2.txt", max_bytes=10)
    fresh = host.bind("cc-a", account_id="acct-a")
    with pytest.raises(BoxError):  # the failed op id never re-runs
        host.destroy(fresh, "d1")
    assert host.destroy(fresh, "d2").new_epoch == fresh.epoch + 1
    host.close()










def test_streaming_write_input_is_bounded_as_it_arrives(tmp_path):
    provider = _local(tmp_path)
    handle = provider.bind("cc-a", account_id="acct-a")

    def endless():
        while True:
            yield b"x" * 1024

    with pytest.raises(BoxError):
        provider.write(handle, "w1", "/cc/a.bin", endless(), max_bytes=4096)
    assert provider.stat(handle, "/cc/a.bin") is None


def test_an_unconsumed_download_does_not_leak_its_descriptor(tmp_path):
    provider = _local(tmp_path)
    handle = provider.bind("cc-a", account_id="acct-a")
    provider.write(handle, "w1", "/cc/a.bin", b"x" * 10, max_bytes=100)
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(50):
        provider.download(handle, "/cc/a.bin").close()
    assert len(os.listdir("/proc/self/fd")) <= before + 1


def test_a_restart_advances_the_generation_of_a_box_with_an_uncertain_operation(tmp_path):
    import subprocess

    repo = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, "-c", _CRASHING_HOST, str(tmp_path)], check=True,
                   cwd=repo, env={**os.environ, "PYTHONPATH": str(repo)}, timeout=60)
    import sqlite3

    from tinyassets.boxes.state import BoxHostState
    db = tmp_path / "state" / "boxhost.db"
    before = sqlite3.connect(db).execute(
        "SELECT generation FROM boxes WHERE command_center_id = 'cc-a'").fetchone()[0]
    host = _local(tmp_path)
    try:
        handle = host.bind("cc-a", account_id="acct-a")
        assert host.committed_generation(handle) > before
    finally:
        host.close()
    assert BoxHostState  # imported for the schema it owns


def test_a_host_that_fails_to_start_does_not_keep_ownership(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "boxhost.db").write_bytes(b"not a database" * 100)
    with pytest.raises(Exception):
        _local(tmp_path)
    (state / "boxhost.db").unlink()
    _local(tmp_path).close()  # the failed start released the lock


def test_a_failed_write_that_created_directories_is_recorded(tmp_path, monkeypatch):
    host = _local(tmp_path)
    handle = host.bind("cc-a", account_id="acct-a")
    g0 = host.committed_generation(handle)

    def failing_fsync(fd):
        raise OSError(28, "simulated ENOSPC")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        host.write(handle, "w1", "/cc/new/dir/a.txt", b"x", max_bytes=10)
    monkeypatch.undo()
    assert host.stat(handle, "/cc/new/dir").kind == "dir"
    assert host.committed_generation(handle) > g0
    with pytest.raises(BoxError):  # recorded as a partial failure: never re-run
        host.write(handle, "w1", "/cc/new/dir/a.txt", b"x", max_bytes=10)
    host.close()


def test_owner_boxd_opens_only_its_mounted_owner_tree(tmp_path):
    from tinyassets.boxes import BoxAuthError, BoxOperationRefused
    from tinyassets.boxes.boxd import OwnerBoxd

    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    worker = OwnerBoxd(owner_id="owner-a", owner_root=owner_root,
                       state_dir=tmp_path / "state")
    try:
        handle = worker.bind("owner-a", session_id="session-a")
        assert handle.account_id == "owner-a" and handle.turn_id == "session-a"
        worker.write(handle, worker.operation_prefix() + "write-a", "/cc/file.txt",
                     b"owner bytes", max_bytes=32)
        assert (owner_root / "file.txt").read_bytes() == b"owner bytes"
        assert not (owner_root / "owner-a").exists()
        with pytest.raises(BoxAuthError):
            worker.bind("owner-b", session_id="session-b")
        with pytest.raises(BoxOperationRefused):
            worker.destroy(handle, "destroy-a")
    finally:
        worker.close()
