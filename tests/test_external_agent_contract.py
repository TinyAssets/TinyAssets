"""Shipped package data cannot add a shell, path escape, or unknown transport."""
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from tinyassets.extension_manifest import ExtensionError, parse_manifest
from tinyassets.external_agents.registry import resolve


def _manifest():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tinyassets/external_agents/packages"
    return json.loads((path / "codex/extension.json").read_text())


def test_shipped_package_is_digest_pinned():
    package = resolve("codex")
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tinyassets/external_agents/packages"
    digest = hashlib.sha256((path / "codex/extension.json").read_bytes()).hexdigest()
    assert digest == package.digest


@pytest.mark.parametrize("mutation", [
    lambda row: row["job"]["start"]["params"].update(model="prefix-{model}"),
    lambda row: row["auth"]["files"].append("../auth.json"),
    lambda row: row["catalogue"].update(transport="shell"),
    lambda row: row["job"]["input"]["params"].update(input="{unknown}"),
])
def test_invalid_package_refused_before_launch(mutation):
    document = copy.deepcopy(_manifest())
    mutation(document["external_agents"][0])
    with pytest.raises(ExtensionError):
        parse_manifest(json.dumps(document).encode())


def test_final_turn_summary_supplies_text_when_item_event_is_missing():
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.providers.base import ModelConfig

    turn = JsonRpcJob(
        SimpleNamespace(), job=resolve("codex").row["job"], agent="codex",
        tools=None, profile=ModelConfig().stream_timeout_profile(),
        start=time.monotonic(), turn_wait=5, tool_wait=5,
    )
    turn._dispatch({"method": "thread/tokenUsage/updated", "params": {
        "tokenUsage": {"last": {"inputTokens": 26, "outputTokens": 7}}}})
    turn._dispatch({"method": "turn/completed", "params": {"turn": {
        "status": "completed", "items": [
            {"type": "agentMessage", "text": "ORACLE_OK"}],
    }}})
    assert turn.outcome.messages == ["ORACLE_OK"]
    assert turn.outcome.usage_seen


def test_reported_model_comes_from_declared_turn_event():
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.providers.base import ModelConfig

    turn = JsonRpcJob(
        SimpleNamespace(), job=resolve("codex").row["job"], agent="codex",
        tools=None, profile=ModelConfig().stream_timeout_profile(),
        start=time.monotonic(), turn_wait=5, tool_wait=5,
    )
    turn.outcome.configured_model = "configured"
    turn._dispatch({"method": "turn/started", "params": {
        "turn": {"model": "answering"}}})
    assert turn.outcome.reported_model == "answering"


def test_failed_native_turn_receipts_a_bounded_vendor_class():
    from tinyassets.exceptions import ProviderError
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.external_agents.runner import _job_failure_reason
    from tinyassets.providers.base import ModelConfig

    turn = JsonRpcJob(
        SimpleNamespace(), job=resolve("codex").row["job"], agent="codex",
        tools=None, profile=ModelConfig().stream_timeout_profile(),
        start=time.monotonic(), turn_wait=5, tool_wait=5,
    )
    turn.phase = "stream"
    turn._dispatch({"method": "turn/completed", "params": {"turn": {
        "status": "failed", "error": {"code": "invalid_grant", "message": "secret-value"},
    }}})
    reason = _job_failure_reason(ProviderError(
        "external-agent turn did not complete with text and usage"),
        turn, turn.outcome)
    assert reason == "job_failed:incomplete"
    assert "secret-value" not in reason


def test_unavailable_codex_binding_is_loud(tmp_path, _owner_box_double):
    import asyncio

    from tinyassets.boxes.provider import ExecLimits
    from tinyassets.control_plane.binding import bind_session, use_binding
    from tinyassets.exceptions import ExternalAgentUnavailable
    from tinyassets.external_agents.provider import ExternalAgentProvider
    from tinyassets.providers.base import ModelConfig

    assert _owner_box_double is not None, "the owner box double needs Linux"
    center = tmp_path / "u-owner"
    center.mkdir()
    # An admitted owner session with no codex credential in custody.
    binding = bind_session(provider=_owner_box_double, limits=ExecLimits(), universe_dir=center,
                           owner="owner", execution_id="codex-signed-out")
    with use_binding(binding), pytest.raises(ExternalAgentUnavailable) as failure:
        asyncio.run(ExternalAgentProvider("codex").complete(
            "hello", "system", ModelConfig(invocation_owner_user_id="owner"),
            universe_dir=center))
    assert failure.value.agent == "codex"
    assert failure.value.reason == "signed_out"


def test_codex_terminal_auth_error_is_classified_without_leaking_its_message():
    import time

    from tinyassets.exceptions import ProviderAuthenticationError
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.external_agents.runner import _job_failure_reason
    from tinyassets.providers.base import ModelConfig

    turn = JsonRpcJob(
        SimpleNamespace(returncode=None), job=resolve("codex").row["job"],
        agent="codex", tools=None, profile=ModelConfig().stream_timeout_profile(),
        start=time.monotonic(), turn_wait=5, tool_wait=5,
    )
    turn._dispatch({"method": "turn/completed", "params": {"turn": {
        "status": "failed", "error": {
            "message": "secret-value", "codexErrorInfo": "unauthorized"},
    }}})
    assert turn.outcome.auth_rejected
    assert _job_failure_reason(ProviderAuthenticationError("fresh sign-in"), turn,
                               turn.outcome) == "auth_invalid"


def test_codex_terminal_non_auth_error_does_not_mark_credential_invalid():
    import time

    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.providers.base import ModelConfig

    turn = JsonRpcJob(
        SimpleNamespace(returncode=None), job=resolve("codex").row["job"],
        agent="codex", tools=None, profile=ModelConfig().stream_timeout_profile(),
        start=time.monotonic(), turn_wait=5, tool_wait=5,
    )
    turn._dispatch({"method": "turn/completed", "params": {"turn": {
        "status": "failed", "error": {
            "message": "Tool returned 401 unauthorized",
            "codexErrorInfo": "other"},
    }}})
    assert not turn.outcome.auth_rejected


def test_job_declaration_uses_the_one_agent_definition():
    from mcp.types import Tool

    from tinyassets.agent_definition import agent_definition
    from tinyassets.external_agents.jsonrpc_job import dynamic_tools, method_params
    from tinyassets.served_tools import FOUR_MODEL_TOOLS

    definition = agent_definition([
        Tool(name=name, description=name, inputSchema={"type": "object"})
        for name in FOUR_MODEL_TOOLS], "Owner instructions")
    job = resolve("codex").row["job"]
    params = method_params(job["start"], {
        "model": "gpt-6.1-sol", "workdir": "/agent-home/jobs/job",
        "system": definition.instructions,
    })
    assert params["baseInstructions"] == definition.instructions
    assert [tool["name"] for tool in dynamic_tools(definition)] == list(FOUR_MODEL_TOOLS)
    assert job["tools"]["define_at"] == "/dynamicTools"
