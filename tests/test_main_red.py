"""Tests for scripts/main_red.py and .github/workflows/main-red.yml."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

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
    assert _decide(required="timed_out") == "alarm"


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
    assert mr.required_conclusion("o/r", 1, 2) == "success"
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


@pytest.fixture
def handler(monkeypatch):
    state = SimpleNamespace(alarms=[], reverts=[], reruns=[], failures=["tests/t.py::test_x"],
                            tip="sha", tip_required="failure", required="failure",
                            latest={"run_attempt": 2, "status": "completed"},
                            collect_failures=mr.new_failures)

    def gh(*args, **kwargs):
        if args == ("api", "repos/o/r/actions/runs/1"):
            return json.dumps(state.latest)
        state.reruns.append(args)
        return ""

    def run(*args, **kwargs):
        return SimpleNamespace(stdout={"ls-remote": state.tip, "log": "feat: x",
                                       "rev-list": "sha parent"}[args[1]])

    def revert(*args):
        state.reverts.append(args)
        return "https://pr"

    monkeypatch.setattr(mr, "_gh", gh)
    monkeypatch.setattr(mr, "_run", run)
    monkeypatch.setattr(mr, "required_conclusion", lambda *args: state.required)
    monkeypatch.setattr(mr, "parent_conclusion",
                        lambda repo, sha: "success" if sha == "parent" else state.tip_required)
    monkeypatch.setattr(mr, "new_failures", lambda *args: state.failures)
    monkeypatch.setattr(mr, "_alarm", lambda repo, body, sha: state.alarms.append(body))
    monkeypatch.setattr(mr, "_open_revert", revert)
    return state


def _handle(attempt=2):
    return mr.handle("o/r", 1, "sha", "push", attempt, "https://run", "private-token")


@pytest.mark.parametrize("failure", ["exception", "push", "expired-pat", "details", "conflict"])
def test_alarm_survives_revert_and_failure_detail_errors(monkeypatch, handler, failure):
    def fail(*args):
        if failure == "conflict":
            return None
        if failure == "push":
            raise subprocess.CalledProcessError(1, ["git", "push", "private-token"])
        raise RuntimeError("private-token")

    monkeypatch.setattr(mr, "new_failures" if failure == "details" else "_open_revert", fail)
    _handle()
    assert len(handler.alarms) == 1
    assert "revert could not be opened" in handler.alarms[0]
    assert "private-token" not in handler.alarms[0]


def test_pending_slots_are_isolated_for_each_red_event():
    wf = yaml.safe_load((_REPO / ".github/workflows/main-red.yml").read_text("utf-8"))
    group = wf["concurrency"]["group"]
    # Different SHAs, scheduled runs of one SHA, and reruns all retain a slot.
    def resolve(sha, run, attempt):
        resolved = group
        for field, value in (("head_sha", sha), ("id", run), ("run_attempt", attempt)):
            resolved = resolved.replace(
                "${{ github.event.workflow_run." + field + " }}", str(value))
        return resolved

    groups = []
    for event in [("a", 1, 1), ("b", 2, 1), ("c", 3, 1), ("a", 4, 1), ("a", 1, 2)]:
        groups.append(resolve(*event))
    assert len(set(groups)) == len(groups)


@pytest.mark.parametrize("attempt", [1, 2])
def test_historical_red_does_nothing_after_tip_goes_green(handler, attempt):
    handler.latest["run_attempt"] = attempt
    handler.tip, handler.tip_required = "new-tip", "success"
    assert _handle(attempt) == "none"
    assert not handler.alarms and not handler.reverts and not handler.reruns


@pytest.mark.parametrize("required,failures", [
    ("failure", []),  # missing artifacts / setup or installation failure
    ("timed_out", []),
    ("timed_out", ["tests/t.py::test_x"]),
])
def test_infrastructure_failure_only_alarms_without_claiming_causation(handler, required, failures):
    handler.required, handler.failures = required, failures
    assert _handle() == "alarm"
    assert not handler.reverts
    assert "cause is not established" in handler.alarms[0]
    assert "culprit" not in handler.alarms[0]


def test_event_attempt_is_read_exactly(monkeypatch):
    calls = []
    monkeypatch.setattr(mr, "_gh", lambda *args: calls.append(args) or "failure\n")
    assert mr.required_conclusion("o/r", 1, 2) == "failure"
    assert "repos/o/r/actions/runs/1/attempts/2/jobs" in calls[0]
    assert "filter=latest" not in str(calls)


def test_parent_lookup_uses_the_selected_runs_attempt(monkeypatch):
    monkeypatch.setattr(mr, "_gh", lambda *args: json.dumps([
        {"id": 10, "run_attempt": 1}, {"id": 12, "run_attempt": 3},
    ]))
    calls = []
    monkeypatch.setattr(mr, "required_conclusion",
                        lambda *args: calls.append(args) or "success")
    assert mr.parent_conclusion("o/r", "parent") == "success"
    assert calls == [("o/r", 12, 3)]


@pytest.mark.parametrize("current_artifact", [False, True])
def test_stale_failure_artifact_cannot_trigger_revert(monkeypatch, handler, current_artifact):
    handler_gh = mr._gh
    def gh(*args):
        if args == ("api", "repos/o/r/actions/runs/1"):
            return handler_gh(*args)
        if args[-1].endswith("/attempts/2"):
            return json.dumps({"run_started_at": "2026-10-02T02:00:00Z",
                               "updated_at": "2026-10-02T02:10:00Z"})
        artifacts = [{"id": 10, "name": "junit-required-tests", "expired": False,
                      "created_at": "2026-10-02T01:05:00Z"}]
        if current_artifact:
            artifacts.append({**artifacts[0], "id": 20, "created_at": "2026-10-02T02:05:00Z"})
        return "\n".join(json.dumps(a) for a in artifacts)

    def download(args, **kwargs):
        assert args[-1] == "repos/o/r/actions/artifacts/20/zip"
        with zipfile.ZipFile(kwargs["stdout"], "w") as bundle:
            bundle.writestr("junit.xml", '<testsuite><testcase classname="tests.t" name="test_x">'
                            '<failure message="failed"/></testcase></testsuite>')

    monkeypatch.setattr(mr, "_gh", gh)
    monkeypatch.setattr(mr.subprocess, "run", download)
    monkeypatch.setattr(mr, "new_failures", handler.collect_failures)
    assert _handle() == ("revert" if current_artifact else "alarm")
    assert bool(handler.reverts) == current_artifact
    assert len(handler.alarms) == 1


def test_newer_pending_attempt_defers_until_its_completion_event(handler):
    handler.latest = {"run_attempt": 3, "status": "in_progress"}
    assert _handle() == "defer"
    assert not handler.alarms and not handler.reverts and not handler.reruns
    handler.latest["status"] = "completed"
    assert _handle(attempt=3) == "revert"
    assert len(handler.alarms) == len(handler.reverts) == 1
    assert "not armed" in handler.reverts[0][3]


@pytest.mark.parametrize("location", ["body", "comment", "absent"])
def test_alarm_deduplicates_sha_in_issue_body_or_comments(monkeypatch, location):
    marker = "<!-- main-red:sha -->"
    calls = []

    def gh(*args):
        calls.append(args)
        if args[:2] == ("issue", "list"):
            assert "number,body" in args
            return json.dumps([{"number": 10, "body": marker if location == "body" else ""}])
        if args[0] == "api":
            return marker if location == "comment" else ""
        return ""

    monkeypatch.setattr(mr, "_gh", gh)
    mr._alarm("o/r", marker, "sha")
    comments = [c for c in calls if c[:2] == ("issue", "comment")]
    assert len(comments) == (1 if location == "absent" else 0)
