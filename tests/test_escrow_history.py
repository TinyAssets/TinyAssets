"""Retirement keeps existing history readable without exposing a writer."""
import importlib
import sqlite3

import pytest

from tinyassets.payments import escrow


@pytest.mark.parametrize("currency_column", [True, False])
def test_existing_escrow_is_read_only(tmp_path, currency_column):
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE escrow_locks (lock_id TEXT, gate_claim_id TEXT, "
                     "staker_id TEXT, amount INTEGER, status TEXT, locked_at TEXT"
                     + (", currency TEXT" if currency_column else "") + ")")
        conn.execute("INSERT INTO escrow_locks VALUES ('lock', 'claim', 'owner', "
                     "12, 'locked', '2026-01-01'"
                     + (", 'historical-unit'" if currency_column else "") + ")")
    before = path.read_bytes()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        assert escrow.get_lock(conn, "lock").amount == 12
        assert escrow.get_lock_for_claim(conn, "claim", "owner").lock_id == "lock"
        assert len(escrow.list_locks_for_claim(conn, "claim")) == 1
        assert escrow.get_lock(conn, "absent") is None
    assert path.read_bytes() == before
    assert not any(hasattr(escrow, name) for name in (
        "lock_bonus", "release_bonus", "refund_bonus", "migrate_escrow_schema",
    ))


def test_removed_extensions_cannot_be_imported():
    for name in ("actions", "funding", "wallets", "settlement_backend", "settlement_ledger"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"tinyassets.payments.{name}")
