"""Shared-layer recovery and retained outer-store installation contracts."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
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
def test_shared_archive_restore_leaves_owner_volumes_untouched(tmp_path, remote_restore):
    from tests.test_box_restore_authority import fingerprint, seed

    volume = tmp_path / "volume/_data"
    seed(volume)
    (volume / "record.txt").write_text("backup shared bytes")
    remote = tmp_path / "remote"
    remote.mkdir()
    archive = remote / "tinyassets-data-2026-10-17T00-00-00Z.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(volume, arcname="_data")
    import hashlib

    manifest_dir = remote / "platform"
    manifest_dir.mkdir()
    (manifest_dir / "tinyassets-platform-2026-10-17T00-00-00Z.json").write_text(json.dumps({
        "version": 1, "archive": archive.name, "snapshots": [],
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }))
    owners = tmp_path / "owners"
    owners.mkdir()
    (owners / "300001.img").write_bytes(b"current opaque owner volume")
    before = fingerprint(owners)
    (volume / "record.txt").write_text("newer shared bytes")
    binary = tmp_path / "bin"
    binary.mkdir()
    _shim(
        binary,
        "docker",
        'case "$1 $2" in "volume inspect") echo "$TEST_VOLUME";; "ps -q") :;; *) exit 79;; esac\n',
    )
    _shim(binary, "systemctl", 'echo "$*" >> "$TEST_COMMANDS"\n')
    _shim(
        binary,
        "rclone",
        'case "$1" in copyto) cp "${@: -2:1}" "${@: -1}";; '
        'ls) test -f "${@: -1}";; '
        'lsf) find "${@: -1}" -type f -printf "%f\\n";; '
        'cat) cat "${@: -1}";; *) exit 79;; esac\n',
    )
    for command in ("findmnt", "xfs_quota", "xfs_freeze"):
        _shim(binary, command, 'echo "owner store accessed" >&2; exit 79\n')
    _shim(
        binary,
        "mv",
        'if [[ "${FAIL_DATA_SWAP:-0}" == 1 && "$*" == *".tinyassets-restore-stage."* '
        '&& "${@: -1}" == "$TEST_VOLUME" ]]; then exit 1; fi\n'
        'exec /bin/mv "$@"\n',
    )
    wrapper = binary / "python3"
    wrapper.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import os, runpy, sys
from pathlib import Path
if len(sys.argv) > 1 and sys.argv[1].endswith('/platform-recovery.py'):
    sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[1]))
    from tinyassets import restore_recovery as recovery
    if os.environ.get('TEST_AUTHORITIES') == 'available':
        async def recovered(*args, **kwargs):
            return True
        recovery.resume_deletion_jobs = recovered
        recovery.reconcile_authority = recovered
        from tinyassets import router_account_deletion as identity
        identity.workos_user_exists = lambda owner: owner == 'alice'
        identity.revoke_workos_sessions = lambda owner: None
    if '--finish' in sys.argv:
        if not (os.environ.get('TEST_AUTHORITIES') == 'available'):
            raise recovery.RecoveryUnavailable('synthetic startup authority unavailable')
        import json, sqlite3
        from types import SimpleNamespace
        root = Path(sys.argv[sys.argv.index('--current') + 1])
        layout = json.loads((root / '.layout.json').read_text())
        server = SimpleNamespace(_data_root=root, _owner_identities=SimpleNamespace(
            _connect=lambda **kwargs: sqlite3.connect(root / '.broker/state/owner-identities.db')))
        import asyncio
        server.routing = SimpleNamespace(changed=recovered)
        asyncio.run(recovery.platform_recovery(
            server, backup_time=layout['recovery']['backup_time'],
            now=layout['recovery']['restore_time']))
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
"""
    )
    wrapper.chmod(0o755)
    log = tmp_path / "commands"
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "BACKUP_VOLUME": "synthetic-platform",
        "TEST_VOLUME": str(volume),
        "BOX_STORE_DIR": str(owners),
        "BACKUP_LOG": str(tmp_path / "restore.log"),
        "TEST_COMMANDS": str(log),
        "BACKUP_DEST": str(remote),
    }
    args = ["--timestamp=2026-10-17T00-00-00Z"] if remote_restore else []
    if not remote_restore:
        env["BACKUP_FILE"] = str(archive)

    def restore(**updates):
        return subprocess.run(
            ["bash", str(ROOT / "deploy/backup-restore.sh"), *args],
            env={**env, **updates},
            capture_output=True,
            text=True,
        )

    dry = restore(DRY_RUN="1")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert (volume / "record.txt").read_text() == "newer shared bytes"
    assert not log.exists()
    pending = restore()
    assert pending.returncode == 4, pending.stdout + pending.stderr
    assert "synthetic startup authority unavailable" in pending.stderr
    assert (volume / "record.txt").read_text() == "backup shared bytes"
    assert json.loads((volume / ".layout.json").read_text())["state"] == "recovering"
    assert fingerprint(owners) == before
    completed = restore(TEST_AUTHORITIES="available")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (volume / "record.txt").read_text() == "backup shared bytes"
    assert fingerprint(owners) == before
    with sqlite3.connect(volume / ".api-keys.sqlite3") as db:
        assert db.execute("SELECT * FROM api_keys").fetchall() == []
    assert log.read_text().splitlines() == ["stop boxhostd.service", "start boxhostd.service"] * 2
    assert list(volume.parent.glob(".tinyassets-restore-old.*/record.txt"))
    (volume / "record.txt").write_text("retained on failed swap")
    failed = restore(TEST_AUTHORITIES="available", FAIL_DATA_SWAP="1")
    assert failed.returncode == 4, failed.stdout + failed.stderr
    assert (volume / "record.txt").read_text() == "retained on failed swap"
    assert fingerprint(owners) == before
    assert json.loads((volume / ".layout.json").read_text())["state"] == "recovering"
    archive.write_bytes(b"corrupt archive")
    damaged = restore()
    assert damaged.returncode == 4, damaged.stdout + damaged.stderr
    assert (volume / "record.txt").read_text() == "retained on failed swap"
    assert fingerprint(owners) == before
    assert log.read_text().splitlines() == (
        ["stop boxhostd.service", "start boxhostd.service"] * 2 + ["stop boxhostd.service"])


