"""Deposits retain owner/conflict checks across broker prepare and commit."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys

import pytest

from tests.test_broker_disconnect import removal  # noqa: F401
from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")

FIRST = {"host": "models.example.com", "path_template": "/catalogue", "methods": ["GET"]}
EXTRA = {"host": "models.example.com", "path_template": "/extra", "methods": ["GET"]}


def connect(**changes):
    from tinyassets.api.http_connection import connect_http
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        return connect_http(universe_id="cc-alice", payload=dict(
            destination="fresh", secret="synthetic-fixture", auth_scheme="bearer",
            allowed_endpoints=[FIRST]) | changes)


def test_fresh_repeat_additive_and_full_deposit(removal):
    from tinyassets.api.http_connection import _ids
    from tinyassets.credential_vault import load_credential_vault
    for changes in ({}, {}, {"allowed_endpoints": [FIRST, EXTRA]}):
        result = connect(**changes)
        assert result.get("status") == "provisioned", result
        assert "synthetic-fixture" not in str(result)
    connection, grant = _ids(universe_id="cc-alice", destination="fresh")
    resource = removal.ledger._get_connection_resource(connection)
    assert len(resource.allowed_endpoints) == 2 and resource.access_mode == "exact"
    assert removal.ledger.get_grant(grant).owner_user_id == "alice"
    assert removal.ledger.get_grant(grant).unprompted_action_cap is None
    for _ in range(2):
        result = connect(destination="fresh-full", access="full")
        assert result.get("status") == "provisioned", result
        assert removal.ledger.access_mode(result["connection_id"]) == "full"
    assert load_credential_vault(removal.root / "cc-alice")
    assert not (removal.root / "outbound.db").exists()


@pytest.mark.parametrize("table", ["outbound_connections", "outbound_connection_grants"])
def test_foreign_conflict_precedes_vault_write(removal, monkeypatch, table):
    from tinyassets import credential_vault
    with removal.ledger._connect() as conn:
        conn.execute(f"UPDATE {table} SET owner_user_id='bob'")

    def forbidden(*args, **kwargs):
        raise AssertionError("conflict changed vault")
    monkeypatch.setattr(credential_vault, "write_credential_vault", forbidden)
    result = connect(destination="fixture")
    assert result["error"] == "connection_conflict", result


def test_failed_deposit_creates_no_connection_or_grant(removal, monkeypatch):
    from tinyassets import credential_vault
    from tinyassets.api.http_connection import _ids

    def fail(*args, **kwargs):
        raise OSError("fixture vault failure")
    monkeypatch.setattr(credential_vault, "write_credential_vault", fail)
    assert connect()["error"] == "deposit_failed"
    connection, grant = _ids(universe_id="cc-alice", destination="fresh")
    assert removal.ledger._get_connection_resource(connection) is None
    assert removal.ledger.get_grant(grant) is None


@pytest.mark.parametrize("table,column,value", [
    ("outbound_connections", "incarnation", "replacement"),
    ("outbound_connections", "access_mode", "full"),
    ("outbound_connection_grants", "owner_user_id", "bob"),
])
def test_commit_rechecks_both_rows(removal, monkeypatch, table, column, value):
    from tinyassets.broker.client import BrokerClient
    original = BrokerClient.http_connect

    def changed(client, document):
        if document["action"] == "commit":
            with removal.ledger._connect() as conn:
                conn.execute(f"UPDATE {table} SET {column}=?", (value,))
        return original(client, document)
    monkeypatch.setattr(BrokerClient, "http_connect", changed)
    result = connect(destination="fixture", allowed_endpoints=[FIRST, EXTRA])
    assert result["error"] == "connection_conflict", result
    assert len(removal.ledger._get_connection_resource(removal.connection).allowed_endpoints) == 1


def test_legacy_upgrade_and_extension_share_transaction(removal):
    with removal.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET scopes_json='[\"http\"]'")
    result = connect(destination="fixture", allowed_endpoints=[FIRST, EXTRA])
    assert result["status"] == "provisioned", result
    resource = removal.ledger._get_connection_resource(removal.connection)
    assert resource.scopes == ("GET",) and len(resource.allowed_endpoints) == 2


def test_lost_commit_ack_is_not_replayed(removal, monkeypatch):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker.client import BrokerClient
    from tinyassets.storage.outbound_connections import ProxyRequestError
    original = BrokerClient.http_connect
    calls = []

    def lost(client, document):
        calls.append(document["action"])
        result = original(client, document)
        if document["action"] == "commit":
            raise ProxyRequestError("lost acknowledgement")
        return result
    monkeypatch.setattr(BrokerClient, "http_connect", lost)
    with pytest.raises(ProxyRequestError):
        connect()
    assert calls == ["prepare", "commit"]
    connection, grant = _ids(universe_id="cc-alice", destination="fresh")
    assert removal.ledger._get_connection_resource(connection) is not None
    assert removal.ledger.get_grant(grant) is not None
    monkeypatch.setattr(BrokerClient, "http_connect", original)
    assert connect()["status"] == "provisioned"


def test_outage_precedes_vault_write_without_local_ledger(removal, monkeypatch):
    from tinyassets import credential_vault
    from tinyassets.broker import supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    def forbidden(*args, **kwargs):
        raise AssertionError("outage changed vault")
    monkeypatch.setattr(credential_vault, "write_credential_vault", forbidden)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProxyRequestError):
        connect()
    assert not (removal.root / "outbound.db").exists()
