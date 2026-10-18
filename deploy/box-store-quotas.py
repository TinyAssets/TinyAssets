#!/usr/bin/env python3
"""Reapply XFS project quotas from the paired broker identity database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.box_project_readback import verify_owner, verify_tree  # noqa: E402
from tinyassets.sqlite_readonly import readonly_db  # noqa: E402


def reapply(store: Path, trees: Path, registry: Path, *, clear: bool = False) -> None:
    owner_dirs = {
        entry.name for entry in trees.iterdir()
        if entry.name.isdecimal() and entry.is_dir() and not entry.is_symlink()
    }
    if not owner_dirs:
        return
    if registry.is_symlink() or not registry.is_file():
        raise ValueError("paired broker identity database is absent or not regular")
    with readonly_db(registry) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("paired broker identity database failed integrity check")
        rows = db.execute("SELECT machine, project_id, bhard_bytes FROM box_records").fetchall()
    by_uid = {}
    for uid, project, limit in rows:
        if (any(type(v) is not int for v in (uid, project, limit))
                or not 300001 <= uid <= 399999 or project != uid
                or limit <= 0 or limit % 1024 or str(uid) in by_uid):
            raise ValueError("invalid permanent owner quota reservation")
        by_uid[str(uid)] = {"gid": uid, "project_id": uid, "bhard_bytes": limit}
    # An admitted owner may not have bound its first runtime yet. Every actual
    # tree needs a reservation; an unused reservation needs no fabricated tree.
    if not owner_dirs <= by_uid.keys():
        raise ValueError("box archive contains an owner absent from its paired broker database")
    for uid in sorted(owner_dirs):
        row = by_uid[uid]
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
        if clear:
            verify_tree(tree, 0, False)
        if not clear:
            subprocess.run(
                ["xfs_quota", "-x", "-c", f"limit -p bhard={limit // 1024}k {project}", str(store)],
                check=True,
            )
            verify_owner(store, tree, project, limit)


def _cutover_manifest(root: Path, *, exclude=()) -> dict:
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in files + [n for n in dirs if (Path(directory) / n).is_symlink()]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative in exclude:
                continue
            if path.is_symlink():
                result[relative] = "link:" + os.readlink(path)
            else:
                with path.open("rb") as stream:
                    result[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def _restore_cutover_source(current: Path, staged: Path, restored_store: Path) -> None:
    """Normalize the verified legacy pair through the one forward cutover."""
    from dataclasses import asdict

    from tinyassets.box_layout_migration import (
        _build_cutover,
        _owner_plan,
        _publish_cutover,
    )

    path = current / ".owner-cutover.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("verified cutover source receipt missing")
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("cutover receipt is not root-private")
    receipt = json.loads(path.read_text())
    if receipt.get("version") != 1 or receipt.get("phase") != "complete":
        raise ValueError("cutover receipt is malformed")
    if _cutover_manifest(staged, exclude=(".tinyassets.db.prebudgetrepair.bak",)) != receipt.get(
            "source_manifest"):
        raise ValueError("restore is not the verified cutover source")
    journal = dict(receipt, phase="build", store=str(restored_store.resolve()),
                   plan=[asdict(owner) for owner in _owner_plan(staged)])
    _build_cutover(staged, restored_store, journal, apply_quotas=False)
    _publish_cutover(staged, restored_store, journal)


def fence_restore(current: Path, staged: Path, *, cutover_store: Path | None = None,
                  restored_store: Path | None = None, disaster: bool = False) -> None:
    """Fence rollback receipts; never roll back permanent identities or deletion."""
    def layout(root):
        marker = root / ".layout.json"
        return json.loads(marker.read_text()).get("box_layout") if marker.exists() else None

    staged_layout = layout(staged)
    # Disaster recovery has no trustworthy live layout. Only a verified final
    # backup can supply it; legacy normalization needs the live cutover receipt.
    current_layout = staged_layout if disaster else layout(current)
    if disaster and staged_layout != "owner-box":
        raise ValueError("disaster restore requires a verified owner-box backup pair")
    if "owner-box" not in (current_layout, staged_layout):
        return
    if current_layout == "owner-box" and staged_layout in (None, "owner-split"):
        if restored_store is None:
            raise ValueError("owner-box restore requires both owner stores")
        _restore_cutover_source(current, staged, restored_store)
    if current_layout != "owner-box" or layout(staged) != "owner-box":
        raise ValueError("owner-box restore requires reviewed forward layout recovery")

    from tinyassets.restore_facts import carry_shared, erase_trees

    if cutover_store is None or restored_store is None:
        raise ValueError("owner-box restore requires both owner stores")
    erased = carry_shared(current, staged, disaster=disaster)
    fence_owner_journals(cutover_store, restored_store, erased=erased, disaster=disaster)
    erase_trees(restored_store, erased)
    receipt = staged / ".owner-cutover.json"
    if receipt.exists():
        from tinyassets.box_layout_migration import _atomic_json

        journal = json.loads(receipt.read_text())
        journal["store"] = str(cutover_store.resolve())
        journal["plan"] = [owner for owner in journal["plan"] if owner["uid"] not in erased]
        journal["owner_manifests"] = {
            tree.name: _cutover_manifest(tree) for tree in restored_store.iterdir()
            if tree.name.isdecimal() and tree.is_dir()}
        journal["final_manifest"] = _cutover_manifest(staged, exclude=(".owner-cutover.json",))
        _atomic_json(receipt, journal)


def _owner_journals(root: Path, *, disaster: bool = False) -> dict:
    if disaster and not root.exists() and not root.is_symlink():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError("restore owner store is not a directory")
    result = {}
    for tree in root.iterdir():
        if not tree.name.isdecimal():
            continue
        if tree.is_symlink() or not tree.is_dir():
            raise ValueError("unsafe restore owner tree")
        directory = tree / ".owner-boxd"
        if not directory.exists() and not directory.is_symlink():
            continue  # This owner has never started its local tools.
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("unsafe owner journal directory")
        path = directory / "boxhost.db"
        for member in directory.iterdir():
            if member.name in {"boxhost.db", "boxhost.db-wal", "boxhost.db-shm",
                               "boxhost.db-journal", "boxhost.lock"}:
                info = member.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("unsafe owner journal member")
        try:
            with readonly_db(path) as db:
                if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    if disaster:
                        continue
                    raise ValueError("owner journal failed integrity check")
                result[tree.name] = {
                    table: db.execute(f"SELECT * FROM {table}").fetchall()
                    for table in ("host", "boxes", "ops")
                }
        except (OSError, sqlite3.DatabaseError):
            if not disaster:
                raise
    return result


def fence_owner_journals(current: Path, staged: Path, *, erased=frozenset(),
                         disaster: bool = False) -> None:
    """Offline only: preserve refusal authority in each restored owner's box."""
    from tinyassets.boxes.state import _SCHEMA

    live, restored = _owner_journals(current, disaster=disaster), _owner_journals(staged)
    for uid in sorted(live.keys() | restored.keys()):
        if int(uid) in erased:
            continue
        tree = staged / uid
        tree.mkdir(mode=0o700, exist_ok=True)
        if tree.is_symlink() or not tree.is_dir():
            raise ValueError("unsafe restored owner tree")
        directory = tree / ".owner-boxd"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / "boxhost.db"
        with closing(sqlite3.connect(path)) as target, target:
            target.execute("PRAGMA synchronous=FULL")
            target.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                target.execute(statement)
            for row in live.get(uid, {}).get("ops", []):
                previous = target.execute("SELECT kind,digest FROM ops WHERE "
                                          "command_center_id=? AND op_id=?", row[:2]).fetchone()
                if previous is not None and previous != row[2:4]:
                    raise ValueError("restored operation id has conflicting arguments")
                target.execute("INSERT OR IGNORE INTO ops VALUES (?,?,?,?,?,?,?)", row)
            for owner, epoch, generation in live.get(uid, {}).get("boxes", []):
                target.execute("INSERT INTO boxes VALUES (?,?,?) ON CONFLICT(command_center_id) "
                               "DO UPDATE SET epoch=MAX(epoch,excluded.epoch), "
                               "generation=MAX(generation,excluded.generation)",
                               (owner, epoch, generation))
            target.execute("UPDATE boxes SET epoch=epoch+1, generation=generation+1")
            target.execute("UPDATE ops SET state='unknown_after_restore'")
            incarnation = max((row[1] for journal in (live, restored)
                               for row in journal.get(uid, {}).get("host", [])
                               if row[0] == "incarnation"), default=0)
            target.execute("INSERT INTO host VALUES ('incarnation',?) "
                           "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (incarnation + 1,))
        # New journals may not have existed in the snapshot. They belong to the
        # restored owner, including SQLite sidecars, never to the restore uid.
        members = [directory / name for name in ("boxhost.db", "boxhost.db-wal",
                   "boxhost.db-shm", "boxhost.db-journal", "boxhost.lock")
                   if (directory / name).exists()]
        for member in (tree, directory, *members):
            os.chown(member, int(uid), int(uid))
            os.chmod(member, 0o700 if member.is_dir() else 0o600)


def check_snapshot(data: Path, *, quiescent: bool = False) -> None:
    """Caller holds the exclusive layout lock; no mutation or recovery here."""
    marker = data / ".layout.json"
    if not marker.exists():
        return  # pre-owner-box archive
    layout = json.loads(marker.read_text())
    if layout.get("box_layout") != "owner-box":
        return
    if layout.get("state") != "stable":
        raise ValueError("owner-box snapshot layout is not stable")
    root, database = data / ".boxhost", data / ".boxhost/boxhost.db"
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid, stat.S_IMODE(info.st_mode)) != (0, 0o700):
        raise ValueError("snapshot needs its root-private host receipt journal")
    for entry in root.iterdir():
        if entry.name not in {"boxhost.db", "boxhost.db-wal", "boxhost.db-shm", "boxhost.lock"}:
            raise ValueError("unexpected host journal member")
        member = entry.lstat()
        if (not stat.S_ISREG(member.st_mode) or member.st_uid != 0
                or member.st_nlink != 1 or member.st_mode & 0o077):
            raise ValueError("unsafe host journal sidecar")
    info = database.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or info.st_mode & 0o077):
        raise ValueError("snapshot receipt database has unsafe identity or mode")
    with readonly_db(database) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("snapshot receipt journal failed integrity check")
        for table in ("host", "boxes", "ops", "execs", "owner_destroys"):
            db.execute(f"SELECT * FROM {table} LIMIT 0")
    if quiescent:
        result = subprocess.run([
            "/opt/tinyassets/boxhostd/current/runsc", "--root", "/var/lib/boxhostd/state/runsc",
            "--systemd-cgroup=false", "list", "--format=json"],
            check=True, capture_output=True, text=True, timeout=10)
        if json.loads(result.stdout) != []:
            raise ValueError("surviving owner sandbox prevents coherent snapshot/restore")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("store", type=Path, nargs="?")
    parser.add_argument("trees", type=Path, nargs="?")
    parser.add_argument("registry", type=Path, nargs="?")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--snapshot-data", type=Path)
    parser.add_argument("--quiescent", action="store_true")
    parser.add_argument("--restore-from", type=Path)
    parser.add_argument("--cutover-store", type=Path)
    parser.add_argument("--restored-store", type=Path)
    parser.add_argument("--disaster-restore", action="store_true")
    parser.add_argument("--acknowledge-unrecoverable-facts", action="store_true")
    parser.add_argument("--backup-time")
    args = parser.parse_args()
    if args.disaster_restore != args.acknowledge_unrecoverable_facts:
        parser.error("disaster restore requires --acknowledge-unrecoverable-facts")
    if args.disaster_restore and not (args.snapshot_data and args.restore_from
                                      and args.backup_time):
        parser.error("disaster restore requires a staged backup, live path and backup time")
    if args.snapshot_data:
        if args.store or args.trees or args.registry or args.clear:
            parser.error("snapshot verification does not accept quota mutation arguments")
        check_snapshot(args.snapshot_data.resolve(), quiescent=args.quiescent)
        if args.restore_from:
            fence_restore(args.restore_from.resolve(), args.snapshot_data.resolve(),
                          cutover_store=args.cutover_store, restored_store=args.restored_store,
                          disaster=args.disaster_restore)
            if args.disaster_restore:
                from tinyassets.restore_facts import disaster_unknowns

                print("\n".join(disaster_unknowns(args.restore_from, args.snapshot_data,
                                                  args.backup_time)))
    elif not all((args.store, args.trees, args.registry)) or args.quiescent or args.restore_from:
        parser.error("quota restoration requires store, trees, paired broker database")
    else:
        reapply(args.store, args.trees, args.registry, clear=args.clear)
