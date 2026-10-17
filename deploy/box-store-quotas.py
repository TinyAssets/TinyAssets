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


def _pristine_cutover_restore(current: Path, staged: Path, store: Path | None) -> bool:
    """Only the exact paired source before any post-cutover write can go back."""
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
    if _cutover_manifest(current, exclude=(".owner-cutover.json",)) != receipt.get(
            "final_manifest"):
        raise ValueError("post-cutover shared writes require forward recovery")
    expected = receipt.get("owner_manifests", {})
    actual = {p.name for p in store.iterdir() if p.name.isdecimal()}
    if actual != set(expected):
        raise ValueError("cutover owner tree inventory changed")
    for uid, manifest in expected.items():
        if not uid.isdecimal() or (store / uid).is_symlink():
            raise ValueError("unsafe cutover owner tree")
        if _cutover_manifest(store / uid) != manifest:
            raise ValueError("post-cutover owner writes require forward recovery")
    return True


def fence_restore(current: Path, staged: Path, *, cutover_store: Path | None = None) -> None:
    """Fence rollback receipts; never roll back permanent identities or deletion."""
    def layout(root):
        marker = root / ".layout.json"
        return json.loads(marker.read_text()).get("box_layout") if marker.exists() else None

    if "owner-box" not in (layout(current), layout(staged)):
        return
    if layout(current) == "owner-box" and layout(staged) in (None, "owner-split"):
        if _pristine_cutover_restore(current, staged, cutover_store):
            return
    if layout(current) != "owner-box" or layout(staged) != "owner-box":
        raise ValueError("owner-box restore requires reviewed forward layout recovery")

    def rows(root, relative, table):
        with sqlite3.connect((root / relative).as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("restore authority failed integrity check")
            return sorted(db.execute(f"SELECT * FROM {table}").fetchall())

    # These authorities must not be reconstructed from an older archive.
    # C12/integration owns forward retention of deletion tombstones and the
    # immutable identity frontier. Refuse until that contract is integrated.
    for table in ("owner_identities", "center_admissions", "box_records"):
        relative = ".broker/state/owner-identities.db"
        if rows(current, relative, table) != rows(staged, relative, table):
            raise ValueError("identity/admission frontier differs; forward recovery needed")
    if rows(current, ".tinyassets.db", "deleted_principals") != rows(
        staged, ".tinyassets.db", "deleted_principals"
    ):
        raise ValueError("deletion tombstones differ; C12 forward recovery needed")
    relative = ".boxhost/boxhost.db"
    if rows(current, relative, "owner_destroys") != rows(staged, relative, "owner_destroys"):
        raise ValueError("owner destruction frontier differs; forward recovery needed")
    with sqlite3.connect((current / relative).as_uri() + "?mode=ro", uri=True) as live:
        with sqlite3.connect(staged / relative) as target:
            target.execute("PRAGMA synchronous=FULL")
            page = target.execute("PRAGMA page_size").fetchone()[0]
            if target.execute(f"PRAGMA max_page_count={16 * 1024 * 1024 // page}").fetchone()[0] \
                    > 16 * 1024 * 1024 // page:
                raise ValueError("restored receipt journal exceeds its bound")
            target.execute("BEGIN IMMEDIATE")
            for row in live.execute("SELECT * FROM ops"):
                previous = target.execute("SELECT kind,digest FROM ops WHERE "
                                          "command_center_id=? AND op_id=?", row[:2]).fetchone()
                if previous is not None and previous != row[2:4]:
                    raise ValueError("restored operation id has conflicting arguments")
                target.execute("INSERT OR IGNORE INTO ops VALUES (?,?,?,?,?,?,?)", row)
            for row in live.execute("SELECT * FROM execs"):
                previous = target.execute("SELECT * FROM execs WHERE exec_id=?", row[:1]).fetchone()
                if previous is not None and previous != row:
                    raise ValueError("restored execution id has conflicting ownership")
                target.execute("INSERT OR IGNORE INTO execs VALUES (?,?,?)", row)
            for owner, epoch, generation in live.execute("SELECT * FROM boxes"):
                target.execute("INSERT INTO boxes VALUES (?,?,?) ON CONFLICT(command_center_id) "
                               "DO UPDATE SET epoch=MAX(epoch,excluded.epoch), "
                               "generation=MAX(generation,excluded.generation)",
                               (owner, epoch, generation))
            target.execute("UPDATE boxes SET epoch=epoch+1, generation=generation+1")
            # A pre-restore 'done' receipt no longer describes restored bytes.
            # Preserve every seen operation ID, but never report or replay it.
            target.execute("UPDATE ops SET state='unknown_after_restore'")
            # A newer CLI CAS may not exist in the restored credential store.
            # Never spend that custody pin again based on its old acknowledgement.
            target.execute("UPDATE ops SET outcome=json_set(outcome, '$.cli_ack', json('false'), "
                           "'$.cli_restore_hold', json('true')) "
                           "WHERE json_extract(outcome, '$.cli_lease') IS NOT NULL")
            incarnation = live.execute("SELECT v FROM host WHERE k='incarnation'").fetchone()[0]
            target.execute("UPDATE host SET v=MAX(v,?)+1 WHERE k='incarnation'", (incarnation,))


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
    args = parser.parse_args()
    if args.snapshot_data:
        if args.store or args.trees or args.registry or args.clear:
            parser.error("snapshot verification does not accept quota mutation arguments")
        check_snapshot(args.snapshot_data.resolve(), quiescent=args.quiescent)
        if args.restore_from:
            fence_restore(args.restore_from.resolve(), args.snapshot_data.resolve(),
                          cutover_store=args.cutover_store)
    elif not all((args.store, args.trees, args.registry)) or args.quiescent or args.restore_from:
        parser.error("quota restoration requires store, trees, paired broker database")
    else:
        reapply(args.store, args.trees, args.registry, clear=args.clear)
