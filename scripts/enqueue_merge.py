#!/usr/bin/env python3
"""Put a reviewed, green pull request into the merge queue.

``gh pr merge --auto`` calls ``enablePullRequestAutoMerge``. GitHub adds that
PR to the merge queue only when the actor is a person. A GitHub App or
``GITHUB_TOKEN`` sets the flag and the queue never admits the PR. Some apps
are GraphQL type ``User`` (``cursor[bot]``), so a typename check cannot tell
them from a person, and enabling auto-merge does not emit
``pull_request_target``, so nothing comes back to replace the enrollment.

This calls ``enqueuePullRequest`` with the merge-attribution token once every
required check has passed at the head, the ``review`` check among them (a PR
review that approves this change, ``review.yml``). No review, no enqueue.

A token that cannot call the mutation (missing ``Merge queues: Read and
write``) logs that sentence and returns success. The job stays green so the
missing permission is not mistaken for a bad pull request.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_CHECKS = (
    "Diff scope declared",
    "required-tests",
    "invariants",
    "review",
)
# Every required check runs on the pull request; a skip means it did not run.

_PR_QUERY = """query($owner:String!,$name:String!,$number:Int!){
repository(owner:$owner,name:$name){pullRequest(number:$number){
id state isDraft baseRefName baseRefOid headRefOid
headRepository { nameWithOwner }
mergeQueueEntry { state }
commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){nodes{
__typename
... on CheckRun { name conclusion startedAt completedAt }
... on StatusContext { context state createdAt }
}}}}}}
}}}"""

_ENQUEUE = """mutation($id:ID!,$oid:GitObjectID!){
enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$oid}){
mergeQueueEntry { state }
}}"""

PERMISSION_LOG = (
    'MERGE_ATTRIBUTION_TOKEN lacks permission "Merge queues: Read and write". '
    "Not enqueued. Add that repository permission on the fine-grained PAT "
    "(resource owner TinyAssets). Leave Contents and Pull requests at "
    "Read and write. Do not replace the secret with GITHUB_TOKEN."
)


def latest_conclusions(nodes: list[dict] | None) -> dict[str, str]:
    """Newest conclusion per required check. Older failures do not count."""
    best: dict[str, tuple[str, str]] = {}
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        name = node.get("name") or node.get("context")
        if name not in REQUIRED_CHECKS:
            continue
        # A run still in progress has no completedAt: its start orders it, so a pending
        # decision posted after a pass is the newest, and is not green.
        when = node.get("completedAt") or node.get("startedAt") or node.get("createdAt") or ""
        conclusion = (node.get("conclusion") or node.get("state") or "").upper()
        previous = best.get(name)
        if previous is None or str(when) >= previous[0]:
            best[name] = (str(when), conclusion)
    return {name: conclusion for name, (_, conclusion) in best.items()}


def checks_green(conclusions: dict[str, str]) -> bool:
    for name in REQUIRED_CHECKS:
        if conclusions.get(name) != "SUCCESS":
            return False
    return True


def skip_reason(
    *,
    state: str,
    draft: bool,
    base: str,
    same_repo: bool,
    in_queue: bool,
    capped: bool,
    green: bool,
    head_matches: bool,
) -> str | None:
    """None means enqueue. Anything else is a quiet skip."""
    if state != "OPEN":
        return f"state is {state or 'unknown'}"
    if not head_matches:
        return "head moved"
    if draft:
        return "draft"
    if base != "main":
        return "base is not main"
    if not same_repo:
        return "fork"
    if in_queue:
        return "already queued"
    if capped:
        return "queue-attempt cap"
    if not green:
        return "required checks are not green"
    return None


def lacks_merge_queue_permission(detail: str) -> bool:
    """True when enqueuePullRequest was refused for the token's permissions."""
    text = detail.lower()
    if "resource not accessible" in text:
        return True
    if "enqueuepullrequest" in text and "forbidden" in text:
        return True
    if "merge queue" in text and any(
        word in text for word in ("permission", "scope", "accessible")
    ):
        return True
    return "requires" in text and "permission" in text


