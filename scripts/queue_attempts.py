#!/usr/bin/env python3
"""Cap blind merge-queue retries: two counted failures on one head, then hand off.

Measured 2026-09-27..10-01: 39 of 65 queue removals were `failed_checks`, and
one PR (#4164) was removed for failed checks six times on the way to merging.
Each retry costs a full merge-group run and holds every PR queued behind it.

A removal counts against the head only when its run failed a test the entry's
diff selected (drift audit F3). The PR's own run already ran every vitest leg
and its affected Python tests, so a failure seen only in the queue is a flake,
main drift or an entry ahead, unless the diff selected that test:

- a pytest id (`tests/x.py::name`) when its file is in the run's own selection
  (`affected-selection`: `affected_tests.py --gate`), or the selection is ALL;
- a vitest id (`platform/<ws>/<file> > <suite> > <name>`) when the diff touches
  `platform/`. Workspaces import one another, so a workspace is not a boundary.

It fails closed. All of these count, as every removal did before:
- any error reading GitHub;
- no failed Tests run of this PR's queue entry between its enqueue and this removal;
- an unreadable log, selection or diff;
- a failed job that names no test (typecheck, the browser job, a lost shard).

Everything is keyed by the head SHA, never by a timestamp:

- On each `failed_checks` dequeue, `handoff` records that removal against the
  dequeued head SHA in one ledger comment on the PR (idempotent per removal).
  It records it as counted FIRST, and only then reads the evidence; it moves
  the removal to "not counted" only on a complete reading that says so.
- On the second COUNTED removal of the same SHA it posts the failure record
  (marker `queue-handoff:<sha>`), adds the `queue-handoff` label and disables
  auto-merge, after re-checking that the head has not moved.
- `check` (auto-enroll) reports capped only while BOTH hold: the record for
  the CURRENT head exists and the label is on the PR. A new head is never
  capped; removing the label is how a person retries a head; and nothing is
  blocked unless the record was actually delivered.

    queue_attempts.py check   --repo R --pr N             # exit 3 when capped
    queue_attempts.py handoff --repo R --pr N --head SHA  # on dequeue
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime

CAP = 2
LABEL = "queue-handoff"
CAPPED_EXIT = 3
LEDGER_MARKER = "<!-- queue-attempts-ledger -->"
RECORD_MARKER = "<!-- queue-handoff:{head} -->"
GH_TIMEOUT = 60
#: The aggregate fails whenever a leg does; it names a test only through them.
AGGREGATE_JOB = "required-tests"

_PR_QUERY = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
pullRequest(number:$number){headRefOid labels(first:50){nodes{name}}
timelineItems(last:5,itemTypes:[REMOVED_FROM_MERGE_QUEUE_EVENT]){
nodes{... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}"""
_LEDGER_LINE = re.compile(
    r"^<!-- (?P<kind>attempt|not-counted) (?P<sha>[0-9a-f]{40}) (?P<at>\S+) -->$", re.M)
_FAILED_HEADER = "**FAILED — this PR introduces test failures"
_BULLET = re.compile(r"`(tests/[^`]+::[^`]+)`")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STAMP = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ?")
#: Vitest's summary line: ` FAIL  [project]  <file> > <suite> > <name>`, or a
#: file that failed as a whole: ` FAIL  <file> [ <file> ]`.
_VITEST = re.compile(
    r"^\s*FAIL\s+(?:\S+\s+)??(?P<file>[\w./@-]+\.(?:test|spec)\.[cm]?[jt]sx?)"
    r"(?:\s+>\s+(?P<name>.+?))?(?:\s+\[.*\])?\s*$")
_WORKSPACE = re.compile(r"\s-w\s+([\w-]+)")


# ---- the decision (pure) -----------------------------------------------------


def parse_ledger(body: str) -> dict[str, dict[str, set[str]]]:
    """{head sha: {"attempt": counted removals, "not-counted": the others}}."""
    ledger: dict[str, dict[str, set[str]]] = {}
    for m in _LEDGER_LINE.finditer(body):
        entry = ledger.setdefault(m["sha"], {"attempt": set(), "not-counted": set()})
        entry[m["kind"]].add(m["at"])
    return ledger


def render_ledger(ledger: dict[str, dict[str, set[str]]]) -> str:
    lines = [LEDGER_MARKER,
             "Merge-queue removals for failed checks, per head (`scripts/queue_attempts.py`; "
             f"a head is handed off after {CAP} counted). A removal counts only when its "
             "run failed a test this PR's diff selected."]
    for sha in sorted(ledger):
        entry = ledger[sha]
        for kind in ("attempt", "not-counted"):
            lines += [f"<!-- {kind} {sha} {at} -->" for at in sorted(entry[kind])]
        lines.append(f"- `{sha[:12]}`: {len(entry['attempt'])} counted, "
                     f"{len(entry['not-counted'])} not counted")
    return "\n".join(lines) + "\n"


