"""Pin chat-only refusals before introducing a separate workflow adapter."""

import asyncio
from types import SimpleNamespace

import pytest

from tinyassets.provider_assignment import check_served_agent_tool_authority
from tinyassets.providers.base import ModelConfig, UniverseContext
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.router import ProviderRouter


def test_chat_tool_fence_rejects_work_carrier(tmp_path):
    with pytest.raises(PermissionError, match="requires a current served request"):
        check_served_agent_tool_authority(UniverseContext(
            universe_dir=tmp_path, provider_invocation=object(),
        ))


@pytest.mark.parametrize("kind", ["engine_inference", "native_agent"])
def test_agent_kind_cannot_turn_a_work_carrier_into_chat(tmp_path, kind):
    with pytest.raises(PermissionError, match="agent step requires the selected served writer"):
        asyncio.run(ProviderRouter({}).call(
            "writer", "hello", "", ModelConfig(engine_mcp_enabled=True),
            operation="converse", _agent_execution_kind=kind,
            universe_context=UniverseContext(
                universe_dir=tmp_path, provider_invocation=object(),
                model_selection=ModelRef("source", "model"),
            ),
        ))


def test_unarmed_structured_request_cannot_infer():
    with pytest.raises(
        PermissionError, match="agent inference requires the selected served writer",
    ):
        asyncio.run(ProviderRouter({})._call_routed(
            "writer", "hello", "", ModelConfig(agent_request=object(), engine_mcp_enabled=True),
            operation="converse", universe_context=None,
        ))


def test_selected_http_tools_require_the_coordinator(tmp_path, monkeypatch):
    # Only reach the existing pre-launch guard; this fake is never launch proof.
    from tinyassets.providers import router as module

    authority = SimpleNamespace(
        provider="owned-http", selected_model=SimpleNamespace(provider="owned-http"),
        native_selection=None, settlement_owner=None,
        _receipt=SimpleNamespace(principal_id="owner"),
    )
    monkeypatch.setattr(module, "_provider_invocation_carrier", lambda *a, **k: authority)
    with pytest.raises(
        PermissionError, match="selected HTTP agent tool execution is not implemented",
    ):
        asyncio.run(ProviderRouter({})._call_routed(
            "writer", "hello", "", ModelConfig(engine_mcp_enabled=True),
            operation="run_graph", universe_context=UniverseContext(universe_dir=tmp_path),
        ))
