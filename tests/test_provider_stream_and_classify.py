"""Tests for the streamed served-attempt reader + failure taxonomy (Slice 1).

Change: ``stream-and-classify-provider-attempts``. These tests NEVER call the
live ``claude`` CLI — they drive :meth:`ClaudeProvider.complete` with a fake
subprocess that replays synthetic ``--output-format stream-json`` NDJSON, and
they exercise the router cooldown map, the interactive no-sleep guarantee, and
the honest-notice mapping against the classified exceptions.

Idle intervals are injected short (sub-second) via ``ModelConfig`` so the
watchdog fires in test time without real 30s waits.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.support.owned_spawn import fake_owned_spawn
from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    InteractiveDeadlineError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from tinyassets.providers import claude_provider as claude_provider_module
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    SandboxUnavailableError,
)
from tinyassets.providers.claude_provider import ClaudeProvider
from tinyassets.providers.quota import QuotaTracker
from tinyassets.providers.router import ProviderRouter

# ---------------------------------------------------------------------------
# Synthetic stream-json fixtures + a fake subprocess that replays them
# ---------------------------------------------------------------------------


def _line(obj: dict) -> bytes:
    """One NDJSON stdout line (the documented stream-json shape)."""
    return (json.dumps(obj) + "\n").encode("utf-8")


INIT = {"type": "system", "subtype": "init", "session_id": "s-test"}


def _assistant_text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _partial_text(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        },
    }


def _thinking_delta(text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": text},
        },
    }


def _tool_use(name: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]},
    }


def _tool_result() -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "ok"}]},
    }


def _api_retry(error: str, error_status: int, retry_delay_ms: int) -> dict:
    """A REAL Claude 2.1.236 ``system/api_retry`` event.

    The CLI's documented fields are ``error`` (string, e.g. "rate_limit" /
    "overloaded"), ``error_status`` (int HTTP status, 429 / 529), and
    ``retry_delay_ms`` (int). These are the ground-truth field names — NOT
    payloads shaped to the implementation (the change-#1 lesson).
    """
    return {
        "type": "system",
        "subtype": "api_retry",
        "error": error,
        "error_status": error_status,
        "retry_delay_ms": retry_delay_ms,
        "attempt": 1,
    }


def _rate_limit_event(status: str, *, resets_at: float | None = None) -> dict:
    """A REAL Claude 2.1.236 top-level ``rate_limit_event``.

    ``rate_limit_info.status`` == "rejected" is an active limit, with
    ``resetsAt`` a unix-seconds reset time; "allowed" and "allowed_warning" are
    informational (the reference trace shows "allowed" on a SUCCESSFUL turn).
    """
    info: dict = {
        "status": status,
        "rateLimitType": "five_hour",
        "overageStatus": "rejected",
    }
    if resets_at is not None:
        info["resetsAt"] = resets_at
    return {"type": "rate_limit_event", "rate_limit_info": info}


def _result(text: str, *, subtype: str = "success", **extra) -> dict:
    obj = {"type": "result", "subtype": subtype, "result": text}
    obj.update(extra)
    return obj


class FakeStreamProcess:
    """A stand-in for an ``asyncio`` subprocess replaying NDJSON stdout.

    ``stdout_items`` is a list of either raw ``bytes`` (returned immediately)
    or ``(delay_s, bytes)`` tuples (the reader sleeps ``delay_s`` before the
    line arrives — a large delay simulates a hung/idle stream that the watchdog
    catches via ``asyncio.wait_for``).

    Process lifecycle follows ``asyncio.subprocess.Process`` rather than a
    convenient shortcut, because teardown *reads* it. The precise rule is not
    "only ``wait()`` publishes the exit status": on the real class the child
    watcher reaps **asynchronously**, so ``returncode`` can flip from ``None``
    to a status at any await point once the child exits, with no ``wait()``
    call of ours in between. What holds is the direction -- ``None`` means not
    yet reaped, and once it is set it never goes back. This fake publishes the
    status on ``wait()`` because that is the point this file's flows reach it;
    it is a deliberately conservative stand-in, not a claim about when the real
    runtime reaps.

    What matters for these assertions is the other half: a fake that exposes an
    exit status **while still streaming** claims the adapter has already reaped
    the child, which silently voids every ``proc.killed`` assertion in this
    file -- teardown correctly declines to signal a pid it no longer owns.
    """

    def __init__(self, stdout_items, *, stderr: bytes = b"", returncode: int = 0):
        self._items = list(stdout_items)
        self._idx = 0
        #: Status the child reports when reaped; published by ``wait()`` only.
        self._exit_status = returncode
        #: Live, unreaped handle — exactly what asyncio reports before ``wait()``.
        self.returncode = None
        self.killed = False
        self.stdout = self._Stdout(self)
        self.stderr = self._Stderr(stderr)
        self.stdin = self._Stdin()

    class _Stdout:
        def __init__(self, parent):
            self._p = parent

        async def readline(self):
            p = self._p
            if p._idx >= len(p._items):
                return b""
            item = p._items[p._idx]
            p._idx += 1
            if isinstance(item, tuple):
                delay, data = item
                if delay:
                    await asyncio.sleep(delay)
                return data
            return item

    class _Stderr:
        def __init__(self, data: bytes):
            self._data = data
            self._sent = False

        async def read(self, _n: int) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return self._data

    class _Stdin:
        def write(self, _b): ...
        async def drain(self): ...
        def close(self): ...

    def kill(self):
        # ``asyncio.subprocess.Process.kill`` raises ProcessLookupError once the
        # process has been reaped (``_check_proc``). Reproducing that keeps the
        # fake honest about the window in which a kill is meaningful.
        if self.returncode is not None:
            raise ProcessLookupError("process already reaped")
        self.killed = True

    async def wait(self):
        # Reaping publishes the exit status. A kill signal does not rewrite it:
        # these fakes model a child that has already produced its status (EOF
        # reached, zombie awaiting reap), which is what the streaming teardown
        # path actually meets.
        if self.returncode is None:
            self.returncode = self._exit_status
        return self.returncode


def _run_stream(proc: FakeStreamProcess, config: ModelConfig) -> ProviderResponse:
    """Drive ClaudeProvider._read_stream against a fake process."""
    provider = ClaudeProvider()
    return asyncio.run(provider._read_stream(proc, "prompt", config))


# Short, injected watchdog profile so idle fires in test time.
_FAST = ModelConfig(
    init_timeout_s=0.15,
    first_progress_s=0.15,
    idle_timeout_s=0.15,
    absolute_cap_s=5.0,
)


def _root_answer(text="hello", model="actual-model", message_id="answer-1", **extra):
    # CLI 2.1.261 live capture, 2026-09-17: assistant.message.{id,model,content}
    # with explicit parent_tool_use_id:null; result carries the final text.
    return {
        "type": "assistant", "parent_tool_use_id": None,
        "message": {"id": message_id, "model": model,
                    "content": [{"type": "text", "text": text}]},
        **extra,
    }


def _answer_response(*frames, text="hello"):
    return _run_stream(
        FakeStreamProcess([_line(INIT), *map(_line, frames), _line(_result(text))]),
        ModelConfig(native_model_id="requested-alias"),
    )


def test_root_answer_reports_observed_model_through_receipt_and_native_record():
    from tinyassets.providers.execution_receipt import WriterExecutionReceipt
    from tinyassets.storage.agent_native_records import NativeInput, NativeTerminal

    response = _answer_response(_root_answer(model=" actual-model "))
    assert response.model == "requested-alias"
    assert response.reported_model == "actual-model"
    receipt = WriterExecutionReceipt()
    receipt.observe(response)
    assert receipt.projection() == {
        "provider": "claude-code", "model": "actual-model", "model_status": "reported",
    }
    candidate = NativeInput(
        source_ref="claude-code", model="requested-alias", binding_id="binding",
        reservation_id="reservation", binding_generation=1,
        binding_digest="sha256:" + "a" * 64, request_digest="sha256:" + "b" * 64,
    )
    terminal = NativeTerminal(
        "completed", evidence=response.native_evidence, text=response.text,
        configured_model=response.model, reported_model=response.reported_model,
    )
    assert json.loads(terminal.canonical_json(candidate))["reported_model"] == "actual-model"


def test_children_and_partial_frames_never_replace_root_answer_evidence():
    response = _answer_response(
        _root_answer(),
        _root_answer(model="child-model", parent_tool_use_id="tool-child"),
        _partial_text("hello"),
    )
    assert response.reported_model == "actual-model"


def test_split_root_content_blocks_share_the_answer_model():
    response = _answer_response(
        _root_answer("hel"),
        _root_answer("ignored", model="child-model", parent_tool_use_id="child"),
        _root_answer("lo"),
    )
    assert response.reported_model == "actual-model"


@pytest.mark.parametrize("bad", [None, True, 123, "", " ", "bad\nlabel", "a" * 201,
                                      "<synthetic>", " <synthetic> "])
def test_bad_last_root_model_clears_earlier_evidence(bad):
    response = _answer_response(_root_answer(), _root_answer(model=bad, message_id="answer-2"))
    assert response.text == "hello"
    assert response.reported_model == ""


@pytest.mark.parametrize("change", [
    "missing_model", "missing_parent", "bad_parent", "empty_content", "error",
    "wrong_text", "wrong_model_same_id",
])
def test_incomplete_or_ambiguous_final_evidence_is_unknown(change):
    frame = _root_answer(message_id="answer-2")
    if change == "missing_model":
        del frame["message"]["model"]
    elif change == "missing_parent":
        del frame["parent_tool_use_id"]
    elif change == "bad_parent":
        frame["parent_tool_use_id"] = False
    elif change == "empty_content":
        frame["message"]["content"] = []
    elif change == "error":
        frame["error"] = "unknown"
    elif change == "wrong_text":
        frame["message"]["content"][0]["text"] = "different"
    else:
        frame["message"].update(id="answer-1", model="different-model")
    assert _answer_response(_root_answer(), frame).reported_model == ""


def test_init_and_aggregate_model_are_not_answer_evidence():
    response = _run_stream(FakeStreamProcess([
        _line({**INIT, "model": "init-model"}),
        _line(_assistant_text("hello")),
        _line(_result("hello", modelUsage={"aggregate-model": {}})),
    ]), _FAST)
    assert response.reported_model == ""


def test_empty_assistant_metadata_does_not_extend_idle_watchdog():
    frame = _root_answer()
    frame["message"]["content"] = []
    proc = FakeStreamProcess([
        _line(INIT), _line(_root_answer()),
        (0.10, _line(frame)), (0.10, _line(_result("hello"))),
    ])
    with pytest.raises(ProviderIdleTimeoutError):
        _run_stream(proc, _FAST)


def test_interleaved_streams_have_request_local_model_evidence():
    async def run():
        async def answer(model, delay):
            proc = FakeStreamProcess([
                _line(INIT), _line(_root_answer(model=model)),
                (delay, _line(_result("hello"))),
            ])
            return await ClaudeProvider()._read_stream(proc, "prompt", _FAST)
        return await asyncio.gather(answer("model-a", 0.04), answer("model-b", 0.01))

    assert [r.reported_model for r in asyncio.run(run())] == ["model-a", "model-b"]


@pytest.mark.parametrize("category", [
    "authentication_failed", "oauth_org_not_allowed", "billing_error", "rate_limit",
    "overloaded", "invalid_request", "model_not_found", "server_error",
    "max_output_tokens", "unknown",
])
def test_unsuccessful_terminal_keeps_only_typed_error_evidence(category):
    event = _assistant_text("private upstream request content")
    event["error"] = category
    proc = FakeStreamProcess([
        _line(INIT), _line(event),
        _line(_result("private result content", is_error=True, errors=["private error content"])),
    ])
    with pytest.raises(ProviderError) as raised:
        _run_stream(proc, _FAST)
    exc = raised.value
    if category == "authentication_failed":
        from tinyassets.exceptions import ProviderAuthenticationError

        assert type(exc) is ProviderAuthenticationError
        assert exc.failure_class == "auth_invalid"
    else:
        assert type(exc) is ProviderError
        assert exc.failure_class is None
        assert category in str(exc)
        assert "is_error=true" in str(exc)
    assert "private" not in str(exc)
    assert exc.attempt_telemetry["last_assistant_error"] == category
    assert exc.attempt_telemetry["terminal_is_error"] == "true"
    assert exc.native_evidence.protocol_complete is False
    from tinyassets.providers.agent_capacity_boundary import capacity_boundary
    from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
    from tinyassets.providers.model_policy import ModelRef

    assert capacity_boundary(
        ModelRef("claude-code", ""), [ProviderAttemptDiagnostic(
            "claude-code", "failed", "provider_error", failure_class=exc.failure_class,
            side_effect_state=exc.attempt_telemetry["side_effect_state"],
        )], execution_kind="native_agent", native_evidence=(exc.native_evidence,),
    ) is None


@pytest.mark.parametrize("value", [
    "private token=not-for-logs", {"secret": "private"}, ["private"],
])
def test_unknown_native_error_values_never_enter_failure_diagnostics(value):
    event = _assistant_text("private text")
    event["error"] = value
    proc = FakeStreamProcess([
        _line(event), _line(_result("private result", subtype="private subtype", is_error=value)),
    ])
    with pytest.raises(ProviderError) as raised:
        _run_stream(proc, _FAST)
    assert "private" not in str(raised.value)
    assert "unrecognized" in str(raised.value)
    assert raised.value.attempt_telemetry["terminal_is_error"] == "non_boolean"


def test_assistant_error_does_not_override_a_later_successful_terminal():
    event = _assistant_text("temporary error")
    event["error"] = "server_error"
    proc = FakeStreamProcess([_line(event), _line(_result("recovered", is_error=False))])
    assert _run_stream(proc, _FAST).text == "recovered"


def test_native_error_text_is_not_a_successful_reply():
    event = _assistant_text("Invalid authentication credentials")
    event["error"] = "authentication_failed"
    proc = FakeStreamProcess([_line(event), _line(_result("", is_error=False))])
    with pytest.raises(ProviderError, match="no assistant text"):
        _run_stream(proc, _FAST)


@pytest.mark.parametrize("returncode", [0, 1])
def test_native_auth_error_text_then_terminal_keeps_clue(returncode):
    event = _assistant_text("Invalid authentication credentials")
    event["error"] = "authentication_failed"
    proc = FakeStreamProcess([
        _line(INIT), _line(event), _line(_result("", is_error=True)),
    ], returncode=returncode)
    with pytest.raises(ProviderError) as raised:
        _run_stream(proc, _FAST)
    from tinyassets.exceptions import ProviderAuthenticationError

    assert type(raised.value) is ProviderAuthenticationError
    assert raised.value.attempt_telemetry["phase"] == "init"
    assert raised.value.attempt_telemetry["last_assistant_error"] == "authentication_failed"
    assert raised.value.native_evidence.protocol_complete is False


@pytest.mark.parametrize("progress", [
    _assistant_text("recovered progress"), _tool_use("example"), _tool_result(),
])
def test_native_auth_clue_is_superseded_by_later_useful_progress(progress):
    event = _assistant_text("Sign-in problem")
    event["error"] = "authentication_failed"
    proc = FakeStreamProcess([
        _line(event), _line(progress), _line(_result("", is_error=True)),
    ])
    with pytest.raises(ProviderError) as raised:
        _run_stream(proc, _FAST)
    assert raised.value.attempt_telemetry["last_assistant_error"] is None
    assert "last_assistant_error=not_reported" in str(raised.value)


def test_native_auth_clue_after_prior_tool_progress_does_not_prove_safe_retry():
    event = _assistant_text("Sign-in problem")
    event["error"] = "authentication_failed"
    proc = FakeStreamProcess([
        _line(_tool_use("example")), _line(_tool_result()), _line(event),
        _line({"type": "system", "subtype": "status"}),
        _line({"type": "future_opaque_event"}), _line(_result("", is_error=True)),
    ])
    with pytest.raises(ProviderError) as raised:
        _run_stream(proc, _FAST)
    assert raised.value.attempt_telemetry["last_assistant_error"] == "authentication_failed"
    assert raised.value.attempt_telemetry["side_effect_state"] == "committed"
    assert raised.value.native_evidence.protocol_complete is False


# ---------------------------------------------------------------------------
# 5.1 Behavior parity: recorded stream assembles the SAME final text
# ---------------------------------------------------------------------------


class TestStreamAssembly:
    def test_terminal_result_is_canonical_final_text(self):
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_assistant_text("Hello")),
            _line(_assistant_text(" world")),
            _line(_result("Hello world", usage={"input_tokens": 12, "output_tokens": 5},
                          total_cost_usd=0.0003)),
        ])
        resp = _run_stream(proc, _FAST)
        assert isinstance(resp, ProviderResponse)
        assert resp.text == "Hello world"
        assert resp.provider == "claude-code"
        assert resp.family == "anthropic"
        assert resp.input_tokens == 12
        assert resp.output_tokens == 5
        assert resp.cost_microunits == 300
        assert resp.ttft_ms is not None

    def test_assembles_from_assistant_deltas_when_result_text_empty(self):
        # Terminal result present (success) but with no result string — the
        # assembled assistant text blocks are the fallback source of truth.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_assistant_text("Hello")),
            _line(_assistant_text(" world")),
            _line(_result("")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "Hello world"

    def test_partial_deltas_are_fallback_and_thinking_is_never_relayed(self):
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_thinking_delta("secret reasoning I must never leak")),
            _line(_partial_text("Streamed ")),
            _line(_partial_text("answer")),
            _line(_result("")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "Streamed answer"
        assert "secret reasoning" not in resp.text

    def test_tool_events_set_side_effect_state(self):
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_tool_use("write_graph")),
            _line(_tool_result()),
            _line(_assistant_text("done")),
            _line(_result("done")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "done"
        assert resp.tool_phase == "tool_result"
        assert resp.side_effect_state == "committed"

    def test_success_result_with_no_text_anywhere_fails_loud(self):
        proc = FakeStreamProcess([_line(INIT), _line(_result(""))])
        with pytest.raises(ProviderError, match="no assistant text"):
            _run_stream(proc, _FAST)


# ---------------------------------------------------------------------------
# 5.2 Idle watchdog: progress keeps it alive; only real idle fails it
# Pending native tool work is not model-idle silence. Keep this regression on
# the real reader rather than simulating a successful ProviderResponse.


def test_identified_pending_tool_survives_model_idle_interval():
    start = _tool_use("example")
    start["message"]["content"][0]["id"] = "call-a"
    done = _tool_result()
    done["message"]["content"][0]["tool_use_id"] = "call-a"
    proc = FakeStreamProcess([
        _line(INIT), _line(start), (0.35, _line(done)), _line(_result("done")),
    ])
    response = _run_stream(proc, _FAST)
    assert response.text == "done"
    assert response.side_effect_state == "committed"


def test_one_completed_tool_does_not_hide_another_pending_tool():
    first, second = _tool_use("first"), _tool_use("second")
    first["message"]["content"][0]["id"] = "call-a"
    second["message"]["content"][0]["id"] = "call-b"
    done_a, done_b = _tool_result(), _tool_result()
    done_a["message"]["content"][0]["tool_use_id"] = "call-a"
    done_b["message"]["content"][0]["tool_use_id"] = "call-b"
    proc = FakeStreamProcess([
        _line(INIT), _line(first), _line(second), _line(done_a),
        _line(_assistant_text("still waiting")), (0.35, _line(done_b)),
        _line(_result("done")),
    ])
    assert _run_stream(proc, _FAST).text == "done"


def test_completed_identified_tool_restores_model_idle_interval():
    start = _tool_use("example")
    start["message"]["content"][0]["id"] = "call-a"
    done = _tool_result()
    done["message"]["content"][0]["tool_use_id"] = "call-a"
    proc = FakeStreamProcess([
        _line(INIT), _line(start), _line(done), (0.35, _line(_result("late"))),
    ])
    with pytest.raises(ProviderIdleTimeoutError):
        _run_stream(proc, _FAST)


def _started(name: str, tool_id, **extra) -> dict:
    """A full assistant ``tool_use`` frame carrying the provider's own id."""
    frame = _tool_use(name)
    frame["message"]["content"][0]["id"] = tool_id
    frame.update(extra)
    return frame


def _partial_start(name: str, tool_id) -> dict:
    """The PARTIAL framing of the same start (``content_block_start``)."""
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": name, "id": tool_id},
        },
    }


