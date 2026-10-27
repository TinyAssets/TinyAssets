"""C2/C3: installed shared backup, off-host verification and snapshot retention."""
from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pytest

from tinyassets import platform_backup as backup
from tinyassets.boxes.volumes import OwnerVolumes

REPO = Path(__file__).parents[1]


def shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def shared_backup(tmp_path, monkeypatch):
    root, remote = tmp_path / "_data", tmp_path / "remote"
    (root / ".broker/state").mkdir(parents=True)
    remote.mkdir()
    (root / ".layout.lock").touch()
    (root / ".layout.json").write_text('{"box_layout":"owner-box","state":"stable"}')
    (root / "published.txt").write_text("published shared bytes")
    with sqlite3.connect(root / ".broker/state/owner-identities.db") as db:
        db.execute("CREATE TABLE owner_volumes (principal, uid, snapshot_id, snapshot_sha256)")
        db.execute("CREATE TABLE owner_snapshots (principal, snapshot_id, snapshot_sha256)")
        db.execute("INSERT INTO owner_volumes VALUES (?,?,?,?)",
                   ("alice", 300001, "a" * 32, "b" * 64))
    owners = tmp_path / "owners"
    owners.mkdir()
    (owners / "300001.img").write_bytes(b"PRIVATE OWNER VOLUME")
    binary = tmp_path / "bin"
    binary.mkdir()
    # Local files model only the transport boundary. Every executable is real
    # production code except Docker discovery and the remote object service.
    rclone = binary / "rclone"
    rclone.write_text("#!" + sys.executable + "\n" + """import os, shutil, sys
from pathlib import Path
op, *args = sys.argv[1:]
if os.environ.get('FAIL_UPLOAD') == '1' and op == 'copyto':
    raise SystemExit(3)
if op == 'mkdir':
    Path(args[-1]).mkdir(parents=True, exist_ok=True)
elif op == 'lsf' and '--include' in args:
    root, owner = Path(args[-3]), args[-1].strip('/*')
    print('\\n'.join(f'{owner}/{p.name}' for p in (root / owner).glob('*') if p.is_file()))
elif op == 'lsf':
    print('\\n'.join(p.name for p in Path(args[-1]).iterdir() if p.is_file()))
elif op == 'cat':
    print(Path(args[-1]).read_text())
elif op in ('copyto', 'moveto'):
    source, target = map(Path, args[-2:])
    if op == 'copyto':
        shutil.copyfile(source, target)
        if os.environ.get('CORRUPT_READBACK') == '1' and target.name == 'readback.tar.gz':
            target.write_bytes(b'corrupt readback')
    else:
        source.rename(target)
elif op == 'deletefile':
    # The installed rclone (before v1.62) exits 1 for a missing object.
    if not Path(args[-1]).is_file():
        raise SystemExit(1)
    Path(args[-1]).unlink()
else:
    raise SystemExit(79)
""")
    rclone.chmod(0o755)
    shim(binary, "docker", 'echo "$TEST_DATA"\n')
    shim(binary, "systemctl", 'echo "unexpected service stop" >&2; exit 79\n')
    monkeypatch.setenv("PATH", f"{binary}:{os.environ['PATH']}")
    monkeypatch.setenv("TEST_DATA", str(root))
    monkeypatch.setattr(backup, "LOCK", tmp_path / "retention.lock")
    return root, remote, owners, binary


