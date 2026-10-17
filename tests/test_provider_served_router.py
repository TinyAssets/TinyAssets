from __future__ import annotations

import asyncio

import pytest

from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call


async def _model_call(router, role, prompt, system, config=None, *, operation=None,
                      universe_context=None):
    context = universe_context
    if context is None or not any((context.provider_request, context.served_provider,
                                   context.provider_invocation)):
        return await router.call(role, prompt, system, config, operation=operation,
                                 universe_context=context)
    result = await call(ModelCall(
        model=None, fallbacks=(), context=ModelContext(
            role=role, prompt=prompt, system=system, config=config,
            operation=operation, universe_context=context, router=router,
        ),
    ), Parent(owner="", command_center=context.universe_dir.name))
    return result.response


class _RecordingProvider(BaseProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.family = name
        self.calls = 0

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir=None,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            text=f"{self.name}:{prompt}",
            provider=self.name,
            model="fixture",
            family=self.family,
            latency_ms=1.0,
        )


def _definition() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "Served",
        "description": "router fixture",
        "tags": ["test"],
        "components": {"identity": {"kind": "soul", "config": {}}},
    }


def _binding() -> dict[str, object]:
    return {"schema_version": 1, "name": "Served", "role": "writer"}


def _served_context(
    tmp_path,
    monkeypatch,
    *,
    capability_owner: str = "owner-1",
):
    from tests.support.http_model import seed_http_assignment
    from tinyassets.auth.middleware import (
        claim_provider_request,
        mint_provider_request_carrier,
        reserve_provider_request,
    )
    from tinyassets.config import load_universe_config
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_http_assignment(tmp_path, owner_user_id="owner-1", universe_id="u-owner")
    universe_dir = tmp_path / "u-owner"
    serving = resolve_serving_agent_binding(
        tmp_path, universe_id="u-owner", owner_user_id="owner-1",
    )
    reserve = reserve_provider_request(
        principal_id=capability_owner,
        session_id="session-1",
        request_id="request-1",
        tool_name="converse",
    )
    capability = claim_provider_request(reserve, tool_name="converse")
    carrier = mint_provider_request_carrier(
        universe_id="u-owner",
        agent_binding_id=serving["agent_binding_id"],
        binding_revision=serving["revision"],
        operation="converse",
    )
    context = UniverseContext(
        universe_dir=universe_dir,
        config=load_universe_config(universe_dir),
        provider_request=carrier,
    )
    return universe_dir, serving, capability, context


def _fresh_served_request(universe_dir, serving, *, request_id: str):
    from tinyassets.auth.middleware import (
        claim_provider_request,
        mint_provider_request_carrier,
        reserve_provider_request,
    )
    from tinyassets.config import load_universe_config

    reserve = reserve_provider_request(
        principal_id="owner-1",
        session_id=f"session-{request_id}",
        request_id=request_id,
        tool_name="converse",
    )
    capability = claim_provider_request(reserve, tool_name="converse")
    carrier = mint_provider_request_carrier(
        universe_id="u-owner",
        agent_binding_id=serving["agent_binding_id"],
        binding_revision=serving["revision"],
        operation="converse",
    )
    return capability, UniverseContext(
        universe_dir=universe_dir,
        config=load_universe_config(universe_dir),
        provider_request=carrier,
    )


@pytest.mark.parametrize("operation", [None, "run_graph"])
def test_universe_scoped_calls_never_route_from_config_without_live_authority(
    tmp_path,
    operation,
):
    from tinyassets.config import UniverseConfig
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter

    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()
    provider = _RecordingProvider("codex")
    context = UniverseContext(
        universe_dir=universe_dir,
        config=UniverseConfig(
            preferred_writer="codex",
            allowed_providers=["codex"],
            engine_assignment_state="ready",
        ),
    )

    with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
        asyncio.run(
            _model_call(ProviderRouter({"codex": provider}),
                "writer",
                "must not run",
                "system",
                operation=operation,
                universe_context=context,
            )
        )
    assert provider.calls == 0