def _finished(tool_id) -> dict:
    frame = _tool_result()
    frame["message"]["content"][0]["tool_use_id"] = tool_id
    return frame


def test_nested_child_tool_completion_does_not_close_the_parent_tool():
    # A parent tool's child completing is not the parent's result: the CLI
    # frames a sub-agent's tools with ``parent_tool_use_id`` set, and the
    # parent stays in flight until its OWN result arrives.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("parent", "call-parent")),
        _line(_started("child", "call-child", parent_tool_use_id="call-parent")),
        _line(_finished("call-child")),
        _line(_assistant_text("child done")),
        (0.35, _line(_finished("call-parent"))),
        _line(_result("done")),
    ])
    response = _run_stream(proc, _FAST)
    assert response.text == "done"


def test_duplicate_full_and_partial_starts_are_one_pending_tool():
    # The same start arrives twice (full assistant frame + partial
    # content_block_start). ONE matching result must therefore restore
    # ordinary model-idle behaviour — a duplicate frame is not a second tool.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        _line(_partial_start("example", "call-a")),
        _line(_finished("call-a")),
        (0.35, _line(_result("late"))),
    ])
    with pytest.raises(ProviderIdleTimeoutError):
        _run_stream(proc, _FAST)


def test_a_partial_start_alone_earns_the_pending_tool_allowance():
    # A start seen ONLY in its partial framing is still an identified tool.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_partial_start("example", "call-a")),
        (0.35, _line(_finished("call-a"))),
        _line(_result("done")),
    ])
    assert _run_stream(proc, _FAST).text == "done"


