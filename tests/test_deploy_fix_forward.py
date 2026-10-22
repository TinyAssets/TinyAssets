"""C4 synthetic executable recovery proofs; no host or network access.

Fix forward: dispatch deploy-prod with image_tag=<fixed-commit> and
on_post_commit_failure=hold. A stable committed layout bypasses migration;
pre_traffic resumes canaries, owner roll, admission consumption and first backup.
Rollback is pre_traffic-only and restores the preserved original bundle with
its recorded image. Legacy eligibility without a preserved bundle fails closed.
"""

import json
import os
import subprocess

import pytest
import yaml

from tests.test_deploy_bundle_transaction import (
    NEW_IMAGE,
    OLD_IMAGE,
    REPO,
    _daemon_starts,
    _owner_box_cutover,
    _result,
)
from tests.test_deploy_bundle_transaction import box as box
from tests.test_owner_cutover_census import source as source

pytestmark = [
    pytest.mark.skipif(os.name == "nt", reason="POSIX deploy proof; run via linux_oracle"),
    pytest.mark.verifies("owner-daemon-in-box:C4"),
]


def committed(box):
    volume, env, _, _ = _owner_box_cutover(box)
    (volume / ".layout.json").write_text(json.dumps({
        "layout": 1, "state": "stable", "box_layout": "owner-box",
    }))
    control = box.state_dir / "cutover"
    control.mkdir(mode=0o700)
    state = control / "state.json"
    state.write_text(json.dumps({"state": "pre_traffic", "backup": "synthetic",
                                 "sha256": "synthetic", "old_image": OLD_IMAGE}))
    state.chmod(0o600)
    box.stage_bundle()
    return volume, env, control


@pytest.mark.parametrize("reason", ["mcp=failure", "app=failure", "owner_boxes=failure"])
def test_after_commit_failure_holds_without_stopping_or_restoring(box, reason):
    _, env, control = committed(box)
    before = (box.runtime / "compose.yml").read_bytes()
    result = box.run("--post-deploy-failure", NEW_IMAGE,
                     POST_COMMIT_REASON=reason, **env)
    assert result.returncode == 3
    assert _result(result) == "post_commit_held", result.stderr
    assert reason in result.stderr
    assert "gh workflow run deploy-prod.yml" in result.stdout
    assert "tinyassets.cutover_control rollback" in result.stdout
    assert not _daemon_starts(box)
    assert "stop " not in box.docker_calls_text()
    assert (box.runtime / "compose.yml").read_bytes() == before
    assert json.loads((control / "state.json").read_text())["state"] == "pre_traffic"


