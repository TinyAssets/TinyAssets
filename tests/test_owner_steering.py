"""Harness S2: a message sent while the universe works reaches it mid-turn.

Founder, 2026-10-01: the agent should work like Claude Code, where a message
typed while it works lands at the next tool boundary. Before this the app held
the line in the browser ("Queued -- your universe sees this when its current
turn ends").

The path under test, end to end in pieces:
the app posts to ``/app/turn/steer`` -> ``agent_steering`` queues it for the
owner's thread -> the engine appends it to the next tool result of THAT session
only -> at turn end ``converse`` records what was delivered between the message
and the reply, and hands back what was not, which the app queues for the next
turn.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import agent_steering, engine_steering


def _universe(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "u-alpha"
    path.mkdir(parents=True)
    return path


THREAD = "thread:principal:owner-1"
NODE = "node:branch-1:agent"


# -- the queue ---------------------------------------------------------------


def test_messages_are_delivered_once_in_the_order_sent(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.enqueue(universe, THREAD, "also check the invoice")
    agent_steering.enqueue(universe, THREAD, "and skip the draft")
    first = agent_steering.take(universe, THREAD)
    assert [m.text for m in first] == ["also check the invoice", "and skip the draft"]
    assert agent_steering.take(universe, THREAD) == [], "delivered exactly once"


def test_a_session_only_ever_takes_its_own_messages(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.enqueue(universe, THREAD, "for the chat")
    assert agent_steering.take(universe, NODE) == []
    assert [m.text for m in agent_steering.take(universe, THREAD)] == ["for the chat"]


def test_settle_splits_delivered_from_undelivered_and_empties_the_queue(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.enqueue(universe, THREAD, "seen")
    agent_steering.take(universe, THREAD)
    agent_steering.enqueue(universe, THREAD, "not seen")
    delivered, undelivered = agent_steering.settle(universe, THREAD)
    assert [m.text for m in delivered] == ["seen"]
    assert [m.text for m in undelivered] == ["not seen"]
    assert agent_steering.settle(universe, THREAD) == ([], [])


def test_the_store_lives_outside_the_universe_folder(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.enqueue(universe, THREAD, "x")
    assert not any(universe.rglob("steering.db"))
    assert (tmp_path / "data" / ".agent-sessions" / "u-alpha" / "steering.db").exists()


@pytest.mark.parametrize("text", ["", "   ", "x" * (agent_steering.MAX_STEER_CHARS + 1)])
def test_an_empty_or_oversized_message_is_refused_not_cut(tmp_path, text):
    with pytest.raises(agent_steering.SteeringRefused):
        agent_steering.enqueue(_universe(tmp_path), THREAD, text)


def test_a_full_queue_refuses_instead_of_dropping(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(agent_steering, "MAX_PENDING", 2)
    agent_steering.enqueue(universe, THREAD, "1")
    agent_steering.enqueue(universe, THREAD, "2")
    with pytest.raises(agent_steering.SteeringRefused):
        agent_steering.enqueue(universe, THREAD, "3")


# -- the route names the session ---------------------------------------------


def test_the_engine_route_names_the_launchs_session():
    url = engine_steering.route_with_session("http://127.0.0.1:8790/mcp", THREAD)
    assert url == "http://127.0.0.1:8790/mcp?session=thread%3Aprincipal%3Aowner-1"
    assert engine_steering.route_with_session("http://h/mcp", "") == "http://h/mcp"
    config = SimpleNamespace(agent_session=SimpleNamespace(key=THREAD))
    assert engine_steering.session_of(config) == THREAD
    assert engine_steering.session_of(SimpleNamespace()) == ""


def test_a_codex_launch_routes_its_engine_calls_with_its_session(monkeypatch):
    from tinyassets.providers import codex_provider

    fake = SimpleNamespace(url="http://127.0.0.1:8790/mcp", secret="s3cret")
    monkeypatch.setattr("tinyassets.engine_mcp_http.read_engine_mcp_route",
                        lambda **_kw: fake)
    monkeypatch.setattr(codex_provider, "granted_tools", lambda _c: ["read"])
    config = SimpleNamespace(engine_mcp_enabled=True, engine_mcp_actor_id="owner-1",
                             engine_mcp_graph_id="u-alpha",
                             agent_session=SimpleNamespace(key=THREAD))
    args = codex_provider._codex_engine_mcp_args(config, {})
    server = next(a for a in args if a.startswith("mcp_servers.tinyassets="))
    assert 'url="http://127.0.0.1:8790/mcp?session=thread%3Aprincipal%3Aowner-1"' in server


# -- the engine delivers on the next tool result of that session only ---------


def _result(text: str):
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent

    return ToolResult(content=[TextContent(type="text", text=text)])


@pytest.mark.parametrize("session, steered", [(THREAD, True), (NODE, False), ("", False)])
def test_the_next_tool_result_carries_the_message_for_the_thread_only(
    monkeypatch, session, steered,
):
    taken: list[str] = []

    def take(key):
        taken.append(key)
        return "[1 new message from your founder]\n[12:00 UTC] stop and summarize"

    monkeypatch.setattr(engine_steering, "_session_key", lambda: session)
    monkeypatch.setattr(engine_steering, "_take", take)

    async def call_next(_context):
        return _result("$ pytest\n3 passed")

    out = asyncio.run(engine_steering.OwnerSteering().on_call_tool(
        SimpleNamespace(message=SimpleNamespace(name="bash", arguments={})), call_next))
    texts = [block.text for block in out.content]
    assert texts[0] == "$ pytest\n3 passed", "the tool's own output is untouched"
    if steered:
        assert taken == [THREAD]
        assert texts[1].endswith("stop and summarize")
    else:
        assert taken == [] and len(texts) == 1


def test_the_rendered_block_is_mechanical_and_ordered():
    block = agent_steering.render([
        agent_steering.Steer(1, "first", 0.0), agent_steering.Steer(2, "second", 60.0)])
    lines = block.splitlines()
    assert lines[0].startswith("[2 new messages from your founder")
    assert lines[1] == "[00:00 UTC] first" and lines[2] == "[00:01 UTC] second"


# -- turn end ----------------------------------------------------------------


def test_delivered_messages_are_recorded_between_the_message_and_the_reply(tmp_path):
    from tinyassets import conversation_store

    universe = _universe(tmp_path)
    assert conversation_store.record_exchange(
        universe, "principal:owner-1", "start the job", "Done.",
        interjections=[("also check the invoice", 1.0), ("and skip the draft", 2.0)],
    )
    turns = conversation_store.load_recent(universe, "principal:owner-1")
    spoken = [(getattr(t, "speaker", None), getattr(t, "text", getattr(t, "content", None)))
              for t in turns]
    assert [s for s, _ in spoken] == ["founder", "founder", "founder", "universe"]
    assert [t for _, t in spoken] == [
        "start the job", "also check the invoice", "and skip the draft", "Done."]


def test_a_turn_with_no_reply_hands_back_every_mid_turn_message(tmp_path, monkeypatch):
    from tinyassets import universe_server

    universe = _universe(tmp_path)
    agent_steering.enqueue(universe, THREAD, "seen")
    agent_steering.take(universe, THREAD)
    agent_steering.enqueue(universe, THREAD, "not seen")
    payload = universe_server._with_unsettled_steering(
        {"error": "Interrupted"}, universe, "principal:owner-1")
    assert payload["steering_undelivered"] == ["seen", "not seen"]
    assert agent_steering.settle(universe, THREAD) == ([], [])


# -- the endpoint ------------------------------------------------------------


def test_the_route_steers_only_the_callers_own_running_turn(monkeypatch, tmp_path):
    """Keyed exactly like Stop: only the caller's own running turn, into the
    caller's own thread. No live turn means ``steered: false`` and the page
    sends the line as an ordinary message."""
    from tests.test_turn_interrupt import _Request
    from tinyassets import onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware
    from tinyassets.turn_interrupt import interactive_turn

    universe = _universe(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path / "data")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    caller = SimpleNamespace(user_id="owner-1")
    monkeypatch.setattr(middleware, "current_identity", lambda: caller)

    def post(body):
        response = asyncio.run(onboarding._handle_turn_steer(_Request(body)))
        return response.status_code, json.loads(response.body)

    assert post({"universe_id": "u-alpha", "text": "hi"})[1]["steered"] is False
    with interactive_turn("owner-1", "u-alpha"):
        status, body = post({"universe_id": "u-alpha", "text": "also check the invoice"})
        assert status == 200 and body["steered"] is True
        assert post({"universe_id": "u-alpha", "text": "  "})[0] == 400
    caller.user_id = "intruder"
    with interactive_turn("owner-1", "u-alpha"):
        assert post({"universe_id": "u-alpha", "text": "x"})[1]["steered"] is False
    assert [m.text for m in agent_steering.take(universe, THREAD)] == [
        "also check the invoice"]
    assert agent_steering.take(universe, "thread:principal:intruder") == []


def test_converse_records_what_the_agent_heard_and_returns_what_it_did_not(
    monkeypatch, tmp_path,
):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth
    from tinyassets.conversation_store import load_recent

    _founder_auth(monkeypatch, base=tmp_path)
    universe = tmp_path / "u-x"
    universe.mkdir(exist_ok=True)
    key = "thread:principal:founder-1"

    def turn_with_steering(uid, msg, **kwargs):
        assert kwargs["session_key"] == key
        agent_steering.enqueue(universe, key, "also check the invoice")
        agent_steering.take(universe, key)          # reached the agent mid-turn
        agent_steering.enqueue(universe, key, "and skip the draft")   # too late
        return "Done, invoice checked."

    monkeypatch.setattr(ui, "converse", turn_with_steering)
    out = json.loads(us.converse(message="start the job", graph_id="u-x"))
    assert out["reply"] == "Done, invoice checked."
    assert out["steering_undelivered"] == ["and skip the draft"]
    rows = load_recent(universe, "principal:founder-1")
    assert [(r.speaker, r.text) for r in rows][-3:] == [
        ("founder", "start the job"), ("founder", "also check the invoice"),
        ("universe", "Done, invoice checked.")]
    assert agent_steering.settle(universe, key) == ([], [])


# -- the page ------------------------------------------------------------------

_STEER_PAGE = r"""
setQueueOwner("p-1"); setQueueScope("u-1");
globalThis.authHeaders=()=>({});
const steerPosts=[];
globalThis.fetch=async(url,init)=>{
  steerPosts.push({url, body:JSON.parse(init.body)});
  return {ok:true,status:200,json:async()=>({steered:true,universe_id:"u-1"})};
};
const first=sendTurn("start the long job");
await settle();
sendTurn("also check the invoice");
await settle(); await settle();
const during=bubbles().map(b=>({text:b.text, queued:b.queued}));
const queuedDuring=sendQueue.length;
gates[0].resolve({reply:"Done.", steering_undelivered:SCENARIO.undelivered});
await first; await settle(); await settle();
console.log(JSON.stringify({steerPosts, during, queuedDuring, sent:converseCalls,
  after:bubbles().map(b=>({text:b.text, queued:b.queued}))}));
"""


def _page_run(tmp_path, undelivered):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    if _NODE is None:
        pytest.skip("node is required to execute the page's own source")
    page, _csp = onboarding.render_app_html()
    return _run(tmp_path, page, {"undelivered": undelivered}, _STEER_PAGE)


def test_a_line_typed_mid_turn_goes_into_the_running_turn(tmp_path):
    out = _page_run(tmp_path, [])
    assert out["steerPosts"] == [{"url": "/app/turn/steer",
                                  "body": {"universe_id": "u-1", "text": "also check the invoice"}}]
    assert out["queuedDuring"] == 0, "a steered line must not also wait in the queue"
    line = next(b for b in out["during"] if b["text"] == "also check the invoice")
    assert line["queued"] is False
    # The agent heard it, so no second turn carries it.
    assert out["sent"] == ["start the long job"]


def test_a_steered_line_the_agent_never_heard_goes_out_next(tmp_path):
    out = _page_run(tmp_path, ["also check the invoice"])
    assert out["sent"] == ["start the long job", "also check the invoice"]