def test_duplicate_results_for_one_tool_leave_another_pending():
    # Repeating a result is idempotent and must not discharge a DIFFERENT
    # tool's pending work.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("first", "call-a")),
        _line(_started("second", "call-b")),
        _line(_finished("call-a")),
        _line(_finished("call-a")),
        (0.35, _line(_finished("call-b"))),
        _line(_result("done")),
    ])
    assert _run_stream(proc, _FAST).text == "done"


def test_an_unknown_result_id_does_not_discharge_a_pending_tool():
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        _line(_finished("call-unrelated")),
        (0.35, _line(_finished("call-a"))),
        _line(_result("done")),
    ])
    assert _run_stream(proc, _FAST).text == "done"


@pytest.mark.parametrize("tool_id", [None, "", "   ", 123, True, {"id": "call-a"}])
def test_missing_or_malformed_tool_ids_earn_no_allowance(tool_id):
    # Fail closed: an unidentifiable start can never be paired with a result,
    # so it keeps the ordinary idle boundary rather than a 900s wait.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", tool_id)),
        (0.35, _line(_result("never"))),
    ])
    with pytest.raises(ProviderIdleTimeoutError):
        _run_stream(proc, _FAST)


def test_a_malformed_result_id_cannot_discharge_an_identified_tool():
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        _line(_finished(None)),
        (0.35, _line(_finished("call-a"))),
        _line(_result("done")),
    ])
    assert _run_stream(proc, _FAST).text == "done"