def is_capped(head: str, labels: set[str], comment_bodies: list[str]) -> bool:
    record = RECORD_MARKER.format(head=head)
    return LABEL in labels and any(record in body for body in comment_bodies)


def _python_ids(text: str) -> list[str]:
    """Node ids under the gate's NEW-failures heading only.

    The same summary also lists stale quarantine entries, which are PASSING
    tests, and the failures the attribution excused; reading every backticked
    id would name those as this PR's.
    """
    found: set[str] = set()
    for block in text.split(_FAILED_HEADER)[1:]:
        for line in block.splitlines()[1:]:
            # count=1: after an empty match at the start, a second, non-empty
            # match from the same place would eat the whole line.
            line = re.sub(r"^.*?(?=- `|\*\*|$)", "", line, count=1).strip()
            if line.startswith("**"):
                break  # the next section (e.g. stale entries) starts here
            found.update(_BULLET.findall(line))
    return sorted(found)


def _vitest_ids(lines: list[str], workspace: str | None) -> list[str]:
    root = f"platform/{workspace}/" if workspace else "platform/"
    found = set()
    for line in lines:
        m = _VITEST.match(line)
        if m:
            found.add(root + m["file"] + (f" > {m['name']}" if m["name"] else ""))
    return sorted(found)


def job_failures(job: str, log: str) -> list[str]:
    """The test ids one failed job's log names.

    Pytest ids come from the gate's FAILED heading; vitest ids from its summary,
    under the leg's workspace (`-w <ws>` in the job's name). The whole job log,
    not `gh run view --log-failed`, which cut run 41246700526's leg before its
    summary.
    """
    lines = [_STAMP.sub("", _ANSI.sub("", raw)) for raw in log.splitlines()]
    ws = _WORKSPACE.search(job)
    return sorted(set(_python_ids("\n".join(lines)))
                  | set(_vitest_ids(lines, ws[1] if ws else None)))


def is_selected(test_id: str, *, changed: list[str], selection: list[str] | None) -> bool:
    """Whether the entry's diff selected this test. `selection` None means ALL."""
    if test_id.startswith("platform/"):
        return any(path.startswith("platform/") for path in changed)
    return selection is None or test_id.split("::", 1)[0] in selection


def attempt_counts(jobs: dict[str, list[str]] | None, *, changed: list[str] | None,
                   selection: list[str] | None) -> tuple[bool, list[str], str]:
    """(counts against the head, the failing tests, why). Fails closed."""
    if jobs is None:
        return True, [], "the failed run or its log could not be read"
    if changed is None:
        return True, [], "the PR's diff could not be read"
    legs = {job: ids for job, ids in jobs.items() if job != AGGREGATE_JOB} or jobs
    unnamed = sorted(job for job, ids in legs.items() if not ids)
    if unnamed:
        return True, [], f"a failed job names no test: {', '.join(unnamed)}"
    ids = sorted({test_id for found in legs.values() for test_id in found})
    selected = [t for t in ids if is_selected(t, changed=changed, selection=selection)]
    if selected:
        return True, selected, "it failed a test the diff selected"
    return False, ids, "every failing test lies outside the diff's selection"


def render_record(head: str, run: int | None, failing: list[str], why: str,
                  counted: set[str]) -> str:
    tests = "\n".join(f"- `{t}`" for t in failing[:40]) or "- (none named)"
    return "\n".join([
        RECORD_MARKER.format(head=head),
        f"### Queue handoff: {CAP} counted merge-queue failures on `{head[:12]}`",
        "",
        "This head will not be re-armed as it is (`scripts/queue_attempts.py`). Push "
        f"a fix (a new head is never capped), or remove the `{LABEL}` label to retry "
        "this head.",
        "",
        f"Counted removals: {', '.join(sorted(counted))}.",
        f"The last one's run: {f'`{run}`' if run else 'not found'}; it counted because {why}:",
        "",
        tests,
    ]) + "\n"


# ---- GitHub I/O (thin) ---------------------------------------------------------


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True, timeout=GH_TIMEOUT,
    ).stdout


def fetch_pr(repo: str, number: int) -> dict:
    owner, name = repo.split("/", 1)
    raw = _gh("api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}",
              "-F", f"number={number}", "-f", f"query={_PR_QUERY}")
    return json.loads(raw)["data"]["repository"]["pullRequest"]


