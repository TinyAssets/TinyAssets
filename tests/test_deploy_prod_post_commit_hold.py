"""A red public check after a committed cutover holds; it never image-rolls-back.

Cutover attempt 10 (2026-10-22): the canary rollback reinstalled the previous
bundle under the committed owner-box layout and the new image, and the daemon
crash-looped. The rollback step must detect this run's pre_traffic cutover and
hold before any --restore-bundle call.
"""

import os
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.verifies('owner-daemon-in-box:C4')


def _rollback_step():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-prod.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    return next(s for s in steps if s.get("name") == "Roll back if the public canary is red")


def test_committed_cutover_holds_before_any_image_rollback():
    script = _rollback_step()["run"]
    # #5203's check now lives under the host lock, shared with deploy health
    # failures. There must be no independent workflow restore path.
    assert "--post-deploy-failure" in script
    assert "--restore-bundle" not in script
    assert "ConnectTimeout=20" in script
    assert "could not read this run's cutover state" in script
    deploy = (ROOT / "deploy/deploy_fail_safe.sh").read_text()
    recovery = deploy[deploy.index("# --- 1. record"):]
    assert recovery.index("guard_cutover_recovery") < recovery.index("RESTORE_BUNDLE=1")
    assert 'state["state"] == "consumed"' in deploy
    assert '"pre_traffic", "consumed"' in deploy


@pytest.mark.skipif(os.name == "nt", reason="Linux shell proof")
@pytest.mark.parametrize("rc", [1, 255])
def test_ssh_read_failure_holds_without_a_second_recovery(tmp_path, rc):
    import subprocess

    ssh = tmp_path / "ssh"
    calls = tmp_path / "calls"
    ssh.write_text(f'#!/bin/sh\necho call >> "{calls}"\nexit {rc}\n')
    ssh.chmod(0o755)
    output = tmp_path / "output"
    result = subprocess.run(["bash", "-e", "-c", _rollback_step()["run"]], env={
        **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DO_SSH_USER": "synthetic", "DO_DROPLET_HOST": "synthetic.invalid",
        "ON_POST_COMMIT_FAILURE": "hold", "POST_COMMIT_REASON": "canary=failure",
        "IMAGE_ROLLBACK_ALLOWED": "1", "PREV_IMAGE": "synthetic", "IMAGE_REF": "synthetic",
        "GITHUB_OUTPUT": str(output),
    }, capture_output=True, text=True)
    assert result.returncode != 0
    assert "HOLDING" in result.stdout
    assert "result=recovery_unavailable" in output.read_text()
    assert calls.read_text().splitlines() == ["call"]
