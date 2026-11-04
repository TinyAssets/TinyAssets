"""The `review` check: a PR review on a commit, carried to later heads only by `carries`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import drain_review_gate as gate

_ROOT = Path(__file__).resolve().parents[1]
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
            association: str = "OWNER", state: str = "COMMENTED") -> dict:
    return {"id": ident, "body": body, "commit_id": commit, "submitted_at": when,
            "author_association": association, "state": state,
            "html_url": f"https://github.com/o/r/pull/1#pullrequestreview-{ident}"}


def test_the_latest_trusted_verdict_wins():
    approve = _review("APPROVE\n\nread it all", ident=1)
    block = _review("BLOCK\n\nthe lock is wrong", ident=2, when="2026-11-04T11:00:00Z")
    assert gate.latest_verdict([approve]) == gate.Verdict("APPROVE", A, approve["html_url"])
    assert gate.latest_verdict([block, approve]).verdict == "BLOCK"
    later = _review("APPROVE", B, ident=3, when="2026-11-04T12:00:00Z")
    assert gate.latest_verdict([approve, block, later]) == gate.Verdict(
        "APPROVE", B, later["html_url"])


@pytest.mark.parametrize("review", [
    _review("APPROVE", association="CONTRIBUTOR"),
    _review("APPROVE", association="NONE"),
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
    assert gate.latest_verdict([review]) is None


def test_leading_whitespace_before_the_verdict_is_accepted():
    assert gate.latest_verdict([_review("\n\n   APPROVE  \n\nevidence")]).verdict == "APPROVE"


def test_an_ordinary_review_after_a_verdict_changes_nothing():
    approve = _review("APPROVE", ident=1)
    chat = _review("one question about line 4", ident=2, when="2026-11-04T11:00:00Z")
    assert gate.latest_verdict([approve, chat]).verdict == "APPROVE"


@pytest.mark.parametrize("review", [
    "not a review",
    {"id": 1, "body": "APPROVE", "author_association": "OWNER", "state": "COMMENTED",
     "submitted_at": "2026-11-04T10:00:00Z", "commit_id": "short"},
    {"id": 1, "body": "APPROVE", "state": "COMMENTED"},
])
def test_an_unreadable_review_list_refuses(review):
    with pytest.raises(ValueError):
        gate.latest_verdict([review])


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
    return gate.review_check(reviews, base="0" * 40, head=head, carry=carry)


def test_no_review_is_pending_not_red():
    state = _state([])
    assert state.conclusion is None and state.title == f"waiting for review of {B}"


def test_an_approve_that_carries_passes_and_one_that_does_not_waits():
    reviews = [_review("APPROVE", A)]
    assert _state(reviews, lambda commit: "only prose").conclusion == "success"
    waiting = _state(reviews)
    assert waiting.conclusion is None and "delta review" in waiting.title


def test_a_block_fails_where_it_carries_and_waits_after_a_new_change():
    reviews = [_review("APPROVE", A, ident=1), _review("BLOCK", A, ident=2)]
    assert _state(reviews, lambda commit: "this head").conclusion == "failure"
    assert _state(reviews).conclusion is None


def _cli(path, reviews, timeline, base, head, pr="12"):
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "drain_review_gate.py"), "--review-check",
         "--reviews-file", str(reviews), "--timeline-file", str(timeline), "--pr", pr,
         "--base", base, "--head", head], cwd=path, capture_output=True, text=True)


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


# ---- a base change voids the reviews before it ----------------------------------------


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
    later = _review("APPROVE", approved, ident=2, when="2026-11-04T11:30:00Z")
    assert gate.latest_verdict([later], since="2026-11-04T11:00:00Z").verdict == "APPROVE"


def test_the_last_base_change_is_read_from_the_timeline():
    assert gate.last_base_change([]) is None
    assert gate.last_base_change([
        {"event": "base_ref_changed", "created_at": "2026-11-04T09:00:00Z"},
        {"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"},
        {"event": "labeled", "created_at": "2026-11-04T12:00:00Z"}]) == "2026-11-04T11:00:00Z"
    with pytest.raises(ValueError):
        gate.last_base_change([{"event": "base_ref_changed"}])


# ---- the signal names only its own PR, and the App alone decides ----------------------


def test_a_signal_names_only_the_pr_whose_branch_it_ran_on():
    pull = {"head": {"ref": "lane-a", "repo": {"full_name": "o/r"}}}
    assert gate.signal_pr("12\n", pull, run_repo="o/r", run_branch="lane-a") == 12
    for text, run_repo, run_branch in [("12", "o/r", "lane-b"), ("12", "fork/r", "lane-a"),
                                       ("x", "o/r", "lane-a"), ("0", "o/r", "lane-a")]:
        with pytest.raises(ValueError):
            gate.signal_pr(text, pull, run_repo=run_repo, run_branch=run_branch)


def test_the_review_app_is_required_and_is_not_actions():
    assert gate.review_app_id({"REVIEW_APP_ID": "777"}) == 777
    for value in ("", "x", "15368"):
        with pytest.raises(ValueError):
            gate.review_app_id({"REVIEW_APP_ID": value})


def test_review_yml_decides_per_pr_with_the_app_in_its_environment():
    import yaml

    wf = yaml.safe_load((_ROOT / ".github" / "workflows" / "review.yml").read_text())
    on = wf[True] if True in wf else wf["on"]
    assert "edited" in on["pull_request_target"]["types"]
    decide = wf["jobs"]["decide"]
    assert decide["environment"] == "review"
    assert decide["concurrency"]["group"] == (
        "review-${{ github.repository }}-${{ needs.resolve.outputs.pr }}")
    assert decide["concurrency"]["cancel-in-progress"] is False
    assert "concurrency" not in wf
    steps = "\n".join(str(step) for step in decide["steps"])
    assert "create-github-app-token" in steps and "REVIEW_APP_PRIVATE_KEY" in steps
    for name, job in wf["jobs"].items():
        assert job.get("name") != "review" and name != "review"
    queue = yaml.safe_load((_ROOT / ".github" / "workflows" / "review-queue.yml").read_text())
    assert queue["permissions"] == {}
    assert all(job.get("name") != "review" for job in queue["jobs"].values())
    enqueue = yaml.safe_load((_ROOT / ".github" / "workflows" / "enqueue-merge.yml").read_text())
    assert "workflow_run.id" in enqueue["concurrency"]["group"]


# ---- the merge group ---------------------------------------------------------------------


def test_the_merge_group_ref_names_its_pr():
    assert gate.queued_pr(f"refs/heads/gh-readonly-queue/main/pr-5416-{A}") == 5416
    assert gate.queued_pr(f"gh-readonly-queue/main/pr-7-{A}") == 7
    for ref in ("refs/heads/main", f"gh-readonly-queue/main/pr-0-{A}",
                "gh-readonly-queue/main/pr-7-abc", f"x/gh-readonly-queue/main/pr-7-{A}"):
        assert gate.queued_pr(ref) is None


def test_only_the_latest_successful_run_made_for_this_pr_passes():
    done = {"id": 1, "name": "review", "status": "completed", "conclusion": "success",
            "external_id": "pr-12"}
    waiting = {**done, "id": 2, "status": "in_progress", "conclusion": None}
    failed = {**done, "id": 3, "conclusion": "failure"}
    assert gate.review_run_passed([done], 12)
    assert not gate.review_run_passed([done], 13), "another PR on the same head"
    assert not gate.review_run_passed([{**done, "external_id": None}], 12)
    assert not gate.review_run_passed([], 12)
    assert not gate.review_run_passed(None, 12)
    assert not gate.review_run_passed([done, waiting], 12)
    assert not gate.review_run_passed([failed, done], 12)
    assert not gate.review_run_passed([{**done, "name": "required-tests"}], 12)


def test_no_receipt_wording_is_left_where_people_read_it():
    """The receipt is gone: nothing posts, documents or prompts for one."""
    for rel in (".github/workflows/okf-watch.yml", ".github/workflows/enqueue-merge.yml",
                "scripts/main_red.py", "docs/host-actions.md",
                "docs/reference/executable-gates.md"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "review receipt" not in text and "Drain-Review-" not in text, rel
        assert "a receipt like every PR" not in text, rel


def test_okf_watch_ready_comment_runs_no_command():
    """Backticks in the double-quoted comment body would run `review` as a command."""
    text = (_ROOT / ".github" / "workflows" / "okf-watch.yml").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if "The cooldown is over" in line)
    assert "`" not in line


def test_only_the_review_apps_run_passes_a_reader(monkeypatch, capsys):
    """An Actions-app run named review for the PR (any PR's own job can post one) is not a
    pass: the App filter is what makes the check the review App's alone."""
    head = "c" * 40
    actions = {"id": 9, "name": "review", "status": "completed", "conclusion": "success",
               "external_id": "pr-12", "app": {"id": 15368}}
    ours = {**actions, "id": 3, "app": {"id": 777}}

    def api(runs):
        def fake(path):
            if path.endswith("/pulls/12"):
                return {"head": {"sha": head}}
            assert path == f"repos/o/r/commits/{head}/check-runs?check_name=review&per_page=100"
            return {"check_runs": runs}
        return fake

    monkeypatch.setenv("REVIEW_APP_ID", "777")
    monkeypatch.setattr(gate, "_gh_json", api([actions]))
    assert not gate.review_passed_at("o/r", head, 12)
    assert gate._queue_review(["--repo", "o/r", "--ref", f"gh-readonly-queue/main/pr-12-{A}",
                               "--sha", B]) == 0
    assert json.loads(capsys.readouterr().out)["conclusion"] == "failure"
    monkeypatch.setattr(gate, "_gh_json", api([actions, ours]))
    assert gate.review_passed_at("o/r", head, 12)
    assert gate._queue_review(["--repo", "o/r", "--ref", f"gh-readonly-queue/main/pr-12-{A}",
                               "--sha", B]) == 0
    assert json.loads(capsys.readouterr().out)["conclusion"] == "success"
