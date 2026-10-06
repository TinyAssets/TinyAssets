"""One agent definition, every provider (founder rule 2026-10-06).

For every registered provider kind, render the ONE definition through that
kind's real launch code and compare what the model would see -- tools with
their schemas, instructions, capabilities -- with the definition itself. A
kind that cannot run an agent must be refused for agent turns, never run one
with less. Provider-specific translation (flags, MCP config, app-server
protocol, model catalog, wire envelope) is allowed; a difference in what the
agent can do is not.

The rendered launch is the adapter's own; the real CLIs' request captures
(``scripts/native_cli_payload.py``, ``scripts/codex_cli_smoke.py``) are the
evidence that each CLI then sends exactly that.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp.types import Tool

from tinyassets.agent_definition import AGENT_CAPABILITIES, AgentTool, agent_definition
from tinyassets.providers import provider_resolver
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.definition import _HTTP_PROTOCOLS
from tinyassets.served_tools import FOUR_MODEL_TOOLS

INSTRUCTIONS = "You are the owner's agent. Work in /u."


def _route_tools() -> list[Tool]:
    """The engine route's own registrations of the model-visible tools."""
    from tinyassets.engine_mcp_server import mcp

    registered = asyncio.run(mcp.list_tools(run_middleware=False))
    return [Tool(name=t.name, description=t.description, inputSchema=t.parameters)
            for t in registered if t.name in FOUR_MODEL_TOOLS]


@pytest.fixture(scope="module")
def definition():
    return agent_definition(_route_tools(), INSTRUCTIONS)


def _registered_kinds() -> list[str]:
    """Every provider kind the platform can execute, from its registries."""
    from tinyassets.providers import call

    kinds = [f"cli:{name}" for name in provider_resolver._CLI_PROVIDER_CLASSES]
    kinds += [f"http:{protocol}" for protocol in _HTTP_PROTOCOLS]
    source = Path(call.__file__).read_text(encoding="utf-8")
    kinds += ["local:ollama"] if "OllamaProvider()" in source else []
    return kinds


# --- per-kind renderers: the adapter's real launch -> what the model sees ------

def _http(protocol, definition):
    from tinyassets.providers.agent_chat_codec import tool_definitions
    from tinyassets.providers.protocol_encoders import agent_codec_for

    codec = agent_codec_for(protocol)
    if codec is None:
        return None
    _, body = codec.encode(prompt="Reply OK.", system=definition.instructions,
                           source_ref="guard", model="guard",
                           tools=tool_definitions(tuple(_route_tools())))
    tools = tuple(AgentTool(t["function"]["name"], t["function"]["description"],
                            t["function"]["parameters"]) for t in body["tools"])
    (system,) = [m["content"] for m in body["messages"] if m["role"] == "system"]
    return tools, system, _http_capabilities()


def _http_capabilities():
    # The HTTP loop's tool session is the engine route's: open the coordinator's
    # own tool session and see what it dials.
    from types import SimpleNamespace

    from tinyassets import agent_turn_coordinator

    opened = []

    def open_engine_tools(**kwargs):
        opened.append(kwargs)
        return SimpleNamespace()

    coordinator = SimpleNamespace(
        adapter=SimpleNamespace(engine_identity=lambda context, config: ("owner", "center")),
        context=None, interrupt=None,
        config=ModelConfig(engine_mcp_enabled=True, engine_mcp_actor_id="owner",
                           engine_mcp_graph_id="center"))
    coordinator.steering = lambda: agent_turn_coordinator.AgentTurnCoordinator.steering(
        coordinator)
    original = agent_turn_coordinator.open_engine_tools
    agent_turn_coordinator.open_engine_tools = open_engine_tools
    try:
        agent_turn_coordinator.AgentTurnCoordinator._open_tools(coordinator, 1.0)
    finally:
        agent_turn_coordinator.open_engine_tools = original
    route = [(o["actor_id"], o["graph_id"]) for o in opened] == [("owner", "center")]
    return _capabilities(route_tools=route, kind="engine_inference")


def _capabilities(*, route_tools: bool, kind: str) -> frozenset[str]:
    found = set()
    if route_tools:
        # Steering and the activity fence are engine-route middleware, so any
        # executor whose tools all cross the route has them.
        found |= {"engine_route_tools", "owner_steering"}
    if route_tools and _activity_launches(kind) and _activity_stops(kind):
        found.add("activities")
    return frozenset(found)


