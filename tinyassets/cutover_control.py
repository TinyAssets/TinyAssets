"""C4 deployment-owned, one-shot eligibility. Never imported by the router."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from tinyassets.boxes.host_service import host_lock


class CutoverRefused(RuntimeError):
    pass


def protect_root(root):
    """C4: shared services cannot replace deployment-owned admission."""
    os.chown(root, 0, 1001)
    root.chmod(0o1775)


def _root():
    if os.geteuid() != 0:
        raise CutoverRefused("cutover control requires root")


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, document, *, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=".cutover-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(document, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def close_admission(root, *, recovering=False):
    """Migration/recovery may close admission; only consumed eligibility opens it."""
    from tinyassets.router_traffic import NAME

    _root()
    # C14 recovery admits nobody, including the cutover canary.
    _write(root / NAME, {"ordinary": None if recovering else False}, mode=0o644)


def admit(directory, root):
    from tinyassets.router_traffic import NAME

    _root()
    layout = json.loads((root / ".layout.json").read_text())
    if layout.get("state") != "stable" or layout.get("box_layout") != "owner-box":
        raise CutoverRefused("committed owner-box layout required")
    gate = json.loads((root / NAME).read_text())
    if not isinstance(gate, dict) or set(gate) != {"ordinary"} or gate["ordinary"] is not False:
        raise CutoverRefused("closed admission required")
    open_admission(directory, open_ordinary=lambda: _write(
        root / NAME, {"ordinary": True}, mode=0o644))


def initialize(directory, *, backup, old_image):
    """Called once, after the stopped backup and before migration starts.

    The exclusive directory creation is intentional: even a crash before the
    state publication cannot turn a restarted workflow into a fresh cutover.
    """
    from tinyassets.box_layout_migration import _verified_archive

    _root()
    fault = os.environ.get("CUTOVER_ROLLBACK_FAULT", "")
    if fault:
        import subprocess

        label = subprocess.run(
            ["docker", "volume", "inspect", "tinyassets-data", "--format",
             '{{ index .Labels "io.tinyassets.live-like" }}'],
            check=True, capture_output=True, text=True).stdout.strip()
        if fault != "after_initialize" or label != "synthetic":
            raise CutoverRefused("rollback fault requires the gate's synthetic volume")
    backup = Path(backup).resolve(strict=True)
    digest = _verified_archive(backup)
    if not isinstance(old_image, str) or not old_image:
        raise CutoverRefused("old image identity required")
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    _fsync_directory(directory.parent)
    _write(directory / "state.json", {
        "state": "pre_traffic", "backup": str(backup),
        "sha256": digest, "old_image": old_image,
    })
    if fault:
        raise CutoverRefused("injected failure after cutover eligibility initialization")


@contextmanager
def _locked(directory, *, expected_state="pre_traffic"):
    _root()
    directory = Path(directory)
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise CutoverRefused("cutover state unavailable") from error
    try:
        metadata = os.fstat(fd)
        if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise CutoverRefused("cutover state is not root-owned")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            current = directory.lstat()
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise CutoverRefused("cutover state directory changed while waiting for its lock")
            state_fd = os.open("state.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                               dir_fd=fd)
            with os.fdopen(state_fd) as stream:
                metadata = os.fstat(stream.fileno())
                if (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600
                        or not stat.S_ISREG(metadata.st_mode)):
                    raise CutoverRefused("cutover state is not root-owned")
                state = json.load(stream)
            if (set(state) != {"state", "backup", "sha256", "old_image"}
                    or state["state"] != expected_state
                    or any(not isinstance(state[key], str) or not state[key]
                           for key in ("backup", "sha256", "old_image"))):
                if expected_state == "consumed":
                    raise CutoverRefused("retry cleanup requires an intact consumed receipt")
                raise CutoverRefused("cutover eligibility unavailable or consumed")
        except (OSError, ValueError, TypeError) as error:
            raise CutoverRefused("cutover state unreadable") from error
        yield directory, state
    finally:
        os.close(fd)


def open_admission(directory, *, open_ordinary):
    """Fsync consumption before the deployment step can open ordinary work."""
    with _locked(directory) as (directory, state):
        _write(directory / "state.json", {**state, "state": "consumed"})
        open_ordinary()
        _archive_receipt(directory, "committed")


def _archive_receipt(directory, outcome):
    """Caller holds the receipt lock through rename and parent durability."""
    import time

    directory.rename(directory.with_name(f"{directory.name}-{outcome}-{time.time_ns()}"))
    _fsync_directory(directory.parent)


def cancel_initialization(directory, *, backup):
    """Retire this attempt's authority without restoring unchanged data.

    The lock-holding deploy caller may use this only before invoking apply.
    Initialization can fail before state publication; that grants no authority.
    """
    _root()
    directory = Path(directory)
    if not (directory / "state.json").exists():
        return
    with _locked(directory) as (directory, state):
        if Path(state["backup"]) != Path(backup).resolve():
            raise CutoverRefused("cutover authority belongs to another deploy attempt")
        _write(directory / "state.json", {**state, "state": "consumed"})


def rollback(directory, *, stop_new, install_old, start_old, expected_backup=None):
    """Only the recorded old image and verified stopped backup may be restored."""
    from tinyassets.box_layout_migration import _verified_archive

    with _locked(directory) as (directory, state):
        backup = Path(state["backup"])
        if expected_backup is not None and backup != Path(expected_backup).resolve():
            raise CutoverRefused("cutover authority belongs to another deploy attempt")
        if _verified_archive(backup, fresh=False) != state["sha256"]:
            raise CutoverRefused("pre-cutover backup changed")
        stop_new()
        install_old(backup)
        # Starting the old system also opens writable traffic. No later
        # workflow may apply this old snapshot again after that point.
        _write(directory / "state.json", {**state, "state": "consumed"})
        start_old(state["old_image"])


def _attempt_inventory(state):
    """Read the immutable pre-cutover identities, never today's writable database."""
    import sqlite3
    import tarfile

    from tinyassets.box_layout_migration import _verified_archive

    backup = Path(state["backup"])
    if _verified_archive(backup, fresh=False) != state["sha256"]:
        raise CutoverRefused("pre-cutover backup changed")
    with tarfile.open(backup, "r:gz") as archive:
        def read(name):
            member = archive.getmember("_data/" + name)
            if not member.isfile():
                raise CutoverRefused("backup inventory is not a regular file")
            return archive.extractfile(member).read()

        marker = json.loads(read(".layout.json"))
        with tempfile.TemporaryDirectory(prefix="cutover-inventory-") as temporary:
            database = Path(temporary) / "identities.db"
            database.write_bytes(read(".broker/state/owner-identities.db"))
            for suffix in ("-wal", "-shm"):
                try:
                    payload = read(".broker/state/owner-identities.db" + suffix)
                except KeyError:
                    continue
                database.with_name(database.name + suffix).write_bytes(payload)
            db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            try:
                uids = {row[0] for row in db.execute("SELECT machine_id FROM owner_identities")}
            finally:
                db.close()
    if any(type(uid) is not int or not 300001 <= uid <= 399999 for uid in uids):
        raise CutoverRefused("backup has invalid owner identities")
    # Legacy deletion targets existed before this attempt and may have been
    # restored with the paired store. They are never retry-cleanup targets.
    legacy = set()
    paired = backup.with_name(backup.name.replace("tinyassets-snapshot-",
                                                "tinyassets-box-store-", 1))
    if paired != backup and paired.exists():
        _verified_archive(paired, fresh=False)
        with tarfile.open(paired, "r:gz") as archive:
            for member in archive.getmembers():
                parts = member.name.rstrip("/").split("/")
                if (len(parts) == 2 and parts[0] == "owners" and parts[1].isdecimal()
                        and member.isdir()):
                    legacy.add(parts[1])
    return marker, uids, legacy


