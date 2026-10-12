"""S2 — per-universe engine assignment (BYO API key) via universe action=set_engine.

Covers: config.yaml partial-merge write path, the llm_api_key vault type +
CLI-subprocess env injection, the founder-only set_engine action, and that the
API key never reaches the response or the ledger.
"""
from __future__ import annotations

import base64
import json

from tinyassets.config import load_universe_config, write_universe_config_fields
from tinyassets.credential_vault import (
    provider_auth_env_overrides,
    resolve_llm_api_key,
    supported_llm_api_key_services,
    write_credential_vault,
)
from tinyassets.providers.base import subprocess_env_for_provider


def test_write_universe_config_fields_merges_and_preserves(tmp_path):
    write_universe_config_fields(tmp_path, preferred_writer="codex")
    assert load_universe_config(tmp_path).preferred_writer == "codex"
    # A second partial write must preserve the earlier field.
    write_universe_config_fields(tmp_path, preferred_judge="gemini-free")
    cfg = load_universe_config(tmp_path)
    assert cfg.preferred_writer == "codex"
    assert cfg.preferred_judge == "gemini-free"


def test_llm_api_key_injects_into_claude_cli_env(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_api_key",
        "service": "anthropic",
        "secret_b64": base64.b64encode(b"sk-ant-test-XYZ").decode("ascii"),
    }])
    assert resolve_llm_api_key(tmp_path, "ANTHROPIC_API_KEY") == "sk-ant-test-XYZ"
    # provider_auth_env_overrides maps it to the CLI env var.
    overrides = provider_auth_env_overrides(tmp_path, "claude-code")
    assert overrides["ANTHROPIC_API_KEY"] == "sk-ant-test-XYZ"
    # The subprocess env carries it even though the global api-key strip runs first.
    env = subprocess_env_for_provider("claude-code", universe_dir=tmp_path)
    assert env.get("ANTHROPIC_API_KEY") == "sk-ant-test-XYZ"


def test_openai_key_maps_to_codex_only(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_api_key",
        "service": "openai",
        "secret_b64": base64.b64encode(b"sk-openai-test").decode("ascii"),
    }])
    # The deposit still resolves to the OpenAI env name. Codex no longer takes a
    # child-process overlay: the package runner materializes owner custody, and
    # the key must not leak onto Claude's overlay either.
    assert resolve_llm_api_key(tmp_path, "OPENAI_API_KEY") == "sk-openai-test"
    assert provider_auth_env_overrides(tmp_path, "codex") == {}
    claude = provider_auth_env_overrides(tmp_path, "claude-code")
    assert "OPENAI_API_KEY" not in claude
    assert "ANTHROPIC_API_KEY" not in claude
    child = subprocess_env_for_provider("claude-code", universe_dir=tmp_path)
    assert "sk-openai-test" not in child.values()


def test_set_engine_action_writes_vault_and_config(tmp_path, monkeypatch):
    from tinyassets.api import universe as uni

    udir = tmp_path / "u-test"
    udir.mkdir()
    monkeypatch.setattr(uni, "_request_universe", lambda universe_id="": "u-test")
    monkeypatch.setattr(uni, "_universe_dir", lambda uid: udir)

    out = json.loads(uni._action_set_engine(
        universe_id="u-test",
        inputs_json=json.dumps({"service": "anthropic", "api_key": "sk-secret-KEY"}),
    ))
    assert out["status"] == "engine_set"
    assert out["preferred_writer"] == "claude-code"  # inferred from service
    # The key is NEVER echoed in the response.
    assert "sk-secret-KEY" not in json.dumps(out)
    # config.yaml + vault were written and resolve end-to-end.
    assert load_universe_config(udir).preferred_writer == "claude-code"
    assert resolve_llm_api_key(udir, "ANTHROPIC_API_KEY") == "sk-secret-KEY"


def test_set_engine_requires_key_and_known_service(tmp_path, monkeypatch):
    from tinyassets.api import universe as uni

    udir = tmp_path / "u-x"
    udir.mkdir()
    monkeypatch.setattr(uni, "_request_universe", lambda universe_id="": "u-x")
    monkeypatch.setattr(uni, "_universe_dir", lambda uid: udir)

    missing_key = json.loads(uni._action_set_engine(
        universe_id="u-x", inputs_json=json.dumps({"service": "anthropic"})))
    assert "error" in missing_key

    bad_service = json.loads(uni._action_set_engine(
        universe_id="u-x",
        inputs_json=json.dumps({"service": "nonsense", "api_key": "k"})))
    assert "error" in bad_service
    assert "nonsense" in bad_service["error"]


def test_ledger_extractor_never_leaks_the_key():
    from tinyassets.api.universe import _extract_set_engine

    target, summary, payload = _extract_set_engine(
        {"inputs_json": json.dumps({"api_key": "sk-SECRET-LEDGER"})},
        {"universe_id": "u-1", "service": "anthropic",
         "preferred_writer": "claude-code", "status": "engine_set"},
    )
    assert "sk-SECRET-LEDGER" not in json.dumps([target, summary, payload])


def test_set_engine_is_founder_admin_scoped():
    from tinyassets.api.universe import WRITE_ACTIONS
    from tinyassets.auth.provider import _UNIVERSE_ADMIN_ACTIONS

    # Founder-only (admin scope) + ledger/ACL-write gated.
    assert "set_engine" in _UNIVERSE_ADMIN_ACTIONS
    assert "set_engine" in WRITE_ACTIONS


def test_supported_services_cover_both_cli_routes():
    services = supported_llm_api_key_services()
    assert {"anthropic", "openai"} <= services
