"""The owner stops their own live conversation turn (founder, 2026-09-30).

"there is no way to interrupt a run and push your messages. it should work just
like it works in claude code where i can press escape to interrupt and send all
pending messages".

The served-chat cases drive the REAL writer, router, HTTP adapter, journal and
engine client (``tests/test_interactive_http_agent.py``'s rig: only the remote
wires are synthetic). The native case drives the REAL ``ClaudeProvider``
against a real process tree (``tests/test_provider_real_adapter_deadline_reap``'s
probe: only command resolution and environment are patched), so "killed
cleanly" is observed on processes, not asserted of a mock.
"""
# ruff: noqa: F811 -- imported pytest fixtures

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_interactive_http_agent import agent, reader, rig, run, served  # noqa: F401
from tests.test_provider_real_adapter_deadline_reap import (  # noqa: F401
    _assert_tree_is_gone,
    _await_descendant_pid,
    probe,
)
from tinyassets import engine_tool_client, turn_interrupt
from tinyassets.agent_turn_coordinator import AgentTurnCoordinator, turn_effects
from tinyassets.providers.base import ModelConfig
from tinyassets.storage.agent_turn_boot import BOOT
from tinyassets.storage.agent_turn_journal import AgentTurnJournal
from tinyassets.turn_interrupt import TurnInterrupted, interactive_turn, request_interrupt


def _uid(agent):
    return agent.served.context.universe_dir.name


def _reservations(agent):
    with agent.journal._ledger.connection() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'served_provider_budget_reservations'"
        ).fetchone():
            return []  # nothing was ever reserved
        return [row[0] for row in conn.execute(
            "SELECT state FROM served_provider_budget_reservations ORDER BY rowid"
        )]


def _not_thinking(agent, turn):
    """No surface may paint this turn as work any more, and no seat is held."""
    observed = agent.journal.universe_working_turn(
        _uid(agent), now=datetime.now(timezone.utc), max_age_s=3600,
    )
    assert observed is None, f"a stopped turn still reads as thinking: {observed}"
    assert (_uid(agent), turn.turn_id) not in BOOT._claimed, "the boot still runs it"
    assert "reserved" not in _reservations(agent), "a reservation was left held"


# ---------------------------------------------------------------------------
# Served chat on an HTTP model: the real rig.
# ---------------------------------------------------------------------------


def test_stop_during_http_inference_keeps_the_reply_and_runs_no_tool(agent):
    """The HTTP round returns (it cannot be aborted); its tool call never runs."""
    with interactive_turn("owner", _uid(agent)) as live:
        agent.before_reply = live.request
        with pytest.raises(TurnInterrupted) as stopped:
            run(agent)
    turn = agent.latest()
    assert len(agent.wires) == 1 and agent.tools == [], "a tool ran after the stop"
    # The requested call is recorded as proven-unsent, never as unknown.
    assert turn.rounds[0].state == "received"
    assert [tool.state for tool in turn.rounds[0].tools] == ["not_sent"]
    assert turn.state == "held_tool_not_sent"
    # The round's real spend was settled, not charged as indeterminate.
    assert _reservations(agent) == ["succeeded"]
    assert stopped.value.turn_effects == "none"
    assert stopped.value.completed_tools == ()
    _not_thinking(agent, turn)


def test_stop_during_a_tool_call_lets_it_finish_and_starts_nothing_after(agent, monkeypatch):
    """A running engine call completes and is recorded; no further inference."""
    original = engine_tool_client.EngineToolSession.call
    with interactive_turn("owner", _uid(agent)) as live:
        async def call(self, name, arguments):
            live.request()
            return await original(self, name, arguments)

        monkeypatch.setattr(engine_tool_client.EngineToolSession, "call", call)
        with pytest.raises(TurnInterrupted) as stopped:
            run(agent)
    turn = agent.latest()
    assert len(agent.tools) == 1 and len(agent.wires) == 1, (
        "the stop either aborted the running tool or let another round start")
    tool = turn.rounds[0].tools[0]
    assert tool.state == "completed" and "exact result" in tool.result_json
    # Quiescent after a completed tool: closed with everything it did kept.
    assert turn.state == "abandoned"
    assert stopped.value.turn_effects == "some"
    # The model's one tool call: bash running `ta call read_graph` (four tools).
    assert stopped.value.completed_tools == ("bash",)
    assert "ta call read_graph" in agent.tools[0][1]["command"]
    _not_thinking(agent, turn)


