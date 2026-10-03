"""CLI entry for run-transcript rotation (BUG-023 Phase 2).

Wraps :func:`tinyassets.storage.rotation.rotate_run_transcripts` so systemd
or docker compose can schedule it. Reads retention from
``TINYASSETS_RUN_TRANSCRIPT_RETENTION_DAYS`` (default 30).

Usage:
    python -m scripts.rotate_run_transcripts            # full rotation
    python -m scripts.rotate_run_transcripts --dry-run  # plan only

Exit codes:
    0  Completed (even if some per-file errors occurred — those are
       surfaced in stdout for operator review).
    1  Fatal: data_dir() resolution failed or runs_dir inaccessible.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from tinyassets.storage import data_dir
from tinyassets.storage.rotation import (
    _retention_days_from_env,
    rotate_run_transcripts,
)

logger = logging.getLogger("tinyassets.rotate_run_transcripts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retention-days", type=int, default=None,
        help="Override TINYASSETS_RUN_TRANSCRIPT_RETENTION_DAYS.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would rotate without modifying files.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    retention = args.retention_days
    if retention is None:
        retention = _retention_days_from_env()

    try:
        runs_dir = data_dir() / "runs"
    except Exception as exc:  # noqa: BLE001
        logger.error("cannot resolve data_dir: %s", exc)
        return 1

    if args.dry_run:
        logger.info(
            "DRY-RUN: would rotate files older than %d days in %s",
            retention, runs_dir,
        )
        print(json.dumps({
            "dry_run": True,
            "retention_days": retention,
            "runs_dir": str(runs_dir),
        }))
        return 0

    # The hourly rotation writes into the data root: the layout guard (storage_layout.py).
    from tinyassets.storage_layout import LayoutRefused, require_layout

    try:
        require_layout(runs_dir.parent)
    except LayoutRefused as exc:
        logger.error("layout refused: %s", exc)
        return 3
    result = rotate_run_transcripts(runs_dir, retention_days=retention)
    summary = result.as_dict()
    summary["retention_days"] = retention
    summary["runs_dir"] = str(runs_dir)
    logger.info("rotation summary: %s", summary)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
