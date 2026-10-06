"""The shared owner-answer door routes by stored asker, never selected chat."""

import json
from contextlib import closing

import pytest

from tests.owner_answer import answer_request
from tests.test_owner_notifications import _home
from tinyassets import request_answers, request_continuations, turn_interrupt
from tinyassets.api.pending_requests import request_from_user
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.custom_agents import create_binding, publish_definition
from tinyassets.storage import pending_requests as store

OWNER = "answer-owner"
OTHER = "other-owner"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    home = _home(tmp_path, "answer-home", OWNER)
    _home(tmp_path, "other-home", OTHER)
    definition = publish_definition(tmp_path, author_id=OWNER, payload={
        "schema_version": 1, "name": "Social Media Manager", "components": {
            "identity": {"kind": "soul", "config": {"instructions": "Manage social drafts."}},
        },
    })
    binding = create_binding(tmp_path, universe_id=home.name,
                             definition_id=definition["agent_definition_id"], created_by=OWNER,
                             payload={"schema_version": 1, "name": "Social Media Manager"})
    with identity_context(Identity(user_id=OWNER, username=OWNER,
                                   capabilities=["tinyassets.universe.write"])):
        yield home, binding["agent_binding_id"]


def ask(home, agent="main", **extra):
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent) as live:
        row = request_from_user(universe_id=home.name, payload={
            "kind": "Review", "title": "Reply ready on X", "body": "Check the draft",
            "fields": [{"name": "reply", "type": "text", "label": "Your reply"}],
            "action": {"type": "answer"}, **extra,
        })
        assert row["asking_context"]["turn"] == live.live_id
    assert not row.get("error"), row
    assert row["agent"] == agent
    return row


def drain(home):
    received = []

    def run(_home, payload):
        received.append(payload)
        return {"status": "completed"}

    request_continuations.recover(home, run=run)
    assert request_continuations.recover(home, run=run) == 0
    return received


@pytest.mark.parametrize("surface", ["app_sheet", "inline_card", "phone_push", "desktop"])
def test_subagent_answer_through_shared_owner_door(world, surface):
    home, agent = world
    row = ask(home, agent)
    payload = {"request_id": row["request_id"], "values": {"reply": "Use the right account"}}
    if surface == "phone_push":
        payload = {"request_id": row["request_id"], "reply": "Use the right account",
                   "reply_id": "phone-1"}
    result = answer_request(universe_id=home.name, payload=payload)
    assert not result.get("error"), result
    received, = drain(home)
    assert received["agent"] == agent
    assert received["owner"] == OWNER
    assert "Use the right account" in json.dumps(received["outcome"])


def test_main_asked_and_duplicate_reply(world):
    home, _ = world
    row = ask(home)
    payload = {"request_id": row["request_id"], "reply": "Proceed tomorrow", "reply_id": "one"}
    for _ in range(2):
        assert answer_request(universe_id=home.name, payload=payload)["status"] == "reply_queued"
    received, = drain(home)
    assert received["agent"] == "main"
    assert store.get_request(home, row["request_id"])["status"] == "pending"


@pytest.mark.parametrize("attack", ["answer_owner", "binding_owner", "home_owner"])
def test_cross_owner_refused_before_answer_mutation(world, attack):
    home, agent = world
    row = ask(home, agent)
    if attack == "binding_owner":
        from tinyassets.custom_agents import _agent_connect

        with _agent_connect(home.parent) as conn:
            conn.execute("UPDATE agent_bindings SET created_by=? WHERE agent_binding_id=?",
                         (OTHER, agent))
    if attack == "home_owner":
        with closing(store._db(home)) as conn, conn:
            origin = {**row["asking_context"], "owner": OTHER}
            conn.execute("UPDATE pending_requests SET asking_context_json=? WHERE request_id=?",
                         (json.dumps(origin), row["request_id"]))
    actor = OTHER if attack == "answer_owner" else OWNER
    with identity_context(Identity(user_id=actor, username=actor, capabilities=[])):
        result = answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "values": {"reply": "steal"}})
    assert result["error"] == "not_found"
    assert store.get_request(home, row["request_id"])["status"] == "pending"
    assert drain(home) == []


