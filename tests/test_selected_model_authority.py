"""Current assignment -> fresh catalogue -> real HTTP executor, synthetic network.

No public activation or live account claim: fixtures establish the internal
serving intent because general v2 readiness is deliberately still gated.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from types import SimpleNamespace

import pytest

from tests import test_discovery_snapshot as snapshot_tests
from tinyassets.auth import middleware as auth
from tinyassets.config import load_universe_config
from tinyassets.custom_agents import (
    create_binding,
    publish_definition,
    set_binding_serving_in_transaction,
)
from tinyassets.exceptions import ProviderAuthorityHeldError, ProviderUnavailableError
from tinyassets.provider_assignment import (
    authorize_served_provider_call,
    load_provider_assignment,
    provider_assignment_admission,
)
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import bind_serving_provider
from tinyassets.providers import discovery_snapshot as snapshots
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig, UniverseContext
from tinyassets.providers.discovery_protocols import discovery_protocol
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.model_selection import SelectedModel, prepare_selected_model
from tinyassets.providers.router import ProviderRouter
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

rig = snapshot_tests.rig
reader = snapshot_tests.reader
MODEL = "new-company/new-model"


@pytest.fixture
def served(rig, reader, monkeypatch):
    definition = publish_definition(
        rig.base,
        author_id="owner",
        payload={
            "schema_version": 1,
            "name": "Selected",
            "description": "fixture",
            "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        rig.base,
        universe_id="u-models",
        definition_id=definition["agent_definition_id"],
        created_by="owner",
        payload={"schema_version": 1, "name": "Selected", "role": "writer"},
    )
    connected = bind_serving_provider(
        base_path=rig.base,
        universe_dir=rig.base / "u-models",
        owner_user_id="owner",
        universe_id="u-models",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=agent["revision"],
        provider=rig.definition.id,
        model_access={rig.definition.id: ModelAccess("discovered")},
    )
    with SQLiteProviderWorkAuthorityStore(rig.base).connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent = set_binding_serving_in_transaction(
            conn,
            universe_id="u-models",
            binding_id=agent["agent_binding_id"],
            expected_revision=connected["agent_binding"]["revision"],
            owner_user_id="owner",
            enabled=True,
        )
        conn.commit()
    reserve = auth.reserve_provider_request(
        principal_id="owner",
        session_id="selection-session",
        request_id="selection-request",
        tool_name="converse",
    )
    capability = auth.claim_provider_request(reserve, tool_name="converse")
    carrier = auth.mint_provider_request_carrier(
        universe_id="u-models",
        agent_binding_id=agent["agent_binding_id"],
        binding_revision=agent["revision"],
        operation="converse",
    )
    provider = f"api_key_http:{rig.definition.id}"
    context = UniverseContext(
        universe_dir=rig.base / "u-models",
        config=load_universe_config(rig.base / "u-models"),
        provider_request=carrier,
        model_selection=ModelRef(provider, MODEL),
    )
    wire = []

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            wire.append((verb, document))
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "model": "actual-answer-model",
                        "choices": [{"message": {"content": "selected answer"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                    }
                ),
            }

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", lambda *args, **kwargs: Proxy())
    result = SimpleNamespace(
        rig=rig,
        context=context,
        capability=capability,
        wire=wire,
        agent=agent,
        router=ProviderRouter({}),
    )
    yield result
    auth.revoke_provider_request(capability)


def _call(served, *, config=None, context=None):
    return asyncio.run(
        served.router.call(
            "writer",
            "hello",
            "be terse",
            config,
            operation="converse",
            universe_context=context or served.context,
        )
    )


def _authorize(served, selection=None):
    return authorize_served_provider_call(
        served.rig.base,
        universe_dir=served.context.universe_dir,
        request_carrier=served.context.provider_request,
        role="writer",
        operation="converse",
        model_selection=selection or served.context.model_selection,
    )


def test_selected_model_flows_through_real_router_and_http_encoder(served):
    before = load_provider_assignment(served.rig.base, universe_id="u-models")
    injected = SelectedModel("other", "injected", "invalid", (), "fake", 1)
    response = _call(served, config=ModelConfig(selected_model=injected))
    assert response.text == "selected answer" and response.model == "actual-answer-model"
    assert response.provider == served.context.model_selection.connection_id
    verb, wire = served.wire[0]
    assert verb == "POST" and wire["url"] == "https://owned.example/custom/chat"
    assert wire["body"]["model"] == MODEL
    assert wire["body"]["provider"] == {
        "max_price": {"prompt": "0", "completion": "0", "image": "0", "request": "0"},
        "require_parameters": True,
    }
    assert served.rig.definition.model == "legacy-fixed"
    assert load_provider_assignment(served.rig.base, universe_id="u-models") == before
    assert auth._active_provider_request(served.capability)["invocations"] == 1


def test_refresh_adds_new_model_without_new_assignment_or_definition(served, reader, monkeypatch):
    _call(served)
    before = load_provider_assignment(served.rig.base, universe_id="u-models")
    original = reader[1]

    def new_catalogue(**kwargs):
        if "models/user" in kwargs["url"]:
            return {"data": [snapshot_tests._model("future-vendor/future-model")]}
        return original(**kwargs)

    monkeypatch.setattr(snapshots, "read_http_discovery_document", new_catalogue)
    context = replace(
        served.context,
        model_selection=ModelRef(
            served.context.model_selection.connection_id,
            "future-vendor/future-model",
        ),
    )
    _call(served, context=context)
    assert served.wire[-1][1]["body"]["model"] == "future-vendor/future-model"
    assert load_provider_assignment(served.rig.base, universe_id="u-models") == before


@pytest.mark.parametrize("selection", [ModelRef("api_key_http:unaccepted", MODEL), object()])
def test_unaccepted_selection_refused_before_discovery(served, reader, selection):
    reader[0].clear()
    with pytest.raises(ProviderAuthorityHeldError):
        with _authorize(served, selection):
            pytest.fail("must not authorize")
    assert reader[0] == [] and served.wire == []


@pytest.mark.parametrize("change", ["grant", "profile", "expired", "clock"])
def test_prelaunch_source_recheck_refuses_changes(served, monkeypatch, change):
    with _authorize(served) as authority:
        if change == "grant":
            served.rig.ledger.revoke_grant("grant-models")
        elif change == "profile":
            served.rig.publish(enabled=False)
        else:
            shift = timedelta(minutes=6) if change == "expired" else -timedelta(seconds=1)
            monkeypatch.setattr(snapshots, "_now", lambda: snapshot_tests.NOW + shift)
        with pytest.raises(ProviderUnavailableError):
            authority.before_provider_launch()
    assert served.wire == []
    assert auth._active_provider_request(served.capability)["invocations"] == 0


@pytest.mark.parametrize("change", ["paid", "unknown_price", "no_context", "no_text", "missing"])
def test_fresh_model_must_fit_permitted_cost_and_capabilities(served, monkeypatch, change):
    row = snapshot_tests._model()
    if change == "paid":
        row["pricing"]["prompt"] = "0.000001"
    elif change == "unknown_price":
        row["pricing"]["unfamiliar_fee"] = "0"
    elif change == "no_context":
        row.pop("context_length")
    elif change == "no_text":
        row["architecture"]["output_modalities"] = ["image"]
    else:
        row["id"] = "another-model"
    monkeypatch.setattr(snapshots, "read_http_discovery_document", lambda **kwargs: {"data": [row]})
    with pytest.raises(ProviderAuthorityHeldError):
        _call(served)
    assert served.wire == []
    assert auth._active_provider_request(served.capability)["invocations"] == 0


@pytest.mark.parametrize(
    "extra,eligible",
    [
        ({}, True),
        ({"input_cache_read": "0"}, True),
        ({"web_search": "0.01"}, False),
        ({"overrides": [{"min_prompt_tokens": 1000, "prompt": "0.000001"}]}, False),
        ({"unknown_component": "0"}, False),
    ],
)
def test_real_selected_dispatch_uses_optional_pricing(served, monkeypatch, extra, eligible):
    row = snapshot_tests._model()
    row["pricing"] = {"prompt": "0", "completion": "0", **extra}
    monkeypatch.setattr(snapshots, "read_http_discovery_document", lambda **kwargs: {"data": [row]})
    if eligible:
        response = _call(served)
        assert response.text == "selected answer"
        assert served.wire[0][1]["body"]["provider"]["max_price"]["request"] == "0"
        assert auth._active_provider_request(served.capability)["invocations"] == 1
    else:
        with pytest.raises(ProviderAuthorityHeldError):
            _call(served)
        assert served.wire == []
        assert auth._active_provider_request(served.capability)["invocations"] == 0


def test_text_execution_is_not_misrepresented_as_full_agent_tools(served):
    with pytest.raises(PermissionError, match="tool execution"):
        _call(served, config=ModelConfig(engine_mcp_enabled=True))
    assert served.wire == []
    assert auth._active_provider_request(served.capability)["invocations"] == 0


def test_model_context_limit_refuses_without_truncation_or_launch(served):
    with pytest.raises(PermissionError, match="inference context"):
        _call(served, config=ModelConfig(max_tokens=32000))
    assert served.wire == []


def test_absent_selection_keeps_v2_legacy_execution_hold(served):
    with pytest.raises(ProviderAuthorityHeldError):
        _call(served, context=replace(served.context, model_selection=None))
    assert served.wire == []


@pytest.mark.parametrize(
    "access",
    [
        ModelAccess(),
        ModelAccess("explicit", ("not-accepted",)),
        ModelAccess("explicit", ("",)),
        ModelAccess("discovered", cost_caps=(("unknown", 0),)),
    ],
)
def test_scope_and_unenforceable_costs_are_refused(rig, reader, access):
    with pytest.raises(PermissionError):
        prepare_selected_model(
            base_path=rig.base,
            owner_user_id="owner",
            universe_id="u-models",
            provider=f"api_key_http:{rig.definition.id}",
            model_id=MODEL,
            access=access,
        )


def test_explicit_scope_can_select_exact_accepted_model(rig, reader):
    selected, recheck = prepare_selected_model(
        base_path=rig.base,
        owner_user_id="owner",
        universe_id="u-models",
        provider=f"api_key_http:{rig.definition.id}",
        model_id=MODEL,
        access=ModelAccess("explicit", (MODEL,)),
    )
    assert selected.model_id == MODEL
    recheck()


@pytest.mark.parametrize("micros", [0, 1, 100000, 1234567, 999999999999999999])
def test_wire_price_ceiling_never_rounds_up(micros):
    contract = discovery_protocol("openrouter_user_models_v1")
    body = {"model": MODEL, "messages": []}
    caps = tuple((name, micros) for name in sorted(contract.price_components))
    bounded = contract.constrain_inference(body, caps)
    for value in bounded["provider"]["max_price"].values():
        assert isinstance(value, str)
        assert Fraction(value) == Fraction(micros, 10**6)
    assert "provider" not in body


@pytest.mark.parametrize("field", ["plugins", "models", "provider", "tools", "transforms"])
def test_price_encoder_does_not_merge_unbounded_features(field):
    contract = discovery_protocol("openrouter_user_models_v1")
    with pytest.raises(ValueError):
        contract.constrain_inference(
            {"model": MODEL, "messages": [], field: []},
            tuple((name, 0) for name in contract.price_components),
        )


def test_model_config_keeps_legacy_positional_timeout():
    assert ModelConfig(123).timeout == 123


def _record_allowlist(universe_dir, allowlist):
    from tinyassets import provider_authority

    current = provider_authority.authority_for(universe_dir, None)
    provider_authority.write_record(universe_dir, {**current, "allowed_providers": allowlist})


@pytest.mark.parametrize("allowlist", [[], ["another-provider"]])
def test_explicit_universe_allowlist_still_narrows_selected_authority(served, allowlist):
    from tinyassets.exceptions import AllProvidersExhaustedError

    context = replace(
        served.context,
        config=replace(
            served.context.config,
            allowed_providers=allowlist,
        ),
    )
    # The universe allowlist is authority: it lives in the platform record
    # (tinyassets.provider_authority), which the router re-reads.
    _record_allowlist(context.universe_dir, allowlist)
    with pytest.raises((ProviderAuthorityHeldError, AllProvidersExhaustedError)):
        _call(served, context=context)
    assert served.wire == []


def test_revoked_primary_does_not_prevent_independent_accepted_member(served, monkeypatch):
    from tests.test_model_discovery_capability import DESCRIPTOR
    from tinyassets.providers.definition import register_definition

    rig = served.rig
    endpoint_json, _ = rig.ledger.policy_json("conn-models")
    rig.ledger.create_connection(
        connection_id="independent",
        owner_user_id="owner",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("GET", "POST"),
        provider="http",
        destination="compute:independent",
        credential_ref="vault://http/other",
        allowed_endpoints=json.loads(endpoint_json),
    )
    grant = rig.ledger.grant_connection(
        grant_id="independent-grant",
        connection_id="independent",
        owner_user_id="owner",
        universe_id="u-models",
    )
    other = register_definition(
        universe_id="u-models",
        owner_user_id="owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="unchanged-other-pin",
        ref=grant.grant_id,
    )
    rig.ledger.configure_capability(
        connection_id="independent",
        capability_kind="model_discovery",
        descriptor=DESCRIPTOR,
        enabled=True,
        expected_grant=grant,
    )
    connected = bind_serving_provider(
        base_path=rig.base,
        universe_dir=served.context.universe_dir,
        owner_user_id="owner",
        universe_id="u-models",
        agent_binding_id=served.agent["agent_binding_id"],
        expected_revision=served.agent["revision"],
        provider=rig.definition.id,
        model_access={
            rig.definition.id: ModelAccess("discovered"),
            other.id: ModelAccess("discovered"),
        },
    )
    with SQLiteProviderWorkAuthorityStore(rig.base).connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent = set_binding_serving_in_transaction(
            conn,
            universe_id="u-models",
            binding_id=served.agent["agent_binding_id"],
            expected_revision=connected["agent_binding"]["revision"],
            owner_user_id="owner",
            enabled=True,
        )
        conn.commit()
    carrier = auth.mint_provider_request_carrier(
        universe_id="u-models",
        agent_binding_id=agent["agent_binding_id"],
        binding_revision=agent["revision"],
        operation="converse",
    )
    rig.ledger.revoke_grant("grant-models")
    reads = []

    def read(**kwargs):
        reads.append(kwargs["definition"].ref)
        assert kwargs["definition"].ref == "independent-grant"
        return {"data": [snapshot_tests._model()]}

    monkeypatch.setattr(snapshots, "read_http_discovery_document", read)
    context = replace(
        served.context,
        provider_request=carrier,
        config=load_universe_config(served.context.universe_dir),
        model_selection=ModelRef(f"api_key_http:{other.id}", MODEL),
    )
    response = _call(served, context=context)
    assert response.provider == f"api_key_http:{other.id}" and reads
    assert len(served.wire) == 1
    with SQLiteProviderWorkAuthorityStore(rig.base).connection() as conn:
        row = conn.execute(
            "SELECT binding_id, state FROM served_provider_budget_reservations"
        ).fetchone()
    assignment = load_provider_assignment(rig.base, universe_id="u-models")
    member = next(m for m in assignment.candidates if m.provider == response.provider)
    assert tuple(row) == (member.binding_id, "succeeded")


def test_without_selected_authority_router_discards_configuration_override(tmp_path):
    from tests.test_provider_served_router import _RecordingProvider, _served_context

    _, _, capability, context = _served_context(tmp_path)
    seen = []

    class Recording(_RecordingProvider):
        async def complete(self, prompt, system, config, *, universe_dir=None):
            seen.append(config.selected_model)
            return await super().complete(prompt, system, config, universe_dir=universe_dir)

    try:
        asyncio.run(
            ProviderRouter({"codex": Recording("codex")}).call(
                "writer",
                "hello",
                "system",
                ModelConfig(selected_model=object()),
                operation="converse",
                universe_context=context,
            )
        )
    finally:
        auth.revoke_provider_request(capability)
    assert seen == [None]


def test_request_revocation_refuses_before_discovery(served, reader):
    auth.revoke_provider_request(served.capability)
    reader[0].clear()
    with pytest.raises(ProviderAuthorityHeldError):
        _call(served)
    assert reader[0] == [] and served.wire == []


@pytest.fixture
def blocked_discovery(served, reader, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        if "models/user" in kwargs["url"]:
            entered.set()
            assert release.wait(5), "test failed to release discovery"
        return reader[1](**kwargs)

    monkeypatch.setattr(snapshots, "read_http_discovery_document", read)
    yield entered, release, calls
    release.set()


def _async_call(served):
    return served.router.call(
        "writer", "hello", "system", operation="converse", universe_context=served.context
    )


def test_discovery_yields_event_loop_and_assignment_admission(served, blocked_discovery):
    entered, release, _ = blocked_discovery
    thread_ids = []

    def assignment_writer():
        with provider_assignment_admission().exclusive(served.context.universe_dir):
            # A different thread can take the real fence while discovery waits.
            thread_ids.append(threading.get_ident())
            with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()

    async def scenario():
        task = asyncio.create_task(_async_call(served))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert not task.done() and not release.is_set()
            await asyncio.wait_for(asyncio.to_thread(assignment_writer), 2)
            assert thread_ids and thread_ids[0] != threading.get_ident()
            assert served.wire == []
            release.set()
            assert (await asyncio.wait_for(task, 5)).text == "selected answer"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["request", "agent", "assignment", "grant", "profile"])
def test_async_discovery_rechecks_changes_after_releasing_admission(
    served, blocked_discovery, change
):
    entered, release, _ = blocked_discovery

    async def scenario():
        task = asyncio.create_task(_async_call(served))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert not task.done()
            # This is the same thread as router ingress: a retained shared
            # admission would raise the non-reentrant guard immediately.
            with provider_assignment_admission().exclusive(served.context.universe_dir):
                if change == "request":
                    # Revoke from an independent lease-expiry context, not the
                    # copied asyncio context owning a different reset token.
                    contextvars.Context().run(auth.revoke_provider_request, served.capability)
                elif change == "grant":
                    served.rig.ledger.revoke_grant("grant-models")
                elif change == "profile":
                    served.rig.publish(enabled=False)
                else:
                    sql = (
                        "UPDATE agent_bindings SET revision = revision + 1 WHERE universe_id = ?"
                        if change == "agent"
                        else "UPDATE provider_assignments SET generation = generation + 1 "
                        "WHERE universe_id = ?"
                    )
                    with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
                        conn.execute(sql, ("u-models",))
                        conn.commit()
            release.set()
            with pytest.raises(ProviderAuthorityHeldError):
                await task
            assert served.wire == []
            if change != "request":
                assert auth._active_provider_request(served.capability)["invocations"] == 0
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_router_does_not_launch_or_duplicate_discovery(served, blocked_discovery):
    entered, release, calls = blocked_discovery

    async def scenario():
        first = asyncio.create_task(_async_call(served))
        second = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert not first.done()
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert served.wire == []
            assert auth._active_provider_request(served.capability)["invocations"] == 0
            second = asyncio.create_task(_async_call(served))
            await asyncio.sleep(0)
            assert not second.done() and len(calls) == 1
            release.set()
            await asyncio.wait_for(second, 5)
            assert len(served.wire) == 1
            assert auth._active_provider_request(served.capability)["invocations"] == 1
            assert len([c for c in calls if "models/user" in c["url"]]) == 1
            assert not snapshots._INFLIGHT
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("selection", [ModelRef("api_key_http:unaccepted", MODEL), object()])
def test_async_unaccepted_selection_refused_before_discovery(served, reader, selection):
    reader[0].clear()
    with pytest.raises(ProviderAuthorityHeldError):
        _call(served, context=replace(served.context, model_selection=selection))
    assert reader[0] == [] and served.wire == []


@pytest.mark.parametrize("change", ["profile", "agent"])
def test_router_rechecks_after_waiting_for_a_slot_without_charging_launch(
    served, monkeypatch, change
):
    from contextlib import asynccontextmanager

    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers import router as routing

    @asynccontextmanager
    async def slot(**kwargs):
        if change == "profile":
            served.rig.publish(enabled=False)
        else:
            with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
                conn.execute(
                    "UPDATE agent_bindings SET status = 'configured' WHERE universe_id = ?",
                    ("u-models",),
                )
                conn.commit()
        yield

    monkeypatch.setattr(routing, "_provider_slot", slot)
    with pytest.raises((AllProvidersExhaustedError, PermissionError)):
        _call(served)
    assert served.wire == []
    assert auth._active_provider_request(served.capability)["invocations"] == 0
    with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
        rows = conn.execute(
            "SELECT state, actual_total_tokens, actual_cost_microunits "
            "FROM served_provider_budget_reservations"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("succeeded", 0, 0)]


def test_agent_change_during_discovery_is_refused(served, reader, monkeypatch):
    original = reader[1]

    def read(**kwargs):
        with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
            conn.execute(
                "UPDATE agent_bindings SET status = 'configured' WHERE universe_id = ?",
                ("u-models",),
            )
            conn.commit()
        return original(**kwargs)

    monkeypatch.setattr(snapshots, "read_http_discovery_document", read)
    with pytest.raises(ProviderAuthorityHeldError):
        _call(served)
    assert served.wire == []


def test_paid_selection_requires_complete_accepted_caps(rig, reader, monkeypatch):
    row = snapshot_tests._model()
    row["pricing"]["prompt"] = "0.000000000001"
    monkeypatch.setattr(snapshots, "read_http_discovery_document", lambda **kwargs: {"data": [row]})
    contract = discovery_protocol("openrouter_user_models_v1")
    caps = tuple(
        (name, 1 if name == "input_million_tokens_usd" else 0)
        for name in sorted(contract.price_components)
    )
    selected, _ = prepare_selected_model(
        base_path=rig.base,
        owner_user_id="owner",
        universe_id="u-models",
        provider=f"api_key_http:{rig.definition.id}",
        model_id=MODEL,
        access=ModelAccess("discovered", cost_caps=caps),
    )
    assert selected.cost_caps == caps
    with pytest.raises(PermissionError):
        prepare_selected_model(
            base_path=rig.base,
            owner_user_id="owner",
            universe_id="u-models",
            provider=f"api_key_http:{rig.definition.id}",
            model_id=MODEL,
            access=ModelAccess("discovered"),
        )


def test_selected_price_bounds_constrain_budget_admission_and_unknown_settlement(served):
    from tinyassets.provider_assignment import (
        finalize_served_provider_budget,
        reserve_served_provider_budget,
    )

    with _authorize(served) as authority:
        # Isolate cost accounting from wire/discovery tests: use a trusted
        # server-side selected value with expensive output and a request fee.
        selected = replace(
            authority.selected_model,
            cost_caps=(
                ("input_million_tokens_usd", 0),
                ("output_million_tokens_usd", 1000000000),
                ("request_usd", 1000000),
                ("image_usd", 0),
            ),
        )
        narrowed = replace(authority, selected_model=selected, max_cost_microunits=1500000)
        reservation = reserve_served_provider_budget(
            served.rig.base,
            universe_dir=served.context.universe_dir,
            authority=narrowed,
            requested_output_tokens=1000,
            estimated_input_tokens=10,
        )
        assert reservation.output_tokens == 500
        assert reservation.reserved_cost_microunits == 1500000
        finalize_served_provider_budget(
            served.rig.base,
            authority=narrowed,
            reservation=reservation,
            input_tokens=10,
            output_tokens=1,
            cost_microunits=None,
        )
        with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
            row = conn.execute(
                "SELECT state, actual_cost_microunits FROM served_provider_budget_reservations"
            ).fetchone()
        assert tuple(row) == ("succeeded", 1500000)
        with pytest.raises(ProviderAuthorityHeldError):
            reserve_served_provider_budget(
                served.rig.base,
                universe_dir=served.context.universe_dir,
                authority=replace(narrowed, max_cost_microunits=999999),
                requested_output_tokens=1,
                estimated_input_tokens=1,
            )


def test_cancelled_http_inference_keeps_slot_and_reservation_until_worker_finishes(
    served,
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from tinyassets.providers import router as routing

    entered, release = threading.Event(), threading.Event()
    state = {"slots": 0, "calls": 0, "closed": 0}

    @asynccontextmanager
    async def slot(**kwargs):
        state["slots"] += 1
        try:
            yield
        finally:
            state["slots"] -= 1

    class WaitingProxy:
        def request(self, verb, document):
            state["calls"] += 1
            entered.set()
            assert release.wait(5), "test failed to release HTTP request"
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "choices": [{"message": {"content": "late response"}}],
                    }
                ),
            }

        def close(self):
            state["closed"] += 1

    monkeypatch.setattr(routing, "_provider_slot", slot)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", lambda *a, **k: WaitingProxy())

    def reservations():
        with SQLiteProviderWorkAuthorityStore(served.rig.base).connection() as conn:
            return [
                row[0]
                for row in conn.execute(
                    "SELECT state FROM served_provider_budget_reservations",
                ).fetchall()
            ]

    async def scenario():
        task = asyncio.create_task(_async_call(served))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert reservations() == ["reserved"]
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done() and state["slots"] == 1
            assert state["closed"] == 0 and reservations() == ["reserved"]
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done() and state["slots"] == 1
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert state == {"slots": 0, "calls": 1, "closed": 1}
            assert reservations() == ["indeterminate"]
            assert auth._active_provider_request(served.capability)["invocations"] == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
