"""C4 deployment-owned, one-shot eligibility. Never imported by the router."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


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
def _locked(directory):
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
            state_fd = os.open("state.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                               dir_fd=fd)
            with os.fdopen(state_fd) as stream:
                metadata = os.fstat(stream.fileno())
                if (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600
                        or not stat.S_ISREG(metadata.st_mode)):
                    raise CutoverRefused("cutover state is not root-owned")
                state = json.load(stream)
            if (set(state) != {"state", "backup", "sha256", "old_image"}
                    or state["state"] != "pre_traffic"
                    or any(not isinstance(state[key], str) or not state[key]
                           for key in ("backup", "sha256", "old_image"))):
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


def rollback_live(directory, root, *, deploy_script, store=None, start_old=None,
                  expected_backup=None):
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
        if store is not None:
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
        import time

        directory = Path(directory)
        directory.rename(directory.with_name(f"{directory.name}-rolled-back-{time.time_ns()}"))
        _fsync_directory(directory.parent)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=(
        "initialize", "cancel-initialization", "admit", "rollback"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--old-image")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--store", type=Path, default=Path("/var/lib/boxhostd/owners"))
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
        if args.action == "admit":
            admit(args.state, args.data)
        else:
            rollback_live(args.state, args.data, deploy_script=args.deploy_script,
                          store=args.store, start_old=print if args.defer_start else None,
                          expected_backup=args.backup)


if __name__ == "__main__":
    main()
