"""Synthetic index drift is repairable; table corruption never is."""
import sqlite3
from contextlib import closing

import pytest

from tests.test_owner_cutover_census import _fingerprint
from tests.test_owner_cutover_census import source as source
from tinyassets.box_layout_migration import census
from tinyassets.sqlite_integrity import check


def corrupt_index(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE INDEX drift ON agent_turns(turn_id) WHERE 0")
        db.execute("PRAGMA writable_schema=ON")
        db.execute("UPDATE sqlite_master SET sql='CREATE INDEX drift ON agent_turns(turn_id)' "
                   "WHERE name='drift'")


def test_census_reports_index_drift_without_writing_and_apply_policy_repairs(source):
    path = source / ".tinyassets.db"
    corrupt_index(path)
    before = _fingerprint(source)
    report = census(source)
    assert report["ready"], report["refusals"]
    assert report["repairable_indexes"] == ["repairable index: .tinyassets.db/drift"]
    assert _fingerprint(source) == before
    with closing(sqlite3.connect(path)) as db, db:
        assert check(db, repair=True) == ["drift"]
        assert check(db) == []
        assert db.execute("SELECT count(*) FROM agent_turns INDEXED BY drift").fetchone() == (4,)


def test_table_integrity_failure_refuses_even_when_an_index_is_repairable(source):
    path = source / ".tinyassets.db"
    corrupt_index(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE universe_owner SET owner_id=NULL WHERE universe_id='a1'")
        db.execute("PRAGMA writable_schema=ON")
        db.execute("UPDATE sqlite_master SET sql=replace(sql, 'owner_id TEXT', "
                   "'owner_id TEXT NOT NULL') WHERE name='universe_owner'")
    before = _fingerprint(source)
    report = census(source)
    assert not report["ready"]
    assert report["refusals"]["database_integrity"] == 1
    with closing(sqlite3.connect(path)) as db, pytest.raises(ValueError, match="not index-only"):
        check(db, repair=True)
    assert _fingerprint(source) == before


def damage_many_indexes(path, *, corrupt_table):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE index_faults (value)")
        db.execute("INSERT INTO index_faults VALUES (1)")
        for number in range(110):
            db.execute(f"CREATE INDEX damaged_{number} ON index_faults(value) WHERE 0")
        db.execute("CREATE TABLE trailing_fault (value CHECK(value > 0))")
        if corrupt_table:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute("INSERT INTO trailing_fault VALUES (-1)")
        db.execute("PRAGMA writable_schema=ON")
        db.execute("UPDATE sqlite_master SET sql=replace(sql, ' WHERE 0', '') "
                   "WHERE name LIKE 'damaged_%'")
    with closing(sqlite3.connect(path)) as db:
        errors = db.execute("PRAGMA integrity_check").fetchall()
        assert len(errors) == 100
        assert all("index" in row[0] for row in errors)
        all_errors = db.execute("PRAGMA integrity_check(1000000000)").fetchall()
        if corrupt_table:
            assert any("CHECK" in row[0] for row in all_errors), all_errors


@pytest.mark.parametrize("caller", ["census", "forward", "seed", "rollback"])
def test_non_index_fault_after_110_damaged_indexes_is_never_hidden(source, tmp_path, caller):
    from tests.test_box_restore_authority import paired
    from tinyassets.box_layout_migration import _build_cutover
    from tinyassets.erasure_ledger import ErasureLedger, seed_legacy
    from tinyassets.restore_recovery import normal_preflight

    damage_many_indexes(source / ".tinyassets.db", corrupt_table=True)
    from tinyassets.sqlite_readonly import readonly_db

    with readonly_db(source / ".tinyassets.db") as db:
        errors = db.execute("PRAGMA integrity_check(1000000000)").fetchall()
        assert any("CHECK" in row[0] for row in errors), errors
        with pytest.raises(ValueError, match="not index-only"):
            check(db, allow_index_drift=True)
    before = (source / ".tinyassets.db").read_bytes()
    if caller == "census":
        assert census(source)["refusals"]["database_integrity"] == 1
    elif caller == "forward":
        with pytest.raises(RuntimeError, match="census refused"):
            _build_cutover(source, tmp_path / "store", {"source_marker": None})
    elif caller == "seed":
        ledger_root = tmp_path / "ledger"
        ledger_root.mkdir()
        ledger = ErasureLedger(ledger_root)
        ledger.initialize()
        with pytest.raises(ValueError, match="not index-only"):
            seed_legacy(source, ledger, store_root=tmp_path)
    else:
        current, _, live, _ = paired(tmp_path / "pair")
        with pytest.raises(ValueError, match="not index-only"):
            normal_preflight(current, source, live, backup_time=1)
    assert (source / ".tinyassets.db").read_bytes() == before


def test_rollback_refuses_unrepaired_index_only_drift(source, tmp_path):
    from tests.test_box_restore_authority import paired
    from tinyassets.restore_recovery import normal_preflight

    current, _, live, _ = paired(tmp_path / "pair")
    damage_many_indexes(source / ".tinyassets.db", corrupt_table=False)
    with pytest.raises(ValueError, match="requires repair"):
        normal_preflight(current, source, live, backup_time=1)
    with closing(sqlite3.connect(source / ".tinyassets.db")) as db, db:
        assert len(check(db, repair=True)) == 110
        assert check(db) == []
