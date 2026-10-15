"""Missing canary config warns and records provisioned=0; a complete config records 1."""

from __future__ import annotations

import json

from scripts import verify_core_canary_provision as provision

_COMPLETE = {
    "email": "canary@example.invalid",
    "password": "secret-value",
    "universe_id": "u-1",
    "connection_id": "c-1",
    "service_path": "/hook",
    "service_hmac_key": "hmac-secret",
    "intake_receiver_id": "r-1",
    "branch_id": "b-1",
}


def test_empty_config_warns_and_records_unprovisioned(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("TINYASSETS_CORE_CANARY_CONFIG", raising=False)
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "core_canary_provisioned=0" in captured.out
    assert "::warning::core canary is not provisioned" in captured.err
    assert "secret-value" not in captured.err
    assert output.read_text(encoding="utf-8") == "provisioned=0\n"


def test_incomplete_config_names_the_field_not_the_secret(monkeypatch, capsys):
    config = dict(_COMPLETE)
    config["password"] = ""
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", json.dumps(config))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "core_canary_provisioned=0" in captured.out
    assert "password" in captured.err
    assert "hmac-secret" not in captured.err
    assert "::warning::" in captured.err


def test_complete_config_passes_without_printing_values(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", json.dumps(_COMPLETE))
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "core_canary_provisioned=1" in captured.out
    assert "secret-value" not in captured.out
    assert "hmac-secret" not in captured.out
    assert output.read_text(encoding="utf-8") == "provisioned=1\n"
