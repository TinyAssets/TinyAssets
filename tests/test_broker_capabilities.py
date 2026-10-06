"""Capability authority is checked again in the transaction that uses it."""
import pytest

from tinyassets.broker.capabilities import local_operation
from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError


@pytest.fixture
def ledger(tmp_path):
    value = ConnectionLedger(tmp_path / "outbound.db")
    value.create_connection(
        connection_id="conn-a", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="fixture", credential_ref="vault://http/fixture",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/catalogue",
                            "methods": ["GET", "POST"]}])
    value.grant_connection(grant_id="grant-a", connection_id="conn-a",
                           owner_user_id="alice", universe_id="cc-alice")
    return value


def document(**changes):
    return dict(action="configure", grant_id="grant-a", connection_id="conn-a",
                capability_kind="constant_headers", descriptor={"headers": {"X-Fixture": "yes"}},
                enabled=True, preview=False) | changes


@pytest.mark.parametrize("action", ["read", "configure"])
@pytest.mark.parametrize("change", ["revoked", "replaced", "foreign"])
def test_capability_rechecks_grant_inside_transaction(ledger, monkeypatch, action, change):
    from tinyassets.broker import ledger_queries

    query = ledger_queries.local_query

    def changed(*args, **kwargs):
        result = query(*args, **kwargs)
        with ledger._connect() as conn:
            if change == "revoked":
                conn.execute("UPDATE outbound_connection_grants SET revoked_at=1")
            elif change == "replaced":
                conn.execute("UPDATE outbound_connection_grants SET granted_at=granted_at+1")
            else:
                conn.execute("UPDATE outbound_connection_grants SET owner_user_id='bob'")
        return result

    monkeypatch.setattr(ledger_queries, "local_query", changed)
    args = document() if action == "configure" else document(
        action="read", enabled=False, descriptor=None)
    with pytest.raises(PermissionError):
        local_operation(ledger, principal="alice", command_center="cc-alice", document=args)
    assert ledger.get_connection_capability("conn-a", "constant_headers") is None


@pytest.mark.parametrize("scope", [
    {"principal": "bob"}, {"command_center": "cc-bob"},
])
def test_capability_foreign_scope_does_not_mutate(ledger, scope):
    with pytest.raises(GrantResolutionError):
        local_operation(ledger, **(dict(principal="alice", command_center="cc-alice") | scope),
                        document=document())
    assert ledger.get_connection_capability("conn-a", "constant_headers") is None


def test_capability_configure_read_and_disable(ledger):
    def run(**changes):
        return local_operation(ledger, principal="alice", command_center="cc-alice",
                               document=document(**changes))

    expected = {"descriptor": {"headers": {"X-Fixture": "yes"}}}
    assert run() == expected
    assert run(action="read", descriptor=None, enabled=False) == expected
    assert run(enabled=False, descriptor=None) == {"descriptor": None}
    assert run(action="read", descriptor=None, enabled=False) == {"descriptor": None}
