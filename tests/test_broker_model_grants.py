"""Model metering is durable broker grant state, never published box metadata."""
import pytest

from tinyassets.storage.agent_request_usage import InferenceUsageRequired
from tinyassets.storage.outbound_connections import ConnectionLedger, CredentialBlindBroker


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


def test_platform_registration_latches_before_file_write_and_never_unlatches(ordinary, monkeypatch):
    from tinyassets.broker.owner_sources import read
    from tinyassets.providers import definition as pd

    write = pd._write

    def checked_write(center, rows):
        assert ordinary.grant_requires_inference_usage("grant")
        assert read(ordinary, center)
        write(center, rows)

    monkeypatch.setattr(pd, "_write", checked_write)
    fields = dict(universe_id="center", owner_user_id="owner", access_method="api_key_http",
                  protocol="openai_chat", ref="grant", model="model")
    assert not ordinary.grant_requires_inference_usage("grant")
    first = pd.register_definition(**fields)
    assert pd.register_definition(**fields).id == first.id
    write("center", [])
    with ordinary._connect() as conn:
        conn.execute("DELETE FROM box_model_sources")
    assert ConnectionLedger(ordinary._db_path, data_root=ordinary._data_root
                            ).grant_requires_inference_usage("grant")


@pytest.mark.parametrize("bad", ["owner", "grant", "revoked"])
def test_source_publication_refuses_unowned_or_dead_grant_without_latching(ordinary, bad):
    from tinyassets.broker.client import BrokerRefused
    from tinyassets.broker.owner_sources import read
    from tinyassets.providers.definition import register_definition

    if bad == "revoked":
        ordinary.revoke_connection("connection")
    with pytest.raises(BrokerRefused):
        register_definition(
            universe_id="center",
            owner_user_id="foreign" if bad == "owner" else "owner", access_method="api_key_http",
            protocol="openai_chat", ref="absent" if bad == "grant" else "grant", model="model")
    assert not ordinary.grant_requires_inference_usage("grant")
    assert read(ordinary, "center") == []


def test_source_publication_rolls_back_descriptor_when_latch_fails(ordinary):
    from tinyassets.broker.client import BrokerRefused
    from tinyassets.broker.owner_sources import read
    from tinyassets.providers.definition import register_definition

    with ordinary._connect() as conn:
        conn.execute("CREATE TRIGGER refuse_latch BEFORE UPDATE OF inference_required "
                     "ON outbound_connection_grants BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(BrokerRefused):
        register_definition(universe_id="center", owner_user_id="owner",
                            access_method="api_key_http", protocol="openai_chat",
                            ref="grant", model="model")
    assert not ordinary.grant_requires_inference_usage("grant")
    assert read(ordinary, "center") == []


@pytest.mark.parametrize("source", ["compute", "model_use", "model_discovery", "ordinary"])
def test_model_classification_is_recorded_at_grant_and_survives_metadata_removal(tmp_path, source):
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "owner")
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
                            owner_user_id="owner")
    with ledger._connect() as conn:
        recorded = conn.execute(
            "SELECT inference_required FROM outbound_connection_grants").fetchone()[0]
        assert recorded == (source in {"model_use", "model_discovery"})
        conn.execute("DELETE FROM connection_capabilities")
        conn.execute("UPDATE outbound_connections SET destination='service'")
    # Reopening the ledger cannot downgrade the recorded requirement.
    reopened = ConnectionLedger(ledger._db_path, data_root=tmp_path,
                              verify_authenticated_principal=lambda: "owner")
    assert reopened.grant_requires_inference_usage("grant") == (
        source in {"model_use", "model_discovery"})
    events = []

    def credential(*_):
        events.append("credential")
        return "synthetic-secret"

    def network(**_):
        events.append("send")
        return {"status": 200, "body": "ok"}

    broker = CredentialBlindBroker(reopened, resolve_credential=credential, network_request=network)
    if source in {"ordinary", "compute"}:
        assert broker.dispatch("grant", "POST", {
            "url": "https://models.example.com/chat"})["status"] == 200
        assert events == ["credential", "send"]
    else:
        with pytest.raises(InferenceUsageRequired):
            broker.dispatch("grant", "POST", {"url": "https://models.example.com/chat"})
        assert events == []


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


def test_reopening_never_reads_old_host_center_definitions(ordinary, monkeypatch):
    from pathlib import Path

    original = Path.open

    def forbidden(path, *args, **kwargs):
        if path.name == "provider_definitions.json":
            raise AssertionError("broker backfill must not read a private host center")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", forbidden)
    with ordinary._connect() as conn:
        conn.execute("DELETE FROM broker_schema_migrations")
    reopened = ConnectionLedger(ordinary._db_path, data_root=ordinary._data_root)
    assert reopened.grant_requires_inference_usage("grant") is False
    assert not (ordinary._data_root / "center").exists()


def test_same_owner_source_is_visible_across_local_centers(ordinary):
    from tinyassets.broker.owner_sources import read
    from tinyassets.providers.definition import register_definition
    source = register_definition(universe_id="first", owner_user_id="owner",
        access_method="api_key_http", protocol="openai_chat", ref="grant", model="model")
    assert ordinary.grant_requires_inference_usage("grant")
    assert read(ordinary, "first") == read(ordinary, "second")
    assert read(ordinary, "second")[0]["id"] == source.id
