"""The reviewer's poster: one COMMENT review on the commit read, its verdict first,
findings by marker, every earlier Important accounted for, and only the accounts
that count read. GitHub is a fake; nothing is posted."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import review_post as rp  # noqa: E402

REPO, PR = "o/r", 7
HEAD = "b" * 40
OLD = "a" * 40
WRITER, OUTSIDER, BOT = "writer", "outsider", "github-actions[bot]"
PATCH = ("@@ -1,2 +1,3 @@\n line one\n+line two\n line three\n"
         "@@ -10,1 +11,2 @@\n-gone\n+here\n+and here")


def _review(ident: int, body: str, login: str = WRITER, *, when: str = "2026-11-28T10:00:00Z",
            state: str = "COMMENTED", commit: str = OLD) -> dict:
    return {"id": ident, "body": body, "state": state, "submitted_at": when,
            "commit_id": commit, "user": {"login": login}}


def _comment(ident: int, body: str, login: str = WRITER, *, review: int | None = None,
             line: int | None = 2, when: str = "2026-11-28T10:00:00Z") -> dict:
    return {"id": ident, "body": body, "user": {"login": login}, "path": "a.py",
            "line": line, "original_line": 2, "commit_id": OLD, "original_commit_id": OLD,
            "pull_request_review_id": review, "created_at": when, "updated_at": when}


class GitHub:
    """A fake of the reads and writes `review_post` makes."""

    def __init__(self, *, reviews=(), comments=(), issue_comments=(), timeline=(),
                 head=HEAD, fail_post: str | None = None, checks=None):
        self.answers = {
            f"repos/{REPO}/pulls/{PR}": {"head": {"sha": head}},
            f"repos/{REPO}/commits/{head}/check-runs?per_page=100":
                checks if checks is not None else {"total_count": 0, "check_runs": []},
            f"repos/{REPO}/issues/{PR}/timeline?per_page=100": list(timeline),
            f"repos/{REPO}/pulls/{PR}/reviews?per_page=100": list(reviews),
            f"repos/{REPO}/pulls/{PR}/comments?per_page=100": list(comments),
            f"repos/{REPO}/issues/{PR}/comments?per_page=100": list(issue_comments),
            f"repos/{REPO}/pulls/{PR}/files?per_page=100": [
                {"filename": "a.py", "patch": PATCH}, {"filename": "img.png"}],
        }
        self.posts: list[tuple[str, dict | None]] = []
        self.fail_post = fail_post

    def get(self, path: str) -> object:
        return self.answers[path]

    def post(self, path: str, payload: dict | None) -> object:
        if self.fail_post and self.fail_post in path:
            raise OSError("HTTP 404")
        self.posts.append((path, payload))
        return {"html_url": "https://example.invalid/review"}


@pytest.fixture
def writers(tmp_path: Path) -> Path:
    path = tmp_path / "writers.json"
    path.write_text(json.dumps({"items": [
        {"login": WRITER, "id": 1, "role_name": "admin"},
        {"login": "reader", "id": 2, "role_name": "read"}], "nextPage": 0}))
    return path


def _post(tmp_path: Path, writers: Path, gh: GitHub, spec: dict, commit: str = HEAD) -> int:
    review = tmp_path / "review.json"
    review.write_text(json.dumps(spec))
    return rp.run(["post", "--repo", REPO, "--pr", str(PR), "--writers", str(writers),
                   "--commit", commit, "--review", str(review)], get=gh.get, post=gh.post)


def test_a_posted_review_is_one_comment_on_the_commit_read_its_verdict_first(
        tmp_path, writers):
    gh = GitHub()
    spec = {"verdict": "BLOCK", "summary": "Read the diff.", "findings": [
        {"severity": "Important", "path": "a.py", "line": 2, "text": "x is None crashes."},
        {"severity": "Nit", "path": "a.py", "line": 40, "text": "an unchanged line."},
        {"severity": "Pre-existing", "text": "no line at all."}]}
    assert _post(tmp_path, writers, gh, spec) == 0
    [(path, payload)] = gh.posts
    assert path == f"repos/{REPO}/pulls/{PR}/reviews"
    assert payload["commit_id"] == HEAD and payload["event"] == "COMMENT"
    assert payload["body"].splitlines()[0] == "BLOCK"
    assert payload["comments"] == [{"path": "a.py", "line": 2, "side": "RIGHT",
                                    "body": "Important: x is None crashes."}]
    assert "Important: `a.py:2` x is None crashes." in payload["body"]
    assert "Nit: `a.py:40` an unchanged line." in payload["body"]
    assert "Pre-existing: no line at all." in payload["body"]


def test_only_the_new_side_of_a_hunk_takes_an_inline_comment():
    lines = rp.commentable([{"filename": "a.py", "patch": PATCH}, {"filename": "img.png"}])
    assert lines == {"a.py": {1, 2, 3, 11, 12}, "img.png": set()}


@pytest.mark.parametrize("spec, why", [
    ({"verdict": "LGTM"}, "not one of"),
    ({"verdict": "APPROVE", "findings": [{"severity": "Important", "text": "t"}]},
     "APPROVE while"),
    ({"verdict": "APPROVE", "founder_question": "Ship it?"}, "APPROVE while"),
    ({"verdict": "BLOCK", "findings": [{"severity": "Nit", "text": "t"}]}, "BLOCK with no"),
    ({"verdict": "APPROVE", "findings": [{"severity": "Nit", "text": "t"}] * 6}, "6 Nits"),
    ({"verdict": "APPROVE", "findings": [{"severity": "Blocker", "text": "t"}]}, "severity"),
    ({"verdict": "APPROVE", "summary": "Important: hidden here"}, "opens with a marker"),
    ({"verdict": "BLOCK", "findings": [{"severity": "Important",
                                        "text": "one\nNit: and a second"}]}, "marker line"),
    ({"verdict": "BLOCK", "findings": [{"severity": "Important",
                                        "text": "first.\n\nthe failing case."}]},
     "one paragraph"),
    ({"verdict": "APPROVE", "summary": "ok\nFounder question: hidden?"}, "founder question"),
    ({"verdict": "APPROVE", "summary": "ok\n  - Founder question: ship it?"},
     "founder question"),
    ({"verdict": "APPROVE", "findings": [{"severity": "Nit", "text": 3}]}, "its text"),
])
def test_a_review_that_contradicts_itself_is_refused(tmp_path, writers, spec, why, capsys):
    gh = GitHub()
    assert _post(tmp_path, writers, gh, spec) == 2
    assert why in capsys.readouterr().err and gh.posts == []


def test_a_head_past_the_commit_read_fails_loudly_and_posts_nothing(tmp_path, writers, capsys):
    gh = GitHub(head="c" * 40)
    assert _post(tmp_path, writers, gh, {"verdict": "APPROVE"}) == 1
    assert f"head is {'c' * 40}, not the commit you read ({HEAD})" in capsys.readouterr().err
    assert gh.posts == []


@pytest.mark.parametrize("content", [
    None, "", "not json", "[]", '{"items": []}',
    '{"items": [{"login": "reader", "id": 2, "role_name": "read"}]}',
    '{"items": [{"login": "writer", "id": 1, "role_name": "admin"}], "nextPage": 2}',
    '{"message": "Bad credentials"}',
])
def test_a_missing_empty_partial_or_failed_writers_list_is_refused(
        tmp_path, content, capsys):
    path = tmp_path / "writers.json"
    if content is not None:
        path.write_text(content)
    gh = GitHub()
    assert rp.run(["read", "--repo", REPO, "--pr", str(PR), "--writers", str(path)],
                  get=gh.get, post=gh.post) == 2
    assert "refused" in capsys.readouterr().err
    assert rp.run(["read", "--repo", REPO, "--pr", str(PR)], get=gh.get, post=gh.post) == 2


def test_every_page_of_the_writers_list_is_read(tmp_path):
    path = tmp_path / "writers.json"
    path.write_text(json.dumps([
        {"items": [{"login": "a", "id": 1, "role_name": "write"}], "nextPage": 2},
        {"items": [{"login": "b", "id": 2, "role_name": "maintain"}], "nextPage": 0}]))
    assert rp.load_writers(path) == {"a", "b"}


def test_an_outsiders_finding_or_comment_is_not_listed():
    reviews = [_review(1, "BLOCK\n\nImportant: outsider says so", OUTSIDER),
               _review(2, "BLOCK\n\nImportant: the writer says so")]
    comments = [_comment(10, "Important: outsider inline", OUTSIDER, review=1)]
    found = rp.earlier_findings(reviews, comments, writers=frozenset({WRITER}))
    assert [f.id for f in found] == ["review:2#1"]
    by_writers, by_bots = rp.pr_comments(
        [{"id": 5, "user": {"login": OUTSIDER}, "body": "Founder (2026-11-28): yes",
          "created_at": "2026-11-28T11:00:00Z"}], writers=frozenset({WRITER}))
    assert by_writers == [] and by_bots == []


def test_an_outsider_cannot_withdraw_an_earlier_finding(tmp_path, writers, capsys):
    """Their review lists no finding, so the writer's Important must still be answered."""
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nImportant: a real bug"),
                         _review(2, "APPROVE\n\nwithdrawn, all fine", OUTSIDER,
                                 when="2026-11-28T11:00:00Z")])
    assert _post(tmp_path, writers, gh, {"verdict": "APPROVE"}) == 2
    assert "earlier review:1#1" in capsys.readouterr().err and gh.posts == []


