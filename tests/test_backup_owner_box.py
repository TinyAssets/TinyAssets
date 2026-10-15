"""Exercise the host barrier sequence with disposable service shims on Linux."""

from __future__ import annotations

import os
import shutil
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
class OwnerBoxBarrierTest(unittest.TestCase):
    def test_complete_pair_and_survivor_refusal(self) -> None:
        for survivor in (False, True):
            with self.subTest(survivor=survivor), tempfile.TemporaryDirectory(
                prefix="ta-backup-barrier-", dir="/tmp"
            ) as temporary:
                root = Path(temporary)
                deploy, scripts, binary = (root / part for part in ("deploy", "scripts", "bin"))
                for directory in (deploy, scripts, binary):
                    directory.mkdir()
                for name in ("backup-owner-box.sh", "backup-pair.py"):
                    shutil.copy2(REPO / "deploy" / name, deploy / name)
                shutil.copy2(REPO / "scripts/backup_prune.py", scripts / "backup_prune.py")
                shim(deploy, "box-store-check.sh", "exit 0\n")
                (deploy / "box-store-quotas.py").write_text("raise SystemExit(0)\n")
                (deploy / "box-store-archive.py").write_text("raise SystemExit(0)\n")
                data, box, remote = (root / part for part in ("_data", "owners", "remote"))
                for directory in (data, box, remote):
                    directory.mkdir()
                (data / ".layout.json").write_text(
                    '{"state":"stable","box_layout":"owner-box"}'
                )
                (data / "file").write_text("receipt and platform data")
                private = data / ".platform" / "credential-vaults" / "center"
                private.mkdir(parents=True)
                (private / ".credential-vault.json").write_text("platform credential")
                owner = box / "300001"
                owner.mkdir()
                (owner / "file").write_text("owner bytes")
                for name in ("tinyassets-daemon", "tinyassets-broker", "boxhostd"):
                    (root / name).write_text("true")
                if not survivor:
                    for name in (
                        "tinyassets-data-2026-01-01T00-00-00Z.tar.gz",
                        "tinyassets-box-store-2026-01-01T00-00-00Z.tar.gz",
                        "tinyassets-pair-2026-01-01T00-00-00Z.json",
                        "unrelated.txt",
                    ):
                        (remote / name).write_text("old")
                shim(binary, "docker", """
case "$1" in
  inspect) cat "$TEST_STATE/${@: -1}" ;;
  stop) echo "stop ${@: -1}" >> "$TEST_LOG"; echo false > "$TEST_STATE/${@: -1}" ;;
  start) echo "start ${@: -1}" >> "$TEST_LOG"; echo true > "$TEST_STATE/${@: -1}" ;;
  ps) [[ "$TEST_SURVIVOR" == 1 ]] && echo foreign-container || true ;;
  *) exit 1 ;;
esac
""")
                shim(binary, "systemctl", """
case "$1" in
  is-active) [[ "$(cat "$TEST_STATE/boxhostd")" == true ]] ;;
  stop) echo 'stop boxhostd' >> "$TEST_LOG"; echo false > "$TEST_STATE/boxhostd" ;;
  start) echo 'start boxhostd' >> "$TEST_LOG"; echo true > "$TEST_STATE/boxhostd" ;;
  *) exit 1 ;;
esac
""")
                shim(binary, "xfs_freeze", 'echo "freeze $1" >> "$TEST_LOG"\n')
                shim(binary, "cp", """
args=()
for arg in "$@"; do
  if [[ "$arg" == --reflink=always ]]; then
    echo 'reflink required' >> "$TEST_LOG"
  else
    args+=("$arg")
  fi
done
# The fixture is on ext4, while the host store is XFS. Exercise the barrier
# ordering here; the production command must require a same-filesystem clone.
exec /bin/cp "${args[@]}"
""")
                shim(binary, "tar", """
echo "archive $(cat "$TEST_STATE/tinyassets-daemon")" >> "$TEST_LOG"
exec /usr/bin/tar "$@"
""")
                shim(binary, "rclone", """
case "$1" in
  copyto) cp "${@: -2:1}" "${@: -1}" ;;
  lsf) find "$TEST_REMOTE" -maxdepth 1 -type f -printf '%f\\n' ;;
  deletefile) rm -f -- "${@: -1}" ;;
  *) exit 1 ;;
esac
""")
                log = root / "actions.log"
                env = {
                    **os.environ,
                    "PATH": f"{binary}:{os.environ['PATH']}",
                    "TEST_STATE": str(root),
                    "TEST_LOG": str(log),
                    "TEST_REMOTE": str(remote),
                    "TEST_SURVIVOR": "1" if survivor else "0",
                    "BACKUP_SNAPSHOT_DIR": str(root / "stage"),
                    "BACKUP_RETAIN_DAILY": "1",
                    "BACKUP_RETAIN_WEEKLY": "0",
                    "BACKUP_RETAIN_MONTHLY": "0",
                }
                result = subprocess.run(
                    ["bash", str(deploy / "backup-owner-box.sh"), str(data), str(box), str(remote)],
                    env=env, capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    result.returncode, 2 if survivor else 0, result.stdout + result.stderr
                )
                self.assertEqual(
                    log.read_text().splitlines()[:3],
                    ["stop tinyassets-daemon", "stop tinyassets-broker", "stop boxhostd"],
                )
                self.assertEqual((root / "tinyassets-daemon").read_text().strip(), "true")
                self.assertEqual((root / "tinyassets-broker").read_text().strip(), "true")
                self.assertEqual((root / "boxhostd").read_text().strip(), "true")
                self.assertEqual(
                    len(list(remote.glob("tinyassets-pair-*.json"))), 0 if survivor else 1
                )
                if not survivor:
                    actions = log.read_text().splitlines()
                    self.assertIn("archive true", actions)
                    self.assertIn("reflink required", actions)
                    self.assertFalse(any(action.startswith("freeze ") for action in actions))
                    self.assertLess(actions.index("start tinyassets-daemon"),
                                    actions.index("archive true"))
                    self.assertEqual(len(list(remote.glob("tinyassets-data-*.tar.gz"))), 1)
                    self.assertEqual(len(list(remote.glob("tinyassets-box-store-*.tar.gz"))), 1)
                    with tarfile.open(next(remote.glob("tinyassets-data-*.tar.gz"))) as archive:
                        member = archive.extractfile(
                            "_data/.platform/credential-vaults/center/.credential-vault.json")
                        self.assertIsNotNone(member)
                        self.assertEqual(member.read(), b"platform credential")
                    box_archive = next(remote.glob("tinyassets-box-store-*.tar.gz"))
                    with tarfile.open(box_archive) as archive:
                        self.assertFalse(any("credential-vault" in name
                                             for name in archive.getnames()))
                    self.assertTrue((remote / "unrelated.txt").exists())


if __name__ == "__main__":
    unittest.main()
