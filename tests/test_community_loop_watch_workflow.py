"""Tests for .github/workflows/community-loop-watch.yml."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "community-loop-watch.yml"

pytestmark = pytest.mark.skipif(
    not _YAML_AVAILABLE, reason="pyyaml not installed"
)


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(wf: dict, job: str) -> list[dict]:
    return wf.get("jobs", {}).get(job, {}).get("steps", [])


def _alarm_script(wf: dict) -> str:
    step = next(
        (
            s for s in _steps(wf, "alarm-sink")
            if s.get("name") == "Manage community-loop-red issue"
        ),
        None,
    )
    assert step is not None, "alarm-sink must manage the community-loop-red issue"
    return str(step.get("with", {}).get("script", ""))


def test_alarm_sink_can_dispatch_actions():
    wf = _load()
    permissions = wf.get("permissions", {})
    assert permissions.get("actions") == "write", (
        "community-loop-watch needs actions: write to dispatch stale uptime canary checks"
    )
    assert permissions.get("pull-requests") is None


def test_alarm_sink_dispatches_only_stale_uptime_canary_workflow():
    wf = _load()
    script = _alarm_script(wf)
    retired_workflows = [
        "wiki-" + "bug-sync.yml",
        "auto-" + "fix-bug.yml",
        "auto-" + "check-pr.yml",
    ]
    assert "createWorkflowDispatch" in script
    assert "uptime-canary.yml" in script
    for workflow_id in retired_workflows:
        assert workflow_id not in script
    assert "has not run successfully" in script


def test_workflow_triggers_exclude_retired_cheat_loop_workflows():
    wf = _load()
    triggers = wf.get(True, wf.get("on", {}))
    workflow_run = triggers.get("workflow_run", {})
    workflows = workflow_run.get("workflows", [])

    assert "Uptime canary" in workflows
    assert "deploy-site-react" in workflows
    assert "Deploy prod" in workflows
    retired_names = [
        "Wiki " + "bug sync",
        "Auto-" + "fix change",
        "Auto-" + "check PR",
    ]
    for workflow_name in retired_names:
        assert workflow_name not in workflows


def test_alarm_sink_retries_transient_issue_api_failures():
    wf = _load()
    script = _alarm_script(wf)
    assert "const retryDelaysMs = [1000, 3000, 7000]" in script
    assert "isRetryableGitHubError" in script
    assert "githubCall('append RED alarm comment'" in script
    assert "githubCall('open RED alarm issue'" in script
    assert "githubCall('append recovered alarm comment'" in script
    assert "githubCall('close recovered alarm issue'" in script


@pytest.mark.parametrize("overall,stage_status,workflow,summary,followup,expected", [
    ("yellow", "yellow", "uptime-canary.yml", "has not run successfully", False,
     ["uptime-canary.yml", "community-loop-watch.yml"]),
    ("yellow", "yellow", "uptime-canary.yml", "has not run successfully", True,
     ["uptime-canary.yml"]),
    ("yellow", "yellow", "deploy-prod.yml", "has not run successfully", False, []),
    ("yellow", "yellow", "uptime-canary.yml", "latest run is in_progress", False, []),
    ("yellow", "unknown", "uptime-canary.yml", "has not run successfully", False, []),
    ("unknown", "yellow", "uptime-canary.yml", "has not run successfully", False, []),
])
def test_yellow_stale_canary_recovers_without_incident_mutations(
    overall, stage_status, workflow, summary, followup, expected,
):
    """Execute the actual alarm sink with recording GitHub stubs, no network."""
    env = {
        "OVERALL": overall,
        "PROBE_MSG": json.dumps({"stages": [{
            "status": stage_status, "summary": summary,
            "details": {"workflow_id": workflow},
        }]}),
    }
    harness = r"""
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const github = {rest: {
  actions: {createWorkflowDispatch: async (args) => calls.push(args)},
  issues: new Proxy({}, {get() {throw new Error('unexpected incident mutation');}}),
}};
const context = {repo: {owner: 'fixture', repo: 'fixture'}, payload: {
  inputs: {self_heal_followup: input.followup ? 'true' : 'false'},
}};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('github', 'context', 'core', 'process', 'console', input.script)(
  github, context, {warning(){}}, {env: input.env}, {log(){}},
).then(() => process.stdout.write(JSON.stringify(calls)))
 .catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps({"script": _alarm_script(_load()), "env": env, "followup": followup}),
        text=True, capture_output=True, timeout=10, check=True,
    )
    calls = json.loads(result.stdout)
    assert [call["workflow_id"] for call in calls] == expected
    assert all(call["ref"] == "main" for call in calls)
    if "community-loop-watch.yml" in expected:
        assert calls[-1]["inputs"] == {"self_heal_followup": "true"}