def test_stop_before_the_first_round_sends_nothing(agent):
    with interactive_turn("owner", _uid(agent)) as live:
        live.request()
        with pytest.raises(TurnInterrupted) as stopped:
            run(agent)
    turn = agent.latest()
    assert agent.wires == [] and agent.tools == []
    assert turn.state == "abandoned" and not turn.rounds
    assert stopped.value.turn_effects == "none"
    _not_thinking(agent, turn)


def test_another_user_cannot_stop_the_turn(agent):
    """The key is the caller's own verified subject: nobody else's stop reaches it."""
    counts = []
    with interactive_turn("owner", _uid(agent)):
        def intruders():
            counts.append(request_interrupt("intruder", _uid(agent)))
            # The owner's name on a DIFFERENT universe is not this turn either.
            counts.append(request_interrupt("owner", "some-other-universe"))

        agent.before_reply = intruders
        assert run(agent) == "finished exact answer"
    # Asked before every reply the turn received (two rounds), never honoured.
    assert counts == [0, 0, 0, 0]
    assert agent.latest().state == "completed" and len(agent.tools) == 1


def test_the_registry_is_empty_once_the_turn_returns(agent):
    with interactive_turn("owner", _uid(agent)):
        assert turn_interrupt.live_count("owner", _uid(agent)) == 1
        run(agent)
    assert turn_interrupt.live_count("owner", _uid(agent)) == 0
    assert request_interrupt("owner", _uid(agent)) == 0


def test_an_empty_subject_is_never_a_key():
    with pytest.raises(ValueError):
        request_interrupt("", "u-1")
    with pytest.raises(ValueError), interactive_turn("  ", "u-1"):
        pass


# ---------------------------------------------------------------------------
# A native CLI turn: the real Claude adapter against a real process tree.
# ---------------------------------------------------------------------------


class _NativeAdapter:
    """Only the served-authority seams are stand-ins; the provider call is real."""

    def check(self, context, config):
        return "owner"

    def create_turn(self, journal, *, owner, context, prompt, system, plan):
        return journal.create(owner, context.universe_dir.name, prompt=prompt, system=system)

    def round_input(self, authority, reservation, config, *, owner, context,
                    prompt, system, native_input, kind):
        from tinyassets.storage.agent_native_records import NativeInput

        assert kind == "native_agent"
        return NativeInput("claude-code", "", "binding-1", "reservation-1", 1,
                           "sha256:" + "0" * 64, "sha256:" + "1" * 64)

    async def infer(self, *, router, prompt, system, config, context, observer, kind):
        from tinyassets.providers.claude_provider import ClaudeProvider

        observer(None, None, config)
        # Generous bounds: only the owner's stop may end this call.
        return await ClaudeProvider().complete(prompt, "", ModelConfig(
            init_timeout_s=60.0, first_progress_s=60.0, idle_timeout_s=60.0,
            absolute_cap_s=120.0,
        ))


def _native_coordinator(base: Path, live):
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home

    uid = "u-native"
    (base / uid).mkdir(parents=True)
    set_founder_home(base, founder_sub="owner", universe_id=uid, platform_generated=True)
    # The first owner check installs the starter, which needs the owner binding.
    grant_universe_ownership(base, universe_id=uid, owner_id="owner")
    router = SimpleNamespace(selected_agent_execution_kind=lambda selection: "native_agent")
    context = SimpleNamespace(
        universe_dir=base / uid, agent_model_plan=None,
        model_selection=SimpleNamespace(connection_id="claude-code", model_id=""),
    )
    return AgentTurnCoordinator(
        adapter=_NativeAdapter(), router=router, prompt="do the long thing", system="",
        universe_context=context, config=ModelConfig(absolute_cap_s=120.0),
        interrupt=live,
    )


