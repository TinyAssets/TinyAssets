"""N10 PR1: owner authority is broker-held, widened only by the router, gated at OPEN."""
# ruff: noqa: F811

from __future__ import annotations

import json
import socket
import sqlite3
import sys
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tests.test_broker_server import _connect, broker, new_op_id  # noqa: F401
from tests.test_owner_policy import _session, admissions
from tinyassets import rpc_frames as rf
from tinyassets.broker import authority_gate
from tinyassets.broker.policy import PolicyRefused, PolicyStore
from tinyassets.broker.server import request_digest
from tinyassets.storage.outbound_connections import ConnectionLedger

pytestmark = pytest.mark.verifies(
    "owner-daemon-in-box:Owner authority is held by the broker and gated at OPEN")
COOKIE = "interactive-cookie-" + "n" * 32
URL = "https://api.example.com/v1/items"


@pytest.fixture
def rig(tmp_path):
    identities = admissions(tmp_path)
    store = PolicyStore(tmp_path, identities)
    store.bootstrap()
    _session(tmp_path, "alice", COOKIE)
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "alice")
    ledger.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="items-api", credential_ref="vault://http/items",
        allowed_endpoints=[{"host": "api.example.com", "path_template": "/v1/items",
                            "methods": ["GET", "POST"]}])
    ledger.grant_connection(grant_id="grant-a", connection_id="conn-a", owner_user_id="alice")
    for name, mode in (("git-exact", "exact"), ("git-full", "full")):
        ledger.create_connection(
            connection_id=name, owner_user_id="alice", connection_class="http",
            scopes=("git_read:owner/repo", "git_write:owner/repo"), provider="http",
            destination=name, credential_ref="vault://http/" + name, connection_type="http",
            auth_scheme="bearer", git_host="git.test", access_mode=mode,
            allowed_endpoints=[{"host": "git.test", "path_template": "/api",
                                "methods": ["GET"]}])
        ledger.grant_connection(grant_id="grant-" + name, connection_id=name,
                                owner_user_id="alice")
    return SimpleNamespace(store=store, ledger=ledger, root=tmp_path)


def _rule(rig, action_class, behaviour, **extra):
    current = rig.store.read("alice")
    document = json.loads(json.dumps(current.document))
    document["entries"].append({"scope": "account", "table": "rules", "values": {
        "agent": "main", "action_class": action_class, "behaviour": behaviour}})
    document["entries"].extend(extra.get("entries", []))
    return rig.store.write("alice", COOKIE, current.generation, document)


def _check(rig, verb="POST", *, grant="grant-a", connection="conn-a", request=None,
           authority=None):
    request = request if request is not None else {"url": URL, "body": "{}"}
    return authority_gate.check(
        rig.store, rig.ledger, owner="alice", grant_id=grant, connection_id=connection,
        verb=verb, request=request, authority=authority or {"agent": "main"},
        request_digest=request_digest(grant_id=grant, connection_id=connection, verb=verb,
                                      request=request))


def _gate_receipts(rig):
    return [row for row in rig.store.receipts("alice") if row["kind"] == "gate"]


def test_an_ask_first_open_without_approval_is_refused_and_receipted(rig):
    rig.store.grant_consent("alice", COOKIE, "authenticated_external_call", "items-api")
    assert _check(rig) is None
    _rule(rig, "app.write", "ask_first")
    assert _check(rig) == "policy_ask"
    assert _gate_receipts(rig)[0]["decision"] == "deny"
    # Only the broker's ledger makes a connection a model source.
    with rig.ledger._connect() as db:
        db.execute("UPDATE outbound_connection_grants SET inference_required=1")
    assert _check(rig) is None


def test_hand_off_floor_ignores_the_box_agent_label(rig):
    entry = {"scope": "account", "table": "operation_kinds", "values": {
        "connection": "conn-a", "method": "POST", "kind": "security"}}
    current = rig.store.read("alice")
    document = json.loads(json.dumps(current.document))
    document["entries"] += [entry, {"scope": "account", "table": "rules", "values": {
        "agent": "worker", "action_class": "security.change", "behaviour": "do"}}]
    rig.store.write("alice", COOKIE, current.generation, document)
    assert _check(rig, authority={"agent": "worker"}) == "policy_hand_off"


