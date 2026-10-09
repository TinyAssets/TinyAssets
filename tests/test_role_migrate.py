"""Portable contracts of deploy/role_migrate.py.

The kernel behaviour (kill at every step, rerun, byte-identical manifest; each
precondition refusing) is proven as root on ext4 by scripts/role_migrate_probe.py.
"""
import os
import runpy
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import pytest

if os.name == "nt":
    pytest.skip("the migration is Linux-only (Linux oracle)", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
MIGRATE = runpy.run_path(str(ROOT / "deploy" / "role_migrate.py"))
MODES = runpy.run_path(str(ROOT / "tinyassets" / "role_modes.py"))
BINDINGS = {"u-a": 300001}


def _info(kind, mode=0o644):
    return os.stat_result((kind | mode, 1, 1, 1, 1001, 1001, 0, 0, 0, 0))


def target(path, kind=stat.S_IFREG, mode=0o644):
    return MIGRATE["target"](path, _info(kind, mode), BINDINGS, MODES)


def _schema(path):
    with closing(sqlite3.connect(path)) as db:
        return sorted(db.execute("SELECT type, name, sql FROM sqlite_master"))


def test_identity_ddl_matches_the_broker_database(tmp_path):
    from tinyassets.broker.owner_identities import OwnerIdentities

    broker = tmp_path / "broker"
    broker.mkdir(mode=0o700)
    OwnerIdentities(broker / "ids.db", initialize=True)
    with closing(sqlite3.connect(tmp_path / "migrated.db")) as db:
        for statement in MIGRATE["IDENTITY_DDL"]:
            db.execute(statement)
    assert _schema(tmp_path / "migrated.db") == _schema(broker / "ids.db")


def test_accounting_schema_matches_the_application(tmp_path):
    from tinyassets.storage.agent_request_usage import _SCHEMA

    app, migrated = tmp_path / "app.db", tmp_path / "migrated.db"
    with closing(sqlite3.connect(app)) as db:
        for statement in _SCHEMA:
            db.execute(statement)
    with closing(sqlite3.connect(migrated)) as db:
        for statement in (*MIGRATE["ACCOUNTING_SCHEMA"], MIGRATE["ACCOUNTING_INDEX"]):
            db.execute(statement)
    with closing(sqlite3.connect(app)) as db:
        assert MIGRATE["_accounting_facts"](db).keys() == set(MIGRATE["ACCOUNTING_TABLES"])


def test_owner_root_gets_the_canonical_admission_label():
    contract = runpy.run_path(str(ROOT / "deploy" / "role_admission_contract.py"))
    assert target("u-a", stat.S_IFDIR, 0o755) == contract["canonical_label"](300001)


def test_owner_work_is_owner_owned_and_daemon_keeps_access():
    uid, gid, mode, access, default = target("u-a/notes", stat.S_IFDIR, 0o755)
    assert (uid, gid, mode) == (300001, 300001, 0o755)
    assert access == MIGRATE["_acl"](7, {1001: 7}, 5, mask=5, other=5)
    assert default == MIGRATE["_acl"](7, {1001: 7})
    assert target("u-a/notes/a.md", mode=0o4755)[:3] == (300001, 300001, 0o755)
    assert target("u-a/notes/a.md")[3] == MIGRATE["KEEP"]
    assert target("u-a/.agent-workspace", stat.S_IFDIR, 0o755)[0] == 300001
    assert target("u-a/notes-link", stat.S_IFLNK) == "skip"


def test_platform_entries_inside_a_center_stay_daemon_owned():
    assert target("u-a/.credentials", stat.S_IFDIR)[:3] == (1001, 1102, 0o2750)
    assert target("u-a/provider_definitions.json")[:3] == (1001, 1102, 0o640)
    assert target("u-a/.runtime/cache/a", mode=0o600)[:3] == (1001, 1001, 0o600)
    sealed = target("u-a/.runtime/provider-launch-credentials/s/auth.json", mode=0o600)
    assert sealed[:3] == (1001, 1001, 0o440)
    assert sealed[3] == MIGRATE["_acl"](4, {300001: 4}, mask=4)


def test_platform_areas_and_stale_entries():
    assert target(".broker", stat.S_IFDIR)[:3] == (1002, 1101, 0o2700)
    assert target(".broker/state", stat.S_IFDIR)[:3] == (1002, 1101, 0o700)
    assert target(".broker/owner.json") == "remove"
    assert target(".universe-sidecars/u-a/egress-7.sock", stat.S_IFSOCK) == "remove"
    assert target(".layout.lock")[:3] == (1001, 1001, 0o666)
    assert target(".layout.json") == "skip"
    assert target(".tinyassets.db")[:3] == (1001, 1001, 0o600)


def test_sidecar_files_keep_their_own_mode():
    # U2 concern 2026-10-06 (metadata-sidecar-mode): a regular sidecar file never
    # gains setgid or execute from the directory mode.
    assert target(".universe-sidecars/u-a/relay.json", mode=0o600)[:3] == (1001, 1100, 0o600)
    assert target(".universe-sidecars/u-a", stat.S_IFDIR)[:3] == (1001, 1100, 0o2710)


@pytest.mark.parametrize("path, kind", [
    ("u-a/notes/pipe", stat.S_IFIFO),
    ("u-a/.runtime/link", stat.S_IFLNK),
    ("community-pool/socket", stat.S_IFSOCK),
])
def test_special_files_refuse(path, kind):
    with pytest.raises(MIGRATE["MigrationRefused"]):
        target(path, kind)
