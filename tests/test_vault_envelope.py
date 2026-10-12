"""Broker key service: envelope, migration, tamper, rotation, and cross-center copy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.broker.key_service import KeyService, account_id, broker_service, reset_for_tests
from tinyassets.broker.root_key import (
    KeyServiceUnavailable,
    MemoryRootKey,
    ReconnectRequired,
    RootKeyError,
)
from tinyassets.broker.vault_cutover import CutoverStopped, migrate_data_root
from tinyassets.vault_envelope import open_envelope


@pytest.fixture(autouse=True)
def _fresh_service():
    reset_for_tests()
    yield
    reset_for_tests()


def _service(tmp_path: Path) -> KeyService:
    root = MemoryRootKey()
    return KeyService(tmp_path, root)


def _record(token: str, destination: str = "fixture") -> dict:
    return {
        "credential_type": "http",
        "destination": destination,
        "service": destination,
        "token": token,
    }


def test_memory_provider_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("TINYASSETS_ENV", "production")
    with pytest.raises(RootKeyError, match="refused in production"):
        MemoryRootKey()


def test_missing_kms_configuration_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("TINYASSETS_VAULT_TEST_DOUBLE", raising=False)
    monkeypatch.setenv("TINYASSETS_VAULT_KMS_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("TINYASSETS_ENV", raising=False)
    with pytest.raises(KeyServiceUnavailable, match="not configured"):
        broker_service(tmp_path / "data")


def test_seal_open_and_a_second_load_does_not_rewrite(tmp_path):
    service = _service(tmp_path)
    service.replace_center(owner="alice", center="home", records=[_record("one")])
    first = service.load_center("home")
    again = service.load_center("home")
    assert first == again == [_record("one")]
    with service._connect() as conn:
        generations = [
            row["generation"] for row in conn.execute("SELECT generation FROM sealed_records")
        ]
    assert generations == [1]


def test_tamper_and_cross_center_copy_do_not_open(tmp_path):
    service = _service(tmp_path)
    service.replace_center(owner="alice", center="home", records=[_record("secret-value")])
    with service._connect() as conn:
        row = conn.execute("SELECT envelope, dek_id FROM sealed_records").fetchone()
        envelope = json.loads(row["envelope"])
        moved = dict(envelope, command_center="other")
        conn.execute(
            "UPDATE sealed_records SET command_center='other', envelope=? WHERE record_id=?",
            (json.dumps(moved), envelope["record_id"]),
        )
    assert service.load_center("other") == []
    assert service.pending_reconnects("other")
    dek = service._dek_by_id(account_id("alice"), "home", row["dek_id"])
    with pytest.raises(Exception):
        open_envelope(moved, key=dek)


def test_root_rotation_rewraps_keks_and_records_still_open(tmp_path):
    service = _service(tmp_path)
    service.replace_center(owner="alice", center="home", records=[_record("kept")])
    service.provider.rotate()
    assert service.rewrap_account_keks() == 1
    assert service.load_center("home") == [_record("kept")]
    assert service.provider._retired == []


def test_dek_rotation_reseals_and_retires_the_old_key(tmp_path):
    service = _service(tmp_path)
    service.replace_center(owner="alice", center="home", records=[_record("kept")])
    new_id = service.rotate_center_dek(owner="alice", center="home")
    assert service.load_center("home") == [_record("kept")]
    with service._connect() as conn:
        rows = list(conn.execute("SELECT dek_id, active FROM center_deks"))
    assert rows == [(new_id, 1)] or [tuple(row) for row in rows] == [(new_id, 1)]


def test_kms_outage_is_retryable_and_not_a_reconnect(tmp_path):
    service = _service(tmp_path)
    service.provider.fail = True
    with pytest.raises(KeyServiceUnavailable) as exc:
        service.replace_center(owner="alice", center="home", records=[_record("x")])
    assert exc.value.reason == "key_service_unavailable"
    assert service.pending_reconnects("home") == []
    service.provider.fail = False
    service.replace_center(owner="alice", center="home", records=[_record("x")])
    assert service.load_center("home") == [_record("x")]


def test_erasure_tombstone_refuses_reimport(tmp_path):
    service = _service(tmp_path)
    service.replace_center(owner="alice", center="home", records=[_record("gone")])
    with service._connect() as conn:
        wrapped = conn.execute(
            "SELECT wrapped, kek_id, provider_id, key_ref FROM account_keks"
        ).fetchone()
    service.erase_account("alice")
    with pytest.raises(ReconnectRequired, match="not re-imported"):
        service.import_account_kek(
            owner="alice",
            wrapped=wrapped["wrapped"],
            kek_id=wrapped["kek_id"],
            provider_id=wrapped["provider_id"],
            key_ref=wrapped["key_ref"],
        )


def test_legacy_cutover_compares_then_deletes(tmp_path):
    center = tmp_path / "home"
    center.mkdir()
    record = _record("legacy-token")
    (center / ".credential-vault.json").write_text(
        json.dumps({"credentials": [record]}),
        encoding="utf-8",
    )
    service = _service(tmp_path)
    result = migrate_data_root(tmp_path, owner_for_center=lambda _path: "alice", service=service)
    assert result["removed"] == 1
    assert not (center / ".credential-vault.json").exists()
    assert service.load_center("home") == [record]


def test_legacy_mismatch_removes_nothing(tmp_path, monkeypatch):
    center = tmp_path / "home"
    center.mkdir()
    (center / ".credential-vault.json").write_text(
        json.dumps({"credentials": [_record("legacy-token")]}),
        encoding="utf-8",
    )
    service = _service(tmp_path)

    def lie(center_name):
        return [_record("different")]

    monkeypatch.setattr(service, "load_center", lie)
    with pytest.raises(CutoverStopped, match="home"):
        migrate_data_root(tmp_path, owner_for_center=lambda _path: "alice", service=service)
    assert (center / ".credential-vault.json").is_file()
