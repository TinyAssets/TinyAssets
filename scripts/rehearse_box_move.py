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


def restore_replay_probe(mode: str) -> int:
    """Synthetic operations in the disposable venue, through real owner tools."""
    import json
    import runpy
    import sqlite3
    import subprocess

    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxError

    data, store, backup = Path("/data"), Path("/box-store"), Path("/backup/replay")
    backup.mkdir(exist_ok=True)
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        owners = db.execute("SELECT principal,machine FROM box_records").fetchall()
    proof = backup / "proof.json"
    epochs = json.loads(proof.read_text()) if mode == "verify" else {}
    for owner, uid in owners:
        tree = store / str(uid)
        core = OwnerBoxd(owner_id=owner, owner_root=tree, state_dir=tree / ".owner-boxd")
        try:
            handle = core.bind(owner)
            if mode == "snapshot":
                core.write(handle, "rehearsal-before", "/cc/.replay-proof", b"snapshot",
                           max_bytes=100)
                epochs[owner] = handle.epoch
            elif mode == "write":
                core.write(handle, "rehearsal-after", "/cc/.replay-proof", b"after", max_bytes=100)
            elif mode == "verify":
                if handle.epoch <= epochs[owner]:
                    raise RuntimeError("owner epoch did not advance")
                if (tree / ".replay-proof").read_bytes() != b"snapshot":
                    raise RuntimeError("owner snapshot was not restored")
                core.write(handle, "rehearsal-later", "/cc/.replay-proof", b"later work",
                           max_bytes=100)
                for op_id, content in (("rehearsal-before", b"snapshot"),
                                       ("rehearsal-after", b"after")):
                    try:
                        core.write(handle, op_id, "/cc/.replay-proof", content, max_bytes=100)
                    except BoxError as exc:
                        if "not re-run" not in str(exc):
                            raise
                    else:
                        raise RuntimeError("restored operation was admitted again")
                if (tree / ".replay-proof").read_bytes() != b"later work":
                    raise RuntimeError("replayed operation changed later work")
            else:
                raise ValueError("unknown replay proof phase")
        finally:
            core.close()
    if mode == "snapshot":
        proof.write_text(json.dumps(epochs))
        stamp = "2026-10-17T01-00-00Z"
        shared = backup / f"tinyassets-data-{stamp}.tar.gz"
        boxes = backup / f"tinyassets-box-store-{stamp}.tar.gz"
        subprocess.run(["tar", "-czf", str(shared), "--transform=s,^data,_data,",
                        "-C", "/", "--numeric-owner", "--acls", "--xattrs", "data"], check=True)
        subprocess.run(["tar", "-czf", str(boxes), "--transform=s,^,owners/,",
                        "-C", str(store), "--numeric-owner", "--acls", "--xattrs",
                        *(str(uid) for _, uid in owners)], check=True)
        runpy.run_path("/support/deploy/backup-pair.py")["create"](
            shared, boxes, backup / f"tinyassets-pair-{stamp}.json")
    return len(owners)


def archive_rehearsal(image, archive, *, store_bytes):
    from scripts.archive_rehearsal import archive_rehearsal as rehearse

    return rehearse(image, archive, store_bytes=store_bytes)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.archive_rehearsal import main

    main()
