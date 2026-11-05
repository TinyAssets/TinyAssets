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

from scripts import drain_review_gate as gate

pytestmark = pytest.mark.verifies(
    "development-coordination-runtime:"
    "A review verdict is a check run only the review App posts"
)

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


def test_a_dismissal_voids_every_verdict_at_or_before_it():
    approve = _review("APPROVE", ident=1)
    block = _review("BLOCK", ident=2, when="2026-11-04T11:00:00Z", state="DISMISSED")
    assert gate.latest_verdict([approve, block]) is None
    assert gate.review_check([approve, block], base="0" * 40, head=A,
                             carry=lambda commit: "this head").conclusion is None
    # A dismissed ordinary comment, or one by anyone, voids just the same.
    chat = _review("why?", ident=2, when="2026-11-04T11:00:00Z", state="DISMISSED",
                   association="NONE")
    assert gate.latest_verdict([approve, chat]) is None
    later = _review("APPROVE", B, ident=3, when="2026-11-04T12:00:00Z")
    assert gate.latest_verdict([approve, block, later]).commit == B
    with pytest.raises(ValueError):
        gate.latest_verdict([approve, {"state": "DISMISSED", "id": 2}])


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


def _cli(path, reviews, timeline, base, head, *more, pr="12"):
    return subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "drain_review_gate.py"), "--review-check",
         "--reviews-file", str(reviews), "--timeline-file", str(timeline), "--pr", pr,
         "--base", base, "--head", head, *more], cwd=path, capture_output=True, text=True)


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


def test_githubs_own_retarget_after_a_parent_merges_counts_as_a_base_change():
    automatic = {"event": "automatic_base_change_succeeded", "created_at": "2026-11-04T13:00:00Z"}
    assert gate.last_base_change([
        {"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"},
        automatic]) == "2026-11-04T13:00:00Z"
    assert gate.last_base_change([{"event": "automatic_base_change_failed",
                                   "created_at": "2026-11-04T14:00:00Z"}]) is None
    approve = _review("APPROVE", when="2026-11-04T12:00:00Z")
    assert gate.latest_verdict([approve], since=gate.last_base_change([automatic])) is None


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
    for name, job in wf["jobs"].items():
        assert job.get("name") != "review" and name != "review"
    queue = yaml.safe_load((_ROOT / ".github" / "workflows" / "review-queue.yml").read_text())
    assert queue["permissions"] == {}
    assert all(job.get("name") != "review" for job in queue["jobs"].values())


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
    # A stale run left in progress does not hold back a newer pass.
    assert gate.review_run_passed([{**done, "id": 4}, waiting], 12)


def test_only_the_review_apps_run_passes_a_reader(monkeypatch):
    """An Actions-app run named review for the PR (any PR's own job can post one) is not a
    pass: the App filter is what makes the check the review App's alone."""
    head = "c" * 40
    actions = {"id": 9, "name": "review", "status": "completed", "conclusion": "success",
               "external_id": "pr-12", "app": {"id": 15368}}
    ours = {**actions, "id": 3, "app": {"id": 777}}

    def api(runs):
        def check_runs(path):
            assert path == (f"repos/o/r/commits/{head}/check-runs"
                            "?check_name=review&filter=all&per_page=100")
            return runs
        monkeypatch.setattr(gate, "_gh_check_runs", check_runs)

    monkeypatch.setenv("REVIEW_APP_ID", "777")
    api([actions])
    assert not gate.review_passed_at("o/r", head, 12)
    api([actions, ours])
    assert gate.review_passed_at("o/r", head, 12)


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
        assert all("permissions" not in job and job.get("name") != "review"
                   for job in data["jobs"].values()), name
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
# - A PATCH updates the App's own run and answers with the run as it now is. A completed
#   run is frozen: the PATCH answers 200 and changes nothing, which is what the
#   evidence suggests GitHub does. data["ignore_patch"] freezes every run that way;
#   data["race"] = id completes that run just before the first PATCH of it lands.
# - data["fail_posts"] = n makes the next n POSTs exit 1.
# - A listing of `commits/<sha>/check-runs` returns the runs on that commit.
# - data["fail"] names a path fragment whose call exits 1, as a 502 would.
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
runs = data["check_runs"]
method = args[args.index("-X") + 1] if "-X" in args else "GET"
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
elif method == "PATCH":
    answer = next((r for r in runs if path.endswith(f"/check-runs/{r['id']}")), None)
    if answer is None or answer["app"]["id"] != 777:
        sys.exit(1)
    if data.get("race") == answer["id"]:
        # Another writer completed it between the listing and this PATCH.
        answer.update(status="completed", conclusion="failure")
        data.pop("race")
    if answer.get("status") != "completed" and not data.get("ignore_patch"):
        answer.update(body)
        if body.get("status") == "in_progress":
            answer["conclusion"] = None