def test_removed_asking_agent_falls_back_with_note(world):
    from tinyassets.custom_agents import _agent_connect

    home, agent = world
    row = ask(home, agent)
    with _agent_connect(home.parent) as conn:
        conn.execute("DELETE FROM agent_bindings WHERE agent_binding_id=?", (agent,))
    assert not answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "Keep this feedback"}}).get("error")
    received, = drain(home)
    assert received["agent"] == "main"
    assert agent in received["routing_note"] and "removed or retired" in received["routing_note"]


def test_each_item_reaches_asking_agent(world):
    home, agent = world
    row = ask(home, agent, items=[{"item_id": "a", "title": "Draft A", "fields": [
        {"name": "reply", "type": "text", "label": "Reply"}]}])
    result = answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "item_id": "a", "values": {"reply": "Good"}})
    assert not result.get("error"), result
    received, = drain(home)
    assert received["agent"] == agent and received["outcome"]["item_id"] == "a"


def test_notification_reply_uses_asking_agent(world):
    from tinyassets.api.agent_notifications import notify

    home, agent = world
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent):
        row = notify(universe_id=home.name, payload={"title": "Published", "body": "Draft is live"})
    result = answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "reply": "Thanks", "reply_id": "notification-1"})
    assert result["status"] == "reply_queued"
    received, = drain(home)
    assert received["agent"] == agent


@pytest.mark.parametrize("decision", ["allowed", "declined"])
def test_install_decisions_do_not_enqueue_model_work_but_explicit_replies_do(world, decision):
    home, agent = world
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent):
        row = store.create_request(
            home, kind="Install", title="Install package", body="", fields=[],
            action={"type": "install"}, dedupe_key="install-decision", agent=agent,
        )
    assert store.resolve_request(home, row["request_id"], status="answered", decision=decision)
    assert drain(home) == []
    request_answers.reply(home, row, "Explain this install", "intentional-reply")
    received, = drain(home)
    assert received["agent"] == agent
    assert received["outcome"]["reply"] == "Explain this install"


def test_dispatch_targets_converse_conversation(world, monkeypatch):
    home, agent = world
    row = ask(home, agent)
    calls = []

    def converse(**kwargs):
        calls.append(kwargs)
        return json.dumps({"status": "completed"})

    monkeypatch.setattr("tinyassets.universe_server.converse", converse)
    answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "No blank line"}})
    assert request_continuations.recover(home) == 1
    call, = calls
    assert call["agent_id"] == agent and call["graph_id"] == home.name
    assert "No blank line" in call["message"]


def test_workflow_and_native_launch_preserve_asking_provenance(world, monkeypatch):
    from tinyassets.engine_steering import turn_of

    home, agent = world
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent):
        request_answers.remember_workflow(home.parent, {
            "author": OWNER, "branch_def_id": "social-chat"})
    # A later scheduled run has no interactive turn to inherit.
    with request_answers.workflow_launch(home, owner=OWNER, session_key="node:social",
                                         run_id="run-social", workflow_id="social-chat") as launch:
        assert turn_of() == launch["turn"]
    # Native tools arrive on another request/thread: only the server's exact
    # launch key recovers the origin, without accepting fields in the ask.
    monkeypatch.setattr("tinyassets.engine_steering._route_params",
                        lambda: ("node:social", launch["turn"]))
    row = request_from_user(universe_id=home.name, payload={
        "kind": "Workflow", "title": "Social workflow asks", "action": {"type": "answer"},
        "fields": [{"name": "reply", "type": "text", "label": "Reply"}],
        "agent": "main", "run_id": "caller-cannot-change-origin",
    })
    assert row["agent"] == agent
    assert row["asking_context"]["run_id"] == "run-social"
    assert row["asking_context"]["workflow_id"] == "social-chat"
    assert not answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "Ship it"}}).get("error")
    received, = drain(home)
    assert received["agent"] == agent and received["run_id"] == "run-social"


def test_unacknowledged_delivery_retries_without_changing_agent(world):
    home, agent = world
    row = ask(home, agent)
    answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "Try later"}})
    assert request_continuations.recover(home, run=lambda *_: {"error": "no_model"}) == 0
    with closing(store._db(home)) as conn, conn:
        conn.execute("UPDATE request_answer_deliveries SET next_attempt_at=0")
    received, = drain(home)
    assert received["agent"] == agent


