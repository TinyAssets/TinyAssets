"""Drive a real ``ProviderRouter`` call under one universe owner's authority.

Hard Rule 15 (the platform has no LLM): the router refuses any call without an
owner authority. Tests that exercise what happens AFTER admission -- failure
classification, telemetry, diagnostics -- bind the call to a stand-in carrier
for one provider in one universe. Carrier minting, validation and settlement
have their own tests; here the carrier resolution is patched and settlement is
consumer-owned (``settlement_owner=None``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tinyassets.provider_work_authority import ProviderInvocationCarrier
from tinyassets.providers.base import ModelConfig, UniverseContext
from tinyassets.providers.model_policy import ModelRef


def owner_carrier(provider: str, *, role: str = "writer", operation: str = "run_graph"):
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner", universe_id="u-owner-bound")
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
    """Drive the one router executor under an owner-bound test carrier."""
    carrier = owner_carrier(provider, role=role)
    context = UniverseContext(
        universe_dir=Path("u-owner-bound"), provider_invocation=carrier,
    )
    if policy and isinstance(policy.get("preferred"), dict):
        preferred = policy["preferred"]
        context = replace(context, model_selection=ModelRef(
            preferred["provider"], preferred.get("model_id", ""),
        ))
    with patch(
        "tinyassets.providers.router._provider_invocation_carrier",
        return_value=carrier,
    ), patch(
        "tinyassets.providers.served_model_plan.connected_sources",
        return_value=frozenset({provider}),
    ):
        return await router.call(
            role, prompt, system, config or ModelConfig(max_tokens=10),
            operation="run_graph", universe_context=context,
        )
