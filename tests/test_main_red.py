"""Tests for scripts/main_red.py and .github/workflows/main-red.yml."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("main_red", _REPO / "scripts" / "main_red.py")
assert _spec and _spec.loader
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)


def _decide(**kw):
    base = {"event": "push", "attempt": 2, "required": "failure", "parent_required": "success",
            "head_is_main_tip": True, "is_revert_commit": False}
    return mr.decide(**{**base, **kw})


def test_a_red_merge_with_a_green_parent_is_reverted_after_one_rerun():
    assert _decide(attempt=1) == "rerun"
    assert _decide() == "revert"
    assert _decide(required="timed_out") == "revert"


@pytest.mark.parametrize("kw", [
    {"parent_required": None},          # the parent never got a verdict
    {"parent_required": "failure"},     # main was already red: not this merge
    {"event": "schedule"},              # hourly run: no single culprit
    {"is_revert_commit": True},         # never revert a revert
])
def test_an_unknown_culprit_only_raises_the_alarm(kw):
    assert _decide(**kw) == "alarm"


@pytest.mark.parametrize("required", ["cancelled", "skipped", None])
def test_no_verdict_is_not_a_red_main(required):
    assert _decide(required=required) == "none"
    assert _decide(required=required, attempt=1) == "none"


def test_green_on_the_tip_closes_the_alarm_and_green_elsewhere_does_nothing():
    assert _decide(required="success") == "close-alarm"
    assert _decide(required="success", head_is_main_tip=False) == "none"


def test_the_body_never_mentions_anyone_and_lists_failures():
    body = mr.render_body(sha="a" * 40, subject="feat: x (#1)", run_url="https://r",
                          failures=[f"tests/t.py::test_{i}" for i in range(45)],
                          action="revert")
    assert body.startswith(f"<!-- main-red:{'a' * 40} -->")
    assert "@" not in body
    assert "`tests/t.py::test_0`" in body and "and 5 more" in body
    assert "not armed" in body
    alarm = mr.render_body(sha="a" * 40, subject="s", run_url="u", failures=[], action="alarm")
    assert "nothing was reverted" in alarm


def test_revert_titles_are_recognised_as_reverts():
    title = mr.revert_title("feat: x (#1)")
    assert title.startswith('Revert "')


def test_only_the_required_aggregate_job_counts(monkeypatch):
    """heavy-tests (red baseline) and shard jobs never decide the verdict."""
    seen = {}

    def fake_gh(*args, token=None):
        seen["jq"] = args[-1]
        return "success\n"

    monkeypatch.setattr(mr, "_gh", fake_gh)
    assert mr.required_conclusion("o/r", 1) == "success"
    assert '.name == "required-tests"' in seen["jq"]


def test_the_workflow_runs_after_main_tests_with_least_privilege():
    wf = yaml.safe_load((_REPO / ".github" / "workflows" / "main-red.yml").read_text("utf-8"))
    triggers = wf[True] if True in wf else wf["on"]
    assert triggers["workflow_run"] == {"workflows": ["Tests"], "types": ["completed"],
                                        "branches": ["main"]}
    assert wf["permissions"] == {}
    assert wf["concurrency"]["cancel-in-progress"] is False
    job = wf["jobs"]["react"]
    assert "push" in job["if"] and "schedule" in job["if"] and "merge_group" not in job["if"]
    assert job["permissions"] == {"actions": "write", "contents": "read", "issues": "write"}
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] is False
    env = job["steps"][-1]["env"]
    assert env["REVERT_TOKEN"] == "${{ secrets.MERGE_ATTRIBUTION_TOKEN }}"
