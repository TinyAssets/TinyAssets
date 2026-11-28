#!/usr/bin/env python3
"""The review check and the body's Untangle check (ADR-038).

* `review.yml` posts the one review gate, the required `review` check, from the PR's
  reviews (`--review-check`, `review_check`).
* `pr-scope-guard.yml`, the required `Diff scope declared`, asks a change to more than
  prose for an Untangle check in its body (`--untangle-check`, `untangle_check_problems`),
  and refuses any change that drops an open owed item from the decision files
  (`owed_problems`); `--owed` lists the open ones.

Every pull request is reviewed: on 2026-10-01 auto-enroll armed #4247, a superseded
head with a known data-loss bug, because it touched no path that needed a review, and
before 2026-09-26 a verdict was only a PR comment, so #3989 auto-merged at the exact
head its reviewer had BLOCKED.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# One `git diff --raw` record header with FULL object ids (SHA-1 or SHA-256).
_RAW_META_RE = re.compile(
    rb":[0-7]{6} [0-7]{6} ([0-9a-f]{40}|[0-9a-f]{64}) ([0-9a-f]{40}|[0-9a-f]{64}) [A-Z][0-9]*"
)

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
# standing verdicts (`standing_verdicts`) into the `review` check run on the head:
#
# * no verdict standing -> in progress, "waiting for review of <head>" (pending,
#   not red);
# * a BLOCK stands -> failure. It stands until a verdict is posted after it on the
#   same commit or a descendant (on the head, for a commit force-pushed away);
# * else the latest verdict on the most descendant reviewed commit, an APPROVE at
#   A, with `carries(A, head)` -> success;
# * an APPROVE that does not carry -> failure, naming the review the change since A
#   needs. In progress, it waited with no event for a review nobody knew to ask
#   for (#5725, #5727: 3 h, 2026-11-21); a failure is an event and reads red.
#
# Never the latest by time alone: reviews of two heads overlap, so the head read
# last is not always the one posted last (#5856's reviews, 2026-11-28). A late
# APPROVE of an earlier head must not void a newer head's BLOCK, nor a late BLOCK
# of an earlier head lose to an APPROVE of a newer one. Commit order is git
# ancestry, and a review of a commit shows it existed then
# (docs/decisions/untangle-2026-11/verdict-order.md).
#
# COMMENT, not APPROVE, because the one writer account cannot approve its own
# PR (ADR-038 decision 2).
#
# A verdict counts only from an account that can push to the repository. The
# review's `author_association` says nothing about that: MEMBER is any member of
# the owning organization, with or without access here, and COLLABORATOR anyone
# invited, read-only included (GitHub's `CommentAuthorAssociation`). GitHub's
# `GET /repos/{r}/collaborators/{login}/permission` gives the highest role from
# every grant, the repository's, a team's, the organization's and the
# enterprise's, with maintain read as `write` and triage as `read`.

_VERDICTS = frozenset({"APPROVE", "BLOCK"})
_PUSH = {"admin": True, "write": True, "read": False, "none": False}
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?$")


class PermissionUnread(Exception):
    """Whether a verdict's author can push could not be read: no verdict counts."""


@dataclass(frozen=True)
class Verdict:
    verdict: str
    commit: str
    url: str


