"""Paired box-store archive and restore against a disposable store."""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux host tools required")


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("remote_restore", [False, True])
def test_box_store_archive_and_temp_restore(tmp_path: Path, remote_restore: bool) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    volume = tmp_path / "volume" / "_data"
    volume.mkdir(parents=True)
    broker = volume / ".broker"
    broker.mkdir()
    uid, gid = os.getuid(), os.getgid()
    (broker / "box-registry.json").write_text(json.dumps({"boxes": [{
        "uid": uid, "gid": gid, "project_id": 123456, "bhard_bytes": 8 * 1024 * 1024,
    }]}))
    (volume / "record.txt").write_text("data")
    store = tmp_path / "owners"
    owner = store / str(uid)
    owner.mkdir(parents=True)
    (owner / "box.txt").write_text("box")
    (owner / "link.txt").symlink_to("box.txt")
    (store / ".box-preflight-scratch").mkdir()
    (store / ".tinyassets-box-restore-old.retained").mkdir()
    remote = tmp_path / "remote"
    remote.mkdir()
    log = tmp_path / "commands.log"
    _shim(
        bin_dir, "docker",
        'case "$1 $2" in "volume inspect") echo "$TEST_VOLUME_DIR";; '
        '"ps -q") :;; *) :;; esac\n',
    )
    _shim(bin_dir, "findmnt", '''
if [[ "${@: -1}" == FSTYPE ]]; then echo xfs; else echo rw,pquota,nodiscard; fi
''')
    _shim(bin_dir, "xfs_freeze", 'echo "$1" >> "$TEST_COMMAND_LOG"\n')
    _shim(bin_dir, "xfs_quota", '''
echo "$*" >> "$TEST_COMMAND_LOG"
if [[ "${FAIL_RESTORED_QUOTA:-0}" == 1 &&
      "$*" == *"project -s -p ${BOX_STORE_DIR}/${TEST_UID} "* &&
      ! -e "$TEST_QUOTA_FAIL_MARKER" ]]; then
    touch "$TEST_QUOTA_FAIL_MARKER"
    exit 1
fi
''')
    _shim(bin_dir, "systemctl", 'echo "$*" >> "$TEST_COMMAND_LOG"\n')
    _shim(bin_dir, "mv", '''
if [[ "${FAIL_DATA_SWAP:-0}" == 1 && "$*" == *".tinyassets-restore-stage."* &&
      "${@: -1}" == "$TEST_VOLUME_DIR" ]]; then
    exit 1
fi
exec /bin/mv "$@"
''')
    _shim(bin_dir, "rclone", '''
case "$1" in
  copyto) cp "${@: -2:1}" "${@: -1}" ;;
  rcat) cat > "${@: -1}" ;;
  cat) cat "${@: -1}" ;;
  ls) test -f "${@: -1}" ;;
  lsf) : ;;
  deletefile) : ;;
  *) exit 1 ;;
esac
''')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "TEST_VOLUME_DIR": str(volume), "TEST_COMMAND_LOG": str(log),
           "BOX_STORE_DIR": str(store), "BACKUP_VOLUME": "test-volume",
           "BACKUP_DEST": str(remote), "BACKUP_LOG": str(tmp_path / "backup.log"),
           "GH_TOKEN": "", "TEST_UID": str(uid),
           "TEST_QUOTA_FAIL_MARKER": str(tmp_path / "quota-failed")}
    backup = subprocess.run(["bash", str(ROOT / "deploy/backup.sh")], env=env,
                            capture_output=True, text=True)
    assert backup.returncode == 0, backup.stdout + backup.stderr
    assert log.read_text().splitlines()[:2] == ["-f", "-u"]
    data_archive, = remote.glob("tinyassets-data-*.tar.gz")
    box_archive, = remote.glob("tinyassets-box-store-*.tar.gz")
    os.mkfifo(owner / "unsupported.pipe")
    invalid = subprocess.run(["bash", str(ROOT / "deploy/backup.sh")], env=env,
                             capture_output=True, text=True)
    assert invalid.returncode == 2
    assert "box-store archive validation failed" in invalid.stdout
    (owner / "unsupported.pipe").unlink()
    (volume / "record.txt").write_text("changed")
    (owner / "box.txt").write_text("changed")
    env.update(BACKUP_FILE=str(data_archive), BOX_BACKUP_FILE=str(box_archive),
               BACKUP_LOG=str(tmp_path / "restore.log"))
    restore_args = []
    if remote_restore:
        del env["BACKUP_FILE"]
        del env["BOX_BACKUP_FILE"]
        timestamp = data_archive.name.removeprefix("tinyassets-data-").removesuffix(".tar.gz")
        restore_args = [f"--timestamp={timestamp}"]
    restore = subprocess.run(
        ["bash", str(ROOT / "deploy/backup-restore.sh"), *restore_args],
        env=env, capture_output=True, text=True,
    )
    assert restore.returncode == 0, restore.stdout + restore.stderr
    assert (volume / "record.txt").read_text() == "data"
    assert (owner / "box.txt").read_text() == "box"
    assert (owner / "link.txt").read_text() == "box"
    assert "bhard=8192k 123456" in log.read_text()
    assert "report -p -n" in log.read_text()
    assert "project -C -p" in log.read_text()
    commands = log.read_text().splitlines()
    assert commands.index("stop boxhostd.service") < next(
        i for i, line in enumerate(commands) if "project -C -p" in line)
    assert list(store.glob(".tinyassets-box-restore-old.*/" + str(uid) + "/box.txt"))
    with tarfile.open(box_archive, "r:gz") as archive:
        assert all(member.name == "owners" or member.name.startswith(f"owners/{uid}")
                   for member in archive)
    (volume / "record.txt").write_text("current")
    (owner / "box.txt").write_text("current-box")
    env["FAIL_RESTORED_QUOTA"] = "1"
    failed = subprocess.run(
        ["bash", str(ROOT / "deploy/backup-restore.sh"), *restore_args],
        env=env, capture_output=True, text=True,
    )
    assert failed.returncode == 4, failed.stdout + failed.stderr
    assert (volume / "record.txt").read_text() == "current"
    assert (owner / "box.txt").read_text() == "current-box"
    assert log.read_text().count("bhard=8192k 123456") >= 2
    env.pop("FAIL_RESTORED_QUOTA")
    env["FAIL_DATA_SWAP"] = "1"
    failed_data = subprocess.run(
        ["bash", str(ROOT / "deploy/backup-restore.sh"), *restore_args],
        env=env, capture_output=True, text=True,
    )
    assert failed_data.returncode == 4, failed_data.stdout + failed_data.stderr
    assert (volume / "record.txt").read_text() == "current"
    assert (owner / "box.txt").read_text() == "current-box"
    env.pop("FAIL_DATA_SWAP")
    box_archive.unlink()
    env.pop("BOX_BACKUP_FILE", None)
    missing_pair = subprocess.run(
        ["bash", str(ROOT / "deploy/backup-restore.sh"), *restore_args],
        env=env, capture_output=True, text=True,
    )
    assert missing_pair.returncode == 2
    assert "paired box-store archive missing" in missing_pair.stdout
    env["ALLOW_PRE_STORE_BACKUP"] = "1"
    legacy = subprocess.run(
        ["bash", str(ROOT / "deploy/backup-restore.sh"), *restore_args],
        env=env, capture_output=True, text=True,
    )
    assert legacy.returncode == 0, legacy.stdout + legacy.stderr
    assert "pre-store data archive" in legacy.stdout
    assert (volume / "record.txt").read_text() == "data"
    assert (owner / "box.txt").read_text() == "current-box"


