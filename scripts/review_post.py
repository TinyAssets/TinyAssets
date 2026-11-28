#!/usr/bin/env python3
"""The reviewer's poster: one `COMMENT` review on the commit it read (`REVIEW.md`).

    review_post.py read --repo O/R --pr N --writers FILE
    review_post.py post --repo O/R --pr N --writers FILE --commit SHA --review FILE

Run it from a detached worktree of `origin/main`, the reviewer's working directory,
as `python3 -I scripts/review_post.py`, never the PR's copy: a PR must not loosen
its own review. It calls `gh` by its absolute path and refuses one inside a git
checkout, which a PR's tree could put first on `PATH`, and imports only the
`review_checks` beside it, main's too, for the edit rule `review.yml` applies.

`read` lists what a review must answer: every earlier `Important:` finding, edit
and `Founder question:` by a writer, and the PR's comments. `post` builds the
review from the reviewer's JSON and posts it on `--commit`, which must be the
PR's head:

    {"verdict": "BLOCK",
     "summary": "What was read and how.",
     "findings": [{"severity": "Important", "path": "a.py", "line": 12,
                   "text": "One paragraph: the input, what happens, what should."},
                  {"severity": "Nit", "text": "A finding with no line."}],
     "earlier": [{"id": "review:123#1", "status": "fixed", "note": "abc1234"},
                 {"id": "question:120#1", "status": "answered",
                  "answer": "issuecomment:456"}],
     "founder_question": "One sentence a non-engineer can answer."}

The verdict is the body's first line, which `review.yml` reads. The body is the
record: it holds every finding, `path:line` first, and one goes inline as well
when its line is in the PR's diff. Every earlier entry is listed: an Important
or an edit as `fixed`, `withdrawn` (with its reason in `note`) or `standing`; a
founder question as `answered`, by a writer's unedited comment posted after it,
or `standing`. The verdict is `BLOCK` exactly when one stands, or a new Important
or founder question. After posting, the threads of earlier inline findings it
lists as fixed are resolved. Exit 0 posted, 1 GitHub or the head refused it, 2
the input is refused, 3 posted but a thread was not resolved.

Who counts (`REVIEW.md`): findings, edits, questions and answers only from a
writer in `--writers`, the GitHub MCP's `list_repository_collaborators` result
saved as JSON (a session's proxy refuses GitHub's permission read). Comments
from `github-actions[bot]` (the queue-handoff ledger) are listed apart and
answer nothing. A base change resets verdicts in `review.yml`, never findings:
the code a finding names is still in the head.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_checks as checks  # noqa: E402

# The two verdicts review.yml reads as a review's first line
# (scripts/tests/test_review_post.py reads a posted one through review_checks).
VERDICTS = ("APPROVE", "BLOCK")
MARKERS = ("Important", "Nit", "Pre-existing")
# A marker or a founder question opens a line after any indent or list bullet.
_LEAD = r"^\s*(?:[-*+]\s+)?"
_MARKER_RE = re.compile(_LEAD + r"(Important|Nit|Pre-existing):\s*(.*)$")
_QUESTION_RE = re.compile(_LEAD + r"Founder question:\s*(.*)$")
_WHERE_RE = re.compile(r"^`(?P<path>[^`\n]+):(?P<line>\d+)` (?P<text>.*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NITS = 5  # REVIEW.md: at most five a review
_STATUSES = {"important": ("fixed", "withdrawn", "standing"),
             "edited": ("fixed", "withdrawn", "standing"),
             "question": ("answered", "standing")}
_WRITER_ROLES = frozenset({"admin", "maintain", "write"})
_BOTS = frozenset({"github-actions[bot]"})


class Refused(Exception):
    """The input cannot make a review: nothing is posted."""


def load_writers(path: Path | None) -> frozenset[str]:
    """The logins with `admin`, `maintain` or `write`, from the MCP's answer.

    The file holds one `list_repository_collaborators` page, or a JSON list of
    every page in order. A missing, unreadable, partial or writer-less list is
    refused: it is never read as everyone, nor as no one.
    """
    if path is None:
        raise Refused("no --writers file: read list_repository_collaborators first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"the writers file {path} cannot be read: {exc}") from exc
    pages = data if isinstance(data, list) else [data]
    writers: set[str] = set()
    for number, page in enumerate(pages, 1):
        items = page.get("items") if isinstance(page, dict) else None
        if not isinstance(items, list):
            raise Refused(f"the writers file's page {number} is not the MCP's answer")
        more = page.get("nextPage") not in (None, 0)
        if more != (number < len(pages)):
            raise Refused("the writers file is not every page of the list, in order")
        for item in items:
            login = item.get("login") if isinstance(item, dict) else None
            role = item.get("role_name") if isinstance(item, dict) else None
            if not (isinstance(login, str) and login and isinstance(role, str)):
                raise Refused(f"the writers file holds {item!r}, not a collaborator")
            if role in _WRITER_ROLES:
                writers.add(login)
    if not writers:
        raise Refused("the writers file names no admin, maintain or write account")
    return frozenset(writers)


def _login(record: dict) -> str:
    """The author's login; "" for a deleted account, which counts for no one."""
    user = record.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    return login if isinstance(login, str) else ""


