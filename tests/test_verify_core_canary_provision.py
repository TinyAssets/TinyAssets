"""Missing canary config warns and records provisioned=0; a complete config records 1."""

from __future__ import annotations

import json

from scripts import verify_core_canary_provision as provision

_COMPLETE = {
    "email": "canary@example.invalid",
    "password": "secret-value",
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
    assert "secret-value" not in captured.err
    assert "::warning::" in captured.err


def test_derived_fields_are_rejected_without_printing_values(monkeypatch, capsys):
    config = dict(_COMPLETE)
    config["service_hmac_key"] = "hmac-secret"
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", json.dumps(config))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "core_canary_provisioned=0" in captured.out
    assert "unexpected: service_hmac_key" in captured.err
    assert "hmac-secret" not in captured.out
    assert "hmac-secret" not in captured.err
    assert "secret-value" not in captured.out
    assert "secret-value" not in captured.err


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
