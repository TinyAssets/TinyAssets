"""N10 PR1: owner authority is broker-held, widened only by the router, gated at OPEN."""
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_broker_server import Script, _connect, broker, new_op_id  # noqa: F401
from tests.test_owner_box_lifecycle import fleet  # noqa: F401
from tests.test_owner_policy import _session, admissions
from tinyassets import rpc_frames as rf
from tinyassets.broker import authority_gate
from tinyassets.broker.policy import ImportPending, PolicyRefused, PolicyStore
from tinyassets.broker.server import request_digest
from tinyassets.storage.outbound_connections import ConnectionLedger

pytestmark = pytest.mark.verifies(
    "owner-daemon-in-box:Owner authority is held by the broker and gated at OPEN")
COOKIE = "interactive-cookie-" + "n" * 32
URL = "https://api.example.com/v1/items"
CALL = "authenticated_external_call"
PEERCRED = pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                              reason="the box channel uses Unix peer credentials")


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


def _rule(rig, action_class, behaviour, *, agent="main", **extra):
    current = rig.store.read("alice")
    document = json.loads(json.dumps(current.document))
    document["entries"].append({"scope": "account", "table": "rules", "values": {
        "agent": agent, "action_class": action_class, "behaviour": behaviour}})
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


def _git(kind, target, *, method="POST", host="git.test"):
    return {"host": host, "method": method, "target": target, "agent": "git/2",
            "incarnation": "i", "upload": method == "POST"}


def _gate_receipts(rig):
    return [row for row in rig.store.receipts("alice") if row["kind"] == "gate"]


def test_an_ask_first_open_without_approval_is_refused_and_receipted(rig):
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
    assert _check(rig) is None
    _rule(rig, "app.write", "ask_first")
    assert _check(rig) == "policy_ask"
    assert _gate_receipts(rig)[0]["decision"] == "deny"


def test_no_ledger_fact_the_box_can_write_exempts_a_call(rig):
    """Review finding 1: a box-configured model capability or flag exempted every gate."""
    from tinyassets.broker import capabilities

    _rule(rig, "app.write", "hand_off")
    assert _check(rig) == "policy_hand_off"
    capabilities.local_operation(rig.ledger, principal="alice", command_center="alice",
                                 document={
        "action": "configure", "grant_id": "grant-a", "connection_id": "conn-a",
        "capability_kind": "model_use", "descriptor": {
            "wire": "openai_chat", "models": [{"id": "m", "tools": False, "context": 10}],
            "billing": "free"}, "enabled": True, "preview": False})
    with rig.ledger._connect() as db:
        db.execute("UPDATE outbound_connection_grants SET inference_required=1")
    assert _check(rig) == "policy_hand_off"
    rig.store.write("alice", COOKIE, rig.store.read("alice").generation,
                    {**rig.store.read("alice").document, "entries": []})
    assert _check(rig) == "consent_missing"  # a model call needs consent like any other


def test_an_agent_label_the_owner_never_configured_decides_as_main(rig):
    """Review finding 2: an unknown label fell back to the laxer defaults."""
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
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
    entry = {"scope": "account", "table": "operation_kinds", "values": {
        "connection": "conn-a", "method": "POST", "kind": "security"}}
    current = rig.store.read("alice")
    document = json.loads(json.dumps(current.document))
    document["entries"] += [entry, {"scope": "account", "table": "rules", "values": {
        "agent": "worker", "action_class": "security.change", "behaviour": "do"}}]
    rig.store.write("alice", COOKIE, current.generation, document)
    assert _check(rig, authority={"agent": "worker"}) == "policy_hand_off"


def test_a_once_approval_is_spent_once_and_goes_stale_with_the_generation(rig):
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
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


def test_consent_is_checked_before_a_once_approval_is_spent(rig):
    """Review finding 10b: a consent refusal used to burn the owner's approval."""
    written = _rule(rig, "app.write", "ask_first")
    digest = request_digest(grant_id="grant-a", connection_id="conn-a", verb="POST",
                            request={"url": URL, "body": "{}"})
    token = rig.store.issue_once("alice", COOKIE, written.generation, digest)["token"]
    assert _check(rig, authority={"agent": "main", "approval": token}) == "consent_missing"
    assert not [row for row in rig.store.receipts("alice")
                if row["kind"] == "once_approval" and row["outcome"] == "consumed"]
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
    # The consent grant advanced the generation; the approval went stale with it.
    assert _check(rig, authority={"agent": "main", "approval": token}) == "policy_ask"
    token = rig.store.issue_once("alice", COOKIE, rig.store.read("alice").generation,
                                 digest)["token"]
    assert _check(rig, authority={"agent": "main", "approval": token}) is None


