"""HTTP router failure classification, cooldown, and timeout behavior."""

from __future__ import annotations

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


@pytest.fixture
def _solo_router(monkeypatch):
    def build(provider):
        provider.name = _PROVIDER_NAME
        monkeypatch.setattr("tinyassets.providers.provider_resolver.provider_for_definition",
                            lambda definition: provider)
        quota = QuotaTracker()
        return ProviderRouter(providers={provider.name: provider}, quota=quota), quota
    return build


@pytest.fixture(autouse=True)
def _model_root(tmp_path, monkeypatch):
    from tests.support.http_model import provider_name, seed_http_assignment

    global _MODEL_ROOT, _PROVIDER_NAME
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_http_assignment(tmp_path, universe_id="u-stream", owner_user_id="owner")
    _MODEL_ROOT = tmp_path / "u-stream"
    _PROVIDER_NAME = provider_name("owner", "u-stream")


def _bound(provider: str | None = None, role: str = "writer") -> dict:
    """Hard Rule 15: a routed call carries one universe owner's authority."""
    from unittest.mock import MagicMock

    from tinyassets.provider_work_authority import ProviderInvocationCarrier
    from tinyassets.providers.base import UniverseContext

    provider = provider or _PROVIDER_NAME
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


class TestRouterCooldownMap:
    @pytest.mark.asyncio
    async def test_idle_timeout_does_not_cool_the_sole_writer(self, _solo_router):
        provider = _RaisingProvider(ProviderIdleTimeoutError("idle"))
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available(_PROVIDER_NAME, owner="owner") is True  # NOT cooled
        assert ei.value.failure_class == "provider_idle_timeout"

    @pytest.mark.asyncio
    async def test_interactive_deadline_does_not_cool_the_sole_writer(self, _solo_router):
        provider = _RaisingProvider(InteractiveDeadlineError("cap"))
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available(_PROVIDER_NAME, owner="owner") is True
        assert ei.value.failure_class == "interactive_deadline"

    @pytest.mark.asyncio
    async def test_next_turn_after_idle_timeout_is_attempted_normally(self, _solo_router):
        provider = _RaisingProvider(ProviderIdleTimeoutError("idle"), then_ok=True)
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError):
            await model_call(router, "writer", "prompt", "system", **_bound())
        # The writer stayed eligible: the very next turn goes through.
        resp = await model_call(router, "writer", "prompt", "system", **_bound())
        assert resp.text == "ok now"
        assert provider.calls == 2

    @pytest.mark.asyncio
    async def test_rate_limited_cools_with_retry_after(self, _solo_router):
        provider = _RaisingProvider(
            ProviderRateLimitedError("rl", retry_after=30)
        )
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError) as ei:
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available(_PROVIDER_NAME, owner="owner") is False  # cooled
        remaining = quota.cooldown_remaining(_PROVIDER_NAME, owner="owner")
        assert 25 <= remaining <= 32  # honors retry_after (+1s margin)
        assert ei.value.failure_class == "provider_rate_limited"

    @pytest.mark.asyncio
    async def test_overloaded_cools_with_retry_after(self, _solo_router):
        provider = _RaisingProvider(
            ProviderOverloadedError("ov", retry_after=8)
        )
        router, quota = _solo_router(provider)

        with pytest.raises(AllProvidersExhaustedError):
            await model_call(router, "writer", "prompt", "system", **_bound())

        assert quota.available(_PROVIDER_NAME, owner="owner") is False
        assert 3 <= quota.cooldown_remaining(_PROVIDER_NAME, owner="owner") <= 10


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
                    provider=_PROVIDER_NAME, status="skipped",
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




# ---------------------------------------------------------------------------
# 5.11 Failure telemetry attached to raised exceptions (blocker K)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5.12 Policy-router cooldown by failure_class + authority preserved (blocker F)
# ---------------------------------------------------------------------------




class TestPolicyRouterCooldownMap:
    @pytest.mark.asyncio
    async def test_policy_preserves_authority_held(self, _solo_router):
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
