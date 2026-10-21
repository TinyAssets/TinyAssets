"""Tests for the provider layer: routing, fallback, quota, subprocess providers.

Unit tests mock the subprocess layer so they run without real CLI
binaries or network access.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tinyassets.provider_work_authority import (
    ProviderInvocationCarrier,
    ProviderInvocationSettlementOwner,
)
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call, call_sync

# Hard Rule 15: every routed call names the universe whose owner authorised it.
_UDIR = Path("universe-under-test")


def _ctx(carrier) -> UniverseContext:
    return UniverseContext(universe_dir=_UDIR, provider_invocation=carrier)


async def _model_call(router, role, prompt, system, config=None, *, operation=None,
                      universe_context=None):
    context = universe_context
    result = await call(ModelCall(
        model=None, fallbacks=(), context=ModelContext(
            role=role, prompt=prompt, system=system, config=config,
            operation=operation, universe_context=context, router=router,
        ),
    ), Parent(owner=context.provider_invocation._receipt.principal_id,
              command_center=context.universe_dir.name))
    return result.response


def _model_call_sync(router, role, prompt, system, config=None, *, operation=None,
                     universe_context=None):
    context = universe_context
    return call_sync(ModelCall(
        model=None, fallbacks=(), context=ModelContext(
            role=role, prompt=prompt, system=system, config=config,
            operation=operation, universe_context=context, router=router,
        ),
    ), Parent(owner=context.provider_invocation._receipt.principal_id,
              command_center=context.universe_dir.name)).response

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




# =====================================================================
# ProviderRouter -- single call routing
# =====================================================================


class TestProviderRouterCall:
    @pytest.fixture(autouse=True)
    def _model_root(self, tmp_path, monkeypatch):
        root = tmp_path / "universe-under-test"
        root.mkdir()
        monkeypatch.setattr(sys.modules[__name__], "_UDIR", root)

    @staticmethod
    def _carrier(
        *, provider: str = "codex", role: str = "writer",
        operation: str = "repository_spec_delivery", max_tokens: int = 77,
        max_cost_microunits: int = 1,
    ):
        carrier = MagicMock(spec=ProviderInvocationCarrier)
        carrier._receipt = MagicMock(
            principal_id="owner", universe_id=_UDIR.name,
            admitted_sources=frozenset({provider}),
        )
        carrier.provider = provider
        carrier.role = role
        carrier.operation = operation
        carrier.max_tokens = max_tokens
        carrier.max_cost_microunits = max_cost_microunits
        carrier.selected_model = None
        carrier.validate_for_call.return_value = provider
        return carrier

    @staticmethod
    def _carrier_resolver(carrier):
        def resolve(_context, *, role, operation):
            carrier.validate_for_call(role=role, operation=operation)
            return carrier
        return resolve








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
            return await _model_call(router,
                role, "prompt", "system", ModelConfig(max_tokens=10),
                operation="repository_spec_delivery",
                universe_context=UniverseContext(
                    universe_dir=_UDIR, provider_invocation=carrier,
                ),
            )






# =====================================================================
# ProviderRouter -- register / available_providers
# =====================================================================




# =====================================================================
# ClaudeProvider (subprocess mock)
# =====================================================================


def _cl(obj) -> bytes:
    import json as _json

    return (_json.dumps(obj) + "\n").encode("utf-8")


# =====================================================================
# CodexProvider (subprocess mock)
# =====================================================================


# =====================================================================
# OllamaProvider (HTTP mock)
# =====================================================================


# =====================================================================
# Explicit model choice
# =====================================================================


class TestCarrierSettlementWithUnknownUsage:
    @pytest.fixture(autouse=True)
    def _model_root(self, tmp_path, monkeypatch):
        root = tmp_path / "universe-under-test"
        root.mkdir()
        monkeypatch.setattr(sys.modules[__name__], "_UDIR", root)

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
        carrier._receipt = MagicMock(
            principal_id="owner", universe_id=_UDIR.name,
            admitted_sources=frozenset({"codex"}),
        )
        carrier.provider = "codex"
        carrier.role = "writer"
        carrier.operation = "run_graph"
        carrier.max_tokens = 77
        carrier.max_cost_microunits = 1_000
        carrier.selected_model = None
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



@pytest.fixture(autouse=True)
def _router_test_connected_sources(monkeypatch):
    """Router fixtures simulate an already admitted owner's source."""
    monkeypatch.setattr(
        "tinyassets.providers.served_model_plan.connected_sources",
        lambda *args, **kwargs: frozenset({
            "codex", "claude-code", "gemini-free", "groq-free",
            "grok-free", "ollama-local",
        }),
    )
