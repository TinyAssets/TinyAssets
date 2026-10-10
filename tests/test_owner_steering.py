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


def _codex_dialled(monkeypatch, config):
    """What the codex served turn asks the engine route for (open_engine_tools)."""
    import asyncio
    import contextlib

    from tinyassets.providers import codex_provider

    opened = []

    @contextlib.asynccontextmanager
    async def record(**kwargs):
        opened.append(kwargs)
        yield None

    monkeypatch.setattr("tinyassets.engine_tool_client.open_engine_tools", record)

    async def dial():
        async with contextlib.AsyncExitStack() as stack:
            await codex_provider._served_engine_tools(stack, config, timeout=5)

    asyncio.run(dial())
    return opened[0] if opened else None


def test_a_codex_launch_routes_its_engine_calls_with_its_session_and_turn(monkeypatch):
    from tinyassets import turn_interrupt

    config = SimpleNamespace(engine_mcp_enabled=True, engine_mcp_actor_id="owner-1",
                             engine_mcp_graph_id="u-alpha", engine_tool_grant=None,
                             agent_session=SimpleNamespace(key=THREAD))
    with turn_interrupt.interactive_turn("owner-1", "u-alpha") as live:
        dialled = _codex_dialled(monkeypatch, config)
    assert dialled["session_key"] == THREAD
    assert dialled["turn"] == live.live_id


def test_claude_launches_of_different_sessions_never_share_a_config(tmp_path, monkeypatch):
    """gpt-6-astra: one shared config file let a background launch read the
    owner's route and take the owner's lines."""
    from tinyassets.providers import claude_provider

    universe = _universe(tmp_path)
    monkeypatch.setattr("tinyassets.engine_mcp_http.read_engine_mcp_route",
                        lambda **_kw: SimpleNamespace(url="http://127.0.0.1:1/mcp",
                                                      secret="s"))
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path / "data")

    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    monkeypatch.setattr("tinyassets.credential_vault._write_exclusive_snapshot_file",
                        lambda path, data: path.write_bytes(data))

    def launch(key):
        config = SimpleNamespace(engine_mcp_actor_id="owner-1", engine_mcp_graph_id="u-alpha",
                                 agent_session=SimpleNamespace(key=key), selected_model=None,
                                 credential_snapshot_dir=snapshot)
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

    assert _NODE is not None, "node is required to execute the page's own source"
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



# -- P1, live 2026-10-02: a line sent while ANOTHER window's turn ran was lost --
#
# The founder's desktop app showed "Your agent is thinking... started in another
# window"; a message sent then went out as a second, competing turn, never
# reached the saved thread, and a reload a few seconds later left nothing to
# recover. A mid-turn send must be saved on the SERVER before the page says it
# was sent, must survive a reload, and must show in the thread.


