from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)


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


@pytest.mark.parametrize("terminal,category,clue", [
    ("true", "authentication_failed", True),
    ("false", "authentication_failed", False),
    ("absent", "authentication_failed", False),
    ("true", "billing_error", False),
    ("true", "oauth_org_not_allowed", False),
    ("true", "unrecognized", False),
    ("true", "not_reported", False),
])
def test_native_auth_notice_keeps_served_retry_fenced(
    tmp_path, terminal, category, clue,
):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import AllProvidersExhaustedError, ProviderError
    from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence
    from tinyassets.providers.quota import COOLDOWN_OTHER
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.universe_server import _served_failure_notice

    _, _, capability, context = _served_context(tmp_path)
    error = ProviderError(
        "fixture-native terminal result was not success "
        f"(subtype=success, is_error={terminal}, last_assistant_error={category})"
    )
    error.attempt_telemetry = {
        "phase": "streaming", "terminal_is_error": terminal,
        "last_assistant_error": category, "side_effect_state": "possible",
    }
    error.native_evidence = NativeCompletionEvidence("codex", False, True, "possible")

    class RejectingProvider(_RecordingProvider):
        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise error

    provider = RejectingProvider("codex")
    fallback = _RecordingProvider("ollama-local")
    router = ProviderRouter(providers={"codex": provider, "ollama-local": fallback})
    try:
        with patch.object(router._quota, "cooldown") as cooldown:
            with pytest.raises(AllProvidersExhaustedError) as caught:
                asyncio.run(router.call(
                    "writer", "hello", "system", universe_context=context,
                    operation="converse",
                ))
        cooldown.assert_called_once_with("codex", COOLDOWN_OTHER, owner="owner-1")
        aggregate = caught.value
        assert aggregate.failure_class is None
        assert aggregate.native_evidence == (None,)
        assert len(aggregate.attempts) == 1
        assert aggregate.attempts[0].skip_class == "provider_error"
        assert aggregate.attempts[0].side_effect_state == "possible"
        assert provider.calls == 1
        assert fallback.calls == 0
        notice = _served_failure_notice(aggregate).lower()
        if clue:
            assert "sign-in" in notice and "reconnect" in notice
            assert "expired" not in notice and "revoked" not in notice
            assert "will fix" not in notice
        else:
            assert "could not identify why" in notice
            assert "not a usage or billing limit" not in notice
    finally:
        revoke_provider_request(capability)


def _binding() -> dict[str, object]:
    return {"schema_version": 1, "name": "Served", "role": "writer"}


def _served_context(
    tmp_path,
    *,
    capability_owner: str = "owner-1",
    path_backed: bool = False,
):
    from tinyassets.auth.middleware import (
        claim_provider_request,
        mint_provider_request_carrier,
        reserve_provider_request,
    )
    from tinyassets.config import load_universe_config
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()
    credential = {
        "credential_type": "llm_subscription",
        "service": "codex",
        "auth_json_b64": "e30=",
    }
    if path_backed:
        auth_home = universe_dir / "codex-auth"
        auth_home.mkdir()
        (auth_home / "auth.json").write_bytes(b'{"tokens":{"access_token":"first"}}')
        credential = {
            "credential_type": "llm_subscription",
            "service": "codex",
            "codex_home": str(auth_home),
        }
    write_credential_vault(
        universe_dir,
        [credential],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    definition = publish_definition(
        tmp_path,
        author_id="owner-1",
        payload=_definition(),
    )
    agent = create_binding(
        tmp_path,
        universe_id="u-owner",
        definition_id=definition["agent_definition_id"],
        created_by="owner-1",
        payload=_binding(),
    )
    connected = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=1,
        provider="codex",
    )
    serving = set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"],
        enabled=True,
    )["agent_binding"]
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


