"""Deposits retain owner/conflict checks across broker prepare and commit."""

# ruff: noqa: F811 -- imported fixtures
import socket
import sys

import pytest

from tests.support.admission import drive, projection
from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_disconnect import removal  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.admission import local_mutate

pytestmark = [
    pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials"),
    pytest.mark.verifies('owner-daemon-in-box:T6'),
]

FIRST = {"host": "models.example.com", "path_template": "/catalogue", "methods": ["GET"]}
EXTRA = {"host": "models.example.com", "path_template": "/extra", "methods": ["GET"]}


def test_delayed_commit_after_erasure_writes_no_rows(removal, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase
    from tinyassets.broker.http_connect import local_operation

    ledger = removal.ledger
    document = {"action": "prepare", "destination": "fresh", "expected": "", "secret": "",
                "policy": {"auth_scheme": "none", "scopes": ["GET"], "endpoints": [FIRST],
                           "access_mode": "exact", "git_host": ""}}

    def call(doc):
        return local_operation(ledger, principal="alice", command_center="cc-alice",
                               document=doc, committed_via="door")

    revision = call(document)["revision"]
    entered, resume = threading.Event(), threading.Event()
    original = ledger._connect
    main_thread = threading.get_ident()

    def delayed():
        if threading.get_ident() != main_thread:
            entered.set()
            assert resume.wait(10)
        return original()

    monkeypatch.setattr(ledger, "_connect", delayed)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(call, {**document, "action": "commit", "expected": revision})
        try:
            assert entered.wait(5)
            drive(local_mutate(removal.root / ".broker/state/owner-identities.db", "alice", "erase",
                routing=projection(removal.root / ".broker/state/owner-identities.db")))
            local_erase(ledger, principal="alice", command_center=ACCOUNT_SCOPE,
                        erase_external_keys=False)
        finally:
            resume.set()
        with pytest.raises(PermissionError):
            pending.result(5)
    with original() as db:
        for table in ("outbound_connections", "outbound_connection_grants"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_http_connect_registers_before_worker_and_close_all_cancels(broker, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace

    from tinyassets.broker.server import OWNER, _Connection

    async def exercise():
        entered, resume = threading.Event(), threading.Event()
        aborted = []
        channel = _Connection(broker.server, SimpleNamespace(
            transport=SimpleNamespace(abort=lambda: aborted.append(True))), OWNER)

        def worker(doc):
            entered.set()
            assert resume.wait(5)
            assert channel._closed.is_set()
            return {"op": "HTTP_CONNECT_REFUSED"}

        monkeypatch.setattr(channel, "_http_connect", worker)
        task = asyncio.create_task(channel._connection_op("HTTP_CONNECT", {
            "op": "HTTP_CONNECT", "principal": "alice", "command_center": "cc-alice"}))
        channel._handler = task
        broker.server._connections.add(channel)
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert channel._principals == {"alice"}
            broker.server.close_owner("alice")
            assert channel._closed.is_set() and aborted == [True]
        finally:
            resume.set()
            await asyncio.gather(task, return_exceptions=True)
            broker.server._connections.discard(channel)

    asyncio.run(exercise())


def connect(**changes):
    from tests.support.connection_door import connect_http
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        return connect_http(universe_id="cc-alice", payload=dict(
            destination="fresh", secret="synthetic-fixture", auth_scheme="bearer",
            allowed_endpoints=[FIRST]) | changes)


def test_fresh_repeat_additive_and_full_deposit(removal):
    from tinyassets.api.http_connection import _ids
    from tinyassets.credential_vault import custody_namespace, load_credential_vault
    for changes in ({}, {}, {"allowed_endpoints": [FIRST, EXTRA]}):
        result = connect(**changes)
        assert result.get("status") == "provisioned", result
        assert "synthetic-fixture" not in str(result)
    connection, grant = _ids(owner="alice", destination="fresh")
    resource = removal.ledger._get_connection_resource(connection)
    assert len(resource.allowed_endpoints) == 2 and resource.access_mode == "exact"
    assert removal.ledger.get_grant(grant).owner_user_id == "alice"
    for _ in range(2):
        result = connect(destination="fresh-full", access="full")
        assert result.get("status") == "provisioned", result
        assert removal.ledger.access_mode(result["connection_id"]) == "full"
    assert load_credential_vault(custody_namespace(removal.root, "alice"))
    assert not (removal.root / "outbound.db").exists()


@pytest.mark.parametrize("table", ["outbound_connections", "outbound_connection_grants"])
def test_foreign_conflict_precedes_vault_write(removal, monkeypatch, table):
    from tinyassets.broker import vault as credential_vault
    with removal.ledger._connect() as conn:
        conn.execute(f"UPDATE {table} SET owner_user_id='bob'")

    def forbidden(*args, **kwargs):
        raise AssertionError("conflict changed vault")
    monkeypatch.setattr(credential_vault, "write_credential_vault", forbidden)
    result = connect(destination="fixture")
    assert result["error"] == "connection_conflict", result


def test_failed_deposit_creates_no_connection_or_grant(removal, monkeypatch):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker import vault as credential_vault

    def fail(*args, **kwargs):
        raise OSError("fixture vault failure")
    monkeypatch.setattr(credential_vault, "write_credential_vault", fail)
    assert connect()["error"] == "deposit_failed"
    connection, grant = _ids(owner="alice", destination="fresh")
    assert removal.ledger._get_connection_resource(connection) is None
    assert removal.ledger.get_grant(grant) is None


@pytest.mark.parametrize("table,column,value", [
    ("outbound_connections", "incarnation", "replacement"),
    ("outbound_connections", "access_mode", "full"),
    ("outbound_connection_grants", "owner_user_id", "bob"),
])
def test_commit_rechecks_both_rows(removal, monkeypatch, table, column, value):
    from tinyassets.credential_vault import custody_namespace, load_credential_vault

    before = load_credential_vault(custody_namespace(removal.root, "alice"))
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
    assert load_credential_vault(custody_namespace(removal.root, "alice")) == before


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
    assert connect()["error"] == "model_connection_incomplete"
    assert calls == ["prepare", "commit"]
    connection, grant = _ids(owner="alice", destination="fresh")
    assert removal.ledger._get_connection_resource(connection) is not None
    assert removal.ledger.get_grant(grant) is not None
    monkeypatch.setattr(BrokerClient, "http_connect", original)
    assert connect()["status"] == "provisioned"


def test_outage_precedes_vault_write_without_local_ledger(removal, monkeypatch):
    from tinyassets.broker import supervisor
    from tinyassets.broker import vault as credential_vault
    def forbidden(*args, **kwargs):
        raise AssertionError("outage changed vault")
    monkeypatch.setattr(credential_vault, "write_credential_vault", forbidden)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    assert connect()["error"] == "model_connection_incomplete"
    assert not (removal.root / "outbound.db").exists()


def test_only_door_commit_can_complete_a_model_ask(removal, monkeypatch):
    from tinyassets.api import pending_requests
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.pending_requests import get_request

    uses = {"model": {"wire": "openai_chat", "billing": "free", "models": [
        {"id": "fixture-model", "tools": True, "context": 32768}]}}
    endpoint = {**FIRST, "methods": ["POST"]}
    applied = []
    monkeypatch.setattr(pending_requests, "_complete_connect",
                        lambda *args: applied.append(args) or {})
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        asked = pending_requests.request_from_user(universe_id="cc-alice", payload={
            "kind": "connection", "title": "Use models", "body": "Approve model access.",
            "fields": [], "action": {"type": "connect", "destination": "fresh",
                "auth_scheme": "bearer", "endpoints": [endpoint], "uses": uses}})
        assert "error" not in asked, asked
        ref = asked["request_id"]
        result = connect(request_ref=ref, allowed_endpoints=[endpoint])
        assert result["status"] == "provisioned"
        connection, _ = _ids(owner="alice", destination="fresh")
        # Defense in depth: even a forged/stale reference on a box grant cannot approve.
        with removal.ledger._connect() as db:
            assert db.execute("SELECT committed_via FROM outbound_connections "
                              "WHERE connection_id=?", (connection,)).fetchone()[0] == "door"
            db.execute("UPDATE outbound_connections SET committed_via='box' "
                       "WHERE connection_id=?", (connection,))
        home = removal.root / "cc-alice"
        pending_requests.reconcile_connections("cc-alice", home)
        assert get_request(home, ref)["status"] == "pending"
        assert applied == []
        assert connect(request_ref=ref, allowed_endpoints=[endpoint])["status"] == "provisioned"
        pending_requests.reconcile_connections("cc-alice", home)
        assert get_request(home, ref)["status"] == "answered"
        assert len(applied) == 1


def test_owner_connections_to_same_service_keep_distinct_custody(removal):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker.disconnect import disconnect
    from tinyassets.broker.ledger_queries import GRANTED_RESOURCE, query_ledger
    from tinyassets.credential_vault import custody_namespace, load_credential_vault
    from tinyassets.storage.outbound_connections import GrantResolutionError

    for handle in ("first-account", "second-account"):
        assert connect(destination=handle, secret="synthetic-" + handle)["status"] == "provisioned"
    first, first_grant = _ids(owner="alice", destination="first-account")
    second, second_grant = _ids(owner="alice", destination="second-account")
    assert first != second and first_grant != second_grant
    assert {r["destination"]: r["token"] for r in load_credential_vault(
        custody_namespace(removal.root, "alice"))} == {
            "first-account": "synthetic-first-account",
            "second-account": "synthetic-second-account"}
    with pytest.raises(GrantResolutionError):
        query_ledger(removal.root, query=GRANTED_RESOURCE, principal="bob", command_center="bob",
                     grant_id=first_grant, connection_id=first)
    args = dict(principal="alice", command_center="alice", destination="first-account")
    incarnation = removal.ledger.incarnation(first)
    disconnect(removal.root, **args, action="fence", incarnation=incarnation)
    disconnect(removal.root, **args, action="erase", incarnation=incarnation)
    assert removal.ledger.get_connection(first) is None
    assert removal.ledger.get_connection(second).connection_id == second
    assert [r["token"] for r in load_credential_vault(
        custody_namespace(removal.root, "alice"))] == [
        "synthetic-second-account"]
