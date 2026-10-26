"""Restack open PRs whose base PR was squash-merged.

For every open PR whose base branch belongs to a merged PR, replay only the
child's own commits onto origin/<default> (git rebase --onto main <old base
head>), check the patches are unchanged (git range-diff), push with a lease and
retarget the PR to the default branch. A conflict is reported, never forced.

Usage: python restack.py [--repo-dir PATH] [--dry-run] [PR ...]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def sh(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True)


def gh_json(cwd: Path, *args: str):
    return json.loads(sh("gh", *args, cwd=cwd).stdout)


def restack(repo: Path, child: dict, parent: dict, default: str, dry: bool) -> str:
    number, head = child["number"], child["headRefName"]
    old_base = parent["headRefOid"]
    sh("git", "fetch", "-q", "origin", default, head, cwd=repo)
    with tempfile.TemporaryDirectory(prefix="restack-") as tmp:
        work = Path(tmp) / "wt"
        sh("git", "worktree", "add", "-q", "--detach", str(work), f"origin/{head}", cwd=repo)
        try:
            if sh(
                "git", "merge-base", "--is-ancestor", old_base, "HEAD", cwd=work, check=False
            ).returncode:
                return f"#{number}: skip, {old_base[:10]} is not in its history"
            own = sh("git", "rev-list", "--count", f"{old_base}..HEAD", cwd=work).stdout.strip()
            rebase = sh(
                "git",
                "rebase",
                "-q",
                "--onto",
                f"origin/{default}",
                old_base,
                cwd=work,
                check=False,
            )
            if rebase.returncode:
                sh("git", "rebase", "--abort", cwd=work, check=False)
                return (
                    f"#{number}: CONFLICT replaying {own} commit(s) onto {default}; restack by hand"
                )
            new_count = sh(
                "git", "rev-list", "--count", f"origin/{default}..HEAD", cwd=work
            ).stdout.strip()
            diff = sh(
                "git",
                "range-diff",
                f"{old_base}..origin/{head}",
                f"origin/{default}..HEAD",
                cwd=work,
            ).stdout
            changed = [line for line in diff.splitlines() if line[:1].isdigit() and " ! " in line]
            if dry:
                return (
                    f"#{number}: would replay {own} commit(s) -> {new_count}; "
                    f"changed patches {len(changed)}"
                )
            sh(
                "git",
                "push",
                "-q",
                f"--force-with-lease={head}:{child['headRefOid']}",
                "origin",
                f"HEAD:{head}",
                cwd=work,
            )
            sh("gh", "pr", "edit", str(number), "--base", default, cwd=repo)
            note = (
                "patches identical"
                if not changed
                else f"{len(changed)} patch(es) changed by the rebase: re-review"
            )
            return f"#{number}: restacked {own} commit(s) onto {default} and retargeted ({note})"
        finally:
            sh("git", "worktree", "remove", "--force", str(work), cwd=repo, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("prs", nargs="*", type=int, help="limit to these child PRs")
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    repo = args.repo_dir
    default = gh_json(repo, "repo", "view", "--json", "defaultBranchRef")["defaultBranchRef"][
        "name"
    ]
    fields = "number,baseRefName,headRefName,headRefOid"
    open_prs = gh_json(repo, "pr", "list", "--state", "open", "--limit", "200", "--json", fields)
    # GitHub retargets a child to the default branch itself when the merged
    # parent's branch is deleted, so the child's base name no longer finds the
    # parent. Look instead for a recently merged PR head that the child still
    # carries but the default branch does not (its squash replaced it).
    merged = gh_json(
        repo,
        "pr",
        "list",
        "--state",
        "merged",
        "--limit",
        "60",
        "--json",
        "number,headRefOid,headRefName,mergedAt",
    )
    sh("git", "fetch", "-q", "origin", default, cwd=repo)
    for child in open_prs:
        if args.prs and child["number"] not in args.prs:
            continue
        if child["baseRefName"] != default:
            named = [m for m in merged if m["headRefName"] == child["baseRefName"]]
            if named:
                print(restack(repo, child, named[0], default, args.dry_run))
            continue
        sh("git", "fetch", "-q", "origin", child["headRefName"], cwd=repo, check=False)
        carried = [
            m
            for m in merged
            if sh(
                "git",
                "merge-base",
                "--is-ancestor",
                m["headRefOid"],
                f"origin/{child['headRefName']}",
                cwd=repo,
                check=False,
            ).returncode
            == 0
            and sh(
                "git",
                "merge-base",
                "--is-ancestor",
                m["headRefOid"],
                f"origin/{default}",
                cwd=repo,
                check=False,
            ).returncode
            != 0
        ]
        if carried:
            # The newest carried parent head bounds the child's own commits.
            print(
                restack(
                    repo, child, max(carried, key=lambda m: m["mergedAt"]), default, args.dry_run
                )
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