def _edited(comment: dict) -> bool:
    """Whether a comment was edited after it was posted, by `review_checks`'
    `is_edited`; raises on one with no times, which the decision cannot read."""
    made, updated = comment.get("created_at"), comment.get("updated_at")
    if not (isinstance(made, str) and isinstance(updated, str)):
        raise ValueError(f"{comment.get('id')!r} has no created_at or updated_at")
    return checks.is_edited(made, updated)


def resolve_gh() -> str:
    """`gh`'s absolute path, refused when it lives inside a git checkout: a PR's
    tree could put its own `gh` first on `PATH`. Read from the files, not from
    `git`, which `PATH` could replace too."""
    found = shutil.which("gh")
    if found is None:
        raise Refused("no gh on PATH")
    real = Path(os.path.realpath(found))
    inside = next((d for d in real.parents if (d / ".git").exists()), None)
    if inside is not None:
        raise Refused(f"gh resolves to {real}, inside the checkout {inside}")
    return str(real)


def _text(first: str, rest: list[str]) -> str:
    """A finding's text: its first line and the lines after it, up to a blank
    line, the next marker or a founder question. The poster refuses a blank line
    in a text, so this
    reads back what it wrote."""
    out = [first.strip()]
    for line in rest:
        if not line.strip() or _MARKER_RE.match(line) or _QUESTION_RE.match(line):
            break
        out.append(line.strip())
    return " ".join(out)


def _marked(text: str) -> bool:
    """Whether a line of `text` opens with a marker or a founder question, which a
    later read would take for an entry of its own."""
    return any(_MARKER_RE.match(line) or _QUESTION_RE.match(line)
               for line in text.splitlines())


@dataclass(frozen=True)
class Earlier:
    """An earlier entry a later review accounts for: an `Important:` finding, an
    edit to a writer's review or finding, or a founder question."""

    id: str
    kind: str  # "important", "edited" or "question"
    login: str
    when: str
    commit: str
    where: str
    text: str
    comment: int | None = None  # the inline comment whose thread a fix resolves


