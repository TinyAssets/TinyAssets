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


@pytest.mark.parametrize("extra", [{"amount_cap": 10}, {"recipient": "someone"},
                                    {"expires_at": 1}, {"disabled": True}, {"path": "/narrow"}])
def test_unknown_qualifier_cannot_become_a_class_wide_permission(extra):
    with pytest.raises(InvalidBundle, match="qualifier"):
        portable_rules(projection(entries=[rule("do", action="people.message", **extra)]),
                       owner="owner-a", center="a", agents={"main": "main"})


@pytest.mark.parametrize("entry", [rule("do", record_kind="future-deny"),
                                   {"scope": "a", "table": "future-limits", "values": {}}])
def test_unknown_scoped_policy_records_refuse_instead_of_disappearing(entry):
    with pytest.raises(InvalidBundle, match="Unsupported"):
        portable_rules(projection(entries=[entry]), owner="owner-a", center="a",
                       agents={"main": "main"})


def test_missing_agent_matches_broker_main_semantics():
    record = rule("hand_off")
    del record["values"]["agent"]
    files = portable_rules(projection(entries=[record]), owner="owner-a", center="a",
                           agents={"main": "main", "editor": "editor"})
    main = json.loads(files["agents/main/rules.json"])["behaviours"]
    assert main["workspace.files"] == "hand_off"
    assert json.loads(files["agents/editor/rules.json"])["behaviours"]["workspace.files"] == "do"


@pytest.mark.parametrize("section,value", [
    ("egress", {"mode": "allowlist", "allowlist": ["limited.example"]}),
    ("spend", {"transaction": 1}),
    ("future_limits", {}),
])
def test_unmapped_document_limits_cannot_disappear(section, value):
    doc = projection()
    doc["policy"][section] = value
    with pytest.raises(InvalidBundle):
        portable_rules(doc, owner="owner-a", center="a", agents={"main": "main"})


def test_content_only_roster_still_declares_required_local_policy():
    from tinyassets.okf.agent_export import roster

    row = roster({"agents/main/AGENTS.md": b"Help"}, ["main"])[0]
    assert row["rules"] == "agents/main/rules.json"
