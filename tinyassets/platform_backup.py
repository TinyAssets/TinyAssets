"""C2/C3/T3 shared-layer backups and retained-map snapshot pins.

The input is the shared data volume after cutover. Owner volumes are not inputs.
The host lock spans map capture through remote publication and snapshot pruning;
it never serializes ordinary box work or snapshot upload.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

CATALOG = "platform"
MANIFEST = re.compile(r"tinyassets-platform-(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ)\.json")
LOCK = Path("/var/lock/tinyassets-snapshot-retention.lock")


@contextmanager
def retention_lock():
    import fcntl

    with LOCK.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def remote(*args):
    return subprocess.run(["rclone", *map(str, args)], check=True, capture_output=True,
                          text=True, timeout=3600).stdout


def manifests(destination):
    """Missing/unreadable catalog denies pruning; no empty-on-error fallback."""
    result = {}
    for name in remote("lsf", "--files-only", f"{destination}/{CATALOG}").splitlines():
        if not MANIFEST.fullmatch(name):
            continue
        document = json.loads(remote("cat", f"{destination}/{CATALOG}/{name}"))
        validate_manifest(name, document)
        result[name] = document
    return result


def validate_manifest(name, document):
    if document.get("version") != 1 or not isinstance(document.get("snapshots"), list):
        raise ValueError("unreadable retained platform backup")
    stamp = MANIFEST.fullmatch(name)[1]
    if (document.get("archive") != f"tinyassets-data-{stamp}.tar.gz"
            or not re.fullmatch(r"[a-f0-9]{64}", document.get("sha256", ""))):
        raise ValueError("unreadable retained platform archive")
    for binding in document["snapshots"]:
        if (type(binding.get("uid")) is not int
                or not 300001 <= binding["uid"] <= 399999
                or not re.fullmatch(r"[a-f0-9]{32}", binding.get("snapshot_id", ""))
                or not re.fullmatch(r"[a-f0-9]{64}", binding.get("sha256", ""))):
            raise ValueError("unreadable retained volume binding")


def pinned_snapshots(destination):
    return {(row["uid"], row["snapshot_id"])
            for doc in manifests(destination).values() for row in doc["snapshots"]}


def _copy_shared(source, target, relative=Path()):
    """Copy shared storage only; SQLite uses its online transaction snapshot.

    Symlinks are preserved without dereferencing. Runtime sockets and SQLite
    sidecars are not durable stores. Browser vault keys have separate custody.
    A vanished or unreadable shared file fails the entire unpublished backup.
    """
    info = source.lstat()
    if stat.S_ISDIR(info.st_mode):
        target.mkdir()
        for child in source.iterdir():
            name = relative / child.name
            if name == Path(".broker/browser-vault"):
                continue
            if child.name.endswith(("-wal", "-shm", "-journal")):
                continue
            _copy_shared(child, target / child.name, name)
    elif stat.S_ISLNK(info.st_mode):
        target.symlink_to(os.readlink(source))
        os.lchown(target, info.st_uid, info.st_gid)
        return
    elif stat.S_ISSOCK(info.st_mode):
        return
    elif stat.S_ISREG(info.st_mode):
        with source.open("rb") as stream:
            sqlite = stream.read(16) == b"SQLite format 3\0"
        if sqlite:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live:
                with closing(sqlite3.connect(target)) as copy:
                    live.backup(copy)
                    if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise ValueError("shared database backup failed integrity check")
        else:
            shutil.copyfile(source, target)
    else:
        raise ValueError("unsupported shared storage inode")
    shutil.copystat(source, target, follow_symlinks=False)
    os.chown(target, info.st_uid, info.st_gid)


def _bindings(stage):
    registry = stage / ".broker/state/owner-identities.db"
    with closing(sqlite3.connect(registry.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT uid,snapshot_id,snapshot_sha256 FROM owner_volumes "
                          "WHERE snapshot_id IS NOT NULL").fetchall()
    return [dict(zip(("uid", "snapshot_id", "sha256"), row)) for row in rows]


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(root, destination, *, retention=(7, 4, 6)):
    import fcntl

    from scripts.backup_prune import _apply_retention_one_tier

    root = Path(root).absolute()
    destination = destination.rstrip("/")
    # The layout lock is the same inode held by serving and cutover.
    with (root / ".layout.lock").open("rb") as layout_lock, retention_lock():
        fcntl.flock(layout_lock, fcntl.LOCK_SH)
        layout = json.loads((root / ".layout.json").read_text())
        if layout.get("state") != "stable" or layout.get("box_layout") != "owner-box":
            raise ValueError("platform backup requires the committed shared-only layout")
        remote("mkdir", f"{destination}/{CATALOG}")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        name = f"tinyassets-platform-{stamp}.json"
        if name in manifests(destination):
            raise FileExistsError("platform backup timestamp already published")
        with tempfile.TemporaryDirectory(prefix="ta-platform-backup-") as directory:
            work = Path(directory)
            stage = work / "_data"
            _copy_shared(root, stage)
            references = _bindings(stage)
            archive = work / f"tinyassets-data-{stamp}.tar.gz"
            # GNU tar preserves shared ownership, ACLs and xattrs for C3 restore.
            subprocess.run(["tar", "-czf", str(archive), "--numeric-owner", "--acls",
                            "--xattrs", "-C", str(work), "_data"], check=True, timeout=3600)
            digest = _digest(archive)
            remote("copyto", archive, f"{destination}/{archive.name}")
            readback = work / "readback.tar.gz"
            remote("copyto", f"{destination}/{archive.name}", readback)
            if _digest(readback) != digest:
                raise ValueError("platform backup off-host readback mismatch")
            with tarfile.open(readback) as verified:
                verified.getmembers()
            document = {"version": 1, "archive": archive.name, "sha256": digest,
                        "snapshots": references}
            validate_manifest(name, document)
            manifest = work / name
            manifest.write_text(json.dumps(document, sort_keys=True))
            pending = f"{destination}/{CATALOG}/{name}.pending"
            remote("copyto", manifest, pending)
            if json.loads(remote("cat", pending)) != document:
                raise ValueError("platform manifest off-host readback mismatch")
            remote("moveto", pending, f"{destination}/{CATALOG}/{name}")
            # Manifest removal withdraws the recovery point before releasing pins.
            catalog = manifests(destination)
            for victim in _apply_retention_one_tier(list(catalog), *retention):
                remote("deletefile", f"{destination}/{CATALOG}/{victim}")
                remote("deletefile", f"{destination}/{catalog[victim]['archive']}")
            return document
