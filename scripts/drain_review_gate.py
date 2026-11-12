#!/usr/bin/env python3
"""Decide whether a pull request carries a current review receipt.

EVERY pull request needs one (2026-10-02). Two callers, one receipt format:

* `auto-enroll-merge.yml` arms auto-merge only for a head whose body carries a
  receipt (a pre-filter, so unreviewed heads never reach the queue);
* `pr-scope-guard.yml` -- the REQUIRED check -- asks, via `--blocking-review`,
  for the full receipt: the body's `Drain-Review-Verdict: APPROVE` lines, bound
  to this head, to an earlier head of the same tree-diff, or to the diff key,
  citing a comment on THIS PR by a trusted author that itself opens with the
  same approval.

Why every PR: until 2026-10-02 only drain/ branches and a narrow set of
gate-defining and authority paths needed a receipt, and on 2026-10-01
auto-enroll armed #4247 -- a superseded head with a known data-loss bug, no
receipt, and no path in that set -- and the merge queue admitted it. Before
2026-09-26 the verdict was only a PR comment, and PR #3989 auto-merged at the
exact head its reviewer had BLOCKED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIFF_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
# One `git diff --raw` record header with FULL object ids (SHA-1 or SHA-256).
_RAW_META_RE = re.compile(
    rb":[0-7]{6} [0-7]{6} ([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) [A-Z][0-9]*"
)
_ARTIFACT_RE = re.compile(
    r"Drain-Review-Artifact: "
    r"(docs/[A-Za-z0-9_./-]+\.md|https://github\.com/\S+)"
)

# A comment ON THIS PR, by URL. The three anchors are the three places a review
# verdict can live on a pull request: a top-level issue comment, a submitted
# review, and an inline review comment. Anything else — a docs path, a link to
# another PR, another repository, a bare commit URL — is not the durable,
# timestamped artifact this receipt is supposed to point at.
_COMMENT_ARTIFACT_RE = re.compile(
    r"Drain-Review-Artifact: (?P<url>https://github\.com/"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(?P<pr>[1-9][0-9]*)"
    r"#(?:issuecomment-[0-9]+|pullrequestreview-[0-9]+|discussion_r[0-9]+))"
)

# `author_association` as GitHub computes it at read time — trusted metadata,
# not something a comment body can claim. CONTRIBUTOR and NONE are excluded:
# anyone can comment on a public repo's PR, and the receipt must not be
# satisfiable by a drive-by comment.
_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def review_allows_merge(
    *,
    head: str,
    body: str,
    artifact_must_be_comment_on: tuple[str, int] | None = None,
    trusted_comment_urls: frozenset[str] | None = None,
    diff_key: str | None = None,
    same_change: Callable[[str], bool] | None = None,
) -> bool:
    """True when the body carries a receipt for this head (or its diff key).

    With `artifact_must_be_comment_on=(repo, pr)` the receipt's artifact must
    additionally name a comment on THAT pull request, present in
    `trusted_comment_urls`. A missing inventory (the API read failed) denies:
    a receipt we cannot corroborate is not a receipt.
    """
    if not _SHA_RE.fullmatch(head):
        return False

    lines = leading_lines(body, 3)
    if not (_attests_approval(lines, head, diff_key, same_change=same_change) and len(lines) == 3):
        return False
    artifact = lines[2]
    if _ARTIFACT_RE.fullmatch(artifact) is None:
        return False
    if artifact_must_be_comment_on is None:
        return True
    repo, pr = artifact_must_be_comment_on
    return artifact_names_trusted_comment(
        artifact, repo=repo, pr=pr, trusted_comment_urls=trusted_comment_urls
    )


def leading_lines(text: str, count: int) -> list[str]:
    """The first `count` non-blank lines, trailing whitespace removed.

    **A receipt is only read at the TOP of the text**, and that is the whole
    anti-hiding rule. Nothing can precede the first line of a document, so no
    construct can be open when it is read: an HTML comment, a fence, a
    `<details>`, a blockquote or a list all have to START somewhere, and if one
    does, the first non-blank line is its opener and not the verdict.

    This replaced a markdown scanner, and the reason is worth keeping. Three
    cross-family review rounds each found defects in that scanner, in BOTH
    directions -- approvals hidden in a nested `<details>`, in an HTML comment, in
    a lazily-continued blockquote, in a list-nested quote; and honest receipts
    wrongly refused after a heading, after a fence marker inside an HTML block,
    after a literal `<!--` in a code example. Each fix created the next round's
    findings, which `AGENTS.md` names as a loop rather than progress, and says to
    answer with a redesign: recurring findings in one area mean the shape is
    wrong. Modelling GitHub's renderer was the wrong shape. A position that
    cannot have anything in front of it needs no renderer.

    Trailing whitespace is stripped because an editor adding a space must not
    void a receipt, and trailing whitespace can hide nothing. Leading blank
    lines are skipped for the same reason.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        lines.append(line)
        if len(lines) == count:
            break
    return lines