def test_the_repos_bot_is_listed_apart_and_is_never_a_source_of_findings():
    """A PR's own workflow can post as the bot, so it can neither plant a finding nor
    answer for the founder."""
    answer = {"id": 6, "user": {"login": BOT}, "body": "Founder (2026-11-28): yes",
              "created_at": "2026-11-28T11:00:00Z"}
    by_writers, by_bots = rp.pr_comments([answer], writers=frozenset({WRITER}))
    assert by_writers == [] and by_bots == [answer]
    found = rp.earlier_findings([_review(3, "BLOCK\n\nImportant: planted", BOT)],
                                [_comment(6, "Important: planted inline", BOT)],
                                writers=frozenset({WRITER}))
    assert found == []


def test_a_base_change_keeps_every_earlier_entry(tmp_path, writers, capsys):
    """Only verdicts reset at a base change (review.yml); the code a finding names is
    still in the head, and a PR's own workflow can retarget it twice."""
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nImportant: in the body")],
                comments=[_comment(10, "Important: inline", review=1)],
                timeline=[{"event": "base_ref_changed", "created_at": "2026-11-28T10:30:00Z"}])
    assert _post(tmp_path, writers, gh, {"verdict": "APPROVE"}) == 2
    err = capsys.readouterr().err
    assert "earlier review:1#1" in err and "earlier comment:10#1" in err


