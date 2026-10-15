"""Keep the newest complete local cutover snapshot; leave other backups alone."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

SNAPSHOT_DIR = Path("/var/backups/tinyassets")
NAME = re.compile(
    r"tinyassets-(snapshot|box-store)-"
    r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)\.tar\.gz(\.sha256)?\Z"
)


def plan(directory: Path) -> tuple[list[tuple[Path, os.stat_result]], str | None]:
    """Inspect all managed names before selecting any deletion."""
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return [], None
    if not stat.S_ISDIR(mode):
        raise ValueError(f"snapshot directory is not a real directory: {directory}")

    sets: dict[str, dict[str, tuple[Path, os.stat_result]]] = {}
    with os.scandir(directory) as entries:
        for entry in entries:
            match = NAME.fullmatch(entry.name)
            if match is None:
                continue
            info = Path(entry.path).lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"refusing non-regular snapshot file: {entry.name}")
            kind, stamp, checksum = match.groups()
            sets.setdefault(stamp, {})[kind + (".sha256" if checksum else "")] = (
                Path(entry.path), info
            )

    complete = [
        stamp for stamp, files in sets.items()
        if "snapshot" in files and "snapshot.sha256" in files
    ]
    newest = max(complete, default=None)
    remove = []
    for stamp, files in sets.items():
        keep = set()
        if stamp == newest:
            keep.update(("snapshot", "snapshot.sha256"))
            if "box-store" in files and "box-store.sha256" in files:
                keep.update(("box-store", "box-store.sha256"))
        remove.extend(item for kind, item in files.items() if kind not in keep)
    return sorted(remove, key=lambda item: item[0].name), newest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        remove, newest = plan(args.directory)
        freed = 0
        for path, original in remove:
            if args.apply:
                current = path.lstat()
                if not stat.S_ISREG(current.st_mode) or (
                    current.st_dev, current.st_ino, current.st_size
                ) != (original.st_dev, original.st_ino, original.st_size):
                    raise ValueError(f"snapshot file changed during prune: {path.name}")
                path.unlink()
            freed += original.st_size
            action = "deleted" if args.apply else "would delete"
            print(f"{action} {path.name} {original.st_size} bytes")
        print(f"kept={newest or 'none'} {'freed' if args.apply else 'would_free'}={freed} bytes")
    except (OSError, ValueError) as exc:
        print(f"snapshot retention refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
