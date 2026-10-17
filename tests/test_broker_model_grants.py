"""Model metering is durable broker grant state, never published box metadata."""
import pytest

from tinyassets.storage.agent_request_usage import InferenceUsageRequired
from tinyassets.storage.outbound_connections import ConnectionLedger, CredentialBlindBroker


@pytest.mark.parametrize("source", ["compute", "model_use", "model_discovery", "ordinary"])
def test_model_classification_is_recorded_at_grant_and_survives_metadata_removal(tmp_path, source):
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="compute:model" if source == "compute" else "service",
        credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                            "methods": ["GET", "POST"]}],
    )
    if source in {"model_use", "model_discovery"}:
        # The classifier needs only the broker's capability kind, not a box's
        # descriptor (or the capability's provider-specific execution details).
        with ledger._connect() as conn:
            conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, '{}', 1)",
                         ("connection", source))
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="owner", universe_id="center")
    with ledger._connect() as conn:
        recorded = conn.execute(
            "SELECT inference_required FROM outbound_connection_grants").fetchone()[0]
        assert recorded == (source != "ordinary")
        conn.execute("DELETE FROM connection_capabilities")
        conn.execute("UPDATE outbound_connections SET destination='service'")
    # Reopening the ledger cannot downgrade the recorded requirement.
    reopened = ConnectionLedger(ledger._db_path, data_root=tmp_path)
    assert reopened.grant_requires_inference_usage("grant") == (source != "ordinary")
    events = []

    def credential(*_):
        events.append("credential")
        return "synthetic-secret"

    def network(**_):
        events.append("send")
        return {"status": 200, "body": "ok"}

    broker = CredentialBlindBroker(reopened, resolve_credential=credential, network_request=network)
    if source == "ordinary":
        assert broker.dispatch("grant", "POST", {"path": "/chat"})["status"] == 200
        assert events == ["credential", "send"]
    else:
        with pytest.raises(InferenceUsageRequired):
            broker.dispatch("grant", "POST", {"path": "/chat"})
        assert events == []


def test_model_capability_added_after_grant_latches_metering(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",),
        provider="http", destination="service", credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="owner", universe_id="center")
    assert not ledger.grant_requires_inference_usage("grant")
    ledger.configure_capability(
        connection_id="connection", capability_kind="model_use", enabled=True,
        descriptor={"wire": "openai_chat", "models": [
            {"id": "model", "tools": True, "context": 4096}], "billing": "flat"})
    ledger.configure_capability(connection_id="connection", capability_kind="model_use",
                                enabled=False)
    assert ledger.grant_requires_inference_usage("grant")


@pytest.mark.parametrize("model", [False, True])
def test_existing_grants_backfill_from_broker_connection_only(tmp_path, model):
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="connection", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",),
        provider="http", destination="compute:model" if model else "service",
        credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="owner", universe_id="center")
    with ledger._connect() as conn:
        conn.execute("ALTER TABLE outbound_connection_grants DROP COLUMN inference_required")
    reopened = ConnectionLedger(ledger._db_path, data_root=tmp_path)
    assert reopened.grant_requires_inference_usage("grant") == model
