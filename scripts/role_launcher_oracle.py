"""Production-image launcher/broker substep, not engine-class acceptance.

Synthetic data, no network or host mount. The oracle supplies a trusted daemon
fixture as the launcher's child; it does NOT claim the image CMD launches the
real daemon or that accounting/refresh and engine routing are complete.
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import runpy
import signal
import socket
import struct
import tempfile
import time
import traceback
from pathlib import Path


def _request(path, document, *, descriptor=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(35)
        connection.connect(str(path))
        payload = document if isinstance(document, bytes) else json.dumps(document).encode()
        if descriptor is None:
            connection.sendall(payload)
        else:
            connection.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                           array.array("i", [descriptor]))])
        return json.loads(connection.recv(4096))


def _fence(path, proof, **extra):
    from tinyassets import rpc_frames as rf

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        _pid, uid, gid = struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        assert (uid, gid) == (1002, 1002)
        connection.sendall(rf.control(rf.CONNECTION, {"op": "FENCE", "proof": proof, **extra}))
        return rf.read_frame_blocking(connection).control()


def _seed_ledger(root):
    """Synthetic setup before role retirement; never a daemon ledger open."""
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(root / ".broker/outbound.db", data_root=root)
    for owner in ("alice", "bob", "catalog"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
            provider="http", destination=f"compute:{owner}", credential_ref="vault://http/fixture",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                                "methods": ["POST"]}],
        )
        ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                                owner_user_id=owner, universe_id=owner)
        ledger.configure_capability(
            connection_id=f"conn-{owner}", capability_kind="model_use", enabled=True,
            descriptor={"wire": "chat_messages", "models": [
                {"id": f"{owner}-fixture", "tools": True, "context": 20000}], "billing": "free"},
        )
    for number in range(70):
        ledger.grant_connection(grant_id=f"page-{number:03}", connection_id="conn-catalog",
                                owner_user_id="catalog", universe_id="catalog")
    with ledger._connect() as conn:
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-bob", "model_discovery", "malformed pricing must still block"))
    from tinyassets.onboarding.hosted_model_auth import load_preset
    from tinyassets.onboarding.model_bootstrap import endpoint_policy

    preset = load_preset("openrouter_user_models_v1")
    ledger.create_connection(
        connection_id="conn-bootstrap", owner_user_id="bootstrap", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="model:" + preset.id, credential_ref="vault://http/fixture",
        allowed_endpoints=endpoint_policy(preset))
    ledger.grant_connection(grant_id="grant-bootstrap", connection_id="conn-bootstrap",
                            owner_user_id="bootstrap", universe_id="bootstrap")
    ledger.configure_capability(
        connection_id="conn-bootstrap", capability_kind="model_discovery", enabled=True,
        expected_grant=ledger.get_grant("grant-bootstrap"),
        descriptor={"protocol": preset.id, "catalogue_url": preset.catalogue_url,
                    "benchmark_url": preset.benchmark_url})
    from tinyassets.api.http_connection import _ids

    for destination in ("remove-first", "remove-restart"):
        connection, grant = _ids(universe_id="disconnect", destination=destination)
        ledger.create_connection(
            connection_id=connection, owner_user_id="disconnect", connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET",), provider="http",
            destination=destination, credential_ref="vault://http/" + destination,
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                                "methods": ["GET"]}])
        ledger.grant_connection(grant_id=grant, connection_id=connection,
                                owner_user_id="disconnect", universe_id="disconnect")
    from tinyassets.storage.outbound_connections import ActionCap

    ledger.create_connection(
        connection_id="cloud-destination", owner_user_id="cloud",
        connection_class="pull-request-writer",
        scopes=("pull_requests:write", "pull_requests:read_for_commit"), provider="github",
        destination="github.com/example/project", credential_ref="vault://github/fixture")
    ledger.grant_connection(grant_id="cloud-grant", connection_id="cloud-destination",
                            owner_user_id="cloud", universe_id="cloud",
                            unprompted_action_cap=ActionCap("one", 1, "pull_requests"))
    for path in (root / ".broker").glob("outbound.db*"):
        os.chown(path, 1002, 1101)
        path.chmod(0o600)


def _query_consumers(root, supervisor):
    from tinyassets import rpc_frames as rf
    from tinyassets.api.connection_uses import model_use_refusal
    from tinyassets.broker.client import BrokerClient, BrokerRefused
    from tinyassets.broker.ledger_queries import (
        DISCOVERY_FACTS,
        GRANTED_RESOURCE,
        HAS_PRICED_SOURCE,
        query_ledger,
    )
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.discovery_http import (
        ModelDiscoveryUnavailable,
        read_granted_discovery_document,
    )
    from tinyassets.providers.discovery_snapshot import _context
    from tinyassets.storage.outbound_connections import GrantResolutionError

    os.environ["TINYASSETS_DATA_DIR"] = str(root)
    os.environ["TINYASSETS_CREDENTIAL_BROKER"] = "process"
    _injected_consumers(root)
    definition = register_definition(
        universe_id="alice", owner_user_id="alice", access_method="api_key_http",
        protocol="chat_messages", model="alice-fixture", ref="grant-alice",
    )
    context = _context(root, "alice", "alice", definition.id)
    assert context.profile.descriptor()["models"][0]["id"] == "alice-fixture"
    assert len(context.digest) == 64
    assert model_use_refusal(base=root, uid="alice", actor="alice", connection_id="conn-alice",
                             grant_id="grant-alice") is None
    from tinyassets.storage.outbound_connections import MODEL_USE_PRICED_CONFLICT

    assert model_use_refusal(base=root, uid="bob", actor="bob", connection_id="conn-bob",
                             grant_id="grant-bob")["detail"] == MODEL_USE_PRICED_CONFLICT
    arguments = dict(query=DISCOVERY_FACTS, principal="alice", command_center="alice",
                     grant_id="grant-alice", connection_id="conn-alice")
    facts = query_ledger(root, **arguments)
    assert facts["resource"]["owner_user_id"] == "alice"
    assert "bob-fixture" not in json.dumps(facts)
    from tinyassets.api.compute_connection import _validate_http_grant
    from tinyassets.api.model_access_requests import _connection_incarnations
    from tinyassets.providers.source_display import source_display_name

    provider = f"api_key_http:{definition.id}"
    from types import SimpleNamespace

    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.storage.agent_request_usage import UsageStore

    accounting = UsageStore(root)
    source_args = dict(scope=("alice", "alice", "fixture-usage"),
                       attempt=SimpleNamespace(source_ref=provider, model="alice-fixture"),
                       grant_id="grant-alice", connection_id="conn-alice", verb="POST",
                       request={"body": {"model": "alice-fixture"}})
    accounting._validate_source(**source_args)
    try:
        accounting._validate_source(**(source_args | {"connection_id": "conn-bob"}))
    except ProviderAuthorityHeldError:
        pass
    else:
        raise AssertionError("accounting admitted a foreign source connection")
    assert not (root / "outbound.db").exists()
    print("D43 actual accounting source binding via launcher broker: installed definition/model, "
          "foreign connection refusal, no daemon ledger: PASS (usage runtime IPC pending)",
          flush=True)
    _accounting_runtime(root, provider)
    assert _validate_http_grant(base=root, universe_id="alice", actor="alice",
                                grant_id="grant-alice") is None
    assert _validate_http_grant(base=root, universe_id="alice", actor="alice",
                                grant_id="grant-bob") == {
        "error": "not_found", "resource": "connection"}
    assert _connection_incarnations(root, "alice", "alice", [provider]) == {
        provider: facts["resource"]["incarnation"]}
    try:
        _connection_incarnations(root, "bob", "alice", [provider])
    except PermissionError:
        pass
    else:
        raise AssertionError("foreign principal captured an incarnation")
    assert source_display_name(base=root, universe_id="alice", provider=provider) == "compute:alice"
    foreign = register_definition(
        universe_id="alice", owner_user_id="alice", access_method="api_key_http",
        protocol="chat_messages", model="foreign-fixture", ref="grant-bob",
    )
    foreign_provider = f"api_key_http:{foreign.id}"
    try:
        _connection_incarnations(root, "alice", "alice", [foreign_provider])
    except PermissionError:
        pass
    else:
        raise AssertionError("owned definition captured a foreign grant incarnation")
    assert source_display_name(base=root, universe_id="alice", provider=foreign_provider) == ""
    print("D23 actual compute-grant/incarnation/display consumers via launcher broker: "
          "scoped reads, foreign refusal, no daemon ledger: PASS", flush=True)
    from tinyassets import bound_requests
    from tinyassets.effectors import authenticated_external_call as effector
    from tinyassets.storage.effector_consents import grant_consent

    scope = dict(db_path=root / "outbound.db", grant_id="grant-alice",
                 connection_id="conn-alice", universe_id="alice", principal="alice")
    grant, view, error = effector._read_connection_context(**scope)
    assert not error and grant.owner_user_id == view.owner_user_id == "alice"
    grant_consent(root / "alice", sink="authenticated_external_call",
                  destination=view.destination, granted_by="alice")
    packet = {"connection_id": "conn-alice", "grant_id": "grant-alice", "verb": "POST",
              "request": {"path": "/v1/chat"}}
    authority = bound_requests._authority(root / "alice", packet, "alice", "main")
    assert authority["connection_revision"] == bound_requests.digest([
        view.as_dict(), facts["resource"]["incarnation"]])
    for change in ({"principal": "bob"}, {"universe_id": "bob"},
                   {"grant_id": "grant-bob"}, {"connection_id": "conn-bob"}):
        assert effector._read_connection_context(**(scope | change)) == (
            None, None, "connection_authority_unavailable")
    try:
        bound_requests._authority(root / "alice", packet, "bob", "main")
    except bound_requests.RequestRefused:
        pass
    else:
        raise AssertionError("bound preview accepted foreign authority")
    assert not (root / "outbound.db").exists()
    print("D24 actual effector/bound-preview consumers via launcher broker: "
          "scoped snapshot, foreign refusal, no daemon ledger: PASS", flush=True)
    from tinyassets.credential_vault import _connection_grant_record_digest
    from tinyassets.provider_serving_binding import (
        ServingProviderNotOwned,
        _open_connection_id,
        _open_serving_context,
        verify_open_grant_custody,
    )

    assert _open_serving_context(root, "alice", "alice", definition.id) == (
        provider, "grant-alice", "conn-alice", "vault://http/fixture")
    assert _open_connection_id(root, "alice", provider, owner_user_id="alice") == "conn-alice"
    from types import SimpleNamespace

    custody = SimpleNamespace(_record_digest=_connection_grant_record_digest(
        grant_id="grant-alice", connection_id="conn-alice", credential_ref="vault://http/fixture",
        owner_user_id="alice", universe_id="alice"))
    assert verify_open_grant_custody(root, "alice", "alice", provider, custody) == "conn-alice"
    for owner, did in (("bob", definition.id), ("", definition.id), ("alice", foreign.id)):
        try:
            _open_connection_id(root, "alice", f"api_key_http:{did}", owner_user_id=owner)
        except ServingProviderNotOwned:
            pass
        else:
            raise AssertionError("serving lookup accepted foreign or missing owner authority")
    assert not (root / "outbound.db").exists()
    print("D25 actual serving context/id/custody via launcher broker: "
          "scoped reads, foreign refusal, no daemon ledger: PASS", flush=True)
    from tinyassets.exceptions import ProviderUnavailableError
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.providers.base import ModelConfig

    compute = ApiKeyHttpProvider(definition)
    config = ModelConfig(invocation_owner_user_id="alice")
    connection, owner, view = compute._connection_context(root / "alice", config)
    assert (connection, owner, view.owner_user_id) == ("conn-alice", "alice", "alice")
    proxy = compute._resolve_proxy(
        db_path=root / "outbound.db", universe_id="alice", grant_id="grant-alice",
        connection_id=connection, owner_user_id=owner)
    assert proxy.grant_id == "grant-alice" and proxy.access_mode == view.access_mode
    proxy.close()
    for did, who, center in ((definition, "", "alice"), (definition, "bob", "alice"),
                             (definition, "alice", "bob"), (foreign, "alice", "alice")):
        try:
            ApiKeyHttpProvider(did)._connection_context(
                root / center, ModelConfig(invocation_owner_user_id=who))
        except ProviderUnavailableError:
            pass
        else:
            raise AssertionError("compute read accepted missing or foreign admitted authority")
    assert not (root / "outbound.db").exists()
    print("D26 actual HTTP compute source/proxy consumers via launcher broker: "
          "scoped reads/acquisition, foreign refusal, no daemon ledger: PASS "
          "(inference accounting/POST not claimed)", flush=True)
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.request_budget import _source_budget_facts

    budget_context = SimpleNamespace(universe_dir=root / "alice", model_selection=SimpleNamespace(
        connection_id=provider, model_id="alice-fixture"))
    assert _source_budget_facts(budget_context, owner="alice") == ("alice", None)
    for who, center, source in (("bob", "alice", provider), (None, "alice", provider),
                                ("alice", "bob", provider), ("alice", "alice", foreign_provider)):
        bad = SimpleNamespace(universe_dir=root / center, model_selection=SimpleNamespace(
            connection_id=source, model_id="alice-fixture"))
        try:
            _source_budget_facts(bad, owner=who)
        except ProviderAuthorityHeldError:
            pass
        else:
            raise AssertionError("source budget accepted missing or foreign authority")
    assert not (root / "outbound.db").exists()
    print("D30 source-budget facts via launcher broker: scoped read and missing/foreign "
          "authority refusal, no daemon ledger: PASS", flush=True)
    _capability_consumers(root, supervisor)
    _catalog_consumers(root, supervisor)
    _bootstrap_consumers(root)
    _disconnect_consumer(root)
    for changes in ({"principal": "bob"}, {"command_center": "bob"},
                    {"grant_id": "grant-bob"}, {"connection_id": "conn-bob"}):
        try:
            query_ledger(root, **(arguments | changes))
        except GrantResolutionError:
            pass
        else:
            raise AssertionError("broker returned foreign discovery facts")
    assert query_ledger(root, **(arguments | {"query": HAS_PRICED_SOURCE})) == {"priced": False}
    # A profile-independent query still checks scope and the live fence. Bob's
    # malformed model profile cannot break his otherwise legitimate HTTP grant.
    for owner in ("alice", "bob"):
        resource = query_ledger(root, query=GRANTED_RESOURCE, principal=owner,
                                command_center=owner, grant_id=f"grant-{owner}")
        assert resource["resource"]["owner_user_id"] == owner
        assert set(resource) == {"resource"}
    for owner, grant, reason in (("alice", "grant-alice", "missing_discovery_scope"),
                                  ("alice", "grant-bob", "source_revoked")):
        try:
            read_granted_discovery_document(
                db_path=root / "outbound.db", grant_id=grant, owner_user_id=owner,
                universe_id=owner, url="https://models.example.com/ungranted",
            )
        except ModelDiscoveryUnavailable as exc:
            assert exc.reason == reason
        else:
            raise AssertionError("discovery HTTP admitted foreign scope/ungranted URL")
    print("D20 actual discovery HTTP via launcher broker: scoped resource/endpoint "
          "refusals, malformed-profile independence, no daemon ledger: PASS "
          "(successful HTTP stream not claimed)", flush=True)
    bad = BrokerClient(supervisor.socket_path, principal="alice", command_center="alice",
                       fence=lambda: (1, "wrong"), verify_peer=supervisor.verify_broker, timeout=5)
    try:
        bad.ledger_query(query=DISCOVERY_FACTS, grant_id="grant-alice")
    except BrokerRefused:
        pass
    else:
        raise AssertionError("broker admitted unfenced ledger query")
    generation, token = supervisor.fence()
    for extra in ({"query": "_connect"}, {"sql": "SELECT * FROM outbound_connections"},
                  {"path": "/data/outbound.db"}):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(supervisor.socket_path))
            supervisor.verify_broker(connection)
            connection.sendall(rf.control(rf.CONNECTION, {
                "op": "LEDGER_QUERY", "generation": generation, "token": token,
                **arguments, **extra,
            }))
            assert rf.read_frame_blocking(connection).control()["op"] == "LEDGER_REFUSED"
    assert not (root / "outbound.db").exists(), "daemon constructed a fallback ledger"
    print("D11 actual discovery/priced-source consumers via launcher broker; "
          "foreign scope, fence, SQL/path/method refusal; no local ledger: PASS", flush=True)


def _injected_consumers(root):
    from dataclasses import replace
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from tinyassets.broker.connection_authority import BrokerConnectionAuthority
    from tinyassets.effectors.outbound_boundary import execute_capped_action
    from tinyassets.provider_work_authority import ProviderWorkBindingSeed
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore
    from tinyassets.user_owned_cloud_automation import (
        AutomationAdmissionError,
        RepositorySpecWorkDefinition,
        resolve_inactive_cloud_authority,
    )

    store = SQLiteProviderWorkAuthorityStore(
        root, clock=lambda: datetime(2026, 7, 31, tzinfo=timezone.utc), allow_test_fixtures=True)
    binding = store.install_test_binding(ProviderWorkBindingSeed(
        owner_user_id="cloud", universe_id="cloud", provider="codex",
        credential_reference_digest="sha256:" + "9" * 64,
        allowed_operations=("repository_spec_delivery",), allowed_roles=("writer",),
        assignment_generation=2, assignment_digest="sha256:" + "8" * 64,
        max_invocations=4, max_tokens=100000, max_cost_microunits=5000000,
        expires_at="2026-08-02T00:00:00Z")).record
    definition = RepositorySpecWorkDefinition.from_dict(dict(
        schema_version=1, principal_id="cloud", universe_id="cloud", repository="example/project",
        accepted_spec_ref="openspec/specs/example/spec.md",
        accepted_spec_digest="sha256:" + "a" * 64,
        branch_def_id="branch", branch_version_id="branch@abc12345",
        branch_content_digest="sha256:" + "b" * 64,
        acceptance_scenario_id="scenario:fixture", acceptance_scenario_digest="sha256:" + "c" * 64,
        input_artifact_digests=["sha256:" + "a" * 64], provider_binding_id=binding.binding_id,
        destination_grant_id="cloud-grant", destination_purpose="pull_request", max_attempts=2,
        max_provider_invocations=4, max_wall_time_seconds=3600, max_tokens=100000,
        max_cost_microunits=5000000))
    authority = BrokerConnectionAuthority(root, "cloud", lambda: "cloud")
    resolved = resolve_inactive_cloud_authority(
        definition, provider_store=store, connection_ledger=authority)
    assert resolved.destination_grant_id == "cloud-grant"
    for foreign in (replace(authority, command_center="bob"),
                    replace(authority, verify_authenticated_principal=lambda: "bob")):
        try:
            resolve_inactive_cloud_authority(definition, provider_store=store,
                                              connection_ledger=foreign)
        except AutomationAdmissionError:
            pass
        else:
            raise AssertionError("cloud definition widened injected authority")
    # Above-cap effect is actually journaled held; no external PR is attempted.
    (root / "cloud").mkdir(exist_ok=True)
    result = execute_capped_action(
        universe_dir=root / "cloud", ledger=authority, grant_id="cloud-grant",
        proxy=SimpleNamespace(grant_id="cloud-grant"), tool_authorized=True,
        action_value=2, action_unit="pull_requests", effect_key="cloud-held", sink="pull_request",
        run_id="cloud-run", verb="POST", request={"title": "synthetic fixture"})
    assert result["status"] == "held" and not (root / "outbound.db").exists()
    print("D41 actual cloud authority and capped-effect hold via launcher broker: scoped snapshot, "
          "foreign refusal, no daemon ledger: PASS", flush=True)


def _disconnect_consumer(root):
    from tinyassets.api.http_connection import preview_rotate_http, remove_http, rotate_http
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.disconnect import disconnect
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.storage.outbound_connections import GrantResolutionError

    (root / "disconnect").mkdir(exist_ok=True)
    grant_universe_access(root, universe_id="disconnect", actor_id="disconnect", permission="admin")
    scope = dict(principal="disconnect", command_center="disconnect")
    for destination in ("remove-first", "remove-restart"):
        snapshot = disconnect(root, **scope, destination=destination)
        if snapshot["resource"] is not None:
            break
    else:
        raise AssertionError("no seeded disconnect remains")
    for changes in ({"principal": "bob"}, {"action": "fence", "incarnation": "stale"},
                    {"action": "erase", "incarnation": snapshot["incarnation"]}):
        try:
            disconnect(root, **(scope | changes), destination=destination)
        except GrantResolutionError:
            pass
        else:
            raise AssertionError("disconnect accepted foreign/stale/unfenced mutation")
    with identity_context(Identity(user_id="disconnect", username="disconnect",
                                   capabilities=["write"])):
        from tinyassets.api.http_connection import connect_http

        fresh = "connect-" + destination
        deposit = dict(destination=fresh, secret="synthetic-deposit-only", auth_scheme="bearer",
                       scopes=["git_read:owner/repo", "git_write:owner/repo"],
                       allowed_endpoints=[{"host": "models.example.com",
                                           "path_template": "/catalogue", "methods": ["GET"]}])
        for _ in range(2):
            result = connect_http(universe_id="disconnect", payload=deposit)
            assert result["status"] == "provisioned", result
            assert "synthetic-deposit-only" not in str(result)
        deposit["allowed_endpoints"].append(
            {"host": "models.example.com", "path_template": "/extra", "methods": ["GET"]})
        result = connect_http(universe_id="disconnect", payload=deposit)
        assert result["status"] == "provisioned" and len(result["allowed_endpoints"]) == 2
        from tinyassets.api.pending_requests import answer_request

        consent = request_from_user(universe_id="disconnect", payload={
            "kind": "Approval", "title": "Checkout", "body": "Fixture checkout", "fields": [],
            "action": {"type": "grant_workspace_consent",
                       "connection_id": result["connection_id"], "repo": "owner/repo",
                       "consents": ["workspace_checkout", "workspace_push"]}})
        assert consent.get("status") == "pending", consent
        granted = answer_request(universe_id="disconnect", payload={
            "request_id": consent["request_id"], "values": {}})
        assert granted.get("status") == "answered", granted
        assert "models.example.com/owner/repo" in granted["destinations"][0]
        print("D38 actual workspace consent capture/answer via launcher broker: owner metadata "
              "and daemon consent write: PASS", flush=True)
        from types import SimpleNamespace

        from tinyassets.effectors import EffectChain, EffectFailedError
        from tinyassets.effectors.workspace import _connection_for_mount, _Refused
        from tinyassets.graph_compiler import BranchExecutionContext, _wrap_with_effects

        packet = dict(sink="workspace", op="checkout", repo="owner/repo",
                      connection_id=result["connection_id"], grant_id=result["grant_id"],
                      owner_user_id="disconnect")
        node = SimpleNamespace(node_id="checkout", effects=["workspace"], output_keys=["packet"],
                               input_keys=[], timeout_seconds=0)
        for owner in ("disconnect", "bob"):
            chain = EffectChain(base_path=root / "disconnect", run_id="probe", dry_run=True)
            wrapped = _wrap_with_effects(
                lambda state: {"packet": packet}, node, chain, [], None,
                execution_context=BranchExecutionContext(
                    owner_user_id=owner, universe_id="disconnect"))
            try:
                wrapped({})
            except EffectFailedError as exc:
                assert owner == "bob" and exc.error_kind == "connection_authority_unavailable"
            else:
                assert owner == "disconnect" and chain.evidence["checkout"]["workspace"]["dry_run"]
        mount = SimpleNamespace(connection_id=result["connection_id"], grant_id=result["grant_id"])
        resource = _connection_for_mount(root / "disconnect", mount, fallback=None,
                                         principal="disconnect")
        assert resource.connection_id == result["connection_id"]
        try:
            _connection_for_mount(root / "disconnect", mount, fallback=None, principal="bob")
        except _Refused:
            pass
        else:
            raise AssertionError("foreign workspace mount revalidation admitted")
        print("D39 actual compiler/workspace admission and mount revalidation via launcher broker: "
              "trusted owner, foreign refusal, no daemon ledger: PASS", flush=True)
        from tinyassets import runs, workspace_intents

        runs.initialize_runs_db(root)
        with runs._connect(root) as conn:
            conn.execute("INSERT OR REPLACE INTO runs "
                         "(run_id,branch_def_id,thread_id,actor,owner_user_id,queue_universe_id,"
                         "started_at) VALUES ('intent-probe','b','t','universe:disconnect',"
                         "'disconnect','disconnect',0)")
        intent = workspace_intents.PushIntent(
            intent_id="probe", run_id="intent-probe", node_id="push",
            connection_id=result["connection_id"], grant_id=result["grant_id"],
            universe_id="disconnect", repo="owner/repo", host="models.example.com",
            remote_ref="refs/heads/tiny/disconnect/work", sha="a" * 40, state="sent")
        assert workspace_intents._broker_credential_ref(root / "disconnect", intent) == (
            "vault://http/" + fresh)
        with runs._connect(root) as conn:
            conn.execute("UPDATE runs SET owner_user_id='bob' WHERE run_id='intent-probe'")
        try:
            workspace_intents._broker_credential_ref(root / "disconnect", intent)
        except GrantResolutionError:
            pass
        else:
            raise AssertionError("intent accepted foreign persisted owner")
        print("D40 actual intent custody resolver via launcher broker: persisted run scope, "
              "foreign refusal, no daemon ledger: PASS (worker transport not claimed)", flush=True)
        from tinyassets.credential_vault import load_credential_vault

        assert any(row.get("token") == "synthetic-deposit-only"
                   for row in load_credential_vault(root / "disconnect"))
        assert not (root / "outbound.db").exists()
        try:
            (root / ".broker/outbound.db").open("rb")
        except PermissionError:
            pass
        else:
            raise AssertionError("daemon opened private ledger after connect")
        assert remove_http(universe_id="disconnect", payload={"destination": fresh})[
            "connection_removed"]
        print("D37 actual HTTP connect/redeposit via launcher broker: prepare/commit, "
              "fresh/repeat/additive, daemon-only vault and no daemon ledger: PASS", flush=True)
        preview = preview_rotate_http(universe_id="disconnect",
                                      payload={"destination": destination})
        assert preview["incarnation"] == snapshot["incarnation"]
        rotated = rotate_http(universe_id="disconnect", payload={
            "destination": destination, "incarnation": snapshot["incarnation"],
            "secret": "synthetic-rotation-only"})
        assert rotated["status"] == "rotated", rotated
        assert disconnect(root, **scope, destination=destination) == snapshot
        print("D35 actual HTTP rotation via launcher broker: live snapshot, owner vault write, "
              "ledger policy unchanged: PASS", flush=True)
        from tinyassets.api.http_connection import extend_http
        from tinyassets.broker.http_policy import read_policy, update_policy

        resource, _grant, policy = read_policy(root, **scope, destination=destination)
        assert not update_policy(root, **scope, destination=destination, action="full",
                                 expected=policy | {"incarnation": "stale"},
                                 git_host=resource.git_host)
        endpoint = {"host": "models.example.com", "path_template": "/extra", "methods": ["GET"]}
        extended = extend_http(universe_id="disconnect", payload={
            "destination": destination, "endpoints": [endpoint]})
        assert extended["status"] == "extended" and len(extended["allowed_endpoints"]) == 2
        full = extend_http(universe_id="disconnect", payload={
            "destination": destination, "access": "full"})
        assert full["status"] == "extended" and full["access"] == "full"
        print("D36 actual HTTP endpoint and full-access extension via launcher broker: "
              "scoped mutation and stale CAS refusal, no daemon ledger: PASS", flush=True)
        asked = request_from_user(universe_id="disconnect", payload={
            "kind": "Connection", "title": "Disconnect", "body": "Remove access", "fields": [],
            "action": {"type": "remove_http", "destination": destination}})
        assert asked.get("status") == "pending", asked
        result = remove_http(universe_id="disconnect", payload={"destination": destination})
        assert result["status"] == "removed" and result["connection_removed"] is True
        again = remove_http(universe_id="disconnect", payload={"destination": destination})
        assert again["connection_removed"] is False
    from tinyassets.providers.connection_lifecycle import intentionally_disconnected
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(root).connection() as conn:
        conn.execute("UPDATE connection_disconnections SET model_source=1 "
                     "WHERE owner_user_id='disconnect'")
        conn.commit()
    assert intentionally_disconnected(root, owner="disconnect", uid="disconnect")
    print("D34 actual removal request capture and lifecycle status via launcher broker: PASS",
          flush=True)
    assert disconnect(root, **scope, destination=destination)["resource"] is None
    assert not (root / "outbound.db").exists()
    print("D33 actual HTTP disconnect via launcher broker: fence/erase/repeat, foreign/stale "
          "refusal, no daemon ledger: PASS", flush=True)


def _bootstrap_consumers(root):
    from tinyassets.broker.ledger_queries import BOOTSTRAP_RECOVERY, query_ledger
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.onboarding.hosted_model_auth import load_preset
    from tinyassets.onboarding.model_bootstrap import _pending_confirmation
    from tinyassets.onboarding.model_bootstrap_candidate import prepare_candidate
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.pending_requests import create_request

    universe = root / "bootstrap"
    universe.mkdir(exist_ok=True)
    set_founder_home(root, founder_sub="bootstrap", universe_id="bootstrap",
                     platform_generated=True)
    grant_universe_access(root, universe_id="bootstrap", actor_id="bootstrap", permission="admin")
    preset = load_preset("openrouter_user_models_v1")
    definition = register_definition(universe_id="bootstrap", owner_user_id="bootstrap",
                                     access_method="api_key_http", protocol="chat_messages",
                                     model="fixture", ref="grant-bootstrap")
    assert prepare_candidate(base=root, uid="bootstrap", owner="bootstrap",
                              grant_id="grant-bootstrap", preset=preset) == definition.id
    access = {definition.id: ModelAccess("discovered").document()}
    request = create_request(universe, kind="Models", title="Fixture", body="Fixture", fields=[],
                             action={"type": "bind_model_access", "provider": definition.id,
                                     "model_access": access, "previous_membership": {},
                                     "proposed_membership": access}, dedupe_key="fixture")
    pending = _pending_confirmation(root, universe=universe, uid="bootstrap",
                                    owner="bootstrap", preset=preset)
    assert pending["request_id"] == request["request_id"]
    args = dict(query=BOOTSTRAP_RECOVERY, principal="bootstrap", command_center="bootstrap",
                grant_id="grant-bootstrap")
    facts = query_ledger(root, **args)
    assert set(facts["recovery"]) == {"destination", "descriptor"}
    assert "vault://" not in json.dumps(facts)
    for change in ({"principal": "alice"}, {"command_center": "alice"},
                   {"grant_id": "grant-alice"}, {"connection_id": "conn-alice"}):
        assert query_ledger(root, **(args | change)) == {"recovery": None}
    assert not (root / "outbound.db").exists()
    print("D31 actual bootstrap candidate and pending-confirmation consumers through launcher "
          "broker: scoped metadata, foreign refusal, no daemon ledger: PASS", flush=True)


def _catalog_consumers(root, supervisor):
    import asyncio

    from tinyassets.broker.catalog import connections
    from tinyassets.broker.client import BrokerClient, BrokerRefused
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    rows = list(connections(root, principal="catalog", command_center="catalog"))
    assert len(rows) == 71 and len({g.grant_id for g, _, _ in rows}) == 71
    assert all(g.owner_user_id == v.owner_user_id == "catalog" and incarnation
               for g, v, incarnation in rows)
    assert all(not hasattr(view, "credential_ref") for _, view, _ in rows)
    from tinyassets.api.package_requests import _connections_you_have
    from tinyassets.broker.owner_metadata import view as owner_view

    assert _connections_you_have("catalog", "catalog") == {v.destination for _, v, _ in rows}
    assert owner_view(root, principal="bob", command_center="catalog",
                      connection_id=rows[0][1].connection_id) is None
    print("D38 actual package connection-name preview via launcher broker: owner pages and "
          "foreign metadata absence: PASS", flush=True)
    assert list(connections(root, principal="alice", command_center="catalog")) == []
    assert list(connections(root, principal="catalog", command_center="alice")) == []
    backend = Capabilities(root / "catalog", ExecutionContext(
        universe="catalog", owner="catalog", initiating_agent="main"), [], None, lambda: None)
    result = asyncio.run(backend.dispatch({"op": "catalog"}))
    assert {row["name"] for row in result["capabilities"]} == {
        "connection:conn-catalog:GET", "connection:conn-catalog:POST"}
    assert "credential_ref" not in json.dumps(result) and "vault://" not in json.dumps(result)
    bad = BrokerClient(supervisor.socket_path, principal="catalog", command_center="catalog",
                       fence=lambda: (1, "wrong"), verify_peer=supervisor.verify_broker, timeout=5)
    try:
        bad.connection_catalog(cursor="", limit=64)
    except BrokerRefused:
        pass
    else:
        raise AssertionError("catalog admitted invalid fence")
    assert not (root / "outbound.db").exists()
    print("D28 actual capability catalog via launcher broker: 71 grants over bounded pages, "
          "redaction, foreign/fence refusal, no daemon ledger: PASS", flush=True)
    from tinyassets.api.cloud_connections import cloud_connections
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import grant_universe_access

    (root / "catalog").mkdir(exist_ok=True)
    grant_universe_access(root, universe_id="catalog", actor_id="catalog", permission="admin")
    with identity_context(Identity(user_id="catalog", username="catalog", capabilities=["write"])):
        graph = cloud_connections(action="list", universe_id="catalog")
    assert graph["count"] == 71
    assert all(row["uses"]["model"]["models"][0]["id"] == "catalog-fixture"
               for row in graph["connections"])
    assert "credential_ref" not in json.dumps(graph) and "vault://" not in json.dumps(graph)
    assert not (root / "outbound.db").exists()
    print("D32 actual graph connection inventory via launcher broker: 71 scoped rows with "
          "capability metadata, no daemon ledger: PASS", flush=True)


def _capability_consumers(root, supervisor):
    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.broker.capabilities import capability_operation
    from tinyassets.broker.client import BrokerClient, BrokerRefused
    from tinyassets.broker.ledger_queries import CONNECTION_GRANTS, query_ledger
    from tinyassets.storage.outbound_connections import GrantResolutionError

    scope = dict(principal="alice", command_center="alice", grant_id="grant-alice",
                 connection_id="conn-alice", capability_kind="constant_headers")
    applied = apply_connection_uses(base=root, uid="alice", actor="alice",
                                    grant_id="grant-alice", uses={},
                                    constant_headers={"X-Fixture": "broker"})
    assert applied["constant_headers"] == {"X-Fixture": "broker"}
    assert capability_operation(root, **scope).descriptor() == {
        "headers": {"X-Fixture": "broker"}}
    assert query_ledger(root, query=CONNECTION_GRANTS, principal="alice",
                         command_center="alice", grant_id="lookup",
                         connection_id="conn-alice") == {"grant_ids": ["grant-alice"]}
    for changes in ({"principal": "bob"}, {"command_center": "bob"},
                    {"grant_id": "grant-bob"}, {"connection_id": "conn-bob"}):
        try:
            capability_operation(root, **(scope | changes), action="configure", enabled=True,
                                 descriptor={"headers": {"X-Fixture": "foreign"}})
        except GrantResolutionError:
            pass
        else:
            raise AssertionError("capability mutation admitted foreign scope")
    bad = BrokerClient(supervisor.socket_path, principal="alice", command_center="alice",
                       fence=lambda: (1, "wrong"), verify_peer=supervisor.verify_broker, timeout=5)
    try:
        bad.capability(dict(action="configure", grant_id="grant-alice",
                            connection_id="conn-alice", capability_kind="constant_headers",
                            descriptor={"headers": {"X-Fixture": "unfenced"}},
                            enabled=True, preview=False))
    except BrokerRefused:
        pass
    else:
        raise AssertionError("capability mutation admitted invalid fence")
    assert capability_operation(root, **scope).descriptor() == {
        "headers": {"X-Fixture": "broker"}}
    assert capability_operation(root, **scope, action="configure", enabled=False) is None
    assert capability_operation(root, **scope) is None
    assert not (root / "outbound.db").exists()
    print("D27 actual connection-uses capability mutation via launcher broker: "
          "configure/read/disable, foreign/fence refusal, no daemon ledger: PASS", flush=True)


def _daemon(root, run, ready, control, launcher):
    launcher["retire_child"]("daemon")
    launcher["close_descriptors"]((ready, control))
    assert os.read(ready, 1) == b"1"
    os.close(ready)
    path = run / "launcher.sock"
    from tinyassets.broker import supervisor as supervisor_module

    supervisor_module.LAUNCHER_SOCKET = path
    supervisor_module.BROKER_SOCKET = run / "broker/broker.sock"
    supervisor = supervisor_module.BrokerSupervisor(root)
    proof = supervisor._proof
    start = {"op": "START_BROKER", "proof_sha256": hashlib.sha256(proof.encode()).hexdigest()}
    # Same uid is insufficient. The parent has not reaped this child, so this
    # exercise cannot accidentally test a recycled pid.
    wrong = os.fork()
    if wrong == 0:
        try:
            for name in ("mem", "environ"):
                try:
                    descriptor = os.open(f"/proc/{os.getppid()}/{name}", os.O_RDONLY)
                except PermissionError:
                    pass
                else:
                    os.close(descriptor)
                    raise AssertionError("same-uid child could open daemon private procfs")
            try:
                assert _request(path, start)["op"] == "REFUSED"
            except (ConnectionResetError, BrokenPipeError):
                # The peer gate intentionally closes without reading payload.
                # AF_UNIX may reset a socket that has unread request bytes.
                pass
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
    assert os.waitpid(wrong, 0)[1] == 0
    for bad in (b"{", b"[" * 1100 + b"]" * 1100, b"x" * 5000,
                {**start, "argv": ["/bin/sh"]},
                {**start, "op": "spawn"}, {**start, "proof_sha256": "bad"}):
        assert _request(path, bad)["op"] == "REFUSED"
    with open("/dev/null", "rb") as handle:
        assert _request(path, start, descriptor=handle.fileno())["op"] == "REFUSED"
    answer = _request(path, start)
    assert answer["op"] == "BROKER_READY" and answer["restarts"] == 0
    assert answer["socket"] == str(run / "broker/broker.sock")
    socket_info = Path(answer["socket"]).stat()
    assert (socket_info.st_uid, socket_info.st_gid, socket_info.st_mode & 0o7777) == (
        1002, 1101, 0o660)
    fenced = _fence(answer["socket"], proof)
    assert fenced["op"] == "FENCE_ACK" and fenced["generation"] == 1 and fenced["token"]
    assert _fence(answer["socket"], proof) == fenced
    assert _fence(answer["socket"], "wrong")["op"] == "FENCE_REFUSED"
    assert _fence(answer["socket"], proof, generation=1)["op"] == "FENCE_REFUSED"
    supervisor.start()
    assert supervisor_module.get_supervisor(root) is supervisor
    assert supervisor.fence() == (fenced["generation"], fenced["token"])
    _query_consumers(root, supervisor)
    if os.environ.get("TA_ORACLE_HTTPS") == "1":
        runpy.run_path("/app/scripts/role_stream_oracle.py")["probe"](root)
    # A socket at the daemon uid must never receive a proof, even when its
    # pathname was supplied by trusted startup configuration.
    fake_path = root / "fake-broker.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as fake:
        fake.bind(str(fake_path))
        fake.listen(1)
        fake.settimeout(5)
        actual_path = supervisor._socket
        supervisor._socket = fake_path
        try:
            supervisor._fence()
        except supervisor_module.BrokerUidSplitRequired:
            pass
        else:
            raise AssertionError("daemon accepted a same-uid broker")
        finally:
            supervisor._socket = actual_path
        with fake.accept()[0] as received:
            received.settimeout(5)
            assert received.recv(1) == b"", "daemon disclosed proof before broker authentication"
    fake_path.unlink()
    print("daemon non-dumpable procfs; same-uid fake broker gets no proof: PASS", flush=True)
    from tinyassets import rpc_frames as rf

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(answer["socket"])
        connection.sendall(rf.control(1, {
            "op": "OPEN", "principal": "alice", "command_center": "alice",
            "op_id": "00000000-0000-4000-8000-000000000001",
            "grant_id": "absent", "connection_id": "absent", "verb": "GET",
            "request": {}, "generation": fenced["generation"], "token": fenced["token"],
        }))
        refused = rf.read_frame_blocking(connection).control()
        assert refused["outcome"] == "refused"
        assert refused["error_class"] == "GrantResolutionError"
    for private in ("outbound.db", "state/fence.json", ".outbound-proxy"):
        try:
            (root / ".broker" / private).stat()
        except PermissionError:
            pass
        else:
            raise AssertionError(f"daemon could access {private}")
    print("launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS",
          flush=True)
    print("launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS",
          flush=True)
    assert _request(path, {**start, "proof_sha256": "f" * 64})["op"] == "REFUSED"
    os.write(control, b"K")
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        answer = _request(path, start)
        if answer["op"] == "BROKER_READY" and answer["restarts"] == 1:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("launcher did not restart broker")
    assert _fence(answer["socket"], proof) == fenced
    assert supervisor.fence() == (fenced["generation"], fenced["token"])
    _query_consumers(root, supervisor)
    if os.environ.get("TA_ORACLE_HTTPS") == "1":
        runpy.run_path("/app/scripts/role_stream_oracle.py")["probe"](root)
    from tinyassets.storage.outbound_connections import (
        GrantResolutionError,
        ProxyRequestError,
        _broker_channel,
    )

    os.environ[supervisor_module.ENV_SWITCH] = supervisor_module.PROCESS
    channel = _broker_channel(root, principal="alice", command_center="alice",
                              grant_id="absent", connection_id="absent")
    try:
        channel._client.request(grant_id="absent", connection_id="absent", verb="GET",
                                request={}, op_id="00000000-0000-4000-8000-000000000002")
    except GrantResolutionError:
        pass
    else:
        raise AssertionError("broker admitted missing grant after restart")
    supervisor.stop()
    assert Path(answer["socket"]).is_socket(), "daemon stop unlinked broker socket"
    assert _fence(answer["socket"], proof) == fenced, "daemon stop killed the broker"
    try:
        _broker_channel(root, principal="alice", command_center="alice",
                        grant_id="absent", connection_id="absent")
    except ProxyRequestError:
        pass
    else:
        raise AssertionError("stopped supervisor retained owner authority")
    print("daemon supervisor acquisition, private-memory channel after restart, "
          "stop without signal: PASS",
          flush=True)
    print("launcher broker crash/restart preserves in-memory owner fence: PASS", flush=True)
    os.write(control, b"D")
    # The launcher must signal this foreign uid at shutdown.
    while True:
        signal.pause()


def _accounting_runtime(root, provider):
    from tinyassets import process_liveness
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.request_budget import RequestBudgetExceeded, TurnRequestBudget

    # D44 tests IPC with declared permissions. D45 must replace this fixture
    # preparation with the runtime creator and migration before activation.
    original = process_liveness.hold_liveness

    def hold(base, token):
        held = original(base, token)
        directory = Path(base) / process_liveness.LIVENESS_DIR
        os.chown(directory, -1, 1102)
        directory.chmod(0o2750)
        os.fchown(held.fd, -1, 1102)
        os.fchmod(held.fd, 0o640)
        return held

    process_liveness.hold_liveness = hold
    budget = TurnRequestBudget("alice", "alice", max_requests=1)
    try:
        budget.persist(root)
        ordinal = budget.reserve(owner="alice", universe="alice", source_ref=provider,
                                  model="alice-fixture", free=True)
        budget.link("turn", "accounting-fixture")
        budget.dispatched(ordinal)
        budget.settle(ordinal, "succeeded")
        receipt = budget.receipt()
        assert receipt["dispatched"] == 1 and receipt["attempts"][0]["state"] == "succeeded"
        try:
            budget.reserve(owner="alice", universe="alice", source_ref=provider,
                           model="alice-fixture", free=True)
        except RequestBudgetExceeded as exc:
            assert exc.reason == "turn_attempt_limit" and exc.request_receipt == receipt
        else:
            raise AssertionError("IPC accounting exceeded the parent limit")
        try:
            budget._store.receipt(("bob", "alice", budget.usage_id))
        except ProviderAuthorityHeldError:
            pass
        else:
            raise AssertionError("foreign accounting receipt exposed")
        budget.close()
        assert budget.receipt()["closed"]
        print("D44 actual accounting create/reserve/dispatch/settle/receipt/close via launcher "
              "broker, foreign refusal and committed budget stop: PASS "
              "(fixture lock modes; inference POST and runtime permissions pending)", flush=True)
    finally:
        if budget._lease is not None:
            budget._lease.close()
        process_liveness.hold_liveness = original


def main():
    launcher = runpy.run_path("/usr/local/libexec/ta-launch.py")
    permissions = runpy.run_path("/usr/local/libexec/ta-egress-migration.py")["_permissions"]

    def directory_permissions(path, uid, gid, mode):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            permissions(fd, uid, gid, mode)
        finally:
            os.close(fd)
    root = Path(tempfile.mkdtemp(prefix="uid-launcher-data-"))
    root.chmod(0o755)
    os.chown(root, 1001, 1001)
    for child in (".broker", ".broker/state", ".broker/.outbound-proxy"):
        directory = root / child
        directory.mkdir(mode=0o2700)
        directory_permissions(directory, 1002, 1101, 0o2700)
    _seed_ledger(root)
    if os.environ.get("TA_ORACLE_HTTPS") == "1":
        stream = runpy.run_path("/app/scripts/role_stream_oracle.py")
        stream["install_fixture_trust"]()
        stream["seed"](root)
    run = Path(tempfile.mkdtemp(prefix="uid-launcher-", dir="/run"))
    run.chmod(0o755)
    ipc = run / "broker"
    ipc.mkdir()
    directory_permissions(ipc, 1002, 1101, 0o2750)
    # Ledger fixture connections use SQLite's transaction context, which does
    # not close the handle. Collect those cyclic setup handles before forking;
    # their later collection must not change the launcher's fd-leak baseline.
    import gc

    gc.collect()
    runner = os.fork()
    if runner == 0:
        server = None
        try:
            # Serving with migration capabilities must fail before any bind.
            try:
                launcher["BrokerLauncher"](root, run, os.getpid())
            except launcher["Refused"]:
                pass
            else:
                raise AssertionError("launcher admitted migration capabilities")
            launcher["retire_migration_authority"]()
            print("launcher migration-capability retirement/readback and pre-bind refusal: PASS",
                  flush=True)
            ready_read, ready_write = os.pipe()
            control_read, control_write = os.pipe()
            daemon = os.fork()
            if daemon == 0:
                try:
                    _daemon(root, run, ready_read, control_write, launcher)
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
            os.close(ready_read)
            os.close(control_write)
            os.set_blocking(control_read, False)
            server = launcher["BrokerLauncher"](root, run, daemon)
            server.bind()
            assert not server.listener.get_inheritable()
            info = (run / "launcher.sock").stat()
            assert (info.st_uid, info.st_gid, info.st_mode & 0o7777) == (0, 1001, 0o660)
            stranger = os.fork()
            if stranger == 0:
                try:
                    launcher["retire_child"]("broker")
                    launcher["close_descriptors"]()
                    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
                        try:
                            connection.connect(str(run / "launcher.sock"))
                        except PermissionError:
                            os._exit(0)
                    os._exit(1)
                except BaseException:
                    os._exit(1)
            assert os.waitpid(stranger, 0)[1] == 0
            os.write(ready_write, b"1")
            os.close(ready_write)
            complete = False
            deadline = time.monotonic() + 100
            baseline = len(os.listdir("/proc/self/fd"))
            while time.monotonic() < deadline and server.poll():
                try:
                    command = os.read(control_read, 1)
                except BlockingIOError:
                    continue
                if command == b"K":
                    fields = dict(line.split(":", 1) for line in Path(
                        f"/proc/{server.broker_pid}/status").read_text().splitlines())
                    assert all(int(fields[key], 16) == 0 for key in launcher["CAP_FIELDS"])
                    assert int(fields["NoNewPrivs"]) == 1
                    assert fields["Groups"].split() == ["1102"]
                    # /proc/<pid> itself can retain the process uid even when
                    # non-dumpable. Prove the protected file and actual read.
                    environment = Path(f"/proc/{server.broker_pid}/environ")
                    assert environment.stat().st_uid == 0
                    sibling = os.fork()
                    if sibling == 0:
                        try:
                            launcher["retire_child"]("broker")
                            launcher["close_descriptors"]()
                            try:
                                environment.read_bytes()
                            except PermissionError:
                                os._exit(0)
                            os._exit(1)
                        except BaseException:
                            os._exit(1)
                    assert os.waitpid(sibling, 0)[1] == 0
                    assert len(os.listdir("/proc/self/fd")) == baseline, (
                        baseline, os.listdir("/proc/self/fd"))
                    os.kill(server.broker_pid, signal.SIGKILL)
                elif command == b"D":
                    complete = True
                    break
            assert complete, "daemon fixture exited or timed out before acceptance"
            server.stop()
            server = None
            print("broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; "
                  "cross-uid shutdown: PASS",
                  flush=True)
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            if server is not None:
                server.stop()
            os._exit(1)
    assert os.waitpid(runner, 0)[1] == 0
    assert not (root / ".broker/owner.json").exists()
    info = (root / ".broker/outbound.db").stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (1002, 1101, 0o600)
    assert not (root / "outbound.db").exists()
    print("launcher wrong-uid filesystem refusal; actual broker uses private ledger: PASS",
          flush=True)
    print("LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, inference accounting, "
          "refresh and engine classes pending; HTTPS stream requires --production-stream",
          flush=True)


if __name__ == "__main__":
    main()