def test_a_once_approval_is_spent_once_and_goes_stale_with_the_generation(rig):
    rig.store.grant_consent("alice", COOKIE, "authenticated_external_call", "items-api")
    written = _rule(rig, "app.write", "ask_first")
    request = {"url": URL, "body": "{}"}
    digest = request_digest(grant_id="grant-a", connection_id="conn-a", verb="POST",
                            request=request)
    token = rig.store.issue_once("alice", COOKIE, written.generation, digest)["token"]
    assert _check(rig, request={**request, "body": "{\"x\":1}"},
                  authority={"agent": "main", "approval": token}) == "policy_ask"
    assert _check(rig, authority={"agent": "main", "approval": token}) is None
    assert _check(rig, authority={"agent": "main", "approval": token}) == "policy_ask"
    token = rig.store.issue_once("alice", COOKIE, written.generation, digest)["token"]
    rig.store.write("alice", COOKIE, written.generation, written.document)
    assert _check(rig, authority={"agent": "main", "approval": token}) == "policy_ask"


def test_a_standing_grant_matches_on_broker_recomputed_revisions(rig):
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        authority_from_facts,
        local_query,
    )

    rig.store.grant_consent("alice", COOKIE, "authenticated_external_call", "items-api")
    grant, resource, incarnation = authority_from_facts(local_query(
        rig.ledger, query=AUTHORIZED_CONNECTION, principal="alice", command_center="alice",
        grant_id="grant-a", connection_id="conn-a"), principal="alice", grant_id="grant-a",
        connection_id="conn-a")
    view = resource.to_view()
    consent = [row for row in rig.store.consents("alice")
               if row["destination"] == view.destination]
    standing = {"owner": "alice", "agent": "main", "action_class": "app.write",
                "operation": "POST", "connection": "conn-a",
                "origin": "https://api.example.com",
                "connection_revision": authority_gate.digest([view.as_dict(), incarnation]),
                "consent_digest": authority_gate.digest([asdict(grant), consent]),
                "binding_revision": "binding-1", "scope": "always", "expires_at": None}
    entry = {"scope": "account", "table": "approval_grants", "values": {
        "agent": "main", "record_kind": "preapproval", "decision_id": "request-1:1",
        "grant_json": json.dumps(standing)}}
    _rule(rig, "app.write", "ask_first", entries=[entry])
    assert _check(rig, authority={"agent": "main", "binding_revision": "binding-1"}) is None
    assert _check(rig, authority={"agent": "main", "binding_revision": "other"}) == "policy_ask"
    rig.store.revoke_consent("alice", "authenticated_external_call", "items-api")
    assert _check(rig, authority={"agent": "main", "binding_revision": "binding-1"}) \
        == "policy_ask"


def test_consent_gates_non_reads_only(rig):
    assert _check(rig) == "consent_missing"
    assert _check(rig, "GET", request={"url": URL}) is None
    rig.store.grant_consent("alice", COOKIE, "authenticated_external_call", "items-api")
    assert _check(rig) is None


def test_a_workspace_push_needs_consent_unless_access_is_full(rig):
    def push(name):
        return _check(rig, "git_write:owner/repo", grant="grant-" + name, connection=name,
                      request={"host": "git.test", "method": "POST", "upload": True})

    assert push("git-exact") == "consent_missing"
    assert push("git-full") is None
    assert _check(rig, "git_read:owner/repo", grant="grant-git-exact",
                  connection="git-exact", request={"host": "git.test"}) is None
    with pytest.raises(PolicyRefused):
        rig.store.grant_consent("alice", "agent-token-" + "x" * 32, "workspace",
                                "push:git-exact:git.test/owner/repo")
    authority_gate.require_grantable(rig.ledger, "alice", "workspace",
                                     "push:git-exact:git.test/owner/repo")
    with pytest.raises(PermissionError):
        authority_gate.require_grantable(rig.ledger, "alice", "workspace",
                                         "push:git-exact:elsewhere.test/owner/repo")
    rig.store.grant_consent("alice", COOKIE, "workspace", "push:git-exact:git.test/owner/repo")
    assert push("git-exact") is None


def _legacy_v1(tmp_path, identities=None):
    """An owner admitted before N10: a provisioned user_version 1 policy."""
    store = PolicyStore(tmp_path, identities or admissions(tmp_path))
    store.bootstrap()
    with sqlite3.connect(store._path("alice")) as db:
        db.execute("DELETE FROM import_state")
        db.execute("PRAGMA user_version=1")
    restarted = PolicyStore(tmp_path, store._admissions)
    restarted.bootstrap()
    return restarted


