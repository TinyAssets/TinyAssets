"""The served codex turn runs under the idle-watchdog profile - parity with claude.

Founder rule 2026-08-29: *"a turn should continue till finished unless
interrupted by the user or should stop for some other reason."*

What happened: the universe was three clean GitHub round-trips into a five-step
job when the served turn hit ``timeout=300``. The generic
``ProviderTimeoutError`` put the provider on a 120s cooldown and the user read
*"Served provider 'codex' exhausted"* - a timer reported as a quota.

The served turn is now ``codex app-server`` (``codex_app_server.AppServerTurn``)
rather than ``codex exec --json``, and these are the exec reader's guarantees
carried over to it: an idle watchdog is the hang control, the absolute cap is a
backstop, neither cools the provider, and a turn waiting on its OWN tool is not
idle (``run_graph`` took 42s live; a 30s idle budget would have killed it). The
scripted server (tests/support/fake_codex_app_server.py) stands in for the CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
import types

import pytest
from mcp.types import CallToolResult, TextContent

from tests.support.fake_codex_app_server import EOF, ScriptedAppServer, finished
from tests.test_codex_app_server import served  # noqa: F401 - the shared fixture
from tinyassets.exceptions import (
    InteractiveDeadlineError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderTimeoutError,
)

_PROFILE = dict(init_timeout_s=1.0, first_progress_s=1.0, idle_timeout_s=0.25,
                soft_slo_s=60.0, absolute_cap_s=5.0)

_STARTED = (0, {"method": "turn/started", "params": {"threadId": "thr-1"}})


class SlowTools:
    """Engine tools that take ``delays[name]`` seconds; ``None`` never returns."""

    def __init__(self, delays=None):
        from tests.test_codex_app_server import _engine_tools

        self.tools = _engine_tools()
        self.delays = delays or {}
        self.calls = []
        self.cancelled = []
        self.running = 0
        self.most_at_once = 0

    async def call(self, name, arguments):
        self.calls.append(name)
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        try:
            delay = self.delays.get(name, 0)
            if delay is None:
                await asyncio.Event().wait()
            elif delay:
                await asyncio.sleep(delay)
            return CallToolResult(content=[TextContent(type="text", text=f"{name} ok")])
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        finally:
            self.running -= 1


def _waits(monkeypatch, *, turn=None, tool=None):
    from tinyassets.providers import codex_provider

    if turn is not None:
        monkeypatch.setattr(codex_provider, "_TURN_WAIT_S", turn)
    if tool is not None:
        monkeypatch.setattr(codex_provider, "_TOOL_WAIT_S", tool)


async def _play(fixture, script, *, tools=None, launch_delay=0.0, exit_code=None, **profile):
    run, _launch, state, config, _root = fixture
    if tools is not None:
        state["tools"] = tools
    server = ScriptedAppServer(script, launch_delay=launch_delay, exit_code=exit_code)
    t0 = time.monotonic()
    try:
        response, _ = await run(server=server, cfg=config(**{**_PROFILE, **profile}))
    finally:
        server.elapsed = time.monotonic() - t0
    return response, server


async def _stop(fixture, script, error, **kwargs):
    server_box = {}

    async def go():
        try:
            return await _play(fixture, script, **kwargs)
        finally:
            server_box["server"] = fixture[2]["server"]

    with pytest.raises(error) as info:
        await go()
    return info, server_box["server"]


# --- a turn waiting on its own tool is not idle -------------------------------


@pytest.mark.asyncio
async def test_a_tool_call_longer_than_the_idle_budget_does_not_kill_the_turn(
        served, monkeypatch):  # noqa: F811
    """The case that would have made the new reader WORSE than the 300s cap."""
    _waits(monkeypatch, turn=0.25)
    response, server = await _play(served, [
        _STARTED, (0, ("call", "bash", {"command": "run_graph"})), *finished(),
    ], tools=SlowTools({"bash": 0.7}))                  # 0.7s > the 0.25s turn wait
    assert response.text == "done"
    assert server.tool_results[0]["success"] is True


@pytest.mark.asyncio
async def test_silence_after_the_first_event_is_generation_not_idle(served):  # noqa: F811
    """Once the turn is started, silence is the model generating: app-server
    sends nothing until it has something to say."""
    steps = finished()
    response, server = await _play(served, [(0.7, steps[0][1]), *steps[1:]],
                                   init_timeout_s=0.3)
    assert response.text == "done"
    assert server.elapsed >= 0.7, "outlived the launch budget: the turn rule applied"


@pytest.mark.asyncio
async def test_silence_after_a_tool_result_is_the_next_generation_step(served):  # noqa: F811
    """THE live failure: tool result at 08:47:31, next tool call at 08:48:02,
    killed as idle at 08:48:16. After a tool answers the model reads the result
    and generates the next step - the same silence as before the first call."""
    steps = finished()
    response, server = await _play(served, [
        _STARTED, (0, ("call", "bash", {"command": "x"})), (0.7, steps[0][1]), *steps[1:],
    ], tools=SlowTools({"bash": 0.4}), init_timeout_s=0.3)
    assert response.text == "done"
    assert server.tool_results == [
        {"success": True, "contentItems": [{"type": "inputText", "text": "bash ok"}]}]


@pytest.mark.asyncio
async def test_the_launch_edge_is_still_guarded_by_the_init_budget(served):  # noqa: F811
    """Before the CLI answers anything nothing is generating: a server that
    never speaks is ended on ``init_s``."""
    info, server = await _stop(served, finished(), ProviderIdleTimeoutError,
                               launch_delay=5.0, init_timeout_s=0.3)
    assert server.killed is True
    assert info.value.attempt_telemetry["phase"] == "launch"
    assert info.value.attempt_telemetry["tool_phase"] is None


# --- an open turn's silence is the model generating, not a hang ----------------


@pytest.mark.asyncio
async def test_model_generation_silence_inside_an_open_turn_is_not_idle(served):  # noqa: F811
    response, server = await _play(served, [
        _STARTED,
        (0, ("call", "bash", {"command": "x"})),          # tool answered...
        (0.7, ("call", "read", {"path": "y"})),           # ...model thinks 0.7s
        *finished(),
    ], tools=SlowTools())
    assert response.text == "done"
    assert len(server.tool_results) == 2


@pytest.mark.asyncio
async def test_generation_silence_with_the_best_effort_events_dropped_is_still_not_idle(
        served):  # noqa: F811
    """No ``turn/started`` at all: the turn is running once ``turn/start`` was
    answered, or the generation rule silently reverts to the launch budget."""
    steps = finished()
    response, server = await _play(served, [
        (0, ("call", "bash", {"command": "x"})), (0.7, steps[0][1]), *steps[1:],
    ], tools=SlowTools(), init_timeout_s=0.3)
    assert response.text == "done"
    assert server.requests("turn/start"), "the turn ran without a turn/started"


@pytest.mark.asyncio
async def test_a_stalled_exit_after_turn_completed_returns_the_finished_stream(
        served):  # noqa: F811
    """``turn/completed`` is the turn's end. A server still holding stdout
    open afterwards is ended at once and the finished turn is returned."""
    response, server = await _play(served, [
        *finished(), (5.0, {"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "text": "straggler"}}}),
    ])
    assert server.elapsed < 2.0
    assert response.text == "done"
    assert server.killed is True                            # ended, not failed


@pytest.mark.asyncio
async def test_the_tail_grace_outlasts_codex_own_shutdown_bound(served):  # noqa: F811
    """There is no tail grace left to outlast: the adapter ends the app server
    itself once the turn completes, never waiting on codex's own shutdown."""
    response, server = await _play(served, [*finished(), (60.0, EOF)])
    assert server.elapsed < 2.0 and server.killed and response.text == "done"


