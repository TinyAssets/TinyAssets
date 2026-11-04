#!/usr/bin/env python3
"""The review check and the declared lane.

Every pull request needs a review (2026-10-02): the reviewer posts a PR review on the
commit it read, its first line `APPROVE` or `BLOCK`, and `.github/workflows/review.yml`
turns the latest trusted verdict into the required `review` check on the head
(`review_check`). A verdict carries to a later head only by `carries`: the same change,
or a delta of prose only.

Why every PR: on 2026-10-01 auto-enroll armed #4247 -- a superseded head with a known
data-loss bug and no review -- and the merge queue admitted it. Before 2026-09-26 the
verdict was only a PR comment, and PR #3989 auto-merged at the exact head its reviewer
had BLOCKED. Both are why the verdict is a required check bound to a commit.

The verdict used to be a receipt repeated in the PR body and a comment, and found by
reading the top of each text. That made the body edit the gate's trigger and failed every
PR at open; the review's own `commit_id` and a check run replace it.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# One `git diff --raw` record header with FULL object ids (SHA-1 or SHA-256).
_RAW_META_RE = re.compile(
    rb":[0-7]{6} [0-7]{6} ([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) [A-Z][0-9]*"
)
# `author_association` as GitHub computes it at read time — trusted metadata,
# not something a review body can claim. CONTRIBUTOR and NONE are excluded:
# anyone can review a public repo's PR, and a drive-by review must not count.
_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _raw_change(base: str, head: str, *, cwd: Path | None = None) -> bytes:
    """The CHANGE a PR makes, independent of its commit history.

    `git diff --raw -z` of merge-base(base, head)..head: every path with both modes
    and both FULL blob ids, no rename detection. Blob ids are content hashes, so
    this pins exactly which bytes the PR replaces and with what, binary files
    included:

    * merge main in, or rebase, where main did not touch the PR's files: the merge
      base moves, but every path's before/after blob is unchanged -> the same change;
    * any edit to the PR's own change, or a catch-up where main DID touch a file the
      PR changes (the "before" blob or the merged "after" blob moves) -> that path
      differs: the change being merged is no longer the one reviewed.

    Deliberately not `git patch-id`: it hashes only textual hunk lines, so two
    different binary changes to the same path would share an id. Raises on any git
    failure; `carries` treats that as no carry.
    """
    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=True
        ).stdout

    merge_base = git("merge-base", base, head).strip().decode("ascii")
    # `--no-abbrev` is what makes the ids full: `--full-index` alone still
    # prints abbreviated ids in --raw output, and two different blobs can share
    # an abbreviation (cross-family review 2026-09-27 built such a pair).
    return git(
        "diff",
        "--raw",
        "--no-renames",
        "--full-index",
        "--no-abbrev",
        # A gitlink change must never vanish from the key: without this, a
        # `.gitmodules` entry with `ignore = all` hides submodule edits.
        "--ignore-submodules=none",
        "-z",
        merge_base,
        head,
    )


def change_entries(raw: bytes) -> dict[bytes, bytes]:
    """`git diff --raw -z --no-abbrev` output as {path: record}; refuse anything else."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("unexpected `git diff --raw -z` output")
    entries: dict[bytes, bytes] = {}
    for meta, path in zip(fields[0::2], fields[1::2]):
        if _RAW_META_RE.fullmatch(meta) is None:
            raise ValueError(f"unexpected `git diff --raw` record: {meta!r}")
        if path in entries:
            raise ValueError(f"path listed twice in `git diff --raw`: {path!r}")
        entries[path] = meta
    return entries


# ---- The review check (`.github/workflows/review.yml`) -------------------------
#
# The reviewer posts a GitHub PR review on the commit it read (`commit_id`),
# event COMMENT, whose body's first non-blank line is exactly `APPROVE` or
# `BLOCK`, the evidence below it. The review records natively which commit was
# read, so nothing is repeated in the PR body. A trusted workflow turns the
# latest verdict into the `review` check run on the PR's head:
#
# * no verdict -> in progress, "waiting for review of <head>" (pending, not red);
# * the latest verdict is BLOCK and it carries to the head -> failure;
# * the latest verdict is APPROVE at A and `carries(A, head)` -> success;
# * anything else -> in progress: the change since A needs a delta review.
#
# COMMENT, not APPROVE, because the one writer account cannot approve its own
# PR (the honesty note in pr-scope-guard.yml).

_VERDICTS = frozenset({"APPROVE", "BLOCK"})


