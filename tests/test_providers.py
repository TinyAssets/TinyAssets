"""Tests for the provider layer: routing, fallback, quota, subprocess providers.

Unit tests mock the subprocess layer so they run without real CLI
binaries or network access.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.support.owned_spawn import fake_owned_spawn
from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from tinyassets.provider_work_authority import (
    ProviderInvocationCarrier,
    ProviderInvocationReservationState,
    ProviderInvocationSettlementOwner,
)
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.quota import QuotaTracker
from tinyassets.providers.router import FALLBACK_CHAINS, ProviderRouter

# Hard Rule 15: every routed call names the universe whose owner authorised it.
_UDIR = Path("universe-under-test")


def _ctx(carrier) -> UniverseContext:
    return UniverseContext(universe_dir=_UDIR, provider_invocation=carrier)

# =====================================================================
# Helpers -- fake providers for testing
# =====================================================================


class FakeProvider(BaseProvider):
    """A configurable fake provider for unit tests."""

    def __init__(
        self,
        name: str,
        family: str,
        response_text: str = "ok",
        *,
        fail_with: Exception | None = None,
    ) -> None:
        self.name = name
        self.family = family
        self._response_text = response_text
        self._fail_with = fail_with
        self.call_count = 0
        self.last_config: ModelConfig | None = None

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir=None,
    ) -> ProviderResponse:
        self.call_count += 1
        self.last_config = config
        if self._fail_with is not None:
            raise self._fail_with
        return ProviderResponse(
            text=self._response_text,
            provider=self.name,
            model="fake",
            family=self.family,
            latency_ms=1.0,
        )


class SlowCountingProvider(BaseProvider):
    """Tracks whether router sync wrappers let calls overlap."""

    name = "claude-code"
    family = "anthropic"

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir=None,
    ) -> ProviderResponse:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
        finally:
            with self.lock:
                self.active -= 1
        return ProviderResponse(
            text="ok",
            provider=self.name,
            model="fake",
            family=self.family,
            latency_ms=1.0,
        )


def _make_providers(**overrides: FakeProvider) -> dict[str, FakeProvider]:
    """Build a full provider map with defaults.  Override specific ones."""
    defaults = {
        "claude-code": FakeProvider("claude-code", "anthropic", "claude-resp"),
        "codex": FakeProvider("codex", "openai", "codex-resp"),
        "ollama-local": FakeProvider("ollama-local", "local", "ollama-resp"),
    }
    defaults.update(overrides)
    return defaults


# =====================================================================
# QuotaTracker
# =====================================================================


class TestQuotaTracker:
    def test_available_when_no_cooldown(self):
        qt = QuotaTracker()
        assert qt.available("claude-code") is True

    def test_cooldown_blocks_availability(self):
        qt = QuotaTracker()
        qt.cooldown("claude-code", 3600)
        assert qt.available("claude-code") is False

    def test_cooldown_expires(self):
        qt = QuotaTracker()
        # Set a cooldown that already expired.
        qt._cooldowns["claude-code"] = time.monotonic() - 1
        assert qt.available("claude-code") is True



# =====================================================================
# ProviderRouter -- single call routing
# =====================================================================


class TestProviderRouterCall:
    @staticmethod
    def _carrier(
        *, provider: str = "codex", role: str = "writer",
        operation: str = "repository_spec_delivery", max_tokens: int = 77,
        max_cost_microunits: int = 1,
    ):
        carrier = MagicMock(spec=ProviderInvocationCarrier)
        carrier.provider = provider
        carrier.role = role
        carrier.operation = operation
        carrier.max_tokens = max_tokens
        carrier.max_cost_microunits = max_cost_microunits
        carrier.selected_model = None
        carrier.native_selection = None
        carrier.validate_for_call.return_value = provider
        return carrier

    @staticmethod
    def _carrier_resolver(carrier):
        def resolve(_context, *, role, operation):
            carrier.validate_for_call(role=role, operation=operation)
            return carrier
        return resolve

    @pytest.mark.asyncio
    async def test_armed_carrier_narrows_provider_and_token_ceiling(self):
        providers = _make_providers()
        health = MagicMock()
        router = ProviderRouter(providers=providers, auth_health=health)
        carrier = self._carrier()

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            response = await router.call(
                "writer", "prompt", "system", ModelConfig(max_tokens=None),
                operation="repository_spec_delivery",
                universe_context=_ctx(carrier),
            )

        assert response.provider == "codex"
        assert providers["codex"].call_count == 1
        assert providers["codex"].last_config is not None
        assert providers["codex"].last_config.max_tokens == 77
        assert providers["claude-code"].call_count == 0
        health.assert_not_called()
        carrier.validate_for_call.assert_called_once_with(
            role="writer", operation="repository_spec_delivery",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("field", "message"),
        (
            ("max_tokens", "token budget"),
            ("max_cost_microunits", "cost budget"),
        ),
    )
    async def test_armed_carrier_rejects_zero_budget_authority(
        self,
        field,
        message,
    ):
        providers = _make_providers()
        router = ProviderRouter(providers=providers)
        carrier = self._carrier(**{field: 0})

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            with pytest.raises(PermissionError, match=message):
                await router.call(
                    "writer",
                    "prompt",
                    "system",
                    ModelConfig(max_tokens=None),
                    operation="repository_spec_delivery",
                    universe_context=_ctx(carrier),
                )

        assert all(provider.call_count == 0 for provider in providers.values())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("max_tokens", [-1, 78])
    async def test_armed_carrier_rejects_invalid_or_wider_token_cap(self, max_tokens):
        providers = _make_providers()
        router = ProviderRouter(providers=providers)

        carrier = self._carrier(max_tokens=77)
        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            with pytest.raises(PermissionError, match="token ceiling"):
                await router.call(
                    "writer", "prompt", "system", ModelConfig(max_tokens=max_tokens),
                    operation="repository_spec_delivery",
                    universe_context=_ctx(carrier),
                )

        assert all(provider.call_count == 0 for provider in providers.values())

    @pytest.mark.asyncio
    async def test_armed_carrier_never_falls_back_to_another_provider(self):
        providers = _make_providers(
            codex=FakeProvider(
                "codex",
                "openai",
                fail_with=ProviderUnavailableError("down"),
            )
        )
        router = ProviderRouter(providers=providers)

        carrier = self._carrier(max_tokens=10)
        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            with pytest.raises(AllProvidersExhaustedError):
                await router.call(
                    "writer", "prompt", "system", ModelConfig(max_tokens=10),
                    operation="repository_spec_delivery",
                    universe_context=_ctx(carrier),
                )

        assert providers["codex"].call_count == 1
        assert providers["claude-code"].call_count == 0

    @pytest.mark.asyncio
    async def test_invalid_carrier_holds_before_health_quota_or_provider(self):
        providers = _make_providers()
        health = MagicMock()
        quota = MagicMock()
        carrier = self._carrier()
        carrier.validate_for_call.side_effect = PermissionError("stale carrier")
        router = ProviderRouter(providers=providers, quota=quota, auth_health=health)

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            with pytest.raises(PermissionError, match="stale carrier"):
                await router.call(
                    "writer", "prompt", "system",
                    operation="repository_spec_delivery",
                    universe_context=_ctx(carrier),
                )

        health.assert_not_called()
        quota.available.assert_not_called()
        assert all(provider.call_count == 0 for provider in providers.values())

    @pytest.mark.asyncio
    async def test_nonexact_or_operationless_carrier_holds_before_provider_access(self):
        providers = _make_providers()
        router = ProviderRouter(providers=providers)
        context = _ctx(self._carrier())

        with pytest.raises(PermissionError, match="requires an operation"):
            await router.call("writer", "prompt", "system", universe_context=context)
        with pytest.raises(PermissionError, match="server-owned"):
            await router.call(
                "writer", "prompt", "system",
                operation="repository_spec_delivery", universe_context=context,
            )

        assert all(provider.call_count == 0 for provider in providers.values())

    @pytest.mark.asyncio
    async def test_armed_carrier_bypasses_policy_fallback_and_ensemble_fanout(self):
        providers = _make_providers()
        router = ProviderRouter(providers=providers)
        policy_carrier = self._carrier(role="judge")
        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(policy_carrier),
        ):
            text, provider, _meta = await router.call_with_policy(
                "judge", "prompt", "system",
                {"preferred": {"provider": "claude-code"}},
                ModelConfig(max_tokens=10),
                operation="repository_spec_delivery",
                universe_context=_ctx(policy_carrier),
            )
        ensemble_carrier = self._carrier(role="judge")
        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(ensemble_carrier),
        ):
            ensemble = await router.call_judge_ensemble(
                "prompt", "system", ModelConfig(max_tokens=10),
                operation="repository_spec_delivery",
                universe_context=_ctx(ensemble_carrier),
            )

        assert text == "codex-resp"
        assert provider == "codex"
        assert [response.provider for response in ensemble] == ["codex"]
        assert providers["codex"].call_count == 2
        assert providers["claude-code"].call_count == 0

    # Hard Rule 15 retired the platform fallback chain these tests used to
    # walk (claude-code -> codex -> ... -> ollama-local), the per-universe
    # preference over it, and the judge fan-out across host providers. A call
    # with no owner authority is refused
    # (``tests/test_platform_has_no_llm.py``); what survives is how the ONE
    # owner-named provider's failures are classified.

    async def _bound_call(self, router, carrier, role="writer"):
        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            return await router.call(
                role, "prompt", "system", ModelConfig(max_tokens=10),
                operation="repository_spec_delivery",
                universe_context=UniverseContext(
                    universe_dir=_UDIR, provider_invocation=carrier,
                ),
            )

    @pytest.mark.asyncio
    async def test_owner_provider_exhaustion_raises_without_widening(self):
        providers = _make_providers(
            codex=FakeProvider("codex", "openai", fail_with=ProviderError("down")),
        )
        router = ProviderRouter(providers=providers)

        with pytest.raises(AllProvidersExhaustedError):
            await self._bound_call(router, self._carrier(max_tokens=10))
        assert sum(p.call_count for p in providers.values()) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure", [ProviderUnavailableError("down"), ProviderTimeoutError("hung")],
    )
    async def test_owner_provider_failure_applies_cooldown(self, failure):
        providers = _make_providers(
            codex=FakeProvider("codex", "openai", fail_with=failure),
        )
        quota = QuotaTracker()
        router = ProviderRouter(providers=providers, quota=quota)

        with pytest.raises(AllProvidersExhaustedError):
            await self._bound_call(router, self._carrier(max_tokens=10))
        assert quota.available("codex") is False
        assert providers["claude-code"].call_count == 0

    @pytest.mark.asyncio
    async def test_bound_judge_ensemble_is_the_owners_one_judge(self):
        providers = _make_providers()
        router = ProviderRouter(providers=providers)
        carrier = self._carrier(role="judge")

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            ensemble = await router.call_judge_ensemble(
                "prompt", "system", ModelConfig(max_tokens=10),
                operation="repository_spec_delivery",
                universe_context=UniverseContext(
                    universe_dir=_UDIR, provider_invocation=carrier,
                ),
            )
        assert [response.provider for response in ensemble] == ["codex"]
        assert sum(p.call_count for p in providers.values()) == 1

    def test_call_sync_does_not_serialize_on_single_shared_worker(self):
        provider = SlowCountingProvider()
        router = ProviderRouter(providers={provider.name: provider})
        carrier = self._carrier(provider="claude-code", max_tokens=10)
        start = threading.Barrier(3)

        def _call() -> ProviderResponse:
            start.wait(timeout=2)
            return router.call_sync(
                "writer", "prompt", "system", ModelConfig(max_tokens=10),
                operation="repository_spec_delivery",
                universe_context=UniverseContext(
                    universe_dir=_UDIR, provider_invocation=carrier,
                ),
            )

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._carrier_resolver(carrier),
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_call), pool.submit(_call)]
                start.wait(timeout=2)
                results = [future.result(timeout=2) for future in futures]

        assert [result.provider for result in results] == ["claude-code", "claude-code"]
        assert provider.max_active == 2


# =====================================================================
# ProviderRouter -- register / available_providers
# =====================================================================


class TestProviderRegistration:
    def test_register_provider(self):
        router = ProviderRouter()
        assert router.available_providers == []

        fake = FakeProvider("test-provider", "test-family")
        router.register(fake)
        assert "test-provider" in router.available_providers

    def test_register_overwrites(self):
        router = ProviderRouter()
        router.register(FakeProvider("p", "f1", "v1"))
        router.register(FakeProvider("p", "f2", "v2"))
        assert len(router.available_providers) == 1

    def test_claude_provider_not_registered_when_binary_absent(self):
        """claude-code must not appear in available_providers when 'claude' binary is missing."""
        from tinyassets.providers.claude_provider import ClaudeProvider

        with patch("shutil.which", return_value=None):
            assert not ClaudeProvider.is_available()
            router = ProviderRouter()
            if ClaudeProvider.is_available():
                router.register(ClaudeProvider())
            assert "claude-code" not in router.available_providers

    def test_codex_provider_not_registered_when_binary_absent(self):
        """codex must not appear in available_providers when 'codex' binary is missing."""
        from tinyassets.providers.codex_provider import CodexProvider

        with patch("shutil.which", return_value=None):
            assert not CodexProvider.is_available()
            router = ProviderRouter()
            if CodexProvider.is_available():
                router.register(CodexProvider())
            assert "codex" not in router.available_providers

    def test_claude_provider_registered_when_binary_present(self):
        """claude-code is registered when 'claude' binary is found."""
        from tinyassets.providers.claude_provider import ClaudeProvider

        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            assert ClaudeProvider.is_available()

    def test_codex_provider_registered_when_binary_present(self):
        """codex is registered when 'codex' binary is found."""
        from tinyassets.providers.codex_provider import CodexProvider

        with patch("shutil.which", return_value="/usr/local/bin/codex"):
            assert CodexProvider.is_available()

    def test_effective_chain_excludes_unregistered_providers(self):
        """Runtime chain skips absent CLI providers instead of advertising them first."""
        router = ProviderRouter(
            providers={
                "codex": FakeProvider("codex", "openai"),
                "ollama-local": FakeProvider("ollama-local", "local"),
            },
        )

        chain, excluded = router.effective_chain(FALLBACK_CHAINS["writer"])

        assert chain == ["codex", "ollama-local"]
        assert [attempt.provider for attempt in excluded] == ["claude-code"]
        assert {attempt.skip_class for attempt in excluded} == {"not_in_registry"}


# =====================================================================
# ClaudeProvider (subprocess mock)
# =====================================================================


class _FakeClaudeStdout:
    """Replays a list of (delay_s, bytes) stdout lines for the stream reader."""

    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    async def readline(self):
        if self._idx >= len(self._items):
            return b""
        delay, data = self._items[self._idx]
        self._idx += 1
        if delay:
            await asyncio.sleep(delay)
        return data


class _FakeClaudeStderr:
    def __init__(self, data=b""):
        self._data = data
        self._sent = False

    async def read(self, _n):
        if self._sent:
            return b""
        self._sent = True
        return self._data


class _FakeClaudeStdin:
    def write(self, _b): ...
    async def drain(self): ...
    def close(self): ...


class _FakeClaudeProc:
    """Stand-in for ``asyncio.subprocess.Process``.

    ``returncode`` starts as ``None`` and only becomes the exit status once the
    handle is reaped, matching the real class in the direction that teardown
    depends on. This fake used to expose the status from construction, i.e. to
    claim it had already been reaped while still streaming; ``kill_owned_tree``
    then correctly declined to signal a handle it no longer owned, and every
    ``killed`` assertion here passed only because the pre-anchor teardown
    signalled unconditionally. Same trap documented on ``FakeStreamProcess`` in
    ``tests/test_provider_stream_and_classify.py``.
    """

    def __init__(self, stdout_items, *, stderr=b"", returncode=0):
        self.stdout = _FakeClaudeStdout(stdout_items)
        self.stderr = _FakeClaudeStderr(stderr)
        self.stdin = _FakeClaudeStdin()
        self._exit_status = returncode
        self.returncode = None
        self.killed = False

    def kill(self):
        self.killed = True

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._exit_status
        return self.returncode


def _cl(obj) -> bytes:
    import json as _json

    return (_json.dumps(obj) + "\n").encode("utf-8")


class TestClaudeProvider:
    @pytest.mark.asyncio
    async def test_success(self):
        from tinyassets.providers.claude_provider import ClaudeProvider

        proc = _FakeClaudeProc([
            (0.0, _cl({"type": "system", "subtype": "init"})),
            (0.0, _cl({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": "Hello world"}]}})),
            (0.0, _cl({"type": "result", "subtype": "success", "result": "Hello world"})),
        ])

        with (
            patch("tinyassets.providers.claude_provider._resolve_claude_cmd",
                  return_value=(["claude"], False)),
            fake_owned_spawn("tinyassets.providers.claude_provider", return_value=proc),
        ):
            provider = ClaudeProvider()
            resp = await provider.complete("prompt", "system", ModelConfig())

        assert resp.text == "Hello world"
        assert resp.provider == "claude-code"
        assert resp.family == "anthropic"

    @pytest.mark.asyncio
    async def test_exit_code_1_quick_triggers_unavailable(self):
        from tinyassets.providers.claude_provider import ClaudeProvider

        proc = _FakeClaudeProc([], stderr=b"unavailable", returncode=1)

        with (
            patch("tinyassets.providers.claude_provider._resolve_claude_cmd",
                  return_value=(["claude"], False)),
            fake_owned_spawn("tinyassets.providers.claude_provider", return_value=proc),
        ):
            provider = ClaudeProvider()
            with pytest.raises(ProviderUnavailableError):
                await provider.complete("prompt", "system", ModelConfig())

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        from tinyassets.providers.claude_provider import ClaudeProvider

        # init, then a long stall past the (injected short) idle interval — the
        # idle watchdog ends the turn as a ProviderTimeoutError subclass.
        proc = _FakeClaudeProc([
            (0.0, _cl({"type": "system", "subtype": "init"})),
            (10.0, _cl({"type": "result", "subtype": "success", "result": "late"})),
        ])
        fast = ModelConfig(
            init_timeout_s=0.1, first_progress_s=0.1, idle_timeout_s=0.1,
            absolute_cap_s=2.0,
        )

        with (
            patch("tinyassets.providers.claude_provider._resolve_claude_cmd",
                  return_value=(["claude"], False)),
            fake_owned_spawn("tinyassets.providers.claude_provider", return_value=proc),
        ):
            provider = ClaudeProvider()
            with pytest.raises(ProviderTimeoutError):
                await provider.complete("prompt", "system", fast)
        assert proc.killed is True


# =====================================================================
# CodexProvider (subprocess mock)
# =====================================================================


class TestCodexProvider:
    @pytest.mark.asyncio
    async def test_success(self):
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
            provider = CodexProvider()
            resp = await provider.complete("prompt", "system", ModelConfig())

        assert resp.text == "codex output"
        assert resp.provider == "codex"
        assert resp.family == "openai"

    @pytest.mark.asyncio
    async def test_error_raises_provider_error(self):
        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"bad"))
        mock_proc.returncode = 2
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
                  return_value=(["codex"], False)),
            fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=mock_proc,
            ),
        ):
            provider = CodexProvider()
            with pytest.raises(ProviderError):
                await provider.complete("prompt", "system", ModelConfig())

    @pytest.mark.asyncio
    async def test_empty_stdout_raises_provider_error(self):
        """Empty stdout with exit 0 must raise ProviderError, not return ''."""
        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
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
            provider = CodexProvider()
            with pytest.raises(ProviderError, match="empty response"):
                await provider.complete("prompt", "system", ModelConfig())

    @pytest.mark.asyncio
    async def test_auth_failure_exit_0_raises_provider_error(self):
        """codex v0.122 exits 0 on 401 but emits Unauthorized in stderr."""
        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b"",
            b"Error: 401 Unauthorized - Reconnecting...",
        ))
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
            provider = CodexProvider()
            with pytest.raises(ProviderError, match="auth-error"):
                await provider.complete("prompt", "system", ModelConfig())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("override", [None, "", " \t"])
    async def test_skip_git_repo_check_in_command_without_bwrap(self, monkeypatch, override):
        """codex exec must bypass sandbox only when bwrap is unavailable."""
        from tinyassets.providers.codex_provider import CodexProvider

        if override is None:
            monkeypatch.delenv("TINYASSETS_CODEX_MODEL", raising=False)
        else:
            monkeypatch.setenv("TINYASSETS_CODEX_MODEL", override)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
                  return_value=(["codex"], False)),
            patch("tinyassets.providers.codex_provider.get_sandbox_status",
                  return_value={"bwrap_available": False, "reason": "test"}),
            fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=mock_proc,
            ) as spawn,
        ):
            provider = CodexProvider()
            response = await provider.complete("prompt", "system", ModelConfig())

        captured_cmd = list(spawn.call_args.args)
        assert "--skip-git-repo-check" in captured_cmd, (
            f"Expected --skip-git-repo-check in command: {captured_cmd}"
        )
        assert "--dangerously-bypass-approvals-and-sandbox" in captured_cmd
        assert "--full-auto" not in captured_cmd
        for name in ("apps", "plugins", "remote_plugin"):
            assert ("--disable", name) in zip(captured_cmd, captured_cmd[1:])
        assert "--ephemeral" in captured_cmd
        assert "-C" in captured_cmd
        assert "-m" not in captured_cmd and "--model" not in captured_cmd
        assert "gpt-5.4" not in captured_cmd
        assert response.model == "provider-default"

    @pytest.mark.asyncio
    async def test_runs_from_repo_root_so_coding_tasks_can_read_source(self):
        """BUG-060: loop investigations need repo source/tests, not an empty tempdir."""
        import tinyassets.providers.codex_provider as codex_provider
        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
                  return_value=(["codex"], False)),
            patch("tinyassets.providers.codex_provider.get_sandbox_status",
                  return_value={"bwrap_available": False, "reason": "test"}),
            fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=mock_proc,
            ) as spawn,
        ):
            provider = CodexProvider()
            await provider.complete("prompt", "system", ModelConfig())

        captured_cmd = list(spawn.call_args.args)
        repo_root = Path(codex_provider.__file__).resolve().parents[2]
        assert "-C" in captured_cmd
        assert captured_cmd[captured_cmd.index("-C") + 1] == str(repo_root)

    @pytest.mark.asyncio
    async def test_model_can_be_overridden_by_env(self, monkeypatch):
        """Operators can move the provider forward after the deployed CLI supports it."""
        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        monkeypatch.setenv("TINYASSETS_CODEX_MODEL", "future-model-2030")
        with (
            patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
                  return_value=(["codex"], False)),
            patch("tinyassets.providers.codex_provider.get_sandbox_status",
                  return_value={"bwrap_available": True, "reason": None}),
            fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=mock_proc,
            ) as spawn,
        ):
            provider = CodexProvider()
            await provider.complete("prompt", "system", ModelConfig())

        captured_cmd = list(spawn.call_args.args)
        assert captured_cmd[captured_cmd.index("-m") + 1] == "future-model-2030"

    @pytest.mark.asyncio
    async def test_uses_workspace_sandbox_when_bwrap_available(self):
        """Healthy bwrap hosts should keep Codex's sandboxed auto mode."""
        from tinyassets.providers.codex_provider import CodexProvider

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with (
            patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
                  return_value=(["codex"], False)),
            patch("tinyassets.providers.codex_provider.get_sandbox_status",
                  return_value={"bwrap_available": True, "reason": None}),
            fake_owned_spawn(
                "tinyassets.providers.codex_provider", return_value=mock_proc,
            ) as spawn,
        ):
            provider = CodexProvider()
            await provider.complete("prompt", "system", ModelConfig())

        captured_cmd = list(spawn.call_args.args)
        assert ("--sandbox", "workspace-write") in zip(captured_cmd, captured_cmd[1:])
        assert "--full-auto" not in captured_cmd
        for name in ("apps", "plugins", "remote_plugin"):
            assert ("--disable", name) in zip(captured_cmd, captured_cmd[1:])
        assert "--dangerously-bypass-approvals-and-sandbox" not in captured_cmd
        assert "--skip-git-repo-check" in captured_cmd
        assert "--ephemeral" in captured_cmd