@pytest.mark.asyncio
async def test_a_failed_turn_with_a_stalled_exit_is_cut_the_same_way(served):  # noqa: F811
    """A failed ``turn/completed`` is terminal too: its reason is reported, not
    an idle timeout mislabelling a turn failure."""
    info, server = await _stop(served, [
        _STARTED, *finished(reply="", status="failed", error="boom"), (5.0, EOF),
    ], ProviderError)
    assert "boom" in str(info.value)
    assert not isinstance(info.value, ProviderTimeoutError)
    assert server.killed is True and server.elapsed < 2.0


@pytest.mark.asyncio
async def test_turn_completed_closes_a_tool_left_open(served):  # noqa: F811
    """A tool still running when the turn completes is not waited for."""
    tools = SlowTools({"bash": None})                     # never returns
    steps = finished()
    response, server = await _play(served, [
        _STARTED, (0, ("call_nowait", "bash", {"command": "x"})), (0.1, steps[0][1]), *steps[1:],
    ], tools=tools)
    assert server.elapsed < 2.0
    assert response.text == "done" and tools.cancelled == ["bash"]


@pytest.mark.asyncio
async def test_an_open_turn_is_still_bounded_by_the_cap(served):  # noqa: F811
    info, server = await _stop(served, [_STARTED, (5.0, EOF)], InteractiveDeadlineError,
                               absolute_cap_s=0.5)
    assert info.value.attempt_telemetry["tool_phase"] == "in_turn"
    assert server.killed is True