def test_body_inline_and_outdated_findings_are_all_listed_and_pending_ones_are_not():
    reviews = [_review(1, "BLOCK\n\nImportant: first\ncontinued\n\nNit: small\n"
                          "- Important: second\n  - Founder question: indented?"),
               _review(2, "", state="PENDING", when=None)]
    comments = [_comment(10, "Important: inline", review=1),
                _comment(11, "Important: moved away", review=1, line=None),
                _comment(12, "Nit: not important", review=1),
                _comment(13, "Important: draft", review=2),
                {**_comment(14, "Important: a reply", review=1), "in_reply_to_id": 10},
                _comment(15, "Context first.\n\nImportant: x crashes\n- Important: y too",
                         review=1),
                _comment(16, "Context.\nFounder question: ship X?", review=1),
                {**_comment(17, "Founder question: in a reply?", review=1),
                 "in_reply_to_id": 10},
                {**_comment(18, "Important: was a Nit\nFounder question: after an edit?",
                            review=1), "updated_at": "2026-11-28T12:00:00Z"}]
    found = rp.earlier_findings(reviews, comments, writers=frozenset({WRITER}))
    assert [(f.id, f.where, f.text) for f in found] == [
        ("comment:10#1", "a.py:2", "inline"),
        ("comment:11#1", "a.py:2", "moved away"),
        ("comment:15#1", "a.py:2", "x crashes"),
        ("comment:15#2", "a.py:2", "y too"),
        ("question:1#1", "", "indented?"),
        ("question:comment:16#1", "a.py:2", "ship X?"),
        ("question:comment:17#1", "a.py:2", "in a reply?"),
        ("question:comment:18#1", "a.py:2", "after an edit?"),
        ("review:1#1", "", "first continued"),
        ("review:1#2", "", "second"),
        ("comment:18:edited", "a.py:2", "this finding was edited: Important: was a Nit")]