# =====================================================================
# OllamaProvider (HTTP mock)
# =====================================================================


class TestOllamaProvider:
    @pytest.mark.asyncio
    async def test_success(self):
        import json

        from tinyassets.providers.ollama_provider import OllamaProvider

        response_body = json.dumps({"response": "ollama output"}).encode()
        mock_response = MagicMock()
        mock_response.read.return_value = response_body
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            provider = OllamaProvider()
            resp = await provider.complete("prompt", "system", ModelConfig())

        assert resp.text == "ollama output"
        assert resp.provider == "ollama-local"
        assert resp.family == "local"

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        import urllib.error

        from tinyassets.providers.ollama_provider import OllamaProvider

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            provider = OllamaProvider()
            with pytest.raises(ProviderUnavailableError):
                await provider.complete("prompt", "system", ModelConfig())


# =====================================================================
# Fallback chain definitions
# =====================================================================


class TestFallbackChainDefinitions:
    def test_writer_preference_chain_starts_with_claude(self):
        """Static preference may name Claude; runtime effective_chain probes it."""
        assert FALLBACK_CHAINS["writer"][0] == "claude-code"
        assert FALLBACK_CHAINS["writer"][-1] == "ollama-local"

    def test_judge_chain_starts_with_codex(self):
        assert FALLBACK_CHAINS["judge"][0] == "codex"

    def test_extract_chain_starts_with_codex(self):
        assert FALLBACK_CHAINS["extract"][0] == "codex"
        assert FALLBACK_CHAINS["extract"][-1] == "ollama-local"

    def test_embed_is_local_only(self):
        assert FALLBACK_CHAINS["embed"] == ["ollama-local"]

    def test_all_chains_include_ollama(self):
        """Ollama is in every chain as last-resort fallback."""
        for role, chain in FALLBACK_CHAINS.items():
            assert "ollama-local" in chain, f"{role} chain missing ollama-local"

    def test_ollama_is_last_in_judge_chain(self):
        """Ollama is last in judge chains (text-parsed, not JSON)."""
        assert FALLBACK_CHAINS["judge"][-1] == "ollama-local"


