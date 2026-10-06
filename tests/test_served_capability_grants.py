"""Cutover prerequisite: changing model visibility never changes ta authority."""
from types import SimpleNamespace

import pytest

from tests.test_ta_capabilities import (
    CATALOG,
    _setup,
    grant_engine,
    signed_launch,
    through_ta,
)
from tinyassets import served_tools as subject


@pytest.mark.parametrize("grant", [None, ("read_graph", "bash"), ("bash",)])
def test_visibility_does_not_change_signed_backend_authority(monkeypatch, grant):
    config = SimpleNamespace(engine_tool_grant=grant)
    before = subject.granted_tools(config)
    signed = subject.launch_grant("key", "thread:owner", "turn", before)
    monkeypatch.setattr(subject, "SERVED_ENGINE_MCP_TOOLS", subject.FOUR_MODEL_TOOLS)
    assert subject.granted_tools(config) == before
    assert subject.verified_launch_grant("key", "thread:owner", "turn", signed) == before
    assert subject.model_tools(config) == tuple(t for t in subject.FOUR_MODEL_TOOLS if t in before)
    assert subject.node_tool_grant(["agent", "read_graph", "bash"]) == ("read_graph", "bash")
    assert subject.verified_launch_grant("key", "thread:other", "turn", signed) is None


def test_four_visible_tools_keep_ta_catalog_and_connection_grant(tmp_path, monkeypatch):
    _, root, _ = _setup(tmp_path)
    calls = []
    # Capture a real backend inventory before reducing only the displayed set.
    server = grant_engine(monkeypatch, root, calls)
    monkeypatch.setattr(subject, "SERVED_ENGINE_MCP_TOOLS", subject.FOUR_MODEL_TOOLS)
    signed_launch(monkeypatch, subject.granted_tools(SimpleNamespace()))
    catalog, result = through_ta(server, CATALOG, {
        "op": "call", "name": "write_graph", "arguments": {"target": "app_ui"},
    })
    names = {item["name"] for item in catalog["capabilities"]}
    assert {"read_graph", "write_graph", "run_graph", "connection:conn-http:POST"} <= names
    assert not names.intersection(subject.FOUR_MODEL_TOOLS)
    assert result == {"result": {"called": "write_graph"}}
    assert calls == ["write_graph"]


def test_native_node_denials_do_not_depend_on_visible_registry(monkeypatch):
    from tinyassets.providers.base import ModelConfig
    from tinyassets.shared_self import _granted_config

    monkeypatch.setattr(subject, "SERVED_ENGINE_MCP_TOOLS", subject.FOUR_MODEL_TOOLS)
    config = _granted_config(ModelConfig(allowed_tools=(
        "mcp__tinyassets__read_graph", "mcp__tinyassets__write_graph",
    )), {"tools_allowed": ["agent", "read_graph", "bash"]})
    assert config.allowed_tools == ("mcp__tinyassets__read_graph",)
    assert "mcp__tinyassets__write_graph" in config.disallowed_tools
    assert config.engine_tool_grant == ("read_graph", "bash")


def test_coordinator_passes_four_handles_and_full_backend_grant(monkeypatch):
    from tinyassets import agent_turn_coordinator
    from tinyassets.providers.base import ModelConfig

    monkeypatch.setattr(subject, "SERVED_ENGINE_MCP_TOOLS", subject.FOUR_MODEL_TOOLS)
    captured = []
    monkeypatch.setattr(agent_turn_coordinator, "open_engine_tools",
                        lambda **kwargs: captured.append(kwargs))
    turn = SimpleNamespace(
        adapter=SimpleNamespace(engine_identity=lambda *_: ("owner", "center")),
        context=object(), config=ModelConfig(), steering=lambda: {},
    )
    agent_turn_coordinator.AgentTurnCoordinator._open_tools(turn, 60)
    assert captured[0]["enabled_tools"] == subject.FOUR_MODEL_TOOLS
    assert captured[0]["capability_grant"] == subject.BACKEND_ENGINE_CAPABILITIES


def test_codex_displays_four_handles_but_signs_full_backend_grant(monkeypatch):
    import re
    from urllib.parse import parse_qs, urlsplit

    from tinyassets import engine_mcp_http
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.codex_provider import _codex_engine_mcp_args

    monkeypatch.setattr(subject, "SERVED_ENGINE_MCP_TOOLS", subject.FOUR_MODEL_TOOLS)
    route = SimpleNamespace(url="http://127.0.0.1:8790/mcp", secret="secret", grant_key="key")
    monkeypatch.setattr(engine_mcp_http, "read_engine_mcp_route", lambda **_: route)
    config = ModelConfig(engine_mcp_enabled=True, engine_mcp_actor_id="owner",
                         engine_mcp_graph_id="center")
    args = _codex_engine_mcp_args(config, {})
    server = args[-1]
    assert 'enabled_tools=["read","write","edit","bash"]' in server
    url = re.search(r'url="([^"]+)"', server).group(1)
    grant = parse_qs(urlsplit(url).query)["grant"][0]
    assert subject.verified_launch_grant("key", "", "", grant) == (
        subject.BACKEND_ENGINE_CAPABILITIES
    )
