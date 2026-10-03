"""Queue measurements must not turn incomplete evidence into fast success."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "queue_throughput", Path(__file__).resolve().parents[1] / "scripts/queue_throughput.py"
)
assert _spec and _spec.loader
qt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qt)


def event(kind, at, **fields):
    return {"__typename": kind, "createdAt": f"2026-10-02T{at}:00Z", **fields}


def snapshot(events=(), runs=(), **fields):
    return {
        "schema_version": 1,
        "repo": "o/r",
        "since": "2026-10-02T01:00:00Z",
        "until": "2026-10-02T05:00:00Z",
        "runs": list(runs),
        "prs": [
            {
                "number": 1,
                "html_url": "https://github.com/o/r/pull/1",
                "created_at": "2026-10-02T00:00:00Z",
                "state": "open",
                "draft": False,
                "events": list(events),
                **fields,
            }
        ],
    }


def test_retry_durations_pair_each_removal_not_first_entry_to_merge():
    result = qt.report(
        snapshot(
            [
                event("ReadyForReviewEvent", "00:30"),
                event("AddedToMergeQueueEvent", "00:45"),
                event("RemovedFromMergeQueueEvent", "01:15", reason="FAILED_CHECKS"),
                event("AddedToMergeQueueEvent", "02:00"),
                event("MergedEvent", "02:20"),
            ],
            state="closed",
        )
    )
    assert result["counts"]["queue_entries"] == 1  # first entry predates the window
    assert result["removal_reasons"] == {"FAILED_CHECKS": 1}
    assert result["durations"]["queue_entry_to_ejection"]["p50_minutes"] == 30
    assert result["durations"]["final_queue_entry_to_merge"]["p50_minutes"] == 20
    assert result["durations"]["latest_explicit_ready_to_merge"]["p50_minutes"] == 110
    assert result["prs"][0]["explicit_ready_age_minutes"] is None


def test_no_ready_event_is_unknown_not_creation_time():
    result = qt.report(snapshot([event("MergedEvent", "02:00")], state="closed"))
    assert result["counts"]["merges_without_explicit_ready"] == 1
    assert "latest_explicit_ready_to_merge" not in result["durations"]
    assert result["durations"]["created_to_merge"]["p50_minutes"] == 120


def test_draft_resets_ready_episode_and_pending_review_is_not_response():
    result = qt.report(
        snapshot(
            [
                event("ReadyForReviewEvent", "01:00"),
                event("ConvertToDraftEvent", "01:10"),
                event("PullRequestReview", "01:15", state="APPROVED"),
                event("ReadyForReviewEvent", "02:00"),
                {"__typename": "PullRequestReview", "submittedAt": None, "state": "PENDING"},
                {
                    "__typename": "PullRequestReview",
                    "submittedAt": "2026-10-02T02:30:00Z",
                    "state": "COMMENTED",
                },
                event("PullRequestReview", "03:00", state="APPROVED"),
            ]
        )
    )
    assert result["durations"]["ready_to_first_native_review"] == {
        "samples": 1,
        "p50_minutes": 30,
        "p95_minutes": 30,
    }
    assert result["prs"][0]["explicit_ready_age_minutes"] == 180


def test_open_queue_and_unmatched_exit_never_count_as_merges():
    result = qt.report(
        snapshot(
            [
                event("RemovedFromMergeQueueEvent", "01:30", reason="UNKNOWN"),
                event("AddedToMergeQueueEvent", "03:00"),
                event("MergedEvent", "06:00"),  # collected later, outside report window
            ]
        )
    )
    assert result["counts"].get("merged", 0) == 0
    assert result["counts"]["unpaired_queue_exits"] == 1
    assert result["prs"][0]["open_queue_age_minutes"] == 120


def test_equal_timestamp_events_preserve_api_order():
    result = qt.report(
        snapshot(
            [
                event("AddedToMergeQueueEvent", "02:00"),
                event("RemovedFromMergeQueueEvent", "02:00", reason="MANUAL"),
            ]
        )
    )
    assert result["durations"]["queue_entry_to_ejection"]["p50_minutes"] == 0


@pytest.mark.parametrize("removed_first", [True, False])
def test_successful_queue_removal_and_merge_are_one_episode(removed_first):
    ending = [
        event("RemovedFromMergeQueueEvent", "02:30", reason="merged"),
        event("MergedEvent", "02:30"),
    ]
    if not removed_first:
        ending.reverse()
    result = qt.report(snapshot([event("AddedToMergeQueueEvent", "02:00"), *ending]))
    assert result["counts"]["queue_ejections"] == 0
    assert result["counts"].get("unpaired_queue_exits", 0) == 0
    assert result["durations"]["final_queue_entry_to_merge"] == {
        "samples": 1,
        "p50_minutes": 30,
        "p95_minutes": 30,
    }


def run(number, event_name="merge_group", **fields):
    return {
        "id": number,
        "event": event_name,
        "head_sha": "a" * 40,
        "created_at": "2026-10-02T02:00:00Z",
        "status": "completed",
        "conclusion": "success",
        "run_attempt": 1,
        **fields,
    }


def test_runs_deduplicate_and_preserve_event_outcome_and_hidden_attempts():
    result = qt.report(
        snapshot(
            runs=[
                run(1),
                run(1),
                run(2, conclusion="cancelled"),
                run(3, "pull_request", run_attempt=2),
                run(4, status="in_progress", conclusion=None),
            ]
        )
    )
    assert result["tests_workflow_outcomes"]["merge_group"] == {
        "success": 1,
        "cancelled": 1,
        "in_progress_or_queued": 1,
    }
    assert result["run_counts"]["merge_group_same_sha_extra_runs"] == 2
    assert result["run_counts"]["pull_request_same_sha_extra_runs"] == 0
    assert result["run_counts"]["rerun_records"] == 1


def test_percentiles_include_denominators_and_empty_is_not_zero():
    assert qt.distribution([]) == {"samples": 0, "p50_minutes": None, "p95_minutes": None}
    assert qt.distribution([100, 1, 2])["p95_minutes"] == 100


def test_graphql_pagination_and_null_cursor(monkeypatch):
    calls = []

    def fake(*args):
        calls.append(args)
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "timelineItems": {
                            "nodes": [event("ReadyForReviewEvent", "01:00")],
                            "pageInfo": {"hasNextPage": len(calls) == 1, "endCursor": "next"},
                        }
                    }
                }
            }
        }

    monkeypatch.setattr(qt, "gh", fake)
    assert len(qt.timeline("o/r", 1)) == 2
    assert "cursor=next" not in calls[0]
    assert "cursor=next" in calls[1]


def test_graphql_pagination_stall_is_error(monkeypatch):
    monkeypatch.setattr(
        qt,
        "gh",
        lambda *args: {
            "data": {
                "repository": {
                    "pullRequest": {
                        "timelineItems": {
                            "nodes": [],
                            "pageInfo": {"hasNextPage": True, "endCursor": None},
                        }
                    }
                }
            }
        },
    )
    with pytest.raises(ValueError, match="pagination"):
        qt.timeline("o/r", 1)


def test_collection_refuses_capped_workflow_results(monkeypatch):
    monkeypatch.setattr(
        qt,
        "gh",
        lambda endpoint: (
            [] if "/pulls?" in endpoint else {"total_count": 1000, "workflow_runs": []}
        ),
    )
    with pytest.raises(ValueError, match="1000-run cap"):
        qt.collect("o/r", "2026-10-02T01:00:00Z", "2026-10-02T05:00:00Z")


def test_timezone_required():
    with pytest.raises(ValueError, match="timezone"):
        qt.stamp("2026-10-02T01:00:00")


def test_collection_includes_dormant_open_work_but_excludes_future_prs(monkeypatch):
    def pr(number, created, updated):
        return {
            "number": number,
            "html_url": f"https://github.com/o/r/pull/{number}",
            "created_at": created,
            "updated_at": updated,
            "merged_at": None,
            "state": "open",
            "draft": False,
        }

    old = pr(1, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z")
    future = pr(2, "2026-10-03T00:00:00Z", "2026-10-03T00:00:00Z")

    def fake(endpoint):
        if "state=all" in endpoint:
            return [future, old]
        if "state=open" in endpoint:
            return [old, future]
        return {"total_count": 0, "workflow_runs": []}

    monkeypatch.setattr(qt, "gh", fake)
    monkeypatch.setattr(qt, "timeline", lambda repo, number: [])
    result = qt.collect("o/r", "2026-10-02T01:00:00Z", "2026-10-02T05:00:00Z")
    assert [pr["number"] for pr in result["prs"]] == [1]