class TestCarrierSettlementWithUnknownUsage:
    """A successful call whose usage the provider did not report must survive.

    Live failure, 2026-08-27: every prompt-template run in the founder's
    universe died with "provider invocation usage could not be settled" while
    effect-only branches kept working. The chain:

      1. `ProviderResponse.input_tokens/output_tokens/cost_microunits` all
         default to None -- deliberately, so "every existing construction site
         and non-streaming provider stays a valid terminal ProviderResponse"
         (providers/base.py).
      2. codex_provider populates them ONLY under machine accounting, which is
         `bool(config.sandbox_workspace)` (codex_provider.py:350). A plain
         prompt-template node has no sandbox workspace, so a completely
         successful call returns all three as None.
      3. The router forwarded them into a SUCCEEDED settlement, and
         `settle_invocation` rejects anything that is not an int -- so
         settling a successful call is what destroyed it.

    Introduced by #2559, which first put a ROUTER-settled carrier on the
    foreground run path. Every existing router test missed it because the mock
    carrier's `settlement_owner` is a bare MagicMock attribute, so
    `router_settles_carrier` was False and settlement never ran at all.
    """

    @staticmethod
    def _settling_carrier():
        carrier = MagicMock(spec=ProviderInvocationCarrier)
        carrier.provider = "codex"
        carrier.role = "writer"
        carrier.operation = "run_graph"
        carrier.max_tokens = 77
        carrier.max_cost_microunits = 1_000
        carrier.selected_model = None
        carrier.native_selection = None
        carrier.validate_for_call.return_value = "codex"
        # The bit that makes the router actually settle.
        carrier.settlement_owner = ProviderInvocationSettlementOwner.ROUTER
        return carrier

    @staticmethod
    def _resolver(carrier):
        def resolve(_context, *, role, operation):
            carrier.validate_for_call(role=role, operation=operation)
            return carrier
        return resolve

    @pytest.mark.asyncio
    async def test_unreported_usage_settles_indeterminate_not_fatal_succeeded(self):
        """FakeProvider reports no usage -- exactly what real codex does here."""
        carrier = self._settling_carrier()
        router = ProviderRouter(providers=_make_providers(), auth_health=MagicMock())

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._resolver(carrier),
        ):
            response = await router.call(
                "writer", "prompt", "system", ModelConfig(max_tokens=None),
                operation="run_graph",
                universe_context=_ctx(carrier),
            )

        assert response.text == "codex-resp"
        carrier.settle.assert_called_once()
        state = carrier.settle.call_args.args[0]
        assert state is ProviderInvocationReservationState.INDETERMINATE, (
            "unknown usage must settle INDETERMINATE -- the state that already "
            "means exactly this and whose budget treatment is conservative"
        )
        # Never zeros: reporting a free call would leave the budget undrainable.
        assert carrier.settle.call_args.kwargs.get("input_tokens") is None
        assert carrier.settle.call_args.kwargs.get("output_tokens") is None
        assert carrier.settle.call_args.kwargs.get("cost_microunits") is None

    @pytest.mark.asyncio
    async def test_reported_usage_still_settles_succeeded_with_its_numbers(self):
        """The fix must not blind the accounting path that does report usage."""
        class _AccountingProvider(FakeProvider):
            async def complete(self, prompt, system, config, *, universe_dir=None):
                self.call_count += 1
                self.last_config = config
                return ProviderResponse(
                    text="counted", provider=self.name, model="fake",
                    family=self.family, latency_ms=1.0,
                    input_tokens=70, output_tokens=30, cost_microunits=5,
                )

        carrier = self._settling_carrier()
        providers = _make_providers(codex=_AccountingProvider("codex", "openai"))
        router = ProviderRouter(providers=providers, auth_health=MagicMock())

        with patch(
            "tinyassets.providers.router._provider_invocation_carrier",
            side_effect=self._resolver(carrier),
        ):
            await router.call(
                "writer", "prompt", "system", ModelConfig(max_tokens=None),
                operation="run_graph",
                universe_context=_ctx(carrier),
            )

        carrier.settle.assert_called_once()
        assert (
            carrier.settle.call_args.args[0]
            is ProviderInvocationReservationState.SUCCEEDED
        )
        assert carrier.settle.call_args.kwargs == {
            "input_tokens": 70, "output_tokens": 30, "cost_microunits": 5,
        }
