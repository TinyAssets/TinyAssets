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


def close_admission(root):
    """Migration/recovery may close admission; only consumed eligibility opens it."""
    from tinyassets.router_traffic import NAME

    _root()
    _write(root / NAME, {"ordinary": False}, mode=0o644)


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


def rollback(directory, *, stop_new, install_old, start_old):
    """Only the recorded old image and verified stopped backup may be restored."""
    from tinyassets.box_layout_migration import _verified_archive

    with _locked(directory) as (directory, state):
        backup = Path(state["backup"])
        if _verified_archive(backup, fresh=False) != state["sha256"]:
            raise CutoverRefused("pre-cutover backup changed")
        stop_new()
        install_old(backup)
        # Starting the old system also opens writable traffic. No later
        # workflow may apply this old snapshot again after that point.
        _write(directory / "state.json", {**state, "state": "consumed"})
        start_old(state["old_image"])


def rollback_live(directory, root, *, deploy_script):
    """Stopped, one-shot old-layout install. Owner volumes are not an input."""
    import shutil
    import subprocess

    from tinyassets.cutover_restore import restore_old_layout

    def run(*args):
        return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()

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
        os.rename(root, parent / "displaced")
        try:
            os.rename(restored, root)
        except BaseException:
            os.rename(parent / "displaced", root)
            raise
        _fsync_directory(root.parent)
        shutil.rmtree(parent)
        _fsync_directory(root.parent)

    rollback(directory, stop_new=stop_new, install_old=install_old,
             start_old=lambda image: run("bash", str(deploy_script), "--restore-bundle", image))


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("initialize", "admit", "rollback"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--old-image")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--deploy-script", type=Path, default=Path("/tmp/deploy_fail_safe.sh"))
    args = parser.parse_args()
    if args.action == "initialize":
        if args.backup is None or args.old_image is None:
            parser.error("initialization requires the verified backup and old image")
        initialize(args.state, backup=args.backup, old_image=args.old_image)
    else:
        if args.data is None:
            parser.error("admission and rollback require the shared data root")
        if args.action == "admit":
            admit(args.state, args.data)
        else:
            rollback_live(args.state, args.data, deploy_script=args.deploy_script)


if __name__ == "__main__":
    main()
