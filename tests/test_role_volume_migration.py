"""Complete stopped-volume orchestration under a single layout lock."""
# ruff: noqa: F811 -- shared Linux root fixture
import json
import os
import sqlite3

import pytest

from deploy import role_egress_migration as egress
from deploy import role_metadata_migration as protected
from deploy import role_owner_migration as owner
from deploy import role_volume_inventory as inventory
from deploy import role_volume_migration as migration
from tests.test_role_owner_migration import metadata, volume  # noqa: F401
from tests.test_role_volume_inventory import reserve, seed
from tinyassets import role_modes


def setup(root):
    seed(root)
    reserve(root, [("alice", 300001), ("bob", 300002)])
    os.chown(root / ".broker", 1002, 1101)
    (root / ".broker").chmod(0o2700)
    for center in ("alice", "bob"):
        os.chown(root / center / "universe.json", 1001, 1001)
    with sqlite3.connect(root / "outbound.db") as db:
        db.execute("CREATE TABLE sentinel(value TEXT)")
        db.execute("INSERT INTO sentinel VALUES ('retained')")
    os.chown(root / "outbound.db", 1001, 1001)
    (root / "outbound.db").chmod(0o600)


def run(root, **kwargs):
    return migration.migrate(root, owner=vars(owner), egress=vars(egress),
                             metadata=vars(protected), inventory=vars(inventory),
                             modes=vars(role_modes), launch={}, **kwargs)


def test_full_dry_apply_repeat_reverse(volume):
    setup(volume)
    os.link(volume / "alice/work/payload", volume / "bob/work/alias")
    original = (volume / "alice/work/payload").stat().st_ino
    before = metadata(volume)
    run(volume, dry_run=True)
    assert metadata(volume) == before
    run(volume)
    assert json.loads((volume / ".layout.json").read_text())["state"] == "stable"
    for center, machine in (("alice", 300001), ("bob", 300002)):
        for name in ("work", "previews", "universe.json"):
            assert (volume / center / name).stat().st_uid == machine
    for name in ("alice/work/payload", "bob/work/alias"):
        path = volume / owner.STATE / "quarantine" / name
        assert path.stat().st_ino == original
        assert path.read_bytes() == b"alice"
    stable = metadata(volume)
    run(volume)
    assert metadata(volume) == stable
    before = metadata(volume)
    run(volume, reverse=True, dry_run=True)
    assert metadata(volume) == before
    run(volume, reverse=True)
    assert (volume / "outbound.db").stat().st_uid == 1001
    with sqlite3.connect(volume / "outbound.db") as db:
        assert db.execute("SELECT value FROM sentinel").fetchall() == [("retained",)]
    assert (volume / "alice/work/execute").stat().st_uid == 1001
    stable = metadata(volume)
    run(volume, reverse=True)
    assert metadata(volume) == stable


@pytest.mark.parametrize("boundary", ["volume-journal", "volume-egress",
                                      "volume-reservations", "volume-owners",
                                      "volume-complete", "volume-admit"])
@pytest.mark.parametrize("reverse", [False, True])
def test_full_crash_recovery(volume, boundary, reverse):
    setup(volume)
    if reverse:
        run(volume)

    def crash(step):
        if step == boundary:
            raise InterruptedError(step)

    with pytest.raises(InterruptedError, match=boundary):
        run(volume, reverse=reverse, after_step=crash)
    run(volume, reverse=reverse)
    assert json.loads((volume / ".layout.json").read_text())["state"] == "stable"
    stable = metadata(volume)
    run(volume, reverse=reverse)
    assert metadata(volume) == stable