def standing_verdicts(reviews: list[object], *, head: str,
                      in_line: Callable[[str, str], bool],
                      can_push: Callable[[str], bool],
                      since: str | None = None,
                      edits: Iterable[tuple[str, str]] = ()) -> list[Verdict]:
    """The verdicts that still stand for `head`, newest first.

    `reviews` is `GET /repos/{r}/pulls/{n}/reviews`. A review is a verdict when it is
    not dismissed or pending and its body's first non-blank line, with surrounding
    whitespace removed, is exactly `APPROVE` or `BLOCK`. Anything else is an ordinary
    review comment and changes nothing.

    A verdict counts when its commit is in the head's history (`in_line(commit,
    head)`: the commit is `head` or an ancestor of it) and its author `can_push`; a
    verdict by anyone else is ignored. A counted verdict stands unless a counted
    verdict submitted after it (by `submitted_at`, then id) is on the same commit or
    a descendant. So a BLOCK stands until one is, and the latest verdict on the most
    descendant commit reviewed always stands.

    An APPROVE of a commit is late, and ignored, when any review (anyone's, of any
    kind, dismissed ones too) of a descendant was submitted before it: that newer
    head existed and the APPROVE read an older one. So nothing that removes a
    newer head's BLOCK (a dismissal, a base change, an edit) lets a late APPROVE of
    an earlier head through.

    A commit force-pushed away is in no order with the head's: an APPROVE on it is
    ignored, and a writer's BLOCK on it stands until a counted verdict on the head
    itself is submitted after it. It answers and voids nothing.

    Verdicts are read newest first, and `can_push(login)` is asked once a login,
    only for a BLOCK, or an APPROVE that is not late, that no newer counted verdict
    already answers (in the head's history, one on its commit or a descendant;
    outside it, one on the head), and for the author of each edit: an answered
    verdict cannot decide, so its author's read never holds a PR. When it raises
    `PermissionUnread`, so does this: that author may be a writer whose BLOCK stands.

    A dismissed review, by anyone, voids every verdict at or before it: dismissing
    a BLOCK must not bring back the APPROVE it answered (#3989's failure), so the
    check goes back to waiting until a newer verdict. An edit by a writer, `(login,
    when)` from `review_edits`, voids every verdict at or before its time the same
    way: an edited first line would otherwise turn a BLOCK into an APPROVE, or into
    no verdict, with no review after it.

    `since` is the time the PR's base last changed (`last_base_change`): a review
    submitted before it read a diff against another base, so it counts for nothing.

    Raises ValueError on a review that is not the API's shape: a list the
    decision cannot read must not shrink into one it can.
    """
    void: tuple[str, int] | None = None
    seen: list[tuple[str, int, str]] = []
    for review in reviews:
        if isinstance(review, dict) and review.get("state") == "DISMISSED":
            when, ident = review.get("submitted_at"), review.get("id")
            if not (isinstance(when, str) and isinstance(ident, int)):
                raise ValueError(f"dismissed review {ident!r} has no time or id")
            void = (when, ident) if void is None or (when, ident) > void else void
    pushes: dict[str, bool] = {}
    for login, when in edits:
        if login not in pushes:
            pushes[login] = can_push(login)
        if pushes[login] and (void is None or (when, _AFTER_ALL) > void):
            void = (when, _AFTER_ALL)
    verdicts: list[tuple[str, int, str, Verdict]] = []
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError(f"not a review: {review!r}")
        state = review.get("state")
        body = review.get("body") or ""
        commit = review.get("commit_id")
        when = review.get("submitted_at")
        ident = review.get("id")
        if (state != "PENDING" and isinstance(commit, str) and _SHA_RE.fullmatch(commit)
                and isinstance(when, str) and isinstance(ident, int)):
            seen.append((when, ident, commit))
        if state in {"DISMISSED", "PENDING"}:
            continue
        if not isinstance(body, str):
            raise ValueError(f"review {ident!r} has no body")
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if first not in _VERDICTS:
            continue
        user = review.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        if not (isinstance(commit, str) and _SHA_RE.fullmatch(commit)
                and isinstance(when, str) and isinstance(ident, int)
                and isinstance(login, str)):
            raise ValueError(f"review {ident!r} has no commit, time, id or author")
        if since is not None and when <= since:
            continue
        if void is not None and (when, ident) <= void:
            continue
        verdicts.append((when, ident, login,
                         Verdict(first, commit, str(review.get("html_url") or ""))))
    lines: dict[tuple[str, str], bool] = {}

    def line(older: str, newer: str) -> bool:
        if (older, newer) not in lines:
            lines[older, newer] = in_line(older, newer)
        return lines[older, newer]

    def late(when: str, ident: int, commit: str) -> bool:
        return any((then, other) < (when, ident) and read != commit
                   and line(read, head) and line(commit, read)
                   for then, other, read in seen)

    standing: list[tuple[Verdict, bool]] = []
    for when, ident, login, verdict in sorted(verdicts, key=lambda v: v[:2], reverse=True):
        inside = line(verdict.commit, head)
        if inside:
            answered = any(within and line(verdict.commit, newer.commit)
                           for newer, within in standing)
        else:
            answered = verdict.verdict == "APPROVE" or any(
                newer.commit == head for newer, _ in standing)
        if answered or (verdict.verdict == "APPROVE" and late(when, ident, verdict.commit)):
            continue
        if login not in pushes:
            pushes[login] = can_push(login)
        if pushes[login]:
            standing.append((verdict, inside))
    return [verdict for verdict, _ in standing]


# An edit's time from the timeline (`review_edits`) voids every verdict submitted at
# or before it, whatever its id.
_AFTER_ALL = 2**63
# GitHub sets a review's `updated_at` a second after its `submitted_at` with no edit:
# of the 249 reviews on #5662 to #5862, read 2026-11-28, 231 had a 0 s gap and 18 had
# 1 s (docs/decisions/untangle-2026-11/verdict-order.md).
_EDITED_AFTER_S = 10


