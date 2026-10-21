"""Lane C: execute the rollback transaction with real archives on root Linux."""

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.test_deploy_bundle_transaction import (
    NEW_IMAGE,
    OLD_IMAGE,
    _deployed_image,
    _owner_box_cutover,
    _result,
)
from tests.test_deploy_bundle_transaction import (
    box as box,
)
from tests.test_owner_cutover_census import _fingerprint
from tests.test_owner_cutover_census import source as source
from tests.test_owner_cutover_restore import cutover as cutover

pytestmark = pytest.mark.verifies('owner-daemon-in-box:C4')


def test_explicit_restore_retries_requested_image_and_returns_zero(box):
    # Production's old container is still the recorded PREV_IMAGE. A transient
    # compose failure used to reach `finish rolled_back ... 2` on the retry.
    docker = box.bin / "docker"
    body = docker.read_text()
    body = body.replace('    if argv[:1] == ["create"]:', '''
    if "up" in argv and "daemon" in argv and not state.get("failed_once"):
        state["failed_once"] = True
        save_state(state)
        return 1
    if argv[:1] == ["create"]:''')
    docker.write_text(body)
    receipt = box.state_dir / "cutover"
    receipt.mkdir(mode=0o700)
    (receipt / "state.json").write_text('{"state":"consumed"}')
    result = box.run("--restore-bundle", OLD_IMAGE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _deployed_image(result) == OLD_IMAGE
    assert box.docker_state_json()["failed_once"]
    assert json.loads((receipt / "state.json").read_text())["state"] == "consumed"


def test_explicit_restore_never_falls_back_to_the_failed_candidate(box):
    assert box.run(NEW_IMAGE).returncode == 0
    box.set_docker_state(up_fail_images=[OLD_IMAGE])
    result = box.run("--restore-bundle", OLD_IMAGE)
    assert result.returncode == 3, result.stdout + result.stderr
    assert _result(result) == "rollback_unhealthy"
    assert f"TINYASSETS_IMAGE={OLD_IMAGE}" in box.env_file.read_text()


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("failure", ["apply", "after_initialize", "restore_transient", "reused"])
def test_failed_one_shot_automatically_restores_data_store_and_harness(box, source, staged,
                                                                   failure):
    volume, env, _, harness = _owner_box_cutover(box)
    shutil.copytree(source, volume, dirs_exist_ok=True)
    before = _fingerprint(volume)
    store = box.root / "owners"
    store.mkdir()
    env["BOX_STORE_DIR"] = str(store)
    env["FAKE_DOCKER_BOX_STORE_DIR"] = str(store)
    if failure == "reused":
        import hashlib

        image_id = "sha256:" + hashlib.sha256(NEW_IMAGE.encode()).hexdigest()
        reused = harness / ("harness-" + image_id.removeprefix("sha256:"))
        reused.mkdir()
        (reused / ".box-image-id").write_text(image_id)
    # Replace only the fake backup's payload with actual synthetic trees. The
    # deploy script, cutover control, tar extraction and digest checks are real.
    backup = Path(env["BACKUP_SCRIPT"])
    body = backup.read_text()
    start = body.index('    payload = b"snapshot-member')
    end = body.index('    digest = ', start)
    body = body[:start] + '''    with tarfile.open(path, "w:gz") as archive:
        if name.startswith("tinyassets-box-store-"):
            archive.add(os.environ["BOX_STORE_DIR"], arcname="owners")
        else:
            archive.add(os.environ["FAKE_DOCKER_VOLUME_DIR"], arcname="_data")
''' + body[end:]
    backup.write_text(body)
    docker = box.bin / "docker"
    body = docker.read_text().replace(
        '            state["box_migration_apply_attempted"] = True', '''
            store = os.environ["BOX_STORE_DIR"]
            open(os.path.join(store, "300001.img"), "wb").write(b"failed candidate")
            os.mkdir(os.path.join(store, ".locks"))
            open(os.path.join(os.environ["FAKE_DOCKER_VOLUME_DIR"], "a1/notes.txt"),
                 "w").write("mutated candidate bytes")
            state["box_migration_apply_attempted"] = True''')
    docker.write_text(body)
    if failure == "after_initialize":
        env["CUTOVER_ROLLBACK_FAULT"] = failure
        docker.write_text(body.replace('    if argv[:1] == ["create"]:', '''
    if argv[:2] == ["volume", "inspect"] and "Labels" in argv[-1]:
        print("synthetic")
        return 0
    if argv[:1] == ["create"]:'''))
    elif failure == "restore_transient":
        docker.write_text(body.replace('    if argv[:1] == ["create"]:', '''
    if "up" in argv and "daemon" in argv and not state.get("failed_once"):
        state["failed_once"] = True
        save_state(state)
        return 1
    if argv[:1] == ["create"]:'''))
    box.set_docker_state(box_migration_apply="fail")
    if staged:
        box.stage_bundle()
    result = box.run(NEW_IMAGE, **env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert _result(result) == "rolled_back", result.stdout + result.stderr
    assert _deployed_image(result) == OLD_IMAGE
    assert _fingerprint(volume) == before
    assert list(store.iterdir()) == []
    assert not list(harness.glob("harness-*"))
    assert not (box.state_dir / "cutover").exists()
    receipts = list(box.state_dir.glob("cutover-rolled-back-*/state.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["state"] == "consumed"
    assert box.docker_state_json()["containers"]["tinyassets-daemon"]["status"] == "running"
    # A fresh attempt can initialize its own eligibility and complete without
    # deleting stale images, locks, a harness or a consumed receipt by hand.
    box.set_docker_state(box_migration_apply="commit")
    env.pop("CUTOVER_ROLLBACK_FAULT", None)
    retry = box.run(NEW_IMAGE, **env)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _deployed_image(retry) == NEW_IMAGE


def test_another_attempt_cannot_replay_pretraffic_authority(cutover, tmp_path):
    from tinyassets.cutover_control import CutoverRefused, rollback

    control, _ = cutover
    with pytest.raises(CutoverRefused, match="another deploy"):
        rollback(control, expected_backup=tmp_path / "other",
                 stop_new=lambda: pytest.fail("stopped"), install_old=None, start_old=None)


@pytest.mark.parametrize("label", ["", "synthetic"])
def test_fault_is_only_admitted_on_labeled_synthetic_volume(source, tmp_path, monkeypatch, label):
    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_control import CutoverRefused, initialize

    backup = tmp_path / "backup.tar.gz"
    _archive(backup, b"verified synthetic bytes")
    monkeypatch.setenv("CUTOVER_ROLLBACK_FAULT", "after_initialize")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, label + "\n", ""))
    control = tmp_path / "control"
    with pytest.raises(CutoverRefused, match="injected" if label else "synthetic volume"):
        initialize(control, backup=backup, old_image=OLD_IMAGE)
    assert control.exists() == bool(label)


@pytest.mark.parametrize("damage", ["digest", "traversal", "attached", "mounted"])
def test_store_restore_refuses_before_removing_anything(cutover, tmp_path, monkeypatch, damage):
    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_control import CutoverRefused, _restore_box_store

    _, backup = cutover
    store = tmp_path / "owners"
    store.mkdir()
    image = store / "300001.img"
    image.write_bytes(b"must remain")
    archive = backup.with_name(backup.name.replace("snapshot", "box-store"))
    with tarfile.open(archive, "w:gz") as stream:
        root = tarfile.TarInfo("owners")
        root.type = tarfile.DIRTYPE
        stream.addfile(root)
        if damage == "traversal":
            stream.addfile(tarfile.TarInfo("owners/../outside"))
    _archive(archive, archive.read_bytes())
    if damage == "digest":
        archive.write_bytes(b"corrupt")
    if damage == "attached":
        monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                            subprocess.CompletedProcess(a, 0, "/dev/loop99\n", ""))
    if damage == "mounted":
        monkeypatch.setattr("tinyassets.boxes.volumes.OwnerVolumes._mounts",
                            lambda: [(str(store / "300001"), "/dev/loop99")])
    with pytest.raises((RuntimeError, CutoverRefused)):
        _restore_box_store(backup, store)
    assert image.read_bytes() == b"must remain"


def test_store_restore_retains_archived_legacy_bytes_links_and_modes(cutover, tmp_path):
    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_control import _restore_box_store

    _, backup = cutover
    legacy = tmp_path / "legacy/owners/300001"
    legacy.mkdir(parents=True)
    original = legacy / "notes"
    original.write_bytes(b"legacy deletion job still owns these bytes")
    original.chmod(0o640)
    (legacy / "link").symlink_to("notes")
    (legacy / "hardlink").hardlink_to(original)
    archive = backup.with_name(backup.name.replace("snapshot", "box-store"))
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(legacy.parent, arcname="owners")
    _archive(archive, archive.read_bytes())
    store = tmp_path / "owners"
    store.mkdir()
    (store / ".locks").mkdir()
    _restore_box_store(backup, store)
    restored = store / "300001/notes"
    assert restored.read_bytes() == original.read_bytes()
    assert restored.stat().st_mode & 0o777 == 0o640
    assert (store / "300001/link").readlink() == Path("notes")
    assert (store / "300001/hardlink").stat().st_ino == restored.stat().st_ino
    assert not (store / ".locks").exists()
