#!/usr/bin/env python3
"""Commit or verify the two immutable members of one owner-box generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

FORMAT = "tinyassets-owner-box-pair-v1"


def member(path: Path) -> dict[str, int | str]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError(f"unsafe backup member: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        if os.name == "posix":
            os.fsync(source.fileno())
    after = path.lstat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    ):
        raise ValueError(f"backup member changed during verification: {path}")
    return {"sha256": digest.hexdigest(), "bytes": info.st_size}


def create(data: Path, box: Path, manifest: Path) -> None:
    if data.parent != box.parent or data.parent != manifest.parent:
        raise ValueError("paired members and manifest must share a staging directory")
    members = {path.name: member(path) for path in (data, box)}
    document = {"format": FORMAT, "generation": str(uuid.uuid4()), "members": members}
    with manifest.open("x", encoding="utf-8") as target:
        json.dump(document, target, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    if os.name == "posix":
        directory = os.open(manifest.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def verify(data: Path, box: Path, manifest: Path) -> None:
    info = manifest.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe paired manifest")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if (type(document) is not dict or document.get("format") != FORMAT
            or type(document.get("generation")) is not str
            or not document["generation"]):
        raise ValueError("invalid paired backup manifest")
    expected = document.get("members")
    if type(expected) is not dict or set(expected) != {data.name, box.name}:
        raise ValueError("paired backup names differ from manifest")
    for path in (data, box):
        if expected[path.name] != member(path):
            raise ValueError(f"paired backup digest differs: {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("create", "verify"))
    parser.add_argument("data", type=Path)
    parser.add_argument("box", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            create(args.data, args.box, args.manifest)
        else:
            verify(args.data, args.box, args.manifest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"paired backup refused: {exc}\n")


if __name__ == "__main__":
    main()
