"""Restore the pre-cutover old layout into private staging.

Root Linux proof uses real tar, SQLite and byte comparisons. Production rollback
eligibility and stopped-service installation are the separate C4 deployment lane.
"""

import json
import shutil
import tarfile
from pathlib import Path

import pytest

from tests.test_owner_cutover_census import _fingerprint, _sql
from tests.test_owner_cutover_census import source as source
from tinyassets.boxes.layout import OWNER_BOX_MIGRATION

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cutover(tmp_path, source):
    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_control import initialize

    backup = tmp_path / "tinyassets-snapshot-2026-10-19T00-00-00Z.tar.gz"
    with tarfile.open(backup, "w:gz") as archive:
        archive.add(source, arcname="_data")
    _archive(backup, backup.read_bytes())
    control = tmp_path / "deployment-cutover"
    initialize(control, backup=backup, old_image="old-image-digest")
    return control, backup


def test_cutover_consumption_is_durable_before_open_and_cannot_be_reinitialized(cutover,
                                                                             monkeypatch):
    import os
    import stat

    from tinyassets.cutover_control import CutoverRefused, initialize, open_admission, rollback

    control, backup = cutover
    events = []
    flushed = []
    fsync = os.fsync

    def recorded_fsync(fd):
        fsync(fd)
        flushed.append(stat.S_ISDIR(os.fstat(fd).st_mode))

    monkeypatch.setattr(os, "fsync", recorded_fsync)

    def open_ordinary():
        assert json.loads((control / "state.json").read_text())["state"] == "consumed"
        assert flushed == [False, True], "state and containing directory must be durable"
        events.append("open")

    open_admission(control, open_ordinary=open_ordinary)
    assert events == ["open"]
    with pytest.raises(CutoverRefused, match="consumed"):
        open_admission(control, open_ordinary=lambda: pytest.fail("reopened"))
    with pytest.raises(CutoverRefused, match="consumed"):
        rollback(control, stop_new=lambda: pytest.fail("stopped"),
                 install_old=lambda _: pytest.fail("restored"),
                 start_old=lambda _: pytest.fail("started"))
    with pytest.raises(FileExistsError):
        initialize(control, backup=backup, old_image="old-image-digest")


def test_cutover_pretraffic_rollback_stops_restores_exact_old_layout_then_starts(cutover, source,
                                                                            tmp_path):
    from tinyassets.cutover_control import rollback
    from tinyassets.cutover_restore import restore_old_layout

    control, backup = cutover
    events = []
    destination = tmp_path / "restored"

    def install_old(selected):
        assert events == ["stop-new"]
        assert selected == backup
        restore_old_layout(selected, destination)
        assert _fingerprint(destination) == _fingerprint(source)
        events.append("restore-old-layout")

    def start_old(image):
        assert image == "old-image-digest"
        assert events == ["stop-new", "restore-old-layout"]
        assert json.loads((control / "state.json").read_text())["state"] == "consumed"
        events.append("start-old")

    rollback(control, stop_new=lambda: events.append("stop-new"), install_old=install_old,
             start_old=start_old)
    assert events == ["stop-new", "restore-old-layout", "start-old"]


@pytest.mark.parametrize("damage", ["absent", "missing-record", "unreadable", "wrong-owner",
                                    "fifo", "symlink", "directory"])
def test_missing_cutover_authority_refuses_rollback_and_open(cutover, damage, tmp_path):
    import os

    from tinyassets.cutover_control import CutoverRefused, open_admission, rollback

    control, _ = cutover
    if damage == "absent":
        control = tmp_path / "missing"
    elif damage == "missing-record":
        (control / "state.json").unlink()
    elif damage == "unreadable":
        (control / "state.json").write_text("broken")
    elif damage == "wrong-owner":
        os.chown(control / "state.json", 1001, 1001)
    else:
        state = control / "state.json"
        saved = state.read_bytes()
        state.unlink()
        if damage == "fifo":
            os.mkfifo(state, 0o600)
        elif damage == "directory":
            state.mkdir(mode=0o600)
        else:
            outside = tmp_path / "outside"
            outside.write_bytes(saved)
            outside.chmod(0o600)
            state.symlink_to(outside)
    with pytest.raises(CutoverRefused):
        open_admission(control, open_ordinary=lambda: pytest.fail("opened without authority"))
    with pytest.raises(CutoverRefused):
        rollback(control, stop_new=lambda: pytest.fail("stopped without authority"),
                 install_old=lambda _: pytest.fail("restored without authority"),
                 start_old=lambda _: pytest.fail("started without authority"))