def test_a_line_with_no_open_turn_is_held_on_the_server_and_listed(monkeypatch, tmp_path):
    from tests.test_turn_interrupt import _Request
    from tinyassets import onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    universe = _universe(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path / "data")
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    caller = SimpleNamespace(user_id="owner-1")
    monkeypatch.setattr(middleware, "current_identity", lambda: caller)
    from tinyassets.api import permissions

    monkeypatch.setattr(permissions, "universe_access_allows",
                        lambda uid, write=False: caller.user_id == "owner-1" and uid == "u-alpha")

    response = asyncio.run(onboarding._handle_turn_steer(
        _Request({"universe_id": "u-alpha", "text": "also check the invoice"})))
    body = json.loads(response.body)
    assert body["steered"] is False and body["held"] is True and body["steer_id"] > 0
    assert [(m.text, m.state) for m in agent_steering.pending(universe, THREAD)] == [
        ("also check the invoice", "held")]
    # The next served turn folds it in, and a page that re-sends it is not repeated.
    assert agent_steering.take_carryover(universe, THREAD, "also check the invoice") == []

    listed = asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha"})))
    assert json.loads(listed.body)["pending"] == []  # taken by the turn above
    asyncio.run(onboarding._handle_turn_steer(
        _Request({"universe_id": "u-alpha", "text": "and the receipt"})))
    listed = json.loads(asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha"}))).body)["pending"]
    assert [(p["text"], p["state"]) for p in listed] == [("and the receipt", "held")]
    caller.user_id = "intruder"
    other = json.loads(asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha"}))).body)
    assert other.get("pending", []) == []
    refused = json.loads(asyncio.run(onboarding._handle_turn_steer(
        _Request({"universe_id": "u-alpha", "text": "plant this"}))).body)
    assert refused == {"steered": False, "universe_id": "u-alpha"}, "nothing saved for a stranger"


def test_a_line_held_for_another_agent_comes_back_for_that_agent(monkeypatch, tmp_path):
    """A line typed to one of the owner's OTHER agents is held under THAT
    agent's thread, and the reload that asks for that agent gets it back.

    The steer path routes per agent (harness §4.18) while the hold is what makes
    a line survive (S2). Reading the main thread regardless -- which is what the
    pending route did -- lost every line held for another agent on reload: held
    on the server, invisible to the page, never sent.
    """
    from tests.conftest import own_universe
    from tests.test_turn_interrupt import _Request
    from tinyassets import addressed_agents, onboarding
    from tinyassets.api import helpers, permissions
    from tinyassets.auth import middleware
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.daemon_server import ensure_universe_registered

    base = tmp_path / "data"
    universe = _universe(tmp_path)
    own_universe(base, "u-alpha")
    ensure_universe_registered(base, universe_id="u-alpha", universe_path=universe)
    monkeypatch.setattr(helpers, "_base_path", lambda: base)
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    caller = SimpleNamespace(user_id="owner-1")
    monkeypatch.setattr(middleware, "current_identity", lambda: caller)
    monkeypatch.setattr(permissions, "universe_access_allows",
                        lambda uid, write=False: caller.user_id == "owner-1" and uid == "u-alpha")

    definition = publish_definition(base, author_id="owner-1", payload={
        "schema_version": 1, "name": "Evidence Weaver",
        "components": {"identity": {"kind": "soul", "config": {"instructions": "Weave."}}},
    })
    weaver = create_binding(
        base, universe_id="u-alpha", definition_id=definition["agent_definition_id"],
        created_by="owner-1", payload={"schema_version": 1, "name": "Evidence Weaver"},
    )["agent_binding_id"]

    held = json.loads(asyncio.run(onboarding._handle_turn_steer(
        _Request({"universe_id": "u-alpha", "agent_id": weaver,
                  "text": "check the methods section"}))).body)
    assert held["steered"] is False and held["held"] is True

    # Held under the WEAVER's thread, which is where its turn will look.
    weaver_thread = f"thread:{addressed_agents.memory_session('owner-1', weaver)}"
    assert [m.text for m in agent_steering.pending(universe, weaver_thread)] == [
        "check the methods section"]
    assert agent_steering.pending(universe, THREAD) == [], "not on the main thread"

    # The reload: the page asks for the agent it is showing and gets it back.
    listed = json.loads(asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha", "agent_id": weaver}))).body)
    assert [(p["text"], p["state"]) for p in listed["pending"]] == [
        ("check the methods section", "held")]

    # The main thread's reload does not show another agent's line...
    on_main = json.loads(asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha"}))).body)
    assert on_main["pending"] == []
    # ...and claiming it from the main thread does not take it.
    claimed = json.loads(asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha", "claim": [listed["pending"][0]["id"]]}))).body)
    assert claimed["claimed"] == []
    assert [m.text for m in agent_steering.pending(universe, weaver_thread)] == [
        "check the methods section"], "still held for its own agent"

    # Claimed once, by the agent it was held for.
    claimed = json.loads(asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha", "agent_id": weaver,
                  "claim": [listed["pending"][0]["id"]]}))).body)
    assert claimed["claimed"] == [listed["pending"][0]["id"]]

    # An id that is not one of the owner's agents is refused by name, never
    # answered with the main thread's lines.
    unknown = asyncio.run(onboarding._handle_turn_pending(
        _Request({"universe_id": "u-alpha", "agent_id": "not-an-agent"})))
    assert unknown.status_code == 404
    assert json.loads(unknown.body)["error"] == "agent_not_found"


_OTHER_WINDOW = r"""
globalThis.authHeaders=()=>({});
const posts=[];
globalThis.fetch=(url,init)=>{
  const method=(init&&init.method)||"GET";
  posts.push({url, method, body:init&&init.body?JSON.parse(init.body):null});
  const body=init&&init.body?JSON.parse(init.body):{};
  const doc=url==="/app/turn/steer"?SCENARIO.post
    :(body.claim?(SCENARIO.claim||{claimed:body.claim}):(SCENARIO.pending||{pending:[]}));
  return Promise.resolve({ok:true,status:200,json:async()=>doc});
};
// The page learns its account and home with the server answering, as on load.
setQueueOwner("p-1"); setQueueScope("u-1");
// Which agent the page is showing, when the scenario names one. Injected as
// the global collaborator the page asks for through `typeof`, which is how the
// agent switcher reaches this code; with none, the page is on the main agent.
if(SCENARIO.agent) globalThis.addressedAgentId=()=>SCENARIO.agent.agent_id;
const working={active_turn:{turn_id:"t9",state:"inference_started",age_s:5,stale:false}};
readServerTurn(working);
if(SCENARIO.reload){ queueRestored=false; restoreQueue(); }
else { sendTurn("also check the invoice"); }
await settle(); await settle(); await settle();
const during={converse:converseCalls.slice(), queued:sendQueue.length,
  posts:posts.filter(p=>p.url==="/app/turn/steer").map(p=>p.body&&p.body.text),
  pending:posts.filter(p=>p.url==="/app/turn/pending").map(p=>p.body),
  thread:bubbles().map(b=>b.text)};
readServerTurn({active_turn:null});
await settle(); await settle(); await settle();
console.log(JSON.stringify({during, sent:converseCalls.slice(), queuedAfter:sendQueue.length}));
"""


def _other_window(tmp_path, scenario, script=None):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required to execute the page's own source"
    page, _csp = onboarding.render_app_html()
    return _run(tmp_path, page, scenario, script or _OTHER_WINDOW)


def test_a_send_during_another_windows_turn_is_held_not_a_competing_turn(tmp_path):
    out = _other_window(tmp_path, {"post": {"steered": False, "held": True, "steer_id": 3}})
    assert out["during"]["converse"] == [], "no second turn while one is running"
    assert out["during"]["posts"] == ["also check the invoice"], "saved on the server first"
    assert "also check the invoice" in out["during"]["thread"], "it shows in the thread"
    assert out["during"]["queued"] == 1
    assert out["sent"] == ["also check the invoice"], "it goes out when the turn ends"


def test_a_send_into_another_windows_open_turn_steers_it(tmp_path):
    out = _other_window(tmp_path, {"post": {"steered": True, "steer_id": 3}})
    assert out["during"]["converse"] == [] and out["during"]["queued"] == 0
    assert out["sent"] == [], "the running turn heard it: no second turn"


def test_a_held_line_survives_a_reload_and_still_goes_out(tmp_path):
    held = {"pending": [{"id": 3, "text": "also check the invoice", "state": "held"}]}
    out = _other_window(tmp_path, {"reload": True, "pending": held})
    assert "also check the invoice" in out["during"]["thread"], "back on screen after reload"
    assert out["during"]["queued"] == 1 and out["during"]["converse"] == []
    assert out["sent"] == ["also check the invoice"]
    # The read names the agent it is for, so the server can answer from that
    # agent's thread; the main agent is "main".
    assert [p["agent_id"] for p in out["during"]["pending"]] == ["main"]


def test_a_reload_showing_another_agent_asks_for_that_agents_held_line(tmp_path):
    """The reload of a page showing one of the owner's other agents asks for
    THAT agent's held lines, and puts them back on screen.

    Without the agent id on the read the server answers from the main thread,
    so a line held for this agent stays on the server and is never sent."""
    held = {"pending": [{"id": 4, "text": "check the methods section", "state": "held"}]}
    out = _other_window(tmp_path, {
        "reload": True, "pending": held,
        "agent": {"agent_id": "w1", "name": "Evidence Weaver"}})

    assert [p["agent_id"] for p in out["during"]["pending"]] == ["w1"]
    assert "check the methods section" in out["during"]["thread"], "back on screen"
    assert out["during"]["queued"] == 1 and out["during"]["converse"] == []
    assert out["sent"] == ["check the methods section"]


# -- gpt-6-astra on #4290: ids, not text, decide what is sent once -------------


def test_a_held_line_is_claimed_once_whatever_its_shape(tmp_path):
    universe = _universe(tmp_path)
    held = agent_steering.hold(universe, THREAD, "first part\n\nsecond part")
    assert agent_steering.claim(universe, THREAD, [held.id]) == [held.id]
    assert agent_steering.claim(universe, THREAD, [held.id]) == [], "a second tab gets nothing"
    assert agent_steering.take_carryover(universe, THREAD, "anything") == [], "and no repeat"
    other = agent_steering.hold(universe, "thread:principal:someone-else", "theirs")
    assert agent_steering.claim(universe, THREAD, [other.id]) == [], "only your own thread"


def test_the_running_turns_message_is_known_for_a_reload(tmp_path):
    universe = _universe(tmp_path)
    agent_steering.open_turn(universe, THREAD, "live-1", message="build the village map")
    active = agent_steering.active(universe, THREAD)
    assert active["text"] == "build the village map" and active["started_at"] > 0
    agent_steering.settle(universe, THREAD, "live-1")
    assert agent_steering.active(universe, THREAD) is None


def test_a_held_line_another_tab_already_sent_is_not_sent_again(tmp_path):
    held = {"pending": [{"id": 3, "text": "also check the invoice", "state": "held"}]}
    out = _other_window(tmp_path, {"reload": True, "pending": held, "claim": {"claimed": []}})
    assert out["sent"] == [], "the claim failed: someone else already sent it"


_IDLE_FLUSH = r"""
globalThis.authHeaders=()=>({});
globalThis.fetch=()=>Promise.resolve({ok:true,status:200,json:async()=>({pending:[],claimed:[]})});
setQueueOwner("p-1"); setQueueScope("u-1");
queueTurn("waiting line","waiting line",{inputMethod:"typed"});
// The busy window was never seen live by this page (a poll gap), then idle.
readServerTurn({active_turn:null});
await settle(); await settle();
console.log(JSON.stringify({sent:converseCalls.slice()}));
"""


def test_an_idle_answer_sends_what_waits_even_without_seeing_the_turn_end(tmp_path):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required"
    page, _csp = onboarding.render_app_html()
    out = _run(tmp_path, page, {}, _IDLE_FLUSH)
    assert out["sent"] == ["waiting line"]


_KEEP_LOCAL = r"""
globalThis.authHeaders=()=>({});
globalThis.fetch=()=>Promise.resolve({ok:true,status:200,json:async()=>(
  {pending:[{id:3,text:"from the server",state:"held"}]})});
localStorage.setItem(QUEUE_KEY, JSON.stringify([{message:"only on this device",
  display:"only on this device", owner:"p-1", scope:"u-1", ts:Date.now(), inputMethod:"typed"}]));
readServerTurn({active_turn:{turn_id:"t9",state:"inference_started",age_s:5,stale:false}});
setQueueOwner("p-1"); setQueueScope("u-1");
queueRestored=false; restoreQueue();
await settle(); await settle(); await settle();
console.log(JSON.stringify({saved:readSavedQueue().map(i=>i.message).sort()}));
"""


def test_restoring_the_servers_lines_keeps_the_devices_own(tmp_path):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required"
    page, _csp = onboarding.render_app_html()
    out = _run(tmp_path, page, {}, _KEEP_LOCAL)
    assert out["saved"] == ["from the server", "only on this device"]


_RELOAD_MID_TURN = r"""
globalThis.authHeaders=()=>({});
globalThis.fetch=()=>Promise.resolve({ok:true,status:200,json:async()=>(
  {pending:[], active:{text:"build the village map", started_at:Date.now()/1000-30,
    client_send_id:"village-send"}})});
const working={active_turn:{turn_id:"t9",state:"inference_started",age_s:30,stale:false}};
readServerTurn(working);
setQueueOwner("p-1"); setQueueScope("u-1");
queueRestored=false; restoreQueue();
await settle(); await settle(); await settle();
const during=bubbles().map(b=>b.text+"|"+(els.thread.children.find(n=>n.workingNote)?"working":""));
readServerTurn({active_turn:null});
await settle(); await settle(); await settle();
console.log(JSON.stringify({during, after:bubbles().map(b=>b.text), sent:converseCalls.slice()}));
"""


def test_a_reload_mid_turn_shows_the_message_being_worked_on_then_its_reply(tmp_path):
    """P2, live 2026-10-02: the reloaded page showed neither the message nor that
    it was being worked on, so the owner sent it again."""
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required"
    page, _csp = onboarding.render_app_html()
    out = _run(tmp_path, page, {"history": [
        {"speaker": "founder", "text": "build the village map", "ts": 1,
         "client_send_id": "village-send"},
        {"speaker": "universe", "text": "Here is the village map.", "ts": 2}]},
        _RELOAD_MID_TURN)
    assert out["during"] == ["build the village map|working"]
    assert out["after"] == ["build the village map", "Here is the village map."]
    assert out["sent"] == [], "nothing sent again"


@pytest.mark.parametrize("held_id", [None, "", "unconfirmed-send"])
def test_a_reload_mid_turn_without_id_draws_only_its_replies_and_forgets_nothing(tmp_path, held_id):
    from tests.test_app_working_indicator import _NODE, _run
    from tinyassets import onboarding

    assert _NODE is not None, "node is required"
    page, _csp = onboarding.render_app_html()
    body = _RELOAD_MID_TURN.replace('client_send_id:"village-send"', "")
    setup = """
let forgotten=0;
const originalForget=forgetInflight;
forgetInflight=()=>{ forgotten++; originalForget(); };
"""
    if held_id is not None:
        setup += (
            'rememberInflight("build the village map","build the village map",'
            f'Date.now(),"typed",null,null,"main",{json.dumps(held_id)});\n'
        )
    setup += "const heldBefore=JSON.stringify(readInflight());\n"
    body = body.replace(
        'readServerTurn({active_turn:null});', setup + 'readServerTurn({active_turn:null});'
    )
    body = body.replace(
        "{during, after:",
        "{forgotten, heldBefore, heldAfter:JSON.stringify(readInflight()), during, after:",
    )
    out = _run(tmp_path, page, {"history": [
        {"speaker": "founder", "text": "build the village map", "ts": 1},
        {"speaker": "universe", "text": "Old map.", "ts": 2},
        {"speaker": "founder", "text": "build the village map", "ts": 3},
        {"speaker": "universe", "text": "Here is the village map.", "ts": 4},
        {"speaker": "universe", "text": "Map saved.", "ts": 5},
        {"speaker": "founder", "text": "another request", "ts": 6},
        {"speaker": "universe", "text": "Unrelated reply.", "ts": 7},
    ]}, body)
    assert out["during"] == ["build the village map|working"]
    assert out["after"] == ["build the village map", "Here is the village map.", "Map saved."]
    assert out["sent"] == [], "nothing sent again"
    assert out["forgotten"] == 0
    assert out["heldAfter"] == out["heldBefore"], "display cannot confirm a send"


# -- gpt-6-astra on #4290, P1: an agent switch while a claim is in flight ------

_SWITCH_DURING_CLAIM = r"""
globalThis.authHeaders=()=>({});
// The page is showing one of the owner's OTHER agents, injected the way the
// agent switcher reaches this code.
let liveAgent="w1";
globalThis.addressedAgentId=()=>liveAgent;
const claims=[]; let releaseClaim=null;
globalThis.fetch=(url,init)=>{
  const body=init&&init.body?JSON.parse(init.body):{};
  if(body.claim){
    claims.push(body);
    // Held open, so the owner can switch agents while the claim is on the wire.
    return new Promise(resolve=>{ releaseClaim=()=>resolve({ok:true,status:200,
      json:async()=>({claimed:body.claim})}); });
  }
  return Promise.resolve({ok:true,status:200,json:async()=>(
    {pending:[{id:7,text:"check the methods section",state:"held"}]})});
};
// Which agent each converse went to; converseCalls keeps its own shape.
const agentCalls=[], realConverse=MCP.converse;
MCP.converse=async(m,im,mc,cr,agentId)=>{ agentCalls.push({m,agentId});
  return realConverse(m,im,mc,cr,agentId); };
setQueueOwner("p-1"); setQueueScope("u-1");
queueRestored=false; restoreQueue();
await settle(); await settle();
// The claim is on the wire and the line is already OUT of the queue: with no
// active turn, no queued line and no pending steer, this is exactly the state
// in which addressAgent permits a switch.
const during={claims:claims.slice(), queued:sendQueue.length,
  sent:converseCalls.slice()};
liveAgent="w2";                       // the owner switches agents, as allowed
releaseClaim();
await settle(); await settle();
console.log(JSON.stringify({during, agentCalls, sent:converseCalls.slice(),
  inflightAgent:(readInflight()||{}).agent||null}));
"""


def test_an_agent_switch_while_a_claim_is_in_flight_does_not_redirect_the_line(tmp_path):
    """A held line claimed for one agent is SENT to that agent, even if the
    owner switches agents while the claim is on the wire.

    The claim deletes the server's copy and the line is already out of the
    queue -- which is what lets ``addressAgent`` switch at all. Re-reading the
    live agent after the claim spoke one agent's held line to another
    (gpt-6-astra on #4290, P1). Dropping the line instead would lose it: its
    server copy is gone by then.
    """
    out = _other_window(tmp_path, {}, _SWITCH_DURING_CLAIM)

    # The window is real: the line left the queue before the claim answered.
    assert out["during"]["queued"] == 0 and out["during"]["sent"] == []
    # The claim named the agent the line was held for.
    assert [c["agent_id"] for c in out["during"]["claims"]] == ["w1"]
    # ...and so does the send that follows it, after the switch to w2.
    assert out["sent"] == ["check the methods section"]
    assert [c["agentId"] for c in out["agentCalls"]] == ["w1"]
    # The recovery record describes the turn on the wire, not the screen.
    assert out["inflightAgent"] == "w1"


_RESTORED_LINE_KEEPS_ITS_AGENT = r"""
globalThis.authHeaders=()=>({});
let liveAgent="w1";
globalThis.addressedAgentId=()=>liveAgent;
// No flush: a turn is running, so the restored line stays queued and saved.
globalThis.fetch=(url,init)=>{
  const body=init&&init.body?JSON.parse(init.body):{};
  if(body.claim) return Promise.resolve({ok:true,status:200,
    json:async()=>({claimed:body.claim})});
  return Promise.resolve({ok:true,status:200,json:async()=>(
    {pending:[{id:7,text:"check the methods section",state:"held"}]})});
};
setQueueOwner("p-1"); setQueueScope("u-1");
readServerTurn({active_turn:{turn_id:"t9",state:"inference_started",age_s:5,stale:false}});
queueRestored=false; restoreQueue();
await settle(); await settle();
console.log(JSON.stringify({queued:sendQueue.length,
  opts:sendQueue.map(q=>q.opts.agentId),
  saved:(JSON.parse(store[QUEUE_KEY]||"[]")).map(r=>r.agent)}));
"""


def test_a_restored_held_line_is_saved_under_the_agent_it_was_held_for(tmp_path):
    """A line restored for one agent records THAT agent on disk.

    ``savedItem`` reads the agent off ``opts.agentId``; without it a line held
    for another agent is written as the main agent's, and the next reload
    offers it on the wrong thread."""
    out = _other_window(tmp_path, {}, _RESTORED_LINE_KEEPS_ITS_AGENT)

    assert out["queued"] == 1, "a running turn keeps it queued"
    assert out["opts"] == ["w1"]
    assert out["saved"] == ["w1"], "the saved row names the agent, not 'main'"


_CLAIM_PINS_AN_UNPINNED_LINE = r"""
globalThis.authHeaders=()=>({});
let liveAgent="w1";
globalThis.addressedAgentId=()=>liveAgent;
const claims=[];
globalThis.fetch=(url,init)=>{
  const body=init&&init.body?JSON.parse(init.body):{};
  claims.push(body);
  return Promise.resolve({ok:true,status:200,json:async()=>({claimed:body.claim})});
};
setQueueOwner("p-1"); setQueueScope("u-1");
// A held line that reached the queue WITHOUT an agent of its own.
const item={message:"m", display:"m", owner:"p-1", scope:"u-1", bubble:null,
  heldId:11, opts:{echoed:true}, ts:Date.now()};
const kept=await claimHeldLines([item]);
liveAgent="w2";                       // the switch lands after the claim
console.log(JSON.stringify({claims, kept:kept.length, pinned:item.opts.agentId}));
"""


def test_the_claim_pins_the_agent_onto_a_line_that_had_none(tmp_path):
    """The claim writes the agent it claimed for onto the line.

    This is the invariant the send depends on: whatever agent the claim named,
    the line now carries, so no later reader can resolve a different one."""
    out = _other_window(tmp_path, {}, _CLAIM_PINS_AN_UNPINNED_LINE)

    assert [c["agent_id"] for c in out["claims"]] == ["w1"]
    assert out["kept"] == 1
    assert out["pinned"] == "w1", "pinned at claim time, before the switch"