def test_stop_kills_a_native_cli_turn_and_releases_it(tmp_path, probe):
    from tinyassets.providers import claude_provider as claude_mod

    probe.install(claude_mod, frame="claude", cmd_resolver="_resolve_claude_cmd",
                  env_builder="subprocess_env_for_provider")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(claude_mod, "_sandbox_cli_args", lambda *a, **k: ([], None))
    base = tmp_path / "data"
    try:
        with interactive_turn("owner", "u-native") as live:
            coordinator = _native_coordinator(base, live)

            async def drive():
                task = asyncio.ensure_future(coordinator.run())
                await _await_descendant_pid(probe.pid_file)
                assert coordinator.turn.state == "native_started"
                started = time.monotonic()
                # From another thread, as the app route delivers it.
                threading.Thread(target=live.request).start()
                with pytest.raises(TurnInterrupted) as stopped:
                    await task
                return time.monotonic() - started, stopped.value

            elapsed, stopped = asyncio.run(drive())
    finally:
        monkey.undo()
    # Promptly: the CLI streams forever, so only the stop could have ended it.
    assert elapsed < 15, f"the stop took {elapsed:.1f}s"
    _assert_tree_is_gone(probe, probe.procs[-1].pid)
    turn = AgentTurnJournal(base).get("owner", "u-native", coordinator.turn.turn_id)
    # A killed agent may have acted: recorded as indeterminate, never as done.
    assert turn.state == "held_native_unknown"
    assert turn.rounds[-1].reply.status == "indeterminate"
    assert turn_effects(turn)[0] == "unknown" and stopped.turn_effects == "unknown"
    assert ("u-native", turn.turn_id) not in BOOT._claimed


class _ActivityNativeAdapter(_NativeAdapter):
    """The native call run for an activity, through the work adapter's own gate."""

    def __init__(self, binding):
        self.activity_binding = binding

    async def infer(self, **kwargs):
        from tinyassets.workflow_agent import WorkAgentAdapter

        return await WorkAgentAdapter._until_activity_stops(self, super().infer(**kwargs))


def test_an_activity_yield_kills_a_native_cli_turn_and_releases_it(tmp_path, probe):
    """The activity twin of the owner's stop: once the activity yields to an
    owner request, the native CLI's process tree is ended and its claim is
    released -- no further native tool loop, no held seat."""
    from tinyassets import activity_runner, agent_activities
    from tinyassets.providers import claude_provider as claude_mod

    probe.install(claude_mod, frame="claude", cmd_resolver="_resolve_claude_cmd",
                  env_builder="subprocess_env_for_provider")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(claude_mod, "_sandbox_cli_args", lambda *a, **k: ([], None))
    base = tmp_path / "data"
    try:
        coordinator = _native_coordinator(base, None)
        universe = base / "u-native"
        record = agent_activities.create(universe, owner_principal="owner", title="t",
                                         brief="b", origin_kind="ask")
        aid = record["activity_id"]
        generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
        assert agent_activities.bind_run(universe, aid, generation, "run-1")
        coordinator.adapter = _ActivityNativeAdapter(
            activity_runner.ActivityRunBinding(universe, aid, generation, "run-1"))

        async def drive():
            task = asyncio.ensure_future(coordinator.run())
            await _await_descendant_pid(probe.pid_file)
            assert coordinator.turn.state == "native_started"
            started = time.monotonic()
            # The agent's own ask, as the engine route records it.
            threading.Thread(target=agent_activities.wait_on,
                             args=(universe, aid, "req-1", "asked")).start()
            with pytest.raises(activity_runner.ActivityYielded):
                await task
            return time.monotonic() - started

        elapsed = asyncio.run(drive())
    finally:
        monkey.undo()
    assert elapsed < 15, f"the yield took {elapsed:.1f}s to end the native turn"
    _assert_tree_is_gone(probe, probe.procs[-1].pid)
    turn = AgentTurnJournal(base).get("owner", "u-native", coordinator.turn.turn_id)
    # The agent acted before its ask: recorded as indeterminate, never as done.
    assert turn.state == "held_native_unknown"
    assert ("u-native", turn.turn_id) not in BOOT._claimed
    assert agent_activities.get(universe, aid)["status"] == agent_activities.WAITING_ON_YOU


# ---------------------------------------------------------------------------
# The unplanned served call (no journal) and everything that is not a chat.
# ---------------------------------------------------------------------------


def _slow_provider(kind: str, seconds: float):
    """A provider of one execution kind, dispatched by the REAL router."""
    from tinyassets.providers.base import BaseProvider, ProviderResponse

    state = SimpleNamespace(cancelled=False, finished=False)

    class Slow(BaseProvider):
        name = "claude-code" if kind == "native_agent" else "http-source"
        family = "anthropic"
        agent_execution_kind = kind

        async def complete(self, prompt, system, config, *, universe_dir=None):
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                state.cancelled = True
                raise
            state.finished = True
            return ProviderResponse(text="done", provider=self.name, model="m",
                                    family="anthropic", latency_ms=1.0,
                                    input_tokens=1, output_tokens=1, cost_microunits=5)

    return Slow(), state