def _comments(repo: str, number: int) -> list[dict]:
    raw = _gh("api", "--paginate", f"repos/{repo}/issues/{number}/comments",
              "--jq", ".[] | {id, body}")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _queue_pr(head_branch: str) -> int | None:
    m = re.match(r"gh-readonly-queue/[^/]+/pr-(\d+)-", head_branch or "")
    return int(m.group(1)) if m else None


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def queued_at(events: list[dict], removal_at: str) -> str | None:
    """When this PR last entered the queue before the removal (REST issue timeline)."""
    at = _when(removal_at)
    added = [e["created_at"] for e in events
             if e.get("event") == "added_to_merge_queue" and _when(e["created_at"]) <= at]
    return max(added, key=_when) if added else None


def removal_run(runs: list[dict], number: int, queued: str, removal_at: str) -> int | None:
    """This queue entry's failed Tests run: created after it was queued, before its removal.

    Bounded below by the enqueue, so an earlier entry's run is never read: a
    removal by another required check, or before Tests concluded, finds none
    and counts.
    """
    low, high = _when(queued), _when(removal_at)
    own = [r for r in runs if _queue_pr(r["head_branch"]) == number
           and r["conclusion"] == "failure" and low <= _when(r["created_at"]) <= high]
    return max(own, key=lambda r: _when(r["created_at"]))["id"] if own else None


def _timeline(repo: str, number: int) -> list[dict]:
    raw = _gh("api", "--paginate", f"repos/{repo}/issues/{number}/timeline?per_page=100",
              "--jq", '.[] | select(.event == "added_to_merge_queue") | {event, created_at}')
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _runs(repo: str, since: str) -> list[dict]:
    raw = _gh("api", "--paginate",
              f"repos/{repo}/actions/workflows/tests.yml/runs?event=merge_group"
              f"&created=>={_when(since):%Y-%m-%dT%H:%M:%SZ}&per_page=100",
              "--jq", ".workflow_runs[] | {id, head_branch, conclusion, created_at}")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _selection(repo: str, run_id: int) -> list[str] | None:
    """The run's own Python selection; None means ALL, and so does no artifact."""
    ids = _gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts?name=affected-selection",
              "--jq", ".artifacts[] | select(.expired | not) | .id").split()
    if len(ids) != 1:
        return None
    blob = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{ids[0]}/zip"],
                          capture_output=True, check=True, timeout=GH_TIMEOUT).stdout
    text = zipfile.ZipFile(io.BytesIO(blob)).read("affected.txt").decode("utf-8")
    entries = [line.strip() for line in text.splitlines() if line.strip()]
    return None if entries == ["ALL"] else entries


#: `GET /pulls/{n}/files` lists at most 3000 files, so a list that long may be cut.
FILES_API_CAP = 3000


def changed_paths(files: list[dict]) -> list[str] | None:
    """Every path the PR's files name (a rename's old path too); None if the list may be cut."""
    if len(files) >= FILES_API_CAP:
        return None
    return sorted({path for f in files
                   for path in (f.get("filename"), f.get("previous_filename")) if path})


def _changed(repo: str, number: int) -> list[str] | None:
    raw = _gh("api", "--paginate", f"repos/{repo}/pulls/{number}/files?per_page=100",
              "--jq", ".[] | {filename, previous_filename}")
    return changed_paths([json.loads(line) for line in raw.splitlines() if line.strip()])


def _failed_jobs(repo: str, run_id: int) -> dict[str, list[str]]:
    """{each job that did not pass: the test ids its log names}."""
    raw = _gh("api", "--paginate", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
              "--jq", '.jobs[] | select(.conclusion != "success" and .conclusion != '
              '"skipped" and .conclusion != "neutral") | {id, name}')
    jobs = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return {job["name"]: job_failures(job["name"], _gh(
        "api", f"repos/{repo}/actions/jobs/{job['id']}/logs")) for job in jobs}


def classify_removal(repo: str, number: int,
                     removal_at: str) -> tuple[bool, int | None, list[str], str]:
    """(counts, run, failing tests, why). Any error reading GitHub counts."""
    try:
        queued = queued_at(_timeline(repo, number), removal_at)
        if queued is None:
            return True, None, [], "no enqueue was found before this removal"
        run = removal_run(_runs(repo, queued), number, queued, removal_at)
        if run is None:
            return True, None, [], "no failed Tests run of this queue entry was found"
        counts, ids, why = attempt_counts(_failed_jobs(repo, run),
                                          changed=_changed(repo, number),
                                          selection=_selection(repo, run))
        return counts, run, ids, why
    except Exception as exc:  # noqa: BLE001 - fail closed: an unread removal counts
        return True, None, [], f"the evidence could not be read ({type(exc).__name__}: {exc})"