def test_claude_serving_requires_a_valid_owner(tmp_path):
    from tinyassets.provider_serving_binding import bind_serving_provider

    with pytest.raises(ValueError):
        bind_serving_provider(
            base_path=str(tmp_path),
            universe_dir=str(tmp_path),
            owner_user_id="",  # cleared the claude hold; fails later on owner
            universe_id="u-owner",
            agent_binding_id="binding-1",
            expected_revision=1,
            provider="api_key_http:provdef_test",
        )


def _http_authority(tmp_path):
    from tinyassets.provider_assignment import ServedProviderAuthority

    return ServedProviderAuthority(
        authority_kind="connection_grant",
        provider="api_key_http:provdef_test",
        max_invocations=10,
        request_max_invocations=2,
        max_tokens=1000,
        max_cost_microunits=100_000,
        owner_user_id="o",
        universe_id="u",
        agent_binding_id="b",
        binding_revision=1,
        binding_id="bid",
        binding_generation=1,
        binding_digest="d",
        credential_reference_id="c",
        credential_reference_generation=1,
        credential_reference_digest="cd",
        credential_service="http",
        request_capability=object(),
    )


def test_finalize_tolerates_row_already_reconciled(tmp_path):
    """A call that outran its lease is settled by the reconciler; its late
    finalize must return gracefully, NOT raise an accounting error (Codex
    re-review #4). A genuinely MISSING row still raises.
    """
    import sqlite3

    from tinyassets import provider_assignment as pa
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import (
        ServedProviderBudgetReservation,
        finalize_served_provider_budget,
    )
    from tinyassets.storage import db_path

    authority = _http_authority(tmp_path)  # provider irrelevant to finalize
    reservation = ServedProviderBudgetReservation(
        reservation_id="r-reconciled",
        binding_id=authority.binding_id,
        binding_generation=authority.binding_generation,
        output_tokens=50,
        reserved_total_tokens=100,
        reserved_cost_microunits=10_000,
    )
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        pa._ensure_served_budget_schema(conn)
        # The reconciler already settled this row as succeeded.
        conn.execute(
            "INSERT INTO served_provider_budget_reservations "
            "(reservation_id, binding_id, binding_generation, state, "
            "reserved_total_tokens, reserved_cost_microunits, "
            "actual_total_tokens, actual_cost_microunits, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "r-reconciled",
                authority.binding_id,
                authority.binding_generation,
                "succeeded",
                100,
                10_000,
                100,
                10_000,
                1.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Already-reconciled: returns without raising.
    finalize_served_provider_budget(
        str(tmp_path),
        authority=authority,
        reservation=reservation,
        input_tokens=10,
        output_tokens=40,
        cost_microunits=5_000,
    )

    # A truly missing reservation is still an accounting anomaly.
    missing = ServedProviderBudgetReservation(
        reservation_id="r-missing",
        binding_id=authority.binding_id,
        binding_generation=authority.binding_generation,
        output_tokens=50,
        reserved_total_tokens=100,
        reserved_cost_microunits=10_000,
    )
    with pytest.raises(ProviderAuthorityHeldError, match="accounting failed"):
        finalize_served_provider_budget(
            str(tmp_path),
            authority=authority,
            reservation=missing,
            input_tokens=10,
            output_tokens=40,
            cost_microunits=5_000,
        )


def recording_router(monkeypatch, provider):
    """Keep real authority checks and replace only the HTTP transport executor."""
    from tests.support.http_model import provider_name
    from tinyassets.providers.router import ProviderRouter
    provider.name = provider_name("owner-1", "u-owner")
    monkeypatch.setattr("tinyassets.providers.provider_resolver.provider_for_definition",
                        lambda definition: provider)
    return ProviderRouter({provider.name: provider})
