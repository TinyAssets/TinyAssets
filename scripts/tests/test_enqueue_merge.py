"""enqueue-merge enqueues nothing unless GitHub applies the ruleset file's rules to main."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import enqueue_merge  # noqa: E402


def _as_github_returns(rules: list[dict]) -> list[dict]:
    """`GET /repos/{repo}/rules/branches/main`: each rule says which ruleset it came from."""
    return [{**rule, "ruleset_source_type": "Repository", "ruleset_source": "o/r",
             "ruleset_id": 1} for rule in rules]


def test_the_rules_the_file_declares_are_no_drift() -> None:
    assert enqueue_merge.gate_drift(enqueue_merge.RULES,
                                    _as_github_returns(enqueue_merge.RULES)) == []


def test_the_same_checks_in_another_order_are_no_drift() -> None:
    live = copy.deepcopy(enqueue_merge.RULES)
    gate = next(rule for rule in live if rule["type"] == "required_status_checks")
    gate["parameters"]["required_status_checks"].reverse()
    assert enqueue_merge.gate_drift(enqueue_merge.RULES, _as_github_returns(live)) == []


def test_a_check_required_live_but_not_in_the_file_is_drift() -> None:
    live = copy.deepcopy(enqueue_merge.RULES)
    gate = next(rule for rule in live if rule["type"] == "required_status_checks")
    gate["parameters"]["required_status_checks"].append({"context": "review", "integration_id": 1})
    drift = enqueue_merge.gate_drift(enqueue_merge.RULES, _as_github_returns(live))
    assert [line.split(":")[0] for line in drift] == ["file only", "live only"]
    assert '"review"' in drift[1]


def test_a_live_rule_the_file_lacks_is_drift() -> None:
    live = [*enqueue_merge.RULES, {"type": "deletion"}]
    assert enqueue_merge.gate_drift(enqueue_merge.RULES, _as_github_returns(live)) == [
        'live only: {"parameters": {}, "type": "deletion"}']


def test_a_pr_its_auto_merge_queued_first_is_already_queued(monkeypatch, capsys) -> None:
    """#5656: auto-merge queued it a second before the enqueue, and GitHub's refusal turned
    the job red."""
    green = [{"__typename": "CheckRun", "name": name, "conclusion": "SUCCESS",
              "completedAt": "2026-11-16T21:35:00Z"} for name in enqueue_merge.REQUIRED_CHECKS]
    pr = {"id": "PR_1", "state": "OPEN", "isDraft": False, "baseRefName": "main",
          "baseRefOid": "b" * 40, "headRefOid": "a" * 40, "body": "", "mergeQueueEntry": None,
          "headRepository": {"nameWithOwner": "o/r"},
          "commits": {"nodes": [{"commit": {"statusCheckRollup": {
              "contexts": {"nodes": green}}}}]}}

    def graphql(query: str, variables: dict) -> dict:
        if "enqueuePullRequest" in query:
            raise RuntimeError("[{'type': 'UNPROCESSABLE', 'path': ['enqueuePullRequest'], "
                               "'message': 'Pull request is already in the queue'}]")
        return {"data": {"repository": {"pullRequest": pr}}}

    monkeypatch.setattr(enqueue_merge, "_graphql", graphql)
    monkeypatch.setattr(enqueue_merge, "_capped", lambda repo, number: False)
    monkeypatch.setattr(enqueue_merge, "_comments", lambda repo, number: "")
    monkeypatch.setattr(enqueue_merge, "_change_key", lambda base, head: (None, None))
    monkeypatch.setattr(enqueue_merge, "_fetch_commits", lambda shas: None)
    monkeypatch.setattr(enqueue_merge, "trusted_receipt", lambda **kwargs: True)
    assert enqueue_merge.enqueue("o/r", 5656) == 0
    assert capsys.readouterr().out == "PR #5656: already queued.\n"
