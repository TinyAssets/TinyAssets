"""User-visible disconnect fences model authority, not merely the stored secret."""

import json

import pytest

from tests.test_http_connection_provisioning import (  # noqa: F401
    _connect,
    _login,
    _make_universe,
    _reset_auth,
)
from tests.test_onboarding_openai_device import _drive_get, _home, _user
from tinyassets.api.http_connection import remove_http
from tinyassets.custom_agents import create_binding, get_binding, publish_definition
from tinyassets.provider_assignment import load_provider_assignment
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import bind_serving_provider
from tinyassets.providers.definition import register_definition
from tinyassets.storage.outbound_connections import ConnectionLedger


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets.daemon_server import set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    universe = _make_universe(tmp_path, "u-owner", admin="owner")
    set_founder_home(tmp_path, founder_sub="owner", universe_id="u-owner", platform_generated=True)
    _login("owner")
    definition = publish_definition(
        tmp_path,
        author_id="owner",
        payload={
            "schema_version": 1,
            "name": "My custom agent",
            "description": "Keep this",
            "tags": [],
            "components": {"identity": {"kind": "soul", "config": {"note": "mine"}}},
        },
    )
    agent = create_binding(
        tmp_path,
        universe_id="u-owner",
        definition_id=definition["agent_definition_id"],
        created_by="owner",
        payload={"schema_version": 1, "name": "My custom agent", "role": "writer"},
    )
    return tmp_path, universe, agent


def source(rig, destination="model:mine"):
    provisioned = _connect("u-owner", destination=destination)
    assert provisioned.get("status") == "provisioned", provisioned
    definition = register_definition(
        universe_id="u-owner",
        owner_user_id="owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="test-model",
        ref=provisioned["grant_id"],
    )
    return provisioned, definition


def bind(rig, definitions):
    result = bind_serving_provider(
        base_path=rig[0],
        universe_dir=rig[1],
        owner_user_id="owner",
        universe_id="u-owner",
        agent_binding_id=rig[2]["agent_binding_id"],
        expected_revision=rig[2]["revision"],
        provider=definitions[0].id,
        model_access={d.id: ModelAccess() for d in definitions},
    )
    agent = result["agent_binding"]
    # Seed serving intent, not network model discovery: this cohort tests the
    # real assignment/custody withdrawal boundary independently of a remote API.
    from tinyassets.custom_agents import set_binding_serving_in_transaction
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(rig[0]).connection() as db:
        db.execute("BEGIN IMMEDIATE")
        serving = set_binding_serving_in_transaction(
            db,
            universe_id="u-owner",
            binding_id=agent["agent_binding_id"],
            expected_revision=agent["revision"],
            owner_user_id="owner",
            enabled=True,
        )
        db.commit()
        return serving


def remove(destination="model:mine", **extra):
    return remove_http(universe_id="u-owner", payload={"destination": destination, **extra})


def test_disconnect_fences_custody_and_old_binding_without_erasing_agent(rig):
    from tinyassets.credential_vault import current_connection_grant_custody
    from tinyassets.onboarding.model_setup import model_setup_state
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    conn, definition = source(rig)
    before_agent = bind(rig, [definition])
    before = load_provider_assignment(rig[0], universe_id="u-owner")
    assert remove()["status"] == "removed"
    after = load_provider_assignment(rig[0], universe_id="u-owner")
    assert after.state == "unassigned" and after.generation > before.generation
    assert after.assignment_digest != before.assignment_digest
    after_agent = get_binding(
        rig[0], universe_id="u-owner", binding_id=before_agent["agent_binding_id"]
    )
    assert after_agent["configuration"] == before_agent["configuration"]
    assert after_agent["revision"] == before_agent["revision"]
    assert after_agent["status"] == "configured"
    store = SQLiteProviderWorkAuthorityStore(rig[0])
    assert store.get(before.binding_id).state.value == "revoked"
    with store.connection() as db:
        assert (
            current_connection_grant_custody(
                db,
                owner_user_id="owner",
                universe_id="u-owner",
                connection_id=conn["connection_id"],
            )
            is None
        )
    assert (
        model_setup_state(rig[0], universe=rig[1], uid="u-owner", owner="owner") == "disconnected"
    )


def test_redeposit_does_not_resurrect_serving(rig):
    from tinyassets.provider_serving_binding import serving_connection_is_current

    _, definition = source(rig)
    bind(rig, [definition])
    remove()
    source(rig)
    assert not serving_connection_is_current(
        rig[0], universe_dir=rig[1], universe_id="u-owner", owner_user_id="owner"
    )


@pytest.mark.parametrize("removed_index", [0, 1])
def test_disconnect_preserves_other_accepted_source(rig, removed_index):
    first, a = source(rig)
    second, b = source(rig, "model:other")
    bind(rig, [a, b])
    before = load_provider_assignment(rig[0], universe_id="u-owner")
    assert remove(["model:mine", "model:other"][removed_index])["status"] == "removed"
    after = load_provider_assignment(rig[0], universe_id="u-owner")
    expected = [b, a][removed_index]
    assert after.state == "ready"
    assert [m.provider for m in after.candidates] == ["api_key_http:" + expected.id]
    assert after.generation == before.generation + 1
    ledger = ConnectionLedger(rig[0] / ".broker" / "outbound.db", data_root=rig[0])
    assert ledger.get_connection([second, first][removed_index]["connection_id"]) is not None
    from tinyassets.provider_serving_binding import serving_connection_is_current

    assert serving_connection_is_current(
        rig[0], universe_dir=rig[1], universe_id="u-owner", owner_user_id="owner"
    )


