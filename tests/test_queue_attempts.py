"""The queue-attempt cap counts a removal only when its run failed a test the diff selected.

`scripts/queue_attempts.py` (drift audit F3): pytest ids against the run's own
selection, vitest ids against whether the diff touches `platform/`; anything it
cannot read counts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import queue_attempts as qa  # noqa: E402

pytestmark = pytest.mark.verifies(
    "ci-linux-proof-venue:"
    "A merge-queue removal counts against a head only for a test its diff selected",
)


def test_vitest_and_pytest_failures_are_read_from_a_jobs_log() -> None:
    owner = "\n".join([
        "2026-11-04T18:06:59.3960436Z \x1b[31m⎯⎯ Failed Tests 2 ⎯⎯\x1b[39m",
        "2026-11-04T18:06:59.3962713Z \x1b[41m\x1b[1m FAIL \x1b[22m\x1b[49m \x1b[30m\x1b[45m"
        " workerd \x1b[49m\x1b[39m test/archive-mail.test.ts\x1b[2m > \x1b[22msegments"
        "\x1b[2m > \x1b[22mare streamed (review N5)",
        "2026-11-04T18:06:59.3962800Z  FAIL  test/boot.test.ts [ test/boot.test.ts ]",
    ])
    assert qa.job_failures("platform-tests (owner, npm run test:workerd -w owner)", owner) == [
        "platform/owner/test/archive-mail.test.ts > segments > are streamed (review N5)",
        "platform/owner/test/boot.test.ts",
    ]
    shard = "\n".join([
        "2026-11-04T11:53:17.5136093Z ",
        "2026-11-04T11:53:17.5136100Z **Not this PR's (1):** retried with the patch",
        "2026-11-04T11:53:17.5136200Z - `tests/test_router.py::test_flaky`: a flake",
        "2026-11-04T11:53:17.5138336Z **FAILED — this PR introduces test failures that "
        "`main` does not have.**",
        "2026-11-04T11:53:17.5138589Z ",
        "2026-11-04T11:53:17.5138876Z - `tests/test_owner_router.py::test_box[False]`",
        "2026-11-04T11:53:17.5138900Z ",
        "2026-11-04T11:53:17.5138950Z **FAILED — quarantined tests are passing now. Delete:**",
        "2026-11-04T11:53:17.5139000Z - `tests/test_stale.py::test_passing`",
    ])
    assert qa.job_failures("required-tests shard 4/6", shard) == [
        "tests/test_owner_router.py::test_box[False]"]


VITEST = "platform/owner/test/a.test.ts > s > n"
PYTEST = "tests/test_router.py::test_x"
SHARD = "required-tests shard 4/6"
LEG = "platform-tests (owner, npm run test:workerd -w owner)"


@pytest.mark.parametrize("jobs, changed, selection, counts", [
    # A vitest failure counts only when the diff touches platform/.
    ({LEG: [VITEST], "required-tests": []}, ["docs/a.md"], None, False),
    ({LEG: [VITEST], "required-tests": []}, ["platform/doors/x.ts"], None, True),
    # A pytest failure counts when its file is in the run's selection, or that is ALL.
    ({SHARD: [PYTEST]}, ["platform/x.ts"], ["tests/test_other.py"], False),
    ({SHARD: [PYTEST]}, ["platform/x.ts"], ["tests/test_router.py"], True),
    ({SHARD: [PYTEST]}, ["tinyassets/x.py"], None, True),
    # One selected failure among unselected ones counts.
    ({LEG: [VITEST], SHARD: [PYTEST]}, ["platform/x.ts"], ["tests/test_other.py"], True),
    # Fail closed: a failed job that names no test, only the aggregate, unreadable data.
    ({LEG: [VITEST], "platform-app": []}, ["docs/a.md"], None, True),
    ({"required-tests": []}, ["docs/a.md"], None, True),
    (None, ["docs/a.md"], None, True),
    ({LEG: [VITEST]}, None, None, True),
])
def test_a_removal_counts_only_when_it_failed_a_test_the_diff_selected(
        jobs, changed, selection, counts) -> None:
    assert qa.attempt_counts(jobs, changed=changed, selection=selection)[0] is counts


def test_the_removals_own_run_is_this_queue_entrys_newest_failed_one() -> None:
    runs = [
        # The previous queue entry's failure: before this enqueue, never read (S2).
        {"id": 1, "head_branch": "gh-readonly-queue/main/pr-7-aaa", "conclusion": "failure",
         "created_at": "2026-11-04T10:00:00Z"},
        {"id": 2, "head_branch": "gh-readonly-queue/main/pr-7-bbb", "conclusion": "failure",
         "created_at": "2026-11-04T11:00:00Z"},
        {"id": 3, "head_branch": "gh-readonly-queue/main/pr-7-ccc", "conclusion": "failure",
         "created_at": "2026-11-04T13:00:00Z"},
        {"id": 4, "head_branch": "gh-readonly-queue/main/pr-8-bbb", "conclusion": "failure",
         "created_at": "2026-11-04T11:30:00Z"},
        {"id": 5, "head_branch": "gh-readonly-queue/main/pr-7-ddd", "conclusion": "success",
         "created_at": "2026-11-04T11:40:00Z"},
    ]
    events = [{"event": "added_to_merge_queue", "created_at": "2026-11-04T09:59:00Z"},
              {"event": "added_to_merge_queue", "created_at": "2026-11-04T10:50:00Z"},
              {"event": "added_to_merge_queue", "created_at": "2026-11-04T12:30:00Z"}]
    queued = qa.queued_at(events, "2026-11-04T12:00:00Z")
    assert queued == "2026-11-04T10:50:00Z"
    assert qa.removal_run(runs, 7, queued, "2026-11-04T12:00:00Z") == 2
    assert qa.removal_run(runs, 9, queued, "2026-11-04T12:00:00Z") is None
    # Removed by another check, or before Tests concluded: this entry has no failed
    # Tests run, and the previous entry's run 1 is not read in its place.
    assert qa.removal_run(runs[:1] + runs[4:], 7, queued, "2026-11-04T12:00:00Z") is None
    assert qa.queued_at(events, "2026-11-04T09:00:00Z") is None


def test_a_removal_whose_evidence_cannot_be_read_counts(monkeypatch) -> None:
    def boom(*args):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 502")

    monkeypatch.setattr(qa, "_gh", boom)
    counts, run, _, why = qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")
    assert counts is True and run is None and "could not be read" in why

    def bad_selection(repo, run):
        raise UnicodeDecodeError("utf-8", bytes([255]), 0, 1, "not utf-8")

    monkeypatch.setattr(qa, "_timeline", lambda repo, number: [
        {"event": "added_to_merge_queue", "created_at": "2026-11-04T11:00:00Z"}])
    monkeypatch.setattr(qa, "_runs", lambda repo, since: [
        {"id": 3, "head_branch": "gh-readonly-queue/main/pr-7-c", "conclusion": "failure",
         "created_at": "2026-11-04T11:01:00Z"}])
    monkeypatch.setattr(qa, "_failed_jobs", lambda repo, run: {
        "platform-tests (owner, x -w owner)": ["platform/owner/a.test.ts > s"]})
    monkeypatch.setattr(qa, "_changed", lambda repo, number: ["docs/a.md"])
    monkeypatch.setattr(qa, "_selection", bad_selection)
    assert qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")[0] is True
    # The same removal, read in full, does not count: the vitest test is unselected.
    monkeypatch.setattr(qa, "_selection", lambda repo, run: None)
    assert qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")[0] is False
    # The PR's files list may be cut at the API's 3000: the diff is unknown, so it counts.
    monkeypatch.setattr(qa, "_changed", lambda repo, number: qa.changed_paths(
        [{"filename": f"docs/f{i}.md"} for i in range(qa.FILES_API_CAP)]))
    assert qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")[0] is True
    monkeypatch.setattr(qa, "_changed", lambda repo, number: ["docs/a.md"])
    # No enqueue before the removal: counts.
    monkeypatch.setattr(qa, "_timeline", lambda repo, number: [])
    assert qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")[0] is True
    monkeypatch.setattr(qa, "_timeline", lambda repo, number: [
        {"event": "added_to_merge_queue", "created_at": "2026-11-04T11:00:00Z"}])
    # No run at all, or only an earlier entry's run: counts.
    for runs in ([], [{"id": 2, "head_branch": "gh-readonly-queue/main/pr-7-b",
                       "conclusion": "failure", "created_at": "2026-11-04T10:30:00Z"}]):
        monkeypatch.setattr(qa, "_runs", lambda repo, since, runs=runs: runs)
        assert qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")[0] is True


def test_the_files_list_names_old_and_new_paths_and_refuses_a_full_page() -> None:
    files = [{"filename": "platform/x.ts", "previous_filename": "tinyassets/x.py"},
             {"filename": "docs/a.md", "previous_filename": None}]
    assert qa.changed_paths(files) == ["docs/a.md", "platform/x.ts", "tinyassets/x.py"]
    assert qa.changed_paths([{"filename": "a"}] * (qa.FILES_API_CAP - 1)) == ["a"]
    assert qa.changed_paths([{"filename": "a"}] * qa.FILES_API_CAP) is None


def test_a_not_counted_removal_never_moves_the_head_toward_the_cap(monkeypatch) -> None:
    head = "a" * 40
    removals = iter(["2026-11-04T10:00:00Z", "2026-11-04T11:00:00Z", "2026-11-04T12:00:00Z"])
    verdicts = iter([False, True, True])
    state = {"removal": None, "comments": [], "calls": []}

    def fetch(repo, number):
        return {"headRefOid": head, "labels": {"nodes": []},
                "timelineItems": {"nodes": [{"createdAt": state["removal"],
                                             "reason": "failed_checks"}]}}

    def gh(*args):
        state["calls"].append(args)
        if args[:2] == ("pr", "comment"):
            state["comments"].append({"id": len(state["comments"]), "body": args[-1]})
        elif args[:3] == ("api", "-X", "PATCH"):
            state["comments"][int(args[3].rsplit("/", 1)[1])]["body"] = args[-1][len("body="):]
        return ""

    monkeypatch.setattr(qa, "fetch_pr", fetch)
    monkeypatch.setattr(qa, "_comments", lambda repo, number: list(state["comments"]))
    monkeypatch.setattr(qa, "_gh", gh)
    def classify(repo, number, at):
        # The removal is in the ledger, as counted, before any evidence is read (S1).
        assert f"<!-- attempt {head} {at} -->" in state["comments"][0]["body"]
        return next(verdicts), 41, ["platform/owner/test/a.test.ts > s > n"], "why"

    monkeypatch.setattr(qa, "classify_removal", classify)

    def dequeue() -> bool:
        state["removal"] = next(removals)
        state["calls"].clear()
        qa.handoff("o/r", 7, head, settle_seconds=0)
        return any(call[:2] == ("pr", "edit") for call in state["calls"])

    assert dequeue() is False  # not counted
    assert dequeue() is False  # counted: 1 of 2
    ledger = qa.parse_ledger(state["comments"][0]["body"])
    assert {k: len(v) for k, v in ledger[head].items()} == {"attempt": 1, "not-counted": 1}
    assert dequeue() is True   # counted: 2 of 2, handed off
    assert any(qa.RECORD_MARKER.format(head=head) in c["body"] for c in state["comments"])


def test_a_ledger_written_before_this_change_still_reads() -> None:
    sha = "b" * 40
    old = f"{qa.LEDGER_MARKER}\n<!-- attempt {sha} 2026-11-01T00:00:00Z -->\n"
    ledger = qa.parse_ledger(old)
    assert ledger == {sha: {"attempt": {"2026-11-01T00:00:00Z"}, "not-counted": set()}}
    assert qa.parse_ledger(qa.render_ledger(ledger)) == ledger
