"""Harness S2: a message sent while the universe works reaches it mid-turn.

Founder, 2026-10-01: the agent should work like Claude Code, where a message
typed while it works lands at the next tool boundary. Before this the app held
the line in the browser ("Queued -- your universe sees this when its current
turn ends").

The path under test, in pieces: the served turn OPENS itself in
``agent_steering`` under its live id -> the app posts to ``/app/turn/steer``,
admitted only while that turn is open and bound to it -> the engine appends
bound lines to the next tool result of THAT session and turn only -> at turn
end ``converse`` records what was delivered between the message and the reply,
keeps what was not as carryover, and hands it to the page by id.
gpt-6-astra's refute of the first draft (#4188) is why admission, binding,
carryover and the page's id reconciliation exist; its race schedules are
replayed here.
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


# -- the queue: admission, binding, delivery, settle, carryover ---------------


def test_a_line_is_admitted_only_while_a_turn_is_open(tmp_path):
    universe = _universe(tmp_path)
    assert agent_steering.enqueue(universe, THREAD, "nobody is working") is None
    agent_steering.open_turn(universe, THREAD, "live-1")
    assert agent_steering.enqueue(universe, THREAD, "now it is") is not None


def test_a_line_sent_after_the_turn_settles_is_refused_not_stranded(tmp_path):
    """gpt-6-astra: the endpoint checked liveness, the turn settled, and the
    line was queued for nobody. Admission and settle share one transaction."""
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    agent_steering.settle(universe, THREAD, "live-1")
    assert agent_steering.enqueue(universe, THREAD, "too late") is None


def test_lines_are_delivered_once_in_order_to_their_own_turn_only(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    agent_steering.enqueue(universe, THREAD, "also check the invoice")
    agent_steering.enqueue(universe, THREAD, "and skip the draft")
    assert agent_steering.take(universe, THREAD, "live-OTHER") == []
    assert agent_steering.take(universe, NODE, "live-1") == []
    first = agent_steering.take(universe, THREAD, "live-1")
    assert [m.text for m in first] == ["also check the invoice", "and skip the draft"]
    assert agent_steering.take(universe, THREAD, "live-1") == [], "exactly once"


def test_one_result_carries_a_budgeted_batch_and_the_rest_waits(tmp_path):
    """gpt-6-astra: the block was added after the result ceiling, unbounded."""
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    for index in range(3):
        agent_steering.enqueue(universe, THREAD, f"{index}" * 2500)
    first = agent_steering.take(universe, THREAD, "live-1", budget=6000)
    assert len(first) == 2
    assert len(agent_steering.take(universe, THREAD, "live-1", budget=6000)) == 1
    agent_steering.enqueue(universe, THREAD, "x" * 9000)
    assert len(agent_steering.take(universe, THREAD, "live-1", budget=6000)) == 1, (
        "a single line over the budget still goes alone, never cut")


def test_settle_returns_delivered_and_keeps_undelivered_as_carryover(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    agent_steering.enqueue(universe, THREAD, "seen")
    agent_steering.take(universe, THREAD, "live-1")
    agent_steering.enqueue(universe, THREAD, "not seen")
    delivered, undelivered = agent_steering.settle(universe, THREAD, "live-1")
    assert [m.text for m in delivered] == ["seen"]
    assert [m.text for m in undelivered] == ["not seen"]
    # The page normally re-sends it; one that never got the answer did not.
    assert [m.text for m in agent_steering.take_carryover(universe, THREAD, "hello")] == [
        "not seen"]
    assert agent_steering.take_carryover(universe, THREAD, "hello") == []


def test_carryover_the_page_already_resent_is_not_repeated(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    agent_steering.enqueue(universe, THREAD, "not seen")
    agent_steering.settle(universe, THREAD, "live-1")
    assert agent_steering.take_carryover(universe, THREAD, "first\n\nnot seen") == []


def test_a_turn_a_dead_process_left_open_is_closed_and_its_lines_carried(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "dead-1")
    agent_steering.enqueue(universe, THREAD, "sent before the deploy")
    agent_steering.open_turn(universe, THREAD, "live-2", live_ids=())
    assert agent_steering.take(universe, THREAD, "dead-1") == []
    assert [m.text for m in agent_steering.take_carryover(universe, THREAD, "")] == [
        "sent before the deploy"]


def test_the_store_lives_outside_the_universe_folder(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    assert not any(universe.rglob("steering.db"))
    assert (tmp_path / "data" / ".agent-sessions" / "u-alpha" / "steering.db").exists()


@pytest.mark.parametrize("text", ["", "   ", "x" * (agent_steering.MAX_STEER_CHARS + 1)])
def test_an_empty_or_oversized_line_is_refused_not_cut(tmp_path, text):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    with pytest.raises(agent_steering.SteeringRefused):
        agent_steering.enqueue(universe, THREAD, text)


def test_a_full_queue_refuses_instead_of_dropping(tmp_path, monkeypatch):
    universe = _universe(tmp_path)
    monkeypatch.setattr(agent_steering, "MAX_PENDING", 2)
    agent_steering.open_turn(universe, THREAD, "live-1")
    agent_steering.enqueue(universe, THREAD, "1")
    agent_steering.enqueue(universe, THREAD, "2")
    with pytest.raises(agent_steering.SteeringRefused):
        agent_steering.enqueue(universe, THREAD, "3")


# -- launches name their session and turn -------------------------------------


def test_the_engine_route_names_the_launchs_session_and_turn():
    url = engine_steering.route_with_session("http://127.0.0.1:8790/mcp", THREAD, "abc")
    assert url == ("http://127.0.0.1:8790/mcp?session=thread%3Aprincipal%3Aowner-1"
                   "&turn=abc")
    assert engine_steering.route_with_session("http://h/mcp", "") == "http://h/mcp"
    config = SimpleNamespace(agent_session=SimpleNamespace(key=THREAD))
    assert engine_steering.session_of(config) == THREAD
    assert engine_steering.session_of(SimpleNamespace()) == ""


def test_a_codex_launch_routes_its_engine_calls_with_its_session_and_turn(monkeypatch):
    from tinyassets import turn_interrupt
    from tinyassets.providers import codex_provider

    fake = SimpleNamespace(url="http://127.0.0.1:8790/mcp", secret="s3cret")
    monkeypatch.setattr("tinyassets.engine_mcp_http.read_engine_mcp_route",
                        lambda **_kw: fake)
    monkeypatch.setattr(codex_provider, "granted_tools", lambda _c: ["read"])
    config = SimpleNamespace(engine_mcp_enabled=True, engine_mcp_actor_id="owner-1",
                             engine_mcp_graph_id="u-alpha",
                             agent_session=SimpleNamespace(key=THREAD))
    with turn_interrupt.interactive_turn("owner-1", "u-alpha") as live:
        args = codex_provider._codex_engine_mcp_args(config, {})
    server = next(a for a in args if a.startswith("mcp_servers.tinyassets="))
    assert (f'url="http://127.0.0.1:8790/mcp?session=thread%3Aprincipal%3Aowner-1'
            f'&turn={live.live_id}"') in server


def test_claude_launches_of_different_sessions_never_share_a_config(tmp_path, monkeypatch):
    """gpt-6-astra: one shared config file let a background launch read the
    owner's route and take the owner's lines."""
    from tinyassets.providers import claude_provider

    universe = _universe(tmp_path)
    monkeypatch.setattr("tinyassets.engine_mcp_http.read_engine_mcp_route",
                        lambda **_kw: SimpleNamespace(url="http://127.0.0.1:1/mcp",
                                                      secret="s"))
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path / "data")

    def launch(key):
        config = SimpleNamespace(engine_mcp_actor_id="owner-1", engine_mcp_graph_id="u-alpha",
                                 agent_session=SimpleNamespace(key=key), selected_model=None)
        flags = claude_provider._engine_mcp_flags(config, universe)
        return flags[flags.index("--mcp-config") + 1]

    owner, node = launch(THREAD), launch(NODE)
    assert owner != node
    assert "session=node" in Path(node).read_text(encoding="utf-8")
    assert "session=thread" in Path(owner).read_text(encoding="utf-8")


# -- the engine delivers on the next tool result of that session and turn ------


def _result(text: str):
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent

    return ToolResult(content=[TextContent(type="text", text=text)])


@pytest.mark.parametrize("session, turn, steered", [
    (THREAD, "live-1", True), (NODE, "live-1", False), (THREAD, "", False), ("", "", False)])
def test_the_next_tool_result_carries_the_line_for_the_live_thread_only(
    monkeypatch, session, turn, steered,
):
    taken: list[tuple[str, str]] = []

    def take(key, live):
        taken.append((key, live))
        return "[1 new message from your founder]\n[12:00 UTC] stop and summarize"

    monkeypatch.setattr(engine_steering, "_route_params", lambda: (session, turn))
    monkeypatch.setattr(engine_steering, "_take", take)

    async def call_next(_context):
        return _result("$ pytest\n3 passed")

    out = asyncio.run(engine_steering.OwnerSteering().on_call_tool(
        SimpleNamespace(message=SimpleNamespace(name="bash", arguments={})), call_next))
    texts = [block.text for block in out.content]
    assert texts[0] == "$ pytest\n3 passed", "the tool's own output is untouched"
    if steered:
        assert taken == [(THREAD, "live-1")]
        assert texts[1].endswith("stop and summarize")
    else:
        assert taken == [] and len(texts) == 1


def test_a_refused_tool_call_still_carries_the_line(monkeypatch):
    """gpt-6-astra: refusals raise ToolError, so "stop doing that" never landed
    while the agent kept hitting the same refusal."""
    from fastmcp.exceptions import ToolError

    monkeypatch.setattr(engine_steering, "_route_params", lambda: (THREAD, "live-1"))
    monkeypatch.setattr(engine_steering, "_take", lambda *_a: "[1 new message]\n[x] stop")

    async def call_next(_context):
        raise ToolError("refused: not allowed")

    with pytest.raises(ToolError) as raised:
        asyncio.run(engine_steering.OwnerSteering().on_call_tool(
            SimpleNamespace(message=SimpleNamespace(name="write_graph")), call_next))
    assert str(raised.value).startswith("refused: not allowed")
    assert str(raised.value).endswith("[x] stop")


def test_the_rendered_block_is_mechanical_and_ordered():
    block = agent_steering.render([
        agent_steering.Steer(1, "first", 0.0), agent_steering.Steer(2, "second", 60.0)])
    lines = block.splitlines()
    assert lines[0].startswith("[2 new messages from your founder")
    assert lines[1] == "[00:00 UTC] first" and lines[2] == "[00:01 UTC] second"


# -- turn end ------------------------------------------------------------------


def test_delivered_lines_are_recorded_between_the_message_and_the_reply(tmp_path):
    from tinyassets import conversation_store

    universe = _universe(tmp_path)
    assert conversation_store.record_exchange(
        universe, "principal:owner-1", "start the job", "Done.",
        interjections=[("also check the invoice", 1.0), ("and skip the draft", 2.0)],
    )
    turns = conversation_store.load_recent(universe, "principal:owner-1")
    assert [(t.speaker, t.text) for t in turns] == [
        ("founder", "start the job"), ("founder", "also check the invoice"),
        ("founder", "and skip the draft"), ("universe", "Done.")]


def _founder(monkeypatch, tmp_path):
    from tests.test_converse_handle import _founder_auth

    _founder_auth(monkeypatch, base=tmp_path)
    universe = tmp_path / "u-x"
    universe.mkdir(exist_ok=True)
    return universe, "thread:principal:founder-1"


def test_converse_records_what_the_agent_heard_and_returns_the_rest_by_id(
    monkeypatch, tmp_path,
):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tinyassets.conversation_store import load_recent
    from tinyassets.turn_interrupt import current

    universe, key = _founder(monkeypatch, tmp_path)
    ids: dict[str, int] = {}

    def turn_with_steering(uid, msg, **kwargs):
        live = current().live_id
        ids["heard"] = agent_steering.enqueue(universe, key, "also check the invoice").id
        agent_steering.take(universe, key, live)          # reached the agent mid-turn
        ids["late"] = agent_steering.enqueue(universe, key, "and skip the draft").id
        return "Done, invoice checked."

    monkeypatch.setattr(ui, "converse", turn_with_steering)
    out = json.loads(us.converse(message="start the job", graph_id="u-x"))
    assert out["reply"] == "Done, invoice checked."
    assert out["steering"] == {"delivered": [ids["heard"]],
                               "undelivered": [{"id": ids["late"], "text": "and skip the draft"}]}
    rows = load_recent(universe, "principal:founder-1")
    assert [(r.speaker, r.text) for r in rows][-3:] == [
        ("founder", "start the job"), ("founder", "also check the invoice"),
        ("universe", "Done, invoice checked.")]

    # The page never re-sent the late line: the next turn folds it in, once.
    seen: list[str] = []
    monkeypatch.setattr(ui, "converse", lambda uid, msg, **kw: seen.append(msg) or "ok")
    us.converse(message="next thing", graph_id="u-x")
    assert seen == ["and skip the draft\n\nnext thing"]
    us.converse(message="after that", graph_id="u-x")
    assert seen[-1] == "after that"


def test_a_turn_with_no_reply_hands_back_every_line(monkeypatch, tmp_path):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tinyassets.turn_interrupt import TurnInterrupted, current

    universe, key = _founder(monkeypatch, tmp_path)

    def stopped(uid, msg, **kwargs):
        live = current().live_id
        agent_steering.enqueue(universe, key, "seen")
        agent_steering.take(universe, key, live)
        agent_steering.enqueue(universe, key, "not seen")
        raise TurnInterrupted("stopped")

    monkeypatch.setattr(ui, "converse", stopped)
    out = json.loads(us.converse(message="do the thing", graph_id="u-x"))
    assert [u["text"] for u in out["steering"]["undelivered"]] == ["seen", "not seen"]
    assert out["steering"]["delivered"] == []


# -- the endpoint --------------------------------------------------------------


def test_the_route_steers_only_the_callers_own_open_turn(monkeypatch, tmp_path):
    """Keyed exactly like Stop: only the caller's own running turn, into the
    caller's own thread. No open turn means ``steered: false`` and the page
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
    with interactive_turn("owner-1", "u-alpha") as live:
        # Live in this process but not opened for steering: refused.
        assert post({"universe_id": "u-alpha", "text": "early"})[1]["steered"] is False
        agent_steering.open_turn(universe, THREAD, live.live_id)
        status, body = post({"universe_id": "u-alpha", "text": "also check the invoice"})
        assert status == 200 and body["steered"] is True and body["steer_id"] > 0
        assert post({"universe_id": "u-alpha", "text": "  "})[0] == 400
        caller.user_id = "intruder"
        assert post({"universe_id": "u-alpha", "text": "x"})[1]["steered"] is False
        assert [m.text for m in agent_steering.take(universe, THREAD, live.live_id)] == [
            "also check the invoice"]


