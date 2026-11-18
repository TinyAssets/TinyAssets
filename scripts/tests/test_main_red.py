"""main-red's decision: a red main is re-run once, then alarmed, and never reverted."""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_red  # noqa: E402
import queue_attempts  # noqa: E402


def test_readers_recognize_the_aggregate_reported_by_the_tests_workflow(monkeypatch) -> None:
    workflow = main_red.REPO_ROOT / ".github" / "workflows" / "tests.yml"
    job_id = "required-tests"
    aggregate = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"][job_id]
    name = aggregate.get("name", job_id)
    jobs = {"jobs": [{"name": name, "conclusion": "failure"},
                     {"name": "other job", "conclusion": "success"}]}

    def jobs_response(*args: str) -> str:
        query = args[args.index("--jq") + 1]
        return subprocess.check_output(["jq", "-cr", query], input=json.dumps(jobs), text=True)

    monkeypatch.setattr(main_red, "_gh", jobs_response)
    assert main_red.required_conclusion("owner/repo", 7, 2) == "failure"

    # A docs-only change did not select this leg's failing test. Its aggregate, which
    # reports the failure too, must not count as a second, unexplained job failure.
    failing = "platform/files/test/chunks.test.ts > getChunks"
    counts, _, _ = queue_attempts.attempt_counts(
        {name: [], "files leg": [failing]}, changed=["docs/readme.md"], selection=None)
    assert counts is False


@pytest.mark.parametrize("required,attempt,tip,expected", [
    ("failure", 1, True, "rerun"),
    ("timed_out", 1, False, "rerun"),
    ("failure", 2, True, "alarm"),
    ("failure", 2, False, "alarm"),
    ("timed_out", 3, True, "alarm"),
    ("success", 1, True, "close-alarm"),
    ("success", 2, False, "none"),
    # No verdict about the code: cancelled, skipped, or no required job at all.
    ("cancelled", 2, True, "none"),
    ("skipped", 1, True, "none"),
    (None, 2, True, "none"),
])
def test_a_red_main_is_rerun_once_then_alarmed(required, attempt, tip, expected) -> None:
    assert main_red.decide(attempt=attempt, required=required,
                           head_is_main_tip=tip) == expected


def test_nothing_reverts() -> None:
    # The decision reads no parent and no event, so no merge can be named a cause, and
    # the script holds no way to push a branch or open a pull request.
    assert set(inspect.signature(main_red.decide).parameters) == {
        "attempt", "required", "head_is_main_tip"}
    source = inspect.getsource(main_red)
    for word in ('"revert"', '"checkout"', '"pr"', "REVERT_TOKEN", "x-access-token"):
        assert word not in source, word
    body = main_red.render_body(sha="a" * 40, subject="s", run_url="u",
                                failures=["tests/test_x.py::test_y"])
    assert "nothing is reverted automatically" in body and "tests/test_x.py::test_y" in body
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows"
                / "main-red.yml").read_text(encoding="utf-8")
    assert "secrets." not in workflow