def _standing(rig, **overrides):
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        authority_from_facts,
        local_query,
    )

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
                "binding_revision": "binding-1", "scope": "always", "expires_at": None,
                **overrides}
    return {"scope": "account", "table": "approval_grants", "values": {
        "agent": "main", "record_kind": "preapproval", "decision_id": "request-1:1",
        "grant_json": json.dumps(standing)}}


def test_a_standing_grant_matches_on_broker_recomputed_revisions(rig):
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
    _rule(rig, "app.write", "ask_first", entries=[_standing(rig)])
    assert _check(rig) is None
    with pytest.raises(ValueError):  # the box's binding label is not accepted at all
        authority_gate.authority_fields({"agent": "main", "binding_revision": "binding-1"})
    rig.store.revoke_consent("alice", CALL, "items-api")
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
    assert _check(rig) == "policy_ask"  # the consent digest moved


def test_a_task_scoped_grant_is_asked_again_at_the_gate(rig):
    """Review finding 7: the task id is the box's assertion."""
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
    _rule(rig, "app.write", "ask_first",
          entries=[_standing(rig, scope="task", task_id="task-1", expires_at=time.time() + 60)])
    assert _check(rig) == "policy_ask"
    with pytest.raises(ValueError):
        authority_gate.authority_fields({"agent": "main", "task_id": "task-1"})


def test_consent_gates_non_reads_only(rig):
    assert _check(rig) == "consent_missing"
    assert _check(rig, "GET", request={"url": URL}) is None
    rig.store.grant_consent("alice", COOKIE, CALL, "items-api")
    assert _check(rig) is None


@pytest.mark.parametrize("smuggled", [
    {"body": "{\"delete\":true}"},
    {"headers": {"X-HTTP-Method-Override": "DELETE"}},
    {"headers": [["x-method-override", "PUT"]]},
    {"headers": {"X-Http-Method": "POST"}},
])
def test_a_get_with_a_body_or_method_override_is_a_write(rig, smuggled):
    """Review finding 9."""
    assert _check(rig, "GET", request={"url": URL}) is None
    assert _check(rig, "GET", request={"url": URL, **smuggled}) == "consent_missing"
    assert _check(rig, "HEAD", request={"url": URL, **smuggled}) == "consent_missing"


def test_a_workspace_push_needs_consent_unless_access_is_full(rig):
    def push(name):
        return _check(rig, "git_write:owner/repo", grant="grant-" + name, connection=name,
                      request=_git("git_write", "/owner/repo.git/git-receive-pack"))

    assert push("git-exact") == "consent_missing"
    assert push("git-full") is None
    assert _check(rig, "git_read:owner/repo", grant="grant-git-exact", connection="git-exact",
                  request=_git("git_read", "/owner/repo.git/info/refs?service=git-upload-pack",
                               method="GET")) is None
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


@pytest.mark.parametrize("verb,request_", [
    # A push consent for one repo is checked against the verb only: another target passed.
    ("git_write:owner/repo", _git("git_write", "/owner/OTHER.git/git-receive-pack")),
    # A git_read verb reached receive-pack.
    ("git_read:owner/repo", _git("git_read", "/owner/repo.git/git-receive-pack")),
    ("git_read:owner/repo", _git("git_read", "/owner/repo.git/info/refs?service="
                                             "git-receive-pack", method="GET")),
    ("git_read:owner/repo", _git("git_read", "/owner/repo.git/git-upload-pack",
                                 host="evil.test")),
    ("git_read:owner/other", _git("git_read", "/owner/other.git/git-upload-pack")),
])
def test_a_git_call_reaches_only_its_own_verbs_endpoints(rig, verb, request_):
    """Review finding 3."""
    rig.store.grant_consent("alice", COOKIE, "workspace", "push:git-exact:git.test/owner/repo")
    assert _check(rig, verb, grant="grant-git-exact", connection="git-exact",
                  request=request_) == "refused"


def test_git_dispatch_refuses_a_target_outside_the_verb(rig):
    from tinyassets.broker.git_http import git_target
    from tinyassets.broker.ledger_queries import (
        AUTHORIZED_CONNECTION,
        authority_from_facts,
        local_query,
    )

    _grant, resource, _ = authority_from_facts(local_query(
        rig.ledger, query=AUTHORIZED_CONNECTION, principal="alice", command_center="alice",
        grant_id="grant-git-exact", connection_id="git-exact"), principal="alice",
        grant_id="grant-git-exact", connection_id="git-exact")
    assert git_target(resource, "git_write:owner/repo",
                      _git("git_write", "/owner/repo.git/git-receive-pack")) \
        == ("git_write", "owner/repo")
    with pytest.raises(PermissionError):
        git_target(resource, "git_write:owner/repo",
                   _git("git_write", "/owner/OTHER.git/git-receive-pack"))


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


