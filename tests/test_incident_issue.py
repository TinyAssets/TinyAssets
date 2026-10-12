"""One open incident issue: comment on failure, close on recovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "scripts" / "incident_issue.js"
_NODE = shutil.which("node")

_HARNESS = r"""
const {recordFailure, recordRecovery} = require(process.env.MODULE);
const scenario = JSON.parse(process.env.SCENARIO);
const calls = [];
const issues = scenario.issues.map((issue) => ({...issue}));
let allowCalls = 0;

function pageOf(args) {
  const perPage = args.per_page;
  const page = args.page || 1;
  const start = (page - 1) * perPage;
  return issues.filter((issue) => issue.state !== 'closed').slice(start, start + perPage);
}

const github = {
  rest: {
    issues: {
      listForRepo: async (args) => {
        calls.push({name: 'issues.listForRepo', args});
        return {data: pageOf(args)};
      },
      createComment: async (args) => {
        calls.push({name: 'issues.createComment', args});
        return {data: {}};
      },
      create: async (args) => {
        calls.push({name: 'issues.create', args});
        const number = 900 + calls.filter((call) => call.name === 'issues.create').length;
        issues.unshift({number, title: args.title, state: 'open'});
        return {data: {number}};
      },
      update: async (args) => {
        calls.push({name: 'issues.update', args});
        const issue = issues.find((item) => item.number === args.issue_number);
        if (issue && args.state) issue.state = args.state;
        return {data: {}};
      },
    },
  },
};
const context = {repo: {owner: 'owner', repo: 'repo'}};

function optionsFor(step) {
  const options = {...(step.options || {})};
  if (step.allowCreate === 'callback') {
    options.allowCreate = async () => {
      allowCalls += 1;
      return step.allowResult !== false;
    };
  }
  return options;
}

