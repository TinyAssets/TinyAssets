"""Executable regression tests for the uptime-canary alarm sink."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "uptime-canary.yml"
_NODE = shutil.which("node")


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _alarm_script() -> str:
    step = next(
        step
        for step in _workflow()["jobs"]["alarm-sink"]["steps"]
        if step.get("id") == "gate"
    )
    return step["with"]["script"]


_HARNESS = r"""
const fs = require('fs');
const config = JSON.parse(process.env.TEST_CONFIG);
const script = fs.readFileSync(0, 'utf8');
const calls = [];
const outputs = {};
const warnings = [];
const summaries = [];
const measuredRun = {
  id: 1, run_attempt: 2, status: 'completed', head_branch: 'main', head_sha: 'abc',
  path: '.github/workflows/uptime-canary.yml', updated_at: new Date().toISOString(),
  ...config.runOverrides,
};

function record(name, result) {
  return async (args) => {
    calls.push({name, args});
    return result;
  };
}

const issues = {
  getLabel: async (args) => {
    calls.push({name: 'issues.getLabel', args});
    if (config.missingLabel) {
      const error = new Error('not found');
      error.status = 404;
      throw error;
    }
    return {data: {}};
  },
  createLabel: record('issues.createLabel', {data: {}}),
  listForRepo: record('issues.listForRepo', {data: config.openIssue ? [config.openIssue] : []}),
  createComment: record('issues.createComment', {data: {}}),
  create: record('issues.create', {data: {number: 99}}),
  update: record('issues.update', {data: {}}),
};
const github = {
  rest: {
    issues,
    actions: {
      listWorkflowRuns: record('actions.listWorkflowRuns', {
        data: {workflow_runs: config.priorRuns || (config.measuredRed ? [measuredRun] :
          config.priorRed ? [{id: 1, conclusion: 'failure'}] : [])},
      }),
      listJobsForWorkflowRunAttempt: async (args) => {
        calls.push({name: 'actions.listJobsForWorkflowRunAttempt', args});
        if (config.historyError) throw new Error('unavailable history');
        return {data: {total_count: 1, jobs: [{
          name: 'probe', run_id: 1, run_attempt: 2, status: 'completed', head_sha: 'abc',
          steps: [{name: 'Layer-1 measured red v1', status: 'completed',
                   conclusion: 'failure', ...config.stepOverrides}],
          ...config.jobOverrides,
        }]}};
      },
      getWorkflowRun: record('actions.getWorkflowRun', {data: {
        ...measuredRun, ...config.latestOverrides,
      }}),
    },
  },
};
const core = {
  setOutput: (key, value) => { outputs[key] = value; },
  warning: (message) => { warnings.push(message); },
  summary: {
    addRaw: (message) => ({ write: async () => { summaries.push(message); } }),
  },
};
const context = {repo: {owner: 'owner', repo: 'repo'}};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('github', 'context', 'core', script)(github, context, core)
  .then(() => console.log('__RESULT__' + JSON.stringify({calls, outputs, warnings, summaries})))
  .catch((error) => {
    console.error(error.stack || error);
    process.exitCode = 1;
  });
