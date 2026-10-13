"""The merge queue ignores auto-merge armed by an app. Enqueue explicitly.

``enablePullRequestAutoMerge`` admits a PR only for a person. This workflow
calls ``enqueuePullRequest`` after a Jonnyton approval comment, the required
checks, and the scope guard's label, using the merge-attribution token. These
tests are the pre-merge check: the workflow runs on ``workflow_run`` with a
secret, after Tests, invariants, or the scope guard succeeds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import scripts.enqueue_merge as enqueue_mod
from scripts.drain_review_gate import _TRUSTED_ASSOCIATIONS
from scripts.enqueue_merge import (
    PERMISSION_LOG,
    REQUIRED_CHECKS,
    REQUIRED_LABEL,
    checks_green,
    enqueue,
    jonnyton_inventory,
    label_block,
    lacks_merge_queue_permission,
    latest_conclusions,
    load_scope_policy,
    skip_reason,
    trusted_receipt,
)

_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "enqueue-merge.yml"
)
_SECRET = "MERGE_ATTRIBUTION_TOKEN"


@pytest.fixture(scope="module")
def source() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


def _workflow(source: str) -> dict:
    loaded = yaml.safe_load(source)
    # PyYAML parses a bare `on:` key as True.
    if True in loaded:
        loaded["on"] = loaded.pop(True)
    return loaded


def _ready(**overrides):
    values = dict(
        state="OPEN",
        draft=False,
        base="main",
        same_repo=True,
        in_queue=False,
        capped=False,
        receipt=True,
        green=True,
        head_matches=True,
    )
    values.update(overrides)
    return values


def test_a_reviewed_green_pr_is_enqueued():
    assert skip_reason(**_ready()) is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"state": "MERGED"}, "state"),
        ({"draft": True}, "draft"),
        ({"base": "dev"}, "base"),
        ({"same_repo": False}, "fork"),
        ({"in_queue": True}, "already queued"),
        ({"capped": True}, "cap"),
        ({"receipt": False}, "receipt"),
        ({"green": False}, "required checks"),
        ({"head_matches": False}, "head moved"),
    ],
)
def test_each_missing_condition_skips(change, reason):
    text = skip_reason(**_ready(**change))
    assert text is not None and reason in text


def test_latest_success_beats_an_older_failure():
    nodes = [
        {"name": "required-tests", "conclusion": "FAILURE", "completedAt": "2026-10-12T01:00:00Z"},
        {"name": "required-tests", "conclusion": "SUCCESS", "completedAt": "2026-10-12T02:00:00Z"},
        {"name": "slow-tests", "conclusion": "SKIPPED", "completedAt": "2026-10-12T02:00:01Z"},
        {"name": "invariants", "conclusion": "SUCCESS", "completedAt": "2026-10-12T02:00:02Z"},
        {
            "name": "Diff scope declared",
            "conclusion": "SUCCESS",
            "completedAt": "2026-10-12T02:00:03Z",
        },
        {"name": "noise", "conclusion": "FAILURE", "completedAt": "2026-10-12T03:00:00Z"},
    ]
    conclusions = latest_conclusions(nodes)
    assert checks_green(conclusions)
    assert set(conclusions) == set(REQUIRED_CHECKS)


def test_a_newer_failure_is_not_green():
    nodes = [
        {"name": name, "conclusion": "SUCCESS", "completedAt": "2026-10-12T01:00:00Z"}
        for name in REQUIRED_CHECKS
    ]
    nodes.append(
        {"name": "invariants", "conclusion": "FAILURE", "completedAt": "2026-10-12T02:00:00Z"}
    )
    assert not checks_green(latest_conclusions(nodes))


def test_a_missing_required_check_is_not_green():
    nodes = [
        {"name": name, "conclusion": "SUCCESS", "completedAt": "t"}
        for name in REQUIRED_CHECKS
        if name != "invariants"
    ]
    assert not checks_green(latest_conclusions(nodes))


def test_workflow_enqueues_with_the_attribution_token_and_keeps_the_receipt(source):
    workflow = _workflow(source)
    triggers = workflow["on"]
    assert triggers["workflow_run"]["workflows"] == ["Tests", "invariants", "PR scope guard"]
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    step = next(
        s for s in workflow["jobs"]["enqueue"]["steps"]
        if s.get("name") == "Enqueue when the receipt and required checks have passed"
    )
    assert step["env"]["GH_TOKEN"] == "${{ secrets.%s }}" % _SECRET
    assert "github.token" not in source
    assert "scripts/enqueue_merge.py" in step["run"]
    assert "toJSON(github.event.workflow_run.pull_requests)" in source
    program = (
        Path(__file__).resolve().parents[1] / "scripts" / "enqueue_merge.py"
    ).read_text(encoding="utf-8")
    assert "enqueuePullRequest" in program
    assert "drain_review_gate" in program
    assert "queue_attempts.py" in program
    assert "Jonnyton" in program or "jonnyton" in program
    assert REQUIRED_LABEL in program


def test_an_empty_secret_fails_before_enqueue(source):
    workflow = _workflow(source)
    steps = workflow["jobs"]["enqueue"]["steps"]
    names = [s.get("name") for s in steps]
    guard = names.index("Require the merge-attribution token")
    assert guard < names.index("Enqueue when the receipt and required checks have passed")
    step = steps[guard]
    assert step["env"]["TOKEN_PRESENT"] == "${{ secrets.%s != '' }}" % _SECRET
    assert 'if [ "$TOKEN_PRESENT" != "true" ]' in step["run"]
    assert "exit 1" in step["run"]


def test_merge_group_and_failed_runs_do_not_enqueue(source):
    text = " ".join(workflow_if(source).split())
    assert "workflow_run.conclusion == 'success'" in text
    assert "workflow_run.event == 'pull_request'" in text
    assert "workflow_run.event == 'pull_request_target'" in text
    assert "merge_group" not in text


def workflow_if(source: str) -> str:
    return str(_workflow(source)["jobs"]["enqueue"]["if"])


_HEAD = "a" * 40
_OTHER = "b" * 40
_REPO = "TinyAssets/TinyAssets"
_URL = f"https://github.com/{_REPO}/pull/7#issuecomment-99"


def _comment(login="Jonnyton", association="OWNER", head=_HEAD, verdict="APPROVE", url=_URL):
    return {
        "url": url,
        "association": association,
        "login": login,
        "body": f"Drain-Review-Verdict: {verdict}\nDrain-Review-Head: {head}\n\nLGTM\n",
    }


def _body(head=_HEAD, url=_URL):
    return (
        "Drain-Review-Verdict: APPROVE\n"
        f"Drain-Review-Head: {head}\n"
        f"Drain-Review-Artifact: {url}\n"
    )


def _stream(*comments) -> str:
    return "\n".join(json.dumps(comment) for comment in comments)


def test_a_jonnyton_comment_for_this_head_is_a_receipt():
    assert enqueue_mod._TRUSTED_ASSOCIATIONS == _TRUSTED_ASSOCIATIONS
    comments = _stream(_comment())
    assert trusted_receipt(
        head=_HEAD, body=_body(), comments=comments, repo=_REPO, number=7
    )
    kept = jonnyton_inventory(comments)
    assert kept is not None and "Jonnyton" not in kept
    assert _URL.lower() in kept.lower()


@pytest.mark.parametrize(
    "comment",
    [
        _comment(login="someone"),
        _comment(association="CONTRIBUTOR"),
        _comment(association="NONE"),
        _comment(verdict="BLOCK"),
        _comment(head=_OTHER),
        _comment(login="jonnyton"),  # login matches; this one must pass
    ],
)
def test_receipt_author_verdict_and_head(comment):
    ok = trusted_receipt(
        head=_HEAD, body=_body(), comments=_stream(comment), repo=_REPO, number=7
    )
    assert ok is (comment["login"].lower() == "jonnyton" and comment["association"] == "OWNER"
                  and "APPROVE" in comment["body"] and _HEAD in comment["body"])


def test_a_body_that_cites_someone_else_is_not_a_receipt():
    other = "https://github.com/TinyAssets/TinyAssets/pull/7#issuecomment-100"
    comments = _stream(_comment(), _comment(login="Ada", url=other))
    assert not trusted_receipt(
        head=_HEAD, body=_body(url=other), comments=comments, repo=_REPO, number=7
    )


def test_an_unreadable_comment_inventory_is_not_a_receipt():
    assert jonnyton_inventory("{") is None
    assert not trusted_receipt(
        head=_HEAD, body=_body(), comments="{", repo=_REPO, number=7
    )


def test_an_unchanged_rebase_carries_jonnytons_earlier_head():
    comments = _stream(_comment(head=_OTHER))
    assert trusted_receipt(
        head=_HEAD,
        body=_body(head=_OTHER),
        comments=comments,
        repo=_REPO,
        number=7,
        diff_key_value="c" * 64,
        same_change=lambda cited: cited == _OTHER,
    )
    assert not trusted_receipt(
        head=_HEAD,
        body=_body(head=_OTHER),
        comments=comments,
        repo=_REPO,
        number=7,
        diff_key_value="c" * 64,
        same_change=lambda cited: False,
    )


def test_scope_policy_is_the_scope_guards_and_labels_follow_it():
    guard = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pr-scope-guard.yml"
    ).read_text(encoding="utf-8")
    pattern, cap = load_scope_policy(guard)
    assert "*,infra-change,*)" in guard
    assert label_block(["README.md"], set(), pattern=pattern, cap=cap) is None
    assert label_block(
        [".github/workflows/enqueue-merge.yml"], set(), pattern=pattern, cap=cap
    ) == f"missing {REQUIRED_LABEL} label"
    assert label_block(
        [".github/workflows/enqueue-merge.yml"],
        {REQUIRED_LABEL},
        pattern=pattern,
        cap=cap,
    ) is None
    many = [f".github/workflows/n{i}.yml" for i in range(cap + 1)]
    blocked = label_block(many, {REQUIRED_LABEL}, pattern=pattern, cap=cap)
    assert blocked is not None and "cap" in blocked


def test_only_slow_tests_may_be_skipped():
    nodes = [
        {"name": name, "conclusion": "SUCCESS", "completedAt": "t"}
        for name in REQUIRED_CHECKS
    ]
    assert checks_green(latest_conclusions(nodes))
    skipped = [
        {"name": name, "conclusion": "SKIPPED", "completedAt": "t"}
        for name in REQUIRED_CHECKS
    ]
    conclusions = latest_conclusions(skipped)
    assert not checks_green(conclusions)
    conclusions["slow-tests"] = "SKIPPED"
    for name in REQUIRED_CHECKS:
        if name != "slow-tests":
            conclusions[name] = "SUCCESS"
    assert checks_green(conclusions)


def test_a_missing_merge_queue_permission_is_recognized():
    detail = (
        '[{"type": "FORBIDDEN", "path": ["enqueuePullRequest"], '
        '"message": "Resource not accessible by personal access token"}]'
    )
    assert lacks_merge_queue_permission(detail)
    assert "Merge queues: Read and write" in PERMISSION_LOG
    assert not lacks_merge_queue_permission("pull request is closed")


def _open_pr():
    return {
        "id": "PR_1",
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": "d" * 40,
        "headRefOid": _HEAD,
        "headRepository": {"nameWithOwner": _REPO},
        "body": _body(),
        "labels": {"pageInfo": {"hasNextPage": False}, "nodes": [{"name": REQUIRED_LABEL}]},
        "mergeQueueEntry": None,
        "commits": {"nodes": [{
            "commit": {"statusCheckRollup": {"contexts": {"nodes": [
                {"name": name, "conclusion": "SUCCESS", "completedAt": "t"}
                for name in REQUIRED_CHECKS
            ]}}},
        }]},
    }


def test_missing_permission_logs_and_does_not_fail(monkeypatch, capsys):
    calls = {"enqueue": 0}

    def graphql(query, variables):
        if "enqueuePullRequest" in query:
            calls["enqueue"] += 1
            raise RuntimeError("Resource not accessible by personal access token")
        return {"data": {"repository": {"pullRequest": _open_pr()}}}

    monkeypatch.setattr("scripts.enqueue_merge._graphql", graphql)
    monkeypatch.setattr("scripts.enqueue_merge._capped", lambda repo, number: False)
    monkeypatch.setattr(
        "scripts.enqueue_merge._changed_paths",
        lambda repo, number: ["README.md"],
    )
    monkeypatch.setattr("scripts.enqueue_merge._comments", lambda repo, number: _stream(_comment()))
    monkeypatch.setattr(
        "scripts.enqueue_merge._change_key", lambda base, head: (None, None)
    )
    monkeypatch.setattr("scripts.enqueue_merge._fetch_commits", lambda shas: None)
    assert enqueue(_REPO, 7) == 0
    assert calls["enqueue"] == 1
    assert "Merge queues: Read and write" in capsys.readouterr().out


def test_a_pr_without_the_receipt_is_not_enqueued(monkeypatch, capsys):
    def graphql(query, variables):
        if "enqueuePullRequest" in query:
            raise AssertionError("mutation ran")
        pr = _open_pr()
        pr["body"] = "no receipt"
        return {"data": {"repository": {"pullRequest": pr}}}

    monkeypatch.setattr("scripts.enqueue_merge._graphql", graphql)
    monkeypatch.setattr("scripts.enqueue_merge._capped", lambda repo, number: False)
    monkeypatch.setattr(
        "scripts.enqueue_merge._changed_paths",
        lambda repo, number: ["README.md"],
    )
    monkeypatch.setattr("scripts.enqueue_merge._comments", lambda repo, number: "")
    monkeypatch.setattr(
        "scripts.enqueue_merge._change_key", lambda base, head: (None, None)
    )
    monkeypatch.setattr("scripts.enqueue_merge._fetch_commits", lambda shas: None)
    assert enqueue(_REPO, 7) == 0
    assert "no review receipt" in capsys.readouterr().out


def test_a_release_critical_pr_without_the_label_is_not_enqueued(monkeypatch, capsys):
    def graphql(query, variables):
        if "enqueuePullRequest" in query:
            raise AssertionError("mutation ran")
        pr = _open_pr()
        pr["labels"] = {"pageInfo": {"hasNextPage": False}, "nodes": []}
        return {"data": {"repository": {"pullRequest": pr}}}

    monkeypatch.setattr("scripts.enqueue_merge._graphql", graphql)
    monkeypatch.setattr("scripts.enqueue_merge._capped", lambda repo, number: False)
    monkeypatch.setattr(
        "scripts.enqueue_merge._changed_paths",
        lambda repo, number: [".github/workflows/enqueue-merge.yml"],
    )
    assert enqueue(_REPO, 7) == 0
    assert f"missing {REQUIRED_LABEL} label" in capsys.readouterr().out