def test_served_router_uses_only_universe_authorized_provider(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(tmp_path)
    codex = _RecordingProvider("codex")
    ambient = _RecordingProvider("claude-code")
    router = ProviderRouter({"codex": codex, "claude-code": ambient})
    try:
        response = asyncio.run(
            router.call(
                "writer",
                "hello",
                "system",
                operation="converse",
                universe_context=context,
            )
        )
    finally:
        revoke_provider_request(capability)

    assert response.provider == "codex"
    assert codex.calls == 1
    assert ambient.calls == 0


def test_served_router_fails_closed_without_exact_live_authority(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_serving_binding import set_serving
    from tinyassets.providers.router import ProviderRouter

    universe_dir, serving, capability, context = _served_context(tmp_path)
    provider = _RecordingProvider("codex")
    router = ProviderRouter({"codex": provider})
    set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=serving["agent_binding_id"],
        expected_revision=serving["revision"],
        enabled=False,
    )
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            asyncio.run(
                router.call(
                    "writer",
                    "must not run",
                    "system",
                    operation="converse",
                    universe_context=context,
                )
            )
    finally:
        revoke_provider_request(capability)
    assert provider.calls == 0


def test_served_router_rejects_request_principal_that_is_not_binding_owner(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(
        tmp_path,
        capability_owner="visitor-1",
    )
    provider = _RecordingProvider("codex")
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            asyncio.run(
                ProviderRouter({"codex": provider}).call(
                    "writer",
                    "must not run",
                    "system",
                    operation="converse",
                    universe_context=context,
                )
            )
    finally:
        revoke_provider_request(capability)
    assert provider.calls == 0


def test_served_router_rejects_credential_rotation_after_selection(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.credential_vault import write_credential_vault
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter

    universe_dir, _, capability, context = _served_context(tmp_path)
    write_credential_vault(
        universe_dir,
        [
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "auth_json_b64": "eyJyb3RhdGVkIjp0cnVlfQ==",
            }
        ],
    )
    provider = _RecordingProvider("codex")
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            asyncio.run(
                ProviderRouter({"codex": provider}).call(
                    "writer",
                    "must not run",
                    "system",
                    operation="converse",
                    universe_context=context,
                )
            )
    finally:
        revoke_provider_request(capability)
    assert provider.calls == 0


def test_budget_reservation_revalidates_path_credential_at_moment_of_use(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import (
        authorize_served_provider_call,
        reserve_served_provider_budget,
    )

    universe_dir, serving, capability, context = _served_context(
        tmp_path,
        path_backed=True,
    )
    try:
        with authorize_served_provider_call(
            tmp_path,
            universe_dir=universe_dir,
            request_carrier=context.provider_request,
            role="writer",
            operation="converse",
        ) as authority:
            (universe_dir / "codex-auth" / "auth.json").write_bytes(
                b'{"tokens":{"access_token":"rotated"}}'
            )
            with pytest.raises(ProviderAuthorityHeldError, match="budget"):
                reserve_served_provider_budget(
                    tmp_path,
                    universe_dir=universe_dir,
                    authority=authority,
                    requested_output_tokens=8,
                    estimated_input_tokens=1,
                )
    finally:
        revoke_provider_request(capability)


def test_many_concurrent_reservations_share_one_binding_without_bricking(tmp_path):
    """One user driving many surfaces at once (plus automations) must not brick.

    A served turn reserves ~len(system+prompt bytes) of in-flight budget; a
    rebuilt persona/brain system prompt is ~15-30 KB. Ten simultaneous in-flight
    reservations for the SAME binding (many surfaces + concurrent LangGraph
    automations) must all be admitted under the concurrency-sized cap. The
    in-flight cap is a runaway guard, not a spend limit, so legitimate
    concurrency never trips it.
    """
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.provider_assignment import (
        authorize_served_provider_call,
        reserve_served_provider_budget,
    )

    universe_dir, _serving, capability, context = _served_context(
        tmp_path,
        path_backed=True,
    )
    try:
        with authorize_served_provider_call(
            tmp_path,
            universe_dir=universe_dir,
            request_carrier=context.provider_request,
            role="writer",
            operation="converse",
        ) as authority:
            reservations = [
                reserve_served_provider_budget(
                    tmp_path,
                    universe_dir=universe_dir,
                    authority=authority,
                    requested_output_tokens=4_000,
                    estimated_input_tokens=20_000,
                )
                for _ in range(10)
            ]
            assert len(reservations) == 10
            assert all(r.output_tokens >= 1 for r in reservations)
    finally:
        revoke_provider_request(capability)


def test_stale_binding_rebind_advances_generation_and_reflows_ceiling(tmp_path, monkeypatch):
    """A binding whose signed ceiling drifts from current policy must be HEALED by
    a re-bind (advance generation + digest, re-sign at the current ceiling), not
    replayed with the stale authority. Exact-equality gate: a raise heals UP and a
    tightening reflows DOWN, both via a re-signed authority — never an
    admission-time override (Codex 2026-08-22). Completing fix for the
    existing-binding case #2479's constant raise did not reach.
    """
    import tinyassets.provider_serving_binding as serving_binding
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.provider_serving_binding import bind_serving_provider

    # Bind initially with the OLD stale-low ceiling (what the founder persists).
    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 32_768)
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 10_000_000)
    universe_dir, serving, capability, _ctx = _served_context(tmp_path)
    revoke_provider_request(capability)

    def _rebind(rev):
        return bind_serving_provider(
            base_path=tmp_path,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            agent_binding_id=serving["agent_binding_id"],
            expected_revision=rev,
            provider="codex",
        )

    # Same policy -> REPLAY (no generation change); capture the stale identity.
    replay = _rebind(serving["revision"])
    assert replay.get("replayed") is True
    stale_gen = replay["provider_binding"]["generation"]
    stale_digest = replay["provider_binding"]["binding_digest"]
    rev = replay["agent_binding"]["revision"]

    # Raise policy -> HEAL up: not replayed, generation advances, digest changes.
    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 4_000_000)
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 400_000_000)
    healed = _rebind(rev)
    assert healed.get("replayed") is not True
    assert healed["provider_binding"]["generation"] > stale_gen
    assert healed["provider_binding"]["binding_digest"] != stale_digest
    assert healed["provider_binding"]["max_tokens"] == 4_000_000
    assert healed["provider_binding"]["max_cost_microunits"] == 400_000_000
    healed_gen = healed["provider_binding"]["generation"]
    rev = healed["agent_binding"]["revision"]

    # Tighten policy -> also NOT replayed (exact-equality gate): ceiling reflows
    # DOWN via a fresh re-signed generation, never a stale-high replay.
    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 1_000_000)
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 100_000_000)
    tightened = _rebind(rev)
    assert tightened.get("replayed") is not True
    assert tightened["provider_binding"]["generation"] > healed_gen
    assert tightened["provider_binding"]["max_tokens"] == 1_000_000
    assert tightened["provider_binding"]["max_cost_microunits"] == 100_000_000


