#!/usr/bin/env python3
"""Put a reviewed, green pull request into the merge queue.

``gh pr merge --auto`` calls ``enablePullRequestAutoMerge``. GitHub adds that
PR to the merge queue only when the actor is a person. A GitHub App or
``GITHUB_TOKEN`` sets the flag and the queue never admits the PR. Some apps
are GraphQL type ``User`` (``cursor[bot]``), so a typename check cannot tell
them from a person, and enabling auto-merge does not emit
``pull_request_target``, so nothing comes back to replace the enrollment.

This calls ``enqueuePullRequest`` with the merge-attribution token after the
same receipt ``pr-scope-guard.yml`` requires: a comment by Jonnyton that opens
with ``Drain-Review-Verdict: APPROVE`` and ``Drain-Review-Head`` for this
change, cited at the top of the body. Release-critical diffs also need the
``infra-change`` label, using that workflow's path list and cap. No receipt,
no label, no enqueue.

A token that cannot call the mutation (missing ``Merge queues: Read and
write``) logs that sentence and returns success. The job stays green so the
missing permission is not mistaken for a bad pull request.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.drain_review_gate import (  # noqa: E402
    _TRUSTED_ASSOCIATIONS,
    binding_head,
    diff_key,
    published_approval_urls,
    review_allows_merge,
    same_change_as,
)

REQUIRED_CHECKS = (
    "Diff scope declared",
    "required-tests",
    "invariants",
    "slow-tests",
)
# slow-tests is skipped on pull_request and re-run by the queue. The other
# three run on the pull request; a skip there means the gate did not run.
_SKIPPABLE = frozenset({"slow-tests"})
_PASSING = frozenset({"SUCCESS", "SKIPPED"})
REQUIRED_LABEL = "infra-change"
TRUSTED_LOGIN = "jonnyton"
_SCOPE_GUARD = REPO_ROOT / ".github" / "workflows" / "pr-scope-guard.yml"

_PR_QUERY = """query($owner:String!,$name:String!,$number:Int!){
repository(owner:$owner,name:$name){pullRequest(number:$number){
id state isDraft baseRefName baseRefOid headRefOid
headRepository { nameWithOwner }
body
labels(first:100){pageInfo{hasNextPage} nodes{name}}
mergeQueueEntry { state }
commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){nodes{
__typename
... on CheckRun { name conclusion completedAt }
... on StatusContext { context state createdAt }
}}}}}}
}}}"""

_ENQUEUE = """mutation($id:ID!,$oid:GitObjectID!){
enqueuePullRequest(input:{pullRequestId:$id,expectedHeadOid:$oid}){
mergeQueueEntry { state }
}}"""

_COMMENT_JQ = (
    '.[] | {url: .html_url, association: .author_association, '
    'login: (.user.login // ""), body: (.body // "")}'
)

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
        when = node.get("completedAt") or node.get("createdAt") or ""
        conclusion = (node.get("conclusion") or node.get("state") or "").upper()
        previous = best.get(name)
        if previous is None or str(when) >= previous[0]:
            best[name] = (str(when), conclusion)
    return {name: conclusion for name, (_, conclusion) in best.items()}


def checks_green(conclusions: dict[str, str]) -> bool:
    for name in REQUIRED_CHECKS:
        allowed = _PASSING if name in _SKIPPABLE else frozenset({"SUCCESS"})
        if conclusions.get(name) not in allowed:
            return False
    return True


def load_scope_policy(text: str) -> tuple[re.Pattern[str], int]:
    """The scope guard's release-critical pattern and hard cap.

    Read from the workflow text so enqueue cannot grow a second list.
    """
    sensitive = re.search(r"^\s*SENSITIVE_RE='([^']+)'", text, re.M)
    cap = re.search(r"^\s*MAX_SENSITIVE=(\d+)", text, re.M)
    if sensitive is None or cap is None:
        raise RuntimeError(
            "pr-scope-guard.yml has no SENSITIVE_RE or MAX_SENSITIVE"
        )
    return re.compile(sensitive.group(1)), int(cap.group(1))


def label_block(
    paths: list[str],
    labels: set[str],
    *,
    pattern: re.Pattern[str],
    cap: int,
) -> str | None:
    """None when the label rule is satisfied."""
    hits = [path for path in paths if pattern.search(path)]
    if not hits:
        return None
    if len(hits) > cap:
        return f"release-critical cap ({len(hits)} > {cap})"
    if REQUIRED_LABEL not in labels:
        return f"missing {REQUIRED_LABEL} label"
    return None


def skip_reason(
    *,
    state: str,
    draft: bool,
    base: str,
    same_repo: bool,
    in_queue: bool,
    capped: bool,
    receipt: bool,
    green: bool,
    head_matches: bool,
    label_problem: str | None = None,
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
    if label_problem:
        return label_problem
    if not receipt:
        return "no review receipt for this head"
    return None


def jonnyton_inventory(stream: str) -> str | None:
    """Comments by Jonnyton whose association the review gate trusts.

    ``None`` means the inventory could not be read. An empty string means it
    was read and contains no such comment. Other authors are dropped so a
    body cannot cite them.
    """
    decoder = json.JSONDecoder()
    kept: list[str] = []
    index = 0
    length = len(stream)
    while index < length:
        while index < length and stream[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            obj, end = decoder.raw_decode(stream, index)
        except ValueError:
            return None
        index = end
        if not isinstance(obj, dict):
            return None
        login = obj.get("login")
        if not isinstance(login, str) or login.lower() != TRUSTED_LOGIN:
            continue
        association = obj.get("association")
        url = obj.get("url")
        body = obj.get("body")
        if association not in _TRUSTED_ASSOCIATIONS:
            continue
        if not isinstance(url, str) or not isinstance(body, str):
            return None
        kept.append(json.dumps(
            {"url": url, "association": association, "body": body},
            separators=(",", ":"),
        ))
    return "\n".join(kept)


def trusted_receipt(
    *,
    head: str,
    body: str,
    comments: str,
    repo: str,
    number: int,
    diff_key_value: str | None = None,
    same_change=None,
) -> bool:
    """True when the body cites a Jonnyton comment that approves this change."""
    inventory = jonnyton_inventory(comments)
    if inventory is None:
        return False
    trusted = published_approval_urls(
        inventory,
        head=head,
        diff_key=diff_key_value,
        same_change=same_change,
    )
    if trusted is None:
        return False
    return review_allows_merge(
        head=head,
        body=body,
        artifact_must_be_comment_on=(repo, number),
        trusted_comment_urls=trusted,
        diff_key=diff_key_value,
        same_change=same_change,
    )


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


def _have_commit(sha: str) -> bool:
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode == 0


def _fetch_commits(shas: list[str]) -> None:
    missing = [sha for sha in shas if sha and not _have_commit(sha)]
    if not missing:
        return
    subprocess.run(
        ["git", "fetch", "--no-tags", "--filter=blob:none", "origin", *missing],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )


def _cited_heads(body: str, comments: str, *, head: str) -> list[str]:
    found: list[str] = []

    def add(text: str) -> None:
        cited = binding_head(text)
        if cited and cited != head and cited not in found:
            found.append(cited)

    add(body)
    decoder = json.JSONDecoder()
    index = 0
    length = len(comments)
    while index < length:
        while index < length and comments[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            obj, end = decoder.raw_decode(comments, index)
        except ValueError:
            break
        index = end
        if isinstance(obj, dict) and isinstance(obj.get("body"), str):
            add(obj["body"])
    return found


def _changed_paths(repo: str, number: int) -> list[str] | None:
    proc = _gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{number}/files",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
        ],
        timeout=90,
    )
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line]


def _comments(repo: str, number: int) -> str | None:
    parts: list[str] = []
    endpoints = (
        f"repos/{repo}/issues/{number}/comments",
        f"repos/{repo}/pulls/{number}/comments",
        f"repos/{repo}/pulls/{number}/reviews",
    )
    for endpoint in endpoints:
        proc = _gh(
            ["api", "--paginate", endpoint, "--jq", _COMMENT_JQ],
            timeout=90,
        )
        if proc.returncode != 0:
            return None
        parts.append(proc.stdout)
    return "".join(parts)


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


def _change_key(base: str, head: str) -> tuple[str | None, object]:
    """Diff key and the same-change comparer, or (None, None) when unreadable."""
    _fetch_commits([base, head])
    try:
        key = diff_key(base, head, cwd=REPO_ROOT)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None, None
    return key, same_change_as(base, key, cwd=REPO_ROOT)


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


def _labels(pr: dict) -> set[str] | None:
    block = pr.get("labels") or {}
    page = block.get("pageInfo") or {}
    if page.get("hasNextPage"):
        return None
    names = set()
    for node in block.get("nodes") or []:
        if isinstance(node, dict) and isinstance(node.get("name"), str):
            names.add(node["name"])
    return names


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
    base_sha = pr.get("baseRefOid") or ""
    head_repo = (pr.get("headRepository") or {}).get("nameWithOwner") or ""
    contexts = []
    commits = (pr.get("commits") or {}).get("nodes") or []
    if commits:
        rollup = (commits[0].get("commit") or {}).get("statusCheckRollup") or {}
        contexts = ((rollup.get("contexts") or {}).get("nodes")) or []
    conclusions = latest_conclusions(contexts)
    labels = _labels(pr)
    reason = skip_reason(
        state=pr.get("state") or "",
        draft=bool(pr.get("isDraft")),
        base=pr.get("baseRefName") or "",
        same_repo=head_repo == repo,
        in_queue=pr.get("mergeQueueEntry") is not None,
        capped=False,
        receipt=True,
        green=checks_green(conclusions),
        head_matches=not expect_head or expect_head == head,
        label_problem=None,
    )
    if reason is None and labels is None:
        reason = "could not read every label"
    if reason is None and _capped(repo, number):
        reason = "queue-attempt cap"
    if reason is None:
        try:
            pattern, cap = load_scope_policy(_SCOPE_GUARD.read_text(encoding="utf-8"))
        except (OSError, RuntimeError) as exc:
            print(f"::error::could not read the scope policy: {exc}", file=sys.stderr)
            return 1
        paths = _changed_paths(repo, number)
        if paths is None:
            reason = "could not read changed files"
        else:
            reason = label_block(paths, labels or set(), pattern=pattern, cap=cap)
    if reason is None:
        comments = _comments(repo, number)
        if comments is None:
            reason = "could not read review comments"
        else:
            key, carry = _change_key(base_sha, head)
            _fetch_commits(_cited_heads(pr.get("body") or "", comments, head=head))
            if not trusted_receipt(
                head=head,
                body=pr.get("body") or "",
                comments=comments,
                repo=repo,
                number=number,
                diff_key_value=key,
                same_change=carry,
            ):
                reason = "no review receipt for this head"
    if reason is not None:
        missing = [
            name for name in REQUIRED_CHECKS
            if conclusions.get(name) not in (
                _PASSING if name in _SKIPPABLE else frozenset({"SUCCESS"})
            )
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
