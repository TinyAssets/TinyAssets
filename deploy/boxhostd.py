#!/usr/bin/env python3
"""Host entrypoint: idle on owner-split, live only on committed owner-box."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HARNESS = Path("/opt/tinyassets/boxhostd/harness-current")


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
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 1001:
        raise RuntimeError("data layout marker is not the daemon-owned regular file")
    document = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("state") != "stable":
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
    if not owner_box_committed(data_root()):
        from boxhostd_idle import main as idle

        idle()
        return
    rootfs = HARNESS.resolve(strict=True)
    if not rootfs.is_dir() or not rootfs.is_relative_to(HARNESS.parent):
        raise RuntimeError("harness rootfs is unavailable")
    app = rootfs / "app"
    if not app.is_dir() or app.is_symlink():
        raise RuntimeError("harness application is unavailable")
    sys.path.insert(0, str(app))
    from tinyassets.boxes.host_service import main as serve

    serve(rootfs=rootfs, data_root=data_root())


if __name__ == "__main__":
    main()
