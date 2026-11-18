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
import typing
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
    if job.startswith(("platform-tests (", "platform-sizes (")):
        return _vitest_ids(lines, ws[1] if ws else None)
    if job == AGGREGATE_JOB or job.startswith("required-tests shard "):
        return _python_ids("\n".join(lines))
    return []


def is_selected(test_id: str, *, changed: list[str], selection: list[str] | None) -> bool:
    """Whether the entry's diff selected this test. `selection` None means ALL."""
    if test_id.startswith("platform/"):
        return any(path.startswith("platform/") for path in changed)
    return selection is None or test_id.split("::", 1)[0] in selection


class FailedJob(typing.NamedTuple):
    name: str
    ids: list[str]
    link: str = ""
    outcome: str = "failure"
    complete: bool = True

    def describe(self) -> str:
        job = f"[{self.name}]({self.link})" if self.link else self.name
        return f"{job} ({self.outcome})"


def attempt_counts(jobs: list[FailedJob] | dict[str, list[str]] | None, *, changed: list[str] | None,
                   selection: list[str] | None) -> tuple[bool, list[str], str]:
    """(counts against the head, the tests identified, why). Fails closed."""
    if isinstance(jobs, dict):
        # Callers that already separated the failed legs supply the aggregate's echo too.
        legs = {name: ids for name, ids in jobs.items() if name != AGGREGATE_JOB}
        jobs = [FailedJob(name, ids) for name, ids in (legs or jobs).items()]
    if not jobs:
        return True, [], "the failed run supplied no unsuccessful job evidence"
    ids = sorted({test for job in jobs for test in job.ids})
    sources = ", ".join(job.describe() for job in jobs)
    if any(job.name == AGGREGATE_JOB for job in jobs):
        return True, ids, f"the aggregate has no confirmed upstream cause in {sources}"
    if any(not job.ids or not job.complete for job in jobs):
        return True, ids, f"test attribution is incomplete for {sources}"
    if changed is None:
        return True, ids, f"the diff or selection could not be read for {sources}"
    selected = [t for t in ids if is_selected(t, changed=changed, selection=selection)]
    if selected:
        sources = ", ".join(job.describe() for job in jobs if set(job.ids).intersection(selected))
        return True, selected, f"it failed a test the diff selected in {sources}"
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
        f"The last one's run: {f'`{run}`' if run else 'not found'}. It counted because {why}.",
        "Tests identified in the available evidence:",
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
              "--jq", ".workflow_runs[] | {id, head_branch, conclusion, created_at, run_attempt}")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _selection(repo: str, run_id: int, removal_at: str) -> list[str] | None:
    """The run's own Python selection; None means ALL, and so does no artifact."""
    raw = _gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts?name=affected-selection",
              "--jq", ".artifacts[] | select(.expired | not) | {id, updated_at}")
    archives = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(_when(a["updated_at"]) > _when(removal_at) for a in archives):
        raise ValueError("the selection artifact changed after this removal")
    if len(archives) != 1:
        return None
    blob = subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{archives[0]['id']}/zip"],
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


def _test_step(job: str, step: str) -> bool:
    """Only a failing test runner can attribute a failure to a test."""
    if job.startswith(("platform-tests (", "platform-sizes (")):
        return step.startswith(("Run npm run test ", "Run npm run test:"))
    if job.startswith("required-tests shard "):
        return step in {"Run this shard of the required test surface", "Run this shard of the selection"}
    return job == AGGREGATE_JOB and step.startswith("Decide the gate over ")


def _vitest_complete(log: str, ids: list[str]) -> bool:
    # A job can stop after printing its first FAIL, or report an error outside any test.
    summary = list(re.finditer(r"^[ \t]*Test Files\s+(\d+) failed[^\n]*\n"
                               r"[ \t]*Tests\s+(\d+) failed[^\n]*$", log, re.M))
    if len(summary) != 1 or any(" > " not in test for test in ids):
        return False
    if re.search(r"Unhandled Errors?|Source Errors?|errors? not related to your test files", log, re.I):
        return False
    files, cases = (int(n) for n in summary[0].groups())
    tail = log[summary[0].end():]
    return (files == len({test.split(" > ", 1)[0] for test in ids}) and cases == len(ids)
            and re.search(r"^[ \t]*Duration\s+[^\n]+[\s\S]*?"
                          r"^##\[error\]Process completed with exit code 1\.$", tail, re.M) is not None)


def _shard_test_summary(log: str, ids: list[str]) -> bool:
    # A shard displays at most 50 new ids and can fail its quarantine check independently.
    counts = set(re.findall(r"^- NEW failures: \*\*(\d+)\*\*$", log, re.M))
    stale = set(re.findall(r"^- stale quarantine entries: \*\*(\d+)\*\*$", log, re.M))
    return counts == {str(len(ids))} and stale == {"0"} and "**Malformed quarantine file:" not in log


