#!/usr/bin/env python3
"""Select the single serving path from the stable data layout marker."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def check_layout(data: Path) -> None:
    marker = data / ".layout.json"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        raise RuntimeError("owner-box layout marker is required") from None
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("data layout marker is not a regular file")
    document = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("state") != "stable":
        raise RuntimeError("data layout migration is incomplete")
    box_layout = document.get("box_layout")
    if box_layout == "owner-box":
        if os.geteuid() != 1001:
            raise RuntimeError("owner-box daemon must start unprivileged")
        return
    raise RuntimeError("unrecognized box layout")


def main() -> None:
    data = Path(os.environ.get("TINYASSETS_DATA_DIR", "/data"))
    check_layout(data)
    from tinyassets.boxes.daemon_control import broker_bootstrap
    from tinyassets.broker.supervisor import BrokerSupervisor
    from tinyassets.sqlite_floor import require_sqlite_floor

    require_sqlite_floor()
    bootstrap = broker_bootstrap()
    supervisor = BrokerSupervisor.from_boxhost(
        data, socket_path=Path(bootstrap["broker_socket"]), proof=bootstrap["proof"])
    supervisor.start()
    from tinyassets.universe_server import main as serve

    serve()


if __name__ == "__main__":
    main()
