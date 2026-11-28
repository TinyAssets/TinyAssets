#!/usr/bin/env python3
"""The reviewer's poster: one `COMMENT` review on the commit it read (`REVIEW.md`).

    review_post.py read --repo O/R --pr N --writers FILE
    review_post.py post --repo O/R --pr N --writers FILE --commit SHA --review FILE

`read` lists what a review must answer: the PR's earlier `Important:` findings
and its comments, each only from an account that counts. `post` builds the
review from the reviewer's JSON and posts it on `--commit`, which must be the
PR's head:

    {"verdict": "BLOCK",
     "summary": "What was read and how.",
     "findings": [{"severity": "Important", "path": "a.py", "line": 12,
                   "text": "The input, what happens, what should."},
                  {"severity": "Nit", "text": "A finding with no line."}],
     "earlier": [{"id": "comment:123", "status": "fixed", "note": "abc1234"}],
     "founder_question": "One sentence a non-engineer can answer."}

The verdict is the body's first line, which `review.yml` reads. A finding goes
inline when its line is in the PR's diff, else in the body with `path:line`.
Every earlier Important is listed as `fixed`, `withdrawn` (with its reason in
`note`) or `standing`; the verdict is `BLOCK` exactly when an Important finding
or a founder question stands. After posting, the threads of earlier inline
findings it lists as fixed are resolved. Exit 0 posted, 1 GitHub or the head
refused it, 2 the input is refused, 3 posted but a thread was not resolved.

Who counts (`REVIEW.md`): a writer from `--writers`, the GitHub MCP's
`list_repository_collaborators` result saved as JSON (a session's proxy refuses
GitHub's permission read), and `github-actions[bot]`, which posts the
queue-handoff ledger. A founder answer counts only from a writer.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_checks as checks  # noqa: E402

# The verdicts review.yml reads, and the commit shape it checks.
VERDICTS = checks._VERDICTS
_SHA_RE = checks._SHA_RE

MARKERS = ("Important", "Nit", "Pre-existing")
_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s+)?(Important|Nit|Pre-existing):\s*(.*)$")
_NITS = 5  # REVIEW.md: at most five a review
_STATUSES = ("fixed", "withdrawn", "standing")
_WRITER_ROLES = frozenset({"admin", "maintain", "write"})
_BOTS = frozenset({"github-actions[bot]"})
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


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
            if not (isinstance(login, str) and checks._LOGIN_RE.fullmatch(login)
                    and isinstance(role, str)):
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


def _text(first: str, rest: list[str]) -> str:
    """A finding's text: its first line and the lines after it, up to a blank
    line or the next marker."""
    out = [first.strip()]
    for line in rest:
        if not line.strip() or _MARKER_RE.match(line):
            break
        out.append(line.strip())
    return " ".join(out)


@dataclass(frozen=True)
class Earlier:
    """An earlier `Important:` finding a later review accounts for."""

    id: str
    login: str
    when: str
    commit: str
    where: str
    text: str
    comment: int | None  # the inline comment whose thread a fix resolves


def earlier_findings(reviews: list[object], comments: list[object], *,
                     writers: frozenset[str], since: str | None) -> list[Earlier]:
    """Every `Important:` in the PR's reviews and inline comments that counts.

    `reviews` is `GET pulls/{n}/reviews`, `comments` `GET pulls/{n}/comments`,
    outdated ones included. A finding counts from an account that counts, posted
    after `since`, the last base change; a pending review (no `submitted_at`)
    and its comments, which nobody else can read, do not count. A reply, a
    resolve or a dismissal decides nothing.
    """
    found: list[Earlier] = []
    states: dict[int, tuple[str, str | None]] = {}
    for review in reviews:
        if not isinstance(review, dict) or not isinstance(review.get("id"), int):
            raise ValueError(f"not a review: {review!r}")
        states[review["id"]] = (str(review.get("state")), review.get("submitted_at"))
        login, when = _login(review), review.get("submitted_at")
        if (not _counts(login, writers) or not isinstance(when, str)
                or (since is not None and when <= since)):
            continue
        lines = (review.get("body") or "").splitlines()
        k = 0
        for i, line in enumerate(lines):
            mark = _MARKER_RE.match(line)
            if mark and mark.group(1) == "Important":
                k += 1
                found.append(Earlier(f"review:{review['id']}#{k}", login, when,
                                     str(review.get("commit_id")), "",
                                     _text(mark.group(2), lines[i + 1:]), None))
    for comment in comments:
        if not isinstance(comment, dict) or not isinstance(comment.get("id"), int):
            raise ValueError(f"not a review comment: {comment!r}")
        state, submitted = states.get(comment.get("pull_request_review_id"), ("", None))
        when = submitted or comment.get("created_at")
        login, body = _login(comment), comment.get("body") or ""
        mark = _MARKER_RE.match(body.splitlines()[0] if body else "")
        if (state == "PENDING" or not mark or mark.group(1) != "Important"
                or not _counts(login, writers) or not isinstance(when, str)
                or (since is not None and when <= since)):
            continue
        line = comment.get("line")
        where = (f"{comment.get('path')}:{line}" if line is not None else
                 f"{comment.get('path')}:{comment.get('original_line')} (outdated)")
        found.append(Earlier(f"comment:{comment['id']}", login, when,
                             str(comment.get("original_commit_id") or comment.get("commit_id")),
                             where, _text(mark.group(2), body.splitlines()[1:]),
                             comment["id"]))
    return sorted(found, key=lambda f: (f.when, f.id))


def _counts(login: str, writers: frozenset[str]) -> bool:
    return login in writers or login in _BOTS


def pr_comments(comments: list[object], *, writers: frozenset[str],
                since: str | None) -> tuple[list[dict], list[dict]]:
    """The PR's comments (`GET issues/{n}/comments`) after `since`: the writers',
    where a founder answer can be, and the repo's bots', which never answer for
    him. Anyone else's is left out."""
    by_writers: list[dict] = []
    by_bots: list[dict] = []
    for comment in comments:
        if not isinstance(comment, dict):
            raise ValueError(f"not a comment: {comment!r}")
        when = comment.get("created_at")
        if since is not None and isinstance(when, str) and when <= since:
            continue
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