def test_replay_gate_checks_token_and_cost_independently(tmp_path, monkeypatch):
    """Each replay guard (token AND cost) must independently force a rebind, so
    neither can be deleted silently: drift in EITHER ceiling alone must skip the
    replay (Codex 2026-08-22).
    """
    import tinyassets.provider_serving_binding as serving_binding
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.provider_serving_binding import bind_serving_provider

    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 4_000_000)
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 400_000_000)
    universe_dir, serving, capability, _ctx = _served_context(tmp_path)
    revoke_provider_request(capability)

    def _rebind(rev):
        return bind_serving_provider(
            base_path=tmp_path,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            agent_binding_id=serving["agent_binding_id"],
            expected_revision=rev,
            provider="codex",
        )

    # Same policy -> replay baseline.
    r0 = _rebind(serving["revision"])
    assert r0.get("replayed") is True
    rev = r0["agent_binding"]["revision"]

    # TOKEN-only drift (cost unchanged) must skip replay — the token guard fires.
    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 5_000_000)
    r1 = _rebind(rev)
    assert r1.get("replayed") is not True
    assert r1["provider_binding"]["max_tokens"] == 5_000_000
    rev = r1["agent_binding"]["revision"]

    # COST-only drift (token now unchanged at 5M) must skip replay — cost guard.
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 500_000_000)
    r2 = _rebind(rev)
    assert r2.get("replayed") is not True
    assert r2["provider_binding"]["max_cost_microunits"] == 500_000_000


def test_prior_32k_cap_bricked_the_second_concurrent_turn(tmp_path, monkeypatch):
    """Regression oracle: a 32_768 ceiling bricks at ~2 concurrent turns.

    A single ~20 KB system-prompt turn nearly fills 32_768; the SECOND
    simultaneous in-flight turn got ``output_tokens < 1`` and surfaced as "budget
    exhausted". This pins the root cause. It also asserts the AUTHORITY CONTRACT:
    a reservation never exceeds the binding's OWN stored ceiling — admission never
    floors/expands it above the digest-covered value (the healing path is a
    re-bind, see ``test_stale_binding_rebind_advances_generation_and_reflows_ceiling``).
    """
    import tinyassets.provider_serving_binding as serving_binding
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import (
        authorize_served_provider_call,
        reserve_served_provider_budget,
    )

    # Bake the OLD ceilings into the binding at creation time.
    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 32_768)
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 10_000_000)
    universe_dir, _serving, capability, context = _served_context(
        tmp_path,
        path_backed=True,
    )
    try:
        with authorize_served_provider_call(
            tmp_path,
            universe_dir=universe_dir,
            request_carrier=context.provider_request,
            role="writer",
            operation="converse",
        ) as authority:
            assert authority.max_tokens == 32_768
            first = reserve_served_provider_budget(
                tmp_path,
                universe_dir=universe_dir,
                authority=authority,
                requested_output_tokens=4_000,
                estimated_input_tokens=20_000,
            )
            assert first.output_tokens >= 1
            # Authority contract: the reservation is bounded by the binding's OWN
            # stored ceiling, never floored above it at admission.
            assert first.reserved_total_tokens <= 32_768
            with pytest.raises(ProviderAuthorityHeldError, match="budget"):
                reserve_served_provider_budget(
                    tmp_path,
                    universe_dir=universe_dir,
                    authority=authority,
                    requested_output_tokens=4_000,
                    estimated_input_tokens=20_000,
                )
    finally:
        revoke_provider_request(capability)