def test_store_unit_contract_and_installer_guards() -> None:
    unit = (ROOT / "deploy/boxhostd.service").read_text()
    mount = (ROOT / "deploy/var-lib-boxhostd-owners.mount").read_text()
    prepare = (ROOT / "deploy/box-store-device.service").read_text()
    device = (ROOT / "deploy/box-store-device.sh").read_text()
    # The idle service survives G4's controlled unmount; ExecStartPre still
    # refuses to start unless the store is mounted and healthy.
    assert "Wants=var-lib-boxhostd-owners.mount" in unit
    assert "ExecStartPre=/opt/tinyassets/boxhostd/box-store-check.sh" in unit
    assert "Options=pquota,noatime,nodiscard" in mount
    assert "DefaultDependencies=no" in prepare
    assert "@BOX_STORE_DEVICE_DEPENDENCIES@" in prepare
    assert "fallocate -l" in device and "reserve=$((6 * 1024 * 1024 * 1024))" in device
    assert "discard_max_bytes" in device and "--direct-io=on" in device
    assert "mkfs.xfs" in device and "if [[ ! -e \"$device\" ]]" in device
    assert device.index('blkid -p -s TYPE') < device.index('mkfs.xfs "$device"')
    assert 'wipefs -n --noheadings --output TYPE' in device
    for name in ("box-store-device.sh", "box-store-unit.sh", "box-store-device-stop.sh",
                 "box-store-check.sh", "install-boxhostd-preflight.sh", "backup.sh",
                 "backup-restore.sh"):
        result = subprocess.run(["bash", "-n", str(ROOT / "deploy" / name)], check=False)
        assert result.returncode == 0, name


