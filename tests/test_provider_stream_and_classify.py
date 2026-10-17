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

from tests.support.model_call import call as model_call
from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    InteractiveDeadlineError,
    ProviderIdleTimeoutError,
    ProviderOverloadedError,
    ProviderRateLimitedError,
)
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
)
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


# ---------------------------------------------------------------------------
# 5.1 Behavior parity: recorded stream assembles the SAME final text
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5.2 Idle watchdog: progress keeps it alive; only real idle fails it
# Pending native tool work is not model-idle silence. Keep this regression on
# the real reader rather than simulating a successful ProviderResponse.


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


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5.3 Failure taxonomy from api_retry / exit / malformed
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5.4 Router cooldown map by failure_class
# ---------------------------------------------------------------------------


class _RaisingProvider(BaseProvider):
    """A api_key_http:fixture fake whose complete() raises a chosen exception once,
    then (optionally) succeeds — to prove next-turn eligibility."""

    name = "api_key_http:fixture"
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


@pytest.fixture(autouse=True)
def _model_root(tmp_path):
    global _MODEL_ROOT
    _MODEL_ROOT = tmp_path / "u-stream"
    _MODEL_ROOT.mkdir()


def _bound(provider: str = "api_key_http:fixture", role: str = "writer") -> dict:
    """Hard Rule 15: a routed call carries one universe owner's authority."""
    from unittest.mock import MagicMock

    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers.base import UniverseContext

    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(
        principal_id="owner", universe_id="u-stream",
        admitted_sources=frozenset({provider}),
    )
    carrier.provider = provider
    carrier.role = role
    carrier.operation = "run_graph"
    carrier.max_tokens = 1000
    carrier.max_cost_microunits = 1000
    carrier.selected_model = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = provider
    return {
        "operation": "run_graph",
        "universe_context": UniverseContext(
            universe_dir=_MODEL_ROOT, provider_invocation=carrier,
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
    monkeypatch.setattr("tinyassets.providers.served_model_plan.connected_sources",
                        lambda *a, **k: frozenset({"api_key_http:fixture"}))


class TestRouterCooldownMap:
    @pytest.mark.asyncio
    async def test_idle_timeout_does_not_cool_the_sole_writer(self):
        provider = _RaisingProvider(ProviderIdleTimeoutError("idle"))
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available("api_key_http:fixture", owner="owner") is True  # NOT cooled
        assert ei.value.failure_class == "provider_idle_timeout"

    @pytest.mark.asyncio
    async def test_interactive_deadline_does_not_cool_the_sole_writer(self):
        provider = _RaisingProvider(InteractiveDeadlineError("cap"))
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available("api_key_http:fixture", owner="owner") is True
        assert ei.value.failure_class == "interactive_deadline"

    @pytest.mark.asyncio
    async def test_next_turn_after_idle_timeout_is_attempted_normally(self):
        provider = _RaisingProvider(ProviderIdleTimeoutError("idle"), then_ok=True)
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError):
            await model_call(router, "writer", "prompt", "system", **_bound())
        # The writer stayed eligible: the very next turn goes through.
        resp = await model_call(router, "writer", "prompt", "system", **_bound())
        assert resp.text == "ok now"
        assert provider.calls == 2

    @pytest.mark.asyncio
    async def test_rate_limited_cools_with_retry_after(self):
        provider = _RaisingProvider(
            ProviderRateLimitedError("rl", retry_after=30)
        )
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available("api_key_http:fixture", owner="owner") is False  # cooled
        remaining = quota.cooldown_remaining("api_key_http:fixture", owner="owner")
        assert 25 <= remaining <= 32  # honors retry_after (+1s margin)
        assert ei.value.failure_class == "provider_rate_limited"

    @pytest.mark.asyncio
    async def test_overloaded_cools_with_retry_after(self):
        provider = _RaisingProvider(
            ProviderOverloadedError("ov", retry_after=8)
        )
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError):
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available("api_key_http:fixture", owner="owner") is False
        assert 3 <= quota.cooldown_remaining("api_key_http:fixture", owner="owner") <= 10


# ---------------------------------------------------------------------------
# 5.5 The interactive path does not sleep on a sole-writer timeout
# ---------------------------------------------------------------------------


class TestInteractiveNoSleep:
    def test_call_writer_never_sleeps_on_exhaustion(self, monkeypatch):
        import tinyassets.universe_intelligence as ui

        slept: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))

        seen_kwargs = {}

        def _fake_text_call(*args, **kwargs):
            seen_kwargs.update(kwargs)
            # All-skipped exhaustion => the safe case that used to sleep+retry.
            from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic

            raise AllProvidersExhaustedError(
                "exhausted",
                attempts=[ProviderAttemptDiagnostic(
                    provider="api_key_http:fixture", status="skipped",
                    skip_class="quota_or_cooldown",
                )],
            )

        monkeypatch.setattr(ui, "text_sync", _fake_text_call)

        with pytest.raises(AllProvidersExhaustedError):
            ui._call_writer("hi", system="s", universe_context=object(), config=None)

        assert slept == []  # NEVER sleeps on the interactive path
        # The interactive call does not request a retry.
        assert seen_kwargs.get("retry_on_exhaustion") is False


# ---------------------------------------------------------------------------
# 5.6 _failure_notice maps failure_class to honest text (timeout != capacity)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 5.10 Cancellation / cleanup (blocker E): caller-cancel kills the subprocess
# ---------------------------------------------------------------------------


_SLOW = ModelConfig(
    init_timeout_s=30.0, first_progress_s=30.0, idle_timeout_s=30.0,
    absolute_cap_s=60.0,
)


# ---------------------------------------------------------------------------
# 5.11 Failure telemetry attached to raised exceptions (blocker K)
# ---------------------------------------------------------------------------


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
            await model_call(router, "writer", "p", "s", ModelConfig(), **_bound())
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


# ---------------------------------------------------------------------------
# 5.14 NDJSON framing in the UNIT suite: blank lines, interspersed noise,
#      concurrent stderr, an unterminated final line.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Codex re-review blockers F / C / D — driven through the REAL classifier
# (not injected pre-classified exceptions), which is what caught the original
# double-execution regression.
# ---------------------------------------------------------------------------