def test_account_deletion_names_the_session_and_steering_records(tmp_path):
    from tinyassets import account_deletion
    from tinyassets.agent_sessions import RECORDS_DIR

    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1")
    assert (tmp_path / "data" / RECORDS_DIR / "u-alpha" / "steering.db").exists()
    source = Path(account_deletion.__file__).read_text(encoding="utf-8")
    assert '"agent_session_records"' in source
    # The removal itself is proven in test_account_deletion (#4216's block).
    assert "parent = root / RECORDS_DIR" in source
    assert "parent / _home_dir(root, home).name" in source


# -- the page ------------------------------------------------------------------

_STEER_PAGE = r"""
setQueueOwner("p-1"); setQueueScope("u-1");
globalThis.authHeaders=()=>({});
const steerPosts=[]; let steerAnswer=null;
globalThis.fetch=(url,init)=>{
  steerPosts.push({url, body:JSON.parse(init.body)});
  const reply={ok:true,status:200,json:async()=>({steered:true,universe_id:"u-1",steer_id:7})};
  if(SCENARIO.lateAck) return new Promise(resolve=>{ steerAnswer=()=>resolve(reply); });
  return Promise.resolve(reply);
};
const first=sendTurn("start the long job");
await settle();
sendTurn("also check the invoice");
await settle(); await settle();
const queuedDuring=sendQueue.length;
if(SCENARIO.lost) gates[0].reject(Object.assign(new Error("net"),{transport:true}));
else gates[0].resolve({reply:"Done.", steering:SCENARIO.receipt});
await first.catch(()=>{}); await settle();
if(steerAnswer){ steerAnswer(); await settle(); await settle(); }
await settle();
console.log(JSON.stringify({steerPosts, queuedDuring, sent:converseCalls,
  stillQueued:sendQueue.length}));
"""


