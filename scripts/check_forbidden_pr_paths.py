#!/usr/bin/env python3
"""Refuse a PR that adds to, edits or moves a path no PR may change.

Not "release-critical" (that is pr-scope-guard's count, shown to the reviewer).
This is the narrower list of files whose per-PR edits are PURE COST: every
branch appends to them, every append lands in the same region, and every
landing conflicts. The record they were keeping lives somewhere better.

Deleting one is allowed. A deletion ends the conflict the rule exists for, and
the cutover deletes these files with the old stack, so a rule that refused it
would block the very change that retires it. Moving one is still refused: the
appends would go on at the new path.

With ``--changes``, reads the PR files API's entries on stdin, one per line as
``status<TAB>filename<TAB>previous_filename`` (the last empty unless renamed),
as pr-scope-guard fetches them. Without it, reads bare paths, one per line,
each taken as an edit. Exits 0 when nothing is forbidden, 1 naming each
offender and what to do instead.

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

#: The files API's status for a path the PR deletes.
REMOVED = "removed"


def _normal(raw: str) -> str:
    path = raw.strip().replace("\\", "/")
    # NOT `lstrip("./")`: that strips a CHARACTER SET, so it would eat the
    # leading dot of ".agents/..." and the rule would never match.
    if path.startswith("./"):
        path = path[2:]
    return path


def _reason(path: str) -> str | None:
    for rule, reason in FORBIDDEN.items():
        if path == rule or (rule.endswith("/") and path.startswith(rule)):
            return reason
    return None


def forbidden_hits(changes: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """``(path, reason)`` for every forbidden path a change adds to, edits or moves.

    ``changes`` are ``(status, filename, previous_filename)``. A rename touches both
    of its paths; a deletion touches none.
    """
    hits = []
    for status, filename, previous in changes:
        touched = [previous] if status.strip() == REMOVED else [filename, previous]
        for path in map(_normal, touched):
            reason = _reason(path) if path else None
            if reason:
                hits.append((path, reason))
    return hits


def parse(lines: list[str], *, changes: bool) -> list[tuple[str, str, str]]:
    out = []
    for line in lines:
        line = line.rstrip("\r\n")
        if not line:
            continue
        if changes:
            status, filename, previous = (line.split("\t") + ["", ""])[:3]
            out.append((status, filename, previous))
        else:
            out.append(("modified", line, ""))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changes",
        action="store_true",
        help="Read `status<TAB>filename<TAB>previous_filename` lines, so a deletion is told apart.",
    )
    args = parser.parse_args(argv)
    lines = sys.stdin.readlines()
    hits = forbidden_hits(parse(lines, changes=args.changes))
    if not hits:
        print(f"forbidden-paths: clean ({len(lines)} change(s) checked)")
        return 0

    # One line per offender on stderr so the workflow can quote it verbatim
    # into the step summary without re-deriving anything.
    for path, reason in hits:
        print(f"{path}: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
