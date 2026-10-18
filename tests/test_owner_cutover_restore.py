"""Restore today's paired owner-split snapshot after partial cutover writes.

Linux oracle proof: real tar, SQLite, file swaps and restore script; only host
service discovery and the XFS mount query are replaced for the disposable tree.
There are no quota writes when restoring the empty pre-cutover box store.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests.test_owner_cutover_census import _fingerprint, _sql
from tests.test_owner_cutover_census import source as source

REPO = Path(__file__).resolve().parents[1]


def _shim(directory, name, body):
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("interruption", ["copied_tree", "box_records", "removed_host_tree"])
def test_paired_restore_after_half_move_is_byte_exact(source, tmp_path, interruption):
    volume = tmp_path / "volume/_data"
    shutil.copytree(source, volume)
    store = tmp_path / "owners"
    store.mkdir()
    before = _fingerprint(volume)
    data_archive = tmp_path / "tinyassets-snapshot-2026-10-17T00-00-00Z.tar.gz"
    box_archive = tmp_path / "tinyassets-box-store-2026-10-17T00-00-00Z.tar.gz"
    with tarfile.open(data_archive, "w:gz") as archive:
        archive.add(volume, arcname="_data")
    with tarfile.open(box_archive, "w:gz") as archive:
        archive.add(store, arcname="owners")

    # Failures after any of these writes must restore the original schema and
    # marker as well as the owner bytes. Never require the destination schema
    # to exist in the live half-moved volume to restore the source generation.
    (volume / ".layout.json").write_text(json.dumps({
        "layout": 2, "split": "owner-split", "state": "migrating", "migration": "owner-box",
    }))
    shutil.copytree(volume / "a1", store / "300001/a1")
    if interruption in {"box_records", "removed_host_tree"}:
        _sql(volume / ".broker/state/owner-identities.db", """
            CREATE TABLE box_records (principal TEXT, machine INT, project_id INT, bhard_bytes INT);
            INSERT INTO box_records VALUES ('alice',300001,300001,1073741824);
        """)
    if interruption == "removed_host_tree":
        shutil.rmtree(volume / "a1")
        _sql(volume / ".tinyassets.db", "DELETE FROM agent_turns WHERE owner_user_id='alice'")

    binary = tmp_path / "bin"
    binary.mkdir()
    commands = tmp_path / "host-commands.log"
    _shim(binary, "docker", '''case "$1 $2" in
"volume inspect") printf '%s\\n' "$TEST_VOLUME" ;;
"ps -q") : ;;
*) exit 79 ;;
esac
''')
    _shim(binary, "systemctl", 'printf "%s\\n" "$*" >> "$TEST_COMMANDS"\n')
    _shim(binary, "findmnt", '''if [[ "${@: -1}" == FSTYPE ]]; then
echo xfs
else
echo rw,prjquota,nodiscard
fi
''')
    _shim(binary, "xfs_quota", 'echo "unexpected quota write" >&2; exit 79\n')
    (binary / "python3").symlink_to(sys.executable)
    result = subprocess.run(
        ["bash", str(REPO / "deploy/backup-restore.sh")],
        env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}",
             "BACKUP_FILE": str(data_archive), "BOX_BACKUP_FILE": str(box_archive),
             "BACKUP_VOLUME": "disposable-cutover", "BOX_STORE_DIR": str(store),
             "BACKUP_LOG": str(tmp_path / "restore.log"), "TEST_VOLUME": str(volume),
             "TEST_COMMANDS": str(commands)},
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _fingerprint(volume) == before
    assert not (store / "300001").exists()
    assert list(store.glob(".tinyassets-box-restore-old.*/300001/a1/notes.txt"))
    assert commands.read_text().splitlines() == ["stop boxhostd.service"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0,
                    reason="root-private restore journals; owner=c6-owner-box "
                           "runs-in=linux_oracle.py--as-root")
def test_disaster_restore_requires_acknowledgement_and_reports_unknown_facts(tmp_path):
    import runpy
    import sqlite3

    from tests.test_box_restore_authority import seed
    from tests.test_owner_cutover_apply import _restore_paired
    from tinyassets.boxes.state import BoxHostState
    from tinyassets.router_credentials import DeletedOwners

    data, store = tmp_path / "volume/_data", tmp_path / "owners"
    seed(data)
    (data / ".boxhost/boxhost.db").chmod(0o600)
    for uid in (300001, 300002, 300003):
        owner = {300001: "alice", 300002: "bob", 300003: "carol"}[uid]
        if uid != 300001:
            _sql(data / ".broker/state/owner-identities.db", f"""
                INSERT INTO owner_identities VALUES ('{owner}',{uid});
                INSERT INTO box_records VALUES ({uid},{uid},1048576);
            """)
        tree = store / str(uid)
        tree.mkdir(parents=True, mode=0o700)
        os.chown(tree, uid, uid)
        (tree / "note").write_text("backup bytes")
        journal = BoxHostState(tree / ".owner-boxd/boxhost.db")
        journal.begin(owner, "backup-op", "write", "digest")
        journal.finish(owner, "backup-op", {})
        journal.close()
    # Bob's erasure and an already revoked key are known in the backup.
    DeletedOwners(data).add("bob", {})
    _sql(data / ".api-keys.sqlite3", """
        CREATE TABLE api_keys (id TEXT PRIMARY KEY, revoked_at REAL);
        INSERT INTO api_keys VALUES ('revoked-before-backup',42);
        INSERT INTO api_keys VALUES ('revoked-after-backup',NULL);
    """)
    timestamp = "2026-10-17T00-00-00Z"
    data_archive = tmp_path / f"tinyassets-data-{timestamp}.tar.gz"
    box_archive = tmp_path / f"tinyassets-box-store-{timestamp}.tar.gz"
    for path, root, name in ((data_archive, data, "_data"), (box_archive, store, "owners")):
        with tarfile.open(path, "w:gz") as archive:
            archive.add(root, arcname=name)
    runpy.run_path(str(REPO / "deploy/backup-pair.py"))["create"](
        data_archive, box_archive, tmp_path / f"tinyassets-pair-{timestamp}.json")

    # Simulate damaged live layout, custody and journal, while independent
    # deletion/key records and Alice's newer consumed operation survive.
    (data / ".layout.json").write_text("unreadable layout")
    (data / ".broker/credential-custody.db").write_bytes(b"lost database")
    (store / "300003/.owner-boxd/boxhost.db").write_bytes(b"lost journal")
    DeletedOwners(data).add("carol", {})
    _sql(data / ".api-keys.sqlite3",
         "UPDATE api_keys SET revoked_at=84 WHERE id='revoked-after-backup'")
    journal = BoxHostState(store / "300001/.owner-boxd/boxhost.db")
    journal.begin("alice", "surviving-op", "bash", "surviving-digest")
    journal.finish("alice", "surviving-op", {})
    previous_epoch = journal.epoch("alice")
    journal.close()
    before = _fingerprint(data), _fingerprint(store)
    refused = _restore_paired((data, store, data_archive, box_archive), tmp_path)
    assert refused.returncode == 4, refused.stdout + refused.stderr
    # The shell creates its layout lock, but neither live payload changes.
    assert {k: v for k, v in _fingerprint(data).items() if k != ".layout.lock"} == before[0]
    assert _fingerprint(store) == before[1]
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
           "BACKUP_FILE": str(data_archive), "BOX_BACKUP_FILE": str(box_archive),
           "BACKUP_VOLUME": "synthetic-disaster", "BOX_STORE_DIR": str(store),
           "BACKUP_LOG": str(tmp_path / "restore.log"), "TEST_VOLUME": str(data)}

    def run(*args):
        return subprocess.run(["bash", str(REPO / "deploy/backup-restore.sh"), *args],
                              env=env, capture_output=True, text=True, check=False)

    flags = ("--disaster-restore", "--acknowledge-unrecoverable-facts")
    for flag in flags:
        assert run(flag).returncode == 1
    newer = tmp_path / "tinyassets-data-2026-10-18T00-00-00Z.tar.gz"
    newer.write_bytes(b"incomplete newest backup must not be skipped")
    assert run(*flags).returncode == 2
    newer.unlink()
    completed = run(*flags)
    assert completed.returncode == 6, completed.stdout + completed.stderr
    assert "restore complete. Data installed" in completed.stdout
    assert not (store / "300002").exists()  # backup erasure
    assert not (store / "300003").exists()  # surviving erasure
    assert (store / "300001/note").read_text() == "backup bytes"
    with sqlite3.connect(store / "300001/.owner-boxd/boxhost.db") as db:
        assert db.execute("SELECT op_id,state FROM ops ORDER BY op_id").fetchall() == [
            ("backup-op", "unknown_after_restore")]
        assert db.execute("SELECT epoch FROM boxes WHERE command_center_id='alice'"
                          ).fetchone()[0] > previous_epoch
    with sqlite3.connect(data / ".api-keys.sqlite3") as db:
        assert db.execute("SELECT * FROM api_keys").fetchall() == []
    log = (tmp_path / "restore.log").read_text()
    report = json.loads((data / ".disaster-restore.json").read_text())
    assert report["credential_roots_reset"] and report["operation_fences_reset"]
    assert "owner consents and grants" in report["reverted_classes"]
    assert report["backup_time"] < report["restore_time"]
    assert "WARN disaster restore:" in completed.stdout
    assert "WARN disaster restore completed" in log

    # A wholly lost generation has no surviving directory entries to consult.
    # Preserve the synthetic trees elsewhere and present empty replacement mounts.
    data.rename(tmp_path / "lost-data")
    store.rename(tmp_path / "lost-owners")
    data.mkdir()
    store.mkdir()
    assert run().returncode == 4
    recovered = run(*flags)
    assert recovered.returncode == 6, recovered.stdout + recovered.stderr
    assert (store / "300001/note").read_text() == "backup bytes"
    assert not (store / "300002").exists()
    with sqlite3.connect(data / ".api-keys.sqlite3") as db:
        assert db.execute("SELECT * FROM api_keys").fetchall() == []
    with sqlite3.connect(store / "300001/.owner-boxd/boxhost.db") as db:
        assert db.execute("SELECT op_id,state FROM ops").fetchall() == [
            ("backup-op", "unknown_after_restore")]
    assert "credential_roots_reset" in recovered.stdout
