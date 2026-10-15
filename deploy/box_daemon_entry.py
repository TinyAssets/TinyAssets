#!/usr/bin/env python3
"""Select the single serving path from the stable data layout marker."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

LEGACY_LAUNCHER = Path("/usr/local/libexec/ta-launch.py")


def _legacy(reason: str) -> list[str]:
    if not LEGACY_LAUNCHER.is_file():
        raise SystemExit(
            f"startup refused: the data volume is not migrated to the owner-box layout "
            f"({reason}); this image serves only owner-box data. Run the one-shot box "
            "migration through deploy/deploy_fail_safe.sh or restore the paired backup."
        )
    return ["/opt/venv/bin/python", "-I", "-B", str(LEGACY_LAUNCHER)]


def target(data: Path) -> list[str]:
    marker = data / ".layout.json"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        # The existing launcher owns fresh-volume initialization.
        return _legacy("no layout marker")
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("data layout marker is not a regular file")
    document = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("state") != "stable":
        raise RuntimeError("data layout migration is incomplete")
    box_layout = document.get("box_layout", "owner-split")
    if box_layout == "owner-split":
        return _legacy("owner-split marker")
    if box_layout == "owner-box":
        if os.geteuid() != 1001:
            raise RuntimeError("owner-box daemon must start unprivileged")
        return ["/opt/venv/bin/python", "-B", "-m", "tinyassets.boxes.frontend"]
    raise RuntimeError("unrecognized box layout")


def main() -> None:
    try:
        argv = target(Path(os.environ.get("TINYASSETS_DATA_DIR", "/data")))
    except SystemExit as refused:
        print(refused, file=sys.stderr, flush=True)
        raise SystemExit(78) from None
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
