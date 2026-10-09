"""Graph inventory uses scoped catalogs and fresh capability reads over real IPC."""
# ruff: noqa: F811 -- imported pytest fixtures
import json
import socket
import sys

import pytest

from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


@pytest.fixture
def graph(discovery, monkeypatch):
    from tinyassets.api import cloud_connections
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(discovery.root))
    (discovery.root / "cc-alice").mkdir()
    grant_universe_access(discovery.root, universe_id="cc-alice", actor_id="alice",
                          permission="admin")
    discovery.ledger.configure_capability(
        connection_id="conn-a", capability_kind="constant_headers", enabled=True,
        descriptor={"headers": {"X-Fixture": "scope"}})

    def forbidden(*args):
        raise AssertionError("graph consumer opened daemon ledger")

    monkeypatch.setattr(cloud_connections, "_ledger", forbidden)

    def read(owner="alice"):
        with identity_context(Identity(user_id=owner, username=owner, capabilities=["write"])):
            return cloud_connections.cloud_connections(action="list", universe_id="cc-alice")

    discovery.graph = read
    return discovery


def test_graph_inventory_includes_scoped_uses_without_credential_refs(graph):
    result = graph.graph()
    assert result["count"] == 1
    assert result["connections"][0]["constant_headers"] == {"X-Fixture": "scope"}
    assert result["connections"][0]["uses"] == {"call": {}}
    assert result["workspace_consents"] == []
    assert "credential_ref" not in json.dumps(result) and "vault://" not in json.dumps(result)
    assert not (graph.root / "outbound.db").exists()


def test_graph_inventory_foreign_actor_has_no_rows(graph):
    from tinyassets.daemon_server import grant_universe_access

    # Even an actor allowed to read the center cannot enumerate its owner's grants.
    grant_universe_access(graph.root, universe_id="cc-alice", actor_id="bob", permission="read")
    assert graph.graph("bob") == {"universe_id": "cc-alice", "connections": [],
                                  "count": 0, "workspace_consents": []}
    assert not (graph.root / "outbound.db").exists()


def test_graph_inventory_revocation_between_page_and_details_refuses(graph, monkeypatch):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.storage.outbound_connections import GrantResolutionError

    original = BrokerClient.connection_catalog

    def revoke(client, **kwargs):
        result = original(client, **kwargs)
        graph.ledger.revoke_grant("grant-a")
        return result

    monkeypatch.setattr(BrokerClient, "connection_catalog", revoke)
    with pytest.raises(GrantResolutionError):
        graph.graph()
    assert not (graph.root / "outbound.db").exists()


def test_graph_inventory_outage_never_falls_back(graph, monkeypatch):
    from tinyassets.broker import supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProxyRequestError):
        graph.graph()
    assert not (graph.root / "outbound.db").exists()
