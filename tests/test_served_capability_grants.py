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
