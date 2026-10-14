"""The deploy refuses unless the dedicated canary owner config is complete."""

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


def test_empty_config_fails(monkeypatch, capsys):
    monkeypatch.delenv("TINYASSETS_CORE_CANARY_CONFIG", raising=False)
    assert provision.main() == 1
    assert "secret-value" not in capsys.readouterr().err


def test_incomplete_config_names_the_field_not_the_secret(monkeypatch, capsys):
    config = dict(_COMPLETE)
    config["password"] = ""
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", json.dumps(config))
    assert provision.main() == 1
    err = capsys.readouterr().err
    assert "password" in err
    assert "hmac-secret" not in err


def test_complete_config_passes_without_printing_values(monkeypatch, capsys):
    monkeypatch.setenv("TINYASSETS_CORE_CANARY_CONFIG", json.dumps(_COMPLETE))
    assert provision.main() == 0
    captured = capsys.readouterr()
    assert "secret-value" not in captured.out
    assert "hmac-secret" not in captured.out