def _call_sync(provider, operation):
    """``router.call_sync`` bound to one owner, as the unplanned served path is."""
    from unittest.mock import patch

    from tests.support.owner_bound import owner_carrier
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.router import ProviderRouter

    router = ProviderRouter(providers={provider.name: provider})
    carrier = owner_carrier(provider.name, operation=operation)
    context = UniverseContext(universe_dir=Path("u-owner-bound"), provider_invocation=carrier)
    with patch("tinyassets.providers.router._provider_invocation_carrier",
               return_value=carrier):
        return router.call_sync("writer", "p", "s", ModelConfig(absolute_cap_s=60.0),
                                operation=operation, universe_context=context)


def test_an_unplanned_native_served_call_is_cancelled_by_the_stop():
    provider, state = _slow_provider("native_agent", 30.0)
    with interactive_turn("owner", "u-1") as live:
        threading.Timer(0.3, live.request).start()
        started = time.monotonic()
        with pytest.raises(TurnInterrupted):
            _call_sync(provider, "converse")
    assert time.monotonic() - started < 10 and state.cancelled and not state.finished


def test_a_stop_already_asked_for_launches_nothing_and_charges_nothing():
    """astra round 2: a native call cancelled before its coroutine ran was settled
    INDETERMINATE (its reservation consumed) although nothing launched."""
    from unittest.mock import patch

    from tests.support.owner_bound import owner_carrier
    from tinyassets.provider_work_authority import (
        ProviderInvocationReservationState,
        ProviderInvocationSettlementOwner,
    )
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.router import ProviderRouter

    provider, state = _slow_provider("native_agent", 30.0)
    entered = []
    original = provider.complete

    async def complete(*args, **kwargs):
        entered.append(True)
        return await original(*args, **kwargs)

    provider.complete = complete
    router = ProviderRouter(providers={provider.name: provider})
    carrier = owner_carrier(provider.name, operation="converse")
    carrier.settlement_owner = ProviderInvocationSettlementOwner.ROUTER
    context = UniverseContext(universe_dir=Path("u-owner-bound"), provider_invocation=carrier)
    with interactive_turn("owner", "u-1") as live:
        live.request()
        with patch("tinyassets.providers.router._provider_invocation_carrier",
                   return_value=carrier), pytest.raises(TurnInterrupted):
            router.call_sync("writer", "p", "s", ModelConfig(absolute_cap_s=60.0),
                             operation="converse", universe_context=context)
    assert entered == [], "the provider was launched after the owner's Stop"
    settled = [call.args[0] for call in carrier.settle.call_args_list]
    assert settled == [ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH], settled


def test_an_http_call_is_never_cancelled_so_its_usage_settles():
    """astra round 1: cancelling an HTTP call lost its response and its real spend.

    The HTTP provider waits for its request even when cancelled, so a stop there
    buys no time; the call returns and the turn stops at its next boundary.
    """
    provider, state = _slow_provider("engine_inference", 0.6)
    with interactive_turn("owner", "u-1") as live:
        threading.Timer(0.1, live.request).start()
        assert _call_sync(provider, "converse").text == "done"
        assert live.requested()
    assert state.finished and not state.cancelled


def test_another_operation_inside_the_chat_turns_own_loop_is_not_stopped():
    """The dispatch site gates on the operation itself, not only on call_sync."""
    from tests.support.owner_bound import owner_bound_call
    from tinyassets.providers.router import ProviderRouter

    provider, state = _slow_provider("native_agent", 0.2)
    router = ProviderRouter(providers={provider.name: provider})
    with interactive_turn("owner", "u-1") as live:
        live.request()
        # asyncio.run copies this context, so the live turn IS visible here.
        response = asyncio.run(owner_bound_call(router, provider.name))
    assert response.text == "done" and state.finished and not state.cancelled


def test_a_call_that_is_not_the_chat_turn_is_never_stopped():
    """A stop already requested reaches only ``converse``: never an automation's call."""
    provider, state = _slow_provider("native_agent", 0.2)
    with interactive_turn("owner", "u-1") as live:
        live.request()
        assert _call_sync(provider, "run_graph").text == "done"
    assert state.finished and not state.cancelled


