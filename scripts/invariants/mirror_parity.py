"""Reject tracked files under the retired Claude plugin runtime path."""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import CheckResult, HealResult, Invariant, Status

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/"


def tracked_mirror(root: Path = REPO_ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--", RUNTIME_PATH],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()


class MirrorParityInvariant(Invariant):
    name = "mirror-parity"
    description = "The hosted Claude plugin has no tracked runtime."
    pre_commit_scope = True
    poll_interval_s = None
    auto_heal = False

    def __init__(self, root: Path = REPO_ROOT) -> None:
        self.root = Path(root)

    def _check(self) -> CheckResult:
        paths = tracked_mirror(self.root)
        if paths:
            return CheckResult(
                status=Status.VIOLATED,
                message=f"{len(paths)} tracked plugin runtime file(s)",
                evidence={"paths": paths},
            )
        return CheckResult(status=Status.OK, message="no tracked plugin runtime files")

    def _heal(self) -> HealResult:
        return HealResult(healed=False, message="remove runtime files from the index")
