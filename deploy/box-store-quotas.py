#!/usr/bin/env python3
"""Reapply XFS project quotas from the broker's exported box registry."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def reapply(store: Path, trees: Path, registry: Path, *, clear: bool = False) -> None:
    owner_dirs = {
        entry.name for entry in trees.iterdir()
        if entry.name.isdecimal() and entry.is_dir() and not entry.is_symlink()
    }
    if not owner_dirs:
        return
    rows = json.loads(registry.read_text())["boxes"]
    by_uid = {str(row["uid"]): row for row in rows}
    projects = {int(row["project_id"]) for row in rows}
    if (
        len(by_uid) != len(rows)
        or len(projects) != len(rows)
        or owner_dirs != by_uid.keys()
        or not all(name.isdecimal() for name in owner_dirs)
    ):
        raise ValueError("box archive and broker registry owners differ")
    for uid, row in by_uid.items():
        gid = int(row["gid"])
        project = int(row["project_id"])
        limit = int(row["bhard_bytes"])
        if min(int(uid), gid, project, limit) <= 0 or limit % 1024:
            raise ValueError("invalid box registry quota row")
        tree = trees / uid
        os.chown(tree, int(uid), gid)
        os.chmod(tree, 0o700)
        if clear:
            command = f"project -C -p {tree} {project}"
        else:
            command = f"project -s -p {tree} {project}"
        subprocess.run(["xfs_quota", "-x", "-c", command, str(store)], check=True)
        if not clear:
            subprocess.run(
                ["xfs_quota", "-x", "-c", f"limit -p bhard={limit // 1024}k {project}", str(store)],
                check=True,
            )
    subprocess.run(["xfs_quota", "-x", "-c", "report -p -n", str(store)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("store", type=Path)
    parser.add_argument("trees", type=Path)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()
    reapply(args.store, args.trees, args.registry, clear=args.clear)
