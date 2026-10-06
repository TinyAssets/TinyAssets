"""Host backup keeps the relocated ledger in its strict SQLite tier."""
import json
import os
import sqlite3
import stat
import tarfile

import pytest

from tests.test_backup_script import (
    RESTORE_SH,
    _backup_archive,
    _fake_restore_bin,
    _restore_env,
    _run,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="host backup runs on Linux")


@pytest.fixture(autouse=True)
def no_offsite_upload(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)


@pytest.mark.parametrize("relocated", [False, True])
def test_strict_backup_contains_latest_wal_rows_at_original_relative_path(tmp_path, relocated):
    source = tmp_path / "source-volume" / "_data"
    source.mkdir(parents=True)
    (source / ".layout.json").write_text(json.dumps({"state": "stable"}))
    parent = source / ".broker" if relocated else source
    if relocated:
        parent.mkdir(mode=0o2700)
        if os.geteuid() == 0:
            os.chown(parent, 1002, 1101)
        parent.chmod(0o2700)
    ledger = parent / "outbound.db"
    with sqlite3.connect(ledger) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE proof (value TEXT)")
        conn.execute("INSERT INTO proof VALUES ('committed-in-wal')")
        conn.commit()
        if relocated and os.geteuid() == 0:
            os.chown(ledger, 1002, 1101)
        ledger.chmod(0o600)
        assert ledger.with_name("outbound.db-wal").stat().st_size > 0
        full = _backup_archive(tmp_path, source)
        brain = next(full.parent.glob("tinyassets-brain-*.tar.gz"))
        relative = ".broker/outbound.db" if relocated else "outbound.db"
        restored = tmp_path / "restored.db"
        with tarfile.open(brain) as archive:
            assert "." not in archive.getnames(), "brain repair would overwrite volume-root modes"
            member = archive.getmember("./" + relative)
            assert member.mode == 0o600
            assert (member.uid, member.gid) == (ledger.stat().st_uid, ledger.stat().st_gid)
            restored.write_bytes(archive.extractfile(member).read())
            if relocated:
                directory = archive.getmember("./.broker")
                assert directory.mode == stat.S_IMODE(parent.stat().st_mode)
                assert (directory.uid, directory.gid) == (
                    parent.stat().st_uid, parent.stat().st_gid)
        with sqlite3.connect(restored) as saved:
            assert saved.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert saved.execute("SELECT value FROM proof").fetchall() == [("committed-in-wal",)]
        with tarfile.open(full) as archive:
            assert archive.getmember("_data/" + relative).mode == 0o600
        target = tmp_path / "target-volume" / "_data"
        target.mkdir(parents=True)
        fake_bin = _fake_restore_bin(tmp_path, target)
        result = _run(RESTORE_SH, _restore_env(tmp_path, target, full, fake_bin))
        assert result.returncode == 0, result.stdout + result.stderr
        recovered = target / relative
        assert (recovered.stat().st_uid, recovered.stat().st_gid,
                stat.S_IMODE(recovered.stat().st_mode)) == (
                    ledger.stat().st_uid, ledger.stat().st_gid, 0o600)
        with sqlite3.connect(recovered) as saved:
            assert saved.execute("SELECT value FROM proof").fetchall() == [("committed-in-wal",)]


@pytest.mark.parametrize("plant", ["parent-link", "file-link", "hardlink", "fifo", "corrupt"])
def test_unsafe_broker_backup_fails_before_upload_without_changing_outside(tmp_path, plant):
    source = tmp_path / "source-volume" / "_data"
    source.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "outbound.db"
    target.write_bytes(b"outside-sentinel")
    before = target.stat()
    broker = source / ".broker"
    if plant == "parent-link":
        broker.symlink_to(outside, target_is_directory=True)
    else:
        broker.mkdir()
        ledger = broker / "outbound.db"
        if plant == "file-link":
            ledger.symlink_to(target)
        elif plant == "hardlink":
            os.link(target, ledger)
        elif plant == "fifo":
            os.mkfifo(ledger)
        else:
            ledger.write_bytes(b"not-sqlite")
    with pytest.raises(AssertionError, match="backup.sh failed"):
        _backup_archive(tmp_path, source)
    assert not list((tmp_path / "remote").iterdir())
    after = target.stat()
    assert target.read_bytes() == b"outside-sentinel"
    assert (before.st_uid, before.st_gid, before.st_mode, before.st_mtime_ns) == (
        after.st_uid, after.st_gid, after.st_mode, after.st_mtime_ns)