def _gh(args: list[str], *, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _graphql(query: str, variables: dict[str, str]) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        flag = "-F" if isinstance(value, int) else "-f"
        args.extend([flag, f"{key}={value}"])
    proc = _gh(args)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if proc.returncode != 0 or payload.get("errors"):
        detail = payload.get("errors") or (proc.stderr or proc.stdout).strip()
        raise RuntimeError(str(detail))
    return payload


def _capped(repo: str, number: int) -> bool:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/queue_attempts.py",
            "check",
            "--repo",
            repo,
            "--pr",
            str(number),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    # Exit 3 is the cap. Any other failure must not block a merge: the cap
    # stops a retry loop, the same rule auto-enroll already follows.
    if proc.returncode not in (0, 3):
        print(
            f"::warning::queue-attempt cap check failed (exit {proc.returncode}); "
            "enqueueing without it.",
            file=sys.stderr,
        )
        return False
    return proc.returncode == 3


def _benign_enqueue_error(detail: str) -> bool:
    text = detail.lower()
    return any(
        phrase in text
        for phrase in (
            "already in the merge queue",
            "already queued",
            "pull request is in the merge queue",
        )
    )


def enqueue(repo: str, number: int, *, expect_head: str = "") -> int:
    owner, name = repo.split("/", 1)
    try:
        payload = _graphql(
            _PR_QUERY,
            {"owner": owner, "name": name, "number": number},
        )
    except RuntimeError as exc:
        print(f"::error::could not read PR #{number}: {exc}", file=sys.stderr)
        return 1
    pr = payload["data"]["repository"]["pullRequest"]
    if pr is None:
        print(f"PR #{number} does not exist.")
        return 0
    head = pr["headRefOid"] or ""
    head_repo = (pr.get("headRepository") or {}).get("nameWithOwner") or ""
    contexts = []
    commits = (pr.get("commits") or {}).get("nodes") or []
    if commits:
        rollup = (commits[0].get("commit") or {}).get("statusCheckRollup") or {}
        contexts = ((rollup.get("contexts") or {}).get("nodes")) or []
    conclusions = latest_conclusions(contexts)
    reason = skip_reason(
        state=pr.get("state") or "",
        draft=bool(pr.get("isDraft")),
        base=pr.get("baseRefName") or "",
        same_repo=head_repo == repo,
        in_queue=pr.get("mergeQueueEntry") is not None,
        capped=False,
        green=checks_green(conclusions),
        head_matches=not expect_head or expect_head == head,
    )
    if reason is None and _capped(repo, number):
        reason = "queue-attempt cap"
    if reason is not None:
        missing = [
            name for name in REQUIRED_CHECKS
            if conclusions.get(name) != "SUCCESS"
        ]
        extra = f" (not green: {', '.join(missing)})" if reason.startswith("required") else ""
        print(f"PR #{number}: not enqueued ({reason}){extra}.")
        return 0
    try:
        _graphql(_ENQUEUE, {"id": pr["id"], "oid": head})
    except RuntimeError as exc:
        detail = str(exc)
        if _benign_enqueue_error(detail):
            print(f"PR #{number}: already queued.")
            return 0
        if lacks_merge_queue_permission(detail):
            print(f"PR #{number}: {PERMISSION_LOG}")
            return 0
        print(f"::error::enqueuePullRequest failed for PR #{number}: {detail}", file=sys.stderr)
        return 1
    print(f"PR #{number} enqueued at {head[:12]}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, action="append", required=True, dest="prs")
    parser.add_argument(
        "--head",
        default="",
        help="Only enqueue when the PR head is still this SHA.",
    )
    args = parser.parse_args(argv)
    if args.repo.count("/") != 1:
        parser.error("--repo must be OWNER/REPO")
    code = 0
    for number in args.prs:
        code = max(code, enqueue(args.repo, number, expect_head=args.head))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
