"""Credential vault envelope: migration, tamper, missing key, rotation.

The autouse fixture in tests/conftest.py installs an explicit test master key.
These tests remove or replace it when the case is "no key" or "another key".
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tinyassets.credential_vault import (
    load_credential_vault,
    rewrap_credential_vault,
    write_credential_vault,
)
from tinyassets.platform_secrets import DAEMON_AUTH_ENV, child_env
from tinyassets.providers.base import subprocess_env_without_api_keys
from tinyassets.vault_envelope import (
    MASTER_KEY_ENV,
    MASTER_KEY_ID_ENV,
    PREVIOUS_KEYS_ENV,
    VAULT_KEY_ENV_NAMES,
    VaultKeyError,
    set_vault_key_provider,
)

SECRET = "vault-envelope-secret-value"
SECRET_B64 = base64.b64encode(SECRET.encode("utf-8")).decode("ascii")


def _legacy(token: str = SECRET) -> str:
    return json.dumps({
        "schema_version": 1,
        "credentials": [{
            "credential_type": "http",
            "service": "example",
            "destination": "example",
            "token": token,
        }],
    })


def _write_legacy(universe: Path, token: str = SECRET) -> Path:
    universe.mkdir(parents=True, exist_ok=True)
    path = universe / ".credential-vault.json"
    path.write_text(_legacy(token), encoding="utf-8")
    return path


def _b64(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode("ascii")


def _assert_secret_not_on_disk(path: Path) -> None:
    raw = path.read_bytes()
    assert SECRET.encode("utf-8") not in raw
    assert SECRET_B64.encode("ascii") not in raw


def test_write_seals_and_load_returns_the_secret(tmp_path):
    summary = write_credential_vault(tmp_path, [{
        "credential_type": "http",
        "service": "example",
        "destination": "example",
        "token": SECRET,
        "secret_b64": SECRET_B64,
    }])
    path = tmp_path / ".credential-vault.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == "tinyassets-credential-vault-envelope"
    assert document["version"] == 1
    assert document["key_id"] == "test-1"
    _assert_secret_not_on_disk(path)
    assert SECRET not in json.dumps(summary)
    records = load_credential_vault(tmp_path)
    assert records[0]["token"] == SECRET
    assert records[0]["secret_b64"] == SECRET_B64


def test_legacy_migration_is_idempotent(tmp_path):
    path = _write_legacy(tmp_path)
    first = load_credential_vault(tmp_path)
    assert first[0]["token"] == SECRET
    sealed = path.read_bytes()
    _assert_secret_not_on_disk(path)
    assert json.loads(sealed)["format"] == "tinyassets-credential-vault-envelope"
    second = load_credential_vault(tmp_path)
    assert second[0]["token"] == SECRET
    assert path.read_bytes() == sealed


def test_migration_verification_failure_leaves_the_legacy_file(monkeypatch, tmp_path):
    path = _write_legacy(tmp_path)
    original = path.read_bytes()
    from tinyassets import vault_envelope

    real = vault_envelope.decrypt_envelope

    def wrong(payload, *, universe_name):
        real(payload, universe_name=universe_name)
        return b"{}"

    monkeypatch.setattr(vault_envelope, "decrypt_envelope", wrong)
    with pytest.raises(VaultKeyError) as exc:
        load_credential_vault(tmp_path)
    assert SECRET not in str(exc.value)
    assert path.read_bytes() == original


def test_tamper_fails_closed_without_echoing_the_secret(tmp_path, caplog):
    _write_legacy(tmp_path)
    load_credential_vault(tmp_path)
    path = tmp_path / ".credential-vault.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    flipped = bytearray(base64.b64decode(document["ciphertext"]))
    flipped[0] ^= 0x01
    document["ciphertext"] = base64.b64encode(bytes(flipped)).decode("ascii")
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="authentication failed") as exc:
        load_credential_vault(tmp_path)
    assert SECRET not in str(exc.value)
    assert SECRET.encode("utf-8") not in before
    assert SECRET not in caplog.text
    assert path.read_bytes() == before


def test_missing_key_does_not_read_or_write_plaintext(monkeypatch, tmp_path):
    monkeypatch.delenv(MASTER_KEY_ENV)
    monkeypatch.delenv(MASTER_KEY_ID_ENV, raising=False)
    monkeypatch.delenv(PREVIOUS_KEYS_ENV, raising=False)
    with pytest.raises(VaultKeyError) as exc:
        write_credential_vault(tmp_path, [{
            "credential_type": "http",
            "service": "example",
            "destination": "example",
            "token": SECRET,
        }])
    assert SECRET not in str(exc.value)
    assert not (tmp_path / ".credential-vault.json").exists()

    path = _write_legacy(tmp_path)
    original = path.read_bytes()
    with pytest.raises(VaultKeyError):
        load_credential_vault(tmp_path)
    assert path.read_bytes() == original
    assert SECRET.encode("utf-8") in original  # the fixture file is still legacy


def test_invalid_key_material_is_refused_without_echo(monkeypatch, tmp_path):
    monkeypatch.setenv(MASTER_KEY_ENV, "not-a-real-key")
    with pytest.raises(VaultKeyError) as exc:
        write_credential_vault(tmp_path, [{
            "credential_type": "http",
            "service": "example",
            "destination": "example",
            "token": SECRET,
        }])
    assert "not-a-real-key" not in str(exc.value)
    assert not (tmp_path / ".credential-vault.json").exists()


def test_hex_master_key_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv(MASTER_KEY_ENV, "ab" * 32)
    monkeypatch.setenv(MASTER_KEY_ID_ENV, "hex-1")
    write_credential_vault(tmp_path, [{
        "credential_type": "http",
        "service": "example",
        "destination": "example",
        "token": SECRET,
    }])
    assert load_credential_vault(tmp_path)[0]["token"] == SECRET


def test_rotation_unwraps_the_previous_key_and_reseals(monkeypatch, tmp_path):
    first = b"\x21" * 32
    second = b"\x22" * 32
    monkeypatch.setenv(MASTER_KEY_ENV, _b64(first))
    monkeypatch.setenv(MASTER_KEY_ID_ENV, "1")
    write_credential_vault(tmp_path, [{
        "credential_type": "http",
        "service": "example",
        "destination": "example",
        "token": SECRET,
    }])
    monkeypatch.setenv(MASTER_KEY_ENV, _b64(second))
    monkeypatch.setenv(MASTER_KEY_ID_ENV, "2")
    monkeypatch.setenv(PREVIOUS_KEYS_ENV, f"1={_b64(first)}")
    assert load_credential_vault(tmp_path)[0]["token"] == SECRET
    rewrap_credential_vault(tmp_path)
    document = json.loads((tmp_path / ".credential-vault.json").read_text(encoding="utf-8"))
    assert document["key_id"] == "2"
    _assert_secret_not_on_disk(tmp_path / ".credential-vault.json")
    monkeypatch.delenv(PREVIOUS_KEYS_ENV)
    assert load_credential_vault(tmp_path)[0]["token"] == SECRET
    monkeypatch.setenv(MASTER_KEY_ENV, _b64(first))
    monkeypatch.setenv(MASTER_KEY_ID_ENV, "1")
    with pytest.raises(VaultKeyError, match="not available"):
        load_credential_vault(tmp_path)


def test_copy_into_another_command_center_does_not_open(tmp_path):
    source = tmp_path / "alpha"
    write_credential_vault(source, [{
        "credential_type": "http",
        "service": "example",
        "destination": "example",
        "token": SECRET,
    }])
    target = tmp_path / "beta"
    target.mkdir()
    target_vault = target / ".credential-vault.json"
    target_vault.write_bytes((source / ".credential-vault.json").read_bytes())
    with pytest.raises(ValueError, match="authentication failed"):
        load_credential_vault(target)
    assert load_credential_vault(source)[0]["token"] == SECRET


def test_pluggable_provider_seals_without_the_env_var(monkeypatch, tmp_path):
    material = b"\x33" * 32

    class Store:
        def active_key(self) -> tuple[str, bytes]:
            return "store-1", material

        def key_by_id(self, key_id: str) -> bytes:
            if key_id != "store-1":
                raise VaultKeyError("credential vault master key id is not available")
            return material

    monkeypatch.delenv(MASTER_KEY_ENV)
    set_vault_key_provider(Store())
    write_credential_vault(tmp_path, [{
        "credential_type": "http",
        "service": "example",
        "destination": "example",
        "token": SECRET,
    }])
    document = json.loads((tmp_path / ".credential-vault.json").read_text(encoding="utf-8"))
    assert document["key_id"] == "store-1"
    assert load_credential_vault(tmp_path)[0]["token"] == SECRET


def test_host_local_child_env_does_not_receive_the_master_key():
    env = subprocess_env_without_api_keys()
    assert env is not None
    assert VAULT_KEY_ENV_NAMES.isdisjoint(env)
    assert VAULT_KEY_ENV_NAMES <= DAEMON_AUTH_ENV
    assert VAULT_KEY_ENV_NAMES.isdisjoint(child_env({name: "x" for name in VAULT_KEY_ENV_NAMES}))


def test_unrecognized_json_is_not_rewritten(tmp_path):
    path = tmp_path / ".credential-vault.json"
    path.write_text('{"k": "leave-this-file"}', encoding="utf-8")
    assert load_credential_vault(tmp_path) == []
    assert path.read_text(encoding="utf-8") == '{"k": "leave-this-file"}'


def test_unsupported_envelope_version_fails_closed(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "http",
        "service": "example",
        "destination": "example",
        "token": SECRET,
    }])
    path = tmp_path / ".credential-vault.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["version"] = 99
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="version is not supported"):
        load_credential_vault(tmp_path)
    assert path.read_bytes() == before