def test_workflow_provenance_preserves_live_steering_turn(world):
    from tinyassets.engine_steering import turn_of

    home, agent = world
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent) as live:
        for session in ("node:first", "node:second"):
            with request_answers.workflow_launch(home, owner=OWNER, session_key=session,
                                                 run_id="run", workflow_id="workflow") as origin:
                assert turn_of() == live.live_id == origin["turn"]
                assert origin["agent"] == agent
        assert turn_of() == live.live_id
    with closing(store._db(home)) as conn:
        assert conn.execute("SELECT count(*) FROM request_asking_launches").fetchone()[0] == 2


def test_non_admin_principal_records_no_asker_but_keeps_running(world):
    home, _ = world
    with request_answers.workflow_launch(home, owner=OTHER, session_key="node:agent",
                                         run_id="run", workflow_id="workflow") as origin:
        assert origin is None
    with identity_context(Identity(user_id=OTHER, username=OTHER, capabilities=[])):
        assert request_answers.capture(home, "main") == {}


def co_admin(home):
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(home.parent, universe_id=home.name, actor_id=OTHER,
                          permission="admin", granted_by=OWNER)


def test_co_admin_does_not_block_the_recorded_owner(world):
    """Review 4532 r1: a second admin made every answer `not_found`."""
    home, agent = world
    before = ask(home, agent, title="Asked before the grant")
    co_admin(home)
    after = ask(home, agent, title="Asked after the grant")
    assert after["asking_context"]["owner"] == OWNER
    with request_answers.workflow_launch(home, owner=OWNER, session_key="node:agent",
                                         run_id="run", workflow_id="workflow") as origin:
        assert origin["owner"] == OWNER
    for row in (before, after):
        assert not answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "values": {"reply": "Still mine"}}).get("error")
    assert answer_request(universe_id=home.name, payload={
        "request_id": before["request_id"], "reply": "And a reply", "reply_id": "co-admin",
    })["status"] == "reply_queued"
    received = drain(home)
    assert [r["agent"] for r in received] == [agent] * 3
    assert {r["owner"] for r in received} == {OWNER}


@pytest.mark.parametrize("shape", ["values", "reply"])
def test_co_admin_cannot_answer_or_reply_to_anothers_asker(world, shape):
    """Passes the admin gate, so this exercises the recorded-owner fence itself."""
    home, agent = world
    row = ask(home, agent)
    co_admin(home)
    payload = ({"values": {"reply": "steal"}} if shape == "values"
               else {"reply": "steal", "reply_id": "cross-owner"})
    with identity_context(Identity(user_id=OTHER, username=OTHER,
                                   capabilities=["tinyassets.universe.write"])):
        result = answer_request(universe_id=home.name,
                                payload={"request_id": row["request_id"], **payload})
    assert result["error"] == "not_found"
    assert store.get_request(home, row["request_id"])["status"] == "pending"
    with closing(store._db(home)) as conn:
        assert conn.execute("SELECT count(*) FROM request_answer_deliveries").fetchone()[0] == 0
    assert drain(home) == []


def unrecorded(home, row):
    """An ask pending from before provenance, or created with no admin identity."""
    with closing(store._db(home)) as conn, conn:
        conn.execute("UPDATE pending_requests SET asking_context_json='{}' WHERE request_id=?",
                     (row["request_id"],))


def as_other():
    return identity_context(Identity(user_id=OTHER, username=OTHER,
                                     capabilities=["tinyassets.universe.write"]))


def test_co_admin_attempt_never_stamps_an_unrecorded_ask(world):
    """Review 4532 r2 probe 1: one failed co-admin attempt locked the owner out."""
    home, agent = world
    row = ask(home, agent)
    unrecorded(home, row)
    co_admin(home)
    with as_other():
        result = answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "values": {"reply": "steal"}})
    assert result["error"] == "not_found"
    assert store.get_request(home, row["request_id"])["asking_context"] == {}
    assert not answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "Still mine"}}).get("error")
    assert store.get_request(home, row["request_id"])["asking_context"]["owner"] == OWNER
    received, = drain(home)
    assert (received["owner"], received["agent"]) == (OWNER, agent)


def deliveries(home):
    with closing(store._db(home)) as conn:
        return conn.execute("SELECT count(*) FROM request_answer_deliveries").fetchone()[0]