def test_the_absolute_cap_still_ends_a_wedged_pending_tool():
    # The tool allowance is min(absolute cap, 900s): the cap is never relaxed,
    # and reaching it is an interactive-deadline outcome, not idle.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        (30.0, _line(_result("never"))),
    ])
    config = ModelConfig(
        init_timeout_s=0.15, first_progress_s=0.15, idle_timeout_s=0.15,
        absolute_cap_s=0.5,
    )
    with pytest.raises(InteractiveDeadlineError):
        _run_stream(proc, config)
    assert proc.killed is True


def test_a_smaller_injected_tool_allowance_bounds_the_wait(monkeypatch):
    # Drive the bound from the constant itself (absolute cap 5s, tool wait
    # 0.3s): the wait that fires is the TOOL allowance, not the cap, and it is
    # reported as idle.
    monkeypatch.setattr(claude_provider_module, "_TOOL_WAIT_S", 0.3)
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        (2.0, _line(_result("never"))),
    ])
    with pytest.raises(ProviderIdleTimeoutError) as ei:
        _run_stream(proc, _FAST)
    assert ei.value.attempt_telemetry["tool_phase"] == "in_tool"
    assert proc.killed is True


def test_a_pending_tool_timeout_reports_in_tool_not_the_last_tool_event():
    # Two tools, the first finished: the LAST tool event is a result while the
    # turn is still in a tool. Keying on it would read as post-tool silence.
    monkeypatch_free = FakeStreamProcess([
        _line(INIT),
        _line(_started("first", "call-a")),
        _line(_started("second", "call-b")),
        _line(_finished("call-a")),
        (30.0, _line(_result("never"))),
    ])
    config = ModelConfig(
        init_timeout_s=0.15, first_progress_s=0.15, idle_timeout_s=0.15,
        absolute_cap_s=0.5,
    )
    with pytest.raises(InteractiveDeadlineError) as ei:
        _run_stream(monkeypatch_free, config)
    tele = ei.value.attempt_telemetry
    assert tele["tool_phase"] == "in_tool"
    assert tele["side_effect_state"] == "committed"


def test_post_tool_silence_still_reports_the_last_tool_event():
    # The control: all tools matched, so the phase is the last event kind and
    # the failure is ordinary model idle.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        _line(_finished("call-a")),
        (0.35, _line(_result("never"))),
    ])
    with pytest.raises(ProviderIdleTimeoutError) as ei:
        _run_stream(proc, _FAST)
    assert ei.value.attempt_telemetry["tool_phase"] == "tool_result"


def test_a_terminal_result_clears_pending_tools():
    # A tool left open at the terminal result must not be reported in flight.
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        _line(_assistant_text("answer")),
        _line(_result("answer")),
    ])
    response = _run_stream(proc, _FAST)
    assert response.text == "answer"
    assert response.tool_phase == "tool_use"


def test_cancellation_during_a_pending_tool_terminates_and_reaps():
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        (30.0, _line(_result("never"))),
    ])

    async def _drive() -> None:
        provider = ClaudeProvider()
        task = asyncio.create_task(provider._read_stream(proc, "prompt", _FAST))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())
    # Cancellation kills the child; nothing is replayed automatically.
    assert proc.killed is True


def test_a_documented_retry_grace_survives_a_pending_tool(monkeypatch):
    # A provider-stated retry wait longer than the tool allowance is preserved:
    # the pending-tool branch takes the MAX, it does not cap the retry grace.
    monkeypatch.setattr(claude_provider_module, "_TOOL_WAIT_S", 0.2)
    config = ModelConfig(
        init_timeout_s=0.15, first_progress_s=0.15, idle_timeout_s=0.15,
        absolute_cap_s=5.0,
    )
    proc = FakeStreamProcess([
        _line(INIT),
        _line(_started("example", "call-a")),
        _line(_api_retry("overloaded", 529, 400)),
        (0.9, _line(_finished("call-a"))),
        _line(_result("done")),
    ])
    assert _run_stream(proc, config).text == "done"

# ---------------------------------------------------------------------------


class TestIdleWatchdog:
    def test_long_but_progressing_stream_is_not_failed_for_elapsed_time(self):
        # Each gap (0.08s) is under the idle interval (0.15s); total elapsed
        # (~0.5s) far exceeds any single interval — a naive total deadline of
        # 0.15s would have failed it. Progress keeps resetting the watchdog.
        proc = FakeStreamProcess([
            _line(INIT),
            (0.08, _line(_assistant_text("a"))),
            (0.08, _line(_assistant_text("b"))),
            (0.08, _line(_assistant_text("c"))),
            (0.08, _line(_assistant_text("d"))),
            (0.08, _line(_result("abcd"))),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "abcd"

    def test_stream_that_goes_idle_yields_provider_idle_timeout(self):
        # init + one delta, then a 10s gap — the 0.15s idle watchdog fires first.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_assistant_text("partial...")),
            (10.0, _line(_result("never arrives"))),
        ])
        with pytest.raises(ProviderIdleTimeoutError):
            _run_stream(proc, _FAST)
        assert proc.killed is True

    def test_no_init_within_init_timeout_is_idle_timeout(self):
        # The very first line stalls past init_timeout — a CLI/MCP startup hang.
        proc = FakeStreamProcess([(10.0, _line(INIT))])
        with pytest.raises(ProviderIdleTimeoutError):
            _run_stream(proc, _FAST)
        assert proc.killed is True

    def test_absolute_cap_yields_interactive_deadline_even_while_progressing(self):
        # Every gap (0.08s) resets the idle watchdog, but the absolute cap
        # (0.3s) is a hard backstop: a still-progressing over-long turn ends as
        # interactive_deadline, NOT idle_timeout.
        config = ModelConfig(
            init_timeout_s=0.2, first_progress_s=0.2, idle_timeout_s=0.2,
            absolute_cap_s=0.3,
        )
        items = [_line(INIT)] + [(0.08, _line(_assistant_text("x"))) for _ in range(20)]
        proc = FakeStreamProcess(items)
        with pytest.raises(InteractiveDeadlineError):
            _run_stream(proc, config)
        assert proc.killed is True


# ---------------------------------------------------------------------------
# 5.3 Failure taxonomy from api_retry / exit / malformed
# ---------------------------------------------------------------------------


