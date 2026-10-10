#!/usr/bin/env python3
"""Decide whether a pull request carries a current review receipt.

EVERY pull request needs one (2026-10-02). Two callers, one receipt format:

* `auto-enroll-merge.yml` arms auto-merge only for a head whose body carries a
  receipt (a pre-filter, so unreviewed heads never reach the queue);
* `pr-scope-guard.yml` -- the REQUIRED check -- asks, via `--blocking-review`,
  for the full receipt: the body's `Drain-Review-Verdict: APPROVE` lines, bound
  to this head or its diff key, citing a comment on THIS PR by a trusted author
  that itself opens with the same approval.

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
import re
import subprocess
import sys
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
    if not (_attests_approval(lines, head, diff_key) and len(lines) == 3):
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


def _attests_approval(lines: list[str], head: str, diff_key: str | None = None) -> bool:
    """Do these leading lines OPEN with an approval of `head`, or of its diff?

    ONE definition of what an approval looks like, used for the PR body and for
    the cited comment alike. Exact string equality on the first two non-blank
    lines, in order, so `BLOCK`, a lower-case verdict, trailing prose, or a head
    line for any other commit all refuse.

    The second line binds the approval to what was reviewed, in one of two ways:

    * `Drain-Review-Head: <sha>` — this exact commit. Any push voids it.
    * `Drain-Review-Diff: <key>` — this exact CHANGE, as `diff_key` computes it
      for the current head. Merging main in or rebasing leaves the key alone
      unless the PR's own change moves with it, so it survives the catch-ups
      that voided receipts on 2026-09-26 without accepting any content the
      reviewer did not see. Only honoured when the caller computed a key.
    """
    bindings = [f"Drain-Review-Head: {head}"]
    if diff_key is not None and _DIFF_KEY_RE.fullmatch(diff_key):
        bindings.append(f"Drain-Review-Diff: {diff_key}")
    return len(lines) >= 2 and lines[0] == "Drain-Review-Verdict: APPROVE" and lines[1] in bindings


def comment_attests_approval(comment_body: str, head: str, diff_key: str | None = None) -> bool:
    """Does this comment OPEN by publishing an approval of `head`?

    Comment identity was not enough. Cross-family review 2026-09-26, finding 6:
    the gate accepted any trusted-author comment as the artifact, so a body
    receipt citing an OWNER comment that said `VERDICT: BLOCK` for this exact
    head passed. Checking the comment's own attestation is what makes the
    artifact evidence rather than a bookmark — and it closes the stale-comment
    gap too (finding 7), because the comment must name the CURRENT head, which
    needs no clock.
    """
    return _attests_approval(leading_lines(comment_body, 2), head, diff_key)


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
    stream: str, *, head: str, diff_key: str | None = None,
    release_critical_count: int | None = None,
) -> frozenset[str] | None:
    """URLs of comments that PUBLISH a trusted approval of `head`.

    The workflow reads the PR's issue comments, reviews and review comments and
    appends each object to one file (`gh api --jq '.[] | {...}'` emits one
    compact object per line, with the body's newlines JSON-escaped). Both
    filters happen HERE, not in a jq expression, so they are unit tested rather
    than buried in a shell string:

    * `author_association` must be trusted — GitHub computes it at read time, so
      it is not something a comment body can claim about itself;
    * the comment must itself attest `APPROVE` at this exact head. A trusted
      author's comment saying `VERDICT: BLOCK` is not an approval, and before
      this filter existed the gate accepted one as the artifact.

    When a release-critical count is required, the SAME comment must declare
    it as its third non-blank line, immediately after the approval binding.
    Filtering this inventory keeps the PR body's artifact URL authoritative:
    a declaration in any other comment cannot authorize the cited receipt.

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
            body, head, diff_key
        ):
            if release_critical_count is not None:
                lines = leading_lines(body, 3)
                if len(lines) != 3 or lines[2] != (
                    f"Drain-Review-Release-Critical: {release_critical_count}"
                ):
                    continue
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
    def git(*args: str) -> bytes:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, check=True
        ).stdout

    merge_base = git("merge-base", base, head).strip().decode("ascii")
    # `--no-abbrev` is what makes the ids full: `--full-index` alone still
    # prints abbreviated ids in --raw output, and two different blobs can share
    # an abbreviation (cross-family review 2026-09-27 built such a pair).
    raw = git(
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
    return diff_key_from_raw(raw)


def diff_key_from_raw(raw: bytes) -> str:
    """Hash `git diff --raw -z --no-abbrev` output; refuse anything else."""
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("unexpected `git diff --raw -z` output")
    pairs = sorted(zip(fields[1::2], fields[0::2]))
    digest = hashlib.sha256()
    for path, meta in pairs:
        if _RAW_META_RE.fullmatch(meta) is None:
            raise ValueError(f"unexpected `git diff --raw` record: {meta!r}")
        # Length-prefixed, never delimiter-joined: a path may contain a tab or
        # a newline, and joining would let one path spell two entries.
        for part in (path, meta):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.hexdigest()


_REGULAR_BLOB_MODES = frozenset({"100644", "100755"})


def _read_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


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
    trusted = (
        None
        if comments is None
        else published_approval_urls(
            comments, head=args.head, diff_key=key,
            release_critical_count=args.release_critical_count,
        )
    )
    if body is not None and review_allows_merge(
        head=args.head,
        body=body,
        artifact_must_be_comment_on=(args.review_repo, args.review_pr),
        trusted_comment_urls=trusted,
        diff_key=key,
    ):
        print("allow")
        return 0
    print("deny")
    return 2


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
    if sys.argv[1:2] == ["--print-diff-key"]:
        return _print_diff_key(sys.argv[2:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument(
        "--diff-key",
        default="",
        help="The PR's diff key as computed by the workflow (diff_key), which "
        "lets a `Drain-Review-Diff:` receipt match. Empty or malformed means "
        "only `Drain-Review-Head:` receipts can match.",
    )
    parser.add_argument(
        "--blocking-review",
        action="store_true",
        help="The required check's decision: prints allow (exit 0) or deny "
        "(exit 2), naming the head and diff key a receipt must carry on "
        "stderr. Requires --review-repo, --review-pr, --review-comments-file.",
    )
    parser.add_argument("--review-repo", default="", help="owner/repo of this PR.")
    parser.add_argument(
        "--release-critical-count", type=int,
        help="With --blocking-review, also require the cited approval comment's "
        "third non-blank line to be Drain-Review-Release-Critical: <this count>.",
    )
    parser.add_argument("--review-pr", type=int, help="This PR's number.")
    parser.add_argument(
        "--review-comments-file",
        type=Path,
        help="JSON objects ({url, association}) for this PR's comments, "
        "reviews and review comments. Unreadable => deny.",
    )
    args = parser.parse_args()

    if args.release_critical_count is not None and (
        not args.blocking_review or args.release_critical_count < 0
    ):
        parser.error("--release-critical-count needs --blocking-review and a nonnegative count")

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
    ):
        print("allow")
        return 0
    print("deny")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
