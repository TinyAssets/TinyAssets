"""The `review` check: a PR review on a commit, carried to later heads only by `carries`."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import review_checks as gate

# The checkout's root, wherever the file sits.
_ROOT = next(at for at in Path(__file__).resolve().parents if (at / "AGENTS.md").is_file())
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
A = "a" * 40
B = "b" * 40


def _review(body: str, commit: str = A, *, ident: int = 1, when: str = "2026-11-04T10:00:00Z",
            login: str = "writer", state: str = "COMMENTED") -> dict:
    return {"id": ident, "body": body, "commit_id": commit, "submitted_at": when,
            "user": {"login": login}, "state": state,
            "html_url": f"https://github.com/o/r/pull/1#pullrequestreview-{ident}"}


def _writer(login: str) -> bool:
    """`can_push`: only `writer` can (GitHub's read: scripts/tests/test_review_writers.py)."""
    return login == "writer"


def _line(older: str, newer: str) -> bool:
    """`in_line` for a PR whose history is A then B (real git: the cases below)."""
    return older == newer or (older, newer) == (A, B)


def _standing(reviews, **kwargs):
    return gate.standing_verdicts(reviews, head=B, in_line=_line, can_push=_writer, **kwargs)


def _latest(reviews, **kwargs):
    """The newest standing verdict, or None."""
    return next(iter(_standing(reviews, **kwargs)), None)


def test_a_newer_verdict_on_the_same_commit_or_a_descendant_answers_an_older_one():
    approve = _review("APPROVE\n\nread it all", ident=1)
    block = _review("BLOCK\n\nthe lock is wrong", ident=2, when="2026-11-04T11:00:00Z")
    assert _standing([approve]) == [gate.Verdict("APPROVE", A, approve["html_url"])]
    assert _standing([block, approve]) == [gate.Verdict("BLOCK", A, block["html_url"])]
    later = _review("APPROVE", B, ident=3, when="2026-11-04T12:00:00Z")
    assert _standing([approve, block, later]) == [gate.Verdict(
        "APPROVE", B, later["html_url"])]
    # A newer verdict on an ancestor answers nothing: both stand.
    late = _review("BLOCK", A, ident=4, when="2026-11-04T13:00:00Z")
    assert [v.commit for v in _standing([later, late])] == [A, B]


@pytest.mark.parametrize("review", [
    _review("APPROVE", login="reader"),
    _review("APPROVE", state="DISMISSED"),
    _review("APPROVE", state="PENDING"),
    _review("approve"),
    _review("APPROVE."),
    _review("APPROVE with nits"),
    _review("Drain-Review-Verdict: APPROVE"),
    _review("looks fine\nAPPROVE"),
    _review(""),
])
def test_what_is_not_a_verdict(review):
    assert _latest([review]) is None


def test_leading_whitespace_before_the_verdict_is_accepted():
    assert _latest([_review("\n\n   APPROVE  \n\nevidence")]).verdict == "APPROVE"


def test_an_ordinary_review_after_a_verdict_changes_nothing():
    approve = _review("APPROVE", ident=1)
    chat = _review("one question about line 4", ident=2, when="2026-11-04T11:00:00Z")
    assert _latest([approve, chat]).verdict == "APPROVE"


def test_a_dismissal_voids_every_verdict_at_or_before_it():
    approve = _review("APPROVE", ident=1)
    block = _review("BLOCK", ident=2, when="2026-11-04T11:00:00Z", state="DISMISSED")
    assert _latest([approve, block]) is None
    assert gate.review_check([approve, block], base="0" * 40, head=A, can_push=_writer,
                             in_line=_line, carry=lambda commit: "this head").conclusion is None
    # A dismissed ordinary comment, or one by anyone, voids just the same.
    chat = _review("why?", ident=2, when="2026-11-04T11:00:00Z", state="DISMISSED",
                   login="reader")
    assert _latest([approve, chat]) is None
    later = _review("APPROVE", B, ident=3, when="2026-11-04T12:00:00Z")
    assert _latest([approve, block, later]).commit == B
    with pytest.raises(ValueError):
        _latest([approve, {"state": "DISMISSED", "id": 2}])


@pytest.mark.parametrize("review", [
    "not a review",
    {"id": 1, "body": "APPROVE", "user": {"login": "writer"}, "state": "COMMENTED",
     "submitted_at": "2026-11-04T10:00:00Z", "commit_id": "short"},
    {"id": 1, "body": "APPROVE", "state": "COMMENTED"},
    {"id": 1, "body": "APPROVE", "state": "COMMENTED", "commit_id": A,
     "submitted_at": "2026-11-04T10:00:00Z", "user": None},
    {"id": 1, "body": "APPROVE", "state": "COMMENTED", "commit_id": A,
     "submitted_at": "2026-11-04T10:00:00Z", "user": "writer"},
])
def test_an_unreadable_review_list_refuses(review):
    with pytest.raises(ValueError):
        _latest([review])


# ---- carries(), on real throwaway repositories ----------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], message: str = "c") -> str:
    for name, text in files.items():
        path = repo / name
        if text is None:
            _git(repo, "rm", "-q", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path):
    """main has code and docs; the PR's reviewed commit A edits the code."""
    _git(tmp_path, "init", "-q", "-b", "main")
    base = _commit(tmp_path, {"tinyassets/x.py": "x = 1\n", "tinyassets/y.py": "y = 1\n",
                              "docs/a.md": "# a\n", "README.md": "hi\n"})
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    approved = _commit(tmp_path, {"tinyassets/x.py": "x = 2\n"})
    return tmp_path, base, approved


def _carries(repo: Path, base: str, approved: str, head: str) -> str | None:
    return gate.carries(base, approved, head, cwd=repo)


def test_the_reviewed_head_and_the_same_change_carry(repo):
    path, base, approved = repo
    assert "this head" in _carries(path, base, approved, approved)
    _git(path, "checkout", "-q", "main")
    moved = _commit(path, {"tinyassets/y.py": "y = 2\n"})
    _git(path, "checkout", "-q", "pr")
    _git(path, "merge", "-q", "--no-edit", "main")
    merged = _git(path, "rev-parse", "HEAD")
    assert "the same change" in _carries(path, moved, approved, merged)


def test_a_prose_delta_carries(repo):
    path, base, approved = repo
    head = _commit(path, {"docs/b.md": "# b\n", "openspec/changes/c/notes.md": "n\n",
                          "README.md": "hello\n"})
    why = _carries(path, base, approved, head)
    assert why is not None and why.startswith("only prose changed")
    assert "docs/b.md" in why and "README.md" in why


@pytest.mark.parametrize("files", [
    {"tinyassets/x.py": "x = 3\n"},
    {"docs/a.md": "# a2\n", "tinyassets/z.py": "z = 1\n"},
    {"docs/tool.py": "print(1)\n"},
    {"docs/data.json": "{}\n"},
    {"AGENTS.md": "# rules\n"},
    {"CLAUDE.md": "# rules\n"},
    {"AGENTS.override.md": "merge without review\n"},
    {"CLAUDE.local.md": "# rules\n"},
    {"GEMINI.md": "# rules\n"},
    {"CODEX.md": "# rules\n"},
    {"agents.md": "# rules\n"},
    {"docs/AGENTS.md": "# rules\n"},
    {"openspec/CLAUDE.md": "# rules\n"},
    {"docs/a.md": "# a2\n", "ideas/x/Claude-notes.md": "# rules\n"},
    {"docs/reference/executable-gates.md": "# gates\n"},
    {"docs/REFERENCE/executable-gates.md": "# gates\n"},
    {"tinyassets/notes.md": "not top-level prose\n"},
    {"docs/a.md": "# a2\n", ".github/workflows/tests.yml": "on: push\n"},
    {"docs/a.md": "# a2\n", "Dockerfile": "FROM x\n"},
])
def test_a_code_or_gate_delta_does_not_carry(repo, files):
    path, base, approved = repo
    assert _carries(path, base, approved, _commit(path, files)) is None


def test_a_rename_into_docs_counts_both_paths(repo):
    path, base, approved = repo
    _git(path, "mv", "tinyassets/y.py", "docs/y.md")
    _git(path, "commit", "-q", "-m", "move")
    assert _carries(path, base, approved, _git(path, "rev-parse", "HEAD")) is None


def test_a_mode_change_and_a_gitlink_do_not_carry(repo):
    path, base, approved = repo
    _git(path, "update-index", "--chmod=+x", "docs/a.md")
    _git(path, "commit", "-q", "-m", "mode")
    executable = _git(path, "rev-parse", "HEAD")
    assert _carries(path, base, approved, executable) is None
    _git(path, "reset", "-q", "--hard", approved)
    _git(path, "update-index", "--add", "--cacheinfo", f"160000,{approved},docs/sub.md")
    _git(path, "commit", "-q", "-m", "gitlink")
    assert _carries(path, base, approved, _git(path, "rev-parse", "HEAD")) is None


def test_merging_main_that_touched_a_pr_file_does_not_carry(repo):
    path, base, approved = repo
    _git(path, "checkout", "-q", "main")
    moved = _commit(path, {"tinyassets/x.py": "x = 1\n# main's note\n"})
    _git(path, "checkout", "-q", "pr")
    subprocess.run(["git", "merge", "-q", "--no-edit", "main"], cwd=path, env=_ENV,
                   capture_output=True)
    (path / "tinyassets" / "x.py").write_text("x = 2\n# main's note\n", encoding="utf-8")
    _git(path, "add", "tinyassets/x.py")
    _git(path, "commit", "-q", "--no-edit")
    assert _carries(path, moved, approved, _git(path, "rev-parse", "HEAD")) is None


def test_an_unreadable_commit_does_not_carry(repo):
    path, base, approved = repo
    assert _carries(path, base, approved, "c" * 40) is None
    assert _carries(path, base, "not-a-sha", approved) is None


# ---- the check run's state --------------------------------------------------------------


def _state(reviews, carry=lambda commit: None, head=B):
    return gate.review_check(reviews, base="0" * 40, head=head, carry=carry,
                             in_line=_line, can_push=_writer)


def test_no_review_is_pending_not_red():
    state = _state([])
    assert state.conclusion is None and state.title == f"waiting for review of {B}"


def test_an_approve_that_carries_passes_and_one_that_does_not_fails_naming_the_delta():
    """In progress, a delta after a review waited with no event (#5725, #5727)."""
    reviews = [_review("APPROVE", A)]
    assert _state(reviews, lambda commit: "only prose").conclusion == "success"
    owed = _state(reviews)
    assert owed.conclusion == "failure"
    assert owed.title == f"APPROVE at {A}; the change since needs a delta review"


def test_a_block_fails_where_it_carries_and_after_a_new_change_names_the_review():
    reviews = [_review("APPROVE", A, ident=1), _review("BLOCK", A, ident=2)]
    assert _state(reviews, lambda commit: "this head").title == "BLOCK: this head"
    owed = _state(reviews)
    assert owed.conclusion == "failure"
    assert owed.title == f"BLOCK at {A}; the change since needs a fresh review"


def _cli(path, reviews, timeline, base, head, *more, pr="12"):
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "review_checks.py"), "--review-check",
         "--reviews-file", str(reviews), "--timeline-file", str(timeline), "--pr", pr,
         "--repo", "o/r", "--base", base, "--head", head, *more],
        cwd=path, capture_output=True, text=True,
        env={**_ENV, **_fake_gh(path, {"check_runs": [],
                                       "permissions": {"writer": "write", "reader": "read"}})})


