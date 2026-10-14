"""Private bytes remain in erasure inventory across interrupted import writes."""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from tests.test_okf_transfer import archive
from tinyassets import command_center_packages as packages
from tinyassets import universe_files
from tinyassets.account_deletion import _delete_satellite_rows
from tinyassets.daemon_server import grant_universe_ownership, set_founder_home
from tinyassets.okf.transfer import MANIFEST, ingest_zip

OWNER = "user_import_owner"
PEER = "user_import_peer"
HOME = "u-aaaaaaaaaaaaaaaa"


@pytest.fixture
def custody(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / HOME).mkdir()
    (tmp_path / HOME / "soul.md").write_text("# Import test", encoding="utf-8")
    grant_universe_ownership(tmp_path, universe_id=HOME, owner_id=OWNER)
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME,
                     platform_generated=True)
    bundle = ingest_zip(archive({"workspace/note": b"private interrupted import"}))
    blob = packages.build_blob(json.loads(bundle.files[MANIFEST]), {
        path: data for path, data in bundle.files.items() if path != MANIFEST
    })
    return tmp_path, bundle, blob, hashlib.sha256(blob).hexdigest()


def import_bundle(custody):
    base, bundle, _, _ = custody
    return packages.quarantine_import(
        base, owner=OWNER, command_center=HOME, epoch=1, bundle=bundle,
    )


def erase(base):
    _delete_satellite_rows(packages.database_path(base), principal=OWNER,
                           home=HOME, counts={}, label="packages")


def counts(base):
    with packages._db(base) as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("bundle_imports", "blobs"))


def test_inventory_insert_failure_cannot_leave_private_bytes(custody):
    base, _, _, sha = custody
    with packages._db(base) as conn:
        conn.execute(
            "CREATE TRIGGER reject_import BEFORE INSERT ON bundle_imports "
            "BEGIN SELECT RAISE(ABORT, 'inventory write failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="inventory write failed"):
        import_bundle(custody)
    assert counts(base) == (0, 0)
    assert not packages._blob_path(base, sha).exists()
    erase(base)
    assert not packages._blob_path(base, sha).exists()


class InterruptedImport(BaseException):
    """No application Exception handler can turn this into a successful import."""


def fail_at(base, point, patch):
    if point == "ownership":
        with packages._db(base) as conn:
            conn.execute(
                "CREATE TRIGGER reject_owner BEFORE INSERT ON blobs "
                "BEGIN SELECT RAISE(ABORT, 'ownership write failed'); END"
            )
        return
    if point == "after_custody":
        original = packages.store_blob

        def store(*args, **kwargs):
            original(*args, **kwargs)
            raise InterruptedImport()

        patch.setattr(packages, "store_blob", store)
        return
    original = universe_files.write_data_path

    def write(*args, **kwargs):
        if point == "after_file":
            original(*args, **kwargs)
        raise InterruptedImport()

    patch.setattr(universe_files, "write_data_path", write)


@pytest.mark.parametrize("point", ["before_file", "after_file", "ownership", "after_custody"])
@pytest.mark.parametrize("retry", [False, True])
def test_interrupted_custody_remains_erasable(custody, monkeypatch, point, retry):
    base, _, blob, sha = custody
    with monkeypatch.context() as patch:
        fail_at(base, point, patch)
        with pytest.raises((InterruptedImport, sqlite3.IntegrityError)):
            import_bundle(custody)
    assert counts(base) == (1, int(point == "after_custody"))
    assert packages._blob_path(base, sha).exists() == (point != "before_file")
    with packages._db(base) as conn:
        row = conn.execute("SELECT * FROM bundle_imports").fetchone()
        assert (row["owner_id"], row["command_center_id"], row["epoch"], row["state"]) == (
            OWNER, HOME, 1, "quarantined",
        )
        conn.execute("DROP TRIGGER IF EXISTS reject_owner")
    if retry:
        receipt = import_bundle(custody)
        assert receipt["sha256"] == sha
        assert packages.read_blob(base, sha) == blob
        assert counts(base) == (2, 1)
    erase(base)
    assert counts(base) == (0, 0)
    assert not packages._blob_path(base, sha).exists()


@pytest.mark.parametrize("retention", ["peer", "public"])
def test_failed_import_erasure_preserves_other_references(custody, monkeypatch, retention):
    base, _, blob, sha = custody
    with monkeypatch.context() as patch:
        fail_at(base, "after_file", patch)
        with pytest.raises(InterruptedImport):
            import_bundle(custody)
    packages.store_blob(base, author_id=PEER if retention == "peer" else OWNER, blob=blob)
    if retention == "public":
        packages.record_version(base, author_id=OWNER, name="Published", version=1,
                                sha256=sha)
    erase(base)
    assert counts(base) == (0, 1)
    assert packages.read_blob(base, sha) == blob