def binding_head(text: str) -> str | None:
    """The commit a receipt's second line names, or None if it names a diff key."""
    lines = leading_lines(text, 2)
    if len(lines) < 2:
        return None
    match = re.fullmatch(r"Drain-Review-Head: ([0-9a-f]{40})", lines[1])
    return match.group(1) if match else None


def _attests_approval(
    lines: list[str],
    head: str,
    diff_key: str | None = None,
    *,
    same_change: Callable[[str], bool] | None = None,
) -> bool:
    """Do these leading lines OPEN with an approval of `head`, or of its diff?

    ONE definition of what an approval looks like, used for the PR body and for
    the cited comment alike. Exact string equality on the first two non-blank
    lines, in order, so `BLOCK`, a lower-case verdict, trailing prose, or a head
    line for any other commit all refuse.

    The second line binds the approval to what was reviewed, in one of three ways:

    * `Drain-Review-Head: <sha>` — this exact commit.
    * `Drain-Review-Head: <other sha>` — an earlier commit of the SAME change.
      Honoured only when `same_change(other)` is true, which the caller sets by
      comparing tree-diff hashes (`diff_key`). A rebase or merge-main that
      leaves every path's before/after blob unchanged carries the receipt.
      Any real edit, including a conflict resolution that changes bytes, does
      not. A missing commit is not the same change.
    * `Drain-Review-Diff: <key>` — this exact CHANGE, as `diff_key` computes it
      for the current head. Only honoured when the caller computed a key.
    """
    if len(lines) < 2 or lines[0] != "Drain-Review-Verdict: APPROVE":
        return False
    if lines[1] == f"Drain-Review-Head: {head}":
        return True
    if (
        diff_key is not None
        and _DIFF_KEY_RE.fullmatch(diff_key)
        and lines[1] == f"Drain-Review-Diff: {diff_key}"
    ):
        return True
    cited = binding_head("\n".join(lines))
    if (
        cited
        and cited != head
        and diff_key is not None
        and _DIFF_KEY_RE.fullmatch(diff_key)
        and same_change is not None
    ):
        return bool(same_change(cited))
    return False


def comment_attests_approval(
    comment_body: str,
    head: str,
    diff_key: str | None = None,
    *,
    same_change: Callable[[str], bool] | None = None,
) -> bool:
    """Does this comment OPEN by publishing an approval of `head`?

    Comment identity was not enough. Cross-family review 2026-09-26, finding 6:
    the gate accepted any trusted-author comment as the artifact, so a body
    receipt citing an OWNER comment that said `VERDICT: BLOCK` for this exact
    head passed. Checking the comment's own attestation is what makes the
    artifact evidence rather than a bookmark — and it closes the stale-comment
    gap too (finding 7), because the comment must approve this same change
    (this head, or an earlier commit with the same tree-diff), which needs no
    clock.
    """
    return _attests_approval(
        leading_lines(comment_body, 2), head, diff_key, same_change=same_change
    )


