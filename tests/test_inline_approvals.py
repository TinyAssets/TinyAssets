"""Bound owner decisions exercise the ordinary generic effector and its guards."""

import json
import time
from contextlib import closing

import pytest

from tests.test_authenticated_external_call_effector import _setup
from tinyassets import agent_rules, turn_interrupt
from tinyassets import bound_requests as bound
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import owner_sessions
from tinyassets.storage import pending_requests


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _, home, _ = _setup(tmp_path)
    agent_rules.set_rule(home, "app.write", "ask_first")
    identity = Identity(
        user_id="user-1", username="owner", capabilities=["tinyassets.universe.write"]
    )
    raw = {
        "executor": "authenticated_external_call",
        "arguments": {
            "connection_id": "conn-http",
            "grant_id": "grant-http",
            "verb": "POST",
            "request": {"path": "/v1/messages", "body": "exact body\n"},
        },
    }
    with identity_context(identity), turn_interrupt.interactive_turn("user-1", home.name):
        card = bound.capture(home, raw)
    cookie = "test-protected-session"
    with owner_sessions.store() as conn:
        conn.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (owner_sessions.hashed(cookie), json.dumps(identity.to_dict()), time.time() + 3600),
        )
    session = owner_sessions.lookup(cookie)
    return home, card, session, raw


def decision(preview, choice="approve"):
    return {
        "request_id": preview["request_id"],
        "expected_revision": preview["revision"],
        "action_sha256": preview["action_sha256"],
        "approval_token": preview["approval_token"],
        "decision": choice,
        "scope": "once",
    }


def test_protected_preview_preserves_bytes_and_never_uses_legacy_prose(case):
    home, card, session, _ = case
    with closing(bound.connect(home)) as conn:
        conn.execute("UPDATE pending_requests SET title='Wrong destination',body='fake approval'")
        conn.commit()
    preview = bound.preview(home, card["request_id"], session)
    assert preview["draft"] == "exact body\n"
    assert preview["destination"] == "https://api.example.com/v1/messages"
    assert "Wrong destination" not in json.dumps(preview)
    assert "real-vault-http-token" not in json.dumps(preview)
    assert "approval_token" not in json.dumps(
        pending_requests.get_request(home, card["request_id"])
    )


def test_approve_executes_once_through_ordinary_effector(case, monkeypatch):
    from tinyassets.effectors import authenticated_external_call as effector

    home, card, session, _ = case
    calls = []

    class Proxy:
        def request(self, verb, request):
            calls.append((verb, request))
            return {"status": 200, "body": "sent", "headers": {}}

    monkeypatch.setattr(effector, "_open_connection_proxy", lambda **kw: Proxy())
    preview = bound.preview(home, card["request_id"], session)
    result = bound.decide(home, decision(preview), session)
    assert result["phase"] == "confirmed", result
    assert len(calls) == 1
    assert calls[0][1]["body"] == "exact body\n"
    assert bound.decide(home, decision(preview), session)["phase"] == "confirmed"
    assert len(calls) == 1
    assert not any("preapproval" == getattr(r, "kind", "") for r in agent_rules.list_rules(home))


@pytest.mark.parametrize(
    "change",
    ["token", "session", "owner", "revision", "scope", "logout", "policy", "stop", "expiry"],
)
def test_stale_forged_or_revoked_approval_never_dispatches(case, monkeypatch, change):
    home, card, session, _ = case
    from tinyassets.effectors import authenticated_external_call as effector

    monkeypatch.setattr(
        effector,
        "run_authenticated_external_call_effector",
        lambda **kw: pytest.fail("Unauthorized effect dispatched"),
    )
    preview = bound.preview(home, card["request_id"], session)
    payload = decision(preview)
    if change == "token":
        payload["approval_token"] = "forged"
    elif change == "session":
        session = {**session, "session_hash": "another-session"}
    elif change == "owner":
        session = {**session, "identity_json": json.dumps({"user_id": "bob"})}
    elif change == "revision":
        payload["expected_revision"] += 1
    elif change == "scope":
        payload["scope"] = "always"
    elif change == "logout":
        owner_sessions.revoke("test-protected-session")
    elif change == "policy":
        agent_rules.set_rule(home, "app.write", "hand_off")
    elif change == "stop":
        bound.stop(home, "user-1", "main")
    elif change == "expiry":
        with closing(bound.connect(home)) as conn:
            conn.execute("UPDATE activities SET task_expires_at=1")
            conn.commit()
    with pytest.raises(bound.RequestRefused):
        bound.decide(home, payload, session)