class TestFailureTaxonomy:
    def test_real_429_api_retry_then_no_recovery_is_rate_limited(self):
        # REAL schema: error="rate_limit", error_status=429, retry_delay_ms=30000.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("rate_limit", 429, 30000)),
        ], returncode=0)
        with pytest.raises(ProviderRateLimitedError) as ei:
            _run_stream(proc, _FAST)
        assert ei.value.retry_after == 30.0  # 30000ms -> 30s
        assert ei.value.failure_class == "provider_rate_limited"
        assert proc.killed is True

    def test_real_529_api_retry_is_overloaded_with_retry_after(self):
        # REAL schema: error="overloaded", error_status=529, retry_delay_ms=5000.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("overloaded", 529, 5000)),
        ], returncode=0)
        with pytest.raises(ProviderOverloadedError) as ei:
            _run_stream(proc, _FAST)
        assert ei.value.retry_after == 5.0
        assert ei.value.failure_class == "provider_overloaded"

    def test_real_429_then_quick_exit_1_is_rate_limited_not_unavailable(self):
        # Blocker A (Codex re-review #2): a REAL 429 api_retry followed by a quick
        # exit code 1 must be classified from the TYPED signal (rate-limited, with
        # retry_after), NOT masked as generic "unavailable" by the exit-1 heuristic.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("rate_limit", 429, 45000)),
        ], returncode=1)  # quick exit 1 AFTER the typed retry
        with pytest.raises(ProviderRateLimitedError) as ei:
            _run_stream(proc, _FAST)
        assert ei.value.failure_class == "provider_rate_limited"
        assert ei.value.retry_after == 45.0

    def test_real_529_then_quick_exit_1_is_overloaded_not_unavailable(self):
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("overloaded", 529, 7000)),
        ], returncode=1)
        with pytest.raises(ProviderOverloadedError) as ei:
            _run_stream(proc, _FAST)
        assert ei.value.failure_class == "provider_overloaded"
        assert ei.value.retry_after == 7.0

    def test_api_retry_classifies_by_error_status_when_string_is_unknown(self):
        # Even if the error STRING is not a recognized word, error_status=429
        # classifies it rate-limited (schema field, not substring luck).
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("some_new_wording", 429, 12000)),
        ], returncode=0)
        with pytest.raises(ProviderRateLimitedError) as ei:
            _run_stream(proc, _FAST)
        assert ei.value.retry_after == 12.0

    def test_api_retry_that_recovers_returns_success(self):
        # A rate-limit retry the CLI rides out and then completes must NOT fail.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("rate_limit", 429, 2000)),
            _line(_assistant_text("recovered")),
            _line(_result("recovered")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "recovered"

    def test_rate_limit_event_allowed_is_informational_not_a_failure(self):
        # status=="allowed" appears on a SUCCESSFUL turn (per the real trace) —
        # it must be a liveness heartbeat, never a rate-limit failure.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_rate_limit_event("allowed", resets_at=9_999_999_999)),
            _line(_assistant_text("fine")),
            _line(_rate_limit_event("allowed", resets_at=9_999_999_999)),
            _line(_result("fine")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "fine"

    def test_rate_limit_event_non_allowed_is_rate_limited(self):
        import time as _time
        # A "rejected" status is an active limit; retry_after derives from
        # resetsAt (unix seconds).
        resets = _time.time() + 40
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_rate_limit_event("rejected", resets_at=resets)),
        ], returncode=0)
        with pytest.raises(ProviderRateLimitedError) as ei:
            _run_stream(proc, _FAST)
        assert ei.value.failure_class == "provider_rate_limited"
        assert 30 <= ei.value.retry_after <= 45  # ~40s from resetsAt

    def test_rate_limit_event_allowed_warning_is_informational_not_a_failure(self):
        import time as _time
        # 2026-10-05 outage: "allowed_warning" with the weekly window's resetsAt
        # (days away) was read as a limit and cooled the source for a day while
        # the account was still allowed. It is a heartbeat, never a failure.
        weekly = _time.time() + 410_349
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_rate_limit_event("allowed_warning", resets_at=weekly)),
            _line(_assistant_text("still fine")),
            _line(_result("still fine")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "still fine"
        # And when the turn then ends WITHOUT a result for some other reason, the
        # warning must not be blamed: no rate-limit class, no day-long cooldown.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_rate_limit_event("allowed_warning", resets_at=weekly)),
        ], returncode=1)
        with pytest.raises(Exception) as ei:
            _run_stream(proc, _FAST)
        assert not isinstance(ei.value, ProviderRateLimitedError)
        assert getattr(ei.value, "retry_after", None) is None

    def test_known_retry_delay_longer_than_idle_is_not_killed_as_idle(self):
        # Blocker B: a documented retry states a 0.8s wait — longer than the
        # 0.15s idle interval. The idle watchdog must extend to cover it so the
        # turn survives the wait and completes when the CLI recovers, rather than
        # being relabeled provider_idle_timeout at 0.15s.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("rate_limit", 429, 800)),  # 0.8s stated wait
            (0.5, _line(_assistant_text("recovered after the wait"))),
            _line(_result("recovered after the wait")),
        ])
        resp = _run_stream(proc, _FAST)  # idle_timeout_s=0.15 << 0.5s gap
        assert resp.text == "recovered after the wait"

    def test_malformed_line_is_protocol_error(self):
        proc = FakeStreamProcess([_line(INIT), b"{not valid json at all\n"])
        with pytest.raises(ProviderProtocolError):
            _run_stream(proc, _FAST)
        assert proc.killed is True

    def test_non_object_json_line_is_protocol_error(self):
        proc = FakeStreamProcess([_line(INIT), b"\"a bare string\"\n"])
        with pytest.raises(ProviderProtocolError):
            _run_stream(proc, _FAST)

    def test_exit_1_quick_without_result_is_unavailable(self):
        proc = FakeStreamProcess([], returncode=1)
        with pytest.raises(ProviderUnavailableError):
            _run_stream(proc, _FAST)

    def test_eof_without_terminal_result_is_classified_protocol_error(self):
        # Blocker J: EOF with a clean exit but NO terminal result is a TRUNCATED
        # stream — classify it as provider_protocol_error, not a bare
        # unclassified ProviderError (which the earlier test wrongly blessed).
        proc = FakeStreamProcess([_line(INIT), _line(_assistant_text("hi"))],
                                 returncode=0)
        with pytest.raises(ProviderProtocolError, match="truncated") as ei:
            _run_stream(proc, _FAST)
        assert ei.value.failure_class == "provider_protocol_error"
        assert proc.killed is True

    def test_bwrap_failure_on_stderr_raises_sandbox_unavailable(self):
        # A bwrap failure remains a terminal outcome even on the streaming path.
        import sys
        if sys.platform == "win32":
            pytest.skip("bwrap is Linux-only")
        proc = FakeStreamProcess(
            [],
            stderr=b"bwrap: No permissions to create a new namespace",
            returncode=1,
        )
        with pytest.raises(SandboxUnavailableError):
            _run_stream(proc, _FAST)

    def test_whitespace_lines_do_not_reset_the_idle_watchdog(self):
        # Blocker C reconciliation: recognized framing (message_start) is now a
        # LIVENESS heartbeat, so the non-liveness input that must NOT reset the
        # watchdog is whitespace-only lines (no events, not malformed). init then
        # a blank line then a long gap still fails idle.
        proc = FakeStreamProcess([
            _line(INIT),
            b"   \n",  # whitespace-only: no events, not malformed, not liveness
            (10.0, _line(_result("late"))),
        ])
        with pytest.raises(ProviderIdleTimeoutError):
            _run_stream(proc, _FAST)
        assert proc.killed is True

    def test_unknown_wellformed_type_does_not_reset_the_idle_watchdog(self):
        # An unknown-but-well-formed JSON object is tolerated (not a protocol
        # error) but is NOT counted as liveness — a hung process emits nothing,
        # so an unrecognized type must not keep a stalled turn alive forever.
        proc = FakeStreamProcess([
            _line(INIT),
            _line({"type": "some_future_event", "detail": "x"}),
            (10.0, _line(_result("late"))),
        ])
        with pytest.raises(ProviderIdleTimeoutError):
            _run_stream(proc, _FAST)


# ---------------------------------------------------------------------------
# 5.4 Router cooldown map by failure_class
# ---------------------------------------------------------------------------