def test_a_workflow_agent_turn_never_takes_the_live_stop():
    """Automations and agent nodes run through WorkflowAgentTurn: no interrupt handle."""
    from tinyassets.workflow_agent import WorkflowAgentTurn

    with interactive_turn("owner", "u-1") as live:
        live.request()
        node = WorkflowAgentTurn(
            adapter=SimpleNamespace(), router=SimpleNamespace(), prompt="p", system="s",
            universe_context=SimpleNamespace(agent_model_plan=None), config=ModelConfig(),
        )
    assert node.interrupt is None


# ---------------------------------------------------------------------------
# What the owner sees, and the route that asks.
# ---------------------------------------------------------------------------


def test_the_notice_says_interrupted_and_what_ran():
    from tinyassets.conversation_failure import failure_notice, turn_failure

    nothing = failure_notice(turn_failure("interrupted", effects="none"))
    assert nothing.startswith("Interrupted — you stopped this turn") and "Nothing ran." in nothing
    some = failure_notice(turn_failure(
        "interrupted", stage="tool", effects="some",
        provider_detail="Completed before the stop: read_graph",
    ))
    assert some.startswith("Interrupted")
    assert "Some actions ran before it stopped" in some and "read_graph" in some
    # Never led by the stage: a stop before a tool is not that tool failing.
    assert "Running one of your universe's tools" not in some


def test_converse_returns_interrupted_and_keeps_it_in_history(monkeypatch, tmp_path):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth
    from tinyassets.conversation_store import load_recent

    _founder_auth(monkeypatch, base=tmp_path)
    seen = []

    def stopped_turn(uid, msg, **kwargs):
        seen.append(turn_interrupt.live_count("founder-1", uid))
        assert request_interrupt("founder-1", uid) == 1
        error = TurnInterrupted("stopped")
        error.turn_effects, error.turn_stage, error.turn_ref = "some", "tool", "t-ref-1"
        error.completed_tools = ("read_graph",)
        turn_interrupt.current().check()
        raise error

    monkeypatch.setattr(ui, "converse", stopped_turn)
    out = json.loads(us.converse(message="do the thing", graph_id="u-x"))
    assert seen == [1], "the served turn was not registered for its caller"
    assert out["interrupted"] is True and out["history_saved"] is True
    assert out["failure_notice"].startswith("Interrupted")
    assert out["turn_failure"]["code"] == "interrupted"
    assert turn_interrupt.live_count("founder-1", "u-x") == 0
    rows = load_recent(tmp_path / "u-x", "principal:founder-1")
    assert [r.speaker for r in rows][-2:] == ["founder", "platform"]
    assert rows[-2].text == "do the thing" and rows[-1].text.startswith("Interrupted")


class _Request:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")
        self.headers = {"content-type": "application/json", "origin": "https://tinyassets.io",
                        "host": "tinyassets.io", "content-length": str(len(self._body))}

    async def stream(self):
        yield self._body


def test_the_route_stops_only_the_callers_own_turn(monkeypatch):
    from tinyassets import onboarding
    from tinyassets.auth import middleware

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    caller = SimpleNamespace(user_id="owner")
    monkeypatch.setattr(middleware, "current_identity", lambda: caller)

    def post(body):
        response = asyncio.run(onboarding._handle_turn_interrupt(_Request(body)))
        return response.status_code, json.loads(response.body)

    with interactive_turn("owner", "u-1") as mine, interactive_turn("other", "u-1") as theirs:
        assert post({"universe_id": "u-1"}) == (200, {"interrupted": 1, "universe_id": "u-1"})
        assert mine.requested() and not theirs.requested(), (
            "a stop reached another user's turn in the same universe")
    caller.user_id = "intruder"
    with interactive_turn("owner", "u-1") as mine:
        assert post({"universe_id": "u-1"})[1]["interrupted"] == 0
        assert not mine.requested()


def test_live_run_returns_work_that_finished_before_the_stop():
    """A result that exists is kept; the stop then applies at the next boundary."""
    live = turn_interrupt.LiveTurn("owner", "u-1")

    async def quick():
        return "answer"

    async def drive():
        return await live.run(quick())

    assert asyncio.run(drive()) == "answer"