def _marked(text: str) -> bool:
    """Whether a line of `text` opens with a marker, which a later read would
    take for a finding of its own."""
    return any(_MARKER_RE.match(line) for line in text.splitlines())


@dataclass(frozen=True)
class Review:
    body: str
    comments: list[dict]
    fixed: list[int]  # earlier inline findings whose threads this review resolves


def build_review(spec: object, *, earlier: list[Earlier],
                 lines: dict[str, set[int]]) -> Review:
    """The review `spec` asks for, or `Refused` with every reason it cannot be."""
    if not isinstance(spec, dict):
        raise Refused("the review file is not a JSON object")
    problems: list[str] = []
    verdict = spec.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f"the verdict is {verdict!r}, not one of {sorted(VERDICTS)}")
    summary = spec.get("summary") or ""
    question = spec.get("founder_question") or ""
    if not (isinstance(summary, str) and isinstance(question, str)):
        raise Refused("summary and founder_question are text")
    if _marked(summary):
        problems.append("a summary line opens with a marker: put it in findings")
    if "\n" in question.strip():
        problems.append("the founder question is one line")
    findings = spec.get("findings") or []
    if not isinstance(findings, list):
        raise Refused("findings is a list")
    inline: list[dict] = []
    in_body: list[str] = []
    severities: list[str] = []
    for n, finding in enumerate(findings, 1):
        severity = finding.get("severity") if isinstance(finding, dict) else None
        text = finding.get("text") if isinstance(finding, dict) else None
        if severity not in MARKERS or not isinstance(text, str) or not text.strip():
            problems.append(f"finding {n} needs a severity in {MARKERS} and its text")
            continue
        text = text.strip()
        if _marked(text):
            problems.append(f"finding {n}'s text holds a marker line: one finding each")
            continue
        severities.append(severity)
        path, line = finding.get("path"), finding.get("line")
        if line is not None and not (isinstance(path, str) and isinstance(line, int)
                                     and line > 0):
            problems.append(f"finding {n}'s line needs its path and a line number")
            continue
        if isinstance(path, str) and line in lines.get(path, ()):
            inline.append({"path": path, "line": line, "side": "RIGHT",
                           "body": f"{severity}: {text}"})
        else:
            where = f"`{path}:{line}` " if line else (f"`{path}` " if path else "")
            in_body.append(f"{severity}: {where}{text}")
    if severities.count("Nit") > _NITS:
        problems.append(f"{severities.count('Nit')} Nits, past REVIEW.md's {_NITS}")
    accounted: dict[str, dict] = {}
    for entry in spec.get("earlier") or []:
        ident = entry.get("id") if isinstance(entry, dict) else None
        status = entry.get("status") if isinstance(entry, dict) else None
        if status not in _STATUSES or not isinstance(ident, str):
            problems.append(f"earlier entry {entry!r} needs an id and a status in {_STATUSES}")
        elif ident in accounted:
            problems.append(f"earlier {ident} is listed twice")
        elif status == "withdrawn" and not str(entry.get("note") or "").strip():
            problems.append(f"withdrawing earlier {ident} needs its reason in note")
        elif _marked(str(entry.get("note") or "")):
            problems.append(f"earlier {ident}'s note holds a marker line")
        else:
            accounted[ident] = entry
    known = {f.id: f for f in earlier}
    problems += [f"earlier {i} is not an earlier Important finding" for i in accounted
                 if i not in known]
    problems += [f"earlier {f.id} ({f.where or f.commit[:9]}: {f.text[:80]}) is not "
                 "listed as fixed, withdrawn or standing" for f in earlier
                 if f.id not in accounted]
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
    if in_body:
        parts.append("\n\n".join(in_body))
    if earlier:
        rows = []
        for f in earlier:
            entry = accounted[f.id]
            note = str(entry.get("note") or "").strip()
            rows.append(f"- {entry['status']}: {f.id} ({f.where or f.commit[:9]}) "
                        f"{f.text[:120]}" + (f" -- {note}" if note else ""))
        parts.append("Earlier Important findings:\n\n" + "\n".join(rows))
    fixed = [f.comment for f in earlier
             if f.comment is not None and accounted[f.id]["status"] == "fixed"]
    return Review("\n\n".join(parts) + "\n", inline, fixed)


