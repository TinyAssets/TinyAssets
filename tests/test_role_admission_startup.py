"""DA7 wired into U2's startup coordinator (Linux root oracle).

Every restart is ``role_volume_migration.migrate`` with the real admission
contract and a real retired broker child (UID1002) on the real log, then the
volume, metadata and owner phases. Runtime admission is reproduced at the
filesystem/log level DA4 leaves behind (canonical root label, admin grant,
``admit`` row); the production-image path through admit_center, the mapper and
bubblewrap is scripts/role_admission_startup_probe.py.
"""
# ruff: noqa: F811 -- shared Linux root fixture
import json
import os
import shutil
import sqlite3
import time
import traceback

import pytest

from deploy import role_admission_contract as contract
from deploy.role_owner_migration import STATE, MigrationRefused
from deploy.role_volume_inventory import InventoryRefused
from tests.test_role_owner_migration import volume  # noqa: F401
from tests.test_role_volume_migration import (  # noqa: F401
    admissions,
    broker,
    metadata,
    oracle_interpreter,
    run,
    setup,
)


def journal(volume):
    return json.loads((volume / STATE / "volume.json").read_text())


def label(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return contract.read_label(fd)
    finally:
        os.close(fd)


def reserve(volume, principal):
    return broker(volume, lambda db: db.resolve(principal, allocate=True).uid)


def publish(volume, principal, center, *, home=False, payload=b"owner bytes"):
    """What DA4 leaves before its log append: a canonically labelled root."""
    machine = reserve(volume, principal)
    root = volume / center
    root.mkdir(mode=0o750)
    os.chown(root, 1001, machine)
    os.chmod(root, 0o750)
    os.setxattr(root, contract.ACCESS, contract.canonical_root_acl(machine))
    (root / "previews").mkdir(mode=0o700)
    os.chown(root / "previews", 1001, 1001)
    (root / "notes.md").write_bytes(payload)  # an owner write after the bind
    os.chmod(root / "notes.md", 0o600)
    os.chown(root / "notes.md", machine, machine)
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        if home:
            db.execute("INSERT INTO founder_home VALUES (?,?)", (principal, center))
        else:
            db.execute("INSERT INTO universe_acl VALUES (?,?,'admin')", (center, principal))
    return machine


def admit(volume, principal, center, **kwargs):
    """A completed runtime admission: published, then the ``admit`` row."""
    machine = publish(volume, principal, center, **kwargs)
    generation = broker(volume, lambda db: db.admission("admit", principal, center).generation)
    return machine, generation


def legacy_center(volume, principal, center, *, home=True):
    """What the old single-UID image creates: 1001:1001, no label, no row."""
    root = volume / center
    root.mkdir(mode=0o755)
    (root / "universe.json").write_text("{}")
    (root / "draft.md").write_bytes(b"legacy bytes")
    for path in (root, root / "universe.json", root / "draft.md"):
        os.chown(path, 1001, 1001)
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        if home:
            db.execute("INSERT INTO founder_home VALUES (?,?)", (principal, center))
        else:
            db.execute("INSERT INTO universe_acl VALUES (?,?,'admin')", (center, principal))


def retire(volume, principal, center):
    """D218's DA6 row after both passes removed the tree (the passes are
    tests/test_role_owner_tree_deletion.py's)."""
    shutil.rmtree(volume / center)
    return broker(volume, lambda db: db.admission("retire", principal, center).generation)


def trees(volume, report):
    """Every path of every bound center, by its owner's machine identity."""
    found = {}

    def walk(path, machine):
        found.setdefault(machine, []).append(str(path))
        if not path.is_dir() or path.is_symlink():
            return
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            names = os.listdir(fd)
        finally:
            os.close(fd)
        for name in names:
            walk(path / name, machine)

    for center, machine in report["bindings"].items():
        walk(volume / center, machine)
    return found


def foreign(volume, report):
    """D59-style: each owner's real identity against every other owner's paths."""
    paths = trees(volume, report)
    totals = {"bytes": 0, "mutations": 0, "attempts": 0}
    for machine in sorted(paths):
        others = [p for owner, owned in paths.items() if owner != machine for p in owned]
        read, write = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read)
            try:
                os.setgroups([])
                os.setresgid(machine, machine, machine)
                os.setresuid(machine, machine, machine)
                got = {"bytes": 0, "mutations": 0, "attempts": 0}

                def attempt(action):
                    got["attempts"] += 1
                    try:
                        action()
                    except OSError:
                        return
                    got["mutations"] += 1

                for path in others:
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    except OSError:
                        pass
                    else:
                        try:
                            got["bytes"] += (len(os.listdir(fd)) if os.path.isdir(path)
                                             else len(os.read(fd, 1 << 20)))
                        except OSError:
                            pass
                        os.close(fd)
                    attempt(lambda p=path: os.chmod(p, 0o777))
                    attempt(lambda p=path: os.chown(p, machine, machine))
                    attempt(lambda p=path: os.rename(p, p + ".moved"))
                    attempt(lambda p=path: os.close(os.open(p, os.O_WRONLY | os.O_NOFOLLOW)))
                os.write(write, json.dumps(got).encode())
                os._exit(0)
            except BaseException:
                os.write(write, traceback.format_exc().encode())
                os._exit(1)
        os.close(write)
        with os.fdopen(read, "rb") as handle:
            raw = handle.read()
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0, raw.decode()
        for key, value in json.loads(raw).items():
            totals[key] += value
    return totals