def test_an_import_is_accepted_once_and_counts_invalid_entries(tmp_path):
    store = _legacy_v1(tmp_path)
    assert store.import_pending("alice") and not store.import_pending("bob")
    document = {"source_digest": "sha256:" + "0" * 64, "entries": [
        {"scope": "account", "table": "rules", "values": {
            "agent": "main", "action_class": "app.write", "behaviour": "ask_first"}},
        {"scope": "alpha", "table": "rules", "values": {}},
    ], "consents": [
        {"sink": "authenticated_external_call", "destination": "items-api",
         "granted_at": 5.0, "granted_by": "alice"},
        {"sink": "", "destination": "x", "granted_at": 1.0, "granted_by": "alice"},
    ]}
    result = store.import_authority("alice", document)
    assert (result["entries"], result["consents"], result["dropped"]) == (1, 1, 2)
    assert store.decide("alice", center="alice", action_class="app.write")["decision"] == "ask"
    assert store.consent_active("alice", "authenticated_external_call", "items-api")
    with pytest.raises(PolicyRefused, match="not pending"):
        store.import_authority("alice", document)
    receipt = next(row for row in store.receipts("alice") if row["outcome"] == "committed"
                   and row["kind"] == "import")
    assert json.loads(receipt["rule"]) == {"entries": 1, "consents": 1, "dropped": 2}


def test_another_box_operation_first_ends_the_import_loudly(tmp_path, caplog):
    store = _legacy_v1(tmp_path)
    store.abandon_import("alice")
    assert "ALARM: owner authority import abandoned for alice" in caplog.text
    with pytest.raises(PolicyRefused, match="not pending"):
        store.import_authority("alice", {"source_digest": "", "entries": [], "consents": []})
    assert store.read("alice").document["entries"] == []


