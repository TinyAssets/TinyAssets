"""N10 a1: owner action rules and once-approvals are broker-held and gated at OPEN."""
# ruff: noqa: F811

from __future__ import annotations

import hashlib
import itertools
import json
import os
import socket
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_broker_server import _connect, broker, new_op_id  # noqa: F401
from tests.test_owner_policy import _session, admissions
from tinyassets import rpc_frames as rf
from tinyassets.broker import authority_gate
from tinyassets.broker.policy import PolicyStore
from tinyassets.broker.server import request_digest
from tinyassets.storage.outbound_connections import ConnectionLedger

pytestmark = pytest.mark.verifies(
    "owner-daemon-in-box:Owner action policy is held by the broker and enforced at OPEN")
COOKIE = "interactive-cookie-" + "n" * 32
URL = "https://api.example.com/v1/items"
PEERCRED = pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                              reason="the box channel uses Unix peer credentials")


def _ledger(root):
    ledger = ConnectionLedger(root / ".broker/outbound.db", data_root=root,
                              verify_authenticated_principal=lambda: "alice")
    ledger.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="items-api", credential_ref="vault://http/items",
        allowed_endpoints=[{"host": "api.example.com", "path_template": "/v1/items",
                            "methods": ["GET", "POST"]}])
    ledger.grant_connection(grant_id="grant-a", connection_id="conn-a", owner_user_id="alice")
    return ledger


@pytest.fixture
def rig(tmp_path):
    identities = admissions(tmp_path)
    store = PolicyStore(tmp_path, identities)
    store.bootstrap()
    _session(tmp_path, "alice", COOKIE)
    ledger = _ledger(tmp_path)
    ledger.create_connection(
        connection_id="git-a", owner_user_id="alice", connection_class="http",
        scopes=("git_read:owner/repo", "git_write:owner/repo"), provider="http",
        destination="git-a", credential_ref="vault://http/git-a", connection_type="http",
        auth_scheme="bearer", git_host="git.test",
        allowed_endpoints=[{"host": "git.test", "path_template": "/api", "methods": ["GET"]}])
    ledger.grant_connection(grant_id="grant-git", connection_id="git-a", owner_user_id="alice")
    return SimpleNamespace(store=store, ledger=ledger, root=tmp_path)


def _rule(rig, action_class, behaviour, *, agent="main", entries=()):
    current = rig.store.read("alice")
    document = json.loads(json.dumps(current.document))
    document["entries"].append({"scope": "account", "table": "rules", "values": {
        "agent": agent, "action_class": action_class, "behaviour": behaviour}})
    document["entries"].extend(entries)
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
    assert _check(rig) is None
    _rule(rig, "app.write", "ask_first")
    assert _check(rig) == "policy_ask"
    assert _gate_receipts(rig)[0]["decision"] == "deny"
    _rule(rig, "app.write", "hand_off")
    assert _check(rig) == "policy_hand_off"


def test_no_ledger_fact_the_box_can_write_exempts_a_call(rig):
    _rule(rig, "app.write", "hand_off")
    # The metering latch a box's model-source publish can set exempts nothing.
    with rig.ledger._connect() as db:
        db.execute("UPDATE outbound_connection_grants SET inference_required=1")
    assert _check(rig) == "policy_hand_off"


def test_an_agent_label_the_owner_never_configured_decides_as_main(rig):
    _rule(rig, "app.write", "hand_off")
    assert _check(rig, authority={"agent": "main"}) == "policy_hand_off"
    assert _check(rig, authority={}) == "policy_hand_off"
    assert _check(rig, authority={"agent": "ghost"}) == "policy_hand_off"
    # A configured agent's rules join main's; the stricter decides.
    current = rig.store.read("alice")
    rig.store.write("alice", COOKIE, current.generation, {**current.document, "entries": []})
    _rule(rig, "app.write", "ask_first", agent="worker")
    assert _check(rig, authority={"agent": "worker"}) == "policy_ask"
    assert _check(rig, authority={"agent": "ghost"}) is None
    assert _check(rig, authority={"agent": "main"}) is None


def test_hand_off_floor_ignores_the_box_agent_label(rig):
    _rule(rig, "security.change", "do", agent="worker", entries=[
        {"scope": "account", "table": "operation_kinds", "values": {
            "connection": "conn-a", "method": "POST", "kind": "security"}}])
    assert _check(rig, authority={"agent": "worker"}) == "policy_hand_off"


def test_a_once_approval_is_spent_once_and_goes_stale_with_the_generation(rig):
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
    with pytest.raises(PermissionError):  # only an owner session mints one
        rig.store.issue_once("alice", "agent-token-" + "x" * 32,
                             rig.store.read("alice").generation, digest)