def artifact_names_trusted_comment(
    artifact_line: str,
    *,
    repo: str,
    pr: int,
    trusted_comment_urls: frozenset[str] | None,
) -> bool:
    """Does this artifact line name a trusted comment on THIS pull request?

    Three independent conditions, all required:

    * the URL is shaped like a comment anchor on `repo`'s PR `pr` — not a docs
      path, not another PR, not another repository;
    * that exact URL is in the inventory read from the API, so the comment
      actually EXISTS (a receipt can otherwise cite an invented comment id);
    * the inventory only ever contains comments whose `author_association` is
      trusted, so a drive-by commenter cannot supply the artifact.

    Fails closed when the inventory is unavailable.
    """
    if trusted_comment_urls is None:
        return False
    match = _COMMENT_ARTIFACT_RE.fullmatch(artifact_line)
    if match is None:
        return False
    # GitHub resolves owner/repo case-insensitively, so a stamper who types a
    # different casing must not be refused; the PR number is compared as the
    # canonical decimal string the regex already constrained.
    if match["repo"].lower() != repo.lower() or match["pr"] != str(pr):
        return False
    return match["url"].lower() in trusted_comment_urls


def published_approval_urls(
    stream: str,
    *,
    head: str,
    diff_key: str | None = None,
    same_change: Callable[[str], bool] | None = None,
) -> frozenset[str] | None:
    """URLs of comments that PUBLISH a trusted approval of `head`.

    The workflow reads the PR's issue comments, reviews and review comments and
    appends each object to one file (`gh api --jq '.[] | {...}'` emits one
    compact object per line, with the body's newlines JSON-escaped). Both
    filters happen HERE, not in a jq expression, so they are unit tested rather
    than buried in a shell string:

    * `author_association` must be trusted — GitHub computes it at read time, so
      it is not something a comment body can claim about itself;
    * the comment must itself attest `APPROVE` of this change: this head, an
      earlier commit with the same tree-diff, or the diff key. A trusted
      author's comment saying `VERDICT: BLOCK` is not an approval, and before
      this filter existed the gate accepted one as the artifact.

    Returns `None` on anything unparseable — a partially understood inventory
    must deny, never silently shrink to a set that a receipt cannot match and
    also never grow past what was actually read.
    """
    decoder = json.JSONDecoder()
    urls: set[str] = set()
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
        url = obj.get("url")
        association = obj.get("association")
        body = obj.get("body")
        if not isinstance(url, str) or not isinstance(association, str):
            return None
        if not isinstance(body, str):
            # The projection uses `(.body // "")`, so an absent body means the
            # inventory is not the shape this gate reads. Refuse it.
            return None
        if association in _TRUSTED_ASSOCIATIONS and comment_attests_approval(
            body, head, diff_key, same_change=same_change
        ):
            urls.add(url.lower())
    return frozenset(urls)


def diff_key(base: str, head: str, *, cwd: Path | None = None) -> str:
    """The identity of the CHANGE a PR makes, independent of its commit history.

    sha256 over every path in `git diff merge-base(base, head)..head`, each
    with both modes and both FULL blob ids (`--raw --no-abbrev`, no rename
    detection). Blob ids are content hashes, so this pins exactly which bytes
    the PR replaces and with what, including binary files, and nothing else:

    * merge main in, or rebase, where main did not touch the PR's files: the
      merge base moves, but every path's before/after blob is unchanged -> SAME
      key;
    * any edit to the PR's own change, or a catch-up where main DID touch a
      file the PR changes (the "before" blob or the merged "after" blob moves)
      -> DIFFERENT key, so the reviewer re-reviews exactly when the reviewed
      change is no longer the change being merged.

    Deliberately not `git patch-id`: it hashes only textual hunk lines, so two
    different binary changes to the same path would share an id.

    Raises on any git failure; callers must treat that as "no key" (deny).
    """
    return diff_key_from_raw(_raw_change(base, head, cwd=cwd))


def _raw_change(base: str, head: str, *, cwd: Path | None = None) -> bytes:
    """`git diff --raw -z` of merge-base(base, head)..head, full ids, no renames."""
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


