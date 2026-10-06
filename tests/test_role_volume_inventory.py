"""Stopped-volume authority discovery, real Linux no-atime descriptor reads."""
# ruff: noqa: F811 -- shared Linux volume fixture

import json
import os
import sqlite3

import pytest

from deploy import role_egress_migration, role_owner_migration
from deploy.role_volume_inventory import InventoryRefused, inventory
from tests.test_role_owner_migration import metadata, volume  # noqa: F401


def seed(root):
    for center in ("alice", "bob"):
        (root / center / "universe.json").write_text("{}")
    with sqlite3.connect(root / ".tinyassets.db") as db:
        db.executescript("CREATE TABLE founder_home(founder_sub TEXT, universe_id TEXT);"
                         "CREATE TABLE universe_acl(universe_id TEXT,"
                         "actor_id TEXT, permission TEXT);")
        db.executemany("INSERT INTO founder_home VALUES (?,?)",
                       [("alice", "alice"), ("bob", "bob")])
    os.chown(root / ".tinyassets.db", 1001, 1001)


def read(root):
    return inventory(root, owner=vars(role_owner_migration), egress=vars(role_egress_migration))


def test_complete_discovery_and_no_atime_or_database_mutation(volume):
    seed(volume)
    for path in (volume, volume / "alice", volume / "bob", volume / ".tinyassets.db"):
        os.utime(path, ns=(1, 1))
    before = metadata(volume)
    assert read(volume) == {"principals": {"alice": "alice", "bob": "bob"},
                            "bindings": {}, "unallocated": ["alice", "bob"]}
    assert metadata(volume) == before
    assert not (volume / ".broker").exists()


def test_incomplete_serial_tree_cannot_be_omitted(volume):
    seed(volume)
    (volume / "u-unregistered").mkdir()
    before = metadata(volume)
    with pytest.raises(InventoryRefused, match="u-unregistered"):
        read(volume)
    assert metadata(volume) == before


def test_serial_symlink_refuses_without_reading_target(volume, tmp_path):
    seed(volume)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"outside")
    (volume / "u-alias").symlink_to(outside)
    before = metadata(outside)
    with pytest.raises(InventoryRefused, match="plain directory"):
        read(volume)
    assert metadata(outside) == before


def test_legacy_single_admin_resolves_but_multiple_admins_refuse(volume):
    seed(volume)
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        db.execute("DELETE FROM founder_home WHERE universe_id='alice'")
        db.execute("INSERT INTO universe_acl VALUES ('alice','legacy-owner','admin')")
    assert read(volume)["principals"]["alice"] == "legacy-owner"
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        db.execute("INSERT INTO universe_acl VALUES ('alice','second-admin','admin')")
    with pytest.raises(InventoryRefused, match="ambiguous"):
        read(volume)


def test_competing_home_bindings_refuse_instead_of_selecting_first(volume):
    seed(volume)
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        db.execute("INSERT INTO founder_home VALUES ('another-owner','alice')")
    with pytest.raises(InventoryRefused, match="ambiguous"):
        read(volume)


def test_authority_view_is_not_executed_as_a_table(volume):
    seed(volume)
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        db.executescript("DROP TABLE founder_home; CREATE VIEW founder_home AS "
                         "SELECT 'forged' AS founder_sub, 'alice' AS universe_id;")
    with pytest.raises(InventoryRefused, match="stored table"):
        read(volume)


def reserve(root, records):
    state = root / ".broker/state"
    state.mkdir(parents=True, mode=0o700)
    os.chown(state, 1002, 1101)
    path = state / "owner-identities.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE owner_identities(principal TEXT,machine_id INTEGER)")
        db.executemany("INSERT INTO owner_identities VALUES (?,?)", records)
    path.chmod(0o600)
    os.chown(path, 1002, 1002)


def test_existing_reservations_are_read_without_reallocation(volume):
    seed(volume)
    reserve(volume, [("retired-owner", 300001), ("alice", 300002)])
    before = metadata(volume)
    result = read(volume)
    assert result["bindings"] == {"alice": 300002}
    assert result["unallocated"] == ["bob"]
    assert metadata(volume) == before


@pytest.mark.parametrize("records", [
    [("alice", 300000)], [("alice", 400000)],
    [("alice", 300001), ("bob", 300001)], [("alice", 300001), ("alice", 300002)],
])
def test_invalid_identity_maps_never_become_fresh_maps(volume, records):
    seed(volume)
    reserve(volume, records)
    before = metadata(volume)
    with pytest.raises(InventoryRefused, match="invalid durable"):
        read(volume)
    assert metadata(volume) == before


def test_missing_map_after_any_owner_migration_refuses(volume):
    seed(volume)
    marker = volume / ".layout.json"
    document = json.loads(marker.read_text())
    document["roles"] = {"owners": {"direction": "reverse", "state": "stable"}}
    marker.write_text(json.dumps(document))
    with pytest.raises(InventoryRefused, match="permanent identity map is missing"):
        read(volume)


@pytest.mark.parametrize("records", [[], [("another-owner", 300001), ("bob", 300002)]])
def test_present_map_cannot_lose_or_reassign_journaled_reservations(volume, records):
    seed(volume)
    for center in ("alice", "bob"):
        os.chown(volume / center / "universe.json", 1001, 1001)
    role_owner_migration.migrate(
        volume, bindings={"alice": 300001, "bob": 300002},
        work={"alice": ["work"], "bob": ["work"]},
    )
    role_owner_migration.migrate(
        volume, bindings={"alice": 300001, "bob": 300002},
        work={"alice": ["work"], "bob": ["work"]}, reverse=True,
    )
    reserve(volume, records)
    before = metadata(volume)
    with pytest.raises(InventoryRefused, match="reservation|authority changed"):
        read(volume)
    assert metadata(volume) == before