def _on_defaults_with_notice(store, owner="alice"):
    """The ALARM paths: a new owner's defaults, an alarm receipt, a notice to review."""
    assert store.import_alarm(owner)
    assert store.decide(owner, center=owner, action_class="app.write")["decision"] == "allow"
    assert any(row["kind"] == "import" and row["decision"] == "alarm"
               for row in store.receipts(owner))
    return True


def test_an_import_is_accepted_once_and_alarms_when_rows_were_invalid(tmp_path, caplog):
    store = _legacy_v1(tmp_path)
    assert store.import_pending("alice") and not store.import_pending("bob")
    document = {"source_digest": "sha256:" + "0" * 64, "entries": [
        {"scope": "account", "table": "rules", "values": {
            "agent": "main", "action_class": "share", "behaviour": "hand_off"}},
        {"scope": "alpha", "table": "rules", "values": {}},
    ], "consents": [
        {"sink": CALL, "destination": "items-api", "granted_at": 5.0, "granted_by": "alice"},
        {"sink": "", "destination": "x", "granted_at": 1.0, "granted_by": "alice"},
    ]}
    result = store.import_authority("alice", document)
    assert (result["entries"], result["consents"], result["dropped"]) == (1, 1, 2)
    assert store.decide("alice", center="alice", action_class="share")["decision"] == "deny"
    # A row that could not be read is reported to the owner, never silently lost.
    assert _on_defaults_with_notice(store) and "ALARM" in caplog.text
    assert store.consent_active("alice", CALL, "items-api")
    with pytest.raises(PolicyRefused, match="not pending"):
        store.import_authority("alice", document)
    receipt = next(row for row in store.receipts("alice") if row["kind"] == "import"
                   and row["outcome"] == "partial")
    assert json.loads(receipt["rule"]) == {"entries": 1, "consents": 1, "dropped": 2}


def test_a_clean_import_raises_no_notice(tmp_path):
    store = _legacy_v1(tmp_path)
    store.import_authority("alice", {"source_digest": "sha256:" + "0" * 64,
                                     "entries": [], "consents": []})
    assert not store.import_alarm("alice")
    assert store.decide("alice", center="alice", action_class="app.write")["decision"] \
        == "allow"


def test_a_box_operation_waits_for_the_import_and_cannot_end_it(tmp_path, rig):
    """Review finding 5: any box op used to abandon the import."""
    store = _legacy_v1(tmp_path, rig.store._admissions)
    with pytest.raises(ImportPending):
        store.require_settled("alice")
    assert store.import_pending("alice")
    gate = authority_gate.check(
        store, rig.ledger, owner="alice", grant_id="grant-a", connection_id="conn-a",
        verb="POST", request={"url": URL}, authority={}, request_digest="sha256:x")
    assert gate == "authority_import_pending" and store.import_pending("alice")


def test_an_abandoned_import_alarms_and_tells_the_owner(tmp_path, caplog):
    """Review finding 10c, as decided: the defaults (the agent keeps working),
    an ALARM, and a notice until the owner saves their rules."""
    store = _legacy_v1(tmp_path)
    _session(tmp_path, "alice", COOKIE)
    store.abandon_import("alice", reason="extraction errors: 1")
    assert "ALARM: owner authority import abandoned for alice" in caplog.text
    with pytest.raises(PolicyRefused, match="not pending"):
        store.import_authority("alice", {"source_digest": "", "entries": [], "consents": []})
    assert _on_defaults_with_notice(store)
    current = store.read("alice")
    assert current.document["entries"] == []
    store.write("alice", COOKIE, current.generation, current.document)
    assert not store.import_alarm("alice")


def test_the_owners_first_router_write_supersedes_the_import(tmp_path):
    store = _legacy_v1(tmp_path)
    _session(tmp_path, "alice", COOKIE)
    current = store.read("alice")
    store.write("alice", COOKIE, current.generation, current.document)
    assert not store.import_pending("alice")
    with pytest.raises(PolicyRefused, match="not pending"):
        store.import_authority("alice", {"source_digest": "", "entries": [], "consents": []})
    assert any(row["outcome"] == "superseded" for row in store.receipts("alice"))


def test_a_pending_import_ends_with_a_notice_a_deadline_after_its_first_lift(tmp_path, monkeypatch):
    from tinyassets.broker import policy

    store = _legacy_v1(tmp_path)
    monkeypatch.setattr(policy.time, "time", lambda: 1_000.0)
    assert store.import_pending("alice")  # an owner who never wakes is never failed
    assert store.note_import_attempt("alice") == 1
    monkeypatch.setattr(policy.time, "time", lambda: 1_000.0 + policy.IMPORT_DEADLINE_S + 1)
    assert not store.import_pending("alice")
    assert _on_defaults_with_notice(store)