# --- the cap is a backstop, and it is classified honestly ---------------------


@pytest.mark.asyncio
async def test_progressing_past_the_absolute_cap_is_an_interactive_deadline(
        served, monkeypatch):  # noqa: F811
    """Still working, out of runway: NOT the generic timeout the router cools on.
    Progress every 0.1s keeps the 0.25s turn wait from ever firing."""
    _waits(monkeypatch, turn=0.25)
    progress = (0.1, {"method": "item/started", "params": {"item": {"type": "agentMessage"}}})
    info, server = await _stop(served, [_STARTED] + [progress] * 40, InteractiveDeadlineError,
                               absolute_cap_s=0.45)
    assert info.value.failure_class == "interactive_deadline"
    assert isinstance(info.value, ProviderTimeoutError), (
        "must stay a ProviderTimeoutError subclass for legacy except clauses"
    )
    assert server.killed is True


@pytest.mark.asyncio
async def test_a_finished_turn_returns_stdout_and_stderr_unchanged(served):  # noqa: F811
    """Downstream sees exactly what codex reported: its reply and its usage.
    Blank lines are not protocol."""
    response, _server = await _play(served, [
        _STARTED, (0, b"\n"), *finished(reply="hi", usage=(3, 2)),
    ])
    assert response.text == "hi"
    assert response.input_tokens == 3
    assert response.output_tokens == 2


@pytest.mark.asyncio
async def test_non_json_output_keeps_the_process_but_not_the_clock(
        served, monkeypatch):  # noqa: F811
    """Chatter proves the process is alive, not that it is making progress.
    Inside the turn the bound is ``_TURN_WAIT_S``."""
    _waits(monkeypatch, turn=0.25)
    info, _server = await _stop(served, [
        _STARTED, (0.15, b"not json\n"), (0.15, b"still not json\n"),
        (0.15, finished()[0][1]),                         # never reached
    ], ProviderIdleTimeoutError)
    assert info.value.attempt_telemetry["tool_phase"] == "in_turn"
    assert info.value.attempt_telemetry["phase"] == "streaming"


# --- the served turn gets the generous cap, not the library default -----------