def earlier_findings(reviews: list[object], comments: list[object], *,
                     writers: frozenset[str],
                     edits: dict[int, tuple[str, str]] | None = None) -> list[Earlier]:
    """Every earlier entry by a writer, from `GET pulls/{n}/reviews` and
    `GET pulls/{n}/comments` (outdated ones included), whatever the base did.

    A review's body is the record of its findings; both it and an inline comment
    are read line by line. An inline `Important:` that repeats one of its
    review's (same line and text) is that finding, and a fix resolves its thread;
    one its review's body lacks is a finding of its own (`comment:<id>#<k>`).
    A founder question is one in either (`question:<review id>#<k>`,
    `question:comment:<id>#<k>`). An edited review (`edits`, from
    `review_checks.review_edits`) or an edited thread's first comment is an entry too: what
    it said before is gone, so it stands until a review marks it. A pending
    review (no `submitted_at`) and its comments, which nobody else can read, do
    not count.
    """
    found: list[Earlier] = []
    states: dict[int, str] = {}
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get("id"), int):
            raise ValueError(f"not a review: {review!r}")
        rid, login, when = review["id"], _login(review), review.get("submitted_at")
        states[rid] = str(review.get("state"))
        if login not in writers or not isinstance(when, str):
            continue
        commit = str(review.get("commit_id"))
        lines = (review.get("body") or "").splitlines()
        k = q = 0
        for i, line in enumerate(lines):
            mark, question = _MARKER_RE.match(line), _QUESTION_RE.match(line)
            if mark and mark.group(1) == "Important":
                k += 1
                text = _text(mark.group(2), lines[i + 1:])
                where = _WHERE_RE.match(text)
                found.append(Earlier(
                    f"review:{rid}#{k}", "important", login, when, commit,
                    f"{where['path']}:{where['line']}" if where else "",
                    where["text"] if where else text))
            elif question:
                q += 1
                found.append(Earlier(f"question:{rid}#{q}", "question", login, when,
                                     commit, "", question.group(1).strip()))
        if rid in (edits or {}):
            found.append(Earlier(f"review:{rid}:edited", "edited", login, edits[rid][1],
                                 commit, "", "this review was edited after it was posted"))
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get("id"), int):
            raise ValueError(f"not a review comment: {comment!r}")
        cid, login = comment["id"], _login(comment)
        rid = comment.get("pull_request_review_id")
        when = comment.get("created_at")
        if (login not in writers or states.get(rid) == "PENDING"
                or comment.get("in_reply_to_id") is not None or not isinstance(when, str)):
            continue
        where = f"{comment.get('path')}:{comment.get('original_line')}"
        commit = str(comment.get("original_commit_id") or comment.get("commit_id"))
        body = (comment.get("body") or "").splitlines()
        if _edited(comment):
            found.append(Earlier(f"comment:{cid}:edited", "edited", login,
                                 str(comment["updated_at"]), commit, where,
                                 "this finding was edited: " + _text(body[0] if body else "",
                                                                     body[1:])))
            continue
        k = q = 0
        for i, line in enumerate(body):
            mark, question = _MARKER_RE.match(line), _QUESTION_RE.match(line)
            if question:
                q += 1
                found.append(Earlier(f"question:comment:{cid}#{q}", "question", login,
                                     when, commit, where, question.group(1).strip()))
            if not mark or mark.group(1) != "Important":
                continue
            k += 1
            text = _text(mark.group(2), body[i + 1:])
            twin = next((n for n, f in enumerate(found)
                         if f.id.startswith(f"review:{rid}#") and f.where == where
                         and f.text == text and f.comment is None), None)
            if twin is not None:
                found[twin] = replace(found[twin], comment=cid)
            else:
                found.append(Earlier(f"comment:{cid}#{k}", "important", login, when,
                                     commit, where, text, cid))
    return sorted(found, key=lambda f: (f.when, f.id))


def pr_comments(comments: list[object], *,
                writers: frozenset[str]) -> tuple[list[dict], list[dict]]:
    """The PR's comments (`GET issues/{n}/comments`): the writers', where a founder
    answer can be, and the repo's bots', which never answer for him. Anyone
    else's is left out."""
    by_writers: list[dict] = []
    by_bots: list[dict] = []
    for comment in comments:
        if not isinstance(comment, dict):
            raise ValueError(f"not a comment: {comment!r}")
        login = _login(comment)
        if login in writers:
            by_writers.append(comment)
        elif login in _BOTS:
            by_bots.append(comment)
    return by_writers, by_bots


