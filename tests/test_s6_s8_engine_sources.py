"""S6/S7/S8 — engine-source onboard choices + the market-offer supply side.

set_engine carries engine_source (byo_api_key | self_hosted_endpoint |
host_daemon), all persisted to config.yaml.
"""
from __future__ import annotations

import json

from tinyassets.config import load_universe_config


def _setup(tmp_path, monkeypatch):
    from tinyassets.api import universe as uni

    udir = tmp_path / "u-eng"
    udir.mkdir()
    monkeypatch.setattr(uni, "_request_universe", lambda universe_id="": "u-eng")
    monkeypatch.setattr(uni, "_universe_dir", lambda uid: udir)
    return uni, udir


def test_byo_api_key_still_works_and_records_source(tmp_path, monkeypatch):
    uni, udir = _setup(tmp_path, monkeypatch)
    out = json.loads(uni._action_set_engine(
        inputs_json=json.dumps({"service": "anthropic", "api_key": "sk-x"})))
    assert out["engine_source"] == "byo_api_key"  # default source
    assert load_universe_config(udir).engine_source == "byo_api_key"


def test_byo_api_key_preserves_existing_github_credential(tmp_path, monkeypatch):
    from tinyassets.credential_vault import (
        load_credential_vault,
        write_credential_vault,
    )

    uni, udir = _setup(tmp_path, monkeypatch)
    write_credential_vault(udir, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "write",
        "token": "ghs-existing",
    }])

    out = json.loads(uni._action_set_engine(
        inputs_json=json.dumps({"service": "anthropic", "api_key": "sk-x"})))

    assert out["status"] == "engine_set"
    (kept,) = [r for r in load_credential_vault(udir)
               if r.get("credential_type") == "vcs"]
    assert kept["destination"] == "TinyAssets/TinyAssets"
    assert kept["token"] == "ghs-existing"


def test_self_hosted_endpoint_persists(tmp_path, monkeypatch):
    uni, udir = _setup(tmp_path, monkeypatch)
    out = json.loads(uni._action_set_engine(inputs_json=json.dumps({
        "engine_source": "self_hosted_endpoint",
        "endpoint": "http://localhost:11434",
        "preferred_writer": "ollama-local"})))
    assert out["engine_source"] == "self_hosted_endpoint"
    cfg = load_universe_config(udir)
    assert cfg.engine_source == "self_hosted_endpoint"
    assert cfg.engine_endpoint == "http://localhost:11434"


def test_host_daemon_persists_and_points_at_summon(tmp_path, monkeypatch):
    uni, udir = _setup(tmp_path, monkeypatch)
    out = json.loads(uni._action_set_engine(inputs_json=json.dumps({
        "engine_source": "host_daemon", "provider": "codex"})))
    assert out["engine_source"] == "host_daemon"
    # PR-178 collapsed the surface; runtime-daemon binding is deliberately NOT
    # an advertised handle, so the guidance routes to the operator surface
    # instead of naming the retired `daemon_summon` verb. Pin the absence.
    assert "daemon_summon" not in out["next_step"], out["next_step"]
    assert "operator surface" in out["next_step"], out["next_step"]
    assert load_universe_config(udir).engine_source == "host_daemon"


def test_unknown_engine_source_errors(tmp_path, monkeypatch):
    uni, _udir = _setup(tmp_path, monkeypatch)
    out = json.loads(uni._action_set_engine(
        inputs_json=json.dumps({"engine_source": "telepathy"})))
    assert "error" in out


def test_engine_actions_are_founder_admin_scoped():
    from tinyassets.api.universe import UNIVERSE_ACTIONS, WRITE_ACTIONS
    from tinyassets.auth.provider import _UNIVERSE_ADMIN_ACTIONS

    for action in ("set_engine",):
        assert action in _UNIVERSE_ADMIN_ACTIONS
        assert action in WRITE_ACTIONS
        assert action in UNIVERSE_ACTIONS
