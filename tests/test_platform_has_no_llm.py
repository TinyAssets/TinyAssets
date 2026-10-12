"""AGENTS.md Hard Rule 15: the platform has no LLM.

Only a powered universe calls a model, with its owner's own connected
credentials, for that universe alone. These tests pin the one enforcement
point (``tinyassets/providers/owner_binding.py``, wired into every provider
launch in ``ProviderRouter``) and the three platform features that used to
reach a provider with no owner at all. Per the founder (2026-09-24) none is
rewired to a universe: the quality leaderboard's selector fails closed, the
market's ``run_canonical`` auto-refresh therefore keeps the stored canonical,
and the bug-investigation auto-trigger is deleted.

Every "cannot reach a provider" test installs a real ``ProviderRouter`` whose
executors are spies registered under every built-in name, turns the test
suite's force-mock OFF, and asserts no spy ran. On the pre-rule tree each of
them reaches a spy through the host fallback chain.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tinyassets.exceptions import PlatformLLMCallRefusedError, ProviderAuthorityHeldError
from tinyassets.provider_work_authority import ProviderInvocationCarrier
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse, UniverseContext
from tinyassets.providers.router import ProviderRouter

_ALL_BUILTIN = ("claude-code", "codex", "ollama-local")


def _builtin_executor_classes() -> dict[str, type]:
    from tinyassets.providers.claude_provider import ClaudeProvider
    from tinyassets.providers.codex_provider import CodexProvider
    from tinyassets.providers.ollama_provider import OllamaProvider

    classes = (ClaudeProvider, CodexProvider, OllamaProvider)
    return {cls.name: cls for cls in classes}


class _SpyProvider(BaseProvider):
    """Records calls; carries the REAL executor's declared credential source."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.family = name
        self.calls: list[str] = []
        real = _builtin_executor_classes().get(name)
        if real is not None and hasattr(real, "credential_source"):
            self.credential_source = real.credential_source

    async def complete(self, prompt, system, config, *, universe_dir=None):
        self.calls.append(prompt)
        return ProviderResponse(
            text='{"ranked_entries": []}', provider=self.name, model="spy",
            family=self.family, latency_ms=1.0,
        )


def _spy_router(**kwargs) -> tuple[ProviderRouter, dict[str, _SpyProvider]]:
    spies = {name: _SpyProvider(name) for name in _ALL_BUILTIN}
    return ProviderRouter(providers=dict(spies), **kwargs), spies


def _calls(spies: dict[str, _SpyProvider]) -> dict[str, int]:
    return {name: len(spy.calls) for name, spy in spies.items() if spy.calls}


def _carrier(provider: str, *, role: str = "writer", operation: str = "run_graph"):
    carrier = MagicMock(spec=ProviderInvocationCarrier)
    carrier._receipt = MagicMock(principal_id="owner", universe_id="u1")
    carrier.provider = provider
    carrier.role = role
    carrier.operation = operation
    carrier.max_tokens = 50
    carrier.max_cost_microunits = 5
    carrier.selected_model = None
    carrier.native_selection = None
    carrier.settlement_owner = None
    carrier.validate_for_call.return_value = provider
    return carrier


def _resolver(carrier):
    def resolve(_context, *, role, operation):
        carrier.validate_for_call(role=role, operation=operation)
        return carrier
    return resolve


def test_nested_helper_uses_bound_owner_model_only_for_its_run(tmp_path, monkeypatch):
    from tests.support.fantasy_model import ConnectedFantasySource
    from tinyassets.providers import call as provider_call
    from tinyassets.providers.model_call import bind_model_call, owner_model_scope, text_sync

    source = ConnectedFantasySource(tmp_path / "owned-center")
    monkeypatch.setattr(provider_call, "get_provider_router", lambda: source)
    monkeypatch.setattr(provider_call, "register_open_providers_for", lambda _context: None)
    bound = bind_model_call(text_sync, source.context, operation="run_graph")

    with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
        text_sync("Choose only tools", role="extract")
    with owner_model_scope(bound):
        assert text_sync("Choose only tools", role="extract") == '{"tools": []}'
        assert source.receipts[-1].reported_model == source.model
        assert source.receipts[-1].outcome == "succeeded"
    with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
        text_sync("Choose only tools", role="extract")


