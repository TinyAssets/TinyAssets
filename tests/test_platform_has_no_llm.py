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
from unittest.mock import MagicMock, patch

import pytest

from tinyassets.exceptions import PlatformLLMCallRefusedError, ProviderAuthorityHeldError
from tinyassets.provider_work_authority import ProviderInvocationCarrier
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse, UniverseContext
from tinyassets.providers.router import FALLBACK_CHAINS, ProviderRouter

_ALL_BUILTIN = sorted({name for chain in FALLBACK_CHAINS.values() for name in chain})


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
    carrier._receipt = MagicMock(principal_id="owner")
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


# ---------------------------------------------------------------------------
# The router: no owner authority, no provider
# ---------------------------------------------------------------------------


def test_router_call_with_no_universe_is_refused_before_any_provider_or_probe():
    health = MagicMock(return_value={"status": "ok"})
    router, spies = _spy_router(auth_health=health)

    with pytest.raises(PlatformLLMCallRefusedError, match="platform has no LLM"):
        asyncio.run(router.call("writer", "prompt", "system"))
    with pytest.raises(PlatformLLMCallRefusedError):
        router.call_sync("writer", "prompt", "system")
    with pytest.raises(PlatformLLMCallRefusedError):
        asyncio.run(router.call_with_policy(
            "writer", "prompt", "system", {"preferred": {"provider": "claude-code"}},
        ))
    with pytest.raises(PlatformLLMCallRefusedError):
        asyncio.run(router.call_judge_ensemble("prompt", "system"))

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


def test_call_provider_with_no_universe_is_refused_not_degraded(monkeypatch):
    """No mock, no ``fallback_response``: the refusal is the result (Hard Rule 8)."""
    from tinyassets.providers import call as bridge

    router, spies = _spy_router()
    monkeypatch.setattr(bridge, "_force_mock", False)
    monkeypatch.setattr(bridge, "_real_router", router)

    with pytest.raises(PlatformLLMCallRefusedError):
        bridge.call_provider("summarize this", "system", fallback_response="looks real")
    assert _calls(spies) == {}


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
            asyncio.run(router.call(
                "writer", "prompt", "system", ModelConfig(max_tokens=10),
                operation="run_graph",
                universe_context=UniverseContext(
                    universe_dir=tmp_path / "u1", provider_invocation=carrier,
                ),
            ))
    assert _calls(spies) == {}


def test_exactly_the_host_credential_executors_declare_it():
    from tinyassets.providers.owner_binding import is_host_credential_provider

    host_only = sorted(
        name for name, cls in _builtin_executor_classes().items()
        if is_host_credential_provider(cls)
    )
    assert host_only == ["ollama-local"]


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

    with patch("tinyassets.providers.router._provider_invocation_carrier",
               side_effect=_resolver(carrier)):
        response = asyncio.run(router.call(
            "writer", "prompt", "system", ModelConfig(max_tokens=10),
            operation="run_graph",
            universe_context=UniverseContext(
                universe_dir=tmp_path / "u1", provider_invocation=carrier,
            ),
        ))
    assert response.provider == "codex"
    assert _calls(spies) == {"codex": 1}
    health.assert_not_called()


# ---------------------------------------------------------------------------
# Platform features: leaderboard, market run_canonical, bug investigation
# ---------------------------------------------------------------------------


@pytest.fixture
def platform_base(tmp_path: Path, monkeypatch):
    """Real stores, force-mock OFF, and a spy router behind ``call_provider``."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID", raising=False)
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.providers import call as bridge
    from tinyassets.runs import initialize_runs_db

    initialize_author_server(tmp_path)
    initialize_runs_db(tmp_path)
    router, spies = _spy_router()
    monkeypatch.setattr(bridge, "_force_mock", False)
    monkeypatch.setattr(bridge, "_real_router", router)
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
