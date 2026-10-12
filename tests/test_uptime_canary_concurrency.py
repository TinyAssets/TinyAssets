"""Executable shared-state proof for uptime-canary alarm concurrency."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parent.parent
_WORKFLOW = _REPO / ".github" / "workflows" / "uptime-canary.yml"
_NODE = shutil.which("node")


def _alarm_script() -> str:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        step
        for step in workflow["jobs"]["alarm-sink"]["steps"]
        if step.get("id") == "gate"
    )
    return step["with"]["script"]


def test_uptime_canary_keeps_global_non_cancelling_concurrency_group():
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))

    assert workflow["concurrency"] == {
        "group": "uptime-canary",
        "cancel-in-progress": False,
    }


_SHARED_STATE_HARNESS = r"""
const fs = require('fs');
const script = fs.readFileSync(0, 'utf8');
const events = JSON.parse(process.env.TEST_EVENTS);
const state = {
  issue: null,
  labels: new Set(['p0-outage']),
  comments: [],
  issueCreates: 0,
  mutations: 0,
};

function issueList() {
  return state.issue && state.issue.state === 'open' ? [state.issue] : [];
}

async function runEvent(event, ordinal) {
  const calls = [];
  const outputs = {};
  const warnings = [];
  const summaries = [];
  const mutationStart = state.mutations;
  process.env.OVERALL = event.overall;
  process.env.PAGE_NEEDED = event.overall === 'red' ? 'true' : 'false';
  process.env.PROBE_STATUS = event.overall === 'red' ? '3' : '0';
  process.env.PROBE_MSG = `shared-state ${event.overall}`;
  process.env.LABEL = 'p0-outage';
  process.env.THRESHOLD = '2';
  process.env.PROBE_URL = 'https://tinyassets.io/mcp';
  process.env.GITHUB_SERVER_URL = 'https://github.com';
  process.env.GITHUB_REPOSITORY = 'owner/repo';
  process.env.GITHUB_RUN_ID = String(100 + ordinal);

  const issues = {
    getLabel: async (args) => {
      calls.push({name: 'issues.getLabel', args});
      if (!state.labels.has(args.name)) {
        const error = new Error('not found'); error.status = 404; throw error;
      }
      return {data: {}};
    },
    createLabel: async (args) => {
      calls.push({name: 'issues.createLabel', args});
      state.labels.add(args.name); state.mutations++; return {data: {}};
    },
    listForRepo: async (args) => {
      calls.push({name: 'issues.listForRepo', args});
      return {data: issueList()};
    },
    createComment: async (args) => {
      calls.push({name: 'issues.createComment', args});
      state.comments.push({
        body: args.body,
        created_at: '2026-07-23T20:00:00Z',
        user: {login: 'github-actions[bot]'},
      });
      state.mutations++; return {data: {}};
    },
    create: async (args) => {
      calls.push({name: 'issues.create', args});
      state.issue = {number: 99, state: 'open'}; state.issueCreates++; state.mutations++;
      return {data: {number: 99}};
    },
    update: async (args) => {
      calls.push({name: 'issues.update', args});
      state.issue.state = args.state; state.mutations++; return {data: {}};
    },
  };
  const github = {
    rest: {
      issues,
      actions: {
        listWorkflowRuns: async (args) => {
          calls.push({name: 'actions.listWorkflowRuns', args});
          return {data: {workflow_runs: event.priorRed ? [{
            id: 1, run_attempt: 1, status: 'completed', head_branch: 'main', head_sha: 'abc',
            path: '.github/workflows/uptime-canary.yml', updated_at: new Date().toISOString(),
          }] : []}};
        },
        listJobsForWorkflowRunAttempt: async (args) => {
          calls.push({name: 'actions.listJobsForWorkflowRunAttempt', args});
          return {data: {total_count: 1, jobs: [{
            name: 'probe', run_id: 1, run_attempt: 1, status: 'completed', head_sha: 'abc',
            steps: [{name: 'Layer-1 measured red v1', status: 'completed', conclusion: 'failure'}],
          }]}};
        },
        getWorkflowRun: async (args) => {
          calls.push({name: 'actions.getWorkflowRun', args});
          return {data: {run_attempt: 1, status: 'completed', head_sha: 'abc'}};
        },
      },
    },
  };
  const core = {
    setOutput: (key, value) => { outputs[key] = value; },
    warning: (message) => { warnings.push(message); },
    summary: { addRaw: (message) => ({write: async () => summaries.push(message)}) },
  };
  const context = {repo: {owner: 'owner', repo: 'repo'}};
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction(
    'github', 'context', 'core', 'require', script,
  )(github, context, core, require);
  return {
    event: event.overall, calls, outputs, warnings, summaries,
    mutationDelta: state.mutations - mutationStart,
    issue: state.issue ? {...state.issue} : null, issueCreates: state.issueCreates,
  };
}