def test_served_none_max_tokens_reserves_bounded_per_call_not_whole_ceiling(tmp_path):
    """Production converse path leaves max_tokens=None. The router must reserve a
    BOUNDED per-call output, not the entire binding ceiling — else the first turn
    reserves the whole budget and the second concurrent turn bricks regardless of
    ceiling size (Codex 2026-08-22, the real production-path defect)."""
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.router import (
        _SERVED_PER_CALL_MAX_TOKENS,
        ProviderRouter,
    )

    seen: dict[str, object] = {}

    class _Rec(_RecordingProvider):
        async def complete(self, prompt, system, config, *, universe_dir=None):
            seen["max_tokens"] = config.max_tokens
            return await super().complete(
                prompt, system, config, universe_dir=universe_dir
            )

    _, _, capability, context = _served_context(tmp_path)
    router = ProviderRouter({"codex": _Rec("codex")})
    try:
        asyncio.run(
            router.call(
                "writer", "hi", "sys",
                config=ModelConfig(max_tokens=None),
                operation="converse",
                universe_context=context,
            )
        )
    finally:
        revoke_provider_request(capability)
    # The per-call reservation is the bounded default, NOT the multi-million
    # aggregate in-flight ceiling.
    assert seen["max_tokens"] == _SERVED_PER_CALL_MAX_TOKENS


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows admission uses exclusive msvcrt file locks that serialize "
    "even shared readers, so two same-universe calls cannot overlap in-flight "
    "here; concurrent readers (fcntl LOCK_SH) run on the Linux prod/CI host, "
    "which is where the concurrency budget brick actually occurs.",
)
def test_two_concurrent_served_none_max_tokens_calls_both_reach_provider(tmp_path):
    """Two overlapping served turns on the production max_tokens=None path, the
    first held in flight while the second reserves, must BOTH be admitted at
    budget reservation and reach the provider — the concurrency brick the founder
    hit. Pre-fix the first turn reserved the whole ceiling and the second got
    'Provider authority budget is exhausted'.

    Scope: this proves BUDGET ADMISSION under concurrency (this PR's fix). The
    separate process-wide provider worker pool (ProviderRouter, 8 workers) is the
    provider-execution concurrency control and merely queues excess turns; it is
    not what bricked. Each worker claims and revokes its OWN request capability
    in-thread (contextvars are per-thread), so the request-capability ContextVar
    is never contaminated across threads or leaked into later tests.
    """
    import threading

    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.router import ProviderRouter

    both_in = threading.Event()
    lock = threading.Lock()

    class _Blocking(_RecordingProvider):
        async def complete(self, prompt, system, config, *, universe_dir=None):
            with lock:
                self.calls += 1
                n = self.calls
            if n >= 2:
                both_in.set()  # the second turn was admitted while the first waits
            if not both_in.wait(timeout=5):
                raise AssertionError(
                    "second concurrent served turn was never admitted while the "
                    "first was in flight"
                )
            return ProviderResponse(
                text="ok", provider=self.name, model="f",
                family=self.family, latency_ms=1.0,
            )

    # Set up the shared serving binding, then revoke the setup's main-thread
    # capability immediately so only the per-worker in-thread capabilities are
    # ever live (no ContextVar leak into later tests).
    universe_dir, serving, setup_cap, _ = _served_context(tmp_path)
    revoke_provider_request(setup_cap)
    provider = _Blocking("codex")
    router = ProviderRouter({"codex": provider})
    results: dict[str, object] = {}

    def worker(key, request_id):
        cap, ctx = _fresh_served_request(
            universe_dir, serving, request_id=request_id
        )
        try:
            results[key] = asyncio.run(
                router.call(
                    "writer", key, "s", config=ModelConfig(max_tokens=None),
                    operation="converse", universe_context=ctx,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
            results[key] = exc
        finally:
            revoke_provider_request(cap)  # in-thread: no ContextVar contamination

    t1 = threading.Thread(target=worker, args=("a", "req-a"))
    t2 = threading.Thread(target=worker, args=("b", "req-b"))
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    assert provider.calls == 2, "both turns must reach the provider"
    assert not isinstance(results.get("a"), Exception), results.get("a")
    assert not isinstance(results.get("b"), Exception), results.get("b")
    assert results["a"].text == "ok" and results["b"].text == "ok"


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
            ProviderRouter({"codex": provider}).call(
                "writer",
                "must not run",
                "system",
                operation=operation,
                universe_context=context,
            )
        )
    assert provider.calls == 0


def test_served_budget_overrun_delivers_the_reply_and_charges_actual(tmp_path, monkeypatch):
    """A per-call overrun is RECORDED, not withheld (2026-08-22 founder e2e).

    A served provider injects its own large context, so a normal turn routinely
    exceeds the prompt-byte reservation estimate; withholding the reply threw
    away work the founder already generated and paid for on their own
    subscription. Now the overrun settles as 'exceeded' (actual charged) and the
    reply is DELIVERED. The aggregate anti-runaway guard is the invocation
    high-water within the rolling window — see
    ``test_binding_generation_high_water_blocks_runaway_across_requests`` — not
    per-call withholding.
    """
    import tinyassets.provider_serving_binding as serving_binding
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setattr(serving_binding, "_MAX_TOKENS", 16)
    monkeypatch.setattr(serving_binding, "_MAX_COST_MICROUNITS", 1_600)
    _, _, capability, context = _served_context(tmp_path)

    class _OverBudgetProvider(_RecordingProvider):
        async def complete(self, prompt, system, config, *, universe_dir=None):
            self.calls += 1
            return ProviderResponse(
                text="overspent",
                provider=self.name,
                model="fixture",
                family=self.family,
                latency_ms=1.0,
                input_tokens=12,
                output_tokens=8,
                cost_microunits=2_000,
            )

    provider = _OverBudgetProvider("codex")
    router = ProviderRouter({"codex": provider})
    try:
        # Turn 1 overruns its reservation -> reply DELIVERED, actual charged.
        r1 = asyncio.run(
            router.call(
                "writer", "p", "s",
                config=ModelConfig(max_tokens=8),
                operation="converse",
                universe_context=context,
            )
        )
        assert r1.text == "overspent"
        # The settled overrun released its in-flight hold, so turn 2 is admitted
        # fresh and likewise delivered.
        r2 = asyncio.run(
            router.call(
                "writer", "p", "s",
                config=ModelConfig(max_tokens=1),
                operation="converse",
                universe_context=context,
            )
        )
        assert r2.text == "overspent"
    finally:
        revoke_provider_request(capability)
    # Both turns reached the provider AND returned their replies (never withheld).
    assert provider.calls == 2
    # The overrun is RECORDED, not silently ignored: rows settle as 'exceeded'
    # with the actual usage charged (audit / upstream-metered on the founder's
    # own subscription).
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    store = SQLiteProviderWorkAuthorityStore(tmp_path)
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT state, actual_total_tokens FROM "
            "served_provider_budget_reservations ORDER BY created_at"
        ).fetchall()
    assert [r[0] for r in rows] == ["exceeded", "exceeded"]
    assert all(r[1] == 20 for r in rows)  # measured 12 input + 8 output


