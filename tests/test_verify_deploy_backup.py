"""The pre-move snapshot is restorable, checksummed, and from this deploy."""

from __future__ import annotations

import hashlib
import io
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import verify_deploy_backup as verify


def _archive(path: Path, payload: bytes = b"member\n") -> str:
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("inventory.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def test_a_fresh_archive_with_a_matching_digest_passes(tmp_path):
    name = "tinyassets-snapshot-2026-10-14T23-20-00Z.tar.gz"
    path = tmp_path / name
    digest = _archive(path)
    started = int(datetime(2026, 10, 14, 23, 19, tzinfo=timezone.utc).timestamp())
    assert verify.verify(path, started) == f"verified backup: {name} sha256:{digest}"


def test_a_backup_older_than_this_run_fails(tmp_path):
    path = tmp_path / "tinyassets-snapshot-2026-10-14T05-22-25Z.tar.gz"
    _archive(path)
    started = int(datetime(2026, 10, 14, 23, 20, tzinfo=timezone.utc).timestamp())
    with pytest.raises(SystemExit):
        verify.verify(path, started)


def test_a_corrupt_digest_fails(tmp_path):
    path = tmp_path / "tinyassets-snapshot-2026-10-14T23-20-00Z.tar.gz"
    _archive(path)
    path.write_bytes(path.read_bytes() + b"x")
    started = 0
    with pytest.raises(SystemExit):
        verify.verify(path, started)


def test_an_unreadable_tar_fails(tmp_path):
    path = tmp_path / "tinyassets-snapshot-2026-10-14T23-20-00Z.tar.gz"
    path.write_bytes(b"not a tar")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    with pytest.raises(SystemExit):
        verify.verify(path, 0)