def test_the_served_turn_has_no_wall_clock_only_a_hang_control():
    """A granted turn runs until it is FINISHED (founder, 2026-09-30).

    ``_SERVED_ABSOLUTE_CAP_S = 3600`` killed it at the hour mark. The 30-second
    idle watchdog stays: a provider that has emitted nothing is hung, which is
    liveness, not duration.
    """
    from tinyassets import universe_intelligence
    from tinyassets.universe_intelligence import (
        UNBOUNDED_TURN_SECONDS,
        _sandboxed_config,
    )

    assert not hasattr(universe_intelligence, "_SERVED_ABSOLUTE_CAP_S")

    ctx = types.SimpleNamespace(config=types.SimpleNamespace(timeout=300))
    cfg = _sandboxed_config(ctx, granted=True)
    profile = cfg.stream_timeout_profile()
    # The profile's field is a float and None there means the library 600s, so
    # "no cap" is an unreachable number rather than None: 30 days, which no turn
    # reaches and which every timeout API on both platforms accepts.
    import threading

    assert profile.absolute_cap_s == UNBOUNDED_TURN_SECONDS
    assert profile.absolute_cap_s >= 30 * 24 * 3600
    assert profile.absolute_cap_s < threading.TIMEOUT_MAX, (
        "a cap the executor cannot accept is a cap of zero, not of none"
    )
    assert profile.idle_s == 30.0, "the hang control stays fast"


def test_a_universe_may_override_its_own_knobs():
    from tinyassets.universe_intelligence import _sandboxed_config

    ctx = types.SimpleNamespace(
        config=types.SimpleNamespace(timeout=300, absolute_cap_s=120, idle_timeout_s=10)
    )
    profile = _sandboxed_config(ctx, granted=True).stream_timeout_profile()
    assert profile.absolute_cap_s == 120.0
    assert profile.idle_s == 10.0


def test_a_nonsense_override_falls_back_to_no_cap_rather_than_a_guessed_one():
    """"forever" is not a number, so it is not honoured as one -- and the
    fallback is the platform default, which is now no cap at all."""
    from tinyassets.universe_intelligence import (
        UNBOUNDED_TURN_SECONDS,
        _sandboxed_config,
    )

    ctx = types.SimpleNamespace(
        config=types.SimpleNamespace(timeout=300, absolute_cap_s="forever")
    )
    profile = _sandboxed_config(ctx, granted=True).stream_timeout_profile()
    assert profile.absolute_cap_s == UNBOUNDED_TURN_SECONDS


# --- the real vocabulary, recorded from codex-cli 0.160.0 ---------------------


@pytest.mark.asyncio
async def test_the_real_codex_vocabulary_streams_through_and_the_tool_key_matches(
        served):  # noqa: F811
    """The server messages a real codex-cli 0.160.0 app-server turn produced
    (one dynamic ``read`` call, then a reply), replayed in order. A wrong
    method or field name here would make every served turn idle out or lose
    its reply (tests/support/codex_app_server_recording.py)."""
    from tests.support.codex_app_server_recording import recorded_turn

    script, call = recorded_turn()
    tools = SlowTools({"read": 0.7})                  # beyond the 0.25s idle budget
    response, server = await _play(served, script, tools=tools)
    replies = [step["params"]["item"]["text"] for _, step in script
               if step.get("method") == "item/completed"
               and step["params"]["item"]["type"] == "agentMessage"]
    assert tools.calls == [call["tool"]]
    assert response.text == replies[-1]
    assert (response.input_tokens, response.output_tokens) == (240, 20)


@pytest.mark.asyncio
async def test_a_tool_that_never_completes_is_still_bounded_by_the_cap(served):  # noqa: F811
    """A wedged tool waits for the tool allowance or the cap, whichever is
    first; with the default allowance the cap ends it. Must stay bounded."""
    info, server = await _stop(served, [_STARTED, (0, ("call", "bash", {"command": "x"}))],
                               InteractiveDeadlineError, tools=SlowTools({"bash": None}),
                               absolute_cap_s=0.5)
    assert server.killed is True
    assert info.value.attempt_telemetry["tool_phase"] == "in_tool"


def test_an_ungranted_turn_keeps_the_library_cap():
    """Codex round 2 (P1): the synchronous learning extractor calls
    _sandboxed_config with the defaults and runs BEFORE the reply is returned,
    so a generous cap there could withhold an already-generated reply."""
    from tinyassets.providers.base import DEFAULT_ABSOLUTE_CAP_S
    from tinyassets.universe_intelligence import _sandboxed_config

    ctx = types.SimpleNamespace(config=types.SimpleNamespace(timeout=300))
    profile = _sandboxed_config(ctx).stream_timeout_profile()
    assert profile.absolute_cap_s == DEFAULT_ABSOLUTE_CAP_S


