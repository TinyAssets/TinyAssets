"""D11 named reads: scoped snapshots, pricing precedence and no local fallback."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tinyassets.broker.fence import Fence
from tinyassets.broker.ledger_queries import (
    DISCOVERY_FACTS,
    HAS_PRICED_SOURCE,
    local_query,
    query_ledger,
)
from tinyassets.broker.server import _Connection
from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError


@pytest.fixture
def ledger(tmp_path):
    ledger = ConnectionLedger(tmp_path / "outbound.db")
    for owner in ("alice", "bob"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
            provider="http", destination=f"compute:{owner}", credential_ref="vault://http/fixture",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                                "methods": ["POST"]}],
        )
        ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                                owner_user_id=owner, universe_id=f"cc-{owner}")
    return ledger


def _query(ledger, **changes):
    args = dict(query=DISCOVERY_FACTS, principal="alice", command_center="cc-alice",
                grant_id="grant-alice", connection_id="conn-alice")
    return local_query(ledger, **(args | changes))


@pytest.mark.parametrize("changes", [
    {"principal": "bob"}, {"command_center": "cc-bob"}, {"grant_id": "grant-bob"},
    {"connection_id": "conn-bob"}, {"grant_id": "missing"},
])
def test_foreign_or_missing_scope_never_returns_facts(ledger, changes):
    with pytest.raises(GrantResolutionError):
        _query(ledger, **changes)


@pytest.mark.parametrize("kind", ["grant", "connection"])
def test_revoked_authority_never_returns_facts(ledger, kind):
    if kind == "grant":
        ledger.revoke_grant("grant-alice")
    else:
        ledger.revoke_connection("conn-alice")
    with pytest.raises(GrantResolutionError):
        _query(ledger)


def test_priced_presence_wins_even_when_descriptor_is_malformed(ledger):
    with ledger._connect() as conn:
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-alice", "model_discovery", "broken JSON"))
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-alice", "model_use", '{"billing":"free"}'))
    assert _query(ledger, query=HAS_PRICED_SOURCE) == {"priced": True}
    with pytest.raises(ValueError):
        _query(ledger)


def test_only_an_actually_absent_pair_gets_predeposit_no_pricing(ledger):
    assert _query(ledger, query=HAS_PRICED_SOURCE, grant_id="new", connection_id="new") == {
        "priced": False}
    for grant, connection in (("new", "conn-bob"), ("grant-bob", "new"),
                              ("new", "conn-alice")):
        with pytest.raises(GrantResolutionError):
            _query(ledger, query=HAS_PRICED_SOURCE, grant_id=grant, connection_id=connection)


def test_snapshot_does_not_mix_concurrent_authority_and_profile_versions(ledger, monkeypatch):
    with ledger._connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-alice", "model_use", '{"version":"before"}'))
    connect = ledger._connect

    class RacingConnection:
        def __enter__(self):
            self.conn = connect().__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, *args):
            if sql.startswith("SELECT * FROM outbound_connections"):
                with connect() as writer:
                    writer.execute("UPDATE outbound_connections SET revoked_at=1")
                    writer.execute("UPDATE connection_capabilities SET descriptor_json=?",
                                   (json.dumps({"version": "after"}),))
            return self.conn.execute(sql, *args)

    monkeypatch.setattr(ledger, "_connect", RacingConnection)
    facts = _query(ledger)
    assert facts["resource"]["revoked_at"] is None
    assert facts["profile"] == {"version": "before"}
    monkeypatch.setattr(ledger, "_connect", connect)
    with pytest.raises(GrantResolutionError):
        _query(ledger)


def test_selected_broker_never_constructs_a_local_ledger(ledger, monkeypatch, tmp_path):
    from tinyassets.broker import supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    monkeypatch.setenv(supervisor.ENV_SWITCH, supervisor.PROCESS)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("local database opened"))
    with pytest.raises(ProxyRequestError, match="not running"):
        query_ledger(tmp_path, query=DISCOVERY_FACTS, principal="alice",
                     command_center="cc-alice", grant_id="grant-alice")


def test_wire_whitelist_and_fence_are_checked_before_ledger_open(ledger, tmp_path):
    fence = Fence(tmp_path / "fence.json", verify_lease_proof=lambda g, p: p == "proof")
    generation, token = fence.barrier(1, "proof", cancel_older=lambda g: None,
                                      close_older=lambda g: None)
    calls = []

    def ledger_for(principal):
        calls.append(principal)
        return ledger

    handler = object.__new__(_Connection)
    handler._server = SimpleNamespace(_fence=fence, _ledger_for=ledger_for)
    doc = dict(op="LEDGER_QUERY", query=DISCOVERY_FACTS, principal="alice",
               command_center="cc-alice", grant_id="grant-alice", connection_id="conn-alice",
               generation=generation, token=token)
    for extra in ({"token": "wrong"}, {"generation": True}, {"query": "_connect"},
                  {"sql": "SELECT *"}, {"path": "/data/outbound.db"}, {"query": []}):
        assert handler._ledger_query(doc | extra)["op"] == "LEDGER_REFUSED"
    assert calls == []
    assert handler._ledger_query(doc)["result"]["resource"]["owner_user_id"] == "alice"
    assert calls == ["alice"]