def _standing(rig, **overrides):
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        authority_from_facts,
        local_query,
    )

    _grant, resource, incarnation = authority_from_facts(local_query(
        rig.ledger, query=AUTHORIZED_CONNECTION, principal="alice", command_center="alice",
        grant_id="grant-a", connection_id="conn-a"), principal="alice", grant_id="grant-a",
        connection_id="conn-a")
    standing = {"owner": "alice", "agent": "main", "action_class": "app.write",
                "operation": "POST", "connection": "conn-a",
                "origin": "https://api.example.com",
                "connection_revision": authority_gate.digest(
                    [resource.to_view().as_dict(), incarnation]),
                "consent_digest": "box-fact", "binding_revision": "box-fact",
                "scope": "always", "expires_at": None, **overrides}
    return {"scope": "account", "table": "approval_grants", "values": {
        "agent": "main", "record_kind": "preapproval", "decision_id": "request-1:1",
        "grant_json": json.dumps(standing)}}


def test_a_standing_grant_matches_on_broker_recomputed_facts(rig):
    _rule(rig, "app.write", "ask_first", entries=[_standing(rig, connection_revision="stale")])
    assert _check(rig) == "policy_ask"
    _rule(rig, "app.write", "ask_first", entries=[_standing(rig)])
    assert _check(rig) is None
    assert any(row["kind"] == "standing_grant" for row in rig.store.receipts("alice"))
    with pytest.raises(ValueError):  # the box's binding label is not accepted at all
        authority_gate.authority_fields({"agent": "main", "binding_revision": "binding-1"})


def test_a_task_scoped_grant_is_asked_again_at_the_gate(rig):
    _rule(rig, "app.write", "ask_first",
          entries=[_standing(rig, scope="task", task_id="task-1", expires_at=time.time() + 60)])
    assert _check(rig) == "policy_ask"
    with pytest.raises(ValueError):
        authority_gate.authority_fields({"agent": "main", "task_id": "task-1"})


@pytest.mark.parametrize("smuggled", [
    {"body": "{\"delete\":true}"},
    {"headers": {"X-HTTP-Method-Override": "DELETE"}},
    {"headers": [["x-method-override", "PUT"]]},
    {"headers": {"X-Http-Method": "POST"}},
])
def test_a_declared_read_with_a_body_or_method_override_is_a_write(rig, smuggled):
    _rule(rig, "app.write", "hand_off", entries=[
        {"scope": "account", "table": "operation_kinds", "values": {
            "connection": "conn-a", "method": "GET", "kind": "read"}}])
    assert _check(rig, "GET", request={"url": URL}) is None
    assert _check(rig, "GET", request={"url": URL, **smuggled}) == "policy_hand_off"
    assert _check(rig, "POST") == "policy_hand_off"


@pytest.mark.parametrize("method,target,refusal", [
    ("GET", "/owner/repo.git/info/refs?service=git-upload-pack", None),
    ("POST", "/owner/repo.git/git-upload-pack", None),
    # A git_read verb that reaches receive-pack is a write, whatever it is labelled.
    ("POST", "/owner/repo.git/git-receive-pack", "policy_hand_off"),
    ("GET", "/owner/repo.git/info/refs?service=git-receive-pack", "policy_hand_off"),
    ("POST", "/owner/other.git/git-upload-pack", "policy_hand_off"),
])
def test_a_git_read_is_a_read_only_at_its_own_upload_pack(rig, method, target, refusal):
    _rule(rig, "app.write", "hand_off")
    request = {"host": "git.test", "method": method, "target": target, "agent": "git/2",
               "incarnation": "i", "upload": method == "POST"}
    assert _check(rig, "git_read:owner/repo", grant="grant-git", connection="git-a",
                  request=request) == refusal
    assert _check(rig, "git_write:owner/repo", grant="grant-git", connection="git-a",
                  request={**request, "target": "/owner/repo.git/git-receive-pack"}
                  ) == "policy_hand_off"


def test_erasing_an_owner_erases_its_held_rules_and_approvals(rig):
    written = _rule(rig, "app.write", "ask_first")
    rig.store.issue_once("alice", COOKIE, written.generation, "sha256:" + "1" * 64)
    path = rig.store._path("alice")
    assert rig.store.erase("alice") == 1
    assert not path.exists()