def test_migration_consents_each_live_model_grant(tmp_path, rig):
    """Before N10 no model call needed consent; the migration keeps them working."""
    with rig.ledger._connect() as db:
        db.execute("UPDATE outbound_connection_grants SET inference_required=1 "
                   "WHERE grant_id='grant-a'")
    store = _legacy_v1(tmp_path, rig.store._admissions)
    assert store.consent_active("alice", CALL, "items-api")
    assert not store.consent_active("alice", CALL, "git-exact")


def _old_files(mount, *, entries=None, consents=(("patch_intake", "r-1", None),)):
    """A box volume as it stood before N10: its policy file and one center's consents."""
    from tinyassets.boxes import authority_import as lift

    sidecar = mount / lift.SIDECARS / "center"
    sidecar.mkdir(parents=True)
    with sqlite3.connect(mount / lift.POLICY_FILE) as db:
        db.execute("CREATE TABLE policy (singleton INTEGER, generation INTEGER, digest TEXT, "
                   "document TEXT)")
        db.execute("INSERT INTO policy VALUES (1,1,'',?)", (json.dumps({"entries": entries or [
            {"scope": "account", "table": "rules", "values": {
                "agent": "main", "action_class": "share", "behaviour": "hand_off"}}]}),))
    with sqlite3.connect(sidecar / lift.CONSENTS_FILE) as db:
        db.execute("CREATE TABLE effector_consents (sink TEXT, destination TEXT, "
                   "granted_at REAL, granted_by TEXT, revoked_at REAL)")
        for sink, destination, revoked in consents:
            db.execute("INSERT INTO effector_consents VALUES (?,?,2,'alice',?)",
                       (sink, destination, revoked))
    return mount / lift.POLICY_FILE, sidecar / lift.CONSENTS_FILE


def test_the_extractor_lifts_live_rows_and_remove_deletes_the_files(tmp_path):
    from tinyassets.boxes.authority_import import extract, remove

    mount = tmp_path / "mount"
    policy, consents = _old_files(mount, consents=[("patch_intake", "r-1", None),
                                                   ("patch_intake", "r-0", 3.0)])
    payload = extract(mount, tmp_path / "private")
    assert (payload["errors"], payload["found"]) == (0, 2)
    assert payload["entries"][0]["values"]["action_class"] == "share"
    assert [row["destination"] for row in payload["consents"]] == ["r-1"]
    assert policy.exists() and consents.exists()  # extraction touches nothing
    remove(mount)
    assert not policy.exists() and not consents.exists()
    assert extract(mount, tmp_path / "private")["found"] == 0


def test_the_extractor_folds_a_wal_into_its_private_copy(tmp_path):
    from tinyassets.boxes import authority_import as lift

    mount = tmp_path / "mount"
    mount.mkdir()
    db = sqlite3.connect(mount / lift.POLICY_FILE)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE policy (singleton INTEGER, document TEXT)")
    db.execute("INSERT INTO policy VALUES (1, ?)", (json.dumps({"entries": [
        {"scope": "account", "table": "rules", "values": {"action_class": "share"}}]}),))
    db.commit()
    try:
        assert (mount / (lift.POLICY_FILE + "-wal")).stat().st_size > 0
        payload = lift.extract(mount, tmp_path / "private")
    finally:
        db.close()
    assert payload["errors"] == 0 and len(payload["entries"]) == 1


@pytest.mark.parametrize("damage", ["symlink", "oversized", "linked_dir", "hardlink"])
def test_the_extractor_refuses_untrusted_files_and_counts_them(tmp_path, monkeypatch, damage):
    from tinyassets.boxes import authority_import as lift

    mount = tmp_path / "mount"
    policy, consents = _old_files(mount)
    target = tmp_path / "outside.db"
    if damage == "symlink":
        policy.rename(target)
        policy.symlink_to(target)
    elif damage == "oversized":
        monkeypatch.setattr(lift, "MAX_BYTES", 16)
    elif damage == "linked_dir":
        sidecars = mount / lift.SIDECARS
        sidecars.rename(tmp_path / "elsewhere")
        sidecars.symlink_to(tmp_path / "elsewhere")
    else:
        os.link(consents, target)
    payload = lift.extract(mount, tmp_path / "private")
    assert payload["errors"] >= 1
    assert (payload["entries"], payload["consents"]) == ([], [])
    if damage == "symlink":
        assert target.exists() and policy.is_symlink()
    if damage == "linked_dir":
        assert (tmp_path / "elsewhere/center" / lift.CONSENTS_FILE).exists()


