"""The merge queue ignores auto-merge armed by an app. Enqueue explicitly.

``enablePullRequestAutoMerge`` admits a PR only for a person. This workflow
calls ``enqueuePullRequest`` after the review receipt and the required checks,
using the merge-attribution token. These tests are the pre-merge check: the
workflow runs on ``pull_request_target`` and ``workflow_run`` with a secret.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.enqueue_merge import (
    REQUIRED_CHECKS,
    checks_green,
    latest_conclusions,
    skip_reason,
)

_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "enqueue-merge.yml"
)
_SECRET = "MERGE_ATTRIBUTION_TOKEN"


@pytest.fixture(scope="module")
def source() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _workflow(source: str) -> dict:
    yaml = pytest.importorskip("yaml")
    loaded = yaml.safe_load(source)
    # PyYAML parses a bare `on:` key as True.
    if True in loaded:
        loaded["on"] = loaded.pop(True)
    return loaded


def _ready(**overrides):
    values = dict(
        state="OPEN",
        draft=False,
        base="main",
        same_repo=True,
        in_queue=False,
        capped=False,
        receipt=True,
        green=True,
        head_matches=True,
    )
    values.update(overrides)
    return values


def test_a_reviewed_green_pr_is_enqueued():
    assert skip_reason(**_ready()) is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"state": "MERGED"}, "state"),
        ({"draft": True}, "draft"),
        ({"base": "dev"}, "base"),
        ({"same_repo": False}, "fork"),
        ({"in_queue": True}, "already queued"),
        ({"capped": True}, "cap"),
        ({"receipt": False}, "receipt"),
        ({"green": False}, "required checks"),
        ({"head_matches": False}, "head moved"),
    ],
)
def test_each_missing_condition_skips(change, reason):
    text = skip_reason(**_ready(**change))
    assert text is not None and reason in text


def test_latest_success_beats_an_older_failure():
    nodes = [
        {"name": "required-tests", "conclusion": "FAILURE", "completedAt": "2026-10-12T01:00:00Z"},
        {"name": "required-tests", "conclusion": "SUCCESS", "completedAt": "2026-10-12T02:00:00Z"},
        {"name": "slow-tests", "conclusion": "SKIPPED", "completedAt": "2026-10-12T02:00:01Z"},
        {"name": "invariants", "conclusion": "SUCCESS", "completedAt": "2026-10-12T02:00:02Z"},
        {
            "name": "Diff scope declared",
            "conclusion": "SUCCESS",
            "completedAt": "2026-10-12T02:00:03Z",
        },
        {"name": "noise", "conclusion": "FAILURE", "completedAt": "2026-10-12T03:00:00Z"},
    ]
    conclusions = latest_conclusions(nodes)
    assert checks_green(conclusions)
    assert set(conclusions) == set(REQUIRED_CHECKS)


def test_a_newer_failure_is_not_green():
    nodes = [
        {"name": name, "conclusion": "SUCCESS", "completedAt": "2026-10-12T01:00:00Z"}
        for name in REQUIRED_CHECKS
    ]
    nodes.append(
        {"name": "invariants", "conclusion": "FAILURE", "completedAt": "2026-10-12T02:00:00Z"}
    )
    assert not checks_green(latest_conclusions(nodes))


def test_a_missing_required_check_is_not_green():
    nodes = [
        {"name": name, "conclusion": "SUCCESS", "completedAt": "t"}
        for name in REQUIRED_CHECKS
        if name != "invariants"
    ]
    assert not checks_green(latest_conclusions(nodes))


def test_workflow_enqueues_with_the_attribution_token_and_keeps_the_receipt(source):
    workflow = _workflow(source)
    triggers = workflow["on"]
    assert triggers["workflow_run"]["workflows"] == ["Tests", "invariants", "PR scope guard"]
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert "edited" in triggers["pull_request_target"]["types"]
    step = next(
        s for s in workflow["jobs"]["enqueue"]["steps"]
        if s.get("name") == "Enqueue when the receipt and required checks have passed"
    )
    assert step["env"]["GH_TOKEN"] == "${{ secrets.%s }}" % _SECRET
    assert "github.token" not in source
    assert "scripts/enqueue_merge.py" in step["run"]
    assert "toJSON(github.event.workflow_run.pull_requests)" in source
    program = (
        Path(__file__).resolve().parents[1] / "scripts" / "enqueue_merge.py"
    ).read_text(encoding="utf-8")
    assert "enqueuePullRequest" in program
    assert "drain_review_gate.py" in program
    assert "queue_attempts.py" in program


def test_an_empty_secret_fails_before_enqueue(source):
    workflow = _workflow(source)
    steps = workflow["jobs"]["enqueue"]["steps"]
    names = [s.get("name") for s in steps]
    guard = names.index("Require the merge-attribution token")
    assert guard < names.index("Enqueue when the receipt and required checks have passed")
    step = steps[guard]
    assert step["env"]["TOKEN_PRESENT"] == "${{ secrets.%s != '' }}" % _SECRET
    assert 'if [ "$TOKEN_PRESENT" != "true" ]' in step["run"]
    assert "exit 1" in step["run"]


def test_merge_group_and_failed_runs_do_not_enqueue(source):
    text = " ".join(workflow_if(source).split())
    assert "workflow_run.conclusion == 'success'" in text
    assert "workflow_run.event == 'pull_request'" in text
    assert "draft == false" in text


def workflow_if(source: str) -> str:
    return str(_workflow(source)["jobs"]["enqueue"]["if"])