def test_an_inline_finding_is_recorded_in_its_body_so_deleting_it_changes_nothing(
        tmp_path, writers):
    first = GitHub()
    spec = {"verdict": "BLOCK", "findings": [
        {"severity": "Important", "path": "a.py", "line": 2, "text": "x crashes"}]}
    assert _post(tmp_path, writers, first, spec) == 0
    body = first.posts[0][1]["body"]
    assert "Important: `a.py:2` x crashes" in body
    kept = rp.earlier_findings([_review(1, body)], [], writers=frozenset({WRITER}))
    assert [(f.id, f.where, f.text, f.comment) for f in kept] == [
        ("review:1#1", "a.py:2", "x crashes", None)]
    both = rp.earlier_findings([_review(1, body)],
                               [_comment(10, "Important: x crashes", review=1)],
                               writers=frozenset({WRITER}))
    assert [(f.id, f.comment) for f in both] == [("review:1#1", 10)]


def test_an_edited_finding_or_review_stands_until_a_review_marks_it(tmp_path, writers, capsys):
    edited = {**_comment(5, "Nit: actually fine", review=1),
              "updated_at": "2026-11-28T12:00:00Z"}
    slack = {**_comment(6, "Important: stamped a second late", review=1),
             "updated_at": "2026-11-28T10:00:01Z"}
    gh = GitHub(reviews=[_review(1, "BLOCK")], comments=[edited, slack],
                timeline=[{"event": "reviewed", "id": 1, "user": {"login": WRITER},
                           "submitted_at": "2026-11-28T10:00:00Z",
                           "updated_at": "2026-11-28T11:00:00Z"}])
    assert _post(tmp_path, writers, gh, {"verdict": "APPROVE"}) == 2
    err = capsys.readouterr().err
    assert "earlier comment:5:edited" in err and "earlier review:1:edited" in err
    assert "earlier comment:6#1 " in err
    spec = {"verdict": "BLOCK", "earlier": [
        {"id": "comment:5:edited", "status": "standing"},
        {"id": "review:1:edited", "status": "withdrawn", "note": "a typo fixed"},
        {"id": "comment:6#1", "status": "standing"}]}
    assert _post(tmp_path, writers, gh, spec) == 0


def test_an_earlier_founder_question_stands_until_a_writers_answer_after_it(
        tmp_path, writers, capsys):
    def at(ident, login, when, updated=None):
        return {"id": ident, "user": {"login": login}, "body": "Founder (2026-11-28): yes",
                "created_at": when, "updated_at": updated or when}
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nFounder question: ship X?",
                                 when="2026-11-28T10:00:00Z")],
                issue_comments=[at(4, WRITER, "2026-11-28T09:00:00Z"),
                                at(5, BOT, "2026-11-28T11:00:00Z"),
                                at(6, WRITER, "2026-11-28T11:00:00Z"),
                                at(7, WRITER, "2026-11-28T11:00:00Z", "2026-11-28T15:00:00Z")])
    assert _post(tmp_path, writers, gh, {"verdict": "APPROVE", "summary": "delta fine"}) == 2
    assert "earlier question:1#1" in capsys.readouterr().err

    def answered(by: str) -> dict:
        return {"verdict": "APPROVE", "earlier": [
            {"id": "question:1#1", "status": "answered", "answer": by}]}
    for early_bot_or_edited in ("issuecomment:4", "issuecomment:5", "issuecomment:7"):
        assert _post(tmp_path, writers, gh, answered(early_bot_or_edited)) == 2
        assert "not a writer's comment posted after" in capsys.readouterr().err
    assert _post(tmp_path, writers, gh, {"verdict": "APPROVE", "earlier": [
        {"id": "question:1#1", "status": "standing"}]}) == 2
    assert _post(tmp_path, writers, gh, answered("issuecomment:6")) == 0
    assert "- answered: question:1#1" in gh.posts[0][1]["body"]


def test_every_founder_question_of_a_review_is_listed():
    """A review written by hand can ask more than one."""
    found = rp.earlier_findings([_review(1, "BLOCK\n\nFounder question: a?\n"
                                            "Founder question: b?")], [],
                                writers=frozenset({WRITER}))
    assert [(f.id, f.text) for f in found] == [("question:1#1", "a?"), ("question:1#2", "b?")]


