"""A served Codex turn is the one agent definition over ``codex app-server``.

The model-visible tools are the engine route's granted tools as
``dynamicTools``; Codex runs none of them, every call is forwarded through the
engine route, and no MCP server, bearer or native tool reaches the jail. The
fake server (tests/support/fake_codex_app_server.py) stands in for the CLI;
the real CLI's request captures are recorded in the K2 implementation
evidence.
"""

import asyncio
import contextlib
import json
import sys

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent, Tool

from tests.support.fake_codex_app_server import FakeAppServer, Turn
from tests.support.owned_spawn import install_fake_owned_spawn
from tinyassets import agent_sessions
from tinyassets.agent_definition import agent_definition
from tinyassets.engine_tool_client import EngineToolError
from tinyassets.exceptions import (
    InteractiveDeadlineError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderIdleTimeoutError,
    ProviderUnavailableError,
)
from tinyassets.providers import codex_app_server as app
from tinyassets.providers.base import ModelConfig
from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES, FOUR_MODEL_TOOLS

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="native sessions are held with flock (Linux hosts)",
)

BUNDLED = {"models": [
    {"slug": "gpt-6-astra", "display_name": "GPT-6", "tool_mode": "code_mode_only",
     "multi_agent_version": "v2", "apply_patch_tool_type": "freeform",
     "experimental_supported_tools": ["exec"], "supports_search_tool": True},
    {"slug": "gpt-5.5", "display_name": "GPT-5.5", "multi_agent_version": "v1"},
]}


def _engine_tools():
    return [Tool(name=name, description=f"{name} a file in /u",
                 inputSchema={"type": "object", "properties": {"path": {"type": "string"}},
                              "required": ["path"], "additionalProperties": False})
            for name in FOUR_MODEL_TOOLS]


class FakeTools:
    def __init__(self, results=None):
        self.tools = _engine_tools()
        self.calls = []
        self._results = results or {}

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        result = self._results.get(name)
        if isinstance(result, BaseException):
            raise result
        return result or CallToolResult(content=[TextContent(type="text", text=f"{name} ok")])


@pytest.fixture
def served(monkeypatch, tmp_path):
    """The real served path with only the spawn, sandbox probe and route faked."""
    from tinyassets.providers import codex_provider as provider

    monkeypatch.delenv("TINYASSETS_CODEX_MODEL", raising=False)
    auth_dir = tmp_path / ".runtime" / "auth"
    auth_dir.mkdir(parents=True)
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(provider, "get_sandbox_status", lambda: {
        "bwrap_available": True, "bwrap_path": "fake-bwrap"})
    monkeypatch.setattr(provider, "subprocess_env_for_provider", lambda *a, **kw: {
        "CODEX_HOME": str(auth_dir), "TINYASSETS_ENGINE_MCP_BEARER": "stale-secret"})
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda command: [])
    monkeypatch.setattr(provider, "_codex_home_file_mounts", lambda path: [])
    monkeypatch.setattr(app, "bundled_catalog", lambda base_cmd, **kw: BUNDLED)
    state = {"tools": FakeTools(), "opened": [], "server": None}

    @contextlib.asynccontextmanager
    async def open_engine_tools(**kwargs):
        state["opened"].append(kwargs)
        if isinstance(state["tools"], BaseException):
            raise state["tools"]
        yield state["tools"]

    monkeypatch.setattr("tinyassets.engine_tool_client.open_engine_tools", open_engine_tools)

    def spawn(argv, **kwargs):
        return state["server"]

    launch = install_fake_owned_spawn(monkeypatch, provider.__name__, side_effect=spawn)

    def config(**overrides):
        fields = dict(sandbox_workspace=True, engine_mcp_enabled=True,
                      engine_mcp_actor_id="acct_alice", engine_mcp_graph_id="u-alice")
        fields.update(overrides)
        return ModelConfig(**fields)

    async def run(turn=None, *, cfg=None, prompt="fresh prompt", system="system", server=None):
        state["server"] = server or FakeAppServer(turn)
        response = await provider.CodexProvider().complete(
            prompt, system, cfg or config(), universe_dir=tmp_path)
        return response, state["server"]

    return run, launch, state, config, tmp_path


# --- the launch: app-server, no native tool, no MCP server -------------------

@pytest.mark.asyncio
async def test_served_launch_is_the_app_server_with_every_native_tool_off(served):
    run, launch, _state, _config, root = served
    await run()
    argv = list(launch.call_args.args)
    assert argv[:2] == ["codex", "app-server"]
    assert argv[1:1 + len(app.SERVED_LAUNCH_ARGS)] == list(app.SERVED_LAUNCH_ARGS)
    assert argv[-2:] == ["-c", 'model_catalog_json="/codex-home/model-catalog.json"']
    flat = " ".join(argv)
    for absent in ("exec", "mcp_servers", "--json", 'web_search="cached"', "workspace-write"):
        assert absent not in argv and absent not in flat.split()
    assert "mcp_servers" not in flat
    kwargs = launch.call_args.kwargs
    assert not kwargs.get("nested_sandbox"), "codex runs nothing that needs its own sandbox"
    assert "TINYASSETS_ENGINE_MCP_BEARER" not in kwargs["env"], "no bearer enters the jail"
    mounts = {(m.dest, str(m.source)) for m in kwargs["universe_view"].mounts if m.source}
    assert ("/codex-home/model-catalog.json",
            str(root.resolve() / ".runtime" / "codex-model-catalog.json")) in mounts