def test_the_cli_prints_the_check_run_for_its_pr(tmp_path, repo):
    path, base, approved = repo
    reviews = tmp_path / "reviews.json"
    timeline = tmp_path / "timeline.json"
    reviews.write_text(json.dumps([_review("APPROVE", approved)]), encoding="utf-8")
    timeline.write_text("[]", encoding="utf-8")
    run = json.loads(_cli(path, reviews, timeline, base, approved).stdout)
    assert run["name"] == "review" and run["head_sha"] == approved
    assert run["external_id"] == "pr-12"
    assert run["status"] == "completed" and run["conclusion"] == "success"
    reviews.write_text("{}", encoding="utf-8")
    assert _cli(path, reviews, timeline, base, approved).returncode == 2


# ---- which verdict decides, across heads: real commits, as review.yml decides -----------
#
# #5856's review of f83cc0f15 (I-1) named these; head H2 unless stated, t1 < t2 < t3.
# H1 is the PR's reviewed commit. H2 is H1 and an empty commit (the same change) or a
# code edit (a code delta). H1' is H1's change committed again on the base, as a
# force-push leaves it: the same change, not in H1's line. Each case runs as the PR's
# decision and as its merge group's, which follows it.

_T1, _T2, _T3 = "2026-11-04T10:00:00Z", "2026-11-04T11:00:00Z", "2026-11-04T12:00:00Z"