class _RaisingProvider(BaseProvider):
    """A claude-code fake whose complete() raises a chosen exception once,
    then (optionally) succeeds — to prove next-turn eligibility."""

    name = "claude-code"
    family = "anthropic"

    def __init__(self, exc, *, then_ok: bool = False):
        self._exc = exc
        self._then_ok = then_ok
        self.calls = 0

    async def complete(self, prompt, system, config, *, universe_dir=None):
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        if self._then_ok:
            return ProviderResponse(
                text="ok now", provider=self.name, model="claude",
                family=self.family, latency_ms=1.0,
            )
        raise self._exc


def _solo_router(provider):
    quota = QuotaTracker()
    return ProviderRouter(providers={provider.name: provider}, quota=quota), quota


def _bound(provider: str = "claude-code", role: str = "writer") -> dict:
    """Hard Rule 15: a routed call carries one universe owner's authority."""
    from pathlib import Path
    from unittest.mock import MagicMock

    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers.base import UniverseContext

    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner")
    carrier.provider = provider
    carrier.role = role
    carrier.operation = "run_graph"
    carrier.max_tokens = 1000
    carrier.max_cost_microunits = 1000
    carrier.selected_model = None
    carrier.native_selection = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = provider
    return {
        "operation": "run_graph",
        "universe_context": UniverseContext(
            universe_dir=Path("u-stream"), provider_invocation=carrier,
        ),
    }


@pytest.fixture(autouse=True)
def _carrier_resolution(monkeypatch):
    """Resolve a test carrier the way the store-minted one resolves."""
    import tinyassets.providers.router as router_mod

    def resolve(universe_context, *, role, operation):
        carrier = getattr(universe_context, "provider_invocation", None)
        if carrier is not None:
            carrier.validate_for_call(role=role, operation=operation)
        return carrier

    monkeypatch.setattr(router_mod, "_provider_invocation_carrier", resolve)


class TestRouterCooldownMap:
    @pytest.mark.asyncio
    async def test_idle_timeout_does_not_cool_the_sole_writer(self):
        provider = _RaisingProvider(ProviderIdleTimeoutError("idle"))
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await router.call("writer", "prompt", "system", **_bound())

        assert quota.available("claude-code", owner="owner") is True  # NOT cooled
        assert ei.value.failure_class == "provider_idle_timeout"

    @pytest.mark.asyncio
    async def test_interactive_deadline_does_not_cool_the_sole_writer(self):
        provider = _RaisingProvider(InteractiveDeadlineError("cap"))
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await router.call("writer", "prompt", "system", **_bound())

        assert quota.available("claude-code", owner="owner") is True
        assert ei.value.failure_class == "interactive_deadline"

    @pytest.mark.asyncio
    async def test_next_turn_after_idle_timeout_is_attempted_normally(self):
        provider = _RaisingProvider(ProviderIdleTimeoutError("idle"), then_ok=True)
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError):
            await router.call("writer", "prompt", "system", **_bound())
        # The writer stayed eligible: the very next turn goes through.
        resp = await router.call("writer", "prompt", "system", **_bound())
        assert resp.text == "ok now"
        assert provider.calls == 2

    @pytest.mark.asyncio
    async def test_rate_limited_cools_with_retry_after(self):
        provider = _RaisingProvider(
            ProviderRateLimitedError("rl", retry_after=30)
        )
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await router.call("writer", "prompt", "system", **_bound())

        assert quota.available("claude-code", owner="owner") is False  # cooled
        remaining = quota.cooldown_remaining("claude-code", owner="owner")
        assert 25 <= remaining <= 32  # honors retry_after (+1s margin)
        assert ei.value.failure_class == "provider_rate_limited"

    @pytest.mark.asyncio
    async def test_overloaded_cools_with_retry_after(self):
        provider = _RaisingProvider(
            ProviderOverloadedError("ov", retry_after=8)
        )
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError):
            await router.call("writer", "prompt", "system", **_bound())

        assert quota.available("claude-code", owner="owner") is False
        assert 3 <= quota.cooldown_remaining("claude-code", owner="owner") <= 10


# ---------------------------------------------------------------------------
# 5.5 The interactive path does not sleep on a sole-writer timeout
# ---------------------------------------------------------------------------


class TestInteractiveNoSleep:
    def test_call_writer_never_sleeps_on_exhaustion(self, monkeypatch):
        import tinyassets.universe_intelligence as ui

        slept: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

        seen_kwargs = {}

        def _fake_call_provider(*args, **kwargs):
            seen_kwargs.update(kwargs)
            # All-skipped exhaustion => the safe case that used to sleep+retry.
            from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

            raise AllProvidersExhaustedError(
                "exhausted",
                attempts=[ProviderAttemptDiagnostic(
                    provider="claude-code", status="skipped",
                    skip_class="quota_or_cooldown",
                )],
            )

        monkeypatch.setattr(ui, "call_provider", _fake_call_provider)

        with pytest.raises(AllProvidersExhaustedError):
            ui._call_writer("hi", system="s", universe_context=object(), config=None)

        assert slept == []  # NEVER sleeps on the interactive path
        # And it disables the tenacity backoff in call.py.
        assert seen_kwargs.get("retry_on_exhaustion") is False

    def test_call_provider_no_retry_does_not_engage_tenacity_sleep(self, monkeypatch):
        from unittest.mock import MagicMock

        import tinyassets.providers.call as call_mod

        slept: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

        router = MagicMock()
        router.call_sync.side_effect = AllProvidersExhaustedError("exhausted")
        monkeypatch.setattr(call_mod, "_real_router", router)
        monkeypatch.setattr(call_mod, "_force_mock", False)

        with pytest.raises(AllProvidersExhaustedError):
            call_mod.call_provider("p", role="writer", retry_on_exhaustion=False)

        assert router.call_sync.call_count == 1  # no retry loop
        assert slept == []


# ---------------------------------------------------------------------------
# 5.6 _failure_notice maps failure_class to honest text (timeout != capacity)
# ---------------------------------------------------------------------------


class TestBackwardSafeNonStreaming:
    @pytest.mark.asyncio
    async def test_other_provider_still_returns_terminal_response(self):
        from unittest.mock import AsyncMock, patch

        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"codex output", b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
                  return_value=(["codex"], False)),
            fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=mock_proc,
            ),
        ):
            resp = await CodexProvider().complete("prompt", "system", ModelConfig())

        assert isinstance(resp, ProviderResponse)
        assert resp.text == "codex output"
        assert resp.provider == "codex"

    @pytest.mark.asyncio
    async def test_claude_complete_json_is_unchanged_terminal_response(self):
        from unittest.mock import AsyncMock, patch

        payload = json.dumps({"result": "structured answer"}).encode("utf-8")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(payload, b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("tinyassets.providers.claude_provider._resolve_claude_cmd",
                  return_value=(["claude"], False)),
            fake_owned_spawn(
                "tinyassets.providers.claude_provider", return_value=mock_proc,
            ),
        ):
            resp = await ClaudeProvider().complete_json(
                "prompt", "system", ModelConfig(),
            )

        assert isinstance(resp, ProviderResponse)
        assert resp.text == "structured answer"
        assert resp.provider == "claude-code"


# ---------------------------------------------------------------------------
# 5.8 Heartbeat liveness (blocker C): a reasoning/thinking-only stretch stays
#     alive; heartbeat content is never relayed.
# ---------------------------------------------------------------------------


def _thinking_tokens(n: int) -> dict:
    return {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": n}


def _signature_delta(sig: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "signature_delta", "signature": sig},
        },
    }


def _framing(event_type: str) -> dict:
    return {"type": "stream_event", "event": {"type": event_type}}