def test_a_stop_reaches_the_addressed_agent_and_stop_all_reaches_every_one():
    """Harness §4.18: a stop targets the agent being talked to; stop-all stays."""
    from tinyassets import turn_interrupt as ti

    with ti.interactive_turn("owner-x", "u-x") as main_turn, \
            ti.interactive_turn("owner-x", "u-x", agent_id="researcher") as other:
        assert ti.request_interrupt("owner-x", "u-x", agent_id="researcher") == 1
        assert other.requested() and not main_turn.requested()
        assert ti.request_interrupt("owner-x", "u-x") == 2
        assert main_turn.requested()


def test_the_route_stops_the_addressed_agent_and_leaves_the_others_running(monkeypatch):
    """Harness §4.18: Stop belongs to the conversation it was pressed in.

    With no ``agent_id`` the route is explicit stop-all, which is what it has
    always been and what a caller that wants everything still gets. With one,
    it must reach only that agent's live turn -- otherwise ending a main chat
    ends a custom agent's background turn too.
    """
    from types import SimpleNamespace as NS

    from tinyassets import addressed_agents, onboarding
    from tinyassets.api import helpers
    from tinyassets.auth import middleware

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(middleware, "current_identity", lambda: NS(user_id="owner"))
    monkeypatch.setattr(helpers, "_base_path", lambda: Path("/nonexistent-base"))

    def resolve(_base, *, universe_id, owner, agent_id):
        assert (universe_id, owner) == ("u-1", "owner")
        wanted = "" if agent_id is None else str(agent_id)
        if wanted in ("", "main"):
            return None
        if wanted == "a-weaver":
            return NS(agent_id="a-weaver", name="Evidence Weaver")
        raise addressed_agents.AgentNotAddressable(f"no agent {wanted!r} here")

    monkeypatch.setattr(addressed_agents, "resolve", resolve)

    def post(body):
        response = asyncio.run(onboarding._handle_turn_interrupt(_Request(body)))
        return response.status_code, json.loads(response.body)

    with interactive_turn("owner", "u-1") as main_turn, \
            interactive_turn("owner", "u-1", agent_id="a-weaver") as weaver:
        status, answer = post({"universe_id": "u-1", "agent_id": "a-weaver"})
        assert status == 200
        assert answer == {"interrupted": 1, "universe_id": "u-1", "agent_id": "a-weaver"}
        assert weaver.requested(), "the addressed agent's turn was not stopped"
        assert not main_turn.requested(), "the main turn was stopped as well"

        # main is an agent, not a wildcard.
        assert post({"universe_id": "u-1", "agent_id": "main"})[1]["interrupted"] == 1
        assert main_turn.requested()

    # An id that is not the owner's own is refused by name, never widened to all.
    with interactive_turn("owner", "u-1") as mine:
        status, refused = post({"universe_id": "u-1", "agent_id": "someone-elses"})
        assert status == 404 and refused["error"] == "agent_not_found"
        assert not mine.requested(), "a refused agent id stopped a turn anyway"

    # No agent_id at all stays the explicit stop-all this route has always been.
    with interactive_turn("owner", "u-1") as a, \
            interactive_turn("owner", "u-1", agent_id="a-weaver") as b:
        assert post({"universe_id": "u-1"})[1] == {"interrupted": 2, "universe_id": "u-1"}
        assert a.requested() and b.requested()


@pytest.mark.parametrize("failure", [
    TurnInterrupted("connection ended"), ConnectionError("connection lost"),
])
def test_disconnect_never_records_an_owner_stop(monkeypatch, tmp_path, failure):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us
    from tests.test_converse_handle import _founder_auth
    from tinyassets.conversation_store import load_recent

    _founder_auth(monkeypatch, base=tmp_path)

    def disconnected(uid, msg, **kwargs):
        assert not turn_interrupt.current().requested()
        raise failure

    monkeypatch.setattr(ui, "converse", disconnected)
    out = json.loads(us.converse(message="do the thing", graph_id="u-x"))
    assert not out.get("interrupted")
    assert out["history_saved"]
    assert out["turn_failure"]["code"] != "interrupted"
    rows = load_recent(tmp_path / "u-x", "principal:founder-1")
    assert "you stopped" not in rows[-1].text


def test_cancelling_a_live_connection_does_not_request_stop():
    async def scenario():
        live = turn_interrupt.LiveTurn("owner", "u-1")
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(live.run(work()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not live.requested()

    asyncio.run(scenario())
