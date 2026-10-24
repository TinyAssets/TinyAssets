"""Read-only census of every owner volume, and the deploy image hold.

The hold records each owner image's device, inode, size, and allocated blocks
before boxhostd restarts, then refuses the deploy if a restart removes,
replaces, or shrinks one. It does not format, restore, or wake a box.

`audit` enumerates owner_volumes without printing principals, snapshot
hashes, or credential bytes. A missing identity database is a failure, not
zero owners. Run rows and serving bindings are counted only from a tree that
is already mounted; this program never creates a database and never mounts
an image.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

UID_MIN = 300001
UID_MAX = 399999
_IMAGE = re.compile(r"([0-9]+)\.img\Z")
_ACCOUNT = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE = re.compile(r"[a-z0-9][a-z0-9-]{1,63}\Z")
_RUN_NAMES = (
    ".runs.db", ".runs.db-wal", ".runs.db-shm",
    ".langgraph_runs.db", ".langgraph_runs.db-wal", ".langgraph_runs.db-shm",
)
_OK_WORK = {None, "running", "snapshot"}
_BASELINE_FAILURES = {
    "image_replaced", "image_shrunk", "image_set_changed", "baseline_unreadable",
    "grants_dropped", "subscription_files_dropped", "subscription_bytes_dropped",
    "runs_dropped", "serving_bindings_dropped", "markers_dropped",
    "run_store_shrunk",
}


class HoldError(RuntimeError):
    """The image hold could not be recorded or no longer matches."""

    def __init__(self, message: str, code: str = "baseline_unreadable"):
        super().__init__(message)
        self.code = code


def _connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2)


def discover_data_root() -> Path | None:
    try:
        result = subprocess.run(
            ["docker", "volume", "inspect", "--format", "{{ .Mountpoint }}", "tinyassets-data"],
            check=True, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    if not text or "\n" in text or "\x00" in text:
        return None
    path = Path(text)
    if path.is_symlink() or not path.is_dir():
        return None
    return path


def image_census(store: Path) -> dict[str, dict]:
    """Map uid string to device, inode, size, and allocated blocks."""
    if store.is_symlink() or not store.is_dir():
        raise HoldError("owner store is missing or is a symlink", "owner_store_unreadable")
    found: dict[str, dict] = {}
    try:
        entries = list(store.iterdir())
    except OSError as exc:
        raise HoldError("owner store is unreadable", "owner_store_unreadable") from exc
    for entry in entries:
        match = _IMAGE.fullmatch(entry.name)
        if match is None or entry.is_symlink():
            continue
        uid = int(match.group(1))
        if not UID_MIN <= uid <= UID_MAX:
            continue
        try:
            info = entry.lstat()
        except OSError as exc:
            raise HoldError("owner image is unreadable", "owner_store_unreadable") from exc
        if not stat.S_ISREG(info.st_mode):
            raise HoldError("owner image is not a regular file", "owner_store_unreadable")
        found[str(uid)] = {
            "device": info.st_dev, "inode": info.st_ino,
            "size": info.st_size, "blocks": info.st_blocks,
        }
    return found


def _regular_size(path: Path) -> int | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return info.st_size


def _sum_named(root: Path, names: tuple[str, ...]) -> int:
    total = 0
    for name in names:
        size = _regular_size(root / name)
        if size is not None:
            total += size
    try:
        children = list(root.iterdir())
    except OSError:
        return total
    for child in children:
        if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
            continue
        for name in names:
            size = _regular_size(child / name)
            if size is not None:
                total += size
    return total


def _rows(path: Path, query: str) -> int | None:
    if _regular_size(path) is None:
        return None
    try:
        with _connect(path) as conn:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(query).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if row is None or type(row[0]) is not int:
        return None
    return row[0]


def measure_tree(root: Path) -> dict:
    """Byte sizes and row counts already on a mounted owner tree."""
    if root.is_symlink() or not root.is_dir():
        return {"mounted": False}
    markers = 0
    cli = root / ".ta-cli"
    if cli.is_dir() and not cli.is_symlink():
        try:
            sources = list(cli.iterdir())
        except OSError:
            sources = []
        for source in sources:
            if source.is_symlink() or not source.is_dir() or not _SOURCE.fullmatch(source.name):
                continue
            size = _regular_size(source / "connection")
            if size:
                markers += 1
    runs = _rows(root / ".runs.db", "SELECT COUNT(*) FROM runs")
    serving = _rows(
        root / ".tinyassets.db",
        "SELECT COUNT(*) FROM agent_bindings WHERE status = 'serving'")
    return {
        "mounted": True,
        "run_store_bytes": _sum_named(root, _RUN_NAMES),
        "checkpoints_bytes": _sum_named(root, ("checkpoints.db",)),
        "activity_bytes": _sum_named(root, ("activity.log",)),
        "runs_rows": runs,
        "serving_bindings": serving,
        "subscription_markers": markers,
    }


def _boxhostd_mounts(mount_root: Path) -> dict[str, Path]:
    try:
        result = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", "boxhostd.service"],
            check=True, capture_output=True, text=True, timeout=5)
        pid = int(result.stdout.strip() or "0")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if pid <= 0:
        return {}
    root = mount_root if mount_root.is_absolute() else Path("/") / mount_root
    base = Path("/proc") / str(pid) / "root" / root.relative_to("/")
    found = {}
    try:
        entries = list(base.iterdir()) if base.is_dir() and not base.is_symlink() else []
    except OSError:
        return {}
    for entry in entries:
        if entry.is_symlink() or not entry.name.isdigit():
            continue
        uid = int(entry.name)
        if UID_MIN <= uid <= UID_MAX and entry.is_dir():
            found[str(uid)] = entry
    return found


def custody_counts(data_root: Path | None) -> dict:
    """Grant and subscription-file counts. Never the account hash or the bytes."""
    empty = {"grants": None, "subscription_files": None, "subscription_bytes": None,
             "grants_unreadable": False, "subscription_unreadable": False}
    if data_root is None or data_root.is_symlink() or not data_root.is_dir():
        return empty
    broker = data_root / ".broker"
    grants = None
    grants_unreadable = False
    ledger = broker / "outbound.db"
    if ledger.is_symlink():
        grants_unreadable = True
    elif ledger.is_file():
        try:
            with _connect(ledger) as conn:
                conn.execute("PRAGMA query_only=ON")
                row = conn.execute(
                    "SELECT COUNT(*) FROM outbound_connection_grants "
                    "WHERE revoked_at IS NULL").fetchone()
            grants = row[0] if row is not None and type(row[0]) is int else None
            grants_unreadable = grants is None
        except (OSError, sqlite3.Error):
            grants_unreadable = True
    files = 0
    total = 0
    unreadable = False
    accounts = broker / "accounts"
    if accounts.is_symlink():
        unreadable = True
    elif accounts.is_dir():
        try:
            entries = list(accounts.iterdir())
        except OSError:
            entries = []
            unreadable = True
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir() or not _ACCOUNT.fullmatch(entry.name):
                continue
            path = entry / "credentials" / ".subscription-state.json"
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                unreadable = True
                continue
            if stat.S_ISREG(info.st_mode) and info.st_size > 0:
                files += 1
                total += info.st_size
    if not accounts.exists() and not accounts.is_symlink():
        files_out: int | None = 0
        bytes_out: int | None = 0
    elif unreadable:
        files_out = bytes_out = None
    else:
        files_out, bytes_out = files, total
    return {"grants": grants, "subscription_files": files_out,
            "subscription_bytes": bytes_out, "grants_unreadable": grants_unreadable,
            "subscription_unreadable": unreadable}


def _load_registry(data_root: Path) -> tuple[list[dict], int | None]:
    path = data_root / ".broker" / "state" / "owner-identities.db"
    if path.is_symlink() or not path.is_file():
        raise HoldError("identity database is unreadable", "identity_unreadable")
    try:
        with _connect(path) as conn:
            conn.execute("PRAGMA query_only=ON")
            try:
                identity_rows = conn.execute("SELECT COUNT(*) FROM owner_identities").fetchone()[0]
            except sqlite3.Error:
                identity_rows = None
            rows = conn.execute(
                "SELECT uid, local, volume_generation, snapshot_id IS NOT NULL, work "
                "FROM owner_volumes ORDER BY uid").fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise HoldError("identity database is unreadable", "identity_unreadable") from exc
    if type(identity_rows) is not int:
        identity_rows = None
    parsed = []
    for uid, local, generation, has_snapshot, work_text in rows:
        if type(uid) is not int or not UID_MIN <= uid <= UID_MAX:
            raise HoldError("owner volume uid is outside the owner range", "identity_unreadable")
        try:
            work = json.loads(work_text) if work_text else None
        except json.JSONDecodeError:
            work = {"kind": "invalid"}
        if work is not None and not isinstance(work, dict):
            work = {"kind": "invalid"}
        parsed.append({
            "uid": uid, "local": bool(local), "generation": generation,
            "has_snapshot": bool(has_snapshot), "work": work,
        })
    return parsed, identity_rows


def disposition(row: dict, image: dict | None) -> str:
    work = row["work"] or {}
    kind = work.get("kind") if isinstance(work, dict) else "invalid"
    restore_pending = kind == "restore" or bool(work.get("restore_snapshot_id"))
    if image is None:
        return "missing_image"
    if restore_pending or kind == "erase":
        return "restore_or_erase_pending"
    if kind == "create" or (not row["local"] and not row["has_snapshot"]):
        return "format_on_next_wake"
    if not row["local"] and row["has_snapshot"]:
        return "published_snapshot_on_next_wake"
    if kind not in _OK_WORK:
        return "unexpected_work"
    return "serving_local_image"


def _action(name: str) -> str:
    return {
        "serving_local_image": "none",
        "published_snapshot_on_next_wake":
            "ordinary wake installs the published snapshot; do not call volumes.restore",
        "format_on_next_wake": "do not wake; wake would format",
        "missing_image": "do not wake; the image file is absent",
        "restore_or_erase_pending": "do not start another restore",
        "unexpected_work": "do not wake while work is unrecognized",
    }.get(name, "do not wake")


def _write_json(path: Path, document: dict) -> None:
    if path.is_symlink():
        raise HoldError("baseline path is a symlink")
    parent = path.parent
    if parent.is_symlink():
        raise HoldError("baseline directory is a symlink")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = parent / (path.name + ".tmp")
    if temporary.is_symlink():
        raise HoldError("baseline temp is a symlink")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        os.unlink(temporary)
        raise
    os.replace(temporary, path)


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise HoldError("baseline is missing or is a symlink")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HoldError("baseline is unreadable") from exc
    if not isinstance(document, dict) or not isinstance(document.get("images"), dict):
        raise HoldError("baseline is unreadable")
    return document


def compare_images(before: dict, after: dict) -> list[str]:
    failures = []
    if set(before) != set(after):
        failures.append("image_set_changed")
    for uid, old in before.items():
        new = after.get(uid)
        if new is None:
            continue
        if (old.get("device") != new.get("device") or old.get("inode") != new.get("inode")):
            failures.append("image_replaced")
        if (type(new.get("size")) is not int or type(old.get("size")) is not int
                or new["size"] < old["size"]
                or type(new.get("blocks")) is not int or type(old.get("blocks")) is not int
                or new["blocks"] < old["blocks"]):
            failures.append("image_shrunk")
    return list(dict.fromkeys(failures))


def _dropped(before, after, name: str) -> list[str]:
    if before is None:
        return []
    if type(before) is not int or type(after) is not int or after < before:
        return [name]
    return []


def build_audit(store: Path, data_root: Path | None, mount_root: Path, *,
                skip_mounts: bool) -> dict:
    failures: list[str] = []
    images = image_census(store)
    registry: list[dict] = []
    identity_rows = None
    if data_root is None:
        failures.append("identity_unreadable")
    else:
        try:
            registry, identity_rows = _load_registry(data_root)
        except HoldError:
            failures.append("identity_unreadable")
    custody = custody_counts(data_root)
    if custody["grants_unreadable"]:
        failures.append("grants_unreadable")
    if custody["subscription_unreadable"]:
        failures.append("subscription_unreadable")
    mounts = {} if skip_mounts else _boxhostd_mounts(mount_root)
    rows = []
    for row in registry:
        uid = str(row["uid"])
        image = images.get(uid)
        name = disposition(row, image)
        if name != "serving_local_image":
            failures.append(name)
        measured = measure_tree(mounts[uid]) if uid in mounts else {"mounted": False}
        rows.append({
            "uid": row["uid"],
            "local": row["local"],
            "generation": row["generation"],
            "has_snapshot": row["has_snapshot"],
            "work": (row["work"] or {}).get("kind") if isinstance(row["work"], dict) else None,
            "restore_pending": bool(
                isinstance(row["work"], dict) and (
                    row["work"].get("kind") == "restore"
                    or row["work"].get("restore_snapshot_id"))),
            "image_present": image is not None,
            "image_bytes": None if image is None else image["size"],
            "image_blocks": None if image is None else image["blocks"],
            "disposition": name,
            "action": _action(name),
            **measured,
        })
    known = {str(row["uid"]) for row in registry}
    for uid in sorted(set(images) - known, key=int):
        if registry:
            failures.append("image_without_owner")
        rows.append({
            "uid": int(uid), "local": None, "disposition": "image_without_owner",
            "action": "do not restore; the image has no volume row",
            "image_present": True, "image_bytes": images[uid]["size"],
            "image_blocks": images[uid]["blocks"], "mounted": False,
        })
    owners = None if "identity_unreadable" in failures else len(registry)
    affected = None if owners is None else sum(
        1 for row in rows if row.get("disposition") != "serving_local_image")
    return {
        "ok": not failures,
        "owners": owners,
        "identity_rows": identity_rows,
        "affected": affected,
        "images": len(images),
        "grants": custody["grants"],
        "subscription_files": custody["subscription_files"],
        "subscription_bytes": custody["subscription_bytes"],
        "failures": list(dict.fromkeys(failures)),
        "rows": rows,
        "limits": (
            "run and connection counts are asserted only against a baseline "
            "that recorded them. This census does not restore images."
        ),
    }


def hold_document(store: Path, data_root: Path | None, mount_root: Path, *,
                  skip_mounts: bool) -> dict:
    images = image_census(store)
    report = build_audit(store, data_root, mount_root, skip_mounts=skip_mounts)
    by_uid = {str(row["uid"]): row for row in report["rows"]}
    per_owner = {}
    for uid, image in images.items():
        row = by_uid.get(uid) or {}
        per_owner[uid] = {
            **image,
            "runs_rows": row.get("runs_rows"),
            "serving_bindings": row.get("serving_bindings"),
            "subscription_markers": row.get("subscription_markers"),
            "run_store_bytes": row.get("run_store_bytes"),
        }
    return {
        "version": 1,
        "images": per_owner,
        "grants": report["grants"],
        "subscription_files": report["subscription_files"],
        "subscription_bytes": report["subscription_bytes"],
    }


def compare_hold(before: dict, after: dict, *, images_only: bool) -> list[str]:
    failures = compare_images(before.get("images") or {}, after.get("images") or {})
    if images_only:
        return failures
    failures += _dropped(before.get("grants"), after.get("grants"), "grants_dropped")
    failures += _dropped(before.get("subscription_files"), after.get("subscription_files"),
                         "subscription_files_dropped")
    failures += _dropped(before.get("subscription_bytes"), after.get("subscription_bytes"),
                         "subscription_bytes_dropped")
    old_images = before.get("images") or {}
    new_images = after.get("images") or {}
    for uid, old in old_images.items():
        new = new_images.get(uid) or {}
        if old.get("runs_rows") is None or new.get("runs_rows") is None:
            continue
        failures += _dropped(old.get("runs_rows"), new.get("runs_rows"), "runs_dropped")
        failures += _dropped(old.get("serving_bindings"), new.get("serving_bindings"),
                             "serving_bindings_dropped")
        failures += _dropped(old.get("subscription_markers"), new.get("subscription_markers"),
                             "markers_dropped")
        failures += _dropped(old.get("run_store_bytes"), new.get("run_store_bytes"),
                             "run_store_shrunk")
    return list(dict.fromkeys(failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "record", "check"))
    parser.add_argument("--store", type=Path, default=Path("/var/lib/boxhostd/owners"))
    parser.add_argument("--mount-root", type=Path, default=Path("/var/lib/boxhostd/mounts"))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--images-only", action="store_true")
    parser.add_argument("--skip-mounts", action="store_true")
    args = parser.parse_args(argv)
    data_root = args.data_root
    if data_root is None and not args.images_only and args.command != "check":
        data_root = discover_data_root()
    try:
        if args.command == "record":
            if args.baseline is None:
                raise HoldError("baseline path is required")
            document = hold_document(
                args.store, data_root, args.mount_root, skip_mounts=args.skip_mounts)
            _write_json(args.baseline, document)
            print(f"owner image hold: record images={len(document['images'])}", file=sys.stderr)
            return 0
        if args.command == "check":
            if args.baseline is None:
                raise HoldError("baseline path is required")
            before = _read_json(args.baseline)
            after = hold_document(
                args.store, data_root, args.mount_root, skip_mounts=args.skip_mounts)
            failures = compare_hold(before, after, images_only=args.images_only)
            if failures:
                print("owner image hold: check failed " + ",".join(failures), file=sys.stderr)
                return 1
            print(f"owner image hold: check images={len(after['images'])}", file=sys.stderr)
            return 0
        report = build_audit(
            args.store, data_root, args.mount_root, skip_mounts=args.skip_mounts)
        report["baseline"] = "absent"
        if args.baseline is not None and args.baseline.exists():
            before = _read_json(args.baseline)
            after = hold_document(
                args.store, data_root, args.mount_root, skip_mounts=args.skip_mounts)
            extra = compare_hold(before, after, images_only=False)
            report["failures"] = list(dict.fromkeys([*report["failures"], *extra]))
            report["ok"] = not report["failures"]
            report["baseline"] = "mismatch" if extra else "match"
        elif args.baseline is not None:
            report["baseline"] = "absent"
        json.dump(report, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
    except HoldError as exc:
        print(f"owner image hold: {exc}", file=sys.stderr)
        if args.command == "audit":
            json.dump({"ok": False, "owners": None, "affected": None,
                       "failures": [exc.code], "rows": [], "baseline": "absent"},
                      sys.stdout, sort_keys=True)
            sys.stdout.write("\n")
            return 2
        return 1
    if args.command != "audit":
        return 0
    if any(item in _BASELINE_FAILURES for item in report["failures"]):
        return 3
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