def test_edit_invalidates_token_and_denial_records_one_wake(case):
    home, card, session, _ = case
    old = bound.preview(home, card["request_id"], session)
    new = bound.preview(home, card["request_id"], session, draft="changed\n", edit=True)
    with pytest.raises(bound.RequestRefused):
        bound.decide(home, decision(old), session)
    assert new["revision"] == old["revision"] + 1
    assert new["draft"] == "changed\n"
    bound.decide(home, decision(new, "deny"), session)
    with closing(bound.connect(home)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM effect_intents").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM activity_events WHERE wake_required=1").fetchone()[0]
            == 1
        )


def test_secrets_and_forged_provenance_are_rejected(case):
    _, _, _, raw = case
    with pytest.raises(bound.RequestRefused):
        bound.validate_action({**raw, "subject": {"owner": "bob"}})
    raw["arguments"]["request"]["headers"] = {"Authorization": "Bearer private"}
    with pytest.raises(bound.RequestRefused):
        bound.validate_action(raw)


def test_wake_retries_until_ack_and_retains_tombstone(case):
    from tinyassets.request_continuations import recover

    home, card, session, _ = case
    preview = bound.preview(home, card["request_id"], session)
    bound.decide(home, decision(preview, "deny"), session)
    assert recover(home, lambda *a: {"error": "no_power"}) == 0
    assert recover(home, lambda *a: {"reply": "Continued"}) == 1
    assert recover(home, lambda *a: pytest.fail("Duplicate computation")) == 0
    with closing(bound.connect(home)) as conn:
        row = conn.execute("SELECT * FROM activity_events WHERE wake_required=1").fetchone()
        assert row["processed_at"]
        assert json.loads(row["result_json"]) == {"reply": "Continued"}


def test_stopped_wake_stays_held_unacknowledged(case):
    from tinyassets.request_continuations import recover

    home, card, session, _ = case
    bound.decide(home, decision(bound.preview(home, card["request_id"], session), "deny"), session)
    bound.stop(home, "user-1", "main")
    assert recover(home, lambda *a: pytest.fail("Stopped task resumed")) == 0
    with closing(bound.connect(home)) as conn:
        assert (
            conn.execute(
                "SELECT processed_at FROM activity_events WHERE wake_required=1"
            ).fetchone()[0]
            is None
        )


def test_policy_is_rechecked_after_reservation_before_dispatch(case, monkeypatch):
    home, card, session, _ = case
    preview = bound.preview(home, card["request_id"], session)
    original = bound._current
    checks = 0

    def changed(*args):
        nonlocal checks
        checks += 1
        if checks == 2:
            agent_rules.set_rule(home, "app.write", "hand_off")
        return original(*args)

    monkeypatch.setattr(bound, "_current", changed)
    with pytest.raises(bound.RequestRefused, match="Authority changed"):
        bound.decide(home, decision(preview), session)
    with closing(bound.connect(home)) as conn:
        assert conn.execute("SELECT state FROM effect_intents").fetchone()[0] == "planned"


def test_unrelated_rule_edit_preserves_the_displayed_policy(case):
    home, card, session, _ = case
    old = bound.preview(home, card["request_id"], session)
    agent_rules.set_rule(home, "workspace.files", "ask_first")
    new = bound.preview(home, card["request_id"], session)
    assert new["action_sha256"] == old["action_sha256"]


def test_another_home_cannot_borrow_the_bound_grant(case):
    home, _, _, raw = case
    other = home.parent / "other-home"
    other.mkdir()
    owner = Identity(user_id="user-1", username="owner")
    with identity_context(owner), turn_interrupt.interactive_turn("user-1", other.name):
        with pytest.raises(bound.RequestRefused, match="Connection authority"):
            bound.capture(other, raw)


