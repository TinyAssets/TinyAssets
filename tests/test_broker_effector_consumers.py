"""D24 exact effector authority stays broker-owned in split mode."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from tinyassets import bound_requests
from tinyassets.broker import ledger_queries, supervisor
from tinyassets.effectors import authenticated_external_call as effector
from tinyassets.storage.outbound_connections import (
    ActionCap,
    ConnectionLedger,
    GrantResolutionError,
    ProxyRequestError,
)
from tinyassets.ta_capabilities import ExecutionContext


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    for owner in ("alice", "bob"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET",), provider="http",
            destination=f"compute:{owner}", credential_ref="vault://http/fixture",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/models",
                                "methods": ["GET"]}],
        )
        ledger.grant_connection(
            grant_id=f"grant-{owner}", connection_id=f"conn-{owner}", owner_user_id=owner,
            universe_id="cc-alice", unprompted_action_cap=ActionCap("calls", 3, "requests"))
    calls = []

    class Client:
        def __init__(self, path, *, principal, command_center, **kwargs):
            self.principal, self.center = principal, command_center

        def ledger_query(self, **kwargs):
            calls.append((self.principal, self.center, kwargs))
            return ledger_queries.local_query(ledger, principal=self.principal,
                                              command_center=self.center, **kwargs)

    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: SimpleNamespace(
        socket_path=tmp_path / "broker.sock", fence=lambda: (1, "synthetic"), verify_broker=None))
    monkeypatch.setattr("tinyassets.broker.client.BrokerClient", Client)

    def no_local(*args, **kwargs):
        raise AssertionError("daemon constructed a local ledger")

    monkeypatch.setattr("tinyassets.storage.outbound_connections.ConnectionLedger", no_local)
    args = dict(data_root=tmp_path, grant_id="grant-alice",
                connection_id="conn-alice", universe_id="cc-alice", principal="alice")
    return SimpleNamespace(root=tmp_path, ledger=ledger, calls=calls, args=args)


def test_scoped_snapshot_preserves_grant_cap_and_redacts_effector_view(case):
    grant, view, error = effector._read_connection_context(**case.args)
    assert not error
    assert grant == case.ledger.get_grant("grant-alice")
    assert grant.unprompted_action_cap == ActionCap("calls", 3, "requests")
    assert view == case.ledger.get_connection_view("conn-alice")
    assert "credential_ref" not in asdict(view)
    assert case.calls == [("alice", "cc-alice", {
        "query": ledger_queries.AUTHORIZED_CONNECTION,
        "grant_id": "grant-alice", "connection_id": "conn-alice"})]
    assert not (case.root / "outbound.db").exists()


@pytest.mark.parametrize("change", [
    {"principal": "bob"}, {"universe_id": "cc-bob"}, {"grant_id": "grant-bob"},
    {"connection_id": "conn-bob"}, {"grant_id": "absent"},
])
def test_effector_never_reads_foreign_authority(case, change):
    assert effector._read_connection_context(**(case.args | change)) == (
        None, None, "connection_authority_unavailable")


def test_missing_principal_refuses_without_querying_or_guessing(case):
    assert effector._read_connection_context(**(case.args | {"principal": ""})) == (
        None, None, "no_universe_authority")
    assert case.calls == []


@pytest.mark.parametrize("kind", ["grant", "connection"])
def test_proxy_rechecks_revocation_after_effector_snapshot(case, kind):
    assert effector._read_connection_context(**case.args)[2] == ""
    if kind == "grant":
        case.ledger.revoke_grant("grant-alice")
    else:
        case.ledger.revoke_connection("conn-alice")
    args = {k: v for k, v in case.args.items() if k != "principal"}
    with pytest.raises(GrantResolutionError):
        effector._open_connection_proxy(**args, owner_user_id="alice")


def test_unavailable_broker_never_opens_daemon_ledger(case, monkeypatch):
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: None)
    with pytest.raises(ProxyRequestError, match="not running"):
        effector._read_connection_context(**case.args)


def test_proxy_preserves_access_mode_and_uses_exact_broker_channel(case, monkeypatch):
    from tinyassets.storage import outbound_connections as outbound

    with case.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET access_mode = 'full' "
                     "WHERE connection_id = 'conn-alice'")
    channel = SimpleNamespace(close=lambda: None)
    seen = []

    def open_channel(root, **scope):
        seen.append((root, scope))
        return channel

    monkeypatch.setattr(outbound, "_broker_channel", open_channel)
    args = {k: v for k, v in case.args.items() if k != "principal"}
    proxy = effector._open_connection_proxy(**args, owner_user_id="alice")
    assert proxy.access_mode == "full"
    assert proxy._channel is channel
    assert seen == [(case.root, dict(principal="alice", command_center="cc-alice",
                                     grant_id="grant-alice", connection_id="conn-alice"))]


@pytest.mark.parametrize("projection", [None, {}, {"resource": None, "grant": {}},
                                      {"resource": {}, "grant": []}])
def test_malformed_authority_is_fixed_transport_error(case, monkeypatch, projection):
    monkeypatch.setattr(ledger_queries, "query_ledger", lambda *a, **kw: projection)
    with pytest.raises(ProxyRequestError, match="invalid credential broker authority projection"):
        effector._read_connection_context(**case.args)


@pytest.mark.parametrize("field,value", [
    ("grant_id", "grant-bob"), ("connection_id", "conn-bob"),
    ("owner_user_id", "bob"), ("universe_id", "cc-bob"), ("revoked_at", 1),
])
def test_projection_scope_is_validated_before_use(case, monkeypatch, field, value):
    facts = ledger_queries.local_query(
        case.ledger, query=ledger_queries.AUTHORIZED_CONNECTION, principal="alice",
        command_center="cc-alice", grant_id="grant-alice", connection_id="conn-alice")
    facts["grant"][field] = value
    monkeypatch.setattr(ledger_queries, "query_ledger", lambda *a, **kw: facts)
    with pytest.raises(ProxyRequestError, match="invalid credential broker authority projection"):
        effector._read_connection_context(**case.args)


def test_full_effector_binds_execution_principal_before_consent(case):
    packet = {"sink": "authenticated_external_call", "connection_id": "conn-alice",
              "grant_id": "grant-alice", "verb": "GET", "request": {"path": "/models"}}
    result = effector.run_authenticated_external_call_effector(
        node_id="fixture", output_keys=["call"], run_state={"call": packet},
        base_path=case.root / "cc-alice",
        execution_context=ExecutionContext("cc-alice", "bob", "main"))
    assert result["error_kind"] == "connection_authority_unavailable"
    assert case.calls[0][0] == "bob"


def test_bound_preview_uses_same_snapshot_for_grant_view_and_incarnation(case):
    from tinyassets.storage.effector_consents import grant_consent

    home = case.root / "cc-alice"
    grant_consent(home, sink="authenticated_external_call", destination="compute:alice",
                  granted_by="alice")
    packet = {"connection_id": "conn-alice", "grant_id": "grant-alice", "verb": "GET",
              "request": {"path": "/models"}}
    authority = bound_requests._authority(home, packet, "alice", "main")
    assert authority["connection_revision"] == bound_requests.digest([
        case.ledger.get_connection_view("conn-alice").as_dict(),
        case.ledger.incarnation("conn-alice")])
    assert len(case.calls) == 1
    with pytest.raises(bound_requests.RequestRefused, match="authority is unavailable"):
        bound_requests._authority(home, packet, "bob", "main")
