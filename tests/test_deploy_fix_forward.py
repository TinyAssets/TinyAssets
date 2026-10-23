"""C4 synthetic executable recovery proofs; no host or network access.

Fix forward: dispatch deploy-prod with image_tag=<fixed-commit> and
on_post_commit_failure=hold. A stable committed layout bypasses migration;
pre_traffic resumes canaries, owner roll, admission consumption and first backup.
Full data rollback is pre_traffic-only and restores the preserved original bundle with
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
    assert "-d -o 1001 -g 1102 -m 2770 -- " in box.install_calls_text()
    assert "chown 1001:1102 -- " in box.install_calls_text()
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

    # A later public-check failure must use ordinary bundle/image recovery,
    # even if the dispatch still requests the cutover rollback policy.
    recovered = box.run("--post-deploy-failure", OLD_IMAGE,
                        IMAGE_ROLLBACK_ALLOWED="1", ROLLBACK_IMAGE=OLD_IMAGE,
                        ON_POST_COMMIT_FAILURE="rollback", **env)
    assert recovered.returncode == 2, recovered.stdout + recovered.stderr
    assert _result(recovered) == "rolled_back"
    assert box.env_image() == OLD_IMAGE


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
    box.set_docker_state(turns_rc=10)
    result = box.run(NEW_IMAGE, **env)
    assert _result(result) == "post_commit_held", result.stdout + result.stderr
    assert (box.runtime / "compose.yml").read_bytes() == (box.stage / "compose.yml").read_bytes()
    assert not _daemon_starts(box)
    assert json.loads((control / "state.json").read_text())["state"] == "pre_traffic"


def test_consumed_owner_box_deploy_without_canary_warns_and_continues(box):
    _, env, control = committed(box)
    state = json.loads((control / "state.json").read_text())
    (control / "state.json").write_text(json.dumps({**state, "state": "consumed"}))

    result = box.run(NEW_IMAGE, CORE_CANARY_PROVISIONED="0", **env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _result(result) == "deployed"
    assert "WARNING: core canary is not provisioned" in result.stdout
    assert "::warning::core canary is not provisioned" in result.stderr
    assert "box layout: already owner-box; no move" in result.stdout
    calls = box.docker_calls_text()
    assert "box_layout_migration apply" not in calls
    assert "agent_file_migration apply" not in calls
    assert not list((box.root / "backups").glob("*.tar.gz"))
    assert _daemon_starts(box)
    assert json.loads((control / "state.json").read_text())["state"] == "consumed"


@pytest.mark.parametrize("receipt", ["consumed", "archived"])
@pytest.mark.parametrize("failure", ["health", "public", "manual"])
def test_completed_cutover_restores_normal_image_rollback(box, receipt, failure):
    _, env, control = committed(box)
    # Both releases support owner-box and both bundles run the non-root daemon.
    old_compose = box.valid_compose() + "\n# previous owner-box release\n"
    (box.runtime / "compose.yml").write_text(old_compose)
    box.set_docker_state(
        active_box_layout={NEW_IMAGE: "owner-box", OLD_IMAGE: "owner-box"},
        known_layouts={image: {"layouts": [1, 2], "boxes": ["owner-box"]}
                       for image in (NEW_IMAGE, OLD_IMAGE)},
    )
    state = json.loads((control / "state.json").read_text())
    (control / "state.json").write_text(json.dumps({**state, "state": "consumed"}))
    if receipt == "archived":
        control.rename(control.with_name("cutover-archive"))
    if failure == "health":
        box.set_docker_state(unhealthy_images=[NEW_IMAGE])
        result = box.run(NEW_IMAGE, **env)
        assert result.returncode == 2, result.stdout + result.stderr
    else:
        deployed = box.run(NEW_IMAGE, **env)
        assert deployed.returncode == 0, deployed.stdout + deployed.stderr
        assert "cutover_admission_pending=true" not in deployed.stdout
        if failure == "public":
            result = box.run("--post-deploy-failure", NEW_IMAGE,
                             IMAGE_ROLLBACK_ALLOWED="1", ROLLBACK_IMAGE=OLD_IMAGE,
                             ON_POST_COMMIT_FAILURE="rollback", **env)
            assert result.returncode == 2, result.stdout + result.stderr
        else:
            result = box.run("--restore-bundle", OLD_IMAGE, **env)
            assert result.returncode == 0, result.stdout + result.stderr
    assert _result(result) == ("deployed" if failure == "manual" else "rolled_back")
    assert box.env_image() == OLD_IMAGE
    assert (box.runtime / "compose.yml").read_text() == old_compose


@pytest.mark.parametrize("receipt", ["malformed", "missing", "unknown"])
@pytest.mark.parametrize("entry", ["health", "public", "manual"])
def test_unreadable_cutover_state_cannot_fall_through_to_rollback(box, receipt, entry):
    _, env, control = committed(box)
    state = control / "state.json"
    if receipt == "missing":
        state.unlink()
    else:
        state.write_text("{" if receipt == "malformed" else '{"state": "unknown"}')
    before = (box.runtime / "compose.yml").read_bytes()
    if entry == "health":
        box.set_docker_state(unhealthy_images=[NEW_IMAGE])
        result = box.run(NEW_IMAGE, **env)
        expected = (box.stage / "compose.yml").read_bytes()
    else:
        args = ("--restore-bundle", OLD_IMAGE) if entry == "manual" else (
            "--post-deploy-failure", NEW_IMAGE)
        result = box.run(*args, IMAGE_ROLLBACK_ALLOWED="1", ROLLBACK_IMAGE=OLD_IMAGE, **env)
        expected = before
    assert result.returncode == 3, result.stdout + result.stderr
    assert _result(result) == "rollback_needs_restore"
    assert (box.runtime / "compose.yml").read_bytes() == expected


def test_source_env_file_exports_an_unquoted_private_key(tmp_path):
    env = tmp_path / "env"
    env.write_text(
        "\n# comment\n"
        "BACKUP_DEST=spaces:/snapshots/owner-snapshots\n"
        "BOX_KEY=-----BEGIN PRIVATE KEY-----\n"
        'export QUOTED="-----BEGIN PRIVATE KEY-----"\n'
        "not a assignment\n",
        encoding="utf-8",
    )
    script = REPO / "deploy/deploy_fail_safe.sh"
    text = script.read_text(encoding="utf-8")
    assert 'source "$ENV_FILE"' not in text
    assert '. "$ENV_FILE"' not in text
    assert text.count('source_env_file "$ENV_FILE"') == 3
    completed = subprocess.run(
        ["bash", "-c", """
set -euo pipefail
eval "$(sed -n '/^source_env_file() {$/,/^}$/p' "$1")"
source_env_file "$2"
printf 'BACKUP_DEST=%s\\n' "$BACKUP_DEST"
printf 'BOX_KEY=%s\\n' "$BOX_KEY"
printf 'QUOTED=%s\\n' "$QUOTED"
""", "bash", str(script), str(env)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PRIVATE: command not found" not in completed.stderr
    assert "BACKUP_DEST=spaces:/snapshots/owner-snapshots" in completed.stdout
    assert "BOX_KEY=-----BEGIN PRIVATE KEY-----" in completed.stdout
    assert "QUOTED=-----BEGIN PRIVATE KEY-----" in completed.stdout
