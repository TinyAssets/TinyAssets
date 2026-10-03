#!/usr/bin/env python3
"""Re-run a REQUIRED check whose latest run on an armed PR was cancelled.

On 2026-10-01 the fd-flake fix (#4204) was stamped and armed but never
entered the merge queue: its newest "PR scope guard" run had been cancelled
as superseded, so the required "Diff scope declared" context read as missing
even though an earlier run had passed. Nothing re-ran it until a person
noticed. This finds that state and re-runs the cancelled run.

Per armed (auto-merge enabled), non-draft open PR, per required check name on
its head commit: if the NEWEST run of that check (highest workflow-run id) is
CANCELLED, and that run was raised by the PR itself (pull_request or
pull_request_target, on the current head SHA), the whole run is re-run once
per tick. Immediately before re-running, the PR is read again: if its head
moved or the check is no longer cancelled-newest, nothing happens (a stale
re-run would cancel the new head's run through per-PR concurrency). A run on
its third attempt is left alone; at most MAX_PER_TICK runs are re-run per
invocation; a PR with more check contexts than one page is skipped rather
than judged on a partial view.

    rerun_cancelled_required.py --repo OWNER/NAME [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

MAX_ATTEMPT = 3
MAX_PER_TICK = 5
GH_TIMEOUT = 60
PR_EVENTS = frozenset({"pull_request", "pull_request_target"})

_PRS = """query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){
pullRequests(states:OPEN,first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{id number isDraft autoMergeRequest{enabledAt}}}}}"""
_CHECKS = """query($id:ID!){node(id:$id){... on PullRequest{headRefOid isDraft
autoMergeRequest{enabledAt} commits(last:1){nodes{commit{oid
statusCheckRollup{contexts(first:100){pageInfo{hasNextPage} nodes{... on CheckRun{name
conclusion isRequired(pullRequestId:$id) checkSuite{workflowRun{databaseId}}}}}}}}}}}}"""


def cancelled_required_runs(contexts: list[dict]) -> list[tuple[str, int]]:
    """(check name, workflow run id) for required checks whose newest run was cancelled."""
    newest: dict[str, dict] = {}
    for ctx in contexts:
        run = ((ctx.get("checkSuite") or {}).get("workflowRun") or {}).get("databaseId")
        if not ctx.get("name") or not ctx.get("isRequired") or run is None:
            continue
        if ctx["name"] not in newest or run > newest[ctx["name"]]["run"]:
            newest[ctx["name"]] = {"run": run, "conclusion": ctx.get("conclusion")}
    return sorted((name, v["run"]) for name, v in newest.items()
                  if v["conclusion"] == "CANCELLED")


def eligible(run: dict, head: str) -> str | None:
    """Why a run must NOT be re-run, or None when it may be."""
    if run.get("event") not in PR_EVENTS:
        return f"raised by {run.get('event')!r}, not by the PR"
    if run.get("head_sha") != head:
        return "for an older head"
    if int(run.get("run_attempt") or 1) >= MAX_ATTEMPT:
        return f"already on attempt {run.get('run_attempt')}"
    return None


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True, timeout=GH_TIMEOUT).stdout


def _armed_prs(owner: str, name: str) -> list[dict]:
    out: list[dict] = []
    after = None
    while True:
        args = ["api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}",
                "-f", f"query={_PRS}"]
        if after:
            args += ["-F", f"after={after}"]
        page = json.loads(_gh(*args))["data"]["repository"]["pullRequests"]
        out += [p for p in page["nodes"] if not p["isDraft"] and p["autoMergeRequest"]]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def _state(pr_id: str) -> tuple[str, list[tuple[str, int]] | None, bool]:
    """(head sha, cancelled required runs or None if unreadable, still armed)."""
    node = json.loads(_gh("api", "graphql", "-F", f"id={pr_id}", "-f",
                          f"query={_CHECKS}"))["data"]["node"]
    armed = bool(node["autoMergeRequest"]) and not node["isDraft"]
    commits = node["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    if not rollup:
        return node["headRefOid"], [], armed
    if rollup["contexts"]["pageInfo"]["hasNextPage"]:
        return node["headRefOid"], None, armed
    return node["headRefOid"], cancelled_required_runs(rollup["contexts"]["nodes"]), armed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    owner, name = args.repo.split("/", 1)
    done: set[int] = set()
    for pr in _armed_prs(owner, name):
        head, cancelled, _ = _state(pr["id"])
        if cancelled is None:
            print(f"#{pr['number']}: more than one page of checks; not judging a partial view.")
            continue
        for check, run_id in cancelled:
            if run_id in done or len(done) >= MAX_PER_TICK:
                continue
            run = json.loads(_gh("api", f"repos/{args.repo}/actions/runs/{run_id}"))
            reason = eligible(run, head)
            if reason:
                print(f"#{pr['number']} {check}: run {run_id} is {reason}; leaving it.")
                continue
            # Re-read right before acting: a push in between must not get a
            # stale re-run that cancels its own new run.
            now_head, now_cancelled, armed = _state(pr["id"])
            if not armed or now_head != head or (check, run_id) not in (now_cancelled or []):
                print(f"#{pr['number']} {check}: changed while checking; leaving it.")
                continue
            print(f"#{pr['number']} {check}: newest run {run_id} was cancelled; re-running.")
            done.add(run_id)
            if not args.dry_run:
                try:
                    # The whole run: a run cancelled before any job started has
                    # no failed job for `--failed` to pick up.
                    _gh("run", "rerun", str(run_id), "-R", args.repo)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    print(f"  re-run refused: {getattr(exc, 'stderr', exc)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