def test_rollback_policy_routes_to_control_not_image_rollback(box):
    _, env, control = committed(box)
    # Spy only on the module boundary; all other Python and docker calls run.
    real_python = subprocess.check_output(["which", "python3"], text=True).strip()
    shim = box.bin / "python3"
    log = box.root / "control-call"
    shim.write_text("#!/bin/sh\n"
                    'if [ "$1 $2" = "-m tinyassets.cutover_control" ]; then\n'
                    f'  printf "%s\\n" "$*" > "{log}"; exit 0\nfi\n'
                    f'exec "{real_python}" "$@"\n')
    shim.chmod(0o755)
    result = box.run("--post-deploy-failure", NEW_IMAGE,
                     ON_POST_COMMIT_FAILURE="rollback", **env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert _result(result) == "cutover_rolled_back"
    assert f"rollback --state {control}" in log.read_text()
    assert "--restore-bundle" not in box.docker_calls_text()
    assert not _daemon_starts(box)


def test_fix_forward_on_pretraffic_skips_moves_and_resumes_acceptance(box):
    _, env, control = committed(box)
    result = box.run(NEW_IMAGE, **env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _result(result) == "deployed"
    assert "cutover_admission_pending=true" in result.stdout
    assert "tinyassets.box_layout_migration apply" not in box.docker_calls_text()
    assert "tinyassets.agent_file_migration apply" not in box.docker_calls_text()
    assert _daemon_starts(box)
    assert (box.runtime / "compose.yml").read_bytes() == (box.stage / "compose.yml").read_bytes()
    assert json.loads((control / "state.json").read_text())["state"] == "pre_traffic"
    workflow = yaml.safe_load((REPO / ".github/workflows/deploy-prod.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    names = [s.get("name", "") for s in steps]
    ordered = ["Public MCP canary (--assert-handles)", "Public app-surface probe",
               "Roll owner boxes and accept the canary box",
               "Consume cutover eligibility and open ordinary admission after acceptance",
               "Take the first platform backup of the new layout"]
    assert [names.index(n) for n in ordered] == sorted(names.index(n) for n in ordered)
    for name in ordered[-2:]:
        assert steps[names.index(name)]["if"] == (
            "steps.deploy.outputs.cutover_admission_pending == 'true'")


def test_failed_fix_forward_keeps_candidate_bundle(box):
    _, env, _ = committed(box)
    box.set_docker_state(daemon_health="unhealthy")
    result = box.run(NEW_IMAGE, **env)
    assert _result(result) == "post_commit_held", result.stdout + result.stderr
    assert (box.runtime / "compose.yml").read_bytes() == (box.stage / "compose.yml").read_bytes()
    assert "TINYASSETS_IMAGE=" + NEW_IMAGE in box.env_file.read_text()


def test_explicit_image_rollback_cannot_touch_committed_bundle(box):
    _, env, _ = committed(box)
    before = (box.runtime / "compose.yml").read_bytes()
    result = box.run("--restore-bundle", OLD_IMAGE, **env)
    assert _result(result) == "post_commit_held", result.stdout + result.stderr
    assert (box.runtime / "compose.yml").read_bytes() == before
    assert not _daemon_starts(box)


@pytest.mark.parametrize("mismatch", ["root-user", "static-image"])
def test_mixed_bundle_and_owner_box_image_refuses_before_start(box, mismatch):
    _, env, _ = committed(box)
    # No stage: exercise the start guard, not staged-bundle validation.
    import shutil

    compose = (box.stage / "compose.yml").read_text()
    if mismatch == "root-user":
        compose = compose.replace('user: "1001:1001"', 'user: "0:0"', 1)
    else:
        rendered = yaml.safe_load(compose)
        rendered["services"]["daemon"]["image"] = OLD_IMAGE
        compose = yaml.safe_dump(rendered)
    (box.runtime / "compose.yml").write_text(compose)
    shutil.rmtree(box.stage)
    result = box.run(NEW_IMAGE, **env)
    assert result.returncode == 3
    assert "bundle/image layout mismatch" in result.stderr
    assert not _daemon_starts(box)
    assert "stop " not in box.docker_calls_text()


def test_workflow_failure_handler_defaults_hold_and_reports_reason():
    workflow = yaml.safe_load((REPO / ".github/workflows/deploy-prod.yml").read_text())
    trigger = workflow.get("on", workflow.get(True))
    policy = trigger["workflow_dispatch"]["inputs"]["on_post_commit_failure"]
    assert policy["default"] == "hold"
    assert policy["options"] == ["hold", "rollback"]
    steps = workflow["jobs"]["deploy"]["steps"]
    handler = next(s for s in steps if s.get("id") == "post_commit_failure")
    assert "steps.deploy.outputs.rc != ''" in handler["if"]
    assert "--post-deploy-failure" in handler["run"]
    assert "--restore-bundle" not in handler["run"]
    assert "owner_boxes" in handler["env"]["POST_COMMIT_REASON"]
    incident = next(s for s in steps if s.get("id") == "deploy_failed")
    assert "FAILURE_REASON" in incident["env"]


def test_full_rollback_after_retry_restores_original_data_bundle_and_image(box, source,
                                                                        monkeypatch):
    import tarfile
    from types import SimpleNamespace

    from tests.test_box_layout_migration import _archive
    from tinyassets import cutover_control
    from tinyassets.cutover_control import initialize, rollback_live

    # This new file runs in CI's ordinary venue. Its synthetic files belong to
    # that uid; impersonate only the control module's root metadata checks.
    # The --as-root oracle run and test_owner_cutover_restore exercise the real
    # root ownership checks without this adapter. Locks/fsync/restore stay real.
    if os.geteuid() != 0:
        monkeypatch.setattr(cutover_control, "os", SimpleNamespace(**(vars(os) | {
            "geteuid": lambda: 0,
            "fstat": lambda fd: SimpleNamespace(st_uid=0, st_mode=os.fstat(fd).st_mode),
        })))

    old_compose = box.valid_compose().replace('user: "1001:1001"', 'user: "0:0"', 1)
    (box.runtime / "compose.yml").write_text(old_compose)
    (box.runtime / "deploy/compose.yml").write_text(old_compose)
    box.stage_bundle()
    first = box.run(NEW_IMAGE)
    assert first.returncode == 0, first.stdout + first.stderr
    original_pointer = box.pointer.read_text()
    backup = box.root / "original.tar.gz"
    with tarfile.open(backup, "w:gz") as archive:
        archive.add(source, arcname="_data")
    _archive(backup, backup.read_bytes())
    control = box.state_dir / "cutover"
    initialize(control, backup=backup, old_image=OLD_IMAGE)
    volume, env, _, _ = _owner_box_cutover(box)
    (volume / ".layout.json").write_text(json.dumps({
        "layout": 2, "state": "stable", "box_layout": "owner-box",
    }))
    box.set_docker_state(known_layouts={
        NEW_IMAGE: {"layouts": [1, 2], "boxes": ["owner-box"]},
        OLD_IMAGE: {"layouts": [1, 2], "boxes": ["owner-split"]},
    })
    retry = box.run(NEW_IMAGE, **env)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert box.pointer.read_text() != original_pointer
    assert (control / "bundle/compose.yml").read_text() == old_compose
    for key, value in box.env(**env).items():
        monkeypatch.setenv(key, value)
    rollback_live(control, volume, deploy_script=REPO / "deploy/deploy_fail_safe.sh")
    assert box.env_image() == OLD_IMAGE
    assert (box.runtime / "compose.yml").read_text() == old_compose
    assert (volume / "a1/notes.txt").read_bytes() == (source / "a1/notes.txt").read_bytes()
    assert json.loads((volume / ".layout.json").read_text())["split"] == "owner-split"
    assert json.loads((control / "state.json").read_text())["state"] == "consumed"


def test_legacy_cutover_without_original_bundle_refuses_before_stopping(box, monkeypatch):
    from tinyassets.cutover_control import CutoverRefused, rollback_live

    volume, env, control = committed(box)
    box.pointer.write_text(str(box.root / "untrusted-latest-snapshot"))
    for key, value in box.env(**env).items():
        monkeypatch.setenv(key, value)
    with pytest.raises(CutoverRefused, match="original cutover bundle unavailable"):
        rollback_live(control, volume, deploy_script=REPO / "deploy/deploy_fail_safe.sh")
    assert not box.systemctl_calls.exists()
    assert not box.docker_calls.exists()


def test_fix_forward_preflight_failure_holds_bundle_and_admission(box):
    _, env, control = committed(box)
    result = box.run(NEW_IMAGE, CORE_CANARY_PROVISIONED="0", **env)
    assert _result(result) == "post_commit_held", result.stdout + result.stderr
    assert (box.runtime / "compose.yml").read_bytes() == (box.stage / "compose.yml").read_bytes()
    assert not _daemon_starts(box)
    assert json.loads((control / "state.json").read_text())["state"] == "pre_traffic"