def test_initiating_agents_rule_is_used_instead_of_mains(case, monkeypatch):
    from tinyassets import addressed_agents
    from tinyassets.effectors import authenticated_external_call as effector

    home, _, session, raw = case
    monkeypatch.setattr(addressed_agents, "resolve", lambda *a, **kw: None)
    agent_rules.set_rule(home, "app.write", "hand_off", agent="main")
    agent_rules.set_rule(home, "app.write", "ask_first", agent="researcher")
    owner = Identity(user_id="user-1", username="owner")
    with (
        identity_context(owner),
        turn_interrupt.interactive_turn("user-1", home.name, agent_id="researcher"),
    ):
        card = bound.capture(home, raw)
    assert card["agent"] == "researcher"
    calls = []

    class Proxy:
        def request(self, verb, request):
            calls.append(verb)
            return {"status": 200, "body": "done"}

    monkeypatch.setattr(effector, "_open_connection_proxy", lambda **kw: Proxy())
    result = bound.decide(home, decision(bound.preview(home, card["request_id"], session)), session)
    assert result["phase"] == "confirmed"
    assert calls == ["POST"]


def test_bearer_answer_alias_cannot_execute_bound_action(case, monkeypatch):
    from tinyassets.api import pending_requests as api

    home, card, _, _ = case
    monkeypatch.setattr(api, "_owner_gate", lambda _: (home.name, home, None))
    for payload in (
        {"values": {}},
        {"decision": "approve"},
        {"dismiss": True},
        {"item_id": "fake", "values": {}},
        {"decision": "retry"},
    ):
        result = api.answer_request(
            universe_id=home.name, payload={"request_id": card["request_id"], **payload}
        )
        assert result["error"] == "interactive_approval_required"


def test_interrupted_and_fenced_wake_attempts_do_not_ack(case):
    from tinyassets.request_continuations import recover

    home, card, session, _ = case
    bound.decide(home, decision(bound.preview(home, card["request_id"], session), "deny"), session)

    def interrupted(*args):
        raise RuntimeError("process interrupted before processed ack")

    with pytest.raises(RuntimeError):
        recover(home, interrupted)
    with closing(bound.connect(home)) as conn:
        old = conn.execute(
            "SELECT attempt_ref FROM activity_events WHERE wake_required=1"
        ).fetchone()[0]

    def fenced(*args):
        with closing(bound.connect(home)) as conn:
            conn.execute(
                "UPDATE activity_events SET attempt_ref='replacement' WHERE wake_required=1"
            )
            conn.commit()
        return {"reply": "cannot commit"}

    assert recover(home, fenced) == 0
    assert recover(home, lambda *a: {"reply": "recovered"}) == 1
    with closing(bound.connect(home)) as conn:
        row = conn.execute("SELECT * FROM activity_events WHERE wake_required=1").fetchone()
        assert row["attempt_ref"] != old
        assert json.loads(row["result_json"]) == {"reply": "recovered"}


def test_wake_and_processed_tombstone_survive_event_trimming(case):
    from tinyassets import agent_activities
    from tinyassets.request_continuations import recover

    home, card, session, _ = case
    bound.decide(home, decision(bound.preview(home, card["request_id"], session), "deny"), session)
    task = agent_activities.get(home, card["action"]["envelope"]["subject"]["task_id"])
    for processed in (False, True):
        if processed:
            assert recover(home, lambda *a: {"reply": "done"}) == 1
        with closing(bound.connect(home)) as conn:
            for _ in range(agent_activities.MAX_EVENTS + 1):
                agent_activities._event(conn, task, "created")
            conn.commit()
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM activity_events WHERE dedupe_key IS NOT NULL"
                ).fetchone()[0]
                == 1
            )


def test_effector_opens_one_card_for_repeated_attempt_in_same_turn(case):
    from tinyassets.effectors import authenticated_external_call as effector

    home, _, _, raw = case
    owner = Identity(user_id="user-1", username="owner")
    packet = {"sink": raw["executor"], **raw["arguments"]}
    with identity_context(owner), turn_interrupt.interactive_turn("user-1", home.name):
        results = [
            effector.run_authenticated_external_call_effector(
                node_id="n",
                output_keys=["action"],
                run_state={"action": packet},
                base_path=home,
                run_id="run",
            )
            for _ in range(2)
        ]
    assert results[0]["error_kind"] == "rule_ask_first"
    assert results[0]["request_id"] == results[1]["request_id"]


def test_review_setting_revision_invalidates_even_if_switched_back(case):
    from tinyassets import agent_review

    home, card, session, _ = case
    preview = bound.preview(home, card["request_id"], session)
    agent_review.set_review(home, "app.write", True)
    agent_review.set_review(home, "app.write", False, confirm=True)
    with pytest.raises(bound.RequestRefused, match="Authority changed"):
        bound.decide(home, decision(preview), session)
