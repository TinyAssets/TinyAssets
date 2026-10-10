"""Real broker catalog IPC and the daemon's capability catalog consumer."""
# ruff: noqa: F811 -- imported pytest fixtures
import asyncio
import json
import socket
import sys

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.catalog import connections
from tinyassets.broker.client import BrokerClient, BrokerRefused
from tinyassets.storage.outbound_connections import ProxyRequestError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


def test_catalog_real_ipc_pages_and_consumer_redact_authority(discovery):
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    for number in range(70):
        discovery.ledger.grant_connection(grant_id=f"page-{number:03}", connection_id="conn-a",
                                          owner_user_id="alice", universe_id="cc-alice")
    rows = list(connections(discovery.root, principal="alice", command_center="cc-alice"))
    assert len(rows) == 71
    assert len({grant.grant_id for grant, _, _ in rows}) == 71
    assert all(not hasattr(view, "credential_ref") for _, view, _ in rows)
    backend = Capabilities(discovery.root / "cc-alice", ExecutionContext(
        universe="cc-alice", owner="alice", initiating_agent="main"), [], None, lambda: None)
    result = asyncio.run(backend.dispatch({"op": "catalog"}))
    assert [row["name"] for row in result["capabilities"]] == ["browser", "connection:conn-a:GET"]
    assert "credential_ref" not in json.dumps(result)
    assert "vault://" not in json.dumps(result)
    assert not (discovery.root / "outbound.db").exists()


@pytest.mark.parametrize("scope", [
    {"principal": "bob", "command_center": "cc-alice"},
    {"principal": "alice", "command_center": "cc-bob"},
])
def test_catalog_foreign_scope_gets_no_rows(discovery, scope):
    assert list(connections(discovery.root, **scope)) == []
    assert not (discovery.root / "outbound.db").exists()


def test_catalog_rechecks_revocation_between_pages(discovery, monkeypatch):
    for number in range(70):
        discovery.ledger.grant_connection(grant_id=f"page-{number:03}", connection_id="conn-a",
                                          owner_user_id="alice", universe_id="cc-alice")
    original = BrokerClient.connection_catalog
    calls = []

    def revoke(client, **kwargs):
        answer = original(client, **kwargs)
        calls.append(kwargs)
        discovery.ledger.revoke_connection("conn-a")
        return answer

    monkeypatch.setattr(BrokerClient, "connection_catalog", revoke)
    rows = list(connections(discovery.root, principal="alice", command_center="cc-alice"))
    assert len(rows) == 64 and len(calls) == 2
    assert calls[1]["cursor"] == rows[-1][0].grant_id


def test_catalog_outage_and_stale_fence_do_not_open_local_ledger(discovery, monkeypatch):
    from tinyassets.broker import supervisor

    discovery.broker.state["token"] = "wrong"
    with pytest.raises(BrokerRefused):
        list(connections(discovery.root, principal="alice", command_center="cc-alice"))
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProxyRequestError):
        list(connections(discovery.root, principal="alice", command_center="cc-alice"))
    assert not (discovery.root / "outbound.db").exists()


@pytest.mark.parametrize("change", ["owner", "cursor", "credential"])
def test_catalog_malformed_projection_is_refused(discovery, monkeypatch, change):
    original = BrokerClient.connection_catalog

    def malformed(client, **kwargs):
        answer = original(client, **kwargs)
        if change == "owner":
            answer["items"][0]["connection"]["owner_user_id"] = "bob"
        elif change == "cursor":
            answer["next_cursor"] = "same"
        else:
            answer["items"][0]["connection"]["credential_ref"] = "vault://unexpected"
        return answer

    monkeypatch.setattr(BrokerClient, "connection_catalog", malformed)
    with pytest.raises(ProxyRequestError):
        list(connections(discovery.root, principal="alice", command_center="cc-alice"))


def test_catalog_refuses_sql_paths_and_unbounded_limits(discovery):
    from tinyassets import rpc_frames as rf

    for extra in ({"sql": "SELECT * FROM outbound_connections"}, {"path": "/data"},
                  {"limit": 1000}, {"cursor": "\0"}):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(discovery.broker.path))
            connection.sendall(rf.control(rf.CONNECTION, {
                "op": "CONNECTION_CATALOG", "principal": "alice", "command_center": "cc-alice",
                "generation": discovery.broker.state["generation"],
                "token": discovery.broker.state["token"], "cursor": "", "limit": 64, **extra}))
            assert rf.read_frame_blocking(connection).control() == {"op": "CATALOG_REFUSED"}


def test_actual_app_and_summary_catalogs_over_ipc(discovery, monkeypatch):
    from starlette.requests import Request

    from tests.test_onboarding_openai_device import _user
    from tinyassets import daemon_server, onboarding, shared_self, universe_tools
    from tinyassets.api import status
    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import serving
    from tinyassets.onboarding.connections import handle_connections
    from tinyassets.providers import connection_lifecycle

    root = discovery.root
    (root / "cc-alice").mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "_read_home", lambda *a, **k: "cc-alice")
    monkeypatch.setattr(shared_self, "require_founder_home", lambda *a: None)
    monkeypatch.setattr(serving, "_require_current_admin", lambda *a, **k: None)
    monkeypatch.setattr(connection_lifecycle, "unfinished_disconnections", lambda *a, **k: [])

    async def app():
        with identity_context(_user("alice")):
            return await handle_connections(Request({"type": "http", "method": "GET",
                                                       "path": "/app/connections", "headers": []}))

    response = asyncio.run(app())
    assert response.status_code == 200
    rows = json.loads(response.body)["connections"]
    assert len(rows) == 1 and rows[0]["connection_id"] == "conn-a"
    assert rows[0]["incarnation"] == discovery.ledger.incarnation("conn-a")
    assert "credential_ref" not in response.body.decode()
    assert "vault://" not in response.body.decode()
    monkeypatch.setattr(daemon_server, "get_founder_home", lambda *a: "cc-alice")
    monkeypatch.setattr(daemon_server, "list_branch_definitions", lambda *a, **k: [])
    monkeypatch.setattr(status, "_universe_active_turn", lambda *a: None)
    summary = universe_tools.command_center_summary(root / "cc-alice", "alice")
    assert "compute:discovery" in summary
    assert "vault://" not in summary
    assert not (root / "outbound.db").exists()
