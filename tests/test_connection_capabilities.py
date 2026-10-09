from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from tinyassets.storage.outbound_connections import ConnectionLedger, SsrfValidationError


def _create_connection(ledger: ConnectionLedger, *, credential_ref: str = "vault://http/a"):
    return ledger.create_connection(
        connection_id="conn-voice",
        owner_user_id="founder-1",
        connection_class="outbound-http",
        scopes=("POST",),
        provider="http",
        destination="bridge.example",
        credential_ref=credential_ref,
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=[{
            "host": "bridge.example",
            "path_template": "/v1/session",
            "methods": ["POST"],
        }],
    )


def _descriptor(**changes):
    value = {
        "protocol": "tinyassets.voice.v1",
        "session_url": "https://bridge.example/v1/session",
        "service_name": "Example Voice",
        "privacy_url": "https://bridge.example/privacy",
    }
    value.update(changes)
    return value


def test_capability_round_trips_without_changing_public_connection_view(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    view = _create_connection(ledger)

    capability = ledger.configure_capability(
        connection_id=view.connection_id,
        capability_kind="realtime_voice",
        descriptor=_descriptor(),
        enabled=True,
    )

    assert capability is not None
    assert capability.descriptor() == _descriptor()
    assert ledger.get_connection_capability(
        view.connection_id, "realtime_voice"
    ) == capability
    assert "capabil" not in json.dumps(dataclasses.asdict(ledger.get_connection_view(
        view.connection_id
    )))
    assert not hasattr(capability, "credential_ref")


def test_capability_upsert_and_revoke_are_idempotent(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    view = _create_connection(ledger)

    first = ledger.configure_capability(
        connection_id=view.connection_id,
        capability_kind="realtime_voice",
        descriptor=_descriptor(service_name="First"),
        enabled=True,
    )
    second = ledger.configure_capability(
        connection_id=view.connection_id,
        capability_kind="realtime_voice",
        descriptor=_descriptor(service_name="Second"),
        enabled=True,
    )
    assert first is not None and second is not None
    assert ledger.get_connection_capability(
        view.connection_id, "realtime_voice"
    ).service_name == "Second"

    assert ledger.configure_capability(
        connection_id=view.connection_id,
        capability_kind="realtime_voice",
        enabled=False,
    ) is None
    assert ledger.configure_capability(
        connection_id=view.connection_id,
        capability_kind="realtime_voice",
        enabled=False,
    ) is None
    assert ledger.get_connection_capability(view.connection_id, "realtime_voice") is None


@pytest.mark.parametrize(
    "descriptor",
    [
        _descriptor(protocol="vendor.voice.v1"),
        _descriptor(session_url="http://bridge.example/v1/session"),
        _descriptor(session_url="https://user:pass@bridge.example/v1/session"),
        _descriptor(session_url="https://bridge.example/v1/session#fragment"),
        _descriptor(service_name="bad\x00label"),
        {**_descriptor(), "credential_ref": "vault://http/secret"},
    ],
)
def test_capability_rejects_malformed_or_secret_shaped_descriptors(tmp_path, descriptor):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    view = _create_connection(ledger)

    with pytest.raises(ValueError):
        ledger.configure_capability(
            connection_id=view.connection_id,
            capability_kind="realtime_voice",
            descriptor=descriptor,
            enabled=True,
        )
    assert ledger.get_connection_capability(view.connection_id, "realtime_voice") is None


def test_capability_cannot_widen_endpoint_or_method_authority(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    view = _create_connection(ledger)

    with pytest.raises(SsrfValidationError):
        ledger.configure_capability(
            connection_id=view.connection_id,
            capability_kind="realtime_voice",
            descriptor=_descriptor(session_url="https://other.example/v1/session"),
            enabled=True,
        )

    with sqlite3.connect(tmp_path / ".broker" / "outbound.db") as raw:
        raw.execute(
            "UPDATE outbound_connections SET scopes_json = ? WHERE connection_id = ?",
            (json.dumps(["GET"]), view.connection_id),
        )
    with pytest.raises(PermissionError):
        ledger.configure_capability(
            connection_id=view.connection_id,
            capability_kind="realtime_voice",
            descriptor=_descriptor(),
            enabled=True,
        )


def test_delete_then_reprovision_does_not_resurrect_capability(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    view = _create_connection(ledger)
    ledger.configure_capability(
        connection_id=view.connection_id,
        capability_kind="realtime_voice",
        descriptor=_descriptor(),
        enabled=True,
    )

    assert ledger.delete_connection(view.connection_id) is True
    replacement = _create_connection(ledger, credential_ref="vault://http/b")
    assert replacement.connection_id == view.connection_id
    assert ledger.get_connection_capability(
        replacement.connection_id, "realtime_voice"
    ) is None


def test_existing_database_gains_capability_table_without_widening_rows(tmp_path):
    db_path = tmp_path / "old.db"
    with sqlite3.connect(db_path) as raw:
        raw.execute(
            """
            CREATE TABLE outbound_connections (
                connection_id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                connection_class TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                provider TEXT NOT NULL,
                destination TEXT NOT NULL,
                credential_ref TEXT NOT NULL,
                revoked_at REAL
            )
            """
        )
        raw.execute(
            "INSERT INTO outbound_connections VALUES (?,?,?,?,?,?,?,NULL)",
            (
                "legacy",
                "founder-1",
                "legacy",
                "[]",
                "legacy",
                "legacy.example",
                "vault://legacy/ref",
            ),
        )

    ledger = ConnectionLedger(db_path)
    assert ledger.get_connection("legacy") is not None
    assert ledger.get_connection_capability("legacy", "realtime_voice") is None
    with sqlite3.connect(db_path) as raw:
        columns = raw.execute("PRAGMA table_info(connection_capabilities)").fetchall()
    assert [column[1] for column in columns] == [
        "connection_id",
        "capability_kind",
        "descriptor_json",
        "configured_at",
    ]
