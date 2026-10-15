"""One-shot owner-split -> owner-box move, run only by deploy_fail_safe.

The old trees stay in place. The new image serves only after the marker is
published. Any failure after ``migrating`` requires the paired snapshot restore;
there is deliberately no resume, startup hook, or reverse move.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tinyassets import storage_layout
from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST
from tinyassets.universe_owner import account_type_of
from tinyassets.usage_policy import limits_for

PENDING = 10
MAX_BACKUP_AGE_S = 3600
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class OwnerMove:
    principal: str
    uid: int
    centers: tuple[str, ...]
    quota: int


def _marker(data: Path) -> dict:
    marker = storage_layout.read_marker(data)
    if (not isinstance(marker, dict) or marker.get("layout") not in
            storage_layout.KNOWN_LAYOUTS or marker.get("state") != "stable"):
        raise RuntimeError("stable data layout marker required")
    return marker


def check(data: Path) -> int:
    marker = _marker(data)
    layout = marker.get("box_layout", "owner-split")
    if layout == "owner-box":
        return 0
    if layout == "owner-split":
        return PENDING
    raise RuntimeError(f"unrecognized box layout {layout!r}")


def _verified_archive(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise RuntimeError(f"missing complete snapshot {path}")
    if not 0 <= time.time() - path.stat().st_mtime <= MAX_BACKUP_AGE_S:
        raise RuntimeError(f"snapshot {path} is not fresh")
    digest = path.with_name(path.name + ".sha256")
    expected = digest.read_text(encoding="ascii").split()
    if len(expected) != 2 or expected[1] != path.name or not re.fullmatch(
        r"[a-f0-9]{64}", expected[0]
    ):
        raise RuntimeError(f"invalid snapshot digest {digest}")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    if sha.hexdigest() != expected[0]:
        raise RuntimeError(f"snapshot digest mismatch {path}")


def _verified_pair(data_backup: Path, box_backup: Path) -> None:
    if not data_backup.name.startswith("tinyassets-snapshot-"):
        raise RuntimeError("data backup is not a full cutover snapshot")
    suffix = data_backup.name.removeprefix("tinyassets-snapshot-")
    if box_backup.name != "tinyassets-box-store-" + suffix:
        raise RuntimeError("box backup does not pair with data backup")
    _verified_archive(data_backup)
    _verified_archive(box_backup)


def _owner_plan(data: Path) -> list[OwnerMove]:
    identity_db = data / ".broker" / "state" / "owner-identities.db"
    with sqlite3.connect(f"file:{identity_db}?mode=ro", uri=True) as db:
        identities = dict(db.execute(
            "SELECT principal, machine_id FROM owner_identities"
        ))
        admissions = db.execute(
            "SELECT event, principal, center, machine FROM center_admissions "
            "ORDER BY generation"
        ).fetchall()
    owners: dict[str, set[str]] = {principal: set() for principal in identities}
    retired: set[str] = set()
    for event, principal, center, machine in admissions:
        if (principal not in identities or identities[principal] != machine
                or not _CENTER.fullmatch(center)
                or type(machine) is not int
                or not OWNER_ID_FIRST <= machine <= OWNER_ID_LAST):
            raise RuntimeError("invalid broker owner admission")
        if event == "admit":
            if center in retired or any(center in names for names in owners.values()):
                raise RuntimeError("duplicate or recycled center admission")
            owners[principal].add(center)
        elif event == "retire":
            if center not in owners[principal]:
                raise RuntimeError("retire without matching owner admission")
            owners[principal].remove(center)
            retired.add(center)
        else:
            raise RuntimeError("invalid owner admission event")
    plan = []
    used = set()
    for principal, uid in sorted(identities.items(), key=lambda pair: pair[1]):
        if type(uid) is not int or not OWNER_ID_FIRST <= uid <= OWNER_ID_LAST or uid in used:
            raise RuntimeError("invalid permanent owner identity")
        used.add(uid)
        centers = tuple(sorted(owners[principal]))
        for center in centers:
            source = data / center
            info = source.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 1001
                    or info.st_gid != uid):
                raise RuntimeError(f"noncanonical owner center {center}")
        quota = int(limits_for(account_type_of(data, principal)).storage_bytes)
        quota = (quota // 1024) * 1024
        if quota <= 0:
            raise RuntimeError("owner quota is not positive")
        plan.append(OwnerMove(principal, uid, centers, quota))
    # Refuse silently orphaned owner roots; the new image could never reach them.
    for source in data.iterdir():
        if source.name.startswith(".") or not source.is_dir() or source.is_symlink():
            continue
        info = source.lstat()
        if OWNER_ID_FIRST <= info.st_gid <= OWNER_ID_LAST and source.name not in {
            center for owner in plan for center in owner.centers
        }:
            raise RuntimeError(f"unadmitted owner center {source.name}")
    return plan


def _mounted(store: Path) -> None:
    result = subprocess.run(
        ["findmnt", "--mountpoint", str(store), "-no", "FSTYPE,OPTIONS"],
        check=True, capture_output=True, text=True,
    )
    fields = result.stdout.strip().split(None, 1)
    if (len(fields) != 2 or fields[0] != "xfs"
            or not {"pquota", "prjquota"}.intersection(fields[1].split(","))
            or "discard" in fields[1].split(",")):
        raise RuntimeError("owner store must be mounted XFS with project quota, no discard")


def _owner_inode_preflight(data: Path, plan: list[OwnerMove]) -> None:
    """Census before the first write, while all source writers are stopped."""
    seen: dict[tuple[int, int], tuple[str, Path]] = {}
    from tinyassets.boxes.destroy import _mount

    data_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        mount = _mount(data_fd)
    finally:
        os.close(data_fd)
    for owner in plan:
        sources = [data / center for center in owner.centers]
        home = data / ".platform" / "owners" / str(owner.uid) / "agent-home"
        if home.exists() or home.is_symlink():
            sources.append(home)
        for source in sources:
            info = source.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise RuntimeError(f"owner source is not a real directory: {source}")
            for directory, dirs, files, fd in os.fwalk(source, follow_symlinks=False):
                if _mount(fd) != mount:
                    raise RuntimeError(f"foreign mount in owner source: {directory}")
                for name in (".", *dirs, *files):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    path = Path(directory) if name == "." else Path(directory) / name
                    key = (info.st_dev, info.st_ino)
                    previous = seen.setdefault(key, (owner.principal, path))
                    if previous[0] != owner.principal:
                        raise RuntimeError(f"cross-owner inode: {previous[1]} and {path}")


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            info = path.stat()
            os.chown(temporary, info.st_uid, info.st_gid)
            os.chmod(temporary, stat.S_IMODE(info.st_mode))
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _copy(source: Path, target: Path, uid: int) -> None:
    command = [
        "rsync", "-aH", "--xattrs", "--one-file-system", "--numeric-ids",
        "--chown", f"{uid}:{uid}", f"{source}/", f"{target}/",
    ]
    subprocess.run(command, check=True)
    result = subprocess.run(
        [*command[:-2], "--checksum", "--dry-run", "--itemize-changes", *command[-2:]],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip():
        raise RuntimeError(f"rsync checksum differs for {source}")


def apply(data: Path, store: Path, data_backup: Path, box_backup: Path) -> None:
    if os.geteuid() != 0:
        raise PermissionError("box layout apply requires the one-shot root container")
    _verified_pair(data_backup, box_backup)
    _mounted(store)
    lock = os.open(data / storage_layout.LOCK, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = _marker(data)
        if marker.get("box_layout", "owner-split") != "owner-split":
            raise RuntimeError("one-shot box migration requires owner-split")
        plan = _owner_plan(data)
        _owner_inode_preflight(data, plan)
        if any(entry.name != "lost+found" for entry in store.iterdir()):
            raise RuntimeError("box store must be empty before the one-shot move")
        # This is the point of no automatic restart. A failed copy remains
        # visibly migrating until the paired snapshot is restored by an operator.
        _atomic_json(storage_layout.marker_path(data), {
            **marker, "state": "migrating", "migration": "owner-box",
        })
        for owner in plan:
            tree = store / str(owner.uid)
            tree.mkdir(mode=0o700)
            os.chown(tree, owner.uid, owner.uid)
            subprocess.run(
                ["xfs_quota", "-x", "-c", f"project -s -p {tree} {owner.uid}",
                 str(store)], check=True,
            )
            subprocess.run(
                ["xfs_quota", "-x", "-c",
                 f"limit -p bhard={owner.quota // 1024}k {owner.uid}", str(store)],
                check=True,
            )
            for center in owner.centers:
                destination = tree / center
                destination.mkdir(mode=0o700)
                os.chown(destination, owner.uid, owner.uid)
                _copy(data / center, destination, owner.uid)
            agent_home = data / ".platform" / "owners" / str(owner.uid) / "agent-home"
            if agent_home.exists():
                if not agent_home.is_dir() or agent_home.is_symlink():
                    raise RuntimeError("owner agent home is not a real directory")
                destination = tree / ".agent-home"
                destination.mkdir(mode=0o700)
                os.chown(destination, owner.uid, owner.uid)
                _copy(agent_home, destination, owner.uid)
        broker_state = data / ".broker" / "state"
        with sqlite3.connect(broker_state / "owner-identities.db") as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, "
                       "machine INTEGER UNIQUE NOT NULL, project_id INTEGER UNIQUE NOT NULL, "
                       "bhard_bytes INTEGER NOT NULL)")
            db.executemany(
                "INSERT INTO box_records VALUES (?, ?, ?, ?)",
                ((owner.principal, owner.uid, owner.uid, owner.quota) for owner in plan),
            )
        from tinyassets.boxes.state import BoxHostState

        journal = data / ".boxhost"
        journal.mkdir(mode=0o700)  # one-shot migration; never adopt an existing receipt root
        host_state = BoxHostState(journal / "boxhost.db", max_bytes=16 * 1024 * 1024)
        host_state.close()
        os.chmod(journal / "boxhost.db", 0o600)
        _atomic_json(storage_layout.marker_path(data), {
            **marker, "box_layout": "owner-box", "state": "stable",
        })
    finally:
        os.close(lock)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "apply"))
    parser.add_argument("data", type=Path)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--box-backup", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "check":
            return check(args.data)
        if not all((args.store, args.backup, args.box_backup)):
            parser.error("apply requires store and both paired backups")
        apply(args.data, args.store, args.backup, args.box_backup)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error,
            subprocess.CalledProcessError) as error:
        print(f"box layout migration refused: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
