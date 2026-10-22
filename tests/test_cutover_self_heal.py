"""Black-box proof that a failed owner-box cutover cleans up automatically.

Run in the root Linux oracle with ``--docker-engine --as-root``.  The store is
an actual, separate XFS loop mount; all contents and archives are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

from tests.test_deploy_bundle_transaction import (
    NEW_IMAGE,
    OLD_IMAGE,
    OTHER_IMAGE,
    Box,
)
from tests.test_deploy_bundle_transaction import (
    box as bundle_box,
)

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C4")
INNER = os.environ.get("TA_CUTOVER_SELF_HEAL_INNER") == "1"


@pytest.fixture(name="box")
def _box(tmp_path: Path, monkeypatch) -> Box:
    box = bundle_box.__wrapped__(tmp_path)
    host_state = box.root / "host-state"
    host_state.mkdir(mode=0o700)
    runtime = box.bin / "runsc"
    runtime.write_text('#!/bin/sh\nprintf "[]\\n"\n')
    runtime.chmod(0o755)
    monkeypatch.setenv("BOX_HOST_STATE", str(host_state))
    monkeypatch.setenv("BOX_RUNSC", str(runtime))
    return box


@pytest.mark.skipif(INNER, reason="outer oracle launcher")
def test_real_xfs_cutover_proof_in_privileged_venue(tmp_path: Path):
    if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires root Linux oracle; owner=lane-c-tests "
            "runs-in=linux_oracle.py --docker-engine --as-root"
        )
    image = os.environ["TA_REHEARSAL_BASE_IMAGE"]
    name = "ta-cutover-self-heal-" + uuid.uuid4().hex[:12]
    subprocess.run(
        ["docker", "create", "--name", name, "--privileged", "--network", "none",
         "--label", "tinyassets.disposable=true", "-e", "TA_CUTOVER_SELF_HEAL_INNER=1",
         image, "sleep", "3600"],
        check=True, capture_output=True, text=True,
    )
    try:
        subprocess.run(["docker", "start", name], check=True, capture_output=True)
        source = Path(__file__).resolve().parents[1]
        package = tmp_path / "proof"
        package.mkdir()
        for part in ("tinyassets", "deploy", "scripts", "tests"):
            shutil.copytree(source / part, package / part,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        shutil.copy2(source / "pyproject.toml", package / "pyproject.toml")
        subprocess.run(["docker", "cp", str(package), name + ":/proof"],
                       check=True, capture_output=True, text=True)
        result = subprocess.run(
            ["docker", "exec", "-w", "/proof", name, "python", "-m", "pytest",
             "-p", "no:cacheprovider", "tests/test_cutover_self_heal.py", "-q", "--tb=short"],
            capture_output=True, text=True, timeout=600,
        )
        print(result.stdout + result.stderr, flush=True)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def xfs_store(tmp_path: Path):
    if not INNER:
        pytest.skip(
            "requires privileged XFS venue; owner=lane-c-tests "
            "runs-in=linux_oracle.py --docker-engine --as-root"
        )
    store = tmp_path / "owners"
    store.mkdir()
    backing = tmp_path / "store-backing.img"
    with backing.open("wb") as stream:
        stream.truncate(512 * 1024 * 1024)
    subprocess.run(["mkfs.xfs", "-f", str(backing)], check=True, capture_output=True)
    device = subprocess.run(
        ["losetup", "--find", "--show", str(backing)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    try:
        subprocess.run(["mount", "-t", "xfs", device, str(store)], check=True)
        try:
            assert subprocess.run(
                ["findmnt", "--mountpoint", str(store), "-no", "FSTYPE"],
                check=True, capture_output=True, text=True,
            ).stdout.strip() == "xfs"
            yield store
        finally:
            subprocess.run(["umount", str(store)], check=True)
    finally:
        subprocess.run(["losetup", "--detach", device], check=True)


def _archive(box: Box) -> Path:
    """A real verified old-layout archive for the public initialize/rollback CLI."""
    original = box.root / "before-cutover"
    original.mkdir()
    (original / ".layout.json").write_text(
        '{"layout": 1, "state": "stable", "box_layout": "owner-split"}\n',
        encoding="utf-8",
    )
    (original / "synthetic.txt").write_bytes(b"original data volume bytes\n")
    (original / ".layout.lock").touch()
    identities = original / ".broker/state/owner-identities.db"
    identities.parent.mkdir(parents=True)
    with sqlite3.connect(identities) as db:
        db.executescript("CREATE TABLE owner_identities (principal TEXT, machine_id INTEGER);"
                         "INSERT INTO owner_identities VALUES ('synthetic', 300001);")
    backup = box.root / "backups" / "tinyassets-snapshot-2026-10-21T00-00-00Z.tar.gz"
    with tarfile.open(backup, "w:gz") as stream:
        stream.add(original, arcname="_data")
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    (backup.parent / (backup.name + ".sha256")).write_text(
        f"{digest}  {backup.name}\n", encoding="ascii",
    )
    paired = backup.with_name(backup.name.replace("snapshot", "box-store"))
    with tarfile.open(paired, "w:gz") as stream:
        entry = tarfile.TarInfo("owners")
        entry.type = tarfile.DIRTYPE
        stream.addfile(entry)
    paired.with_name(paired.name + ".sha256").write_text(
        f"{hashlib.sha256(paired.read_bytes()).hexdigest()}  {paired.name}\n")
    return backup


def _state(box: Box, *, consumed: bool = False, old_image: str = OLD_IMAGE) -> tuple[Path, Path]:
    backup = _archive(box)
    state = box.state_dir / "cutover"
    command = [sys.executable, "-m", "tinyassets.cutover_control", "initialize",
               "--state", str(state), "--backup", str(backup), "--old-image", old_image]
    subprocess.run(command, check=True, capture_output=True, text=True)
    if consumed:
        receipt = state / "state.json"
        document = json.loads(receipt.read_text(encoding="utf-8"))
        document["state"] = "consumed"
        receipt.write_text(json.dumps(document), encoding="utf-8")
        receipt.chmod(0o600)
    return state, backup


def _residue(store: Path) -> None:
    (store / "300001.img").write_bytes(b"synthetic owner image")
    (store / ".locks").mkdir()
    (store / ".locks" / "300001").write_bytes(b"synthetic lock")
    (store / ".cutover-shared").mkdir()
    (store / ".cutover-shared" / "payload").write_bytes(b"synthetic shared staging")
    (store / ".cutover-store-interrupted").mkdir()
    (store / ".cutover-store-interrupted" / "payload").write_bytes(b"synthetic staging")


def _store_is_empty(store: Path) -> None:
    assert sorted(p.name for p in store.iterdir() if p.name != "lost+found") == []


def _archived(state: Path) -> None:
    assert not state.exists()
    archives = [item for item in state.parent.glob(state.name + "*")
                if item.is_dir() and item != state]
    assert len(archives) == 1, archives
    assert re.search(r"\d{4}[-_]?\d{2}[-_]?\d{2}", archives[0].name), archives[0]
    assert (archives[0] / "state.json").exists()


def _snapshot(*paths: Path) -> dict[str, bytes]:
    result = {}
    for root in paths:
        for item in [root, *sorted(root.rglob("*"))]:
            result[str(item)] = item.read_bytes() if item.is_file() else b"<directory>"
    return result


def _deploy(box: Box, store: Path, *, image: str = NEW_IMAGE):
    return box.run(image, BOX_STORE_DIR=str(store),
                   FAKE_DOCKER_BOX_STORE_DIR=str(store),
                   BOX_STORE_CHECK="/bin/true")


def test_manual_rollback_cleans_store_and_archives_eligibility(box: Box, xfs_store: Path):
    state, backup = _state(box)
    _residue(xfs_store)
    data = box.root / "live-data"
    data.mkdir()
    (data / ".layout.json").write_text(
        '{"layout": 2, "state": "stable", "box_layout": "owner-box"}',
        encoding="utf-8",
    )
    (data / "synthetic.txt").write_bytes(b"failed cutover data")
    restore_calls = box.root / "restore-calls"
    deploy_script = box.root / "accept-old.sh"
    deploy_script.write_text(
        '#!/bin/sh\n[ "$1" = --restore-bundle ] && [ "$2" = "$EXPECTED_OLD_IMAGE" ] || exit 7\n'
        'printf "%s\\n" "$2" >> "$RESTORE_CALLS"\n', encoding="utf-8",
    )
    deploy_script.chmod(0o755)
    before_backup = _snapshot(backup, backup.with_name(backup.name + ".sha256"))
    result = subprocess.run(
        [sys.executable, "-m", "tinyassets.cutover_control", "rollback",
         "--state", str(state), "--data", str(data), "--deploy-script", str(deploy_script),
         "--store", str(xfs_store), "--host-state", os.environ["BOX_HOST_STATE"],
         "--runsc", os.environ["BOX_RUNSC"]],
        env=box.env(EXPECTED_OLD_IMAGE=OLD_IMAGE, RESTORE_CALLS=str(restore_calls)),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert restore_calls.read_text(encoding="utf-8").strip() == OLD_IMAGE
    assert (data / "synthetic.txt").read_bytes() == b"original data volume bytes\n"
    _store_is_empty(xfs_store)
    _archived(state)
    assert _snapshot(backup, backup.with_name(backup.name + ".sha256")) == before_backup
    subprocess.run(
        [sys.executable, "-m", "tinyassets.cutover_control", "initialize",
         "--state", str(state), "--backup", str(backup), "--old-image", OLD_IMAGE],
        check=True, capture_output=True, text=True,
    )


def test_next_deploy_self_heals_consumed_rollback(box: Box, xfs_store: Path):
    state, backup = _state(box, consumed=True)
    _residue(xfs_store)
    data = box.root / "default-data-volume"
    (data / "synthetic.txt").write_bytes(b"preserve live data")
    (data / ".layout.lock").touch()
    before_data = _snapshot(data)
    before_backup = _snapshot(backup, backup.with_name(backup.name + ".sha256"))

    result = _deploy(box, xfs_store)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "deploy_result=deployed" in result.stdout
    _store_is_empty(xfs_store)
    _archived(state)
    assert _snapshot(data) == before_data
    assert _snapshot(backup, backup.with_name(backup.name + ".sha256")) == before_backup
    subprocess.run(
        [sys.executable, "-m", "tinyassets.cutover_control", "initialize",
         "--state", str(state), "--backup", str(backup), "--old-image", OLD_IMAGE],
        check=True, capture_output=True, text=True,
    )


def test_failed_deploy_accepts_old_image_with_clean_store(box: Box, xfs_store: Path):
    state, backup = _state(box, consumed=True)
    _residue(xfs_store)
    data = box.root / "default-data-volume"
    (data / ".layout.lock").touch()
    (data / "synthetic.txt").write_bytes(b"preserve live data")
    before_data = _snapshot(data)
    before_backup = _snapshot(backup, backup.with_name(backup.name + ".sha256"))
    box.set_docker_state(unhealthy_images=[NEW_IMAGE])

    result = _deploy(box, xfs_store)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "deploy_result=rolled_back" in result.stdout
    assert box.env_image() == OLD_IMAGE
    _store_is_empty(xfs_store)
    _archived(state)
    assert _snapshot(data) == before_data
    assert _snapshot(backup, backup.with_name(backup.name + ".sha256")) == before_backup


@pytest.mark.parametrize("unsafe,reason", [
    ("new_layout", "layout"),
    ("registered_box", "box|registered"),
    ("running_box", "box|running"),
    ("image_mismatch", "image"),
    ("pre_traffic", "pre[_ -]?traffic|consumed|eligibility"),
])
def test_unsafe_cleanup_refuses_without_changes(
    box: Box, xfs_store: Path, unsafe: str, reason: str,
):
    state, backup = _state(box, consumed=unsafe != "pre_traffic",
                           old_image=OTHER_IMAGE if unsafe == "image_mismatch" else OLD_IMAGE)
    _residue(xfs_store)
    data = box.root / "default-data-volume"
    (data / "synthetic.txt").write_bytes(b"preserve live data")
    (data / ".layout.lock").touch()
    if unsafe == "new_layout":
        (data / ".layout.json").write_text(
            '{"layout": 2, "state": "stable", "box_layout": "owner-box"}',
            encoding="utf-8",
        )
    if unsafe == "registered_box":
        (Path(os.environ["BOX_HOST_STATE"]) / "bundles/owner-300001").mkdir(parents=True)
    if unsafe == "running_box":
        Path(os.environ["BOX_RUNSC"]).write_text(
            '#!/bin/sh\necho \'[{"id":"owner-300001","status":"running"}]\'\n')
    before = _snapshot(state, xfs_store, data, backup,
                       backup.with_name(backup.name + ".sha256"))
    result = _deploy(box, xfs_store)
    assert result.returncode != 0, result.stdout + result.stderr
    assert re.search(reason, (result.stdout + result.stderr).lower())
    assert _snapshot(state, xfs_store, data, backup,
                     backup.with_name(backup.name + ".sha256")) == before
