"""Private bytes remain in erasure inventory across interrupted import writes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.test_okf_transfer import archive
from tinyassets import command_center_packages as packages
from tinyassets import storage_accounting as accounting
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


def test_unreviewable_recompression_refuses_before_custody(custody, monkeypatch):
    import io
    import zipfile

    from tinyassets.okf import import_activation
    from tinyassets.okf.bundle import InvalidBundle
    from tinyassets.okf.transfer import Limits

    base, _, _, _ = custody
    block = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(1280))
    raw = archive({"workspace/long-range": block * 3})
    alternate = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(
        alternate, "w", compression=zipfile.ZIP_LZMA,
    ) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
    limits = Limits(compressed=60000, expanded=150000)
    bundle = ingest_zip(alternate.getvalue(), limits=limits)
    assert len(alternate.getvalue()) < limits.compressed < len(raw)
    monkeypatch.setattr(
        import_activation, "ingest_zip", lambda data: ingest_zip(data, limits=limits),
    )
    with pytest.raises(InvalidBundle, match="Compressed"):
        packages.quarantine_import(base, owner=OWNER, command_center=HOME, epoch=1, bundle=bundle)
    assert counts(base) == (0, 0)


def test_peer_quarantine_reference_retains_interrupted_bytes(custody):
    base, _, blob, sha = custody
    import_bundle(custody)
    with packages._db(base) as conn:
        conn.execute("INSERT INTO bundle_imports VALUES (?,?,?,?,?,'quarantined',?)",
                     ("peer-interrupted", PEER, HOME, 1, sha, time.time()))
    erase(base)
    assert counts(base) == (1, 0)
    assert packages.read_blob(base, sha) == blob
    assert packages.measure_packages(base, [PEER]) == len(blob)
    packages.erase_private_imports(base, owner=PEER)
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
    assert packages.measure_packages(base, [OWNER]) == len(blob)
    if retention == "peer":
        assert packages.measure_packages(base, [PEER]) == len(blob)
        assert packages.measure_packages(base, [OWNER, PEER]) == 2 * len(blob)
    erase(base)
    assert counts(base) == (0, 1)
    assert packages.read_blob(base, sha) == blob
    assert packages.measure_packages(base, [OWNER]) == (len(blob) if retention == "public" else 0)


def test_failed_ownership_is_charged_and_retry_needs_no_second_allocation(custody, monkeypatch):
    base, _, blob, _ = custody
    with monkeypatch.context() as patch:
        fail_at(base, "ownership", patch)
        for _ in range(2):
            with pytest.raises(sqlite3.IntegrityError, match="ownership write failed"):
                import_bundle(custody)
            usage = accounting.usage(base, OWNER)
            assert usage.used_bytes >= len(blob)
            assert usage.reserved_bytes == 0
    assert counts(base) == (2, 0)
    assert accounting.measure(base, OWNER, "packages") == len(blob)
    assert accounting.measure(base, PEER, "packages") == 0
    before = accounting.usage(base, OWNER).used_bytes
    # There is less than a second allocation free. A genuinely new blob cannot
    # fit, but retrying these already retained bytes must still succeed.
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str((before + len(blob) - 1) / 1024**3))
    with pytest.raises(accounting.StorageRefused):
        accounting.reserve(base, account_id=OWNER, scope_id=OWNER,
                           store="packages", nbytes=len(blob))
    with packages._db(base) as conn:
        conn.execute("DROP TRIGGER reject_owner")
    import_bundle(custody)
    assert accounting.usage(base, OWNER).used_bytes == before
    assert accounting.measure(base, OWNER, "packages") == len(blob)
    assert counts(base) == (3, 1)
    erase(base)
    assert accounting.measure(base, OWNER, "packages") == 0


def test_failed_write_before_bytes_has_no_phantom_charge(custody, monkeypatch):
    base, _, _, _ = custody
    with monkeypatch.context() as patch:
        fail_at(base, "before_file", patch)
        with pytest.raises(InterruptedImport):
            import_bundle(custody)
    assert counts(base) == (1, 0)
    assert accounting.usage(base, OWNER).committed_bytes == 0
    assert accounting.measure(base, OWNER, "packages") == 0


def test_process_exit_before_ownership_remains_charged_after_reservation_expiry(custody):
    base, _, blob, sha = custody
    script = r'''
import os, sys
from tests.test_okf_transfer import archive
from tinyassets import command_center_packages as packages, universe_files
from tinyassets.okf.transfer import ingest_zip
write = universe_files.write_data_path
def interrupted(*args, **kwargs):
    write(*args, **kwargs)
    os._exit(42)
universe_files.write_data_path = interrupted
packages.quarantine_import(sys.argv[1], owner=sys.argv[2], command_center=sys.argv[3],
    epoch=1, bundle=ingest_zip(archive({'workspace/note': b'private interrupted import'})))
'''
    result = subprocess.run([sys.executable, "-c", script, str(base), OWNER, HOME],
                            capture_output=True, timeout=15)
    assert result.returncode == 42, result.stderr.decode()
    assert counts(base) == (1, 0)
    assert packages.read_blob(base, sha) == blob
    assert accounting.usage(base, OWNER).reserved_bytes == len(blob)
    with sqlite3.connect(accounting.ledger_path(base)) as conn:
        conn.execute("UPDATE pending SET created_at=? WHERE account_id=?",
                     (time.time() - accounting.RESERVED_TTL_S - 1, OWNER))
    assert accounting.measure(base, OWNER, "packages") == len(blob)
    usage = accounting.usage(base, OWNER)
    assert usage.reserved_bytes == 0
    assert usage.used_bytes >= len(blob)
    erase(base)
    assert accounting.measure(base, OWNER, "packages") == 0


@pytest.mark.parametrize("failure", ["unreadable", "corrupt"])
def test_retained_bytes_measurement_fails_loudly(custody, monkeypatch, failure):
    base, _, _, sha = custody
    with monkeypatch.context() as patch:
        fail_at(base, "ownership", patch)
        with pytest.raises(sqlite3.IntegrityError):
            import_bundle(custody)
    if failure == "unreadable":
        def refused(*args, **kwargs):
            raise PermissionError("retained file unreadable")
        monkeypatch.setattr(packages, "read_data_path", refused)
        expected = PermissionError
    else:
        universe_files.write_data_path(packages._blob_path(base, sha), b"corrupt")
        expected = packages.PackageError
    with pytest.raises(expected):
        accounting.measure(base, OWNER, "packages")
    assert accounting.usage(base, OWNER).committed_bytes > 0


def test_failed_import_keeps_original_error_and_charge_when_verification_fails(
    custody, monkeypatch,
):
    base, _, blob, _ = custody
    with monkeypatch.context() as patch:
        fail_at(base, "ownership", patch)
        def refused(*args, **kwargs):
            raise PermissionError("retained file unreadable")
        patch.setattr(packages, "read_data_path", refused)
        with pytest.raises(sqlite3.IntegrityError, match="ownership write failed") as caught:
            import_bundle(custody)
    assert any("could not be verified" in note for note in caught.value.__notes__)
    assert accounting.usage(base, OWNER).committed_bytes == len(blob)