def test_codex_wrapper_resolution_mounts_real_binary_tree(tmp_path):
    from tinyassets.providers.codex_provider import _codex_sandbox_mounts

    install_root = tmp_path / "codex-install"
    real_codex = install_root / "node_modules" / ".bin" / "codex"
    real_codex.parent.mkdir(parents=True)
    real_codex.write_text("fake executable", encoding="utf-8")
    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "codex"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nCODEX_BIN="{real_codex}"\nexec "$CODEX_BIN" "$@"\n',
        encoding="utf-8",
    )

    mounts = _codex_sandbox_mounts([str(wrapper)])

    assert install_root.resolve() in mounts
    assert wrapper_dir.resolve() in mounts


def test_codex_wrapper_resolution_fails_closed_when_real_tree_is_missing(tmp_path):
    from tinyassets.exceptions import ProviderError
    from tinyassets.providers.codex_provider import _codex_sandbox_mounts

    wrapper = tmp_path / "codex"
    wrapper.write_text(
        '#!/usr/bin/env bash\nCODEX_BIN="/missing/codex-install/codex"\n',
        encoding="utf-8",
    )

    with pytest.raises(ProviderError, match="wrapper's real binary"):
        _codex_sandbox_mounts([str(wrapper)])


@pytest.mark.skipif(
    not (
        os.environ.get("TINYASSETS_REAL_CODEX_TEST_UNIVERSE")
        and os.environ.get("TINYASSETS_REAL_CODEX_TEST_SNAPSHOT")
    ),
    reason=(
        "set TINYASSETS_REAL_CODEX_TEST_UNIVERSE and "
        "TINYASSETS_REAL_CODEX_TEST_SNAPSHOT for the true Codex integration"
    ),
)
def test_true_codex_binary_served_adapter_integration():
    from pathlib import Path

    from tinyassets.providers.codex_provider import CodexProvider

    universe_dir = Path(os.environ["TINYASSETS_REAL_CODEX_TEST_UNIVERSE"])
    snapshot_dir = Path(os.environ["TINYASSETS_REAL_CODEX_TEST_SNAPSHOT"])
    response = asyncio.run(
        CodexProvider().complete(
            "Reply with only: integration-ok",
            "",
            ModelConfig(
                sandbox_workspace=True,
                max_tokens=32,
                credential_snapshot_dir=snapshot_dir,
            ),
            universe_dir=universe_dir,
        )
    )
    assert response.text == "integration-ok"


