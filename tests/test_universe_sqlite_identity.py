"""A per-universe database's name must not steer the daemon into another one.

The threat is named verbatim in ``providers/provider_jail.py``: the daemon
reads and writes a universe's hidden databases from OUTSIDE the jail, so "a
provider that could replace one with a link (``.runs.db ->
/data/<other>/.runs.db``) would steer the daemon's own ``sqlite3.connect`` into
another universe."

It understates it. Measured in the Linux oracle on 2026-10-03, the daemon did
not merely read the other database: it COMMITTED A ROW into it. These tests are
that measurement, kept.

What is NOT claimed here, and cannot be: a no-follow guarantee for SQLite.
Python's ``sqlite3`` takes a path, so ``SQLITE_OPEN_NOFOLLOW`` (a C open flag)
is unreachable, a ``?nofollow=1`` URI parameter is silently ignored, and
``/proc/self/fd/<n>`` is resolved as a path -- after a swap it reads
``".../x.db (deleted)"`` and SQLite creates a new empty database under that
literal name. ``connect_guarded`` is detection in a narrow window; the
structural closures are recorded in its docstring and in
``docs/concerns/2026-10-03-a-universe-database-name-steers-the-daemon.md``.
"""
from __future__ import annotations

import errno
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tinyassets import workspace_fs as fs
from tinyassets.universe_files import (
    SqliteIdentityChanged,
    UniverseFileError,
    connect_guarded,
)

OURS = "OURS"
ANOTHER = "ANOTHER-UNIVERSE"


def _seed(path: Path, mark: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE t (x TEXT)")
        con.execute("INSERT INTO t VALUES (?)", (mark,))
        con.commit()
    finally:
        con.close()


#: The identity comparison is POSIX-only: on a non-POSIX host
#: ``connect_guarded`` takes the check-then-use branch and returns the factory
#: result without comparing, so a test that expects ``SqliteIdentityChanged``
#: would fail on a Windows host that CAN make symlinks rather than skip.
posix_only = pytest.mark.skipif(
    not getattr(fs, "_POSIX", False),
    reason="the identity comparison is POSIX-only; the Windows branch is check-then-use",
)


def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except NotImplementedError:
        pytest.skip("this host cannot create a symlink")
    except OSError as exc:
        # Only a privilege/support refusal is a skip. Anything else -- a full
        # disk, too many links -- is a real failure and must not read as
        # "this host cannot create a symlink".
        if exc.errno in (errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EINVAL):
            pytest.skip(f"this host cannot create a symlink ({exc.errno})")
        raise


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "data"
    (base / "u-alpha").mkdir(parents=True)
    (base / "u-bravo").mkdir(parents=True)
    _seed(base / "u-bravo" / ".runs.db", ANOTHER)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    return base


def _open_ro(db: Path):
    return connect_guarded(
        db, lambda: sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.2))


def test_a_planted_link_at_the_database_name_is_refused(data: Path):
    """The whole attack in one line: alpha's .runs.db is a link to bravo's."""
    alpha_db = data / "u-alpha" / ".runs.db"
    _link(data / "u-bravo" / ".runs.db", alpha_db)

    with pytest.raises(UniverseFileError):
        _open_ro(alpha_db)


@posix_only
def test_a_swap_after_the_check_is_refused_before_any_row_is_read(data: Path):
    """The TOCTOU the old check-then-use pattern lost.

    The swap happens between our verified open and SQLite's, which is exactly
    the window a name-based ``connect`` cannot see. The refusal must arrive
    before any statement runs, so no row of another universe is exposed.
    """
    alpha_dir = data / "u-alpha"
    alpha_db = alpha_dir / ".runs.db"
    _seed(alpha_db, OURS)
    bravo_db = data / "u-bravo" / ".runs.db"

    def swap_then_connect():
        # Precisely the race: a same-uid universe process renames a link over
        # the name we just verified.
        _link(bravo_db, alpha_dir / ".tmp")
        os.replace(alpha_dir / ".tmp", alpha_db)
        return sqlite3.connect(alpha_db.as_uri() + "?mode=ro", uri=True, timeout=0.2)

    with pytest.raises(SqliteIdentityChanged):
        connect_guarded(alpha_db, swap_then_connect)

    # And nothing of bravo's was read or written.
    con = sqlite3.connect(bravo_db)
    try:
        assert con.execute("SELECT x FROM t").fetchall() == [(ANOTHER,)]
    finally:
        con.close()


@posix_only
def test_the_refused_connection_is_closed_not_leaked(data: Path):
    """A refusal must not leak the connection it just opened.

    This suite already exhausts file descriptors
    (docs/concerns/2026-10-03-full-suite-cannot-report-its-own-result.md), so a
    guard that raises while holding one makes that worse. The first draft of
    this guard was a context manager and did exactly that.
    """
    alpha_dir = data / "u-alpha"
    alpha_db = alpha_dir / ".runs.db"
    _seed(alpha_db, OURS)
    opened: list[sqlite3.Connection] = []

    def swap_then_connect():
        _link(data / "u-bravo" / ".runs.db", alpha_dir / ".tmp")
        os.replace(alpha_dir / ".tmp", alpha_db)
        con = sqlite3.connect(alpha_db.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        opened.append(con)
        return con

    with pytest.raises(SqliteIdentityChanged):
        connect_guarded(alpha_db, swap_then_connect)

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):  # closed
        opened[0].execute("SELECT 1")