# ---------------------------------------------------------------------------
# The router: no owner authority, no provider
# ---------------------------------------------------------------------------


def test_router_call_with_no_universe_is_refused_before_any_provider_or_probe():
    health = MagicMock(return_value={"status": "ok"})
    router, spies = _spy_router(auth_health=health)
    assert set(router.available_providers) == set(_ALL_BUILTIN)

    with pytest.raises(PlatformLLMCallRefusedError, match="platform has no LLM"):
        asyncio.run(router.call("writer", "prompt", "system"))
    with pytest.raises(PlatformLLMCallRefusedError):
        router.call_sync("writer", "prompt", "system")
    assert not hasattr(router, "call_judge_ensemble")

    assert _calls(spies) == {}
    health.assert_not_called()


def test_refusal_is_held_authority_so_callers_never_swallow_it():
    assert issubclass(PlatformLLMCallRefusedError, ProviderAuthorityHeldError)


def test_a_universe_with_no_owner_authority_gets_the_connect_refusal(tmp_path):
    router, spies = _spy_router()
    context = UniverseContext(universe_dir=tmp_path / "u1")

    with pytest.raises(PlatformLLMCallRefusedError, match="(?i)connect your provider"):
        asyncio.run(router.call(
            "writer", "prompt", "system", operation="converse", universe_context=context,
        ))
    assert _calls(spies) == {}


def test_model_call_with_no_universe_is_refused_before_dispatch():
    from tinyassets.providers import call as bridge
    from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call_sync

    router, spies = _spy_router()
    previous = bridge.get_provider_router()
    bridge.set_provider_router(router)
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="owner-bound"):
            call_sync(ModelCall(model=None, fallbacks=(),
                                context=ModelContext(prompt="summarize this")),
                      Parent(owner="", command_center=""))
        assert _calls(spies) == {}
    finally:
        bridge.set_provider_router(previous)


@pytest.mark.parametrize("requested", [
    ("codex", "gpt-6"),
    ("claude-code", "different-model"),
])
def test_workflow_model_request_cannot_silently_use_armed_default(
    tmp_path, requested,
):
    from tinyassets.provider_work_authority import (
        ProviderInvocationReservationState,
        ProviderInvocationSettlementOwner,
    )
    from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call_sync
    from tinyassets.providers.model_policy import ModelRef

    router, spies = _spy_router()
    carrier = _carrier("claude-code")
    carrier.settlement_owner = ProviderInvocationSettlementOwner.ROUTER
    context = UniverseContext(
        universe_dir=tmp_path / "u1", provider_invocation=carrier,
    )
    with patch("tinyassets.providers.router._provider_invocation_carrier",
               side_effect=_resolver(carrier)):
        with pytest.raises(ProviderAuthorityHeldError, match="Requested model"):
            call_sync(ModelCall(
                model=ModelRef(*requested), fallbacks=(),
                context=ModelContext(prompt="review", universe_context=context,
                                     operation="run_graph", router=router),
            ), Parent(owner="owner", command_center="u1"))
    assert _calls(spies) == {}
    carrier.settle.assert_called_once_with(
        ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
        input_tokens=0, output_tokens=0, cost_microunits=0,
    )


# ---------------------------------------------------------------------------
# Host and environment credentials never serve a call
# ---------------------------------------------------------------------------


