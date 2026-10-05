"""Standing sheet decisions preserve the existing owner/action boundaries."""

import json
from contextlib import closing

import pytest

from tests.test_inline_approvals import case as _case
from tests.test_inline_approvals import decision
from tinyassets import agent_rules, approval_scopes, bound_requests

case = _case


@pytest.mark.parametrize("scope", ["task", "site", "always"])
def test_scoped_decision_is_bound_visible_and_revocable(case, monkeypatch, scope):
    from tinyassets.effectors import authenticated_external_call as effector

    home, card, session, raw = case
    sends = []

    class Proxy:
        def request(self, verb, request):
            sends.append(request)
            return {"status": 200, "body": "sent", "headers": {}}

    monkeypatch.setattr(effector, "_open_connection_proxy", lambda **kw: Proxy())
    preview = bound_requests.preview(home, card["request_id"], session, scope=scope)
    payload = {**decision(preview), "scope": scope}
    assert preview["predicate"]["scope"] == scope
    assert bound_requests.decide(home, payload, session)["phase"] == "confirmed"
    assert len(sends) == 1
    rules = [rule for rule in agent_rules.list_rules(home) if rule.kind == "preapproval"]
    assert len(rules) == 1
    assert rules[0].grant["scope"] == scope
    packet = raw["arguments"]
    task_token = approval_scopes.continuation_task.set(
        (str(home.resolve()), card["action"]["envelope"]["subject"]["task_id"])
    )
    try:
        assert approval_scopes.matches(home, packet, "user-1", "main")
        with pytest.raises(bound_requests.RequestRefused):
            approval_scopes.matches(home, packet, "user-1", "other-agent")
        assert agent_rules.delete_rule(home, rules[0].id)
        assert not approval_scopes.matches(home, packet, "user-1", "main")
        assert bound_requests.decide(home, payload, session)["phase"] == "confirmed"
        assert len(sends) == 1
        assert not [r for r in agent_rules.list_rules(home) if r.kind == "preapproval"]
    finally:
        approval_scopes.continuation_task.reset(task_token)


def test_scope_change_requires_a_fresh_token(case):
    home, card, session, _ = case
    once = bound_requests.preview(home, card["request_id"], session)
    site = bound_requests.preview(home, card["request_id"], session, scope="site")
    with pytest.raises(bound_requests.RequestRefused):
        bound_requests.decide(home, {**decision(once), "scope": "site"}, session)
    assert once["approval_token"] != site["approval_token"]
    assert not [r for r in agent_rules.list_rules(home) if r.kind == "preapproval"]


def test_unfinalized_grant_cannot_authorize_and_does_not_change_policy(case):
    home, card, session, raw = case
    preview = bound_requests.preview(home, card["request_id"], session, scope="site")
    original = bound_requests._authority(home, raw["arguments"], "user-1", "main")
    with closing(bound_requests.connect(home)) as conn:
        stored = json.loads(
            conn.execute(
                "SELECT decision_json FROM pending_requests WHERE request_id=?",
                (card["request_id"],),
            ).fetchone()[0]
        )
        stored["choice"] = "approve"
        approval_scopes.materialize(
            home, conn, card["request_id"], stored, card["action"]["envelope"]
        )
        conn.execute(
            "UPDATE pending_requests SET decision_json='{}' WHERE request_id=?",
            (card["request_id"],),
        )
        conn.commit()
    assert not approval_scopes.matches(home, raw["arguments"], "user-1", "main")
    assert bound_requests._authority(home, raw["arguments"], "user-1", "main") == original
    assert preview["scope"] == "site"