(async () => {
  const records = [];
  for (let index = 0; index < events.length; index++) {
    records.push(await runEvent(events[index], index));
  }
  console.log('__RESULT__' + JSON.stringify({records, state}));
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
"""


def _run_shared_sink(events: list[dict[str, object]]) -> dict:
    if not _NODE:
        pytest.skip("node is required to execute the github-script alarm sink")
    result = subprocess.run(
        [_NODE, "-e", _SHARED_STATE_HARNESS],
        input=_alarm_script(),
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env={
            **os.environ,
            "TEST_EVENTS": json.dumps(events),
            "GITHUB_WORKSPACE": str(_REPO),
        },
    )
    assert result.returncode == 0, result.stderr
    marker = next(
        line.removeprefix("__RESULT__")
        for line in result.stdout.splitlines()
        if line.startswith("__RESULT__")
    )
    return json.loads(marker)


def _one_running_one_replaceable_pending(
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Model the documented one-running/one-pending replacement discipline."""
    assert events
    running = events[0]
    pending = None
    for event in events[1:]:
        pending = event
    return [running] + ([pending] if pending is not None else [])


def _pager_decision(comments: list[dict], now: dt.datetime, first_alarm: bool) -> tuple[bool, str]:
    scripts_dir = _REPO / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        from pushover_page import should_page
    finally:
        sys.path.remove(str(scripts_dir))
    return should_page(comments, now, is_first_alarm=first_alarm)


def test_exact_alarm_sink_preserves_one_incident_across_serial_observations():
    observed = _run_shared_sink([
        {"overall": "red", "priorRed": False},
        {"overall": "red", "priorRed": True},
        {"overall": "red", "priorRed": False},
        {"overall": "unknown", "priorRed": False},
        {"overall": "red", "priorRed": False},
        {"overall": "green", "priorRed": False},
    ])
    first_red, threshold_red, red_one, unknown, red_two, green = observed["records"]

    assert first_red["outputs"] == {
        "page_eligible": "false", "issue_number": "", "is_first_alarm": "false",
    }
    assert threshold_red["outputs"] == {
        "page_eligible": "true", "issue_number": "99", "is_first_alarm": "true",
    }
    assert threshold_red["issueCreates"] == 1
    assert red_one["outputs"]["page_eligible"] == "true"
    assert unknown["calls"] == []
    assert unknown["mutationDelta"] == 0
    assert red_two["issue"]["number"] == 99
    assert red_two["issueCreates"] == 1
    assert any(call["name"] == "issues.createComment" for call in red_two["calls"])
    assert green["issue"]["state"] == "closed"
    assert any(call["name"] == "issues.update" for call in green["calls"])

    shared_comments = observed["state"]["comments"]
    now = dt.datetime(2026, 7, 23, 20, 0, tzinfo=dt.timezone.utc)
    page_eligible = [threshold_red, red_one, red_two]
    first_page, _ = _pager_decision(shared_comments, now, first_alarm=True)
    assert first_page is True
    shared_comments.append({
        "body": "[PAGED 2026-07-23T20:00:00+00:00 first-alarm]",
        "created_at": "2026-07-23T20:00:00Z",
        "user": {"login": "github-actions[bot]"},
    })
    for offset, record in enumerate(page_eligible[1:], start=5):
        duplicate_page, reason = _pager_decision(
            shared_comments,
            now + dt.timedelta(minutes=offset),
            first_alarm=record["outputs"]["is_first_alarm"] == "true",
        )
        assert duplicate_page is False
        assert reason == f"within_window_{3600 - offset * 60}s_to_next"


def test_coalesced_pending_burst_executes_exact_sink_without_duplicate_incident():
    arrivals = [{"overall": "red", "priorRed": True}]
    arrivals.extend(
        {"overall": "unknown" if index % 2 else "red", "priorRed": False}
        for index in range(998)
    )
    arrivals.append({"overall": "green", "priorRed": False})
    executed = _one_running_one_replaceable_pending(arrivals)
    observed = _run_shared_sink(executed)

    assert len(arrivals) == 1_000
    assert [event["overall"] for event in executed] == ["red", "green"]
    assert [record["event"] for record in observed["records"]] == ["red", "green"]
    assert observed["state"]["issueCreates"] == 1
    assert observed["state"]["issue"]["number"] == 99
    assert observed["state"]["issue"]["state"] == "closed"


def test_shared_paged_marker_blocks_duplicate_immediate_page():
    now = dt.datetime(2026, 7, 23, 20, 5, tzinfo=dt.timezone.utc)
    comments = [{
        "body": "[PAGED 2026-07-23T20:00:00+00:00 first-alarm]",
        "created_at": "2026-07-23T20:00:00Z",
        "user": {"login": "github-actions[bot]"},
    }]
    page, reason = _pager_decision(comments, now, first_alarm=False)

    assert page is False
    assert reason == "within_window_3300s_to_next"
