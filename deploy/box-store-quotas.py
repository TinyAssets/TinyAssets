#!/usr/bin/env python3
"""Reapply XFS project quotas from the paired broker identity database."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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
    if clear:
        # Permanent project IDs equal UIDs. Retained live trees can postdate
        # the backup; clearing them needs neither generation's identity store.
        for uid in sorted(owner_dirs):
            if not 300001 <= int(uid) <= 399999 or str(int(uid)) != uid:
                raise ValueError("invalid permanent owner tree")
            tree = trees / uid
            subprocess.run(["xfs_quota", "-x", "-c",
                            f"project -C -p {tree} {uid}", str(store)], check=True)
            verify_tree(tree, 0, False)
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
        command = f"project -s -p {tree} {project}"
        subprocess.run(["xfs_quota", "-x", "-c", command, str(store)], check=True)
        subprocess.run(
            ["xfs_quota", "-x", "-c", f"limit -p bhard={limit // 1024}k {project}", str(store)],
            check=True,
        )
        verify_owner(store, tree, project, limit)


def fence_restore(current: Path, staged: Path, *, cutover_store: Path | None = None,
                  restored_store: Path | None = None, disaster: bool = False,
                  backup_time: float = 0, restore_adapters=None, retained_store=None) -> None:
    """Prepare the one exact or disaster path before either volume is swapped."""
    from tinyassets.restore_recovery import disaster_restore, normal_preflight

    if cutover_store is None or restored_store is None:
        raise ValueError("paired restore requires both owner stores")
    if disaster:
        marker = staged / ".layout.json"
        layout = json.loads(marker.read_text()) if marker.exists() else {}
        if layout.get("box_layout") != "owner-box":
            raise ValueError("pre-cutover backup cannot be a disaster source after traffic: "
                             "it requires forward migration; select a verified owner-box pair")
        disaster_restore(current, staged, restored_store, backup_time=backup_time,
                         live_trees=cutover_store, retained_trees=retained_store,
                         **(restore_adapters or {}))
    else:
        normal_preflight(current, staged, cutover_store, backup_time=backup_time,
                         restored_owners=restored_store, retained_owners=retained_store)


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
    if quiescent:
        result = subprocess.run([
            "/opt/tinyassets/boxhostd/current/runsc", "--root", "/var/lib/boxhostd/state/runsc",
            "--systemd-cgroup=false", "list", "--format=json"],
            check=True, capture_output=True, text=True, timeout=10)
        if json.loads(result.stdout) != []:
            raise ValueError("surviving owner sandbox prevents coherent snapshot/restore")


def rehearsal_adapters():
    """Only the harness mounts this file; production images never contain it."""
    module = Path("/run/tinyassets-rehearsal/rehearsal_restore_provider.py")
    if module.is_symlink() or not module.is_file():
        raise ValueError("recorded providers require the harness-only mount")
    sys.path.insert(0, str(module.parent))
    try:
        from rehearsal_restore_provider import adapters

        return adapters()
    finally:
        sys.path.remove(str(module.parent))


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
    parser.add_argument("--retained-store", type=Path)
    parser.add_argument("--finish-erasures", type=Path)
    parser.add_argument("--disaster-restore", action="store_true")
    parser.add_argument("--acknowledge-unrecoverable-facts", action="store_true")
    parser.add_argument("--backup-time")
    parser.add_argument("--rehearsal-provider", action="store_true")
    args = parser.parse_args()
    if args.disaster_restore != args.acknowledge_unrecoverable_facts:
        parser.error("disaster restore requires --acknowledge-unrecoverable-facts")
    if args.rehearsal_provider and not args.disaster_restore:
        parser.error("recorded providers require disaster restore")
    if args.disaster_restore and not (args.snapshot_data and args.restore_from
                                      and args.backup_time):
        parser.error("disaster restore requires a staged backup, live path and backup time")
    if args.finish_erasures:
        from tinyassets.restore_recovery import finish_erasures

        if args.cutover_store is None:
            parser.error("erasure retry requires --cutover-store")
        finish_erasures(args.finish_erasures.resolve(), args.cutover_store.resolve())
    elif args.snapshot_data:
        if args.store or args.trees or args.registry or args.clear:
            parser.error("snapshot verification does not accept quota mutation arguments")
        check_snapshot(args.snapshot_data.resolve(), quiescent=args.quiescent)
        if args.restore_from:
            from datetime import datetime, timezone

            stamp = datetime.strptime(args.backup_time, "%Y-%m-%dT%H-%M-%SZ").replace(
                tzinfo=timezone.utc).timestamp() if args.backup_time else 0
            fence_restore(args.restore_from.resolve(), args.snapshot_data.resolve(),
                          cutover_store=args.cutover_store, restored_store=args.restored_store,
                          disaster=args.disaster_restore, backup_time=stamp,
                          retained_store=args.retained_store,
                          restore_adapters=(rehearsal_adapters()
                                            if args.rehearsal_provider else None))
    elif not all((args.store, args.trees, args.registry)) or args.quiescent or args.restore_from:
        parser.error("quota restoration requires store, trees, paired broker database")
    else:
        reapply(args.store, args.trees, args.registry, clear=args.clear)
