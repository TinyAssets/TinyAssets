"""Add exact Git ancestry and merged PR membership to the deployment receipt."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def containment(revision, *, cwd=None):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("release revision must be a full SHA")
    output = subprocess.run(
        ["git", "log", "--format=%H%x09%s", revision], cwd=cwd,
        check=True, capture_output=True, text=True).stdout
    commits, prs = [], set()
    for line in output.splitlines():
        sha, subject = line.split("\t", 1)
        commits.append(sha)
        # GitHub's merge and squash conventions. Other commit subjects that
        # merely mention a PR are not evidence that it merged.
        match = re.match(r"Merge pull request #(\d+)\b", subject)
        squash = re.search(r" \(#(\d+)\)$", subject)
        if match or squash:
            prs.add(int((match or squash).group(1)))
    return {"git_sha": revision, "commits": commits, "prs": sorted(prs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    receipt["containment"] = containment(receipt["git_sha"])
    args.receipt.write_text(json.dumps(receipt) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
