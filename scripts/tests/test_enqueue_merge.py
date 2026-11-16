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