def _write_ledger(repo: str, number: int, comment: dict | None, body: str) -> dict:
    if comment is None:
        _gh("pr", "comment", str(number), "-R", repo, "--body", body)
        return next(c for c in _comments(repo, number) if LEDGER_MARKER in c["body"])
    if comment["body"] != body:
        _gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment['id']}",
            "-f", f"body={body}")
    return {**comment, "body": body}


def handoff(repo: str, number: int, head: str, settle_seconds: float = 20.0) -> int:
    pr = fetch_pr(repo, number)
    removals = pr["timelineItems"]["nodes"]
    if pr["headRefOid"] != head:
        print(f"PR #{number} moved past {head[:12]}; this dequeue is stale.")
        return 0
    if not removals or removals[-1].get("reason") != "failed_checks":
        print(f"PR #{number}: latest removal is not failed_checks; not an attempt.")
        return 0
    removal_at = removals[-1]["createdAt"]

    comments = _comments(repo, number)
    ledger_comment = next((c for c in comments if LEDGER_MARKER in c["body"]), None)
    ledger = parse_ledger(ledger_comment["body"]) if ledger_comment else {}
    entry = ledger.setdefault(head, {"attempt": set(), "not-counted": set()})
    if removal_at in entry["attempt"] | entry["not-counted"]:
        # Either a re-delivered event, or this removal's timeline entry is not
        # readable yet and the latest one is the previous removal. Look once more.
        time.sleep(settle_seconds)
        latest = fetch_pr(repo, number)["timelineItems"]["nodes"]
        if not latest or latest[-1].get("reason") != "failed_checks":
            return 0
        removal_at = latest[-1]["createdAt"]
        if removal_at in entry["attempt"] | entry["not-counted"]:
            return 0
    # Counted first: if anything below fails or the job dies, the removal stays counted.
    entry["attempt"].add(removal_at)
    ledger_comment = _write_ledger(repo, number, ledger_comment, render_ledger(ledger))
    counts, run, failing, why = classify_removal(repo, number, removal_at)
    if not counts:
        entry["attempt"].discard(removal_at)
        entry["not-counted"].add(removal_at)
        ledger_comment = _write_ledger(repo, number, ledger_comment, render_ledger(ledger))
    print(f"PR #{number}: the removal at {removal_at} (run {run}) "
          f"{'counts' if counts else 'does not count'}: {why}. "
          f"Failing: {', '.join(failing) or 'none named'}")
    if len(entry["attempt"]) < CAP:
        print(f"PR #{number}: {len(entry['attempt'])} counted failure(s) on {head[:12]}; "
              f"cap {CAP}.")
        return 0

    record = render_record(head, run, failing, why, entry["attempt"])
    # Re-check right before acting: a push during the reads above must not be
    # disabled by a handoff that was about the previous head.
    if fetch_pr(repo, number)["headRefOid"] != head:
        print(f"PR #{number} moved while the record was built; not handing off.")
        return 0
    if not any(RECORD_MARKER.format(head=head) in c["body"] for c in _comments(repo, number)):
        _gh("pr", "comment", str(number), "-R", repo, "--body", record)
    _gh("label", "create", LABEL, "-R", repo, "--force", "--color", "B60205",
        "--description", "Failed the merge queue twice on one head; not re-armed")
    _gh("pr", "edit", str(number), "-R", repo, "--add-label", LABEL)
    try:
        _gh("pr", "merge", str(number), "-R", repo, "--disable-auto")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # Not armed: the removal already disarmed it.
    print(record)
    return 0


def check(repo: str, number: int) -> int:
    pr = fetch_pr(repo, number)
    head = pr["headRefOid"]
    labels = {n["name"] for n in pr["labels"]["nodes"]}
    if LABEL not in labels:
        return 0
    if is_capped(head, labels, [c["body"] for c in _comments(repo, number)]):
        print(f"PR #{number} is handed off on head {head[:12]}: push a fix, or remove "
              f"the {LABEL} label to retry this head.")
        return CAPPED_EXIT
    # The label belongs to an earlier head; it no longer describes this one.
    _gh("pr", "edit", str(number), "-R", repo, "--remove-label", LABEL)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["check", "handoff"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head", help="handoff: the dequeued head SHA (from the event)")
    args = ap.parse_args(argv)
    if args.command == "check":
        return check(args.repo, args.pr)
    if not args.head or not re.fullmatch(r"[0-9a-f]{40}", args.head):
        ap.error("handoff needs --head <40-hex sha>")
    return handoff(args.repo, args.pr, args.head)


if __name__ == "__main__":
    sys.exit(main())
