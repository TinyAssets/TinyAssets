"""Retention-prune helper for deploy/backup.sh.

Reads a newline-delimited list of archive filenames from stdin (rclone lsf
--format n output), applies the daily/weekly/monthly keep policy, and writes
the names to DELETE to stdout — one per line.

Usage (called by backup.sh):
    rclone lsf --format n DEST/ | python3 backup_prune.py \\
        --keep-daily 7 --keep-weekly 4 --keep-monthly 6

Exit codes:
    0   success (pruned names printed; if nothing to prune, empty output)
    1   argument error
"""

from __future__ import annotations

import argparse
import re
import sys

# Tier prefixes managed by this pruner. Names not matching one of these
# patterns are NEVER deleted — the destination may hold files we don't
# own. (Before 2026-06-10 the delete set was computed as all-names minus
# kept, which would have deleted any unrecognized file at the dest.)
TIER_PATTERNS = (
    r"^tinyassets-data-\d.*\.tar\.gz$",
    r"^tinyassets-brain-\d.*\.tar\.gz$",
    r"^tinyassets-box-store-\d.*\.tar\.gz$",
)


def _apply_retention_one_tier(
    names: list[str],
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
) -> list[str]:
    """Return names within one tier that should be deleted (oldest first)."""
    keep: set[str] = set()
    week_seen: dict[str, bool] = {}
    month_seen: dict[str, bool] = {}
    daily_count = 0
    weekly_count = 0
    monthly_count = 0

    for name in sorted(names, reverse=True):
        daily_count += 1
        if daily_count <= keep_daily:
            keep.add(name)
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        if not m:
            continue
        date_str = m.group(1)
        day = int(date_str[8:10])
        week_bucket = date_str[:7] + f"-W{(day + 6) // 7}"
        if week_bucket not in week_seen:
            week_seen[week_bucket] = True
            weekly_count += 1
            if weekly_count <= keep_weekly:
                keep.add(name)
                continue
        month_bucket = date_str[:7]
        if month_bucket not in month_seen:
            month_seen[month_bucket] = True
            monthly_count += 1
            if monthly_count <= keep_monthly:
                keep.add(name)
                continue

    return sorted(set(names) - keep)


def _apply_retention(
    names: list[str],
    keep_daily: int,
    keep_weekly: int,
    keep_monthly: int,
) -> list[str]:
    """Return names that should be deleted (oldest first).

    Retention is applied independently per tier prefix so brain archives
    never crowd out full-volume archives (or vice versa). Unrecognized
    names are never returned.
    """
    to_delete: list[str] = []
    for pattern in TIER_PATTERNS:
        tier_names = [n for n in names if re.match(pattern, n)]
        to_delete.extend(
            _apply_retention_one_tier(tier_names, keep_daily, keep_weekly, keep_monthly)
        )
    return sorted(to_delete)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backup retention pruner")
    p.add_argument("--keep-daily", type=int, default=7)
    p.add_argument("--keep-weekly", type=int, default=4)
    p.add_argument("--keep-monthly", type=int, default=6)
    args = p.parse_args(argv)

    names = [line.strip() for line in sys.stdin if line.strip()]
    to_delete = _apply_retention(names, args.keep_daily, args.keep_weekly, args.keep_monthly)
    for name in to_delete:
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