@pytest.mark.parametrize("actor", [OWNER, OTHER])
@pytest.mark.parametrize("shape", ["values", "reply", "declined"])
def test_co_admin_cannot_answer_an_unrecorded_main_ask(world, actor, shape):
    """Review 4532 r2 probe 2, and the explained refusal from its final review."""
    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    co_admin(home)
    payload = {"values": {"reply": "Mine"}, "reply": {"reply": "Mine", "reply_id": "amb"},
               "declined": {"decision": "declined"}}[shape]
    with identity_context(Identity(user_id=actor, username=actor,
                                   capabilities=["tinyassets.universe.write"])):
        result = answer_request(universe_id=home.name,
                                payload={"request_id": row["request_id"], **payload})
    assert result["error"] == "unrecorded_asker_ambiguous"
    assert "doesn't record which agent asked" in result["detail"]
    assert result["request_pending"] is True
    stored = store.get_request(home, row["request_id"])
    assert (stored["status"], stored["asking_context"]) == ("pending", {})
    assert deliveries(home) == 0
    assert drain(home) == []


@pytest.mark.parametrize("actor", [OWNER, OTHER])
def test_any_admin_dismisses_an_ambiguous_unrecorded_ask_delivering_nothing(world, actor):
    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    co_admin(home)
    with identity_context(Identity(user_id=actor, username=actor,
                                   capabilities=["tinyassets.universe.write"])):
        result = answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "dismiss": True})
    assert result["status"] == "dismissed", result
    stored = store.get_request(home, row["request_id"])
    assert (stored["status"], stored["asking_context"]) == ("dismissed", {})
    assert deliveries(home) == 0
    assert drain(home) == []


def test_co_admin_cannot_dismiss_an_unrecorded_ask_with_a_known_owner(world):
    """The binding's creator owns it unambiguously, so a co-admin is cross-owner."""
    home, agent = world
    row = ask(home, agent)
    unrecorded(home, row)
    co_admin(home)
    with as_other():
        result = answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "dismiss": True})
    assert result["error"] == "not_found"
    stored = store.get_request(home, row["request_id"])
    assert (stored["status"], stored["asking_context"]) == ("pending", {})
    assert drain(home) == []


def test_non_admin_cannot_dismiss_an_ambiguous_unrecorded_ask(world):
    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    co_admin(home)
    stranger = "stranger"
    with identity_context(Identity(user_id=stranger, username=stranger,
                                   capabilities=["tinyassets.universe.write"])):
        result = answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "dismiss": True})
    assert result["error"] == "not_found"
    assert store.get_request(home, row["request_id"])["status"] == "pending"


@pytest.mark.parametrize("shape", ["item", "dismiss_reply", "dismiss_item"])
def test_ambiguous_unrecorded_ask_refuses_item_and_mixed_dismissals(world, shape):
    """Only a bare dismissal clears it; an item answer or a dismiss riding an
    answer would still have to guess whose agent asked."""
    home, _ = world
    row = ask(home, items=[{"item_id": "a", "title": "Draft A", "fields": [
        {"name": "reply", "type": "text", "label": "Reply"}]}])
    unrecorded(home, row)
    co_admin(home)
    payload = {"item": {"item_id": "a", "values": {"reply": "Good"}},
               "dismiss_reply": {"dismiss": True, "reply": "Mine", "reply_id": "amb"},
               "dismiss_item": {"dismiss": True, "item_id": "a"}}[shape]
    with as_other():
        result = answer_request(universe_id=home.name,
                                payload={"request_id": row["request_id"], **payload})
    assert result["error"] == "unrecorded_asker_ambiguous", result
    stored = store.get_request(home, row["request_id"])
    assert (stored["status"], stored["asking_context"]) == ("pending", {})
    assert stored["item_answers"]["a"]["status"] == "pending"
    assert deliveries(home) == 0
    assert drain(home) == []


def test_check_explains_only_to_an_admin(world):
    """The ambiguity explanation is for admins; anyone else stays a bare refusal."""
    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    co_admin(home)
    fresh = store.get_request(home, row["request_id"])
    with as_other(), pytest.raises(request_answers.UnrecordedAskerAmbiguous):
        request_answers.check(home, fresh)
    for actor in ("stranger", ""):
        with identity_context(Identity(user_id=actor, username=actor or "anon",
                                       capabilities=["tinyassets.universe.write"])):
            with pytest.raises(PermissionError) as refused:
                request_answers.check(home, fresh)
        assert not isinstance(refused.value, request_answers.UnrecordedAskerAmbiguous)
    assert store.get_request(home, row["request_id"])["asking_context"] == {}


