"""Tests for Q6.3 — per-universe `allowed_providers` allowlist primitive.

Spec: docs/design-notes/2026-04-27-q63-third-party-provider-privacy.md §5
Dispositions: .claude/agent-memory/navigator/q63_section4_dispositions.md

Hard Rule 15 (the platform has no LLM) removed the platform fallback chain,
the judge fan-out and the host writer pin this allowlist used to filter. The
allowlist now bounds the ONE provider an owner's authority names: a carrier
whose provider the universe does not allow is refused before launch.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tinyassets import runtime_singletons as runtime
from tinyassets.config import UniverseConfig
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.provider_work_authority import ProviderInvocationCarrier
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.quota import QuotaTracker
from tinyassets.providers.router import ProviderRouter


class _FakeProvider(BaseProvider):
    def __init__(self, name: str, text: str = "content") -> None:
        self.name = name
        self.family = "fake"
        self._text = text
        self.call_count = 0

    async def complete(
        self, prompt: str, system: str, config: ModelConfig, *, universe_dir=None,
    ) -> ProviderResponse:
        self.call_count += 1
        return ProviderResponse(
            text=self._text,
            provider=self.name,
            model="fake",
            family="fake",
            latency_ms=0.0,
        )


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_universe_config():
    """Snapshot + restore runtime_singletons.universe_config across tests."""
    saved = runtime.universe_config
    saved_pin = os.environ.get("TINYASSETS_PIN_WRITER")
    runtime.universe_config = UniverseConfig()
    if "TINYASSETS_PIN_WRITER" in os.environ:
        del os.environ["TINYASSETS_PIN_WRITER"]
    try:
        yield
    finally:
        runtime.universe_config = saved
        if saved_pin is not None:
            os.environ["TINYASSETS_PIN_WRITER"] = saved_pin
        elif "TINYASSETS_PIN_WRITER" in os.environ:
            del os.environ["TINYASSETS_PIN_WRITER"]


def _router_with_all_providers() -> tuple[
    ProviderRouter, dict[str, _FakeProvider],
]:
    names = [
        "claude-code", "codex", "gemini-free", "groq-free",
        "grok-free", "ollama-local",
    ]
    providers = {n: _FakeProvider(n) for n in names}
    router = ProviderRouter(providers=providers, quota=QuotaTracker())
    return router, providers


def _bound_call(router, *, provider: str, allowed_providers):
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner")
    carrier.provider = provider
    carrier.role = "writer"
    carrier.operation = "run_graph"
    carrier.max_tokens = 10
    carrier.max_cost_microunits = 5
    carrier.selected_model = None
    carrier.native_selection = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = provider

    def resolve(_context, *, role, operation):
        carrier.validate_for_call(role=role, operation=operation)
        return carrier

    context = UniverseContext(
        universe_dir=Path("u-allowlist"),
        config=UniverseConfig(allowed_providers=allowed_providers),
        provider_invocation=carrier,
    )
    with patch("tinyassets.providers.router._provider_invocation_carrier",
               side_effect=resolve):
        return _run(router.call(
            "writer", "p", "s", ModelConfig(max_tokens=10),
            operation="run_graph", universe_context=context,
        ))


def test_allowlist_none_serves_only_the_owner_named_provider(isolated_universe_config):
    router, providers = _router_with_all_providers()

    resp = _bound_call(router, provider="codex", allowed_providers=None)

    assert resp.provider == "codex"
    assert {n: p.call_count for n, p in providers.items() if p.call_count} == {"codex": 1}


def test_allowlist_containing_the_owner_provider_serves_it(isolated_universe_config):
    router, providers = _router_with_all_providers()

    resp = _bound_call(router, provider="codex", allowed_providers=["codex"])

    assert resp.provider == "codex"
    assert providers["codex"].call_count == 1


def test_allowlist_excluding_the_owner_provider_hard_fails(isolated_universe_config):
    router, providers = _router_with_all_providers()

    with pytest.raises(AllProvidersExhaustedError) as exc_info:
        _bound_call(router, provider="codex", allowed_providers=["does-not-exist"])

    msg = str(exc_info.value)
    assert "allowed_providers" in msg
    assert "does-not-exist" in msg
    assert all(p.call_count == 0 for p in providers.values())
