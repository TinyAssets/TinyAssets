#!/usr/bin/env python3
"""Host entrypoint: idle on owner-split, live only on committed owner-box."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HARNESS = Path("/opt/tinyassets/boxhostd/harness-current")


def prepare_delegation() -> Path:
    """Evacuate the service before enabling v2 domain controllers or forking."""
    entries = Path("/proc/self/cgroup").read_text().splitlines()
    unified = [line.removeprefix("0::") for line in entries if line.startswith("0::")]
    name = Path(unified[0]).name if len(unified) == 1 else ""
    if name != "boxhostd.service" and not (name.startswith("boxhostd@")
                                           and name.endswith(".service")):
        raise RuntimeError("boxhostd must start in its delegated systemd service cgroup")
    parent = Path("/sys/fs/cgroup") / unified[0].lstrip("/")
    needed = {"cpu", "memory", "pids"}
    if not needed <= set((parent / "cgroup.controllers").read_text().split()):
        raise RuntimeError("boxhostd requires delegated cpu, memory and pids controllers")
    supervisor = parent / "supervisor"
    supervisor.mkdir(exist_ok=True)
    (supervisor / "cgroup.procs").write_text(str(os.getpid()))
    if (parent / "cgroup.procs").read_text().strip():
        raise RuntimeError("boxhostd service cgroup is still populated")
    (parent / "cgroup.subtree_control").write_text("+cpu +memory +pids")
    if not needed <= set((parent / "cgroup.subtree_control").read_text().split()):
        raise RuntimeError("boxhostd controller delegation did not take effect")
    return parent


def data_root() -> Path:
    result = subprocess.run(
        ["docker", "volume", "inspect", "tinyassets-data", "--format", "{{.Mountpoint}}"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    root = Path(result.stdout.strip())
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("data volume mountpoint is unavailable")
    return root


def owner_box_committed(root: Path) -> bool:
    marker = root / ".layout.json"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return False  # a fresh preflight host still serves nothing
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, 1001}:
        raise RuntimeError("data layout marker is not the platform-owned regular file")
    document = json.loads(marker.read_text(encoding="utf-8"))
    if (not isinstance(document, dict)
            or document.get("state") not in {"stable", "recovering"}):
        raise RuntimeError("data layout is not stable; refusing box service")
    box_layout = document.get("box_layout")
    if box_layout in (None, "owner-split"):
        return False
    if box_layout != "owner-box":
        raise RuntimeError("unknown box layout marker")
    return True


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("boxhostd needs root for delegated cgroups and XFS quotas")
    cgroup_parent = prepare_delegation()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path,
                        help="explicit host data mount; otherwise inspect tinyassets-data")
    root = parser.parse_args().data_root or data_root()
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise RuntimeError("data volume mountpoint is unavailable")
    if not owner_box_committed(root):
        from boxhostd_idle import main as idle

        idle()
        return
    rootfs = HARNESS.resolve(strict=True)
    if not rootfs.is_dir() or not rootfs.is_relative_to(HARNESS.parent):
        raise RuntimeError("harness rootfs is unavailable")
    app = rootfs / "app"
    if not app.is_dir() or app.is_symlink():
        raise RuntimeError("harness application is unavailable")
    # Import through the harness-current symlink. prune_old_harnesses deletes
    # the resolved harness-<id>/app directory while this process is still up.
    sys.path.insert(0, str(HARNESS / "app"))
    from tinyassets.boxes.host_service import main as serve

    serve(rootfs=rootfs, data_root=root, cgroup_parent=cgroup_parent)


if __name__ == "__main__":
    main()