def connection_owned(home, row, owner=OWNER):
    """An older connection ask: its continuation recorded the owner before
    asking_context existed."""
    context = {"kind": "connection", "owner": owner, "home": home.name, "agent": "main",
               "task_id": "act-older", "task_generation": 1, "turn": "t-older"}
    from tinyassets.bound_requests import _schema

    with closing(store._db(home)) as conn, conn:
        _schema(conn)
        conn.execute("UPDATE pending_requests SET context_json=? WHERE request_id=?",
                     (json.dumps(context), row["request_id"]))


def woken(home, request_id):
    with closing(store._db(home)) as conn:
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT payload_json FROM activity_events WHERE request_id=?", (request_id,))]


def test_unrecorded_connection_ask_belongs_to_its_recorded_owner(world):
    """Review 4536 r1: a co-admin's dismissal declined the owner's connection."""
    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    connection_owned(home, row)
    co_admin(home)
    with as_other():
        for payload in ({"dismiss": True}, {"values": {"reply": "steal"}}):
            result = answer_request(universe_id=home.name,
                                    payload={"request_id": row["request_id"], **payload})
            assert result["error"] == "not_found", result
    assert store.get_request(home, row["request_id"])["status"] == "pending"
    assert woken(home, row["request_id"]) == []
    result = answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "dismiss": True})
    assert result["status"] == "dismissed", result
    stored = store.get_request(home, row["request_id"])
    assert stored["asking_context"]["owner"] == OWNER
    wake, = woken(home, row["request_id"])
    assert (wake["owner"], wake["agent"], wake["outcome"]) == (
        OWNER, "main", {"connection": "declined"})


def test_unrecorded_ask_an_activity_waits_on_belongs_to_its_owner(world):
    from tinyassets import agent_activities

    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    activity = agent_activities.create(home, owner_principal=OWNER, title="Post the reply",
                                       brief="Wait for the owner's reply.", origin_kind="ask")
    with closing(agent_activities._connect(home)) as conn, conn:
        conn.execute("UPDATE activities SET status=?, waiting_request_id=? WHERE activity_id=?",
                     (agent_activities.WAITING_ON_YOU, row["request_id"],
                      activity["activity_id"]))
    co_admin(home)
    with as_other():
        result = answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "dismiss": True})
    assert result["error"] == "not_found", result
    assert agent_activities.get(home, activity["activity_id"])["status"] == (
        agent_activities.WAITING_ON_YOU)
    result = answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "Ship it"}})
    assert not result.get("error"), result
    assert agent_activities.get(home, activity["activity_id"])["status"] != (
        agent_activities.WAITING_ON_YOU)
    received, = drain(home)
    assert (received["owner"], received["agent"]) == (OWNER, "main")


def test_sole_admin_still_answers_an_unrecorded_ask(world):
    home, _ = world
    row = ask(home)
    unrecorded(home, row)
    assert not answer_request(universe_id=home.name, payload={
        "request_id": row["request_id"], "values": {"reply": "Mine"}}).get("error")
    received, = drain(home)
    assert (received["owner"], received["agent"]) == (OWNER, "main")


@pytest.mark.parametrize("shape", ["values", "reply"])
def test_caller_supplied_agent_in_an_answer_is_ignored(world, shape):
    home, agent = world
    row = ask(home, agent)
    forged = {"agent": "main", "agent_id": "main", "owner": OTHER,
              "asking_context": {"owner": OTHER, "agent": "main", "home": "other-home"}}
    payload = ({"values": {"reply": "Route me"}} if shape == "values"
               else {"reply": "Route me", "reply_id": "forged"})
    result = answer_request(universe_id=home.name,
                            payload={"request_id": row["request_id"], **payload, **forged})
    assert not result.get("error"), result
    received, = drain(home)
    assert received["agent"] == agent and received["owner"] == OWNER
    assert received["home"] == home.name