def test_a_box_proposal_is_held_for_the_router_to_apply(rig):
    current = rig.store.read("alice")
    change = {"type": "policy_proposal", "expected_generation": current.generation,
              "policy": current.document}
    rig.store.receipt_proposal("alice", "alice", change, "request-1")
    with pytest.raises(PermissionError, match="already held"):  # immutable per card
        rig.store.receipt_proposal("alice", "alice", {**change, "policy": {}}, "request-1")
    assert rig.store.proposal("alice", "request-1") == change
    assert rig.store.proposal("alice", "request-2") is None
    assert rig.store.read("alice").generation == current.generation


def _box_channel(running, monkeypatch, store):
    from tinyassets.broker import box_admission
    from tinyassets.broker.server import BOX_HOST

    running.server._policy_store = store
    running.server._roles = {os.getuid(): BOX_HOST}
    monkeypatch.setattr(box_admission, "resolve_owner", lambda identities, request, **_: {
        "op": "BOX_ADMITTED", "uid": 300001, "quota": 1024**3})
    sock = _connect(running)
    sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CHANNEL", "principal": "alice"}))
    assert rf.read_frame_blocking(sock).control()["op"] == "BOX_ADMITTED"
    return sock


def _box_rig(running, tmp_path):
    _session(tmp_path, "alice", COOKIE)
    store = PolicyStore(tmp_path, running.server._owner_identities)
    store.bootstrap()
    ledger = _ledger(tmp_path)
    ledger.create_connection(
        connection_id="git-a", owner_user_id="alice", connection_class="http",
        scopes=("git_read:owner/repo",), provider="http", destination="git-a",
        credential_ref="vault://http/git-a", connection_type="http", auth_scheme="bearer",
        git_host="git.test",
        allowed_endpoints=[{"host": "git.test", "path_template": "/api", "methods": ["GET"]}])
    ledger.grant_connection(grant_id="grant-git", connection_id="git-a", owner_user_id="alice")
    running.server._ledger_for = lambda owner: ledger
    return store, ledger


_OPS = itertools.count(1)


def _box_open(sock, **extra):
    sock.sendall(rf.control(1, {
        "op": "OPEN", "op_id": new_op_id(f"{next(_OPS):016d}"), "principal": "alice",
        "grant_id": "grant-a",
        "connection_id": "conn-a", "verb": "POST", "credit": 8192,
        "request": {"url": URL, "body": "{}"}, "generation": -1, "token": "box", **extra}))
    return rf.read_frame_blocking(sock).control()


@PEERCRED
def test_a_box_open_with_an_inference_flag_still_meets_the_rules(broker, monkeypatch,
                                                                  tmp_path):
    store, _ledger = _box_rig(broker, tmp_path)
    _rule(SimpleNamespace(store=store), "app.write", "ask_first")
    with _box_channel(broker, monkeypatch, store) as sock:
        end = _box_open(sock, inference_usage={"reference": "x"},
                        authority={"agent": "main"})
    assert (end["op"], end["error_class"], end["stream_sent"]) == ("END", "policy_ask", False)
    assert broker.sent == []
    assert any(row["kind"] == "gate" for row in store.receipts("alice"))


@PEERCRED
def test_spend_caps_meet_a_metered_grant_the_box_calls_without_its_usage_flag(
        broker, monkeypatch, tmp_path):
    store, ledger = _box_rig(broker, tmp_path)
    current = store.read("alice")
    capped = json.loads(json.dumps(current.document))
    capped["spend"]["daily"] = 100
    store.write("alice", COOKIE, current.generation, capped)
    with ledger._connect() as db:
        db.execute("UPDATE outbound_connection_grants SET inference_required=1")
    with _box_channel(broker, monkeypatch, store) as sock:
        end = _box_open(sock)
    assert (end["op"], end["error_class"], end["stream_sent"]) == ("END", "refused", False)
    assert broker.sent == []
    assert any(row["rule"] == "spend_amount_required" for row in store.receipts("alice"))


def _git_open(target, method="GET"):
    return dict(grant_id="grant-git", connection_id="git-a", verb="git_read:owner/repo",
                request={"host": "git.test", "method": method, "target": target,
                         "agent": "git/2", "incarnation": "i", "upload": method == "POST"})


