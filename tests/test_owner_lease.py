"""One owner commits per command center, and a successor is proven, not guessed.

Change ``execution-owner-lease`` slice B1. Process death is simulated the way the
kernel performs it: a tree member's lock is released (``OwnerTree.leave``), which
is exactly what happens to its descriptors when the process dies.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tinyassets import owner_lease
from tinyassets.owner_lease import (
    LeaseBusy,
    LeaseLost,
    OwnerTree,
    RestoreInProgress,
    acquire,
    key_for,
    using_tree,
)
from tinyassets.storage.owner_fence import advance_fence, check_fence, stored_fences

A, B = key_for("cc-a"), key_for("cc-b")


def _store(base: Path) -> Path:
    """An owner store with a fence table, registered in the catalog."""
    path = base / "owned.db"
    sqlite3.connect(path).close()
    owner_lease.register_store(base, path, "agent_turn_journal")
    return path


def _fenced_write(store: Path, lease) -> None:
    conn = sqlite3.connect(store, isolation_level=None)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        check_fence(conn, lease)
        conn.execute("INSERT INTO t VALUES ('x')")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


@pytest.fixture
def base(tmp_path):
    return tmp_path


@pytest.fixture
def owner(base):
    tree = OwnerTree.start(base)
    with using_tree(tree):
        yield tree
    tree.leave()


def test_first_use_takes_generation_one_and_rereading_keeps_it(base, owner):
    _store(base)
    first = acquire(base, A)
    assert first.generation == 1 and first.held()
    assert acquire(base, A).generation == 1
    assert first.verify(1, first.proof) and not first.verify(1, "0" * 64)
    assert not first.verify(2, first.proof)


def test_a_live_owner_is_never_displaced(base, owner):
    acquire(base, A)
    rival = OwnerTree.start(base)
    try:
        with using_tree(rival), pytest.raises(LeaseBusy):
            acquire(base, A, wait_s=0.2)
    finally:
        rival.leave()
    assert acquire(base, A).generation == 1


def test_a_dead_owner_is_succeeded_and_its_writes_are_fenced_off(base, owner):
    store = _store(base)
    old = acquire(base, A)
    _fenced_write(store, old)
    owner.leave()  # the old owner's whole tree dies
    heir = OwnerTree.start(base)
    try:
        with using_tree(heir):
            new = acquire(base, A, wait_s=1)
            assert new.generation == 2
            assert stored_fences(store)[A] == 2
            _fenced_write(store, new)
            # The old owner, resumed with what it believed, commits NOTHING.
            with pytest.raises(LeaseLost):
                _fenced_write(store, old)
            assert not old.held()
    finally:
        heir.leave()


def test_moving_one_command_center_does_not_fence_another(base, owner):
    store = _store(base)
    a = acquire(base, A)
    b = acquire(base, B)
    owner_lease.release(b)  # B goes idle and moves
    heir = OwnerTree.start(base)
    try:
        with using_tree(heir):
            assert acquire(base, B).generation == 2
        _fenced_write(store, a)  # A's owner keeps committing
        with pytest.raises(LeaseLost):
            _fenced_write(store, b)
    finally:
        heir.leave()


def test_a_voluntary_release_is_taken_without_any_death(base, owner):
    _store(base)
    lease = acquire(base, A)
    assert owner_lease.release(lease) is True
    assert owner_lease.release(lease) is False  # not twice
    heir = OwnerTree.start(base)
    try:
        with using_tree(heir):
            assert acquire(base, A, wait_s=0).generation == 2
    finally:
        heir.leave()


def test_a_member_that_joins_late_cannot_act_for_the_moved_key(base, owner):
    """Round-3 refute finding 1: the old tree gained a member after its death was
    proven. It joins, but the key has a living owner now, and its old generation
    is fenced off."""
    store = _store(base)
    old = acquire(base, A)
    owner.leave()
    heir = OwnerTree.start(base)
    try:
        with using_tree(heir):
            acquire(base, A, wait_s=1)
        late = OwnerTree(base, owner.tree_id).join()  # the delayed child
        try:
            with using_tree(late):
                # Its founder is dead, so it refuses outright; and its old
                # generation is fenced off even if it tried to write directly.
                with pytest.raises(LeaseLost):
                    acquire(base, A, wait_s=0.2)
                with pytest.raises(LeaseLost):
                    _fenced_write(store, old)
        finally:
            late.leave()
    finally:
        heir.leave()


def test_a_joining_member_blocks_the_death_proof(base, owner):
    """The gate: while someone is joining the tree, it is not provably dead."""
    acquire(base, A)
    owner.leave()
    gate = owner_lease._try_lock(base / owner_lease.TREE_DIR / owner.tree_id / ".gate")
    assert gate is not None
    try:
        assert owner_lease.tree_alive(base, owner.tree_id) is True
    finally:
        owner_lease._unlock(gate)
    assert owner_lease.tree_alive(base, owner.tree_id) is False


def test_an_unprovable_tree_blocks_it_is_never_read_as_dead(base, owner):
    acquire(base, A)
    with owner_lease.lease_db(base) as conn:
        conn.execute("UPDATE owner_lease SET holder_tree = ? WHERE owner_key = ?",
                     ("f" * 32, A))
    assert owner_lease.tree_alive(base, "f" * 32) is True
    with pytest.raises(LeaseBusy):
        acquire(base, A, wait_s=0.2)


def test_advance_fence_refuses_a_lost_lease_and_never_lowers(base, owner):
    store = _store(base)
    old = acquire(base, A)
    owner.leave()
    heir = OwnerTree.start(base)
    try:
        with using_tree(heir):
            acquire(base, A, wait_s=1)
        with pytest.raises(LeaseLost):
            advance_fence(store, old)
        assert stored_fences(store)[A] == 2
    finally:
        heir.leave()


# --- restore ----------------------------------------------------------------


def _restore():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import owner_lease_restore

    return owner_lease_restore


def test_an_interrupted_restore_fails_closed(base, owner):
    restore = _restore()
    restore.begin(base)
    with pytest.raises(RestoreInProgress):
        acquire(base, A)
    restore.finish(base, restore.high_water(base))
    assert acquire(base, A).generation == 1


def test_restore_high_water_includes_the_recovered_lease_and_every_store(base, owner):
    """Round-2 refute finding 3: a lease committed at 9 whose fences stayed at 8
    must not be restored to 8 and re-issued as 9."""
    from tinyassets.storage import DB_FILENAME

    journal_store = base / DB_FILENAME
    conn = sqlite3.connect(journal_store)
    conn.execute("CREATE TABLE owner_fence (owner_key TEXT PRIMARY KEY, generation INTEGER)")
    conn.execute("INSERT INTO owner_fence VALUES (?, 8)", (A,))
    conn.execute("CREATE TABLE agent_turns (universe_id TEXT, owner_generation INTEGER)")
    conn.execute("INSERT INTO agent_turns VALUES ('cc-b', 12)")
    conn.commit()
    conn.close()
    with owner_lease.lease_db(base) as lease_conn:
        lease_conn.execute(
            "INSERT INTO owner_lease VALUES (?, 9, ?, 'x', 'open', 'now', NULL)",
            (A, "e" * 32),
        )
    restore = _restore()
    manifest = restore.run(base)
    assert manifest["high_water"] == {A: 9, B: 12}
    assert acquire(base, A, wait_s=0).generation == 10
    assert acquire(base, B, wait_s=0).generation == 13


def test_every_store_kind_has_an_enumerator_that_finds_its_store(base):
    from tinyassets.owner_stores import FENCED
    from tinyassets.storage import DB_FILENAME

    for kind in set(FENCED.values()):
        assert kind in owner_lease.STORE_ENUMERATORS, kind
    (base / DB_FILENAME).write_bytes(b"")
    assert owner_lease.STORE_ENUMERATORS["agent_turn_journal"](base) == [base / DB_FILENAME]


# --- the tree reaches every executor the owner spawns -----------------------


def test_the_daemon_advertises_its_tree_to_children(base, monkeypatch):
    # Recorded first, so teardown restores the variable's absence even though
    # start_owner_tree writes os.environ directly.
    monkeypatch.setenv(owner_lease.TREE_ENV, "")
    tree = owner_lease.start_owner_tree(base)
    try:
        import os

        assert os.environ[owner_lease.TREE_ENV] == tree.tree_id
        # A child process inherits it and joins the SAME tree: alive while it is.
        child = subprocess.run(
            [sys.executable, "-c",
             "import os, sys; from tinyassets import owner_lease; "
             "t = owner_lease.current_tree(sys.argv[1]); "
             "print(t.tree_id == os.environ[owner_lease.TREE_ENV])", str(base)],
            capture_output=True, text=True, timeout=60,
        )
        assert child.stdout.strip() == "True", child.stderr
    finally:
        tree.leave()


# --- B1 code review round 1 -------------------------------------------------


def test_a_child_acts_only_while_its_founder_lives_and_never_succeeds_a_dead_owner(base):
    """Finding 2: an inherited tree pins a child to the owner that spawned it."""
    founder = OwnerTree.start(base)
    child = OwnerTree(base, founder.tree_id).join()
    try:
        with using_tree(child):
            assert child.founder_alive() is True
            assert acquire(base, A).generation == 1  # acting for a living founder
        founder.leave()  # the daemon dies; the child lingers
        with using_tree(child), pytest.raises(LeaseLost):
            acquire(base, A)
        # And a child of a NEW owner never takes a key by death recovery.
        heir = OwnerTree.start(base)
        heir_child = OwnerTree(base, heir.tree_id).join()
        try:
            child.leave()  # the old tree is now entirely dead
            with using_tree(heir_child), pytest.raises(LeaseBusy):
                acquire(base, A, wait_s=0.2)
            with using_tree(heir):
                assert acquire(base, A, wait_s=1).generation == 2
        finally:
            heir_child.leave()
            heir.leave()
    finally:
        child.leave()
        founder.leave()


def test_join_inherited_tree_refuses_when_the_founder_is_gone(base, monkeypatch):
    founder = OwnerTree.start(base)
    monkeypatch.setenv(owner_lease.TREE_ENV, founder.tree_id)
    monkeypatch.setattr(owner_lease, "_trees", {})
    assert owner_lease.join_inherited_tree(base).tree_id == founder.tree_id
    monkeypatch.setattr(owner_lease, "_trees", {})
    founder.leave()
    with pytest.raises(LeaseLost):
        owner_lease.join_inherited_tree(base)
    monkeypatch.delenv(owner_lease.TREE_ENV)
    assert owner_lease.join_inherited_tree(base) is None


def test_the_daemon_starts_its_tree_before_spawning_any_executor():
    """Finding 1: main() must advertise the tree before engines or the consumer."""
    from tinyassets import universe_server

    source = Path(universe_server.__file__).read_text(encoding="utf-8")
    main = source[source.index("def main("):]
    tree = main.index("start_owner_tree(")
    for spawn in ("start_engine_mcp_http_servers()", "assigned_consumer.start()",
                  "start_run_owner_watcher()", 'mcp.run(transport="sse"'):
        assert tree < main.index(spawn), spawn


def test_restore_reads_cataloged_stores_too(base, owner):
    """Finding 5: a store only the catalog knows still lifts the high-water."""
    store = _store(base)  # registered, but not at an enumerated path
    conn = sqlite3.connect(store)
    conn.execute("CREATE TABLE owner_fence (owner_key TEXT PRIMARY KEY, generation INTEGER)")
    conn.execute("INSERT INTO owner_fence VALUES (?, 12)", (A,))
    conn.commit()
    conn.close()
    assert _restore().run(base)["high_water"] == {A: 12}


def test_restore_finds_consumers_by_mount_overlap_not_volume_name(tmp_path):
    """Finding 4: a bind of the restored path, or of its parent, is a consumer."""
    restore = _restore()
    data = tmp_path / "data"
    data.mkdir()
    inspected = [
        {"Id": "named", "Mounts": [{"Source": str(data), "Destination": "/data"}]},
        {"Id": "parent", "Mounts": [{"Source": str(tmp_path), "Destination": "/srv"}]},
        {"Id": "child", "Mounts": [{"Source": str(data / "u1"), "Destination": "/u"}]},
        {"Id": "elsewhere", "Mounts": [{"Source": str(tmp_path / "other")}]},
        {"Id": "none", "Mounts": []},
    ]
    assert restore.consumers_of(data, inspected) == ["named", "parent", "child"]


@pytest.mark.skipif(not hasattr(__import__("os"), "fork"), reason="POSIX fork only")
def test_a_forked_child_inherits_no_membership_and_leaves_the_parents_intact(base):
    """Finding 8: the at-fork hook drops the inherited member descriptor WITHOUT
    unlocking it (an flock belongs to the shared open file description), so the
    parent stays a member while the child is not one."""
    import os

    tree = OwnerTree.start(base)
    with using_tree(tree):
        pid = os.fork()
        if pid == 0:  # the child: the hook already ran
            code = 0 if not owner_lease._trees else 1
            os._exit(code)
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0, "the child kept the parent's tree registry"
        assert owner_lease.tree_alive(base, tree.tree_id) is True, (
            "the child's exit released the parent's membership")
    tree.leave()


def test_a_restarted_daemon_recovers_idle_keys_before_its_children_need_them(base, monkeypatch):
    """Round-2 finding 2: the previous daemon finished its turns but left its key
    open. Reconcile skips it (nothing progressing), and a child cannot succeed a
    dead owner -- so the founder takes it at start, before spawning anything."""
    old = OwnerTree.start(base)
    with using_tree(old):
        acquire(base, A)
    old.leave()  # exits with the key still open
    monkeypatch.setenv(owner_lease.TREE_ENV, "")
    monkeypatch.setattr(owner_lease, "_trees", {})
    daemon = owner_lease.start_owner_tree(base)
    try:
        assert owner_lease.held_generation(base, A) == (2, daemon.tree_id)
        child = OwnerTree(base, daemon.tree_id).join()
        try:
            with using_tree(child):
                assert acquire(base, A, wait_s=0).generation == 2
        finally:
            child.leave()
    finally:
        daemon.leave()


def test_a_live_holder_is_not_recovered(base, owner, monkeypatch):
    acquire(base, A)
    monkeypatch.setenv(owner_lease.TREE_ENV, "")
    monkeypatch.setattr(owner_lease, "_trees", {})
    second = owner_lease.start_owner_tree(base)
    try:
        assert owner_lease.held_generation(base, A) == (1, owner.tree_id)
    finally:
        second.leave()


def test_tree_files_live_only_at_the_data_root(base):
    """The reason ``owner_lease``'s raw file ops are pinned, not routed.

    ``tests/test_universe_path_io_guard.py`` lets a module keep raw I/O only
    where no universe can plant a link on the path. Every file this module
    touches is a registered data-root platform entry, so the claim has to stay
    true as the module changes: a universe folder sitting beside them is never
    written into, and the names stay classified in ``ROOT_ENTRIES``.

    Scope, so it is not read as more than it is: this proves where the files
    PERSIST for this invocation, not that a future caller cannot hand
    ``base_path`` a universe directory. Nothing stops that but the callers
    (``storage/agent_turn_journal.py``, ``agent_turn_reconcile.py``), which
    pass the ledger base.
    """
    from tinyassets import storage_accounting

    assert "platform" in storage_accounting.ROOT_ENTRIES[owner_lease.TREE_DIR]
    assert "platform" in storage_accounting.ROOT_ENTRIES[owner_lease.LEASE_DB_NAME]

    universe = base / "u-someone"
    universe.mkdir()
    before = set(base.rglob("*"))

    founder = OwnerTree.start(base)
    try:
        assert founder.founder_alive() is True  # the founder short-circuits
        member = OwnerTree(base, founder.tree_id).join()
        try:
            # Only a NON-founder member reads the `founder` file, which is the
            # .read_text() the guard pins; the founder returns self.alive.
            assert member.founder is False
            assert member.founder_alive() is True
        finally:
            member.leave()
        acquire(base, A)
    finally:
        founder.leave()

    for path in set(base.rglob("*")) - before:
        top = path.relative_to(base).parts[0]
        assert top == owner_lease.TREE_DIR or top.startswith(owner_lease.LEASE_DB_NAME), (
            f"owner_lease wrote outside its registered data-root entries: {path}")
    assert not list(universe.rglob("*")), "a universe folder was written into"