def commentable(files: list[object]) -> dict[str, set[int]]:
    """The new-side lines each file's patch shows (`GET pulls/{n}/files`): where
    GitHub takes an inline comment. A file with no patch (binary, too large)
    takes none."""
    lines: dict[str, set[int]] = {}
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
            raise ValueError(f"not a PR file: {entry!r}")
        shown = lines.setdefault(entry["filename"], set())
        number = 0
        for line in (entry.get("patch") or "").splitlines():
            hunk = _HUNK_RE.match(line)
            if hunk:
                number = int(hunk.group(1))
            elif number and not line.startswith(("-", "\\")):
                shown.add(number)
                number += 1
    return lines


@dataclass(frozen=True)
class Review:
    body: str
    comments: list[dict]
    fixed: list[int]  # earlier inline findings whose threads this review resolves


def build_review(spec: object, *, earlier: list[Earlier], lines: dict[str, set[int]],
                 answers: dict[str, str] | None = None) -> Review:
    """The review `spec` asks for, or `Refused` with every reason it cannot be.

    `answers` maps each writer's unedited comment, `issuecomment:<id>`, to when
    it was posted: a founder question is answered only by one posted after it.
    An edited one answers nothing: what it quoted before is gone.
    """
    if not isinstance(spec, dict):
        raise Refused("the review file is not a JSON object")
    problems: list[str] = []
    verdict = spec.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f"the verdict is {verdict!r}, not one of {list(VERDICTS)}")
    summary = spec.get("summary") or ""
    question = spec.get("founder_question") or ""
    if not (isinstance(summary, str) and isinstance(question, str)):
        raise Refused("summary and founder_question are text")
    if _marked(summary):
        problems.append("a summary line opens with a marker or a founder question")
    if "\n" in question.strip():
        problems.append("the founder question is one line")
    findings = spec.get("findings") or []
    if not isinstance(findings, list):
        raise Refused("findings is a list")
    inline: list[dict] = []
    recorded: list[str] = []
    severities: list[str] = []
    for n, finding in enumerate(findings, 1):
        severity = finding.get("severity") if isinstance(finding, dict) else None
        text = finding.get("text") if isinstance(finding, dict) else None
        if severity not in MARKERS or not isinstance(text, str) or not text.strip():
            problems.append(f"finding {n} needs a severity in {MARKERS} and its text")
            continue
        text = text.strip()
        if _marked(text) or any(not line.strip() for line in text.splitlines()):
            problems.append(f"finding {n}'s text is one paragraph with no marker line")
            continue
        severities.append(severity)
        path, line = finding.get("path"), finding.get("line")
        if line is not None and not (isinstance(path, str) and isinstance(line, int)
                                     and line > 0):
            problems.append(f"finding {n}'s line needs its path and a line number")
            continue
        where = f"`{path}:{line}` " if line else (f"`{path}` " if path else "")
        recorded.append(f"{severity}: {where}{text}")
        if isinstance(path, str) and line in lines.get(path, ()):
            inline.append({"path": path, "line": line, "side": "RIGHT",
                           "body": f"{severity}: {text}"})
    if severities.count("Nit") > _NITS:
        problems.append(f"{severities.count('Nit')} Nits, past REVIEW.md's {_NITS}")
    known = {f.id: f for f in earlier}
    accounted: dict[str, dict] = {}
    for entry in spec.get("earlier") or []:
        ident = entry.get("id") if isinstance(entry, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        note = str(entry.get("note") or "") if isinstance(entry, dict) else ""
        if not isinstance(ident, str) or ident not in known:
            problems.append(f"earlier {ident!r} is not an earlier entry `read` lists")
        elif ident in accounted:
            problems.append(f"earlier {ident} is listed twice")
        elif status not in _STATUSES[known[ident].kind]:
            problems.append(f"earlier {ident} needs a status in "
                            f"{_STATUSES[known[ident].kind]}")
        elif status == "withdrawn" and not note.strip():
            problems.append(f"withdrawing earlier {ident} needs its reason in note")
        elif _marked(note):
            problems.append(f"earlier {ident}'s note holds a marker line")
        elif status == "answered" and not ((answers or {}).get(str(entry.get("answer")), "")
                                           > known[ident].when):
            problems.append(f"earlier {ident}'s answer is not a writer's comment "
                            "posted after the question and never edited")
        else:
            accounted[ident] = entry
    problems += [f"earlier {f.id} ({f.where or f.commit[:9]}: {f.text[:80]}) is not "
                 f"listed as {' or '.join(_STATUSES[f.kind])}" for f in earlier
                 if f.id not in accounted and not any(f.id in p for p in problems)]
    stands = ("Important" in severities or question.strip()
              or any(e["status"] == "standing" for e in accounted.values()))
    if verdict == "APPROVE" and stands:
        problems.append("APPROVE while an Important finding or a founder question stands")
    if verdict == "BLOCK" and not stands:
        problems.append("BLOCK with no Important finding or founder question standing")
    if problems:
        raise Refused("; ".join(problems))
    parts = [str(verdict)]
    if summary.strip():
        parts.append(summary.strip())
    if question.strip():
        parts.append(f"Founder question: {question.strip()}")
    if recorded:
        parts.append("\n\n".join(recorded))
    if earlier:
        rows = []
        for f in earlier:
            entry = accounted[f.id]
            extra = str(entry.get("answer") if entry["status"] == "answered"
                        else entry.get("note") or "").strip()
            rows.append(f"- {entry['status']}: {f.id} ({f.where or f.commit[:9]}) "
                        f"{f.text[:120]}" + (f" -- {extra}" if extra else ""))
        parts.append("Earlier entries:\n\n" + "\n".join(rows))
    threads = sorted({f.comment for f in earlier if f.comment is not None})
    fixed = [c for c in threads if all(accounted[f.id]["status"] == "fixed"
                                       for f in earlier if f.comment == c)]
    return Review("\n\n".join(parts) + "\n", inline, fixed)


Get = Callable[[str], object]
Post = Callable[[str, dict | None], object]


def gh_client(gh: str) -> tuple[Get, Post]:
    """The reads and the write, through `gh` at its absolute path (`resolve_gh`).

    A read takes every page (`--paginate --slurp`) of a list; a failure fails the
    run, and the reviewer runs it again. A write is made once: a retried POST can
    post a review twice.
    """
    def get(path: str) -> object:
        done = subprocess.run([gh, "api", "--paginate", "--slurp", path],
                              capture_output=True, text=True, timeout=120, check=True)
        pages = json.loads(done.stdout)
        if isinstance(pages, list) and all(isinstance(p, list) for p in pages):
            return [item for page in pages for item in page]
        return pages[0] if isinstance(pages, list) and len(pages) == 1 else pages

    def post(path: str, payload: dict | None) -> object:
        done = subprocess.run([gh, "api", "-X", "POST", path, "--input", "-"],
                              input=json.dumps(payload or {}), capture_output=True,
                              text=True, timeout=120, check=True)
        return json.loads(done.stdout or "{}")

    return get, post


@dataclass(frozen=True)
class PullState:
    head: str
    earlier: list[Earlier]
    writer_comments: list[dict]
    bot_comments: list[dict]


def read_pull(get: Get, repo: str, pr: int, writers: frozenset[str]) -> PullState:
    pull = get(f"repos/{repo}/pulls/{pr}")
    head = pull.get("head", {}).get("sha") if isinstance(pull, dict) else None
    if not (isinstance(head, str) and _SHA_RE.fullmatch(head)):
        raise ValueError(f"PR {pr} has no head commit")
    edits = checks.review_edits(get(f"repos/{repo}/issues/{pr}/timeline?per_page=100"))
    earlier = earlier_findings(get(f"repos/{repo}/pulls/{pr}/reviews?per_page=100"),
                               get(f"repos/{repo}/pulls/{pr}/comments?per_page=100"),
                               writers=writers, edits=edits)
    by_writers, by_bots = pr_comments(get(f"repos/{repo}/issues/{pr}/comments?per_page=100"),
                                      writers=writers)
    return PullState(head, earlier, by_writers, by_bots)


def _show(state: PullState, writers: frozenset[str]) -> str:
    out = [f"head {state.head}; writers {', '.join(sorted(writers))}",
           "", "Earlier entries (list each in \"earlier\"):"]
    out += [f"  {f.id}  {f.login} {f.when} on {f.commit[:9]} {f.where}\n    {f.text}"
            for f in state.earlier] or ["  none"]
    for title, comments in (("Comments by writers (a founder answer counts only here):",
                             state.writer_comments),
                            ("Comments by the repo's bots (never a founder answer):",
                             state.bot_comments)):
        out += ["", title]
        out += [f"  issuecomment:{c.get('id')}  {_login(c)} {c.get('created_at')}\n"
                + "\n".join(f"    {line}" for line in (c.get("body") or "").splitlines())
                for c in comments] or ["  none"]
    return "\n".join(out)


def run(argv: list[str], *, get: Get | None = None, post: Post | None = None) -> int:
    """The command line; `get` and `post` are GitHub's, through `resolve_gh`'s `gh`
    unless given."""
    parser = argparse.ArgumentParser(prog="review_post.py")
    parser.add_argument("command", choices=("read", "post"))
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--writers", type=Path,
                        help="list_repository_collaborators' answer, saved as JSON")
    parser.add_argument("--commit", help="post: the commit you read")
    parser.add_argument("--review", type=Path, help="post: the review, as JSON")
    args = parser.parse_args(argv)
    try:
        writers = load_writers(args.writers)
        spec = None
        if args.command == "post":
            if not (args.commit and _SHA_RE.fullmatch(args.commit) and args.review):
                raise Refused("post needs --commit <40-char sha> and --review <file>")
            try:
                spec = json.loads(args.review.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise Refused(f"the review file cannot be read: {exc}") from exc
        if get is None or post is None:
            get, post = gh_client(resolve_gh())
    except Refused as exc:
        print(f"review_post: refused: {exc}", file=sys.stderr)
        return 2
    try:
        state = read_pull(get, args.repo, args.pr, writers)
        if args.command == "read":
            print(_show(state, writers))
            return 0
        if state.head != args.commit:
            print(f"review_post: PR {args.pr}'s head is {state.head}, not the commit you "
                  f"read ({args.commit}): nothing posted; review {state.head}",
                  file=sys.stderr)
            return 1
        answers = {f"issuecomment:{c.get('id')}": str(c.get("created_at"))
                   for c in state.writer_comments if not _edited(c)}
        review = build_review(spec, earlier=state.earlier, answers=answers, lines=commentable(
            get(f"repos/{args.repo}/pulls/{args.pr}/files?per_page=100")))
    except Refused as exc:
        print(f"review_post: refused: {exc}", file=sys.stderr)
        return 2
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        why = getattr(exc, "stderr", "") or exc
        print(f"review_post: cannot read PR {args.pr}: {why}", file=sys.stderr)
        return 1
    try:
        posted = post(f"repos/{args.repo}/pulls/{args.pr}/reviews",
                      {"commit_id": args.commit, "event": "COMMENT",
                       "body": review.body, "comments": review.comments})
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        why = getattr(exc, "stderr", "") or exc
        print(f"review_post: GitHub did not take the review: {why}", file=sys.stderr)
        return 1
    print(posted.get("html_url", "posted") if isinstance(posted, dict) else "posted")
    unresolved: list[str] = []
    for comment in review.fixed:
        try:
            post(f"repos/{args.repo}/pulls/{args.pr}/ccr/comments/{comment}/resolve", None)
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            unresolved.append(f"comment:{comment} ({getattr(exc, 'stderr', '') or exc})")
    if unresolved:
        print("review_post: posted, but these fixed threads are not resolved (resolve "
              "them with the GitHub MCP's resolve_review_thread): " + "; ".join(unresolved),
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