def test_host_writer_pin_and_host_login_cannot_serve_an_unbound_call(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PIN_WRITER", "claude-code")
    health = MagicMock(return_value={"status": "ok"})
    router, spies = _spy_router(auth_health=health)

    with pytest.raises(PlatformLLMCallRefusedError):
        asyncio.run(router.call("writer", "prompt", "system"))
    assert _calls(spies) == {}
    health.assert_not_called()


@pytest.mark.parametrize("host_provider", ["ollama-local"])
def test_owner_authority_cannot_launch_a_host_credential_provider(
    host_provider, tmp_path, monkeypatch,
):
    """Env API keys and the host's own model server are not an owner's connection."""
    monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "host-key")
    router, spies = _spy_router()
    carrier = _carrier(host_provider)

    with patch("tinyassets.providers.router._provider_invocation_carrier",
               side_effect=_resolver(carrier)):
        with pytest.raises(PlatformLLMCallRefusedError, match=host_provider):
            from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call

            (tmp_path / "u1").mkdir()
            asyncio.run(call(ModelCall(
                model=None, fallbacks=(), context=ModelContext(
                    role="writer", prompt="prompt", system="system",
                    config=ModelConfig(max_tokens=10), operation="run_graph",
                    universe_context=UniverseContext(
                        universe_dir=tmp_path / "u1", provider_invocation=carrier,
                    ), router=router,
                ),
            ), Parent(owner=carrier._receipt.principal_id, command_center="u1")))
    assert _calls(spies) == {}


def test_exactly_the_host_credential_executors_declare_it():
    from tinyassets.providers.owner_binding import is_host_credential_provider

    host_only = sorted(
        name for name, cls in _builtin_executor_classes().items()
        if is_host_credential_provider(cls)
    )
    assert host_only == ["ollama-local"]


def test_dispatch_rechecks_owner_source_membership_after_grant_revocation(
    tmp_path, monkeypatch,
):
    from tests.test_open_serving_bind import _CONN_ID, _GRANT_ID, _setup
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.owner_binding import require_owner_bound_dispatch
    from tinyassets.providers.served_model_plan import connected_sources
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    universe, _agent, definition = _setup(tmp_path, monkeypatch)
    source = f"api_key_http:{definition.id}"
    authority = SimpleNamespace(owner_user_id="owner-1", universe_id=universe.name)
    carrier = SimpleNamespace(
        _receipt=SimpleNamespace(principal_id="owner-1", universe_id=universe.name),
        provider=source,
    )
    assert source in connected_sources(tmp_path, universe, "owner-1")
    require_owner_bound_dispatch(source, universe_dir=universe, served_authority=authority)
    require_owner_bound_dispatch(source, universe_dir=universe, invocation_carrier=carrier)
    foreign_carrier = SimpleNamespace(
        _receipt=SimpleNamespace(principal_id="owner-2", universe_id=universe.name),
        provider=source,
    )
    with pytest.raises(PlatformLLMCallRefusedError, match="current connected source"):
        require_owner_bound_dispatch(
            source, universe_dir=universe, served_authority=authority,
            invocation_carrier=foreign_carrier,
        )
    other_connection = "http_" + "c" * 32
    other_grant = "http_grant_" + "d" * 32
    other_ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-2",
    )
    other_ledger.create_connection(
        connection_id=other_connection, owner_user_id="owner-2",
        connection_class="http", connection_type="http", auth_scheme="bearer",
        scopes=("http",), provider="http", destination="compute:other",
        credential_ref="vault://http/other",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions", "methods": ["POST"]}],
    )
    other_ledger.grant_connection(
        grant_id=other_grant, connection_id=other_connection, owner_user_id="owner-2",
        universe_id=universe.name,
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    other_definition = register_definition(
        universe_id=universe.name, owner_user_id="owner-2",
        access_method="api_key_http", protocol="openai_chat",
        model="other/model", ref=other_grant,
    )
    with pytest.raises(PlatformLLMCallRefusedError, match="current connected source"):
        require_owner_bound_dispatch(
            f"api_key_http:{other_definition.id}", universe_dir=universe,
            served_authority=authority,
        )

    owner_ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner-1",
    )
    owner_ledger.configure_capability(
        connection_id=_CONN_ID, capability_kind="model_use", enabled=False,
    )
    with pytest.raises(PlatformLLMCallRefusedError, match="current connected source"):
        require_owner_bound_dispatch(source, universe_dir=universe, served_authority=authority)
    owner_ledger.configure_capability(
        connection_id=_CONN_ID, capability_kind="model_use", enabled=True,
        descriptor={
            "wire": "openai_chat", "billing": "free",
            "models": [{"id": "moonshotai/kimi-k2", "tools": True, "context": 8192}],
        },
    )
    owner_ledger.revoke_grant(_GRANT_ID)
    with pytest.raises(PlatformLLMCallRefusedError, match="current connected source"):
        require_owner_bound_dispatch(source, universe_dir=universe, served_authority=authority)
    with pytest.raises(PlatformLLMCallRefusedError, match="current connected source"):
        require_owner_bound_dispatch(source, universe_dir=universe, invocation_carrier=carrier)


