#!/usr/bin/env python3
"""Validate a streamed box-store tar before extracting it."""

from __future__ import annotations

import argparse
import posixpath
import sys
import tarfile
from pathlib import PurePosixPath


def validate(source: str) -> None:
    if source == "-":
        archive = tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz")
    else:
        archive = tarfile.open(source, mode="r:gz")
    with archive:
        members = list(archive)
    if not members:
        raise ValueError("empty box-store archive")
    symlinks = {member.name.rstrip("/") for member in members if member.issym()}
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or path.parts[0] != "owners" or ".." in path.parts:
            raise ValueError("unsafe box-store archive member")
        if len(path.parts) > 1 and not path.parts[1].isdecimal():
            raise ValueError("box-store archive has a non-owner top-level member")
        if len(path.parts) <= 2 and not member.isdir():
            raise ValueError("box-store archive owner roots must be directories")
        if any(str(parent) in symlinks for parent in path.parents):
            raise ValueError("box-store archive traverses a symlink")
        if member.issym():
            # An owner's symlink target is data. No later archive path may
            # descend through it, so extraction cannot follow it to write.
            continue
        if member.islnk():
            target = member.linkname
            if target.startswith("/"):
                raise ValueError("absolute box-store link")
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(member.name) if member.issym() else "", target)
            )
            if len(path.parts) < 2 or not resolved.startswith(f"owners/{path.parts[1]}/"):
                raise ValueError("box-store link leaves its owner tree")
        elif not (member.isfile() or member.isdir()):
            raise ValueError("unsupported box-store archive member")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    args = parser.parse_args()
    try:
        validate(args.source)
    except (ValueError, OSError, tarfile.TarError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