def test_a_hostile_view_is_refused_fast_and_the_files_are_kept(tmp_path):
    """Review finding 6: a recursive view ran unbounded in root boxhostd."""
    from tinyassets.boxes import authority_import as lift

    mount = tmp_path / "mount"
    mount.mkdir()
    with sqlite3.connect(mount / lift.POLICY_FILE) as db:
        db.execute(
            "CREATE VIEW policy AS WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 "
            "FROM c WHERE n<20000000) SELECT 1 AS singleton, json_object('entries', "
            "json_array()) AS document FROM (SELECT max(n) FROM c)")
    started = time.monotonic()
    payload = lift.extract(mount, tmp_path / "private")
    assert time.monotonic() - started < 2
    assert payload["errors"] == 1 and (mount / lift.POLICY_FILE).exists()


@pytest.mark.parametrize("hostile", ["huge_value", "slow_scan", "deep_json", "many_rows"])
def test_hostile_tables_hit_a_bound_not_the_host(tmp_path, monkeypatch, hostile):
    from tinyassets.boxes import authority_import as lift

    mount = tmp_path / "mount"
    mount.mkdir()
    with sqlite3.connect(mount / lift.POLICY_FILE) as db:
        if hostile == "huge_value":
            db.execute("CREATE TABLE policy (singleton INTEGER, document TEXT "
                       "GENERATED ALWAYS AS (printf('%.*c', 4000000, 'x')) VIRTUAL)")
            db.execute("INSERT INTO policy(singleton) VALUES (1)")
        elif hostile == "slow_scan":
            monkeypatch.setattr(lift, "STEP_BUDGET", 100_000)
            db.execute("CREATE TABLE policy (singleton INTEGER, document TEXT)")
            db.execute("INSERT INTO policy WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL "
                       "SELECT n+1 FROM c WHERE n<300000) SELECT 0, '' FROM c")
        elif hostile == "deep_json":
            db.execute("CREATE TABLE policy (singleton INTEGER, document TEXT)")
            db.execute("INSERT INTO policy VALUES (1, ?)", ("[" * 100_000 + "]" * 100_000,))
        else:
            monkeypatch.setattr(lift, "MAX_ROWS", 3)
            db.execute("CREATE TABLE policy (singleton INTEGER, document TEXT)")
            db.executemany("INSERT INTO policy VALUES (1, '{}')", [()] * 5)
    payload = lift.extract(mount, tmp_path / "private")
    assert payload["errors"] == 1 and payload["entries"] == []
    assert (mount / lift.POLICY_FILE).exists()


def _fleet_store(fleet, tmp_path, *, pending):
    store = PolicyStore(tmp_path, fleet.identities)
    store.bootstrap()
    if pending:
        with sqlite3.connect(store._path("a")) as db:
            db.execute("DELETE FROM import_state")
            db.execute("PRAGMA user_version=1")
        store = PolicyStore(tmp_path, fleet.identities)
        store.bootstrap()
    fleet.routing.server._policy_store = store
    return store


def _mounted(fleet, tmp_path, monkeypatch):
    mount = tmp_path / "mount"
    volumes = fleet.manager.volumes
    attach = volumes.attach
    monkeypatch.setattr(volumes, "attach", lambda uid: SimpleNamespace(
        mountpoint=mount, oci_mount=attach(uid).oci_mount))
    seen = []
    start = fleet.runtime.start

    def started(uid, gid, **kwargs):
        seen.append(sorted(path.name for path in mount.rglob("*.db")))
        return start(uid, gid, **kwargs)

    monkeypatch.setattr(fleet.runtime, "start", started)
    return mount, seen


def test_a_wake_of_a_pending_owner_imports_its_old_files_once(fleet, tmp_path, monkeypatch):
    mount, seen = _mounted(fleet, tmp_path, monkeypatch)
    _old_files(mount)
    store = _fleet_store(fleet, tmp_path, pending=True)
    fleet.wake("a")
    assert seen == [[]]  # the box process starts with the files already gone
    assert store.decide("a", center="a", action_class="share")["decision"] == "deny"
    assert store.consent_active("a", "patch_intake", "r-1")
    assert not store.import_pending("a")
    assert [row["outcome"] for row in store.receipts("a") if row["kind"] == "import"][0] \
        == "committed"
    assert list(fleet.manager.authority_holding.glob("*.json"))  # held until settled