def test_the_exporter_sends_box_files_once_and_removes_them(tmp_path, monkeypatch):
    from tinyassets import authority_export
    from tinyassets.broker import client

    root = tmp_path / "box"
    sidecar = root / ".universe-sidecars" / "center"
    sidecar.mkdir(parents=True)
    with sqlite3.connect(root / authority_export.POLICY_FILE) as db:
        db.execute("CREATE TABLE policy (singleton INTEGER, generation INTEGER, digest TEXT, "
                   "document TEXT)")
        db.execute("INSERT INTO policy VALUES (1,1,'',?)", (json.dumps({"entries": [
            {"scope": "account", "table": "rules", "values": {"action_class": "share",
                                                             "behaviour": "do"}}]}),))
    with sqlite3.connect(sidecar / authority_export.CONSENTS_FILE) as db:
        db.execute("CREATE TABLE effector_consents (sink TEXT, destination TEXT, "
                   "granted_at REAL, granted_by TEXT, revoked_at REAL)")
        db.execute("INSERT INTO effector_consents VALUES ('patch_intake','r-1',2,'alice',NULL)")
        db.execute("INSERT INTO effector_consents VALUES ('patch_intake','r-0',1,'alice',3)")
    sent = []
    monkeypatch.setattr(client, "owner_policy_operation",
                        lambda data_root, owner, action, **fields: sent.append(
                            (owner, action, fields["document"])) or {"generation": 2})
    assert authority_export.export(root, "alice") == {"generation": 2}
    (owner, action, document), = sent
    assert (owner, action) == ("alice", "import")
    assert document["entries"][0]["values"]["action_class"] == "share"
    assert [row["destination"] for row in document["consents"]] == ["r-1"]
    assert not (root / authority_export.POLICY_FILE).exists()
    assert not (sidecar / authority_export.CONSENTS_FILE).exists()
    assert authority_export.export(root, "alice") is None


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                    reason="the box channel uses Unix peer credentials")
@pytest.mark.parametrize("action", ["write", "issue_once", "consent_grant"])
def test_the_box_channel_refuses_every_widening_even_with_proof(
        broker, monkeypatch, tmp_path, action):
    from tinyassets.broker import box_admission
    from tinyassets.broker.server import BOX_HOST

    _session(tmp_path, "alice", COOKIE)
    store = PolicyStore(tmp_path, broker.server._owner_identities)
    store.bootstrap()
    broker.server._policy_store = store
    broker.server._roles = {__import__("os").getuid(): BOX_HOST}
    monkeypatch.setattr(box_admission, "resolve_owner", lambda identities, request, **_: {
        "op": "BOX_ADMITTED", "uid": 300001, "quota": 1024**3})
    before = store.read("alice")
    proof = {"session_hash": __import__("hashlib").sha256(COOKIE.encode()).hexdigest()}
    with _connect(broker) as sock:
        sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CHANNEL", "principal": "alice"}))
        assert rf.read_frame_blocking(sock).control()["op"] == "BOX_ADMITTED"
        sock.sendall(rf.control(rf.CONNECTION, {
            "op": "OWNER_POLICY", "action": action, "session_proof": proof,
            "expected_generation": before.generation, "policy": before.document,
            "sink": "authenticated_external_call", "destination": "items-api"}))
        assert rf.read_frame_blocking(sock) is None
    assert store.read("alice").generation == before.generation
    assert store.consents("alice") == []


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                    reason="the box channel uses Unix peer credentials")
def test_a_box_open_with_an_inference_flag_still_meets_the_rules(
        broker, monkeypatch, tmp_path):
    import os

    from tinyassets.broker import box_admission
    from tinyassets.broker.server import BOX_HOST

    _session(tmp_path, "alice", COOKIE)
    store = PolicyStore(tmp_path, broker.server._owner_identities)
    store.bootstrap()
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "alice")
    ledger.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
        destination="items-api", credential_ref="vault://http/items",
        allowed_endpoints=[{"host": "api.example.com", "path_template": "/v1/items",
                            "methods": ["POST"]}])
    ledger.grant_connection(grant_id="grant-a", connection_id="conn-a", owner_user_id="alice")
    rig = SimpleNamespace(store=store)
    _rule(rig, "app.write", "ask_first")
    broker.server._policy_store = store
    broker.server._ledger_for = lambda owner: ledger
    broker.server._roles = {os.getuid(): BOX_HOST}
    monkeypatch.setattr(box_admission, "resolve_owner", lambda identities, request, **_: {
        "op": "BOX_ADMITTED", "uid": 300001, "quota": 1024**3})
    with _connect(broker) as sock:
        sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CHANNEL", "principal": "alice"}))
        assert rf.read_frame_blocking(sock).control()["op"] == "BOX_ADMITTED"
        sock.sendall(rf.control(1, {
            "op": "OPEN", "op_id": new_op_id(), "principal": "alice", "grant_id": "grant-a",
            "connection_id": "conn-a", "verb": "POST", "credit": 8192,
            "request": {"url": URL, "body": "{}"}, "inference_usage": {"reference": "x"},
            "authority": {"agent": "main"}, "generation": -1, "token": "box"}))
        end = rf.read_frame_blocking(sock).control()
    assert (end["op"], end["error_class"], end["stream_sent"]) == ("END", "policy_ask", False)
    assert broker.sent == []
    assert any(row["kind"] == "gate" for row in store.receipts("alice"))


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                    reason="the box channel uses Unix peer credentials")
@pytest.mark.parametrize("first", ["import", "read"])
def test_the_box_import_is_its_first_operation_only(broker, monkeypatch, tmp_path, first):
    import os

    from tinyassets.broker import box_admission
    from tinyassets.broker.server import BOX_HOST

    store = _legacy_v1(tmp_path, broker.server._owner_identities)
    broker.server._policy_store = store
    broker.server._roles = {os.getuid(): BOX_HOST}
    monkeypatch.setattr(box_admission, "resolve_owner", lambda identities, request, **_: {
        "op": "BOX_ADMITTED", "uid": 300001, "quota": 1024**3})
    document = {"source_digest": "sha256:" + "1" * 64, "consents": [], "entries": [
        {"scope": "account", "table": "rules", "values": {
            "agent": "main", "action_class": "share", "behaviour": "do"}}]}

    def box_policy(doc):
        with _connect(broker) as sock:
            sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CHANNEL", "principal": "alice"}))
            assert rf.read_frame_blocking(sock).control()["op"] == "BOX_ADMITTED"
            sock.sendall(rf.control(rf.CONNECTION, {"op": "OWNER_POLICY", **doc}))
            return rf.read_frame_blocking(sock).control()

    if first == "read":
        assert box_policy({"action": "read"})["op"] == "OWNER_POLICY_RESULT"
    answer = box_policy({"action": "import", "document": document})
    if first == "import":
        assert answer["op"] == "OWNER_POLICY_RESULT" and answer["result"]["entries"] == 1
        assert store.decide("alice", center="alice", action_class="share")["decision"] == "allow"
        answer = box_policy({"action": "import", "document": document})
    assert answer == {"op": "OWNER_POLICY_REFUSED", "reason": "authority import is not pending"}


def test_the_router_routes_refuse_inside_a_box(monkeypatch):
    import asyncio

    from tinyassets import onboarding, owner_runtime

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(owner_runtime, "current", lambda: object())
    for handler in (onboarding._handle_policy, onboarding._handle_consents):
        response = asyncio.run(handler(SimpleNamespace(method="POST")))
        assert response.status_code == 403
        assert json.loads(response.body) == {"error": "owner_authority_requires_router"}
