"""Tests for scripts/rerun_cancelled_required.py and its workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "rerun_cancelled_required", _REPO / "scripts" / "rerun_cancelled_required.py"
)
assert _spec and _spec.loader
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def _ctx(name, conclusion, run, required=True):
    return {"name": name, "conclusion": conclusion, "isRequired": required,
            "checkSuite": {"workflowRun": {"databaseId": run}}}


def test_a_required_check_whose_newest_run_was_cancelled_is_selected():
    contexts = [
        _ctx("Diff scope declared", "SUCCESS", 10),
        _ctx("Diff scope declared", "CANCELLED", 12),  # the newest: this is #4204
        _ctx("invariants", "SUCCESS", 11),
    ]
    assert rr.cancelled_required_runs(contexts) == [("Diff scope declared", 12)]


def test_an_older_cancelled_run_superseded_by_a_newer_one_is_left_alone():
    contexts = [_ctx("Diff scope declared", "CANCELLED", 12),
                _ctx("Diff scope declared", None, 14)]  # newer run still in progress
    assert rr.cancelled_required_runs(contexts) == []


def test_optional_checks_and_status_contexts_are_ignored():
    contexts = [
        _ctx("lint", "CANCELLED", 20, required=False),
        {"state": "PENDING"},  # a commit status, not a check run
        {"name": "required-tests", "conclusion": "CANCELLED", "isRequired": True,
         "checkSuite": None},
    ]
    assert rr.cancelled_required_runs(contexts) == []


def test_the_workflow_never_runs_pr_code_and_can_only_rerun_actions():
    wf = yaml.safe_load(
        (_REPO / ".github" / "workflows" / "rerun-cancelled-checks.yml").read_text("utf-8")
    )
    triggers = wf[True] if True in wf else wf["on"]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert wf["permissions"] == {"contents": "read", "actions": "write",
                                 "pull-requests": "read"}
    steps = wf["jobs"]["rerun"]["steps"]
    checkout = next(s for s in steps if "actions/checkout" in str(s.get("uses", "")))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    run = next(s for s in steps if "run" in s)
    assert run["run"] == 'python scripts/rerun_cancelled_required.py --repo "$REPO"'


def test_only_the_prs_own_run_on_its_current_head_is_eligible():
    head = "a" * 40
    ok = {"event": "pull_request_target", "head_sha": head, "run_attempt": 1}
    assert rr.eligible(ok, head) is None
    assert "merge_group" in rr.eligible({**ok, "event": "merge_group"}, head)
    assert "older head" in rr.eligible({**ok, "head_sha": "b" * 40}, head)
    assert "attempt 3" in rr.eligible({**ok, "run_attempt": 3}, head)
