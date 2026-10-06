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


@pytest.mark.real_browser
@pytest.mark.parametrize("surface", ["app_sheet", "inline_card", "phone_push", "desktop"])
def test_subagent_card_answer_in_real_browser(world, surface):
    from playwright.sync_api import sync_playwright

    from tests.test_app_browser_notifications import functions

    home, agent = world
    row = ask(home, agent)
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
        else:
            page.locator('[id^="f_"]').fill("Use the Social Media Manager account")
            page.get_by_role("button", name="Accept", exact=True).click()
        page.wait_for_function("document.querySelector('[id^=note_]').textContent.includes('Sent')")
        assert responses and not responses[0].get("error"), responses
        assert "Sent" in page.locator('[id^="note_"]').inner_text()
        browser.close()
    received, = drain(home)
    assert received["agent"] == agent
    assert "Social Media Manager account" in json.dumps(received["outcome"])

