#!/usr/bin/env python3
"""How long each PR took, phase by phase, from GitHub's API (reads only).

    python scripts/pr_times.py 5704 5705     # these PRs
    python scripts/pr_times.py --last 20     # the last 20 merged

Per PR, in minutes: opened -> last ready for review, ready -> first queue entry, last queue
entry -> merged, opened -> merged; the number of queue entries (more than one is a
re-queue); and the head's `Tests` run. Then the median and the max of each column.
The repository is the checkout's (`gh`'s `{owner}/{repo}`) unless `--repo` names one.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_checks import _gh_read  # noqa: E402

COLUMNS = ("open>ready", "ready>queued", "queued>merged", "open>merged", "queue_entries",
           "tests_min")


def _get(path: str) -> list | dict:
    """One read, every page: `--paginate` prints one JSON document per page."""
    text, decoder, docs, at = _gh_read(["--paginate", path], 120), json.JSONDecoder(), [], 0
    end = len(text.rstrip())
    while at < end:
        while text[at].isspace():
            at += 1
        doc, at = decoder.raw_decode(text, at)
        docs.append(doc)
    if all(isinstance(doc, list) for doc in docs):
        return [item for doc in docs for item in doc]
    return docs[0]


def _when(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def _minutes(start: datetime | None, end: datetime | None) -> int | None:
    return round((end - start).total_seconds() / 60) if start and end else None


def phases(pull: dict, timeline: list, runs: list) -> dict[str, int | None]:
    created, merged = _when(pull["created_at"]), _when(pull.get("merged_at"))
    events = [(e.get("event"), _when(e.get("created_at"))) for e in timeline]
    ready = [at for kind, at in events if kind == "ready_for_review"]
    queued = [at for kind, at in events if kind == "added_to_merge_queue"]
    ready_at = ready[-1] if ready else created
    after_ready = [at for at in queued if at >= ready_at]
    tests = [r for r in runs if r.get("name") == "Tests" and r.get("status") == "completed"]
    latest = max(tests, key=lambda r: r["run_started_at"]) if tests else None
    return {
        "open>ready": _minutes(created, ready_at),
        "ready>queued": _minutes(ready_at, after_ready[0] if after_ready else None),
        "queued>merged": _minutes(queued[-1] if queued else None, merged),
        "open>merged": _minutes(created, merged),
        "queue_entries": len(queued),
        "tests_min": _minutes(_when(latest["run_started_at"]), _when(latest["updated_at"]))
        if latest else None,
    }


def summary(rows: list[dict[str, int | None]]) -> str:
    parts = []
    for column in COLUMNS:
        values = [row[column] for row in rows if row[column] is not None]
        if values:
            parts.append(f"{column} median {statistics.median(values):g} max {max(values)}")
    return "; ".join(parts)


def last_merged(repo: str, count: int) -> list[int]:
    pulls = _get(f"repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100")
    merged = sorted((p for p in pulls if p.get("merged_at")), key=lambda p: p["merged_at"],
                    reverse=True)
    return [p["number"] for p in merged[:count]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("numbers", nargs="*", type=int)
    parser.add_argument("--last", type=int, help="the last N merged PRs")
    parser.add_argument("--repo", default="{owner}/{repo}")
    args = parser.parse_args(argv)
    numbers = args.numbers or (last_merged(args.repo, args.last) if args.last else [])
    if not numbers:
        parser.error("name PR numbers or --last N")
    rows = []
    print("pr", *COLUMNS, sep="\t")
    for number in numbers:
        pull = _get(f"repos/{args.repo}/pulls/{number}")
        row = phases(pull, _get(f"repos/{args.repo}/issues/{number}/timeline?per_page=100"),
                     _get(f"repos/{args.repo}/actions/runs?head_sha={pull['head']['sha']}"
                          "&per_page=100")["workflow_runs"])
        rows.append(row)
        print(number, *("-" if row[c] is None else row[c] for c in COLUMNS), sep="\t")
    print(summary(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