@pytest.mark.asyncio
async def test_the_launch_catalog_clears_every_model_pinned_native_tool(served):
    run, *_rest, root = served
    await run()
    catalog = json.loads((root / ".runtime" / "codex-model-catalog.json").read_text())
    for entry in catalog["models"]:
        assert "tool_mode" not in entry
        assert entry["multi_agent_version"] is None and entry["apply_patch_tool_type"] is None
        assert entry["experimental_supported_tools"] == [] and not entry["supports_search_tool"]


def test_a_requested_model_the_catalog_lacks_is_added_not_defaulted():
    catalog = app.reduced_catalog(BUNDLED, "gpt-7-new")
    assert [m["slug"] for m in catalog["models"]][-1] == "gpt-7-new"
    assert BUNDLED["models"][0]["tool_mode"] == "code_mode_only", "input not mutated"
    assert len(app.reduced_catalog(BUNDLED, "gpt-5.5")["models"]) == 2


# --- what the model sees: exactly the definition -----------------------------

@pytest.mark.asyncio
async def test_the_thread_carries_exactly_the_definitions_tools_and_instructions(served):
    run, *_ = served
    _, server = await run(system="You are the owner's agent.", prompt="hello")
    (start,) = server.requests("thread/start")
    definition = agent_definition(_engine_tools(), "You are the owner's agent.")
    assert start["params"]["dynamicTools"] == app.dynamic_tools(definition)
    assert [t["name"] for t in start["params"]["dynamicTools"]] == list(FOUR_MODEL_TOOLS)
    assert start["params"]["baseInstructions"] == "You are the owner's agent."
    (turn,) = server.requests("turn/start")
    assert turn["params"]["input"] == [{"type": "text", "text": "hello"}]
    assert server.requests("initialize")[0]["params"]["capabilities"] == {"experimentalApi": True}


@pytest.mark.asyncio
async def test_a_turn_without_engine_tools_declares_none(served):
    run, _launch, state, config, _ = served
    _, server = await run(cfg=config(engine_mcp_enabled=False))
    assert server.requests("thread/start")[0]["params"]["dynamicTools"] == []
    assert state["opened"] == []


# --- every tool call crosses the engine route --------------------------------

@pytest.mark.asyncio
async def test_tool_calls_are_forwarded_through_the_engine_route(served):
    run, _launch, state, _config, _ = served
    png = CallToolResult(content=[ImageContent(type="image", data="aGk=", mimeType="image/png")])
    failed = CallToolResult(content=[TextContent(type="text", text="no such file")], isError=True)
    state["tools"] = FakeTools({"read": png, "edit": failed})
    response, server = await run(Turn(calls=[
        {"tool": "bash", "arguments": {"command": "ls"}},
        {"tool": "read", "arguments": {"path": "a.png"}},
        {"tool": "edit", "arguments": {"path": "x"}},
    ]))
    assert state["tools"].calls == [("bash", {"command": "ls"}), ("read", {"path": "a.png"}),
                                    ("edit", {"path": "x"})]
    assert server.tool_results == [
        {"success": True, "contentItems": [{"type": "inputText", "text": "bash ok"}]},
        {"success": True, "contentItems": [{"type": "inputImage",
                                            "imageUrl": "data:image/png;base64,aGk="}]},
        {"success": False, "contentItems": [{"type": "inputText", "text": "no such file"}]},
    ]
    assert response.text == "done"


@pytest.mark.asyncio
async def test_the_route_is_opened_with_the_turns_session_turn_and_full_grant(served):
    run, _launch, state, config, _ = served
    session = agent_sessions.AgentSessionRef(
        universe_dir=served[4], key="thread:acct_alice:main",
        fresh_prompt_digest=agent_sessions.digest("fresh prompt"),
        resume_prompt="new", built_at=1.0)
    await run(cfg=config(agent_session=session))
    (opened,) = state["opened"]
    assert opened["actor_id"] == "acct_alice" and opened["graph_id"] == "u-alice"
    assert opened["session_key"] == "thread:acct_alice:main"
    assert tuple(opened["enabled_tools"]) == FOUR_MODEL_TOOLS
    assert tuple(opened["capability_grant"]) == BACKEND_ENGINE_CAPABILITIES


