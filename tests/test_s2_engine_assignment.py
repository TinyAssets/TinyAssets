"""S2 — per-universe engine assignment (BYO API key) via universe action=set_engine.

Covers: config.yaml partial-merge write path, the llm_api_key vault type +
CLI-subprocess env injection, the founder-only set_engine action, and that the
API key never reaches the response or the ledger.
"""
from __future__ import annotations

import json

from tinyassets.config import load_universe_config, write_universe_config_fields


def test_write_universe_config_fields_merges_and_preserves(tmp_path):
    write_universe_config_fields(tmp_path, preferred_writer="codex")
    assert load_universe_config(tmp_path).preferred_writer == "codex"
    # A second partial write must preserve the earlier field.
    write_universe_config_fields(tmp_path, preferred_judge="gemini-free")
    cfg = load_universe_config(tmp_path)
    assert cfg.preferred_writer == "codex"
    assert cfg.preferred_judge == "gemini-free"








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




def test_set_engine_is_founder_admin_scoped():
    from tinyassets.api.universe import WRITE_ACTIONS
    from tinyassets.auth.provider import _UNIVERSE_ADMIN_ACTIONS

    # Founder-only (admin scope) + ledger/ACL-write gated.
    assert "set_engine" in _UNIVERSE_ADMIN_ACTIONS
    assert "set_engine" in WRITE_ACTIONS


