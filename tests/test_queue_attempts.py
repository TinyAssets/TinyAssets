"""The queue-attempt cap counts a removal only when its run failed a test the diff selected.

`scripts/queue_attempts.py` (drift audit F3): pytest ids against the run's own
selection, vitest ids against whether the diff touches `platform/`; anything it
cannot read counts.
"""
from __future__ import annotations

import json
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

    def bad_selection(repo, run, at):
        raise UnicodeDecodeError("utf-8", bytes([255]), 0, 1, "not utf-8")

    monkeypatch.setattr(qa, "_timeline", lambda repo, number: [
        {"event": "added_to_merge_queue", "created_at": "2026-11-04T11:00:00Z"}])
    monkeypatch.setattr(qa, "_runs", lambda repo, since: [
        {"id": 3, "head_branch": "gh-readonly-queue/main/pr-7-c", "conclusion": "failure", "run_attempt": 1,
         "created_at": "2026-11-04T11:01:00Z"}])
    monkeypatch.setattr(qa, "_failed_jobs", lambda repo, run: [
        qa.FailedJob("platform-tests (owner, x -w owner)", ["platform/owner/a.test.ts > s"])])
    monkeypatch.setattr(qa, "_changed", lambda repo, number: ["docs/a.md"])
    monkeypatch.setattr(qa, "_selection", bad_selection)
    assert qa.classify_removal("o/r", 7, "2026-11-04T12:00:00Z")[0] is True
    # The same removal, read in full, does not count: the vitest test is unselected.
    monkeypatch.setattr(qa, "_selection", lambda repo, run, at: None)
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


def test_an_empty_listing_and_an_aggregate_without_a_failed_leg_both_count() -> None:
    for jobs in ([], [qa.FailedJob(qa.AGGREGATE_JOB, [PYTEST], "aggregate-url")]):
        counts, ids, why = qa.attempt_counts(jobs, changed=["docs/x"], selection=[])
        assert counts
        if jobs:
            assert ids == [PYTEST] and "aggregate-url" in why


def test_a_missing_log_keeps_the_other_jobs_evidence_and_names_the_exact_run(monkeypatch) -> None:
    def job(job_id, name, step, *, conclusion="failure", status="completed"):
        return dict(id=job_id, name=name, status=status, conclusion=conclusion, bad_steps=[dict(name=step, conclusion="failure")])

    # Duplicate display names occur across re-runs and must never replace one another.
    records = [
        job(1, LEG, "Run npm run test:workerd -w owner"),
        job(2, LEG, "Run npm run test:workerd -w owner"),
        job(3, "heavy-tests", "", conclusion="skipped"),
        job(4, "affected-tests ${{ matrix.shard }}/4", "", conclusion="skipped"),
        job(5, qa.AGGREGATE_JOB, "Require the platform's tests, the owner app's browser test and the process leg"),
    ]
    calls = []
    def gh(*args):
        if "--paginate" in args:
            return "\n".join(json.dumps({"total_count": len(records), "jobs": page})
                             for page in (records[:2], records[2:]))
        calls.append(args[-1])
        if args[-1].endswith("/2/logs"):
            raise subprocess.TimeoutExpired("gh", 60)
        return " FAIL  test/a.test.ts > s > n\n"

    monkeypatch.setattr(qa, "_gh", gh)
    jobs = qa._failed_jobs("o/r", 33)
    count, ids, why = qa.attempt_counts(jobs, changed=["docs/x"], selection=[])
    assert count and ids == [VITEST]
    assert "/runs/33/job/1" in why and "/runs/33/job/2" in why and "log unavailable" in why
    assert len(calls) == 2
    assert [job.link.rsplit("/", 1)[1] for job in jobs] == ["1", "2"]


@pytest.mark.parametrize("name, status, conclusion, steps", [
    (LEG, "completed", "cancelled", ["Run npm run test:workerd -w owner"]),
    (LEG, "completed", "timed_out", ["Run npm run test:workerd -w owner"]),
    (LEG, "in_progress", None, []),
    (LEG, "completed", "failure", ["Run actions/upload-artifact@v4"]),
    (LEG, "completed", "failure", ["Run npm run test:workerd -w owner", "Upload"]),
    ("platform-checks (typecheck, npm run typecheck)", "completed", "failure", ["Run npm run typecheck"]),
    ("select", "completed", "skipped", []),
])
def test_an_incomplete_or_non_test_leg_cannot_exempt_the_removal(
        monkeypatch, name, status, conclusion, steps) -> None:
    row = dict(id=91, name=name, status=status, conclusion=conclusion, bad_steps=[dict(name=s, conclusion="failure") for s in steps])
    def gh(*args):
        assert "--paginate" in args, "an incidental line from this log cannot attribute a test"
        return json.dumps({"total_count": 1, "jobs": [row]})

    monkeypatch.setattr(qa, "_gh", gh)
    count, ids, why = qa.attempt_counts(qa._failed_jobs("o/r", 3), changed=[], selection=[])
    assert count and not ids and "/runs/3/job/91" in why
    assert (conclusion or status) in why