def _page_run(tmp_path, scenario):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    if _NODE is None:
        pytest.skip("node is required to execute the page's own source")
    page, _csp = onboarding.render_app_html()
    return _run(tmp_path, page, scenario, _STEER_PAGE)


def test_a_line_typed_mid_turn_goes_into_the_running_turn(tmp_path):
    out = _page_run(tmp_path, {"receipt": {"delivered": [7], "undelivered": []}})
    assert out["steerPosts"] == [{"url": "/app/turn/steer",
                                  "body": {"universe_id": "u-1", "text": "also check the invoice"}}]
    assert out["queuedDuring"] == 0, "a steered line must not also wait in the queue"
    assert out["sent"] == ["start the long job"], "the agent heard it: no second turn"


def test_a_steered_line_the_agent_never_heard_goes_out_next(tmp_path):
    unheard = [{"id": 7, "text": "also check the invoice"}]
    out = _page_run(tmp_path, {"receipt": {"delivered": [], "undelivered": unheard}})
    assert out["sent"] == ["start the long job", "also check the invoice"]


def test_a_reply_before_the_steer_answer_does_not_resend_a_heard_line(tmp_path):
    """gpt-6-astra's duplicate schedule: the reply lands first and the queue used
    to flush the line as another turn although the agent had handled it."""
    out = _page_run(tmp_path, {"lateAck": True,
                               "receipt": {"delivered": [7], "undelivered": []}})
    assert out["sent"] == ["start the long job"]
    assert out["stillQueued"] == 0


def test_a_late_steer_answer_for_an_unheard_line_sends_it_once(tmp_path):
    """gpt-6-astra's Stop schedule: the settlement named the line undelivered
    before the steer answer arrived, and the answer then removed it."""
    out = _page_run(tmp_path, {"lateAck": True, "receipt": {
        "delivered": [], "undelivered": [{"id": 7, "text": "also check the invoice"}]}})
    assert out["sent"] == ["start the long job", "also check the invoice"]


def test_an_answer_that_never_arrives_requeues_every_steered_line(tmp_path):
    """No answer at all: the line goes back in the queue, held behind the
    unconfirmed turn exactly like any queued line (the page's existing rule)."""
    out = _page_run(tmp_path, {"lost": True})
    assert out["stillQueued"] == 1
    assert out["sent"] == ["start the long job"]