@PEERCRED
@pytest.mark.parametrize("extra,refusal", [
    (dict(verb="model_duplex", request={"url": URL}), "policy_hand_off"),
    (dict(verb="GET", request={"url": URL}), None),
    (dict(verb="GET", request={"url": URL, "body": "{}"}), "policy_hand_off"),
    (dict(verb="HEAD", request={"url": URL, "body": "x"}), "policy_hand_off"),
    (dict(verb="GET", request={"url": URL, "headers": {"X-HTTP-Method-Override": "DELETE"}}),
     "policy_hand_off"),
    (_git_open("/owner/repo.git/info/refs?service=git-upload-pack"), None),
    (_git_open("/owner/other.git/git-upload-pack", "POST"), "policy_hand_off"),
    (_git_open("/owner/repo.git/git-receive-pack", "POST"), "policy_hand_off"),
])
def test_the_box_open_path_reads_each_call_itself(broker, monkeypatch, tmp_path, extra,
                                                  refusal):
    """Through the real OPEN: duplex, a disguised read and a git read elsewhere are writes."""
    store, _ledger = _box_rig(broker, tmp_path)
    _rule(SimpleNamespace(store=store), "app.write", "hand_off", entries=[
        {"scope": "account", "table": "operation_kinds", "values": {
            "connection": "conn-a", "method": method, "kind": "read"}}
        for method in ("GET", "HEAD")])
    with _box_channel(broker, monkeypatch, store) as sock:
        first = _box_open(sock, **extra)
    if refusal is None:
        assert first["op"] == "ADMITTED"
    else:
        assert (first["op"], first["error_class"], first["stream_sent"]) == (
            "END", refusal, False)
        assert broker.sent == []


def _box_policy(sock, **doc):
    sock.sendall(rf.control(rf.CONNECTION, {"op": "OWNER_POLICY", **doc}))
    return rf.read_frame_blocking(sock).control()


@PEERCRED
def test_a_held_proposal_cannot_be_swapped_flooded_or_applied_twice(broker, monkeypatch,
                                                                     tmp_path):
    """Review of #5333: the owner's accept applies exactly the pending card it showed."""
    import asyncio

    from tinyassets import onboarding, owner_runtime
    from tinyassets.api import helpers
    from tinyassets.auth import middleware
    from tinyassets.broker import policy, policy_submit
    from tinyassets.broker.policy import PolicyRefused
    from tinyassets.broker.server import OWNER, _Connection
    from tinyassets.onboarding import owner_sessions

    store, _ledger = _box_rig(broker, tmp_path)
    monkeypatch.setattr(policy, "PENDING_PROPOSALS", 5)
    current = store.read("alice")
    doc_a = json.loads(json.dumps(current.document))
    doc_a["entries"].append({"scope": "account", "table": "rules", "values": {
        "agent": "main", "action_class": "app.write", "behaviour": "ask_first"}})
    doc_b = json.loads(json.dumps(current.document))
    doc_b["entries"] += [{"scope": "account", "table": "rules", "values": {
        "agent": "main", "action_class": cls, "behaviour": "do"}}
        for cls in ("money.move", "security.change", "access.grant", "spend")]
    shown_a, change_b = ({"type": "policy_proposal", "expected_generation": current.generation,
                          "policy": doc} for doc in (doc_a, doc_b))
    with _box_channel(broker, monkeypatch, store) as sock:
        def propose(request_id, change):
            return _box_policy(sock, action="proposal", change=change, request_id=request_id)

        assert propose("card-r", shown_a)["op"] == "OWNER_POLICY_RESULT"
        assert propose("card-r", shown_a)["op"] == "OWNER_POLICY_RESULT"  # same doc: no-op
        assert propose("card-r", change_b)["op"] == "OWNER_POLICY_REFUSED"  # refiling
        flood = [propose(f"flood-{i}", {"x": i})["op"] for i in range(8)]
        assert flood == ["OWNER_POLICY_RESULT"] * 4 + ["OWNER_POLICY_REFUSED"] * 4
        assert store.proposal("alice", "card-r") == shown_a  # never evicted

    peer = object.__new__(_Connection)
    peer._role, peer._server = OWNER, broker.server

    def submit(_root, owner, action, **fields):
        answer = asyncio.run_coroutine_threadsafe(peer._policy_operation({
            "op": "OWNER_POLICY", "action": action, "principal": owner, **fields}),
            broker.loop).result(30)
        if answer["op"] != "OWNER_POLICY_RESULT":
            raise PolicyRefused(answer["reason"])
        return answer["result"]

    monkeypatch.setattr(policy_submit, "submit", submit)
    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "_same_origin_json", lambda *a, **k: True)
    monkeypatch.setattr(owner_runtime, "current", lambda: None)
    monkeypatch.setattr(middleware, "current_identity",
                        lambda: SimpleNamespace(user_id="alice"))
    monkeypatch.setattr(owner_sessions, "require", lambda request, owner: {
        "session_hash": hashlib.sha256(COOKIE.encode()).hexdigest()})
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)

    def accept(shown):
        async def body(*_a, **_k):
            return {"action": "apply_proposal", "center": "alpha", "request_id": "card-r",
                    "shown": shown}

        monkeypatch.setattr(onboarding, "_read_small_json", body)
        return asyncio.run(onboarding._handle_policy(
            SimpleNamespace(method="POST", query_params={})))

    assert accept(change_b).status_code == 409  # not the card the owner was shown
    assert store.read("alice").generation == current.generation
    assert accept(shown_a).status_code == 200
    assert store.read("alice").document == doc_a
    assert accept(shown_a).status_code == 409  # applied at most once
    assert store.read("alice").generation == current.generation + 1
    with _box_channel(broker, monkeypatch, store) as sock:
        assert _box_policy(sock, action="proposal", change=change_b,
                           request_id="card-r")["op"] == "OWNER_POLICY_REFUSED"
        # The applied card left the pending set, so a new card is held again.
        assert _box_policy(sock, action="proposal", change={"x": "next"},
                           request_id="card-next")["op"] == "OWNER_POLICY_RESULT"