def test_store_unit_contract_and_installer_guards() -> None:
    unit = (ROOT / "deploy/boxhostd.service").read_text()
    mount = (ROOT / "deploy/var-lib-boxhostd-owners.mount").read_text()
    prepare = (ROOT / "deploy/box-store-device.service").read_text()
    device = (ROOT / "deploy/box-store-device.sh").read_text()
    # The box service requires the mounted store and checks its quota options.
    assert "Requires=var-lib-boxhostd-owners.mount" in unit
    assert "ExecStartPre=/opt/tinyassets/boxhostd/box-store-check.sh" in unit
    assert "Options=pquota,noatime,nodiscard" in mount
    assert "DefaultDependencies=no" in prepare
    assert "@BOX_STORE_DEVICE_DEPENDENCIES@" in prepare
    assert "fallocate -l" in device and "reserve=$((6 * 1024 * 1024 * 1024))" in device
    assert "discard_max_bytes" in device and "--direct-io=on" in device
    assert "mkfs.xfs" in device and 'if [[ ! -e "$device" ]]' in device
    assert device.index("blkid -p -s TYPE") < device.index('mkfs.xfs -m \'reflink=1\' "$device"')
    assert "wipefs -n --noheadings --output TYPE" in device
    for name in (
        "box-store-device.sh",
        "box-store-unit.sh",
        "box-store-device-stop.sh",
        "box-store-check.sh",
        "install-boxhostd-preflight.sh",
        "backup.sh",
        "backup-restore.sh",
    ):
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
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TEST_DEVICE_UNIT": unit}
    rendered = subprocess.run(
        ["bash", str(renderer), volume, str(template)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    assert f"Requires={unit}" in rendered
    assert f"After={unit}" in rendered
    loop = subprocess.run(
        ["bash", str(renderer), "/var/lib/boxhostd/boxes.img", str(template)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    assert ".device" not in loop
    script = ROOT / "deploy/box-store-device.sh"
    check = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; box_store_require_block "$2"',
            "bash",
            str(script),
            "/dev/disk/by-id/never-present",
        ],
        capture_output=True,
        text=True,
    )
    assert check.returncode != 0
    assert "missing" in check.stderr


@pytest.mark.parametrize(
    ("filesystem", "options", "expected"),
    [
        ("xfs", "rw,noatime,attr2,inode64,logbufs=8,logbsize=32k,prjquota", 0),
        ("xfs", "rw,noatime,attr2,inode64,logbufs=8,logbsize=32k,noquota", 1),
        ("xfs", "rw,noatime,attr2,inode64,logbufs=8,logbsize=32k,prjquota,discard", 1),
        ("ext4", "rw,relatime,prjquota", 1),
    ],
)
def test_store_check_requires_all_mount_properties(
    tmp_path: Path, filesystem: str, options: str, expected: int
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _shim(
        bin_dir,
        "findmnt",
        """
if [[ "${@: -1}" == FSTYPE ]]; then echo "$TEST_FS"; else echo "$TEST_OPTIONS"; fi
""",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TEST_FS": filesystem,
        "TEST_OPTIONS": options,
    }
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/box-store-check.sh"), str(tmp_path / "owners")],
        env=env,
        capture_output=True,
        text=True,
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
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "traverses a symlink" in result.stderr
