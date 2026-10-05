"""Real durable transactions for D60's cross-owner machine labels."""
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tinyassets.broker.owner_identities import OWNER_ID_LAST, OwnerIdentities

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX identity storage")


def store(tmp_path):
    tmp_path.chmod(0o700)
    return OwnerIdentities(tmp_path / "identities.db", initialize=True)


def test_parallel_allocation_restart_and_no_reuse(tmp_path):
    identities = store(tmp_path)
    names = ["alice", "bob", "alice", "charlie"] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda name: identities.resolve(name, allocate=True), names))
    assert len(set(values)) == 3
    restarted = OwnerIdentities(identities.path)
    for name, identity in zip(names, values):
        assert restarted.resolve(name) == identity
        assert identity.uid == identity.gid
    with sqlite3.connect(identities.path) as db:
        for statement in ("DELETE FROM owner_identities",
                          "UPDATE owner_identities SET machine_id=399999"):
            with pytest.raises(sqlite3.IntegrityError, match="permanent"):
                db.execute(statement)
    assert restarted.resolve("dave", allocate=True).uid == 300003


def test_missing_map_refused_and_lookup_does_not_allocate(tmp_path):
    identities = store(tmp_path)
    with pytest.raises(LookupError):
        identities.resolve("unknown")
    identities.path.unlink()
    with pytest.raises(sqlite3.OperationalError):
        identities.resolve("alice", allocate=True)
    with pytest.raises(FileNotFoundError):
        OwnerIdentities(identities.path)


def test_exhaustion_does_not_wrap(tmp_path):
    identities = store(tmp_path)
    with sqlite3.connect(identities.path) as db:
        db.execute("INSERT INTO owner_identities VALUES (?, ?)", ("last", OWNER_ID_LAST))
    with pytest.raises(RuntimeError, match="exhausted"):
        identities.resolve("next", allocate=True)
    assert identities.resolve("last").uid == OWNER_ID_LAST


def test_alias_and_public_parent_refused(tmp_path):
    identities = store(tmp_path)
    alias = tmp_path / "alias.db"
    alias.symlink_to(identities.path)
    with pytest.raises(OSError):
        OwnerIdentities(alias)
    alias.unlink()
    os.link(identities.path, alias)
    with pytest.raises(PermissionError):
        OwnerIdentities(alias)
    tmp_path.chmod(0o755)
    with pytest.raises(PermissionError):
        OwnerIdentities(identities.path)


def test_uncommitted_allocation_crash_leaves_no_published_identity(tmp_path):
    import subprocess
    import sys

    identities = store(tmp_path)
    first = identities.resolve("alice", allocate=True)
    result = subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('BEGIN IMMEDIATE')
db.execute('INSERT INTO owner_identities VALUES (?, ?)', ('unpublished', 300001))
os._exit(23)
""", str(identities.path)], check=False)
    assert result.returncode == 23
    restarted = OwnerIdentities(identities.path)
    assert restarted.resolve("alice") == first
    with pytest.raises(LookupError):
        restarted.resolve("unpublished")
    assert restarted.resolve("bob", allocate=True).uid == 300001