elif "/reviews" in path or "/timeline" in path:
    answer = data["reviews" if "/reviews" in path else "timeline"]
elif "/check-runs" in path:
    sha = path.split("/commits/")[1].split("/")[0]
    answer = {"check_runs": [r for r in runs if r.get("head_sha") == sha]}
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


def _decide(tmp_path: Path, checkout: Path, data: dict, **env: str):
    """Run review.yml's decision script as Actions would, with `gh` faked.

    Returns the run, this decision's calls, and the fake's data after it, which a
    later decision can be handed to see what this one left on GitHub."""
    _, decide = _decide_steps()
    (checkout / "scripts").mkdir(exist_ok=True)
    shutil.copy(_ROOT / "scripts" / "drain_review_gate.py", checkout / "scripts")
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
    runner = tmp_path / "runner"
    runner.mkdir(exist_ok=True)
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", decide["run"]],
        cwd=checkout, capture_output=True, text=True, timeout=120,
        env={**_ENV, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", "FAKE_GH": str(state),
             "GH_TOKEN": _ACTIONS_TOKEN, "APP_TOKEN": _APP_TOKEN, "REVIEW_APP_ID": "777",
             "REPO": "o/r", "PR": "12", "KIND": "pr", "GROUP_SHA": "", "GROUP_REF": "",
             "RUNNER_TEMP": str(runner), "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
             **env})
    log = state / "calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    after = json.loads((state / "data.json").read_text(encoding="utf-8"))
    return done, calls, after


def _writes(calls: list[dict]) -> list[tuple[str, str, dict]]:
    """(method, path, body) of each check-run write. Every write, and every listing of
    the App's own runs, is made as the App; every other read as Actions."""
    writes = [c for c in calls if "-X" in c["args"]]
    assert all(c["token"] == _APP_TOKEN for c in writes), "a check run not written as the App"
    for c in calls:
        if c not in writes and c["token"] != _ACTIONS_TOKEN:
            assert c["token"] == _APP_TOKEN and c["args"][:2] == ["api", "--paginate"]
            assert any("/check-runs?check_name=review&filter=all" in a for a in c["args"])
    out = []
    for c in writes:
        method = c["args"][c["args"].index("-X") + 1]
        path = next(a for a in c["args"] if a.startswith("repos/"))
        out.append((method, path, c["input"]))
    return out


def _review_runs(data: dict, sha: str) -> list[dict]:
    return [r for r in data["check_runs"] if r.get("head_sha") == sha and r["app"]["id"] == 777]


def _passes(data: dict, sha: str) -> bool:
    """What every reader decides from the App's runs on `sha` for PR 12."""
    return gate.review_run_passed(_review_runs(data, sha), 12)


@pytest.fixture
def pr_checkout(tmp_path: Path, repo):
    path, base, approved = repo
    return tmp_path, path, {"base": base, "head": approved, "reviews": [], "timeline": [],
                            "check_runs": []}