def test_deployment_lock_serializes_restart_without_replacing_its_inode(cutover):
    import fcntl
    import os
    import subprocess
    import sys

    control, _ = cutover
    inode = control.stat().st_ino
    descriptor = os.open(control, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    code = """
import sys
from pathlib import Path
from tinyassets.cutover_control import open_admission
print('started', flush=True)
open_admission(Path(sys.argv[1]), open_ordinary=lambda: print('opened', flush=True))
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(control)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "started"
        assert process.poll() is None
        assert json.loads((control / "state.json").read_text())["state"] == "pre_traffic"
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
        assert output.strip() == "opened"
        assert control.stat().st_ino == inode
        assert json.loads((control / "state.json").read_text())["state"] == "consumed"
    finally:
        os.close(descriptor)
        if process.poll() is None:
            process.kill()
            process.wait()


def test_interruption_after_consumption_keeps_both_paths_closed(cutover):
    from tinyassets.cutover_control import CutoverRefused, open_admission, rollback

    control, _ = cutover

    def crash():
        raise RuntimeError("process interrupted before admission")

    with pytest.raises(RuntimeError, match="interrupted"):
        open_admission(control, open_ordinary=crash)
    with pytest.raises(CutoverRefused):
        open_admission(control, open_ordinary=lambda: pytest.fail("opened"))
    with pytest.raises(CutoverRefused):
        rollback(control, stop_new=lambda: pytest.fail("stopped"), install_old=None,
                 start_old=None)


@pytest.mark.parametrize("interruption", ["copied_tree", "box_records", "removed_host_tree"])
def test_paired_restore_after_half_move_is_byte_exact(source, tmp_path, interruption):
    volume = tmp_path / "volume/_data"
    shutil.copytree(source, volume)
    store = tmp_path / "owners"
    store.mkdir()
    before = _fingerprint(volume)
    data_archive = tmp_path / "tinyassets-snapshot-2026-10-17T00-00-00Z.tar.gz"
    box_archive = tmp_path / "tinyassets-box-store-2026-10-17T00-00-00Z.tar.gz"
    with tarfile.open(data_archive, "w:gz") as archive:
        archive.add(volume, arcname="_data")
    with tarfile.open(box_archive, "w:gz") as archive:
        archive.add(store, arcname="owners")

    # Failures after any of these writes must restore the original schema and
    # marker as well as the owner bytes. Never require the destination schema
    # to exist in the live half-moved volume to restore the source generation.
    (volume / ".layout.json").write_text(json.dumps({
        "layout": 2, "split": "owner-split", "state": "migrating", "migration": OWNER_BOX_MIGRATION,
    }))
    shutil.copytree(volume / "a1", store / "300001/a1")
    if interruption in {"box_records", "removed_host_tree"}:
        _sql(volume / ".broker/state/owner-identities.db", """
            CREATE TABLE box_records (principal TEXT, machine INT, project_id INT, bhard_bytes INT);
            INSERT INTO box_records VALUES ('alice',300001,300001,1073741824);
        """)
    if interruption == "removed_host_tree":
        shutil.rmtree(volume / "a1")
        _sql(volume / ".tinyassets.db", "DELETE FROM agent_turns WHERE owner_user_id='alice'")

    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_restore import restore_old_layout
    _archive(data_archive, data_archive.read_bytes())
    restored = restore_old_layout(data_archive, tmp_path / "old-layout")
    assert _fingerprint(restored) == before
    assert (store / "300001/a1/notes.txt").read_text() == "private history for a1"
    assert (restored / "a1/notes.txt").read_text() == "private history for a1"
    assert json.loads((restored / ".layout.json").read_text())["state"] == "stable"

    assert json.loads((volume / ".layout.json").read_text())["state"] == "migrating"


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C4')