def restart(volume, **kwargs):
    """One startup migration, then the foreign-bytes matrix on what it binds."""
    report = run(volume, **kwargs)
    if not kwargs.get("reverse"):
        result = foreign(volume, report)
        assert result["bytes"] == 0 and result["mutations"] == 0, result
    return report


def owners_view(volume, centers):
    return {k: v for k, v in metadata(volume).items()
            if any(f"/{center}" in k for center in centers)}


def test_reverse_after_runtime_admission(volume):
    setup(volume)
    first = restart(volume)
    assert [row[1:4] for row in admissions(volume)] == [
        ("admit", "alice", "alice"), ("admit", "bob", "bob")]
    assert first["generation"] == 2
    machine, generation = admit(volume, "carol", "carol-notes")
    kept = owners_view(volume, ["alice", "bob"])
    report = restart(volume)  # forward: the delta explains carol-notes
    assert report["principals"]["carol-notes"] == "carol" and report["generation"] == generation
    assert owners_view(volume, ["alice", "bob"]) == kept  # a lagging phase, no inode work
    assert label(volume / "carol-notes") == contract.canonical_label(machine)
    report = run(volume, reverse=True)
    assert report["principals"] == {"alice": "alice", "bob": "bob", "carol-notes": "carol"}
    for path in (volume / "carol-notes", volume / "carol-notes/notes.md"):
        info = path.lstat()
        assert (info.st_uid, info.st_gid) == (1001, 1001), path
        assert os.listxattr(path) == []  # the canonical label is dropped on reverse
    assert (volume / "carol-notes/notes.md").read_bytes() == b"owner bytes"
    assert journal(volume)["direction"] == "reverse"
    assert [row[1] for row in admissions(volume)].count("admit") == 3  # reverse appends nothing
    # Forward again: the existing row matches its owner, nothing is reseeded.
    report = restart(volume)
    assert report["admits"] == [] and report["generation"] == generation
    assert label(volume / "carol-notes") == contract.canonical_label(machine)


def test_forward_after_the_legacy_image_created_centers(volume):
    setup(volume)
    restart(volume)
    run(volume, reverse=True)
    # The old image runs: a new user signs up and alice creates a named center.
    legacy_center(volume, "dave", "u-01legacyhome")
    legacy_center(volume, "alice", "alice-legacy", home=False)
    before = admissions(volume)
    report = restart(volume)
    assert sorted(report["admits"]) == [["alice", "alice-legacy"], ["dave", "u-01legacyhome"]]
    seeded = admissions(volume)[len(before):]
    assert sorted(row[1:4] for row in seeded) == [
        ("admit", "alice", "alice-legacy"), ("admit", "dave", "u-01legacyhome")]
    assert report["generation"] == max(row[0] for row in seeded)
    dave = report["bindings"]["u-01legacyhome"]
    assert 300001 <= dave <= 399999 and dave not in (300001, 300002)  # allocated once
    assert label(volume / "u-01legacyhome") == contract.canonical_label(dave)
    assert (volume / "u-01legacyhome/draft.md").stat().st_uid == dave
    assert journal(volume)["generation"] == report["generation"]
    stable = metadata(volume)
    restart(volume)
    assert metadata(volume) == stable
    # A legacy deletion of a center the log admitted, then a forward: the log
    # is not authoritative after a reverse (no retire row could be written).
    run(volume, reverse=True)
    shutil.rmtree(volume / "alice-legacy")
    report = restart(volume)
    assert "alice-legacy" not in report["principals"] and report["missing"] == {}