@pytest.mark.parametrize("call", [
    {"tool": "apply_patch", "arguments": {"patch": "x"}, "namespace": "functions"},
    {"tool": "read", "arguments": "not-an-object"},
])
@pytest.mark.asyncio
async def test_a_call_outside_the_definition_never_reaches_the_route(served, call):
    run, _launch, state, *_ = served
    _, server = await run(Turn(calls=[call]))
    assert state["tools"].calls == []
    assert server.tool_results[0]["success"] is False


@pytest.mark.asyncio
async def test_an_engine_failure_reaches_the_model_as_a_failed_call(served):
    run, _launch, state, *_ = served
    state["tools"] = FakeTools({"bash": EngineToolError("engine_tool_outcome_unknown",
                                                        outcome="unknown")})
    _, server = await run(Turn(calls=[{"tool": "bash", "arguments": {"command": "x"}}]))
    assert server.tool_results == [{"success": False, "contentItems": [
        {"type": "inputText", "text": "tool call failed: engine_tool_outcome_unknown"}]}]


@pytest.mark.asyncio
async def test_codex_requests_outside_the_definition_are_declined(served):
    run, *_ = served
    _, server = await run(Turn(server_requests=[
        "item/commandExecution/requestApproval", "item/tool/requestUserInput"]))
    assert [m["error"]["code"] for m in server.declined] == [-32601, -32601]


@pytest.mark.asyncio
async def test_unreachable_engine_tools_fail_the_turn_before_launch(served):
    run, launch, state, *_ = served
    state["tools"] = EngineToolError("engine_tools_unavailable")
    with pytest.raises(ProviderUnavailableError, match="engine_tools_unavailable"):
        await run()
    assert launch.call_count == 0


# --- the turn's terminal ------------------------------------------------------

@pytest.mark.asyncio
async def test_reply_and_usage_come_from_the_turn(served):
    run, *_ = served
    response, server = await run(Turn(reply="answer", usage=(10, 4, 3)))
    assert response.text == "answer"
    assert (response.input_tokens, response.output_tokens) == (10, 7)
    assert response.cost_microunits == 1700
    assert server.killed, "the process is ended once the turn completes"


@pytest.mark.asyncio
async def test_a_signed_out_turn_is_a_sign_in_failure(served):
    run, *_ = served
    with pytest.raises(ProviderAuthenticationError):
        await run(Turn(status="failed", error="Please log in again"))


@pytest.mark.asyncio
async def test_a_failed_turn_reports_its_reason_redacted(served):
    run, *_ = served
    with pytest.raises(ProviderError, match="model rejected") as failure:
        await run(Turn(status="failed", error="model rejected sk-secretsensitive123"))
    assert "secretsensitive" not in str(failure.value)


@pytest.mark.asyncio
async def test_a_turn_without_a_reply_fails_loudly(served):
    run, *_ = served
    with pytest.raises(ProviderError, match="omitted its result"):
        await run(Turn(reply=""))


@pytest.mark.asyncio
async def test_a_silent_turn_ends_on_the_idle_watchdog(served, monkeypatch):
    from tinyassets.providers import codex_provider as provider

    run, *_ = served
    monkeypatch.setattr(provider, "_TURN_WAIT_S", 0.2)
    server = FakeAppServer(Turn(hang=True))
    with pytest.raises(ProviderIdleTimeoutError):
        await run(server=server)
    assert server.killed


@pytest.mark.asyncio
async def test_the_absolute_cap_bounds_a_turn(served, monkeypatch):
    run, _launch, _state, config, _ = served
    server = FakeAppServer(Turn(hang=True))
    with pytest.raises(InteractiveDeadlineError):
        await run(server=server, cfg=config(absolute_cap_s=0.3))
    assert server.killed


@pytest.mark.asyncio
async def test_cancelling_the_turn_ends_the_process(served):
    run, *_ = served
    server = FakeAppServer(Turn(hang=True))
    task = asyncio.create_task(run(server=server))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert server.killed


# --- native session continuity (more in tests/test_agent_sessions.py) ---------

def _ref(udir, *, prompt="fresh prompt", resume="new message", key="thread:principal:o"):
    return agent_sessions.AgentSessionRef(
        universe_dir=udir, key=key, fresh_prompt_digest=agent_sessions.digest(prompt),
        resume_prompt=resume, built_at=100.0)


@posix_only
@pytest.mark.asyncio
async def test_a_changed_tool_set_starts_a_new_thread(served, monkeypatch):
    from tinyassets.providers import codex_provider as provider

    run, _launch, state, config, root = served
    ref = _ref(root)
    await run(cfg=config(agent_session=ref))
    monkeypatch.setattr(provider, "_native_session_exists", lambda store, handle: True)
    state["tools"] = FakeTools()
    state["tools"].tools = state["tools"].tools[:2]
    _, server = await run(cfg=config(agent_session=ref))
    assert server.requests("thread/resume") == []
    assert len(server.requests("thread/start")) == 1