@pytest.mark.parametrize("scope", ["task", "site", "always"])
def test_later_agent_effect_uses_grant_and_stop_or_revoke_wins(case, monkeypatch, scope):
    import threading

    from tinyassets import turn_interrupt
    from tinyassets.effectors import authenticated_external_call as effector
    from tinyassets.ta_capabilities import ExecutionContext

    home, card, session, raw = case
    sends = []
    errors = []

    class Proxy:
        def request(self, verb, request):
            sends.append(request)
            if len(sends) == 2:
                # A separate owner worker can revoke while network I/O is live.
                def revoke():
                    try:
                        for rule in agent_rules.list_rules(home):
                            if rule.kind == "preapproval":
                                agent_rules.delete_rule(home, rule.id)
                    except Exception as exc:
                        errors.append(type(exc).__name__)

                thread = threading.Thread(target=revoke)
                thread.start()
                thread.join(timeout=3)
                assert not thread.is_alive()
                assert not errors
            return {"status": 200, "body": "sent", "headers": {}}

    monkeypatch.setattr(effector, "_open_connection_proxy", lambda **kw: Proxy())
    preview = bound_requests.preview(home, card["request_id"], session, scope=scope)
    assert (
        bound_requests.decide(home, {**decision(preview), "scope": scope}, session)["phase"]
        == "confirmed"
    )
    packet = {"sink": raw["executor"], **raw["arguments"]}
    task_id = card["action"]["envelope"]["subject"]["task_id"]
    context = approval_scopes.continuation_task.set((str(home.resolve()), task_id))
    try:
        with turn_interrupt.interactive_turn("user-1", home.name):
            result = effector.run_authenticated_external_call_effector(
                node_id="later",
                output_keys=["action"],
                run_state={"action": packet},
                base_path=home,
                run_id="later-run",
                execution_context=ExecutionContext(home.name, "user-1", "main"),
            )
        assert result.get("delivered") is True, result
        assert len(sends) == 2
        assert not errors
        assert not approval_scopes.matches(home, raw["arguments"], "user-1", "main")
        with closing(bound_requests.connect(home)) as conn:
            assert (
                conn.execute(
                    "SELECT state FROM effect_intents WHERE intent_key LIKE 'scoped:%'"
                ).fetchone()[0]
                == "confirmed"
            )
    finally:
        approval_scopes.continuation_task.reset(context)


@pytest.mark.parametrize("change", ["stop", "expiry", "generation", "other-task", "policy"])
def test_task_preapproval_never_survives_its_authority(case, monkeypatch, change):
    from tinyassets.effectors import authenticated_external_call as effector

    home, card, session, raw = case
    monkeypatch.setattr(
        effector, "run_authenticated_external_call_effector", lambda **kw: {"status": 200}
    )
    preview = bound_requests.preview(home, card["request_id"], session, scope="task")
    bound_requests.decide(home, {**decision(preview), "scope": "task"}, session)
    task = card["action"]["envelope"]["subject"]["task_id"]
    token = approval_scopes.continuation_task.set((str(home.resolve()), task))
    try:
        agent_rules.set_rule(home, "workspace.files", "hand_off")
        assert approval_scopes.matches(home, raw["arguments"], "user-1", "main")
        if change == "stop":
            bound_requests.stop(home, "user-1", "main")
        elif change in ("expiry", "generation"):
            with closing(bound_requests.connect(home)) as conn:
                column = "task_expires_at" if change == "expiry" else "task_generation"
                conn.execute(
                    f"UPDATE activities SET {column}=? WHERE activity_id=?",
                    (1 if change == "expiry" else 42, task),
                )
                conn.commit()
        elif change == "other-task":
            approval_scopes.continuation_task.set((str(home.resolve()), "unrelated"))
        else:
            agent_rules.set_rule(home, "app.write", "hand_off")
        assert not approval_scopes.matches(home, raw["arguments"], "user-1", "main")
    finally:
        approval_scopes.continuation_task.reset(token)


def test_recovery_invalidates_unfinalized_scope_without_dispatch(case, monkeypatch):
    from tinyassets import request_continuations

    home, card, session, raw = case
    preview = bound_requests.preview(home, card["request_id"], session, scope="site")

    def crash(*args):
        raise RuntimeError("crash before grant finalization")

    monkeypatch.setattr(approval_scopes, "materialize", crash)
    with pytest.raises(RuntimeError):
        bound_requests.decide(home, {**decision(preview), "scope": "site"}, session)
    assert request_continuations.recover(home, run=lambda *_: pytest.fail("unexpected wake")) == 0
    assert not approval_scopes.matches(home, raw["arguments"], "user-1", "main")
    with closing(bound_requests.connect(home)) as conn:
        recovered = bound_requests.card(conn, card["request_id"])
        assert recovered["phase"] == "failed"
        assert recovered["status"] == "unresolved"
        stored = json.loads(
            conn.execute(
                "SELECT decision_json FROM pending_requests WHERE request_id=?",
                (card["request_id"],),
            ).fetchone()[0]
        )
        assert stored["invalidated"] is True
    fresh = bound_requests.preview(home, card["request_id"], session)
    assert fresh["approval_unavailable"]
    assert bound_requests.decide(home, decision(fresh, "skip"), session)["status"] == "answered"