def test_the_decision_completes_its_own_deciding_run_with_the_verdict(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE\n\nread it", data["head"])]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    (post, _, deciding), (patch, where, verdict) = _writes(calls)
    assert (post, patch, where) == ("POST", "PATCH", "repos/o/r/check-runs/700")
    assert deciding == {"name": "review", "head_sha": data["head"], "external_id": "pr-12",
                        "status": "in_progress",
                        "output": {"title": "deciding",
                                   "summary": "review.yml is reading the reviews."}}
    assert verdict["external_id"] == "pr-12" and "head_sha" not in verdict
    assert verdict["status"] == "completed" and verdict["conclusion"] == "success"
    (run,) = _review_runs(after, data["head"])
    assert run["id"] == 700 and run["conclusion"] == "success" and _passes(after, data["head"])


def test_waiting_decisions_keep_one_run_and_a_verdict_completes_it(pr_checkout):
    """Waiting decisions reuse the App's one in-progress run; a later APPROVE turns that
    same run to success."""
    tmp_path, path, data = pr_checkout
    for _ in range(3):
        done, calls, data = _decide(tmp_path, path, data)
        assert done.returncode == 0, done.stderr
        (run,) = _review_runs(data, data["head"])
        assert run["id"] == 700 and run["status"] == "in_progress"
        assert run["output"]["title"] == f"waiting for review of {data['head']}"
    assert [w[0] for w in _writes(calls)] == ["PATCH", "PATCH"]
    data["reviews"] = [_review("APPROVE", data["head"], ident=1)]
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert {w[1] for w in _writes(calls)} == {"repos/o/r/check-runs/700"}
    (run,) = _review_runs(data, data["head"])
    assert run["id"] == 700 and (run["status"], run["conclusion"]) == ("completed", "success")


def test_a_block_after_an_approve_on_one_head_fails_it(pr_checkout):
    """A completed run is frozen, so the BLOCK goes on a new run, the newest, which fails."""
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"], ident=1)]
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert _passes(data, data["head"])
    data["reviews"].append(_review("BLOCK", data["head"], ident=2, when="2026-11-04T11:00:00Z"))
    done, calls, data = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert [(w[0], w[1]) for w in _writes(calls)] == [
        ("POST", "repos/o/r/check-runs"), ("PATCH", "repos/o/r/check-runs/701")]
    newest = max(_review_runs(data, data["head"]), key=lambda r: r["id"])
    assert (newest["id"], newest["conclusion"]) == (701, "failure")
    assert not _passes(data, data["head"])


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


def test_the_run_reused_is_the_apps_newest_for_this_pr_while_in_progress(pr_checkout):
    tmp_path, path, data = pr_checkout
    head = data["head"]
    mine = {"name": "review", "head_sha": head, "external_id": "pr-12", "status": "in_progress",
            "conclusion": None, "output": {"title": "waiting", "summary": ""}}
    data["check_runs"] = [
        {**mine, "id": 41, "app": {"id": 777}},
        {**mine, "id": 42, "app": {"id": 777}},
        {**mine, "id": 50, "app": {"id": 777}, "external_id": "pr-13"},
        {**mine, "id": 60, "app": {"id": 15368}},
    ]
    data["reviews"] = [_review("APPROVE", head)]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    writes = _writes(calls)
    assert {(w[0], w[1]) for w in writes} == {("PATCH", "repos/o/r/check-runs/42")}
    assert writes[0][2] == {"external_id": "pr-12", "status": "in_progress",
                            "output": {"title": "deciding",
                                       "summary": "review.yml is reading the reviews."}}
    by_id = {r["id"]: r for r in after["check_runs"]}
    assert by_id[42]["conclusion"] == "success"
    assert all(by_id[i]["status"] == "in_progress" for i in (41, 50, 60))
    # When this PR's newest run has completed, an older one in progress is not reused.
    data["check_runs"] = [{**mine, "id": 41, "app": {"id": 777}},
                          {**mine, "id": 42, "app": {"id": 777}, "status": "completed",
                           "conclusion": "failure"}]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert [w[0] for w in _writes(calls)] == ["POST", "PATCH"]
    assert _passes(after, head)


def test_a_run_that_cannot_be_listed_or_did_not_take_deciding_gets_a_fresh_one(pr_checkout):
    """Unknown is not none, and a stale pass must not stand: post a new deciding run,
    which readers take as the newest."""
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("BLOCK", data["head"])]
    data["fail"] = "/check-runs?check_name=review"
    done, calls, _ = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert [w[0] for w in _writes(calls)] == ["POST", "PATCH"]
    # The run listed in progress has completed by the time the reopen lands: GitHub
    # answers 200 with it completed, so the reopen did not take.
    data.pop("fail")
    data["check_runs"] = [{"id": 41, "app": {"id": 777}, "name": "review",
                           "head_sha": data["head"], "external_id": "pr-12",
                           "status": "in_progress", "conclusion": None,
                           "output": {"title": "waiting", "summary": ""}}]
    data["race"] = 41
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert [(w[0], w[1]) for w in _writes(calls)] == [
        ("PATCH", "repos/o/r/check-runs/41"), ("POST", "repos/o/r/check-runs"),
        ("PATCH", "repos/o/r/check-runs/700")]
    assert 'did not take "deciding"' in done.stdout
    newest = max(_review_runs(after, data["head"]), key=lambda r: r["id"])
    assert (newest["id"], newest["conclusion"]) == (700, "failure")


