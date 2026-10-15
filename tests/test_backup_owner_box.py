"""Exercise the live owner-box backup with disposable shims on Linux.

24/7: the nightly backup never stops a service. Any docker or systemctl call
is a failure, and SQLite files are copied through the online backup API.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).parents[1]


def shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body, encoding="utf-8")
    path.chmod(0o755)


@unittest.skipUnless(os.name == "posix" and os.geteuid() == 0, "needs Linux root host")
class OwnerBoxLiveBackupTest(unittest.TestCase):
    def test_live_pair_never_stops_a_service(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ta-backup-live-", dir="/tmp") as temporary:
            root = Path(temporary)
            deploy, scripts, binary = (root / part for part in ("deploy", "scripts", "bin"))
            for directory in (deploy, scripts, binary):
                directory.mkdir()
            for name in ("backup-owner-box.sh", "backup-pair.py", "backup-live-sqlite.py"):
                shutil.copy2(REPO / "deploy" / name, deploy / name)
            shutil.copy2(REPO / "scripts/backup_prune.py", scripts / "backup_prune.py")
            shim(deploy, "box-store-check.sh", "exit 0\n")
            (deploy / "box-store-archive.py").write_text("raise SystemExit(0)\n")
            data, box, remote = (root / part for part in ("_data", "owners", "remote"))
            for directory in (data, box, remote):
                directory.mkdir()
            (data / "file").write_text("receipt and platform data")
            private = data / ".platform" / "credential-vaults" / "center"
            private.mkdir(parents=True)
            (private / ".credential-vault.json").write_text("platform credential")
            owner = box / "300001"
            owner.mkdir()
            (owner / "file").write_text("owner bytes")
            # A live WAL database whose committed row is only in the -wal file.
            live = sqlite3.connect(owner / "state.db")
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("PRAGMA wal_autocheckpoint=0")
            live.execute("CREATE TABLE t (v TEXT)")
            live.execute("INSERT INTO t VALUES ('committed')")
            live.commit()
            for name in (
                "tinyassets-data-2026-01-01T00-00-00Z.tar.gz",
                "tinyassets-box-store-2026-01-01T00-00-00Z.tar.gz",
                "tinyassets-pair-2026-01-01T00-00-00Z.json",
                "unrelated.txt",
            ):
                (remote / name).write_text("old")
            log = root / "actions.log"
            for name in ("docker", "systemctl", "xfs_freeze"):
                shim(binary, name, f'echo "{name} $*" >> "$TEST_LOG"\nexit 1\n')
            shim(binary, "cp", """
args=()
for arg in "$@"; do
  if [[ "$arg" == --reflink=always ]]; then
    echo 'reflink required' >> "$TEST_LOG"
  else
    args+=("$arg")
  fi
done
# The fixture is on ext4, while the host store is XFS.
exec /bin/cp "${args[@]}"
""")
            shim(binary, "rclone", """
case "$1" in
  copyto) cp "${@: -2:1}" "${@: -1}" ;;
  lsf) find "$TEST_REMOTE" -maxdepth 1 -type f -printf '%f\\n' ;;
  deletefile) rm -f -- "${@: -1}" ;;
  *) exit 1 ;;
esac
""")
            env = {
                **os.environ,
                "PATH": f"{binary}:{os.environ['PATH']}",
                "TEST_LOG": str(log),
                "TEST_REMOTE": str(remote),
                "BACKUP_SNAPSHOT_DIR": str(root / "stage"),
                "BACKUP_RETAIN_DAILY": "1",
                "BACKUP_RETAIN_WEEKLY": "0",
                "BACKUP_RETAIN_MONTHLY": "0",
            }
            try:
                result = subprocess.run(
                    ["bash", str(deploy / "backup-owner-box.sh"),
                     str(data), str(box), str(remote)],
                    env=env, capture_output=True, text=True, check=False,
                )
            finally:
                live.close()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actions = log.read_text().splitlines()
            self.assertIn("reflink required", actions)
            self.assertFalse([a for a in actions if a.split()[0] in
                              ("docker", "systemctl", "xfs_freeze")], actions)
            self.assertEqual(len(list(remote.glob("tinyassets-pair-*.json"))), 1)
            self.assertEqual(len(list(remote.glob("tinyassets-data-*.tar.gz"))), 1)
            self.assertTrue((remote / "unrelated.txt").exists())
            with tarfile.open(next(remote.glob("tinyassets-data-*.tar.gz"))) as archive:
                member = archive.extractfile(
                    "_data/.platform/credential-vaults/center/.credential-vault.json")
                self.assertIsNotNone(member)
                self.assertEqual(member.read(), b"platform credential")
            restored = root / "restored"
            with tarfile.open(next(remote.glob("tinyassets-box-store-*.tar.gz"))) as archive:
                names = archive.getnames()
                self.assertFalse(any("credential-vault" in name for name in names))
                self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in names))
                archive.extractall(restored, filter="tar")
            copy = sqlite3.connect(restored / "owners" / "300001" / "state.db")
            try:
                self.assertEqual(copy.execute("SELECT v FROM t").fetchall(), [("committed",)])
            finally:
                copy.close()


if __name__ == "__main__":
    unittest.main()