def test_path_backed_credential_snapshot_seals_inflight_cross_process_rotation(
    tmp_path,
):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.router import ProviderRouter

    universe_dir, serving, capability, context = _served_context(
        tmp_path,
        path_backed=True,
    )
    original_assignment = load_provider_assignment(tmp_path, universe_id="u-owner")

    class _SnapshotProvider(_RecordingProvider):
        def __init__(self, *, pause: bool) -> None:
            super().__init__("codex")
            self.pause = pause
            self.started = asyncio.Event()
            self.resume = asyncio.Event()
            self.snapshot_paths = []

        async def complete(self, prompt, system, config, *, universe_dir=None):
            self.calls += 1
            snapshot = config.credential_snapshot_dir
            assert snapshot is not None
            self.snapshot_paths.append(snapshot)
            auth_file = snapshot / "auth.json"
            before = json.loads(auth_file.read_text(encoding="utf-8"))
            self.started.set()
            if self.pause:
                await self.resume.wait()
            after = json.loads(auth_file.read_text(encoding="utf-8"))
            assert after == before
            return ProviderResponse(
                text=after["tokens"]["access_token"],
                provider=self.name,
                model="fixture",
                family=self.family,
                latency_ms=1.0,
            )

    provider = _SnapshotProvider(pause=True)

    async def _rotate_during_call():
        task = asyncio.create_task(
            ProviderRouter({"codex": provider}).call(
                "writer",
                "hello",
                "system",
                operation="converse",
                universe_context=context,
            )
        )
        started = asyncio.create_task(provider.started.wait())
        await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():  # the call ended before the provider ran: say why, never hang
            started.cancel()
            task.result()
            raise AssertionError("the served call returned before the provider started")
        auth_file = universe_dir / "codex-auth" / "auth.json"
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path(r'"
                + str(auth_file)
                + '\').write_text(\'{"tokens":{"access_token":"rotated"}}\')',
            ],
            check=True,
        )
        provider.resume.set()
        return await task

    try:
        response = asyncio.run(_rotate_during_call())
    finally:
        revoke_provider_request(capability)

    assert response.text == "first"
    assert all(not path.exists() for path in provider.snapshot_paths)

    stale_capability, stale_context = _fresh_served_request(
        universe_dir,
        serving,
        request_id="after-raw-rotation",
    )
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            asyncio.run(
                ProviderRouter({"codex": _SnapshotProvider(pause=False)}).call(
                    "writer",
                    "must revalidate",
                    "system",
                    operation="converse",
                    universe_context=stale_context,
                )
            )
    finally:
        revoke_provider_request(stale_capability)

    rebound = bind_serving_provider(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=serving["agent_binding_id"],
        expected_revision=serving["revision"],
        provider="codex",
    )
    rotated_assignment = load_provider_assignment(tmp_path, universe_id="u-owner")
    assert rotated_assignment.generation > original_assignment.generation
    assert (
        rotated_assignment.credential_reference_generation
        > original_assignment.credential_reference_generation
    )
    rotated_serving = set_serving(
        base_path=tmp_path,
        universe_dir=universe_dir,
        owner_user_id="owner-1",
        universe_id="u-owner",
        agent_binding_id=serving["agent_binding_id"],
        expected_revision=rebound["agent_binding"]["revision"],
        enabled=True,
    )["agent_binding"]
    rotated_capability, rotated_context = _fresh_served_request(
        universe_dir,
        rotated_serving,
        request_id="after-rebind",
    )
    rotated_provider = _SnapshotProvider(pause=False)
    try:
        rotated_response = asyncio.run(
            ProviderRouter({"codex": rotated_provider}).call(
                "writer",
                "hello again",
                "system",
                operation="converse",
                universe_context=rotated_context,
            )
        )
    finally:
        revoke_provider_request(rotated_capability)
    assert rotated_response.text == "rotated"
    assert all(not path.exists() for path in rotated_provider.snapshot_paths)


def test_served_request_budget_allows_reply_and_learning_only(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(tmp_path)
    provider = _RecordingProvider("codex")
    router = ProviderRouter({"codex": provider})
    try:
        for prompt in ("reply", "learning"):
            asyncio.run(
                router.call(
                    "writer",
                    prompt,
                    "system",
                    operation="converse",
                    universe_context=context,
                )
            )
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            asyncio.run(
                router.call(
                    "writer",
                    "third launch",
                    "system",
                    operation="converse",
                    universe_context=context,
                )
            )
    finally:
        revoke_provider_request(capability)
    assert provider.calls == 2


def test_two_consecutive_founder_turns_share_one_binding_without_rebind(tmp_path):
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.router import ProviderRouter

    universe_dir, serving, original_capability, _ = _served_context(tmp_path)
    revoke_provider_request(original_capability)
    provider = _RecordingProvider("codex")
    router = ProviderRouter({"codex": provider})

    for turn in range(2):
        capability, context = _fresh_served_request(
            universe_dir,
            serving,
            request_id=f"turn-{turn}",
        )
        try:
            for prompt in ("reply", "learning"):
                asyncio.run(
                    router.call(
                        "writer",
                        prompt,
                        "system",
                        operation="converse",
                        universe_context=context,
                    )
                )
        finally:
            revoke_provider_request(capability)

    assert provider.calls == 4


def test_binding_generation_high_water_blocks_runaway_across_requests(
    tmp_path,
    monkeypatch,
):
    import tinyassets.provider_serving_binding as serving_binding
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setattr(serving_binding, "_MAX_BINDING_INVOCATIONS", 4)
    universe_dir, serving, original_capability, _ = _served_context(tmp_path)
    revoke_provider_request(original_capability)
    provider = _RecordingProvider("codex")
    router = ProviderRouter({"codex": provider})

    for turn in range(2):
        capability, context = _fresh_served_request(
            universe_dir,
            serving,
            request_id=f"bounded-turn-{turn}",
        )
        try:
            for prompt in ("reply", "learning"):
                asyncio.run(
                    router.call(
                        "writer",
                        prompt,
                        "system",
                        operation="converse",
                        universe_context=context,
                    )
                )
        finally:
            revoke_provider_request(capability)

    runaway_capability, runaway_context = _fresh_served_request(
        universe_dir,
        serving,
        request_id="runaway",
    )
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="budget"):
            asyncio.run(
                router.call(
                    "writer",
                    "fifth launch",
                    "system",
                    operation="converse",
                    universe_context=runaway_context,
                )
            )
    finally:
        revoke_provider_request(runaway_capability)
    assert provider.calls == 4