def _activity_launches(kind: str) -> bool:
    """Whether an activity run may launch on this execution kind."""
    import tempfile

    from tests.test_activity_http_yield import _LaunchRecorder, _live_activity_adapter

    with tempfile.TemporaryDirectory() as scratch:
        adapter, config, context, _ = _live_activity_adapter(Path(scratch), name="u-guard")
        router = _LaunchRecorder()
        try:
            asyncio.run(adapter.infer(router=router, prompt="p", system="", config=config,
                                      context=context, observer=None, kind=kind))
        except Exception:  # noqa: BLE001 - a refusal is the answer
            return False
        return router.launches == [("writer", kind)]


def _activity_stops(kind: str) -> bool:
    """Whether a yield stops this kind's agent: a native call is cancelled
    mid-flight; an HTTP turn starts no further round."""
    import tempfile

    from tests.test_activity_http_yield import _LaunchRecorder, _live_activity_adapter
    from tinyassets import agent_activities
    from tinyassets.activity_runner import ActivityYielded

    class Endless:
        launches = []

        async def call(self, *args, **kwargs):
            await asyncio.Event().wait()

    async def yielded_during(adapter, config, context, router):
        task = asyncio.ensure_future(adapter.infer(
            router=router, prompt="p", system="", config=config, context=context,
            observer=None, kind=kind))
        await asyncio.sleep(0.3)
        binding = adapter.activity_binding
        agent_activities.wait_on(binding.universe_dir, binding.activity_id, "req-1", "asked")
        await asyncio.wait_for(task, timeout=5)

    with tempfile.TemporaryDirectory() as scratch:
        adapter, config, context, _ = _live_activity_adapter(Path(scratch), name="u-guard")
        try:
            if kind == "native_agent":
                asyncio.run(yielded_during(adapter, config, context, Endless()))
            else:
                binding = adapter.activity_binding
                agent_activities.wait_on(binding.universe_dir, binding.activity_id,
                                         "req-1", "asked")
                router = _LaunchRecorder()
                asyncio.run(adapter.infer(router=router, prompt="p", system="",
                                          config=config, context=context, observer=None,
                                          kind=kind))
        except ActivityYielded:
            return True
        except Exception:  # noqa: BLE001 - anything else is not a clean stop
            return False
        return False


def _claude(definition, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests.support.owned_spawn import install_fake_owned_spawn
    from tinyassets.providers import claude_provider

    class Captured(Exception):
        pass

    route = SimpleNamespace(url="http://127.0.0.1:8790/mcp", secret="s" * 43, grant_key="k")
    monkeypatch.setattr("tinyassets.engine_mcp_http.read_engine_mcp_route", lambda **_: route)
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(claude_provider, "_resolve_claude_cmd", lambda: (["claude"], False))
    monkeypatch.setattr(claude_provider, "subprocess_env_for_provider", lambda *a, **k: {})
    launch = install_fake_owned_spawn(monkeypatch, claude_provider.__name__,
                                      side_effect=Captured)
    config = ModelConfig(sandbox_workspace=True, engine_mcp_enabled=True,
                         engine_mcp_actor_id="owner", engine_mcp_graph_id="center")
    with pytest.raises(Captured):
        asyncio.run(claude_provider.ClaudeProvider().complete(
            "Reply OK.", definition.instructions, config, universe_dir=tmp_path))
    argv = list(launch.call_args.args)
    pairs = list(zip(argv, argv[1:]))
    native_tools_off = ("--tools", "") in pairs and "--strict-mcp-config" in argv
    servers = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())["mcpServers"]
    route_only = list(servers) == ["tinyassets"] and servers["tinyassets"]["url"].startswith(
        route.url + "?")
    # What Claude is shown is what the route lists for Claude's own URL: ask
    # the route, through its middleware, with that URL's query.
    query = parse_qs(urlsplit(servers["tinyassets"]["url"]).query)
    from tinyassets.served_tools import granted_tools, verified_launch_grant

    signed = verified_launch_grant("k", "", "", query["grant"][0])
    listed = _listed_for(servers["tinyassets"]["url"], monkeypatch)
    tools = tuple(AgentTool(t.name, t.description or "", dict(t.inputSchema or {}))
                  for t in listed) if (native_tools_off and route_only
                                       and signed == granted_tools(config)) else ()
    system = argv[argv.index("--system-prompt") + 1]
    return tools, system, _capabilities(route_tools=native_tools_off and route_only,
                                        kind="native_agent")


