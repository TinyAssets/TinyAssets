"""Local cutover snapshots are pruned before a deploy checks disk headroom."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backup_snapshot_retention.py"
OLD = "2026-10-12T00-00-00Z"
NEW = "2026-10-13T00-00-00Z"
PARTIAL = "2026-10-14T00-00-00Z"


def name(kind: str, stamp: str, checksum: bool = False) -> str:
    return f"tinyassets-{kind}-{stamp}.tar.gz" + (".sha256" if checksum else "")


def put(directory: Path, filename: str, size: int = 3, age_s: float = 2 * 86400) -> None:
    path = directory / filename
    path.write_bytes(b"x" * size)
    then = time.time() - age_s
    os.utime(path, (then, then))


def run(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--directory", str(directory), *args],
        capture_output=True, text=True,
    )


def test_keeps_latest_complete_pair_and_its_box_pair(tmp_path: Path) -> None:
    for stamp in (OLD, NEW):
        for kind in ("snapshot", "box-store"):
            put(tmp_path, name(kind, stamp))
            put(tmp_path, name(kind, stamp, True))
    leftovers = [
        name("snapshot", PARTIAL),
        name("box-store", PARTIAL),
        name("box-store", PARTIAL, True),
        name("snapshot", "2026-10-11T00-00-00Z", True),
    ]
    for filename in leftovers:
        put(tmp_path, filename)
    put(tmp_path, "tinyassets-data-2026-10-11T00-00-00Z.tar.gz")
    put(tmp_path, "other-file.tar.gz")

    before = {p.name for p in tmp_path.iterdir()}
    dry = run(tmp_path)
    assert dry.returncode == 0, dry.stderr
    assert {p.name for p in tmp_path.iterdir()} == before
    removed = {name(kind, OLD, suffix) for kind in ("snapshot", "box-store")
               for suffix in (False, True)} | set(leftovers)
    assert all(f"would delete {filename} 3 bytes" in dry.stdout for filename in removed)
    assert "would_free=24 bytes" in dry.stdout

    applied = run(tmp_path, "--apply")
    assert applied.returncode == 0, applied.stderr
    assert {p.name for p in tmp_path.iterdir()} == before - removed
    assert "freed=24 bytes" in applied.stdout


def test_orphan_box_next_to_latest_data_is_removed(tmp_path: Path) -> None:
    put(tmp_path, name("snapshot", NEW))
    put(tmp_path, name("snapshot", NEW, True))
    put(tmp_path, name("box-store", NEW))
    assert run(tmp_path, "--apply").returncode == 0
    assert {p.name for p in tmp_path.iterdir()} == {
        name("snapshot", NEW), name("snapshot", NEW, True)
    }


def test_fresh_incomplete_set_may_still_be_written_and_is_kept(tmp_path: Path) -> None:
    put(tmp_path, name("snapshot", NEW))
    put(tmp_path, name("snapshot", NEW, True))
    put(tmp_path, name("snapshot", PARTIAL), age_s=60)  # a cutover tar in progress
    assert run(tmp_path, "--apply").returncode == 0
    assert name("snapshot", PARTIAL) in {p.name for p in tmp_path.iterdir()}


@pytest.mark.parametrize("unsafe", ["symlink", "directory"])
def test_refuses_unsafe_managed_file_without_deleting_anything(
    tmp_path: Path, unsafe: str,
) -> None:
    old = name("snapshot", OLD)
    put(tmp_path, old)
    put(tmp_path, name("snapshot", OLD, True))
    target = tmp_path / name("snapshot", NEW)
    if unsafe == "symlink":
        try:
            target.symlink_to(tmp_path / old)
        except OSError as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
    else:
        target.mkdir()
    result = run(tmp_path, "--apply")
    assert result.returncode != 0
    assert "refusing non-regular" in result.stderr
    assert (tmp_path / old).exists()


def test_deploy_and_timer_ship_and_run_before_image_prune() -> None:
    workflow = (ROOT / ".github/workflows/deploy-prod.yml").read_text()
    step = workflow.split("- name: Prune daemon images and require pull headroom", 1)[1]
    step = step.split("- name: Wait for in-flight turns", 1)[0]
    assert step.index("scripts/backup_snapshot_retention.py") < step.index(
        "scripts/daemon_image_retention.py"
    )
    assert step.index("${snapshot_helper} --apply") < step.index(
        "${helper} --apply --prepare-pull"
    )
    installer = (ROOT / "deploy/install-host-uptime-services.sh").read_text()
    service = (ROOT / "deploy/tinyassets-prune.service").read_text()
    build = (ROOT / ".github/workflows/build-image.yml").read_text()
    assert "scripts/backup_snapshot_retention.py" in installer
    assert "ExecStartPre=/usr/bin/python3 /opt/tinyassets-host-uptime/current/" \
           "scripts/backup_snapshot_retention.py --apply" in service
    assert "'scripts/backup_snapshot_retention.py'" in build