def test_a_wake_with_damaged_files_alarms_keeps_them_and_tells_the_owner(
        fleet, tmp_path, monkeypatch, caplog):
    from tinyassets.boxes import authority_import as lift

    mount, seen = _mounted(fleet, tmp_path, monkeypatch)
    policy, consents = _old_files(mount)
    policy.rename(tmp_path / "outside.db")
    policy.symlink_to(tmp_path / "outside.db")
    store = _fleet_store(fleet, tmp_path, pending=True)
    fleet.wake("a")
    assert (mount / lift.POLICY_FILE).is_symlink() and consents.exists()
    assert "ALARM: owner authority import abandoned for a" in caplog.text
    assert _on_defaults_with_notice(store, "a")
    assert not store.import_pending("a")
    assert not list(fleet.manager.authority_holding.glob("*.json"))


def test_the_policy_read_carries_the_import_notice(tmp_path, rig):
    store = _legacy_v1(tmp_path, rig.store._admissions)
    store.abandon_import("alice", reason="extraction errors: 1")
    assert store.import_alarm("alice")
    source = Path("tinyassets/broker/server.py").read_text(encoding="utf-8")
    assert '"import_alarm": store.import_alarm(owner)' in source


def test_destroying_an_owner_erases_its_held_authority(tmp_path):
    from contextlib import nullcontext

    from tinyassets.boxes.owner_daemons import Box, OwnerDaemons

    calls = []
    runtime = SimpleNamespace(box_id=lambda uid: f"owner-{uid}",
                              stop=lambda uid: calls.append(("stop", uid)))
    volumes = SimpleNamespace(operation=lambda uid, **_: nullcontext(),
                              delete=lambda uid: calls.append(("delete", uid)))
    relays = SimpleNamespace(retire=lambda uid: calls.append(("relay", uid)))
    daemons = OwnerDaemons(runtime, None, volumes, relays, tmp_path / "run",
                           authority_holding=tmp_path / "state" / "authority-import")
    _held, mine = daemons._held_authority(Box("alice", 300001))
    _held, other = daemons._held_authority(Box("bob", 300002))
    mine.write_text("{}")
    other.write_text("{}")
    (mine.parent / f".{mine.name}.stage").write_text("{}")
    daemons.destroy(300001)
    assert ("delete", 300001) in calls
    assert not mine.exists() and not (mine.parent / f".{mine.name}.stage").exists()
    assert other.exists()


def test_the_holding_area_is_keyed_by_uid_and_owner(tmp_path):
    from tinyassets.boxes.owner_daemons import Box, OwnerDaemons

    daemons = OwnerDaemons(None, None, None, None, tmp_path / "run",
                           authority_holding=tmp_path / "state" / "authority-import")
    _held, first = daemons._held_authority(Box("alice", 300001))
    _held, second = daemons._held_authority(Box("bob", 300001))
    assert first != second and first.parent == tmp_path / "state" / "authority-import"
    assert (first.parent.stat().st_mode & 0o777) == 0o700


def test_production_holds_lifted_authority_in_persistent_host_state():
    source = Path("tinyassets/boxes/host_service.py").read_text(encoding="utf-8")
    assert 'authority_holding=state / "authority-import"' in source
    assert 'state = Path("/var/lib/boxhostd/state")' in source


def test_a_wake_of_a_settled_owner_never_reads_the_files(fleet, tmp_path, monkeypatch):
    from tinyassets.boxes import authority_import as lift

    mount, _seen = _mounted(fleet, tmp_path, monkeypatch)
    policy, _ = _old_files(mount)
    _fleet_store(fleet, tmp_path, pending=False)
    monkeypatch.setattr(lift, "extract", lambda *_: pytest.fail("settled owner read"))
    fleet.wake("a")
    assert policy.exists()


def _box_channel(broker, monkeypatch, tmp_path, store):
    from tinyassets.broker import box_admission
    from tinyassets.broker.server import BOX_HOST

    broker.server._policy_store = store
    broker.server._roles = {os.getuid(): BOX_HOST}
    monkeypatch.setattr(box_admission, "resolve_owner", lambda identities, request, **_: {
        "op": "BOX_ADMITTED", "uid": 300001, "quota": 1024**3})
    sock = _connect(broker)
    sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CHANNEL", "principal": "alice"}))
    assert rf.read_frame_blocking(sock).control()["op"] == "BOX_ADMITTED"
    return sock