@dataclass(frozen=True)
class Verdict:
    verdict: str
    commit: str
    url: str


def latest_verdict(reviews: list[object], *, since: str | None = None) -> Verdict | None:
    """The latest trusted review whose first non-blank line is a verdict.

    `reviews` is `GET /repos/{r}/pulls/{n}/reviews`. A review counts when its
    author's `author_association` (GitHub's, computed at read time) is trusted,
    it is not dismissed or pending, and its body's first non-blank line, with
    surrounding whitespace removed, is exactly `APPROVE` or `BLOCK`. Anything
    else is an ordinary review comment and changes nothing. The latest one by
    `submitted_at` (then id) wins, so a BLOCK after an APPROVE voids it.

    `since` is the time the PR's base last changed (`last_base_change`): a review
    submitted before it read a diff against another base, so it counts for nothing.

    Raises ValueError on a review that is not the API's shape: a list the
    decision cannot read must not shrink into one it can.
    """
    best: tuple[str, int, Verdict] | None = None
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError(f"not a review: {review!r}")
        state = review.get("state")
        if state in {"DISMISSED", "PENDING"}:
            continue
        association = review.get("author_association")
        body = review.get("body") or ""
        commit = review.get("commit_id")
        when = review.get("submitted_at")
        ident = review.get("id")
        if not isinstance(association, str) or not isinstance(body, str):
            raise ValueError(f"review {ident!r} has no author association or body")
        if association not in _TRUSTED_ASSOCIATIONS:
            continue
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if first not in _VERDICTS:
            continue
        if not (isinstance(commit, str) and _SHA_RE.fullmatch(commit)
                and isinstance(when, str) and isinstance(ident, int)):
            raise ValueError(f"review {ident!r} has no commit, time or id")
        if since is not None and when <= since:
            continue
        verdict = Verdict(first, commit, str(review.get("html_url") or ""))
        if best is None or (when, ident) > best[:2]:
            best = (when, ident, verdict)
    return None if best is None else best[2]


def last_base_change(events: list[object]) -> str | None:
    """When the PR's base last changed, from `GET /issues/{n}/timeline`; None if never.

    A retarget (`base_ref_changed`) changes the diff a review read: a PR stacked on a
    branch of unreviewed commits, approved there and retargeted to main, would
    otherwise merge them under that approval. Raises on an event not the API's shape.
    """
    latest: str | None = None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f"not a timeline event: {event!r}")
        if event.get("event") != "base_ref_changed":
            continue
        when = event.get("created_at")
        if not isinstance(when, str):
            raise ValueError("a base change with no time")
        latest = when if latest is None or when > latest else latest
    return latest


# Prose: Markdown under docs/, openspec/ and ideas/, and the top-level Markdown
# files other than the agents' instructions. Nothing runs or loads these.
_PROSE_DIRS = ("docs/", "openspec/", "ideas/")
_AGENT_INSTRUCTIONS = frozenset({"AGENTS.md", "CLAUDE.md", "CODEX.md"})
_PROSE_MODES = frozenset({b"000000", b"100644"})