@pytest.fixture
def heads(repo):
    path, base, h1 = repo
    same = _commit(path, {}, "the same change")
    _git(path, "checkout", "-q", "-b", "code", h1)
    code = _commit(path, {"tinyassets/x.py": "x = 3\n"}, "a code delta")
    _git(path, "checkout", "-q", "--detach", base)
    again = _commit(path, {"tinyassets/x.py": "x = 2\n"}, "H1 force-pushed again")
    _git(path, "merge", "-q", "--no-edit", h1)
    both = _git(path, "rev-parse", "HEAD")
    return path, base, {"H1": h1, "same": same, "code": code, "H1'": again, "both": both}


@pytest.mark.parametrize(("head", "reviews", "decided"), [
    pytest.param("same", [("BLOCK", "H2", _T1), ("APPROVE", "H1", _T2)], "failure",
                 id="1 a late APPROVE of H1 leaves H2's BLOCK standing"),
    pytest.param("code", [("APPROVE", "H2", _T1), ("BLOCK", "H1", _T2)], "failure",
                 id="2 a late BLOCK of H1 stands over H2's APPROVE"),
    pytest.param("same", [("APPROVE", "H2", _T1), ("BLOCK", "H1", _T2)], "failure",
                 id="3 as 2, the same change"),
    pytest.param("code", [("BLOCK", "H1", _T1), ("APPROVE", "H2", _T2)], "success",
                 id="4 an APPROVE of H2 after H1's BLOCK"),
    pytest.param("same", [("BLOCK", "H1", _T1), ("APPROVE", "H2", _T2)], "success",
                 id="4 the same change"),
    pytest.param("same", [("APPROVE", "H1", _T1)], "success",
                 id="5 H1's APPROVE carries to the same change"),
    pytest.param("code", [("APPROVE", "H1", _T1)], "failure",
                 id="5 not to a code delta"),
    pytest.param("code", [("APPROVE", "H2", _T1), ("BLOCK", "H2", _T2)], "failure",
                 id="6 APPROVE then BLOCK on H2"),
    pytest.param("code", [("BLOCK", "H2", _T1), ("APPROVE", "H2", _T2)], "success",
                 id="6 BLOCK then APPROVE on H2"),
    pytest.param("code", [("APPROVE", "H2", _T1), ("APPROVE", "H1", _T2)], "success",
                 id="the most descendant commit decides, not the latest time"),
    pytest.param("same", [("APPROVE", "H2", _T1), ("BLOCK", "H1", _T2, "reader")], "success",
                 id="a late BLOCK by a reader counts for nothing"),
    pytest.param("same", [("APPROVE", "H2", _T1), ("DISMISSED", "H2", _T2)], "in_progress",
                 id="7 a dismissal voids the verdicts at or before it"),
    pytest.param("same", [("BLOCK", "H2", _T1), ("DISMISSED", "H1", _T2),
                          ("APPROVE", "H1", _T3)], "success",
                 id="7 a verdict after the dismissal counts"),
    pytest.param("H1'", [("APPROVE", "H1", _T1)], "in_progress",
                 id="8 an APPROVE of H1, force-pushed away, is ignored"),
    pytest.param("H1'", [("BLOCK", "H1", _T1), ("APPROVE", "H2", _T2)], "success",
                 id="8 H1's BLOCK is answered by a review of H1' after it"),
    pytest.param("H1'", [("APPROVE", "H2", _T1), ("BLOCK", "H1", _T2)], "in_progress",
                 id="8 a late BLOCK of H1 voids H1''s earlier APPROVE"),
    pytest.param("both", [("APPROVE", "H1", _T1), ("APPROVE", "H1'", _T2)], "failure",
                 id="two reviewed commits in no line need a review of the head"),
])
def test_the_decision_across_heads(tmp_path, heads, head, reviews, decided):
    path, base, commits = heads
    commits = {**commits, "H2": commits[head]}
    data = tmp_path / "reviews.json"
    data.write_text(json.dumps([
        _review("BLOCK" if verdict == "DISMISSED" else verdict, commits[on], ident=at,
                when=when, login=(who or ["writer"])[0],
                state="DISMISSED" if verdict == "DISMISSED" else "COMMENTED")
        for at, (verdict, on, when, *who) in enumerate(reviews, 1)]), encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text("[]", encoding="utf-8")
    for group in ((), ("--group-sha", B)):
        done = _cli(path, data, timeline, base, commits[head], *group)
        assert done.returncode == 0, done.stderr
        run = json.loads(done.stdout)
        want = decided if not group else "success" if decided == "success" else "failure"
        assert run.get("conclusion", run["status"]) == want, run["output"]["title"]


def test_in_line_is_git_ancestry_and_a_missing_head_decides_nothing(heads):
    path, base, c = heads

    def line(older: str, newer: str) -> bool:
        return gate.in_line(older, newer, cwd=path)

    assert line(c["H1"], c["H1"]) and line(c["H1"], c["same"]) and line(base, c["H1"])
    assert not line(c["same"], c["H1"]) and not line(c["H1"], c["H1'"])
    # Not here, with the head here: force-pushed away and not fetched.
    assert not line("c" * 40, c["H1"])
    for older, newer in ((c["H1"], "c" * 40), ("short", c["H1"])):
        with pytest.raises(ValueError):
            line(older, newer)


def test_a_review_before_the_base_changed_counts_for_nothing(tmp_path, repo):
    path, base, approved = repo
    reviews = tmp_path / "reviews.json"
    timeline = tmp_path / "timeline.json"
    reviews.write_text(json.dumps([_review("APPROVE", approved, when="2026-11-04T10:00:00Z")]),
                       encoding="utf-8")
    timeline.write_text(json.dumps([
        {"event": "commented", "created_at": "2026-11-04T12:00:00Z"},
        {"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"}]), encoding="utf-8")
    run = json.loads(_cli(path, reviews, timeline, base, approved).stdout)
    assert run["status"] == "in_progress"
    assert "the base changed at 2026-11-04T11:00:00Z" in run["output"]["title"]
    later = _review("APPROVE", ident=2, when="2026-11-04T11:30:00Z")
    assert _latest([later], since="2026-11-04T11:00:00Z").verdict == "APPROVE"


def test_the_last_base_change_is_read_from_the_timeline():
    assert gate.last_base_change([]) is None
    assert gate.last_base_change([
        {"event": "base_ref_changed", "created_at": "2026-11-04T09:00:00Z"},
        {"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"},
        {"event": "labeled", "created_at": "2026-11-04T12:00:00Z"}]) == "2026-11-04T11:00:00Z"
    with pytest.raises(ValueError):
        gate.last_base_change([{"event": "base_ref_changed"}])


def test_githubs_own_retarget_after_a_parent_merges_counts_as_a_base_change():
    automatic = {"event": "automatic_base_change_succeeded", "created_at": "2026-11-04T13:00:00Z"}
    assert gate.last_base_change([
        {"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"},
        automatic]) == "2026-11-04T13:00:00Z"
    assert gate.last_base_change([{"event": "automatic_base_change_failed",
                                   "created_at": "2026-11-04T14:00:00Z"}]) is None
    approve = _review("APPROVE", when="2026-11-04T12:00:00Z")
    assert _latest([approve], since=gate.last_base_change([automatic])) is None


# ---- the signal names only its own PR, and the App alone decides ----------------------


def test_a_signal_names_only_the_pr_whose_branch_it_ran_on():
    pull = {"head": {"ref": "lane-a", "repo": {"full_name": "o/r"}}}
    assert gate.signal_pr("12\n", pull, run_repo="o/r", run_branch="lane-a") == 12
    for text, run_repo, run_branch in [("12", "o/r", "lane-b"), ("12", "fork/r", "lane-a"),
                                       ("x", "o/r", "lane-a"), ("0", "o/r", "lane-a")]:
        with pytest.raises(ValueError):
            gate.signal_pr(text, pull, run_repo=run_repo, run_branch=run_branch)


def test_review_yml_decides_per_pr_with_the_app_in_its_environment():
    import yaml

    wf = yaml.safe_load((_ROOT / ".github" / "workflows" / "review.yml").read_text())
    on = wf[True] if True in wf else wf["on"]
    assert "edited" in on["pull_request_target"]["types"]
    decide = wf["jobs"]["decide"]
    assert decide["environment"] == "review"
    # The kind is in the group, so a merge group's decision never replaces a pending
    # decision of the PR's head (a BLOCK's), nor the reverse; no other group exists.
    assert decide["concurrency"]["group"] == (
        "review-${{ github.repository }}-${{ needs.resolve.outputs.kind }}"
        "-${{ needs.resolve.outputs.pr }}")
    assert decide["concurrency"]["cancel-in-progress"] is False
    assert "concurrency" not in wf
    assert all("concurrency" not in job for name, job in wf["jobs"].items() if name != "decide")
    for name in ("review-signal.yml", "review-queue.yml"):
        signal = yaml.safe_load((_ROOT / ".github" / "workflows" / name).read_text())
        assert "concurrency" not in signal
        assert all("concurrency" not in job for job in signal["jobs"].values())
    steps = "\n".join(str(step) for step in decide["steps"])
    assert "create-github-app-token" in steps and "REVIEW_APP_PRIVATE_KEY" in steps


# ---- the merge group ---------------------------------------------------------------------


def test_the_merge_group_ref_names_its_pr():
    assert gate.queued_pr(f"refs/heads/gh-readonly-queue/main/pr-5416-{A}") == 5416
    assert gate.queued_pr(f"gh-readonly-queue/main/pr-7-{A}") == 7
    for ref in ("refs/heads/main", f"gh-readonly-queue/main/pr-0-{A}",
                "gh-readonly-queue/main/pr-7-abc", f"x/gh-readonly-queue/main/pr-7-{A}"):
        assert gate.queued_pr(ref) is None


def test_a_merge_group_passes_only_on_a_fresh_pass():
    ok = gate.CheckState("success", "APPROVE: reviewed at this head", "u")
    assert gate.group_check(ok, number=12, head=A) == gate.CheckState(
        "success", f"PR #12 at {A}: APPROVE: reviewed at this head", "u")
    for state in (gate.CheckState("failure", "BLOCK: x", "u"),
                  gate.CheckState(None, f"waiting for review of {A}", "ask")):
        group = gate.group_check(state, number=12, head=A)
        assert group.conclusion == "failure" and state.title in group.title


def test_the_cli_prints_the_group_run_from_the_fresh_decision(tmp_path, repo):
    path, base, approved = repo
    reviews = tmp_path / "reviews.json"
    timeline = tmp_path / "timeline.json"
    timeline.write_text("[]", encoding="utf-8")
    for verdicts, conclusion in ((["APPROVE"], "success"), (["APPROVE", "BLOCK"], "failure"),
                                 ([], "failure")):
        reviews.write_text(json.dumps([
            _review(v, approved, ident=i, when=f"2026-11-04T1{i}:00:00Z")
            for i, v in enumerate(verdicts)]), encoding="utf-8")
        run = json.loads(_cli(path, reviews, timeline, base, approved,
                              "--group-sha", B).stdout)
        assert (run["head_sha"], run["external_id"]) == (B, "queue-pr-12")
        assert (run["status"], run["conclusion"]) == ("completed", conclusion)
    assert _cli(path, reviews, timeline, base, approved, "--group-sha", "x").returncode == 2


# ---- the App's token, and review.yml's decision run against a fake GitHub --------------

# The review App's repository permissions (docs/host-actions.md, "The review App"):
# review.yml may request no more than these.
_APP_PERMISSIONS = {"checks": "write", "metadata": "read"}
_ACTIONS_TOKEN = "actions-token"
_APP_TOKEN = "app-token"
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _review_workflow() -> dict:
    return yaml.safe_load((_WORKFLOWS / "review.yml").read_text())


def _decide_steps() -> tuple[dict, dict]:
    steps = _review_workflow()["jobs"]["decide"]["steps"]
    app = next(s for s in steps if "create-github-app-token" in str(s.get("uses")))
    decide = next(s for s in steps if s.get("name") == "Decide and post the review check")
    assert steps.index(app) < steps.index(decide)
    return app, decide


def test_the_app_token_is_minted_in_the_review_environment_with_no_more_than_the_app_has():
    wf = _review_workflow()
    app, decide = _decide_steps()
    assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", app["uses"])
    assert app["id"] == "app"
    assert app["with"] == {"app-id": "${{ vars.REVIEW_APP_ID }}",
                           "private-key": "${{ secrets.REVIEW_APP_PRIVATE_KEY }}",
                           "permission-checks": "write"}
    for key, level in app["with"].items():
        if key.startswith("permission-"):
            held = _APP_PERMISSIONS.get(key.removeprefix("permission-"))
            assert held == "write" or (level == "read" and held == "read"), key
    assert decide["env"]["APP_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert decide["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert wf["jobs"]["decide"]["environment"] == "review"
    for name, job in wf["jobs"].items():
        if name != "decide":
            assert "environment" not in job and "secrets." not in yaml.safe_dump(job), name
    # Only the App's calls switch to its token; every read stays on the Actions token.
    assert decide["run"].count('GH_TOKEN="$APP_TOKEN"') == 1
    assert re.search(r'^\s*as_app\(\) \{ GH_TOKEN="\$APP_TOKEN" gh api "\$@"; \}$',
                     decide["run"], re.M)


def test_the_triggers_and_conditions_are_exactly_these():
    """Each one is load-bearing: a head with no `synchronize` gets no check; a resolve
    that took any workflow_run event, or a failed signal, would decide on noise."""
    wf = _review_workflow()
    on = wf[True] if True in wf else wf["on"]
    assert on == {
        "pull_request_target": {"types": ["opened", "reopened", "synchronize", "edited"]},
        "workflow_run": {"workflows": ["review-signal", "review (merge queue)"],
                         "types": ["completed"]},
    }
    assert wf["permissions"] == {"contents": "read", "pull-requests": "read", "actions": "read"}
    assert set(wf["jobs"]) == {"resolve", "decide"}
    assert " ".join(wf["jobs"]["resolve"]["if"].split()) == (
        "github.event_name == 'pull_request_target' || "
        "(github.event.workflow_run.conclusion == 'success' && "
        "(github.event.workflow_run.event == 'pull_request_review' || "
        "github.event.workflow_run.event == 'merge_group'))")
    # No App, no decision, and no environment GitHub would make with no branch policy.
    assert wf["jobs"]["decide"]["if"] == "vars.REVIEW_APP_ID != ''"
    assert wf["jobs"]["decide"]["needs"] == "resolve"
    for name, signal in (("review-signal.yml", "review-signal"),
                         ("review-queue.yml", "review (merge queue)")):
        data = yaml.safe_load((_WORKFLOWS / name).read_text())
        assert data["name"] == signal
        assert data["permissions"] == {}, name
        assert all("permissions" not in job for job in data["jobs"].values()), name
    signal_on = yaml.safe_load((_WORKFLOWS / "review-signal.yml").read_text())
    assert signal_on[True] == {
        "pull_request_review": {"types": ["submitted", "edited", "dismissed"]}}
    queue_on = yaml.safe_load((_WORKFLOWS / "review-queue.yml").read_text())
    assert queue_on[True] == {"merge_group": {"types": ["checks_requested"]}}


@pytest.mark.parametrize("name", ["review.yml", "review-signal.yml", "review-queue.yml"])
def test_no_review_workflow_script_expands_an_expression(name):
    """Event data reaches a script only through `env:`, never spliced into its text."""
    wf = yaml.safe_load((_WORKFLOWS / name).read_text())
    for job in wf["jobs"].values():
        for step in job.get("steps") or []:
            assert "${{" not in str(step.get("run", "")), (name, step.get("name"))


def test_host_actions_names_what_review_yml_reads():
    text = (_ROOT / "docs" / "host-actions.md").read_text(encoding="utf-8")
    section = text[text.index("## The review App"):]
    section = section[:section.index("\n## ", 1)]
    for name in ("`review`", "`REVIEW_APP_PRIVATE_KEY`", "`REVIEW_APP_ID`", "`main` only",
                 "`main` alone", "no repository-level `REVIEW_APP_PRIVATE_KEY`",
                 "checks: write", "metadata: read"):
        assert name in section, name
    assert "commit statuses: write" not in section
    wf = (_WORKFLOWS / "review.yml").read_text(encoding="utf-8")
    assert "vars.REVIEW_APP_ID" in wf and "secrets.REVIEW_APP_PRIVATE_KEY" in wf


# A stand-in for `gh` that keeps check runs: it logs each call with the token it carried,
# answers from data.json, which it updates, and applies `--jq` with the real jq.
# - A POST makes a run with the next id (700, 701, ...), owned by whichever token made it.
#   data["fail_posts"] = n makes the next n POSTs exit 1.
# - Any other write exits 1: a decision writes no run that exists.
# - data["fail"] names a path fragment whose call exits 1, as a 404 would.
# - data["fail_5xx"] = {fragment: n} makes that path's next n calls answer HTTP 502.
_FAKE_GH = '''\
import json, os, subprocess, sys
args = sys.argv[1:]
state = os.environ["FAKE_GH"]
store = os.path.join(state, "data.json")
with open(store, encoding="utf-8") as f:
    data = json.load(f)
token = os.environ.get("GH_TOKEN", "")
call = {"token": token, "args": args}
body = None
if "--input" in args:
    with open(args[args.index("--input") + 1], encoding="utf-8") as f:
        body = call["input"] = json.load(f)
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(call) + "\\n")
path = next(a for a in args if a.startswith("repos/"))
if data.get("fail") and data["fail"] in path:
    sys.exit(1)
for fragment, left in (data.get("fail_5xx") or {}).items():
    if left and fragment in path:
        data["fail_5xx"][fragment] = left - 1
        with open(store, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print("gh: Server Error (HTTP 502)", file=sys.stderr)
        sys.exit(1)
runs = data["check_runs"]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
if "/collaborators/" in path and path.split("/")[4] not in data["permissions"]:
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
if method == "POST" and data.get("fail_posts"):
    data["fail_posts"] -= 1
    with open(store, "w", encoding="utf-8") as f:
        json.dump(data, f)
    sys.exit(1)
if method == "POST":
    answer = {"id": 700 + data.get("posted", 0),
              "app": {"id": 777 if token == "app-token" else 15368}, "conclusion": None, **body}
    data["posted"] = data.get("posted", 0) + 1
    runs.append(answer)
elif method != "GET":
    sys.exit(1)
elif "/collaborators/" in path:
    answer = {"permission": data["permissions"][path.split("/")[4]]}
elif "/reviews" in path or "/timeline" in path:
    answer = data["reviews" if "/reviews" in path else "timeline"]
else:
    answer = {"head": {"sha": data["head"]}, "base": {"sha": data["base"]}}
with open(store, "w", encoding="utf-8") as f:
    json.dump(data, f)
text = json.dumps(answer)
if "--jq" in args:
    text = subprocess.run(["jq", "-c", "-r", args[args.index("--jq") + 1]], input=text,
                          capture_output=True, text=True, check=True).stdout.rstrip("\\n")
print(text)
'''


def _fake_gh(tmp_path: Path, data: dict) -> dict[str, str]:
    """Put the fake `gh` first on PATH, holding `data`; the environment that does it."""
    tools = tmp_path / "tools"
    if not tools.exists():
        tools.mkdir()
        (tools / "gh").write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
        (tools / "gh").chmod(0o755)
        (tools / "python").symlink_to(sys.executable)
    state = tmp_path / "fake-gh"
    shutil.rmtree(state, ignore_errors=True)
    state.mkdir()
    (state / "data.json").write_text(json.dumps(data), encoding="utf-8")
    return {"PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", "FAKE_GH": str(state)}


def _decide(tmp_path: Path, checkout: Path, data: dict, **env: str):
    """Run review.yml's decision script as Actions would, with `gh` faked.

    Returns the run, this decision's calls, and the fake's data after it, which a
    later decision can be handed to see what this one left on GitHub."""
    # The script runs jq itself, as GitHub's ubuntu-latest runner provides it, and the
    # fake applies `--jq` with it as gh's embedded jq would. CI's oracle image installs
    # it (docker/linux-oracle.Dockerfile); a missing jq is a broken venue, not a skip.
    assert shutil.which("jq"), "jq is not on PATH: review.yml's script needs it"
    _, decide = _decide_steps()
    (checkout / "scripts").mkdir(exist_ok=True)
    shutil.copy(_ROOT / "scripts" / "review_checks.py", checkout / "scripts")
    fake = _fake_gh(tmp_path, data)
    state = Path(fake["FAKE_GH"])
    runner = tmp_path / "runner"
    runner.mkdir(exist_ok=True)
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", decide["run"]],
        cwd=checkout, capture_output=True, text=True, timeout=120,
        env={**_ENV, **fake,
             "GH_TOKEN": _ACTIONS_TOKEN, "APP_TOKEN": _APP_TOKEN, "REVIEW_APP_ID": "777",
             "REPO": "o/r", "PR": "12", "KIND": "pr", "GROUP_SHA": "",
             "RUNNER_TEMP": str(runner), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
             **env})
    log = state / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    after = json.loads((state / "data.json").read_text(encoding="utf-8"))
    return done, calls, after


def _writes(calls: list[dict]) -> list[tuple[str, str, dict]]:
    """(method, path, body) of each check-run write. Every write is made as the App, and
    every read as Actions."""
    writes = [c for c in calls if "-X" in c["args"]]
    assert all(c["token"] == _APP_TOKEN for c in writes), "a check run not written as the App"
    assert all(c["token"] == _ACTIONS_TOKEN for c in calls if c not in writes)
    out = []
    for c in writes:
        method = c["args"][c["args"].index("-X") + 1]
        path = next(a for a in c["args"] if a.startswith("repos/"))
        out.append((method, path, c["input"]))
    return out


def _review_runs(data: dict, sha: str) -> list[dict]:
    return [r for r in data["check_runs"] if r.get("head_sha") == sha and r["app"]["id"] == 777]


def _passes(data: dict, sha: str) -> bool:
    """GitHub's read of the required check: the App's newest `review` run on `sha`."""
    runs = _review_runs(data, sha)
    newest = max(runs, key=lambda r: r["id"], default={})
    return (newest.get("status"), newest.get("conclusion")) == ("completed", "success")


_FAILED = {"title": "could not decide",
           "summary": "review.yml stopped before its verdict; see its log, then rerun it."}


@pytest.fixture
def pr_checkout(tmp_path: Path, repo):
    path, base, approved = repo
    return tmp_path, path, {"base": base, "head": approved, "reviews": [], "timeline": [],
                            "check_runs": [], "permissions": {"writer": "write"}}


def test_a_decision_is_one_post_of_a_new_run_carrying_its_verdict(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE\n\nread it", data["head"])]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    ((method, where, run),) = _writes(calls)
    assert (method, where) == ("POST", "repos/o/r/check-runs")
    assert run["name"] == "review" and run["head_sha"] == data["head"]
    assert run["external_id"] == "pr-12"
    assert (run["status"], run["conclusion"]) == ("completed", "success")
    (stored,) = _review_runs(after, data["head"])
    assert stored["id"] == 700 and _passes(after, data["head"])


def test_a_decision_reads_who_can_push_as_actions_and_counts_only_a_writer(pr_checkout):
    """The permission read is the run's own token's, and its failure is a named wait."""
    tmp_path, path, data = pr_checkout
    data["permissions"]["reader"] = "read"
    data["reviews"] = [_review("APPROVE", data["head"], login="reader")]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert any("repos/o/r/collaborators/reader/permission" in c["args"] for c in calls)
    ((_, _, run),) = _writes(calls)
    assert run["status"] == "in_progress"
    assert run["output"]["title"] == f"waiting for review of {data['head']}"
    data["reviews"] = [_review("APPROVE", data["head"], login="gone")]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    ((_, _, run),) = _writes(calls)
    assert run["status"] == "in_progress" and not _passes(after, data["head"])
    assert run["output"]["title"] == (
        "no verdict counts: could not read gone's permission: gh: Not Found (HTTP 404)")
    assert run["output"]["title"] in (tmp_path / "summary").read_text(encoding="utf-8")


def test_each_decision_is_a_newer_run_and_the_newest_counts(pr_checkout):
    """Waiting, then an APPROVE, then a BLOCK on one head: each decision posts its own
    run, none is written again, and readers follow the newest."""
    tmp_path, path, data = pr_checkout
    for want in range(700, 702):
        done, calls, data = _decide(tmp_path, path, data)
        assert done.returncode == 0, done.stderr
        assert [w[0] for w in _writes(calls)] == ["POST"]
        newest = max(_review_runs(data, data["head"]), key=lambda r: r["id"])
        assert newest["id"] == want and newest["status"] == "in_progress"
        assert newest["output"]["title"] == f"waiting for review of {data['head']}"
        assert not _passes(data, data["head"])
    data["reviews"] = [_review("APPROVE", data["head"], ident=1)]
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert _passes(data, data["head"])
    data["reviews"].append(_review("BLOCK", data["head"], ident=2, when="2026-11-04T11:00:00Z"))
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert [w[0] for w in _writes(calls)] == ["POST"]
    newest = max(_review_runs(data, data["head"]), key=lambda r: r["id"])
    assert (newest["id"], newest["conclusion"]) == (703, "failure")
    assert not _passes(data, data["head"])
    assert [r["id"] for r in _review_runs(data, data["head"])] == [700, 701, 702, 703]


def test_a_retarget_after_an_approve_reads_waiting(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"], when="2026-11-04T10:00:00Z")]
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert _passes(data, data["head"])
    data["timeline"] = [{"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"}]
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    newest = max(_review_runs(data, data["head"]), key=lambda r: r["id"])
    assert newest["id"] == 701 and newest["status"] == "in_progress"
    assert newest["output"]["title"].startswith(f"waiting for review of {data['head']}")
    assert not _passes(data, data["head"])


def test_the_decision_waits_when_the_base_changed_after_the_verdict(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"], when="2026-11-04T10:00:00Z")]
    data["timeline"] = [{"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"}]
    done, calls, _ = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    ((_, _, verdict),) = _writes(calls)
    assert verdict["status"] == "in_progress" and "conclusion" not in verdict
    assert verdict["output"]["title"] == (
        f"waiting for review of {data['head']} (the base changed at 2026-11-04T11:00:00Z)")


@pytest.mark.parametrize("path", ["/pulls/12", "/reviews", "/timeline", "/collaborators/"])
def test_a_read_github_answers_with_a_502_is_asked_again(pr_checkout, path):
    """A 502 passes: each of the decision's reads, the shell's and the permission read
    alike, is asked again, and the decision posts its verdict as if none came."""
    tmp_path, path_, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    _, plain, _ = _decide(tmp_path, path_, data)
    data["fail_5xx"] = {path: 1}
    done, calls, after = _decide(tmp_path, path_, data)
    assert done.returncode == 0, done.stderr
    assert len(calls) == len(plain) + 1
    assert _passes(after, data["head"])


def test_a_read_that_keeps_answering_502_stops_the_decision(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail_5xx"] = {"/reviews": 3}
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode != 0
    assert sum(any("/reviews" in a for a in c["args"]) for c in calls) == 3
    assert not _passes(after, data["head"])


@pytest.mark.parametrize("fail", ["not a review", "/reviews", "/timeline", "verdict post"])
def test_a_decision_that_stops_before_its_verdict_posts_a_failure(pr_checkout, fail):
    """An unreadable review list, a read that fails (a 404), or the verdict's own POST
    failing: the trap posts one failed run on the head, so no earlier pass shows."""
    tmp_path, path, data = pr_checkout
    data["check_runs"] = [{"id": 41, "app": {"id": 777}, "name": "review",
                           "head_sha": data["head"], "external_id": "pr-12",
                           "status": "completed", "conclusion": "success"}]
    data["reviews"] = [_review("APPROVE", data["head"])]
    if fail == "not a review":
        data["reviews"] = ["not a review"]
    elif fail == "verdict post":
        data["fail_posts"] = 1
    else:
        data["fail"] = fail
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode != 0
    assert _writes(calls)[-1] == ("POST", "repos/o/r/check-runs", {
        "name": "review", "head_sha": data["head"], "external_id": "pr-12",
        "status": "completed", "conclusion": "failure", "output": _FAILED})
    newest = max(_review_runs(after, data["head"]), key=lambda r: r["id"])
    assert (newest["id"], newest["conclusion"]) == (700, "failure")
    assert not _passes(after, data["head"])


def test_a_failure_that_cannot_be_posted_says_so(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail_posts"] = 2
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode != 0
    assert [w[0] for w in _writes(calls)] == ["POST", "POST"]
    assert f"could not post the verdict on {data['head']}" in done.stdout
    assert f"could not post the failed review run on {data['head']}" in done.stdout
    assert _review_runs(after, data["head"]) == []


def test_a_pr_with_no_head_writes_nothing(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["head"] = "null"
    done, calls, _ = _decide(tmp_path, path, data)
    assert done.returncode == 1 and _writes(calls) == []


def _queue(**more: str) -> dict:
    return {"KIND": "queue", "GROUP_SHA": B, **more}


def _pr_run(head: str, **fields) -> dict:
    return {"name": "review", "head_sha": head, "status": "completed", "conclusion": "success",
            "external_id": "pr-12", **fields}


def _group_run(data: dict) -> dict:
    (group,) = _review_runs(data, B)
    assert group["external_id"] == "queue-pr-12" and group["status"] == "completed"
    return group


def test_a_merge_group_fails_on_a_block_whose_own_decision_never_ran(pr_checkout):
    """The PR's head still shows its old pass (the BLOCK's decision has not landed, or
    could not), but the group decides the PR afresh from its reviews, so it fails."""
    tmp_path, path, data = pr_checkout
    head = data["head"]
    data["check_runs"] = [_pr_run(head, id=5, app={"id": 777})]
    data["reviews"] = [_review("APPROVE", head, ident=1),
                       _review("BLOCK", head, ident=2, when="2026-11-04T11:00:00Z")]
    done, calls, after = _decide(tmp_path, path, data, **_queue())
    assert done.returncode == 0, done.stderr
    assert _group_run(after)["conclusion"] == "failure"
    assert _passes(after, head), "the PR's own old pass is untouched, and not what decides"
    # The group never reads the PR's own `review` runs.
    assert not any(f"/commits/{head}/" in a for c in calls for a in c["args"])


@pytest.mark.parametrize(("reviews", "conclusion"), [
    ([("APPROVE", 1, "2026-11-04T10:00:00Z")], "success"),
    ([], "failure"),
    ([("APPROVE", 1, "2026-11-04T10:00:00Z"), ("BLOCK", 2, "2026-11-04T11:00:00Z")], "failure"),
])
def test_a_merge_group_takes_only_its_prs_fresh_decision(pr_checkout, reviews, conclusion):
    """Whatever the PR head's runs say, a pass forged by an Actions job among them, the
    group's run is the fresh decision: success on an approval, failure otherwise."""
    tmp_path, path, data = pr_checkout
    head = data["head"]
    data["check_runs"] = [_pr_run(head, id=9, app={"id": 15368}),
                          _pr_run(head, id=8, app={"id": 777}, conclusion="failure")]
    data["reviews"] = [_review(v, head, ident=i, when=w) for v, i, w in reviews]
    done, calls, after = _decide(tmp_path, path, data, **_queue())
    assert done.returncode == 0, done.stderr
    ((_, _, verdict),) = _writes(calls)
    assert verdict["head_sha"] == B and verdict["external_id"] == "queue-pr-12"
    assert verdict["conclusion"] == conclusion
    assert _group_run(after)["conclusion"] == conclusion


def test_a_merge_group_decision_that_stops_fails_the_group(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail"] = "/timeline"
    done, calls, after = _decide(tmp_path, path, data, **_queue())
    assert done.returncode != 0
    assert _writes(calls) == [("POST", "repos/o/r/check-runs", {
        "name": "review", "head_sha": B, "external_id": "queue-pr-12",
        "status": "completed", "conclusion": "failure", "output": _FAILED})]
    assert _group_run(after)["conclusion"] == "failure"


def test_a_merge_group_with_no_group_commit_writes_nothing(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    done, calls, _ = _decide(tmp_path, path, data, **_queue(GROUP_SHA=""))
    assert done.returncode == 1 and _writes(calls) == []
    assert "not a merge group commit" in done.stdout


@pytest.mark.parametrize("kind", [{}, _queue()])
def test_review_yml_keeps_a_late_block_of_an_earlier_head(tmp_path, heads, kind):
    """Case 3 of the decision across heads, through review.yml's own script."""
    path, base, c = heads
    data = {"base": base, "head": c["same"], "timeline": [], "check_runs": [],
            "permissions": {"writer": "write"},
            "reviews": [_review("APPROVE", c["same"], ident=1, when=_T1),
                        _review("BLOCK", c["H1"], ident=2, when=_T2)]}
    done, calls, after = _decide(tmp_path, path, data, **kind)
    assert done.returncode == 0, done.stderr
    ((_, _, run),) = _writes(calls)
    assert run["conclusion"] == "failure"
    assert f"BLOCK: the same change as {c['H1']}" in run["output"]["title"]
    data["reviews"].append(_review("APPROVE", c["same"], ident=3, when=_T3))
    done, calls, after = _decide(tmp_path, path, data, **kind)
    assert done.returncode == 0, done.stderr
    ((_, _, run),) = _writes(calls)
    assert run["conclusion"] == "success"


def test_a_merge_group_retarget_after_an_approve_fails_the_group(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"], when="2026-11-04T10:00:00Z")]
    data["timeline"] = [{"event": "automatic_base_change_succeeded",
                         "created_at": "2026-11-04T11:00:00Z"}]
    done, calls, after = _decide(tmp_path, path, data, **_queue())
    assert done.returncode == 0, done.stderr
    assert _group_run(after)["conclusion"] == "failure"