# --- the tool rule ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wedged_tool_is_bounded_by_the_tool_wait_not_the_cap(
        served, monkeypatch):  # noqa: F811
    """Codex round 2 (P1): "not idle until the absolute cap" turned a silent
    wedge into an hour-long wait. The tool allowance is bounded."""
    _waits(monkeypatch, tool=0.3)
    info, server = await _stop(served, [_STARTED, (0, ("call", "bash", {"command": "x"}))],
                               ProviderIdleTimeoutError, tools=SlowTools({"bash": None}),
                               absolute_cap_s=60.0)        # the cap is NOT what fires
    assert info.value.attempt_telemetry["tool_phase"] == "in_tool"
    assert server.killed is True
    assert server.elapsed < 5.0


@pytest.mark.asyncio
async def test_a_recoverable_error_event_does_not_clear_an_open_tool(
        served, monkeypatch):  # noqa: F811
    """app-server reports a retrying upstream error as an ``error``
    notification with ``willRetry``; the turn and our tool are still running.
    It must not re-arm the turn wait under the tool."""
    _waits(monkeypatch, turn=0.25)
    retrying = {"method": "error", "params": {"error": {"message": "transient upstream hiccup"},
                                              "willRetry": True}}
    response, server = await _play(served, [
        _STARTED, (0, ("call_nowait", "bash", {"command": "x"})), (0.05, retrying),
        (0.9, finished()[0][1]), *finished()[1:],
    ], tools=SlowTools({"bash": 0.7}))
    assert response.text == "done"
    assert server.tool_results[0]["success"] is True


@pytest.mark.asyncio
async def test_a_terminal_failure_event_closes_the_open_tool(served):  # noqa: F811
    """A failed ``turn/completed`` while our tool runs: the tool is not owed
    an answer any more, so it is cancelled and the failure is reported."""
    tools = SlowTools({"bash": None})
    info, server = await _stop(served, [
        _STARTED, (0, ("call_nowait", "bash", {"command": "x"})),
        (0.1, finished(reply="", status="failed", error="boom")[-1][1]), (5.0, EOF),
    ], ProviderError, tools=tools)
    assert "boom" in str(info.value)
    assert server.elapsed < 2.0
    assert server.killed is True and tools.cancelled == ["bash"]


# --- only the protocol's word ends a turn --------------------------------------


@pytest.mark.asyncio
async def test_turn_completed_in_the_stream_is_the_protocols_word(served):  # noqa: F811
    """A reply MENTIONING the event, the method name as plain text, and a
    response carrying it are none of them completion."""
    mention = {"method": "item/completed", "params": {"item": {
        "type": "agentMessage", "text": "see turn/completed"}}}
    response, server = await _play(served, [
        _STARTED, (0, mention), (0, b"turn/completed\n"),
        (0, {"id": 999, "result": {"method": "turn/completed"}}),
        (0.2, finished()[0][1]), *finished(usage=(4, 1))[1:],
    ])
    assert response.text == "done"
    assert response.input_tokens == 4
    assert server.elapsed >= 0.2, "nothing before the real turn/completed ended it"
    assert server.killed is True


@pytest.mark.asyncio
async def test_the_caller_reads_past_a_nonzero_exit_after_turn_completed(
        served):  # noqa: F811
    """The protocol's completed turn wins over whatever the process exits with."""
    response, server = await _play(served, finished(reply="kept"), exit_code=1)
    assert server.returncode == 1 or server.killed
    assert response.text == "kept"
    assert response.output_tokens == 1


# --- real child processes -----------------------------------------------------