def is_prose(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    if path.startswith(_PROSE_DIRS):
        return True
    return "/" not in path and path not in _AGENT_INSTRUCTIONS


def _modes(meta: bytes | None) -> set[bytes]:
    return set() if meta is None else set(meta[1:].split(b" ")[:2])


def carries(
    base: str,
    approved: str,
    head: str,
    *,
    cwd: Path | None = None,
) -> str | None:
    """Why a verdict on `approved` stands for `head`, or None when it does not.

    Compares the two PRs' changes path by path: each is
    `git diff --raw --no-renames` of merge-base(base, commit)..commit, so a path
    differs when its before or after blob or mode differs, or it is in one
    change and not the other (a rename counts both its paths). The verdict
    carries when:

    1. no path differs: the same change, so a rebase or a
       merge of main that touched none of the PR's files carries;
    2. every differing path is prose (`is_prose`: Markdown nothing runs or
       loads), a regular non-executable file on both sides. A gate, a workflow
       or any code is never prose, so it always gets a fresh review.

    Any git failure, or a commit that cannot be read, is None: a verdict never
    carries across a change nobody can compare.
    """
    if not (_SHA_RE.fullmatch(approved) and _SHA_RE.fullmatch(head)):
        return None
    if approved == head:
        return f"reviewed at this head ({head})"
    try:
        then = change_entries(_raw_change(base, approved, cwd=cwd))
        now = change_entries(_raw_change(base, head, cwd=cwd))
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None
    differing = sorted(p for p in then.keys() | now.keys() if then.get(p) != now.get(p))
    if not differing:
        return f"the same change as {approved} (every path's blobs and modes unchanged)"
    names = [p.decode("utf-8", "surrogateescape") for p in differing]
    if all(
        is_prose(name) and (_modes(then.get(raw)) | _modes(now.get(raw))) <= _PROSE_MODES
        for name, raw in zip(names, differing)
    ):
        return f"only prose changed since {approved}: {', '.join(names)}"
    return None


@dataclass(frozen=True)
class CheckState:
    """What the `review` check run says: `conclusion` None means in progress."""
    conclusion: str | None
    title: str
    summary: str


def review_check(
    reviews: list[object],
    *,
    base: str,
    head: str,
    carry: Callable[[str], str | None],
    since: str | None = None,
) -> CheckState:
    """The `review` check for `head`, from the PR's reviews and `carry(commit)`.

    Only reviews after `since`, the PR's last base change, count.
    """
    ask = (
        f"A reviewer posts a PR review on the commit it read ({head}), its first "
        "line `APPROVE` or `BLOCK`, the evidence below. Any other delta after a "
        "review gets a one-round delta review of only that delta."
    )
    verdict = latest_verdict(reviews, since=since)
    if verdict is None:
        title = f"waiting for review of {head}"
        if since is not None:
            title += f" (the base changed at {since})"
        return CheckState(None, title, ask)
    why = carry(verdict.commit)
    if verdict.verdict == "BLOCK":
        if why is None:
            return CheckState(
                None,
                f"BLOCK at {verdict.commit}; the change since needs a fresh review",
                f"{verdict.url}\n\n{ask}",
            )
        return CheckState("failure", f"BLOCK: {why}", verdict.url)
    if why is None:
        return CheckState(
            None,
            f"APPROVE at {verdict.commit}; the change since needs a delta review",
            f"{verdict.url}\n\n{ask}",
        )
    return CheckState("success", f"APPROVE: {why}", verdict.url)


_QUEUE_REF_RE = re.compile(
    r"^(?:refs/heads/)?gh-readonly-queue/[^/]+/pr-([1-9][0-9]*)-[0-9a-f]{40}$"
)


def queued_pr(ref: str) -> int | None:
    """The PR a merge-group ref (`gh-readonly-queue/main/pr-N-<sha>`) is for."""
    match = _QUEUE_REF_RE.fullmatch(ref)
    return int(match.group(1)) if match else None


def external_id(number: int) -> str:
    """A PR's `review` check run names its PR: a check run lives on a commit, and two
    PRs can share one head, so a reader takes only the run made for its own PR."""
    return f"pr-{number}"


def review_run_passed(check_runs: object, number: int) -> bool:
    """Does the latest `review` run made for PR `number` read success?

    `check_runs` is the `check_runs` list of
    `GET /commits/{head}/check-runs?check_name=review`, already narrowed to the
    review App. Latest is the highest id, the order GitHub created them in. A run
    made for another PR on the same head, no run, or anything unreadable, is not a
    pass.
    """
    if not isinstance(check_runs, list):
        return False
    runs = [r for r in check_runs if isinstance(r, dict) and isinstance(r.get("id"), int)
            and r.get("name") == "review" and r.get("external_id") == external_id(number)]
    if not runs:
        return False
    latest = max(runs, key=lambda r: r["id"])
    return latest.get("status") == "completed" and latest.get("conclusion") == "success"


def _check_run(name_sha: str, state: CheckState, ident: str) -> dict[str, object]:
    run: dict[str, object] = {
        "name": "review",
        "head_sha": name_sha,
        "external_id": ident,
        "output": {"title": state.title[:250], "summary": state.summary},
    }
    if state.conclusion is None:
        run["status"] = "in_progress"
    else:
        run["status"] = "completed"
        run["conclusion"] = state.conclusion
    return run


def _review_check(argv: list[str]) -> int:
    """`--review-check`: print the `review` check run for a PR's head, as the API's JSON."""
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --review-check")
    parser.add_argument("--reviews-file", type=Path, required=True,
                        help="the PR's reviews, one JSON array")
    parser.add_argument("--timeline-file", type=Path, required=True,
                        help="the PR's timeline events, one JSON array")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    if not _SHA_RE.fullmatch(args.head) or args.pr < 1:
        print(f"not a commit and a PR: {args.head!r} #{args.pr}", file=sys.stderr)
        return 2
    try:
        reviews = json.loads(args.reviews_file.read_text(encoding="utf-8"))
        events = json.loads(args.timeline_file.read_text(encoding="utf-8"))
        if not isinstance(reviews, list) or not isinstance(events, list):
            raise ValueError("the reviews or the timeline is not a JSON array")
        state = review_check(
            reviews, base=args.base, head=args.head, since=last_base_change(events),
            carry=lambda commit: carries(args.base, commit, args.head),
        )
    except (OSError, ValueError) as exc:
        print(f"cannot decide the review check: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_check_run(args.head, state, external_id(args.pr))))
    return 0


def signal_pr(text: str, pull: object, *, run_repo: str, run_branch: str) -> int:
    """The PR a review signal names, once GitHub's data says the signal is that PR's.

    The signal comes from the PR's own copy of review-signal.yml, so it is untrusted:
    the PR it names must be the one whose branch the signal run ran on (the
    `workflow_run`'s head repository and branch, which GitHub sets). Otherwise one PR's
    copy could make another PR decide. Raises when they differ.
    """
    number = text.strip()
    if not number.isdigit() or not 0 < int(number) < 10**9:
        raise ValueError("the signal names no PR")
    head = (pull or {}).get("head") if isinstance(pull, dict) else None
    repo = ((head or {}).get("repo") or {}).get("full_name")
    if not head or repo != run_repo or head.get("ref") != run_branch:
        raise ValueError(f"PR #{number}'s branch is not the signal's ({run_repo}:{run_branch})")
    return int(number)


def _signal_pr(argv: list[str]) -> int:
    """`--signal-pr`: print the PR a review signal names, verified (`signal_pr`)."""
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --signal-pr")
    parser.add_argument("--signal-file", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-repo", required=True)
    parser.add_argument("--run-branch", required=True)
    args = parser.parse_args(argv)
    try:
        text = args.signal_file.read_text(encoding="utf-8")[:16]
        number = text.strip()
        pull = _gh_json(f"repos/{args.repo}/pulls/{number}") if number.isdigit() else None
        print(signal_pr(text, pull, run_repo=args.run_repo, run_branch=args.run_branch))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"refusing the signal: {exc}", file=sys.stderr)
        return 2
    return 0


# GitHub Actions: the app every Actions job's check run reports as. A job named
# `review` in any workflow reports as it too, so the review check is not this app's.
_ACTIONS_APP_ID = 15368


def review_app_id(env: dict[str, str] | None = None) -> int:
    """The review App's id (`REVIEW_APP_ID`, the repository variable).

    The `review` check is created only by that App, whose key lives in the main-only
    `review` environment, and the ruleset pins the context to it, so no PR's workflow
    can satisfy it. Missing or malformed fails closed.
    """
    value = (os.environ if env is None else env).get("REVIEW_APP_ID", "")
    if not value.isdigit() or int(value) == _ACTIONS_APP_ID:
        raise ValueError("REVIEW_APP_ID is not the review App's id")
    return int(value)


def _gh_json(path: str) -> object:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True,
                         timeout=60)
    return json.loads(out.stdout)


def review_passed_at(repo: str, head: str, number: int, *, app_id: int | None = None) -> bool:
    """Does `head`'s latest `review` run, by the review App, for PR `number`, read success?

    The one reader of the decision for everything that acts on it (the merge group,
    enqueue, the queue's freshness probe, the floor hotfix). Raises on a read that
    fails: a caller that cannot read the check treats the head as unreviewed.
    """
    if not _SHA_RE.fullmatch(head):
        raise ValueError(f"not a commit: {head!r}")
    app = review_app_id() if app_id is None else app_id
    runs = _gh_json(f"repos/{repo}/commits/{head}/check-runs?check_name=review&per_page=100")
    listed = runs.get("check_runs") if isinstance(runs, dict) else None
    if not isinstance(listed, list):
        raise ValueError(f"no check runs listed for {head}")
    return review_run_passed([r for r in listed if isinstance(r, dict)
                              and (r.get("app") or {}).get("id") == app], number)


def _queue_review(argv: list[str]) -> int:
    """`--queue-review`: print the merge group's `review` check run, as the API's JSON.

    The group's ref names its PR; the PR's head and its own `review` runs are read
    with `gh api` (GitHub's data, never the group's code). A queued PR whose review is
    missing, pending, failed or made for another PR fails the group. An unreadable
    one exits 2, and the workflow posts a failure.
    """
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --queue-review")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True, help="the merge group's branch")
    parser.add_argument("--sha", required=True, help="the merge group's commit")
    args = parser.parse_args(argv)
    number = queued_pr(args.ref)
    if number is None or not _SHA_RE.fullmatch(args.sha):
        print(f"not a merge-queue ref and commit: {args.ref!r} {args.sha!r}", file=sys.stderr)
        return 2
    try:
        pull = _gh_json(f"repos/{args.repo}/pulls/{number}")
        head = pull["head"]["sha"] if isinstance(pull, dict) else None
        if not isinstance(head, str):
            raise ValueError(f"PR #{number} has no head")
        passed = review_passed_at(args.repo, head, number)
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        print(f"PR #{number}: cannot read its review check: {exc}", file=sys.stderr)
        return 2
    state = (CheckState("success", f"PR #{number}'s review passed at {head}", "")
             if passed else
             CheckState("failure", f"PR #{number}'s review at {head} is missing, pending, "
                        "failed or another PR's", ""))
    print(json.dumps(_check_run(args.sha, state, f"queue-{external_id(number)}")))
    return 0


def _read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# A lane's declared files. A lane branched from another lane's WIP commit and
# carried a 689-line deploy refactor through five reviews (2026-10-21), because
# every reviewer read the lane's own commits, not its diff against main. Every PR
# says which files it owns and is held to it: a file outside the lane is a
# smuggled lineage or a second intent, and either way it is refused before review
# starts.
_LANE_RE = re.compile(r"^Lane:[ \t]*(\S.*)$", re.MULTILINE)


def declared_lane(body: str) -> list[str] | None:
    """The body's `Lane:` globs (`dir/` is a prefix), or None when undeclared."""
    match = _LANE_RE.search(body)
    if not match:
        return None
    globs = [g.strip("`") for g in re.split(r"[,\s]+", match.group(1))]
    return [g for g in globs if g] or None


def _in_glob(path: str, glob: str) -> bool:
    return path.startswith(glob) if glob.endswith("/") else fnmatch.fnmatchcase(path, glob)


def outside_lane(paths: list[str], globs: list[str]) -> list[str]:
    return [p for p in paths if p and not any(_in_glob(p, g) for g in globs)]


# Paths no honest lane holds all of: every depth, a dotfile, a deploy script, a workflow,
# product code and platform code. A glob matching each of them (`*`, `**`, `?*`, `[!x]*`
# and the like; fnmatch's `*` crosses `/`) declares no lane at all.
_EVERY_PATH = ("a", ".a", "A/b.c", "deploy/x.sh", ".github/workflows/deploy-prod.yml",
               "tinyassets/a/b/c.py", "platform/owner/src/x.ts", "z/y/x/w/v")


def universal_globs(globs: list[str]) -> list[str]:
    """The globs of a lane that match every path: each one declares nothing."""
    return [g for g in globs if all(_in_glob(path, g) for path in _EVERY_PATH)]


def _lane_scope(argv: list[str]) -> int:
    """`--lane-scope --body-file F [--require]`, changed paths on stdin."""
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --lane-scope")
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--require", action="store_true",
                        help="refuse a body that declares no lane (the scope guard always does)")
    args = parser.parse_args(argv)
    globs = declared_lane(_read_text(args.body_file) or "")
    if globs is None:
        if args.require:
            print("every PR declares its files as `Lane: <glob> <dir/> ...` in the body "
                  "(a prose-only PR: `Lane: docs/`)", file=sys.stderr)
            return 2
        return 0
    everything = universal_globs(globs)
    if everything:
        print("a Lane names the files a PR owns; these match every path, so they name none: "
              + " ".join(everything), file=sys.stderr)
        return 2
    stray = outside_lane(sys.stdin.read().splitlines(), globs)
    if stray:
        print("files outside the declared Lane (branch from origin/main, or "
              "widen the Lane):", file=sys.stderr)
        for path in stray:
            print(f"  {path}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--review-check"]:
        return _review_check(sys.argv[2:])
    if sys.argv[1:2] == ["--queue-review"]:
        return _queue_review(sys.argv[2:])
    if sys.argv[1:2] == ["--signal-pr"]:
        return _signal_pr(sys.argv[2:])
    if sys.argv[1:2] == ["--lane-scope"]:
        return _lane_scope(sys.argv[2:])
    print("usage: drain_review_gate.py --review-check | --queue-review | --signal-pr"
          " | --lane-scope ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
