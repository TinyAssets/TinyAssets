"""Model metering is durable broker grant state, never published box metadata."""
import pytest

from tinyassets.storage.outbound_connections import ConnectionLedger


@pytest.fixture
def ordinary(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "owner")
    ledger.create_connection(
        connection_id="connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",),
        provider="http", destination="service", credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                            "methods": ["POST"]}])
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="owner")
    return ledger


@pytest.mark.verifies('owner-daemon-in-box:T2')
def test_model_capability_added_after_grant_latches_metering(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "owner")
    ledger.create_connection(
        connection_id="connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",),
        provider="http", destination="service", credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="owner")
    assert not ledger.grant_requires_inference_usage("grant")
    ledger.configure_capability(
        connection_id="connection", capability_kind="model_use", enabled=True,
        descriptor={"wire": "openai_chat", "models": [
            {"id": "model", "tools": True, "context": 4096}], "billing": "flat"})
    ledger.configure_capability(connection_id="connection", capability_kind="model_use",
                                enabled=False)
    assert ledger.grant_requires_inference_usage("grant")
