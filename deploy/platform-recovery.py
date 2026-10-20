#!/usr/bin/env python3
"""C3 offline preparation and admission publication after broker startup."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.restore_recovery import (  # noqa: E402
    finish_platform,
    prepare_platform,
    write_layout,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--staged", type=Path)
    parser.add_argument("--backup")
    parser.add_argument("--finish", action="store_true")
    args = parser.parse_args()
    if args.finish:
        import time

        from tinyassets.restore_recovery import RecoveryUnavailable

        deadline = time.monotonic() + 600
        while True:
            try:
                finish_platform(args.current)
                return
            except (RecoveryUnavailable, ValueError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
    if args.staged is None or args.backup is None:
        parser.error("preparation requires --staged and --backup")
    stamp = (args.backup.removeprefix("tinyassets-data-")
             .removeprefix("tinyassets-snapshot-").removesuffix(".tar.gz"))
    backup_time = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%SZ").replace(
        tzinfo=timezone.utc).timestamp()
    # C3: persist the closed state even if preparation fails or is interrupted.
    write_layout(args.current, {"state": "recovering"})
    prepare_platform(args.staged, backup_time=backup_time)


if __name__ == "__main__":
    main()
