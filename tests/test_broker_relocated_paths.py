"""The private ledger parent is not the logical command-center data root."""
from tinyassets.broker.process import _Dispatchers
from tinyassets.storage.outbound_connections import ConnectionLedger


def test_broker_dispatch_preserves_logical_root_with_private_ledger(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    config = ledger.broker_dispatch_config(grant_id="grant", universe_id="alice",
                                         provider="http", destination="compute:test",
                                         owner_user_id="alice", connection_type="http")
    assert config["universe_dir"] == str((tmp_path / "alice").resolve())
    assert config["data_root"] == str(tmp_path.resolve())
    assert config["ledger_db_path"] == str((tmp_path / ".broker/outbound.db").resolve())
    assert config["runtime_root"].startswith(str((tmp_path / ".broker/.outbound-proxy").resolve()))


def test_role_broker_opens_only_the_relocated_ledger(tmp_path):
    dispatcher = _Dispatchers(tmp_path, allow_test_fixtures=False, role_split=True)
    ledger = dispatcher.ledger_for("alice")
    assert ledger.require_authenticated_principal_id() == "alice"
    assert (tmp_path / ".broker/outbound.db").is_file()
    assert not (tmp_path / "outbound.db").exists()
