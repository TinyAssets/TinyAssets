"""Real broker IPC for discovery; scripted upstream, no external traffic."""
import os
import socket
import sys
import threading
from types import SimpleNamespace

import pytest

from tests.test_broker_server import Script, broker  # noqa: F401
from tinyassets.broker import supervisor
from tinyassets.broker.ledger_queries import GRANTED_RESOURCE
from tinyassets.exceptions import ProviderUnavailableError
from tinyassets.providers.discovery_http import (
    ModelDiscoveryUnavailable,
    read_granted_discovery_document,
)
from tinyassets.storage.outbound_connections import ConnectionLedger

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="the broker authenticates Unix socket peers",
)

URL = "https://models.example.com/catalogue"


@pytest.fixture
def discovery(broker, tmp_path, monkeypatch):  # noqa: F811 - imported pytest fixture
    from tinyassets import role_modes

    # Same-uid protocol fixture; production-image probes use real gid 1102.
    monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())
    path = tmp_path / "private" / "outbound.db"
    ledger = ConnectionLedger(path, data_root=tmp_path)
    ledger.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET",),
        provider="http", destination="compute:discovery", credential_ref="vault://http/synthetic",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                            "methods": ["GET"]}],
    )
    ledger.grant_connection(grant_id="grant-a", connection_id="conn-a",
                            owner_user_id="alice", universe_id="cc-alice")
    daemon_thread = threading.get_ident()

    def ledger_for(principal):
        assert threading.get_ident() != daemon_thread
        return ConnectionLedger(path, data_root=tmp_path,
                                verify_authenticated_principal=lambda: principal)

    broker.server._ledger_for = ledger_for
    broker.upstreams["next"] = lambda: Script([b'{"data":', b'["alice"]}'])

    def verify_peer(sock):
        import struct

        peer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, _ = struct.unpack("3i", peer)
        assert (pid, uid) == (os.getpid(), os.getuid())

    live = SimpleNamespace(socket_path=broker.path,
                           fence=lambda: (broker.state["generation"], broker.state["token"]),
                           verify_broker=verify_peer)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: live)

    def read(**changes):
        args = dict(db_path=tmp_path / "outbound.db", grant_id="grant-a",
                    owner_user_id="alice", universe_id="cc-alice", url=URL)
        return read_granted_discovery_document(**(args | changes))

    return SimpleNamespace(read=read, ledger=ledger, broker=broker, root=tmp_path)


def test_discovery_queries_and_streams_without_a_daemon_ledger(discovery):
    assert discovery.read() == {"data": ["alice"]}
    assert discovery.broker.sent == [("alice", "grant-a", "GET", {"url": URL})]
    assert not (discovery.root / "outbound.db").exists()


@pytest.mark.parametrize("change", [
    {"owner_user_id": "bob"}, {"universe_id": "cc-bob"}, {"grant_id": "missing"},
])
def test_discovery_ipc_refuses_foreign_scope_before_stream(discovery, change):
    with pytest.raises(ModelDiscoveryUnavailable) as caught:
        discovery.read(**change)
    assert caught.value.reason == "source_revoked"
    assert not discovery.broker.sent


def test_discovery_ipc_refuses_unavailable_and_stale_fence_without_fallback(discovery, monkeypatch):
    discovery.broker.state["token"] = "stale"
    with pytest.raises(ModelDiscoveryUnavailable) as caught:
        discovery.read()
    assert caught.value.reason == "discovery_unavailable"
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ModelDiscoveryUnavailable) as caught:
        discovery.read()
    assert caught.value.reason == "discovery_unavailable"
    assert not (discovery.root / "outbound.db").exists()
    assert not discovery.broker.sent


def test_discovery_ipc_rechecks_authority_between_query_and_stream(discovery, monkeypatch):
    from tinyassets.broker.client import BrokerClient

    query = BrokerClient.ledger_query

    def revoke_after_query(client, **kwargs):
        answer = query(client, **kwargs)
        assert kwargs["query"] == GRANTED_RESOURCE
        discovery.ledger.revoke_grant("grant-a")
        return answer

    monkeypatch.setattr(BrokerClient, "ledger_query", revoke_after_query)
    with pytest.raises(ProviderUnavailableError, match="discovery transport failed"):
        discovery.read()
    assert not discovery.broker.sent


def test_discovery_ipc_endpoint_refusal_precedes_stream(discovery):
    with pytest.raises(ModelDiscoveryUnavailable) as caught:
        discovery.read(url="https://models.example.com/ungranted")
    assert caught.value.reason == "missing_discovery_scope"
    assert not discovery.broker.sent


def test_discovery_ipc_does_not_require_a_readable_model_profile(discovery):
    with discovery.ledger._connect() as conn:
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-a", "model_discovery", "malformed profile"))
    assert discovery.read() == {"data": ["alice"]}
