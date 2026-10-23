#!/usr/bin/env python3
"""C2/C3 drill: restore one map-referenced opaque image in a disposable store."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tinyassets.platform_backup import validate_manifest  # noqa: E402


def select(manifest, archive, directory):
    document = json.loads(manifest.read_text())
    validate_manifest(manifest.name, document)
    with archive.open("rb") as stream:
        if archive.name != document["archive"] or hashlib.file_digest(
                stream, "sha256").hexdigest() != document["sha256"]:
            raise ValueError("platform manifest archive digest mismatch")
    registry = directory / "owner-identities.db"
    with tarfile.open(archive) as source:
        member = source.getmember("_data/.broker/state/owner-identities.db")
        if not member.isfile():
            raise ValueError("platform owner map is not a regular file")
        with source.extractfile(member) as data, registry.open("wb") as target:
            shutil.copyfileobj(data, target)
    registry.chmod(0o600)
    with sqlite3.connect(registry.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT v.principal,v.uid,v.snapshot_id,v.snapshot_sha256,v.volume_generation "
            "FROM owner_volumes v JOIN owner_identities i "
            "ON i.principal=v.principal AND i.machine_id=v.uid "
            "JOIN box_records b ON b.principal=v.principal AND b.machine=v.uid "
            "WHERE v.snapshot_id IS NOT NULL ORDER BY v.uid").fetchall()
    for owner, uid, snapshot_id, sha256, generation in rows:
        if {"uid": uid, "snapshot_id": snapshot_id, "sha256": sha256} in document["snapshots"]:
            return owner, uid, snapshot_id, sha256, generation, registry
    raise ValueError("single-owner drill needs an admitted map-referenced snapshot")


def restore(manifest, archive, directory, selected):
    from tinyassets.boxes.volumes import OwnerVolumes
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.routing import BrokerLifecycle

    owner, uid, snapshot_id, sha256, generation, registry = selected
    snapshot = manifest.parent / str(uid) / f"{snapshot_id}.img.gz"
    length, digest = 0, hashlib.sha256()
    with gzip.open(snapshot, "rb") as stream:
        while chunk := stream.read(1024**2):
            length += len(chunk)
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise ValueError("map-referenced snapshot digest mismatch")
    outer = directory / "store.img"
    with outer.open("wb") as stream:
        stream.truncate(max(4 * 1024**3, length + 2 * 1024**3))
    def run(*args):
        return subprocess.run(args, check=True, capture_output=True,
                              text=True, timeout=600).stdout.strip()
    run("mkfs.xfs", "-f", "-m", "reflink=1", str(outer))
    store = directory / "store"
    store.mkdir()
    device = run("losetup", "--find", "--show", str(outer))
    mounted = False
    try:
        run("mount", "-t", "xfs", device, str(store))
        mounted = True
        volumes = OwnerVolumes(store, directory / "owner-mounts", ":local:" + str(manifest.parent))
        identities = OwnerIdentities(registry)
        def execute(operation, **fields):
            if operation == "sleep":
                # This disposable store has no owner processes. Complete the
                # same detach acknowledgement before the copy-only restore.
                volumes.detach(fields["uid"])
                return {"stopped": True}
            if operation == "snapshot":
                snapshot_id, digest = volumes.snapshot(fields["uid"], fields["snapshot_id"])
                return {"snapshot_id": snapshot_id, "sha256": digest}
            assert operation == "restore"
            volumes.restore(fields["uid"], fields["snapshot_id"], fields["sha256"])
            return {"restored": True}
        # The broker orchestrates the map; this private drill has no process to stop.
        lifecycle = BrokerLifecycle(SimpleNamespace(
            _owner_identities=identities, _data_root=directory))
        lifecycle.volumes.execute = execute
        restored = asyncio.run(lifecycle.restore(owner, snapshot_id))
        assert restored["volume_generation"] == generation + 1
        with (store / f"{uid}.img").open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == sha256
        volumes.delete(uid)
        print("single-owner restore verified: opaque bytes and new generation; platform unchanged")
    finally:
        if mounted:
            run("umount", str(store))
        run("losetup", "--detach", device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("reference", "restore"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.action == "restore" and os.geteuid() != 0:
        parser.error("restore drill requires root")
    with tempfile.TemporaryDirectory(prefix="ta-dr-owner-") as temporary:
        directory = Path(temporary)
        selected = select(args.manifest, args.archive, directory)
        if args.action == "reference":
            print(f"{selected[1]}/{selected[2]}.img.gz")
        else:
            restore(args.manifest, args.archive, directory, selected)


if __name__ == "__main__":
    main()
