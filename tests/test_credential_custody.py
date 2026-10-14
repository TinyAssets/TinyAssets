"""credential-custody: one broker key hierarchy, no cleartext vault writer."""

from __future__ import annotations

import re
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


def test_broker_process_runs_as_uid_1002():
    process = _source("tinyassets/broker/process.py")
    assert "os.getresuid() != (1002, 1002, 1002)" in process
    launcher = _source("deploy/role_launcher.py")
    assert '"broker": (1002, (1102,))' in launcher
    compose = _source("deploy/compose.yml")
    assert 'user: "0:0"' in compose
    assert "getresuid() is (1002, 1002, 1002)" in compose


def test_an_unreadable_key_database_is_not_an_empty_vault(monkeypatch, tmp_path):
    import tinyassets.broker.credential_store as store

    def denied(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(store.os, "lstat", denied)
    seen: dict[str, str] = {}

    def rpc(universe, document):
        seen["action"] = document["action"]
        return {"records": [{"credential_type": "llm_subscription", "service": "claude"}]}

    monkeypatch.setattr(store, "_rpc", rpc)
    records = store.load_center(tmp_path / "u-alice")
    assert seen["action"] == "load"
    assert records[0]["service"] == "claude"


def test_core_capability_image_starts_from_an_empty_sealed_store():
    source = _source("scripts/core_capability_image.py")
    assert "p.unlink() for p in Path('/data').glob('*/.credential-vault.json')" in source
    assert 'TINYASSETS_VAULT_TEST_DOUBLE="1"' in source


def test_published_test_broker_is_refused_in_production(monkeypatch, tmp_path):
    import json

    from tinyassets.broker.credential_store import _published_test_broker

    monkeypatch.setenv("TINYASSETS_VAULT_TEST_DOUBLE", "1")
    monkeypatch.setenv(
        "TINYASSETS_VAULT_TEST_BROKER",
        json.dumps(
            {
                str(tmp_path.resolve()): {
                    "socket": "/tmp/vault-test.sock",
                    "generation": 1,
                    "token": "lease",
                }
            }
        ),
    )
    monkeypatch.setenv("TINYASSETS_ENV", "production")
    assert _published_test_broker(tmp_path) is None
    monkeypatch.delenv("TINYASSETS_ENV", raising=False)
    broker = _published_test_broker(tmp_path)
    assert broker is not None
    assert broker.fence() == (1, "lease")


def test_deploy_refuses_a_missing_vault_kms_file_before_compose_up():
    source = _source("deploy/deploy_fail_safe.sh")
    match = re.search(r"^restart_stack\(\) \{\n(.*?)^\}", source, re.S | re.M)
    assert match, "restart_stack is no longer a plain shell function"
    body = match.group(1)
    assert "/etc/tinyassets/vault-kms.env" in body
    assert "install-vault-kms" in body
    assert "running stack was not changed" in body
    assert '[ -L "$vault_kms" ]' in body
    assert body.index("remove_compose_temp_daemons") < body.index("vault-kms.env")
    assert body.index("vault-kms.env") < body.index("docker compose")
