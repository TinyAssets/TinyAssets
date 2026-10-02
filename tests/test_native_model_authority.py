"""Accepted native members use real serving authority, without HTTP metadata.

Serving activation is seeded internally until the public readiness integration
lands. These cases do not claim that owner-facing activation already works.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_provider_served_router import (
    _fresh_served_request,
    _RecordingProvider,
    _served_context,
)
from tinyassets.auth.middleware import revoke_provider_request
from tinyassets.custom_agents import set_binding_serving_in_transaction
from tinyassets.exceptions import AllProvidersExhaustedError, ProviderAuthorityHeldError
from tinyassets.provider_assignment import authorize_served_provider_call
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import bind_serving_provider
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


@pytest.fixture
def native(tmp_path, monkeypatch, request):
    universe, agent, initial_capability, _ = _served_context(tmp_path)
    revoke_provider_request(initial_capability)
    bound = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe, owner_user_id="owner-1",
        universe_id=universe.name, agent_binding_id=agent["agent_binding_id"],
        expected_revision=agent["revision"], provider="codex",
        model_access={"codex": (ModelAccess("discovered")
                                if getattr(request, "param", None) == "discovered"
                                else ModelAccess("explicit", ("", "future-native-model")))},
    )
    with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent = set_binding_serving_in_transaction(
            conn, universe_id=universe.name, binding_id=agent["agent_binding_id"],
            expected_revision=bound["agent_binding"]["revision"],
            owner_user_id="owner-1", enabled=True,
        )
        conn.commit()
    capability, context = _fresh_served_request(universe, agent, request_id="native-default")
    context = replace(
        context, model_selection=ModelRef("codex", ""),
        config=replace(context.config, preferred_writer="claude-code"),
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("native default must not fetch HTTP discovery")

    monkeypatch.setattr(
        "tinyassets.providers.discovery_snapshot.refresh_model_discovery", unexpected,
    )
    monkeypatch.setattr(
        "tinyassets.providers.discovery_snapshot.refresh_model_discovery_async", unexpected,
    )
    provider = _RecordingProvider("codex")
    other = _RecordingProvider("claude-code")
    state = SimpleNamespace(
        base=tmp_path, universe=universe, context=context, capability=capability, agent=agent,
        provider=provider, other=other,
        router=ProviderRouter({"codex": provider, "claude-code": other}),
    )
    try:
        yield state
    finally:
        revoke_provider_request(capability)


def _call(native, context=None):
    return asyncio.run(native.router.call(
        "writer", "hello", "system", operation="converse",
        universe_context=context or native.context,
    ))


def test_native_default_uses_member_not_structural_anchor(native):
    result = _call(native)
    assert result.provider == "codex"
    assert native.provider.calls == 1 and native.other.calls == 0


def test_native_default_sync_authority_keeps_owned_snapshot(native):
    with authorize_served_provider_call(
        native.base, universe_dir=native.universe,
        request_carrier=native.context.provider_request, role="writer", operation="converse",
        model_selection=native.context.model_selection,
    ) as authority:
        snapshot = authority.credential_snapshot_dir
        assert authority.authority_kind == "subscription_snapshot"
        assert authority.provider == "codex" and authority.selected_model is None
        assert snapshot is not None and snapshot.is_dir()
    assert not snapshot.exists()


@pytest.mark.parametrize("allowed", [[], ["claude-code"]])
def test_native_selection_cannot_expand_explicit_allowlist(native, allowed):
    context = replace(native.context, config=replace(
        native.context.config, allowed_providers=allowed,
    ))
    # The universe allowlist is authority: it lives in the platform record
    # (tinyassets.provider_authority), which the router re-reads.
    from tinyassets import provider_authority

    current = provider_authority.authority_for(context.universe_dir, None)
    provider_authority.write_record(context.universe_dir, {**current, "allowed_providers": allowed})
    with pytest.raises((ProviderAuthorityHeldError, AllProvidersExhaustedError)):
        _call(native, context)
    assert native.provider.calls == 0 and native.other.calls == 0


def test_native_explicit_model_is_not_silently_treated_as_default(native):
    context = replace(native.context, model_selection=ModelRef("codex", "unverified-model"))
    with pytest.raises(ProviderAuthorityHeldError):
        _call(native, context)
    assert native.provider.calls == 0


def test_native_default_still_requires_current_owned_credential(native):
    from tinyassets.credential_vault import write_credential_vault

    write_credential_vault(
        native.universe, [], owner_user_id="owner-1", universe_id=native.universe.name,
    )
    with pytest.raises(ProviderAuthorityHeldError):
        _call(native)
    assert native.provider.calls == 0 and native.other.calls == 0


def test_accepted_native_model_reaches_request_local_config(native, monkeypatch):
    captured = []
    original = native.provider.complete

    async def record(prompt, system, config, **kwargs):
        captured.append(config)
        return await original(prompt, system, config, **kwargs)

    monkeypatch.setattr(native.provider, "complete", record)
    context = replace(native.context, model_selection=ModelRef("codex", "future-native-model"))
    assert _call(native, context).provider == "codex"
    assert captured[0].native_model_id == "future-native-model"
    assert captured[0].selected_model is None


def test_caller_native_model_cannot_replace_authorized_default(native, monkeypatch):
    from tinyassets.providers.base import ModelConfig

    captured = []
    original = native.provider.complete

    async def record(prompt, system, config, **kwargs):
        captured.append(config)
        return await original(prompt, system, config, **kwargs)

    monkeypatch.setattr(native.provider, "complete", record)
    asyncio.run(native.router.call(
        "writer", "hello", "", ModelConfig(native_model_id="injected-model"),
        operation="converse", universe_context=native.context,
    ))
    assert captured[0].native_model_id == ""


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
@pytest.mark.parametrize("access", [
    ModelAccess(), ModelAccess("discovered"), ModelAccess("explicit", ("",)),
])
def test_native_default_scope_does_not_need_http_model_facts(provider, access):
    from tinyassets.providers.model_selection import _native_default

    assert _native_default(provider, "", access)
    with pytest.raises(PermissionError):
        _native_default(provider, "unverified-model", access)
    with pytest.raises(PermissionError):
        _native_default(provider, "", ModelAccess("explicit", ("only-specific",)))