def test_a_verdict_that_did_not_take_fails_the_decision(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["ignore_patch"] = True
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 1
    assert "check run 700 did not take the verdict" in done.stdout
    # The trap's own PATCH is checked too, and says so when it did not take.
    assert "could not complete check run 700 as a failure" in done.stdout
    (run,) = _review_runs(after, data["head"])
    assert run["status"] == "in_progress" and not _passes(after, data["head"])


def test_the_decision_waits_when_the_base_changed_after_the_verdict(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"], when="2026-11-04T10:00:00Z")]
    data["timeline"] = [{"event": "base_ref_changed", "created_at": "2026-11-04T11:00:00Z"}]
    done, calls, _ = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    _, _, verdict = _writes(calls)[-1]
    assert verdict["status"] == "in_progress" and "conclusion" not in verdict
    assert verdict["output"]["title"] == (
        f"waiting for review of {data['head']} (the base changed at 2026-11-04T11:00:00Z)")


def test_a_decision_that_cannot_read_the_reviews_fails_its_run(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = ["not a review"]
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode != 0
    _, _, failed = _writes(calls)[-1]
    assert failed["conclusion"] == "failure" and failed["output"]["title"] == "could not decide"
    (run,) = _review_runs(after, data["head"])
    assert run["conclusion"] == "failure"


@pytest.mark.parametrize("fail", ["/reviews", "/timeline", "/check-runs/700"])
def test_a_decision_that_stops_after_deciding_fails_that_run(pr_checkout, fail):
    """A read that dies (a 502), or the verdict's own write, leaves no run in progress
    and no earlier pass showing: the trap completes the deciding run as a failure."""
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail"] = fail
    done, calls, _ = _decide(tmp_path, path, data)
    assert done.returncode != 0
    writes = _writes(calls)
    assert writes[-1][:2] == ("PATCH", "repos/o/r/check-runs/700")
    assert writes[-1][2] == {"status": "completed", "conclusion": "failure",
                             "output": {"title": "could not decide", "summary":
                                        "review.yml stopped before its verdict; see its log, "
                                        "then rerun it."}}


def test_a_crash_after_a_passed_run_leaves_the_head_failing(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["check_runs"] = [{"id": 41, "app": {"id": 777}, "name": "review",
                           "head_sha": data["head"], "external_id": "pr-12",
                           "status": "completed", "conclusion": "success"}]
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail"] = "/timeline"
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode != 0
    newest = max(_review_runs(after, data["head"]), key=lambda r: r["id"])
    assert (newest["id"], newest["conclusion"]) == (700, "failure")
    assert not _passes(after, data["head"])


def test_a_pr_with_no_head_writes_nothing(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["head"] = "null"
    done, calls, _ = _decide(tmp_path, path, data)
    assert done.returncode == 1 and _writes(calls) == []


def test_a_failed_deciding_post_is_retried(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail_posts"] = 2
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode == 0, done.stderr
    assert [w[0] for w in _writes(calls)] == ["POST", "POST", "POST", "PATCH"]
    assert _passes(after, data["head"])


def test_a_deciding_post_that_keeps_failing_fails_the_decision(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"])]
    data["fail_posts"] = 3
    done, calls, after = _decide(tmp_path, path, data)
    assert done.returncode != 0
    assert [w[0] for w in _writes(calls)] == ["POST", "POST", "POST"]
    assert "could not post the deciding check run" in done.stdout
    assert _review_runs(after, data["head"]) == []
    assert not any("/reviews" in a for c in calls for a in c["args"]), "decided with no run"


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
    (_, _, deciding), (_, _, verdict) = _writes(calls)
    assert deciding["head_sha"] == B and deciding["external_id"] == "queue-pr-12"
    assert verdict["conclusion"] == conclusion
    assert _group_run(after)["conclusion"] == conclusion


def test_a_merge_group_retarget_after_an_approve_fails_the_group(pr_checkout):
    tmp_path, path, data = pr_checkout
    data["reviews"] = [_review("APPROVE", data["head"], when="2026-11-04T10:00:00Z")]
    data["timeline"] = [{"event": "automatic_base_change_succeeded",
                         "created_at": "2026-11-04T11:00:00Z"}]
    done, calls, after = _decide(tmp_path, path, data, **_queue())
    assert done.returncode == 0, done.stderr
    assert _group_run(after)["conclusion"] == "failure"