def test_unavailable_http_broker_does_not_block_owned_subscription(tmp_path, monkeypatch):
    from tests.test_open_serving_bind import _setup
    from tinyassets.providers.owner_binding import require_owner_bound_dispatch
    from tinyassets.storage.outbound_connections import ProxyRequestError

    universe, _agent, definition = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "tinyassets.credential_vault.current_llm_subscription_custody",
        lambda _conn, **scope: object() if scope["service"] == "claude" else None,
    )

    def broker_down(*_args, **_kwargs):
        raise ProxyRequestError("broker unavailable")

    monkeypatch.setattr("tinyassets.broker.ledger_queries.query_ledger", broker_down)
    authority = SimpleNamespace(owner_user_id="owner-1", universe_id=universe.name)
    require_owner_bound_dispatch(
        "claude-code", universe_dir=universe, served_authority=authority,
    )
    with pytest.raises(PlatformLLMCallRefusedError, match="current connected source"):
        require_owner_bound_dispatch(
            f"api_key_http:{definition.id}", universe_dir=universe,
            served_authority=authority,
        )


def test_owner_authority_without_a_universe_dir_cannot_fall_to_host_env():
    """No universe dir means the child env is the host's: refuse before launch."""
    router, spies = _spy_router()
    carrier = _carrier("codex")

    with patch("tinyassets.providers.router._provider_invocation_carrier",
               side_effect=_resolver(carrier)):
        with pytest.raises(PlatformLLMCallRefusedError):
            asyncio.run(router.call(
                "writer", "prompt", "system", ModelConfig(max_tokens=10),
                operation="run_graph",
                universe_context=UniverseContext(provider_invocation=carrier),
            ))
    assert _calls(spies) == {}


