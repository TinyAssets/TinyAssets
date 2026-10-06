"""Offline, reversible cleanup of legacy provider-child bytes; dry-run by default.

See docs/design-notes/2026-10-05-provider-child-cleanup.md. No deletion is done.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


def plain_directory(path: Path) -> Path:
    """Reject symlinks in every component, including operator-supplied roots."""
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if not stat.S_ISDIR(part.lstat().st_mode):
            raise ValueError(f"not a plain directory: {part}")
    return path


def cleanup(storage_root: Path, universe: str, archive_root: Path, *,
            apply: bool = False, offline: bool = False) -> dict:
    if os.name != "posix":
        raise ValueError("cleanup requires Linux operator filesystem semantics")
    if not universe or universe in {".", ".."} or Path(universe).name != universe:
        raise ValueError("universe must be one immediate directory name")
    root = plain_directory(storage_root)
    home = plain_directory(root / universe)
    archive = plain_directory(archive_root)
    if archive.is_relative_to(root) or root.is_relative_to(archive):
        raise ValueError("archive must be outside and disjoint from storage root")
    archive_stat = archive.stat()
    if archive_stat.st_mode & 0o077 or archive_stat.st_uid != os.geteuid():
        raise ValueError("archive must be private (0700) and owned by this operator")
    if apply and not offline:
        raise ValueError("apply requires --offline: stop all writers and other cleanup runs")
    source = home / ".runtime" / "provider-child"
    digest = hashlib.sha256(str(home).encode()).hexdigest()
    destination = archive / digest
    report = {"source": str(source), "archive": str(destination),
              "files": 0, "bytes": 0, "status": "absent"}
    for part in (home / ".runtime", source):
        if not os.path.lexists(part):
            return report
        plain_directory(part)
    device = source.stat().st_dev
    if device != archive_stat.st_dev or os.path.ismount(source):
        raise ValueError("source and archive must share a filesystem; source cannot be a mount")
    pending = [source]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if info.st_dev != device or os.path.ismount(entry.path):
                    raise ValueError(f"nested filesystem: {entry.path}")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    report["files"] += 1
                    report["bytes"] += info.st_size
                else:
                    raise ValueError(f"symlink or special file: {entry.path}")
    if not report["files"]:
        report["status"] = "empty"
        return report
    if os.path.lexists(destination):
        raise ValueError(f"archive destination already exists: {destination}")
    report["status"] = "dry-run"
    if apply:
        # Atomic, same-device preservation; never copy/delete on EXDEV or error.
        os.rename(source, destination)
        report["status"] = "archived"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--archive-root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="default")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(cleanup(args.storage_root, args.universe, args.archive_root,
                             apply=args.apply, offline=args.offline), sort_keys=True))


if __name__ == "__main__":
    main()
