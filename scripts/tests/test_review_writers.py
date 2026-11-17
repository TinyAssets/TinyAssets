"""A review verdict counts only from an account that can push to the repository, and a
permission that cannot be read is no verdict, never a pass."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import drain_review_gate as gate  # noqa: E402

pytestmark = pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review verdict is a check run only the review App posts"
)

A = "a" * 40
HEAD = "b" * 40


def _review(body: str, login: str, *, ident: int, commit: str = A) -> dict:
    return {"id": ident, "body": body, "commit_id": commit, "state": "COMMENTED",
            "submitted_at": f"2026-11-17T1{ident}:00:00Z", "user": {"login": login},
            "html_url": f"https://github.com/o/r/pull/1#pullrequestreview-{ident}"}


def _github(answers: dict[str, object]):
    """`_gh_json` as GitHub would answer the permission read; a login with no answer, or
    an exception for its answer, fails the read as `gh` does."""
    asked: list[str] = []

    def gh_json(path: str) -> object:
        asked.append(path)
        login = path.split("/")[4]
        answer = answers.get(login)
        if answer is None:
            raise subprocess.CalledProcessError(1, ["gh", "api", path],
                                                stderr="gh: Not Found (HTTP 404)\n")
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return gh_json, asked


def _check(monkeypatch, reviews: list[dict], answers: dict[str, object]) -> gate.CheckState:
    gh_json, _ = _github(answers)
    monkeypatch.setattr(gate, "_gh_json", gh_json)
    return gate.review_check(reviews, base="0" * 40, head=A, carry=lambda commit: "this head",
                             can_push=gate.push_reader("o/r"))


@pytest.mark.parametrize("permission, pushes", [
    ("admin", True), ("write", True), ("read", False), ("none", False)])
def test_admin_and_write_can_push_and_read_and_none_cannot(monkeypatch, permission, pushes):
    """GitHub reads maintain as `write` and triage as `read`, from every grant."""
    gh_json, asked = _github({"someone": {"permission": permission, "role_name": "x"}})
    monkeypatch.setattr(gate, "_gh_json", gh_json)
    assert gate.push_reader("o/r")("someone") is pushes
    assert asked == ["repos/o/r/collaborators/someone/permission"]


@pytest.mark.parametrize("answer, why", [
    (None, "HTTP 404"),
    (subprocess.TimeoutExpired(["gh"], 60), "timed out"),
    (ValueError("Expecting value"), "Expecting value"),
    ({"message": "Resource not accessible by integration"}, "None"),
    ({"permission": "maintain"}, "'maintain'"),
    ({"permission": "triage"}, "'triage'"),
    (["write"], "None"),
])
def test_a_permission_that_cannot_be_read_raises_its_reason(monkeypatch, answer, why):
    gh_json, _ = _github({"someone": answer} if answer is not None else {})
    monkeypatch.setattr(gate, "_gh_json", gh_json)
    with pytest.raises(gate.PermissionUnread, match="someone") as raised:
        gate.push_reader("o/r")("someone")
    assert why in str(raised.value)


def test_a_login_that_is_not_one_is_never_put_in_a_path(monkeypatch):
    gh_json, asked = _github({})
    monkeypatch.setattr(gate, "_gh_json", gh_json)
    for login in ("../../orgs/x", "a/b", "", "-a", "x" * 40):
        with pytest.raises(gate.PermissionUnread, match="not a GitHub login"):
            gate.push_reader("o/r")(login)
    assert asked == []


def test_a_verdict_by_an_account_that_cannot_push_is_an_ordinary_comment(monkeypatch):
    answers = {"writer": {"permission": "write"}, "reader": {"permission": "read"}}
    alone = _check(monkeypatch, [_review("APPROVE", "reader", ident=1)], answers)
    assert alone.conclusion is None and alone.title == f"waiting for review of {A}"
    after = [_review("APPROVE", "writer", ident=1), _review("BLOCK", "reader", ident=2)]
    assert _check(monkeypatch, after, answers).conclusion == "success"
    blocked = [_review("APPROVE", "writer", ident=1), _review("BLOCK", "writer", ident=2)]
    assert _check(monkeypatch, blocked, answers).conclusion == "failure"


@pytest.mark.parametrize("reviews", [
    [_review("APPROVE", "unknown", ident=1)],
    # The unread author may be a writer whose BLOCK decides: the older APPROVE stays void.
    [_review("APPROVE", "writer", ident=1), _review("BLOCK", "unknown", ident=2)],
])
def test_a_permission_that_cannot_be_read_is_no_verdict_and_says_why(monkeypatch, reviews):
    state = _check(monkeypatch, reviews, {"writer": {"permission": "write"}})
    assert state.conclusion is None
    assert state.title == "no verdict counts: could not read unknown's permission: " \
        "gh: Not Found (HTTP 404)"
    assert "can push" in state.summary and "Rerun review.yml" in state.summary
    group = gate.group_check(state, number=1, head=A)
    assert group.conclusion == "failure" and "unknown's permission" in group.title


def test_a_writers_newest_verdict_needs_no_older_authors_read(monkeypatch):
    """Asked newest first, once a login, stopping at the first writer: an older verdict's
    author, readable or not, cannot change the decision."""
    gh_json, asked = _github({"writer": {"permission": "write"}, "reader": {"permission": "read"}})
    monkeypatch.setattr(gate, "_gh_json", gh_json)
    reviews = [_review("BLOCK", "unknown", ident=1), _review("APPROVE", "writer", ident=2),
               _review("APPROVE", "reader", ident=3), _review("BLOCK", "reader", ident=4),
               _review("a question", "unknown", ident=5)]
    verdict = gate.latest_verdict(reviews, can_push=gate.push_reader("o/r"))
    assert verdict == gate.Verdict("APPROVE", A, reviews[1]["html_url"])
    assert asked == ["repos/o/r/collaborators/reader/permission",
                     "repos/o/r/collaborators/writer/permission"]
