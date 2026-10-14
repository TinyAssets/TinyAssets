"""Portable policy preserves restrictions without exporting source authority."""
import json

import pytest

from tinyassets.broker.policy import DEFAULT_RULES
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.policy_export import portable_rules, preview_agents


def projection(owner="owner-a", entries=()):
    return {"owner": owner, "generation": 1, "digest": "sha256:" + "a" * 64,
            "policy": {"entries": list(entries)}}


def rule(behavior, *, scope="a", agent="main", action="workspace.files", **extra):
    return {"scope": scope, "table": "rules", "values": {
        "action_class": action, "behaviour": behavior, "agent": agent, **extra,
    }}


def test_scoped_projection_strips_source_authority_and_preserves_strictest():
    doc = projection(entries=[
        rule("do", scope="account"), rule("ask_first"),
        rule("hand_off", scope="other-center"), rule("hand_off", agent="other-agent"),
        rule("hand_off", action="workspace.shell", connection="private-connection"),
        rule("do", action="people.message", operation="SEND", id="private-id"),
        rule("do", record_kind="preapproval", grant_json='{"secret":"private-grant"}'),
    ])
    files = portable_rules(doc, owner="owner-a", center="a", agents={"main": "main"})
    raw = files["agents/main/rules.json"]
    rules = json.loads(raw)["behaviours"]
    assert rules["workspace.files"] == "ask_first"
    assert rules["workspace.shell"] == "hand_off"
    assert rules["people.message"] == "ask_first"  # other operations keep broker default
    assert rules["money.move"] == "hand_off"
    assert set(rules) == set(DEFAULT_RULES)
    assert not any(value in raw for value in (b"private", b"owner-a", b"other-center"))


@pytest.mark.parametrize("change", [
    {"owner": "someone-else"}, {"generation": True}, {"generation": 0},
    {"digest": "invalid"}, {"policy": {}},
    {"policy": {"entries": [rule("unsupported")] }},
])
def test_invalid_policy_never_defaults(change):
    with pytest.raises(InvalidBundle):
        portable_rules(projection() | change, owner="owner-a", center="a", agents={"main": "main"})


def test_agent_derivation_does_not_select_arbitrary_paths():
    paths = ["AGENTS.md", "agents/main/AGENTS.md", "agents/editor/AGENTS.md",
             "workspace/AGENTS.md", "agents/other/sub/AGENTS.md"]
    assert preview_agents(paths) == ["editor", "main"]