def test_answers_cross_the_real_app_route_table(world, monkeypatch):
    """`/app/approvals/answer` as mounted, with the real cookie and origin checks."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from tests.owner_answer import session_cookie
    from tinyassets import onboarding
    from tinyassets.auth.middleware import current_identity

    home, agent = world
    question = ask(home, agent)
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent):
        approval = store.create_request(
            home, kind="Approve", title="Send the post", body="", fields=[],
            action={"type": "approve_action"}, dedupe_key="approve-route", agent=agent)
    assert approval["asking_context"]["agent"] == agent
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config",
                        lambda: {"resource": "https://tinyassets.io/mcp"})
    identity = current_identity()
    cookie = session_cookie()
    inner = Starlette(routes=onboarding.onboarding_routes())

    async def authenticated(scope, receive, send):
        # Stands in for the bearer middleware; the subject under test is the door.
        with identity_context(identity):
            await inner(scope, receive, send)

    client = TestClient(authenticated, base_url="https://tinyassets.io")

    def post(body, *, with_cookie=True):
        headers = {"origin": "https://tinyassets.io"}
        if with_cookie:
            headers["cookie"] = cookie
        return client.post("/app/approvals/answer", headers=headers,
                           json={"universe_id": home.name, **body})

    answered = post({"request_id": question["request_id"], "values": {"reply": "Via HTTP"}})
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "answered"
    replied = post({"request_id": question["request_id"], "reply": "And more",
                    "reply_id": "http-reply"})
    assert replied.json()["status"] == "reply_queued"
    approve_reply = {"request_id": approval["request_id"], "reply": "Yes, send it",
                     "reply_id": "approve-reply"}
    # A reply cannot satisfy a protected approval without the owner session.
    refused = post(approve_reply, with_cookie=False)
    assert (refused.status_code, refused.json()["error"]) == (
        403, "interactive_approval_required")
    decided = post({"request_id": approval["request_id"], "decision": "allowed"})
    assert (decided.status_code, decided.json()["error"]) == (409, "preview_required")
    assert store.get_request(home, approval["request_id"])["status"] == "pending"
    assert post(approve_reply).json()["status"] == "reply_queued"
    assert store.get_request(home, approval["request_id"])["status"] == "pending"
    received = drain(home)
    assert [(r["request_id"], r["agent"]) for r in received] == [
        (question["request_id"], agent), (question["request_id"], agent),
        (approval["request_id"], agent)]


def test_bearer_reply_cannot_reach_a_protected_approval(world):
    from tinyassets.api.pending_requests import answer_request as bearer_answer

    home, agent = world
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent):
        approval = store.create_request(
            home, kind="Approve", title="Send the post", body="", fields=[],
            action={"type": "approve_action"}, dedupe_key="approve-bearer", agent=agent)
    result = bearer_answer(universe_id=home.name, payload={
        "request_id": approval["request_id"], "reply": "Approved", "reply_id": "bearer"})
    assert result["error"] == "interactive_approval_required"
    assert drain(home) == []


def test_resolving_a_protected_approval_enqueues_no_generic_answer(world):
    """`bound_requests` owns that wake; any `resolve_request` path must not add a second."""
    home, agent = world
    with turn_interrupt.interactive_turn(OWNER, home.name, agent_id=agent):
        approval = store.create_request(
            home, kind="Approve", title="Send the post", body="", fields=[],
            action={"type": "approve_action"}, dedupe_key="approve-resolve", agent=agent)
    assert approval["asking_context"]
    assert store.resolve_request(home, approval["request_id"], status="dismissed")
    with closing(store._db(home)) as conn:
        assert conn.execute("SELECT count(*) FROM request_answer_deliveries").fetchone()[0] == 0


@pytest.mark.parametrize("result", [{"interrupted": True}, {"status": "failed"},
                                    {"error": "provider_unavailable"}])
def test_delivery_is_enqueued_once_and_delivered_at_least_once(world, result):
    """converse has no idempotency key: an unacknowledged turn is sent again."""
    home, agent = world
    row = ask(home, agent)
    payload = {"request_id": row["request_id"], "values": {"reply": "Once"}}
    assert answer_request(universe_id=home.name, payload=payload)["status"] == "answered"
    assert answer_request(universe_id=home.name, payload=payload)["error"] == "already_resolved"
    with closing(store._db(home)) as conn:
        assert conn.execute("SELECT count(*) FROM request_answer_deliveries").fetchone()[0] == 1
    sent = []
    assert request_continuations.recover(
        home, run=lambda _h, p: sent.append(p) or result) == 0
    with closing(store._db(home)) as conn, conn:
        conn.execute("UPDATE request_answer_deliveries SET next_attempt_at=0")
    sent += drain(home)
    assert [p["request_id"] for p in sent] == [row["request_id"]] * 2
    assert request_continuations.recover(home, run=lambda *_: pytest.fail("resent")) == 0


def test_delivery_failure_backs_off_and_does_not_starve_other_answers(world, monkeypatch):
    home, agent = world
    first = ask(home, agent, title="First")
    second = ask(home, agent, title="Second")
    for row in (first, second):
        answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "values": {"reply": "Keep going"}})
    monkeypatch.setattr(request_answers.time, "time", lambda: 1000)
    delivered = []

    def run(_home, payload):
        if payload["request_id"] == first["request_id"]:
            raise RuntimeError("provider unavailable")
        delivered.append(payload)
        return {"reply": "done"}

    assert request_continuations.recover(home, run=run) == 1
    assert [r["request_id"] for r in delivered] == [second["request_id"]]
    with closing(store._db(home)) as conn, conn:
        assert conn.execute("SELECT attempt_count,next_attempt_at FROM request_answer_deliveries "
                            "WHERE request_id=?", (first["request_id"],)).fetchone() == (1, 1060)
    monkeypatch.setattr(request_answers.time, "time", lambda: 1060)
    assert request_continuations.recover(home, run=run) == 0
    with closing(store._db(home)) as conn:
        assert conn.execute("SELECT attempt_count,next_attempt_at FROM request_answer_deliveries "
                            "WHERE request_id=?", (first["request_id"],)).fetchone() == (2, 1180)


@pytest.mark.real_browser
@pytest.mark.parametrize("surface", [
    "app_sheet", "inline_card", "phone_push", "desktop", "phone_push_after_resolution",
])
def test_subagent_card_answer_in_real_browser(world, surface):
    from playwright.sync_api import sync_playwright

    from tests.test_app_browser_notifications import functions

    home, agent = world
    row = ask(home, agent)
    if surface == "phone_push_after_resolution":
        assert not answer_request(universe_id=home.name, payload={
            "request_id": row["request_id"], "values": {"reply": "First answer"}}).get("error")
        original, = drain(home)
        assert original["agent"] == agent
    responses = []

    def answer(payload):
        with identity_context(Identity(user_id=OWNER, username=OWNER,
                                       capabilities=["tinyassets.universe.write"])):
            result = answer_request(universe_id=home.name, payload=payload)
        responses.append(result)
        return result

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.expose_function("answerOwner", answer)
        page.route("https://answers.test/", lambda route: route.fulfill(
            content_type="text/html",
            body='<main id="card"></main><button id="btn-send">Send</button>'))
        page.goto("https://answers.test/")
        page.add_script_tag(content="""
            const $=id=>document.getElementById(id);
            let railOpen=null;
            const MCP={answerRequest:payload=>window.answerOwner(payload)};
            const ConnectOAuth={decorate(){}};
            const refreshRail=async()=>{};
            const sendTurn=()=>{throw new Error('A request answer must not use selected chat');};
            const appendMessage=(_,text)=>{document.querySelector('[id^=note_]').textContent=text;};
            const NATIVE=true,NATIVE_PUSH_RECIPIENT='recipient';
            let pendingReply=null,railCache=[],phoneReply=null;
            const nativePlugin=()=>({consume:async()=>phoneReply});
            localStorage.setItem(NATIVE_PUSH_RECIPIENT,'owner-device');
        """ + functions("railBody", "answerRail", "frameTitle", "answerLine",
                         "replyLine", "refusedGrantLine", "railFieldLink", "railFieldControl",
                         "collectNotificationReply", "applyPendingReply"))
        page.evaluate("row=>document.getElementById('card').appendChild(railBody(row))", row)
        if surface == "phone_push":
            page.evaluate("""async row=>{
                railCache=[row];phoneReply={request_id:row.request_id,
                    text:'Use the Social Media Manager account',recipient:'owner-device'};
                await collectNotificationReply();applyPendingReply();
            }""", row)
        elif surface == "phone_push_after_resolution":
            # The card disappeared on another device before the OS delivered
            # the reply. Its request id still determines the conversation.
            page.evaluate("""async row=>{
                railCache=[];pendingReply={request_id:row.request_id,item_id:'',
                    text:'Use the Social Media Manager account',seen:1};
                await applyPendingReply();
            }""", row)
        else:
            page.locator('[id^="f_"]').fill("Use the Social Media Manager account")
            page.get_by_role("button", name="Accept", exact=True).click()
        page.wait_for_function(
            "document.querySelector('[id^=note_]').textContent.toLowerCase().includes('sent')")
        assert responses and not responses[0].get("error"), responses
        assert "sent" in page.locator('[id^="note_"]').inner_text().lower()
        browser.close()
    received, = drain(home)
    assert received["agent"] == agent
    assert "Social Media Manager account" in json.dumps(received["outcome"])