def test_box_mintable_receipts_cannot_push_a_gate_refusal_out_of_view(rig):
    _rule(rig, "app.write", "hand_off")
    assert _check(rig) == "policy_hand_off"
    for i in range(60):
        rig.store.receipt_proposal("alice", "alice", {"x": i})
        rig.store.decide("alice", center="alice", action_class="app.read")
    receipts = rig.store.receipts("alice")
    assert any(row["kind"] == "gate" and row["decision"] == "deny" for row in receipts)
    assert sum(row["kind"] in {"proposal", "decision"} for row in receipts) == 50


def test_the_router_policy_route_refuses_inside_a_box(monkeypatch):
    import asyncio

    from tinyassets import onboarding, owner_runtime

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(owner_runtime, "current", lambda: object())
    for method in ("GET", "POST"):
        response = asyncio.run(onboarding._handle_policy(SimpleNamespace(method=method)))
        assert response.status_code == 403
        assert json.loads(response.body) == {"error": "policy_requires_owner_session"}


@pytest.mark.parametrize("fields", [
    {"body": "{\"text\":\"hi\"}"},
    {"body": {"text": "hi", "n": 1}},
    {"body": "x", "header_name": " X-Api-Key "},
    {"headers": {"Accept": "application/json"}, "body": "y"},
    {},
])
def test_the_router_once_digest_matches_the_effectors_open(
        tmp_path, monkeypatch, _signed_in_operator, fields):
    """The card's once-approval and the call's OPEN cannot drift."""
    from tests.test_authenticated_external_call_effector import _HTTP_FLAG, _setup
    from tinyassets import policy_rules
    from tinyassets.auth.middleware import identity_context
    from tinyassets.broker.client import AuthorityRefused
    from tinyassets.effectors import authenticated_external_call as aec

    monkeypatch.setattr(policy_rules, "configured_agents", lambda _home: {"main"})
    monkeypatch.setattr(policy_rules, "classify", lambda *_: ("app.write", "POST"))
    monkeypatch.setattr(policy_rules, "decide", lambda *_a, **_kw:
                        policy_rules.Decision(policy_rules.DO, None, "test_policy"))
    monkeypatch.setenv(_HTTP_FLAG, "1")
    seen = []

    class Capture:
        def request(self, verb, request, **_kw):
            seen.append((verb, request))
            raise AuthorityRefused("policy_ask")

        def close(self):
            pass

    monkeypatch.setattr(aec, "_open_connection_proxy", lambda **_k: Capture())
    packet = {"sink": aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL, "connection_id": "conn-http",
              "grant_id": "grant-http", "verb": "POST",
              "request": {"method": "POST", "path": "/v1/messages", **fields}}
    with identity_context(replace(_signed_in_operator, user_id="user-1")):
        data_root, universe_dir, db_path = _setup(tmp_path)
        result = aec.run_authenticated_external_call_effector(
            node_id="n1", output_keys=["out"], run_state={"out": json.dumps(packet)},
            base_path=str(universe_dir), run_id="r1")
        view = ConnectionLedger(db_path, data_root=data_root,
                                verify_authenticated_principal=lambda: "user-1"
                                )._active_resource_snapshot_for_grant("grant-http")[0].to_view()
    verb, wire = seen[0]
    host, _ = aec._resolve_host(packet["request"], view)
    url, _ = aec._build_url(packet["request"], host)  # the card's env.destination
    assert authority_gate.once_request_digest(
        grant_id="grant-http", connection_id="conn-http", verb="POST",
        request=packet["request"], destination=url) == request_digest(
        grant_id="grant-http", connection_id="conn-http", verb=verb, request=wire)
    # The broker's refusal reads as the box's own advisory refusal.
    assert "rule_ask_first" in json.dumps(result)