_CHILD = r"""
import json, os, sys, time
def send(m):
    sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
def big_reply():
    item = {"type": "agentMessage", "text": "x" * 70000}
    send({"method": "item/completed", "params": {"item": item}})
    usage = {"last": {"inputTokens": 1, "outputTokens": 1}}
    send({"method": "thread/tokenUsage/updated", "params": {"tokenUsage": usage}})
    send({"method": "turn/completed", "params": {"turn": {"id": "t1", "status": "completed"}}})
for line in sys.stdin:
    m = json.loads(line); method, ident = m.get("method"), m.get("id")
    if method == "initialize":
        send({"id": ident, "result": {}})
    elif method == "thread/start":
        send({"id": ident, "result": {"thread": {"id": "thr-1", "model": "m"}}})
    elif method == "turn/start":
        send({"id": ident, "result": {"turn": {"id": "t1"}}})
        send({"method": "turn/started", "params": {}})
        MODE
"""

_BIG_REPLY = "big_reply()"

_CLOSE_AND_LINGER = "os.close(1); time.sleep(30)"


async def _real_child(mode: str):
    from tinyassets.providers.codex_provider import _STDOUT_READER_LIMIT

    return await asyncio.create_subprocess_exec(
        sys.executable, "-c", _CHILD.replace("MODE", mode),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=_STDOUT_READER_LIMIT,
    )


@pytest.mark.asyncio
async def test_a_single_event_longer_than_64k_streams_intact(served):  # noqa: F811
    """asyncio's default 64 KiB line limit raised on one 70,000-char event. A
    GET /contents result is the base64 of a whole file."""
    run, _launch, _state, config, _root = served
    child = await _real_child(_BIG_REPLY)
    response, _ = await run(server=child, cfg=config(**_PROFILE))
    assert len(response.text) == 70_000
    assert set(response.text) == {"x"}


@pytest.mark.asyncio
async def test_stdout_eof_with_a_live_child_ends_the_child_and_leaks_no_task(
        served):  # noqa: F811
    """A child that closes fd 1 and keeps running fails the turn at once, is
    ended, and leaves no pending task behind."""
    run, _launch, _state, config, _root = served
    child = await _real_child(_CLOSE_AND_LINGER)
    t0 = time.monotonic()
    with pytest.raises(ProviderError, match="exited before the turn completed"):
        await run(server=child, cfg=config(**_PROFILE))
    elapsed = time.monotonic() - t0
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(child.wait(), timeout=5)
    pending = [t for t in asyncio.all_tasks()
               if t is not asyncio.current_task() and not t.done()]
    assert child.returncode is not None, "child left running behind a returned turn"
    assert elapsed < 15, f"took {elapsed:.1f}s"
    assert pending == [], f"leaked tasks: {pending}"


# --- only the served path is the app server -------------------------------------


def test_only_the_json_path_streams_and_the_legacy_path_is_verbatim():
    """Only a served (sandboxed) turn is the app server with its watchdog; a
    plain-text call has no protocol events, so it keeps ``communicate()`` under
    the legacy total ``config.timeout`` (streaming it once killed every long
    non-served call on the 10s init budget)."""
    import inspect

    from tinyassets.providers import codex_provider

    served_src = inspect.getsource(codex_provider.CodexProvider._complete_served)
    legacy_src = inspect.getsource(codex_provider.CodexProvider.complete)
    assert "app.AppServerTurn(" in served_src and "communicate(" not in served_src
    assert "proc.communicate(input=full_input.encode" in legacy_src
    assert "timeout=config.timeout," in legacy_src


# --- a caller's cancellation must survive the cleanup -----------------------------


@pytest.mark.asyncio
async def test_a_callers_cancellation_propagates_through_the_reap(served):  # noqa: F811
    """Cancelled mid-handshake (the server has not answered ``initialize``):
    the cancellation reaches the caller, the process is ended, and the
    outstanding request is not left pending (cross-family review, 2026-10-06)."""
    run, _launch, state, config, _root = served
    server = ScriptedAppServer(finished(), launch_delay=3600)
    task = asyncio.create_task(run(server=server, cfg=config(**_PROFILE)))
    for _ in range(200):
        if server.requests("initialize"):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pending = [t for t in asyncio.all_tasks()
               if t is not asyncio.current_task() and not t.done()]
    assert server.killed is True
    assert not any("request" in repr(t.get_coro()) for t in pending), pending
