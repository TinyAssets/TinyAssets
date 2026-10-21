from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tinyassets import workspace_fs as fs
from tinyassets.universe_files import (
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


