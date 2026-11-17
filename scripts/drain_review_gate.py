#!/usr/bin/env python3
"""The review check and the body's Untangle check (ADR-038).

* `review.yml` posts the one review gate, the required `review` check, from the PR's
  reviews (`--review-check`, `review_check`); the queue's freshness probe and the floor
  hotfix read it (`review_passed_at`).
* `pr-scope-guard.yml`, the required `Diff scope declared`, asks a change to more than
  prose for an Untangle check in its body (`--untangle-check`, `untangle_check_problems`).

Every pull request is reviewed: on 2026-10-01 auto-enroll armed #4247, a superseded
head with a known data-loss bug, because it touched no path that needed a review, and
before 2026-09-26 a verdict was only a PR comment, so #3989 auto-merged at the exact
head its reviewer had BLOCKED.
"""

from __future__ import annotations

import argparse
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

# `author_association` as GitHub computes it at read time: trusted metadata, not
# something a review body can claim. CONTRIBUTOR and NONE are excluded: anyone can
# review a public repository's PR, and a drive-by review must not decide it.
_TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


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
        # A gitlink change must never vanish from the change: without this, a
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
# PR (ADR-038 decision 2).

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


# ---- The body's Untangle check (the untangle skill, "The PR body") -------------------
#
# A pull request that changes anything but prose carries an **Untangle check**: a
# heading, or a line that opens in bold, named so. In it, an item left, owed or for
# later names its fix: a PR (`#123`), a branch (`untangle/`, `fix/`, `process/` or `land/`)
# or a live wait (`live: ...`). An item is a bullet or a paragraph. It is owed when it sits under a
# label (a heading, a bold opener, or a line ending in `:`) that opens `Owed` or `Later`,
# or under one that opens `Left` and itself says owed or later; a decided keep under
# `Left` is not owed. Code spans are not read, and "owed no" or "nothing owed" is not a
# debt. Calibrated on the 40 PRs merged up to #5588 (2026-11-14).

_UNTANGLE_HEAD_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s*|\*\*)Untangle check\b", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s")
_BULLET_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_BOLD_LABEL_RE = re.compile(r"^\*\*(?P<label>[^*]+)\*\*(?P<colon>:?)\s*(?P<rest>.*)$")
_OWING_LABEL_RE = re.compile(r"^\W*(?P<word>left|owed|later)\b", re.IGNORECASE)
_OWED_WORD_RE = re.compile(r"\b(?:owed|later)\b", re.IGNORECASE)
_NOT_OWED_RE = re.compile(r"\bowed\s+(?:no|nothing)\b|\b(?:nothing|none)\s+(?:is\s+)?owed\b",
                          re.IGNORECASE)
_NAMES_FIX_RE = re.compile(r"#\d+|\b(?:untangle|fix|process|land)/[\w./-]+|\blive:",
                           re.IGNORECASE)
_NOTHING_RE = re.compile(r"^\W*(?:nothing|none)\b", re.IGNORECASE)


def _untangle_section(body: str) -> list[str] | None:
    """The lines of the body's Untangle check, or None when it has none."""
    lines = body.splitlines()
    for at, line in enumerate(lines):
        if _UNTANGLE_HEAD_RE.match(line):
            heading = _HEADING_RE.match(line)
            level = len(heading.group(1)) if heading else 6
            section: list[str] = []
            for each in lines[at + 1:]:
                inner = _HEADING_RE.match(each)
                if inner and len(inner.group(1)) <= level:
                    break
                section.append(each)
            return section
    return None


def _owed_items(section: list[str]) -> list[str]:
    """The items of an Untangle check that are owed, by the rule above."""
    items: list[list] = []  # [the owing word of the item's label, its text]
    label: str | None = None  # the owing word of the paragraph label above
    top: str | None = None  # the owing word of a top-level `- **Left:**` bullet
    open_item = False
    for line in section:
        if not line.strip():
            open_item = False
            continue
        bullet = _BULLET_RE.match(line)
        heading = _HEADING_RE.match(line)
        text = bullet.group(2).strip() if bullet else line.strip()
        if heading:
            text = text.lstrip("#").strip()
        bold = _BOLD_LABEL_RE.match(text)
        owing = _OWING_LABEL_RE.match(bold["label"] if bold else text)
        word = owing["word"].lower() if owing else None
        rest = bold["rest"].strip() if bold else ""
        if bullet and not bullet.group(1):
            top = word if bold else None
            items.append([top or label, rest if top else text])
        elif bullet:
            items.append([top or label, text])
        elif open_item and not (heading or bold):
            items[-1][1] += f" {text}"
            continue
        elif heading or text.endswith(":") or (bold and (
                word or not rest or bold["colon"] or bold["label"].endswith(":"))):
            label, top = word, None
            items.append([word, rest])
        else:
            items.append([label, text])
        open_item = bool(items[-1][1])
    owed = []
    for word, text in items:
        said = _NOT_OWED_RE.sub("", re.sub(r"`[^`]*`", "", text))
        if word is None or not said.strip() or _NOTHING_RE.match(said):
            continue
        if (word != "left" or _OWED_WORD_RE.search(said)) and not _NAMES_FIX_RE.search(text):
            owed.append(text)
    return owed


def untangle_check_problems(body: str, paths: list[str]) -> list[str]:
    """Why the body's Untangle check does not hold for a change to `paths`; [] when it does."""
    if all(is_prose(path) for path in paths):
        return []
    section = _untangle_section(body)
    if section is None:
        return ["the body has no Untangle check (a heading or a bold line named so), and the "
                f"change is not only prose: {next(p for p in paths if not is_prose(p))}"]
    return ["an owed item names no PR (#123), branch (untangle/, fix/, process/, land/) or "
            f"live: wait: {item[:200]}" for item in _owed_items(section)]


def changed_paths(base: str, head: str, *, cwd: Path | None = None) -> list[str]:
    """Every path `git diff merge-base(base, head)..head` changes; a rename is both."""
    return [p.decode("utf-8", "surrogateescape")
            for p in change_entries(_raw_change(base, head, cwd=cwd))]


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


def _untangle_check(argv: list[str]) -> int:
    """`--untangle-check`: does the body's Untangle check hold for the PR's change?

    Prints `allow` (exit 0) or `deny` (exit 2), each problem on stderr.
    """
    parser = argparse.ArgumentParser(prog="drain_review_gate.py --untangle-check")
    parser.add_argument("--base", required=True, help="the PR's base commit")
    parser.add_argument("--head", required=True, help="the PR's head commit")
    parser.add_argument("--body-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        body = args.body_file.read_text(encoding="utf-8", errors="replace")
        problems = untangle_check_problems(body, changed_paths(args.base, args.head))
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        problems = [f"cannot read the PR's body or changed paths: {exc}"]
    for problem in problems:
        print(f"untangle check: {problem}", file=sys.stderr)
    print("deny" if problems else "allow")
    return 2 if problems else 0


_COMMANDS = {
    "--review-check": _review_check,
    "--signal-pr": _signal_pr,
    "--untangle-check": _untangle_check,
}


def main() -> int:
    command = _COMMANDS.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if command is None:
        print(f"usage: drain_review_gate.py {{{','.join(_COMMANDS)}}} ...", file=sys.stderr)
        return 2
    return command(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
