#!/usr/bin/env python3
"""Refuse while the rules GitHub applies to `main` are not the ruleset file's.

`docs/ops/merge-queue-ruleset.json` is the one source of the merge queue's rules
(`docs/decisions/untangle-2026-11/required-checks.md`). Auto-merge relies on them: a PR
armed with auto-merge merges once the checks GitHub requires pass, and the required
`review` check is the only review gate (ADR-038). So auto-enroll arms nothing while the
live rules differ from the file's, a file change merged before its `PUT` among them
(`docs/host-actions.md`).

Exit 0 when they match, 1 when they differ or cannot be read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

RULESET = Path(__file__).resolve().parent.parent / "docs" / "ops" / "merge-queue-ruleset.json"
RULES = json.loads(RULESET.read_text(encoding="utf-8"))["rules"]


def gate_drift(declared: list[dict], live: list[dict]) -> list[str]:
    """Each rule on one side only: the file's, or the rules GitHub applies to main.

    No list in a rule's parameters has a meaningful order, so lists compare sorted."""
    def unordered(value: object) -> object:
        if isinstance(value, dict):
            return {key: unordered(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted((unordered(item) for item in value),
                          key=lambda item: json.dumps(item, sort_keys=True))
        return value

    def canonical(rules: list[dict]) -> list[str]:
        return sorted(json.dumps(unordered({"type": rule["type"],
                                            "parameters": rule.get("parameters", {})}),
                                 sort_keys=True) for rule in rules)
    mine, theirs = canonical(declared), canonical(live)
    return ([f"file only: {rule}" for rule in mine if rule not in theirs]
            + [f"live only: {rule}" for rule in theirs if rule not in mine])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    args = parser.parse_args(argv)
    proc = subprocess.run(["gh", "api", f"repos/{args.repo}/rules/branches/main"],
                          capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        print(f"::error::could not read the rules for main: {proc.stderr.strip()}",
              file=sys.stderr)
        return 1
    drift = gate_drift(RULES, json.loads(proc.stdout))
    if drift:
        print(f"::error::the rules GitHub applies to main are not {RULESET.name}'s, so "
              f"nothing is armed until they are (docs/host-actions.md): {'; '.join(drift)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
