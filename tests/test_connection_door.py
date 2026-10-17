"""Connection inventory and disconnect at an empty router root, through real IPC."""
import asyncio
import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from tests.support.broker_ipc import broker, discovery  # noqa: F401
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.onboarding.connections import handle_connections
from tinyassets.owner_router import OwnerRouter


@pytest.fixture
def door(discovery, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets import onboarding

    root = tmp_path / "router"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    identities = OwnerIdentities(tmp_path / "admissions.db", initialize=True)
    for owner in ("alice", "bob"):
        identities.resolve(owner, allocate=True)
        identities.admission("admit", owner, "cc-" + owner)
    discovery.broker.server._owner_identities = identities

    def forbidden(*args, **kwargs):
        raise AssertionError("the connection door must not read a home or resolve a box")

    monkeypatch.setattr(onboarding, "_read_home", forbidden)
    app = OwnerRouter(Starlette(routes=[Route("/app/connections", handle_connections,
                                             methods=["GET", "POST"])]), resolver=forbidden)

    def call(owner="alice", center="", body=None, cookie=""):
        async def run():
            with identity_context(Identity(user_id=owner, username=owner, capabilities=["write"])):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="https://tinyassets.io",
                    cookies={"__Host-ta-owner": cookie} if cookie else {},
                ) as client:
                    path = "/app/connections" + ("?center=" + center if center else "")
                    if body is None:
                        return await client.get(path)
                    return await client.post(path, json=body,
                                             headers={"Origin": "https://tinyassets.io"})
        result = asyncio.run(run())
        assert {p.name for p in root.iterdir()} <= {".runtime"}, "router created owner state"
        return result

    return call, identities, discovery


def test_protected_cookie_and_bearer_must_name_the_same_owner(door):
    import time

    from tinyassets.onboarding.owner_sessions import hashed, store

    call, _, _ = door
    with store() as db:
        db.execute("INSERT INTO owner_sessions VALUES (?, ?, ?)",
                   (hashed("alice-cookie"), json.dumps({"user_id": "alice"}), time.time() + 600))
    assert call("alice", cookie="alice-cookie").status_code == 200
    response = call("bob", cookie="alice-cookie")
    assert response.status_code == 401 and response.json()["error"] == "identity_conflict"


def test_inventory_at_empty_router_root_uses_sole_admission(door):
    call, _, _ = door
    response = call()
    assert response.status_code == 200, response.text
    assert response.json()["universe_id"] == "cc-alice"
    assert response.json()["connections"][0]["connection_id"] == "conn-a"
    assert "vault://" not in response.text


@pytest.mark.parametrize("owner,center", [("alice", "cc-bob"), ("unknown", ""),
                                          ("alice", "missing"), ("alice", "../cc-bob")])
def test_foreign_or_unknown_center_is_not_found(door, owner, center):
    call, _, _ = door
    assert call(owner, center).status_code == 404


def test_ambiguous_and_retired_admissions_never_select_another_center(door):
    call, identities, _ = door
    identities.admission("admit", "alice", "second")
    assert call().status_code == 404
    assert call(center="second").status_code == 200
    identities.admission("retire", "alice", "cc-alice")
    assert call(center="cc-alice").status_code == 404
    assert call().json()["universe_id"] == "second"
    identities.admission("retire", "alice", "second")
    assert call().status_code == 404


def test_sole_center_query_is_fenced_and_has_no_caller_selected_result(door):
    from tinyassets.broker.owner_identities import _owner_request

    _, _, rig = door
    for change in ({"center": "cc-bob"}, {"principal": ""}, {"uid": 300001}):
        assert _owner_request(rig.root, {"op": "SOLE_CENTER", "principal": "alice", **change}) == {
            "op": "SOLE_CENTER_REFUSED"}
    rig.broker.state["token"] = "stale"
    assert _owner_request(rig.root, {"op": "SOLE_CENTER", "principal": "alice"}) == {
        "op": "SOLE_CENTER_REFUSED"}


def test_door_disconnect_needs_no_owner_tree_and_rejects_foreign_center(door):
    from tinyassets.broker.http_connect import connect_operation

    call, _, rig = door
    args = dict(principal="alice", command_center="cc-alice", destination="door",
                policy={"auth_scheme": "none", "scopes": ["GET"], "endpoints": [{
                    "host": "service.example.com", "path_template": "/", "methods": ["GET"]}],
                    "access_mode": "exact", "git_host": ""})
    prepared = connect_operation(rig.root, **args, action="prepare")
    connected = connect_operation(rig.root, **args, action="commit", expected=prepared["revision"])
    connection = connected["projection"]["connection_id"]
    body = {"universe_id": "cc-alice", "destination": "door",
            "incarnation": rig.ledger.incarnation(connection)}
    assert call("bob", body=body).status_code == 404
    assert call(center="cc-bob", body=body).status_code == 404
    assert call(body=body | {"incarnation": "stale"}).status_code == 409
    response = call(body=body)
    assert response.status_code == 200, response.text
    assert response.json()["connection_removed"]
    assert rig.ledger.get_connection(connection) is None
    assert call(body=body).json()["connection_removed"] is False
    assert "secret" not in json.dumps(response.json()).replace("secrets_removed", "")


def test_door_keeps_failed_cleanup_reachable_without_owner_disconnection_store(door, monkeypatch):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker import vault

    call, _, rig = door
    connection, grant = _ids(universe_id="cc-alice", destination="retry")
    rig.ledger.create_connection(connection_id=connection, owner_user_id="alice",
        connection_class="http", connection_type="http", auth_scheme="none", scopes=["GET"],
        provider="http", destination="retry", credential_ref="vault://http/retry",
        allowed_endpoints=[{"host": "service.example.com", "path_template": "/",
                            "methods": ["GET"]}])
    rig.ledger.grant_connection(grant_id=grant, connection_id=connection,
                                owner_user_id="alice", universe_id="cc-alice")
    body = {"universe_id": "cc-alice", "destination": "retry",
            "incarnation": rig.ledger.incarnation(connection)}
    original = vault.forget_credential

    def fail(*args, **kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(vault, "forget_credential", fail)
    assert call(body=body).status_code == 503
    row = next(row for row in call().json()["connections"] if row["destination"] == "retry")
    assert row["cleanup_pending"]
    monkeypatch.setattr(vault, "forget_credential", original)
    assert call(body=body).json()["connection_removed"]