def test_bound_call_is_served_by_the_owners_provider_and_ignores_host_pin(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("TINYASSETS_PIN_WRITER", "claude-code")
    health = MagicMock(return_value={"status": "not_logged_in"})
    router, spies = _spy_router(auth_health=health)
    carrier = _carrier("codex")
    monkeypatch.setattr(
        "tinyassets.providers.served_model_plan.connected_sources",
        lambda base, universe, owner: frozenset({"codex"}),
    )

    with patch("tinyassets.providers.router._provider_invocation_carrier",
               side_effect=_resolver(carrier)):
        from tinyassets.providers.model_call import ModelCall, ModelContext, Parent, call

        (tmp_path / "u1").mkdir()
        response = asyncio.run(call(ModelCall(
            model=None, fallbacks=(), context=ModelContext(
                role="writer", prompt="prompt", system="system",
                config=ModelConfig(max_tokens=10), operation="run_graph",
                universe_context=UniverseContext(
                    universe_dir=tmp_path / "u1", provider_invocation=carrier,
                ), router=router,
            ),
        ), Parent(owner=carrier._receipt.principal_id, command_center="u1"))).response
    assert response.provider == "codex"
    assert _calls(spies) == {"codex": 1}
    health.assert_not_called()


# ---------------------------------------------------------------------------
# Platform features: leaderboard, market run_canonical, bug investigation
# ---------------------------------------------------------------------------


@pytest.fixture
def platform_base(tmp_path: Path, monkeypatch):
    """Real stores and a spy router behind the model call boundary."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID", raising=False)
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.providers import call as bridge
    from tinyassets.runs import initialize_runs_db

    initialize_author_server(tmp_path)
    initialize_runs_db(tmp_path)
    router, spies = _spy_router()
    monkeypatch.setattr(bridge, "_router", router)
    return tmp_path, spies


def _seed_goal_with_ranked_branches(base: Path, goal_id: str = "g1") -> dict[str, str]:
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import save_branch_definition, save_goal, update_goal
    from tinyassets.runs import RUN_STATUS_COMPLETED, create_run, update_run_status

    save_goal(base, goal=dict(
        goal_id=goal_id, name=goal_id, description="", author="host",
        tags=[], visibility="public",
    ))
    update_goal(base, goal_id=goal_id, updates={
        "auto_canonical_via_leaderboard": True,
        "min_completed_runs_for_canonical": 1,
    })
    graph = {
        "graph_nodes": [{
            "id": "n1", "type": "prompt", "phase": "draft", "prompt": "respond",
            "input_keys": [], "output_keys": ["out"],
        }],
        "edges": [{"from": "START", "to": "n1"}, {"from": "n1", "to": "END"}],
        "state_schema": [{"name": "out", "type": "str"}],
        "entry_point": "n1",
    }
    versions: dict[str, str] = {}
    for bdid, completed in (("weak", 1), ("strong", 4)):
        save_branch_definition(base, branch_def=dict(
            branch_def_id=bdid, name=bdid, description="", author="host", tags=[],
            published=True, goal_id=goal_id, **graph,
        ))
        version = publish_branch_version(
            base,
            branch_dict={"branch_def_id": bdid, "name": bdid, "description": "",
                         "author": "host", "node_defs": [], **graph},
            notes=f"{bdid} v1", publisher="host",
        )
        versions[bdid] = version.branch_version_id
        for _ in range(completed):
            rid = create_run(base, branch_def_id=bdid, thread_id=bdid, inputs={},
                             actor="universe:u-test")
            update_run_status(base, rid, status=RUN_STATUS_COMPLETED, finished_at=time.time())
    return versions


def test_quality_leaderboard_fails_closed_without_reaching_any_provider(platform_base):
    base, spies = platform_base
    _seed_goal_with_ranked_branches(base)
    from tinyassets.api.quality_leaderboard import build_quality_leaderboard

    board = build_quality_leaderboard(base, goal_id="g1", viewer="")

    assert _calls(spies) == {}
    assert board["ok"] is False
    assert board["error_kind"] == "selector_retired"
    assert "platform has no LLM" in board["error"]
    assert board["entries"] == []


def test_goal_bound_selector_is_not_run_either(platform_base):
    base, spies = platform_base
    _seed_goal_with_ranked_branches(base)
    from tinyassets.api.selector_dispatch import (
        dispatch_selector,
        ensure_default_selector_published,
    )
    from tinyassets.daemon_server import update_goal

    bound = ensure_default_selector_published(base)
    update_goal(base, goal_id="g1", updates={"selector_branch_version_id": bound})

    result = dispatch_selector(
        base, goal_id="g1", actor="host",
        candidate_branches=[{"branch_def_id": "strong", "signals": {}}],
    )

    assert _calls(spies) == {}
    assert result == {
        "ok": False,
        "error_kind": "selector_retired",
        "error": result["error"],
    }
    from tinyassets.runs import list_runs

    # Only the seeded runs exist: no selector run was created.
    assert {run["branch_def_id"] for run in list_runs(base)} == {"weak", "strong"}


def test_market_run_canonical_refresh_cannot_reach_a_provider(platform_base):
    base, spies = platform_base
    versions = _seed_goal_with_ranked_branches(base)
    from tinyassets.api.canonical_dispatch import resolve_canonical_for_run
    from tinyassets.daemon_server import set_canonical_branch

    set_canonical_branch(
        base, goal_id="g1", branch_version_id=versions["weak"], set_by="host",
    )
    resolution = resolve_canonical_for_run(base, goal_id="g1", viewer="")

    assert _calls(spies) == {}
    # No platform ranking: the owner's stored canonical stands.
    assert resolution["ok"] is True, resolution
    assert resolution["branch_version_id"] == versions["weak"]


def test_bug_investigation_has_no_platform_trigger_left():
    import tinyassets.bug_investigation as bug_investigation

    for retired in (
        "_resolve_investigation_handler",
        "_maybe_enqueue_investigation",
        "enqueue_investigation_request",
    ):
        assert not hasattr(bug_investigation, retired), retired
