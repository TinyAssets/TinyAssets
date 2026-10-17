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
