"""Account deletion cannot silently miss the relocated broker ledger."""
# ruff: noqa: F811 -- imported pytest fixtures
import socket
import sys

import pytest

from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase
from tinyassets.storage.outbound_connections import ConnectionLedger


def seed(ledger):
    from tinyassets.storage.agent_request_usage import _SCHEMA

    with ledger._connect() as db:
        for statement in _SCHEMA:
            db.execute(statement)
        for owner in ("erase-a", "erase-b"):
            db.execute("INSERT INTO outbound_connections "
                       "(connection_id,owner_user_id,connection_class,scopes_json,provider,"
                       "destination,credential_ref) VALUES (?,?,'http','[]','http','x','ref')",
                       (owner, owner))
            db.execute("INSERT INTO outbound_connection_grants "
                       "(grant_id,connection_id,owner_user_id,universe_id,granted_at) "
                       "VALUES (?,?,?,'shared-home',1)", (owner, owner, owner))
            db.execute("INSERT INTO connection_capabilities VALUES (?,'model_use','{}',1)",
                       (owner,))
            db.execute("INSERT INTO outbound_connector_artifacts VALUES (?,?, '{}','{}',1)",
                       (owner, owner))
            db.execute("INSERT INTO agent_request_usage VALUES "
                       "(?,'shared-home','usage','{}','{}','lease','parent',0,'now')", (owner,))
            db.execute("INSERT INTO agent_request_attempts VALUES "
                       "(?,'shared-home','usage',1,'{}','now','source','unknown')", (owner,))
            db.execute("INSERT INTO agent_request_usage_links VALUES "
                       "(?,'shared-home','usage','run','same-run')", (owner,))
            db.execute("INSERT INTO agent_request_dispatches VALUES "
                       "(?,?,'shared-home','usage',1,1,?,?,'digest','operation',0,1)",
                       (owner, owner, owner, owner))
        db.execute("INSERT INTO outbound_connector_artifact_edges VALUES "
                   "('erase-a','erase-b','erase-b',1)")


def test_transaction_erases_only_owner_and_counts_indirect_rows(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    seed(ledger)
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="erase-a", center="erase-a")
    counts = local_erase(ledger, principal="erase-a", command_center=ACCOUNT_SCOPE)
    assert len(counts) == 9 and set(counts.values()) == {1}
    assert local_erase(ledger, principal="erase-a", command_center=ACCOUNT_SCOPE) == {}
    with ledger._connect() as db:
        for table in counts.keys() - {"outbound_connector_artifact_edges"}:
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert ledger.get_connection("erase-b") is not None


def test_foreign_grant_aborts_entire_erasure(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    seed(ledger)
    with ledger._connect() as db:
        db.execute("UPDATE outbound_connection_grants SET owner_user_id='erase-b' "
                   "WHERE connection_id='erase-a'")
    with pytest.raises(ValueError, match="foreign grant"):
        local_erase(ledger, principal="erase-a", command_center=ACCOUNT_SCOPE)
    with ledger._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_request_usage").fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM outbound_connector_artifact_edges").fetchone()[0] == 1


def test_sql_failure_rolls_back_already_deleted_rows(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    seed(ledger)
    with ledger._connect() as db:
        db.execute("CREATE TRIGGER refuse_erasure BEFORE DELETE ON agent_request_usage "
                   "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(Exception, match="fixture failure"):
        local_erase(ledger, principal="erase-a", command_center=ACCOUNT_SCOPE)
    assert ledger.get_connection("erase-a") is not None
    with ledger._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM outbound_connector_artifact_edges").fetchone()[0] == 1


unix = pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                          reason="Unix peer credentials")






@unix
def test_stale_fence_and_extra_target_fields_refuse(discovery):
    from tinyassets import rpc_frames as rf

    seed(discovery.ledger)
    wire = {"op": "ERASE_ACCOUNT", "principal": "erase-a", "command_center": ACCOUNT_SCOPE,
            "generation": discovery.broker.state["generation"],
            "token": discovery.broker.state["token"]}
    for change in ({"token": "old"}, {"path": "outbound.db"}, {"principal": ""},
                   {"command_center": "shared-home"}, {"generation": True}):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(5)
            channel.connect(str(discovery.broker.path))
            channel.sendall(rf.control(rf.CONNECTION, wire | change))
            assert channel.recv(1) == b""
    assert discovery.ledger.get_connection("erase-a") is not None


def test_broker_local_erasure_replays_after_lost_completion_receipt(discovery):
    seed(discovery.ledger)
    counts = local_erase(discovery.ledger, principal="erase-a", command_center=ACCOUNT_SCOPE)
    assert counts["agent_request_dispatches"] == 1
    assert discovery.ledger.get_connection("erase-a") is None
    assert discovery.ledger.get_connection("erase-b") is not None
    assert local_erase(discovery.ledger, principal="erase-a", command_center=ACCOUNT_SCOPE) == {}


def test_erasure_has_no_outward_or_unavailable_broker_client():
    from tinyassets.broker import account_erasure
    assert not hasattr(account_erasure, "erase_account")
    assert not hasattr(account_erasure, "close_channels")
