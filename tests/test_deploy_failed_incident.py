"""Deploy prod keeps one deploy-failed issue, using the shared incident module."""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_DEPLOY = _REPO / ".github" / "workflows" / "deploy-prod.yml"


def _workflow() -> dict:
    return yaml.safe_load(_DEPLOY.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["deploy"]["steps"]


def _step(name: str) -> dict:
    return next(step for step in _steps() if step.get("name") == name)


def test_failed_deploy_updates_one_issue_and_success_closes_it() -> None:
    names = [step.get("name") for step in _steps()]
    open_at = names.index("Open deploy-failed issue")
    close_at = names.index("Close deploy-failed issue")
    retire_at = names.index("Retire platform LLM logins from the host")
    assert retire_at < open_at < close_at

    failed = _step("Open deploy-failed issue")
    recovered = _step("Close deploy-failed issue")
    assert failed["if"] == "${{ failure() }}"
    assert recovered["if"] == "${{ success() }}"
    assert failed["continue-on-error"] is True
    assert recovered["continue-on-error"] is True
    assert failed["uses"] == "actions/github-script@v7"
    assert recovered["uses"] == "actions/github-script@v7"

    failed_script = failed["with"]["script"]
    recovered_script = recovered["with"]["script"]
    for script in (failed_script, recovered_script):
        assert "scripts/incident_issue.js" in script
        assert "gh issue create" not in script
        assert "owner-content-repair:" in script
        assert "${{" not in script
    assert "recordFailure" in failed_script
    assert "recordRecovery" in recovered_script
    assert "label: 'deploy-failed'" in failed_script
    assert "deploy-failed: run" in failed_script
    for field in ("result:", "commit:", "Run:"):
        assert field in failed_script
        assert field in recovered_script
    assert failed["env"]["DEPLOY_RESULT"] == "${{ steps.deploy.outputs.result }}"
    assert failed["env"]["DEPLOY_COMMIT"] == "${{ steps.tag.outputs.revision || github.sha }}"
    assert "steps.deploy.outputs.rc" in failed["env"]["DEPLOY_RC"]
    assert "steps.deploy.outputs.deployed_image" in failed["env"]["DEPLOYED_IMAGE"]


def test_content_repair_alert_stays_a_separate_issue() -> None:
    repair = _step("Alert on incomplete owner content repair")
    assert "gh issue create" in repair["run"]
    assert "owner-content-repair:" in repair["run"]
    assert "deploy-failed" in repair["run"]
    assert repair["name"] != "Open deploy-failed issue"
