"""Requester-owned, fail-closed provider execution keystone slice."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import runtime_singletons as runtime
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.config import UniverseConfig
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.graph_compiler import CompilerError, compile_branch
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
)
from tinyassets.providers.router import ProviderRouter

pytestmark = pytest.mark.usefixtures("cloud_runtime")


class _SpyProvider(BaseProvider):
    def __init__(self, name: str, *, text: str) -> None:
        self.name = name
        self.family = name
        self.text = text
        self.calls: list[Path | None] = []

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        self.calls.append(universe_dir)
        return ProviderResponse(
            text=self.text,
            provider=self.name,
            model=self.name,
            family=self.family,
            latency_ms=1.0,
        )


def _policy_branch() -> BranchDefinition:
    branch = BranchDefinition(name="provider-authority", entry_point="write")
    branch.node_defs = [NodeDefinition(
        node_id="write",
        display_name="Write",
        prompt_template="Write {topic}",
        input_keys=["topic"],
        output_keys=["answer"],
        llm_policy={"preferred": {"provider": "claude-code"}},
    )]
    branch.graph_nodes = [GraphNodeRef(id="write", node_def_id="write")]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="write"),
        EdgeDefinition(from_node="write", to_node="END"),
    ]
    branch.state_schema = [
        {"name": "topic", "type": "str", "default": ""},
        {"name": "answer", "type": "str", "default": ""},
    ]
    return branch


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_source", ["byo_api_key", "self_hosted_endpoint"])
async def test_user_universe_config_cannot_mint_served_authority(
    engine_source,
    tmp_path,
):
    """MUTATION: trust requester-owned config alone -> a provider spy fires."""
    requester = _SpyProvider("codex", text="requester-owned")
    platform = _SpyProvider("claude-code", text="platform-owned")
    router = ProviderRouter(providers={
        requester.name: requester,
        platform.name: platform,
    })
    context = UniverseContext(
        universe_dir=tmp_path,
        config=UniverseConfig(
            engine_source=engine_source,
            engine_endpoint=(
                "http://requester.invalid" if engine_source == "self_hosted_endpoint"
                else ""
            ),
            preferred_writer="codex",
        ),
    )

    with pytest.raises(
        ProviderAuthorityHeldError,
        match=r"(?i)connect your provider",
    ):
        await router.call_with_policy(
            "writer",
            "prompt",
            "system",
            {"preferred": {"provider": "claude-code"}},
            operation="run_graph",
            universe_context=context,
        )

    assert requester.calls == []
    assert platform.calls == []


def test_missing_user_provider_cannot_degrade_to_fallback(tmp_path):
    """MUTATION: catch the typed hold as exhaustion -> fallback text escapes."""
    from tinyassets.providers import call as call_module

    platform = _SpyProvider("claude-code", text="must-not-run")
    router = ProviderRouter(providers={platform.name: platform})
    saved_router = call_module.get_provider_router()
    saved_mock = call_module.is_force_mock()
    call_module.set_provider_router(router)
    call_module.set_force_mock(False)
    try:
        with pytest.raises(
            ProviderAuthorityHeldError,
            match=r"(?i)connect your provider",
        ):
            call_module.call_provider(
                "prompt",
                fallback_response="must-not-degrade",
                universe_context=UniverseContext(
                    universe_dir=tmp_path,
                    config=UniverseConfig(),
                ),
            )
    finally:
        call_module.set_provider_router(saved_router)
        call_module.set_force_mock(saved_mock)

    assert platform.calls == []


@pytest.mark.asyncio
async def test_user_universe_without_provider_holds_before_provider_access(tmp_path):
    """MUTATION: remove the fail-closed guard -> a platform provider is called."""
    platform = _SpyProvider("claude-code", text="must-not-run")
    router = ProviderRouter(providers={platform.name: platform})
    context = UniverseContext(
        universe_dir=tmp_path,
        config=UniverseConfig(),
    )

    with pytest.raises(
        ProviderAuthorityHeldError,
        match=r"(?i)connect your provider",
    ):
        await router.call(
            "writer", "prompt", "system", universe_context=context,
        )

    assert platform.calls == []


@pytest.mark.asyncio
async def test_unresolved_user_context_cannot_inherit_global_provider(
    tmp_path,
    monkeypatch,
):
    """MUTATION: trust resolved_config here -> the ambient platform spy fires."""
    platform = _SpyProvider("claude-code", text="must-not-run")
    router = ProviderRouter(providers={platform.name: platform})
    monkeypatch.setattr(
        runtime,
        "universe_config",
        UniverseConfig(preferred_writer="claude-code"),
    )

    with pytest.raises(
        ProviderAuthorityHeldError,
        match=r"(?i)connect your provider",
    ):
        await router.call(
            "writer",
            "prompt",
            "system",
            universe_context=UniverseContext(universe_dir=tmp_path),
        )

    assert platform.calls == []


def test_unresolved_run_binding_holds_instead_of_returning_raw_call(monkeypatch):
    """MUTATION: return raw call for an empty run owner -> platform spy fires."""
    from tinyassets.api import runs as api_runs
    from tinyassets.providers import call as call_module

    platform = _SpyProvider("claude-code", text="must-not-run")
    saved_router = call_module.get_provider_router()
    saved_mock = call_module.is_force_mock()
    call_module.set_provider_router(ProviderRouter(providers={
        platform.name: platform,
    }))
    call_module.set_force_mock(False)
    monkeypatch.setattr(
        runtime,
        "universe_config",
        UniverseConfig(preferred_writer="claude-code"),
    )
    try:
        bound = api_runs._bind_run_provider_call(call_module.call_provider, "")
        with pytest.raises(
            ProviderAuthorityHeldError,
            match=r"(?i)connect your provider",
        ):
            bound("prompt")
    finally:
        call_module.set_provider_router(saved_router)
        call_module.set_force_mock(saved_mock)

    assert platform.calls == []


def test_policy_graph_preserves_context_and_holds_without_served_authority(
    tmp_path,
    monkeypatch,
):
    """The bound policy caller preserves owner context and cannot serve without authority."""
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.providers import call as call_module

    universe = tmp_path / "user-u"
    universe.mkdir()
    requester = _SpyProvider("codex", text="requester-owned")
    platform = _SpyProvider("claude-code", text="platform-owned")
    router = ProviderRouter(providers={
        requester.name: requester,
        platform.name: platform,
    })
    saved_router = call_module.get_provider_router()
    saved_mock = call_module.is_force_mock()
    saved_global = runtime.universe_config
    call_module.set_force_mock(False)
    call_module.set_provider_router(router)
    runtime.universe_config = UniverseConfig(preferred_writer="claude-code")
    context = UniverseContext(
        universe_dir=universe,
        config=UniverseConfig(preferred_writer="codex"),
    )
    try:
        # A policy graph needs the same universe-bound policy bridge as a real
        # run. The raw text callable is now refused before authority checking.
        bound = call_module.bind_universe_provider_call(
            call_module.call_provider, context, operation="run_graph",
        )
        compiled = compile_branch(
            _policy_branch(),
            provider_call=bound,
            universe_context=context,
        )
        with pytest.raises(CompilerError, match=r"(?i)connect your provider"):
            compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
                {"topic": "tiny assets"},
                config={"configurable": {"thread_id": "requester-context"}},
            )
    finally:
        runtime.universe_config = saved_global
        call_module.set_provider_router(saved_router)
        call_module.set_force_mock(saved_mock)

    assert requester.calls == []
    assert platform.calls == []


def test_plain_graph_bridge_forwards_exact_universe_context(tmp_path):
    """MUTATION: remove graph bridge context kwarg -> recorded context is None."""
    from langgraph.checkpoint.memory import InMemorySaver

    context = UniverseContext(
        universe_dir=tmp_path,
        config=UniverseConfig(preferred_writer="codex"),
    )
    seen: list[UniverseContext | None] = []

    def provider_call(
        _prompt: str,
        _system: str = "",
        *,
        universe_context: UniverseContext | None = None,
        **_kwargs: Any,
    ) -> str:
        seen.append(universe_context)
        return "requester-owned"

    branch = _policy_branch()
    branch.node_defs[0].llm_policy = None
    compiled = compile_branch(
        branch,
        provider_call=provider_call,
        universe_context=context,
    )
    result = compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
        {"topic": "plain bridge"},
        config={"configurable": {"thread_id": "plain-context"}},
    )

    assert result["answer"] == "requester-owned"
    assert seen == [context]


def test_cloud_automation_policy_cannot_escape_its_authority_owner(monkeypatch):
    """MUTATION: remove the exact-owner bridge -> shared platform router fires."""
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.cloud_automation_continuation import (
        _ClaimedCloudProviderSession,
    )

    calls: list[dict[str, Any]] = []

    def owned_call(self, _prompt, _system="", **kwargs):
        calls.append(kwargs)
        return "cloud-authorized"

    def platform_escape():
        raise AssertionError("shared platform provider must not be called")

    monkeypatch.setattr(_ClaimedCloudProviderSession, "__call__", owned_call)
    monkeypatch.setattr(
        "tinyassets.graph_compiler._get_shared_router", platform_escape,
    )
    owner = object.__new__(_ClaimedCloudProviderSession)
    owner._receipt = SimpleNamespace(provider="claude-code")

    compiled = compile_branch(_policy_branch(), provider_call=owner)
    result = compiled.graph.compile(checkpointer=InMemorySaver()).invoke(
        {"topic": "automation"},
        config={"configurable": {"thread_id": "cloud-owner"}},
    )

    assert result["answer"] == "cloud-authorized"
    assert len(calls) == 1
    assert calls[0]["role"] == "writer"


def _patch_run_branch_dependencies(monkeypatch, branch: Any) -> None:
    monkeypatch.setattr(
        "tinyassets.api.branches._resolve_branch_id",
        lambda branch_id, _base: branch_id,
    )
    monkeypatch.setattr(
        "tinyassets.daemon_server.get_branch_definition",
        # `visibility: public` stated: an absent field now reads as PRIVATE, and
        # this double's subject is the provider session, not the read gate.
        lambda _base, *, branch_def_id: {
            "branch_def_id": branch_def_id, "visibility": "public",
        },
    )
    monkeypatch.setattr(
        "tinyassets.branches.BranchDefinition.from_dict",
        lambda _source: branch,
    )


def _bind_request_home(base_path, universe_id: str) -> None:
    """Put the signed-in request principal at home in ``universe_id``.

    Constructing a run session reads the owner's SAVED model preference, and
    that read is fenced by the founder-home check, so it needs the daemon
    schema. A bare ``tmp_path`` has none: the steps these tests stub out
    (``_ensure_runs_recovery``, ``_request_universe``, the run-write check)
    are exactly the ones that initialise it in production before any run is
    requested. Bind the state production always has by then -- schema present,
    principal at home in the universe being run, NO preference saved -- so the
    session is constructed on the legacy serving binding exactly as before.
    The read-back proves the bind took rather than silently no-op'ing.
    """
    from tinyassets.api.permissions import current_request_actor_id
    from tinyassets.daemon_server import get_founder_home, set_founder_home

    principal = current_request_actor_id()
    assert principal, "these tests run as the signed-in test operator"
    set_founder_home(
        base_path, founder_sub=principal, universe_id=universe_id,
        platform_generated=True,
    )
    assert get_founder_home(base_path, principal) == universe_id


def test_run_branch_hands_execution_a_server_owned_provider_session(
    tmp_path,
    monkeypatch,
):
    """The author supplies no carrier or authority field to foreground execution."""
    from tinyassets.api import runs as api_runs

    universe = tmp_path / "user-u"
    universe.mkdir()
    (universe / "config.yaml").write_text(
        "preferred_writer: codex\n", encoding="utf-8",
    )
    _bind_request_home(tmp_path, "user-u")
    # A branch answers what it declares before run_branch picks a lane; this
    # one declares no file input, so the scalar lane under test is taken.
    branch = SimpleNamespace(version=1, validate=lambda: [],
                             to_dict=lambda: {"branch_def_id": "b1"})
    captured: dict[str, Any] = {}

    def fake_execute(*_args: Any, provider_call=None, **_kwargs: Any):
        captured["provider_call"] = provider_call
        return SimpleNamespace(run_id="r1", status="queued", output={}, error="")

    monkeypatch.setattr(api_runs, "_ensure_runs_recovery", lambda: None)
    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(api_runs, "_universe_dir", lambda _uid: universe)
    monkeypatch.setattr(api_runs, "_request_universe", lambda uid="": uid or "user-u")
    monkeypatch.setattr("tinyassets.runs.execute_branch_async", fake_execute)
    _patch_run_branch_dependencies(monkeypatch, branch)

    payload = json.loads(api_runs._action_run_branch({
        "branch_def_id": "b1",
        "universe_id": "user-u",
    }))

    assert payload["status"] == "queued"
    bound = captured["provider_call"]
    assert type(bound.provider_call).__name__ == "_ForegroundRunProviderSession"
    assert bound.universe_context.universe_dir == universe
    assert bound.universe_context.config.preferred_writer == "codex"
    assert bound.universe_context.provider_request is None
    assert bound.universe_context.provider_invocation is None
    with pytest.raises(ProviderAuthorityHeldError):
        bound("cannot launch before run admission")


@pytest.mark.parametrize("action", ["version", "resume"])
def test_branch_version_and_resume_use_the_same_server_owned_run_session(
    action,
    tmp_path,
    monkeypatch,
):
    """Version and resume entry points cannot restore the old static context lane."""
    from tinyassets.api import runs as api_runs

    universe = tmp_path / "user-u"
    universe.mkdir()
    (universe / "config.yaml").write_text(
        "preferred_writer: codex\n", encoding="utf-8",
    )
    _bind_request_home(tmp_path, "user-u")
    captured: dict[str, Any] = {}

    def fake_execute(*_args: Any, provider_call=None, **_kwargs: Any):
        captured["provider_call"] = provider_call
        return SimpleNamespace(run_id="r1", status="queued", output={}, error="")

    monkeypatch.setattr(api_runs, "_ensure_runs_recovery", lambda: None)
    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(api_runs, "_universe_dir", lambda _uid: universe)
    monkeypatch.setattr(api_runs, "_request_universe", lambda uid="": uid or "user-u")
    if action == "version":
        from tinyassets.api.permissions import current_request_actor_id
        from tinyassets.branch_versions import publish_branch_version
        from tinyassets.daemon_server import save_branch_definition

        # The read gate needs a persisted version and its parent branch. Run
        # the signed-in owner's private snapshot through the real read gate.
        owner = current_request_actor_id()
        assert owner
        branch = _policy_branch()
        branch.author = owner
        branch.visibility = "private"
        saved = save_branch_definition(tmp_path, branch_def=branch.to_dict())
        version = publish_branch_version(tmp_path, saved, publisher=owner)
        monkeypatch.setattr(
            "tinyassets.runs.execute_branch_version_async", fake_execute,
        )
        payload = json.loads(api_runs._action_run_branch_version({
            "branch_version_id": version.branch_version_id,
            "universe_id": "user-u",
        }))
    else:
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda _base, _run_id: {"actor": "universe:user-u"},
        )
        monkeypatch.setattr(api_runs, "_run_write_allowed", lambda _record: True)
        monkeypatch.setattr("tinyassets.runs.resume_run", fake_execute)
        payload = json.loads(api_runs._action_resume_run({"run_id": "r1"}))

    assert payload["status"] == "queued"
    bound = captured["provider_call"]
    assert type(bound.provider_call).__name__ == "_ForegroundRunProviderSession"
    assert bound.universe_context.universe_dir == universe
    assert bound.universe_context.config.preferred_writer == "codex"
    assert bound.universe_context.provider_request is None
    assert bound.universe_context.provider_invocation is None