"""


def _run_alarm_script(overall: str, **config: object) -> dict:
    if not _NODE:
        pytest.skip("node is required to execute the github-script alarm sink")
    result = subprocess.run(
        [_NODE, "-e", _HARNESS],
        input=_alarm_script(),
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env={
            **os.environ,
            "OVERALL": overall,
            "PAGE_NEEDED": str(config.get("pageNeeded", True)).lower(),
            "PROBE_STATUS": "",
            "PROBE_MSG": "",
            "LABEL": str(config.get("label", "p0-outage")),
            "THRESHOLD": "2",
            "PROBE_URL": "https://tinyassets.io/mcp",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "2",
            "TEST_CONFIG": json.dumps(config),
        },
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("__RESULT__")
        for line in result.stdout.splitlines()
        if line.startswith("__RESULT__")
    )
    return json.loads(marker)


@pytest.mark.parametrize("overall", ["", "unknown", "greenish"])
def test_unknown_result_makes_no_rest_calls_or_state_mutation(overall: str) -> None:
    observed = _run_alarm_script(overall, missingLabel=True)

    assert observed["calls"] == []
    assert observed["outputs"] == {
        "page_eligible": "false",
        "issue_number": "",
        "is_first_alarm": "false",
    }
    assert observed["warnings"]
    assert observed["summaries"]


def test_literal_green_recovers_only_an_existing_issue_without_creating_label() -> None:
    observed = _run_alarm_script("green", openIssue={"number": 42}, missingLabel=True)

    assert [call["name"] for call in observed["calls"]] == [
        "issues.listForRepo",
        "issues.createComment",
        "issues.update",
    ]
    assert observed["outputs"]["page_eligible"] == "false"


def test_red_keeps_label_threshold_and_paging_behavior() -> None:
    observed = _run_alarm_script("red", missingLabel=True, measuredRed=True)

    assert [call["name"] for call in observed["calls"]] == [
        "issues.getLabel",
        "issues.createLabel",
        "issues.listForRepo",
        "actions.listWorkflowRuns",
        "actions.listJobsForWorkflowRunAttempt",
        "actions.getWorkflowRun",
        "issues.create",
    ]
    assert observed["outputs"] == {
        "page_eligible": "true",
        "issue_number": "99",
        "is_first_alarm": "true",
    }


def test_unknown_guard_and_literal_green_recovery_precede_mutation_logic() -> None:
    script = _alarm_script()

    defaults = script.index("core.setOutput('page_eligible', 'false')")
    unknown_guard = script.index("overall !== 'red' && overall !== 'green'")
    label_lookup = script.index("github.rest.issues.getLabel")
    green_guard = script.index("if (overall === 'green')")
    recovery = script.index("GREEN — RECOVERED")

    assert defaults < unknown_guard < label_lookup
    assert green_guard < recovery


def test_prior_workflow_failure_without_measured_layer1_receipt_is_not_red() -> None:
    """A previous L2/browser failure alone cannot prove a Layer-1 outage.

    The legacy harness deliberately supplies only a failed workflow conclusion,
    with no measured Layer-1 receipt. Red-first proof for the monitoring shape:
    current code incorrectly opens an incident from that unrelated failure.
    """
    observed = _run_alarm_script("red", priorRed=True)

    assert "issues.create" not in [call["name"] for call in observed["calls"]]
    assert observed["outputs"]["page_eligible"] == "false"


@pytest.mark.parametrize("config", [
    {"stepOverrides": {"conclusion": "skipped"}},
    {"stepOverrides": {"conclusion": "success"}},
    {"stepOverrides": {"status": "in_progress"}},
    {"jobOverrides": {"run_attempt": 1}},
    {"jobOverrides": {"run_id": 9}},
    {"jobOverrides": {"head_sha": "different"}},
    {"jobOverrides": {"steps": []}},
    {"jobOverrides": {"steps": [
        {"name": "Layer-1 measured red v1", "status": "completed", "conclusion": "failure"},
        {"name": "Layer-1 measured red v1", "status": "completed", "conclusion": "failure"},
    ]}},
    {"runOverrides": {"head_branch": "unrelated"}},
    {"runOverrides": {"path": ".github/workflows/other.yml"}},
    {"runOverrides": {"updated_at": "2020-01-01T00:00:00Z"}},
    {"runOverrides": {"updated_at": "not a timestamp"}},
    {"runOverrides": {"status": "queued"}},
    {"latestOverrides": {"run_attempt": 3}},
    {"latestOverrides": {"status": "in_progress"}},
    {"historyError": True},
])
def test_unproven_history_never_counts_as_prior_red(config) -> None:
    observed = _run_alarm_script("red", measuredRed=True, **config)
    assert not any(call["name"] == "issues.create" for call in observed["calls"])
    assert observed["outputs"]["page_eligible"] == "false"


def test_unknown_prior_observation_breaks_chain_before_older_red() -> None:
    observed = _run_alarm_script("red", priorRuns=[
        {"id": 1, "conclusion": "success"},
        {"id": 0, "conclusion": "failure"},
    ])
    assert not any(call["name"] == "issues.create" for call in observed["calls"])


def test_receipt_read_pins_exact_run_and_attempt() -> None:
    observed = _run_alarm_script("red", measuredRed=True)
    call = next(
        c for c in observed["calls"] if c["name"] == "actions.listJobsForWorkflowRunAttempt"
    )
    assert call["args"]["run_id"] == 1
    assert call["args"]["attempt_number"] == 2


def test_sentinel_only_runs_for_measured_red_and_cannot_be_softened() -> None:
    steps = _workflow()["jobs"]["probe"]["steps"]
    sentinel = next(s for s in steps if s.get("name") == "Layer-1 measured red v1")
    assert sentinel["if"] == "steps.combine.outputs.overall == 'red'"
    assert sentinel["run"] == "exit 1"
    assert not sentinel.get("continue-on-error")


def test_scheduled_layer2_does_not_invoke_unavailable_browser_or_llm() -> None:
    steps = _workflow()["jobs"]["layer2-probe"]["steps"]
    script = "\n".join(s.get("run", "") for s in steps)
    assert "l2_status=unknown" in script
    assert "authorized_rendered_session_unavailable" in script
    assert "claude_chat.py" not in script
    assert "uptime_canary_layer2.py" not in script


def test_alarm_sink_stays_always_and_failed_deploy_still_skips_probe() -> None:
    workflow = _workflow()

    assert workflow["jobs"]["alarm-sink"]["if"].strip().lower() == "always()"
    assert workflow["jobs"]["probe"]["if"] == (
        "github.event_name != 'workflow_run' || "
        "github.event.workflow_run.conclusion == 'success'"
    )
    assert workflow["env"]["ALARM_THRESHOLD"] == 2


def test_wiki_probe_uses_gha_output_mode_and_preserves_diagnostic() -> None:
    workflow = _workflow()
    step = next(
        item
        for item in workflow["jobs"]["probe"]["steps"]
        if item.get("id") == "wiki_probe"
    )
    run = step["run"]

    assert step["env"]["TINYASSETS_WIKI_CANARY_TOKEN"] == (
        "${{ secrets.TINYASSETS_WIKI_CANARY_TOKEN }}"
    )
    assert "python scripts/wiki_canary.py" in run
    assert "--verbose" in run
    assert "--format gha" in run
    assert "output=$(" in run and "2>&1" in run
    assert "echo \"wiki_msg<<${_delim}\"" in run
    assert "echo \"$output\"" in run


def test_disk_warning_opens_issue_without_paging_and_critical_pages_existing_issue():
    warning = _run_alarm_script("red", pageNeeded=False, label="disk-pressure")
    assert warning["outputs"]["issue_number"] == "99"
    assert warning["outputs"]["page_eligible"] == "false"
    critical = _run_alarm_script("red", openIssue={"number": 99}, pageNeeded=True,
                                 label="disk-pressure")
    assert critical["outputs"]["page_eligible"] == "true"


def test_disk_alarm_uses_existing_disk_label_without_p0_triage():
    result = _run_alarm_script("red", label="disk-pressure", pageNeeded=False)
    created = next(call["args"] for call in result["calls"] if call["name"] == "issues.create")
    assert created["labels"] == ["disk-pressure"]
    assert "Shared disk pressure" in created["title"]
    assert not any(call["name"].startswith("actions.") for call in result["calls"])
    recovered = _run_alarm_script("green", label="disk-pressure", openIssue={"number": 99})
    assert next(call["args"] for call in recovered["calls"]
                if call["name"] == "issues.listForRepo")["labels"] == "disk-pressure"
    assert recovered["outputs"]["page_eligible"] == "false"
    matrix = _workflow()["jobs"]["alarm-sink"]["strategy"]["matrix"]
    assert matrix["alarm"] == ["uptime", "disk"]
    steps = _workflow()["jobs"]["alarm-sink"]["steps"]
    gate = next(step for step in steps if step.get("id") == "gate")
    assert "disk_overall" in gate["env"]["OVERALL"]
    assert "disk-pressure" in gate["env"]["LABEL"]
    page = next(step for step in steps if step.get("id") == "page")
    assert "disk_status" in page["run"] and "SHARED_DISK_PRESSURE" in page["run"]