@posix_only
def test_an_aba_swap_is_NOT_detected_and_that_is_the_documented_limit(data: Path):
    """The counterexample, pinned so nobody re-reads the guard as airtight.

    Pin A; the attacker keeps A and puts a link to B at the name; SQLite opens
    B; the attacker restores A before the final stat. The comparison passes and
    the connection still addresses B. Step 3 follows links and sees only the
    name's current target -- it never learns SQLite's descriptor, and cannot.

    This test asserting the MISS is deliberate. The defect this whole change
    exists to fix was a guard described more strongly than it behaved; a test
    that documents the hole is how the description stays honest. If someone
    later closes this properly, this test should fail and be deleted with a
    note saying which closure landed.
    """
    alpha_dir = data / "u-alpha"
    alpha_db = alpha_dir / ".runs.db"
    _seed(alpha_db, OURS)
    bravo_db = data / "u-bravo" / ".runs.db"
    kept = alpha_dir / ".kept-a"

    def aba_then_connect():
        os.replace(alpha_db, kept)             # keep A
        _link(bravo_db, alpha_db)              # point the name at B
        con = sqlite3.connect(alpha_db.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        os.unlink(alpha_db)                    # drop the link
        os.replace(kept, alpha_db)             # restore A before the stat
        return con

    con = connect_guarded(alpha_db, aba_then_connect)
    try:
        # No refusal, and the connection is reading B -- the other command
        # center. This is the residual the docstring and the concern describe.
        assert con.execute("SELECT x FROM t").fetchall() == [(ANOTHER,)]
    finally:
        con.close()


def test_an_unswapped_open_is_returned_unchanged(data: Path):
    """The control: the guard is not simply refusing everything."""
    alpha_db = data / "u-alpha" / ".runs.db"
    _seed(alpha_db, OURS)

    con = _open_ro(alpha_db)
    try:
        assert con.execute("SELECT x FROM t").fetchall() == [(OURS,)]
    finally:
        con.close()


@posix_only
def test_pinning_does_not_cancel_another_connections_posix_locks(data: Path):
    """The guard must not become a corruption mechanism while preventing one.

    Closing an ordinary descriptor on an inode drops every POSIX advisory lock
    the PROCESS holds on it -- including another SQLite connection's, while
    SQLite still believes it holds them. SQLite documents this as a way to
    corrupt a database. The guard opens and closes a descriptor on every call,
    so it would have done exactly that with ``O_RDONLY``; it uses ``O_PATH``,
    which Linux excludes from that behaviour.

    The contender must be ANOTHER PROCESS. An in-process one is answered by
    SQLite's own per-inode bookkeeping without ever consulting the kernel, so
    it is refused either way -- the first version of this test did that and
    passed just as happily with the lock-cancelling flag, which made it worse
    than no test.
    """
    alpha_db = data / "u-alpha" / ".runs.db"
    _seed(alpha_db, OURS)

    contend = (
        "import sqlite3,sys\n"
        f"c=sqlite3.connect({str(alpha_db)!r},timeout=0.2)\n"
        "try:\n"
        "    c.execute('BEGIN IMMEDIATE')\n"
        "    print('TOOK-THE-LOCK')\n"
        "except sqlite3.OperationalError as e:\n"
        "    print('REFUSED')\n"
    )

    holder = sqlite3.connect(alpha_db, timeout=0.2)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("INSERT INTO t VALUES ('HELD')")

        # Sanity: while the holder has it, another process is refused.
        first = subprocess.run([sys.executable, "-c", contend],
                               capture_output=True, text=True, timeout=60)
        assert first.stdout.strip() == "REFUSED", first.stdout + first.stderr

        # Now the guard opens a descriptor on this very inode and closes it.
        con = connect_guarded(
            alpha_db,
            lambda: sqlite3.connect(alpha_db.as_uri() + "?mode=ro", uri=True, timeout=0.2))
        con.close()

        # With a lock-cancelling flag the holder's kernel locks are gone here
        # and the other process takes it -- the corruption window. With O_PATH
        # the holder keeps them.
        after = subprocess.run([sys.executable, "-c", contend],
                               capture_output=True, text=True, timeout=60)
        assert after.stdout.strip() == "REFUSED", (
            "the guard's close cancelled the holder's POSIX locks: "
            + after.stdout + after.stderr)

        holder.commit()
    finally:
        holder.close()

    con = sqlite3.connect(alpha_db)
    try:
        assert ("HELD",) in con.execute("SELECT x FROM t").fetchall()
    finally:
        con.close()


def test_a_path_outside_the_data_dir_is_opened_plainly(tmp_path: Path):
    """Nothing outside the data dir is in a universe, so there is nothing to
    steer and no reason to refuse."""
    db = tmp_path / "root-level.db"
    _seed(db, OURS)

    con = connect_guarded(db, lambda: sqlite3.connect(db))
    try:
        assert con.execute("SELECT x FROM t").fetchall() == [(OURS,)]
    finally:
        con.close()


def test_the_nofollow_uri_parameter_really_does_nothing(tmp_path: Path):
    """Kept as a tombstone: the mechanism #4330 proposed, disproved.

    If a future change reaches for ``?nofollow=1`` again, this says why not.
    """
    real, decoy = tmp_path / "real.db", tmp_path / "decoy.db"
    _seed(real, OURS)
    _seed(decoy, ANOTHER)
    link = tmp_path / "link.db"
    _link(decoy, link)

    con = sqlite3.connect(link.absolute().as_uri() + "?nofollow=1", uri=True)
    try:
        # It followed the link, and said nothing about it.
        assert con.execute("SELECT x FROM t").fetchall() == [(ANOTHER,)]
    finally:
        con.close()

    # An invented parameter is accepted just as silently, which is why nothing
    # ever surfaced the mistake.
    con = sqlite3.connect(link.absolute().as_uri() + "?not_a_real_parameter=1", uri=True)
    con.close()