def diff_key_from_raw(raw: bytes) -> str:
    """Hash `git diff --raw -z --no-abbrev` output; refuse anything else."""
    digest = hashlib.sha256()
    for path, meta in sorted(change_entries(raw).items()):
        # Length-prefixed, never delimiter-joined: a path may contain a tab or
        # a newline, and joining would let one path spell two entries.
        for part in (path, meta):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()


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

    A dismissed review, by anyone, voids every verdict at or before it: dismissing
    a BLOCK must not bring back the APPROVE it answered (#3989's failure), so the
    check goes back to waiting until a newer verdict.

    `since` is the time the PR's base last changed (`last_base_change`): a review
    submitted before it read a diff against another base, so it counts for nothing.

    Raises ValueError on a review that is not the API's shape: a list the
    decision cannot read must not shrink into one it can.
    """
    void: tuple[str, int] | None = None
    for review in reviews:
        if isinstance(review, dict) and review.get("state") == "DISMISSED":
            when, ident = review.get("submitted_at"), review.get("id")
            if not (isinstance(when, str) and isinstance(ident, int)):
                raise ValueError(f"dismissed review {ident!r} has no time or id")
            void = (when, ident) if void is None or (when, ident) > void else void
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
        if void is not None and (when, ident) <= void:
            continue
        verdict = Verdict(first, commit, str(review.get("html_url") or ""))
        if best is None or (when, ident) > best[:2]:
            best = (when, ident, verdict)
    return None if best is None else best[2]


_BASE_CHANGES = frozenset({"base_ref_changed", "automatic_base_change_succeeded"})


def last_base_change(events: list[object]) -> str | None:
    """When the PR's base last changed, from `GET /issues/{n}/timeline`; None if never.

    A retarget changes the diff a review read: a PR stacked on a branch of
    unreviewed commits, approved there and retargeted to main, would otherwise merge
    them under that approval. A retarget by hand is `base_ref_changed`; GitHub's own,
    when a stacked parent merges and its branch is deleted, is
    `automatic_base_change_succeeded`. Raises on an event not the API's shape.
    """
    latest: str | None = None
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f"not a timeline event: {event!r}")
        if event.get("event") not in _BASE_CHANGES:
            continue
        when = event.get("created_at")
        if not isinstance(when, str):
            raise ValueError("a base change with no time")
        latest = when if latest is None or when > latest else latest
    return latest


# Prose: Markdown under docs/, openspec/ and ideas/, and the top-level Markdown
# files, that no agent reads as instructions. Agents' instruction files bind at any
# depth and in any variant (Codex reads AGENTS.override.md first; Claude reads
# CLAUDE.local.md and nested CLAUDE.md), and docs/reference/ binds as the gates'
# rules (AGENTS.md Fact 4), so none of them is prose. The refusals compare
# case-insensitively, since a checkout on a case-insensitive disk loads either case.
_PROSE_DIRS = ("docs/", "openspec/", "ideas/")
_AGENT_INSTRUCTIONS_RE = re.compile(r"(?i)^(AGENTS|CLAUDE|GEMINI|CODEX)([._-].*)?\.md$")
_BINDING_DIRS = ("docs/reference/",)
_PROSE_MODES = frozenset({b"000000", b"100644"})


def is_prose(path: str) -> bool:
    if not path.endswith(".md"):
        return False
    if _AGENT_INSTRUCTIONS_RE.fullmatch(path.rsplit("/", 1)[-1]):
        return False
    if path.lower().startswith(_BINDING_DIRS):
        return False
    return path.startswith(_PROSE_DIRS) or "/" not in path


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


def group_check(state: CheckState, *, number: int, head: str) -> CheckState:
    """A merge group's `review`, from PR `number`'s own decision made fresh at `head`.

    The group passes only when that decision is a pass. Anything else, a BLOCK or a
    review still owed, fails the group: the queue cannot wait for a review.
    """
    if state.conclusion == "success":
        return CheckState("success", f"PR #{number} at {head}: {state.title}", state.summary)
    return CheckState("failure", f"PR #{number} at {head} has not passed review: {state.title}",
                      state.summary)


def _review_check(argv: list[str]) -> int:
    """`--review-check`: print the `review` check run for a PR's head, as the API's JSON.

    With `--group-sha`, print the merge group's run instead: the same decision, made
    fresh from the PR's reviews and timeline, as `group_check` on the group's commit.
    It never reads the PR's own `review` runs, so a BLOCK whose own decision has not
    landed, or never could, still fails the group.
    """
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --review-check")
    parser.add_argument("--reviews-file", type=Path, required=True,
                        help="the PR's reviews, one JSON array")
    parser.add_argument("--timeline-file", type=Path, required=True,
                        help="the PR's timeline events, one JSON array")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--group-sha", help="the merge group's commit, to post the group's run")
    args = parser.parse_args(argv)
    if not _SHA_RE.fullmatch(args.head) or args.pr < 1:
        print(f"not a commit and a PR: {args.head!r} #{args.pr}", file=sys.stderr)
        return 2
    if args.group_sha is not None and not _SHA_RE.fullmatch(args.group_sha):
        print(f"not a merge group commit: {args.group_sha!r}", file=sys.stderr)
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
    if args.group_sha is not None:
        group = group_check(state, number=args.pr, head=args.head)
        print(json.dumps(_check_run(args.group_sha, group, f"queue-{external_id(args.pr)}")))
        return 0
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


def _gh_check_runs(path: str) -> list[object]:
    """Every check run a `check-runs` listing holds, across all its pages."""
    out = subprocess.run(["gh", "api", "--paginate", path, "--jq", ".check_runs[]"],
                         capture_output=True, text=True, check=True, timeout=120)
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def review_passed_at(repo: str, head: str, number: int, *, app_id: int | None = None) -> bool:
    """Does `head`'s latest `review` run, by the review App, for PR `number`, read success?

    The reader for what acts on the PR's own check outside a merge group (enqueue,
    the queue's freshness probe, the floor hotfix); a merge group decides afresh
    (`--review-check --group-sha`). It lists every run
    (`filter=all`, every page), because the API's default `filter=latest` keeps one
    run per name and suite by its own choice, not the highest id `review_run_passed`
    takes. Raises on a read that fails: a caller that cannot read the check treats
    the head as unreviewed.
    """
    if not _SHA_RE.fullmatch(head):
        raise ValueError(f"not a commit: {head!r}")
    app = review_app_id() if app_id is None else app_id
    listed = _gh_check_runs(
        f"repos/{repo}/commits/{head}/check-runs?check_name=review&filter=all&per_page=100")
    return review_run_passed([r for r in listed if isinstance(r, dict)
                              and (r.get("app") or {}).get("id") == app], number)


def _read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def same_change_as(
    base: str, current_key: str, *, cwd: Path | None = None
) -> Callable[[str], bool]:
    """True when `cited` has the same tree-diff against `base` as `current_key`.

    Any git failure is "not the same change". A receipt must not carry across
    a commit we cannot read.
    """

    def same(cited: str) -> bool:
        if not _SHA_RE.fullmatch(cited) or not _DIFF_KEY_RE.fullmatch(current_key):
            return False
        try:
            return diff_key(base, cited, cwd=cwd) == current_key
        except (subprocess.CalledProcessError, ValueError, OSError):
            return False

    return same


def _carry_comparer(args: argparse.Namespace) -> Callable[[str], bool] | None:
    key = args.diff_key or ""
    base = getattr(args, "base", "") or ""
    if not base or not _DIFF_KEY_RE.fullmatch(key):
        return None
    return same_change_as(base, key)


def _blocking_review(args: argparse.Namespace) -> int:
    """`--blocking-review`: does the body carry a corroborated receipt?"""
    print(
        f"every PR needs a Drain-Review receipt for head {args.head}"
        + (f" or diff key {args.diff_key}" if args.diff_key else ""),
        file=sys.stderr,
    )

    body = _read_text(args.body_file)
    comments = _read_text(args.review_comments_file)
    key = args.diff_key or None
    carry = _carry_comparer(args)
    trusted = (
        None
        if comments is None
        else published_approval_urls(
            comments, head=args.head, diff_key=key, same_change=carry
        )
    )
    if body is not None and review_allows_merge(
        head=args.head,
        body=body,
        artifact_must_be_comment_on=(args.review_repo, args.review_pr),
        trusted_comment_urls=trusted,
        diff_key=key,
        same_change=carry,
    ):
        print("allow")
        return 0
    print("deny")
    return 2


def _list_carry_heads(argv: list[str]) -> int:
    """Print head SHAs a receipt names, other than the current head.

    The workflow fetches these so an unchanged rebase can be compared. A
    missing or unreadable file prints nothing for that source. Garbage in the
    comment inventory prints the body's SHA only; the gate itself still denies
    a receipt it cannot corroborate.
    """
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --list-carry-heads")
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--review-comments-file", type=Path)
    parser.add_argument("--head", default="")
    args = parser.parse_args(argv)
    shas: list[str] = []

    def add(text: str) -> None:
        cited = binding_head(text)
        if cited and cited != args.head and cited not in shas:
            shas.append(cited)

    add(_read_text(args.body_file) or "")
    comments = _read_text(args.review_comments_file)
    if comments:
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
            body = obj.get("body") if isinstance(obj, dict) else None
            if isinstance(body, str):
                add(body)
    for sha in shas:
        print(sha)
    return 0


def _print_diff_key(argv: list[str]) -> int:
    """`--print-diff-key BASE HEAD`: the value a reviewer stamps as Drain-Review-Diff."""
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --print-diff-key")
    parser.add_argument("base", help="the PR's base, e.g. origin/main")
    parser.add_argument("head", nargs="?", default="HEAD")
    args = parser.parse_args(argv)
    try:
        print(diff_key(args.base, args.head))
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"could not compute the diff key: {exc}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--review-check"]:
        return _review_check(sys.argv[2:])
    if sys.argv[1:2] == ["--signal-pr"]:
        return _signal_pr(sys.argv[2:])
    if sys.argv[1:2] == ["--print-diff-key"]:
        return _print_diff_key(sys.argv[2:])
    if sys.argv[1:2] == ["--list-carry-heads"]:
        return _list_carry_heads(sys.argv[2:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument(
        "--base",
        default="",
        help="The PR base commit. With a valid --diff-key, a Drain-Review-Head "
        "receipt for another commit is accepted when that commit's tree-diff "
        "against this base equals the current head's. Empty means only the "
        "exact head (or an explicit Drain-Review-Diff) can match.",
    )
    parser.add_argument(
        "--diff-key",
        default="",
        help="The PR's diff key as computed by the workflow (diff_key), which "
        "lets a `Drain-Review-Diff:` receipt match and lets an unchanged "
        "rebase carry a head receipt. Empty or malformed means only an exact "
        "`Drain-Review-Head:` of --head can match.",
    )
    parser.add_argument(
        "--blocking-review",
        action="store_true",
        help="The required check's decision: prints allow (exit 0) or deny "
        "(exit 2), naming the head and diff key a receipt must carry on "
        "stderr. Requires --review-repo, --review-pr, --review-comments-file.",
    )
    parser.add_argument("--review-repo", default="", help="owner/repo of this PR.")
    parser.add_argument("--review-pr", type=int, help="This PR's number.")
    parser.add_argument(
        "--review-comments-file",
        type=Path,
        help="JSON objects ({url, association}) for this PR's comments, "
        "reviews and review comments. Unreadable => deny.",
    )
    args = parser.parse_args()

    if args.blocking_review:
        if args.review_pr is None or not args.review_repo:
            parser.error("--blocking-review needs --review-pr and --review-repo")
        return _blocking_review(args)

    try:
        body = args.body_file.read_text(encoding="utf-8")
    except OSError:
        print("deny")
        return 2

    if review_allows_merge(
        head=args.head,
        body=body,
        diff_key=args.diff_key or None,
        same_change=_carry_comparer(args),
    ):
        print("allow")
        return 0
    print("deny")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
