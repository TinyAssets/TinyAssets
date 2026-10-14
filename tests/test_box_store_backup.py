"""Paired box-store archive and restore against a disposable store."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux host tools required")


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body)
    path.chmod(0o755)


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
