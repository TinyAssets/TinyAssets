"""credential-custody: one broker key hierarchy, no cleartext vault writer."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (REPO / relative).read_text(encoding="utf-8")


def test_daemon_vault_writer_does_not_publish_cleartext():
    source = _source("tinyassets/credential_vault.py")
    assert "_persist_role_vault" not in source
    assert "VAULT_FILE_MODE" not in source
    assert "broker-sealed" in source


def test_browser_custody_has_no_raw_key_file():
    source = _source("tinyassets/broker/browser_vault.py")
    assert "SECRET_ROOT" not in source
    assert "generate_key" not in source
    assert ".key'" not in source and '.key"' not in source


def test_vault_kms_names_are_daemon_forbidden():
    from tinyassets.platform_secrets import DAEMON_FORBIDDEN_ENV

    assert {
        "TINYASSETS_VAULT_KMS_KEY_ARN",
        "TINYASSETS_VAULT_AWS_ACCESS_KEY_ID",
        "TINYASSETS_VAULT_AWS_SECRET_ACCESS_KEY",
    } <= DAEMON_FORBIDDEN_ENV
    compose = _source("deploy/compose.yml")
    assert "tinyassets-browser-keys" not in compose
    assert "/etc/tinyassets/vault-kms.env" in compose


def test_role_migration_does_not_grant_the_broker_the_legacy_vault():
    source = _source("deploy/role_migrate.py")
    assert '".credential-vault.json"' not in source.split("VAULT_NAMES", 1)[1].split(")", 1)[0]
    assert 'entry == ".credential-vault.json"' in source
