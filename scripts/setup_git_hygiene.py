"""One-shot git hygiene setup (Layer 0). Idempotent; safe to re-run anywhere.

Part of the branch lifecycle automation; see
``docs/reference/worktree-discipline.md``.

Sets the table-stakes config that prevents the "1,209 behind / stale refs"
drift:

* ``fetch.prune=true``      — deleting a branch on GitHub cleans local refs.
* ``fetch.pruneTags=true``  — same for tags.
* ``rerere.enabled=true``   — remember conflict resolutions across re-merges.

With ``--repo-setting`` it also flips the GitHub ``delete_branch_on_merge``
repo setting via ``gh`` (requires admin + gh auth).
"""

from __future__ import annotations

import argparse
import subprocess
import sys

CONFIG = {
    "fetch.prune": "true",
    "fetch.pruneTags": "true",
    "rerere.enabled": "true",
}


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--local", action="store_true", help="write to this repo only (default: --global)"
    )
    parser.add_argument(
        "--repo-setting",
        action="store_true",
        help="also flip GitHub delete_branch_on_merge via gh",
    )
    args = parser.parse_args(argv)

    scope = "--local" if args.local else "--global"
    for key, val in CONFIG.items():
        _run(["git", "config", scope, key, val])
        got = _run(["git", "config", "--get", key]).stdout.strip()
        print(f"  {key} = {got}")

    if args.repo_setting:
        proc = _run(
            ["gh", "api", "-X", "PATCH", "repos/{owner}/{repo}",
             "-f", "delete_branch_on_merge=true", "--jq", ".delete_branch_on_merge"]
        )
        if proc.returncode == 0:
            print(f"  delete_branch_on_merge = {proc.stdout.strip()}")
        else:
            print(f"  delete_branch_on_merge: SKIPPED ({proc.stderr.strip() or 'gh unavailable'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