def _listed_for(url, monkeypatch):
    """The tools the engine route lists to a client that dialled ``url``."""
    from types import SimpleNamespace

    from fastmcp.server import dependencies

    from tinyassets.engine_mcp_server import mcp

    params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
    with monkeypatch.context() as patch:
        patch.setattr(dependencies, "get_http_request",
                      lambda: SimpleNamespace(query_params=params, scope={}))
        listed = asyncio.run(mcp.list_tools())
    return [Tool(name=t.name, description=t.description, inputSchema=t.parameters)
            for t in listed]


def _codex(definition, tmp_path, monkeypatch):
    from tests.support.fake_codex_app_server import FakeAppServer
    from tests.support.owned_spawn import install_fake_owned_spawn
    from tinyassets.providers import codex_app_server, codex_provider

    class Tools:
        tools = _route_tools()

    import contextlib

    @contextlib.asynccontextmanager
    async def open_engine_tools(**_):
        yield Tools()

    auth = tmp_path / ".runtime" / "auth"
    auth.mkdir(parents=True)
    monkeypatch.setattr("tinyassets.engine_tool_client.open_engine_tools", open_engine_tools)
    monkeypatch.setattr(codex_provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(codex_provider, "get_sandbox_status", lambda: {"bwrap_available": True})
    monkeypatch.setattr(codex_provider, "subprocess_env_for_provider",
                        lambda *a, **k: {"CODEX_HOME": str(auth)})
    monkeypatch.setattr(codex_provider, "_codex_sandbox_mounts", lambda command: [])
    monkeypatch.setattr(codex_provider, "_codex_home_file_mounts", lambda path: [])
    monkeypatch.setattr(codex_app_server, "bundled_catalog", lambda base_cmd, **k: {"models": [
        {"slug": "m", "tool_mode": "code_mode_only", "multi_agent_version": "v2"}]})
    held = {}
    launch = install_fake_owned_spawn(
        monkeypatch, codex_provider.__name__,
        side_effect=lambda argv, **kw: held.setdefault("server", FakeAppServer()))
    config = ModelConfig(sandbox_workspace=True, engine_mcp_enabled=True,
                         engine_mcp_actor_id="owner", engine_mcp_graph_id="center")
    asyncio.run(codex_provider.CodexProvider().complete(
        "Reply OK.", definition.instructions, config, universe_dir=tmp_path))
    argv = list(launch.call_args.args)
    catalog = json.loads((tmp_path / ".runtime" / "codex-model-catalog.json").read_text())
    native_tools_off = (
        argv[1:1 + len(codex_app_server.SERVED_LAUNCH_ARGS)]
        == list(codex_app_server.SERVED_LAUNCH_ARGS)
        and not any("mcp_servers" in arg for arg in argv)
        and all("tool_mode" not in m and m["multi_agent_version"] is None
                for m in catalog["models"]))
    (start,) = held["server"].requests("thread/start")
    tools = tuple(AgentTool(t["name"], t["description"], t["inputSchema"])
                  for t in start["params"]["dynamicTools"]) if native_tools_off else ()
    return tools, start["params"]["baseInstructions"], _capabilities(
        route_tools=native_tools_off, kind="native_agent")


def _render(kind, definition, tmp_path, monkeypatch):
    family, _, name = kind.partition(":")
    if family == "http":
        return _http(name, definition)
    if kind == "cli:claude-code":
        return _claude(definition, tmp_path, monkeypatch)
    if kind == "cli:codex":
        return _codex(definition, tmp_path, monkeypatch)
    if kind == "local:ollama":
        return None
    raise AssertionError(f"registered provider kind {kind} has no renderer in this guard")


# --- the guard -------------------------------------------------------------------

def test_the_guard_covers_every_registered_provider_kind():
    kinds = _registered_kinds()
    assert {"cli:codex", "cli:claude-code", "local:ollama"} <= set(kinds)
    assert {f"http:{p}" for p in _HTTP_PROTOCOLS} <= set(kinds)


@pytest.mark.parametrize("kind", _registered_kinds())
def test_every_agent_provider_renders_exactly_the_one_definition(
    kind, definition, tmp_path, monkeypatch,
):
    rendered = _render(kind, definition, tmp_path, monkeypatch)
    if rendered is None:
        _assert_refused_for_agent_turns(kind)
        return
    tools, instructions, capabilities = rendered
    assert sorted(t.key() for t in tools) == list(definition.tool_keys()), (
        f"{kind}: model-visible tools differ from the definition")
    assert instructions == definition.instructions, f"{kind}: instructions differ"
    assert capabilities == AGENT_CAPABILITIES, (
        f"{kind}: capabilities {sorted(capabilities)} != {sorted(AGENT_CAPABILITIES)}")


def _assert_refused_for_agent_turns(kind):
    """A kind without an agent executor never runs an agent turn with less."""
    family, _, name = kind.partition(":")
    if family == "http":
        # No codec -> executor_tools False (declared_models / discovery) ->
        # model policy excludes the model from any turn that needs tools.
        from tinyassets.providers.model_policy import (
            ConnectionModels,
            Interaction,
            Model,
            ModelPolicy,
            _ineligibility,
        )
        from tinyassets.providers.protocol_encoders import agent_codec_for

        assert agent_codec_for(name) is None
        model = Model("m", tools=True, modalities=frozenset({"text"}))
        connection = ConnectionModels(
            "c", "scope", "http", "fresh", owner_filtered=True,
            executor_tools=agent_codec_for(name) is not None, models=(model,))
        interaction = Interaction(needs_tools=True, modalities=frozenset({"text"}),
                                  charge_components=frozenset())
        policy = ModelPolicy(generation=1, mode="automatic", fallbacks=())
        assert _ineligibility(connection, model, policy, interaction, explicit=False) == (
            "executor_unsupported", "")
        return
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.ollama_provider import OllamaProvider
    from tinyassets.providers.router import ProviderRouter

    provider = OllamaProvider()
    assert getattr(provider, "agent_execution_kind", None) is None
    router = ProviderRouter({provider.name: provider})
    with pytest.raises(ProviderAuthorityHeldError):
        router.selected_agent_execution_kind(ModelRef(provider.name, "any"))


def test_the_definition_is_the_route_s_four_tools():
    definition = agent_definition(_route_tools(), INSTRUCTIONS)
    assert [t.name for t in definition.tools] == list(FOUR_MODEL_TOOLS)
    assert all(t.input_schema.get("type") == "object" for t in definition.tools)
    assert definition.capabilities == AGENT_CAPABILITIES


@pytest.mark.parametrize("budgeted", [False, True])
def test_the_turn_gives_every_executor_the_same_instructions(tmp_path, monkeypatch, budgeted):
    """The coordinator, not a renderer, decides the instructions a round
    carries. HTTP and native rounds of the same turn get the same text,
    the request-budget line included (it was HTTP-only until 2026-10-06)."""
    import contextlib
    from types import SimpleNamespace

    from tests.test_turn_interrupt import _NativeAdapter
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
    from tinyassets.daemon_server import grant_universe_ownership, set_founder_home

    class Seen(Exception):
        pass

    route_tools = tuple(_route_tools())

    def rendered(kind):
        base = tmp_path / kind
        (base / "u-guard").mkdir(parents=True)
        set_founder_home(base, founder_sub="owner", universe_id="u-guard",
                         platform_generated=True)
        grant_universe_ownership(base, universe_id="u-guard", owner_id="owner")
        seen = {}

        class Adapter(_NativeAdapter):
            async def infer(self, *, router, prompt, system, config, context, observer, kind):
                seen["system"] = system
                raise Seen()

        coordinator = AgentTurnCoordinator(
            adapter=Adapter(),
            router=SimpleNamespace(selected_agent_execution_kind=lambda selection: kind),
            prompt="Reply OK.", system=INSTRUCTIONS,
            universe_context=SimpleNamespace(
                universe_dir=base / "u-guard", agent_model_plan=None,
                model_selection=SimpleNamespace(connection_id="source", model_id="")),
            config=ModelConfig(absolute_cap_s=60.0),
        )

        @contextlib.asynccontextmanager
        async def no_tools(timeout):
            yield SimpleNamespace(tools=route_tools)

        coordinator._open_tools = no_tools
        line = SimpleNamespace(prompt_line=lambda: "You have 3 requests left today.")
        coordinator._daily_budget = lambda: line if budgeted else None
        with pytest.raises(Seen):
            asyncio.run(coordinator.run())
        return seen["system"]

    http, native = rendered("engine_inference"), rendered("native_agent")
    assert http == native
    assert ("You have 3 requests left today." in native) is budgeted