Get = Callable[[str], object]
Post = Callable[[str, dict | None], object]


def _gh_get(path: str) -> object:
    """A read, every page (`--paginate --slurp`) for a list; 5xx retried."""
    text = checks._gh_read(["--paginate", "--slurp", path], 120)
    pages = json.loads(text)
    if isinstance(pages, list) and all(isinstance(p, list) for p in pages):
        return [item for page in pages for item in page]
    return pages[0] if isinstance(pages, list) and len(pages) == 1 else pages


def _gh_post(path: str, payload: dict | None) -> object:
    """A write, once: a retried POST can post a review twice."""
    done = subprocess.run(["gh", "api", "-X", "POST", path, "--input", "-"],
                          input=json.dumps(payload or {}), capture_output=True,
                          text=True, timeout=120, check=True)
    return json.loads(done.stdout or "{}")


@dataclass(frozen=True)
class PullState:
    head: str
    since: str | None
    earlier: list[Earlier]
    writer_comments: list[dict]
    bot_comments: list[dict]


def read_pull(get: Get, repo: str, pr: int, writers: frozenset[str]) -> PullState:
    pull = get(f"repos/{repo}/pulls/{pr}")
    head = pull.get("head", {}).get("sha") if isinstance(pull, dict) else None
    if not (isinstance(head, str) and _SHA_RE.fullmatch(head)):
        raise ValueError(f"PR {pr} has no head commit")
    since = checks.last_base_change(get(f"repos/{repo}/issues/{pr}/timeline?per_page=100"))
    earlier = earlier_findings(get(f"repos/{repo}/pulls/{pr}/reviews?per_page=100"),
                               get(f"repos/{repo}/pulls/{pr}/comments?per_page=100"),
                               writers=writers, since=since)
    by_writers, by_bots = pr_comments(get(f"repos/{repo}/issues/{pr}/comments?per_page=100"),
                                      writers=writers, since=since)
    return PullState(head, since, earlier, by_writers, by_bots)


def _show(state: PullState, writers: frozenset[str]) -> str:
    out = [f"head {state.head}; base last changed {state.since or 'never'}; "
           f"writers {', '.join(sorted(writers))}",
           "", "Earlier Important findings (list each in \"earlier\"):"]
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


def run(argv: list[str], *, get: Get = _gh_get, post: Post = _gh_post) -> int:
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
        review = build_review(spec, earlier=state.earlier, lines=commentable(
            get(f"repos/{args.repo}/pulls/{args.pr}/files?per_page=100")))
    except Refused as exc:
        print(f"review_post: refused: {exc}", file=sys.stderr)
        return 2
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"review_post: cannot read PR {args.pr}: {exc}", file=sys.stderr)
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
