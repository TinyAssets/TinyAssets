#!/usr/bin/env python3
"""Offline C3 preparation; no owner-volume inputs."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.restore_recovery import platform_recovery, write_layout  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--backup", required=True)
    args = parser.parse_args()
    stamp = (args.backup.removeprefix("tinyassets-data-")
             .removeprefix("tinyassets-snapshot-").removesuffix(".tar.gz"))
    backup_time = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%SZ").replace(
        tzinfo=timezone.utc).timestamp()
    # C3: persist the closed state even if preparation fails or is interrupted.
    write_layout(args.current, {"state": "recovering"})
    platform_recovery(args.staged, backup_time=backup_time)


if __name__ == "__main__":
    main()
