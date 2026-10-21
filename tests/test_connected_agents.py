"""Actual auth receipts, never manufactured agents or delivery readiness."""
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tests import test_rest_api_keys as api_tests
from tinyassets import connected_agents as connections
from tinyassets.auth.provider import Identity

stores, client, scope = api_tests.stores, api_tests.client, api_tests.scope

def roster(tmp_path, owner="alice", universe="home"):
    return connections.roster(tmp_path, owner=owner, universe_id=universe)


def test_unused_denied_and_revoked_keys_do_not_appear(stores, tmp_path, client):
    keys, authority = stores
    http, _ = client
    key = keys.mint("alice", name="Test assistant", scopes=[scope("read_graph")],
                    authority=authority)
    assert roster(tmp_path) == []
    authority.set_enabled(False)
    headers = {"authorization": "Bearer " + key["key"]}
    assert http.get("/api/command-centers/home/status", headers=headers).status_code == 401
    assert keys.list("alice", authority=authority)["keys"][0]["last_used_at"] is None
    assert roster(tmp_path) == []
    authority.set_enabled(True)
    assert http.get("/api/command-centers/home/status", headers=headers).status_code == 200
    found = roster(tmp_path)
    assert len(found) == 1 and found[0]["name"] == "Test assistant"
    assert found[0]["name_source"] == "owner_supplied"
    assert found[0]["state"] == "previously_authenticated"
    assert found[0]["messaging_ready"] is False
    assert key["key"] not in json.dumps(found)
    assert roster(tmp_path, owner="bob") == []
    assert roster(tmp_path, universe="foreign") == []
    keys.revoke("alice", key["key_id"], authority=authority)
    assert roster(tmp_path) == []
    assert http.get("/api/command-centers/home/status", headers=headers).status_code == 401


def _identity(**metadata):
    return Identity("alice", "alice", metadata={"auth_provider": "workos",
        "oauth_connection": {"id": "fixture-connection", "identity_source": "verified_session"},
        **metadata})


def _mcp(monkeypatch, identity, *, status=200):
    from tinyassets.auth import middleware

    class Provider:
        def resolve_token(self, token):
            return identity if token == "fixture-bearer" else None

    async def handler(request):
        # Consume the actual request stream, just as MCP does.
        await request.body()
        return JSONResponse({"jsonrpc": "2.0", "id": 1, "result": {}}, status_code=status)

    monkeypatch.setattr(middleware, "_get_provider", lambda: Provider())
    return TestClient(middleware.AuthContextMiddleware(Starlette(routes=[
        Route("/mcp", handler, methods=["POST"]), Route("/app/test", handler, methods=["POST"])])))


def _initialize(http, name="Fixture agent", *, path="/mcp", token="fixture-bearer"):
    return http.post(path, headers={"authorization": "Bearer " + token}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": name, "version": "test"}}})






@pytest.mark.parametrize("name", [None, {}, "", "Bearer fixture-secret",
                                  "ta_key_fake_secret", "x" * 81])
def test_unknown_names_are_honest_and_secret_shaped_names_not_retained(
        stores, tmp_path, monkeypatch, name):
    assert _initialize(_mcp(monkeypatch, _identity()), name).status_code == 200
    found = roster(tmp_path)
    assert found[0]["name"] == "Unknown MCP client"
    assert found[0]["name_source"] == "unknown"
    assert b"fixture-secret" not in stores[1].path.read_bytes()


def test_observed_outside_mcp_respects_current_grants_and_revoke(stores, tmp_path):
    _, authority = stores
    bound = {"client": "mcp-fixture", "family": "family", "authenticated_at": 1}
    authority.observe("alice", bound)
    authority.change("alice", bound["client"], family="family", expected_generation=0,
                     scopes=[scope("read_graph")])
    bound = authority.admit("alice", bound)
    connections.observe_mcp(_identity(outside_origin=bound), "Fixture agent")
    assert len(roster(tmp_path)) == 1
    assert roster(tmp_path, universe="foreign") == []
    authority.change("alice", bound["client"], family="family", expected_generation=1,
                     scopes=[], revoke=True)
    assert roster(tmp_path) == []




def test_deleted_owner_valid_mcp_token_cannot_recreate_receipt(stores, tmp_path, monkeypatch):
    from tinyassets.account_deletion import write_tombstone

    http = _mcp(monkeypatch, _identity())
    assert _initialize(http).status_code == 200
    write_tombstone(tmp_path, "alice")
    with stores[1].db() as conn:
        conn.execute("DELETE FROM authenticated_connections WHERE owner='alice'")
    # The issuer's token may remain valid until expiry; even a successful MCP
    # initialize must not recreate erased account metadata.
    assert _initialize(http).status_code == 200
    assert roster(tmp_path) == []


def test_classified_external_client_cannot_hide_with_browser_origin(stores, tmp_path, monkeypatch):
    _, authority = stores
    bound = {"client": "mcp-fixture", "family": "family", "authenticated_at": 1}
    authority.observe("alice", bound)
    authority.change("alice", bound["client"], family="family", expected_generation=0,
                     scopes=[scope("read_graph")])
    http = _mcp(monkeypatch, _identity(outside_origin=bound))
    response = http.post("/mcp", headers={"authorization": "Bearer fixture-bearer",
                                         "origin": "http://testserver"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"clientInfo": {"name": "Fixture external client"}}})
    assert response.status_code == 200
    assert len(roster(tmp_path)) == 1


def test_owner_agents_read_refuses_foreign_center_and_owner(stores, tmp_path, monkeypatch):
    from tinyassets.api.graph_reads import read_graph
    from tinyassets.auth.middleware import identity_context

    (tmp_path / "home").mkdir()
    (tmp_path / "foreign").mkdir()
    monkeypatch.setattr(
        "tinyassets.daemon_server.universe_access_permission",
        lambda base, universe_id, actor_id: "admin" if actor_id == "alice" else None)
    connections.observe_mcp(_identity(), "Fixture agent")
    with identity_context(_identity()):
        result = json.loads(read_graph(target="agents", graph_id="home", limit=100))
        assert result["agents"] == [{"agent_id": "main", "name": "Your agent"}]
        assert len(result["external_agents"]) == 1
        assert json.loads(read_graph(target="agents", graph_id="foreign", limit=100)) == {
            "error": "not_found"}
    with identity_context(Identity("bob", "bob")):
        assert json.loads(read_graph(target="agents", graph_id="home", limit=100)) == {
            "error": "not_found"}


def test_verified_session_and_unknown_credential_refs_are_opaque():
    a = connections.workos_connection({"iss": "fixture", "sid": "session-a"}, "token-a", None)
    b = connections.workos_connection({"iss": "fixture", "sid": "session-a"}, "token-b", None)
    assert a == b and "session-a" not in json.dumps(a)
    unknown = connections.workos_connection({"iss": "fixture"}, "token-a", None)
    assert unknown["identity_source"] == "unknown_client"
    assert "token-a" not in json.dumps(unknown)
    assert unknown != connections.workos_connection({"iss": "fixture"}, "token-b", None)


def test_owner_erasure_inventory_includes_receipts():
    from tinyassets.account_deletion import OWNER_ONLY_TABLES

    assert OWNER_ONLY_TABLES["authenticated_connections"] == "owner"
