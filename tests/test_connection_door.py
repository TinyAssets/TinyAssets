"""Connection inventory and disconnect at an empty router root, through real IPC."""

import asyncio
import json
import os

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from tests.support.admission import drive, projection
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
    admission = root / ".ordinary-admission.json"
    admission.write_text('{"ordinary":true}\n')
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    identities = OwnerIdentities(tmp_path / "admissions.db", initialize=True)
    for owner in ("alice", "bob"):
        identities.resolve(owner, allocate=True)
        drive(identities.admit(owner, routing=projection(identities.path)))
    discovery.broker.server._owner_identities = identities
    discovery.broker.server._data_root = root
    from tinyassets import router_credentials
    from tinyassets.routing_snapshot import RoutingSnapshot, _snapshots

    # This venue has a separate shared router root. Deliver the broker's actual
    # projection over its credential socket before exercising the door.
    monkeypatch.setitem(_snapshots, root, RoutingSnapshot())
    monkeypatch.setattr(router_credentials, "BROKER_UID", os.getuid())
    monkeypatch.setattr(router_credentials, "DAEMON_UID", os.getuid())
    credentials = router_credentials.serving(root)
    asyncio.run_coroutine_threadsafe(
        credentials.__aenter__(), discovery.broker.loop).result(5)
    discovery.broker.server.routing.publish()

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
        assert {p.name for p in root.iterdir()} <= {
            ".runtime", ".ordinary-admission.json"}, "router created owner state"
        assert admission.read_text() == '{"ordinary":true}\n'
        return result

    try:
        yield call, identities, discovery
    finally:
        asyncio.run_coroutine_threadsafe(
            credentials.__aexit__(None, None, None), discovery.broker.loop).result(5)


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


def test_inventory_at_empty_router_root_uses_authenticated_owner(door):
    call, _, _ = door
    response = call()
    assert response.status_code == 200, response.text
    assert response.json()["owner"] == "alice"
    assert response.json()["connections"][0]["connection_id"] == "conn-a"
    assert "vault://" not in response.text


@pytest.mark.parametrize("center", ["cc-bob", "missing", "../cc-bob"])
def test_caller_center_cannot_change_owner_inventory(door, center):
    call, _, _ = door
    response = call("alice", center)
    assert response.status_code == 200
    assert response.json() == call("alice").json()
    assert call("bob", center).json()["connections"] == []
    assert call("unknown", center).status_code == 404


def test_owner_inventory_does_not_depend_on_local_center_membership(door, tmp_path):
    from tinyassets.owner_centers import Centers

    call, identities, _ = door
    before = call().json()
    local = tmp_path / "owner-volume"
    local.mkdir()
    centers = Centers(local)
    assert centers.active() == []
    centers.create("one")
    centers.create("two")
    assert call().json() == before
    centers.retire("one")
    centers.retire("two")
    assert call().json() == before
    assert centers.active() == []


def test_owner_admission_query_is_fenced_and_has_no_caller_selected_box(door):
    from tinyassets.broker.owner_identities import _owner_request

    _, _, rig = door
    for change in ({"center": "cc-bob"}, {"principal": ""}, {"uid": 300001}):
        assert _owner_request(rig.root, {"op": "OWNER_ADMISSION", "principal": "alice",
                                        **change}) == {"op": "OWNER_ADMISSION_REFUSED"}
    rig.broker.state["token"] = "stale"
    assert _owner_request(rig.root, {"op": "OWNER_ADMISSION", "principal": "alice"}) == {
        "op": "OWNER_ADMISSION_REFUSED"}


def test_door_disconnect_needs_no_owner_tree_and_cannot_remove_another_owner_handle(door):
    from tinyassets.broker.http_connect import connect_operation

    call, _, rig = door
    args = dict(principal="alice", command_center="cc-alice", destination="door",
                policy={"auth_scheme": "none", "scopes": ["GET"], "endpoints": [{
                    "host": "service.example.com", "path_template": "/", "methods": ["GET"]}],
                    "access_mode": "exact", "git_host": ""})
    prepared = connect_operation(rig.root, **args, action="prepare")
    connected = connect_operation(rig.root, **args, action="commit", expected=prepared["revision"])
    connection = connected["projection"]["connection_id"]
    body = {"destination": "door",
            "incarnation": rig.ledger.incarnation(connection)}
    assert call("bob", body=body).json()["connection_removed"] is False
    assert rig.ledger.get_connection(connection) is not None
    assert call(body=body | {"owner": "bob"}).status_code == 400
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
    connection, grant = _ids(owner="alice", destination="retry")
    rig.ledger.create_connection(connection_id=connection, owner_user_id="alice",
        connection_class="http", connection_type="http", auth_scheme="none", scopes=["GET"],
        provider="http", destination="retry", credential_ref="vault://http/retry",
        allowed_endpoints=[{"host": "service.example.com", "path_template": "/",
                            "methods": ["GET"]}])
    rig.ledger.grant_connection(grant_id=grant, connection_id=connection,
                                owner_user_id="alice")
    body = {"destination": "retry",
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