@PEERCRED
@pytest.mark.parametrize("action", ["write", "issue_once", "consent_grant"])
def test_the_box_channel_refuses_every_widening_even_with_proof(
        broker, monkeypatch, tmp_path, action):
    _session(tmp_path, "alice", COOKIE)
    store = PolicyStore(tmp_path, broker.server._owner_identities)
    store.bootstrap()
    before = store.read("alice")
    proof = {"session_hash": __import__("hashlib").sha256(COOKIE.encode()).hexdigest()}
    with _box_channel(broker, monkeypatch, tmp_path, store) as sock:
        sock.sendall(rf.control(rf.CONNECTION, {
            "op": "OWNER_POLICY", "action": action, "session_proof": proof,
            "expected_generation": before.generation, "policy": before.document,
            "sink": CALL, "destination": "items-api"}))
        assert rf.read_frame_blocking(sock) is None
    assert store.read("alice").generation == before.generation
    assert store.consents("alice") == []


@PEERCRED
def test_a_box_policy_operation_before_the_import_is_refused_retryably(
        broker, monkeypatch, tmp_path):
    store = _legacy_v1(tmp_path, broker.server._owner_identities)
    with _box_channel(broker, monkeypatch, tmp_path, store) as sock:
        sock.sendall(rf.control(rf.CONNECTION, {
            "op": "OWNER_POLICY", "action": "read", "principal": "alice"}))
        answer = rf.read_frame_blocking(sock).control()
    assert answer == {"op": "OWNER_POLICY_REFUSED",
                      "reason": "owner authority import pending; retry shortly"}
    assert store.import_pending("alice")


@PEERCRED
def test_a_box_open_with_an_inference_flag_still_meets_the_rules(
        broker, monkeypatch, tmp_path):
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
    store.grant_consent("alice", COOKIE, CALL, "items-api")
    _rule(SimpleNamespace(store=store), "app.write", "ask_first")
    broker.server._ledger_for = lambda owner: ledger
    with _box_channel(broker, monkeypatch, tmp_path, store) as sock:
        sock.sendall(rf.control(1, {
            "op": "OPEN", "op_id": new_op_id(), "principal": "alice", "grant_id": "grant-a",
            "connection_id": "conn-a", "verb": "POST", "credit": 8192,
            "request": {"url": URL, "body": "{}"}, "inference_usage": {"reference": "x"},
            "authority": {"agent": "main"}, "generation": -1, "token": "box"}))
        end = rf.read_frame_blocking(sock).control()
    assert (end["op"], end["error_class"], end["stream_sent"]) == ("END", "policy_ask", False)
    assert broker.sent == []
    assert any(row["kind"] == "gate" for row in store.receipts("alice"))


@PEERCRED
def test_a_consent_revoke_cancels_the_owners_live_streams(broker, tmp_path):
    """Review finding 10a: like a policy write, a revoke ends calls in flight."""
    _session(tmp_path, "alice", COOKIE)
    store = PolicyStore(tmp_path, broker.server._owner_identities)
    store.bootstrap()
    store.grant_consent("alice", COOKIE, CALL, "items-api")
    broker.server._policy_store = store
    gate = threading.Event()
    script = Script([b"x"], gate=gate)
    broker.upstreams["next"] = lambda: script
    with _connect(broker) as sock:
        sock.sendall(rf.control(1, {
            "op": "OPEN", "op_id": new_op_id(), "principal": "alice",
            "grant_id": "grant-a", "connection_id": "conn-a",
            "verb": "POST", "request": {"url": "u", "body": {}}, "credit": 10,
            **broker.state}))
        assert rf.read_frame_blocking(sock).control()["op"] == "ADMITTED"
        assert rf.read_frame_blocking(sock).control()["op"] == "HEAD"
        with _connect(broker) as other:
            other.sendall(rf.control(rf.CONNECTION, {
                "op": "OWNER_POLICY", "action": "consent_revoke", "principal": "alice",
                "sink": CALL, "destination": "items-api"}))
            assert rf.read_frame_blocking(other).control()["result"] == {"revoked": True}
        end = rf.read_frame_blocking(sock).control()
    assert end["op"] == "END" and end["outcome"] == "cancelled" and script.closed.is_set()


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


def test_connecting_a_model_source_consents_its_calls(monkeypatch):
    """Review finding 1: with no exemption, the router's connect is the consent."""
    from tinyassets import onboarding
    from tinyassets.broker import policy_submit
    from tinyassets.onboarding import owner_sessions

    calls = []
    monkeypatch.setattr(policy_submit, "submit", lambda *a, **k: calls.append((a, k)) or {})
    monkeypatch.setattr(owner_sessions, "require",
                        lambda request, owner: {"session_hash": "h" * 64})
    result = {"destination": "model:openrouter"}
    onboarding.consent_model_source(object(), "alice", result)
    assert result["consented"] is True
    (_root, owner, action), fields = calls[0]
    assert (owner, action) == ("alice", "consent_grant")
    assert fields == {"session_proof": {"session_hash": "h" * 64}, "sink": CALL,
                      "destination": "model:openrouter"}

    def no_session(request, owner):
        raise PermissionError("no session")

    monkeypatch.setattr(owner_sessions, "require", no_session)
    result = {"destination": "model:openrouter"}
    onboarding.consent_model_source(object(), "alice", result)
    assert result["consent_required"] == {"sink": CALL, "destination": "model:openrouter"}


