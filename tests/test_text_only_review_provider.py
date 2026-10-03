"""Restrictive reviews: actual adapter boundaries, no model or account calls."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from tests import test_code_only_effect_review as composition
from tests.test_api_key_http_provider import _definition, _FakeProxy, _seed
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.base import ModelConfig
from tinyassets.storage.provider_work_authority import db_path

rig = composition.rig


@pytest.mark.parametrize("adapter,method", [
    ("codex_provider", "complete"),
    ("claude_provider", "complete"),
    ("claude_provider", "complete_json"),
])
@pytest.mark.parametrize("extra", [
    {}, {"sandbox_workspace": True, "sandbox_chat": True},
    {"engine_mcp_enabled": True, "engine_mcp_actor_id": "owner",
     "engine_mcp_graph_id": "universe"},
    {"agent_session": object()}, {"allowed_tools": ("Bash", "WebFetch")},
])
def test_native_text_only_refuses_before_environment_argv_or_spawn(
    tmp_path, monkeypatch, adapter, method, extra,
):
    import importlib

    module = importlib.import_module("tinyassets.providers." + adapter)
    cls = module.CodexProvider if adapter == "codex_provider" else module.ClaudeProvider
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("native boundary reached")

    monkeypatch.setattr(module, "aspawn_owned", forbidden)
    monkeypatch.setattr(module, "subprocess_env_for_provider", forbidden)
    if adapter == "codex_provider":
        monkeypatch.setattr(cls, "native_command_resolver", staticmethod(forbidden))
    else:
        monkeypatch.setattr(module, "_resolve_claude_cmd", forbidden)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ambient-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ambient-claude"))
    with pytest.raises(ProviderAuthorityHeldError, match="does not support enforced text-only"):
        asyncio.run(getattr(cls(), method)(
            "review", "requirements", ModelConfig(text_only=True, **extra),
            universe_dir=tmp_path))
    assert attempts == [], "no argv, environment, credential resolution or subprocess is allowed"


def test_empty_claude_allowlist_does_not_enable_review(tmp_path):
    from tinyassets.providers.claude_provider import _sandbox_cli_args

    with pytest.raises(ProviderAuthorityHeldError, match="text-only"):
        _sandbox_cli_args(ModelConfig(text_only=True, allowed_tools=()), tmp_path)


def test_router_refuses_unsupported_selected_provider_without_fallback(rig):
    from tinyassets.providers.claude_provider import ClaudeProvider
    from tinyassets.providers.codex_provider import CodexProvider

    # Other registered executors do not authorize a substitution. The native
    # provider is selected by the real receipt; it must refuse before complete.
    rig["router"].register(CodexProvider())
    rig["router"].register(ClaudeProvider())
    result = rig["fire"]()
    assert result["error_kind"] == "auto_review_unavailable"
    assert "does not support enforced text-only" in result["review"]["reason"]
    assert not rig["terminal"].calls and not rig["sends"]
    with sqlite3.connect(db_path(rig["tmp_path"])) as conn:
        assert conn.execute("SELECT state FROM provider_invocation_reservations").fetchall() == [
            ("cancelled_before_launch",)]


@pytest.fixture
def http(tmp_path):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    universe = tmp_path / "u-x"
    universe.mkdir()
    _seed(tmp_path)
    proxy = _FakeProxy({"status": 200, "body": json.dumps({
        "choices": [{"message": {"content": '{"verdict":"proceed","reason":"ok"}'}}],
    })})
    return ApiKeyHttpProvider(_definition(), proxy_override=proxy), proxy, universe


@pytest.mark.parametrize("protocol", ["openai_chat", "anthropic_messages"])
def test_http_text_only_reaches_only_plain_text_wire(http, protocol):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    _, proxy, universe = http
    if protocol == "anthropic_messages":
        proxy.response["body"] = json.dumps({"content": [{"type": "text", "text": "ok"}]})
    provider = ApiKeyHttpProvider(_definition(protocol), proxy_override=proxy)
    result = asyncio.run(provider.complete("review", "requirements", ModelConfig(text_only=True),
                                           universe_dir=universe))
    assert result.text
    assert len(proxy.calls) == 1
    verb, wire = proxy.calls[0]
    assert verb == "POST"
    assert wire["body"].keys() <= {"model", "messages", "system", "temperature", "max_tokens"}
    assert all(set(message) == {"role", "content"} and isinstance(message["content"], str)
               for message in wire["body"]["messages"])


@pytest.mark.parametrize("conflict", [
    {"engine_mcp_enabled": True}, {"engine_mcp_actor_id": "foreign"},
    {"engine_mcp_graph_id": "foreign"}, {"engine_tool_grant": ("send",)},
    {"agent_request": object()}, {"agent_session": object()},
    {"allowed_tools": ("Bash",)}, {"agent_node_id": "agent"},
    {"text_only": "true"},
])
def test_http_conflicting_config_never_reaches_proxy(http, conflict):
    provider, proxy, universe = http
    cfg = replace(ModelConfig(text_only=True), **conflict)
    with pytest.raises(ProviderAuthorityHeldError, match="text-only"):
        asyncio.run(provider.complete("review", "requirements", cfg, universe_dir=universe))
    # The synchronous entry is equally restrictive.
    with pytest.raises(ProviderAuthorityHeldError, match="text-only"):
        provider._complete_sync("review", "requirements", cfg, universe_dir=universe)
    assert not proxy.calls


@pytest.mark.parametrize("extra", [
    {"tools": []}, {"tool_choice": "auto"}, {"plugins": [{"id": "web"}]},
    {"functions": []}, {"unknown_extension": True},
    {"messages": [{"role": "assistant", "content": "prior session"}]},
    {"messages": [{"role": "user", "content": [{"type": "tool_use"}]}]},
])
def test_http_rejects_tool_or_unknown_wire_extensions_before_send(http, extra):
    provider, proxy, universe = http
    encode = provider._encode

    def poisoned(**kwargs):
        path, body = encode(**kwargs)
        return path, {**body, **extra}

    provider._encode = poisoned
    with pytest.raises(ProviderAuthorityHeldError, match="HTTP request does not support"):
        provider._complete_sync("review", "requirements", ModelConfig(text_only=True),
                                universe_dir=universe)
    assert not proxy.calls


@pytest.mark.parametrize("rig", ["http"], indirect=True)
def test_http_missing_usage_consumes_reservation_without_renewing_budget(rig):
    result = rig["fire"]()
    assert result.get("delivered"), result
    rig["terminal"].answers.append('{"verdict":"proceed","reason":"ok"}')
    assert rig["fire"]().get("delivered")
    assert rig["fire"]()["error_kind"] == "auto_review_unavailable"
    assert len(rig["terminal"].calls) == len(rig["sends"]) == 2
    with sqlite3.connect(db_path(rig["tmp_path"])) as conn:
        assert conn.execute("SELECT state FROM provider_invocation_reservations").fetchall() == [
            ("indeterminate",), ("indeterminate",)]


@pytest.mark.parametrize("tool", [
    {"tool_calls": [{"id": "t", "function": {"name": "send"}}]},
    {"function_call": {"name": "send"}},
    {"blocks": [{"type": "tool_use", "name": "send"}]},
])
def test_http_reply_cannot_smuggle_tool_request_alongside_verdict(http, tool):
    provider, proxy, universe = http
    proxy.response["body"] = json.dumps({"choices": [{"message": {
        "content": '{"verdict":"proceed","reason":"ok"}', **tool}}]})
    with pytest.raises(ProviderAuthorityHeldError, match="returned a tool request"):
        provider._complete_sync("review", "requirements", ModelConfig(text_only=True),
                                universe_dir=universe)
    assert len(proxy.calls) == 1  # Inference only; no follow-up tool dispatch exists.


def test_codex_metadata_registration_reuses_read_only_install_mounts(tmp_path, monkeypatch):
    from tinyassets.providers import codex_provider as codex
    from tinyassets.providers.provider_jail import UniverseView, _install_binds

    install = tmp_path / "install"
    install.mkdir()
    wrapper = install / "codex"
    wrapper.touch()
    binary_tree = install / "vendor"
    binary_tree.mkdir()
    binary = binary_tree / "codex"
    binary.touch()
    universe = tmp_path / "data" / "universe"
    universe.mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(universe.parent))
    monkeypatch.setattr(codex, "_resolved_codex_executable", lambda argv: (wrapper, binary))
    monkeypatch.setattr(codex, "_codex_binary_tree", lambda executable: binary_tree)
    assert codex.CodexProvider.native_install_mounts is codex._codex_sandbox_mounts
    paths = codex.CodexProvider().native_install_mounts([str(wrapper)])
    assert paths == (binary_tree, install)
    view = UniverseView(universe_dir=universe, mounts=())
    binds = _install_binds(paths, view, [])
    assert binds and all(binds[index] == "--ro-bind" for index in range(0, len(binds), 3))
    assert str(universe) not in binds
    with pytest.raises(ProviderAuthorityHeldError, match="overlaps command center data"):
        _install_binds([universe], view, [])


def test_foreground_attempt_cannot_drop_review_restriction(rig):
    from tinyassets import agent_review

    purpose = agent_review._ReviewPurpose(
        rig["wrapper"], rig["universe"], rig["run_id"], "a" * 64, "review text")
    token = agent_review._PURPOSE.set(purpose)
    try:
        with pytest.raises(PermissionError, match="cannot drop its text-only restriction"):
            rig["session"]._call_once("writer", "review text", agent_review.SAFETY_REQUIREMENTS,
                                      ModelConfig(), None, {})
    finally:
        agent_review._PURPOSE.reset(token)
    assert not rig["terminal"].calls and not rig["sends"]