@pytest.mark.verifies('owner-daemon-in-box:C3')
def test_installed_service_publishes_shared_backup_without_owner_bytes(shared_backup, tmp_path):
    from tests.host_uptime_helpers import _install_env, _run_installer

    root, remote, owners, binary = shared_backup
    installation = tmp_path / "installation"
    installation.mkdir()
    installed = _run_installer(_install_env(installation))
    assert installed.returncode == 0, installed.stdout + installed.stderr
    runtime = installation / "runtime/current"
    unit = (installation / "systemd/tinyassets-backup.service").read_text()
    command = next(line.removeprefix("ExecStart=") for line in unit.splitlines()
                   if line.startswith("ExecStart="))
    command = shlex.split(command.replace("/opt/tinyassets-host-uptime/current", str(runtime)))
    shim(binary, "python3", 'exec ' + shlex.quote(sys.executable) + ' -S "$@"\n')
    env = {**os.environ, "PYTHONPATH": str(runtime), "PYTHONNOUSERSITE": "1",
           "BACKUP_VOLUME": "synthetic-installed-backup", "BACKUP_DEST": str(remote),
           "BOX_STORE_DIR": str(owners), "BACKUP_LOG": str(tmp_path / "log"),
           "BACKUP_MODE": "", "DRY_RUN": "0"}
    result = subprocess.run(command, env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    records = backup.manifests(str(remote))
    assert len(records) == 1
    document = next(iter(records.values()))
    assert document["snapshots"] == [{"uid": 300001, "snapshot_id": "a" * 32, "sha256": "b" * 64}]
    with tarfile.open(remote / document["archive"]) as archive:
        assert archive.extractfile("_data/published.txt").read() == b"published shared bytes"
        assert not any("owners" in name or name.endswith(".img") for name in archive.getnames())
        assert all(b"PRIVATE OWNER VOLUME" not in archive.extractfile(member).read()
                   for member in archive if member.isfile())
    assert (owners / "300001.img").read_bytes() == b"PRIVATE OWNER VOLUME"
    assert backup.pinned_snapshots(str(remote)) == {(300001, "a" * 32)}


@pytest.mark.verifies('owner-daemon-in-box:C3')
@pytest.mark.parametrize("failure", ["FAIL_UPLOAD", "CORRUPT_READBACK"])
def test_failed_or_unverified_platform_backup_is_not_published(shared_backup, monkeypatch, failure):
    root, remote, _, _ = shared_backup
    monkeypatch.setenv(failure, "1")
    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        backup.publish(root, str(remote))
    assert backup.manifests(str(remote)) == {}


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_referenced_snapshot_survives_pruning(shared_backup, monkeypatch):
    root, remote, _, _ = shared_backup
    backup.publish(root, str(remote))
    snapshot = remote / "300001" / ("a" * 32 + ".img.gz")
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"retained snapshot")
    stale = snapshot.with_name("c" * 32 + ".img.gz")
    stale.write_bytes(b"unreferenced snapshot")
    volumes = object.__new__(OwnerVolumes)
    volumes.remote = str(remote)
    monkeypatch.setattr(volumes, "_lock", lambda uid: nullcontext())
    assert volumes.delete_snapshots(300001, ["a" * 32], []) == {"a" * 32: "pinned"}
    assert snapshot.read_bytes() == b"retained snapshot"
    assert volumes.delete_snapshots(300001, ["c" * 32], []) == {"c" * 32: "deleted"}
    assert not stale.exists()
    next((remote / "platform").glob("*.json")).write_text("corrupt catalog")
    assert volumes.delete_snapshots(300001, ["a" * 32], []) == {
        "a" * 32: "failed:catalog_unreadable"}
    assert snapshot.read_bytes() == b"retained snapshot"


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_absent_snapshot_object_is_deleted_and_a_pin_is_not(shared_backup, monkeypatch):
    """The installed rclone exits 1 for a missing object, so the listing decides."""
    root, remote, _, _ = shared_backup
    backup.publish(root, str(remote))
    (remote / "300001").mkdir()
    (remote / "300001" / ("e" * 32 + ".img.gz")).write_bytes(b"present")
    volumes = object.__new__(OwnerVolumes)
    volumes.remote = str(remote)
    monkeypatch.setattr(volumes, "_lock", lambda uid: nullcontext())
    codes, deletes = [], []

    def run(*args):
        if args[:2] == ("rclone", "deletefile"):
            deletes.append(args[-1])
            if codes:
                raise subprocess.CalledProcessError(codes.pop(0), args)
        return OwnerVolumes._run(*args)

    monkeypatch.setattr(volumes, "_run", run)
    assert volumes.delete_snapshots(300001, ["c" * 32], []) == {"c" * 32: "deleted"}
    assert deletes == []
    # A present object's failing exit is a real failure; it does not hide the pin.
    codes.append(1)
    assert volumes.delete_snapshots(300001, ["e" * 32, "a" * 32], []) == {
        "e" * 32: "failed:rclone_exit_1", "a" * 32: "pinned"}
    assert (remote / "300001" / ("e" * 32 + ".img.gz")).exists()
    # Newer rclone: the object went between the listing and the delete.
    codes.append(4)
    assert volumes.delete_snapshots(300001, ["e" * 32], []) == {"e" * 32: "deleted"}
    assert codes == [] and len(deletes) == 2


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_an_unreadable_listing_deletes_nothing_and_erasure_lists_once(
        shared_backup, monkeypatch):
    root, remote, _, _ = shared_backup
    backup.publish(root, str(remote))
    (remote / "300001").mkdir()
    objects = [remote / "300001" / (name * 32 + ".img.gz") for name in "ef"]
    for path in objects:
        path.write_bytes(b"present")
    volumes = object.__new__(OwnerVolumes)
    volumes.remote = str(remote)
    monkeypatch.setattr(volumes, "_lock", lambda uid: nullcontext())
    listings = []

    def run(*args):
        if args[:2] == ("rclone", "lsf") and "--include" in args:
            listings.append(args)
            if fail:
                raise subprocess.CalledProcessError(3, args)
        return OwnerVolumes._run(*args)

    monkeypatch.setattr(volumes, "_run", run)
    fail = True
    assert volumes.delete_snapshots(300001, ["e" * 32, "a" * 32], []) == {
        "e" * 32: "failed:catalog_unreadable", "a" * 32: "pinned"}
    assert all(path.exists() for path in objects)
    fail = False
    assert volumes.delete_snapshots(300001, None, []) == dict.fromkeys(
        ["e" * 32, "f" * 32], "deleted")
    assert not any(path.exists() for path in objects) and len(listings) == 2


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_map_capture_and_pruning_serialize(shared_backup, monkeypatch):
    root, remote, _, _ = shared_backup
    snapshot = remote / "300001" / ("a" * 32 + ".img.gz")
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"snapshot being captured")
    entered, release, deleting = threading.Event(), threading.Event(), threading.Event()
    original = backup._copy_shared

    def copying(source, target, relative=Path()):
        if source == root:
            entered.set()
            assert release.wait(10)
        return original(source, target, relative)

    monkeypatch.setattr(backup, "_copy_shared", copying)
    volumes = object.__new__(OwnerVolumes)
    volumes.remote = str(remote)
    monkeypatch.setattr(volumes, "_lock", lambda uid: nullcontext())

    def prune():
        deleting.set()
        return volumes.delete_snapshots(300001, ["a" * 32], [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(backup.publish, root, str(remote))
        assert entered.wait(10)
        pruning = pool.submit(prune)
        assert deleting.wait(10)
        assert not pruning.done()
        release.set()
        assert publication.result(timeout=15)["snapshots"]
        assert pruning.result(timeout=15) == {"a" * 32: "pinned"}
    assert snapshot.read_bytes() == b"snapshot being captured"


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_platform_retention_releases_only_withdrawn_map_pins(shared_backup):
    root, remote, _, _ = shared_backup
    document = backup.publish(root, str(remote))
    manifest = next((remote / "platform").glob("*.json"))
    original_archive = remote / document["archive"]
    document["archive"] = "tinyassets-data-2020-01-01T00-00-00Z.tar.gz"
    original_archive.rename(remote / document["archive"])
    old = manifest.with_name("tinyassets-platform-2020-01-01T00-00-00Z.json")
    old.write_text(json.dumps(document))
    manifest.unlink()
    with sqlite3.connect(root / ".broker/state/owner-identities.db") as db:
        db.execute("UPDATE owner_volumes SET snapshot_id=?", ("c" * 32,))
    assert backup.pinned_snapshots(str(remote)) == {(300001, "a" * 32)}
    latest = backup.publish(root, str(remote))
    backup.prune(str(remote), retention=(1, 0, 0))
    assert backup.pinned_snapshots(str(remote)) == {(300001, "c" * 32)}
    assert not old.exists()
    assert not (remote / document["archive"]).exists()
    assert (remote / latest["archive"]).exists()



@pytest.mark.verifies('owner-daemon-in-box:C3')
def test_shared_sqlite_copy_is_consistent_and_failure_is_fatal(shared_backup, monkeypatch):
    root, remote, _, _ = shared_backup
    with sqlite3.connect(root / "live.db") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE evidence (v)")
        db.execute("INSERT INTO evidence VALUES ('committed')")
        db.commit()
        document = backup.publish(root, str(remote))
    with tarfile.open(remote / document["archive"]) as archive:
        assert "_data/live.db-wal" not in archive.getnames()
        stage = root.parent / "restored"
        archive.extractall(stage, filter="data")
    with sqlite3.connect(stage / "_data/live.db") as db:
        assert db.execute("SELECT * FROM evidence").fetchall() == [("committed",)]
    def fail(*args):
        raise OSError("read failed")

    monkeypatch.setattr(backup, "_copy_shared", fail)
    # Remove this fixture's prior publication to allow a second timestamp in the same second.
    next((remote / "platform").glob("*.json")).unlink()
    with pytest.raises(OSError, match="read failed"):
        backup.publish(root, str(remote))
    assert backup.manifests(str(remote)) == {}

@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_erasure_preserves_a_pin_not_explicitly_released(shared_backup, monkeypatch):
    root, remote, _, _ = shared_backup
    backup.publish(root, str(remote))
    manifest = next((remote / "platform").glob("*.json"))
    document = json.loads(manifest.read_text())
    document["snapshots"].append({"uid": 300001, "snapshot_id": "c" * 32, "sha256": "d" * 64})
    manifest.write_text(json.dumps(document))
    namespace = remote / "300001"
    namespace.mkdir()
    released = namespace / ("a" * 32 + ".img.gz")
    retained = namespace / ("c" * 32 + ".img.gz")
    released.write_bytes(b"map released this reference")
    retained.write_bytes(b"unreleased pin")
    volumes = object.__new__(OwnerVolumes)
    volumes.remote = str(remote)
    monkeypatch.setattr(volumes, "_lock", lambda uid: nullcontext())
    assert volumes.delete_snapshots(300001, ["a" * 32, "c" * 32], ["a" * 32]) == {
        "a" * 32: "deleted", "c" * 32: "pinned"}
    assert not released.exists()
    assert retained.read_bytes() == b"unreleased pin"
