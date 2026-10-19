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
    from tinyassets.box_layout_migration import _build_cutover
    from tinyassets.cutover_sqlite import _repair_staged_databases
    from tinyassets.erasure_ledger import _legacy_main_digests

    damage_many_indexes(source / ".tinyassets.db", corrupt_table=True)
    from tinyassets.sqlite_readonly import readonly_db

    with readonly_db(source / ".tinyassets.db") as db:
        errors = db.execute("PRAGMA integrity_check(1000000000)").fetchall()
        assert any("CHECK" in row[0] for row in errors), errors
        with pytest.raises(ValueError, match="not index-only"):
            check(db, repair=True)
    before = (source / ".tinyassets.db").read_bytes()
    if caller == "census":
        assert census(source)["refusals"]["database_integrity"] == 1
    elif caller == "forward":
        with pytest.raises(RuntimeError, match="census refused"):
            _build_cutover(source, tmp_path / "store", {"source_marker": None})
    elif caller == "seed":
        with pytest.raises(ValueError, match="not index-only"):
            _legacy_main_digests(source)
    else:
        with pytest.raises(ValueError, match="not index-only"):
            _repair_staged_databases(source)
    assert (source / ".tinyassets.db").read_bytes() == before


@pytest.mark.parametrize("many", [False, True])
def test_rollback_database_repair_accepts_index_only_drift(source, many):
    from tinyassets.cutover_sqlite import _repair_staged_databases

    path = source / ".tinyassets.db"
    if many:
        damage_many_indexes(path, corrupt_table=False)
    else:
        corrupt_index(path)
    _repair_staged_databases(source)
    with closing(sqlite3.connect(path)) as db:
        assert check(db) == []
        assert db.execute("SELECT count(*) FROM agent_turns").fetchone() == (4,)


def test_legacy_seeding_repairs_private_copy_without_changing_source(source, tmp_path):
    from tinyassets.erasure_ledger import _legacy_main_digests

    path = source / ".tinyassets.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE deleted_principals (founder_sub TEXT)")
        db.execute("INSERT INTO deleted_principals VALUES ('digest')")
    corrupt_index(path)
    before = _fingerprint(source)
    assert _legacy_main_digests(source) == ["digest"]
    assert _fingerprint(source) == before


def test_staged_repair_preserves_symlinks_without_following_them(source, tmp_path):
    from tinyassets.cutover_sqlite import _repair_staged_databases
    from tinyassets.erasure_ledger import _legacy_main_digests

    outside = tmp_path / "outside.db"
    with closing(sqlite3.connect(outside)) as db, db:
        db.execute("CREATE TABLE agent_turns (turn_id)")
        db.execute("INSERT INTO agent_turns VALUES (1)")
    corrupt_index(outside)
    before = outside.read_bytes()
    link = source / "linked.db"
    link.symlink_to(outside)
    note = source / "note-link.txt"
    note.symlink_to("a1/notes.txt")
    corrupt_index(source / ".tinyassets.db")

    _repair_staged_databases(source)

    assert link.is_symlink() and link.readlink() == outside
    assert note.is_symlink() and note.read_text() == (source / "a1/notes.txt").read_text()
    assert outside.read_bytes() == before
    with closing(sqlite3.connect(source / ".tinyassets.db")) as db:
        assert check(db) == []
    # Skipping links in the scan must not authorize a linked authority file.
    (source / ".tinyassets.db").unlink()
    (source / ".tinyassets.db").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        _legacy_main_digests(source)


def test_staged_repair_preserves_text_hardlinks_but_refuses_database_hardlinks(source):
    import os

    from tinyassets.cutover_sqlite import _repair_staged_databases

    original = source / "a1/notes.txt"
    linked = source / "a1/notes-copy.txt"
    os.link(original, linked)
    _repair_staged_databases(source)
    assert original.samefile(linked) and original.stat().st_nlink == 2
    assert linked.read_text() == "private history for a1"
    database = source / ".tinyassets.db"
    before = database.read_bytes()
    os.link(database, source / "linked-database")
    with pytest.raises(ValueError, match="private regular file"):
        _repair_staged_databases(source)
    assert database.read_bytes() == before