def test_a_partial_page_preserves_known_ids_but_cannot_excuse_a_removal(monkeypatch) -> None:
    row = dict(id=4, name=SHARD, status="completed", conclusion="failure",
               bad_steps=[dict(name="Run this shard of the selection", conclusion="failure")])
    def gh(*args):
        if "--paginate" in args:
            return json.dumps({"total_count": 40, "jobs": [row]})
        return f"{qa._FAILED_HEADER}**\n- `{PYTEST}`\n"

    monkeypatch.setattr(qa, "_gh", gh)
    count, ids, why = qa.attempt_counts(qa._failed_jobs("o/r", 3), changed=[], selection=[])
    assert count and ids == [PYTEST] and "incomplete jobs listing" in why


def test_reruns_and_selections_updated_after_the_removal_do_not_excuse_it(monkeypatch) -> None:
    removal = "2026-11-18T10:05:00Z"
    monkeypatch.setattr(qa, "_gh", lambda *a: json.dumps({
        "id": 17, "updated_at": "2026-11-18T10:06:00Z"}))
    with pytest.raises(ValueError, match="after this removal"):
        qa._selection("o/r", 3, removal)
    monkeypatch.setattr(qa, "_timeline", lambda *a: [
        {"event": "added_to_merge_queue", "created_at": "2026-11-18T10:00:00Z"}])
    monkeypatch.setattr(qa, "_runs", lambda *a: [
        {"id": 3, "head_branch": "gh-readonly-queue/main/pr-7-a", "conclusion": "failure",
         "run_attempt": 2, "created_at": "2026-11-18T10:01:00Z"}])
    count, run, ids, why = qa.classify_removal("o/r", 7, removal)
    assert count and run == 3 and ids == [] and "rerun" in why


def test_aggregate_integrity_failure_can_coexist_with_off_diff_shard_failure(monkeypatch) -> None:
    def gh(*args):
        if "--paginate" in args:
            assert "/attempts/1/jobs?" in args[2]
            rows = [dict(id=i, name=name, status="completed", conclusion="failure",
                         bad_steps=[dict(name=step, conclusion="failure")]) for i, name, step in [
                (11, SHARD, "Run this shard of the selection"),
                (12, qa.AGGREGATE_JOB, "Decide the gate over this entry's selection")]]
            return json.dumps({"total_count": len(rows), "jobs": rows})
        if args[-1].endswith("/11/logs"):
            return f"{qa._FAILED_HEADER}**\n- `{PYTEST}`"
        return "### Required tests - SHARD SET INCOMPLETE\n- missing shard(s) [3] of 6"

    monkeypatch.setattr(qa, "_gh", gh)
    count, ids, why = qa.attempt_counts(qa._failed_jobs("o/r", 3), changed=[], selection=[])
    assert count and ids == [PYTEST]
    assert "/job/11" in why and "/job/12" in why


@pytest.mark.parametrize("extra, expected", [
    ("", True),
    ("- missing shard(s) [5] of 6: no manifest uploaded\n", False),
])
def test_the_aggregate_notice_distinguishes_a_shard_echo_from_a_lost_manifest(extra, expected):
    stamp = "2026-11-15T00:05:54.8509911Z "
    notice = ("##[notice]### Required tests - SHARD SET INCOMPLETE\n\nThe gate fails closed:\n\n"
              "- shard jobs concluded 'failure', not 'success'\n")
    assert qa._only_shard_failure(stamp + notice + extra + stamp +
                                  "##[error]Process completed with exit code 1.") is expected
    assert not qa._only_shard_failure(stamp + notice)


@pytest.mark.parametrize("count, stale, extra, expected", [
    (1, 0, "", True), (51, 0, "", False), (1, 1, "", False),
    (1, 0, "**Malformed quarantine file:**", False),
])
def test_every_new_python_id_must_be_named_before_an_exemption(count, stale, extra, expected):
    log = f"- NEW failures: **{count}**\n- stale quarantine entries: **{stale}**\n{extra}"
    assert qa._shard_test_summary(log, [PYTEST]) is expected


@pytest.mark.parametrize("replacement, expected", [
    (None, True),
    (("Test Files  1 failed", "Test Files  2 failed"), False),
    (("Tests  1 failed", "Tests  2 failed"), False),
    (("Duration", "Unhandled Errors\nDuration"), False),
    (("Duration", "Source Errors\nDuration"), False),
    (("##[error]Process completed with exit code 1.", ""), False),
])
def test_platform_exemption_requires_the_runner_to_finish_and_account_for_its_failures(
        replacement, expected):
    log = (" FAIL guards test/a.test.ts > s > n\nTest Files  1 failed | 70 passed (71)\n"
           "Tests  1 failed | 1467 passed (1468)\nDuration  269.64s\n"
           "##[error]Process completed with exit code 1.")
    if replacement:
        log = log.replace(*replacement)
    assert qa._vitest_complete(log, [VITEST]) is expected


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
