"""Harness D1a: the owner's Custom Rules decide what the agent may do.

Design #4172 §4.8-4.10 (founder-approved 2026-10-01): dots' four behaviours,
seeded to reproduce dots, editable only by the owner, with the three hand-backs
on by default and loosened only after the owner confirms what that allows.
D1a enforces at the credential-blind effector, where rules can only tighten the
standing destination grants.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import agent_review, agent_rules
from tinyassets.agent_rules import ASK_FIRST, DO, DO_IF_PREAPPROVED, HAND_OFF


def _approving_review():
    return agent_review.bound(
        lambda *_a, **_kw: '{"verdict": "proceed", "reason": "ok"}', active=True)

def _universe(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "u-alpha"
    path.mkdir(parents=True)
    return path


# -- seed ----------------------------------------------------------------------


def test_a_new_universe_is_seeded_to_work_like_dots(tmp_path):
    rules = {r.action_class: r.behaviour for r in agent_rules.list_rules(_universe(tmp_path))}
    assert rules["workspace.files"] == DO and rules["app.read"] == DO
    assert rules["people.message"] == ASK_FIRST and rules["commons.publish"] == ASK_FIRST
    for handback in ("money.move", "security.change", "access.grant"):
        assert rules[handback] == HAND_OFF, handback
    assert set(rules) == set(agent_rules.ACTION_CLASSES), "every class has a visible rule"


def test_the_seed_is_written_once_and_never_over_an_owners_edit(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "people.message", DO)
    assert agent_rules.decide(universe, "people.message").behaviour == DO
    assert agent_rules.decide(universe, "people.message").behaviour == DO


def test_the_store_lives_outside_the_universe_folder(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.list_rules(universe)
    assert not any(universe.rglob("rules.db"))
    assert (tmp_path / "data" / ".agent-sessions" / "u-alpha" / "rules.db").exists()


# -- decide ---------------------------------------------------------------------


def test_the_most_specific_rule_wins(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "app.write", ASK_FIRST, connection="github")
    agent_rules.set_rule(universe, "app.write", DO, connection="github", operation="post")
    assert agent_rules.decide(universe, "app.write", connection="github",
                              operation="POST").behaviour == DO
    assert agent_rules.decide(universe, "app.write", connection="github",
                              operation="DELETE").behaviour == ASK_FIRST
    assert agent_rules.decide(universe, "app.write", connection="slack").behaviour == DO


def test_equally_specific_rules_go_to_the_stricter(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "app.write", ASK_FIRST, connection="a")
    agent_rules.set_rule(universe, "app.write", HAND_OFF, operation="DELETE")
    decided = agent_rules.decide(universe, "app.write", connection="b", operation="DELETE")
    assert decided.behaviour == HAND_OFF


def test_a_class_no_rule_covers_asks_first(tmp_path):
    decided = agent_rules.decide(_universe(tmp_path), "not.a.class")
    assert decided.behaviour == ASK_FIRST and not decided.proceeds


# -- the owner's edits -----------------------------------------------------------


def test_loosening_a_handback_needs_the_owner_to_confirm_what_it_allows(tmp_path):
    universe = _universe(tmp_path)
    with pytest.raises(agent_rules.RuleRefused) as refused:
        agent_rules.set_rule(universe, "money.move", ASK_FIRST)
    assert "move money" in str(refused.value)
    assert agent_rules.decide(universe, "money.move").behaviour == HAND_OFF
    agent_rules.set_rule(universe, "money.move", ASK_FIRST, confirm_handback=True)
    assert agent_rules.decide(universe, "money.move").behaviour == ASK_FIRST


def test_unknown_classes_and_behaviours_are_refused(tmp_path):
    universe = _universe(tmp_path)
    with pytest.raises(agent_rules.RuleRefused):
        agent_rules.set_rule(universe, "everything", DO)
    with pytest.raises(agent_rules.RuleRefused):
        agent_rules.set_rule(universe, "app.write", "sometimes")


def test_only_narrowed_rules_can_be_removed(tmp_path):
    universe = _universe(tmp_path)
    narrowed = agent_rules.set_rule(universe, "app.write", HAND_OFF, connection="bank")
    class_wide = next(r for r in agent_rules.list_rules(universe)
                      if r.action_class == "app.write" and not r.connection)
    assert agent_rules.delete_rule(universe, class_wide.id) is False
    assert agent_rules.delete_rule(universe, narrowed.id) is True
    assert agent_rules.decide(universe, "app.write", connection="bank").behaviour == DO


# -- enforcement at the effector -------------------------------------------------


@pytest.mark.parametrize("behaviour, kind", [
    (ASK_FIRST, "rule_ask_first"), (HAND_OFF, "rule_hand_off"),
    (DO_IF_PREAPPROVED, "rule_ask_first"),
])
def test_a_rule_stops_a_call_its_grant_would_allow(tmp_path, behaviour, kind):
    from tinyassets.effectors.authenticated_external_call import _rule_refusal

    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "app.write", behaviour, connection="conn-1")
    refusal = _rule_refusal(universe, "conn-1", "POST")
    assert refusal is not None and refusal["error_kind"] == kind
    assert refusal["dry_run"] is True


def test_the_seed_lets_a_granted_write_proceed_to_its_grant_check(tmp_path):
    from tinyassets.effectors.authenticated_external_call import _rule_refusal

    # The rule lets it through; the check on the run's model (D1d) approves here.
    with _approving_review():
        assert _rule_refusal(_universe(tmp_path), "conn-1", "POST") is None


def test_an_unreadable_rule_store_refuses_the_call(tmp_path, monkeypatch):
    from tinyassets.effectors import authenticated_external_call as effector

    def broken(*_a, **_kw):
        raise OSError("disk")

    monkeypatch.setattr(agent_rules, "decide", broken)
    refusal = effector._rule_refusal(_universe(tmp_path), "conn-1", "POST")
    assert refusal["error_kind"] == "rules_unreadable"


def test_the_effector_checks_rules_before_the_standing_grant():
    """Order matters: a rule must stop a call even when a grant exists."""
    from tinyassets.effectors import authenticated_external_call as effector

    source = Path(effector.__file__).read_text(encoding="utf-8")
    run = source[source.index("def _run("):]
    assert run.index("_rule_refusal(") < run.index("_check_consent(universe_dir")


# -- the owner door ----------------------------------------------------------------


class _Request:
    def __init__(self, method: str, body: dict | None = None, query: dict | None = None):
        import time

        from tinyassets.auth.middleware import current_identity
        from tinyassets.onboarding import owner_sessions
        self.cookies = {owner_sessions.COOKIE: 'interactive-test-owner'}
        with owner_sessions.store() as conn:
            conn.execute('INSERT OR REPLACE INTO owner_sessions VALUES (?,?,?)',
                         (owner_sessions.hashed('interactive-test-owner'),
                          json.dumps({'user_id': current_identity().user_id}), time.time()+300))
        self.method = method
        self._body = json.dumps(body or {}).encode("utf-8")
        self.headers = {"content-type": "application/json", "origin": "https://tinyassets.io",
                        "host": "tinyassets.io", "content-length": str(len(self._body))}
        # GET carries the addressed agent in the query, as a real Starlette
        # request does; the handler reads it the same way on both.
        self.query_params = dict(query or {})

    async def stream(self):
        yield self._body


def test_the_owner_door_reads_and_edits_only_the_callers_own_home(monkeypatch, tmp_path):
    from tinyassets import onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    universe = _universe(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path / "data")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "app_config",
                        lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="owner-1"))
    homes = {"owner-1": "u-alpha"}
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda identity, **_kw: homes.get(identity.user_id, ""))

    def call(method, body=None):
        response = asyncio.run(onboarding._handle_rules(_Request(method, body)))
        return response.status_code, json.loads(response.body)

    status, listing = call("GET")
    assert status == 200 and listing["universe_id"] == "u-alpha"
    assert {r["action_class"] for r in listing["rules"]} == set(agent_rules.ACTION_CLASSES)

    status, refused = call("POST", {"action_class": "access.grant", "behaviour": "do"})
    assert status == 409 and "access" in refused["detail"]
    status, saved = call("POST", {"action_class": "app.write", "behaviour": "hand_off",
                                  "connection": "bank"})
    assert status == 200 and saved["saved"]["behaviour"] == "hand_off"
    assert agent_rules.decide(universe, "app.write", connection="bank").behaviour == HAND_OFF

    # A caller with no home of their own edits nothing.
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="stranger"))
    assert call("GET")[0] == 404


# -- gpt-6-astra on #4193 ------------------------------------------------------------


def test_overlapping_narrow_rules_go_to_the_stricter(tmp_path):
    """Connection does not outrank operation: DELETE on the bank hands off."""
    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "app.write", DO, connection="bank")
    agent_rules.set_rule(universe, "app.write", HAND_OFF, operation="DELETE")
    assert agent_rules.decide(universe, "app.write", connection="bank",
                              operation="DELETE").behaviour == HAND_OFF


def test_removing_a_handback_restriction_needs_confirmation(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "money.move", DO, confirm_handback=True)
    narrowed = agent_rules.set_rule(universe, "money.move", HAND_OFF, connection="bank")
    with pytest.raises(agent_rules.RuleRefused):
        agent_rules.delete_rule(universe, narrowed.id)
    assert agent_rules.decide(universe, "money.move", connection="bank").behaviour == HAND_OFF
    assert agent_rules.delete_rule(universe, narrowed.id, confirm_handback=True)


def test_removing_a_handback_rule_under_a_handback_default_needs_nothing(tmp_path):
    universe = _universe(tmp_path)
    narrowed = agent_rules.set_rule(universe, "money.move", HAND_OFF, connection="bank")
    assert agent_rules.delete_rule(universe, narrowed.id) is True


@pytest.mark.parametrize("bad", [15.9, True, "3", -1, 2 ** 70])
def test_the_owner_door_takes_only_a_rule_id_to_delete(monkeypatch, tmp_path, bad):
    from tinyassets import onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    _universe(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path / "data")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "app_config",
                        lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="owner-1"))
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **_kw: "u-alpha")
    response = asyncio.run(onboarding._handle_rules(_Request("POST", {"delete": bad})))
    assert response.status_code == 400


@pytest.mark.parametrize("kind, failure_class", [
    ("rule_ask_first", "rule_requires_approval"),
    ("rule_hand_off", "rule_hand_off"),
    ("rules_unreadable", "rules_unreadable"),
])
def test_a_rule_refusal_gets_its_own_class_and_advice(kind, failure_class):
    from tinyassets import runs

    line = f"external write failed - authenticated_external_call [{kind}] refused"
    assert runs._classify_external_write(line.lower()) == failure_class
    advice = runs.external_write_suggested_action(failure_class)
    assert advice and "yours to fix" not in advice


# -- D1b: declared operation kinds -----------------------------------------------------


def test_an_undeclared_operation_is_a_write(tmp_path):
    assert agent_rules.classify(_universe(tmp_path), "stripe", "post", "/v1/charges") == (
        "app.write", "POST")


def test_a_declared_payment_is_handed_back_by_default(tmp_path):
    from tinyassets.effectors.authenticated_external_call import _rule_refusal

    universe = _universe(tmp_path)
    agent_rules.declare_kind(universe, "stripe", "payment", method="POST",
                             path_prefix="/v1/charges")
    assert agent_rules.classify(universe, "stripe", "POST", "/v1/charges/ch_1") == (
        "money.move", "POST")
    refusal = _rule_refusal(universe, "stripe", "POST", "/v1/charges")
    assert refusal["error_kind"] == "rule_hand_off"
    # The same connection's undeclared paths are ordinary writes.
    with _approving_review():
        assert _rule_refusal(universe, "stripe", "POST", "/v1/customers") is None


def test_the_longest_prefix_and_a_specific_method_win(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.declare_kind(universe, "gh", "read", path_prefix="/")
    agent_rules.declare_kind(universe, "gh", "write", method="POST", path_prefix="/repos")
    agent_rules.declare_kind(universe, "gh", "access", method="PUT",
                             path_prefix="/repos/o/r/collaborators")
    assert agent_rules.classify(universe, "gh", "GET", "/user")[0] == "app.read"
    assert agent_rules.classify(universe, "gh", "POST", "/repos/o/r/issues")[0] == "app.write"
    assert agent_rules.classify(universe, "gh", "PUT",
                                "/repos/o/r/collaborators/bob")[0] == "access.grant"
    assert agent_rules.classify(universe, "gh", "GET", "/repositories")[0] == "app.read", (
        "a prefix matches whole path segments")


def test_a_message_kind_asks_first_by_default(tmp_path):
    from tinyassets.effectors.authenticated_external_call import _rule_refusal

    universe = _universe(tmp_path)
    agent_rules.declare_kind(universe, "slack", "message", path_prefix="/api/chat.postMessage")
    assert _rule_refusal(universe, "slack", "POST",
                         "/api/chat.postMessage")["error_kind"] == "rule_ask_first"


@pytest.mark.parametrize("prefix", ["v1", "/v1?x=1", "/v1#f"])
def test_a_bad_prefix_or_kind_is_refused(tmp_path, prefix):
    universe = _universe(tmp_path)
    with pytest.raises(agent_rules.RuleRefused):
        agent_rules.declare_kind(universe, "c", "read", path_prefix=prefix)
    with pytest.raises(agent_rules.RuleRefused):
        agent_rules.declare_kind(universe, "c", "steal")


def test_the_request_path_is_read_from_a_url_or_a_path():
    from tinyassets.effectors.authenticated_external_call import _request_path

    assert _request_path({"url": "https://api.x.com/v1/a?b=1"}) == "/v1/a"
    assert _request_path({"path": "/v1/b?c=2"}) == "/v1/b"
    assert _request_path({}) == "/"


# -- gpt-6-astra on #4199 ---------------------------------------------------------------


def test_a_fragment_cannot_hide_a_declared_payment(tmp_path):
    from tinyassets.effectors.authenticated_external_call import _request_path, _rule_refusal

    universe = _universe(tmp_path)
    agent_rules.declare_kind(universe, "stripe", "payment", method="POST",
                             path_prefix="/v1/charges")
    assert _request_path({"path": "/v1/charges#"}) == "/v1/charges"
    refusal = _rule_refusal(universe, "stripe", "POST", _request_path({"path": "/v1/charges#"}))
    assert refusal["error_kind"] == "rule_hand_off"


def test_a_trailing_slash_is_one_spelling(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.declare_kind(universe, "stripe", "payment", method="POST",
                             path_prefix="/v1/charges/")
    assert agent_rules.list_kinds(universe)[0].path_prefix == "/v1/charges"
    assert agent_rules.classify(universe, "stripe", "POST", "/v1/charges/")[0] == "money.move"
    with pytest.raises(agent_rules.RuleRefused):
        # An any-method read on the same path would loosen the payment.
        agent_rules.declare_kind(universe, "stripe", "read", path_prefix="/v1/charges/")


def test_declaring_read_over_an_ask_first_write_needs_confirmation(tmp_path):
    universe = _universe(tmp_path)
    agent_rules.set_rule(universe, "app.write", ASK_FIRST, connection="gh")
    with pytest.raises(agent_rules.RuleRefused) as refused:
        agent_rules.declare_kind(universe, "gh", "read", method="POST", path_prefix="/graphql")
    assert "Confirm" in str(refused.value)
    agent_rules.declare_kind(universe, "gh", "read", method="POST", path_prefix="/graphql",
                             confirm=True)
    assert agent_rules.classify(universe, "gh", "POST", "/graphql")[0] == "app.read"


def test_removing_a_payment_declaration_needs_confirmation(tmp_path):
    universe = _universe(tmp_path)
    declared = agent_rules.declare_kind(universe, "stripe", "payment", path_prefix="/v1")
    with pytest.raises(agent_rules.RuleRefused):
        agent_rules.delete_kind(universe, declared.id)
    assert agent_rules.list_kinds(universe), "nothing removed without confirmation"
    assert agent_rules.delete_kind(universe, declared.id, confirm=True) is True


def test_the_owner_door_reads_and_edits_the_ADDRESSED_agents_rules(monkeypatch, tmp_path):
    """Harness §4.18: the panel edits the rules of the agent being talked to.

    Every per-agent store already keys on the agent and defaults to ``main``;
    this door passed nothing, so the panel read and wrote MAIN's rules whoever
    the conversation was with -- an owner could switch a custom agent's review
    off in the UI and silently change main instead.

    ``addressed_agents.resolve`` is stubbed to its contract (a binding for the
    owner's own agent, ``AgentNotAddressable`` for anything else); the real
    resolution is covered by tests/test_converse_addressed_agent.py. The RULE
    stores here are the real ones.
    """
    from types import SimpleNamespace as NS

    from tinyassets import addressed_agents, agent_review, onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    _universe(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path / "data")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "app_config",
                        lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: NS(user_id="owner-1"))
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **_kw: "u-alpha")

    def resolve(_base, *, universe_id, owner, agent_id):
        assert (universe_id, owner) == ("u-alpha", "owner-1"), "resolved outside the caller's home"
        wanted = "" if agent_id is None else str(agent_id)
        if wanted in ("", "main"):
            return None                      # the main agent, as the real one does
        if wanted == "a-weaver":
            return NS(agent_id="a-weaver", name="Evidence Weaver")
        raise addressed_agents.AgentNotAddressable(f"no agent {wanted!r} here")

    monkeypatch.setattr(addressed_agents, "resolve", resolve)

    def call(method, body=None, query=None):
        response = asyncio.run(onboarding._handle_rules(_Request(method, body, query)))
        return response.status_code, json.loads(response.body)

    # The listing says whose rules it is, on both the default and the addressed read.
    assert call("GET")[1]["agent_id"] == "main"
    assert call("GET", query={"agent_id": "a-weaver"})[1]["agent_id"] == "a-weaver"

    # Switching the weaver's review off must not touch main's.
    status, saved = call("POST", {"agent_id": "a-weaver", "confirm": True,
                                  "review": {"action_class": "people.message", "enabled": False}})
    assert status == 200 and saved["agent_id"] == "a-weaver"
    assert "people.message" in saved["review_off"]
    assert "people.message" not in call("GET")[1]["review_off"], (
        "switching a custom agent's review off changed MAIN's review")
    universe = tmp_path / "data" / "u-alpha"
    assert "people.message" in agent_review.switched_off(universe, "a-weaver")
    assert "people.message" not in agent_review.switched_off(universe, "main")

    # A saved rule lands on the addressed agent and leaves main's behaviour alone.
    def behaviours(query=None):
        return {r["action_class"]: r["behaviour"] for r in call("GET", query=query)[1]["rules"]}

    before = behaviours()
    assert before["app.read"] != "ask_first", "the fixture already had the saved value"
    status, saved = call("POST", {"agent_id": "a-weaver", "action_class": "app.read",
                                  "behaviour": "ask_first"})
    assert status == 200 and saved["saved"]["behaviour"] == "ask_first"
    assert behaviours() == before, "a rule saved for a custom agent changed main's rules"
    # The LISTING must read the addressed agent's rules, not just echo its id:
    # without this the handler can return main's rules under agent_id=a-weaver,
    # which is the silent wrong-agent bug this slice exists to fix.
    assert behaviours({"agent_id": "a-weaver"})["app.read"] == "ask_first", (
        "the addressed listing returned another agent's rules")
    assert saved["rules"], "the save's own listing came back empty"
    assert {r["action_class"]: r["behaviour"] for r in saved["rules"]}["app.read"] == "ask_first"
    weaver = {r.action_class: r.behaviour for r in agent_rules.list_rules(universe, "a-weaver")}
    assert weaver["app.read"] == "ask_first"

    # A NARROWED rule the owner then removes. delete_rule scopes by
    # `id AND agent`, so a delete that forgets the agent finds no row and
    # returns False: the owner simply cannot remove a custom agent's narrowed
    # rule, and the panel reports success on a rule that is still there.
    status, narrowed = call("POST", {"agent_id": "a-weaver", "action_class": "app.read",
                                     "behaviour": "ask_first", "connection": "notion"})
    assert status == 200
    rule_id = narrowed["saved"]["id"]
    assert any(r["id"] == rule_id for r in call("GET", query={"agent_id": "a-weaver"})[1]["rules"])
    status, removed = call("POST", {"agent_id": "a-weaver", "delete": rule_id})
    assert status == 200 and removed["deleted"] is True, (
        "the owner could not delete their custom agent's narrowed rule")
    assert not any(r["id"] == rule_id
                   for r in call("GET", query={"agent_id": "a-weaver"})[1]["rules"])

    # And the scoping holds in the other direction: MAIN cannot delete the
    # weaver's rule by its id. Proving deletion WORKS is not the same as
    # proving it is agent-scoped (Codex refute of this PR, finding E).
    status, mine = call("POST", {"agent_id": "a-weaver", "action_class": "app.read",
                                 "behaviour": "ask_first", "connection": "linear"})
    assert status == 200
    weaver_rule = mine["saved"]["id"]
    status, refused = call("POST", {"delete": weaver_rule})      # addressed to main
    assert status == 200 and refused["deleted"] is False, (
        "main deleted a custom agent's rule by id")
    assert any(r["id"] == weaver_rule
               for r in call("GET", query={"agent_id": "a-weaver"})[1]["rules"]), (
        "the weaver's rule was removed by a delete addressed to main")


def test_an_agent_that_is_not_the_owners_is_refused_by_name_not_treated_as_main(
        monkeypatch, tmp_path):
    """A LookupError, so without its own arm this left the door as a 500."""
    from types import SimpleNamespace as NS

    from tinyassets import addressed_agents, onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    _universe(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path / "data")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "app_config",
                        lambda: {"resource": "https://tinyassets.io"})
    monkeypatch.setattr(middleware, "current_identity", lambda: NS(user_id="owner-1"))
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **_kw: "u-alpha")

    def resolve(_base, *, universe_id, owner, agent_id):
        raise addressed_agents.AgentNotAddressable(f"no agent {agent_id!r} here")

    monkeypatch.setattr(addressed_agents, "resolve", resolve)

    for method, body, query in (("GET", None, {"agent_id": "someone-elses"}),
                                ("POST", {"agent_id": "someone-elses",
                                          "action_class": "app.read",
                                          "behaviour": "ask_first"}, None)):
        response = asyncio.run(onboarding._handle_rules(_Request(method, body, query)))
        assert response.status_code == 404, f"{method} did not refuse by name"
        assert json.loads(response.body)["error"] == "agent_not_found"
