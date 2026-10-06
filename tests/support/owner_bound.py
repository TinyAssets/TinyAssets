"""Drive a real ``ProviderRouter`` call under one universe owner's authority.

Hard Rule 15 (the platform has no LLM): the router refuses any call without an
owner authority. Tests that exercise what happens AFTER admission -- failure
classification, telemetry, diagnostics -- bind the call to a stand-in carrier
for one provider in one universe. Carrier minting, validation and settlement
have their own tests; here the carrier resolution is patched and settlement is
consumer-owned (``settlement_owner=None``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tinyassets.provider_work_authority import ProviderInvocationCarrier
from tinyassets.providers.base import ModelConfig, UniverseContext


def owner_carrier(provider: str, *, role: str = "writer", operation: str = "run_graph"):
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner")
    carrier.provider = provider
    carrier.role = role
    carrier.operation = operation
    carrier.max_tokens = 1000
    carrier.max_cost_microunits = 1000
    carrier.selected_model = None
    carrier.native_selection = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = provider
    return carrier


async def owner_bound_call(
    router: Any,
    provider: str,
    *,
    role: str = "writer",
    prompt: str = "prompt",
    system: str = "system",
    config: ModelConfig | None = None,
    policy: dict | None = None,
):
    """``router.call`` (or ``call_with_policy`` when ``policy`` is given), bound."""
    carrier = owner_carrier(provider, role=role)
    context = UniverseContext(
        universe_dir=Path("u-owner-bound"), provider_invocation=carrier,
    )
    with patch(
        "tinyassets.providers.router._provider_invocation_carrier",
        return_value=carrier,
    ):
        if policy is not None:
            return await router.call_with_policy(
                role, prompt, system, policy, config or ModelConfig(max_tokens=10),
                operation="run_graph", universe_context=context,
            )
        return await router.call(
            role, prompt, system, config or ModelConfig(max_tokens=10),
            operation="run_graph", universe_context=context,
        )