def _run(*args):
    import subprocess

    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)
    return result.stdout.strip()


def _no_owner_boxes(host_state, runsc):
    # The caller holds boxhostd's lifecycle lock: no registration can race this
    # census. Even stopped registrations refuse; a list failure is not emptiness.
    runtime_root = host_state / "runsc"
    runtime_root.mkdir(mode=0o700, exist_ok=True)
    states = json.loads(_run(str(runsc), "--root", str(runtime_root),
                             "list", "--format=json"))
    if states not in ([], None):
        raise CutoverRefused("owner box registered or running in boxhostd")
    # Catch an orphan owner process even if its runtime registration disappeared.
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            status = (process / "status").read_text()
        except FileNotFoundError:
            continue  # exited during census
        for line in status.splitlines():
            if line.startswith("Uid:") and any(
                    300001 <= int(uid) <= 399999 for uid in line.split()[1:]):
                raise CutoverRefused("owner process still running")
    bundles = host_state / "bundles"
    if bundles.exists() and any(bundles.iterdir()):
        raise CutoverRefused("owner box still registered in boxhostd bundles")


def _clear_attempt_store(store, uids, legacy):
    import shutil

    from tinyassets.boxes.volumes import OwnerVolumes

    if store.is_symlink():
        raise CutoverRefused("box store is a symlink")
    store = store.resolve(strict=True)
    if any(Path(target).is_relative_to(store) and Path(target) != store
           for target, _ in OwnerVolumes._mounts()):
        raise CutoverRefused("box store still has mounted owner trees")
    targets = []
    for entry in store.iterdir():
        if entry.name == "lost+found":
            continue
        info = entry.lstat()
        if entry.name in legacy and stat.S_ISDIR(info.st_mode):
            continue
        image = entry.name in {f"{uid}.img" for uid in uids}
        directory = (entry.name in {".locks", ".cutover-shared"}
                     or entry.name.startswith(".cutover-store-"))
        if (entry.is_symlink() or info.st_uid != 0
                or not ((image and stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
                        or (directory and stat.S_ISDIR(info.st_mode)))):
            raise CutoverRefused(f"unrecognized attempt artifact in box store: {entry.name}")
        if image and _run("losetup", "--associated", str(entry),
                          "--noheadings", "--output", "NAME"):
            raise CutoverRefused("box store still has attached owner images")
        if entry.name == ".locks" and any(
                child.name not in {str(uid) for uid in uids} or child.is_symlink()
                or not child.is_file() for child in entry.iterdir()):
            raise CutoverRefused("box store has locks from another attempt")
        targets.append(entry)
    # Validate everything before removing anything. Only attempt artifacts;
    # never restore a snapshot over writes accepted by the old daemon.
    for entry in targets:
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    _fsync_directory(store)


def finish_rollback(directory, root, *, store, old_image,
                    host_state=Path("/var/lib/boxhostd/state"),
                    runsc=Path("/opt/tinyassets/boxhostd/current/runsc")):
    """Self-heal consumed rollback residue under the deploy host-mutation lock.

    old_image is the running or last-accepted image observed by the deploy
    caller, never the requested candidate. No data or backup writes occur.
    """
    with _locked(directory, expected_state="consumed") as (directory, state):
        # Admission can succeed just before the process dies during archiving.
        # This is committed authority, not permission to erase owner images.
        from tinyassets.router_traffic import NAME

        # Open read-only, without creating/chmodding anything on the volume.
        fd = os.open(root / ".layout.lock", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            marker_fd = os.open(root / ".layout.json", os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(marker_fd) as stream:
                actual = json.load(stream)
            if actual.get("state") == "stable" and actual.get("box_layout") == "owner-box":
                try:
                    gate_fd = os.open(root / NAME, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(gate_fd) as stream:
                        gate = json.load(stream)
                except (OSError, ValueError) as error:
                    raise CutoverRefused("committed layout admission unavailable") from error
                if (not isinstance(gate, dict) or set(gate) != {"ordinary"}
                        or gate["ordinary"] is not True):
                    raise CutoverRefused(
                        "committed layout requires open admission; cleanup refused")
                _archive_receipt(directory, "committed")
                return
            if old_image != state["old_image"]:
                raise CutoverRefused("rollback cleanup image mismatch: old_image is not accepted")
            marker, uids, legacy = _attempt_inventory(state)
            if (actual != marker or actual.get("state") != "stable"
                    or actual.get("box_layout", actual.get("split")) != "owner-split"):
                raise CutoverRefused("rollback cleanup requires the stable OLD layout marker")
            if (root.resolve().is_relative_to(store.resolve())
                    or store.resolve().is_relative_to(root.resolve())
                    or Path(state["backup"]).resolve().is_relative_to(store.resolve())):
                raise CutoverRefused("box store overlaps data or backup")
            try:
                with host_lock(host_state):
                    _no_owner_boxes(host_state, runsc)
                    _clear_attempt_store(store, uids, legacy)
                    _archive_receipt(directory, "rolled-back")
            except BlockingIOError as error:
                raise CutoverRefused(
                    "boxhostd is active; owner boxes may be registered or running") from error
        finally:
            os.close(fd)


def _restore_box_store(backup, store):
    """Restore the paired pre-cutover store, only while every writer is stopped."""
    import shutil
    import subprocess
    import tarfile
    from pathlib import PurePosixPath

    from tinyassets.box_layout_migration import _verified_pair

    archive = backup.with_name(backup.name.replace("tinyassets-snapshot-",
                                                  "tinyassets-box-store-", 1))
    _verified_pair(backup, archive, fresh=False)
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        links = {m.name.rstrip("/") for m in members if m.issym()}
        if not members or not stream.getmember("owners").isdir():
            raise CutoverRefused("box-store archive has no owners root")
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or not path.parts or path.parts[0] != "owners"
                    or ".." in path.parts
                    or any(str(p) in links for p in path.parents)
                    or (len(path.parts) > 1 and not path.parts[1].isdecimal())
                    or (len(path.parts) <= 2 and not member.isdir())
                    or not (member.isdir() or member.isfile() or member.issym()
                            or member.islnk())):
                raise CutoverRefused("unsafe box-store archive member")
            if member.islnk():
                target = PurePosixPath(member.linkname)
                if (target.is_absolute() or ".." in target.parts
                        or target.parts[:2] != path.parts[:2]
                        or len(target.parts) < 3 or str(target) in links
                        or any(str(p) in links for p in target.parents)):
                    raise CutoverRefused("unsafe box-store archive hardlink")
    # Never unlink an attached backing file or traverse a nested mount. The
    # migration's mount namespace disappears when its one-shot container exits.
    from tinyassets.boxes.volumes import OwnerVolumes

    store = store.resolve(strict=True)
    if any(Path(target).is_relative_to(store) and Path(target) != store
           for target, _ in OwnerVolumes._mounts()):
        raise CutoverRefused("box store still has mounted owner trees")
    for image in store.glob("*.img"):
        loops = subprocess.run(["losetup", "--associated", str(image), "--noheadings",
                                "--output", "NAME"], check=True, capture_output=True, text=True)
        if loops.stdout.strip():
            raise CutoverRefused("box store still has attached owner images")
    # The store is a separate XFS mount. Extract and rename on that device:
    # cross-device shutil.move silently copies, losing numeric owners/hardlinks.
    with tempfile.TemporaryDirectory(prefix=".cutover-store-", dir=store) as staging:
        subprocess.run(["tar", "-xzf", str(archive), "-C", staging,
                        "--numeric-owner", "--acls", "--xattrs"], check=True)
        for entry in store.iterdir():
            if entry.name == "lost+found" or entry == Path(staging):
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        for entry in (Path(staging) / "owners").iterdir():
            os.rename(entry, store / entry.name)
        _fsync_directory(store)


def rollback_live(directory, root, *, deploy_script, store, start_old=None,
                  expected_backup=None, host_state=Path("/var/lib/boxhostd/state"),
                  runsc=Path("/opt/tinyassets/boxhostd/current/runsc")):
    """Stopped, one-shot old-layout install. Owner volumes are not an input."""
    import shutil
    import subprocess

    from tinyassets.cutover_restore import restore_old_layout

    def run(*args):
        completed = subprocess.run(args, capture_output=True, text=True)
        if completed.returncode:
            raise CutoverRefused(f"{args[0]} failed ({completed.returncode}): "
                                 f"{completed.stdout}\n{completed.stderr}")
        return completed.stdout.strip()

    def stop_new():
        run("systemctl", "stop", "tinyassets-daemon.service", "boxhostd.service")
        consumers = run("docker", "ps", "-q", "--filter", "volume=tinyassets-data").split()
        if consumers:
            run("docker", "stop", *consumers)
        if run("docker", "ps", "-q", "--filter", "volume=tinyassets-data"):
            raise CutoverRefused("new system still running")

    def install_old(backup):
        parent = Path(tempfile.mkdtemp(prefix=".cutover-rollback-", dir=root.parent))
        restored = restore_old_layout(backup, parent / "restored")
        _restore_box_store(backup, store)
        os.rename(root, parent / "displaced")
        try:
            os.rename(restored, root)
        except BaseException:
            os.rename(parent / "displaced", root)
            raise
        _fsync_directory(root.parent)
        shutil.rmtree(parent)
        _fsync_directory(root.parent)

    rollback(directory, stop_new=stop_new, install_old=install_old, expected_backup=expected_backup,
             start_old=start_old or (
                 lambda image: run("bash", str(deploy_script), "--restore-bundle", image)))
    if start_old is None:
        with _locked(directory, expected_state="consumed") as (_, state):
            old_image = state["old_image"]
        finish_rollback(directory, root, store=store, old_image=old_image,
                        host_state=host_state, runsc=runsc)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=(
        "initialize", "cancel-initialization", "admit", "rollback", "finish-rollback"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--old-image")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--store", type=Path, default=Path("/var/lib/boxhostd/owners"))
    parser.add_argument("--host-state", type=Path, default=Path("/var/lib/boxhostd/state"))
    parser.add_argument("--runsc", type=Path,
                        default=Path("/opt/tinyassets/boxhostd/current/runsc"))
    parser.add_argument("--defer-start", action="store_true",
                        help="the lock-holding deploy caller starts the recorded image")
    parser.add_argument("--deploy-script", type=Path, default=Path("/tmp/deploy_fail_safe.sh"))
    args = parser.parse_args()
    if args.action == "initialize":
        if args.backup is None or args.old_image is None:
            parser.error("initialization requires the verified backup and old image")
        initialize(args.state, backup=args.backup, old_image=args.old_image)
    elif args.action == "cancel-initialization":
        if args.backup is None:
            parser.error("cancellation requires this attempt's backup")
        cancel_initialization(args.state, backup=args.backup)
    else:
        if args.data is None:
            parser.error("admission and rollback require the shared data root")
        if args.action == "finish-rollback":
            if not args.old_image:
                parser.error("cleanup requires the running or last-accepted old image")
            finish_rollback(args.state, args.data, store=args.store, old_image=args.old_image,
                            host_state=args.host_state, runsc=args.runsc)
        elif args.action == "admit":
            admit(args.state, args.data)
        else:
            rollback_live(args.state, args.data, deploy_script=args.deploy_script,
                          store=args.store, start_old=print if args.defer_start else None,
                          expected_backup=args.backup, host_state=args.host_state,
                          runsc=args.runsc)


if __name__ == "__main__":
    main()