def test_the_router_spells_a_workspace_cards_consents_itself(monkeypatch):
    """Review finding 4: the page sends the card's fields, never a box-named list."""
    import asyncio

    from tinyassets import onboarding

    granted = []

    async def submit(owner, action, **fields):
        granted.append(fields["destination"])
        return {"destination": fields["destination"]}, None

    monkeypatch.setattr(onboarding, "_owner_authority_submit", submit)
    response = asyncio.run(onboarding._grant_workspace_consents("alice", {"p": 1}, {
        "action": "grant_workspace", "connection_id": "git-exact", "repo": "owner/repo",
        "host": "git.test", "consents": ["workspace_checkout", "workspace_push"]}))
    assert response.status_code == 200
    assert granted == ["checkout:git-exact:git.test/owner/repo",
                       "push:git-exact:git.test/owner/repo"]
    for bad in ({"consents": ["anything"]}, {"repo": "owner/repo/extra"},
                {"sink": "workspace", "destination": "push:x:y/z"}):
        response = asyncio.run(onboarding._grant_workspace_consents("alice", {}, {
            "action": "grant_workspace", "connection_id": "git-exact", "repo": "owner/repo",
            "host": "git.test", "consents": ["workspace_push"], **bad}))
        assert response.status_code == 400


def _js_function(source, name):
    start = source.index(f"function {name}(")
    depth, at = 0, source.index("{", start)
    for index in range(at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(name)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_page_grants_only_what_the_card_itself_names():
    source = Path("tinyassets/onboarding/app/main.js").read_text(encoding="utf-8")
    assert "r.consents" not in source  # a box answer never names what is granted
    script = _js_function(source, "cardConsents") + """
const out=[cardConsents({action:{type:'grant_patch_intake',receiver_id:'r-1'}}),
  cardConsents({action:{type:'grant_workspace_consent',connection_id:'git-exact',
    repo:'owner/repo',host:'git.test',consents:['workspace_push']}}),
  cardConsents({action:{type:'grant_workspace_consent',consents:['workspace_push']}}),
  cardConsents({action:{type:'connect'}})];
console.log(JSON.stringify(out));"""
    out = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True,
                                    text=True).stdout)
    assert out[0]["grants"] == [{"action": "grant", "sink": "patch_intake",
                                 "destination": "r-1"}]
    assert out[1]["grants"] == [{"action": "grant_workspace", "connection_id": "git-exact",
                                 "repo": "owner/repo", "host": "git.test",
                                 "consents": ["workspace_push"]}]
    assert "owner/repo" in out[1]["label"] and "git.test" in out[1]["label"]
    assert out[2:] == [None, None]


def test_the_page_surfaces_consent_and_import_notices_and_no_task_grant():
    page = Path("tinyassets/onboarding/app.html").read_text(encoding="utf-8")
    source = Path("tinyassets/onboarding/app/main.js").read_text(encoding="utf-8")
    # Task-scoped grants never match at the broker, so the card does not offer one.
    assert "Allow for this task" not in source
    # A source connected without a session gets an explicit router consent action.
    assert "Allow live calls for this source" in page
    assert source.count("if(result.consent_required) offerSourceConsent(") == 2
    assert 'postConsent("grant",required.sink,required.destination)' in source
    # An import the broker could not trust is shown to the owner until they save.
    assert 'id="rules-import-notice"' in page
    assert "result.import_alarm===true" in source and "checkRulesImport();" in source


@pytest.mark.parametrize("fields", [
    {"body": "{\"text\":\"hi\"}"},
    {"body": {"text": "hi", "n": 1}},
    {"body": "x", "header_name": " X-Api-Key "},
    {"headers": {"Accept": "application/json"}, "body": "y"},
    {},
])
def test_the_router_once_digest_matches_the_effectors_open(
        tmp_path, monkeypatch, _signed_in_operator, fields):
    """Review finding 8: the card's once-approval and the call's OPEN cannot drift."""
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
    packet = {"sink": CALL, "connection_id": "conn-http", "grant_id": "grant-http",
              "verb": "POST", "request": {"method": "POST", "path": "/v1/messages", **fields}}
    with identity_context(replace(_signed_in_operator, user_id="user-1")):
        data_root, universe_dir, db_path = _setup(tmp_path)
        aec.run_authenticated_external_call_effector(
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