def _only_shard_failure(log: str) -> bool:
    # The aggregate checks the shard results AND their manifests. The emitted notice
    # must end before the next runner line. A second bullet, or a cut log, cannot pass.
    heading = "##[notice]### Required tests - SHARD SET INCOMPLETE"
    lines = log.splitlines()
    notices = [i for i, line in enumerate(lines) if _STAMP.sub("", line) == heading]
    if len(notices) != 1:
        return False
    rest = lines[notices[0] + 1:notices[0] + 6]
    return (len(rest) == 5 and rest[:4] == ["", "The gate fails closed:", "",
                                           "- shard jobs concluded 'failure', not 'success'"]
            and _STAMP.match(rest[4]) is not None
            and _STAMP.sub("", rest[4]) == "##[error]Process completed with exit code 1.")


def _aggregate_echo(step: str, failed: set[str]) -> bool:
    """The aggregate may also fail a check of its own after the upstream legs."""
    if step == "Require structural guards on every event":
        return "structural-guards" in failed
    if step == "Require the openspec leg on every event":
        return "openspec" in failed
    if step == "Require the platform's tests, the owner app's browser test and the process leg":
        return bool(failed & {"platform-tests", "platform-checks", "platform-sizes",
                              "platform-app", "process"})
    return False


def _failed_jobs(repo: str, run_id: int) -> list[FailedJob]:
    """Preserve each job's identity even when its test log is unavailable."""
    raw = _gh("api", "--paginate", f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs?per_page=100",
              "--jq", '{total_count, jobs: [.jobs[] | {id, name, status, conclusion, '
              'bad_steps: [.steps[]? | select(.conclusion != "success" and '
              '.conclusion != "skipped") | {name, conclusion}]}]}')
    pages = [json.loads(line) for line in raw.splitlines() if line.strip()]
    jobs = [job for page in pages for job in page["jobs"]]
    failures = []
    seen = {job["id"] for job in jobs}
    if len(seen) != len(jobs) or any(page["total_count"] != len(seen) for page in pages):
        failures.append(FailedJob("incomplete jobs listing", []))
    # Drop an aggregate echo only when its failed step actually depends on a failed leg.
    failed = {job["name"].split(" (")[0] for job in jobs if job["conclusion"] == "failure"}
    failed_shard = any(name.startswith("required-tests shard ") for name in failed)
    for job in jobs:
        name, conclusion = job["name"], job["conclusion"]
        if job["status"] == "completed" and conclusion == "success":
            continue
        # Only the PR matrix and the scheduled heavy leg skip on a merge group.
        if conclusion == "skipped" and (name.startswith("affected-tests ") or name == "heavy-tests"):
            continue
        link = f"https://github.com/{repo}/actions/runs/{run_id}/job/{job['id']}"
        ids = []
        complete = True
        outcome = conclusion or job["status"]
        steps = job["bad_steps"]
        sole_failure = (conclusion == "failure" and job["status"] == "completed"
                        and len(steps) == 1 and steps[0]["conclusion"] == "failure")
        step = steps[0]["name"] if sole_failure else ""
        if name == AGGREGATE_JOB and _aggregate_echo(step, failed):
            continue
        if step:
            outcome = f"failure in {step}"
        if _test_step(name, step):
            try:
                log = _gh("api", f"repos/{repo}/actions/jobs/{job['id']}/logs")
                if name == AGGREGATE_JOB and failed_shard and _only_shard_failure(log):
                    continue
                ids = job_failures(name, log)
                clean = "\n".join(_STAMP.sub("", _ANSI.sub("", line)) for line in log.splitlines())
                if name.startswith(("platform-tests (", "platform-sizes (")):
                    complete = _vitest_complete(clean, ids)
                elif name.startswith("required-tests shard "):
                    complete = _shard_test_summary(clean, ids)
            except (OSError, subprocess.SubprocessError):
                outcome = "log unavailable"
        failures.append(FailedJob(name, ids, link, outcome, complete))
    return failures


def classify_removal(repo: str, number: int,
                     removal_at: str) -> tuple[bool, int | None, list[str], str]:
    """(counts, run, failing tests, why). Any error reading GitHub counts."""
    try:
        queued = queued_at(_timeline(repo, number), removal_at)
        if queued is None:
            return True, None, [], "no enqueue was found before this removal"
        runs = _runs(repo, queued)
        run = removal_run(runs, number, queued, removal_at)
        if run is None:
            return True, None, [], "no failed Tests run of this queue entry was found"
        if next(row for row in runs if row["id"] == run)["run_attempt"] != 1:
            return True, run, [], "a rerun shares this run ID and its selection artifacts"
        jobs = _failed_jobs(repo, run)
        try:
            changed = _changed(repo, number)
            selection = _selection(repo, run, removal_at)
        except Exception as exc:  # partial evidence still belongs in the handoff
            _, ids, why = attempt_counts(jobs, changed=None, selection=None)
            return True, run, ids, f"{why}; diff or selection unavailable ({type(exc).__name__})"
        counts, ids, why = attempt_counts(jobs, changed=changed, selection=selection)
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