def review_edits(events: list[object]) -> list[tuple[str, str]]:
    """Each edited review, `(login, updated_at)`, from `GET /issues/{n}/timeline`.

    The reviews listing has no edit time; the timeline's `reviewed` events carry
    `updated_at`. A review is edited when that is more than `_EDITED_AFTER_S` after
    its `submitted_at`. A dismissed one is skipped: its dismissal already voids it.
    Raises on a `reviewed` event not the API's shape.
    """
    edits: list[tuple[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f"not a timeline event: {event!r}")
        if event.get("event") != "reviewed" or event.get("state") in {"dismissed", "pending"}:
            continue
        user = event.get("user")
        login = user.get("login") if isinstance(user, dict) else None
        submitted, updated = event.get("submitted_at"), event.get("updated_at")
        if not (isinstance(login, str) and isinstance(submitted, str)
                and isinstance(updated, str)):
            raise ValueError(f"review event {event.get('id')!r} has no author or times")
        gap = datetime.fromisoformat(updated) - datetime.fromisoformat(submitted)
        if gap.total_seconds() > _EDITED_AFTER_S:
            edits.append((login, updated))
    return edits


def in_line(older: str, newer: str, *, cwd: Path | None = None) -> bool:
    """Whether commit `older` is `newer` or an ancestor of it (`in_line` for
    `standing_verdicts`), by `git merge-base --is-ancestor`.

    `newer` must be a commit here: review.yml fetches the head and so its whole
    history. An `older` that is not one here is then not in that history (a commit
    force-pushed away that could not be fetched). Raises ValueError on anything git
    cannot answer: an ancestry nobody can read decides nothing.
    """
    def git(*args: str) -> int:
        try:
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                  timeout=60).returncode
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError(f"cannot read whether {older} is in {newer}'s history: "
                             f"{exc}") from exc

    if not (_SHA_RE.fullmatch(older) and _SHA_RE.fullmatch(newer)):
        raise ValueError(f"not two commits: {older!r} {newer!r}")
    if git("cat-file", "-e", f"{newer}^{{commit}}") != 0:
        raise ValueError(f"commit {newer} is not here")
    if git("cat-file", "-e", f"{older}^{{commit}}") != 0:
        return False
    answer = git("merge-base", "--is-ancestor", older, newer)
    if answer not in (0, 1):
        raise ValueError(f"git cannot say whether {older} is in {newer}'s history")
    return answer == 0


def push_reader(repo: str) -> Callable[[str], bool]:
    """`can_push` for `standing_verdicts`, from GitHub's permission read with `gh`.

    `admin` and `write` (maintain among them) can push; `read` and `none` cannot.
    Anything else, a failed read included, raises `PermissionUnread` with its reason.
    """
    def can_push(login: str) -> bool:
        if not _LOGIN_RE.fullmatch(login):
            raise PermissionUnread(f"{login!r} is not a GitHub login")
        try:
            answer = _gh_json(f"repos/{repo}/collaborators/{login}/permission")
        except subprocess.CalledProcessError as exc:
            why = (exc.stderr or "").strip()[:200] or f"gh exited {exc.returncode}"
            raise PermissionUnread(f"could not read {login}'s permission: {why}") from exc
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise PermissionUnread(f"could not read {login}'s permission: {exc}") from exc
        permission = answer.get("permission") if isinstance(answer, dict) else None
        if permission not in _PUSH:
            raise PermissionUnread(f"{login}'s permission reads {permission!r}")
        return _PUSH[permission]

    return can_push


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


# ---- The owed record (the untangle skill, "The PR body") -----------------------------
#
# What a PR leaves owed is a checkbox line (`- [ ] ...`) in its own decision file under
# `docs/decisions/`, naming its fix as the body's owed items do. The PR that does it
# ticks it (`- [x]`), and may add after its text what did it (`Done (#5680).`, as
# `tasks.md`'s ticks do); an open one is never deleted or reworded. No shared list holds
# them, so no shared line grows with each PR: on 2026-11-21 six passes each appended to
# the same two `tasks.md` lists, and each merge made the next PR conflict and rerun CI.
# `--owed` lists the open ones for the re-check loop; the Untangle check refuses a
# change that drops one, or opens one that names no fix. An item is identified by its
# text, so moving it to another decision file in the same change keeps it.

_DECISIONS = "docs/decisions/"
_OWED_LINE_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$")
_NO_BLOB = re.compile(rb"0+")


