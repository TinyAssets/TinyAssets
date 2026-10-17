#!/usr/bin/env python3
"""Rehearse the final owner cutover; reports contain counts and fixed labels only."""

import re
import sys
import tarfile
from pathlib import Path

ARCHIVE_NAME = re.compile(r"tinyassets-data-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz\Z")


def validate_archive(path: Path) -> int:
    """Apply backup-restore.sh's member rules before mounting or extracting."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("--archive requires an absolute readable non-symlink regular file")
    if not ARCHIVE_NAME.fullmatch(path.name):
        raise ValueError("--archive requires a tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz file")
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError("archive is empty")
            symlinks = {member.name.rstrip("/") for member in members if member.issym()}
            for member in members:
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = name.split("/")
                if (
                    name.startswith("/")
                    or not parts
                    or parts[0] != "_data"
                    or any(part in ("", ".", "..") for part in parts)
                ):
                    raise ValueError(f"unsafe archive path: {name!r}")
                if len(parts) == 1 and not member.isdir():
                    raise ValueError(f"archive root is not a directory: {name!r}")
                if any("/".join(parts[:index]) in symlinks for index in range(1, len(parts))):
                    raise ValueError(f"archive descends through a symlink: {name!r}")
                if member.islnk():
                    target = member.linkname.split("/")
                    if (
                        member.linkname.startswith("/")
                        or not target
                        or target[0] != "_data"
                        or ".." in target
                        or member.linkname in symlinks
                    ):
                        raise ValueError(f"unsafe archive hardlink: {name!r}")
                elif not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError(f"unsupported archive member type: {name!r}")
                total += member.size if member.isfile() else 0
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f"archive unreadable: {type(exc).__name__}") from exc
    return total


def archive_rehearsal(image, archive, *, store_bytes):
    from scripts.archive_rehearsal import archive_rehearsal as rehearse

    return rehearse(image, archive, store_bytes=store_bytes)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.archive_rehearsal import main

    main()
