"""HTTP removal retains owner/incarnation fencing across real broker IPC."""
# ruff: noqa: F811 -- imported pytest fixtures
import socket
import sys

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.disconnect import disconnect
from tinyassets.storage.outbound_connections import GrantResolutionError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def removal(discovery, monkeypatch):
    from tinyassets.api.http_connection import _ids, remove_http
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(discovery.root))
    (discovery.root / "cc-alice").mkdir()
    grant_universe_access(discovery.root, universe_id="cc-alice", actor_id="alice",
                          permission="admin")
    connection, grant = _ids(universe_id="cc-alice", destination="fixture")
    discovery.ledger.create_connection(
        connection_id=connection, owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET",), provider="http",
        destination="fixture", credential_ref="vault://http/fixture",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                            "methods": ["GET"]}])
    discovery.ledger.grant_connection(grant_id=grant, connection_id=connection,
                                     owner_user_id="alice", universe_id="cc-alice")
    discovery.connection = connection
    discovery.grant = grant
    discovery.incarnation = discovery.ledger.incarnation(connection)

    def run(owner="alice", **payload):
        with identity_context(Identity(user_id=owner, username=owner, capabilities=["write"])):
            return remove_http(universe_id="cc-alice",
                               payload={"destination": "fixture", **payload})

    discovery.remove = run
    return discovery


def operation(rig, **changes):
    return disconnect(rig.root, **(dict(principal="alice", command_center="cc-alice",
                                       destination="fixture") | changes))


def test_actual_remove_fences_then_erases_via_ipc(removal, monkeypatch):
    from tinyassets import credential_vault
    original = credential_vault.forget_credential

    def forget(*args, **kwargs):
        assert removal.ledger._get_connection_resource(removal.connection).revoked_at is not None
        return original(*args, **kwargs)

    monkeypatch.setattr(credential_vault, "forget_credential", forget)
    assert removal.remove()["connection_removed"] is True
    assert removal.ledger.get_connection(removal.connection) is None
    assert removal.ledger.get_grant(removal.grant) is None
    monkeypatch.setattr(credential_vault, "forget_credential", original)
    assert removal.remove()["connection_removed"] is False
    assert not (removal.root / "outbound.db").exists()


@pytest.mark.parametrize("action", ["inspect", "fence", "erase"])
def test_foreign_owner_cannot_inspect_or_mutate(removal, action):
    with pytest.raises(GrantResolutionError):
        operation(removal, principal="bob", action=action, incarnation=removal.incarnation)
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is None


def test_stale_incarnation_and_unfenced_erase_refuse(removal):
    for action, incarnation in (("fence", "old"), ("erase", "old"),
                                ("erase", removal.incarnation)):
        with pytest.raises(GrantResolutionError):
            operation(removal, action=action, incarnation=incarnation)
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is None
    assert removal.remove(incarnation="old")["error"] == "connection_changed"


def test_failed_vault_cleanup_remains_fenced_and_retryable(removal, monkeypatch):
    from tinyassets import credential_vault
    original = credential_vault.forget_credential

    def fail(*args, **kwargs):
        raise OSError("fixture custody failure")

    monkeypatch.setattr(credential_vault, "forget_credential", fail)
    with pytest.raises(OSError, match="custody failure"):
        removal.remove()
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is not None
    monkeypatch.setattr(credential_vault, "forget_credential", original)
    assert removal.remove()["connection_removed"] is True


def test_broker_outage_does_not_remove_custody_or_open_ledger(removal, monkeypatch):
    from tinyassets import credential_vault
    from tinyassets.broker import supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    def forbidden(*args, **kwargs):
        raise AssertionError("custody cleanup before broker fence")

    monkeypatch.setattr(credential_vault, "forget_credential", forbidden)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProxyRequestError):
        removal.remove()
    assert not (removal.root / "outbound.db").exists()


def test_fence_failure_rolls_back_daemon_disconnect_record(removal, monkeypatch):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.storage.outbound_connections import ProxyRequestError
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore
    original = BrokerClient.disconnect

    def fail(client, document):
        if document["action"] == "fence":
            raise ProxyRequestError("lost fence acknowledgement")
        return original(client, document)

    monkeypatch.setattr(BrokerClient, "disconnect", fail)
    with pytest.raises(ProxyRequestError):
        removal.remove()
    with SQLiteProviderWorkAuthorityStore(removal.root).connection() as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='connection_disconnections'").fetchone()
        assert not exists or not db.execute("SELECT 1 FROM connection_disconnections").fetchall()
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is None


def test_lost_fence_ack_keeps_egress_denied_and_retry_completes(removal, monkeypatch):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.storage.outbound_connections import ProxyRequestError
    original = BrokerClient.disconnect

    def lose_ack(client, document):
        result = original(client, document)
        if document["action"] == "fence":
            raise ProxyRequestError("lost committed fence acknowledgement")
        return result

    monkeypatch.setattr(BrokerClient, "disconnect", lose_ack)
    with pytest.raises(ProxyRequestError):
        removal.remove()
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is not None
    assert removal.ledger.get_connection("conn-a") is not None
    monkeypatch.setattr(BrokerClient, "disconnect", original)
    assert removal.remove()["connection_removed"] is True
    assert removal.ledger.get_connection("conn-a") is not None


def test_erase_rechecks_incarnation_after_fence(removal):
    operation(removal, action="fence", incarnation=removal.incarnation)
    with removal.ledger._connect() as db:
        db.execute("UPDATE outbound_connections SET incarnation='replacement', revoked_at=NULL "
                   "WHERE connection_id=?", (removal.connection,))
    with pytest.raises(GrantResolutionError):
        operation(removal, action="erase", incarnation=removal.incarnation)
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is None
    assert removal.ledger.get_grant(removal.grant) is not None


def test_wrong_fence_refuses_before_mutation(removal):
    from tinyassets.broker.client import BrokerRefused
    removal.broker.state["token"] = "wrong"
    with pytest.raises(BrokerRefused):
        operation(removal, action="fence", incarnation=removal.incarnation)
    assert removal.ledger._get_connection_resource(removal.connection).revoked_at is None