def test_runaway_guard_ages_out_and_never_permanently_bricks(tmp_path, monkeypatch):
    """The runaway guard is a ROLLING WINDOW: it blocks a burst but ages out.

    The invocation ceiling counts only rows created within `_RUNAWAY_WINDOW_S`,
    so a burst is held while recent (runaway prevention) but once those
    invocations fall outside the window the binding serves again — it never
    permanently bricks a 24/7 binding (Codex reject #3). Contrast the old
    lifetime count, which stayed tripped forever.
    """
    import sqlite3

    import tinyassets.provider_serving_binding as serving_binding
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.storage import db_path

    monkeypatch.setattr(serving_binding, "_MAX_BINDING_INVOCATIONS", 4)
    universe_dir, serving, original_capability, _ = _served_context(tmp_path)
    revoke_provider_request(original_capability)
    provider = _RecordingProvider("codex")
    router = ProviderRouter({"codex": provider})

    # Fill the window to the cap (2 turns x 2 calls = 4).
    for turn in range(2):
        capability, context = _fresh_served_request(
            universe_dir, serving, request_id=f"fill-{turn}"
        )
        try:
            for prompt in ("reply", "learning"):
                asyncio.run(
                    router.call(
                        "writer",
                        prompt,
                        "system",
                        operation="converse",
                        universe_context=context,
                    )
                )
        finally:
            revoke_provider_request(capability)
    assert provider.calls == 4

    # 5th call is blocked while all 4 invocations are inside the window.
    cap5, ctx5 = _fresh_served_request(universe_dir, serving, request_id="blocked")
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="budget"):
            asyncio.run(
                router.call(
                    "writer",
                    "fifth",
                    "system",
                    operation="converse",
                    universe_context=ctx5,
                )
            )
    finally:
        revoke_provider_request(cap5)
    assert provider.calls == 4

    # Age the recorded invocations out of the rolling window.
    conn = sqlite3.connect(db_path(universe_dir.parent))
    try:
        conn.execute(
            "UPDATE served_provider_budget_reservations SET created_at = created_at - ?",
            (2 * 3600.0,),
        )
        conn.commit()
    finally:
        conn.close()

    # The binding serves again — the guard did not permanently brick it.
    cap6, ctx6 = _fresh_served_request(universe_dir, serving, request_id="recovered")
    try:
        asyncio.run(
            router.call(
                "writer",
                "sixth",
                "system",
                operation="converse",
                universe_context=ctx6,
            )
        )
    finally:
        revoke_provider_request(cap6)
    assert provider.calls == 5


def test_claude_serving_held_by_default_without_optin(tmp_path, monkeypatch):
    """claude-code serving stays HELD unless the host explicitly opts in.

    The OpenSpec design forbids silently bypassing the role-completeness hold
    merely because converse asks only for writer (Codex reject #2). The default
    (no flag) must therefore refuse claude-code serving.
    """
    from tinyassets.provider_serving_binding import bind_serving_provider

    monkeypatch.delenv("TINYASSETS_ALLOW_CLAUDE_SERVING", raising=False)
    with pytest.raises(PermissionError, match="held by default"):
        bind_serving_provider(
            base_path=str(tmp_path),
            universe_dir=str(tmp_path),
            owner_user_id="owner-1",
            universe_id="u-owner",
            agent_binding_id="binding-1",
            expected_revision=1,
            provider="claude-code",
        )