def owed_items(text: str) -> list[tuple[int, bool, str]]:
    """A decision file's owed items: (line, ticked, text with its wrapped lines joined)."""
    items: list[list] = []
    indent: int | None = None
    for number, line in enumerate(text.splitlines(), 1):
        item = _OWED_LINE_RE.match(line)
        if item:
            indent = len(item[1])
            items.append([number, item[2] != " ", item[3]])
        elif (indent is not None and line.strip() and not _BULLET_RE.match(line)
              and len(line) - len(line.lstrip()) > indent):
            items[-1][2] += f" {line}"
        else:
            indent = None
    return [(number, ticked, " ".join(text.split())) for number, ticked, text in items]


def owed_problems(base: str, head: str, *, cwd: Path | None = None) -> list[str]:
    """Why merge-base(base, head)..head drops an open owed item or opens one with no fix."""
    def items(blob: bytes) -> list[tuple[int, bool, str]]:
        if _NO_BLOB.fullmatch(blob):
            return []
        return owed_items(subprocess.run(
            ["git", "cat-file", "blob", blob.decode("ascii")], cwd=cwd, capture_output=True,
            check=True).stdout.decode("utf-8", "replace"))

    before: list[tuple[str, str]] = []
    after: list[tuple[str, int, bool, str]] = []
    for raw_path, meta in change_entries(_raw_change(base, head, cwd=cwd)).items():
        path = raw_path.decode("utf-8", "surrogateescape")
        if not (path.startswith(_DECISIONS) and path.endswith(".md")):
            continue
        old, new = meta.split()[2:4]
        before += [(path, text) for _, ticked, text in items(old) if not ticked]
        after += [(path, *item) for item in items(new)]
    kept = {text for *_, text in after}
    done = [text for _, _, ticked, text in after if ticked]
    return [f"{path}: an open owed item is gone without being ticked done (or was reworded): "
            f"{text[:200]}" for path, text in before
            if text not in kept and not any(tick.startswith(text) for tick in done)] + [
        f"{path}:{line}: an owed item names no PR (#123), branch (untangle/, fix/, process/, "
        f"land/) or live: wait: {text[:200]}"
        for path, line, ticked, text in after if not ticked and not _NAMES_FIX_RE.search(text)]


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

    Asked only for a commit in the head's history (`standing_verdicts`), so a
    rebased or force-pushed head never carries a verdict: it waits for a review.
    Compares the two PRs' changes path by path: each is
    `git diff --raw --no-renames` of merge-base(base, commit)..commit, so a path
    differs when its before or after blob or mode differs, or it is in one
    change and not the other (a rename counts both its paths). The verdict
    carries when:

    1. no path differs: the same change, so a merge of main that touched none of
       the PR's files carries;
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
    in_line: Callable[[str, str], bool],
    can_push: Callable[[str], bool],
    since: str | None = None,
    edits: Iterable[tuple[str, str]] = (),
) -> CheckState:
    """The `review` check for `head`, from the PR's reviews, `carry(commit)`,
    `in_line(older, newer)` and `can_push(login)` (`standing_verdicts`).

    The newest standing BLOCK fails it, named; one on a commit outside the head's
    history names that, and the review of the head that answers it. With none, the
    standing verdict on the most descendant commit decides: all the others are
    APPROVEs on its ancestors. With no one such commit (two reviewed commits neither
    in the other's history, after a force-push merged back), the head needs a
    review. Only reviews after `since`, the PR's last base change, count, and an
    edit voids the verdicts before it (`edits`, `review_edits`). A permission that
    cannot be read is no verdict, its reason in the title and summary: never a pass.
    """
    ask = (
        f"A reviewer who can push posts a PR review on the commit it read ({head}), its "
        "first line `APPROVE` or `BLOCK`, the evidence below. Any other delta after a "
        "review gets a one-round delta review of only that delta."
    )
    try:
        standing = standing_verdicts(reviews, head=head, in_line=in_line,
                                     can_push=can_push, since=since, edits=edits)
    except PermissionUnread as exc:
        return CheckState(None, f"no verdict counts: {exc}",
                          f"A verdict counts only from an account that can push, and {exc}. "
                          f"Rerun review.yml once the read works.\n\n{ask}")
    if not standing:
        title = f"waiting for review of {head}"
        if since is not None:
            title += f" (the base changed at {since})"
        return CheckState(None, title, ask)
    blocks = [v for v in standing if v.verdict == "BLOCK"]
    if blocks and not in_line(blocks[0].commit, head):
        return CheckState("failure", f"BLOCK at {blocks[0].commit}, which is not in "
                          f"{head}'s history; a review of {head} after it answers it",
                          f"{blocks[0].url}\n\n{ask}")
    tops = [] if blocks else [
        v for v in standing if all(in_line(o.commit, v.commit) for o in standing)]
    if not (blocks or tops):
        commits = ", ".join(sorted({v.commit for v in standing}))
        return CheckState("failure", f"reviewed commits {commits} are not one line of "
                          f"history; {head} needs a review", ask)
    verdict = (blocks or tops)[0]
    why = carry(verdict.commit)
    if why is None:
        owed = "a fresh review" if verdict.verdict == "BLOCK" else "a delta review"
        return CheckState(
            "failure",
            f"{verdict.verdict} at {verdict.commit}; the change since needs {owed}",
            f"{verdict.url}\n\n{ask}",
        )
    if verdict.verdict == "BLOCK":
        return CheckState("failure", f"BLOCK: {why}", verdict.url)
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
    parser = argparse.ArgumentParser(prog="review_checks.py --review-check")
    parser.add_argument("--reviews-file", type=Path, required=True,
                        help="the PR's reviews, one JSON array")
    parser.add_argument("--timeline-file", type=Path, required=True,
                        help="the PR's timeline events, one JSON array")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repo", required=True, help="owner/repo, whose writers' verdicts count")
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
            edits=review_edits(events),
            carry=lambda commit: carries(args.base, commit, args.head),
            in_line=in_line, can_push=push_reader(args.repo),
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
    parser = argparse.ArgumentParser(prog="review_checks.py --signal-pr")
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