def test_stale_incarnation_cannot_remove_reconnected_source(rig):
    conn, _ = source(rig)
    ledger = ConnectionLedger(rig[0] / ".broker" / "outbound.db", data_root=rig[0])
    old = ledger.incarnation(conn["connection_id"])
    remove()
    source(rig)
    assert remove(incarnation=old)["error"] == "connection_changed"
    assert ledger.get_connection(conn["connection_id"]) is not None


def test_disconnect_does_not_revive_an_already_revoked_other_source(rig):
    from tinyassets.provider_work_authority import (
        ProviderWorkBindingFence,
        ProviderWorkBindingService,
    )
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    _, first = source(rig)
    _, other = source(rig, "model:other")
    bind(rig, [first, other])
    store = SQLiteProviderWorkAuthorityStore(rig[0])
    assignment = load_provider_assignment(rig[0], universe_id="u-owner")
    member = next(m for m in assignment.candidates if m.provider.endswith(other.id))
    ProviderWorkBindingService(store).revoke(ProviderWorkBindingFence(store.get(member.binding_id)))
    assert remove()["status"] == "removed"
    assert store.get(member.binding_id).state.value == "revoked"
    assert load_provider_assignment(rig[0], universe_id="u-owner").state == "unassigned"


def test_explicit_reconnect_reuses_custom_agent_unchanged(rig):
    from tinyassets.onboarding.model_bootstrap_binding import reconnect_owned_binding

    _, definition = source(rig)
    agent = bind(rig, [definition])
    remove()
    restored = reconnect_owned_binding(rig[0], uid="u-owner", owner="owner")
    assert restored["configuration"] == agent["configuration"]
    assert restored["agent_definition_id"] == agent["agent_definition_id"]
    assert restored["revision"] == agent["revision"]


def test_app_lists_and_removes_without_llm(rig, monkeypatch):
    conn, _ = source(rig)
    _home(monkeypatch, "u-owner")
    status, doc = _drive_get(
        "/app/connections", identity=_user("owner"), monkeypatch=monkeypatch
    )
    assert status == 200, doc
    assert len(doc["connections"]) == 1
    assert "sk-SECRET" not in json.dumps(doc) and "vault://" not in json.dumps(doc)
    row = doc["connections"][0]
    status, result = post_connections(
        {
            "universe_id": "u-owner",
            "destination": row["destination"],
            "incarnation": row["incarnation"],
        },
        identity=_user("owner"),
        monkeypatch=monkeypatch,
    )
    assert status == 200, result
    assert result["status"] == "removed"
    ledger = ConnectionLedger(rig[0] / ".broker" / "outbound.db", data_root=rig[0])
    assert ledger.get_connection(conn["connection_id"]) is None


def test_app_foreign_owner_cannot_inspect_or_remove(rig, monkeypatch):
    conn, _ = source(rig)
    _home(monkeypatch, "u-owner")
    status, doc = _drive_get(
        "/app/connections", identity=_user("foreign"), monkeypatch=monkeypatch
    )
    assert status == 403, doc
    ledger = ConnectionLedger(rig[0] / ".broker" / "outbound.db", data_root=rig[0])
    assert ledger.get_connection(conn["connection_id"]) is not None


def test_cross_origin_cannot_disconnect(rig, monkeypatch):
    conn, _ = source(rig)
    ledger = ConnectionLedger(rig[0] / ".broker" / "outbound.db", data_root=rig[0])
    _home(monkeypatch, "u-owner")
    status, _ = post_connections(
        {
            "universe_id": "u-owner",
            "destination": "model:mine",
            "incarnation": ledger.incarnation(conn["connection_id"]),
        },
        identity=_user("owner"),
        monkeypatch=monkeypatch,
        origin="https://attacker.example",
    )
    assert status == 403
    assert ledger.get_connection(conn["connection_id"]) is not None


def test_cleanup_failure_keeps_authority_fenced_and_retryable(rig, monkeypatch):
    import tinyassets.credential_vault as vault

    _, definition = source(rig)
    bind(rig, [definition])
    original = vault.forget_credential
    monkeypatch.setattr(
        vault, "forget_credential", lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError):
        remove()
    assert load_provider_assignment(rig[0], universe_id="u-owner").state == "unassigned"
    monkeypatch.setattr(vault, "forget_credential", original)
    assert remove()["status"] == "removed"


def test_old_remove_request_cannot_delete_replacement(rig):
    from tests.owner_answer import answer_request
    from tinyassets.api.pending_requests import request_from_user

    conn, _ = source(rig)
    asked = request_from_user(
        universe_id="u-owner",
        payload={
            "kind": "Connection",
            "title": "Disconnect",
            "body": "Remove access",
            "fields": [],
            "action": {"type": "remove_http", "destination": "model:mine"},
        },
    )
    assert asked.get("status") == "pending", asked
    remove()
    source(rig)
    answered = answer_request(
        universe_id="u-owner", payload={"request_id": asked["request_id"], "values": {}}
    )
    assert answered.get("error") == "connection_changed", answered
    ledger = ConnectionLedger(rig[0] / ".broker" / "outbound.db", data_root=rig[0])
    assert ledger.get_connection(conn["connection_id"]) is not None


def post_connections(body, *, identity, monkeypatch, origin="https://tinyassets.io"):
    import asyncio

    from starlette.requests import Request

    import tinyassets.onboarding as onboarding
    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding.connections import handle_connections

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def run():
        with identity_context(identity):
            return await handle_connections(
                Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": "/app/connections",
                        "headers": [
                            (b"origin", origin.encode()),
                            (b"content-type", b"application/json"),
                        ],
                        "query_string": b"",
                    },
                    receive,
                )
            )

    response = asyncio.run(run())
    return response.status_code, json.loads(response.body)
