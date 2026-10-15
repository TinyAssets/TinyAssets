#!/usr/bin/env python3
"""Select the single serving path from the stable data layout marker."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def target(data: Path) -> list[str]:
    marker = data / ".layout.json"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        # The existing launcher owns fresh-volume initialization.
        return ["/opt/venv/bin/python", "-I", "-B", "/usr/local/libexec/ta-launch.py"]
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("data layout marker is not a regular file")
    document = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("state") != "stable":
        raise RuntimeError("data layout migration is incomplete")
    box_layout = document.get("box_layout", "owner-split")
    if box_layout == "owner-split":
        return ["/opt/venv/bin/python", "-I", "-B", "/usr/local/libexec/ta-launch.py"]
    if box_layout == "owner-box":
        if os.geteuid() != 1001:
            raise RuntimeError("owner-box daemon must start unprivileged")
        return ["/opt/venv/bin/python", "-B", "-m", "tinyassets.boxes.frontend"]
    raise RuntimeError("unrecognized box layout")


def main() -> None:
    argv = target(Path(os.environ.get("TINYASSETS_DATA_DIR", "/data")))
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
