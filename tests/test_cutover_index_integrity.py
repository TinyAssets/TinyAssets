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
