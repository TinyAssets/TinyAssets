#!/usr/bin/env python3
"""Refuse a PR whose diff touches a path no PR may change.

Not "release-critical" (that is pr-scope-guard's declaration gate, which asks
for a label). This is the narrower list of files whose per-PR edits are PURE
COST: every branch appends to them, every append lands in the same region, and
every landing conflicts. The record they were keeping lives somewhere better.

Reads the changed-path list on stdin or from ``--files-from``, one path per
line, as pr-scope-guard already computes it from the PR files API (which
includes ``previous_filename`` for renames, so a move out of a forbidden path is
caught too). Exits 0 when nothing is forbidden, 1 naming each offender and what
to do instead.

Deliberately NOT a tree check: the file is allowed to exist and allowed to grow
on ``main``. What is refused is a PR carrying a change to it.
"""

from __future__ import annotations

import argparse
import sys

#: path -> what to do instead. Matched exactly, and as a directory prefix when
#: the key ends in "/", so a rename into a subdirectory cannot slip past.
FORBIDDEN: dict[str, str] = {
    ".agents/activity.log": (
        "the git log is the per-PR record. Your commit message is the narrative "
        "entry -- write it there, not in a shared append-only file that every "
        "branch edits in the same region. Restore the file to its base version "
        "(`git show origin/main:.agents/activity.log > .agents/activity.log`) "
        "and keep the prose in the commit."
    ),
}


def forbidden_hits(paths: list[str]) -> list[tuple[str, str]]:
    """Return ``(path, reason)`` for every changed path that is forbidden."""
    hits = []
    for raw in paths:
        path = raw.strip().replace("\\", "/")
        # NOT `lstrip("./")`: that strips a CHARACTER SET, so it would eat the
        # leading dot of ".agents/..." and the rule would never match.
        if path.startswith("./"):
            path = path[2:]
        if not path:
            continue
        for rule, reason in FORBIDDEN.items():
            if path == rule or (rule.endswith("/") and path.startswith(rule)):
                hits.append((path, reason))
                break
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files-from",
        help="File of changed paths, one per line. Default: read stdin.",
    )
    args = parser.parse_args(argv)

    if args.files_from:
        with open(args.files_from, encoding="utf-8") as handle:
            paths = handle.readlines()
    else:
        paths = sys.stdin.readlines()

    hits = forbidden_hits(paths)
    if not hits:
        print(f"forbidden-paths: clean ({len(paths)} changed path(s) checked)")
        return 0

    # One line per offender on stderr so the workflow can quote it verbatim
    # into the step summary without re-deriving anything.
    for path, reason in hits:
        print(f"{path}: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