def test_restart_matrix_signup_deletion_missing_and_empty(volume):
    setup(volume)
    report = restart(volume)
    # Signup and a named center between restarts.
    admit(volume, "erin", "u-01erinhome", home=True)
    admit(volume, "alice", "alice-second")
    # Deletion of bob's center (D218 wrote its retire row).
    retire(volume, "bob", "bob")
    report = restart(volume)
    assert report["principals"] == {"alice": "alice", "alice-second": "alice",
                                    "u-01erinhome": "erin"}
    assert report["missing"] == {} and report["alarms"] == []
    with pytest.raises(RuntimeError, match="never admitted again"):
        broker(volume, lambda db: db.admission("admit", "bob", "bob"))
    # F1 (b): a lost tree with no deletion goes to missing; everyone else starts.
    shutil.rmtree(volume / "alice-second")
    report = restart(volume)
    assert report["missing"] == {"alice-second": "alice"}
    assert set(report["bindings"]) == {"alice", "u-01erinhome"}
    concerns = list((volume / STATE / contract.CONCERNS).iterdir())
    assert [p.name for p in concerns] == [
        time.strftime("%Y-%m-%d", time.gmtime()) + "-missing-center-alice-second.md"]
    assert (concerns[0].stat().st_mode & 0o777, concerns[0].stat().st_uid) == (0o600, 0)
    assert journal(volume)["missing"] == {"alice-second": "alice"}
    report = restart(volume)  # re-checked at every restart
    assert report["missing"] == {"alice-second": "alice"} and len(report["alarms"]) == 1
    # D218 retires the missing center; the next restart drops it.
    broker(volume, lambda db: db.admission("retire", "alice", "alice-second").generation)
    report = restart(volume)
    assert report["missing"] == {} and report["alarms"] == []
    # Delete every center, restart on the empty set, sign up, restart.
    for principal, center in (("alice", "alice"), ("erin", "u-01erinhome")):
        retire(volume, principal, center)
    report = restart(volume)
    assert report["principals"] == {} and report["bindings"] == {}
    assert json.loads((volume / STATE / "journal.json").read_text())[
        "configuration"]["bindings"] == {}
    admit(volume, "fern", "u-01fernhome", home=True)
    report = restart(volume)
    assert report["principals"] == {"u-01fernhome": "fern"}
    assert report["generation"] == admissions(volume)[-1][0]


def test_orphans_staging_and_refusals(volume):
    setup(volume)
    restart(volume)
    # Crash between publish and the log append: adopted on the label check.
    publish(volume, "alice", "alice-orphan")
    # Crash before publish: an unpublished remnant in staging is swept.
    staging = volume / contract.STAGING
    (staging / "half").mkdir(parents=True)
    (staging / "half/partial").write_bytes(b"x")
    report = restart(volume)
    assert report["swept"] == 3 and not staging.exists()
    assert report["admits"] == [["alice", "alice-orphan"]]
    assert admissions(volume)[-1][1:4] == ("admit", "alice", "alice-orphan")
    # A tree the log does not explain and whose label is not canonical refuses
    # before any journal or inode change.
    legacy_center(volume, "mallory", "stray", home=False)
    before = metadata(volume)
    with pytest.raises(MigrationRefused, match="unexplained center tree: stray"):
        run(volume)
    assert metadata(volume) == before
    shutil.rmtree(volume / "stray")
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        db.execute("DELETE FROM universe_acl WHERE universe_id='stray'")
        # A changed owner refuses too (role_volume_inventory.reserved, kept).
        db.execute("UPDATE universe_acl SET actor_id='bob' WHERE universe_id='alice-orphan'")
    before = metadata(volume)
    with pytest.raises(InventoryRefused, match="authority changed .*: alice-orphan"):
        run(volume)
    assert metadata(volume) == before
    with sqlite3.connect(volume / ".tinyassets.db") as db:
        db.execute("UPDATE universe_acl SET actor_id='alice' WHERE universe_id='alice-orphan'")
    restart(volume)


@pytest.mark.parametrize("boundary", ["volume-journal", "volume-reservations",
                                      "volume-admissions", "volume-owners",
                                      "volume-complete"])
def test_an_interrupted_reconciliation_resumes_exactly(volume, boundary):
    setup(volume)
    restart(volume)
    publish(volume, "carol", "u-01carolhome", home=True)  # adopted at this restart

    def crash(step):
        if step == boundary:
            raise InterruptedError(step)

    with pytest.raises(InterruptedError, match=boundary):
        run(volume, after_step=crash)
    if boundary != "volume-complete":
        assert journal(volume)["state"] == "migrating"
        assert journal(volume)["admits"] == [["carol", "u-01carolhome"]]
    report = restart(volume)
    rows = [row[1:4] for row in admissions(volume)]
    assert rows.count(("admit", "carol", "u-01carolhome")) == 1  # idempotent append
    assert report["generation"] == admissions(volume)[-1][0]
    assert journal(volume) == {"direction": "forward", "state": "stable",
                               "principals": {"alice": "alice", "bob": "bob",
                                              "u-01carolhome": "carol"},
                               "missing": {}, "generation": report["generation"]}


@pytest.mark.parametrize("reverse", [False, True])
def test_a_deleted_centers_quarantine_escrow_is_carried(volume, reverse):
    """A retired center's quarantined cross-tree alias stays in root-private escrow."""
    setup(volume)
    os.link(volume / "alice/work/payload", volume / "bob/work/alias")
    restart(volume)
    escrow = volume / STATE / "quarantine"
    names = sorted(str(p.relative_to(escrow)) for p in escrow.rglob("*") if p.is_file())
    assert names == ["alice/work/payload", "bob/work/alias"]
    retire(volume, "alice", "alice")
    report = restart(volume, reverse=reverse) if not reverse else run(volume, reverse=True)
    assert report["principals"] == {"bob": "bob"}
    rows = json.loads((volume / STATE / "journal.json").read_text())["rows"]
    assert sorted(r["path"] for r in rows if r["kind"] == "quarantine") == names
    assert (escrow / "alice/work/payload").read_bytes() == b"alice"