def _gh_read(args: list[str], timeout: int) -> str:
    """`gh api` with `args`, a read, and its whole answer. GitHub's 5xx answers pass (a
    502 stopped #5689's decision), so one is asked again, 5 s then 10 s later; any
    other failure raises at once. review.yml's reads come here through `--gh-read`."""
    for attempt in (1, 2):
        try:
            return subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                                  check=True, timeout=timeout).stdout
        except subprocess.CalledProcessError as exc:
            if not re.search(r"HTTP 5\d\d", exc.stderr or ""):
                raise
            sys.stderr.write(exc.stderr)
            time.sleep(5 * attempt)
    return subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                          check=True, timeout=timeout).stdout


def _gh_json(path: str) -> object:
    return json.loads(_gh_read([path], 60))


def _untangle_check(argv: list[str]) -> int:
    """`--untangle-check`: does the body's Untangle check hold for the PR's change, and
    does the change keep every open owed item?

    Prints `allow` (exit 0) or `deny` (exit 2), each problem on stderr.
    """
    parser = argparse.ArgumentParser(prog="review_checks.py --untangle-check")
    parser.add_argument("--base", required=True, help="the PR's base commit")
    parser.add_argument("--head", required=True, help="the PR's head commit")
    parser.add_argument("--body-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        body = args.body_file.read_text(encoding="utf-8", errors="replace")
        problems = (untangle_check_problems(body, changed_paths(args.base, args.head))
                    + owed_problems(args.base, args.head))
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        problems = [f"cannot read the PR's body or changed paths: {exc}"]
    for problem in problems:
        print(f"untangle check: {problem}", file=sys.stderr)
    print("deny" if problems else "allow")
    return 2 if problems else 0


def _owed(argv: list[str]) -> int:
    """`--owed`: every open owed item in the decision files, as `path:line: text`.

    Run from the repository's root; the re-check loop reads this list.
    """
    argparse.ArgumentParser(prog="review_checks.py --owed").parse_args(argv)
    if not Path(_DECISIONS).is_dir():
        print(f"no {_DECISIONS} here: run from the repository's root", file=sys.stderr)
        return 2
    for path in sorted(Path(_DECISIONS).rglob("*.md")):
        for line, ticked, text in owed_items(path.read_text(encoding="utf-8")):
            if not ticked:
                print(f"{path.as_posix()}:{line}: {text}")
    return 0


def _gh_read_command(argv: list[str]) -> int:
    """`--gh-read`: `gh api` with `argv`, through `_gh_read`, its answer on stdout."""
    try:
        sys.stdout.write(_gh_read(argv, 120))
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or "")
        return 1
    return 0


_COMMANDS = {
    "--gh-read": _gh_read_command,
    "--owed": _owed,
    "--review-check": _review_check,
    "--signal-pr": _signal_pr,
    "--untangle-check": _untangle_check,
}


def main() -> int:
    command = _COMMANDS.get(sys.argv[1] if len(sys.argv) > 1 else "")
    if command is None:
        print(f"usage: review_checks.py {{{','.join(_COMMANDS)}}} ...", file=sys.stderr)
        return 2
    return command(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