(async () => {
  const recorded = [];
  const fn = scenario.op === 'recover' ? recordRecovery : recordFailure;
  const steps = scenario.steps || [{options: scenario.options, allowCreate: scenario.allowCreate,
    allowResult: scenario.allowResult, op: scenario.op}];
  for (const step of steps) {
    const call = (step.op === 'recover' ? recordRecovery : fn);
    recorded.push(await call(github, context, optionsFor(step)));
  }
  console.log('__RESULT__' + JSON.stringify({recorded, calls, allowCalls, issues}));
})().catch((error) => {
  console.log('__ERROR__' + JSON.stringify({message: error.message, calls, allowCalls}));
  process.exitCode = scenario.expectError ? 0 : 1;
});
"""


def _run(scenario: dict) -> dict:
    if not _NODE:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [_NODE, "-e", _HARNESS],
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env={
            **os.environ,
            "MODULE": str(_MODULE),
            "SCENARIO": json.dumps(scenario),
        },
    )
    if scenario.get("expectError"):
        assert result.returncode == 0, result.stderr
        marker = next(line for line in result.stdout.splitlines() if line.startswith("__ERROR__"))
        return json.loads(marker.removeprefix("__ERROR__"))
    assert result.returncode == 0, result.stderr + result.stdout
    marker = next(line for line in result.stdout.splitlines() if line.startswith("__RESULT__"))
    return json.loads(marker.removeprefix("__RESULT__"))


def _failure(**options: object) -> dict:
    return {
        "label": "deploy-failed",
        "title": "deploy-failed: run 1",
        "body": (
            "result: `bundle_invalid`\n"
            "Run: https://github.com/o/r/actions/runs/1\n"
            "commit: `abc`"
        ),
        **options,
    }


def test_failure_comments_on_the_newest_open_issue_and_does_not_create() -> None:
    observed = _run({
        "issues": [
            {"number": 8, "title": "deploy-failed: run 8", "state": "open"},
            {"number": 3, "title": "Deploy failed — old", "state": "open"},
        ],
        "options": _failure(),
    })
    recorded = observed["recorded"][0]
    assert recorded == {"action": "commented", "number": "8"}
    assert [call["name"] for call in observed["calls"]] == [
        "issues.listForRepo",
        "issues.createComment",
    ]
    listed = observed["calls"][0]["args"]
    assert listed["labels"] == "deploy-failed"
    assert listed["state"] == "open"
    assert listed["sort"] == "created"
    assert listed["direction"] == "desc"
    comment = observed["calls"][1]["args"]
    assert comment["issue_number"] == 8
    assert "bundle_invalid" in comment["body"]
    assert "actions/runs/1" in comment["body"]
    assert "abc" in comment["body"]


def test_failure_skips_content_repair_and_pull_requests() -> None:
    observed = _run({
        "issues": [
            {"number": 1, "title": "owner-content-repair: run 9", "state": "open"},
            {"number": 2, "title": "deploy-failed: run 4", "state": "open", "pull_request": {}},
            {"number": 3, "title": "deploy-failed: run 3", "state": "open"},
        ],
        "options": _failure(excludeTitlePrefixes=["owner-content-repair:"]),
    })
    assert observed["recorded"][0] == {"action": "commented", "number": "3"}
    assert observed["calls"][1]["args"]["issue_number"] == 3


def test_failure_opens_one_issue_when_only_excluded_issues_are_open() -> None:
    observed = _run({
        "issues": [{"number": 1, "title": "owner-content-repair: run 9", "state": "open"}],
        "options": _failure(excludeTitlePrefixes=["owner-content-repair:"]),
    })
    assert observed["recorded"][0]["action"] == "opened"
    created = next(call["args"] for call in observed["calls"] if call["name"] == "issues.create")
    assert created["title"] == "deploy-failed: run 1"
    assert created["labels"] == ["deploy-failed"]
    assert "bundle_invalid" in created["body"]


def test_second_failure_comments_instead_of_opening_another() -> None:
    options = _failure(
        body="result: `rollback_unhealthy`\nRun: https://example/runs/2\ncommit: `def`",
    )
    observed = _run({
        "issues": [],
        "steps": [{"options": _failure()}, {"options": options}],
    })
    assert [item["action"] for item in observed["recorded"]] == ["opened", "commented"]
    assert observed["recorded"][1]["number"] == observed["recorded"][0]["number"]
    assert [call["name"] for call in observed["calls"]].count("issues.create") == 1


def test_recovery_closes_only_the_newest_matching_issue() -> None:
    observed = _run({
        "op": "recover",
        "issues": [
            {"number": 4, "title": "owner-content-repair: run 1", "state": "open"},
            {"number": 8, "title": "deploy-failed: run 8", "state": "open"},
            {"number": 3, "title": "deploy-failed: run 3", "state": "open"},
        ],
        "options": {
            "label": "deploy-failed",
            "excludeTitlePrefixes": ["owner-content-repair:"],
            "body": "recovered\nRun: https://example/runs/9\ncommit: `fff`",
        },
    })
    assert observed["recorded"][0] == {"action": "closed", "number": "8"}
    updated = [call["args"] for call in observed["calls"] if call["name"] == "issues.update"]
    assert updated == [{
        "owner": "owner",
        "repo": "repo",
        "issue_number": 8,
        "state": "closed",
        "state_reason": "completed",
    }]
    still_open = [issue["number"] for issue in observed["issues"] if issue["state"] != "closed"]
    assert still_open == [4, 3]


def test_recovery_with_nothing_open_does_not_mutate() -> None:
    observed = _run({
        "op": "recover",
        "issues": [{"number": 1, "title": "owner-content-repair: run 1", "state": "open"}],
        "options": {
            "label": "deploy-failed",
            "excludeTitlePrefixes": ["owner-content-repair:"],
            "body": "recovered",
        },
    })
    assert observed["recorded"][0] == {"action": "none", "number": ""}
    assert [call["name"] for call in observed["calls"]] == ["issues.listForRepo"]


def test_allow_create_callback_runs_only_when_no_issue_is_open() -> None:
    open_issue = _run({
        "issues": [{"number": 5, "title": "p0", "state": "open"}],
        "allowCreate": "callback",
        "allowResult": True,
        "options": {
            "label": "p0-outage",
            "commentBody": "RED",
            "title": "P0",
            "createBody": "open",
        },
    })
    assert open_issue["allowCalls"] == 0
    assert open_issue["recorded"][0]["action"] == "commented"

    blocked = _run({
        "issues": [],
        "allowCreate": "callback",
        "allowResult": False,
        "options": {
            "label": "p0-outage",
            "commentBody": "RED",
            "title": "P0",
            "createBody": "open",
        },
    })
    assert blocked["allowCalls"] == 1
    assert blocked["recorded"][0] == {"action": "none", "number": ""}
    assert "issues.create" not in [call["name"] for call in blocked["calls"]]


def test_later_page_is_searched_before_opening_a_duplicate() -> None:
    issues = [
        {"number": 1, "title": "owner-content-repair: run 1", "state": "open"},
        {"number": 2, "title": "owner-content-repair: run 2", "state": "open"},
        {"number": 7, "title": "deploy-failed: run 7", "state": "open"},
    ]
    observed = _run({
        "issues": issues,
        "options": _failure(excludeTitlePrefixes=["owner-content-repair:"], perPage=2, pageCap=3),
    })
    assert observed["recorded"][0] == {"action": "commented", "number": "7"}
    assert [call["name"] for call in observed["calls"]] == [
        "issues.listForRepo",
        "issues.listForRepo",
        "issues.createComment",
    ]


def test_page_cap_refuses_to_open_when_the_list_is_unfinished() -> None:
    observed = _run({
        "expectError": True,
        "issues": [
            {"number": 1, "title": "owner-content-repair: run 1", "state": "open"},
            {"number": 2, "title": "owner-content-repair: run 2", "state": "open"},
        ],
        "options": _failure(excludeTitlePrefixes=["owner-content-repair:"], perPage=1, pageCap=1),
    })
    assert "refusing to open another" in observed["message"]
    assert "issues.create" not in [call["name"] for call in observed["calls"]]


def test_missing_body_and_excluded_title_fail_loudly() -> None:
    missing = _run({
        "expectError": True,
        "issues": [],
        "options": {"label": "deploy-failed", "title": "deploy-failed: run 1", "body": "  "},
    })
    assert "body is required" in missing["message"]
    excluded = _run({
        "expectError": True,
        "issues": [],
        "options": _failure(
            title="owner-content-repair: run 1",
            excludeTitlePrefixes=["owner-content-repair:"],
        ),
    })
    assert "excluded from later lookup" in excluded["message"]
    assert "issues.create" not in [call["name"] for call in excluded["calls"]]