def test_an_edit_is_read_by_review_checks_one_rule(writers):
    """The poster reads a review's edit and a comment's through review_checks, the
    rule review.yml applies: inside its slack nothing is edited, past it the edit is
    an entry, and a record with no times fails loudly."""
    def event(ident, updated):
        return {"event": "reviewed", "id": ident, "state": "commented",
                "user": {"login": WRITER}, "submitted_at": "2026-11-28T10:00:00Z",
                "updated_at": updated}
    gh = GitHub(reviews=[_review(1, "BLOCK"), _review(2, "BLOCK")],
                comments=[{**_comment(5, "Important: x"), "updated_at": "2026-11-28T10:00:09Z"},
                          {**_comment(6, "Important: y"), "updated_at": "2026-11-28T10:00:11Z"}],
                timeline=[event(1, "2026-11-28T10:00:09Z"), event(2, "2026-11-28T10:00:11Z")])
    ids = [f.id for f in rp.read_pull(gh.get, REPO, PR, rp.load_writers(writers)).earlier]
    assert sorted(ids) == ["comment:5#1", "comment:6:edited", "review:2:edited"]
    with pytest.raises(ValueError):
        rp.earlier_findings([], [{**_comment(7, "Important: z"), "updated_at": None}],
                            writers=frozenset({WRITER}))


def test_a_gh_inside_a_checkout_is_refused(tmp_path, monkeypatch, writers, capsys):
    """A PR's tree could put its own `gh` first on PATH; the real one is outside."""
    def gh_in(folder: Path) -> Path:
        (folder / "bin").mkdir(parents=True)
        gh = folder / "bin" / "gh"
        gh.write_text("#!/bin/sh\necho '[[]]'\n")
        gh.chmod(0o755)
        return gh
    planted = gh_in(tmp_path / "head")
    (tmp_path / "head" / ".git").write_text("gitdir: elsewhere\n")
    monkeypatch.setenv("PATH", str(planted.parent))
    with pytest.raises(rp.Refused, match="inside the checkout"):
        rp.resolve_tool("gh")
    assert rp.run(["read", "--repo", REPO, "--pr", str(PR), "--writers", str(writers)]) == 2
    assert "inside the checkout" in capsys.readouterr().err
    real = gh_in(tmp_path / "tools")
    monkeypatch.setenv("PATH", str(real.parent))
    assert rp.resolve_tool("gh") == str(real)


def test_every_earlier_important_is_accounted_for_and_fixed_threads_resolved(
        tmp_path, writers, capsys):
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nImportant: in the body")],
                comments=[_comment(10, "Important: inline", review=1),
                          _comment(11, "Important: also inline", review=1)])
    base = {"verdict": "APPROVE", "earlier": [
        {"id": "review:1#1", "status": "withdrawn", "note": "the caller checks it"},
        {"id": "comment:10#1", "status": "fixed", "note": "abc1234"}]}
    assert _post(tmp_path, writers, gh, base) == 2
    assert "earlier comment:11#1" in capsys.readouterr().err
    standing = {**base, "earlier": [*base["earlier"],
                                    {"id": "comment:11#1", "status": "standing"}]}
    assert _post(tmp_path, writers, gh, standing) == 2
    blocked = {**standing, "verdict": "BLOCK"}
    assert _post(tmp_path, writers, gh, blocked) == 0
    (_, payload), resolve = gh.posts
    assert payload["body"].splitlines()[0] == "BLOCK"
    assert "- withdrawn: review:1#1" in payload["body"]
    assert "- standing: comment:11#1 (a.py:2) also inline" in payload["body"]
    assert resolve == (f"repos/{REPO}/pulls/{PR}/ccr/comments/10/resolve", None)


def test_a_thread_is_resolved_once_and_only_when_every_finding_in_it_is_fixed(
        tmp_path, writers):
    gh = GitHub(comments=[_comment(10, "Important: one\n\nImportant: two")])

    def spec(second: str) -> dict:
        return {"verdict": "BLOCK" if second == "standing" else "APPROVE", "earlier": [
            {"id": "comment:10#1", "status": "fixed"},
            {"id": "comment:10#2", "status": second}]}
    assert _post(tmp_path, writers, gh, spec("standing")) == 0
    assert _post(tmp_path, writers, gh, spec("fixed")) == 0
    assert [path for path, _ in gh.posts].count(
        f"repos/{REPO}/pulls/{PR}/ccr/comments/10/resolve") == 1


