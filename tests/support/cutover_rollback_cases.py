"""Lane C: execute the rollback transaction with real archives on root Linux."""

import json
import os
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


@pytest.fixture
def xfs_store(tmp_path):
    """Production has a separate owner-store device, with real numeric owners."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    backing = tmp_path / "store.xfs"
    with backing.open("wb") as stream:
        stream.truncate(512 * 1024 * 1024)
    subprocess.run(["mkfs.xfs", "-f", str(backing)], check=True, capture_output=True)
    loop = subprocess.check_output(
        ["losetup", "--find", "--show", str(backing)], text=True).strip()
    store = tmp_path / "owners"
    store.mkdir()
    mounted = False
    try:
        subprocess.run(["mount", loop, str(store)], check=True)
        mounted = True
        assert store.stat().st_dev != store.parent.stat().st_dev
        yield store
    finally:
        if mounted:
            subprocess.run(["umount", str(store)], check=True)
        subprocess.run(["losetup", "--detach", loop], check=True)


@pytest.fixture(autouse=True)
def empty_boxhost(box):
    # Exercise production census/lock through an external runtime command.
    # Only the runsc process protocol is substituted; filesystem guards are real.
    runtime = box.bin / "runsc"
    runtime.write_text('#!/bin/sh\nprintf "[]\\n"\n')
    runtime.chmod(0o755)
    box_state = box.root / "host-state"
    box_state.mkdir(mode=0o700)
    os.environ["BOX_HOST_STATE"] = str(box_state)
    os.environ["BOX_RUNSC"] = str(runtime)
    yield box_state, runtime
    os.environ.pop("BOX_HOST_STATE", None)
    os.environ.pop("BOX_RUNSC", None)


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
@pytest.mark.parametrize("failure", ["apply", "after_initialize", "restore_transient", "reused",
                                     "accepted_marker", "consumed"])
def test_failed_one_shot_automatically_restores_data_store_and_harness(box, source, staged,
                                                                   failure, xfs_store):
    volume, env, _, harness = _owner_box_cutover(box)
    shutil.copytree(source, volume, dirs_exist_ok=True)
    os.chown(volume / "a1/notes.txt", 300001, 300001)
    before = _fingerprint(volume)
    before_inode = (volume / "a1/notes.txt").stat().st_ino
    store = xfs_store
    env["BOX_STORE_DIR"] = str(store)
    env["FAKE_DOCKER_BOX_STORE_DIR"] = str(store)
    if failure == "reused":
        import hashlib

        image_id = "sha256:" + hashlib.sha256(NEW_IMAGE.encode()).hexdigest()
        reused = harness / ("harness-" + image_id.removeprefix("sha256:"))
        reused.mkdir()
        (reused / ".box-image-id").write_text(image_id)
        (harness / "harness-current").symlink_to(reused.name)
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
    if argv[:1] == ["start"]:
        open(os.path.join(os.environ["FAKE_DOCKER_VOLUME_DIR"], "accepted-write"),
             "w").write("old daemon accepted this write")
    if argv[:1] == ["create"]:'''))
    elif failure == "restore_transient":
        docker.write_text(body.replace('    if argv[:1] == ["create"]:', '''
    if "up" in argv and "daemon" in argv and not state.get("failed_once"):
        state["failed_once"] = True
        save_state(state)
        return 1
    if argv[:1] == ["create"]:'''))
    box.set_docker_state(box_migration_apply="fail")
    if failure == "accepted_marker":
        box.set_docker_state(box_migration_apply="commit")
        env["FAKE_RM_FAIL_MATCH"] = str(box.state_dir / "bundle-dirty")
    if staged or failure == "accepted_marker":
        box.stage_bundle()
    result = box.run(NEW_IMAGE, **env)
    if failure == "accepted_marker":
        assert result.returncode == 3, result.stdout + result.stderr
        assert _result(result) == "marker_clear_failed", result.stdout + result.stderr
        assert _deployed_image(result) == NEW_IMAGE
        assert (volume / "a1/notes.txt").read_text() == "mutated candidate bytes"
        assert (volume / "a1/notes.txt").stat().st_uid == 300001
        assert json.loads((volume / ".layout.json").read_text())["box_layout"] == "owner-box"
        assert (store / "300001.img").read_bytes() == b"failed candidate"
        receipt = json.loads((box.state_dir / "cutover/state.json").read_text())
        assert receipt["state"] == "pre_traffic"
        assert box.docker_state_json()["containers"]["tinyassets-daemon"]["status"] == "running"
        return
    assert result.returncode == 2, result.stdout + result.stderr
    assert _result(result) == "rolled_back", result.stdout + result.stderr
    assert _deployed_image(result) == OLD_IMAGE
    assert "start tinyassets-daemon" not in box.docker_calls_text()
    assert _fingerprint(volume) == before
    if failure == "after_initialize":
        assert (volume / "a1/notes.txt").stat().st_ino == before_inode
    assert list(store.iterdir()) == []
    assert (volume / "a1/notes.txt").stat().st_uid == 300001
    assert not list(harness.glob("harness-*"))
    assert not (box.state_dir / "cutover").exists()
    receipts = list(box.state_dir.glob("cutover-rolled-back-*/state.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["state"] == "consumed"
    assert box.docker_state_json()["containers"]["tinyassets-daemon"]["status"] == "running"
    # A fresh attempt can initialize its own eligibility and complete without
    # deleting stale images, locks, a harness or a consumed receipt by hand.
    if failure == "consumed":
        receipts[0].parent.rename(box.state_dir / "cutover")
        (store / "300001.img").write_bytes(b"left by earlier manual rollback")
        (store / ".locks").mkdir()
        (store / ".cutover-shared").mkdir()
        (store / ".cutover-store-crash").mkdir()
    box.set_docker_state(box_migration_apply="commit")
    env.pop("CUTOVER_ROLLBACK_FAULT", None)
    retry = box.run(NEW_IMAGE, **env)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert _deployed_image(retry) == NEW_IMAGE
    if failure == "consumed":
        assert "previous rollback cleaned and archived" in retry.stdout


def test_another_attempt_cannot_replay_pretraffic_authority(cutover, tmp_path):
    from tinyassets.cutover_control import CutoverRefused, rollback

    control, _ = cutover
    with pytest.raises(CutoverRefused, match="another deploy"):
        rollback(control, expected_backup=tmp_path / "other",
                 stop_new=lambda: pytest.fail("stopped"), install_old=None, start_old=None)


@pytest.mark.parametrize("label", ["", "production", "Synthetic", "synthetic"])
def test_fault_is_only_admitted_on_labeled_synthetic_volume(source, tmp_path, monkeypatch, label):
    from tests.test_box_layout_migration import _archive
    from tinyassets.cutover_control import CutoverRefused, initialize

    backup = tmp_path / "backup.tar.gz"
    _archive(backup, b"verified synthetic bytes")
    monkeypatch.setenv("CUTOVER_ROLLBACK_FAULT", "after_initialize")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, label + "\n", ""))
    control = tmp_path / "control"
    synthetic = label == "synthetic"
    with pytest.raises(CutoverRefused, match="injected" if synthetic else "synthetic volume"):
        initialize(control, backup=backup, old_image=OLD_IMAGE)
    assert control.exists() == synthetic


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


def test_store_restore_retains_archived_legacy_bytes_links_and_modes(cutover, tmp_path, xfs_store):
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
    os.chown(legacy, 300001, 300001)
    os.chown(original, 300001, 300001)
    archive = backup.with_name(backup.name.replace("snapshot", "box-store"))
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(legacy.parent, arcname="owners")
    _archive(archive, archive.read_bytes())
    store = xfs_store
    (store / ".locks").mkdir()
    _restore_box_store(backup, store)
    restored = store / "300001/notes"
    assert restored.read_bytes() == original.read_bytes()
    assert restored.stat().st_mode & 0o777 == 0o640
    assert (restored.stat().st_uid, restored.stat().st_gid) == (300001, 300001)
    assert (store / "300001").stat().st_uid == 300001
    assert (store / "300001/link").readlink() == Path("notes")
    assert (store / "300001/hardlink").stat().st_ino == restored.stat().st_ino
    assert not (store / ".locks").exists()


@pytest.mark.parametrize("unsafe", [None, "new-layout", "unstable", "running", "registered",
                                    "image", "foreign-image", "attached", "host-active",
                                    "owner-process", "new-owner-directory"])
def test_consumed_retry_cleans_only_safe_attempt(cutover, source, xfs_store, empty_boxhost,
                                               unsafe):
    from tinyassets.boxes.host_service import host_lock
    from tinyassets.cutover_control import CutoverRefused, _write, finish_rollback, initialize

    control, backup = cutover
    host_state, runsc = empty_boxhost
    state = json.loads((control / "state.json").read_text())
    _write(control / "state.json", {**state, "state": "consumed"})
    store = xfs_store
    (store / "300001.img").write_bytes(b"candidate bytes")
    for name in (".locks", ".cutover-shared", ".cutover-store-interrupted"):
        (store / name).mkdir()
        (store / name / ("300001" if name == ".locks" else "payload")).write_bytes(b"residue")
    old_image = state["old_image"]
    if unsafe in {"new-layout", "unstable"}:
        marker = json.loads((source / ".layout.json").read_text())
        marker.update({"box_layout": "owner-box"} if unsafe == "new-layout"
                      else {"state": "migrating"})
        (source / ".layout.json").write_text(json.dumps(marker))
    if unsafe == "running":
        runsc.write_text("#!/bin/sh\necho '[{\"id\":\"owner-300001\","
                         "\"status\":\"running\"}]'\n")
    if unsafe == "registered":
        (host_state / "bundles/owner-300001").mkdir(parents=True)
    if unsafe == "image":
        old_image = "different-image"
    if unsafe == "foreign-image":
        (store / "399999.img").write_bytes(b"unrelated")
    if unsafe == "new-owner-directory":
        (store / "300003").mkdir()
        (store / "300003/new-write").write_bytes(b"accepted after rollback")
    attached = None
    if unsafe == "attached":
        with (store / "300001.img").open("wb") as stream:
            stream.truncate(1024 * 1024)
        attached = subprocess.check_output(
            ["losetup", "--find", "--show", str(store / "300001.img")], text=True).strip()
    owner_process = None
    if unsafe == "owner-process":
        owner_process = subprocess.Popen(["sleep", "60"], user=300001, group=300001)
    before_data, before_store = _fingerprint(source), _fingerprint(store)
    backup_bytes = backup.read_bytes()

    def cleanup():
        return finish_rollback(control, source, store=store, old_image=old_image,
                               host_state=host_state, runsc=runsc)

    try:
        if unsafe:
            if unsafe == "host-active":
                with host_lock(host_state):
                    with pytest.raises(CutoverRefused, match="boxhostd is active"):
                        cleanup()
            else:
                with pytest.raises(CutoverRefused):
                    cleanup()
            assert _fingerprint(store) == before_store
            assert control.exists()
        else:
            cleanup()
            assert not control.exists()
            assert list(store.iterdir()) == []
            receipts = list(control.parent.glob(control.name + "-rolled-back-*/state.json"))
            assert len(receipts) == 1
            assert json.loads(receipts[0].read_text())["state"] == "consumed"
            initialize(control, backup=backup, old_image=old_image)
            assert json.loads((control / "state.json").read_text())["state"] == "pre_traffic"
        assert _fingerprint(source) == before_data
        assert backup.read_bytes() == backup_bytes
    finally:
        if owner_process is not None:
            owner_process.terminate()
            owner_process.wait(timeout=10)
        if attached:
            subprocess.run(["losetup", "--detach", attached], check=True)


@pytest.mark.parametrize("legacy", [False, True])
def test_manual_rollback_cleans_and_archives(cutover, source, xfs_store, empty_boxhost,
                                           tmp_path, monkeypatch, legacy):
    from tests.test_box_layout_migration import _archive
    from tinyassets import cutover_control

    control, backup = cutover
    host_state, runsc = empty_boxhost
    archive = backup.with_name(backup.name.replace("snapshot", "box-store"))
    with tarfile.open(archive, "w:gz") as stream:
        entry = tarfile.TarInfo("owners")
        entry.type = tarfile.DIRTYPE
        stream.addfile(entry)
        if legacy:
            original = tmp_path / "300002"
            original.mkdir()
            (original / "legacy").write_text("old deletion target")
            stream.add(original, arcname="owners/300002")
    _archive(archive, archive.read_bytes())
    before = _fingerprint(source)
    (source / "a1/notes.txt").write_text("failed candidate writes")
    (xfs_store / "300001.img").write_bytes(b"candidate")
    (xfs_store / ".cutover-shared").mkdir()
    accepted = []
    real_run = subprocess.run

    def commands(args, **kwargs):
        if args[0] in {"systemctl", "docker"}:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "bash":
            assert args[-1] == "old-image-digest"
            assert _fingerprint(source) == before
            accepted.append(args[-1])
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", commands)
    cutover_control.rollback_live(control, source, deploy_script=tmp_path / "deploy.sh",
                                  store=xfs_store, host_state=host_state, runsc=runsc)
    assert accepted == ["old-image-digest"]
    if legacy:
        assert (xfs_store / "300002/legacy").read_text() == "old deletion target"
        assert [entry.name for entry in xfs_store.iterdir()] == ["300002"]
    else:
        assert list(xfs_store.iterdir()) == []
    assert not control.exists()
    assert list(control.parent.glob(control.name + "-rolled-back-*/state.json"))
    assert _fingerprint(source) == before


@pytest.mark.parametrize("interrupted_archive", [False, True])
@pytest.mark.parametrize("refused", [False, True])
def test_deploy_after_successful_admit_preserves_store_and_daemon(
        box, cutover, source, xfs_store, interrupted_archive, refused):
    from tinyassets.cutover_control import admit, close_admission

    control, backup = cutover
    control.rename(box.state_dir / "cutover")
    control = box.state_dir / "cutover"
    volume, env, _, _ = _owner_box_cutover(box)
    shutil.copytree(source, volume, dirs_exist_ok=True)
    marker = volume / ".layout.json"
    marker.write_text(json.dumps({"layout": 2, "state": "stable", "box_layout": "owner-box"}))
    close_admission(volume)
    admit(control, volume)
    assert not control.exists()
    receipt, = box.state_dir.glob("cutover-committed-*/state.json")
    assert json.loads(receipt.read_text())["state"] == "consumed"
    if interrupted_archive:
        receipt.parent.rename(control)
    # These are live owner writes, never rollback residue; neither cleanup nor
    # restore may touch them even though the receipt records a different image.
    (xfs_store / "300001.img").write_bytes(b"accepted owner writes")
    env["BOX_STORE_DIR"] = str(xfs_store)
    env["FAKE_DOCKER_BOX_STORE_DIR"] = str(xfs_store)
    before = _fingerprint(volume), _fingerprint(xfs_store), backup.read_bytes()
    if refused:
        box.set_docker_state(migration_probe_error="synthetic later deploy refusal")
    result = box.run(NEW_IMAGE, **env)
    assert result.returncode == (1 if refused else 0), result.stdout + result.stderr
    assert _result(result) == ("migration_refused" if refused else "deployed")
    assert not control.exists()
    assert (_fingerprint(volume), _fingerprint(xfs_store), backup.read_bytes()) == before
    assert box.docker_state_json()["containers"]["tinyassets-daemon"]["status"] == "running"


def test_missing_initialization_receipt_never_grants_cleanup(cutover, source, xfs_store,
                                                           empty_boxhost):
    from tinyassets.cutover_control import CutoverRefused, cancel_initialization, finish_rollback

    control, backup = cutover
    (control / "state.json").unlink()
    cancel_initialization(control, backup=backup)
    before = _fingerprint(source), _fingerprint(xfs_store), backup.read_bytes()
    host_state, runsc = empty_boxhost
    with pytest.raises(CutoverRefused, match="unreadable"):
        finish_rollback(control, source, store=xfs_store, old_image=OLD_IMAGE,
                        host_state=host_state, runsc=runsc)
    assert (_fingerprint(source), _fingerprint(xfs_store), backup.read_bytes()) == before


@pytest.mark.parametrize("in_flight, receipt", [
    (False, True), (True, True),
    # initialize failed before creating its directory: nothing owns the
    # restart, so the refusal itself must bring the stopped daemon back.
    (True, False),
])
def test_refusal_restart_depends_on_this_attempt_not_receipt_directory(box, in_flight, receipt):
    # Exercise the real refusal handler after a stopped snapshot. A stale
    # committed receipt must not suppress startup; an active cutover must.
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "deploy/deploy_fail_safe.sh").read_text()
    handler = script[script.index("cutover_in_flight() {"):script.index('TURNS_PROBE="')]
    if receipt:
        (box.state_dir / "cutover").mkdir()
    containers = box.docker_state_json()["containers"]
    containers["tinyassets-daemon"]["status"] = "exited"
    box.set_docker_state(containers=containers)
    result = subprocess.run(
        ["bash", "-c", 'log() { :; }; err() { :; }; discard_candidate_harness() { :; };\n'
         + handler + '\nmigration_refused "synthetic backup refusal"'],
        env=box.env(DAEMON_CONTAINER="tinyassets-daemon", INSTALLED_THIS_RUN="0",
                    CUTOVER_INITIALIZE_STARTED=str(int(in_flight)),
                    BUNDLE_STATE_DIR=str(box.state_dir),
                    MIGRATION_DETAIL_FILE=str(box.root / "detail")),
        capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert _result(result) == "migration_refused"
    assert box.docker_state_json()["containers"]["tinyassets-daemon"]["status"] == (
        "exited" if in_flight and receipt else "running")


@pytest.mark.parametrize("receipt_kind", ["rollback", "wrong-image", "committed"])
def test_fail_safe_cleans_before_migration_preflight(box, cutover, source, xfs_store,
                                                empty_boxhost, receipt_kind):
    from tinyassets.cutover_control import _write

    control, _ = cutover
    state = json.loads((control / "state.json").read_text())
    _write(control / "state.json", {**state, "state": "consumed",
                                    "old_image": OLD_IMAGE if receipt_kind == "rollback"
                                    else NEW_IMAGE})
    if receipt_kind == "committed":
        from tinyassets.router_traffic import NAME

        (source / ".layout.json").write_text(json.dumps(
            {"layout": 2, "state": "stable", "box_layout": "owner-box"}))
        (source / NAME).write_text('{"ordinary": true}')
    control.rename(box.state_dir / "cutover")
    (xfs_store / "300001.img").write_bytes(b"residue")
    (xfs_store / ".locks").mkdir()
    docker = box.bin / "docker"
    body = docker.read_text()
    body = body.replace('        if "tinyassets.agent_file_migration" in argv:', '\n'.join([
        '        if "tinyassets.agent_file_migration" in argv:',
        '            receipt = os.path.join(os.environ["BUNDLE_STATE_DIR"], "cutover")',
        '            assert not os.path.isdir(receipt)',
        '            if os.environ["RECEIPT_KIND"] != "committed":',
        '                assert not os.listdir(os.environ["BOX_STORE_DIR"])',
        '            print("migration_preflight=ok")',
    ]))
    docker.write_text(body)
    before = _fingerprint(source)
    before_store = _fingerprint(xfs_store)
    result = box.run(NEW_IMAGE, BOX_STORE_DIR=str(xfs_store),
                     RECEIPT_KIND=receipt_kind, FAKE_DOCKER_VOLUME_DIR=str(source))
    if receipt_kind != "wrong-image":
        assert result.returncode == 0, result.stdout + result.stderr
        assert (result.stdout.index("cleaned and archived")
                < result.stdout.index("migration_preflight=ok"))
    else:
        assert result.returncode != 0
        assert "image mismatch" in result.stderr
        assert "migration_preflight=ok" not in result.stdout
        assert (box.state_dir / "cutover/state.json").exists()
        assert (xfs_store / "300001.img").read_bytes() == b"residue"
    assert _fingerprint(source) == before
    if receipt_kind == "committed":
        assert _fingerprint(xfs_store) == before_store
        assert list(box.state_dir.glob("cutover-committed-*/state.json"))
