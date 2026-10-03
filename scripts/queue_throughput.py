#!/usr/bin/env python3
"""Read-only merge-queue measurements; never a gate or an enrollment action.

Collect: python scripts/queue_throughput.py collect --repo TinyAssets/TinyAssets
         --since 2026-10-02T00:00:00Z --out /tmp/queue.json
Report:  python scripts/queue_throughput.py report /tmp/queue.json

The cohort is PRs updated since --since plus all currently open PRs created by
--until, and Tests workflow runs created in the window. Full timelines measure episodes
crossing the window boundary. Created-open PRs without an explicit ready event
have UNKNOWN readiness: creation is not evidence of review readiness.
Review latency uses submitted GitHub reviews only, not this repo's custom
Drain-Review comments. It does not attest approval validity or code execution.
Snapshots contain metadata, not PR bodies, comments, logs, or credentials.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

TIMELINE = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){
timelineItems(first:100,after:$cursor,itemTypes:[READY_FOR_REVIEW_EVENT,
CONVERT_TO_DRAFT_EVENT,ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT,
MERGED_EVENT,PULL_REQUEST_REVIEW]){pageInfo{hasNextPage endCursor} nodes{
__typename ... on ReadyForReviewEvent{createdAt}
... on ConvertToDraftEvent{createdAt} ... on AddedToMergeQueueEvent{createdAt}
... on RemovedFromMergeQueueEvent{createdAt reason}
... on MergedEvent{createdAt} ... on PullRequestReview{submittedAt state}
}}}}}"""


def stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def gh(*args: str) -> dict | list:
    raw = subprocess.run(
        ["gh", "api", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    result = json.loads(raw.stdout)
    if isinstance(result, dict) and result.get("errors"):
        raise ValueError(f"GraphQL returned errors: {result['errors']}")
    return result


def timeline(repo: str, number: int) -> list[dict]:
    owner, name = repo.split("/")
    events, cursor = [], None
    while True:
        args = [
            "graphql",
            "-f",
            f"query={TIMELINE}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        page = gh(*args)["data"]["repository"]["pullRequest"]["timelineItems"]
        events.extend(page["nodes"])
        info = page["pageInfo"]
        if not info["hasNextPage"]:
            return events
        if not info["endCursor"] or info["endCursor"] == cursor:
            raise ValueError("timeline pagination did not advance")
        cursor = info["endCursor"]


def collect(repo: str, since: str, until: str) -> dict:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("repo must be OWNER/REPO")
    start, end = stamp(since), stamp(until)
    if start >= end:
        raise ValueError("since must be before until")
    prs, page = {}, 1
    while True:
        batch = gh(
            f"repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page=100&page={page}"
        )
        for pr in batch:
            if stamp(pr["updated_at"]) >= start and stamp(pr["created_at"]) <= end:
                prs[pr["number"]] = {
                    key: pr[key]
                    for key in ("number", "html_url", "created_at", "merged_at", "state", "draft")
                }
        if len(batch) < 100 or stamp(batch[-1]["updated_at"]) < start:
            break
        page += 1
    # Dormant open work must not disappear just because it had no recent update.
    page = 1
    while True:
        batch = gh(f"repos/{repo}/pulls?state=open&per_page=100&page={page}")
        for pr in batch:
            if stamp(pr["created_at"]) <= end:
                prs[pr["number"]] = {
                    key: pr[key]
                    for key in ("number", "html_url", "created_at", "merged_at", "state", "draft")
                }
        if len(batch) < 100:
            break
        page += 1
    for number, pr in prs.items():
        pr["events"] = timeline(repo, number)
    runs, page = {}, 1
    while True:
        data = gh(
            f"repos/{repo}/actions/workflows/tests.yml/runs?per_page=100&page={page}"
            f"&created={iso(start)}..{iso(end)}"
        )
        # GitHub caps filtered workflow-run searches at 1000. Refuse a partial
        # baseline instead of claiming pagination fetched everything.
        if data["total_count"] >= 1000:
            raise ValueError("workflow search reached 1000-run cap; choose a shorter window")
        for run in data["workflow_runs"]:
            runs[run["id"]] = {
                key: run[key]
                for key in (
                    "id",
                    "html_url",
                    "event",
                    "head_sha",
                    "head_branch",
                    "created_at",
                    "run_started_at",
                    "status",
                    "conclusion",
                    "run_attempt",
                )
            }
        if len(data["workflow_runs"]) < 100:
            break
        page += 1
    return {
        "schema_version": 1,
        "repo": repo,
        "since": iso(start),
        "until": iso(end),
        "collected_at": iso(datetime.now(timezone.utc)),
        "prs": sorted(prs.values(), key=lambda pr: pr["number"]),
        "runs": list(runs.values()),
        "limitations": [
            "Live API pagination is not an atomic historical snapshot; retain collected_at.",
            "PR current state is observed at collection, not reconstructed at until.",
            "Run records expose latest attempt only; run_attempt>1 hides earlier outcomes.",
            "Native reviews exclude custom Drain-Review comments; no review validity inferred.",
            "Cancellation is an outcome, not proof of obsolete work; logs/steps are not collected.",
            "No active-work, runner-minute, test-count or failure-root-cause estimates.",
        ],
    }


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50_minutes": round(ordered[math.ceil(len(ordered) * 0.5) - 1], 2) if ordered else None,
        "p95_minutes": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 2) if ordered else None,
    }


def report(snapshot: dict) -> dict:
    if snapshot.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema")
    start, end = stamp(snapshot["since"]), stamp(snapshot["until"])
    counts, removals, durations, rows = Counter(), Counter(), {}, []

    def inside(value):
        return start <= value <= end

    def duration(name, beginning, finish):
        durations.setdefault(name, []).append((finish - beginning).total_seconds() / 60)

    for pr in snapshot["prs"]:
        # A submitted review can be pending (null submittedAt). It is not a response.
        events = sorted(
            (
                (stamp(e.get("createdAt") or e["submittedAt"]), e)
                for e in pr["events"]
                if e.get("createdAt") or e.get("submittedAt")
            ),
            key=lambda item: item[0],
        )
        ready = queued = None
        responded = False
        entries = exits = 0
        for at, event in events:
            if at > end:
                continue
            kind = event["__typename"]
            if kind == "ReadyForReviewEvent":
                ready, responded = at, False
                if inside(at):
                    counts["ready_events"] += 1
            elif kind == "ConvertToDraftEvent":
                ready, responded = None, False
            elif kind == "PullRequestReview" and ready and not responded:
                if inside(at):
                    duration("ready_to_first_native_review", ready, at)
                responded = True
            elif kind == "AddedToMergeQueueEvent":
                if queued is not None:
                    counts["unpaired_queue_entries"] += 1
                queued = at
                if inside(at):
                    entries += 1
            elif kind == "RemovedFromMergeQueueEvent":
                # GitHub also emits a removal with reason=merged. It is a
                # successful departure, not an eviction/retry. It can precede
                # or follow MergedEvent, so consume the episode only once.
                merged_exit = (event.get("reason") or "").lower() == "merged"
                if inside(at):
                    removals[event.get("reason") or "UNKNOWN"] += 1
                    if not merged_exit:
                        exits += 1
                    if queued:
                        duration(
                            "final_queue_entry_to_merge"
                            if merged_exit
                            else "queue_entry_to_ejection",
                            queued,
                            at,
                        )
                    elif not merged_exit:
                        counts["unpaired_queue_exits"] += 1
                queued = None
            elif kind == "MergedEvent":
                if inside(at):
                    counts["merged"] += 1
                    duration("created_to_merge", stamp(pr["created_at"]), at)
                    if ready:
                        duration("latest_explicit_ready_to_merge", ready, at)
                    else:
                        counts["merges_without_explicit_ready"] += 1
                    if queued:
                        duration("final_queue_entry_to_merge", queued, at)
                ready = queued = None
        counts["queue_entries"] += entries
        counts["queue_ejections"] += exits
        if queued:
            counts["open_queue_episodes"] += 1
        rows.append(
            {
                "number": pr["number"],
                "url": pr["html_url"],
                "queue_entries": entries,
                "queue_ejections": exits,
                "open_queue_age_minutes": round((end - queued).total_seconds() / 60, 2)
                if queued
                else None,
                "explicit_ready_age_minutes": round((end - ready).total_seconds() / 60, 2)
                if ready and pr["state"] == "open" and not pr["draft"]
                else None,
            }
        )
    by_event, run_counts, seen, shas_by_event = {}, Counter(), set(), {}
    for run in snapshot["runs"]:
        if run["id"] in seen or not inside(stamp(run["created_at"])):
            continue
        seen.add(run["id"])
        outcome = run["conclusion"] if run["status"] == "completed" else "in_progress_or_queued"
        by_event.setdefault(run["event"], Counter())[outcome or "unknown"] += 1
        shas_by_event.setdefault(run["event"], Counter())[run["head_sha"]] += 1
        run_counts["rerun_records"] += run["run_attempt"] > 1
    for event in sorted(by_event):
        # Distinct runs of a SHA are not necessarily waste (different events,
        # intentional retries). Label repetition, never call it obsolete.
        shas = shas_by_event[event]
        run_counts[f"{event}_same_sha_extra_runs"] = sum(n - 1 for n in shas.values())
    return {
        "repo": snapshot["repo"],
        "since": snapshot["since"],
        "until": snapshot["until"],
        "collected_at": snapshot.get("collected_at"),
        "cohort_prs": len(rows),
        "counts": dict(counts),
        "removal_reasons": dict(removals),
        "durations": {name: distribution(values) for name, values in durations.items()},
        "tests_workflow_outcomes": by_event,
        "run_counts": dict(run_counts),
        "prs": rows,
        "limitations": snapshot.get("limitations", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("collect")
    capture.add_argument("--repo", required=True)
    capture.add_argument("--since", required=True)
    capture.add_argument("--until")
    capture.add_argument("--out", type=Path, required=True)
    render = commands.add_parser("report")
    render.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        result = collect(args.repo, args.since, args.until or iso(datetime.now(timezone.utc)))
        # Write only after every API read succeeds; no partial-success snapshot.
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(report(json.loads(args.snapshot.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