def test_an_earlier_finding_listed_twice_or_unknown_is_refused(tmp_path, writers, capsys):
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nImportant: in the body")])
    spec = {"verdict": "BLOCK", "earlier": [{"id": "review:1#1", "status": "standing"},
                                            {"id": "review:1#1", "status": "fixed"}]}
    assert _post(tmp_path, writers, gh, spec) == 2
    assert "listed twice" in capsys.readouterr().err
    spec["earlier"][1]["id"] = "review:9#1"
    assert _post(tmp_path, writers, gh, spec) == 2
    assert "not an earlier entry" in capsys.readouterr().err
    spec = {"verdict": "BLOCK", "earlier": [{"id": "review:1#1", "status": "answered"}]}
    assert _post(tmp_path, writers, gh, spec) == 2
    assert "needs a status in" in capsys.readouterr().err


def test_withdrawing_needs_a_reason(tmp_path, writers, capsys):
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nImportant: in the body")])
    spec = {"verdict": "APPROVE", "earlier": [{"id": "review:1#1", "status": "withdrawn"}]}
    assert _post(tmp_path, writers, gh, spec) == 2
    assert "needs its reason" in capsys.readouterr().err
    spec["earlier"][0]["note"] = "the caller checks it\nImportant: a new one"
    assert _post(tmp_path, writers, gh, spec) == 2
    assert "note holds a marker line" in capsys.readouterr().err


def test_a_founder_question_blocks_on_its_own_line(tmp_path, writers):
    gh = GitHub()
    spec = {"verdict": "BLOCK", "founder_question": "Should reviews also run at night?"}
    assert _post(tmp_path, writers, gh, spec) == 0
    lines = gh.posts[0][1]["body"].splitlines()
    assert lines[0] == "BLOCK"
    assert "Founder question: Should reviews also run at night?" in lines


@pytest.mark.parametrize("verdict", rp.VERDICTS)
def test_a_posted_verdict_is_the_one_review_checks_reads(tmp_path, writers, verdict):
    """Read through review_checks' public decision, as review.yml reads it."""
    import review_checks
    gh = GitHub()
    spec = ({"verdict": verdict} if verdict == "APPROVE"
            else {"verdict": verdict, "founder_question": "Ship it?"})
    assert _post(tmp_path, writers, gh, {**spec, "summary": "Read it."}) == 0
    review = {"id": 9, "state": "COMMENTED", "body": gh.posts[0][1]["body"],
              "commit_id": HEAD, "submitted_at": "2026-11-28T11:00:00Z",
              "user": {"login": WRITER}}
    [read] = review_checks.standing_verdicts([review], head=HEAD,
                                             in_line=lambda older, newer: older == newer,
                                             can_push=lambda login: True)
    assert (read.verdict, read.commit) == (verdict, HEAD)


def test_a_posted_review_whose_thread_will_not_resolve_says_so(tmp_path, writers, capsys):
    gh = GitHub(comments=[_comment(10, "Important: inline")], fail_post="/resolve")
    spec = {"verdict": "APPROVE", "earlier": [{"id": "comment:10#1", "status": "fixed"}]}
    assert _post(tmp_path, writers, gh, spec) == 3
    assert len(gh.posts) == 1 and "comment:10 " in capsys.readouterr().err


def test_read_lists_what_the_review_must_answer(writers, capsys):
    """Every page of the head's check runs too: a reviewer runs no gh of its own."""
    def page(name):
        return {"total_count": 2, "check_runs": [
            {"name": name, "status": "completed", "conclusion": "success",
             "app": {"slug": "github-actions"}}]}
    gh = GitHub(reviews=[_review(1, "BLOCK\n\nImportant: a bug")],
                issue_comments=[{"id": 5, "user": {"login": WRITER}, "body": "Founder: yes",
                                 "created_at": "2026-11-28T11:00:00Z"}],
                checks=[page("tests"), page("invariants")])
    assert rp.run(["read", "--repo", REPO, "--pr", str(PR), "--writers", str(writers)],
                  get=gh.get, post=gh.post) == 0
    out = capsys.readouterr().out
    assert "review:1#1" in out and "issuecomment:5" in out and "Founder: yes" in out
    assert "tests: completed/success (github-actions)" in out
    assert "invariants: completed/success (github-actions)" in out
    assert gh.posts == []
    with pytest.raises(ValueError):
        rp.check_runs({"message": "Not Found"})
