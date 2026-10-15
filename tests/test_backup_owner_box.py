"""Exercise the live owner-box backup with disposable shims on Linux.

24/7: the nightly backup never stops a service. Any docker or systemctl call
is a failure, and SQLite files are copied through the online backup API.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
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
            registry = data / ".broker" / "state"
            registry.mkdir(parents=True)
            with sqlite3.connect(registry / "owner-identities.db") as db:
                db.execute("CREATE TABLE box_records (machine, project_id, bhard_bytes)")
                db.execute("INSERT INTO box_records VALUES (300001, 300001, 1048576)")
                db.execute("INSERT INTO box_records VALUES (300002, 300002, 1048576)")
                db.execute("INSERT INTO box_records VALUES (300003, 300003, 1048576)")
            # Owner 300002's destruction began: its tree must not come back.
            (data / ".boxhost").mkdir()
            with sqlite3.connect(data / ".boxhost" / "boxhost.db") as db:
                db.execute("CREATE TABLE owner_destroys (owner_id, op_id, uid, center,"
                           " epoch, entries, state)")
                db.execute("INSERT INTO owner_destroys VALUES"
                           " ('o2', 'op', 300002, 'c', 1, NULL, 'deleting')")
            (box / "300002").mkdir()
            (box / "300002" / "file").write_text("erased owner bytes")
            for path in (box / "300002", box / "300002" / "file"):
                os.chown(path, 300002, 300002)
            # Owner 300003 is mid-provisioning (still root-owned): its re-take
            # cannot run, but it never fails everyone else's night.
            (box / "300003").mkdir()
            (box / "300003" / "file").write_text("new owner bytes")
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
            # Owner trees are re-taken as their owner, never as root.
            root.chmod(0o755)
            for path in (owner, *owner.rglob("*")):
                os.chown(path, 300001, 300001)
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
                self.assertFalse(any("300002" in name for name in names), names)
                self.assertIn("owners/300003/file", names)
                self.assertFalse(any("credential-vault" in name for name in names))
                self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in names))
                archive.extractall(restored, filter="tar")
            copy = sqlite3.connect(restored / "owners" / "300001" / "state.db")
            try:
                self.assertEqual(copy.execute("SELECT v FROM t").fetchall(), [("committed",)])
            finally:
                copy.close()

    def test_unreserved_owner_tree_fails_the_backup_loudly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ta-backup-pair-", dir="/tmp") as temporary:
            root = Path(temporary)
            deploy, binary = root / "deploy", root / "bin"
            deploy.mkdir()
            binary.mkdir()
            for name in ("backup-owner-box.sh", "backup-live-sqlite.py"):
                shutil.copy2(REPO / "deploy" / name, deploy / name)
            shim(deploy, "box-store-check.sh", "exit 0\n")
            shim(binary, "cp", 'exec /bin/cp "${@/--reflink=always/--reflink=auto}"\n')
            shim(binary, "rclone", 'echo "rclone $*" >> "$TEST_LOG"\nexit 1\n')
            data, box = root / "_data", root / "owners"
            (data / ".broker" / "state").mkdir(parents=True)
            with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
                db.execute("CREATE TABLE box_records (machine, project_id, bhard_bytes)")
            (box / "300002").mkdir(parents=True)
            os.chown(box / "300002", 300002, 300002)
            log = root / "actions.log"
            result = subprocess.run(
                ["bash", str(deploy / "backup-owner-box.sh"), str(data), str(box),
                 str(root / "remote")],
                env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}",
                     "TEST_LOG": str(log), "BACKUP_SNAPSHOT_DIR": str(root / "stage")},
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("without a paired reservation", result.stderr)
            self.assertFalse(log.exists(), "nothing may be uploaded")


def _module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "backup_live_sqlite", REPO / "deploy" / "backup-live-sqlite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveSqliteRetakeTest(unittest.TestCase):
    def test_a_database_that_vanishes_mid_backup_is_skipped_not_fatal(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as temporary:
            live, copy = Path(temporary) / "live", Path(temporary) / "copy"
            live.mkdir()
            copy.mkdir()
            with sqlite3.connect(live / "eph.db") as db:
                db.execute("CREATE TABLE t (v)")
            db.close()
            real = module.sqlite3.connect

            def vanish(*args, **kwargs):
                (live / "eph.db").unlink(missing_ok=True)
                raise module.sqlite3.OperationalError("unable to open database file")

            module.sqlite3.connect = vanish
            try:
                self.assertEqual(module.main(live, copy), 0)
            finally:
                module.sqlite3.connect = real
            self.assertFalse((copy / "eph.db").exists())

    @unittest.skipUnless(os.name == "posix" and os.geteuid() == 0, "needs Linux root host")
    def test_an_owner_symlink_never_reaches_another_owners_database(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ta-backup-leak-", dir="/tmp") as temporary:
            root = Path(temporary)
            root.chmod(0o755)
            other = root / "box" / "300002"
            other.mkdir(parents=True)
            with sqlite3.connect(other / "secret.db") as db:
                db.execute("CREATE TABLE s (v)")
                db.execute("INSERT INTO s VALUES ('OTHER OWNER SECRET')")
            db.close()
            live = root / "box" / "300001"
            (live / "d").mkdir(parents=True)
            with sqlite3.connect(live / "d" / "secret.db") as db:
                db.execute("CREATE TABLE s (v)")
            db.close()
            for uid, tree in ((300001, live), (300002, other)):
                for path in (tree, *tree.rglob("*")):
                    os.chown(path, uid, uid)
                tree.chmod(0o700)
            (root / "box").chmod(0o755)
            stage = root / "stage"
            stage.mkdir(mode=0o711)
            copy = stage / "300001"
            subprocess.run(["cp", "-a", str(live), str(copy)], check=True)
            shutil.rmtree(live / "d")
            os.symlink("../300002", live / "d")
            os.lchown(live / "d", 300001, 300001)
            result = subprocess.run(
                [sys.executable, str(REPO / "deploy" / "backup-live-sqlite.py"),
                 "--as-owner", str(live), str(copy)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # The owner's own reflink copy is kept; the other owner's rows never land.
            with sqlite3.connect(copy / "d" / "secret.db") as db:
                self.assertEqual(db.execute("SELECT * FROM s").fetchall(), [])
            db.close()

    @unittest.skipUnless(os.name == "posix" and os.geteuid() == 0, "needs Linux root host")
    def test_an_owners_read_only_directories_never_fail_the_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ta-backup-ro-", dir="/tmp") as temporary:
            # The store's parent stays root-only (like /var/lib/boxhostd).
            root = Path(temporary) / "store"
            root.mkdir(mode=0o755)
            live = root / "300001"
            (live / "ro").mkdir(parents=True)
            with sqlite3.connect(live / "ro" / "a.db") as db:
                db.execute("CREATE TABLE t (v)")
                db.execute("INSERT INTO t VALUES ('kept')")
            db.close()
            for path in (live, *live.rglob("*")):
                os.chown(path, 300001, 300001)
            stage = root / "stage"
            stage.mkdir(mode=0o711)
            copy = stage / "300001"
            subprocess.run(["cp", "-a", str(live), str(copy)], check=True)
            (copy / "ro").chmod(0o555)
            (live / "ro" / "a.db").chmod(0o000)  # case A: unreadable, read-only copy dir
            (live / "late" / "b").mkdir(parents=True)  # case B: new db under a 0555 parent
            with sqlite3.connect(live / "late" / "b" / "x.db") as db:
                db.execute("CREATE TABLE t (v)")
            db.close()
            for path in (live / "late", *(live / "late").rglob("*")):
                os.chown(path, 300001, 300001)
            (live / "late").chmod(0o555)
            result = subprocess.run(
                [sys.executable, str(REPO / "deploy" / "backup-live-sqlite.py"),
                 "--as-owner", str(live), str(copy)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("reflink copy kept", result.stderr)
            (copy / "ro").chmod(0o755)
            with sqlite3.connect(copy / "ro" / "a.db") as db:
                self.assertEqual(db.execute("SELECT v FROM t").fetchall(), [("kept",)])
            db.close()

    def test_a_root_owned_tree_is_refused_in_owner_mode(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("needs root")
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, str(REPO / "deploy" / "backup-live-sqlite.py"),
                 "--as-owner", temporary, temporary],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("owned by root", result.stderr)

    def test_a_database_created_after_the_tree_copy_is_backed_up(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as temporary:
            live, copy = Path(temporary) / "live", Path(temporary) / "copy"
            (live / "late" / "nested").mkdir(parents=True)
            copy.mkdir()
            with sqlite3.connect(live / "late" / "nested" / "new.db") as db:
                db.execute("CREATE TABLE t (v TEXT)")
                db.execute("INSERT INTO t VALUES ('committed')")
            db.close()
            module.main(live, copy)
            with sqlite3.connect(copy / "late" / "nested" / "new.db") as db:
                self.assertEqual(db.execute("SELECT v FROM t").fetchall(), [("committed",)])
            db.close()


if __name__ == "__main__":
    unittest.main()