def test_claude_serving_optin_clears_the_hold(tmp_path, monkeypatch):
    """With the explicit opt-in AND writer-only serving scope, the hold clears.

    Proven by getting PAST the claude hold to the next validation (missing
    owner -> ValueError, NOT the PermissionError hold).
    """
    from tinyassets.provider_serving_binding import bind_serving_provider

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    with pytest.raises(ValueError):
        bind_serving_provider(
            base_path=str(tmp_path),
            universe_dir=str(tmp_path),
            owner_user_id="",  # cleared the claude hold; fails later on owner
            universe_id="u-owner",
            agent_binding_id="binding-1",
            expected_revision=1,
            provider="claude-code",
        )


def _claude_authority(tmp_path):
    from tinyassets.provider_assignment import ServedProviderAuthority

    return ServedProviderAuthority(
        authority_kind="subscription_snapshot",
        provider="claude-code",
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
        credential_service="claude-code",
        credential_snapshot_dir=tmp_path,
        request_capability=object(),
    )


def test_reserve_holds_claude_serving_authority_without_optin(tmp_path, monkeypatch):
    """Serve-time re-check: a persisted claude-code serving authority is HELD on
    every served call unless the host opts in — closing the grandfathered-binding
    gap where a binding created while the flag was on keeps serving after it is
    cleared (Codex re-review #1). The check returns before any DB access.
    """
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import reserve_served_provider_budget

    monkeypatch.delenv("TINYASSETS_ALLOW_CLAUDE_SERVING", raising=False)
    with pytest.raises(ProviderAuthorityHeldError, match="claude-code serving is held"):
        reserve_served_provider_budget(
            str(tmp_path),
            universe_dir=str(tmp_path),
            authority=_claude_authority(tmp_path),
            requested_output_tokens=10,
            estimated_input_tokens=10,
        )


def test_reserve_passes_claude_hold_with_optin(tmp_path, monkeypatch):
    """With the opt-in the serve-time claude hold clears; the call proceeds past
    it and fails on the (absent) binding/custody, NOT on the claude hold."""
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.provider_assignment import reserve_served_provider_budget

    monkeypatch.setenv("TINYASSETS_ALLOW_CLAUDE_SERVING", "1")
    with pytest.raises(ProviderAuthorityHeldError) as exc:
        reserve_served_provider_budget(
            str(tmp_path),
            universe_dir=str(tmp_path),
            authority=_claude_authority(tmp_path),
            requested_output_tokens=10,
            estimated_input_tokens=10,
        )
    assert "claude-code serving is held" not in str(exc.value)


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

    authority = _claude_authority(tmp_path)  # provider irrelevant to finalize
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


def test_real_input_above_prompt_estimate_is_not_withheld(tmp_path):
    """The founder-facing fix (2026-08-22): a served provider injects its own
    context (codex mounts a workspace + tool schemas), so its ACTUAL input
    tokens far exceed the byte length of our prompt and thus the reservation
    estimate. Such a turn settles as 'exceeded' but its reply is DELIVERED, not
    withheld — the founder already generated and paid for it on their own
    subscription. (The reservation is still sized estimate+output; the change is
    that an overrun no longer withholds the delivered reply.)"""
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(tmp_path)

    class _BigContextProvider(_RecordingProvider):
        async def complete(self, prompt, system, config, *, universe_dir=None):
            self.calls += 1
            # tiny prompt bytes, but codex-style real input of ~12k tokens
            return ProviderResponse(
                text="here is your answer",
                provider=self.name,
                model="fixture",
                family=self.family,
                latency_ms=1.0,
                input_tokens=12_000,
                output_tokens=200,
                cost_microunits=1_220_000,
            )

    provider = _BigContextProvider("codex")
    router = ProviderRouter({"codex": provider})
    try:
        resp = asyncio.run(
            router.call(
                "writer",
                "hi",
                "s",
                config=ModelConfig(max_tokens=512),
                operation="converse",
                universe_context=context,
            )
        )
        assert resp.text == "here is your answer"  # delivered, not withheld
        assert provider.calls == 1
    finally:
        revoke_provider_request(capability)


def test_a_provider_error_is_logged_without_its_secrets(tmp_path, caplog):
    """The router's cooldown log line is shared daemon output: a provider's
    error text is scrubbed before it is written (cross-family review
    2026-10-06 reproduced `refused initialize: bad token sk-...` there)."""
    import logging

    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import AllProvidersExhaustedError, ProviderError
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(tmp_path)
    secret = "sk-SyntheticReviewSecret12345"

    class RejectingProvider(_RecordingProvider):
        async def complete(self, *args, **kwargs):
            raise ProviderError(f"codex app-server refused initialize: bad token {secret}")

    router = ProviderRouter(providers={"codex": RejectingProvider("codex")})
    try:
        with caplog.at_level(logging.WARNING, logger="tinyassets.providers.router"):
            with pytest.raises(AllProvidersExhaustedError):
                asyncio.run(router.call("writer", "hello", "system", universe_context=context,
                                        operation="converse"))
    finally:
        revoke_provider_request(capability)
    assert "refused initialize" in caplog.text
    assert secret not in caplog.text