class TestHeartbeatLiveness:
    def test_thinking_only_stretch_keeps_the_turn_alive(self):
        # A reasoning stretch emits ONLY thinking_tokens + thinking_delta +
        # signature_delta (verified against the real trace). Each arrives just
        # under the idle interval; a naive reader that ignored them would
        # false-kill this working turn. They are liveness, so the turn survives
        # and completes — and none of the thinking content is relayed.
        proc = FakeStreamProcess([
            _line(INIT),
            (0.08, _line(_thinking_tokens(50))),
            (0.08, _line(_thinking_delta("secret chain of thought"))),
            (0.08, _line(_thinking_tokens(150))),
            (0.08, _line(_signature_delta("SIGSIGSIG"))),
            (0.08, _line(_thinking_delta("more private reasoning"))),
            (0.08, _line(_partial_text("Final answer"))),
            (0.08, _line(_result("Final answer"))),
        ])
        resp = _run_stream(proc, _FAST)  # idle 0.15 > each 0.08 gap
        assert resp.text == "Final answer"
        assert "secret chain of thought" not in resp.text
        assert "reasoning" not in resp.text
        assert "SIGSIGSIG" not in resp.text

    def test_hooks_status_and_framing_are_liveness(self):
        # Hooks, status, notification, and stream framing all keep the turn alive.
        proc = FakeStreamProcess([
            _line({"type": "system", "subtype": "hook_started", "hook_id": "h1"}),
            (0.08, _line({"type": "system", "subtype": "hook_response",
                          "hook_id": "h1", "exit_code": 0})),
            (0.08, _line({"type": "system", "subtype": "init"})),
            (0.08, _line(_framing("message_start"))),
            (0.08, _line({"type": "system", "subtype": "status",
                          "status": "requesting"})),
            (0.08, _line(_partial_text("Hi"))),
            (0.08, _line(_framing("content_block_stop"))),
            (0.08, _line(_result("Hi"))),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "Hi"


# ---------------------------------------------------------------------------
# 5.9 Absolute cap is a generous backstop, not a 300s total deadline (blocker D)
# ---------------------------------------------------------------------------


class TestAbsoluteCapIsGenerous:
    def test_default_absolute_cap_is_well_past_300s_and_idle_is_30s(self):
        from tinyassets.providers.base import (
            DEFAULT_ABSOLUTE_CAP_S,
            DEFAULT_IDLE_TIMEOUT_S,
            StreamTimeoutProfile,
        )

        # The old single 300s TOTAL deadline is gone: the cap is a generous
        # backstop (>= 600s) so a genuinely progressing turn survives past 300s,
        # while idle (30s) stays the primary fast-hang control.
        assert DEFAULT_ABSOLUTE_CAP_S >= 600
        assert DEFAULT_ABSOLUTE_CAP_S > 300
        assert DEFAULT_IDLE_TIMEOUT_S == 30
        prof = StreamTimeoutProfile()
        assert prof.absolute_cap_s >= 600 > 300
        assert prof.idle_s == 30

    def test_progressing_turn_survives_far_past_any_sub_cap_total_deadline(self):
        # Structural proof that NO total deadline below the absolute cap can kill
        # a progressing turn: idle 0.2s, cap 5s, ~25 events at 0.1s gaps → total
        # elapsed ~2.5s (>> 10x the idle interval and >> any mid-range total
        # deadline the old design enforced). It completes because only idle + cap
        # matter now. Scaled from the real "past 300s" case (cap 600s asserted
        # above); a 300s wait is not feasible in a unit test.
        import time as _t

        config = ModelConfig(
            init_timeout_s=0.3, first_progress_s=0.3, idle_timeout_s=0.2,
            absolute_cap_s=5.0,
        )
        items = [_line(INIT)]
        items += [(0.1, _line(_partial_text("x"))) for _ in range(24)]
        items += [(0.1, _line(_result("done")))]
        proc = FakeStreamProcess(items)
        t0 = _t.monotonic()
        resp = _run_stream(proc, config)
        elapsed = _t.monotonic() - t0
        assert resp.text == "done"
        # Really did run long past a single idle interval without being killed.
        assert elapsed > 2.0


# ---------------------------------------------------------------------------
# 5.10 Cancellation / cleanup (blocker E): caller-cancel kills the subprocess
# ---------------------------------------------------------------------------


_SLOW = ModelConfig(
    init_timeout_s=30.0, first_progress_s=30.0, idle_timeout_s=30.0,
    absolute_cap_s=60.0,
)


class TestCancellationKillsSubprocess:
    @pytest.mark.asyncio
    async def test_caller_cancellation_kills_the_subprocess_and_reaps_tasks(self):
        # The turn stalls (next line is 100s away) with a generous profile so
        # ONLY caller cancellation ends it. Cancelling the reader task must run
        # the finally: kill the process and reap both helper tasks (blocker E:
        # the Codex probe caught killed_after_caller_cancel=False).
        proc = FakeStreamProcess([
            _line(INIT),
            (100.0, _line(_result("never arrives"))),
        ])
        provider = ClaudeProvider()
        task = asyncio.create_task(provider._read_stream(proc, "prompt", _SLOW))
        await asyncio.sleep(0.05)  # let it reach the blocking readline
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.killed is True  # subprocess did NOT leak


# ---------------------------------------------------------------------------
# 5.11 Failure telemetry attached to raised exceptions (blocker K)
# ---------------------------------------------------------------------------


class TestFailureTelemetry:
    def test_idle_timeout_after_a_tool_carries_side_effect_telemetry(self):
        # A tool started (side_effect_state -> possible), then the stream hangs.
        # The raised idle-timeout must carry the attempt telemetry so the router
        # + notice can reason about whether a side effect may have run.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_tool_use("write_graph")),
            (10.0, _line(_result("never"))),
        ])
        with pytest.raises(ProviderIdleTimeoutError) as ei:
            _run_stream(proc, _FAST)
        tele = ei.value.attempt_telemetry
        assert isinstance(tele, dict)
        assert tele["failure_class"] == "provider_idle_timeout"
        assert tele["side_effect_state"] == "possible"
        assert tele["tool_phase"] == "tool_use"
        assert tele["provider"] == "claude-code"
        assert "last_progress_age_ms" in tele
        assert "exit_code" in tele

    def test_rate_limited_exception_carries_telemetry(self):
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_api_retry("rate_limit", 429, 30000)),
        ], returncode=0)
        with pytest.raises(ProviderRateLimitedError) as ei:
            _run_stream(proc, _FAST)
        tele = ei.value.attempt_telemetry
        assert isinstance(tele, dict)
        assert tele["failure_class"] == "provider_rate_limited"
        assert tele["side_effect_state"] == "none"


# ---------------------------------------------------------------------------
# 5.12 Policy-router cooldown by failure_class + authority preserved (blocker F)
# ---------------------------------------------------------------------------


def _policy(provider_name: str) -> dict:
    return {"preferred": {"provider": provider_name}}


class TestPolicyRouterCooldownMap:
    @pytest.mark.asyncio
    async def test_policy_preserves_authority_held(self):
        from tinyassets.exceptions import ProviderAuthorityHeldError

        provider = _RaisingProvider(ProviderAuthorityHeldError("held"))
        router, _ = _solo_router(provider)
        # Must be re-raised on the policy path, NOT swallowed as generic error +
        # fallthrough (blocker F).
        with pytest.raises(ProviderAuthorityHeldError):
            await router.call_with_policy(
                "writer", "p", "s", _policy("claude-code"), ModelConfig(), **_bound(),
            )
        assert provider.calls == 1  # no fallthrough retry


