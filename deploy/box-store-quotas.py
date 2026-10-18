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


def reapply(store: Path, trees: Path, registry: Path, *, clear: bool = False) -> None:
    owner_dirs = {
        entry.name for entry in trees.iterdir()
        if entry.name.isdecimal() and entry.is_dir() and not entry.is_symlink()
    }
    if not owner_dirs:
        return
    if registry.is_symlink() or not registry.is_file():
        raise ValueError("paired broker identity database is absent or not regular")
    with sqlite3.connect(registry.resolve().as_uri() + "?mode=ro", uri=True) as db:
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


def _pristine_cutover_restore(current: Path, staged: Path, store: Path | None, *,
                             accept_owner_write_loss: bool = False) -> bool:
    """Exact paired source; owner writes may be explicitly surrendered."""
    path = current / ".owner-cutover.json"
    if store is None or not path.is_file() or path.is_symlink():
        return False
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("cutover receipt is not root-private")
    receipt = json.loads(path.read_text())
    if receipt.get("version") != 1 or receipt.get("phase") != "complete":
        return False
    if _cutover_manifest(staged, exclude=(".tinyassets.db.prebudgetrepair.bak",)) != receipt.get(
            "source_manifest"):
        raise ValueError("restore is not the verified cutover source")
    actual_shared = _cutover_manifest(current, exclude=(".owner-cutover.json",))
    expected_shared = receipt.get("final_manifest")
    if accept_owner_write_loss:
        from tinyassets.box_layout_migration import cutover_identity_manifest

        if cutover_identity_manifest(current) != receipt.get("identity_manifest"):
            raise ValueError("post-cutover identity authority requires forward recovery")
        # Serving reports the next wake even when no shared authority changes.
        # This derived index is recreated from restored owner schedules; all
        # identity, deletion, billing, custody and operation receipts stay fenced.
        runtime_files = {".broker/state/" + database + suffix
                         for database in ("next-wake.db", "owner-identities.db")
                         for suffix in ("", "-wal", "-shm", "-journal")}
        actual_shared = {key: value for key, value in actual_shared.items()
                         if key not in runtime_files}
        expected_shared = {key: value for key, value in (expected_shared or {}).items()
                           if key not in runtime_files}
    if actual_shared != expected_shared:
        raise ValueError("post-cutover shared writes require forward recovery")
    expected = receipt.get("owner_manifests", {})
    actual = {p.name for p in store.iterdir() if p.name.isdecimal()}
    if actual != set(expected):
        raise ValueError("cutover owner tree inventory changed")
    for uid, manifest in expected.items():
        if not uid.isdecimal() or (store / uid).is_symlink():
            raise ValueError("unsafe cutover owner tree")
        if not accept_owner_write_loss and _cutover_manifest(store / uid) != manifest:
            raise ValueError("post-cutover owner writes require forward recovery")
    if accept_owner_write_loss and any(
            journal["ops"] for journal in _owner_journals(store).values()):
        # The old layout has no owner journal to carry these refusal records.
        # Explicit content loss cannot authorize forgetting operation authority.
        raise ValueError("post-cutover owner operation authority requires forward recovery")
    return True


def fence_restore(current: Path, staged: Path, *, cutover_store: Path | None = None,
                  accept_owner_write_loss: bool = False,
                  restored_store: Path | None = None) -> None:
    """Fence rollback receipts; never roll back permanent identities or deletion."""
    def layout(root):
        marker = root / ".layout.json"
        return json.loads(marker.read_text()).get("box_layout") if marker.exists() else None

    if "owner-box" not in (layout(current), layout(staged)):
        return
    if layout(current) == "owner-box" and layout(staged) in (None, "owner-split"):
        if _pristine_cutover_restore(current, staged, cutover_store,
                                    accept_owner_write_loss=accept_owner_write_loss):
            return
    if layout(current) != "owner-box" or layout(staged) != "owner-box":
        raise ValueError("owner-box restore requires reviewed forward layout recovery")

    def rows(root, relative, table, *, optional=False):
        path = root / relative
        if path.is_symlink():
            raise ValueError("restore authority is a symlink")
        if optional and not path.exists():
            return []
        with sqlite3.connect((root / relative).as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("restore authority failed integrity check")
            return sorted(db.execute(f"SELECT * FROM {table}").fetchall())

    # These authorities must not be reconstructed from an older archive.
    # C12/integration owns forward retention of deletion tombstones and the
    # immutable identity frontier. Refuse until that contract is integrated.
    for table in ("owner_identities", "center_admissions", "box_records",
                  "owner_box_revocations"):
        relative = ".broker/state/owner-identities.db"
        if rows(current, relative, table) != rows(staged, relative, table):
            raise ValueError("identity/admission frontier differs; forward recovery needed")
    relative = ".router-deleted-owners.sqlite3"
    if rows(current, relative, "deleted_owners", optional=True) != rows(
            staged, relative, "deleted_owners", optional=True):
        raise ValueError("router deletion frontier differs; forward recovery needed")
    if rows(current, ".broker/credential-custody.db", "deleted_principals") != rows(
        staged, ".broker/credential-custody.db", "deleted_principals"
    ):
        raise ValueError("deletion tombstones differ; C12 forward recovery needed")
    relative = ".boxhost/boxhost.db"
    if rows(current, relative, "owner_destroys") != rows(staged, relative, "owner_destroys"):
        raise ValueError("owner destruction frontier differs; forward recovery needed")
    if cutover_store is None or restored_store is None:
        raise ValueError("owner-box restore requires both owner stores")
    fence_owner_journals(cutover_store, restored_store)


def _owner_journals(root: Path) -> dict:
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
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("owner journal failed integrity check")
            result[tree.name] = {
                table: db.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("host", "boxes", "ops")
            }
    return result


def fence_owner_journals(current: Path, staged: Path) -> None:
    """Offline only: preserve refusal authority in each restored owner's box."""
    from tinyassets.boxes.state import _SCHEMA

    live, restored = _owner_journals(current), _owner_journals(staged)
    for uid in sorted(live.keys() | restored.keys()):
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
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
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
    parser.add_argument("--accept-owner-write-loss", action="store_true")
    args = parser.parse_args()
    if args.snapshot_data:
        if args.store or args.trees or args.registry or args.clear:
            parser.error("snapshot verification does not accept quota mutation arguments")
        check_snapshot(args.snapshot_data.resolve(), quiescent=args.quiescent)
        if args.restore_from:
            fence_restore(args.restore_from.resolve(), args.snapshot_data.resolve(),
                          cutover_store=args.cutover_store, restored_store=args.restored_store,
                          accept_owner_write_loss=args.accept_owner_write_loss)
    elif not all((args.store, args.trees, args.registry)) or args.quiescent or args.restore_from:
        parser.error("quota restoration requires store, trees, paired broker database")
    else:
        reapply(args.store, args.trees, args.registry, clear=args.clear)