def test_pinned_device_unit_and_missing_device_failure(tmp_path: Path) -> None:
    template = ROOT / "deploy/box-store-device.service"
    renderer = ROOT / "deploy/box-store-unit.sh"
    volume = "/dev/disk/by-id/scsi-0DO_Volume_tinyassets-box-store"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    unit = r"dev-disk-by\x2did-scsi\x2d0DO_Volume_tinyassets\x2dbox\x2dstore.device"
    _shim(bin_dir, "systemd-escape", 'printf "%s\\n" "$TEST_DEVICE_UNIT"\n')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "TEST_DEVICE_UNIT": unit}
    rendered = subprocess.run(["bash", str(renderer), volume, str(template)],
                              capture_output=True, text=True, check=True, env=env).stdout
    assert f"Requires={unit}" in rendered
    assert f"After={unit}" in rendered
    loop = subprocess.run(["bash", str(renderer), "/var/lib/boxhostd/boxes.img",
                           str(template)], capture_output=True, text=True, check=True,
                          env=env).stdout
    assert ".device" not in loop
    script = ROOT / "deploy/box-store-device.sh"
    check = subprocess.run(["bash", "-c", 'source "$1"; box_store_require_block "$2"',
                            "bash", str(script), "/dev/disk/by-id/never-present"],
                           capture_output=True, text=True)
    assert check.returncode != 0
    assert "missing" in check.stderr


@pytest.mark.parametrize(
    ("filesystem", "options", "expected"),
    [
        ("xfs", "rw,pquota,noatime,nodiscard", 0),
        ("xfs", "rw,noatime,nodiscard", 1),
        ("xfs", "rw,pquota,noatime", 1),
        ("ext4", "rw,pquota,nodiscard", 1),
    ],
)
def test_store_check_requires_all_mount_properties(
    tmp_path: Path, filesystem: str, options: str, expected: int
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _shim(bin_dir, "findmnt", '''
if [[ "${@: -1}" == FSTYPE ]]; then echo "$TEST_FS"; else echo "$TEST_OPTIONS"; fi
''')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "TEST_FS": filesystem, "TEST_OPTIONS": options}
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/box-store-check.sh"), str(tmp_path / "owners")],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == expected


def test_box_archive_rejects_extraction_through_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        root = tarfile.TarInfo("owners/1000")
        root.type = tarfile.DIRTYPE
        output.addfile(root)
        link = tarfile.TarInfo("owners/1000/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        output.addfile(link)
        payload = tarfile.TarInfo("owners/1000/link/payload")
        payload.size = 0
        output.addfile(payload)
    result = subprocess.run(
        ["python3", str(ROOT / "deploy/box-store-archive.py"), str(archive)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "traverses a symlink" in result.stderr