# ---------------------------------------------------------------------------
# 5.13 Sync wrapper vs absolute cap (blocker L)
# ---------------------------------------------------------------------------


class TestSyncWrapperTimeout:
    def test_sync_timeout_is_never_below_the_absolute_cap(self):
        from tinyassets.providers.base import DEFAULT_ABSOLUTE_CAP_S
        from tinyassets.providers.router import _sync_call_timeout_s

        # Default cfg: absolute cap 600 -> sync cap >= 600 (+margin).
        assert _sync_call_timeout_s(ModelConfig()) >= DEFAULT_ABSOLUTE_CAP_S
        # A SMALL legacy timeout must NOT drag the sync cap below the stream
        # absolute cap (the blocker-L defect: a sub-cap sync timeout returns
        # failure while the subprocess keeps streaming).
        assert _sync_call_timeout_s(ModelConfig(timeout=10)) >= DEFAULT_ABSOLUTE_CAP_S

    def test_sync_timeout_cancels_and_kills_the_subprocess(self, monkeypatch):
        # On a sync timeout the coroutine is cancelled (via the in-task
        # asyncio.wait_for), which reaches _read_stream's finally and KILLS the
        # subprocess — proven end-to-end through call_sync. The sync cap is
        # patched tiny so the test is fast (the real cap is 600s+).
        from unittest.mock import patch

        import tinyassets.providers.router as router_mod

        # A process that stalls forever before its first line.
        proc = FakeStreamProcess([(100.0, _line(INIT))])
        provider = ClaudeProvider()
        router = ProviderRouter(providers={"claude-code": provider})

        monkeypatch.setattr(router_mod, "_sync_call_timeout_s", lambda cfg: 0.3)

        from tinyassets.exceptions import ProviderTimeoutError as _PTE

        with (
            patch("tinyassets.providers.claude_provider._resolve_claude_cmd",
                  return_value=(["claude"], False)),
            fake_owned_spawn("tinyassets.providers.claude_provider", return_value=proc),
            # The owner-bound launch resolves the universe's credentials; this
            # test is about the timeout, so the env is stubbed.
            patch("tinyassets.providers.claude_provider.subprocess_env_for_provider",
                  return_value={}),
        ):
            with pytest.raises(_PTE):
                router.call_sync("writer", "prompt", "system", ModelConfig(), **_bound())

        assert proc.killed is True  # subprocess killed on sync timeout


# ---------------------------------------------------------------------------
# 5.14 NDJSON framing in the UNIT suite: blank lines, interspersed noise,
#      concurrent stderr, an unterminated final line.
# ---------------------------------------------------------------------------


class TestStreamFraming:
    def test_blank_lines_between_events_are_ignored(self):
        proc = FakeStreamProcess([
            _line(INIT),
            b"\n",
            b"   \n",
            _line(_partial_text("Hel")),
            b"\n",
            _line(_partial_text("lo")),
            _line(_result("")),
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "Hello"

    def test_concurrent_stderr_noise_does_not_break_a_successful_turn(self):
        # A non-bwrap stderr stream is drained concurrently and never fails the
        # turn (bwrap signatures are the only stderr that raises).
        proc = FakeStreamProcess(
            [_line(INIT), _line(_assistant_text("ok")), _line(_result("ok"))],
            stderr=b"warning: some benign diagnostic chatter\n" * 100,
        )
        resp = _run_stream(proc, _FAST)
        assert resp.text == "ok"

    def test_unterminated_final_result_line_is_still_parsed(self):
        # The final ``result`` arrives WITHOUT a trailing newline before EOF (the
        # real StreamReader returns the partial tail). It must still parse.
        proc = FakeStreamProcess([
            _line(INIT),
            _line(_assistant_text("done")),
            json.dumps(_result("done")).encode("utf-8"),  # no trailing "\n"
        ])
        resp = _run_stream(proc, _FAST)
        assert resp.text == "done"


# ---------------------------------------------------------------------------
# Codex re-review blockers F / C / D — driven through the REAL classifier
# (not injected pre-classified exceptions), which is what caught the original
# double-execution regression.
# ---------------------------------------------------------------------------


class _StreamCountingProvider(ClaudeProvider):
    """Executes the REAL ``_read_stream`` and counts how many times it runs.

    Injecting a pre-classified exception (the old router tests) could not catch
    the policy-path double-execution — only actually running the classifier and
    counting executions does.
    """

    name = "claude-code"

    def __init__(self, stdout_items):
        self._items = stdout_items
        self.calls = 0
        self.procs: list = []

    async def complete(self, prompt, system, config, *, universe_dir=None):
        self.calls += 1
        proc = FakeStreamProcess(list(self._items))
        self.procs.append(proc)
        return await self._read_stream(proc, prompt, config)


def test_tool_progress_and_heartbeats_keep_a_long_tool_turn_alive():
    # Blocker C: a turn emitting ONLY tool_progress / tool_heartbeat / status for
    # far longer than the idle interval is WORKING, not hung — it must survive,
    # and none of that heartbeat content is relayed into the reply.
    tool_progress = {"type": "tool_progress", "name": "build"}
    tool_hb = {"type": "system", "subtype": "tool_heartbeat"}
    status = {"type": "system", "subtype": "status", "text": "thinking"}
    items = [_line(INIT), _line(_tool_use("build"))]
    for _ in range(6):
        items.append((0.04, _line(tool_progress)))
        items.append((0.04, _line(tool_hb)))
        items.append((0.04, _line(status)))
    items.append((0.04, _line(_result("built"))))
    cfg = ModelConfig(
        init_timeout_s=0.2, first_progress_s=0.2, idle_timeout_s=0.1,
        absolute_cap_s=10.0,
    )
    resp = _run_stream(FakeStreamProcess(items), cfg)
    assert resp.text == "built"
    for word in ("thinking", "build", "tool_progress"):
        assert word not in resp.text


def test_legacy_timeout_does_not_reintroduce_a_total_wall_clock_deadline():
    # Blocker D: a tiny legacy ``timeout`` must NOT bound a progressing stream —
    # only the idle watchdog + generous absolute cap govern it. This turn streams
    # for ~0.8s, far past the 0.1s legacy timeout, and still completes.
    items = [_line(INIT)]
    for i in range(15):
        items.append((0.05, _line(_partial_text(str(i)))))
    items.append((0.05, _line(_result("finished"))))
    cfg = ModelConfig(
        timeout=0.1,  # legacy total timeout — must be IGNORED for streaming
        init_timeout_s=0.5, first_progress_s=0.5, idle_timeout_s=0.3,
        absolute_cap_s=30.0,
    )
    resp = _run_stream(FakeStreamProcess(items), cfg)
    assert resp.text == "finished"


@pytest.mark.asyncio
async def test_caller_cancellation_kills_the_subprocess_even_with_blocking_pipes():
    # Blocker E (stronger than the immediate-finish helper): stdout/stderr BLOCK
    # forever; a caller cancellation must still reach the finally and kill+reap
    # the subprocess (no leak).
    class _BlockingProc(FakeStreamProcess):
        def __init__(self):
            super().__init__([])

            class _Block:
                async def readline(self):
                    await asyncio.sleep(3600)
                    return b""

                async def read(self, _n):
                    await asyncio.sleep(3600)
                    return b""

            self.stdout = _Block()
            self.stderr = _Block()

    proc = _BlockingProc()
    provider = ClaudeProvider()
    cfg = ModelConfig(init_timeout_s=100, first_progress_s=100,
                      idle_timeout_s=100, absolute_cap_s=100)
    task = asyncio.create_task(provider._read_stream(proc, "p", cfg))
    await asyncio.sleep(0.05)  # let it start and block on readline
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed, "subprocess must be killed on caller-cancellation"
