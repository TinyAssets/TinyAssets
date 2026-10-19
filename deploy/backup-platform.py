#!/usr/bin/env python3
"""C3 nightly shared-layer archive, verified off-host before publication."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.platform_backup import manifests, publish  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("source")
    parser.add_argument("destination", nargs="?")
    args = parser.parse_args()
    if args.catalog:
        for name, document in sorted(manifests(args.source).items(), reverse=True):
            print(document["archive"], document["sha256"])
    else:
        if not args.destination:
            parser.error("destination required")
        result = publish(Path(args.source), args.destination, retention=tuple(
            int(os.environ.get(f"BACKUP_RETAIN_{name}", default))
            for name, default in (("DAILY", "7"), ("WEEKLY", "4"), ("MONTHLY", "6"))))
        print("verified platform backup published:", result["archive"])
